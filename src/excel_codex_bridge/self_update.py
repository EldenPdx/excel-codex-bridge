"""Install a newer release into the Windows package before excel-codex-desktop.cmd starts.

The launcher runs ``excel-codex update --launcher`` first. That asks GitHub's
releases API for the latest release (one GET, the same one as the update
notice); if it is newer, it downloads the Windows zip, checks it against the
SHA-256 the API lists for it, unpacks it into ``.update`` in this folder,
makes sure the new excel-codex.exe starts, and exits with ``UPDATE_READY``.
Nothing of this install is touched until then.

The launcher then hands over to ``.update\\apply.cmd``, which swaps the files
once this program has exited (a running .exe and its DLLs cannot be replaced,
and cmd.exe reads a batch file line by line while it runs it, so neither can
the launcher itself) and starts the new launcher. Anything that fails starts
the current version instead. ``EXCEL_BRIDGE_AUTO_UPDATE=0`` keeps just the
notice; ``EXCEL_BRIDGE_UPDATE_CHECK=0`` turns both off.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, TextIO

import httpx

from . import __version__, updates

UPDATE_READY = 10
STAGE_NAME = ".update"
STATE_NAME = "update-install.json"
JUST_UPDATED = "EXCEL_BRIDGE_JUST_UPDATED"
ASSET_NAME = "excel-codex-bridge-{version}-windows-x64.zip"
# What the new folder must hold before the old one is given up.
REQUIRED = ("excel-codex.exe", "excel-codex-desktop.cmd", "_internal")
API_TIMEOUT_SECONDS = 5.0
RETRY_AFTER_SECONDS = 3600
_MAX_DOWNLOAD = 1 << 30
# For the end-to-end test only: another releases API, and the version to act as.
_RELEASES_URL_ENV = "EXCEL_BRIDGE_E2E_RELEASES_URL"
_PRETEND_VERSION_ENV = "EXCEL_BRIDGE_E2E_PRETEND_VERSION"

APPLY_CMD = r"""@echo off
rem Written by `excel-codex update`: moves the release unpacked next to this
rem file into the folder above, then starts excel-codex-desktop.cmd again.
rem The launcher hands over to this file, so the old one is no longer read.
rem No ( ) blocks: the folder's path may hold a ")".
setlocal
for %%I in ("%~dp0..") do set "XCB_APP=%%~fI"
set "XCB_NEW=%~dp0new"
set "XCB_OLD=%~dp0old"
set "XCB_VER="
set /p XCB_VER=<"%~dp0version.txt"
if not defined XCB_VER goto start_current
if not exist "%XCB_NEW%\excel-codex.exe" goto start_current
if exist "%XCB_OLD%" rd /s /q "%XCB_OLD%"
mkdir "%XCB_OLD%"
echo Installing excel-codex-bridge %XCB_VER%...

rem Another window running excel-codex from this folder keeps _internal busy.
set "XCB_FROM=%XCB_APP%\_internal"
set "XCB_TO=%XCB_OLD%\_internal"
call :move
if errorlevel 1 goto busy
set "XCB_FROM=%XCB_APP%\excel-codex.exe"
set "XCB_TO=%XCB_OLD%\excel-codex.exe"
call :move
if errorlevel 1 goto undo_internal
set "XCB_FROM=%XCB_APP%\excel-codex-desktop.cmd"
set "XCB_TO=%XCB_OLD%\excel-codex-desktop.cmd"
call :move
if errorlevel 1 goto undo_exe
set "XCB_FROM=%XCB_NEW%\_internal"
set "XCB_TO=%XCB_APP%\_internal"
call :move
if errorlevel 1 goto undo_launcher
set "XCB_FROM=%XCB_NEW%\excel-codex.exe"
set "XCB_TO=%XCB_APP%\excel-codex.exe"
call :move
if errorlevel 1 goto drop_new_internal
set "XCB_FROM=%XCB_NEW%\excel-codex-desktop.cmd"
set "XCB_TO=%XCB_APP%\excel-codex-desktop.cmd"
call :move
if errorlevel 1 goto drop_new_exe
rem The rest (read-me files, docs) is not needed to start.
robocopy "%XCB_NEW%" "%XCB_APP%" /E /MOVE /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul 2>nul
endlocal & set "EXCEL_BRIDGE_JUST_UPDATED=%XCB_VER%" & "%XCB_APP%\excel-codex-desktop.cmd" %*

