import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from excel_codex_bridge import cli, codex_threads

# The columns of Codex's threads table the migration reads or writes, plus one it must leave alone.
SCHEMA = """CREATE TABLE threads (
    id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, updated_at INTEGER NOT NULL,
    model_provider TEXT NOT NULL, title TEXT NOT NULL, model TEXT, reasoning_effort TEXT)"""
ROWS = [
    ("old-sol", "a.jsonl", 3, "excel-bridge", "Fix the parser", "gpt-6-sol-excel", "high"),
    ("old-1m", "b.jsonl", 2, "excel-bridge", "Long refactor", "gpt-6-sol-1m-excel", "medium"),
    ("old-none", "c.jsonl", 1, "excel-bridge", "", None, None),
    ("official", "d.jsonl", 4, "openai", "Official one", "gpt-6-sol", "medium"),
    ("other", "e.jsonl", 5, "someone-else", "Another provider", "gpt-5.6-sol-excel", None),
]
NOW = dt.datetime(2026, 9, 28, 12, 0, 0)


class CodexIndex(unittest.TestCase):
    """A Codex home with a signed-in auth.json and a conversation index."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "codex"
        self.records = Path(folder.name) / "bridge"
        self.home.mkdir()
        self.db = self.home / "state_5.sqlite"
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute(SCHEMA)
            db.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)", ROWS)
        self.sign_in()

    def sign_in(self, auth=None):
        auth = auth if auth is not None else {"auth_mode": "chatgpt", "tokens": {"access_token": "not-a-token"}}
        (self.home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")

    def rows(self, db=None):
        with contextlib.closing(sqlite3.connect(db or self.db)) as conn:
            return conn.execute(
                "SELECT id, model_provider, model, reasoning_effort, title FROM threads ORDER BY id"
            ).fetchall()


class ThreadsTests(CodexIndex):
    def test_the_newest_index_with_a_threads_table_is_used(self):
        self.assertEqual(codex_threads.state_db(self.home), self.db)
        with contextlib.closing(sqlite3.connect(self.home / "state_12.sqlite")) as db:
            db.execute("CREATE TABLE other (id TEXT)")
        (self.home / "state_7.sqlite.before-excel-codex-x").write_bytes(b"")
        self.assertEqual(codex_threads.state_db(self.home), self.db)
        with contextlib.closing(sqlite3.connect(self.home / "state_6.sqlite")) as db, db:
            db.execute(SCHEMA)
        self.assertEqual(codex_threads.state_db(self.home), self.home / "state_6.sqlite")
        self.assertIsNone(codex_threads.state_db(self.home / "missing"))

    def test_only_the_bridge_provider_is_listed(self):
        self.assertEqual(
            [thread.id for thread in codex_threads.bridge_threads(self.home)], ["old-sol", "old-1m", "old-none"]
        )

    def test_migrate_moves_them_under_openai_with_codex_model_names(self):
        before = self.rows()
        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual([thread.id for thread in result.threads], ["old-sol", "old-1m", "old-none"])
        self.assertEqual(result.backup, self.home / "state_5.sqlite.before-excel-codex-20260928-120000")
        self.assertEqual(self.rows(result.backup), before)
        self.assertEqual(self.rows(), [
            ("official", "openai", "gpt-6-sol", "medium", "Official one"),
            ("old-1m", "openai", "gpt-6-sol-1m-excel", "medium", "Long refactor"),
            ("old-none", "openai", None, None, ""),
            ("old-sol", "openai", "gpt-6-sol", "high", "Fix the parser"),
            ("other", "someone-else", "gpt-5.6-sol-excel", None, "Another provider"),
        ])
        record = json.loads((self.records / codex_threads.RECORD_NAME).read_text(encoding="utf-8"))
        self.assertEqual(record["threads"]["old-sol"],
                         {"model_provider": "excel-bridge", "model": "gpt-6-sol-excel", "migrated_model": "gpt-6-sol"})
        self.assertEqual(record["backups"], [str(result.backup)])
        self.assertEqual(codex_threads.migrate(self.home, self.records, now=NOW).threads, [])

    def test_undo_puts_back_what_codex_has_not_changed_since(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            # Continued with another model after the migration: left as it is.
            db.execute("UPDATE threads SET model = 'gpt-6-astra' WHERE id = 'old-1m'")
        undone = codex_threads.undo(self.home, self.records, now=NOW)
        self.assertEqual((undone.restored, undone.kept), (2, 1))
        self.assertEqual(self.rows(), [
            ("official", "openai", "gpt-6-sol", "medium", "Official one"),
            ("old-1m", "openai", "gpt-6-astra", "medium", "Long refactor"),
            ("old-none", "excel-bridge", None, None, ""),
            ("old-sol", "excel-bridge", "gpt-6-sol-excel", "high", "Fix the parser"),
            ("other", "someone-else", "gpt-5.6-sol-excel", None, "Another provider"),
        ])
        self.assertFalse((self.records / codex_threads.RECORD_NAME).exists())
        self.assertTrue((self.records / f"{codex_threads.RECORD_NAME}.undone-20260928-120000").exists())
        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(0, 0))

    def test_a_row_codex_moved_back_keeps_its_first_state(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            # Renamed before it was continued: Codex rebuilt it from the conversation file.
            db.execute("UPDATE threads SET model_provider = 'excel-bridge', model = 'gpt-6-sol-excel' "
                       "WHERE id = 'old-sol'")
        again = codex_threads.migrate(self.home, self.records, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual([thread.id for thread in again.threads], ["old-sol"])
        record = json.loads((self.records / codex_threads.RECORD_NAME).read_text(encoding="utf-8"))
        self.assertEqual(record["threads"]["old-sol"]["model"], "gpt-6-sol-excel")
        self.assertEqual(len(record["backups"]), 2)
        self.assertEqual(codex_threads.undo(self.home, self.records).restored, 3)
        self.assertEqual(self.rows()[3][1:3], ("excel-bridge", "gpt-6-sol-excel"))

    def test_nothing_changes_without_a_codex_sign_in(self):
        self.sign_in({"auth_mode": "chatgpt", "tokens": {}})
        before = self.rows()
        with self.assertRaisesRegex(codex_threads.Refused, "codex login"):
            codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual(self.rows(), before)
        self.assertEqual(list(self.home.glob("*.before-excel-codex-*")), [])
        self.assertFalse(self.records.exists())

    def test_a_failed_change_is_rolled_back(self):
        before = self.rows()
        with mock.patch.object(codex_threads, "_write_record", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual(self.rows(), before)

    def test_an_unreadable_record_stops_both_ways(self):
        self.records.mkdir()
        (self.records / codex_threads.RECORD_NAME).write_text("[]", encoding="utf-8")
        before = self.rows()
        for action in (codex_threads.migrate, codex_threads.undo):
            with self.subTest(action=action.__name__), self.assertRaises(codex_threads.Refused):
                action(self.home, self.records)
        self.assertEqual(self.rows(), before)


class ThreadsCommandTests(CodexIndex):
    def run_cli(self, *argv):
        out = io.StringIO()
        env = {"CODEX_HOME": str(self.home), "EXCEL_BRIDGE_HOME": str(self.records)}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            code = cli.main(["threads", *argv])
        return code, out.getvalue()

    def test_list_migrate_undo(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("3 conversation(s) are filed under the bridge's own provider", out)
        self.assertIn("Fix the parser  (gpt-6-sol-excel)", out)
        self.assertIn("old-none", out)
        self.assertIn("`excel-codex threads migrate`", out)
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("Moved 3 conversation(s)", out)
        self.assertIn("before-excel-codex-", out)
        code, out = self.run_cli()
        self.assertIn("No conversations are filed under the bridge's own provider", out)
        code, out = self.run_cli("undo")
        self.assertEqual(code, 0)
        self.assertIn("Put back 3 conversation(s)", out)
        code, out = self.run_cli("undo")
        self.assertIn("Nothing to undo", out)

    def test_refusal_exits_non_zero(self):
        self.sign_in({})
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed: Codex is not signed in", out)
        code, out = self.run_cli()
        self.assertIn("After `codex login`", out)


if __name__ == "__main__":
    unittest.main()
