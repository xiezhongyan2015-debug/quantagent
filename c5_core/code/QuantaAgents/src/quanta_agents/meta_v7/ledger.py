"""Project-wide immutable research lineage and observed date exposure.

Counts are descriptive bookkeeping, never an estimate of independent trials for
DSR or a multiple-testing correction. All studies must explicitly share this root.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing, nullcontext
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "meta_v7_project_ledger_v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")
_ZERO = "0" * 64
_NARRATIVE = {"name", "title", "description", "reason", "rationale", "metadata", "comment"}
_LINEAGE = {"study_id", "trial_id", "attempt_id", "retry_of", "reused_from", "parent_trial_id", "lineage", "event", "status", "evidence"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("ledger identity must be a short reference, never a path")
    return value


def _dates(start, end):
    if (not isinstance(start, str) or not isinstance(end, str)
            or date.fromisoformat(start).isoformat() != start
            or date.fromisoformat(end).isoformat() != end or start > end):
        raise ValueError("ordered ISO calendar dates required")
    return start, end


def _economic(value, *, top=True):
    """Remove prose and wrapper lineage; retain parameters, sources and scopes.

    Formula aliases/algebra and statistical dependence cannot be inferred here.
    A caller can supply a fully resolved economic spec to obtain stronger reuse.
    """
    if isinstance(value, dict):
        return {key: _economic(item, top=False) for key, item in value.items()
                if key not in _NARRATIVE and not (top and key in _LINEAGE)}
    if isinstance(value, list):
        return [_economic(item, top=False) for item in value]
    return value


def _training_ranges(config):
    ranges = []
    for key in ("training", "train", "fit", "training_scope", "train_scope", "fit_scope"):
        item = config.get(key)
        if isinstance(item, dict) and "start" in item and "end" in item:
            ranges.append(_dates(item["start"], item["end"]))
    for prefix in ("train", "training", "fit"):
        if f"{prefix}_start" in config and f"{prefix}_end" in config:
            ranges.append(_dates(config[f"{prefix}_start"], config[f"{prefix}_end"]))
    scope = config.get("scope")
    if isinstance(scope, dict):
        ranges.extend(_training_ranges(scope))
        if scope.get("role") in {"training", "train", "fit", "development", "previously_exposed_development"}:
            ranges.append(_dates(scope["start"], scope["end"]))
    if isinstance(config.get("split_plan"), dict):
        ranges.extend(_training_ranges(config["split_plan"]))
    return sorted(set(ranges))


class ProjectLedger:
    """Append-only SQLite journal with per-record and complete-chain checks.

    ``trial_id`` identifies an immutable event; record a transition under a new
    id and preserve ``spec.attempt_id`` to count the logical attempt once.
    Hashes detect corruption, not an attacker rewriting the entire database.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "project_ledger.sqlite"
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, previous_sha256 TEXT NOT NULL, chain_sha256 TEXT NOT NULL, UNIQUE(kind,event_key))")
            db.execute("CREATE TABLE IF NOT EXISTS head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), seq INTEGER NOT NULL, sha256 TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO head VALUES (1,0,?)", (_ZERO,))
            self._verified(db)

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=15)

    @staticmethod
    def _verified(db):
        rows, previous = [], _ZERO
        for expected, row in enumerate(db.execute("SELECT seq,kind,event_key,payload,payload_sha256,previous_sha256,chain_sha256 FROM events ORDER BY seq"), 1):
            seq, kind, key, body, body_hash, prior, chain = row
            payload = json.loads(body)
            if (seq != expected or _json(payload) != body or _digest(payload) != body_hash
                    or prior != previous or chain != _digest([seq, kind, key, body_hash, prior])):
                raise ValueError("project ledger integrity failure")
            rows.append({"sequence": seq, "event_kind": kind, "event_key": key,
                         "sha256": body_hash, "chain_sha256": chain, **payload})
            previous = chain
        head = db.execute("SELECT seq,sha256 FROM head WHERE singleton=1").fetchone()
        if head != (len(rows), previous):
            raise ValueError("project ledger head/length integrity failure")
        return rows

    def _read(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            return self._verified(db)

    def _append(self, kind, key, content, *, requires_study=None, _db=None):
        # JSON round-trip prevents mutable caller data escaping into the ledger.
        content = json.loads(_json(content))
        with (closing(self._connect()) if _db is None else nullcontext(_db)) as db:
          with (db if _db is None else nullcontext()):
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
            rows = self._verified(db)
            if requires_study is not None and not any(r["event_kind"] == "study" and r["study_id"] == requires_study for r in rows):
                raise KeyError(f"unknown study: {requires_study}")
            for old in rows:
                if old["event_kind"] == kind and old["event_key"] == key:
                    original = {k: v for k, v in old.items() if k not in {"sequence", "event_kind", "event_key", "sha256", "chain_sha256", "recorded_at"}}
                    if original != content:
                        raise ValueError("immutable ledger identity conflicts with different content")
                    return old
            if kind == "trial" and any(r["event_kind"] == "trial"
                    and r["study_id"] == content["study_id"] and r["attempt_id"] == content["attempt_id"]
                    and r["economic_id"] != content["economic_id"] for r in rows):
                raise ValueError("logical attempt contains conflicting economic specifications")
            payload = {**content, "recorded_at": datetime.now(timezone.utc).isoformat()}
            seq = len(rows) + 1
            prior = rows[-1]["chain_sha256"] if rows else _ZERO
            body_hash = _digest(payload)
            chain = _digest([seq, kind, key, body_hash, prior])
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (seq, kind, key, _json(payload), body_hash, prior, chain))
            db.execute("UPDATE head SET seq=?,sha256=? WHERE singleton=1", (seq, chain))
            return {"sequence": seq, "event_kind": kind, "event_key": key, "sha256": body_hash, "chain_sha256": chain, **payload}

    def register_study(self, study_id, config):
        _id(study_id)
        if not isinstance(config, dict):
            raise ValueError("study config must be a JSON object")
        _training_ranges(config)
        return self._append("study", study_id, {"study_id": study_id, "config": config, "version": VERSION})

    def record_trial(self, study_id, trial_id, kind, spec, *, status="registered", evidence=None):
        for item in (study_id, trial_id, kind, status):
            _id(item)
        if not isinstance(spec, dict):
            raise ValueError("trial spec must be a JSON object")
        attempt = _id(spec.get("attempt_id", trial_id))
        economic = _economic(spec)
        return self._append("trial", f"{study_id}/{trial_id}", {
            "study_id": study_id, "trial_id": trial_id, "kind": kind, "spec": spec,
            "status": status, "evidence": evidence, "attempt_id": attempt,
            "economic_id": _digest({"kind": kind, "spec": economic}),
            "economic_identity_policy": "same canonical JSON excluding prose/wrapper lineage; not algebraic or independent-N inference",
        }, requires_study=study_id)

    def record_exposure(self, study_id, *, start, end, role, evidence_id, reason):
        _id(study_id)
        _id(role)
        _dates(start, end)
        if not isinstance(evidence_id, str) or not evidence_id.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("exposure requires evidence identity and reason")
        content = {"study_id": study_id, "start": start, "end": end,
                   "role": role, "evidence_id": evidence_id, "reason": reason}
        return self._append("exposure", _digest(content), content, requires_study=study_id)

    @staticmethod
    def _page(rows, limit, offset):
        if type(limit) is not int or not 1 <= limit <= 10000 or type(offset) is not int or offset < 0:
            raise ValueError("bounded positive limit and nonnegative offset required")
        return rows[offset:offset + limit]

    def list_trials(self, limit=50, offset=0, *, study_id=None):
        return self._page([r for r in self._read() if r["event_kind"] == "trial" and (study_id is None or r["study_id"] == study_id)], limit, offset)

    def list_exposures(self, limit=50, offset=0, *, study_id=None, start=None, end=None):
        if start is not None or end is not None:
            _dates(start, end)
        return self._page([r for r in self._read() if r["event_kind"] == "exposure"
                           and (study_id is None or r["study_id"] == study_id)
                           and (start is None or (r["start"] <= end and r["end"] >= start))], limit, offset)

    def can_validate(self, study_id, *, start, end):
        _dates(start, end)
        return self._validation_check(self._read(), study_id, start, end)

    def _validation_check(self, rows, study_id, start, end):
        study = next((r for r in rows if r["event_kind"] == "study" and r["study_id"] == study_id), None)
        if study is None:
            raise KeyError(f"unknown study: {study_id}")
        prior = [r for r in rows if r["event_kind"] == "exposure" and r["start"] <= end and r["end"] >= start]
        # The controller loads training values at initialization, so every
        # registered study's frozen training range already counts as exposure.
        for registered in (r for r in rows if r["event_kind"] == "study"):
            for a, b in _training_ranges(registered["config"]):
                if a <= end and b >= start:
                    prior.append({"study_id": registered["study_id"], "start": a, "end": b,
                                  "role": "registered_training", "evidence_id": registered["sha256"]})
        return {"allowed": not prior,
                "reason": "overlaps prior exposure or registered training; not independent" if prior else "no overlap in this project ledger; external/unrecorded exposure remains unknown",
                "prior_exposures": prior, "formal_validation_certified": False,
                "ledger_root": str(self.root), "scope": {"start": start, "end": end}}

    def begin_validation(self, study_id, *, start, end, evidence_id, reason, require_independent):
        """Atomically admit AND record exposure before the caller reads values.

        Admission survives caller failure. Independent retries see their earlier
        exposure and are refused; exposed-temporal reuse stays explicitly exposed.
        """
        _id(study_id)
        _dates(start, end)
        if type(require_independent) is not bool:
            raise ValueError("require_independent must be boolean")
        if not isinstance(evidence_id, str) or not evidence_id.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("validation requires evidence identity and reason")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            decision = self._validation_check(self._verified(db), study_id, start, end)
            if require_independent and not decision["allowed"]:
                raise ValueError("Independent validation blocked: " + decision["reason"])
            content = {"study_id": study_id, "start": start, "end": end,
                       "role": "temporal_validation", "evidence_id": evidence_id, "reason": reason}
            exposure = self._append("exposure", _digest(content), content,
                                    requires_study=study_id, _db=db)
            return {**decision, "exposure_record": exposure,
                    "require_independent": require_independent, "admission_recorded_before_read": True}

    def summary(self):
        rows = self._read()
        trials = [r for r in rows if r["event_kind"] == "trial"]
        attempts = {}
        for row in trials:
            key = (row["study_id"], row["attempt_id"])
            if key in attempts and attempts[key]["economic_id"] != row["economic_id"]:
                raise ValueError("logical attempt contains conflicting economic specifications")
            attempts[key] = row
        unique = {r["economic_id"] for r in attempts.values()}
        failed = [r for r in attempts.values() if r["status"] in {"failed", "error", "rejected", "timeout", "cancelled"}]
        return {"version": VERSION, "ledger_root": str(self.root),
                "studies": sum(r["event_kind"] == "study" for r in rows),
                "trial_events": len(trials), "total_attempts": len(attempts),
                "unique_economic_trials": len(unique), "failed_attempts": len(failed),
                "failure_events": sum(r["status"] in {"failed", "error", "rejected", "timeout", "cancelled"} for r in trials),
                "reuse_attempts": len(attempts) - len(unique),
                "explicit_reuse_attempts": sum(r["status"] in {"reused", "cache_hit"} for r in attempts.values()),
                "exposures": sum(r["event_kind"] == "exposure" for r in rows),
                "head_sha256": rows[-1]["chain_sha256"] if rows else _ZERO,
                "independent_sample_count": None, "dsr_independent_n": None,
                "limitations": ["one configured root; no automatic discovery of other project databases",
                                "economic identity is not algebraic/behavioral equivalence or statistical independence",
                                "unrecorded prior data exposure is unknown; hashes are integrity checks, not signatures"]}
