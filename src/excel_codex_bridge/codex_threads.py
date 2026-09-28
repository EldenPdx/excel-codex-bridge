"""Put the bridge's own conversations into Codex's shared list.

Codex lists and continues a conversation under the provider it was started
with.  Up to 0.5.3, and whenever Codex is not signed in, the bridge is a
provider of its own (``excel-bridge``); a signed-in Codex reaches it through
its own ``openai`` provider (see ``codex_config``), so those conversations are
not in the shared list, and with the bridge off Codex cannot open them at all
("Model provider `excel-bridge` not found").  ``migrate`` files them under
``openai``, with models OpenAI serves (``official_model``: 1M ones too);
``excel-codex desktop`` and ``excel-codex`` do it by themselves while Codex is
not running.

Codex continues a conversation with the provider and model of its index row
(the ``threads`` table in ``state_<n>.sqlite``), and rebuilds that row from the
conversation's file (``sessions/**/rollout-*.jsonl``): the provider from its
first line, then the model, and the provider again, from its latest
``turn_context`` and ``thread_settings_applied`` lines.  So the row changes,
and so do those values in the file, each string in place and nothing else.
Codex must not be running: it would put the index back, and could be writing
to the file.  The index is copied first and every change is recorded, so
``undo`` puts back exactly what was changed.

``migrate(source=...)`` does the same for conversations under another
provider that Codex cannot open any more, such as ``OpenAI``, the name a
relay's Codex config template may give its provider.
"""

from __future__ import annotations

import contextlib
import copy
import csv
import datetime as dt
import json
import os
import re
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
# Later lines Codex rebuilds a conversation's index row from name one of these.
_NAMING_LINES = (b'"turn_context"', b'"thread_settings_applied"')
_SPACE = re.compile(r"[ \t\n\r]*")
_SCALAR = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")
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
    # Moved by an earlier migrate (up to 0.5.8), whose file still named the bridge's models or provider.
    finished: list[Thread] = field(default_factory=list)


@dataclass(frozen=True)
class _Pending:
    thread: Thread
    # The file the index names.
    indexed: str | None
    # Moved by an earlier migrate: its row is under ``openai`` already.
    earlier: bool
    # Its file's first line still names the bridge (0.5.4 and 0.5.5 moved the row only).
    bridge_file: bool


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
    return codex_seen() is not False


def codex_seen() -> bool | None:
    """Whether a Codex process is running; None when the process list cannot be read."""
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
        return None
    names = [name.strip().lower() for name in names if name.strip()]
    return any(name in _CODEX_PROCESSES for name in names) if names else None


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


def _refiled(line: bytes, meta: dict, thread_id: str, old: str, new: str) -> bytes | None:
    """A conversation file's first line naming ``new`` as its provider, if it names ``old`` in one place."""
    found = [match for match in _PROVIDER_FIELD.finditer(line) if _json_string(match.group(1)) == old]
    if len(found) != 1:
        return None
    changed = line[:found[0].start(1)] + json.dumps(new)[1:-1].encode() + line[found[0].end(1):]
    if _session_meta(changed, thread_id) != {**meta, "payload": {**meta["payload"], "model_provider": new}}:
        return None
    return changed


