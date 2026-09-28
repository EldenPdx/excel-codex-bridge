"""End-to-end check: a launcher and the real Codex CLI against a fake Excel backend.

    python tests/e2e/run_e2e.py [--model MODEL] [--desktop] -- <launcher command...>

The launcher is ``dist/excel-codex/excel-codex.exe``, ``excel-codex.cmd`` or
``./excel-codex.sh``. Codex must be on PATH, and this Python needs the
project requirements (the fake backend runs in-process on FastAPI).

The fake backend answers the first request with a ``run_officejs`` call that
wraps Codex's shell tool, and the second with text saying whether the tool's
output came back.  That covers session reading, the ``-c`` overrides, the tool
call round trip and the environment Codex hands to its commands.

``--desktop`` runs ``<launcher> desktop`` instead and a plain ``codex exec``
next to it, the way the desktop app picks up ``config.toml``; the user's own
config must come back byte for byte when the desktop window stops.

``--parallel`` makes the first answer two independent ``run_officejs`` calls
at once: Codex must run both, and the bridge must replay both native calls
followed by both results in the next request.

``--imagegen`` makes the first answer a call to Codex's image tool
(``image_gen.imagegen``): Codex must send it to the bridge's
``images/generations``, the bridge must draw it with the add-in's image
endpoint, and the picture must come back to the model in the tool result.

``--codex-login`` also leaves a ChatGPT sign-in where ``codex login`` puts it:
the bridge must use it rather than the Excel session.  With
``--codex-login refused`` the backend refuses it, and the bridge must retry
with the Excel session and keep that one.

``--shared`` signs Codex itself in (a made-up API key in its ``auth.json``):
the bridge must then stand in for Codex's own ``openai`` provider, so that
the conversation is filed under ``openai`` with the model's official name,
the way the official sign-in files it.  Codex tries a WebSocket first there;
the bridge's 426 must send it to HTTP.

``--images`` also attaches a picture (``codex exec -i``).  Like the real
backend, the fake one refuses a user message with an inline picture; the
bridge must then upload it once to the attachments endpoint, the way the
add-in does, and name it by its file id from then on.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import itertools
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import re
import shlex
import tempfile
import threading
import time
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse  # noqa: E402

from excel_codex_bridge import codex_config, excel_upstream, exit_timezone  # noqa: E402
from helpers import write_codex_login, write_webview_session  # noqa: E402

USER_PYTHONPATH = "e2e-user-pythonpath"
USER_CONFIG = '# e2e user config\nmodel = "gpt-5.5"\n\n[history]\npersistence = "none"\n'
CODEX_ARGS = [
    "exec", "--skip-git-repo-check", "-s", "danger-full-access",
    # A subcommand -c must not knock out the bridge's own overrides.
    "-c", "model_reasoning_effort=high",
    "Run the check.",
]
# Carries the conversation above on (Codex lists only its current provider's).
RESUME_ARGS = [*CODEX_ARGS[:-1], "resume", "--last", "Carry on."]
PYTHON = "python" if sys.platform == "win32" else "python3"
# Prints a marker only a real execution can produce, plus the PYTHONPATH Codex
# gave the command.  Works in bash, PowerShell and cmd alike.
def probe(expression: str) -> str:
    return (
        f"{PYTHON} -c \"import os; print('bridge-e2e-' + str({expression}), "
        "'PP=[' + os.environ.get('PYTHONPATH', '') + ']')\""
    )


PROBE = probe("6*7")  # bridge-e2e-42
SECOND_PROBE = probe("6*7+1")  # bridge-e2e-43
USAGE = {
    "input_tokens": 120,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 12,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 132,
}


def sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def png(width: int = 16, height: int = 16) -> bytes:
    """A small real PNG (Codex decodes pictures before sending them)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + kind + data
                + zlib.crc32(kind + data).to_bytes(4, "big"))

    rows = b"".join(b"\x00" + b"".join(bytes((x * 15, y * 15, 128)) for x in range(width)) for y in range(height))
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


