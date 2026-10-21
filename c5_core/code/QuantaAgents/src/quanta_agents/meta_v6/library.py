"""Append-only experiment evidence and frozen factor-library snapshots.

All attempts, including invalid syntax and failed/duplicate searches, can be
recorded. A factor has no permanent profitability or validity flag: evidence is
attached to a concrete scope, attempt and observation history. SQLite triggers
prevent accidental updates/deletes; this is not adversarial tamper-proof storage.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .factors import FactorSpec


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _scope(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("a nonempty explicit evidence scope is required")
    result = copy.deepcopy(dict(value))
    _dump(result)
    return result


class FactorLibrary:
    """SQLite event journal, usable as a context manager.

    Methods return stable event IDs. `record_attempt` never deduplicates away an
    attempt: duplicate_of links to a prior attempt of the same normalized factor.
    A snapshot embeds exact version/attempt/evidence IDs at an event high-water
    mark. New evidence cannot change a previously saved snapshot.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS factor_library_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL CHECK(kind IN ('factor','attempt','evidence','exposure','snapshot')),
                factor_id TEXT,
                created_utc TEXT NOT NULL,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS factor_library_kind ON factor_library_events(kind, seq);
            CREATE INDEX IF NOT EXISTS factor_library_factor ON factor_library_events(factor_id, seq);
            CREATE TRIGGER IF NOT EXISTS factor_library_no_update
                BEFORE UPDATE ON factor_library_events BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END;
            CREATE TRIGGER IF NOT EXISTS factor_library_no_delete
                BEFORE DELETE ON factor_library_events BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END;
        """)
        self._db.commit()

    def __enter__(self) -> "FactorLibrary":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def _append(self, kind: str, payload: Mapping[str, Any], factor_id: str | None = None) -> str:
        serialized = _dump(dict(payload))
        event_id = uuid.uuid4().hex
        created = datetime.now(timezone.utc).isoformat()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            previous = self._db.execute("SELECT event_hash FROM factor_library_events ORDER BY seq DESC LIMIT 1").fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            event_hash = _hash({"event_id": event_id, "kind": kind, "factor_id": factor_id,
                "created_utc": created, "payload": json.loads(serialized), "previous_hash": previous_hash})
            self._db.execute("""INSERT INTO factor_library_events
                (event_id,kind,factor_id,created_utc,payload,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?)""",
                (event_id, kind, factor_id, created, serialized, previous_hash, event_hash))
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return event_id

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def get_event(self, event_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM factor_library_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self._decode(row)

    def events(self, *, kind: str | None = None, factor_id: str | None = None,
               through_seq: int | None = None) -> list[dict[str, Any]]:
        clauses, values = [], []
        for column, value in (("kind", kind), ("factor_id", factor_id)):
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)
        if through_seq is not None:
            clauses.append("seq<=?")
            values.append(through_seq)
        query = "SELECT * FROM factor_library_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return [self._decode(row) for row in self._db.execute(query + " ORDER BY seq", values)]

    def register_factor(self, spec: FactorSpec) -> str:
        for event in self.events(kind="factor", factor_id=spec.factor_id):
            if event["payload"]["spec_id"] == spec.spec_id:
                return event["event_id"]
        return self._append("factor", {"spec": spec.to_dict(), "spec_id": spec.spec_id,
                                      "factor_id": spec.factor_id}, spec.factor_id)

    def record_attempt(self, spec: FactorSpec | None = None, *, status: str,
                       scope: Mapping[str, Any], seed: Any = None, model: Any = None,
                       parents: tuple[str, ...] = (), run_id: str | None = None,
                       source: Mapping[str, Any] | None = None, exposure: Mapping[str, Any] | None = None,
                       cost: Mapping[str, Any] | None = None, error: str | None = None,
                       expression: str | None = None, metadata: Mapping[str, Any] | None = None,
                       library_snapshot_id: str | None = None) -> str:
        if status not in {"planned", "running", "computed", "evaluated", "succeeded", "failed",
                          "invalid", "rejected", "duplicate", "inconclusive"}:
            raise ValueError("unknown attempt status")
        explicit_scope = _scope(scope)
        if spec is None and not expression:
            raise ValueError("a FactorSpec or the attempted raw expression is required")
        if spec is None and status not in {"invalid", "failed", "rejected"}:
            raise ValueError("unvalidated expressions require invalid/failed/rejected status")
        if library_snapshot_id is not None:
            self.load_snapshot(library_snapshot_id)
        factor_event = self.register_factor(spec) if spec is not None else None
        prior = self.events(kind="attempt", factor_id=spec.factor_id) if spec is not None else []
        duplicate_of = prior[0]["event_id"] if prior else None
        return self._append("attempt", {"factor_event_id": factor_event,
            "factor_id": spec.factor_id if spec else None, "spec_id": spec.spec_id if spec else None,
            "expression": spec.expression if spec else expression, "status": status,
            "scope": explicit_scope, "seed": seed, "model": model,
            "parents": list(parents or (spec.parents if spec else ())), "run_id": run_id,
            "source": dict(source or {}), "exposure": dict(exposure) if exposure is not None else None,
            "cost": dict(cost) if cost is not None else None, "error": error,
            "duplicate_of": duplicate_of, "library_snapshot_id": library_snapshot_id,
            "metadata": dict(metadata or {}), "independent_market_replication": False},
            spec.factor_id if spec else None)

    def record_evidence(self, attempt_id: str, *, scope: Mapping[str, Any], status: str,
                        metrics: Mapping[str, Any] | None = None, artifacts: list[dict[str, Any]] | None = None,
                        cost: Mapping[str, Any] | None = None, exposure: Mapping[str, Any] | None = None,
                        metadata: Mapping[str, Any] | None = None) -> str:
        attempt = self.get_event(attempt_id)
        if attempt["kind"] != "attempt":
            raise ValueError("evidence must refer to an attempt")
        if status not in {"observed", "inconclusive", "failed", "rejected", "supported_in_scope", "contradicted_in_scope"}:
            raise ValueError("evidence status must describe an observation in scope")
        explicit_scope = _scope(scope)
        return self._append("evidence", {"attempt_id": attempt_id, "scope": explicit_scope,
            "status": status, "metrics": dict(metrics or {}), "artifacts": list(artifacts or []),
            "cost": dict(cost) if cost is not None else None,
            "exposure": dict(exposure) if exposure is not None else None,
            "metadata": dict(metadata or {})}, attempt["factor_id"])

    def record_exposure(self, *, scope: Mapping[str, Any], purpose: str,
                        attempt_id: str | None = None, library_snapshot_id: str | None = None,
                        results_revealed: bool = True, used_for_selection: bool = False,
                        metadata: Mapping[str, Any] | None = None) -> str:
        """Record validation consumption; a new seed never clears this history."""
        if not purpose or (attempt_id is None and library_snapshot_id is None):
            raise ValueError("exposure needs a purpose and an attempt or snapshot reference")
        if type(results_revealed) is not bool or type(used_for_selection) is not bool:
            raise ValueError("exposure flags must be booleans")
        if used_for_selection and not results_revealed:
            raise ValueError("selection implies results were revealed")
        factor_id = None
        if attempt_id is not None:
            attempt = self.get_event(attempt_id)
            if attempt["kind"] != "attempt":
                raise ValueError("attempt_id must identify an attempt")
            factor_id = attempt["factor_id"]
        if library_snapshot_id is not None:
            self.load_snapshot(library_snapshot_id)
        return self._append("exposure", {"scope": _scope(scope), "purpose": purpose,
            "attempt_id": attempt_id, "library_snapshot_id": library_snapshot_id,
            "results_revealed": results_revealed, "used_for_selection": used_for_selection,
            "metadata": dict(metadata or {})}, factor_id)

    def create_snapshot(self, *, scope: Mapping[str, Any], factor_ids: list[str] | None = None,
                        metadata: Mapping[str, Any] | None = None) -> str:
        # Capture the high-water mark once; concurrent future appends are excluded.
        row = self._db.execute("SELECT COALESCE(MAX(seq),0) FROM factor_library_events").fetchone()
        cutoff = int(row[0])
        # Snapshot references do not deserialize potentially large metric artifacts.
        events = [dict(row) for row in self._db.execute(
            "SELECT seq,event_id,kind,factor_id FROM factor_library_events WHERE seq<=? ORDER BY seq", (cutoff,))]
        registered = {e["factor_id"] for e in events if e["kind"] == "factor"}
        selected = sorted(registered if factor_ids is None else set(factor_ids))
        if set(selected) - registered:
            raise ValueError("snapshot contains unregistered factor identities")
        selected_set = set(selected)
        payload = {"scope": _scope(scope), "through_seq": cutoff, "factor_ids": selected,
            "factor_event_ids": [e["event_id"] for e in events if e["kind"] == "factor" and e["factor_id"] in selected_set],
            "attempt_ids": [e["event_id"] for e in events if e["kind"] == "attempt" and e["factor_id"] in selected_set],
            "evidence_ids": [e["event_id"] for e in events if e["kind"] == "evidence" and e["factor_id"] in selected_set],
            # Retain the entire prior trial/exposure context, including rejected syntax.
            "trial_history_ids": [e["event_id"] for e in events if e["kind"] == "attempt"],
            "exposure_ids": [e["event_id"] for e in events if e["kind"] == "exposure"],
            "metadata": dict(metadata or {})}
        payload["snapshot_hash"] = _hash(payload)
        return self._append("snapshot", payload)

    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        event = self.get_event(snapshot_id)
        if event["kind"] != "snapshot":
            raise ValueError("snapshot_id must identify a snapshot")
        payload = event["payload"]
        expected = payload["snapshot_hash"]
        if _hash({k: v for k, v in payload.items() if k != "snapshot_hash"}) != expected:
            raise ValueError("snapshot integrity failure")
        return {"snapshot_id": snapshot_id, **payload}

    def evidence_for(self, factor_id: str, *, scope: Mapping[str, Any] | None = None,
                     snapshot_id: str | None = None) -> list[dict[str, Any]]:
        cutoff = None
        if snapshot_id is not None:
            snapshot = self.load_snapshot(snapshot_id)
            if factor_id not in snapshot["factor_ids"]:
                return []
            cutoff = snapshot["through_seq"]
        result = self.events(kind="evidence", factor_id=factor_id, through_seq=cutoff)
        if scope is not None:
            match = _dump(_scope(scope))
            result = [e for e in result if _dump(e["payload"]["scope"]) == match]
        return result

    def verify_integrity(self) -> bool:
        previous = "0" * 64
        for row in self._db.execute("SELECT * FROM factor_library_events ORDER BY seq"):
            event = self._decode(row)
            expected = _hash({k: event[k] for k in ("event_id", "kind", "factor_id", "created_utc", "payload", "previous_hash")})
            if event["previous_hash"] != previous or event["event_hash"] != expected:
                return False
            previous = expected
        return True


# Concise integration alias; both names identify the same API.
Library = FactorLibrary
