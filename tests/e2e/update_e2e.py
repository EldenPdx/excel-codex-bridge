"""End-to-end (Windows): excel-codex-desktop.cmd installs a newer release by itself.

    python tests/e2e/update_e2e.py excel-codex-bridge-<version>-windows-x64.zip

Unpacks the release zip into folders whose paths hold spaces, brackets and
non-ASCII letters, and offers the same build again as the newest release, with
marker files added, from a fake GitHub releases API; the installed copies act
as version 0.0.1. Each run is `excel-codex-desktop.cmd --off`, as cmd.exe
starts it on a double-click:

1. another excel-codex runs from that folder: the update is downloaded and
   unpacked but not put in, and the current version starts;
2. the launcher file is held open: apply.cmd cannot move it, puts back what it
   had moved, and starts the current version;
3. nothing in the way: what was unpacked goes in without a second download and
   the new launcher starts;
4. another copy is offered a zip that does not match its checksum: nothing
   changes and the current version starts.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

MARKER = "UPDATED-BY-E2E.txt"


def fail(message: str, output: str = "") -> None:
    print(output)
    raise SystemExit(f"FAIL: {message}")


def expect(output: str, *texts: str, absent: tuple[str, ...] = ()) -> None:
    for text in texts:
        if text not in output:
            fail(f"expected {text!r} in the output", output)
    for text in absent:
        if text in output:
            fail(f"did not expect {text!r} in the output", output)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Releases(http.server.BaseHTTPRequestHandler):
    answer: dict = {}
    files: dict[str, bytes] = {}
    downloads = 0

    def do_GET(self):
        if self.path == "/latest":
            body = json.dumps(self.answer).encode()
        elif self.path.startswith("/download/") and self.path[len("/download/"):] in self.files:
            type(self).downloads += 1
            body = self.files[self.path[len("/download/"):]]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Windows only: this runs excel-codex-desktop.cmd.")
    package = Path(sys.argv[1]).resolve()
    root = Path(tempfile.mkdtemp(prefix="update-e2e-"))
    with zipfile.ZipFile(package) as archive:
        top = archive.namelist()[0].split("/")[0]
        match = re.fullmatch(r"excel-codex-bridge-(\d+(?:\.\d+)*)-windows-x64", top)
        if match is None:
            raise SystemExit(f"not a Windows package: {package.name}")
        version = match.group(1)

        # The same build, offered as the newest release, with markers to see it went in.
        newer = io.BytesIO()
        with zipfile.ZipFile(newer, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                out.writestr(info, archive.read(info))
            out.writestr(f"{top}/{MARKER}", b"new release")
            out.writestr(f"{top}/_internal/{MARKER}", b"new release")
    newer_bytes = newer.getvalue()
    name = f"excel-codex-bridge-{version}-windows-x64.zip"

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Releases)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    Releases.files = {name: newer_bytes}

    def offer(digest: str) -> None:
        Releases.answer = {
            "tag_name": f"v{version}",
            "html_url": f"https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v{version}",
            "draft": False,
            "prerelease": False,
            "body": "e2e release",
            "assets": [{
                "name": name,
                "digest": f"sha256:{digest}",
                "size": len(newer_bytes),
                "browser_download_url": f"http://127.0.0.1:{port}/download/{name}",
            }],
        }

    offer(hashlib.sha256(newer_bytes).hexdigest())

    def install(label: str) -> Path:
        folder = root / f"{label} (x86) 更新"
        with zipfile.ZipFile(package) as archive:
            archive.extractall(folder)
        return folder / top

    env = {
        key: value for key, value in os.environ.items()
        if not key.lower().endswith("_proxy") and not key.startswith("EXCEL_BRIDGE_")
    }
    env.update(
        NO_PROXY="*",
        EXCEL_BRIDGE_HOME=str(root / "bridge-home"),
        CODEX_HOME=str(root / "codex-home"),
        EXCEL_BRIDGE_E2E_RELEASES_URL=f"http://127.0.0.1:{port}/latest",
        EXCEL_BRIDGE_E2E_PRETEND_VERSION="0.0.1",
    )
    (root / "codex-home").mkdir()

    def start(app: Path, step: str) -> str:
        launcher = app / "excel-codex-desktop.cmd"
        # What Explorer runs on a double-click, give or take /d (no AutoRun).
        command = f'cmd.exe /d /s /c ""{launcher}" --off"'
        began = time.monotonic()
        result = subprocess.run(
            command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=600,
        )
        output = result.stdout.decode("utf-8", "replace")
        print(f"--- {step} ({time.monotonic() - began:.1f}s, exit {result.returncode})\n{output}")
        if result.returncode != 0:
            fail(f"{step}: exit code {result.returncode}", output)
        expect(output, "Nothing to undo in")
        return output

    def staged(app: Path) -> bool:
        stage = app / ".update"
        return (stage / "version.txt").is_file() and (stage / "new" / "excel-codex.exe").is_file()

    def still_old(app: Path) -> None:
        for path in (app / "excel-codex.exe", app / "_internal", app / "excel-codex-desktop.cmd"):
            if not path.exists():
                fail(f"{path} is gone")
        if (app / MARKER).exists() or (app / "_internal" / MARKER).exists():
            fail("the update went in")

    app = install("first copy")

    # 1. Another excel-codex runs from this folder.
    busy = subprocess.Popen(
        [str(app / "excel-codex.exe"), "serve", "--port", str(free_port()), "--log-file", str(root / "busy.log")],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(5)
        if busy.poll() is not None:
            fail(f"the busy copy exited ({busy.returncode})")
        output = start(app, "1. another copy running")
        expect(output, f"{version} is out (you have 0.0.1)", "still running from this folder",
               absent=("Installing",))
        still_old(app)
        if not staged(app):
            fail("the update was not left unpacked for next time", output)
    finally:
        busy.kill()
        busy.wait(30)
    time.sleep(2)

    # 2. The launcher is held open (without delete sharing): apply.cmd must back out.
    held = open(app / "excel-codex-desktop.cmd", "rb")
    try:
        output = start(app, "2. launcher held open")
        expect(output, f"Installing excel-codex-bridge {version}", f"Could not install {version}",
               "Starting the current version.", absent=("Updated to",))
    finally:
        held.close()
    still_old(app)
    if not staged(app):
        fail("the unpacked update was lost when apply.cmd backed out")

    # 3. Nothing in the way.
    output = start(app, "3. update")
    expect(output, f"Installing excel-codex-bridge {version}", f"Updated to {version}.",
           absent=("Downloading",))
    for path in (app / MARKER, app / "_internal" / MARKER, app / "excel-codex.exe", app / "excel-codex-desktop.cmd"):
        if not path.is_file():
            fail(f"{path} is missing after the update", output)
    if (app / ".update").exists():
        fail(".update was not cleaned up", output)
    if Releases.downloads != 1:
        fail(f"downloaded {Releases.downloads} times, expected once")
    said = subprocess.run([str(app / "excel-codex.exe"), "--version"], capture_output=True, text=True, env=env)
    if version not in said.stdout.split():
        fail(f"the updated excel-codex.exe says {said.stdout!r}")

    # 4. A zip that does not match its checksum.
    other = install("second copy")
    offer("0" * 64)
    output = start(other, "4. checksum mismatch")
    expect(output, "does not match the SHA-256 GitHub lists", absent=("Installing",))
    still_old(other)
    if (other / ".update").exists():
        fail(".update was left behind", output)

    server.shutdown()
    print("PASS: excel-codex-desktop.cmd updated itself; busy, locked and mismatched updates left it alone")


if __name__ == "__main__":
    main()
