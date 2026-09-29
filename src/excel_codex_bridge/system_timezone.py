"""Keep the Windows timezone on the proxy exit Codex uses, while ``desktop`` runs.

Codex tells the model the Windows timezone, which only Windows can change;
the bridge corrects it in requests that pass through it (``exit_timezone``),
but Codex's official ChatGPT sign-in talks to OpenAI directly.  So on Windows
``excel-codex desktop`` (excel-codex-desktop.cmd) also finds that exit the way
``exit_timezone`` does (the IP ``https://chatgpt.com/cdn-cgi/trace`` sees
through the proxy Codex uses, then its timezone from a lookup service that
agrees with Cloudflare on the country) and sets Windows to it with
``tzutil``, at start and every minute while its window is open, and puts back
the one from before the first change when the window goes away.  It changes
the timezone for every program; ``--timezone off`` leaves it alone and
``excel-codex timezone restore`` puts it back after a window that was killed.

Codex's proxy is found the way Codex finds it: ``HTTPS_PROXY`` / ``ALL_PROXY``,
else the Windows proxy setting (not PAC scripts), else none.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import codex_config, codex_login, desktop_config, exit_timezone

# The timezone from before the first change, for `excel-codex timezone restore`.
SETTINGS_NAME = "timezone.json"
STATE_NAME = "timezone-state.json"
CHATGPT_HOST = "chatgpt.com"
API_HOST = "api.openai.com"
# Replaces https://{host}/cdn-cgi/trace; mostly for tests.
TRACE_ENV = "EXCEL_BRIDGE_TIMEZONE_TRACE"
# A looked-up exit keeps its timezone this long; the exit IP is checked every CHECK_SECONDS.
ZONE_CACHE = dt.timedelta(hours=24)
CHECK_SECONDS = 60
# A proxy that spreads requests over nodes in several countries would flip
# Windows every minute; another exit's timezone is taken once it held this many checks in a row.
SETTLE_CHECKS = 3
TIMEOUT = httpx.Timeout(12.0)
_TOP_LEVEL = re.compile(r"""^\s*model_provider\s*=\s*["']([^"']+)["']""", re.M)
_INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_TIME_ZONES = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Time Zones"
# Windows' "Set time zone automatically": the service's Start is 4 while it is off.
_AUTO_TIMEZONE = r"SYSTEM\CurrentControlSet\Services\tzautoupdate"
# Held while the timezone is changed, so a sync cannot change it again after it was put back.
_changing = threading.Lock()


class Refused(RuntimeError):
    """Something that stops a sync, said in words for the user."""


class Unreachable(Refused):
    """Codex's exit host could not be reached through Codex's proxy."""


@dataclass(frozen=True)
class Route:
    name: str
    host: str
    proxy: str | None


def _state_path(name: str) -> Path:
    return codex_config.state_dir() / name


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


# ─── which exit Codex uses ────────────────────────────────────────────────────

def _provider() -> str:
    try:
        text = desktop_config.config_path().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return "openai"
    # Only settings before the first [table] are Codex's own.
    match = _TOP_LEVEL.search(re.split(r"^\s*\[", text, maxsplit=1, flags=re.M)[0])
    return match.group(1) if match else "openai"


def _uses_api_key() -> bool:
    auth = _read_json(codex_login.auth_path())
    if isinstance(auth.get("auth_mode"), str):
        return auth["auth_mode"].lower() == "apikey"
    return bool(auth.get("OPENAI_API_KEY")) and not auth.get("tokens")


def _proxy_url(server: str) -> str:
    server = server.strip()
    return server if "://" in server else f"http://{server}"


def _registry_proxy() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS) as key:
            def value(name, default=None):
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except OSError:
                    return default

            enabled, server = value("ProxyEnable", 0), str(value("ProxyServer", "") or "").strip()
    except OSError:
        return None
    return windows_proxy(server) if enabled else None


def windows_proxy(server: str) -> str | None:
    """The proxy for HTTPS in a Windows ``ProxyServer`` value."""
    if not server:
        return None
    if "=" not in server:
        return _proxy_url(server)
    entries = dict(part.strip().split("=", 1) for part in server.split(";") if "=" in part)
    for scheme in ("https", "http"):
        if entries.get(scheme, "").strip():
            return _proxy_url(entries[scheme])
    if entries.get("socks", "").strip():
        return f"socks5h://{entries['socks'].strip()}"
    return None


def codex_proxy() -> str | None:
    """The proxy Codex's requests to OpenAI go through, as Codex picks it."""
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name, "").strip()
        if value:
            return _proxy_url(value)
    return _registry_proxy()