rem Each step undoes itself and falls through to undo the ones before it.
:drop_new_exe
set "XCB_FROM=%XCB_APP%\excel-codex.exe"
set "XCB_TO=%XCB_NEW%\excel-codex.exe"
call :move
:drop_new_internal
set "XCB_FROM=%XCB_APP%\_internal"
set "XCB_TO=%XCB_NEW%\_internal"
call :move
:undo_launcher
set "XCB_FROM=%XCB_OLD%\excel-codex-desktop.cmd"
set "XCB_TO=%XCB_APP%\excel-codex-desktop.cmd"
call :move
:undo_exe
set "XCB_FROM=%XCB_OLD%\excel-codex.exe"
set "XCB_TO=%XCB_APP%\excel-codex.exe"
call :move
:undo_internal
set "XCB_FROM=%XCB_OLD%\_internal"
set "XCB_TO=%XCB_APP%\_internal"
call :move
echo Could not install %XCB_VER%: Windows would not let its files be moved into this folder.
goto start_current

:busy
echo excel-codex is still running from this folder in another window; close it to install %XCB_VER%.

:start_current
echo Starting the current version.
endlocal & set "EXCEL_BRIDGE_JUST_UPDATED=later" & "%XCB_APP%\excel-codex-desktop.cmd" %*

rem Move XCB_FROM to XCB_TO, trying for ten seconds; errorlevel 1 if it never went.
:move
set XCB_TRY=0
:move_again
move /y "%XCB_FROM%" "%XCB_TO%" >nul 2>nul
if exist "%XCB_TO%" if not exist "%XCB_FROM%" exit /b 0
set /a XCB_TRY+=1
if %XCB_TRY% geq 10 exit /b 1
ping -n 2 127.0.0.1 >nul
goto move_again
""".replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")


class Skipped(Exception):
    """The user pressed Esc."""


class Failed(Exception):
    """This release cannot be installed now; the message says why."""


@dataclass(frozen=True)
class Download:
    version: str
    url: str
    sha256: str
    size: int


def enabled() -> bool:
    value = os.environ.get("EXCEL_BRIDGE_AUTO_UPDATE", "").strip().lower()
    return updates.enabled() and value not in {"0", "false", "no", "off"}


def install_dir() -> Path | None:
    """The Windows package folder this build runs from, if it is one."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    if all((folder / name).exists() for name in REQUIRED):
        return folder
    return None


def installs_itself() -> bool:
    """Whether a newer release gets installed the next time the launcher starts."""
    return enabled() and install_dir() is not None


def current_version() -> str:
    return os.environ.get(_PRETEND_VERSION_ENV, "").strip() or __version__


def releases_url() -> str:
    return os.environ.get(_RELEASES_URL_ENV, "").strip() or updates.LATEST_URL


def download_from(data: object) -> Download | None:
    """The Windows zip of the release in a GitHub releases API answer, if it can be checked."""
    release = updates.release_from(data)
    if release is None:
        return None
    name = ASSET_NAME.format(version=release.version)
    for asset in data.get("assets") or []:  # type: ignore[union-attr]
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        digest, url, size = asset.get("digest"), asset.get("browser_download_url"), asset.get("size")
        match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest if isinstance(digest, str) else "")
        if (
            match is None
            or not isinstance(url, str)
            or not url.startswith(("https://", "http://"))
            or not isinstance(size, int)
            or not 0 < size <= _MAX_DOWNLOAD
        ):
            return None
        return Download(release.version, url, match.group(1), size)
    return None