def _at(value: object, path: tuple) -> object:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _string_spans(text: str, wanted: set[tuple]) -> dict[tuple, tuple[int, int]]:
    """Where the string at each of the ``wanted`` paths sits in the JSON ``text``, quotes included."""
    found: dict[tuple, tuple[int, int]] = {}

    def space(i: int) -> int:
        return _SPACE.match(text, i).end()

    def value(i: int, path: tuple) -> int:
        i = space(i)
        char = text[i:i + 1]
        if char == '"':
            end = json.decoder.scanstring(text, i + 1)[1]
            if path in wanted:
                found[path] = (i, end)
            return end
        if char in ("{", "["):
            close = "}" if char == "{" else "]"
            i = space(i + 1)
            if text[i:i + 1] == close:
                return i + 1
            index = 0
            while True:
                if char == "{":
                    i = space(i)
                    if text[i:i + 1] != '"':
                        raise ValueError(f"expected a key at {i}")
                    key, i = json.decoder.scanstring(text, i + 1)
                    i = space(i)
                    if text[i:i + 1] != ":":
                        raise ValueError(f"expected ':' at {i}")
                    i = value(i + 1, (*path, key))
                else:
                    i = value(i, (*path, index))
                    index += 1
                i = space(i)
                if text[i:i + 1] == ",":
                    i += 1
                elif text[i:i + 1] == close:
                    return i + 1
                else:
                    raise ValueError(f"expected ',' or {close!r} at {i}")
        scalar = _SCALAR.match(text, i)
        if scalar is None:
            raise ValueError(f"unexpected {char!r} at {i}")
        return scalar.end()

    value(0, ())
    return found


def _edited(line: bytes, edits: dict[tuple, tuple[str, str]]) -> bytes | None:
    """``line`` with the string at each path changed from its old value to its new one.

    None unless each is there, written plainly, and the line then reads back
    with those values changed and nothing else.
    """
    try:
        text = line.decode("utf-8")
        meta = json.loads(text)
        spans = _string_spans(text, set(edits))
    except (ValueError, RecursionError):
        return None
    expected = copy.deepcopy(meta)
    for path, (old, new) in edits.items():
        if path not in spans or _at(meta, path) != old:
            return None
        start, end = spans[path]
        # So that undo writes back the very same bytes.
        if text[start:end] != json.dumps(old, ensure_ascii=False):
            return None
        _at(expected, path[:-1])[path[-1]] = new
    for path, (start, end) in sorted(spans.items(), key=lambda span: span[1][0], reverse=True):
        text = text[:start] + json.dumps(edits[path][1], ensure_ascii=False) + text[end:]
    try:
        return text.encode("utf-8") if json.loads(text) == expected else None
    except ValueError:
        return None


def _shared_edits(meta: object, source: str = codex_config.PROVIDER_ID) -> dict[tuple, tuple[str, str]]:
    """What a later line of a conversation file changes to under ``openai``: {path: (old, new)}."""
    if not isinstance(meta, dict):
        return {}
    provider = None
    if meta.get("type") == "turn_context":
        base: tuple = ("payload",)
    elif meta.get("type") == "event_msg" and _at(meta, ("payload", "type")) == "thread_settings_applied":
        base = ("payload", "thread_settings")
        provider = (*base, "model_provider_id")
    else:
        return {}
    edits = {}
    for path in ((*base, "model"), (*base, "collaboration_mode", "settings", "model")):
        model = _at(meta, path)
        if isinstance(model, str) and codex_config.official_model(model) != model:
            edits[path] = (model, codex_config.official_model(model))
    if provider and _at(meta, provider) == source:
        edits[provider] = (source, codex_config.OPENAI_PROVIDER_ID)
    return edits


def _planned(path: Path, source: str = codex_config.PROVIDER_ID) -> list:
    """The later lines of a conversation file that name the bridge's models or ``source``.

    As recorded: ``[[line number, [[path, old, new], ...]], ...]``.
    """
    changes = []
    with path.open("rb") as handle:
        handle.readline()
        for number, line in enumerate(handle, 1):
            if not any(marker in line for marker in _NAMING_LINES):
                continue
            try:
                meta = json.loads(line)
            except ValueError:
                continue
            edits = _shared_edits(meta, source)
            if edits:
                changes.append([number, [[list(key), old, new] for key, (old, new) in edits.items()]])
    return changes


class _Unchangeable(Exception):
    pass


