from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from excel_codex_bridge import cli, codex_threads, desktop_config

from helpers import write_webview_session

CATALOG = Path(r"C:\Users\张三\AppData\Local\excel-codex-bridge\codex-model-catalog.json")
USER_CONFIG = """\
# my settings
model = "gpt-5.5"
model_provider = "openai"
approval_policy = "on-request"

[model_providers.excel-bridge]
name = "old paste from print-config"
base_url = "http://127.0.0.1:9999/v1"

[history]
persistence = "none"
"""


def enable(text: str, **kwargs) -> str:
    return desktop_config.enable(text, port=8765, catalog=CATALOG, model="gpt-5.6-sol-excel", **kwargs)


class EnableTests(unittest.TestCase):
    def assert_points_at_bridge(self, text: str) -> dict:
        data = tomllib.loads(text)
        self.assertEqual(data["model_provider"], "excel-bridge")
        self.assertNotIn("openai_base_url", data)
        self.assertEqual(data["model"], "gpt-5.6-sol")
        self.assertEqual(data["model_catalog_json"], str(CATALOG))
        provider = data["model_providers"]["excel-bridge"]
        self.assertEqual(provider["base_url"], "http://127.0.0.1:8765/v1")
        self.assertEqual(provider["wire_api"], "responses")
        self.assertEqual(provider["http_headers"], {"x-openai-actor-authorization": "excel-codex-bridge"})
        return data

    def test_user_settings_survive_and_come_back_exactly(self):
        enabled = enable(USER_CONFIG)
        data = self.assert_points_at_bridge(enabled)
        self.assertEqual(data["approval_policy"], "on-request")
        self.assertEqual(data["history"], {"persistence": "none"})
        self.assertIn(desktop_config.DISABLED_PREFIX + 'model = "gpt-5.5"', enabled)
        self.assertEqual(desktop_config.strip_managed(enabled), USER_CONFIG)

    def test_empty_config(self):
        enabled = enable("")
        self.assert_points_at_bridge(enabled)
        self.assertEqual(desktop_config.strip_managed(enabled), "")

    def test_enabling_twice_is_the_same_as_once(self):
        once = enable(USER_CONFIG)
        self.assertEqual(enable(once), once)
        self.assertEqual(desktop_config.strip_managed(enable(once)), USER_CONFIG)

    def test_crlf_and_missing_final_newline_are_kept(self):
        for original in (USER_CONFIG.replace("\n", "\r\n"), USER_CONFIG.rstrip("\n"), 'model = "x"'):
            with self.subTest(original=original[-20:]):
                enabled = enable(original)
                self.assert_points_at_bridge(enabled)
                if "\r\n" in original:
                    self.assertNotIn("\n", enabled.replace("\r\n", ""))
                self.assertEqual(desktop_config.strip_managed(enabled), original)

    def test_own_provider_sub_tables_are_set_aside_too(self):
        # A hand-made image-tool setup, as users pasted it before the bridge set the header.
        original = (
            "[model_providers.excel-bridge]\n"
            'base_url = "http://127.0.0.1:8765/v1"\n'
            "[model_providers.excel-bridge.http_headers]\n"
            '"x-openai-actor-authorization" = "local-image-extension"\n'
            "[model_providers.excel-bridge-old]\n"
            'name = "kept"\n'
        )
        enabled = enable(original)
        data = self.assert_points_at_bridge(enabled)
        self.assertEqual(data["model_providers"]["excel-bridge-old"], {"name": "kept"})
        self.assertEqual(desktop_config.strip_managed(enabled), original)

    def test_shared_mode_stands_in_for_codex_own_provider(self):
        original = USER_CONFIG.replace("[history]", 'openai_base_url = "https://example.test/v1"\n\n[history]')
        original = 'openai_base_url = "https://proxy.test/v1"\n' + original
        for text in (original, original.replace("\n", "\r\n"), original.rstrip("\n")):
            with self.subTest(text=text[-12:]):
                enabled = enable(text, shared=True)
                data = tomllib.loads(enabled)
                self.assertEqual(data["model_provider"], "openai")
                self.assertEqual(data["openai_base_url"], "http://127.0.0.1:8765/v1")
                self.assertEqual(data["model"], "gpt-5.6-sol")
                # Still defined, for conversations started as excel-bridge.
                self.assertEqual(data["model_providers"]["excel-bridge"]["base_url"], "http://127.0.0.1:8765/v1")
                self.assertIn(desktop_config.DISABLED_PREFIX + 'openai_base_url = "https://proxy.test/v1"', enabled)
                self.assertEqual(desktop_config.strip_managed(enabled), text)
                # Switching modes (signed in or out in between) still comes back exactly.
                self.assertEqual(desktop_config.strip_managed(enable(enabled)), text)
                self.assertEqual(enable(enable(text), shared=True), enabled)

    def test_nested_model_keys_are_left_alone(self):
        original = '[profiles.fast]\nmodel = "gpt-5.5"\n'
        enabled = enable(original)
        self.assertNotIn(desktop_config.DISABLED_PREFIX, enabled)
        self.assertEqual(tomllib.loads(enabled)["profiles"]["fast"]["model"], "gpt-5.5")

    def test_active_profile_with_its_own_model_is_reported(self):
        self.assertEqual(
            desktop_config.profile_override('profile = "fast"\n[profiles.fast]\nmodel = "o3"\n'), "fast"
        )
        self.assertIsNone(desktop_config.profile_override('profile = "fast"\n[profiles.fast]\nx = 1\n'))
        self.assertIsNone(desktop_config.profile_override(USER_CONFIG))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.toml"

    def enable_file(self):
        return desktop_config.enable_file(self.path, port=8765, catalog=CATALOG, model="gpt-5.6-sol-excel")

    def test_round_trip_keeps_bytes_and_a_backup(self):
        original = ("\ufeff" + USER_CONFIG.replace("\n", "\r\n")).encode("utf-8")
        self.path.write_bytes(original)
        backup = self.enable_file()
        self.assertEqual(backup.read_bytes(), original)
        self.assertTrue(desktop_config.is_enabled(self.path.read_text(encoding="utf-8-sig")))
        # A second enable (say after a crash) keeps the first backup.
        self.assertIsNone(self.enable_file())
        self.assertEqual(backup.read_bytes(), original)
        self.assertTrue(desktop_config.disable_file(self.path))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(desktop_config.disable_file(self.path))

    def test_missing_config_is_created_and_emptied(self):
        self.assertIsNone(self.enable_file())
        self.assertTrue(desktop_config.disable_file(self.path))
        self.assertEqual(self.path.read_text(), "")

    def test_shared_round_trip(self):
        self.path.write_text(USER_CONFIG)
        desktop_config.enable_file(self.path, port=8765, catalog=CATALOG, model="gpt-6-sol-excel", shared=True)
        data = tomllib.loads(self.path.read_text())
        self.assertEqual((data["model_provider"], data["model"]), ("openai", "gpt-6-sol"))
        self.assertTrue(desktop_config.disable_file(self.path))
        self.assertEqual(self.path.read_text(), USER_CONFIG)

    def test_invalid_toml_is_left_untouched(self):
        self.path.write_text("approval_policy = \n")
        with self.assertRaises(desktop_config.ConfigError):
            self.enable_file()
        self.assertEqual(self.path.read_text(), "approval_policy = \n")


class DesktopCommandTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.webview = write_webview_session(root / "webview", time.time() + 3 * 86400)
        self.config = root / "codex-home" / "config.toml"
        self.config.parent.mkdir()
        self.config.write_text(USER_CONFIG)
        env = {"CODEX_HOME": str(self.config.parent), "EXCEL_BRIDGE_HOME": str(root / "bridge-home")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
            if hasattr(cli.signal, name):
                sig = getattr(cli.signal, name)
                self.addCleanup(cli.signal.signal, sig, cli.signal.getsignal(sig))
        # This machine's own Codex, if it runs one, is none of these tests' business.
        seen = mock.patch.object(codex_threads, "codex_seen", return_value=False)
        self.codex_seen = seen.start()
        self.addCleanup(seen.stop)

    def run_desktop(self, *extra, while_running=None):
        seen = {}

        def fake_bridge(reader, args, *, host, port, quiet):
            seen["config"] = self.config.read_text()
            seen["port"] = port
            if while_running is not None:
                while_running(seen)
            raise KeyboardInterrupt

        with mock.patch.object(cli, "_run_bridge", fake_bridge), mock.patch.object(cli, "_port_free", lambda p: True):
            code = cli.main(["desktop", "--webview-dir", str(self.webview), "--no-auto-signin", *extra])
        return code, seen

    def test_config_points_at_the_bridge_while_running_and_is_restored(self):
        code, seen = self.run_desktop("--port", "8799")
        self.assertEqual(code, 0)
        self.assertEqual(seen["port"], 8799)
        data = tomllib.loads(seen["config"])
        self.assertEqual(data["model_providers"]["excel-bridge"]["base_url"], "http://127.0.0.1:8799/v1")
        self.assertEqual(self.config.read_text(), USER_CONFIG)

    def test_signed_in_codex_shares_its_conversations(self):
        (self.config.parent / "auth.json").write_text('{"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test"}')
        with mock.patch.object(cli, "_print") as printed:
            code, seen = self.run_desktop("--port", "8799", "--model", "gpt-6-astra-excel")
        self.assertEqual(code, 0)
        data = tomllib.loads(seen["config"])
        self.assertEqual(data["model_provider"], "openai")
        self.assertEqual(data["openai_base_url"], "http://127.0.0.1:8799/v1")
        self.assertEqual(data["model"], "gpt-6-astra")
        self.assertIn(cli._SHARED, [call.args[0] for call in printed.call_args_list])
        self.assertEqual(self.config.read_text(), USER_CONFIG)

    def test_without_codex_sign_in_the_bridge_is_its_own_provider(self):
        with mock.patch.object(cli, "_print") as printed:
            code, seen = self.run_desktop()
        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(seen["config"])["model_provider"], "excel-bridge")
        self.assertIn(cli._SEPARATE, [call.args[0] for call in printed.call_args_list])

    def test_it_says_to_quit_codex_fully_when_done(self):
        for running in (True, False, None):
            with self.subTest(running=running), mock.patch.object(cli, "_print") as printed:
                self.codex_seen.return_value = running
                code, _ = self.run_desktop()
                said = [call.args[0] for call in printed.call_args_list]
                self.assertEqual(code, 0)
                self.assertIn(cli._QUIT_WHEN_DONE, said)
                self.assertIn(cli._REOPEN_AFTER_RESTORE, said)
                # Only when a Codex process is seen: not when the process list cannot be read.
                self.assertEqual(cli._STILL_RUNNING in said, running is True)
        self.assertIn("tray icon", cli._QUIT_WHEN_DONE)
        self.assertIn("os error 10061", cli._REOPEN_AFTER_RESTORE)

    def test_off_says_so_too(self):
        self.run_desktop("--keep-config")
        self.codex_seen.return_value = True
        with mock.patch.object(cli, "_print") as printed:
            self.assertEqual(cli.main(["desktop", "--off"]), 0)
        said = [call.args[0] for call in printed.call_args_list]
        self.assertEqual(said[1:], [cli._REOPEN_AFTER_RESTORE, cli._STILL_RUNNING])

    def test_keep_config_then_off(self):
        code, _ = self.run_desktop("--keep-config")
        self.assertEqual(code, 0)
        self.assertTrue(desktop_config.is_enabled(self.config.read_text()))
        self.assertEqual(cli.main(["desktop", "--off"]), 0)
        self.assertEqual(self.config.read_text(), USER_CONFIG)

    def test_the_windows_timezone_is_put_back_on_the_way_out(self):
        keeper = mock.Mock(**{"put_back.return_value": "China Standard Time"})
        with mock.patch.object(cli, "_keep_windows_timezone", return_value=keeper), \
             mock.patch.object(cli, "_print") as printed:
            code, _ = self.run_desktop()
        self.assertEqual(code, 0)
        keeper.put_back.assert_called_once_with()
        self.assertIn("Windows timezone: put back China Standard Time.",
                      [call.args[0] for call in printed.call_args_list])

    def test_closing_the_window_puts_back_the_timezone(self):
        for extra in ((), ("--keep-config",)):
            with self.subTest(extra=extra):
                self.config.write_text(USER_CONFIG)
                keeper = mock.Mock(**{"put_back.side_effect": ["China Standard Time", None]})
                handlers = []

                def window_closed(seen):
                    # What Windows runs when the console window is closed, before it ends the process.
                    handlers[0]()
                    seen["after"] = self.config.read_text()

                with mock.patch.object(cli, "_keep_windows_timezone", return_value=keeper), \
                     mock.patch.object(cli, "_undo_on_exit", side_effect=handlers.append), \
                     mock.patch.object(cli, "_print"):
                    code, seen = self.run_desktop(*extra, while_running=window_closed)
                self.assertEqual(code, 0)
                # By the handler, then once more (a no-op by then) on the way out.
                self.assertEqual(keeper.put_back.call_count, 2)
                self.assertEqual(seen["after"] == USER_CONFIG, not extra)

    def test_a_timezone_that_cannot_be_put_back_says_what_to_run(self):
        keeper = mock.Mock(**{"put_back.side_effect": RuntimeError("tzutil failed")})
        with mock.patch.object(cli, "_keep_windows_timezone", return_value=keeper), \
             mock.patch.object(cli, "_print") as printed:
            code, _ = self.run_desktop()
        self.assertEqual(code, 0)
        self.assertEqual(self.config.read_text(), USER_CONFIG)
        self.assertTrue(any("tzutil failed" in call.args[0] and "excel-codex timezone restore" in call.args[0]
                            for call in printed.call_args_list))

    def test_busy_port_changes_nothing(self):
        with mock.patch.object(cli, "_port_free", lambda p: False):
            code = cli.main(["desktop", "--webview-dir", str(self.webview), "--no-auto-signin"])
        self.assertEqual(code, 1)
        self.assertEqual(self.config.read_text(), USER_CONFIG)