def _client(timeout: float | httpx.Timeout) -> httpx.Client:
    proxy = os.environ.get("EXCEL_BRIDGE_PROXY", "").strip() or None
    return httpx.Client(
        timeout=timeout,
        proxy=proxy,
        trust_env=proxy is None,
        follow_redirects=True,
        headers={"User-Agent": f"excel-codex-bridge/{__version__}"},
    )


def fetch_download(url: str | None = None) -> Download | None:
    try:
        with _client(API_TIMEOUT_SECONDS) as client:
            response = client.get(url or releases_url(), headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
            return download_from(response.json())
    except (httpx.HTTPError, ValueError):
        return None


def mirrored(url: str) -> str:
    """``EXCEL_BRIDGE_DOWNLOAD_MIRROR``, a prefix such as ``https://mirror.example/``, goes in front."""
    prefix = os.environ.get("EXCEL_BRIDGE_DOWNLOAD_MIRROR", "").strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix + url if prefix.startswith(("https://", "http://")) else url


def unpack(archive_path: Path, target: Path) -> None:
    """Unpack the release zip into ``target``, without its top folder."""
    with zipfile.ZipFile(archive_path) as archive:
        entries = [(info, info.filename.replace("\\", "/")) for info in archive.infolist()]
        tops = {name.split("/", 1)[0] for _, name in entries if name.strip("/")}
        top = next(iter(tops)) + "/" if len(tops) == 1 else ""
        if top and not all(name.startswith(top) or name == top.rstrip("/") for _, name in entries):
            top = ""
        for info, name in entries:
            relative = name[len(top):] if top and name.startswith(top) else name
            if not relative.strip("/"):
                continue
            parts = PurePosixPath(relative).parts
            if relative.startswith("/") or ":" in relative or any(part in {"", ".", ".."} for part in parts):
                raise Failed(f"the download holds an unsafe path ({name})")
            destination = target.joinpath(*parts)
            if name.endswith("/"):
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, "wb") as sink:
                shutil.copyfileobj(source, sink)
    missing = [name for name in REQUIRED if not (target / name).exists()]
    if missing:
        raise Failed(f"the download is not the Windows package (no {', '.join(missing)})")


def _escape_pressed() -> bool:
    import msvcrt

    pressed = False
    try:
        while msvcrt.kbhit():
            pressed = msvcrt.getwch() == "\x1b" or pressed
    except OSError:
        pass
    return pressed


