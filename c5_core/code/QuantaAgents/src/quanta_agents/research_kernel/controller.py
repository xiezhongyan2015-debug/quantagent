"""Budgeted persistent loop: declarative actions -> batches -> retained evidence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import pandas as pd
import numpy as np

from quanta_agents.meta_v6.data import load_market_panel
from quanta_agents.meta_v6.portfolio import AccountPolicy
from .store import Store, clean, digest, exclusive_lock, serial, write_json


DEFAULT_BUDGET = {"max_attempts": 64, "max_executions": 32, "max_batch_size": 16,
                  "max_model_calls": 4, "max_context_bytes": 24000,
                  "max_response_bytes": 16000, "job_timeout_seconds": 900}


def engine_identity():
    base = Path(__file__).parent
    paths = [base / name for name in ("compiler.py", "assets.py", "execution.py", "controller.py", "evidence.py", "store.py")]
    paths += [base.parent / "meta_v6" / name for name in ("factors.py", "data.py", "portfolio.py")]
    paths.append(base.parent / "meta/factor_algebra.py")
    return {**{str(p.relative_to(base.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "numpy_version": np.__version__, "pandas_version": pd.__version__}


_LOADED_ENGINE_IDENTITY = engine_identity()


def _factor_ids(spec):
    from .compiler import factor_ids
    return set().union(*(factor_ids(spec[k]) for k in ("score", "gate", "risk_score") if spec.get(k) is not None))


def expand_controls(spec, controls):
    """Generate paired controls before any results. Originals are always retained."""
    allowed = {"leave_one_out", "without_gate", "equal_weight"}
    if not isinstance(controls, list) or not all(isinstance(v, str) and v in allowed for v in controls):
        raise ValueError("Unknown control; supported: leave_one_out, without_gate, equal_weight")
    rows = [(deepcopy(spec), "proposal")]
    if "without_gate" in controls and spec.get("gate") is not None:
        value = deepcopy(spec)
        value["gate"] = None
        value["name"] += " / without gate"
        rows.append((value, "without_gate"))
    if "equal_weight" in controls and spec.get("allocation", {}).get("weighting") == "inverse_volatility":
        value = deepcopy(spec)
        value["risk_score"] = None
        value["allocation"]["weighting"] = "equal"
        value["name"] += " / equal weight"
        rows.append((value, "equal_weight"))
    if "leave_one_out" in controls:
        score = spec.get("score", {})
        if score.get("op") != "weighted_sum" or len(score.get("args", [])) < 2:
            raise ValueError("leave_one_out requires a top-level weighted_sum with at least two components")
        for index in range(len(score["args"])):
            value = deepcopy(spec)
            value["score"]["args"].pop(index)
            value["score"]["weights"].pop(index)
            value["name"] += f" / omit component {index + 1}"
            rows.append((value, f"omit_{index + 1}"))
    return rows


class ResearchKernel:
    def __init__(self, root):
        from .assets import AssetRegistry
        self.store = Store(root)
        self.root = self.store.root
        self.assets = AssetRegistry(self.root / "assets")

    def initialize(self, config, panel=None):
        config = deepcopy(config)
        allowed = {"start", "end", "data", "budget", "account_policy", "objective", "scope_role"}
        if set(config) - allowed:
            raise ValueError("Unknown study config fields")
        if not {"start", "end"} <= config.keys():
            raise ValueError("Account start and end must be frozen")
        if pd.Timestamp(config["start"]) >= pd.Timestamp(config["end"]):
            raise ValueError("start must precede end")
        if config.get("scope_role", "exposed_development") != "exposed_development":
            raise ValueError("This research loop only admits exposed development data")
        config["scope_role"] = "exposed_development"
        budget = {**DEFAULT_BUDGET, **config.get("budget", {})}
        if set(budget) != set(DEFAULT_BUDGET) or any(type(v) is not int or v <= 0 for v in budget.values()):
            raise ValueError("Budgets must be positive integer limits with known keys")
        if budget["max_context_bytes"] < 6000 or budget["max_response_bytes"] > 65536:
            raise ValueError("Context requires at least 6000 bytes; response limit cannot exceed 65536")
        config["budget"] = budget
        config["account_policy"] = asdict(AccountPolicy(**config.get("account_policy", {})))
        if panel is None:
            panel = load_market_panel(**config["data"])
        if panel.dates[0] > pd.Timestamp(config["start"]) or panel.dates[-1] < pd.Timestamp(config["end"]):
            raise ValueError("Panel does not cover declared account range")
        config["panel_fingerprint"] = panel.fingerprint()
        with self.store.transaction() as db:
            prior = db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
            if prior and json.loads(prior[0]) != config:
                raise ValueError("Study config is frozen; use a new study for a changed scope or budget")
            if not prior:
                self.store.set_meta(db, "config", config)
                self.store.set_meta(db, "stopped", False)
                self.store.event(db, "initialized", {"config_id": digest(config)})
        write_json(self.root / "config.json", config)
        return self.status()

    @property
    def config(self):
        value = self.store.meta("config")
        if value is None:
            raise ValueError("Initialize study first")
        return value

    def _identity(self, spec):
        from .compiler import strategy_id
        current_engine = engine_identity()
        if current_engine != _LOADED_ENGINE_IDENTITY:
            raise ValueError("Research kernel source changed after import; start a fresh process")
        identities = {}
        for factor_id in sorted(_factor_ids(spec)):
            asset = self.assets.get(factor_id)
            if asset.get("status", "executable") not in {"executable", "ready"}:
                raise ValueError(f"Factor {factor_id} is catalogued but not executable: {asset.get('reason', '')}")
            identities[factor_id] = asset.get("expression")
        # Substitute formula identities, so differently named aliases can reuse.
        value = deepcopy(spec)
        def replace_refs(node):
            if node.get("op") == "factor":
                node["id"] = digest({"expression": identities[node["id"]]})
            for child in node.get("args", []):
                replace_refs(child)
        for key in ("score", "gate", "risk_score"):
            if value.get(key) is not None:
                replace_refs(value[key])
        return {"strategy": strategy_id(value), "factors": sorted(set(identities.values())),
                "panel": self.config["panel_fingerprint"], "scope": [self.config["start"], self.config["end"]],
                "policy": self.config["account_policy"], "engine": current_engine}

    def submit_batch(self, specs, *, controls=None, reason="", action_id=None):
        from .compiler import validate_strategy, strategy_id
        if not isinstance(specs, list) or not specs or len(specs) > self.config["budget"]["max_batch_size"]:
            raise ValueError("Nonempty bounded specs list required")
        controls = [] if controls is None else controls
        prepared = []
        for spec in specs:
            try:
                normal = validate_strategy(spec)
                for value, role in expand_controls(normal, controls):
                    value["metadata"].update({"kernel_control_parent": strategy_id(normal), "kernel_control_role": role})
                    value = validate_strategy(value)
                    identity = self._identity(value)
                    prepared.append((value, role, "run_" + digest(identity), identity, None))
            except (ValueError, TypeError, KeyError) as exc:
                prepared.append((spec, "proposal", None, None, str(exc)[:1000]))
        if len(prepared) > self.config["budget"]["max_batch_size"]:
            raise ValueError("Batch plus controls exceeds max_batch_size; split the proposal")
        batch_id = "batch_" + uuid4().hex
        attempts = []
        with self.store.transaction() as db:
            if action_id:
                prior = db.execute("SELECT result FROM actions WHERE id=?", (action_id,)).fetchone()
                if prior:
                    return json.loads(prior[0])
            if self.store.meta("stopped"):
                raise ValueError("Study is stopped")
            count = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            if count + len(prepared) > self.config["budget"]["max_attempts"]:
                raise ValueError("Attempt budget exhausted; failed and reused proposals also count")
            for spec, role, run_id, identity, error in prepared:
                attempt_id = "try_" + uuid4().hex
                state = "rejected" if error else "queued"
                reused = False
                if run_id:
                    prior = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
                    if prior:
                        state = prior[0]
                        reused = True
                    else:
                        payload = {"strategy": spec, "identity": identity}
                        db.execute("INSERT INTO runs(id,spec,status,updated) VALUES (?,?,?,?)",
                                   (run_id, serial(payload), "queued", time.time()))
                name = str(spec.get("name", "invalid"))[:150] if isinstance(spec, dict) else "invalid"
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
                           (attempt_id, batch_id, run_id, name, role, state, error, time.time()))
                attempts.append({"id": attempt_id, "run_id": run_id, "name": name,
                                 "role": role, "status": state, "reused": reused, "error": error})
            result = {"batch_id": batch_id, "attempts": attempts}
            self.store.event(db, "batch_registered", {**result, "reason": reason[:1500]})
            if action_id:
                db.execute("INSERT INTO actions VALUES (?,?,?,?)", (action_id, "propose_batch", serial(result), time.time()))
        return result

    def _verify_artifacts(self, directory, expected_manifest_sha256=None):
        folder = (self.root / directory).resolve()
        if not folder.is_relative_to(self.root / "accounts"):
            raise ValueError("Account artifact path escaped study")
        manifest_raw = (folder / "manifest.json").read_bytes()
        if not expected_manifest_sha256 or hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256:
            raise ValueError("Completed manifest digest is not bound to ledger")
        manifest = json.loads(manifest_raw)
        if set(manifest) != {"daily.parquet", "trades.parquet", "annual.parquet", "result.json", "frozen_inputs.json"}:
            raise ValueError("Completed account manifest has an invalid artifact set")
        for name, expected in manifest.items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Completed account artifact digest mismatch")

    def execute_pending(self, panel=None, *, cancelled=None):
        from .execution import execute_strategy
        from .evidence import compact_account, factor_report, next_diagnostics
        cancelled = cancelled or (lambda: (self.root / "cancel.request").exists())
        output = []
        with exclusive_lock(self.root / "executor.lock"):
            # OS lock proves any previous executor has exited. An unfinished
            # execution consumes its budget; no partial account is reused.
            with self.store.transaction() as db:
                interrupted = db.execute("SELECT id FROM runs WHERE status='running'").fetchall()
                for row in interrupted:
                    db.execute("UPDATE runs SET status='queued',error='interrupted; restarting from frozen inputs' WHERE id=?", (row[0],))
                    db.execute("UPDATE attempts SET status='queued' WHERE run_id=?", (row[0],))
                    self.store.event(db, "interrupted_execution_recovered", {"run_id": row[0]})
            if self.store.meta("stopped") or cancelled():
                return {"status": "stopped", "runs": []}
            panel = panel if panel is not None else load_market_panel(**self.config["data"])
            if panel.fingerprint() != self.config["panel_fingerprint"]:
                raise ValueError("Panel differs from frozen study snapshot")
            for row in self.store.rows("SELECT * FROM runs WHERE status='completed'"):
                self._verify_artifacts(row["artifact_dir"], row["artifact_manifest_sha256"])
            # Ledger insertion order is deterministic even when Windows gives
            # several proposals the same clock tick. Hash ordering must never
            # decide which candidate consumes a limited execution budget.
            rows = self.store.rows("SELECT * FROM runs WHERE status='queued' ORDER BY rowid")
            factor_memo = {}
            for row in rows:
                if cancelled() or self.store.meta("stopped"):
                    break
                with self.store.transaction() as db:
                    consumed = db.execute("SELECT COALESCE(SUM(executions),0) FROM runs").fetchone()[0]
                    if consumed >= self.config["budget"]["max_executions"]:
                        self.store.event(db, "execution_budget_exhausted", {"remaining_queued": len(rows) - len(output)})
                        break
                    db.execute("UPDATE runs SET status='running',executions=executions+1,updated=? WHERE id=?", (time.time(), row["id"]))
                    db.execute("UPDATE attempts SET status='running' WHERE run_id=?", (row["id"],))
                    self.store.event(db, "execution_started", {"run_id": row["id"]})
                started = time.perf_counter()
                try:
                    frozen = json.loads(row["spec"])
                    spec = frozen["strategy"]
                    if self._identity(spec) != frozen["identity"]:
                        raise ValueError("Frozen implementation or factor definition changed; submit a new version")
                    frames, cache = self.assets.resolve(_factor_ids(spec), panel)
                    if set(frames) != _factor_ids(spec):
                        raise ValueError("Required factor values are unavailable: " + serial(cache.get("unavailable", {})))
                    factor_key = digest(sorted(_factor_ids(spec)))
                    persistent_factor_key = "factor_evidence:" + digest({
                        "panel": self.config["panel_fingerprint"], "scope": [self.config["start"], self.config["end"]],
                        "factor_set": {key: self.assets.get(key)["formula_id"] for key in sorted(frames)},
                        "engine": frozen["identity"]["engine"]})
                    if factor_key not in factor_memo:
                        previous_report = self.store.meta(persistent_factor_key)
                        if previous_report:
                            self.store.evidence(previous_report, pointer="/scope")
                            factor_memo[factor_key] = previous_report
                        else:
                            report = factor_report(panel, frames, start=self.config["start"], end=self.config["end"])
                            factor_memo[factor_key] = self.store.put_evidence("factor_diagnostics", report)
                            with self.store.connect() as db:
                                self.store.set_meta(db, persistent_factor_key, factor_memo[factor_key])
                    account = execute_strategy(panel, frames, spec, start=self.config["start"], end=self.config["end"],
                                               policy=AccountPolicy(**self.config["account_policy"]), cancelled=cancelled)
                    brief = compact_account(account)
                    brief.update({"run_id": row["id"], "strategy": spec,
                                  "factor_evidence_id": factor_memo[factor_key], "cache": cache,
                                  "duration_seconds": time.perf_counter() - started,
                                  "suggested_diagnostics": next_diagnostics(brief)})
                    folder = self.root / "accounts" / (row["id"] + "_" + uuid4().hex[:8])
                    folder.mkdir(parents=True)
                    for key in ("daily", "trades", "annual"):
                        frame = account[key].copy(deep=False)
                        frame.attrs = {}
                        frame.to_parquet(folder / (key + ".parquet"))
                    write_json(folder / "result.json", brief)
                    write_json(folder / "frozen_inputs.json", frozen)
                    write_json(folder / "manifest.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()})
                    manifest_sha = hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest()
                    evidence_id = self.store.put_evidence("account", brief)
                    directory = str(folder.relative_to(self.root))
                    with self.store.transaction() as db:
                        db.execute("UPDATE runs SET status='completed',evidence_id=?,artifact_dir=?,artifact_manifest_sha256=?,error=NULL,updated=? WHERE id=?",
                                   (evidence_id, directory, manifest_sha, time.time(), row["id"]))
                        db.execute("UPDATE attempts SET status='completed',error=NULL WHERE run_id=?", (row["id"],))
                        self.store.event(db, "execution_completed", {"run_id": row["id"], "evidence_id": evidence_id,
                                         "duration_seconds": brief["duration_seconds"], "cache": cache})
                    output.append({"run_id": row["id"], "status": "completed", "evidence_id": evidence_id})
                except Exception as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)[:1500]}
                    state = "cancelled" if cancelled() else "failed"
                    with self.store.transaction() as db:
                        db.execute("UPDATE runs SET status=?,error=?,updated=? WHERE id=?", (state, serial(error), time.time(), row["id"]))
                        db.execute("UPDATE attempts SET status=?,error=? WHERE run_id=?", (state, serial(error), row["id"]))
                        self.store.event(db, "execution_" + state, {"run_id": row["id"], "error": error})
                    output.append({"run_id": row["id"], "status": state, "error": error})
            self.refresh_comparisons()
            if self.store.meta("model_stopped", False):
                with self.store.transaction() as db:
                    pending = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
                    if not pending:
                        self.store.set_meta(db, "stopped", True)
        return {"status": "finished_pass", "runs": output, "study": self.status()}

    def retry_run(self, run_id):
        """Explicit retry preserves all prior executions and their budget cost."""
        with self.store.transaction() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or row[0] not in {"failed", "cancelled"}:
                raise ValueError("Only a failed or cancelled run can be explicitly retried")
            consumed = db.execute("SELECT COALESCE(SUM(executions),0) FROM runs").fetchone()[0]
            if consumed >= self.config["budget"]["max_executions"]:
                raise ValueError("Execution budget exhausted")
            db.execute("UPDATE runs SET status='queued',updated=? WHERE id=?", (time.time(), run_id))
            db.execute("UPDATE attempts SET status='queued' WHERE run_id=?", (run_id,))
            self.store.event(db, "explicit_retry", {"run_id": run_id, "previous_status": row[0]})
        return {"run_id": run_id, "status": "queued"}

    def refresh_comparisons(self):
        batches = self.store.rows("SELECT DISTINCT batch_id FROM attempts")
        for batch in batches:
            rows = self.store.rows("SELECT a.name,a.role,a.status,a.error,a.run_id,r.evidence_id,r.spec FROM attempts a LEFT JOIN runs r ON a.run_id=r.id WHERE a.batch_id=? ORDER BY a.created", (batch["batch_id"],))
            result = []
            for row in rows:
                frozen = json.loads(row.pop("spec")) if row.get("spec") else None
                row.pop("spec", None)
                row["control_parent"] = frozen["strategy"]["metadata"].get("kernel_control_parent") if frozen else None
                if row["evidence_id"]:
                    ev = self.store.evidence(row["evidence_id"], pointer="/summary", limit=100)["value"]
                    row["summary"] = ev
                result.append(row)
            baselines = {r["control_parent"]: r["summary"] for r in result if r["role"] == "proposal" and "summary" in r}
            for row in result:
                baseline = baselines.get(row["control_parent"])
                if baseline and "summary" in row:
                    row["delta_from_proposal"] = {key: value - baseline[key] for key, value in row["summary"].items()
                        if type(value) in (int, float) and type(baseline.get(key)) in (int, float)}
            eid = self.store.put_evidence("paired_batch", {"batch_id": batch["batch_id"], "arms": result,
                "interpretation": "Paired development results. Keep failed and negative arms; no independent significance claim."})
            with self.store.connect() as db:
                self.store.set_meta(db, "batch_evidence:" + batch["batch_id"], eid)

    def status(self):
        counts = self.store.rows("SELECT status,COUNT(*) AS count FROM runs GROUP BY status")
        return {"version": "research_kernel_v1", "stopped": self.store.meta("stopped", False),
                "revision": self.store.rows("SELECT COALESCE(MAX(seq),0) AS seq FROM events")[0]["seq"],
                "runs": {r["status"]: r["count"] for r in counts},
                "attempts": self.store.rows("SELECT COUNT(*) AS n FROM attempts")[0]["n"],
                "executions": self.store.rows("SELECT COALESCE(SUM(executions),0) AS n FROM runs")[0]["n"],
                "model_calls": self.store.rows("SELECT COUNT(*) AS n FROM model_calls")[0]["n"],
                "budget": self.config["budget"], "scope": {k: self.config[k] for k in ("start", "end", "scope_role")}}

    def apply_action(self, response, *, action_id=None):
        from .protocol import validate_action
        action, payload = validate_action(response, self.config["budget"]["max_response_bytes"])
        if action_id:
            prior = self.store.rows("SELECT result FROM actions WHERE id=?", (action_id,))
            if prior:
                return json.loads(prior[0]["result"])
        if action == "propose_batch":
            return self.submit_batch(payload["specs"], controls=payload.get("controls", []),
                                     reason=response["reason"], action_id=action_id)
        if action == "register_factor":
            asset = deepcopy(payload["asset"])
            asset["source"] = {"kind": "researcher_action", "action_id": action_id,
                               "note": "Formula syntax validated; not evidence of alpha or out-of-sample success"}
            result = {"asset": self.assets.register(asset)}
        elif action == "query_assets":
            values = self.assets.list(**payload)
            eid = self.store.put_evidence("asset_query", {"assets": values, "query": payload})
            brief = [{k: row.get(k) for k in ("id", "name", "expression", "roles", "status", "required_fields")}
                     for row in values]
            result = {"assets": brief, "full_evidence_id": eid, "returned": len(values)}
            while len(serial(result).encode("utf-8")) > 8000 and result["assets"]:
                result["assets"].pop()
                result["additional_rows_in_full_evidence"] = True
        elif action == "get_evidence":
            result = self.store.evidence(**payload)
        elif action == "request_extension":
            result = {"status": "capability_request_recorded", "request": payload,
                      "executable": False, "instruction": "Implement and validate operator once before registering it. Existing branches can continue."}
        else:
            result = {"status": "stopped", "reason": response["reason"]}
        with self.store.transaction() as db:
            if action == "stop":
                self.store.set_meta(db, "stopped", True)
            self.store.event(db, "action_" + action, {"reason": response["reason"], "result": result})
            if action_id:
                db.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?)", (action_id, action, serial(result), time.time()))
        return result
