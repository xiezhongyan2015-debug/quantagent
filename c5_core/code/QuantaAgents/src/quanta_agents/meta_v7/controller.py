"""Persistent factor-first research; all numerical stages have retained evidence.

The older kernel supplies its safe strategy language, accounting artifacts and
paired controls. V7 owns the stage machine, date boundary and global lineage.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from quanta_agents.meta_v6.data import load_market_panel
from quanta_agents.meta_v6.portfolio import AccountPolicy
from quanta_agents.research_kernel.controller import ResearchKernel, _factor_ids
from quanta_agents.research_kernel.store import digest, exclusive_lock, serial, write_json
from .ledger import ProjectLedger
from .temporal import scope_panel


LIMITS = {"max_factor_jobs": 8, "max_factor_assets": 32, "max_pair_tests": 32,
          "max_validation_jobs": 2, "closing_model_calls": 1, "max_rounds": 4}


class V7ResearchKernel(ResearchKernel):
    def __init__(self, root):
        super().__init__(root)
        with self.store.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS factor_jobs(id TEXT PRIMARY KEY,spec TEXT,status TEXT,
                evidence_id TEXT,error TEXT,executions INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS stage_evidence(stage_id TEXT,evidence_id TEXT,kind TEXT,
                PRIMARY KEY(stage_id,evidence_id));
              CREATE TABLE IF NOT EXISTS batch_reviews(batch_id TEXT PRIMARY KEY,review TEXT);
              CREATE TABLE IF NOT EXISTS validation_jobs(id TEXT PRIMARY KEY,run_id TEXT,spec TEXT,
                status TEXT,evidence_id TEXT,error TEXT,executions INTEGER DEFAULT 0,review TEXT);
              CREATE TABLE IF NOT EXISTS account_budget(id TEXT PRIMARY KEY,stage_id TEXT,phase TEXT,created REAL);
            """)
        if self.store.meta("v7_config"):
            self._bind_project(self.store.meta("v7_config")["project_root"])

    def _bind_project(self, root):
        from .assets import V7AssetRegistry as AssetRegistry
        self.project = ProjectLedger(Path(root) / "ledger")
        self.assets = AssetRegistry(Path(root) / "assets")

    @property
    def v7(self):
        return self.store.meta("v7_config")

    @property
    def research_profile(self):
        return (self.v7 or {}).get("research_profile", "v7")

    def initialize(self, config, panel=None):
        from .validation import validate_split_plan
        cfg = deepcopy(config)
        allowed = {"split_plan", "project_root", "study_id", "data", "budget", "account_policy",
                   "objective", "research_limits", "validation_mode", "prior_exposures", "research_profile"}
        if set(cfg) - allowed or "split_plan" not in cfg:
            raise ValueError("V7 requires split_plan and known configuration keys")
        if cfg.get("research_profile", "v7") not in {"v7", "v8"}:
            raise ValueError("Unknown frozen research profile")
        plan = cfg["split_plan"]
        limits = {**LIMITS, **cfg.get("research_limits", {})}
        if set(limits) != set(LIMITS) or any(type(v) is not int or v < 1 for v in limits.values()):
            raise ValueError("Research limits must be positive bounded integers")
        if limits["max_factor_assets"] > 64 or limits["max_pair_tests"] > 64:
            raise ValueError("Factor and pair jobs are capped at 64")
        cfg["research_limits"] = limits
        cfg.setdefault("study_id", "study_" + digest(str(self.root))[:24])
        cfg.setdefault("project_root", str(Path(__file__).resolve().parents[3] / "output/research/meta_v7_project"))
        cfg["project_root"] = str(Path(cfg["project_root"]).resolve())
        cfg.setdefault("validation_mode", "exposed_temporal")
        if cfg["validation_mode"] not in {"exposed_temporal", "independent_required"}:
            raise ValueError("Unknown validation mode")
        cfg.setdefault("prior_exposures", [])
        budget = {"max_model_calls": 8, **cfg.get("budget", {})}
        if budget["max_model_calls"] <= limits["closing_model_calls"]:
            raise ValueError("Budget must leave research calls before reserved closing calls")
        cfg["budget"] = budget
        old = self.v7
        if old and old != cfg:
            raise ValueError("V7 configuration is frozen")
        # Never load validation values to initialize training. Calendar-only
        # declaration checks are completed again by the validation worker.
        import pandas as pd
        dates = panel.dates if panel is not None else pd.DatetimeIndex(
            pd.to_datetime(Path(cfg["data"]["calendar_path"]).read_text(encoding="utf-8").splitlines()))
        validate_split_plan(plan, dates)
        data = deepcopy(cfg.get("data", {}))
        if data:
            data["end"] = plan["train_end"]
        train = scope_panel(panel, end=plan["train_end"]) if panel is not None else load_market_panel(**data)
        self._bind_project(cfg["project_root"])
        with self.store.connect() as db:
            self.store.set_meta(db, "v7_config", cfg)
        core = {"start": plan["train_start"], "end": plan["train_end"], "data": data,
                "budget": budget, "account_policy": cfg.get("account_policy", {}),
                "objective": cfg.get("objective", "Develop and falsify factor combinations"),
                "scope_role": "exposed_development"}
        super().initialize(core, train)
        self.project.register_study(cfg["study_id"], cfg)
        for i, exposed in enumerate(cfg["prior_exposures"]):
            self.project.record_exposure(cfg["study_id"], start=exposed["start"], end=exposed["end"],
                role="previously_exposed_development", evidence_id="prior_" + str(i), reason=exposed["reason"])
        write_json(self.root / "v7_config.json", cfg)
        return self.status()

    def _identity(self, spec):
        value = super()._identity(spec)
        base = Path(__file__).parent
        value["v7_engine"] = {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
            for name in ("controller.py", "temporal.py", "execution.py", "factor_lab.py", "assets.py", "validation.py")}
        if self.research_profile == "v8":
            value["v8_research_interface"] = {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
                for name in ("revisions.py", "decision_evidence.py", "protocol.py")}
        return value

    def _link(self, stage, kind, value):
        eid = self.store.put_evidence(kind, value)
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO stage_evidence VALUES (?,?,?)", (stage, eid, kind))
            self.store.event(db, "stage_evidence", {"stage_id": stage, "kind": kind, "evidence_id": eid})
        return eid

    def _trial(self, attempt, kind, spec, status, evidence=None):
        return self.project.record_trial(self.v7["study_id"], "event_" + digest([attempt, status, evidence])[:40],
            kind, {"attempt_id": attempt, "experiment": spec}, status=status, evidence=evidence)

    def pending(self):
        return sum(self.store.rows("SELECT COUNT(*) AS n FROM " + table + " WHERE status IN ('queued','running')")[0]["n"]
                   for table in ("factor_jobs", "runs", "validation_jobs"))

    def evaluate_factors(self, ids, *, horizons=(5, 20), pairs=None):
        if self.store.meta("stopped"):
            raise ValueError("Study is stopped")
        if self.store.rows("SELECT id FROM validation_jobs"):
            raise ValueError("Validation is unlocked; new training choices need a new exposed study")
        limit = self.v7["research_limits"]
        if (not isinstance(ids, list) or not ids or len(ids) > limit["max_factor_assets"]
                or len(set(ids)) != len(ids)):
            raise ValueError("Provide a bounded unique factor id list")
        from .factor_lab import semantics
        semantics(horizons)
        if max(horizons) > self.v7["split_plan"]["max_label_horizon"]:
            raise ValueError("Label horizon exceeds the frozen split contract")
        pairs = pairs or []
        if len(pairs) > limit["max_pair_tests"]:
            raise ValueError("Pair budget exceeded")
        for pair in pairs:
            if set(pair) != {"left", "right"} or not {pair["left"], pair["right"]} <= set(ids):
                raise ValueError("Pair must use declared factor ids")
        spec = {"ids": sorted(ids), "horizons": list(horizons), "pairs": pairs,
                "formulas": {i: self.assets.get(i)["formula_id"] for i in sorted(ids)},
                "scope": [self.config["start"], self.config["end"]], "panel": self.config["panel_fingerprint"]}
        job_id = "factor_" + digest(spec)
        with self.store.transaction() as db:
            old = db.execute("SELECT * FROM factor_jobs WHERE id=?", (job_id,)).fetchone()
            if old:
                return {"job_id": job_id, "status": old["status"], "reused": True, "evidence_id": old["evidence_id"]}
            if db.execute("SELECT COUNT(*) FROM factor_jobs").fetchone()[0] >= limit["max_factor_jobs"]:
                raise ValueError("Factor job budget exhausted")
            db.execute("INSERT INTO factor_jobs(id,spec,status) VALUES (?,?,?)", (job_id, serial(spec), "queued"))
            self.store.event(db, "factor_job_registered", {"job_id": job_id, "spec": spec})
        self._trial(job_id, "factor_test", spec, "registered")
        return {"job_id": job_id, "status": "queued", "account_executions": 0}

    def factor_evidence(self):
        coverage = {}
        for row in self.store.rows("SELECT evidence_id FROM factor_jobs WHERE status='completed' ORDER BY rowid"):
            factors = self.store.evidence(row["evidence_id"], pointer="/factors", limit=100, max_bytes=1000000)["value"]
            for key, value in factors.items():
                if value.get("coverage", {}).get("observed_cells", 0) > 0:
                    coverage[key] = row["evidence_id"]
        return coverage

    def _unreviewed(self):
        return self.store.rows("SELECT DISTINCT batch_id FROM attempts WHERE batch_id NOT IN (SELECT batch_id FROM batch_reviews)")

    def submit_batch(self, specs, *, controls=None, reason="", action_id=None, _revision=None):
        if action_id:
            old = self.store.rows("SELECT result FROM actions WHERE id=?", (action_id,))
            if old:
                return json.loads(old[0]["result"])
        if self._unreviewed():
            raise ValueError("Review the preceding batch before choosing the next experiment")
        if self.research_profile == "v8":
            latest = self.store.rows("SELECT review FROM batch_reviews ORDER BY rowid DESC LIMIT 1")
            if latest and json.loads(latest[0]["review"])["verdict"] == "revise" and _revision is None:
                raise ValueError("The latest review binds a revision; use revise_batch with its parent run")
        if self.store.rows("SELECT id FROM validation_jobs"):
            raise ValueError("Candidate is frozen for validation; no further training selection in this study")
        if self.store.rows("SELECT COUNT(*) AS n FROM batch_reviews")[0]["n"] >= self.v7["research_limits"]["max_rounds"]:
            raise ValueError("Research round budget exhausted")
        evidence = self.factor_evidence()
        for spec in specs:
            missing = _factor_ids(spec) - set(evidence)
            if missing:
                raise ValueError("Evaluate these factors before portfolio selection: " + ", ".join(sorted(missing)))
        result = super().submit_batch(specs, controls=controls, reason=reason, action_id=action_id)
        for attempt in result["attempts"]:
            frozen = self.store.rows("SELECT spec FROM runs WHERE id=?", (attempt["run_id"],)) if attempt["run_id"] else []
            value = json.loads(frozen[0]["spec"]) if frozen else {"rejected": attempt["error"]}
            self._trial(attempt["id"], "portfolio", value, "reused" if attempt["reused"] else attempt["status"])
            if frozen:
                with self.store.connect() as db:
                    for eid in set(evidence[i] for i in _factor_ids(value["strategy"])):
                        db.execute("INSERT OR IGNORE INTO stage_evidence VALUES (?,?,?)", (attempt["run_id"], eid, "factor_lab"))
        return result

    def review_batch(self, batch_id, verdict, conclusion, candidate_run_id=None, revision_plan=None):
        if verdict not in {"revise", "reject", "freeze_for_validation", "stop"} or not isinstance(conclusion, str) or not conclusion.strip():
            raise ValueError("Provide a substantive review and a supported verdict")
        rows = self.store.rows("SELECT * FROM attempts WHERE batch_id=?", (batch_id,))
        if not rows or any(r["status"] in {"queued", "running"} for r in rows):
            raise ValueError("Review requires a finished batch")
        value = {"batch_id": batch_id, "verdict": verdict, "conclusion": conclusion,
                 "candidate_run_id": candidate_run_id, "evidence_id": self.store.meta("batch_evidence:" + batch_id)}
        if revision_plan is not None or (self.research_profile == "v8" and verdict == "revise"):
            if self.research_profile != "v8" or verdict != "revise":
                raise ValueError("A revision plan requires the v8 profile and revise verdict")
            from .revisions import freeze_revision_plan
            value["revision_plan"] = freeze_revision_plan(self, batch_id, revision_plan)
        old = self.store.rows("SELECT review FROM batch_reviews WHERE batch_id=?", (batch_id,))
        if old:
            previous = json.loads(old[0]["review"])
            if {k: previous.get(k) for k in value} != value:
                raise ValueError("Batch review is immutable")
            return previous
        if verdict == "freeze_for_validation":
            if not any(r["run_id"] == candidate_run_id and r["status"] == "completed" for r in rows):
                raise ValueError("Freeze one completed training candidate from this batch")
            frozen = json.loads(self.store.rows("SELECT spec FROM runs WHERE id=?", (candidate_run_id,))[0]["spec"])
            job_id = "validation_" + digest([candidate_run_id, self.v7["split_plan"]])
            allowed = self.project.can_validate(self.v7["study_id"], start=self.v7["split_plan"]["validation_start"], end=self.v7["split_plan"]["validation_end"])
            if self.v7["validation_mode"] == "independent_required" and not allowed["allowed"]:
                raise ValueError("Independent validation blocked by recorded exposure: " + str(allowed["reason"]))
            with self.store.transaction() as db:
                db.execute("INSERT OR IGNORE INTO validation_jobs(id,run_id,spec,status) VALUES (?,?,?,?)",
                           (job_id, candidate_run_id, serial({"frozen": frozen, "exposure_check": allowed}), "queued"))
            self._trial(job_id, "temporal_validation", {"frozen": frozen, "plan": self.v7["split_plan"]}, "registered")
            value["validation_job_id"] = job_id
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO batch_reviews VALUES (?,?)", (batch_id, serial(value)))
            self.store.event(db, "batch_review", value)
            if verdict == "stop":
                self.store.set_meta(db, "stopped", True)
        return value

    def revise_batch(self, parent_run_id, review_batch_id, changes, *, name=None,
                     controls=None, reason="", action_id=None):
        """Materialize a reviewed change while inheriting every other parameter."""
        if self.research_profile != "v8":
            raise ValueError("revise_batch requires the frozen v8 research profile")
        from .revisions import derive_revision
        prior = self.store.rows("SELECT result FROM actions WHERE id=?", (action_id,)) if action_id else []
        if prior and "revision" in json.loads(prior[0]["result"]):
            return json.loads(prior[0]["result"])
        derived = derive_revision(self, parent_run_id, review_batch_id, changes, name=name)
        eid = self.store.put_evidence("v8_revision", derived["report"])
        result = self.submit_batch([derived["spec"]], controls=controls, reason=reason,
                                   action_id=action_id, _revision=derived["report"])
        result["revision"] = {"evidence_id": eid, **derived["report"]}
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO stage_evidence VALUES (?,?,?)", (result["batch_id"], eid, "revision"))
            if action_id:
                db.execute("UPDATE actions SET action=?,result=? WHERE id=?", ("revise_batch", serial(result), action_id))
            self.store.event(db, "revision_registered", {"batch_id": result["batch_id"], "evidence_id": eid})
        return result

    def review_validation(self, job_id, conclusion):
        rows = self.store.rows("SELECT * FROM validation_jobs WHERE id=?", (job_id,))
        if not rows or rows[0]["status"] in {"queued", "running"} or not isinstance(conclusion, str) or not conclusion.strip():
            raise ValueError("A finished validation and explicit conclusion are required")
        value = {"job_id": job_id, "conclusion": conclusion, "status": "research_closed",
                 "independent_profitability_proven": False}
        if rows[0]["review"] and json.loads(rows[0]["review"]) != value:
            raise ValueError("Validation review is immutable")
        with self.store.transaction() as db:
            db.execute("UPDATE validation_jobs SET review=? WHERE id=?", (serial(value), job_id))
            self.store.set_meta(db, "stopped", True)
            self.store.event(db, "validation_review", value)
        return value

    def _finish(self, table, row, state, eid=None, error=None):
        with self.store.transaction() as db:
            db.execute("UPDATE " + table + " SET status=?,evidence_id=?,error=? WHERE id=?", (state, eid, serial(error) if error else None, row["id"]))
            if table == "runs":
                db.execute("UPDATE attempts SET status=?,error=? WHERE run_id=?", (state, serial(error) if error else None, row["id"]))
            self.store.event(db, "stage_" + state, {"stage_id": row["id"], "evidence_id": eid, "error": error})
        if table == "runs":
            for attempt in self.store.rows("SELECT id FROM attempts WHERE run_id=?", (row["id"],)):
                self._trial(attempt["id"], "portfolio", json.loads(row["spec"]), state, eid)
        else:
            value = json.loads(row["spec"])
            spec = value if table == "factor_jobs" else {"frozen": value["frozen"], "plan": self.v7["split_plan"]}
            self._trial(row["id"], "factor_test" if table == "factor_jobs" else "temporal_validation", spec, state, eid)

    def _reserve_account(self, stage_id, phase):
        with self.store.transaction() as db:
            used = db.execute("SELECT COUNT(*) FROM account_budget").fetchone()[0]
            if used >= self.config["budget"]["max_executions"]:
                raise ValueError("Shared account budget exhausted, including validation accounts")
            db.execute("INSERT INTO account_budget VALUES (?,?,?,?)", ("account_" + uuid4().hex, stage_id, phase, time.time()))
            self.store.event(db, "account_started", {"stage_id": stage_id, "phase": phase})

    def execute_pending(self, panel=None, *, cancelled=None):
        from .factor_lab import evaluate_factors
        from .execution import execute_strategy
        from .validation import evaluate_frozen_candidates
        from quanta_agents.research_kernel.evidence import compact_account
        cancelled = cancelled or (lambda: (self.root / "cancel.request").exists())
        results = []
        with exclusive_lock(self.root / "executor.lock"):
            with self.store.transaction() as db:
                for table in ("factor_jobs", "runs", "validation_jobs"):
                    db.execute("UPDATE " + table + " SET status='queued',error='interrupted stage; frozen restart' WHERE status='running'")
                db.execute("UPDATE attempts SET status='queued' WHERE run_id IN (SELECT id FROM runs WHERE status='queued')")
            if self.store.meta("stopped") or cancelled():
                return {"status": "stopped", "stages": []}
            train = scope_panel(panel, end=self.config["end"]) if panel is not None else load_market_panel(**self.config["data"])
            if train.fingerprint() != self.config["panel_fingerprint"]:
                raise ValueError("Training panel differs from frozen snapshot")
            for old in self.store.rows("SELECT * FROM runs WHERE status='completed'"):
                self._verify_artifacts(old["artifact_dir"], old["artifact_manifest_sha256"])
            for table in ("factor_jobs", "runs", "validation_jobs"):
                for row in self.store.rows("SELECT * FROM " + table + " WHERE status='queued' ORDER BY rowid"):
                    if cancelled() or self.store.meta("stopped"):
                        break
                    limit = self.config["budget"]["max_executions"] if table == "runs" else self.v7["research_limits"]["max_factor_jobs" if table == "factor_jobs" else "max_validation_jobs"]
                    if self.store.rows("SELECT COALESCE(SUM(executions),0) AS n FROM " + table)[0]["n"] >= limit:
                        self._finish(table, row, "budget_blocked", error={"message": "stage execution budget exhausted"})
                        continue
                    with self.store.connect() as db:
                        db.execute("UPDATE " + table + " SET status='running',executions=executions+1 WHERE id=?", (row["id"],))
                    started = time.perf_counter()
                    try:
                        spec = json.loads(row["spec"])
                        if table == "factor_jobs":
                            for key, formula_id in spec["formulas"].items():
                                if self.assets.get(key)["formula_id"] != formula_id:
                                    raise ValueError("Factor definition changed")
                            frames, cache = self.assets.resolve(spec["ids"], train)
                            if not frames:
                                raise ValueError("No declared factor is computable")
                            report = evaluate_factors(train, frames, start=self.config["start"], end=self.config["end"],
                                horizons=spec["horizons"], pairs=[p for p in spec["pairs"] if {p["left"], p["right"]} <= set(frames)],
                                controls={"roles": {i: self.assets.get(i)["roles"] for i in frames}})
                            report["asset_availability"] = cache
                            report["catalogue_neighbors"] = self.assets.describe_candidates(list(frames), diagnostic_report=report)
                            eid = self._link(row["id"], "factor_lab", report)
                        elif table == "runs":
                            if self._identity(spec["strategy"]) != spec["identity"]:
                                raise ValueError("Frozen engine or formula changed; new study required")
                            frames, cache = self.assets.resolve(_factor_ids(spec["strategy"]), train)
                            if set(frames) != _factor_ids(spec["strategy"]):
                                raise ValueError("Required factor values unavailable")
                            self._reserve_account(row["id"], "training")
                            account = execute_strategy(train, frames, spec["strategy"], start=self.config["start"], end=self.config["end"],
                                policy=AccountPolicy(**self.config["account_policy"]), cancelled=cancelled)
                            brief = compact_account(account)
                            brief.update({"run_id": row["id"], "strategy": spec["strategy"], "cache": cache,
                                "factor_evidence_ids": [v["evidence_id"] for v in self.store.rows("SELECT evidence_id FROM stage_evidence WHERE stage_id=?", (row["id"],))],
                                "duration_seconds": time.perf_counter() - started})
                            folder = self.root / "accounts" / (row["id"] + "_" + uuid4().hex[:8])
                            folder.mkdir(parents=True)
                            for key in ("daily", "trades", "annual"):
                                frame = account[key].copy(deep=False)
                                frame.attrs = {}
                                frame.to_parquet(folder / (key + ".parquet"))
                            write_json(folder / "result.json", brief)
                            write_json(folder / "frozen_inputs.json", spec)
                            write_json(folder / "manifest.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()})
                            eid = self._link(row["id"], "account", brief)
                            with self.store.connect() as db:
                                db.execute("UPDATE runs SET artifact_dir=?,artifact_manifest_sha256=?,updated=? WHERE id=?", (str(folder.relative_to(self.root)), hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest(), time.time(), row["id"]))
                        else:
                            plan = self.v7["split_plan"]
                            if self._identity(spec["frozen"]["strategy"]) != spec["frozen"]["identity"]:
                                raise ValueError("Frozen training engine or formula changed before validation")
                            # Mark exposure BEFORE reading. A failed worker still consumed data.
                            exposure = self.project.begin_validation(self.v7["study_id"], start=plan["validation_start"], end=plan["validation_end"],
                                evidence_id=row["id"], reason="Frozen candidate validation unlocked; any retry is already exposed",
                                require_independent=self.v7["validation_mode"] == "independent_required")
                            data = {**self.v7.get("data", {}), "end": plan["validation_end"]}
                            full = scope_panel(panel, end=plan["validation_end"]) if panel is not None else load_market_panel(**data)
                            frames, cache = self.assets.resolve(_factor_ids(spec["frozen"]["strategy"]), full)
                            report = evaluate_frozen_candidates(full, frames, [spec["frozen"]["strategy"]], plan,
                                policy=AccountPolicy(**self.config["account_policy"]), cancelled=cancelled,
                                before_account=lambda phase, sid: self._reserve_account(row["id"], phase),
                                artifact_dir=self.root / "validation_accounts" / ("v_" + digest(row["id"])[:12] + "_" + uuid4().hex[:8]))
                            report["project_exposure_before_unlock"] = exposure
                            report["independent_holdout_claim"] = False
                            eid = self._link(row["id"], "temporal_validation", report)
                        if table != "validation_jobs":
                            self.project.record_exposure(self.v7["study_id"], start=self.config["start"], end=self.config["end"],
                                role="training", evidence_id=eid, reason="Training evidence used in adaptive factor research")
                        incomplete = table == "validation_jobs" and any(r["status"] != "completed" for r in report["candidates"])
                        state = "failed" if incomplete else "completed"
                        self._finish(table, row, state, eid, {"message": "Frozen validation contains failed account stages"} if incomplete else None)
                        results.append({"stage_id": row["id"], "status": state, "evidence_id": eid})
                    except Exception as exc:
                        error = {"type": type(exc).__name__, "message": str(exc)[:1500],
                                 "retained_evidence": self.store.rows("SELECT evidence_id,kind FROM stage_evidence WHERE stage_id=?", (row["id"],))}
                        state = "cancelled" if cancelled() else "failed"
                        eid = self._link(row["id"], "stage_failure", error)
                        self._finish(table, row, state, eid, error)
                        results.append({"stage_id": row["id"], "status": state, "error": error})
            self.refresh_comparisons()
        return {"status": "finished_pass", "stages": results, "study": self.status()}

    def status(self):
        state = super().status()
        state["version"] = "meta_v8.0.0-dev" if self.research_profile == "v8" else "meta_v7.0.0"
        if self.research_profile == "v8":
            state["research_profile"] = "v8"
        state["factor_jobs"] = self.store.rows("SELECT id,status,evidence_id,error FROM factor_jobs ORDER BY rowid")
        state["validation_jobs"] = self.store.rows("SELECT id,run_id,status,evidence_id,error,review FROM validation_jobs ORDER BY rowid")
        state["unreviewed_batches"] = self._unreviewed()
        state["pending_stages"] = self.pending()
        state["account_executions"] = self.store.rows("SELECT COUNT(*) AS n FROM account_budget")[0]["n"]
        calls_left = state["budget"]["max_model_calls"] - state["model_calls"]
        state["closing_only"] = calls_left <= (self.v7 or {}).get("research_limits", LIMITS)["closing_model_calls"]
        if self.v7:
            state["split_plan"] = self.v7["split_plan"]
            state["validation_mode"] = self.v7["validation_mode"]
        return state

    def refresh_comparisons(self):
        """Failed stages retain evidence without being mistaken for accounts."""
        import pandas as pd
        from .validation import paired_sharpe_bootstrap
        for batch in self.store.rows("SELECT DISTINCT batch_id FROM attempts"):
            rows = self.store.rows("SELECT a.name,a.role,a.status,a.error,a.run_id,r.evidence_id,r.spec,r.artifact_dir FROM attempts a LEFT JOIN runs r ON a.run_id=r.id WHERE a.batch_id=? ORDER BY a.rowid", (batch["batch_id"],))
            arms, daily = [], {}
            for row in rows:
                frozen = json.loads(row.pop("spec")) if row.get("spec") else None
                row.pop("spec", None)
                directory = row.pop("artifact_dir")
                row["control_parent"] = frozen["strategy"]["metadata"].get("kernel_control_parent") if frozen else None
                if frozen:
                    row["strategy"] = frozen["strategy"]
                if row["status"] == "completed" and row["evidence_id"]:
                    report = self.store.evidence(row["evidence_id"], limit=100, max_bytes=500000)["value"]
                    row.update({k: report[k] for k in ("summary", "annual", "execution")})
                    daily[row["run_id"]] = pd.read_parquet(self.root / directory / "daily.parquet")["return"]
                arms.append(row)
            baselines = {r["control_parent"]: r for r in arms if r["role"] == "proposal" and "summary" in r}
            uncertainty = []
            for parent, baseline in baselines.items():
                controls = [r for r in arms if r["control_parent"] == parent and r["role"] != "proposal" and "summary" in r]
                for row in controls:
                    row["delta_from_proposal"] = {key: value - baseline["summary"][key] for key, value in row["summary"].items()
                        if type(value) in (int, float) and type(baseline["summary"].get(key)) in (int, float)}
                if controls:
                    try:
                        report = paired_sharpe_bootstrap(pd.DataFrame({r["run_id"]: daily[r["run_id"]] for r in controls}),
                            daily[baseline["run_id"]], block_sessions=20, repetitions=200, seed=20260909,
                            risk_free_rate=self.config["account_policy"]["risk_free_rate"])
                    except ValueError as exc:
                        report = {"status": "not_evaluable", "reason": str(exc)}
                    uncertainty.append({"control_parent": parent, "baseline_run_id": baseline["run_id"], "report": report})
            eid = self.store.put_evidence("v7_paired_batch", {"batch_id": batch["batch_id"], "arms": arms,
                "paired_uncertainty": uncertainty,
                "interpretation": "Development paired controls. Bootstrap covers the declared family, not adaptive historical selection. Gate gains require exposure/risk-matched follow-up."})
            with self.store.connect() as db:
                self.store.set_meta(db, "batch_evidence:" + batch["batch_id"], eid)

    def apply_action(self, response, *, action_id=None):
        from .protocol import validate_action
        action, payload = validate_action(response, self.config["budget"]["max_response_bytes"])
        if action_id:
            old = self.store.rows("SELECT result FROM actions WHERE id=?", (action_id,))
            if old:
                saved = json.loads(old[0]["result"])
                # Submission and revision decoration are separate durable writes.
                # Rebuild a missing decoration offline after a crash, without
                # registering another attempt or paying for a model response.
                if action != "revise_batch" or "revision" in saved:
                    return saved
        in_flight = bool(action_id and self.store.rows("SELECT id FROM model_calls WHERE id=? AND status IN ('started','verified','needs_recovery')", (action_id,)))
        remaining = self.config["budget"]["max_model_calls"] - self.status()["model_calls"] + int(in_flight)
        if remaining <= self.v7["research_limits"]["closing_model_calls"] and action not in {"review_batch", "review_validation", "stop", "get_evidence"}:
            raise ValueError("Reserved closing budget: review current evidence or stop")
        if action == "review_batch" and payload.get("verdict") == "freeze_for_validation" and remaining <= 1:
            raise ValueError("Freezing needs a remaining model call for the validation review")
        if action == "evaluate_factors":
            result = self.evaluate_factors(**payload)
        elif action == "review_batch":
            result = self.review_batch(**payload)
        elif action == "revise_batch":
            result = self.revise_batch(**payload, reason=response["reason"], action_id=action_id)
        elif action == "review_validation":
            result = self.review_validation(**payload)
        else:
            if action == "stop" and (self._unreviewed() or any(not r["review"] for r in self.status()["validation_jobs"])):
                raise ValueError("Review finished evidence explicitly before closing the research")
            return super().apply_action(response, action_id=action_id)
        with self.store.transaction() as db:
            self.store.event(db, "action_" + action, {"reason": response["reason"], "result": result})
            if action_id:
                db.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?)", (action_id, action, serial(result), time.time()))
        return result
