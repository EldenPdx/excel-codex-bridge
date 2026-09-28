"""Put the bridge's own conversations into Codex's shared list.

Codex lists and continues a conversation under the provider it was started
with.  Up to 0.5.3, and whenever Codex is not signed in, the bridge is a
provider of its own (``excel-bridge``); a signed-in Codex reaches it through
its own ``openai`` provider (see ``codex_config``), so those conversations are
not in the shared list, and with the bridge off Codex cannot open them at all
("Model provider `excel-bridge` not found").  ``migrate`` files them under
``openai``, with the model names Codex now uses; ``excel-codex desktop`` and
``excel-codex`` do it by themselves while Codex is not running.

Codex takes a conversation's provider from the first line of its file
(``sessions/**/rollout-*.jsonl``) and rebuilds its index row from there, so
both change: the ``threads`` row in ``state_<n>.sqlite``, and the provider on
that first line, byte for byte and nothing else.  Codex must not be running:
it would put the index back, and could be writing to the file.  The index is
copied first and every change is recorded, so ``undo`` puts back exactly what
was changed.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import codex_config

RECORD_NAME = "threads-migrated.json"
_STATE = re.compile(r"state_(\d+)\.sqlite")
_PROVIDER_FIELD = re.compile(rb'"model_provider"\s*:\s*"((?:[^"\\]|\\.)*)"')
# Process names of the Codex desktop app, and of the CLI that the IDE extension runs too.
_CODEX_PROCESSES = {"codex", "codex.exe"}
_CREATE_NO_WINDOW = 0x08000000


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
    # Conversations whose file could not be changed (missing, or not laid out as expected): left as they are.
    left: list[Thread] = field(default_factory=list)


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


def codex_running() -> bool:
    """Whether the Codex desktop app, an IDE's Codex or the ``codex`` CLI is running.

    True when the process list cannot be read, so that nothing is changed then.
    """
    if os.environ.get("EXCEL_BRIDGE_ASSUME_CODEX_QUIT", "").strip() == "1":  # for the end-to-end tests
        return False
    try:
        if sys.platform == "win32":
            output = subprocess.run(
                ["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True, errors="replace",
                timeout=15, creationflags=_CREATE_NO_WINDOW,
            ).stdout
            names = [row[0] for row in csv.reader(output.splitlines()) if row]
        else:
            output = subprocess.run(
                ["ps", "-A", "-o", "comm="], capture_output=True, text=True, errors="replace", timeout=15
            ).stdout
            names = [line.strip().rsplit("/", 1)[-1] for line in output.splitlines()]
    except (OSError, subprocess.SubprocessError):
        return True
    names = [name.strip().lower() for name in names if name.strip()]
    return not names or any(name in _CODEX_PROCESSES for name in names)


def _rollout(home: Path, thread_id: str, indexed: str | None) -> Path | None:
    """A conversation's file: where the index says, else found by its id."""
    if indexed and Path(indexed).is_file():
        return Path(indexed)
    if not re.fullmatch(r"[\w-]+", thread_id):
        return None
    for folder in ("sessions", "archived_sessions"):
        for path in sorted((home / folder).rglob(f"rollout-*{thread_id}.jsonl")):
            return path
    return None


def _session_meta(line: bytes, thread_id: str) -> dict | None:
    """The first line of ``thread_id``'s conversation file, parsed; None if it is not that."""
    try:
        meta = json.loads(line)
    except ValueError:
        return None
    payload = meta.get("payload") if isinstance(meta, dict) else None
    if meta.get("type") != "session_meta" or not isinstance(payload, dict) or payload.get("id") != thread_id:
        return None
    return meta


def _file_provider(path: Path, thread_id: str) -> str | None:
    with path.open("rb") as handle:
        meta = _session_meta(handle.readline(), thread_id)
    return meta["payload"].get("model_provider") if meta else None


def _json_string(content: bytes) -> str | None:
    try:
        return json.loads(b'"' + content + b'"')
    except ValueError:
        return None