def _clean_env() -> dict[str, str]:
    """For another PyInstaller build: none of this one's bootloader settings."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("_PYI_", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop(JUST_UPDATED, None)
    return env


def _format_mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


class Installer:
    def __init__(
        self,
        app: Path,
        *,
        state: Path,
        current: str | None = None,
        fetch: Callable[[], Download | None] = fetch_download,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        escape: Callable[[], bool] | None = None,
        busy: Callable[[], bool] = lambda: False,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app = app
        self.stage = app / STAGE_NAME
        self.state = state / STATE_NAME
        self.current = current or current_version()
        self.fetch = fetch
        self.run = run
        self.escape = escape
        self.busy = busy
        self.stream = stream or sys.stdout
        self.clock = clock

    def say(self, message: str = "", end: str = "\n") -> None:
        self.stream.write(message + end)
        self.stream.flush()

    # ── the launcher's step ──────────────────────────────────────────────

    def launcher(self) -> int:
        """UPDATE_READY once a newer release is ready for apply.cmd, else 0 (start this version)."""
        just = os.environ.get(JUST_UPDATED, "").strip()
        if just:
            # apply.cmd started us: installed ("later" when it could not).
            if just == __version__:
                self.clean()
                self._forget_failure()
                self.say(f"Updated to {just}.")
            return 0
        staged = self.staged_version()
        download = self.fetch()
        if download is None:
            # GitHub unreachable; a release unpacked before can still go in.
            if staged and updates.is_newer(staged, self.current):
                return self._ready(staged)
            self.clean()
            return 0
        if not updates.is_newer(download.version, self.current):
            self.clean()
            return 0
        if staged == download.version:
            return self._ready(staged)
        failure = self._recent_failure(download.version)
        if failure:
            self.say(failure)
            return 0
        return self._ready(download.version) if self.prepare(download, retry_later=True) else 0

    def _ready(self, version: str) -> int:
        # Moving the files away under a running copy would break it.
        if self.busy():
            self.say(
                f"excel-codex-bridge {version} is ready, but excel-codex is still running from this folder "
                "(another window?).\n  Close it to install the update; starting the current version."
            )
            return 0
        return UPDATE_READY

    # ── `excel-codex update` typed by hand ────────────────────────────────

    def by_hand(self) -> int:
        download = self.fetch()
        if download is None:
            self.say("Could not read the latest release from GitHub (offline, or behind a proxy?).")
            return 1
        if not updates.is_newer(download.version, self.current):
            self.clean()
            self.say(f"You have the newest release ({self.current}).")
            return 0
        if self.staged_version() != download.version and not self.prepare(download, retry_later=False):
            return 1
        self.say(f"{download.version} is ready: it installs the next time you start excel-codex-desktop.cmd.")
        return 0

    # ── staging ───────────────────────────────────────────────────────────

    def staged_version(self) -> str | None:
        try:
            version = (self.stage / "version.txt").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None
        if updates._version_key(version) is None or not (self.stage / "apply.cmd").is_file():
            return None
        if not all((self.stage / "new" / name).exists() for name in REQUIRED):
            return None
        return version

    def prepare(self, download: Download, *, retry_later: bool) -> bool:
        """Download, check and unpack ``download``; True once apply.cmd can put it in."""
        self.say(
            f"excel-codex-bridge {download.version} is out (you have {self.current}). "
            f"Downloading it ({_format_mb(download.size)})..."
        )
        if self.escape is not None:
            self.say("  Press Esc to skip it this time and start the current version.")
        self.clean()
        archive = self.stage / "download.zip"
        try:
            self.stage.mkdir(parents=True, exist_ok=True)
            digest = self._download(download, archive)
            if digest != download.sha256:
                raise Failed("the download does not match the SHA-256 GitHub lists for it")
            unpack(archive, self.stage / "new")
            archive.unlink()
            self._check_starts(self.stage / "new", download.version)
            (self.stage / "apply.cmd").write_bytes(APPLY_CMD)
            # Written last: a folder without it is never used.
            (self.stage / "version.txt").write_text(download.version, encoding="ascii")
        except Skipped:
            self.clean()
            self.say("Skipped for now.")
            return False
        except Exception as exc:  # whatever it is, the current version still starts
            self.clean()
            reason = str(exc) if isinstance(exc, Failed) else f"{type(exc).__name__}: {exc}"
            self.say(f"Could not update to {download.version}: {reason}.")
            if retry_later:
                self._remember_failure(download.version, reason)
                self.say("  Starting the current version; the update is tried again in an hour.")
            return False
        return True

    def _download(self, download: Download, destination: Path) -> str:
        """The file's SHA-256, once it is all there; Esc (or Ctrl+C) raises Skipped."""
        cancel = threading.Event()
        received = [0]
        outcome: dict[str, object] = {}

        def fetch() -> None:
            try:
                outcome["digest"] = self._fetch_file(download, destination, cancel, received)
            except BaseException as exc:  # handed to the waiting thread
                outcome["error"] = exc

        worker = threading.Thread(target=fetch, name="update-download", daemon=True)
        worker.start()
        live = hasattr(self.stream, "isatty") and self.stream.isatty()
        shown = 0.0
        try:
            while worker.is_alive():
                worker.join(0.1)
                if self.escape is not None and self.escape():
                    raise Skipped
                if live and time.monotonic() - shown >= 0.5:
                    shown = time.monotonic()
                    self.say(f"\r  {_format_mb(received[0])} of {_format_mb(download.size)}", end="")
        except (Skipped, KeyboardInterrupt):
            cancel.set()
            worker.join(2)
            if live:
                self.say()
            raise Skipped from None
        if live:
            self.say(f"\r  {_format_mb(received[0])} of {_format_mb(download.size)}")
        error = outcome.get("error")
        if isinstance(error, BaseException):
            raise error
        return str(outcome["digest"])

    def _fetch_file(self, download: Download, destination: Path, cancel: threading.Event, received: list[int]) -> str:
        digest = hashlib.sha256()
        timeout = httpx.Timeout(30.0, connect=10.0)
        with _client(timeout) as client, client.stream("GET", mirrored(download.url)) as response:
            response.raise_for_status()
            with open(destination, "wb") as sink:
                for chunk in response.iter_bytes(1 << 16):
                    if cancel.is_set():
                        raise Skipped
                    received[0] += len(chunk)
                    if received[0] > download.size:
                        raise Failed("the download is larger than GitHub says it is")
                    digest.update(chunk)
                    sink.write(chunk)
        if received[0] != download.size:
            raise Failed(f"the download stopped at {_format_mb(received[0])} of {_format_mb(download.size)}")
        return digest.hexdigest()

    def _check_starts(self, folder: Path, version: str) -> None:
        command = [str(folder / "excel-codex.exe"), "--version"]
        try:
            result = self.run(
                command, capture_output=True, text=True, errors="replace", timeout=120,
                cwd=str(folder), env=_clean_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Failed(f"the new excel-codex.exe did not start ({exc})") from None
        said = f"{result.stdout or ''}{result.stderr or ''}".strip()
        if result.returncode != 0 or version not in said.split():
            raise Failed(f"the new excel-codex.exe did not start (exit code {result.returncode})")

    def clean(self) -> None:
        shutil.rmtree(self.stage, ignore_errors=True)

    # ── failures wait an hour ─────────────────────────────────────────────

    def _remember_failure(self, version: str, reason: str) -> None:
        payload = {"version": version, "retry_after": self.clock() + RETRY_AFTER_SECONDS, "reason": reason}
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _forget_failure(self) -> None:
        try:
            self.state.unlink()
        except OSError:
            pass

    def _recent_failure(self, version: str) -> str | None:
        try:
            data = json.loads(self.state.read_text(encoding="utf-8"))
            retry_after = float(data["retry_after"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if data.get("version") != version or self.clock() >= retry_after:
            return None
        when = time.strftime("%H:%M", time.localtime(retry_after))
        return f"excel-codex-bridge {version} is out; installing it failed a moment ago, so it is tried again after {when}."


def _canonical(path: str) -> str:
    """One spelling per file: long names, links resolved, case folded (on Windows)."""
    try:
        path = os.path.realpath(path)
    except (OSError, ValueError):
        pass
    return os.path.normcase(os.path.abspath(path))


def running_from(folder: Path) -> bool:
    """Whether another process runs a program from ``folder`` (Windows; False when unsure)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.K32EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    query_limited_information = 0x1000

    prefixes = tuple({_canonical(str(folder)), os.path.normcase(os.path.abspath(str(folder)))})
    prefixes = tuple(prefix.rstrip("\\/") + os.sep for prefix in prefixes)
    size = 1024
    while True:
        pids = (wintypes.DWORD * size)()
        used = wintypes.DWORD()
        if not kernel32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
            return False
        if used.value < ctypes.sizeof(pids):
            break
        size *= 2
    me = os.getpid()
    for pid in pids[: used.value // ctypes.sizeof(wintypes.DWORD)]:
        if pid in (0, me):
            continue
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            continue
        try:
            length = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(length.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                image = os.path.normcase(path.value)
                if image.startswith(prefixes):
                    return True
                # Started through a short (8.3) or otherwise different spelling of the same folder.
                name = os.path.basename(image)
                if ("~" in image or name == "excel-codex.exe") and _canonical(image).startswith(prefixes):
                    return True
        finally:
            kernel32.CloseHandle(handle)
    return False


def installer(app: Path, state: Path) -> Installer:
    """The installer the command line uses: Esc skips when there is a keyboard."""
    escape = _escape_pressed if sys.platform == "win32" and sys.stdin is not None and sys.stdin.isatty() else None

    def busy() -> bool:
        try:
            return running_from(app)
        except (OSError, AttributeError, ValueError):
            return False

    return Installer(app, state=state, escape=escape, busy=busy)
