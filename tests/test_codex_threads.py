import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import subprocess
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
    ("old-sol", 3, "excel-bridge", "Fix the parser", "gpt-6-sol-excel", "high"),
    ("old-1m", 2, "excel-bridge", "Long refactor", "gpt-6-sol-1m-excel", "medium"),
    ("old-none", 1, "excel-bridge", "", None, None),
    ("official", 4, "openai", "Official one", "gpt-6-sol", "medium"),
    ("other", 5, "someone-else", "Another provider", "gpt-5.6-sol-excel", None),
]
NOW = dt.datetime(2026, 9, 28, 12, 0, 0)
MTIME = 1_790_000_000


def rollout_text(thread_id, provider, model, note="the first user message"):
    """A conversation file the way Codex writes it: compact JSON lines, session_meta first."""
    meta = {"timestamp": "2026-09-01T08:00:00.000Z", "type": "session_meta", "payload": {
        "session_id": thread_id, "id": thread_id, "cwd": "C:\\work", "originator": "codex_vscode",
        "cli_version": "0.156.1", "source": "vscode", "model_provider": provider,
        "base_instructions": {"text": 'Say "model_provider":"excel-bridge" only when asked.',
                              "provenance": {"type": "model", "model": model}}}}
    mode = {"mode": "default", "settings": {"model": model, "reasoning_effort": "high", "developer_instructions": None}}
    lines = [meta,
             {"type": "world_state", "payload": {"full": True, "state": {"model": model}}},
             {"type": "turn_context", "payload": {"model": model, "cwd": "C:\\work", "collaboration_mode": mode}},
             {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": f'{note}: {{"type":"turn_context","model":"{model}"}}'}]}},
             {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_id": thread_id,
                                               "thread_settings": {"model": model, "model_provider_id": provider,
                                                                   "collaboration_mode": mode}}},
             {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}},
             {"type": "turn_context", "payload": {"model": model, "cwd": "C:\\work"}}]
    return "".join(json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n" for line in lines)


def rebuilt(path):
    """(provider, model) Codex puts in a conversation's index row when it rebuilds it from the file.

    As codex-rs state/src/extract.rs does: the provider from session_meta, then
    the model, and the provider again, from each later line in turn.
    """
    provider = model = None
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        payload = item["payload"]
        if item["type"] == "session_meta":
            provider = payload["model_provider"]
        elif item["type"] == "turn_context":
            model = payload["model"]
        elif item["type"] == "event_msg" and payload["type"] == "thread_settings_applied":
            model = payload["thread_settings"]["model"]
            provider = payload["thread_settings"]["model_provider_id"]
    return provider, model


def shared(original, provider="excel-bridge", model="gpt-6-sol-excel", official="gpt-6-sol"):
    """A conversation file as migrate leaves it: openai and ``official`` where Codex takes them from."""
    lines = original.splitlines(keepends=True)
    lines[0] = lines[0].replace(f'"model_provider":"{provider}"'.encode(), b'"model_provider":"openai"', 1)
    for number in (2, 4, 6):
        lines[number] = lines[number].replace(f'"model":"{model}"'.encode(), f'"model":"{official}"'.encode())
    lines[4] = lines[4].replace(f'"model_provider_id":"{provider}"'.encode(), b'"model_provider_id":"openai"')
    return b"".join(lines)


class CodexIndex(unittest.TestCase):
    """A Codex home with a signed-in auth.json, conversation files and their index."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "codex"
        self.records = Path(folder.name) / "bridge"
        self.day = self.home / "sessions" / "2026" / "09" / "01"
        self.day.mkdir(parents=True)
        self.db = self.home / "state_5.sqlite"
        self.files = {}
        rows = []
        for thread_id, updated, provider, title, model, effort in ROWS:
            path = self.day / f"rollout-2026-09-01T08-00-00-{thread_id}.jsonl"
            path.write_text(rollout_text(thread_id, provider, model or "gpt-6-sol-excel"), encoding="utf-8")
            os.utime(path, (MTIME, MTIME))
            path.chmod(0o640)
            self.files[thread_id] = path
            rows.append((thread_id, str(path), updated, provider, title, model, effort))
        self.originals = {thread_id: path.read_bytes() for thread_id, path in self.files.items()}
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute(SCHEMA)
            db.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        self.sign_in()
        running = mock.patch.object(codex_threads, "codex_running", return_value=False)
        self.codex_running = running.start()
        self.addCleanup(running.stop)

    def sign_in(self, auth=None):
        auth = auth if auth is not None else {"auth_mode": "chatgpt", "tokens": {"access_token": "not-a-token"}}
        (self.home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")

    def rows(self, db=None):
        with contextlib.closing(sqlite3.connect(db or self.db)) as conn:
            return conn.execute(
                "SELECT id, model_provider, model, reasoning_effort, title FROM threads ORDER BY id"
            ).fetchall()

    def file_provider(self, thread_id):
        with self.files[thread_id].open(encoding="utf-8") as handle:
            return json.loads(handle.readline())["payload"]["model_provider"]

    def record(self):
        return json.loads((self.records / codex_threads.RECORD_NAME).read_text(encoding="utf-8"))

    def add_thread(self, thread_id, provider, model, title="Through a relay"):
        """One more conversation, the newest, as ``provider`` wrote it."""
        path = self.day / f"rollout-2026-09-01T08-00-00-{thread_id}.jsonl"
        path.write_text(rollout_text(thread_id, provider, model), encoding="utf-8")
        os.utime(path, (MTIME, MTIME))
        self.files[thread_id] = path
        self.originals[thread_id] = path.read_bytes()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (thread_id, str(path), 6, provider, title, model, "high"))

    def earlier_version_moved(self, thread_id="old-1m"):
        """What 0.5.6 to 0.5.8 left: the row, the record and the file's first line under openai."""
        self.records.mkdir(exist_ok=True)
        (self.records / codex_threads.RECORD_NAME).write_text(json.dumps({"threads": {thread_id: {
            "model_provider": "excel-bridge", "model": "gpt-6-sol-1m-excel", "migrated_model": "gpt-6-sol-1m-excel",
        }}, "backups": []}), encoding="utf-8")
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE threads SET model_provider = 'openai' WHERE id = ?", (thread_id,))
            db.execute("DELETE FROM threads WHERE model_provider = 'excel-bridge'")
        self.files[thread_id].write_bytes(
            self.originals[thread_id].replace(b'"model_provider":"excel-bridge"', b'"model_provider":"openai"', 1))


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
        self.assertEqual(result.left, [])
        self.assertEqual(result.backup, self.home / "state_5.sqlite.before-excel-codex-20260928-120000")
        self.assertEqual(self.rows(result.backup), before)
        self.assertEqual(self.rows(), [
            ("official", "openai", "gpt-6-sol", "medium", "Official one"),
            # OpenAI has no 1M model: it carries on with the same model's own.
            ("old-1m", "openai", "gpt-6-sol", "medium", "Long refactor"),
            ("old-none", "openai", None, None, ""),
            ("old-sol", "openai", "gpt-6-sol", "high", "Fix the parser"),
            ("other", "someone-else", "gpt-5.6-sol-excel", None, "Another provider"),
        ])
        record = self.record()
        self.assertEqual(record["threads"]["old-sol"], {
            "model_provider": "excel-bridge", "model": "gpt-6-sol-excel", "migrated_model": "gpt-6-sol",
            "lines": [
                [2, [[["payload", "model"], "gpt-6-sol-excel", "gpt-6-sol"],
                     [["payload", "collaboration_mode", "settings", "model"], "gpt-6-sol-excel", "gpt-6-sol"]]],
                [4, [[["payload", "thread_settings", "model"], "gpt-6-sol-excel", "gpt-6-sol"],
                     [["payload", "thread_settings", "collaboration_mode", "settings", "model"],
                      "gpt-6-sol-excel", "gpt-6-sol"],
                     [["payload", "thread_settings", "model_provider_id"], "excel-bridge", "openai"]]],
                [6, [[["payload", "model"], "gpt-6-sol-excel", "gpt-6-sol"]]],
            ]})
        self.assertEqual(record["backups"], [str(result.backup)])
        self.assertEqual(codex_threads.migrate(self.home, self.records, now=NOW).threads, [])

    def test_the_files_name_openai_and_official_models_and_nothing_else(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        models = {"old-sol": "gpt-6-sol-excel", "old-1m": "gpt-6-sol-1m-excel", "old-none": "gpt-6-sol-excel"}
        for thread_id, model in models.items():
            with self.subTest(thread=thread_id):
                before, after = self.originals[thread_id], self.files[thread_id].read_bytes()
                self.assertEqual(after, shared(before, model=model))
                self.assertNotEqual(after, before)
                self.assertEqual(self.file_provider(thread_id), "openai")
                # Quoted text in the instructions and the conversation, and the context Codex compares with, stay.
                self.assertIn(b'Say \\"model_provider\\":\\"excel-bridge\\" only when asked.', after)
                self.assertEqual([after.splitlines()[number] for number in (1, 3, 5)],
                                 [before.splitlines()[number] for number in (1, 3, 5)])
                # Codex orders its list by it.
                self.assertEqual(self.files[thread_id].stat().st_mtime, MTIME)
                if os.name != "nt":  # Windows has no such permission bits
                    self.assertEqual(self.files[thread_id].stat().st_mode & 0o777, 0o640)
        for thread_id in ("official", "other"):
            self.assertEqual(self.files[thread_id].read_bytes(), self.originals[thread_id])
        self.assertEqual([path.name for path in self.day.iterdir() if not path.name.endswith(".jsonl")], [])

    def test_codex_rebuilds_the_same_row_from_the_file(self):
        self.assertEqual(rebuilt(self.files["old-1m"]), ("excel-bridge", "gpt-6-sol-1m-excel"))
        codex_threads.migrate(self.home, self.records, now=NOW)
        rows = {row[0]: row[1:3] for row in self.rows()}
        for thread_id in ("old-sol", "old-1m"):
            with self.subTest(thread=thread_id):
                self.assertEqual(rebuilt(self.files[thread_id]), rows[thread_id])
        self.assertEqual(rebuilt(self.files["old-none"]), ("openai", "gpt-6-sol"))

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
        self.assertEqual(self.files["old-sol"].read_bytes(), self.originals["old-sol"])
        self.assertEqual(self.files["old-none"].read_bytes(), self.originals["old-none"])
        self.assertEqual(self.file_provider("old-1m"), "openai")
        self.assertFalse((self.records / codex_threads.RECORD_NAME).exists())
        self.assertTrue((self.records / f"{codex_threads.RECORD_NAME}.undone-20260928-120000").exists())
        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(0, 0))

    def test_a_row_codex_moved_back_keeps_its_first_state(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE threads SET model_provider = 'excel-bridge', model = 'gpt-6-sol-excel' "
                       "WHERE id = 'old-sol'")
        again = codex_threads.migrate(self.home, self.records, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual([thread.id for thread in again.threads], ["old-sol"])
        self.assertEqual(self.record()["threads"]["old-sol"]["model"], "gpt-6-sol-excel")
        self.assertEqual(len(self.record()["backups"]), 2)
        self.assertEqual(codex_threads.undo(self.home, self.records).restored, 3)
        self.assertEqual(self.rows()[3][1:3], ("excel-bridge", "gpt-6-sol-excel"))
        self.assertEqual(self.files["old-sol"].read_bytes(), self.originals["old-sol"])

    def test_threads_moved_in_the_index_only_are_finished(self):
        # What 0.5.4 and 0.5.5 did: the row and the record, not the file.
        self.records.mkdir()
        (self.records / codex_threads.RECORD_NAME).write_text(json.dumps({"threads": {
            "old-sol": {"model_provider": "excel-bridge", "model": "gpt-6-sol-excel", "migrated_model": "gpt-6-sol"},
        }, "backups": ["an-earlier-copy"]}), encoding="utf-8")
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE threads SET model_provider = 'openai', model = 'gpt-6-sol' WHERE id = 'old-sol'")
        self.assertEqual([thread.id for thread in codex_threads.bridge_threads(self.home)], ["old-1m", "old-none"])
        self.assertEqual([thread.id for thread in codex_threads.bridge_threads(self.home, self.records)],
                         ["old-1m", "old-none", "old-sol"])
        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual([thread.id for thread in result.threads], ["old-1m", "old-none", "old-sol"])
        self.assertEqual(self.file_provider("old-sol"), "openai")
        self.assertEqual(self.rows()[3][1:3], ("openai", "gpt-6-sol"))
        self.assertEqual(self.record()["threads"]["old-sol"]["model"], "gpt-6-sol-excel")
        self.assertEqual(codex_threads.bridge_threads(self.home, self.records), [])
        self.assertEqual(codex_threads.undo(self.home, self.records).restored, 3)
        self.assertEqual(self.files["old-sol"].read_bytes(), self.originals["old-sol"])

    def test_threads_an_earlier_version_moved_are_finished(self):
        # What 0.5.6 to 0.5.8 did: the row, the record and the file's first line, not its later lines.
        self.records.mkdir()
        (self.records / codex_threads.RECORD_NAME).write_text(json.dumps({"threads": {
            "old-sol": {"model_provider": "excel-bridge", "model": "gpt-6-sol-excel", "migrated_model": "gpt-6-sol"},
            "old-1m": {"model_provider": "excel-bridge", "model": "gpt-6-sol-1m-excel",
                       "migrated_model": "gpt-6-sol-1m-excel"},
        }, "backups": ["an-earlier-copy"]}), encoding="utf-8")
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE threads SET model_provider = 'openai', model = 'gpt-6-sol' WHERE id = 'old-sol'")
            db.execute("UPDATE threads SET model_provider = 'openai' WHERE id = 'old-1m'")
        for thread_id in ("old-sol", "old-1m"):
            first = self.originals[thread_id].replace(b'"model_provider":"excel-bridge"', b'"model_provider":"openai"', 1)
            self.files[thread_id].write_bytes(first)
            os.utime(self.files[thread_id], (MTIME, MTIME))
        # Codex puts back the bridge's models, and the bridge as their provider, when it rebuilds these rows.
        self.assertEqual(rebuilt(self.files["old-sol"]), ("excel-bridge", "gpt-6-sol-excel"))
        self.assertEqual([thread.id for thread in codex_threads.bridge_threads(self.home, self.records)], ["old-none"])
        self.assertEqual([thread.id for thread in codex_threads.unfinished_threads(self.home, self.records)],
                         ["old-sol", "old-1m"])

        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual([thread.id for thread in result.threads], ["old-none"])
        self.assertEqual([thread.id for thread in result.finished], ["old-sol", "old-1m"])
        self.assertEqual(self.files["old-sol"].read_bytes(), shared(self.originals["old-sol"]))
        self.assertEqual(self.files["old-1m"].read_bytes(),
                         shared(self.originals["old-1m"], model="gpt-6-sol-1m-excel"))
        rows = {row[0]: row[1:3] for row in self.rows()}
        self.assertEqual(rows["old-1m"], ("openai", "gpt-6-sol"))
        for thread_id in ("old-sol", "old-1m"):
            self.assertEqual(rebuilt(self.files[thread_id]), rows[thread_id])
        self.assertEqual(self.record()["threads"]["old-1m"]["migrated_model"], "gpt-6-sol")
        self.assertEqual(self.record()["threads"]["old-1m"]["model"], "gpt-6-sol-1m-excel")
        self.assertEqual(codex_threads.unfinished_threads(self.home, self.records), [])
        self.assertEqual(codex_threads.migrate(self.home, self.records, now=NOW).finished, [])

        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(3, 0))
        for thread_id in ("old-sol", "old-1m", "old-none"):
            self.assertEqual(self.files[thread_id].read_bytes(), self.originals[thread_id])
        self.assertEqual({row[0]: row[1:3] for row in self.rows()}["old-1m"], ("excel-bridge", "gpt-6-sol-1m-excel"))

    def test_one_carried_on_with_another_model_since_is_finished_but_not_undone(self):
        self.records.mkdir()
        (self.records / codex_threads.RECORD_NAME).write_text(json.dumps({"threads": {
            "old-sol": {"model_provider": "excel-bridge", "model": "gpt-6-sol-excel", "migrated_model": "gpt-6-sol"},
        }, "backups": []}), encoding="utf-8")
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE threads SET model_provider = 'openai', model = 'gpt-6-astra' WHERE id = 'old-sol'")
        self.files["old-sol"].write_bytes(
            self.originals["old-sol"].replace(b'"model_provider":"excel-bridge"', b'"model_provider":"openai"', 1))
        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual([thread.id for thread in result.finished], ["old-sol"])
        self.assertEqual(self.rows()[3][1:3], ("openai", "gpt-6-astra"))
        self.assertEqual(self.files["old-sol"].read_bytes(), shared(self.originals["old-sol"]))
        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(2, 1))
        self.assertEqual(self.rows()[3][1:3], ("openai", "gpt-6-astra"))
        self.assertEqual(self.files["old-sol"].read_bytes(), shared(self.originals["old-sol"]))

    def test_a_model_written_another_way_leaves_its_conversation_alone(self):
        # JSON allows it; undo could not put back the same bytes.
        lines = self.originals["old-sol"].splitlines(keepends=True)
        lines[6] = lines[6].replace(b'"gpt-6-sol-excel"', b'"gpt-6-sol\\u002dexcel"')
        self.files["old-sol"].write_bytes(b"".join(lines))
        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual([thread.id for thread in result.left], ["old-sol"])
        self.assertEqual(self.files["old-sol"].read_bytes(), b"".join(lines))
        self.assertEqual(self.rows()[3][1:3], ("excel-bridge", "gpt-6-sol-excel"))
        self.assertNotIn("old-sol", self.record()["threads"])

    def test_a_line_changed_since_keeps_its_conversation_as_it_is(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        lines = self.files["old-sol"].read_bytes().splitlines(keepends=True)
        lines[6] = lines[6].replace(b'"model":"gpt-6-sol"', b'"model":"gpt-6-astra"')
        self.files["old-sol"].write_bytes(b"".join(lines))
        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(2, 1))
        self.assertEqual(self.files["old-sol"].read_bytes(), b"".join(lines))
        self.assertEqual(self.rows()[3][1:3], ("openai", "gpt-6-sol"))

    def test_a_file_shorter_than_recorded_keeps_its_conversation_as_it_is(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        lines = self.files["old-sol"].read_bytes().splitlines(keepends=True)[:6]
        self.files["old-sol"].write_bytes(b"".join(lines))
        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(2, 1))
        self.assertEqual(self.files["old-sol"].read_bytes(), b"".join(lines))

    def test_a_file_the_index_lost_track_of_is_found_by_its_id(self):
        archived = self.home / "archived_sessions"
        archived.mkdir()
        moved = archived / self.files["old-sol"].name
        self.files["old-sol"].replace(moved)
        self.files["old-sol"] = moved
        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual(len(result.threads), 3)
        self.assertEqual(self.file_provider("old-sol"), "openai")

    def test_a_file_that_cannot_be_changed_leaves_its_conversation_alone(self):
        self.files["old-1m"].unlink()
        text = self.originals["old-none"].replace(b'"source":"vscode"', b'"source":"vscode","x":{"model_provider":"excel-bridge"}')
        self.files["old-none"].write_bytes(text)
        result = codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual([thread.id for thread in result.threads], ["old-sol"])
        self.assertEqual([thread.id for thread in result.left], ["old-1m", "old-none"])
        self.assertEqual(self.files["old-none"].read_bytes(), text)
        self.assertEqual([row[1] for row in self.rows()], ["openai", "excel-bridge", "excel-bridge", "openai",
                                                           "someone-else"])
        self.assertEqual(list(self.record()["threads"]), ["old-sol"])
        self.assertEqual(codex_threads.undo(self.home, self.records), codex_threads.Undone(1, 0))

    def test_nothing_changes_without_a_codex_sign_in(self):
        self.sign_in({"auth_mode": "chatgpt", "tokens": {}})
        before = self.rows()
        with self.assertRaisesRegex(codex_threads.Refused, "codex login"):
            codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.files["old-sol"].read_bytes(), self.originals["old-sol"])
        self.assertEqual(list(self.home.glob("*.before-excel-codex-*")), [])
        self.assertFalse(self.records.exists())

    def test_a_failed_change_is_rolled_back(self):
        before = self.rows()
        with mock.patch.object(codex_threads, "_write_record", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            codex_threads.migrate(self.home, self.records, now=NOW)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.files["old-sol"].read_bytes(), self.originals["old-sol"])

    def test_an_unreadable_record_stops_both_ways(self):
        self.records.mkdir()
        (self.records / codex_threads.RECORD_NAME).write_text("[]", encoding="utf-8")
        before = self.rows()
        for action in (codex_threads.migrate, codex_threads.undo):
            with self.subTest(action=action.__name__), self.assertRaises(codex_threads.Refused):
                action(self.home, self.records)
        self.assertEqual(self.rows(), before)