def codex_route() -> Route:
    if _provider() == "openai" and _uses_api_key():
        return Route("OpenAI API key", API_HOST, codex_proxy())
    return Route("ChatGPT sign-in", CHATGPT_HOST, codex_proxy())


# ─── where the exit is ────────────────────────────────────────────────────────

def _client(proxy: str | None) -> httpx.Client:
    return httpx.Client(proxy=proxy, trust_env=False, timeout=TIMEOUT, follow_redirects=False,
                        headers={"user-agent": "excel-codex-timezone"})


def _checked_url(url: str) -> str:
    host = urlsplit(url).hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not url.startswith("https://") and not loopback:
        raise Refused(f"refusing to look the exit up over plain HTTP: {url}")
    return url


def trace_url(host: str) -> str:
    return os.environ.get(TRACE_ENV, "").strip() or f"https://{host}/cdn-cgi/trace"


def redacted(proxy: str | None) -> str:
    """``proxy`` without a user name or password in it."""
    if not proxy:
        return "no proxy"
    scheme, _, rest = proxy.partition("://")
    return f"{scheme}://{rest.rpartition('@')[2]}" if rest else proxy


def _network_reason(exc: Exception) -> str:
    text = str(exc)
    if "UNEXPECTED_EOF" in text or "EOF occurred in violation of protocol" in text:
        return "the connection was closed during the TLS handshake, as a proxy node that is down does"
    if isinstance(exc, httpx.TimeoutException):
        return f"no answer within {TIMEOUT.connect:g} s ({type(exc).__name__})"
    return exit_timezone._reason(exc)


def find_exit(client: httpx.Client, host: str, proxy: str | None = None) -> exit_timezone.Exit:
    """The exit IP ``host``'s Cloudflare trace sees, and the country Cloudflare places it in."""
    url = _checked_url(trace_url(host))
    try:
        response = client.get(url)
    except httpx.TransportError as exc:
        via = f"through the proxy {redacted(proxy)}" if proxy else "without a proxy"
        raise Unreachable(f"could not reach {host} {via} ({_network_reason(exc)}); "
                          "the timezone is left as it is") from exc
    response.raise_for_status()
    return exit_timezone.parse_trace(response.text, urlsplit(url).hostname)


def lookup(client: httpx.Client, exit: exit_timezone.Exit) -> exit_timezone.Zone:
    """The exit's timezone from the first lookup service that agrees with Cloudflare on the country."""
    reasons = []
    for url in exit_timezone.lookup_urls(exit.ip):
        try:
            response = client.get(_checked_url(url))
            response.raise_for_status()
            return exit_timezone.check_country(exit_timezone.parse_geo(response.json(), exit.ip), exit)
        except Exception as exc:  # noqa: BLE001 - try the next service
            reasons.append(f"{urlsplit(url).hostname}: {exit_timezone._reason(exc)}")
    raise Refused(exit_timezone.lookup_failed(exit, reasons) + "; the timezone is left as it is")


# ─── Windows ──────────────────────────────────────────────────────────────────

def _run(*command: str) -> str:
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise Refused(f"{command[0]} failed: {detail}")
    return result.stdout.strip()


def windows_zone_for(iana: str) -> str:
    """The Windows timezone for an IANA name, from the ICU that comes with Windows 10 1903+."""
    import ctypes

    for name in ("icu.dll", "icuin.dll"):
        try:
            function = ctypes.WinDLL(name).ucal_getWindowsTimeZoneID
            break
        except (OSError, AttributeError):
            continue
    else:
        raise Refused("this Windows has no ICU to map timezones (Windows 10 1903 or later is needed)")
    function.restype = ctypes.c_int32
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.c_wchar_p, ctypes.c_int32,
                         ctypes.POINTER(ctypes.c_int)]
    buffer = ctypes.create_unicode_buffer(128)
    status = ctypes.c_int(0)
    length = function(iana, len(iana), buffer, len(buffer), ctypes.byref(status))
    zone = buffer.value[:length] if status.value <= 0 and length > 0 else ""
    if not zone or not _installed(zone):
        raise Refused(f"Windows has no timezone for {iana}")
    return zone


