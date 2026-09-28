"""Local, single-user Responses endpoint backed by the ChatGPT Excel add-in.

Only loopback clients are served, browser-originated requests are refused
(DNS-rebinding / drive-by protection), and only the Excel model aliases are
routed.  The bridge adds no credentials of its own: it forwards a ChatGPT
sign-in already on this machine, Codex's own or the one the Excel add-in
cached (see ``session``).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import ipaddress
import itertools
import json
import logging
import math
import os
import re
import zlib
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import excel_upstream
from . import exit_timezone
from . import image_generation
from . import images
from . import session
from . import sse
from .excel_stream import excel_tool_stream_transform
from .session import SessionReader

log = logging.getLogger("excel_codex_bridge")

MAX_BODY_BYTES = 64 * 1024 * 1024
NON_STREAMING_ATTEMPTS = 2
# Upstream answers when it will not take a request's pictures as they are.
PICTURE_RETRY_STATUSES = {400, 422}
# Drawing a picture takes a minute or two; the add-in waits five minutes for an edit.
IMAGE_TIMEOUT = httpx.Timeout(600.0, connect=30.0)
_LOOPBACK_NAMES = {"localhost"}
# The backend's answer when it will not take a sign-in.
LOGIN_REFUSED_STATUSES = {401, 403}
# Everyone using the add-in shares one tokens-per-minute budget per model.  When
# it runs out the stream fails at once, saying to try again in some milliseconds;
# Codex does, five times within a second, and gives up.  The bridge waits longer
# first, as long as nothing of the answer has come yet.
RATE_LIMIT_CODES = {"rate_limit_exceeded", "slow_down"}
RATE_LIMIT_DELAYS = (1.0, 2.0, 4.0, 8.0, 15.0)
RATE_LIMIT_WAIT = 60.0
# Codex drops a stream that sends nothing for five minutes.
MAX_RATE_LIMIT_WAIT = 240.0
_STREAM_OPENING = {"response.created", "response.in_progress", "response.queued"}
# How much of a stream is read looking for its first event past the opening ones.
_STREAM_HEAD_LIMIT = 4 * 1024 * 1024
_TRY_AGAIN = re.compile(r"(?i)try again in\s*(\d+(?:\.\d+)?)\s*(ms|s|seconds?)\b")

# Kept for tests ported from ghcp_proxy.
_excel_tool_stream_transform = excel_tool_stream_transform


def _is_loopback_address(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _host_header_name(value: str) -> str:
    value = value.strip()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnly:
    """Pure-ASGI guard: loopback peer, loopback Host header, no browser Origin."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = None
        if not _is_loopback_address(client[0] if client else None):
            reason = "only loopback clients are served"
        elif not _is_loopback_address(_host_header_name(headers.get("host", ""))):
            reason = "the Host header must name a loopback address"
        elif "origin" in headers:
            reason = "browser-originated requests are not accepted"
        if reason is None and (scope["type"] == "websocket" or headers.get("upgrade", "").lower() == "websocket"):
            await _refuse_websocket(scope, receive, send)
            return
        if reason is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = sse.openai_error_response(403, f"Forbidden: {reason}.")
        await response(scope, receive, send)


async def _refuse_websocket(scope, receive, send) -> None:
    """Codex's own provider tries a WebSocket first; 426 sends it straight to HTTP."""
    response = sse.openai_error_response(426, "This bridge speaks HTTP only; use POST /v1/responses.")
    if scope["type"] == "http":
        await response(scope, receive, send)
        return
    await receive()  # websocket.connect
    if "websocket.http.response" not in scope.get("extensions", {}):
        await send({"type": "websocket.close", "code": 1003})
        return
    await send({"type": "websocket.http.response.start", "status": 426, "headers": response.raw_headers})
    await send({"type": "websocket.http.response.body", "body": bytes(response.body)})


def _zstd_decompress(raw: bytes) -> bytes:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ValueError("zstd request bodies need the zstandard package") from exc
    return zstandard.ZstdDecompressor().decompressobj().decompress(raw)