def _rewrite(path: Path, thread_id: str, old: str, new: str, changes: list) -> bool:
    """Make a conversation file name ``new`` as its provider, with ``changes`` made; whether it does now.

    The first line may name ``new`` already.  Unless every change can be made,
    the file stays as it is; otherwise everything else stays byte for byte,
    down to the modification time.
    """
    edits = {number: {tuple(key): (before, after) for key, before, after in line}
             for number, line in changes}
    with path.open("rb") as source:
        first = source.readline()
        meta = _session_meta(first, thread_id)
        if meta is None:
            return False
        provider = meta["payload"].get("model_provider")
        if provider == old:
            first = _refiled(first, meta, thread_id, old, new)
            if first is None:
                return False
        elif provider != new:
            return False
        elif not edits:
            return True
        stat = os.fstat(source.fileno())
        fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(first)
                made = 0
                for number, line in enumerate(source, 1):
                    if number in edits:
                        line = _edited(line, edits[number])
                        if line is None:
                            raise _Unchangeable
                        made += 1
                    target.write(line)
                if made != len(edits):
                    raise _Unchangeable
        except BaseException as exc:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            if isinstance(exc, _Unchangeable):
                return False
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


def _refile_thread(home: Path, thread_id: str, indexed: str | None, old: str, new: str,
                   changes: list = ()) -> bool:
    """Make a conversation's file name ``new`` as its provider, with ``changes`` made; whether it does now."""
    path = _rollout(home, thread_id, indexed)
    if path is None:
        return False
    try:
        return _rewrite(path, thread_id, old, new, list(changes))
    except OSError:
        return False


def _pending(home: Path, index: Path, record: dict | None,
             source: str = codex_config.PROVIDER_ID) -> list[_Pending]:
    """Conversations under ``source`` to move, newest first, then those an earlier migrate did not finish."""
    columns = "SELECT id, title, model, rollout_path FROM threads WHERE"
    found = []
    with contextlib.closing(_connect(index, read_only=True)) as db:
        for row in db.execute(f"{columns} model_provider = ? ORDER BY updated_at DESC", (source,)):
            found.append(_Pending(Thread(*row[:3]), row[3], earlier=False, bridge_file=True))
        if source != codex_config.PROVIDER_ID:
            return found
        # Up to 0.5.8 the model and provider on later lines stayed, and 0.5.4 and 0.5.5 moved the row only:
        # Codex puts back what it rebuilds from the file.
        for thread_id, entry in (record or {}).get("threads", {}).items():
            if not isinstance(entry, dict) or "lines" in entry:
                continue
            row = db.execute(f"{columns} id = ? AND model_provider = ?",
                             (thread_id, codex_config.OPENAI_PROVIDER_ID)).fetchone()
            path = _rollout(home, thread_id, row[3]) if row else None
            if path is None:
                continue
            try:
                bridge_file = _file_provider(path, thread_id) == codex_config.PROVIDER_ID
            except OSError:
                continue
            found.append(_Pending(Thread(*row[:3]), row[3], earlier=True, bridge_file=bridge_file))
    return found


def bridge_threads(home: Path, record_dir: Path | None = None) -> list[Thread]:
    """Conversations Codex files under the bridge's own provider, newest first.

    With ``record_dir``, also those an earlier ``migrate`` moved in the index
    only, whose file still names the bridge.
    """
    record = _read_record(record_dir / RECORD_NAME) if record_dir is not None else None
    return [item.thread for item in _pending(home, _index(home), record) if item.bridge_file]


def threads_under(home: Path, provider: str) -> list[Thread]:
    """Conversations Codex files under ``provider``, newest first."""
    return [item.thread for item in _pending(home, _index(home), None, provider)]


def providers(home: Path) -> dict[str, int]:
    """How many conversations Codex files under each provider, most first."""
    with contextlib.closing(_connect(_index(home), read_only=True)) as db:
        return dict(db.execute(
            "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider ORDER BY COUNT(*) DESC, model_provider"
        ).fetchall())


def unfinished_threads(home: Path, record_dir: Path) -> list[Thread]:
    """Conversations an earlier ``migrate`` (up to 0.5.8) moved, whose file it left naming the bridge's models."""
    record = _read_record(record_dir / RECORD_NAME)
    return [item.thread for item in _pending(home, _index(home), record) if not item.bridge_file]


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


