from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from excel_codex_bridge import cli, exit_timezone, system_timezone
from excel_codex_bridge.exit_timezone import Exit
from excel_codex_bridge.system_timezone import Refused, Route

CHATGPT = Route("ChatGPT sign-in", "chatgpt.com", "http://127.0.0.1:7890")


class Folder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = mock.patch.dict(os.environ, {
            "EXCEL_BRIDGE_HOME": str(self.root / "bridge"),
            "CODEX_HOME": str(self.root / "codex"),
            "EXCEL_BRIDGE_CODEX_AUTH": str(self.root / "codex" / "auth.json"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / "codex").mkdir()


class RouteTests(Folder):
    def route(self, config: str = "", auth: dict | None = None) -> Route:
        (self.root / "codex" / "config.toml").write_text(config, encoding="utf-8")
        if auth is not None:
            (self.root / "codex" / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
        with mock.patch.object(system_timezone, "codex_proxy", return_value=None):
            return system_timezone.codex_route()

    def test_chatgpt_sign_in_follows_chatgpt_exit(self):
        self.assertEqual(self.route(auth={"auth_mode": "chatgpt", "tokens": {}}).host, "chatgpt.com")
        self.assertEqual(self.route().host, "chatgpt.com")

    def test_api_key_follows_the_api_exit(self):
        self.assertEqual(self.route(auth={"auth_mode": "apikey"}).host, "api.openai.com")
        self.assertEqual(self.route(auth={"OPENAI_API_KEY": "k"}).host, "api.openai.com")

    def test_other_providers_and_tables_do_not_count_as_api_key(self):
        self.assertEqual(self.route('model_provider = "excel-bridge"\n', {"auth_mode": "apikey"}).host,
                         "chatgpt.com")
        self.assertEqual(self.route('[profiles.x]\nmodel_provider = "kimi"\n', {"auth_mode": "apikey"}).host,
                         "api.openai.com")

    def test_codex_proxy_comes_from_the_environment_first(self):
        clean = {name: "" for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")}
        # Set after the clean-up: on Windows all_proxy and ALL_PROXY are one variable.
        with mock.patch.dict(os.environ, clean), mock.patch.dict(os.environ, {"ALL_PROXY": "127.0.0.1:1081"}), \
             mock.patch.object(system_timezone, "_registry_proxy", return_value="http://127.0.0.1:1082"):
            self.assertEqual(system_timezone.codex_proxy(), "http://127.0.0.1:1081")
        with mock.patch.dict(os.environ, clean), \
             mock.patch.object(system_timezone, "_registry_proxy", return_value="http://127.0.0.1:1082"):
            self.assertEqual(system_timezone.codex_proxy(), "http://127.0.0.1:1082")

    def test_windows_proxy_setting(self):
        self.assertEqual(system_timezone.windows_proxy("127.0.0.1:7890"), "http://127.0.0.1:7890")
        self.assertEqual(system_timezone.windows_proxy("http=127.0.0.1:1;https=127.0.0.1:2"), "http://127.0.0.1:2")
        self.assertEqual(system_timezone.windows_proxy("socks=127.0.0.1:3"), "socks5h://127.0.0.1:3")
        self.assertIsNone(system_timezone.windows_proxy(""))


class LookupTests(unittest.TestCase):
    def client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_exit_ip_and_lookup(self):
        def handler(request):
            if request.url.path == "/cdn-cgi/trace":
                return httpx.Response(200, text="h=chatgpt.com\nip=1.1.1.1\n")
            if request.url.host == "ipwho.is":
                return httpx.Response(200, json={"success": False})
            return httpx.Response(200, json={"ip": "1.1.1.1", "timezone": "Asia/Tokyo", "utc_offset": "+0900"})

        with self.client(handler) as client:
            self.assertEqual(system_timezone.find_exit(client, "chatgpt.com"), Exit("1.1.1.1"))
            self.assertEqual(system_timezone.lookup(client, Exit("1.1.1.1")).name, "Asia/Tokyo")

    def test_only_a_lookup_that_agrees_with_cloudflare_is_believed(self):
        answers = {
            "ipwho.is": {"success": True, "ip": "1.1.1.1", "country_code": "TW", "timezone": {"id": "Asia/Taipei"}},
            "ipapi.co": {"ip": "1.1.1.1", "timezone": "America/Chicago"},
            "get.geojs.io": {"ip": "1.1.1.1", "country_code": "US", "timezone": "America/Los_Angeles"},
        }

        def handler(request):
            if request.url.path == "/cdn-cgi/trace":
                return httpx.Response(200, text="h=chatgpt.com\nip=1.1.1.1\nloc=US\n")
            return httpx.Response(200, json=answers[request.url.host])

        with self.client(handler) as client:
            exit = system_timezone.find_exit(client, "chatgpt.com")
            self.assertEqual(exit, Exit("1.1.1.1", "US"))
            self.assertEqual(system_timezone.lookup(client, exit).name, "America/Los_Angeles")
            answers["get.geojs.io"]["country_code"] = "TW"
            answers["api.ip.sb"] = {"ip": "1.1.1.1", "country_code": "TW", "timezone": "Asia/Taipei"}
            with self.assertRaises(Refused) as caught:
                system_timezone.lookup(client, exit)
        message = str(caught.exception)
        self.assertIn("which Cloudflare places in US,", message)
        self.assertIn("ipwho.is: says TW (Asia/Taipei), but Cloudflare sees the exit in US", message)
        self.assertIn("ipapi.co: says America/Chicago, no country", message)
        self.assertIn("left as it is", message)

    def test_plain_http_is_only_for_loopback(self):
        with self.client(lambda request: httpx.Response(200, text="h=example.com\nip=1.1.1.1")) as client:
            with mock.patch.dict(os.environ, {system_timezone.TRACE_ENV: "http://example.com/cdn-cgi/trace"}):
                with self.assertRaises(Refused):
                    system_timezone.find_exit(client, "chatgpt.com")
        with self.client(lambda request: httpx.Response(200, text="h=127.0.0.1\nip=1.1.1.1")) as client:
            with mock.patch.dict(os.environ, {system_timezone.TRACE_ENV: "http://127.0.0.1:9/cdn-cgi/trace"}):
                self.assertEqual(system_timezone.find_exit(client, "chatgpt.com").ip, "1.1.1.1")

    def test_all_lookups_failing_says_why(self):
        with self.client(lambda request: httpx.Response(429)) as client:
            with self.assertRaises(Refused) as caught:
                system_timezone.lookup(client, Exit("1.1.1.1"))
        self.assertIn("ipwho.is: HTTP 429", str(caught.exception))


class SyncTests(Folder):
    def run_sync(self, *, probe=False, routes=None, ips=None, current="China Standard Time", lookup_fails=False,
                 stopped=None, settle=None, zone="Taipei Standard Time", automatic=None):
        self.lookups = 0

        def lookup(client, exit):
            self.lookups += 1
            if lookup_fails:
                raise Refused("offline")
            return exit_timezone.Zone("Asia/Taipei")

        with mock.patch.object(system_timezone.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "codex_route", side_effect=routes or [CHATGPT, CHATGPT]), \
             mock.patch.object(system_timezone, "find_exit",
                               side_effect=[ip if isinstance(ip, Exit) else Exit(ip) for ip in ips or ["1.1.1.1"] * 2]), \
             mock.patch.object(system_timezone, "lookup", side_effect=lookup), \
             mock.patch.object(system_timezone, "_cached", return_value=None) if settle else mock.MagicMock(), \
             mock.patch.object(system_timezone, "windows_zone_for", return_value=zone), \
             mock.patch.object(system_timezone, "current_zone", return_value=current), \
             mock.patch.object(system_timezone, "automatic_timezone", return_value=automatic), \
             mock.patch.object(system_timezone, "set_zone") as setter:
            try:
                self.result = system_timezone.sync(probe=probe, stopped=stopped, settle=settle)
            finally:
                self.set_calls = setter.call_count
                self.set_to = [call.args[0] for call in setter.call_args_list]
        return self.result["status"]

    def test_the_exit_timezone_is_set(self):
        self.assertEqual(self.run_sync(), "updated")
        self.assertEqual(self.set_calls, 1)
        state = system_timezone.last_result()
        self.assertEqual((state["exit_host"], state["windows_timezone"]), ("chatgpt.com", "Taipei Standard Time"))

    def test_probe_changes_nothing_and_saves_nothing(self):
        self.assertEqual(self.run_sync(probe=True), "probe")
        self.assertEqual(self.set_calls, 0)
        self.assertEqual(system_timezone.last_result(), {})

    def test_matching_timezone_is_left_alone(self):
        self.assertEqual(self.run_sync(current="Taipei Standard Time"), "unchanged")
        self.assertEqual(self.run_sync(current="Taipei Standard Time_dstoff"), "unchanged")
        self.assertEqual(self.set_calls, 0)

    def test_a_route_or_exit_change_during_the_lookup_changes_nothing(self):
        api = Route("OpenAI API key", "api.openai.com", None)
        self.assertEqual(self.run_sync(routes=[CHATGPT, api]), "skipped")
        self.assertEqual(self.run_sync(ips=["1.1.1.1", "8.8.8.8"]), "skipped")
        self.assertEqual(self.set_calls, 0)

    def test_nothing_is_changed_once_the_window_is_closing(self):
        stopped = system_timezone.threading.Event()
        stopped.set()
        self.assertEqual(self.run_sync(stopped=stopped), "skipped")
        self.assertEqual(self.set_calls, 0)
        self.assertIsNone(system_timezone.original_zone())

    def test_a_failed_lookup_changes_nothing(self):
        with self.assertRaises(Refused):
            self.run_sync(lookup_fails=True)
        self.assertEqual(self.set_calls, 0)

    def test_the_same_exit_is_looked_up_once_a_day(self):
        self.run_sync(current="Taipei Standard Time")
        self.assertEqual(self.lookups, 1)
        self.run_sync(current="Taipei Standard Time")
        self.assertEqual(self.lookups, 0)
        self.run_sync(ips=["8.8.8.8", "8.8.8.8"], current="Taipei Standard Time")
        self.assertEqual(self.lookups, 1)
        path = self.root / "bridge" / system_timezone.STATE_NAME
        state = json.loads(path.read_text(encoding="utf-8"))
        state["looked_up_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)).isoformat()
        path.write_text(json.dumps(state), encoding="utf-8")
        self.run_sync(ips=["8.8.8.8", "8.8.8.8"], current="Taipei Standard Time")
        self.assertEqual(self.lookups, 1)

    def test_a_country_change_or_a_state_from_before_the_country_check_is_looked_up_again(self):
        us = Exit("1.1.1.1", "US")
        self.run_sync(ips=[us, us], current="Taipei Standard Time")
        self.run_sync(ips=[us, us], current="Taipei Standard Time")
        self.assertEqual(self.lookups, 0)
        self.run_sync(ips=[Exit("1.1.1.1", "JP")] * 2, current="Taipei Standard Time")
        self.assertEqual(self.lookups, 1)
        # 0.5.4 saved no exit_country, and could have saved a timezone from a lookup that was wrong.
        path = self.root / "bridge" / system_timezone.STATE_NAME
        state = json.loads(path.read_text(encoding="utf-8"))
        state["exit_country"] = "US"
        path.write_text(json.dumps(state), encoding="utf-8")
        self.run_sync(ips=[us, us], current="Taipei Standard Time")
        self.assertEqual(self.lookups, 0)
        del state["exit_country"]
        path.write_text(json.dumps(state), encoding="utf-8")
        self.run_sync(ips=[us, us], current="Taipei Standard Time")
        self.assertEqual(self.lookups, 1)


class SettleTests(SyncTests):
    """``desktop``'s keeper: exits that alternate between countries, and Windows changing it back."""

    def test_an_exit_that_moves_back_and_forth_does_not_flip_windows(self):
        settle = system_timezone.Settle(checks=3)
        self.assertEqual(self.run_sync(settle=settle), "updated")
        self.assertEqual(settle.kept, "Taipei Standard Time")
        # A node in Japan, then Taiwan again, then Japan: Windows stays on Taipei.
        for zone in ("Tokyo Standard Time", "Taipei Standard Time", "Tokyo Standard Time", "Tokyo Standard Time"):
            status = self.run_sync(settle=settle, zone=zone, current="Taipei Standard Time")
            self.assertEqual(self.set_calls, 0)
        self.assertEqual(status, "waiting")
        self.assertEqual(self.result["windows_timezone"], "Taipei Standard Time")
        self.assertEqual(self.result["exit_windows_timezone"], "Tokyo Standard Time")
        # Three checks in a row in Japan: now it moves.
        self.assertEqual(self.run_sync(settle=settle, zone="Tokyo Standard Time", current="Taipei Standard Time"),
                         "updated")
        self.assertEqual(self.set_to, ["Tokyo Standard Time"])
        self.assertNotIn("reverted", self.result)
        self.assertEqual(settle.kept, "Tokyo Standard Time")

    def test_windows_changing_it_back_is_put_right_and_said(self):
        settle = system_timezone.Settle()
        self.run_sync(settle=settle)
        # No second look at the exit: the kept timezone was settled already.
        status = self.run_sync(settle=settle, ips=["1.1.1.1"], current="China Standard Time", automatic=True)
        self.assertEqual(status, "updated")
        self.assertEqual(self.set_to, ["Taipei Standard Time"])
        self.assertEqual((self.result["reverted"], self.result["automatic"]), (True, True))
        text = cli._describe_sync(self.result)
        self.assertIn("had gone back to China Standard Time; set Taipei Standard Time again", text)
        self.assertIn('"Set time zone automatically" is on', text)
        text.encode("ascii")

    def test_windows_changing_it_back_while_the_exit_moves_puts_back_the_kept_one(self):
        settle = system_timezone.Settle()
        self.run_sync(settle=settle)
        self.run_sync(settle=settle, zone="Tokyo Standard Time", ips=["1.1.1.1"], current="China Standard Time")
        self.assertEqual(self.set_to, ["Taipei Standard Time"])
        self.assertTrue(self.result["reverted"])


class RestoreTests(Folder):
    def test_the_timezone_from_before_the_first_change_is_kept(self):
        with mock.patch.object(system_timezone.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "codex_route", return_value=CHATGPT), \
             mock.patch.object(system_timezone, "find_exit", return_value=Exit("1.1.1.1")), \
             mock.patch.object(system_timezone, "lookup", return_value=exit_timezone.Zone("Asia/Tokyo")), \
             mock.patch.object(system_timezone, "windows_zone_for", return_value="Tokyo Standard Time"), \
             mock.patch.object(system_timezone, "current_zone", side_effect=["China Standard Time", "Korea Standard Time"]), \
             mock.patch.object(system_timezone, "set_zone"):
            system_timezone.sync()
            system_timezone.sync()
        self.assertEqual(system_timezone.original_zone(), "China Standard Time")

    def test_restore_puts_it_back_once(self):
        system_timezone._remember("China Standard Time")
        with mock.patch.object(system_timezone.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "set_zone") as setter:
            self.assertEqual(system_timezone.restore(), "China Standard Time")
            self.assertIsNone(system_timezone.restore())
        setter.assert_called_once_with("China Standard Time")

    def test_only_windows_is_changed(self):
        with mock.patch.object(system_timezone.sys, "platform", "darwin"):
            with self.assertRaises(Refused):
                system_timezone.restore()


class KeeperTests(Folder):
    def keep(self, *results) -> list[dict]:
        results = list(results)
        reported = []
        keeper = system_timezone.Keeper(reported.append, interval=0)

        def sync(**_):
            result = results.pop(0)
            if not results:
                keeper.stop()
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(system_timezone, "sync", side_effect=sync):
            keeper.start()._thread.join(5)
        return reported

    def test_it_reports_the_first_result_changes_and_lasting_errors(self):
        reported = self.keep(
            {"status": "unchanged"}, {"status": "unchanged"}, Refused("offline"), Refused("offline"),
            Refused("offline"), Refused("tzutil failed"), Refused("tzutil failed"), {"status": "unchanged"},
            {"status": "updated"}, {"status": "unchanged"},
        )
        self.assertEqual([(r["status"], r.get("error")) for r in reported], [
            ("unchanged", None), ("error", "offline"), ("error", "tzutil failed"), ("unchanged", None),
            ("updated", None)])
        self.assertEqual(system_timezone.last_result()["error"], "tzutil failed")

    def test_an_error_in_one_check_only_is_not_reported(self):
        reported = self.keep({"status": "unchanged"}, Refused("could not reach chatgpt.com (EOF)"),
                             {"status": "unchanged"}, {"status": "unchanged"})
        self.assertEqual([r["status"] for r in reported], ["unchanged"])

    def test_errors_that_differ_only_in_their_details_are_one(self):
        reported = self.keep(Refused("could not reach chatgpt.com (EOF)"), Refused("could not reach chatgpt.com (timeout)"),
                             Refused("could not reach chatgpt.com (EOF)"), {"status": "unchanged"})
        self.assertEqual([r["status"] for r in reported], ["error", "unchanged"])

    def test_changes_back_and_waiting_are_said_once(self):
        reported = self.keep({"status": "updated"}, {"status": "updated", "reverted": True},
                             {"status": "updated", "reverted": True}, {"status": "waiting"}, {"status": "unchanged"},
                             {"status": "waiting"})
        self.assertEqual([(r["status"], r.get("reverted")) for r in reported],
                         [("updated", None), ("updated", True), ("waiting", None)])

    def test_an_unreachable_exit_says_codex_needs_it_too(self):
        def fail(request):
            raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")

        with httpx.Client(transport=httpx.MockTransport(fail)) as client, self.assertRaises(Refused) as caught:
            system_timezone.find_exit(client, "chatgpt.com", "http://user:secret@127.0.0.1:7890")
        self.assertIsInstance(caught.exception, system_timezone.Unreachable)
        message = str(caught.exception)
        self.assertIn("could not reach chatgpt.com through the proxy http://127.0.0.1:7890", message)
        self.assertIn("closed during the TLS handshake", message)
        self.assertNotIn("secret", message)
        reported = self.keep(caught.exception, {"status": "unchanged"})
        text = cli._describe_sync(reported[0])
        self.assertIn("Codex itself reaches chatgpt.com through this proxy too", text)
        text.encode("ascii")

    def test_put_back_stops_then_restores(self):
        system_timezone._remember("China Standard Time")
        keeper = system_timezone.Keeper(lambda result: None)
        with mock.patch.object(system_timezone.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "set_zone") as setter:
            self.assertEqual(keeper.put_back(), "China Standard Time")
            self.assertIsNone(keeper.put_back())
        self.assertTrue(keeper._stop.is_set())
        setter.assert_called_once_with("China Standard Time")

    def test_desktop_keeps_windows_on_the_exit_unless_turned_off(self):
        with mock.patch.object(cli.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "Keeper") as keeper, mock.patch.object(cli, "_print"):
            with mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: "auto"}):
                self.assertIsNotNone(cli._keep_windows_timezone())
            with mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: "off"}):
                self.assertIsNone(cli._keep_windows_timezone())
        keeper.assert_called_once()
        with mock.patch.object(cli.sys, "platform", "darwin"), \
             mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: "auto"}):
            self.assertIsNone(cli._keep_windows_timezone())