def _decode_body(raw: bytes, content_encoding: str) -> dict:
    encoding = content_encoding.strip().lower()
    if encoding == "gzip" or (not encoding and raw.startswith(b"\x1f\x8b")):
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw)
    elif encoding == "zstd" or (not encoding and raw.startswith(b"\x28\xb5\x2f\xfd")):
        raw = _zstd_decompress(raw)
    elif encoding not in {"", "identity"}:
        raise ValueError(f"unsupported content-encoding {encoding!r}")
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("request body is too large")
    payload = json.loads(raw) if raw else {}
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _canonical_model(model: object) -> str | None:
    """Accept the Excel aliases, plus the same names without ``-excel``."""
    if excel_upstream.is_excel_model(model):
        return excel_upstream.excel_model_id(model)
    if isinstance(model, str):
        return excel_upstream.excel_model_id(f"{model.strip()}-excel")
    return None


def _proxy_url() -> str | None:
    value = os.environ.get("EXCEL_BRIDGE_PROXY", "").strip()
    return value or None


def build_upstream_client() -> httpx.AsyncClient:
    """HTTP/1.1 client; honours EXCEL_BRIDGE_PROXY, else HTTPS_PROXY/system proxy."""
    timeout = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0)
    proxy = _proxy_url()
    return httpx.AsyncClient(
        timeout=timeout,
        # Every conversation or subagent that is generating holds one connection.
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=8, keepalive_expiry=300.0),
        verify=True,
        proxy=proxy,
        trust_env=proxy is None,
    )


def _upstream_error_response(
    upstream: httpx.Response,
    *,
    refused: str | None = None,
    login: str = "the ChatGPT session",
    hint: str = "",
) -> Response:
    """The backend's error for Codex; ``refused`` names what a 401/403 refused, else ``login``."""
    status = upstream.status_code
    try:
        payload = upstream.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    message = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(payload.get("detail"), str):
            message = payload["detail"]
    if message is None:
        message = (upstream.text or "").strip()[:2000] or f"HTTP {status}"
    if status in {401, 403}:
        if refused:
            message = f"The Excel backend refused {refused} ({status}): {message}"
        else:
            message = f"OpenAI rejected {login} ({status}): {message} {hint}".rstrip()
    headers = {}
    retry_after = upstream.headers.get("retry-after")
    if retry_after:
        headers["retry-after"] = retry_after
    return sse.openai_error_response(status, message, headers=headers or None)


def _error_text(response: Response) -> str:
    try:
        return str(json.loads(bytes(response.body))["error"]["message"])[:300]
    except (AttributeError, ValueError, KeyError, TypeError):
        return "no details"


def rate_limit_wait() -> float:
    """Seconds one request may spend waiting out the backend's rate limit (``EXCEL_BRIDGE_RATE_LIMIT_WAIT``)."""
    value = os.environ.get("EXCEL_BRIDGE_RATE_LIMIT_WAIT", "").strip()
    try:
        seconds = float(value) if value else RATE_LIMIT_WAIT
    except ValueError:
        return RATE_LIMIT_WAIT
    if math.isnan(seconds):
        return RATE_LIMIT_WAIT
    return min(max(seconds, 0.0), MAX_RATE_LIMIT_WAIT)


def _rate_limit_delay(message: str, attempt: int, left: float) -> float | None:
    """How long to wait before the next try; None once ``left`` is used up."""
    if left <= 0:
        return None
    advised = 0.0
    match = _TRY_AGAIN.search(message)
    if match:
        advised = float(match.group(1)) / (1000 if match.group(2).lower() == "ms" else 1)
    return min(max(advised, RATE_LIMIT_DELAYS[min(attempt, len(RATE_LIMIT_DELAYS) - 1)]), left)


def _stream_event(block: bytes) -> tuple[bool, str | None]:
    """(whether ``block`` only opens the stream, the rate limit's message if it fails on that)."""
    name, data = sse.parse_sse_block(block.decode("utf-8", "replace"))
    if data is None:
        return True, None
    try:
        payload = json.loads(data)
    except ValueError:
        return False, None
    if not isinstance(payload, dict):
        return False, None
    kind = str(name or payload.get("type") or "").strip().lower()
    if kind in _STREAM_OPENING:
        return True, None
    response = payload.get("response")
    error = response.get("error") if kind == "response.failed" and isinstance(response, dict) else None
    if isinstance(error, dict) and error.get("code") in RATE_LIMIT_CODES:
        return False, str(error.get("message") or error["code"])
    return False, None


