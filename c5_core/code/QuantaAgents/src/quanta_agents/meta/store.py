from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


class Store:
    """Small durable state and append-only events; large artifacts stay on disk."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              ts TEXT NOT NULL, kind TEXT NOT NULL, role TEXT, step TEXT,
              text TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
            CREATE TABLE IF NOT EXISTS calls(
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step TEXT NOT NULL,
              attempt INTEGER NOT NULL, body TEXT NOT NULL,
              UNIQUE(run_id, step, attempt));
            CREATE TABLE IF NOT EXISTS evaluation_exposures(
              case_key TEXT NOT NULL, split TEXT NOT NULL, run_id TEXT NOT NULL,
              opened_at TEXT NOT NULL, submissions_hash TEXT NOT NULL,
              PRIMARY KEY(case_key, split, run_id));
        """)
        self.db.commit()

    def create(self, body: dict) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO runs VALUES (?,?)", (body['id'], dumps(body)))

    def get(self, run_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            return json.loads(row['body'])

    def update(self, run_id: str, change: dict | Callable[[dict], None]) -> dict:
        with self.lock, self.db:
            body = self.get(run_id)
            if callable(change):
                change(body)
            else:
                body.update(change)
            body['updated_at'] = now()
            self.db.execute("UPDATE runs SET body=? WHERE id=?", (dumps(body), run_id))
            return body

    def runs(self) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM runs ORDER BY rowid DESC")]

    def event(self, run_id: str, kind: str, text: str, *, role: str = 'harness',
              step: str = '', data: dict | None = None) -> int:
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO events(run_id,ts,kind,role,step,text,data) VALUES(?,?,?,?,?,?,?)",
                (run_id, now(), kind, role, step, text, dumps(data or {})),
            )
            return int(cur.lastrowid)

    def events(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
            return [{**dict(r), 'data': json.loads(r['data'])} for r in rows]

    def save_call(self, body: dict) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                            (body['id'], body['run_id'], body['step'], body['attempt'], dumps(body)))

    def calls(self, run_id: str) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute(
                "SELECT body FROM calls WHERE run_id=? ORDER BY rowid", (run_id,))]

    def expose_evaluation(self, case_key: str, split: str, run_id: str, submissions_hash: str) -> dict:
        """Register access before evaluation, including failed or fixture attempts."""
        with self.lock, self.db:
            rows = self.db.execute(
                'SELECT run_id,opened_at,submissions_hash FROM evaluation_exposures WHERE case_key=? AND split=?',
                (case_key, split)).fetchall()
            previous = [dict(row) for row in rows if row['run_id'] != run_id]
            own = [row for row in rows if row['run_id'] == run_id]
            if own and own[0]['submissions_hash'] != submissions_hash:
                raise ValueError('同一运行的冻结提交已改变，禁止重新评测。')
            self.db.execute('INSERT OR IGNORE INTO evaluation_exposures VALUES (?,?,?,?,?)',
                            (case_key, split, run_id, now(), submissions_hash))
            return {'case_key': case_key, 'split': split, 'prior_runs': previous,
                    'previously_opened': bool(previous), 'registry_scope': 'this_ledger_only',
                    'note': '已查看时期不能在后续调优后再次称为盲测；其他目录或人工历史查看尚无法自动排除。'}

    def close(self) -> None:
        self.db.close()