def pictures(value) -> list[dict]:
    """Every input_image anywhere in a request body."""
    if isinstance(value, list):
        return [part for item in value for part in pictures(item)]
    if isinstance(value, dict):
        own = [value] if value.get("type") == "input_image" else []
        return own + [part for item in value.values() for part in pictures(item)]
    return []


def inline_in_user_message(body: dict) -> bool:
    return any(item.get("type", "message") == "message" and "data:image" in json.dumps(item)
               for item in body.get("input", []) if isinstance(item, dict))


def shell_call(raw_request: str, command: str = PROBE) -> tuple[str, dict]:
    """Call whichever shell tool this Codex build declared."""
    if "exec_command" in raw_request:
        return "exec_command", {"cmd": command}
    if "shell_command" in raw_request:
        return "shell_command", {"command": command}
    return "shell", {"command": ["bash", "-lc", command] if sys.platform != "win32" else ["cmd", "/c", command]}


def transport_call(n: int, name: str, arguments: dict) -> dict:
    code = json.dumps({"name": name, "arguments": arguments})
    return {
        "type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}",
        "name": "run_officejs", "arguments": json.dumps({"code": code}), "status": "completed",
    }


IMAGE_PROMPT = "a blue whale in a spreadsheet"
EXIT_IP = "1.1.1.1"


def exit_zone_for(now: dt.datetime) -> str:
    """A proxy exit whose day is not today in UTC, so moving the date shows."""
    return "Pacific/Kiritimati" if now.hour >= 10 else "Pacific/Pago_Pago"