def _refused(response: Response) -> bool:
    return response.status_code in PICTURE_RETRY_STATUSES


def _seconds(value: float | None) -> str:
    if value is None:
        return ""
    return f" within {value / 60:g} minutes" if value >= 120 else f" within {value:g} s"


def _request_error_response(exc: httpx.RequestError, timeout: httpx.Timeout) -> Response:
    """Say which step of reaching the backend failed: Codex shows only this text."""
    status, message = sse.upstream_request_error_status_and_message(exc)
    kind = type(exc).__name__
    detail = f"{kind}: {exc}" if str(exc).strip() else kind
    host = urlsplit(excel_upstream.RESPONSES_URL).hostname or "the Excel backend"
    check = "Check this computer's network or proxy (--proxy or EXCEL_BRIDGE_PROXY), then retry."
    if isinstance(exc, httpx.ConnectTimeout):
        message = f"Could not connect to {host}{_seconds(timeout.connect)} ({kind}). {check}"
    elif isinstance(exc, (httpx.ConnectError, httpx.ProxyError)):
        message = f"Could not connect to {host} ({detail}). {check}"
    elif isinstance(exc, httpx.WriteTimeout):
        message = f"Sending the request to {host} stalled ({kind}). {check}"
    elif isinstance(exc, httpx.PoolTimeout):
        message = (
            f"Too many requests to {host} were already running ({kind}). "
            "Retry; if this keeps happening, restart the bridge."
        )
    elif isinstance(exc, httpx.ReadTimeout):
        message = f"{host} took the request but sent nothing back{_seconds(timeout.read)} ({kind}). Retry."
    else:
        message = f"{message} ({detail})"
    log.warning("upstream request failed: %s", detail)
    return sse.openai_error_response(status, message)


class _PastRateLimits:
    """The bytes of a stream; while it fails on the rate limit before any answer, those of a later try.

    Codex gets the first try's opening events (``response.created``) at once,
    so the stream's keepalives run while the bridge waits, and never a second
    set.  At most ``wait`` seconds go on waiting; once they are used up, or a
    try cannot be sent, the last failure goes to Codex as it came.
    """

    def __init__(self, upstream: httpx.Response, send, wait: float) -> None:
        self.upstream = upstream
        self._send = send
        self._wait = wait
        self._closed = False
        self._chunks = self._relay()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        return await self._chunks.__anext__()

    async def aclose(self) -> None:
        """Stop, and send no further try, even while a keepalive's read of this waits in another task."""
        self._closed = True
        await self.upstream.aclose()
        with contextlib.suppress(RuntimeError):  # that read is running; it stops by itself now
            await self._chunks.aclose()

    async def _relay(self):
        waited = 0.0
        opened = False
        try:
            for attempt in itertools.count():
                chunks = self.upstream.aiter_bytes()
                forward_opening = not opened
                pending = b""
                first = None
                limited = None
                try:
                    async for chunk in chunks:
                        pending = (pending + chunk).replace(b"\r\n", b"\n")
                        while first is None and b"\n\n" in pending:
                            block, pending = pending.split(b"\n\n", 1)
                            block += b"\n\n"
                            opening, limited = _stream_event(block)
                            if not opening:
                                first = block
                            elif forward_opening:
                                opened = True
                                yield block
                        if first is not None or len(pending) > _STREAM_HEAD_LIMIT:
                            break
                except httpx.TransportError:
                    if pending:
                        yield pending
                    raise
                rest = (first or b"") + pending
                delay = None if limited is None else _rate_limit_delay(limited, attempt, self._wait - waited)
                if delay is None:
                    if rest:
                        yield rest
                    async for chunk in chunks:
                        yield chunk
                    return
                log.warning("the Excel backend is rate limited (%s); trying again in %g s", limited[:300], delay)
                await self.upstream.aclose()
                await asyncio.sleep(delay)
                waited += delay
                if self._closed:
                    return
                try:
                    retried = await self._send()
                except httpx.RequestError as exc:
                    log.warning("could not try again after the rate limit: %s", type(exc).__name__)
                    yield rest
                    return
                if self._closed or retried.status_code >= 400:
                    await retried.aclose()
                    if self._closed:
                        return
                    log.warning("trying again after the rate limit returned HTTP %s", retried.status_code)
                    yield rest
                    return
                self.upstream = retried
        finally:
            await self.upstream.aclose()


