"""Small transactional ledger and immutable, addressable evidence artifacts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode("utf-8")).hexdigest()


def clean(value):
    """Convert result-only non-finite diagnostics to explicit null, never specs."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serial(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive_lock(path):
    """OS-released lock: a stale filename is not interpreted as a live owner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise RuntimeError("Another live owner holds this operation lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "study.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, spec TEXT NOT NULL,
                    status TEXT NOT NULL, evidence_id TEXT, artifact_dir TEXT,
                    error TEXT, executions INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                    run_id TEXT, name TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
                    error TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS model_calls(id TEXT PRIMARY KEY,
                    status TEXT NOT NULL, directory TEXT NOT NULL, response TEXT,
                    usage TEXT, error TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,
                    action TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                    created REAL NOT NULL);
            """)
            if "artifact_manifest_sha256" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN artifact_manifest_sha256 TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_meta(db, key, value):
        db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, serial(value)))

    @staticmethod
    def event(db, kind, payload):
        cursor = db.execute("INSERT INTO events(kind,payload,created) VALUES (?,?,?)",
                            (kind, serial(clean(payload)), time.time()))
        return cursor.lastrowid

    def rows(self, query, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params)]

    def put_evidence(self, kind, value):
        payload = clean(value)
        evidence_id = "ev_" + digest({"kind": kind, "value": payload})
        path = self.root / "evidence" / (evidence_id + ".json")
        if not path.exists():
            write_json(path, payload)
        raw = path.read_bytes()
        if raw.decode("utf-8") != serial(payload):
            raise ValueError("Existing immutable evidence has changed")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?)",
                       (evidence_id, kind, str(path.relative_to(self.root)),
                        hashlib.sha256(raw).hexdigest(), len(raw), time.time()))
        return evidence_id

    def evidence(self, evidence_id, *, pointer="", limit=20, offset=0, max_bytes=12000):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("evidence limit must be 1..100")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("evidence offset must be nonnegative")
        rows = self.rows("SELECT * FROM evidence WHERE id=?", (evidence_id,))
        if not rows:
            raise KeyError("Unknown evidence ID")
        row = rows[0]
        path = (self.root / row["path"]).resolve()
        if not path.is_relative_to(self.root / "evidence"):
            raise ValueError("Evidence path escaped store")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("Evidence digest mismatch")
        value = json.loads(raw)
        if pointer:
            if not isinstance(pointer, str) or not pointer.startswith("/"):
                raise ValueError("Use a JSON pointer beginning with /")
            for segment in pointer[1:].split("/"):
                key = segment.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
        total = len(value) if isinstance(value, (dict, list, str)) else None
        if isinstance(value, (list, str)):
            value = value[offset:offset + limit]
        elif isinstance(value, dict):
            value = dict(list(value.items())[offset:offset + limit])
        answer = {"id": evidence_id, "pointer": pointer, "offset": offset,
                  "total": total, "value": value}
        if len(serial(answer).encode("utf-8")) > max_bytes:
            answer = {"id": evidence_id, "pointer": pointer[:256], "total": total,
                      "status": "narrow_query_required", "keys": [str(k)[:80] for k in list(value)[:limit]]
                      if isinstance(value, dict) else None, "max_bytes": max_bytes}
            if len(serial(answer).encode("utf-8")) > max_bytes:
                answer.pop("keys")
            if len(serial(answer).encode("utf-8")) > max_bytes:
                raise ValueError("Evidence byte budget is too small for a query receipt")
            return answer
        return answer
