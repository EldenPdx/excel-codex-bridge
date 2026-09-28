from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import unittest
from unittest import mock

import httpx

from excel_codex_bridge import exit_timezone
from excel_codex_bridge.exit_timezone import ExitTimezone, Zone

from test_server import BridgeHarness, text_stream

# 23:30 in Shanghai, already the next day in Tokyo.
NOW = dt.datetime(2026, 9, 28, 15, 30, tzinfo=dt.timezone.utc)
TOKYO = Zone("Asia/Tokyo", 32400)
TRACE = "https://bps.openai.com/cdn-cgi/trace"


def context(zone: str = "Asia/Shanghai", date: str = "2026-09-28") -> str:
    return (
        "<environment_context>\n  <cwd>/work</cwd>\n  <shell>bash</shell>\n"
        f"  <current_date>{date}</current_date>\n  <timezone>{zone}</timezone>\n</environment_context>"
    )


def trace_answer(ip: str = "1.1.1.1", host: str = "bps.openai.com") -> httpx.Response:
    return httpx.Response(200, text=f"fl=1\nh={host}\nip={ip}\nts=1\n")


class Upstream:
    """Answers the trace and the lookups; ``ip`` and ``zones`` change what they say."""

    def __init__(self, ip: str = "1.1.1.1", zones: dict | None = None) -> None:
        self.ip = ip
        self.zones = {"1.1.1.1": "Asia/Tokyo"} if zones is None else zones
        self.trace_fails = False
        self.ipwho_fails = False
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == TRACE:
            return httpx.Response(503) if self.trace_fails else trace_answer(self.ip)
        ip = request.url.path.split("/")[1]
        if request.url.host == "ipwho.is":
            if self.ipwho_fails or ip not in self.zones:
                return httpx.Response(200, json={"ip": ip, "success": False, "message": "reserved range"})
            return httpx.Response(200, json={"ip": ip, "success": True,
                                             "timezone": {"id": self.zones[ip], "offset": 32400}})
        if request.url.host == "ipapi.co":
            if ip not in self.zones:
                return httpx.Response(429)
            return httpx.Response(200, json={"ip": ip, "timezone": self.zones[ip], "utc_offset": "+0900"})
        return httpx.Response(404)

    def lookups(self) -> int:
        return sum(url != TRACE for url in self.requests)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ParseTests(unittest.TestCase):
    def test_trace_gives_the_public_exit_ip(self):
        self.assertEqual(exit_timezone.parse_trace("h=bps.openai.com\nip=1.1.1.1\n", "bps.openai.com"),
                         "1.1.1.1")
        for text, host in (("h=example.com\nip=1.1.1.1", "bps.openai.com"),
                           ("h=bps.openai.com\nip=10.0.0.1", "bps.openai.com"),
                           ("h=bps.openai.com\nip=127.0.0.1", "bps.openai.com"),
                           ("h=bps.openai.com", "bps.openai.com")):
            with self.assertRaises(ValueError):
                exit_timezone.parse_trace(text, host)

    def test_trace_url_is_on_the_backend_host(self):
        self.assertEqual(exit_timezone.trace_url("https://bps.openai.com/basispoints/api/responses"), TRACE)
        self.assertEqual(exit_timezone.trace_url("http://127.0.0.1:9/basispoints/api/responses"),
                         "http://127.0.0.1:9/cdn-cgi/trace")

    def test_both_lookup_services_are_understood(self):
        ip = "1.1.1.1"
        self.assertEqual(exit_timezone.parse_geo({"ip": ip, "success": True,
                                                  "timezone": {"id": "Asia/Tokyo", "offset": 32400}}, ip), TOKYO)
        self.assertEqual(exit_timezone.parse_geo({"ip": ip, "timezone": "America/Los_Angeles",
                                                  "utc_offset": "-0700"}, ip),
                         Zone("America/Los_Angeles", -25200))
        self.assertEqual(exit_timezone.parse_geo({"ip": ip, "timezone": "America/Argentina/Buenos_Aires"}, ip).name,
                         "America/Argentina/Buenos_Aires")

    def test_doubtful_lookups_are_refused(self):
        ip = "1.1.1.1"
        for data in ({"ip": "9.9.9.9", "timezone": "Asia/Tokyo"}, {"ip": ip}, {"success": False},
                     {"ip": ip, "error": True, "timezone": "Asia/Tokyo"},
                     {"ip": ip, "timezone": "Asia/Tokyo</timezone><x>"}, {"ip": ip, "timezone": ""}, []):
            with self.assertRaises(ValueError):
                exit_timezone.parse_geo(data, ip)

    def test_lookup_urls_can_be_replaced(self):
        self.assertEqual(exit_timezone.lookup_urls("1.1.1.1"),
                         ["https://ipwho.is/1.1.1.1", "https://ipapi.co/1.1.1.1/json/"])
        with mock.patch.dict(os.environ, {exit_timezone.LOOKUP_ENV: "http://127.0.0.1:9/geo/{ip}"}):
            self.assertEqual(exit_timezone.lookup_urls("1.1.1.1"), ["http://127.0.0.1:9/geo/1.1.1.1"])