class Bridge:
    def __init__(self, reader: SessionReader, client_factory=build_upstream_client) -> None:
        self.reader = reader
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None
        self.pictures = images.Pictures()
        self.timezone = exit_timezone.ExitTimezone(lambda: self.client)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def aclose(self) -> None:
        await self.timezone.aclose()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def responses(self, body: dict) -> Response:
        model_id = _canonical_model(body.get("model"))
        if model_id is None:
            return sse.openai_error_response(
                400,
                f"Model {body.get('model')!r} is not served by this bridge. "
                f"Use one of: {', '.join(excel_upstream.MODEL_IDS)}.",
                code="model_not_found",
                param="model",
            )
        body = {**body, "model": model_id}
        return await self._signed_in(
            lambda headers: self._send_with_pictures(headers, body), stream=bool(body.get("stream"))
        )

    def _session_headers(self, *, stream: bool) -> dict | Response:
        self.reader.refresh()
        try:
            return self.reader.store.request_headers(stream=stream)
        except RuntimeError as exc:
            detail = f" ({self.reader.last_error})" if self.reader.last_error else ""
            return sse.openai_error_response(401, f"{exc}{detail} {self.reader.hint()}")

    async def _signed_in(self, send, *, stream: bool) -> Response:
        """``send(headers)`` with the sign-in in use; again with the next one if it is refused."""
        headers = self._session_headers(stream=stream)
        if isinstance(headers, Response):
            return headers
        used = self.reader.source
        response = await send(headers)
        if response.status_code not in LOGIN_REFUSED_STATUSES or not self.reader.fall_back(used):
            return response
        log.warning(
            "the Excel backend refused %s (HTTP %s: %s); using %s from now on",
            session.NAMES.get(used or "", "the sign-in"), response.status_code, _error_text(response),
            session.NAMES.get(self.reader.source or "", "the next sign-in"),
        )
        headers = self._session_headers(stream=stream)
        if isinstance(headers, Response):
            return response
        return await send(headers)

    def _rejected(self, upstream: httpx.Response, *, refused: str | None = None) -> Response:
        return _upstream_error_response(
            upstream,
            refused=refused,
            login=session.NAMES.get(self.reader.source or "", "the ChatGPT session"),
            hint=self.reader.hint(),
        )

    async def images(self, operation: str, body: dict) -> Response:
        """Codex's image tool: ``generations`` draws a new picture, ``edits`` changes given ones."""
        try:
            if operation == "generations":
                url, send = image_generation.GENERATIONS_URL, {"json": image_generation.generation_body(body)}
            else:
                data, files = image_generation.edit_form(body)
                url, send = image_generation.EDITS_URL, {"data": data, "files": files}
        except image_generation.Refused as exc:
            return sse.openai_error_response(400, str(exc))
        return await self._signed_in(
            lambda headers: self._draw(operation, url, send, headers), stream=False
        )

    async def _draw(self, operation: str, url: str, send: dict, headers: dict) -> Response:
        if "files" in send:
            headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
        model = (send.get("json") or send.get("data") or {}).get("model")
        try:
            upstream = await self.client.post(url, headers=headers, timeout=IMAGE_TIMEOUT, **send)
        except httpx.RequestError as exc:
            return _request_error_response(exc, IMAGE_TIMEOUT)
        if upstream.status_code >= 400:
            log.warning("image %s with %s: upstream returned HTTP %s", operation, model, upstream.status_code)
            # The session was just checked, so this is about pictures, not signing in.
            return _upstream_error_response(upstream, refused="the image request")
        try:
            payload = upstream.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return sse.openai_error_response(502, "The Excel backend's image answer was not JSON.")
        pictures = payload.get("data")
        log.info("image %s with %s: %d picture(s) came back",
                 operation, model, len(pictures) if isinstance(pictures, list) else 0)
        return JSONResponse(content=payload)

    async def _send_with_pictures(self, headers: dict, body: dict) -> Response:
        """Pictures go inline where the backend takes them, else uploaded, else left out."""
        sent = await self.pictures.rewrite(body, self.client, headers)
        response = await self._send(headers, sent.body, body)
        uploaded = set(sent.uploaded)
        while sent.pictures and _refused(response):
            omit = None
            if sent.reused:
                # Uploads from an earlier request may be gone by now.
                log.info(
                    "the backend refused pictures uploaded earlier (HTTP %s: %s); uploading them again",
                    response.status_code, _error_text(response),
                )
                self.pictures.forget(sent.reused)
            elif sent.inline:
                # One kind at a time, user messages first: they are known not to take them.
                kind = "message" if "message" in sent.inline else min(sent.inline)
                log.info(
                    "the backend refused a request with pictures inline (HTTP %s: %s); uploading those in %s",
                    response.status_code, _error_text(response), kind,
                )
                self.pictures.refuse_inline(kind)
            else:
                log.warning(
                    "the backend refused the request with its pictures (HTTP %s: %s); sending it without them",
                    response.status_code, _error_text(response),
                )
                omit = "the Excel backend did not accept it"
            sent = await self.pictures.rewrite(body, self.client, headers, omit=omit, fresh=uploaded)
            uploaded |= sent.uploaded
            response = await self._send(headers, sent.body, body)
        return response

    async def _send(self, headers: dict, body: dict, original: dict) -> Response:
        response = await self._send_once(headers, body, original)
        if response.status_code != 400 or "encrypted content" not in _error_text(response).lower():
            return response
        # A conversation that went on with the bridge off carries reasoning another
        # backend encrypted; without it the history still reads the same.
        kept = excel_upstream.without_encrypted_reasoning(body)
        if kept is body:
            return response
        log.warning(
            "the Excel backend could not read reasoning from another backend (%s); "
            "sending the conversation without it",
            _error_text(response),
        )
        return await self._send_once(headers, kept, original)

    async def _send_once(self, headers: dict, body: dict, original: dict) -> Response:
        zone = await self.timezone.current()
        if zone is not None:
            body = exit_timezone.rewrite_body(body, zone)
        upstream_body = excel_upstream.prepare_responses_body(
            body,
            tools_version_id=self.reader.store.tools_version_id(),
            # Turn identity must not depend on how pictures went in.
            identity_input=original.get("input"),
        )
        if upstream_body.get("stream"):
            return await self._stream(headers, upstream_body, body)
        return await self._non_stream(headers, upstream_body, body)

    async def _stream(self, headers: dict, upstream_body: dict, source_body: dict) -> Response:
        async def send() -> httpx.Response:
            request = self.client.build_request(
                "POST", excel_upstream.RESPONSES_URL, headers=headers, json=upstream_body
            )
            return await self.client.send(request, stream=True)

        try:
            upstream = await send()
        except httpx.RequestError as exc:
            return _request_error_response(exc, self.client.timeout)
        if upstream.status_code >= 400:
            try:
                await upstream.aread()
            finally:
                await upstream.aclose()
            log.warning("upstream returned HTTP %s", upstream.status_code)
            return self._rejected(upstream)

        transform = excel_tool_stream_transform(source_body)

        async def relay():
            source = _PastRateLimits(upstream, send, rate_limit_wait())
            try:
                chunks = transform(source) if transform is not None else source
                async for chunk in chunks:
                    yield chunk
            except httpx.TransportError as exc:
                # Basispoints sometimes breaks the connection after the final
                # event; if it happened earlier the client sees no
                # response.completed and retries on its own.
                log.warning("upstream stream ended abnormally: %s", type(exc).__name__)
            finally:
                await source.aclose()

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    async def _read_completed_payload(self, upstream: httpx.Response) -> dict | None:
        completed: dict | None = None
        try:
            async for event_name, data in sse.iter_sse_messages(upstream.aiter_bytes()):
                if not sse.is_response_completed_event(event_name, data):
                    continue
                try:
                    parsed = json.loads(data or "")
                except json.JSONDecodeError:
                    continue
                payload = parsed.get("response") if isinstance(parsed, dict) else None
                if isinstance(payload, dict):
                    completed = payload
        except httpx.RemoteProtocolError:
            # Malformed trailing chunk after the completed event is harmless.
            if completed is None:
                raise
        return completed

    async def _non_stream(self, headers: dict, upstream_body: dict, source_body: dict) -> Response:
        model_id = excel_upstream.excel_model_id(source_body.get("model")) or excel_upstream.MODEL_ID
        payload: dict | None = None
        for attempt in range(NON_STREAMING_ATTEMPTS):
            upstream = None
            try:
                request = self.client.build_request(
                    "POST", excel_upstream.RESPONSES_URL, headers=headers, json=upstream_body
                )
                upstream = await self.client.send(request, stream=True)
                if upstream.status_code >= 400:
                    await upstream.aread()
                    return self._rejected(upstream)
                if "text/event-stream" in upstream.headers.get("content-type", "").lower():
                    payload = await self._read_completed_payload(upstream)
                else:
                    await upstream.aread()
                    try:
                        parsed = upstream.json()
                    except json.JSONDecodeError:
                        parsed = None
                    payload = parsed if isinstance(parsed, dict) else None
            except httpx.RemoteProtocolError as exc:
                if attempt + 1 < NON_STREAMING_ATTEMPTS:
                    continue
                return _request_error_response(exc, self.client.timeout)
            except httpx.RequestError as exc:
                return _request_error_response(exc, self.client.timeout)
            finally:
                if upstream is not None:
                    await upstream.aclose()
            break

        if not isinstance(payload, dict):
            return sse.openai_error_response(
                502, "Upstream response did not include a completed Responses payload"
            )
        translated = dict(payload)
        translated["model"] = model_id
        marker_call = excel_upstream.extract_client_tool_call(
            sse.extract_response_output_text(payload) or "",
            excel_upstream.client_tool_types(source_body),
        )
        tool_calls = (
            [marker_call]
            if marker_call is not None
            else excel_upstream.extract_native_client_tool_calls(payload, source_body)
        )
        if tool_calls:
            translated = excel_upstream.response_payload_with_tool_calls(
                payload, tool_calls, model_id=model_id
            )
            sse.normalize_response_reasoning_for_client(translated)
        return JSONResponse(content=translated)


