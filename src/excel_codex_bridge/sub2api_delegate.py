"""Optional sidecar mode that forwards Responses to a separate SUB2API group."""

from __future__ import annotations

import contextlib
import os
import zlib
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import sse
from .server import MAX_BODY_BYTES
from .sub2api import BodyTooLarge, GatewayGuard, GatewayKeys, SESSION_PATH, read_json, read_secret


@dataclass(frozen=True)
class DelegateConfig:
    url: str
    host: str
    key: str = field(repr=False)

    @classmethod
    def from_env(cls):
        url = os.environ["EXCEL_SUB2API_DELEGATE_URL"].rstrip("/")
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.path != "/v1" or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError("EXCEL_SUB2API_DELEGATE_URL must be an absolute /v1 URL")
        host = os.environ.get("EXCEL_SUB2API_DELEGATE_HOST", "").strip()
        if any(char in host for char in "\r\n\x00/ "):
            raise ValueError("Invalid EXCEL_SUB2API_DELEGATE_HOST")
        return cls(url, host, read_secret("EXCEL_SUB2API_DELEGATE_KEY"))


def create_delegate_app(keys: GatewayKeys, *, config: DelegateConfig | None = None,
                        client_factory=None):
    config = config or DelegateConfig.from_env()
    client = (client_factory() if client_factory else httpx.AsyncClient(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(connect=10, read=None, write=60, pool=10),
    ))

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="excel-sub2api-delegate", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    async def forward(request: Request, path: str, body=None):
        if request.headers.get("x-excel-delegate-hop"):
            return sse.openai_error_response(508, "Delegate routing loop detected")
        headers = {
            "authorization": "Bearer " + config.key,
            "x-excel-delegate-hop": "1",
            "accept": request.headers.get("accept", "application/json"),
            "user-agent": request.headers.get("user-agent", "excel-sub2api-delegate/1"),
        }
        if config.host:
            headers["host"] = config.host
        try:
            upstream_request = client.build_request(
                request.method, config.url + path, headers=headers,
                json=body if body is not None else None,
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.RequestError:
            return sse.openai_error_response(502, "Delegate upstream unavailable")
        if 300 <= upstream.status_code < 400:
            await upstream.aclose()
            return sse.openai_error_response(502, "Delegate upstream redirected")

        async def chunks():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        response_headers = {
            name: upstream.headers[name]
            for name in ("content-type", "retry-after", "x-request-id")
            if name in upstream.headers
        }
        return StreamingResponse(chunks(), status_code=upstream.status_code,
                                 headers=response_headers)

    @app.get("/v1/models")
    @app.get("/models")
    async def models(request: Request):
        # The outer gateway expects a standard model list, even for Codex clients.
        return await forward(request, "/models")

    @app.post("/v1/responses")
    @app.post("/responses")
    async def responses(request: Request):
        try:
            body = await read_json(request, MAX_BODY_BYTES)
        except BodyTooLarge:
            return sse.openai_error_response(413, "Request body is too large")
        except (ValueError, zlib.error, RecursionError):
            return sse.openai_error_response(400, "Invalid request body")
        if "stream" in body and not isinstance(body["stream"], bool):
            return sse.openai_error_response(400, "stream must be a boolean")
        return await forward(request, "/responses", body)

    @app.get(SESSION_PATH)
    async def get_session():
        return {"configured": True, "mode": "sub2api-delegate", "expired": False}

    @app.post(SESSION_PATH)
    @app.delete(SESSION_PATH)
    async def reject_session():
        return JSONResponse({"error": "Session import is unavailable in delegate mode"}, status_code=409)

    return GatewayGuard(app, keys)