class FakeExcelBackend:
    def __init__(self, parallel: bool = False, imagegen: bool = False, refuse: str | None = None,
                 exit_zone: str = "Pacific/Kiritimati") -> None:
        self.requests: list[dict] = []
        # The proxy exit the bridge finds: this IP, in this timezone.
        self.exit_zone = exit_zone
        self.lookups: list[str] = []
        # The account of every request, and the one this backend refuses to serve.
        self.accounts: list[str] = []
        self.refuse = refuse
        # What the bridge sent to the add-in's image endpoint, and the picture drawn.
        self.drawings: list[tuple[dict, dict]] = []
        self.drawn = base64.b64encode(png(12, 8)).decode()
        self.unexpected: list[str] = []
        self.shell_tool = ""
        # Requests refused for an inline picture in a user message, and uploads.
        self.refused: list[dict] = []
        self.uploads: list[tuple[dict, bytes]] = []
        counter = itertools.count(1)
        app = FastAPI()

        @app.post("/basispoints/api/attachments")
        async def attachments(request: Request):
            self.uploads.append((dict(request.headers), await request.body()))
            return JSONResponse({"openai_file_id": f"file-e2e-{len(self.uploads)}", "filename": "picture.png",
                                 "content_type": "image/png", "size": 1, "input_tokens": 85})

        @app.post("/basispoints/api/images/generations")
        async def generations(request: Request):
            self.drawings.append((dict(request.headers), await request.json()))
            return JSONResponse({"created": 1, "background": "opaque", "output_format": "png",
                                 "data": [{"b64_json": self.drawn}]})

        @app.get("/cdn-cgi/trace")
        async def trace():
            return PlainTextResponse(f"fl=1\nh=127.0.0.1\nip={EXIT_IP}\nts=1\n")

        @app.get("/geo/{ip}")
        async def geo(ip: str):
            self.lookups.append(ip)
            # No offset: the day there must come from the bundled timezone data.
            return JSONResponse({"ip": ip, "timezone": self.exit_zone})

        @app.middleware("http")
        async def check_sign_in(request: Request, call_next):
            if not request.url.path.startswith("/basispoints/"):
                return await call_next(request)
            account = request.headers.get("chatgpt-account-id", "")
            self.accounts.append(account)
            if account == self.refuse:
                return JSONResponse({"error": {"message": "not for this client"}}, status_code=401)
            return await call_next(request)

        @app.post("/basispoints/api/responses")
        async def responses(request: Request):
            body = await request.json()
            if inline_in_user_message(body):
                self.refused.append(body)
                return JSONResponse({"detail": "Invalid request body."}, status_code=422)
            n = next(counter)
            self.requests.append(body)
            raw = json.dumps(body)
            if n == 1:
                name, arguments = shell_call(raw)
                self.shell_tool = name
                items = [transport_call(1, name, arguments)]
                if imagegen:
                    items = [transport_call(1, "image_gen.imagegen", {"prompt": IMAGE_PROMPT})]
                if parallel:
                    items.append(transport_call(2, *shell_call(raw, SECOND_PROBE)))
                events = [sse("response.created", {"type": "response.created",
                              "response": {"id": "resp_1", "status": "in_progress", "output": []}})]
                for index, item in enumerate(items):
                    events += [
                        sse("response.output_item.added", {"type": "response.output_item.added",
                            "output_index": index, "item": {**item, "arguments": "", "status": "in_progress"}}),
                        sse("response.function_call_arguments.delta", {
                            "type": "response.function_call_arguments.delta", "output_index": index,
                            "item_id": item["id"], "delta": item["arguments"]}),
                        sse("response.function_call_arguments.done", {
                            "type": "response.function_call_arguments.done", "output_index": index,
                            "item_id": item["id"], "arguments": item["arguments"]}),
                        sse("response.output_item.done", {"type": "response.output_item.done",
                            "output_index": index, "item": item}),
                    ]
                events.append(sse("response.completed", {"type": "response.completed", "response": {
                    "id": "resp_1", "status": "completed", "model": body.get("model"),
                    "output": items, "usage": USAGE}}))
            else:
                seen = "bridge-e2e-42" in raw and (not parallel or "bridge-e2e-43" in raw)
                if imagegen:
                    seen = f"data:image/png;base64,{self.drawn}" in raw
                text = "done: tool output seen" if seen else "done: tool output MISSING"
                msg = {"type": "message", "id": f"msg_{n}", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": text, "annotations": []}]}
                events = [
                    sse("response.created", {"type": "response.created",
                        "response": {"id": f"resp_{n}", "status": "in_progress", "output": []}}),
                    sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                        "item": {**msg, "content": [], "status": "in_progress"}}),
                    sse("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 0,
                        "item_id": msg["id"], "content_index": 0, "delta": text}),
                    sse("response.output_text.done", {"type": "response.output_text.done", "output_index": 0,
                        "item_id": msg["id"], "content_index": 0, "text": text}),
                    sse("response.output_item.done", {"type": "response.output_item.done",
                        "output_index": 0, "item": msg}),
                    sse("response.completed", {"type": "response.completed", "response": {
                        "id": f"resp_{n}", "status": "completed", "model": body.get("model"),
                        "output": [msg], "usage": USAGE}}),
                ]

            async def stream():
                for event in events:
                    yield event

            return StreamingResponse(stream(), media_type="text/event-stream")

        @app.api_route("/{path:path}", methods=["GET", "POST"])
        async def other(path: str):
            self.unexpected.append(path)
            return JSONResponse({"error": {"message": "not here"}}, status_code=404)

        self.app = app