def create_app(reader: SessionReader | None = None, *, client_factory=build_upstream_client):
    """Build the ASGI app (wrapped in the loopback guard)."""
    bridge = Bridge(reader or SessionReader(), client_factory)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        await bridge.aclose()

    app = FastAPI(
        title="excel-codex-bridge",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.bridge = bridge

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "session": bridge.reader.refresh()}

    @app.get("/v1/models")
    @app.get("/models")
    async def models():
        return {
            "object": "list",
            "data": [
                {"id": model_id, "object": "model", "created": 0, "owned_by": "openai-excel"}
                for model_id in excel_upstream.MODEL_IDS
            ],
        }

    async def json_body(request: Request) -> dict | Response:
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return sse.openai_error_response(413, "Request body is too large")
        try:
            return _decode_body(raw, request.headers.get("content-encoding", ""))
        except (ValueError, OSError, zlib.error, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return sse.openai_error_response(400, f"Invalid request body: {exc}")

    @app.post("/v1/responses")
    @app.post("/responses")
    async def responses(request: Request):
        body = await json_body(request)
        return body if isinstance(body, Response) else await bridge.responses(body)

    @app.post("/v1/images/{operation}")
    @app.post("/images/{operation}")
    async def images_route(operation: str, request: Request):
        if operation not in {"generations", "edits"}:
            return sse.openai_error_response(404, f"No such image operation: {operation}")
        body = await json_body(request)
        return body if isinstance(body, Response) else await bridge.images(operation, body)

    return LocalOnly(app)
