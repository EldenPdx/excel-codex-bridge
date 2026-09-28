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
        with mock.patch.dict(os.environ, {**clean, "ALL_PROXY": "127.0.0.1:1081"}), \
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
            self.assertEqual(system_timezone.exit_ip(client, "chatgpt.com"), "1.1.1.1")
            self.assertEqual(system_timezone.lookup(client, "1.1.1.1").name, "Asia/Tokyo")

    def test_plain_http_is_only_for_loopback(self):
        with self.client(lambda request: httpx.Response(200, text="h=example.com\nip=1.1.1.1")) as client:
            with mock.patch.dict(os.environ, {system_timezone.TRACE_ENV: "http://example.com/cdn-cgi/trace"}):
                with self.assertRaises(Refused):
                    system_timezone.exit_ip(client, "chatgpt.com")
        with self.client(lambda request: httpx.Response(200, text="h=127.0.0.1\nip=1.1.1.1")) as client:
            with mock.patch.dict(os.environ, {system_timezone.TRACE_ENV: "http://127.0.0.1:9/cdn-cgi/trace"}):
                self.assertEqual(system_timezone.exit_ip(client, "chatgpt.com"), "1.1.1.1")

    def test_all_lookups_failing_says_why(self):
        with self.client(lambda request: httpx.Response(429)) as client:
            with self.assertRaises(Refused) as caught:
                system_timezone.lookup(client, "1.1.1.1")
        self.assertIn("ipwho.is: HTTP 429", str(caught.exception))


class SyncTests(Folder):
    def run_sync(self, *, probe=False, routes=None, ips=None, current="China Standard Time", lookup_fails=False):
        self.lookups = 0

        def lookup(client, ip):
            self.lookups += 1
            if lookup_fails:
                raise Refused("offline")
            return exit_timezone.Zone("Asia/Taipei")

        with mock.patch.object(system_timezone.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "codex_route", side_effect=routes or [CHATGPT, CHATGPT]), \
             mock.patch.object(system_timezone, "exit_ip", side_effect=ips or ["1.1.1.1", "1.1.1.1"]), \
             mock.patch.object(system_timezone, "lookup", side_effect=lookup), \
             mock.patch.object(system_timezone, "windows_zone_for", return_value="Taipei Standard Time"), \
             mock.patch.object(system_timezone, "current_zone", return_value=current), \
             mock.patch.object(system_timezone, "set_zone") as setter:
            try:
                result = system_timezone.sync(probe=probe)
            finally:
                self.set_calls = setter.call_count
        return result["status"]

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


class RestoreTests(Folder):
    def test_the_timezone_from_before_the_first_change_is_kept(self):
        with mock.patch.object(system_timezone.sys, "platform", "win32"), \
             mock.patch.object(system_timezone, "codex_route", return_value=CHATGPT), \
             mock.patch.object(system_timezone, "exit_ip", return_value="1.1.1.1"), \
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
    def test_it_reports_the_first_result_changes_and_new_errors(self):
        results = [
            {"status": "unchanged"}, {"status": "unchanged"}, Refused("offline"), Refused("offline"),
            Refused("tzutil failed"), {"status": "unchanged"}, {"status": "updated"}, {"status": "unchanged"},
        ]
        reported = []
        keeper = system_timezone.Keeper(reported.append, interval=0)

        def sync():
            result = results.pop(0)
            if not results:
                keeper.stop()
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(system_timezone, "sync", side_effect=sync):
            keeper.start()._thread.join(5)
        self.assertEqual([(r["status"], r.get("error")) for r in reported], [
            ("unchanged", None), ("error", "offline"), ("error", "tzutil failed"), ("unchanged", None),
            ("updated", None)])
        self.assertEqual(system_timezone.last_result()["error"], "tzutil failed")

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