class CliTests(Folder):
    def test_restore_says_when_there_is_nothing_to_restore(self):
        with mock.patch.object(system_timezone.sys, "platform", "win32"), mock.patch.object(cli, "_print") as out:
            self.assertEqual(cli._main(["timezone", "restore"]), 0)
        self.assertIn("Nothing to restore", out.call_args.args[0])

    def test_proxy_passwords_are_not_shown(self):
        text = cli._describe_sync({"status": "probe", "route": "ChatGPT sign-in", "exit_host": "chatgpt.com",
                                   "proxy": "http://user:secret@127.0.0.1:7890", "exit_ip": "1.1.1.1",
                                   "iana_timezone": "Asia/Tokyo", "windows_timezone": "", "previous_timezone": ""})
        self.assertIn("via http://127.0.0.1:7890", text)
        self.assertNotIn("secret", text)

    def test_timezone_option_reaches_the_bridge(self):
        args = cli._parser().parse_args(["desktop", "--timezone", "off"])
        with mock.patch.dict(os.environ, {exit_timezone.MODE_ENV: "auto"}):
            cli._apply_proxy(args)
            self.assertFalse(exit_timezone.enabled())


@unittest.skipUnless(sys.platform == "win32" and shutil.which("tzutil"), "needs Windows")
class WindowsTests(unittest.TestCase):
    """Read-only: the timezone is never changed here."""

    def test_iana_names_map_to_installed_windows_timezones(self):
        self.assertEqual(system_timezone.windows_zone_for("Asia/Tokyo"), "Tokyo Standard Time")
        self.assertEqual(system_timezone.windows_zone_for("America/Los_Angeles"), "Pacific Standard Time")
        with self.assertRaises(Refused):
            system_timezone.windows_zone_for("Nowhere/Unknown")
        self.assertTrue(system_timezone.current_zone())


if __name__ == "__main__":
    unittest.main()