class RewriteTests(unittest.TestCase):
    def test_timezone_and_today_follow_the_exit(self):
        rewritten = exit_timezone.rewrite_text(context(), TOKYO, NOW)
        self.assertEqual(rewritten, context("Asia/Tokyo", "2026-09-29"))

    def test_same_day_keeps_the_date(self):
        morning = NOW.replace(hour=3)
        self.assertEqual(exit_timezone.rewrite_text(context(), TOKYO, morning), context("Asia/Tokyo"))

    def test_an_older_context_keeps_its_day(self):
        self.assertEqual(exit_timezone.rewrite_text(context(date="2026-09-20"), TOKYO, NOW),
                         context("Asia/Tokyo", "2026-09-20"))

    def test_without_tzdata_the_looked_up_offset_gives_the_day(self):
        self.assertEqual(exit_timezone.today_in("Nowhere/Unknown", 32400, NOW), "2026-09-29")
        self.assertIsNone(exit_timezone.today_in("Nowhere/Unknown", None, NOW))

    def test_text_outside_the_context_is_left_alone(self):
        text = "my <timezone>Europe/Paris</timezone> note"
        self.assertIs(exit_timezone.rewrite_text(text, TOKYO, NOW), text)
        mixed = f"{text}\n{context()}"
        self.assertEqual(exit_timezone.rewrite_text(mixed, TOKYO, NOW),
                         f"{text}\n{context('Asia/Tokyo', '2026-09-29')}")

    def test_body_is_copied_only_where_it_changes(self):
        tool_output = {"type": "function_call_output", "call_id": "c", "output": context()}
        assistant = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}
        user = {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": context()}, {"type": "input_image", "image_url": "data:x"}]}
        body = {"model": "m", "input": [user, tool_output, assistant]}
        before = json.dumps(body)
        rewritten = exit_timezone.rewrite_body(body, TOKYO, NOW)
        self.assertEqual(json.dumps(body), before)
        self.assertEqual(rewritten["input"][0]["content"][0]["text"], context("Asia/Tokyo", "2026-09-29"))
        self.assertIs(rewritten["input"][0]["content"][1], user["content"][1])
        self.assertIs(rewritten["input"][1], tool_output)
        self.assertIs(rewritten["input"][2], assistant)
        plain = {"model": "m", "input": [assistant]}
        self.assertIs(exit_timezone.rewrite_body(plain, TOKYO, NOW), plain)
        self.assertIs(exit_timezone.rewrite_body({"input": "text"}, TOKYO, NOW)["input"], "text")


class TrackerTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: "auto"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.upstream = Upstream()
        self.clock = Clock()

    def run_async(self, steps):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.upstream)) as client:
                tracker = ExitTimezone(lambda: client, trace=TRACE, clock=self.clock)
                return await steps(tracker)
        return asyncio.run(run())

    async def settle(self, tracker):
        """A request, then the zone once the check it started is done."""
        await tracker.current()
        if tracker._task is not None:
            await tracker._task
        return tracker.zone

    def test_first_request_waits_for_the_lookup(self):
        self.assertEqual(self.run_async(lambda tracker: tracker.current()), TOKYO)

    def test_off_looks_nothing_up(self):
        with mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: "off"}):
            self.assertIsNone(self.run_async(lambda tracker: tracker.current()))
        self.assertEqual(self.upstream.requests, [])

    def test_the_same_exit_is_looked_up_once(self):
        async def steps(tracker):
            await tracker.current()
            await tracker.current()
            self.clock.now += exit_timezone.CHECK_SECONDS
            return await self.settle(tracker)

        self.assertEqual(self.run_async(steps), TOKYO)
        self.assertEqual(self.upstream.requests.count(TRACE), 2)
        self.assertEqual(self.upstream.lookups(), 1)

    def test_a_stale_lookup_is_repeated(self):
        async def steps(tracker):
            await tracker.current()
            self.clock.now += exit_timezone.LOOKUP_SECONDS
            return await self.settle(tracker)

        self.assertEqual(self.run_async(steps), TOKYO)
        self.assertEqual(self.upstream.lookups(), 2)

    def test_a_new_exit_is_looked_up(self):
        self.upstream.zones["8.8.8.8"] = "Europe/Berlin"

        async def steps(tracker):
            await tracker.current()
            self.upstream.ip = "8.8.8.8"
            self.clock.now += exit_timezone.CHECK_SECONDS
            return await self.settle(tracker)

        self.assertEqual(self.run_async(steps).name, "Europe/Berlin")

    def test_a_new_exit_that_cannot_be_looked_up_sends_codex_own_timezone(self):
        async def steps(tracker):
            await tracker.current()
            self.upstream.ip = "8.8.8.8"
            self.clock.now += exit_timezone.CHECK_SECONDS
            return await self.settle(tracker)

        with self.assertLogs("excel_codex_bridge", "WARNING") as logs:
            self.assertIsNone(self.run_async(steps))
        self.assertIn("sending Codex's own timezone", logs.output[0])
        self.assertNotIn("8.8.8.8", logs.output[0])

    def test_a_failed_trace_keeps_the_timezone_and_retries_sooner(self):
        async def steps(tracker):
            await tracker.current()
            self.upstream.trace_fails = True
            self.clock.now += exit_timezone.CHECK_SECONDS
            zone = await self.settle(tracker)
            self.upstream.trace_fails = False
            self.clock.now += exit_timezone.RETRY_SECONDS
            await self.settle(tracker)
            return zone

        with self.assertLogs("excel_codex_bridge", "WARNING") as logs:
            self.assertEqual(self.run_async(steps), TOKYO)
        self.assertIn("still using Asia/Tokyo", logs.output[0])
        self.assertEqual(self.upstream.requests.count(TRACE), 3)

    def test_the_second_service_answers_when_the_first_does_not(self):
        self.upstream.ipwho_fails = True
        self.assertEqual(self.run_async(lambda tracker: tracker.current()), TOKYO)
        self.assertEqual(self.upstream.lookups(), 2)

    def test_a_slow_lookup_does_not_hold_the_first_request(self):
        async def steps(tracker):
            with mock.patch.object(exit_timezone, "FIRST_WAIT_SECONDS", 0.01), \
                 mock.patch.object(tracker, "_check", new=lambda: asyncio.sleep(1)):
                zone = await tracker.current()
                await tracker.aclose()
            return zone

        self.assertIsNone(self.run_async(steps))