def start_server(app) -> tuple[uvicorn.Server, int]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise SystemExit("fake backend did not start")
        time.sleep(0.05)
    return server, port


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def healthy(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def run(command: list[str], *, cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    print("$", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout, text=True, encoding="utf-8", errors="replace",
    )


def run_launcher(launcher, args, webview: Path, project: Path, env: dict) -> tuple[str, list]:
    command = [*launcher, "--webview-dir", str(webview), "--model", args.model, "--", *args.codex_args]
    result = run(command, cwd=project, env=env, timeout=args.timeout)
    return result.stdout, [(result.returncode == 0, f"launcher exit code {result.returncode}")]


def run_desktop(launcher, args, root: Path, webview: Path, project: Path, env: dict) -> tuple[str, list]:
    config = Path(env["CODEX_HOME"]) / "config.toml"
    config.write_bytes(USER_CONFIG.encode())
    port = free_port()
    # On Windows, desktop also sets the system timezone to the exit's; put back afterwards.
    windows_zone = tzutil("/g") if sys.platform == "win32" else None
    command = [*launcher, "desktop", "--webview-dir", str(webview), "--model", args.model, "--port", str(port)]
    print("$", subprocess.list2cmdline(command), "&", flush=True)
    group = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
        else {"start_new_session": True}
    )
    log = root / "desktop.log"
    with open(log, "wb") as out:
        desktop = subprocess.Popen(
            command, cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **group
        )
    checks = []
    output = ""
    try:
        deadline = time.monotonic() + args.timeout
        while not healthy(port) and desktop.poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
        checks.append((healthy(port), "the desktop bridge did not come up"))
        enabled = config.read_text(encoding="utf-8")
        provider = "openai" if args.shared else "excel-bridge"
        checks.append((f"model_provider = '{provider}'" in enabled
                       and (f"openai_base_url = 'http://127.0.0.1:{port}/v1'" in enabled) == args.shared,
                       "config.toml was not pointed at the bridge"))
        codex = shutil.which("codex", path=env.get("PATH"))
        if codex and healthy(port):
            codex_env = dict(env)
            for key in ("NO_PROXY", "no_proxy"):
                codex_env[key] = ",".join(filter(None, [codex_env.get(key, ""), "127.0.0.1", "localhost"]))
            result = run([codex, *args.codex_args], cwd=project, env=codex_env, timeout=args.timeout)
            output = result.stdout
            checks.append((result.returncode == 0, f"codex exit code {result.returncode}"))
        else:
            checks.append((False, "codex not found on PATH" if not codex else "skipped codex"))
    finally:
        if desktop.poll() is None:
            if sys.platform == "win32":
                os.kill(desktop.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(desktop.pid, signal.SIGINT)
        try:
            code = desktop.wait(timeout=60)
        except subprocess.TimeoutExpired:
            desktop.kill()
            code = desktop.wait()
    desktop_output = log.read_text(encoding="utf-8", errors="replace")
    print("--- desktop window\n" + desktop_output)
    backup = config.with_name("config.toml.before-excel-codex")
    checks += [
        (code == 0, f"desktop exit code {code}"),
        (config.read_bytes() == USER_CONFIG.encode(), "config.toml was not restored exactly"),
        (backup.exists() and backup.read_bytes() == USER_CONFIG.encode(), "no exact backup of config.toml"),
    ]
    if windows_zone is not None:
        changed = "Windows timezone changed from" in desktop_output
        restored = run([*launcher, "timezone", "restore"], cwd=project, env=env, timeout=120)
        now = tzutil("/g")
        if now != windows_zone:
            tzutil("/s", windows_zone)
        checks += [
            (changed, "desktop did not set the Windows timezone to the exit's"),
            (restored.returncode == 0 and now == windows_zone,
             f"`timezone restore` left {now} instead of {windows_zone}"),
        ]
    return output, checks


def sign_codex_in(home: Path) -> None:
    """A made-up API key sign-in for Codex itself: the bridge then stands in for its openai provider."""
    (home / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-e2e-not-a-key"}), encoding="utf-8")


def migrate_then_resume(launcher, args, webview: Path, project: Path, env: dict) -> tuple[str, list]:
    """A conversation filed the 0.5.3 way, moved by `threads migrate`, then carried on shared."""
    home = Path(env["CODEX_HOME"])
    first = shlex.split(args.first_launcher) if args.first_launcher else launcher
    output, checks = run_launcher(first, args, webview, project, env)
    args.first_requests = len(args.backend.requests)
    before = codex_threads(home)
    checks.append((len(before) == 1 and before[0][0] == "excel-bridge",
                   f"the first conversation should be filed under excel-bridge, got {before}"))
    sign_codex_in(home)
    moved = run([*launcher, "threads", "migrate"], cwd=project, env=env, timeout=120)
    print(moved.stdout)
    after = codex_threads(home)
    official = codex_config.codex_model(args.model)
    checks += [
        (moved.returncode == 0 and "Moved 1 conversation(s)" in moved.stdout,
         "`threads migrate` did not move the conversation"),
        (after == [("openai", official)], f"`threads migrate` should file it as ('openai', {official}), got {after}"),
        (len(list(home.glob("state_*.sqlite.before-excel-codex-*"))) == 1, "no copy of Codex's index"),
    ]
    args.shared = True
    args.codex_args = list(RESUME_ARGS)
    output, resumed = run_launcher(launcher, args, webview, project, env)
    carried = args.backend.requests[args.first_requests:]
    checks += resumed + [
        (len(carried) == 1 and "bridge-e2e-42" in json.dumps(carried[0]),
         "`resume --last` did not carry the migrated conversation on"),
    ]
    return output, checks


def codex_threads(home: Path) -> list[tuple[str, str]]:
    """(provider, model) of each conversation in this run's own Codex home."""
    found = []
    for path in sorted(home.glob("state_*.sqlite")):
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            found += connection.execute("SELECT model_provider, model FROM threads").fetchall()
        finally:
            connection.close()
    return found


def tzutil(*args: str) -> str:
    return subprocess.run(["tzutil", *args], capture_output=True, text=True, check=True).stdout.strip()


def _desktop_log(root: Path) -> str:
    log = root / "desktop.log"
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="gpt-5.6-sol-excel")
    parser.add_argument("--desktop", action="store_true", help="check `desktop` mode with a plain codex")
    parser.add_argument("--images", action="store_true", help="also attach a picture")
    parser.add_argument("--parallel", action="store_true", help="answer with two tool calls at once")
    parser.add_argument("--imagegen", action="store_true", help="answer with a call to Codex's image tool")
    parser.add_argument("--codex-login", nargs="?", const="accepted", choices=["accepted", "refused"],
                        help="also sign Codex in with ChatGPT; `refused` makes the backend turn it down")
    parser.add_argument("--shared", action="store_true",
                        help="sign Codex itself in, so the bridge stands in for its openai provider")
    parser.add_argument("--migrate", action="store_true",
                        help="file a conversation the 0.5.3 way, `threads migrate` it, then carry it on shared")
    parser.add_argument("--first-launcher",
                        help="with --migrate: the command that starts that conversation, e.g. a 0.5.3 checkout's")
    parser.add_argument("--timeout", type=int, default=900, help="seconds for each long step")
    parser.add_argument("launcher", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    launcher = args.launcher[1:] if args.launcher[:1] == ["--"] else args.launcher
    if not launcher:
        parser.error("give the launcher command after --")
    if Path(launcher[0]).exists():
        launcher[0] = str(Path(launcher[0]).resolve())

    root = Path(tempfile.mkdtemp(prefix="excel-codex-e2e-"))
    webview = write_webview_session(root / "webview", time.time() + 3 * 86400, account="e2e-account")
    project = root / "project"
    project.mkdir()
    # Never whoever runs this: a made-up Codex sign-in, or none at all.
    codex_auth = root / "codex-login" / "auth.json"
    if args.codex_login:
        write_codex_login(codex_auth, time.time() + 3 * 86400, account="e2e-codex-account")
    started_at = dt.datetime.now(dt.timezone.utc)
    exit_zone = exit_zone_for(started_at)
    backend = FakeExcelBackend(parallel=args.parallel, imagegen=args.imagegen,
                               refuse="e2e-codex-account" if args.codex_login == "refused" else None,
                               exit_zone=exit_zone)
    server, port = start_server(backend.app)

    env = dict(os.environ)
    env.update(
        CODEX_HOME=str(root / "codex-home"),
        EXCEL_BRIDGE_CODEX_AUTH=str(codex_auth),
        EXCEL_BRIDGE_HOME=str(root / "bridge-home"),
        GHCP_EXCEL_RESPONSES_URL=f"http://127.0.0.1:{port}/basispoints/api/responses",
        EXCEL_BRIDGE_TIMEZONE="auto",
        EXCEL_BRIDGE_TIMEZONE_LOOKUP=f"http://127.0.0.1:{port}/geo/{{ip}}",
        EXCEL_BRIDGE_TIMEZONE_TRACE=f"http://127.0.0.1:{port}/cdn-cgi/trace",
        PYTHONPATH=USER_PYTHONPATH,
    )
    Path(env["CODEX_HOME"]).mkdir()
    if args.shared and not args.migrate:
        sign_codex_in(Path(env["CODEX_HOME"]))
    args.codex_args = list(CODEX_ARGS)
    args.backend, args.first_requests = backend, 0
    picture = png()
    if args.images:
        (project / "picture.png").write_bytes(picture)
        # -i takes every argument after it, so it goes last.
        args.codex_args += ["-i", str(project / "picture.png")]
    started = time.monotonic()
    if args.migrate:
        output, checks = migrate_then_resume(launcher, args, webview, project, env)
    elif args.desktop:
        output, checks = run_desktop(launcher, args, root, webview, project, env)
    else:
        output, checks = run_launcher(launcher, args, webview, project, env)
    server.should_exit = True

    upstream_model = excel_upstream.EXCEL_MODEL_UPSTREAMS[args.model]
    # A --first-launcher from before 0.5.4 does not move the timezone.
    sent = json.dumps(backend.requests[args.first_requests if args.first_launcher else 0:])
    zones = set(re.findall(r"<timezone>([^<]*)</timezone>", sent))
    dates = set(re.findall(r"<current_date>([^<]*)</current_date>", sent))
    exit_days = {exit_timezone.today_in(exit_zone, now=when)
                 for when in (started_at, dt.datetime.now(dt.timezone.utc))}
    checks += [
        # The bridge looks the exit up once; on Windows desktop's system sync does too.
        (0 < len(backend.lookups) <= 2 and set(backend.lookups) == {EXIT_IP},
         f"expected the exit IP looked up once or twice, got {backend.lookups}"),
        (zones == {exit_zone}, f"Codex's timezone should be the exit's ({exit_zone}), got {zones}"),
        (bool(dates) and dates <= exit_days, f"Codex's date should be the day at the exit {exit_days}, got {dates}"),
        (f"provider: {'openai' if args.shared else 'excel-bridge'}" in output, "Codex used another provider"),
        ("done: tool output seen" in output, "the tool output did not reach the model"),
        (len(backend.requests) >= 2, f"expected 2+ upstream requests, got {len(backend.requests)}"),
        (all(r.get("model") == upstream_model for r in backend.requests),
         f"upstream model is not {upstream_model}"),
        (all(r.get("reasoning_effort") == "high" for r in backend.requests),
         "reasoning effort from the subcommand -c did not arrive"),
        (args.imagegen or len(backend.requests) >= 2
         and f"PP=[{USER_PYTHONPATH}]" in json.dumps(backend.requests[1]),
         "Codex's commands saw a different PYTHONPATH"),
        (not backend.unexpected, f"unexpected upstream paths: {backend.unexpected}"),
    ]
    # Every request carries one sign-in: Codex's when it is there and taken, else the Excel one.
    signed_in = "e2e-codex-account" if args.codex_login == "accepted" else "e2e-account"
    served = backend.accounts
    if args.codex_login == "refused":
        checks.append((served[:1] == ["e2e-codex-account"] and served.count("e2e-codex-account") == 1,
                       f"expected one try with the refused Codex sign-in first, got {served}"))
        served = served[1:]
    checks.append((bool(served) and set(served) == {signed_in},
                   f"expected every request on {signed_in}, got {backend.accounts}"))
    if args.shared:
        threads = codex_threads(Path(env["CODEX_HOME"]))
        official = codex_config.codex_model(args.model)
        checks.append((threads == [("openai", official)],
                       f"the conversation should be filed as the official sign-in files it, got {threads}"))
    if args.parallel and len(backend.requests) >= 2:
        replayed = [(item.get("type"), item.get("call_id") if item.get("type") == "function_call_output"
                     else item.get("id"))
                    for item in backend.requests[1].get("input", [])
                    if isinstance(item, dict)
                    and (item.get("name") == "run_officejs" or item.get("type") == "function_call_output")]
        checks += [
            (len(backend.requests) == 2, f"expected exactly 2 upstream requests, got {len(backend.requests)}"),
            (replayed == [("function_call", "fc_1"), ("function_call", "fc_2"),
                          ("function_call_output", "call_1"), ("function_call_output", "call_2")],
             f"both native calls then both results should be replayed, got {replayed}"),
            (backend.requests[1].get("metadata", {}).get("agent_iteration") == "2",
             "parallel results should count as one agent iteration"),
        ]
    if args.imagegen:
        headers, drawing = backend.drawings[0] if backend.drawings else ({}, {})
        checks += [
            (bool(backend.requests) and "image_gen" in json.dumps(backend.requests[0]),
             "Codex did not offer its image tool"),
            (len(backend.drawings) == 1, f"expected one picture drawn, got {len(backend.drawings)}"),
            # Codex 0.156 leaves the background out; later builds send "opaque".
            ({**drawing, "background": "auto"} == {"background": "auto", "model": "gpt-image-2",
                                                   "output_format": "png", "prompt": IMAGE_PROMPT,
                                                   "quality": "auto", "size": "auto"}
             and drawing.get("background") in {"auto", "opaque"},
             f"the image request was not the add-in's: {drawing}"),
            (headers.get("authorization", "").startswith("Bearer ")
             and headers.get("chatgpt-account-id") == signed_in,
             "the image request did not carry the sign-in"),
        ]
    if args.images:
        announced = "Pictures: sent to OpenAI"
        sent = [pictures(body.get("input")) for body in backend.requests]
        file_ids = sorted({part.get("file_id") for parts in sent for part in parts})
        upload_headers, upload = backend.uploads[0] if backend.uploads else ({}, b"")
        checks += [
            (announced in output + _desktop_log(root), f"the bridge did not announce {announced!r}"),
            (len(backend.refused) == 1, f"expected one inline try, got {len(backend.refused)} refused"),
            (len(backend.uploads) == 1, f"expected the picture uploaded once, got {len(backend.uploads)}"),
            (upload_headers.get("content-type", "").startswith("multipart/form-data")
             and upload_headers.get("authorization", "").startswith("Bearer ")
             and upload_headers.get("chatgpt-account-id") == signed_in and picture in upload,
             "the upload was not the add-in's"),
            (bool(sent) and all(len(parts) == 1 for parts in sent),
             f"expected the picture once in every request, got {[len(parts) for parts in sent]}"),
            (file_ids == ["file-e2e-1"], f"expected every request to name the upload, got {file_ids}"),
        ]
    failures = [message for ok, message in checks if not ok]

    print(output)
    print(f"--- {len(backend.requests)} upstream request(s), shell tool {backend.shell_tool or '-'}, "
          f"{time.monotonic() - started:.0f}s")
    for body in backend.requests:
        print("   ", body.get("model"), "reasoning_effort=", body.get("reasoning_effort"))
    print(f"    exit timezone {exit_zone}: Codex's context said {sorted(zones)} {sorted(dates)}")
    if failures:
        log = root / "bridge-home" / "bridge.log"
        if log.exists():
            print("--- bridge.log\n" + log.read_text(encoding="utf-8", errors="replace")[-4000:])
        if len(backend.requests) >= 2:
            tail = json.dumps(backend.requests[1])
            print("--- 2nd request tail\n" + tail[-1500:])
        for message in failures:
            print("FAIL:", message)
        return 1
    shutil.rmtree(root, ignore_errors=True)
    print("e2e ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
