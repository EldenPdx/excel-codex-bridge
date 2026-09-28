"""Put the bridge's conversations from 0.5.3 and earlier into Codex's shared list.

Codex lists and continues a conversation under the provider it was started
with.  Up to 0.5.3 the bridge was a provider of its own (``excel-bridge``);
from 0.5.4 a signed-in Codex reaches it through its own ``openai`` provider
(see ``codex_config``), so those earlier conversations are no longer listed.
``excel-codex threads migrate`` files them under ``openai``, with the model
names Codex now uses.

Only Codex's conversation index changes (the ``threads`` table in
``state_<n>.sqlite``); the conversations themselves (``sessions/*.jsonl``) are
never touched.  The index is copied first and every change is recorded, so
``excel-codex threads undo`` puts back exactly the rows it changed.  Codex
rebuilds a row from its conversation file when the conversation is renamed or
archived, which files a conversation that has not been continued since under
``excel-bridge`` again; ``migrate`` moves it back.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import codex_config

RECORD_NAME = "threads-migrated.json"
_STATE = re.compile(r"state_(\d+)\.sqlite")


class Refused(RuntimeError):
    """Nothing was changed; the message says why."""


@dataclass(frozen=True)
class Thread:
    id: str
    title: str
    model: str | None


@dataclass(frozen=True)
class Migrated:
    threads: list[Thread]
    backup: Path | None


@dataclass(frozen=True)
class Undone:
    restored: int
    # Rows Codex changed after the migration (another model, deleted): left as they are.
    kept: int


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    # Transactions are begun and ended here, not by the sqlite3 module.
    return sqlite3.connect(path, timeout=10, isolation_level=None)


def state_db(home: Path) -> Path | None:
    """Codex's conversation index: the newest ``state_<n>.sqlite`` with a threads table."""
    found = sorted(
        ((int(match.group(1)), path) for path in home.glob("state_*.sqlite")
         if (match := _STATE.fullmatch(path.name))),
        reverse=True,
    )
    for _, path in found:
        try:
            with contextlib.closing(_connect(path, read_only=True)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
        except sqlite3.Error:
            continue
        if {"id", "model_provider", "model", "title"} <= columns:
            return path
    return None


def _index(home: Path) -> Path:
    path = state_db(home)
    if path is None:
        raise Refused(f"Codex has no conversation index (state_*.sqlite) in {home}.")
    return path


def bridge_threads(home: Path) -> list[Thread]:
    """Conversations filed under the bridge's own provider, newest first."""
    with contextlib.closing(_connect(_index(home), read_only=True)) as db:
        rows = db.execute(
            "SELECT id, title, model FROM threads WHERE model_provider = ? ORDER BY updated_at DESC",
            (codex_config.PROVIDER_ID,),
        ).fetchall()
    return [Thread(*row) for row in rows]


def _read_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"threads": {}, "backups": []}
    except (OSError, ValueError) as exc:
        raise Refused(f"{path} could not be read ({exc}); move it away to go on.") from exc
    if not isinstance(record, dict) or not isinstance(record.get("threads"), dict):
        raise Refused(f"{path} is not a record excel-codex wrote; move it away to go on.")
    record.setdefault("backups", [])
    return record


def _write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def migrate(home: Path, record_dir: Path, *, now: dt.datetime | None = None) -> Migrated:
    """File the bridge's own conversations under ``openai``; the index is copied first."""
    index = _index(home)
    if not codex_config.codex_signed_in(home):
        raise Refused(
            "Codex is not signed in, so the bridge still is a provider of its own and lists these "
            "conversations while it is on. Run `codex login` first to share them with the official sign-in."
        )
    threads = bridge_threads(home)
    if not threads:
        return Migrated([], None)
    record_path = record_dir / RECORD_NAME
    record = _read_record(record_path)

    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = index.with_name(f"{index.name}.before-excel-codex-{stamp}")
    with contextlib.closing(_connect(index)) as db, contextlib.closing(sqlite3.connect(backup)) as copy:
        db.backup(copy)

    moved = []
    with contextlib.closing(_connect(index)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute(
                "SELECT id, title, model FROM threads WHERE model_provider = ?", (codex_config.PROVIDER_ID,)
            ).fetchall()
            for thread_id, title, model in rows:
                new_model = codex_config.codex_model(model) if model else model
                db.execute(
                    "UPDATE threads SET model_provider = ?, model = ? WHERE id = ?",
                    (codex_config.OPENAI_PROVIDER_ID, new_model, thread_id),
                )
                # A row moved back by Codex keeps what it had before the first migration.
                entry = record["threads"].get(thread_id) or {"model_provider": codex_config.PROVIDER_ID, "model": model}
                record["threads"][thread_id] = {**entry, "migrated_model": new_model}
                moved.append(Thread(thread_id, title, model))
            record["backups"].append(str(backup))
            # Recorded before the change is committed: undo skips rows that did not change.
            _write_record(record_path, record)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return Migrated(moved, backup)


def undo(home: Path, record_dir: Path, *, now: dt.datetime | None = None) -> Undone:
    """Put back the rows ``migrate`` changed, where Codex has not changed them since."""
    record_path = record_dir / RECORD_NAME
    record = _read_record(record_path)
    if not record["threads"]:
        return Undone(0, 0)
    index = _index(home)
    restored = 0
    with contextlib.closing(_connect(index)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for thread_id, entry in record["threads"].items():
                restored += db.execute(
                    "UPDATE threads SET model_provider = ?, model = ? "
                    "WHERE id = ? AND model_provider = ? AND model IS ?",
                    (entry.get("model_provider"), entry.get("model"), thread_id,
                     codex_config.OPENAI_PROVIDER_ID, entry.get("migrated_model")),
                ).rowcount
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    record_path.replace(record_path.with_name(f"{RECORD_NAME}.undone-{stamp}"))
    return Undone(restored, len(record["threads"]) - restored)