class BridgeTests(unittest.TestCase):
    def body(self) -> dict:
        return {"model": "gpt-5.6-sol-excel", "stream": True, "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": context()}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ]}

    def send(self, mode: str) -> tuple[dict, list[str]]:
        upstream = Upstream()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/responses"):
                return httpx.Response(200, content=text_stream("ok"),
                                      headers={"content-type": "text/event-stream"})
            return upstream(request)

        harness = BridgeHarness(handler)
        with mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: mode}):
            response = harness.request("POST", "/v1/responses", json=self.body())
        self.assertEqual(response.status_code, 200)
        sent = [json.loads(r.content) for r in harness.upstream_requests if r.url.path.endswith("/responses")]
        return sent[-1], upstream.requests

    def test_the_exit_timezone_reaches_upstream_and_the_turn_keeps_its_identity(self):
        rewritten, lookups = self.send("auto")
        original, none = self.send("off")
        self.assertIn(TRACE, lookups)
        self.assertEqual(none, [])
        text = json.dumps(rewritten)
        self.assertIn("<timezone>Asia/Tokyo</timezone>", text)
        self.assertNotIn("Asia/Shanghai", text)
        self.assertIn("<timezone>Asia/Shanghai</timezone>", json.dumps(original))
        for key in ("task_id", "turn_id", "agent_iteration"):
            self.assertEqual(rewritten["metadata"][key], original["metadata"][key])


if __name__ == "__main__":
    unittest.main()