def _refile(path: Path, thread_id: str, old: str, new: str) -> bool:
    """Change the provider on the first line of a conversation file from ``old`` to ``new``.

    False, with the file untouched, unless that line is this conversation's and
    names ``old`` in one place.  Everything else stays as it was, byte for
    byte, down to the modification time.
    """
    with path.open("rb") as source:
        line = source.readline()
        meta = _session_meta(line, thread_id)
        if meta is None or meta["payload"].get("model_provider") != old:
            return False
        found = [match for match in _PROVIDER_FIELD.finditer(line) if _json_string(match.group(1)) == old]
        if len(found) != 1:
            return False
        changed = line[:found[0].start(1)] + json.dumps(new)[1:-1].encode() + line[found[0].end(1):]
        if _session_meta(changed, thread_id) != {**meta, "payload": {**meta["payload"], "model_provider": new}}:
            return False
        stat = os.fstat(source.fileno())
        fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(changed)
                shutil.copyfileobj(source, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise
    try:
        os.chmod(temp, stat.st_mode & 0o7777)
        os.replace(temp, path)  # once the original is closed: Windows cannot replace an open file
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    return True


def _refile_thread(home: Path, thread_id: str, indexed: str | None, old: str, new: str) -> bool:
    """Make a conversation's file name ``new`` as its provider; whether it does now."""
    path = _rollout(home, thread_id, indexed)
    if path is None:
        return False
    try:
        return _refile(path, thread_id, old, new) or _file_provider(path, thread_id) == new
    except OSError:
        return False


def _pending(home: Path, index: Path, record: dict | None) -> list[tuple[Thread, str | None]]:
    """Conversations to move, newest first, each with the file the index names."""
    columns = "SELECT id, title, model, rollout_path FROM threads WHERE"
    with contextlib.closing(_connect(index, read_only=True)) as db:
        rows = db.execute(
            f"{columns} model_provider = ? ORDER BY updated_at DESC", (codex_config.PROVIDER_ID,)
        ).fetchall()
        # 0.5.4 and 0.5.5 moved the index row only; Codex puts it back from the file when it reads it.
        for thread_id in (record or {}).get("threads", {}):
            row = db.execute(f"{columns} id = ? AND model_provider = ?",
                             (thread_id, codex_config.OPENAI_PROVIDER_ID)).fetchone()
            path = _rollout(home, thread_id, row[3]) if row else None
            with contextlib.suppress(OSError):
                if path is not None and _file_provider(path, thread_id) == codex_config.PROVIDER_ID:
                    rows.append(row)
    return [(Thread(*row[:3]), row[3]) for row in rows]


def bridge_threads(home: Path, record_dir: Path | None = None) -> list[Thread]:
    """Conversations Codex files under the bridge's own provider, newest first.

    With ``record_dir``, also those an earlier ``migrate`` moved in the index
    only, whose file still names the bridge.
    """
    record = _read_record(record_dir / RECORD_NAME) if record_dir is not None else None
    return [thread for thread, _ in _pending(home, _index(home), record)]


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
    """File the bridge's own conversations under ``openai``; the index is copied first.

    Only while Codex is not running (see ``codex_running``).
    """
    index = _index(home)
    if not codex_config.codex_signed_in(home):
        raise Refused(
            "Codex is not signed in, so the bridge still is a provider of its own and lists these "
            "conversations while it is on. Run `codex login` first to share them with the official sign-in."
        )
    record_path = record_dir / RECORD_NAME
    record = _read_record(record_path)
    pending = _pending(home, index, record)
    if not pending:
        return Migrated([], None)

    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = index.with_name(f"{index.name}.before-excel-codex-{stamp}")
    with contextlib.closing(_connect(index)) as db, contextlib.closing(sqlite3.connect(backup)) as copy:
        db.backup(copy)

    earlier = dict(record["threads"])
    moved, left = [], []
    with contextlib.closing(_connect(index)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for thread, _ in pending:
                # A row moved back by Codex keeps what it had before the first migration.
                entry = earlier.get(thread.id) or {"model_provider": codex_config.PROVIDER_ID, "model": thread.model}
                record["threads"][thread.id] = {**entry, "migrated_model": _codex_model(thread.model)}
            record["backups"].append(str(backup))
            # Recorded before anything changes: undo skips what did not.
            _write_record(record_path, record)
            for thread, indexed in pending:
                if not _refile_thread(home, thread.id, indexed, codex_config.PROVIDER_ID,
                                      codex_config.OPENAI_PROVIDER_ID):
                    left.append(thread)
                    continue
                db.execute(
                    "UPDATE threads SET model_provider = ?, model = ? WHERE id = ?",
                    (codex_config.OPENAI_PROVIDER_ID, _codex_model(thread.model), thread.id),
                )
                moved.append(thread)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    if left:
        for thread in left:
            if thread.id in earlier:
                record["threads"][thread.id] = earlier[thread.id]
            else:
                record["threads"].pop(thread.id, None)
        _write_record(record_path, record)
    return Migrated(moved, backup, left)


def _codex_model(model: str | None) -> str | None:
    return codex_config.codex_model(model) if model else model


def undo(home: Path, record_dir: Path, *, now: dt.datetime | None = None) -> Undone:
    """Put back what ``migrate`` changed, where Codex has not changed it since.

    Only while Codex is not running (see ``codex_running``).
    """
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
                row = db.execute(
                    "SELECT rollout_path FROM threads WHERE id = ? AND model_provider = ? AND model IS ?",
                    (thread_id, codex_config.OPENAI_PROVIDER_ID, entry.get("migrated_model")),
                ).fetchone()
                provider = entry.get("model_provider") or codex_config.PROVIDER_ID
                if row is None or not _refile_thread(
                    home, thread_id, row[0], codex_config.OPENAI_PROVIDER_ID, provider
                ):
                    continue
                db.execute(
                    "UPDATE threads SET model_provider = ?, model = ? WHERE id = ?",
                    (provider, entry.get("model"), thread_id),
                )
                restored += 1
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    record_path.replace(record_path.with_name(f"{RECORD_NAME}.undone-{stamp}"))
    return Undone(restored, len(record["threads"]) - restored)
