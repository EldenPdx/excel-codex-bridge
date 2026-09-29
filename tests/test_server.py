from __future__ import annotations

import asyncio
import gzip
import json
import time
import unittest
import zlib
from unittest import mock

import httpx
import zstandard

from excel_codex_bridge import excel_upstream, server
from excel_codex_bridge.server import create_app
from excel_codex_bridge.session import SessionReader

from helpers import session_headers


class StaticReader(SessionReader):
    """A reader whose session is set by the test instead of read from Excel."""

    def refresh(self, *, force: bool = False):
        return self.status()


def sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def text_stream(text: str) -> bytes:
    return b"".join(
        [
            sse("response.created", {"type": "response.created", "response": {"id": "resp_1"}}),
            sse(
                "response.output_text.delta",
                {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0,
                 "content_index": 0, "delta": text},
            ),
            sse(
                "response.completed",
                {"type": "response.completed", "response": {
                    "id": "resp_1", "status": "completed", "model": "gpt-5.6-sol",
                    "output": [{"type": "message", "role": "assistant", "id": "msg_1",
                                "content": [{"type": "output_text", "text": text}]}],
                }},
            ),
        ]
    )


class BridgeHarness:
    def __init__(self, handler, *, configured: bool = True, exp: float | None = None,
                 timeout: httpx.Timeout = httpx.Timeout(5.0)):
        self.upstream_requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.upstream_requests.append(request)
            return handler(request)

        self.reader = StaticReader()
        if configured:
            self.reader.store.configure(
                session_headers(exp), persist=False, allow_expired=True
            )
        self.app = create_app(
            self.reader,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(record), timeout=timeout),
        )

    def request(self, method: str, path: str, *, client_host="127.0.0.1", **kwargs) -> httpx.Response:
        async def run():
            transport = httpx.ASGITransport(app=self.app, client=(client_host, 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(run())

    def upstream_json(self, index: int = -1) -> dict:
        return json.loads(self.upstream_requests[index].content)


def ok_stream(_request):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text_stream("pong"))


class LocalOnlyGuardTests(unittest.TestCase):
    def test_healthz_reports_session_without_secrets(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request("GET", "/healthz")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["session"]["configured"])
        self.assertNotIn("Bearer", response.text)
        self.assertNotIn("account-id", response.text)

    def test_non_loopback_client_is_refused(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request("GET", "/healthz", client_host="192.168.1.20")
        self.assertEqual(response.status_code, 403)

    def test_foreign_host_header_is_refused(self):
        # A DNS-rebinding page reaches 127.0.0.1 but carries its own Host.
        harness = BridgeHarness(ok_stream)
        response = harness.request("GET", "/healthz", headers={"host": "evil.example:8765"})
        self.assertEqual(response.status_code, 403)

    def test_localhost_and_ipv6_host_headers_are_accepted(self):
        harness = BridgeHarness(ok_stream)
        for host in ("localhost:8765", "[::1]:8765", "127.0.0.1"):
            with self.subTest(host=host):
                response = harness.request("GET", "/healthz", headers={"host": host})
                self.assertEqual(response.status_code, 200)

    def test_browser_origin_is_refused(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request(
            "POST", "/v1/responses",
            headers={"origin": "https://example.com"},
            json={"model": "gpt-5.6-sol-excel", "input": "hi"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(harness.upstream_requests, [])

    def test_unknown_routes_are_not_served(self):
        harness = BridgeHarness(ok_stream)
        self.assertEqual(harness.request("POST", "/v1/chat/completions", json={}).status_code, 404)
        self.assertEqual(harness.request("GET", "/docs").status_code, 404)


class SharedProviderTests(unittest.TestCase):
    """Codex's own ``openai`` provider pointed at the bridge (``openai_base_url``)."""

    UPGRADE = {"upgrade": "websocket", "connection": "Upgrade", "sec-websocket-version": "13",
               "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="}

    def test_websocket_upgrade_is_answered_426_so_codex_uses_http(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request("GET", "/v1/responses", headers=self.UPGRADE)
        self.assertEqual(response.status_code, 426)
        self.assertEqual(harness.upstream_requests, [])

    def test_websocket_scope_is_denied(self):
        harness = BridgeHarness(ok_stream)

        def run(extensions):
            sent = []

            async def receive():
                return {"type": "websocket.connect"}

            async def send(message):
                sent.append(message)

            scope = {"type": "websocket", "path": "/v1/responses", "client": ("127.0.0.1", 50000),
                     "headers": [(b"host", b"127.0.0.1:8765")], "extensions": extensions}
            asyncio.run(harness.app(scope, receive, send))
            return sent

        sent = run({"websocket.http.response": {}})
        self.assertEqual((sent[0]["type"], sent[0]["status"]), ("websocket.http.response.start", 426))
        self.assertEqual(run({})[0]["type"], "websocket.close")

    def test_codex_own_sign_in_stays_here(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request(
            "POST", "/v1/responses",
            headers={"authorization": "Bearer codex-own-token", "chatgpt-account-id": "codex-own-account"},
            json={"model": "gpt-6-sol", "input": "hi", "stream": True},
        )
        self.assertEqual(response.status_code, 200)
        sent = harness.upstream_requests[0].headers
        self.assertNotIn("codex-own-token", sent.get("authorization", ""))
        self.assertNotEqual(sent.get("chatgpt-account-id"), "codex-own-account")

    def conversation(self):
        return {
            "model": "gpt-6-luna", "stream": True,
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                {"type": "reasoning", "summary": [], "encrypted_content": "gAAAA-from-another-backend"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "again"}]},
            ],
        }

    def test_reasoning_another_backend_encrypted_is_left_out_once(self):
        answers = iter([
            httpx.Response(400, json={"error": {
                "message": "The encrypted content for item rs_1 could not be verified.",
                "code": "invalid_encrypted_content"}}),
            ok_stream(None),
        ])
        harness = BridgeHarness(lambda _r: next(answers))
        with self.assertLogs("excel_codex_bridge", "WARNING"):
            response = harness.request("POST", "/v1/responses", json=self.conversation())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(harness.upstream_requests), 2)
        kinds = [[item.get("type") for item in harness.upstream_json(i)["input"]] for i in (0, 1)]
        self.assertIn("reasoning", kinds[0])
        self.assertNotIn("reasoning", kinds[1])
        texts = json.dumps(harness.upstream_json(1)["input"])
        self.assertIn("hello", texts)
        self.assertIn("again", texts)

    def test_other_400s_are_not_retried(self):
        harness = BridgeHarness(lambda _r: httpx.Response(400, json={"error": {"message": "bad input"}}))
        response = harness.request("POST", "/v1/responses", json=self.conversation())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(harness.upstream_requests), 1)

    def test_no_reasoning_means_no_retry(self):
        body = self.conversation()
        body["input"] = [item for item in body["input"] if item["type"] != "reasoning"]
        harness = BridgeHarness(lambda _r: httpx.Response(400, json={"error": {
            "message": "Encrypted content could not be decrypted."}}))
        response = harness.request("POST", "/v1/responses", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(harness.upstream_requests), 1)


class ResponsesRouteTests(unittest.TestCase):
    def test_non_excel_model_is_rejected_before_upstream(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request("POST", "/v1/responses", json={"model": "gpt-4o", "input": "hi"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "model_not_found")
        self.assertEqual(harness.upstream_requests, [])

    def test_missing_session_returns_401_with_refresh_hint(self):
        harness = BridgeHarness(ok_stream, configured=False)
        response = harness.request(
            "POST", "/v1/responses", json={"model": "gpt-5.6-sol-excel", "input": "hi", "stream": True}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("ChatGPT add-in pane", response.json()["error"]["message"])
        self.assertEqual(harness.upstream_requests, [])

    def test_expired_session_returns_401(self):
        harness = BridgeHarness(ok_stream, exp=time.time() - 60)
        response = harness.request(
            "POST", "/v1/responses", json={"model": "gpt-5.6-sol-excel", "input": "hi", "stream": True}
        )
        self.assertEqual(response.status_code, 401)

    def test_stream_is_relayed_with_excel_wire_shape(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request(
            "POST", "/v1/responses",
            json={"model": "gpt-5.6-terra-excel", "input": "ping", "stream": True,
                  "reasoning": {"effort": "high"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn('"delta": "pong"', response.text)
        self.assertIn("response.completed", response.text)

        upstream = harness.upstream_requests[0]
        self.assertEqual(str(upstream.url), excel_upstream.RESPONSES_URL)
        self.assertTrue(upstream.headers["authorization"].startswith("Bearer "))
        self.assertEqual(upstream.headers["chatgpt-account-id"], "account-id")
        body = harness.upstream_json()
        self.assertEqual(body["model"], "gpt-5.6-terra")
        self.assertEqual(body["model_selection"], "explicit")
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertIs(body["stream"], True)

    def test_model_without_excel_suffix_is_accepted(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request(
            "POST", "/responses", json={"model": "gpt-5.6-luna", "input": "ping", "stream": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(harness.upstream_json()["model"], "gpt-5.6-luna")

    def test_astra_alias_reaches_upstream_as_gpt_6_astra(self):
        harness = BridgeHarness(ok_stream)
        for model in ("gpt-6-astra-excel", "gpt-6-astra"):
            with self.subTest(model=model):
                response = harness.request(
                    "POST", "/v1/responses", json={"model": model, "input": "ping", "stream": True}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(harness.upstream_json()["model"], "gpt-6-astra")

    def test_gpt_6_sol_and_luna_aliases_reach_upstream(self):
        harness = BridgeHarness(ok_stream)
        for model, upstream in (
            ("gpt-6-sol-excel", "gpt-6-sol"), ("gpt-6-sol", "gpt-6-sol"),
            ("gpt-6-luna-excel", "gpt-6-luna"), ("gpt-6-luna", "gpt-6-luna"),
            ("gpt-6-sol-1m-excel", "gpt-6-sol"), ("gpt-6-sol-1m", "gpt-6-sol"),
            ("gpt-5.6-luna-1m-excel", "gpt-5.6-luna"), ("gpt-5.6-luna-1m", "gpt-5.6-luna"),
        ):
            with self.subTest(model=model):
                response = harness.request(
                    "POST", "/v1/responses", json={"model": model, "input": "ping", "stream": True}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(harness.upstream_json()["model"], upstream)

    def test_compressed_request_bodies_are_decoded(self):
        payload = json.dumps({"model": "gpt-5.6-sol-excel", "input": "ping", "stream": True}).encode()
        for encoding, data in (
            ("gzip", gzip.compress(payload)),
            ("zstd", zstandard.ZstdCompressor().compress(payload)),
        ):
            with self.subTest(encoding=encoding):
                harness = BridgeHarness(ok_stream)
                response = harness.request(
                    "POST", "/v1/responses", content=data,
                    headers={"content-encoding": encoding, "content-type": "application/json"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(harness.upstream_json()["model"], "gpt-5.6-sol")

    def test_bodies_past_64_mb_go_through(self):
        # A long conversation with its pictures passes 64 MB before Codex compacts it.
        payload = json.dumps({"model": "gpt-5.6-sol-excel", "input": "x" * (70 * 1024 * 1024),
                              "stream": True}).encode()
        for encoding, data in (
            ("zstd", zstandard.ZstdCompressor().compress(payload)),
            ("gzip", gzip.compress(payload, compresslevel=1)),
            ("identity", payload),
        ):
            with self.subTest(encoding=encoding):
                harness = BridgeHarness(ok_stream)
                response = harness.request(
                    "POST", "/v1/responses", content=data,
                    headers={"content-encoding": encoding, "content-type": "application/json"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(harness.upstream_requests[0].content), 70 * 1024 * 1024)

    def test_bodies_past_the_limit_are_a_413_decompressed_or_not(self):
        payload = json.dumps({"model": "gpt-5.6-sol-excel", "input": "x" * 4096, "stream": True}).encode()
        for encoding, data in (
            ("zstd", zstandard.ZstdCompressor().compress(payload)),
            ("gzip", gzip.compress(payload)),
            ("deflate", zlib.compress(payload)),
            ("identity", payload),
        ):
            with self.subTest(encoding=encoding), mock.patch.object(server, "MAX_BODY_BYTES", 1024):
                if encoding != "identity":
                    self.assertLess(len(data), 1024)
                harness = BridgeHarness(ok_stream)
                response = harness.request("POST", "/v1/responses", content=data,
                                           headers={"content-encoding": encoding})
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()["error"]["message"],
                                 "Request body is too large (over 1024 bytes decompressed)")
                self.assertEqual(harness.upstream_requests, [])

    def test_a_cut_off_compressed_body_is_a_400(self):
        payload = json.dumps({"model": "gpt-5.6-sol-excel", "input": "ping" * 1000}).encode()
        for encoding, data in (
            ("zstd", zstandard.ZstdCompressor().compress(payload)),
            ("gzip", gzip.compress(payload)),
        ):
            with self.subTest(encoding=encoding):
                harness = BridgeHarness(ok_stream)
                response = harness.request("POST", "/v1/responses", content=data[: len(data) // 2],
                                           headers={"content-encoding": encoding})
                self.assertEqual(response.status_code, 400)
                self.assertIn("Invalid request body", response.json()["error"]["message"])

    def test_invalid_json_is_a_400(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request(
            "POST", "/v1/responses", content=b"{not json", headers={"content-type": "application/json"}
        )
        self.assertEqual(response.status_code, 400)

    def test_upstream_401_carries_refresh_hint(self):
        harness = BridgeHarness(
            lambda _r: httpx.Response(401, json={"error": {"message": "token revoked"}})
        )
        response = harness.request(
            "POST", "/v1/responses", json={"model": "gpt-5.6-sol-excel", "input": "hi", "stream": True}
        )
        self.assertEqual(response.status_code, 401)
        message = response.json()["error"]["message"]
        self.assertIn("token revoked", message)
        self.assertIn("ChatGPT add-in pane", message)

    def test_upstream_429_keeps_status_and_retry_after(self):
        harness = BridgeHarness(
            lambda _r: httpx.Response(429, headers={"retry-after": "30"}, text="slow down")
        )
        response = harness.request(
            "POST", "/v1/responses", json={"model": "gpt-5.6-sol-excel", "input": "hi", "stream": True}
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "30")
        self.assertEqual(response.json()["error"]["message"], "slow down")

    def test_upstream_connection_failure_is_a_502(self):
        def refuse(request):
            raise httpx.ConnectError("refused", request=request)

        harness = BridgeHarness(refuse)
        response = harness.request(
            "POST", "/v1/responses", json={"model": "gpt-5.6-sol-excel", "input": "hi", "stream": True}
        )
        self.assertEqual(response.status_code, 502)
        self.assertIn("Could not connect to bps.openai.com (ConnectError: refused)", response.json()["error"]["message"])

    def test_upstream_timeouts_say_which_step_timed_out(self):
        cases = {
            httpx.ConnectTimeout: "Could not connect to bps.openai.com within 30 s (ConnectTimeout). Check",
            httpx.ReadTimeout: "sent nothing back within 10 minutes (ReadTimeout)",
            httpx.PoolTimeout: "Too many requests",
            httpx.WriteTimeout: "stalled (WriteTimeout). Check",
        }
        for error, expected in cases.items():
            for stream in (True, False):
                with self.subTest(error=error.__name__, stream=stream):
                    def fail(request, error=error):
                        raise error("", request=request)

                    harness = BridgeHarness(
                        fail, timeout=httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0)
                    )
                    response = harness.request(
                        "POST", "/v1/responses",
                        json={"model": "gpt-5.6-sol-excel", "input": "hi", "stream": stream},
                    )
                    self.assertEqual(response.status_code, 504)
                    self.assertIn(expected, response.json()["error"]["message"])

    def test_non_streaming_request_returns_completed_payload(self):
        harness = BridgeHarness(ok_stream)
        response = harness.request(
            "POST", "/v1/responses", json={"model": "gpt-5.6-sol-excel", "input": "ping"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model"], "gpt-5.6-sol-excel")
        self.assertEqual(body["output"][0]["content"][0]["text"], "pong")

    def test_models_lists_only_excel_aliases(self):
        harness = BridgeHarness(ok_stream)
        ids = [item["id"] for item in harness.request("GET", "/v1/models").json()["data"]]
        self.assertEqual(sorted(ids), sorted(excel_upstream.MODEL_IDS))


if __name__ == "__main__":
    unittest.main()
