"""Tell the model the proxy exit's timezone, not this computer's.

Codex puts this computer's timezone and date into every conversation
(``<environment_context>``: ``<timezone>``, ``<current_date>``).  Requests
through the bridge leave from the proxy exit, so the bridge finds out where
that is: the exit IP from ``https://<backend>/cdn-cgi/trace``, its timezone
from an IP geolocation service (ipwho.is, else ipapi.co), both through the
bridge's own proxy.  It writes that timezone, and the day it is there, into
the context before a request goes upstream.  Only the exit IP is looked up;
``EXCEL_BRIDGE_TIMEZONE=off`` (``--timezone off``) turns all of this off.

``system_timezone`` does the same lookup for Codex's official ChatGPT sign-in,
which does not go through the bridge, by setting the Windows timezone.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

log = logging.getLogger("excel_codex_bridge")

MODE_ENV = "EXCEL_BRIDGE_TIMEZONE"
MODES = ("auto", "off")
_OFF = {"off", "0", "false", "no"}
# Comma-separated URLs with {ip} in them; mostly for tests.
LOOKUP_ENV = "EXCEL_BRIDGE_TIMEZONE_LOOKUP"
DEFAULT_LOOKUPS = ("https://ipwho.is/{ip}", "https://ipapi.co/{ip}/json/")
# The exit IP is checked this often; a looked-up IP keeps its timezone for LOOKUP_SECONDS.
CHECK_SECONDS = 300
RETRY_SECONDS = 60
LOOKUP_SECONDS = 12 * 3600
# The first request waits this long for the first lookup, then goes as it is.
FIRST_WAIT_SECONDS = 5.0
REQUEST_TIMEOUT = httpx.Timeout(10.0)

_ZONE_NAME = re.compile(r"(?:UTC|[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){1,2})")
_CONTEXT = re.compile(r"<environment_context>.*?</environment_context>", re.S)
_TIMEZONE = re.compile(r"<timezone>([^<]*)</timezone>")
_DATE = re.compile(r"<current_date>(\d{4}-\d{2}-\d{2})</current_date>")


@dataclass(frozen=True)
class Zone:
    name: str
    # Seconds east of UTC when looked up; the day falls back to it without tzdata.
    offset: int | None = None


def enabled() -> bool:
    return os.environ.get(MODE_ENV, "").strip().lower() not in _OFF


def lookup_urls(ip: str) -> list[str]:
    configured = [url.strip() for url in os.environ.get(LOOKUP_ENV, "").split(",") if url.strip()]
    return [url.replace("{ip}", ip) for url in configured or DEFAULT_LOOKUPS]


def trace_url(responses_url: str) -> str:
    parts = urlsplit(responses_url)
    return f"{parts.scheme}://{parts.netloc}/cdn-cgi/trace"


def parse_trace(text: str, host: str | None) -> str:
    """The public IP a Cloudflare ``/cdn-cgi/trace`` answer saw this request come from."""
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if not host or fields.get("h") != host:
        raise ValueError("the trace answered for another host")
    address = ipaddress.ip_address(fields.get("ip", "").strip())
    if not address.is_global:
        raise ValueError("the trace did not see a public IP")
    return str(address)


def _offset(value: object) -> int | None:
    """``+0900`` as seconds."""
    if isinstance(value, str) and re.fullmatch(r"[+-]\d{4}", value):
        seconds = int(value[1:3]) * 3600 + int(value[3:]) * 60
        return -seconds if value[0] == "-" else seconds
    return None


def parse_geo(data: object, ip: str) -> Zone:
    """The timezone in an ipwho.is or ipapi.co answer about ``ip``."""
    if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
        raise ValueError("the lookup was refused")
    if ipaddress.ip_address(str(data.get("ip", ""))) != ipaddress.ip_address(ip):
        raise ValueError("the lookup answered for another IP")
    zone = data.get("timezone")
    if isinstance(zone, dict):
        offset = zone.get("offset")
        offset = offset if isinstance(offset, int) and not isinstance(offset, bool) else None
        zone = zone.get("id")
    else:
        offset = _offset(data.get("utc_offset"))
    if not isinstance(zone, str) or not _ZONE_NAME.fullmatch(zone):
        raise ValueError("the answer has no timezone")
    return Zone(zone, offset)


def today_in(zone: str | None, offset: int | None = None, now: dt.datetime | None = None) -> str | None:
    now = now or dt.datetime.now(dt.timezone.utc)
    if zone:
        try:
            return now.astimezone(ZoneInfo(zone)).date().isoformat()
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    if offset is None:
        return None
    return now.astimezone(dt.timezone(dt.timedelta(seconds=offset))).date().isoformat()


def rewrite_text(text: str, zone: Zone, now: dt.datetime | None = None) -> str:
    """Codex's environment context with ``zone`` and its day in place of this computer's."""
    if "<environment_context>" not in text:
        return text

    def context(match: re.Match) -> str:
        block = match.group(0)
        reported = _TIMEZONE.search(block)
        client_zone = reported.group(1).strip() if reported else None
        block = _TIMEZONE.sub(lambda _: f"<timezone>{zone.name}</timezone>", block)
        date = _DATE.search(block)
        if date:
            # Only today's date moves; an older context keeps the day it was written.
            client_today = today_in(client_zone, now=now) or (now or dt.datetime.now()).astimezone().date().isoformat()
            exit_today = today_in(zone.name, zone.offset, now)
            if exit_today and date.group(1) == client_today:
                block = block[: date.start(1)] + exit_today + block[date.end(1):]
        return block

    return _CONTEXT.sub(context, text)


def _rewrite_item(item: object, zone: Zone, now: dt.datetime | None) -> object:
    if not isinstance(item, dict) or item.get("type", "message") != "message":
        return item
    content = item.get("content")
    if isinstance(content, str):
        text = rewrite_text(content, zone, now)
        return item if text == content else {**item, "content": text}
    if not isinstance(content, list):
        return item
    parts, changed = [], False
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"input_text", "text"} and isinstance(part.get("text"), str):
            text = rewrite_text(part["text"], zone, now)
            if text != part["text"]:
                part, changed = {**part, "text": text}, True
        parts.append(part)
    return {**item, "content": parts} if changed else item


def rewrite_body(body: dict, zone: Zone, now: dt.datetime | None = None) -> dict:
    items = body.get("input")
    if not isinstance(items, list):
        return body
    rewritten = [_rewrite_item(item, zone, now) for item in items]
    if all(new is old for new, old in zip(rewritten, items)):
        return body
    return {**body, "input": rewritten}


class ExitTimezone:
    """The proxy exit's timezone, checked in the background every few minutes."""

    def __init__(self, client, *, trace: str | None = None, clock=time.monotonic) -> None:
        # ``client()`` returns the bridge's upstream client, so lookups share its proxy.
        self._client = client
        self._trace = trace
        self._clock = clock
        self.zone: Zone | None = None
        self._ip: str | None = None
        self._looked_up: float | None = None
        self._next_check = 0.0
        self._checked = False
        self._task: asyncio.Task | None = None

    async def current(self) -> Zone | None:
        if not enabled():
            return None
        if self._task is None and self._clock() >= self._next_check:
            self._task = asyncio.create_task(self._refresh())
        if not self._checked and self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._task), FIRST_WAIT_SECONDS)
        return self.zone

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _refresh(self) -> None:
        try:
            await self._check()
            self._next_check = self._clock() + CHECK_SECONDS
        except Exception as exc:  # noqa: BLE001 - a lookup must never fail a request
            self._next_check = self._clock() + RETRY_SECONDS
            kept = f"still using {self.zone.name}" if self.zone else "sending Codex's own timezone"
            log.warning("could not find the proxy exit's timezone (%s); %s", _reason(exc), kept)
        finally:
            self._checked = True
            self._task = None

    async def _check(self) -> None:
        from . import excel_upstream

        client = self._client()
        url = self._trace or trace_url(excel_upstream.RESPONSES_URL)
        response = await client.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        ip = parse_trace(response.text, urlsplit(url).hostname)
        fresh = self._looked_up is not None and self._clock() - self._looked_up < LOOKUP_SECONDS
        if ip == self._ip and fresh and self.zone is not None:
            return
        if ip != self._ip:
            # A new exit: the old timezone may be wrong now, so it goes until the lookup works.
            self._ip, self.zone, self._looked_up = ip, None, None
        zone = await self._lookup(client, ip)
        if zone != self.zone:
            log.info("proxy exit timezone: %s; Codex is told this one instead of this computer's", zone.name)
        self.zone, self._looked_up = zone, self._clock()

    async def _lookup(self, client: httpx.AsyncClient, ip: str) -> Zone:
        reasons = []
        for url in lookup_urls(ip):
            try:
                response = await client.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                return parse_geo(response.json(), ip)
            except Exception as exc:  # noqa: BLE001 - try the next service
                reasons.append(f"{urlsplit(url).hostname}: {_reason(exc)}")
        raise LookupError("; ".join(reasons) or "no lookup service")


def _reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}" if str(exc).strip() else type(exc).__name__