class StringSpanTests(unittest.TestCase):
    def test_strings_are_found_by_their_path(self):
        text = '{ "a" : [1, {"b": "x\\"y"}, true], "c": {"b": "z", "d": null}, "e": -1.5e3 }\n'
        spans = codex_threads._string_spans(text, {("a", 1, "b"), ("c", "b"), ("c", "d"), ("f",)})
        self.assertEqual({path: text[start:end] for path, (start, end) in spans.items()},
                         {("a", 1, "b"): '"x\\"y"', ("c", "b"): '"z"'})

    def test_a_line_that_is_not_json_is_refused(self):
        for text in ('{"a": }', '{"a" 1}', '["a" "b"]', '{"a": tru}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                codex_threads._string_spans(text, {("a",)})


class CodexRunningTests(unittest.TestCase):
    def ps(self, output=None, error=None, platform="linux"):
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        with mock.patch.object(codex_threads.sys, "platform", platform), \
                mock.patch.object(codex_threads.subprocess, "run", return_value=result, side_effect=error) as run, \
                mock.patch.dict(os.environ, {"EXCEL_BRIDGE_ASSUME_CODEX_QUIT": ""}):
            return codex_threads.codex_running(), run

    def test_posix_process_names(self):
        self.assertTrue(self.ps("launchd\n/Applications/Codex.app/Contents/MacOS/Codex\n")[0])
        self.assertTrue(self.ps("bash\ncodex\n")[0])
        running, run = self.ps("bash\n/Applications/Codex.app/Contents/MacOS/Codex Helper (Renderer)\nexcel-codex\n")
        self.assertFalse(running)
        self.assertEqual(run.call_args.args[0], ["ps", "-A", "-o", "comm="])

    def test_windows_process_names(self):
        self.assertTrue(self.ps('"System","4","Services","0","144 K"\n"Codex.exe","912","Console","1","90,112 K"\n',
                                platform="win32")[0])
        running, run = self.ps('"excel-codex.exe","12","Console","1","9 K"\n"EXCEL.EXE","40","Console","1","1 K"\n',
                               platform="win32")
        self.assertFalse(running)
        self.assertEqual(run.call_args.args[0][0], "tasklist")

    def test_an_unreadable_process_list_counts_as_running(self):
        self.assertTrue(self.ps(error=OSError("no ps"))[0])
        self.assertTrue(self.ps(error=subprocess.TimeoutExpired("ps", 15))[0])
        self.assertTrue(self.ps("")[0])

    def test_the_end_to_end_tests_can_say_codex_is_quit(self):
        with mock.patch.dict(os.environ, {"EXCEL_BRIDGE_ASSUME_CODEX_QUIT": "1"}), \
                mock.patch.object(codex_threads.subprocess, "run") as run:
            self.assertFalse(codex_threads.codex_running())
        run.assert_not_called()


class RelayIndex(CodexIndex):
    """Plus a conversation under a relay's provider, named `OpenAI` as sub2api's Codex config template does."""

    def setUp(self):
        super().setUp()
        self.add_thread("relay", "OpenAI", "gpt-5.6-sol")


class OtherProviderTests(RelayIndex):
    def test_listed_by_provider(self):
        self.assertEqual([thread.id for thread in codex_threads.threads_under(self.home, "OpenAI")], ["relay"])
        self.assertEqual(list(codex_threads.providers(self.home).items()),
                         [("excel-bridge", 3), ("OpenAI", 1), ("openai", 1), ("someone-else", 1)])

    def test_migrate_from_moves_that_provider_only(self):
        self.earlier_version_moved()
        before = {row[0]: row for row in self.rows()}
        result = codex_threads.migrate(self.home, self.records, source="OpenAI", now=NOW)
        self.assertEqual([thread.id for thread in result.threads], ["relay"])
        self.assertEqual((result.left, result.finished), ([], []))
        after = {row[0]: row for row in self.rows()}
        self.assertEqual(after.pop("relay"), ("relay", "openai", "gpt-5.6-sol", "high", "Through a relay"))
        self.assertEqual(after, {key: row for key, row in before.items() if key != "relay"})
        # Its models are OpenAI's already: only the provider changes, where Codex takes it from.
        moved = self.files["relay"].read_bytes()
        self.assertEqual(moved, shared(self.originals["relay"], "OpenAI", "gpt-5.6-sol", "gpt-5.6-sol"))
        self.assertEqual(rebuilt(self.files["relay"]), ("openai", "gpt-5.6-sol"))
        self.assertEqual(self.files["relay"].stat().st_mtime, MTIME)
        self.assertEqual(self.record()["threads"]["relay"], {
            "model_provider": "OpenAI", "model": "gpt-5.6-sol", "migrated_model": "gpt-5.6-sol",
            "lines": [[4, [[["payload", "thread_settings", "model_provider_id"], "OpenAI", "openai"]]]]})
        for thread_id in ("old-sol", "old-1m", "other"):
            self.assertEqual(self.files[thread_id].read_bytes(),
                             self.originals[thread_id] if thread_id != "old-1m" else
                             self.originals["old-1m"].replace(b'"model_provider":"excel-bridge"',
                                                              b'"model_provider":"openai"', 1))
        self.assertEqual(codex_threads.migrate(self.home, self.records, source="OpenAI", now=NOW).threads, [])

    def test_undo_puts_each_back_where_it_was(self):
        codex_threads.migrate(self.home, self.records, now=NOW)
        codex_threads.migrate(self.home, self.records, source="OpenAI", now=NOW + dt.timedelta(seconds=1))
        self.assertEqual(codex_threads.undo(self.home, self.records, now=NOW), codex_threads.Undone(4, 0))
        self.assertEqual({row[0]: row[1:3] for row in self.rows()}, {
            "official": ("openai", "gpt-6-sol"), "old-1m": ("excel-bridge", "gpt-6-sol-1m-excel"),
            "old-none": ("excel-bridge", None), "old-sol": ("excel-bridge", "gpt-6-sol-excel"),
            "other": ("someone-else", "gpt-5.6-sol-excel"), "relay": ("OpenAI", "gpt-5.6-sol"),
        })
        for thread_id, original in self.originals.items():
            with self.subTest(thread=thread_id):
                self.assertEqual(self.files[thread_id].read_bytes(), original)

    def test_bridge_models_under_another_provider_become_official_too(self):
        result = codex_threads.migrate(self.home, self.records, source="someone-else", now=NOW)
        self.assertEqual([thread.id for thread in result.threads], ["other"])
        self.assertEqual(self.files["other"].read_bytes(),
                         shared(self.originals["other"], "someone-else", "gpt-5.6-sol-excel", "gpt-5.6-sol"))
        self.assertEqual(rebuilt(self.files["other"]), ("openai", "gpt-5.6-sol"))

    def test_names_are_case_sensitive(self):
        self.assertEqual(codex_threads.migrate(self.home, self.records, source="openAI", now=NOW).threads, [])
        self.assertEqual(self.files["relay"].read_bytes(), self.originals["relay"])

    def test_refused_to_openai_or_without_a_sign_in(self):
        with self.assertRaisesRegex(codex_threads.Refused, "moved to"):
            codex_threads.migrate(self.home, self.records, source="openai", now=NOW)
        self.sign_in({})
        with self.assertRaisesRegex(codex_threads.Refused, "`codex login`"):
            codex_threads.migrate(self.home, self.records, source="OpenAI", now=NOW)
        self.assertEqual(self.files["relay"].read_bytes(), self.originals["relay"])
        self.assertEqual(self.rows()[-1][1], "OpenAI")


class ThreadsCommandTests(CodexIndex):
    def run_cli(self, *argv, env=None):
        out = io.StringIO()
        env = {"CODEX_HOME": str(self.home), "EXCEL_BRIDGE_HOME": str(self.records), **(env or {})}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            code = cli.main(["threads", *argv])
        return code, out.getvalue()

    def test_list_migrate_undo(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("3 conversation(s) are filed under the bridge's own provider", out)
        self.assertIn("Fix the parser  (gpt-6-sol-excel)", out)
        self.assertIn("old-none", out)
        self.assertIn("Model provider `excel-bridge` not found", out)
        self.assertIn("by themselves", out)
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("Moved 3 conversation(s)", out)
        self.assertIn("open with the bridge off too", out)
        self.assertIn(cli._1M_MOVED, out)
        self.assertIn("before-excel-codex-", out)
        code, out = self.run_cli()
        self.assertIn("No conversations are filed under the bridge's own provider", out)
        code, out = self.run_cli("migrate")
        self.assertIn("nothing to move", out)
        code, out = self.run_cli("undo")
        self.assertEqual(code, 0)
        self.assertIn("Put back 3 conversation(s)", out)
        code, out = self.run_cli("undo")
        self.assertIn("Nothing to undo", out)

    def test_ones_an_earlier_version_moved_are_listed_and_finished(self):
        self.earlier_version_moved()
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("1 conversation(s) moved by an earlier excel-codex still name the bridge's models", out)
        self.assertIn("No conversations are filed under the bridge's own provider", out)
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("Finished 1 conversation(s) moved by an earlier excel-codex", out)
        self.assertIn(cli._1M_MOVED, out)
        self.assertNotIn("Moved", out)
        self.assertEqual(self.rows()[1][1:3], ("openai", "gpt-6-sol"))
        code, out = self.run_cli("migrate")
        self.assertIn("nothing to move", out)
        code, out = self.run_cli()
        self.assertNotIn("earlier excel-codex", out)

    def test_nothing_changes_while_codex_runs(self):
        self.codex_running.return_value = True
        before = self.rows()
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed: Codex is running", out)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.files["old-sol"].read_bytes(), self.originals["old-sol"])
        self.codex_running.return_value = False
        self.run_cli("migrate")
        self.codex_running.return_value = True
        code, out = self.run_cli("undo")
        self.assertEqual(code, 1)
        self.assertIn("Codex is running", out)
        self.assertEqual(self.file_provider("old-sol"), "openai")

    def test_files_that_cannot_be_changed_are_named(self):
        for thread_id in ("old-sol", "old-1m", "old-none"):
            self.files[thread_id].unlink()
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn("Could not move 3 conversation(s)", out)
        self.assertIn("Fix the parser", out)
        self.assertNotIn("Moved", out)

    def test_refusal_exits_non_zero(self):
        self.sign_in({})
        code, out = self.run_cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed: Codex is not signed in", out)
        code, out = self.run_cli()
        self.assertIn("After `codex login`", out)


class OtherProviderCommandTests(RelayIndex):
    run_cli = ThreadsCommandTests.run_cli

    def test_listed_moved_and_put_back(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("Also filed under other providers: `OpenAI` (1), `someone-else` (1).", out)
        code, out = self.run_cli("--from", "OpenAI")
        self.assertEqual(code, 0)
        self.assertIn("1 conversation(s) are filed under `OpenAI`:", out)
        self.assertIn("Through a relay  (gpt-5.6-sol)", out)
        self.assertIn("Model provider `OpenAI` not found", out)
        self.assertIn("    excel-codex threads migrate --from OpenAI\n", out)
        code, out = self.run_cli("migrate", "--from", "OpenAI")
        self.assertEqual(code, 0)
        self.assertIn("Moved 1 conversation(s) from `OpenAI` to `openai`", out)
        self.assertIn("started with `OpenAI` from now on are filed under it again", out)
        self.assertNotIn(cli._1M_MOVED, out)
        self.assertIn("before-excel-codex-", out)
        self.assertEqual((self.file_provider("relay"), self.file_provider("old-sol")), ("openai", "excel-bridge"))
        code, out = self.run_cli("--from", "OpenAI", "migrate")
        self.assertEqual(code, 0)
        self.assertIn("No conversations are filed under `OpenAI`; nothing to move.", out)
        self.assertIn("`openai` (2)", out)
        code, out = self.run_cli("undo")
        self.assertIn("Put back 1 conversation(s)", out)
        self.assertEqual(self.file_provider("relay"), "OpenAI")

    def test_a_name_in_another_case_lists_what_there_is(self):
        code, out = self.run_cli("--from", "openAI")
        self.assertEqual(code, 0)
        self.assertIn("No conversations are filed under `openAI`.", out)
        self.assertIn("`OpenAI` (1)", out)
        self.assertIn("case-sensitive", out)
        code, out = self.run_cli("migrate", "--from", "openai")
        self.assertEqual(code, 0)
        self.assertIn("share already", out)
        self.assertEqual(self.files["relay"].read_bytes(), self.originals["relay"])

    def test_nothing_changes_while_codex_runs(self):
        self.codex_running.return_value = True
        code, out = self.run_cli("migrate", "--from", "OpenAI")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed: Codex is running", out)
        self.assertEqual(self.files["relay"].read_bytes(), self.originals["relay"])


class AutomaticMoveTests(CodexIndex):
    def move(self, env=None):
        out = io.StringIO()
        env = {"EXCEL_BRIDGE_HOME": str(self.records), "EXCEL_BRIDGE_AUTO_MIGRATE": "", **(env or {})}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            cli._move_bridge_threads(self.home)
        return out.getvalue()

    def test_moved_while_codex_is_quit(self):
        out = self.move()
        self.assertIn("Moved 3 conversation(s)", out)
        self.assertIn("`excel-codex threads undo`", out)
        self.assertEqual(self.file_provider("old-sol"), "openai")
        self.assertEqual(self.move(), "")

    def test_ones_an_earlier_version_moved_are_finished(self):
        self.earlier_version_moved()
        out = self.move()
        self.assertIn("Finished 1 conversation(s) moved by an earlier excel-codex", out)
        self.assertIn(cli._1M_MOVED, out)
        self.assertNotIn("Moved", out)
        self.assertEqual(self.move(), "")

    def test_ones_an_earlier_version_moved_wait_while_codex_runs(self):
        self.earlier_version_moved()
        self.codex_running.return_value = True
        out = self.move()
        self.assertIn("1 conversation(s) moved by an earlier excel-codex still name the bridge's models", out)
        self.assertNotIn("under the bridge's own provider", out)
        self.assertEqual(self.move({"EXCEL_BRIDGE_AUTO_MIGRATE": "0"}).count("threads migrate` finishes them"), 1)

    def test_left_alone_while_codex_runs(self):
        self.codex_running.return_value = True
        out = self.move()
        self.assertIn("3 conversation(s) under the bridge's own provider open only while it is on", out)
        self.assertIn("fully quit", out)
        self.assertEqual(self.file_provider("old-sol"), "excel-bridge")

    def test_can_be_turned_off(self):
        out = self.move({"EXCEL_BRIDGE_AUTO_MIGRATE": "0"})
        self.assertIn("`excel-codex threads migrate`", out)
        self.assertEqual(self.file_provider("old-sol"), "excel-bridge")
        self.codex_running.assert_not_called()

    def test_nothing_to_say_without_bridge_conversations(self):
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DELETE FROM threads WHERE model_provider = 'excel-bridge'")
        self.assertEqual(self.move(), "")
        self.codex_running.assert_not_called()

    def test_a_failure_is_reported_not_raised(self):
        with mock.patch.object(codex_threads, "migrate", side_effect=sqlite3.OperationalError("database is locked")):
            out = self.move()
        self.assertIn("Could not move the bridge's own conversations into the shared list: database is locked", out)


if __name__ == "__main__":
    unittest.main()
