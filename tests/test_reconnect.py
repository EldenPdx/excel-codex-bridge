"""The bridge goes on trying to reach the backend when the network or proxy drops out.

Codex tries a failed request five times within seconds and then gives up on
the turn; a proxy node that goes away for half a minute fails all of them.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

import httpx

from excel_codex_bridge import server
from excel_codex_bridge.server import create_app

from helpers import session_headers
from test_rate_limit_wait import event_names, events
from test_server import StaticReader, text_stream

TOOLS = [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}]


class Upstream:
    """Answers each request with the next of ``answers`` (an exception is raised), the last one from then on."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        if isinstance(answer, type) and issubclass(answer, Exception):
            raise answer("no route to the proxy node", request=request)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=answer)


def ask(upstream: Upstream, *, stream: bool = True, tools: bool = True, wait: str = "30",
        hold: float = 5.0, keepalive: float | None = None, delays=(0.01,)) -> httpx.Response:
    reader = StaticReader()
    reader.store.configure(session_headers(None), persist=False, allow_expired=True)
    app = create_app(reader, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    body = {"model": "gpt-6-astra-excel", "input": "ping", "stream": stream, **({"tools": TOOLS} if tools else {})}

    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            return await client.post("/v1/responses", json=body)

    patches = [
        mock.patch.dict(os.environ, {"EXCEL_BRIDGE_CONNECT_WAIT": wait}),
        mock.patch.object(server, "CONNECT_DELAYS", delays),
        mock.patch.object(server, "CONNECT_HOLD", hold),
    ]
    if keepalive is not None:
        patches.append(mock.patch.object(server, "RATE_LIMIT_KEEPALIVE", keepalive))
    for patch in patches:
        patch.start()
    try:
        return asyncio.run(go())
    finally:
        for patch in reversed(patches):
            patch.stop()


class ReconnectTests(unittest.TestCase):
    def test_a_short_drop_is_tried_again_before_codex_hears_anything(self):
        for error in (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError, httpx.RemoteProtocolError):
            with self.subTest(error=error.__name__):
                upstream = Upstream(error, error, text_stream("pong"))
                response = ask(upstream)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(upstream.requests), 3)
                self.assertEqual(upstream.requests[0].content, upstream.requests[2].content)
                names = event_names(response.text)
                self.assertEqual(names.count("response.created"), 1)
                self.assertIn("resp_1", response.text.split("\n\n")[0])
                self.assertEqual(names[-1], "response.completed")

    def test_a_longer_drop_opens_the_stream_and_waits_with_codex(self):
        for tools in (True, False):
            with self.subTest(tools=tools):
                upstream = Upstream(httpx.ConnectError, httpx.ConnectError, httpx.ConnectError, text_stream("pong"))
                response = ask(upstream, tools=tools, hold=0)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(upstream.requests), 4)
                names = event_names(response.text)
                self.assertEqual(names[0], "response.created")
                # Codex hears of one response only: the backend's own opening is not passed on.
                self.assertEqual(names.count("response.created"), 1)
                self.assertTrue(events(response.text)[0]["response"]["id"].startswith("resp_"))
                self.assertEqual(events(response.text)[0]["response"]["model"], "gpt-6-astra")
                self.assertNotIn("response.failed", names)
                self.assertEqual(names[-1], "response.completed")
                self.assertIn("pong", response.text)

    def test_the_stream_says_it_is_still_going_while_it_waits(self):
        upstream = Upstream(httpx.ConnectError, httpx.ConnectError, text_stream("pong"))
        response = ask(upstream, tools=False, hold=0, keepalive=0.01, delays=(0.05,))
        names = event_names(response.text)
        self.assertGreater(names.count("response.in_progress"), 1)
        self.assertEqual(names[-1], "response.completed")

    def test_once_the_wait_is_used_up_codex_is_told_so_and_not_to_try_again(self):
        upstream = Upstream(httpx.ConnectError)
        response = ask(upstream, tools=False, wait="0.1", hold=0)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(upstream.requests), 2)
        self.assertEqual(event_names(response.text)[0], "response.created")
        self.assertEqual(event_names(response.text)[-1], "response.failed")
        failed = events(response.text)[-1]["response"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["id"], events(response.text)[0]["response"]["id"])
        # Codex would try five more times, each waited out afresh; not under this code.
        self.assertEqual(failed["error"]["code"], "invalid_prompt")
        message = failed["error"]["message"]
        self.assertIn("Still no connection after", message)
        self.assertIn("Could not connect to bps.openai.com (ConnectError: no route to the proxy node)", message)
        self.assertIn("EXCEL_BRIDGE_PROXY", message)

    def test_an_error_answer_after_the_drop_goes_to_codex_to_send_again(self):
        upstream = Upstream(httpx.ConnectError, httpx.Response(401, json={"error": {"message": "token revoked"}}))
        response = ask(upstream, tools=False, hold=0)
        self.assertEqual(event_names(response.text), ["response.created", "response.failed"])
        error = events(response.text)[-1]["response"]["error"]
        self.assertEqual(error["code"], "upstream_error")
        self.assertIn("token revoked", error["message"])

    def test_failures_after_the_backend_answered_are_not_sent_again(self):
        upstream = Upstream(httpx.ReadTimeout)
        response = ask(upstream)
        self.assertEqual(response.status_code, 504)
        self.assertEqual(len(upstream.requests), 1)

    def test_no_second_try_when_it_is_turned_off(self):
        upstream = Upstream(httpx.ConnectError, text_stream("pong"))
        response = ask(upstream, wait="0")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(upstream.requests), 1)

    def test_requests_without_a_stream_are_tried_again_too(self):
        upstream = Upstream(httpx.ConnectError, httpx.ConnectError, text_stream("pong"))
        response = ask(upstream, stream=False, hold=0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(upstream.requests), 3)
        self.assertIn("pong", json.dumps(response.json()))

    def test_the_wait_setting(self):
        for value, expected in (("", 120.0), ("45", 45.0), ("-3", 0.0), ("nan", 120.0), ("x", 120.0),
                                ("99999", server.MAX_CONNECT_WAIT)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"EXCEL_BRIDGE_CONNECT_WAIT": value}):
                self.assertEqual(server.connect_wait(), expected)


if __name__ == "__main__":
    unittest.main()