def _installed(zone: str) -> bool:
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{_TIME_ZONES}\{zone}").Close()
    except OSError:
        return False
    return True


def current_zone() -> str:
    return _run("tzutil", "/g")


def automatic_timezone() -> bool | None:
    """Whether Windows' "Set time zone automatically" is on; None when that cannot be told."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _AUTO_TIMEZONE) as key:
            return winreg.QueryValueEx(key, "Start")[0] != 4
    except (ImportError, OSError):
        return None


def _same_zone(a: str, b: str) -> bool:
    return a.removesuffix("_dstoff") == b.removesuffix("_dstoff")


def set_zone(zone: str) -> None:
    _run("tzutil", "/s", zone)
    now = current_zone()
    if not _same_zone(now, zone):
        raise Refused(f"Windows kept the timezone {now} instead of {zone}")


# ─── sync ─────────────────────────────────────────────────────────────────────

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _cached(exit: exit_timezone.Exit, now: dt.datetime) -> tuple[str, str, str] | None:
    """(IANA zone, Windows zone, when looked up) from the last run, if the exit is the same."""
    state = _read_json(_state_path(STATE_NAME))
    try:
        looked_up = dt.datetime.fromisoformat(state["looked_up_at"])
    except (KeyError, TypeError, ValueError):
        return None
    # A state without exit_country is from before the country was checked (0.5.4), so not trusted.
    if (state.get("exit_ip") != exit.ip or "exit_country" not in state or state["exit_country"] != exit.country
            or not state.get("iana_timezone") or not state.get("windows_timezone")
            or not dt.timedelta(0) <= now - looked_up <= ZONE_CACHE):
        return None
    return state["iana_timezone"], state["windows_timezone"], state["looked_up_at"]


def _record(status: str, **details) -> dict:
    result = {"checked_at": _now().isoformat(), "status": status, **details}
    try:
        _write_json(_state_path(STATE_NAME), result)
    except OSError:
        pass
    return result


class Settle:
    """The timezone the keeper holds Windows on, from one check to the next.

    The first exit's timezone is taken at once.  Another exit's is taken once
    it held ``checks`` checks in a row: a proxy that spreads requests over nodes
    in several countries would otherwise flip Windows every minute.
    """

    def __init__(self, checks: int = SETTLE_CHECKS) -> None:
        self.checks = checks
        self.kept: str | None = None
        self._candidate: str | None = None
        self._seen = 0

    def target(self, found: str) -> tuple[str, bool]:
        """(the timezone Windows should be on now, whether ``found`` is still waiting to be taken)."""
        if self.kept is None or _same_zone(found, self.kept):
            self._candidate, self._seen = None, 0
            return found, False
        if self._candidate is not None and _same_zone(found, self._candidate):
            self._seen += 1
        else:
            self._candidate, self._seen = found, 1
        if self._seen >= self.checks:
            self._candidate, self._seen = None, 0
            return found, False
        return self.kept, True


def sync(*, probe: bool = False, stopped: threading.Event | None = None, settle: Settle | None = None) -> dict:
    """Set Windows to the exit's timezone (``probe``: only say what it would be).

    Once ``stopped`` is set the timezone is left alone: it is being put back.
    With ``settle`` (the keeper), another exit's timezone waits until it held
    a few checks, and Windows going off the kept timezone counts as ``reverted``.
    """
    now = _now()
    route = codex_route()
    with _client(route.proxy) as client:
        exit = find_exit(client, route.host, route.proxy)
        cached = _cached(exit, now)
        if cached:
            iana, found, looked_up = cached
        else:
            iana = lookup(client, exit).name
            found = windows_zone_for(iana) if sys.platform == "win32" else ""
            looked_up = now.isoformat()
        target, waiting = settle.target(found) if settle is not None and found else (found, False)
        before = current_zone() if sys.platform == "win32" else ""
        details = dict(route=route.name, exit_host=route.host, proxy=route.proxy, exit_ip=exit.ip,
                       exit_country=exit.country, iana_timezone=iana, windows_timezone=target, previous_timezone=before,
                       looked_up_at=looked_up)
        if waiting:
            details["exit_windows_timezone"] = found
        if probe or sys.platform != "win32":
            # Not saved: timezone-state.json keeps what the last real sync did.
            return {"checked_at": _now().isoformat(), "status": "probe", **details}
        if _same_zone(before, target):
            if settle is not None:
                settle.kept = target
            return _record("waiting" if waiting else "unchanged", **details)
        # Windows went off the timezone it was kept on: something else changed it.
        reverted = settle is not None and settle.kept is not None and _same_zone(target, settle.kept)
        # Look again right before a first change: the proxy may have just switched.
        # (A kept or settled timezone has been seen for several checks already.)
        if (settle is None or settle.kept is None) and (
                codex_route() != route or find_exit(client, route.host, route.proxy) != exit):
            return _record("skipped", reason="the exit changed during the lookup")
        with _changing:
            if stopped is not None and stopped.is_set():
                return _record("skipped", reason="the window is closing")
            _remember(before)
            set_zone(target)
        if settle is not None:
            settle.kept = target
        if reverted:
            details.update(reverted=True, automatic=automatic_timezone())
        return _record("updated", **details)


def last_result() -> dict:
    return _read_json(_state_path(STATE_NAME))


def _remember(zone: str) -> None:
    path = _state_path(SETTINGS_NAME)
    if not _read_json(path).get("original_timezone"):
        _write_json(path, {"original_timezone": zone, "changed_at": _now().isoformat()})


def original_zone() -> str | None:
    zone = _read_json(_state_path(SETTINGS_NAME)).get("original_timezone")
    return zone if isinstance(zone, str) and zone else None


def restore() -> str | None:
    """Put back the timezone from before the first change (returned), if one was changed."""
    if sys.platform != "win32":
        raise Refused("the system timezone is only changed on Windows")
    with _changing:
        original = original_zone()
        if original:
            set_zone(original)
            _state_path(SETTINGS_NAME).unlink(missing_ok=True)
    return original


class Keeper:
    """``desktop`` on Windows: syncs now and every minute until put back.

    ``report(result)`` hears the first result, every change, and every new error.
    """

    def __init__(self, report, *, interval: float = CHECK_SECONDS, settle: Settle | None = None) -> None:
        self._report = report
        self._interval = interval
        self._settle = settle or Settle()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="windows-timezone", daemon=True)

    def start(self) -> "Keeper":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def put_back(self) -> str | None:
        """Stop, then put back the timezone from before the first change (returned), if there was one."""
        self.stop()
        return restore()

    def _run(self) -> None:
        first = True
        # The kind of error last reported, until a check works again.
        reported_error: str | None = None
        # An error once, not yet reported: a proxy node that fails one check is not worth a line.
        pending_error: str | None = None
        told_waiting = told_reverted = False
        while not self._stop.is_set():
            try:
                result = sync(stopped=self._stop, settle=self._settle)
            except Exception as exc:  # noqa: BLE001 - network, proxy, tzutil
                result = _record("error", error=str(exc) or type(exc).__name__,
                                 unreachable=isinstance(exc, Unreachable))
            status = result["status"]
            if status == "error":
                kind = _error_kind(result)
                if kind != reported_error and (first or kind == pending_error):
                    self._report(result)
                    reported_error = kind
                pending_error = kind
            else:
                pending_error = None
                if status == "updated" and result.get("reverted"):
                    # Said once; every later change back is put right without a word.
                    tell, told_reverted = not told_reverted, True
                elif status == "waiting":
                    tell, told_waiting = not told_waiting, True
                else:
                    tell = first or status == "updated" or reported_error is not None
                if tell:
                    self._report(result)
                reported_error = None
            first = False
            self._stop.wait(self._interval)


def _error_kind(result: dict) -> str:
    """Errors that differ only in their details are one kind, reported once."""
    return str(result.get("error", "")).split(" (", 1)[0]
