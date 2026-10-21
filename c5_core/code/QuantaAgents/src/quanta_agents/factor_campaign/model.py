"""Real model decisions at research nodes, with recovery before another call.

The existing pinned gateway owns original runtime artifacts and identity proof.
This layer owns preregistration, stable action IDs, blockers and an outbox. The
controller's application callback must itself be idempotent for that action ID.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from quanta_agents.research_kernel.store import digest, exclusive_lock, serial, write_json
from .resources import CostLedger


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
EVIDENCE_ANSWER = _object({"answer": TEXT, "evidence_ids": TEXTS})
TEMPLATE = _object({
    "family_id": TEXT, "expression_template": TEXT,
    "parameters": {"type": "array", "items": _object({"name": TEXT, "values": {"type": "array", "items": {"type": "integer"}}})},
    "role": {"type": "string", "enum": ["return", "risk", "condition"]},
    "route": {"type": "string", "enum": ["quality", "complement", "condition"]},
    "mechanism": TEXT, "parents": TEXTS,
    "controls": {"type": "array", "items": _object({"name": TEXT, "expression_template": TEXT, "rationale": TEXT})},
    "falsifier": TEXT,
})
SCHEMAS = {
    "propose": _object({"kind": {"type": "string", "enum": ["propose"]}, "templates": {"type": "array", "items": TEMPLATE},
                        "evidence_ids": TEXTS, "rationale": TEXT}),
    "review": _object({"kind": {"type": "string", "enum": ["review"]}, "failure_analysis": EVIDENCE_ANSWER,
                       "framework_diagnosis": EVIDENCE_ANSWER, "proposed_improvement": EVIDENCE_ANSWER,
                       "cross_version_comparison": EVIDENCE_ANSWER,
                       "next_action": {"type": "string", "enum": ["extend", "revise_framework", "wait"]},
                       "proposed_changes": TEXTS, "falsifier": TEXT}),
    "repair": _object({"kind": {"type": "string", "enum": ["repair"]}, "parent_id": TEXT, "expression": TEXT,
                       "reason": TEXT, "evidence_ids": TEXTS, "falsifier": TEXT}),
    "framework_patch": _object({"kind": {"type": "string", "enum": ["framework_patch"]}, "component": {"type": "string", "enum": ["implementation", "scheduler", "data_interface", "operator", "combination"]},
                                "changes": {"type": "array", "items": _object({"path": TEXT, "content": TEXT})},
                                "evidence_ids": TEXTS, "hypothesis": TEXT, "falsifier": TEXT}),
}
# Keep the original schema stable for saved action recovery. Subsequent versions
# may explicitly choose propose_v2 with the additional provenance declaration.
SCHEMAS["propose_v2"] = deepcopy(SCHEMAS["propose"])
SCHEMAS["propose_v2"]["properties"]["templates"]["items"]["properties"]["transformation_kind"] = {
    "type": "string", "enum": ["raw_formula", "conditional_interaction", "registered_factor_aggregation"]}
SCHEMAS["propose_v2"]["properties"]["templates"]["items"]["required"].append("transformation_kind")
ALIASES = {"proposal": "propose", "structure": "propose", "batch_review": "review", "version_review": "review"}
POLICY = """You are the V10A continuing research model (Astra xhigh). Return only the requested JSON.
The program, not the model, expands parameter grids, evaluates candidates, fits combinations and schedules work.
Frozen success: one unsupervised formula annual daily cross-sectional Pearson IC strictly >0.05 in EACH
2019-2024 year OR one full combination scheme prediction Pearson IC strictly >0.10 in EACH such year.
Labels are unranked open[t+6]/open[t+1]-1; historical HS300 signal-date pool; purge six year-end signal dates;
at least 100 stocks, 200 valid days per year, 80% coverage. Formula direction uses 2016-2018 then freezes.
2019-2024 are all exposed history; only six-year historical attainment can be claimed. OOS and profit unproved.
Never request, read or embed 2025 numeric values. Never alter protocol/evaluator/thresholds/labels/years/results.
Retain return, risk and condition roles; quality, complement and condition routes. Low marginal IC alone does
not eliminate complementary members. Same-definition, same-values and high-correlation redundancy differ.
Proposals need executable causal DSL templates, bounded integer parameter ranges, mechanisms, exact known
parent IDs, matched controls and falsifiers. No call per parameter point. Repair only declared failed formulas;
keep original and new identity, reason and evidence. Reviews answer all four questions using actual evidence IDs:
why target failed; implementation/information/coverage/redundancy/combination/update/selection diagnosis;
one justified framework improvement and what would falsify it; added information/effective candidates/cost vs prior.
Only changes to implementation, scheduler, data interface, operator or combination mechanism justify a framework
version. Window/weight changes are batches; formula changes are structures. Framework changes must be tested
and independently audited in isolation. Do not claim success, billing cost, data availability or audit approval.
"""


class ModelLoop:
    def __init__(self, root, *, verifier=None, capture=None, cost_ledger=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "campaign_model.sqlite"
        self.verifier, self.capture = verifier, capture
        self.cost = cost_ledger or CostLedger(self.root)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls(action_id TEXT PRIMARY KEY,kind TEXT NOT NULL,intent TEXT NOT NULL,status TEXT NOT NULL,response TEXT,receipt TEXT,error TEXT,application TEXT,created REAL NOT NULL,updated REAL NOT NULL)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _row(self, action_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM calls WHERE action_id=?", (action_id,)).fetchone()
        return dict(row) if row else None

    def _update(self, action_id, **fields):
        with self._db() as db:
            db.execute("UPDATE calls SET " + ",".join(key + "=?" for key in fields) + ",updated=? WHERE action_id=?",
                       (*fields.values(), time.time(), action_id))

    def _verify(self, folder, intent):
        from quanta_agents.meta_v6.gateway import verify_saved_completion, capture_saved_session
        verifier = self.verifier or verify_saved_completion
        receipt = verifier(folder, expected_prompt_hash=intent["prompt_hash"], expected_schema=intent["schema"])
        if receipt.get("runtime_identity", {}).get("verified") is not True:
            (self.capture or capture_saved_session)(folder, receipt)
            receipt = verifier(folder, expected_prompt_hash=intent["prompt_hash"], expected_schema=intent["schema"])
        if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or receipt.get("runtime_identity", {}).get("verified") is not True:
            raise ValueError("Saved runtime model/effort identity is unverified")
        return receipt

    def _admit(self, action_id, receipt):
        response = receipt["response"]
        row = self._row(action_id)
        intent = json.loads(row["intent"])
        # The gateway validates schema too; this independent check protects custom transports.
        from quanta_agents.meta_v6.gateway import base
        base._validate_output(response, intent["schema"])
        if len(serial(response).encode("utf-8")) > 160000:
            raise ValueError("Model response exceeds bounded admission size")
        self._update(action_id, status="ready", response=serial(response), receipt=serial(receipt), error=None)
        self.cost.record(action_id, kind="model_call", usage=receipt.get("usage", {}), recovered=True)
        return self._result(self._row(action_id))

    @staticmethod
    def _blocker(exc):
        message = str(exc).lower()
        if any(word in message for word in ("quota", "rate limit", "usage limit", "429", "credit", "capacity")):
            return "model_quota_or_capacity"
        if "timeout" in message or "timed out" in message or "timeout" in type(exc).__name__.lower():
            return "model_timeout_receipt_recovery_required"
        return "model_dependency_or_saved_receipt_unavailable"

    @staticmethod
    def _result(row):
        return {"action_id": row["action_id"], "kind": row["kind"], "status": row["status"],
                "response": json.loads(row["response"]) if row["response"] else None,
                "application": json.loads(row["application"]) if row["application"] else None,
                "blocker": json.loads(row["error"]) if row["error"] else None}

    def request(self, action_id, kind, context, *, gateway=None, timeout_seconds=900, schema=None, on_event=None, cancelled=None):
        if not isinstance(action_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", action_id):
            raise ValueError("action_id must be a stable safe identifier")
        kind = ALIASES.get(kind, kind)
        if kind not in SCHEMAS:
            raise ValueError("Unsupported research model node")
        schema = schema or SCHEMAS[kind]
        prompt = POLICY + "\nNode: " + kind + "\nFrozen program context:\n" + serial(context)
        intent = {"intent_version": 1, "intent_id": action_id, "kind": kind,
                  "prompt_hash": hashlib.sha256(json.dumps(prompt, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest(),
                  "schema_hash": hashlib.sha256(json.dumps(schema, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")).hexdigest(),
                  "context_sha256": digest(context), "schema": schema, "model": "gpt-6-astra", "effort": "xhigh"}
        with exclusive_lock(self.root / "campaign_model.lock"):
            row = self._row(action_id)
            if row:
                if json.loads(row["intent"]) != intent:
                    raise ValueError("An action_id cannot be reused for a changed model request")
                if row["status"] in {"ready", "applied", "application_rejected", "failed_terminal"}:
                    return self._result(row)
                return self._recover_unlocked(action_id)
            with self._db() as db:
                unresolved = db.execute("SELECT action_id FROM calls WHERE status IN ('registered','started','waiting','applying') ORDER BY created LIMIT 1").fetchone()
            if unresolved:
                return {"action_id": unresolved[0], "status": "waiting", "response": None,
                        "blocker": {"reason": "unresolved_previous_model_call", "resume_action_id": unresolved[0]}}
            folder = self.root / "model_calls" / action_id
            if folder.exists():
                # A crash can occur between directory creation and ledger commit.
                # No gateway invocation is possible before the commit below.
                # Adopt only an exactly matching, wholly pre-call orphan.
                allowed = {"intent.json", "preregistered_prompt.txt"}
                try:
                    if not folder.resolve().is_relative_to(self.root) or any(p.name not in allowed for p in folder.iterdir()):
                        raise ValueError("Orphan invocation directory contains ambiguous runtime artifacts")
                    existing_intent = folder / "intent.json"
                    existing_prompt = folder / "preregistered_prompt.txt"
                    if existing_intent.exists() and json.loads(existing_intent.read_text(encoding="utf-8")) != intent:
                        raise ValueError("Orphan invocation intent differs from requested action")
                    if existing_prompt.exists() and existing_prompt.read_bytes() != prompt.encode("utf-8"):
                        raise ValueError("Orphan invocation prompt differs from requested action")
                except (ValueError, OSError) as exc:
                    return {"action_id": action_id, "kind": kind, "status": "waiting", "response": None,
                            "blocker": {"reason": "ambiguous_orphan_model_directory", "message": str(exc),
                                        "path": str(folder), "paid_retry_performed": False}}
            else:
                folder.mkdir(parents=True)
            # Pre-registration commits before the first external call.
            write_json(folder / "intent.json", intent)
            (folder / "preregistered_prompt.txt").write_bytes(prompt.encode("utf-8"))
            with self._db() as db:
                now = time.time()
                db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (action_id, kind, serial(intent), "registered", None, None, None, None, now, now))
            self.cost.record(action_id, kind="model_call", usage={})
            wall, cpu = time.perf_counter(), time.process_time()
            self._update(action_id, status="started")
            try:
                from quanta_agents.meta_v6.gateway import CodexGateway
                (gateway or CodexGateway(timeout_seconds=timeout_seconds)).run(
                    prompt=prompt, schema=schema, workdir=folder, on_event=on_event or (lambda event: None),
                    cancelled=cancelled or (lambda: (self.root / "stop.request").exists()))
                receipt = self._verify(folder, intent)
                return self._admit(action_id, receipt)
            except Exception as exc:
                self.cost.record(action_id, kind="model_call", usage=getattr(exc, "usage", {}) or {}, failed=True)
                # A timeout can leave a complete answer. Try its saved original artifacts now.
                return self._recover_unlocked(action_id, original_error=exc)
            finally:
                self.cost.record(action_id, kind="model_call", cpu_seconds=time.process_time() - cpu,
                                 wall_seconds=time.perf_counter() - wall)

    def _recover_unlocked(self, action_id, original_error=None):
        row = self._row(action_id)
        if not row:
            raise ValueError("Unknown model action")
        if row["status"] in {"ready", "applied", "application_rejected", "failed_terminal"}:
            return self._result(row)
        try:
            return self._admit(action_id, self._verify(self.root / "model_calls" / action_id, json.loads(row["intent"])))
        except Exception as exc:
            cause = original_error or exc
            error = {"reason": self._blocker(cause), "message": str(cause)[:1500],
                     "recovery_error": str(exc)[:1500], "resume_action_id": action_id,
                     "paid_retry_performed": False}
            self._update(action_id, status="waiting", error=serial(error))
            return self._result(self._row(action_id))

    def recover(self, action_id):
        """Offline-only recovery; no invocation is hidden in this operation."""
        with exclusive_lock(self.root / "campaign_model.lock"):
            return self._recover_unlocked(action_id)

    def release_failed_attempt(self, action_id):
        """Allow a future distinct attempt only with observed terminal no-answer proof.

        Offline recovery always runs first. A saved/partial answer, missing exit
        proof or unknown process state keeps the old attempt waiting. This does
        not invoke a model or purchase capacity; the controller separately
        rechecks resources before preregistering a new attempt ID.
        """
        with exclusive_lock(self.root / "campaign_model.lock"):
            result = self._recover_unlocked(action_id)
            if result["status"] != "waiting":
                return result
            folder = self.root / "model_calls" / action_id
            try:
                exited = json.loads((folder / "process_exit.json").read_text(encoding="utf-8"))
                if exited.get("exit_observed") is not True or type(exited.get("process_exit_code")) is not int:
                    return result
                response = folder / "response.json"
                if response.exists() and response.stat().st_size:
                    return result
                events = folder / "codex_events.jsonl"
                if not events.exists():
                    return result
                for line in events.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if event.get("type") in {"turn.completed", "response.completed"}:
                        return result
                    item = event.get("item", {})
                    if item.get("type") in {"agent_message", "message"} and item.get("text"):
                        return result
                self._update(action_id, status="failed_terminal", error=serial({
                    "reason": "observed_terminal_without_saved_answer", "process_exit": exited,
                    "previous_blocker": result["blocker"], "next_attempt_requires_new_action_id": True}))
                return self._result(self._row(action_id))
            except (ValueError, OSError, TypeError):
                return result

    def apply_once(self, action_id, handler):
        """Deliver stable ID to a controller transaction; safe across interrupted delivery.

        A callback must record action_id atomically with its own effects. This
        outbox cannot make arbitrary external, non-idempotent side effects atomic.
        """
        with exclusive_lock(self.root / "campaign_model.lock"):
            row = self._row(action_id)
            if not row:
                raise ValueError("Unknown model action")
            if row["status"] == "applied":
                return self._result(row)
            if row["status"] not in {"ready", "applying"}:
                raise ValueError("Only a verified ready response may be applied")
            self._update(action_id, status="applying")
            try:
                result = handler(json.loads(row["response"]), action_id)
            except (ValueError, KeyError, TypeError) as exc:
                self._update(action_id, status="application_rejected", error=serial({"reason": "action_validation_failed", "message": str(exc)[:1500]}))
                return self._result(self._row(action_id))
            self._update(action_id, status="applied", application=serial(result), error=None)
            return self._result(self._row(action_id))

    def status(self):
        with self._db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM calls ORDER BY created")]
        return {"calls": [self._result(row) for row in rows], "cost": self.cost.summary()}