def migrate(home: Path, record_dir: Path, *, source: str = codex_config.PROVIDER_ID,
            now: dt.datetime | None = None) -> Migrated:
    """File the conversations under ``source`` (the bridge's own) under ``openai``; the index is copied first.

    Only while Codex is not running (see ``codex_running``).
    """
    if source == codex_config.OPENAI_PROVIDER_ID:
        raise Refused(f"`{source}` is where conversations are moved to.")
    index = _index(home)
    if not codex_config.codex_signed_in(home):
        if source == codex_config.PROVIDER_ID:
            raise Refused(
                "Codex is not signed in, so the bridge still is a provider of its own and lists these "
                "conversations while it is on. Run `codex login` first to share them with the official sign-in."
            )
        raise Refused(f"Codex is not signed in, so it could not carry on with conversations under "
                      f"`{codex_config.OPENAI_PROVIDER_ID}`. Run `codex login` first.")
    record_path = record_dir / RECORD_NAME
    record = _read_record(record_path)
    pending = _pending(home, index, record, source)
    if not pending:
        return Migrated([], None)

    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = index.with_name(f"{index.name}.before-excel-codex-{stamp}")
    with contextlib.closing(_connect(index)) as db, contextlib.closing(sqlite3.connect(backup)) as target:
        db.backup(target)

    earlier = dict(record["threads"])
    moved, finished, left = [], [], []
    plans: dict[str, list | None] = {}
    for item in pending:
        path = _rollout(home, item.thread.id, item.indexed)
        try:
            plans[item.thread.id] = _planned(path, source) if path is not None else None
        except OSError:
            plans[item.thread.id] = None
    with contextlib.closing(_connect(index)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for item in pending:
                thread = item.thread
                # A row moved back by Codex keeps what it had before the first migration.
                entry = earlier.get(thread.id) or {"model_provider": source, "model": thread.model}
                migrated_model = entry.get("migrated_model")
                # Carried on with another model since an earlier migrate: undo leaves it as it is.
                if not item.earlier or thread.model == migrated_model or _official(thread.model) != thread.model:
                    migrated_model = _official(thread.model)
                record["threads"][thread.id] = {**entry, "migrated_model": migrated_model,
                                                "lines": _merged(entry.get("lines"), plans[thread.id])}
            record["backups"].append(str(backup))
            # Recorded before anything changes: undo skips what did not.
            _write_record(record_path, record)
            for item in pending:
                thread = item.thread
                plan = plans[thread.id]
                if plan is None or not _refile_thread(home, thread.id, item.indexed, source,
                                                      codex_config.OPENAI_PROVIDER_ID, plan):
                    left.append(thread)
                    continue
                db.execute(
                    "UPDATE threads SET model_provider = ?, model = ? WHERE id = ?",
                    (codex_config.OPENAI_PROVIDER_ID, _official(thread.model), thread.id),
                )
                (moved if item.bridge_file else finished).append(thread)
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
    return Migrated(moved, backup, left, finished)


def _merged(earlier: object, plan: list | None) -> list:
    """Line changes an earlier migrate recorded, then ``plan``'s: undo puts back the first value of each."""
    lines: dict[int, dict[tuple, list]] = {}
    for number, line in [*(earlier if isinstance(earlier, list) else []), *(plan or [])]:
        edits = lines.setdefault(number, {})
        for key, before, after in line:
            edits[tuple(key)] = [edits.get(tuple(key), [before])[0], after]
    return [[number, [[list(key), before, after] for key, (before, after) in edits.items()]]
            for number, edits in sorted(lines.items())]


def _official(model: str | None) -> str | None:
    return codex_config.official_model(model) if model else model


def _reversed(changes: object) -> list:
    """Recorded line changes, the other way round."""
    return [[number, [[key, after, before] for key, before, after in line]] for number, line in changes]


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
                try:
                    changes = _reversed(entry.get("lines", []))
                except (TypeError, ValueError):
                    continue
                if row is None or not _refile_thread(
                    home, thread_id, row[0], codex_config.OPENAI_PROVIDER_ID, provider, changes
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
