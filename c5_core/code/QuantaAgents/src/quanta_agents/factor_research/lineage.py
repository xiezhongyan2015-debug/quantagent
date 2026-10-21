"""Append-only SQLite trial/error/repair history with a verified hash chain."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .contracts import ExpandedCandidate, canonical_json, digest
from .generation import require_generation_phase


class LineageStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL)")
        self.verify()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def events(self) -> tuple[dict, ...]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT sequence,event_id,event_type,payload,previous_hash,event_hash FROM events ORDER BY sequence").fetchall()
        return tuple({"sequence": row[0], "event_id": row[1], "event_type": row[2],
                      "payload": json.loads(row[3]), "previous_hash": row[4], "event_hash": row[5]} for row in rows)

    def verify(self) -> str:
        previous = "0" * 64
        for expected, row in enumerate(self.events(), start=1):
            content = {k: v for k, v in row.items() if k != "event_hash"}
            if row["sequence"] != expected or row["previous_hash"] != previous or digest(content) != row["event_hash"]:
                raise ValueError("lineage history hash chain is corrupt")
            previous = row["event_hash"]
        return previous

    def append(self, event_type: str, payload: Mapping[str, Any], *, event_id: str | None = None) -> dict:
        if event_type not in {"trial", "outcome", "repair_attempt", "budget", "failure"}:
            raise ValueError("unsupported lineage event")
        payload = json.loads(canonical_json(dict(payload)))
        event_id = event_id or digest({"type": event_type, "payload": payload})
        self.verify()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT event_type,payload FROM events WHERE event_id=?", (event_id,)).fetchone()
            if prior:
                if prior != (event_type, canonical_json(payload)):
                    raise ValueError("event id already binds different content")
                return next(row for row in self.events() if row["event_id"] == event_id)
            tail = db.execute("SELECT sequence,event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
            row = {"sequence": tail[0] + 1 if tail else 1, "event_id": event_id,
                   "event_type": event_type, "payload": payload, "previous_hash": tail[1] if tail else "0" * 64}
            row["event_hash"] = digest(row)
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (row["sequence"], event_id, event_type,
                       canonical_json(payload), row["previous_hash"], row["event_hash"]))
        return row

    def record_trial(self, candidate: ExpandedCandidate, *, parent_trial_ids: tuple[str, ...] = ()) -> dict:
        existing = {r["payload"]["trial_id"] for r in self.events() if r["event_type"] == "trial"}
        if candidate.trial_id in parent_trial_ids or set(parent_trial_ids) - existing:
            raise ValueError("trial parents must reference already-recorded attempts")
        repair_of = candidate.spec.metadata.get("repair_of")
        if repair_of is not None and tuple(parent_trial_ids) != (repair_of,):
            raise ValueError("repaired trial must preserve its exact implementation parent")
        return self.append("trial", {**candidate.to_dict(), "parent_trial_ids": list(parent_trial_ids)},
                           event_id=f"trial:{candidate.trial_id}")

    def record_outcome(self, trial_id: str, status: str, *, evidence: Mapping[str, Any],
                       resource_usage: Mapping[str, Any] | None = None) -> dict:
        if trial_id not in {r["payload"]["trial_id"] for r in self.events() if r["event_type"] == "trial"}:
            raise ValueError("outcome requires a registered trial")
        if status not in {"implementation_failed", "evaluated", "duplicate", "rejected", "not_evaluable", "frozen"}:
            raise ValueError("invalid trial outcome")
        return self.append("outcome", {"trial_id": trial_id, "status": status, "evidence": dict(evidence),
            "resource_usage": dict(resource_usage or {}), "recorded_utc": datetime.now(timezone.utc).isoformat()})

    def record_repair_attempt(self, trial_id: str, expression: str, reason: str, *,
                              phase: str = "development", max_repairs: int = 2) -> dict:
        """Reserve repair before executing; failed repairs still consume the cap."""
        require_generation_phase(phase)
        if type(max_repairs) is not int or not 0 <= max_repairs <= 2 or not expression.strip() or not reason.strip():
            raise ValueError("repair must have a bounded cap and an exact expression/reason")
        rows = self.events()
        if trial_id not in {r["payload"]["trial_id"] for r in rows if r["event_type"] == "trial"}:
            raise ValueError("repair requires original trial identity")
        failures = [r for r in rows if r["event_type"] == "outcome" and r["payload"]["trial_id"] == trial_id]
        if not failures or failures[-1]["payload"]["status"] != "implementation_failed":
            raise ValueError("repair is only available for a recorded implementation failure")
        trials = {r["payload"]["trial_id"]: r["payload"] for r in rows if r["event_type"] == "trial"}
        root_trial_id = trial_id
        visited = set()
        while trials[root_trial_id]["spec"]["metadata"].get("repair_of") is not None:
            if root_trial_id in visited:
                raise ValueError("repair lineage cycle")
            visited.add(root_trial_id)
            root_trial_id = trials[root_trial_id]["spec"]["metadata"]["repair_of"]
        used = sum(r["event_type"] == "repair_attempt" and
                   r["payload"].get("root_trial_id", r["payload"]["trial_id"]) == root_trial_id for r in rows)
        if used >= max_repairs:
            raise ValueError("implementation repair budget exhausted")
        return self.append("repair_attempt", {"trial_id": trial_id, "root_trial_id": root_trial_id,
            "repair_number": used + 1, "proposed_expression": expression, "reason": reason},
            event_id=f"repair:{root_trial_id}:{used + 1}")

    def ancestors(self, trial_id: str) -> tuple[dict, ...]:
        trials = {r["payload"]["trial_id"]: r["payload"] for r in self.events() if r["event_type"] == "trial"}
        if trial_id not in trials:
            raise ValueError("unknown trial")
        seen = set()
        result = []
        def visit(identity):
            for parent in trials[identity]["parent_trial_ids"]:
                if parent not in seen:
                    seen.add(parent)
                    result.append(trials[parent])
                    visit(parent)
        visit(trial_id)
        return tuple(result)