# Loses track of the first connection the way asyncio's Windows proactor does when a
# client resets it, then runs the CLI.
LEAKY_DESKTOP = """\
import sys
from asyncio import base_events
from excel_codex_bridge import cli

detach = base_events.Server._detach
base_events.Server._detach = lambda self: setattr(base_events.Server, "_detach", detach)
sys.exit(cli.main(sys.argv[1:]))
"""


class DesktopShutdownTests(unittest.TestCase):
    def test_ctrl_c_restores_the_config_after_a_lost_connection(self):
        root = Path(tempfile.mkdtemp())
        webview = write_webview_session(root / "webview", time.time() + 3 * 86400)
        config = root / "codex-home" / "config.toml"
        config.parent.mkdir()
        config.write_text(USER_CONFIG)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(
            os.environ,
            CODEX_HOME=str(config.parent),
            EXCEL_BRIDGE_HOME=str(root / "bridge-home"),
            PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]),
        )
        group = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
            else {"start_new_session": True}
        )
        command = [sys.executable, "-c", LEAKY_DESKTOP, "desktop", "--webview-dir", str(webview),
                   "--no-auto-signin", "--port", str(port)]
        desktop = subprocess.Popen(
            command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **group
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            deadline = time.monotonic() + 30
            while desktop.poll() is None and time.monotonic() < deadline:
                try:
                    with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2):
                        break
                except OSError:
                    time.sleep(0.2)
            self.assertIsNone(desktop.poll(), "the desktop bridge exited early")
            self.assertTrue(desktop_config.is_enabled(config.read_text()))
            if sys.platform == "win32":
                os.kill(desktop.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(desktop.pid, signal.SIGINT)
            output, _ = desktop.communicate(timeout=30)
        finally:
            if desktop.poll() is None:
                desktop.kill()
                desktop.communicate()
        self.assertEqual(desktop.returncode, 0, output)
        self.assertEqual(config.read_text(), USER_CONFIG)
        # Each request shows up in the window, so users can tell Codex reaches the bridge.
        self.assertIn(b'"GET /healthz HTTP/1.1" 200', output)


if __name__ == "__main__":
    unittest.main()
