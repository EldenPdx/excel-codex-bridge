import asyncio
import json

import httpx

from excel_codex_bridge.sub2api import GatewayKeys
from excel_codex_bridge.sub2api_delegate import DelegateConfig, create_delegate_app


def test_delegate_forwards_models_responses_and_errors_with_its_own_key():
    seen = []

    def upstream(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer " + "c" * 48
        assert request.headers["host"] == "api.itsukic.com"
        assert request.headers["x-excel-delegate-hop"] == "1"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-5.6-luna"}]})
        body = json.loads(request.content)
        if body["model"] == "quota":
            return httpx.Response(429, json={"error": {"message": "quota"}},
                                  headers={"retry-after": "60"})
        assert body["input"] == "hello"
        return httpx.Response(200, content=b"event: response.completed\ndata: {}\n\n",
                              headers={"content-type": "text/event-stream"})

    app = create_delegate_app(
        GatewayKeys("a" * 48, "b" * 48),
        config=DelegateConfig("http://caddy:8080/v1", "api.itsukic.com", "c" * 48),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )

    async def run():
        transport = httpx.ASGITransport(app=app, client=("172.22.0.2", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
            assert (await client.get("/v1/models")).status_code == 401
            headers = {"authorization": "Bearer " + "a" * 48, "user-agent": "codex-test"}
            models = await client.get("/v1/models?client_version=1", headers=headers)
            assert models.json()["data"][0]["id"] == "gpt-5.6-luna"
            assert seen[-1].headers["user-agent"] == "codex-test"
            assert not seen[-1].url.query
            result = await client.post("/v1/responses", headers=headers,
                                       json={"model": "gpt-5.6-luna", "input": "hello", "stream": True})
            assert result.status_code == 200
            assert result.headers["content-type"] == "text/event-stream"
            assert b"response.completed" in result.content
            limited = await client.post("/responses", headers=headers,
                                        json={"model": "quota", "input": "hello"})
            assert limited.status_code == 429
            assert limited.headers["retry-after"] == "60"
            count = len(seen)
            loop = await client.post("/responses", headers={**headers, "x-excel-delegate-hop": "1"},
                                     json={"model": "gpt-5.6-luna", "input": "hello"})
            assert loop.status_code == 508
            assert len(seen) == count
            assert (await client.post("/admin/session", headers={"authorization": "Bearer " + "b" * 48},
                                      json={"headers": {}})).status_code == 404

    asyncio.run(run())
