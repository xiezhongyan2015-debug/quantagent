"""Durable version/batch orchestration; scientific success belongs to the oracle."""
from __future__ import annotations
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import time
import numpy as np

from quanta_agents.research_kernel.store import exclusive_lock, write_json
from quanta_agents.meta_v6.factors import FactorSpec, expression_guide
from .protocol import default_protocol, validate_protocol, digest
from .candidates import inventory, library_neighborhoods, expand, interleave, merge_catalog, execution_queue
from .selection import annual_floor, descriptive_pass, factor_pool, combination_specs, version_decision


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def once(path, value):
    path = Path(path)
    if path.exists():
        if digest(read(path)) != digest(value):
            raise ValueError("Immutable evidence differs: " + str(path))
    else:
        write_json(path, value)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024**2), b""):
            h.update(part)
    return h.hexdigest()


def spec_of(row):
    v = row.get("spec", row)
    return FactorSpec(**{k: v[k] for k in ("name", "expression", "version", "parents", "metadata") if k in v})


def _dense46_control(spec, reference_ids=()):
    """Control identity is declared before results; it never depends on IC."""
    spec = spec.to_dict() if hasattr(spec, "to_dict") else spec or {}
    members = set(spec.get("feature_ids", []))
    return (spec.get("selection_rule") == "all_46_legacy_dense_control" or
            len(reference_ids) == 46 and members == set(reference_ids))


def _fit_availability(models):
    """Keep every update interval's availability, maturity and common sample."""
    if models is None:
        return {"recorded": False, "interval_count": None, "intervals": [], "statuses": {}}
    fields = ("status", "reason", "fit_start", "fit_end", "predict_start", "predict_end",
              "train_days", "train_cells", "sample_mask_id", "last_safe_signal_date", "last_label_endpoint")
    intervals = [{key: row.get(key) for key in fields if key in row} for row in models]
    return {"recorded": True, "interval_count": len(models), "statuses": dict(Counter(row.get("status", "unknown") for row in models)),
            "failure_reasons": dict(Counter(row.get("reason", "unspecified") for row in models if row.get("status") != "fitted")),
            "intervals": intervals}


def _source_reference(source):
    """Source identity stays inline; the pinned registration retains large tables."""
    if source is None:
        return None
    if not isinstance(source, dict):
        return {"reference": source} if isinstance(source, (str, int, float, bool)) else {"record_sha256": digest(source)}
    keys = ("kind", "type", "source_type", "id", "source_id", "action_id", "path", "file", "url",
            "source_path", "sha256", "function", "line", "end_line", "function_sha256",
            "adapter_version", "adapter_sha256")
    result = {key: source[key] for key in keys if key in source and isinstance(source[key], (str, int, float, bool, type(None)))}
    for key in ("source", "source_inventory", "provenance"):
        if key in source:
            result[key] = _source_reference(source[key])
    result["record_sha256"] = digest(source)
    return result


class Campaign:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._context = None
        self._signature_cache = None

    @property
    def config(self):
        path = self.root / "protocol.json"
        if sha(path) != read(self.root / "protocol_identity.json")["sha256"]:
            raise ValueError("Frozen campaign protocol changed")
        return validate_protocol(read(path))

    @property
    def state(self):
        return read(self.root / "state.json")

    @property
    def version_root(self):
        return self.root / "versions" / self.state["version"]

    def _state(self, **changes):
        state = {**self.state, **changes, "updated_utc": datetime.now(timezone.utc).isoformat()}
        if (self.root / "stop.request").exists():
            state["stop_requested"] = True
        write_json(self.root / "state.json", state)
        return state

    def stopped(self):
        return (self.root / "stop.request").exists() or self.state.get("stop_requested", False)

    def event(self, kind, **values):
        item = {"utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **values}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        print(json.dumps(item, ensure_ascii=True), flush=True)

    def initialize(self, source_root):
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.root / "campaign.lock"):
            if (self.root / "state.json").exists():
                return self.status()
            cfg = default_protocol(source_root)
            once(self.root / "protocol.json", cfg)
            once(self.root / "protocol_identity.json", {"sha256": sha(self.root / "protocol.json")})
            references = inventory(cfg["inventory_path"])
            once(self.root / "reference_catalog.json", references)
            vroot = self.root / "versions/V10A"
            vroot.mkdir(parents=True, exist_ok=True)
            write_json(vroot / "catalog.json", references)
            write_json(vroot / "summaries.json", {})
            write_json(vroot / "combination_summaries.json", {})
            write_json(self.root / "state.json", {
                "version": "V10A", "phase": "initialized", "batch": 0,
                "primary_evaluated": 0, "new_evaluated": 0, "reference_evaluated": 0,
                "inherited_evaluated": 0, "controls_evaluated": 0,
                "combinations_evaluated": 0, "combination_controls_evaluated": 0, "combination_controls_attempted": 0,
                "failures": 0, "numeric_duplicates": 0,
                "canonical_duplicates": 0, "numeric_wall_seconds": 0., "numeric_cpu_seconds": 0.,
                "best_factor_floor": None, "best_combination_floor": None,
                "plateau_extensions": 0, "historical_target_met": False,
                "independent_stability_proven": False, "profitability_proven": False,
                "blocker": None, "stop_requested": False, "framework_versions_completed": [],
                "created_utc": datetime.now(timezone.utc).isoformat()})
            self.event("initialized", references=len(references), protocol_sha256=sha(self.root / "protocol.json"))
            return self.status()

    def _model(self, action, kind, context):
        from .model import ModelLoop
        path = self.version_root / "decisions" / (action + ".json")
        if path.exists():
            return read(path)
        request_path = self.version_root / "decisions" / (action + ".request.json")
        if request_path.exists():
            frozen_request = read(request_path)
            kind, context = frozen_request["kind"], frozen_request["context"]
        else:
            once(request_path, {"kind": kind, "context": context})
        self._state(phase="model_" + kind, blocker=None)
        loop = ModelLoop(self.root)
        mapping_path = self.version_root / "decisions" / (action + ".attempts.json")
        mapping = read(mapping_path) if mapping_path.exists() else {"physical_actions": [action]}
        physical = mapping["physical_actions"][-1]
        response = loop.request(physical, kind, context)
        if response["status"] == "waiting" and hasattr(loop, "release_failed_attempt"):
            # Only a proven terminal no-answer may release an attempt; partial answers never retry.
            reason = str(response.get("blocker", {}).get("reason", ""))
            if "quota" not in reason and "rate_limit" not in reason:
                response = loop.release_failed_attempt(physical)
        if response["status"] == "failed_terminal":
            next_action = action + ".attempt" + str(len(mapping["physical_actions"]) + 1)
            mapping["physical_actions"].append(next_action)
            write_json(mapping_path, mapping)
            self._state(phase="waiting", blocker={"kind": "terminal_model_attempt", "completed_attempt": physical,
                                                  "next_resume_attempt": next_action})
            return None
        if response["status"] not in ("ready", "applied"):
            self._state(phase="waiting", blocker=response.get("blocker") or response.get("error"))
            return None
        def apply(value, action_id):
            once(path, value)
            return {"decision_path": str(path), "sha256": sha(path)}
        loop.apply_once(physical, apply)
        return read(path)

    def prepare(self):
        """A real model proposal precedes numeric batch search."""
        action = self.state["version"] + "_initial_proposal"
        refs = read(self.root / "reference_catalog.json")
        inherited_path = self.version_root / "inherited_catalog.json"
        prior_context = {}
        prior_parent_specs = []
        if inherited_path.exists():
            parent_version = self.state.get("last_parent_version")
            previous = self.root / "versions" / parent_version
            parent_files = sorted((previous / "batches").glob("*_parents.json"))
            selected_ids = {p["parent_id"] for p in read(parent_files[-1])} if parent_files else set()
            prior_parent_specs = [r for r in read(inherited_path) if r["factor_id"] in selected_ids]
            reviews = sorted((previous / "decisions").glob("*_review.json"))
            prior_context = {"parent_version": parent_version, "review": read(reviews[-1]) if reviews else None,
                             "framework_origin": read(self.version_root / "framework_origin.json")}
        context = {"request": "Propose 12-20 causal price/volume mechanism templates, total at least 300 expanded primary formulas. Existing library window neighborhoods will also be expanded programmatically, so focus on structural repair/conditional/residual/normalization paths and complementary roles. Parents must be listed registered IDs. Avoid only multiplying momentum variants. Conditions use inputs only, never calendar-year selection. Include matched main-effect controls. Families should have 2-12 window values, bounded 1..120 and short<long when applicable.",
            "dsl": expression_guide(),
            "registered_parents": [{"id": r["factor_id"], "name": r["spec"]["name"], "expression": r["spec"]["expression"], "roles": r["roles"]} for r in refs + prior_parent_specs],
            "preceding_version_evidence": prior_context,
            "prior_evidence": {"V9A": "69 unique total, 45 unique primary, 12/46 old definitions, 5 frozen candidates all failed; F1/pathtrend2019~.013/.020,2020~.072/.057,2021negative,2022~-.031/-.032,2023~.028,2024~.006/.003; old marginally low IC factor roles and evolving combinations were underexplored. Historical legacy IC is not same horizon/Pearson protocol."},
            "evidence_ids": ["V9A_release_acceptance", "all_46_reference_catalog", "approved_V10A_plan"]}
        proposal = self._model(action, "propose_v2" if inherited_path.exists() else "propose", context)
        if proposal is None:
            return self.status()
        families = proposal["templates"]
        origin = {"kind": "actual_gateway_decision", "action_id": action,
                  "path": str(self.version_root / "decisions" / (action + ".json"))}
        rows, attempts = expand(families, origin=origin)
        legacy_rows, legacy_attempts = expand(library_neighborhoods(refs), origin={"kind": "programmatic_legacy_windows", "model_calls": 0})
        inherited_path = self.version_root / "inherited_catalog.json"
        base = read(inherited_path) if inherited_path.exists() else refs
        catalog, dupes = merge_catalog(base, interleave(rows) + interleave(legacy_rows))
        catalog = self._bind_lineage(catalog)
        once(self.version_root / "initial_expansion.json", {"model_attempts": attempts, "legacy_attempts": legacy_attempts, "canonical_duplicates": dupes})
        write_json(self.version_root / "catalog.json", catalog)
        self._state(phase="prepared", canonical_duplicates=len(dupes) + sum(r["status"] == "duplicate" for r in attempts + legacy_attempts))
        self.event("prepared", catalog=len(catalog), model_families=len(families), control_candidates=sum(r["origin"] == "control" for r in catalog))
        return self.status()

    def _bind_lineage(self, rows):
        from .lineage import LineageRegistry
        registry = LineageRegistry(self.root)
        known = {r["factor_id"]: r for r in rows}
        registered_definitions = [r["spec"] for r in read(self.root / "reference_catalog.json")]
        for row in rows:
            parent_defs = [known[p]["spec"] for p in row.get("parents", []) if p in known]
            row.setdefault("lineage", {})["parent_definitions"] = parent_defs
            row["lineage"].setdefault("transformation_kind", "raw_formula")
            registered = registry.register(row["factor_id"], row["spec"]["expression"], parents=row.get("parents", []),
                role=row["roles"][0], route=row.get("route", "quality"), origin=row["origin"],
                version=self.state["version"], source=row.get("source"),
                aggregate_members=row["lineage"].get("aggregation_of_registered_factors", []),
                supervised=row["lineage"].get("supervised", False), parent_definitions=parent_defs,
                registered_definitions=registered_definitions)
            row["lineage"].update({k: registered[k] for k in ("transformation_kind", "parent_definitions", "supervised")})
            row["lineage"]["aggregation_of_registered_factors"] = registered["aggregate_members"]
            row["lineage"]["classification_reason"] = registered.get("classification_reason")
            row["lineage"]["matched_registered_ids"] = registered.get("matched_registered_ids", [])
            row["entity_type"] = registered["acceptance_route"]
        write_json(self.version_root / "lineage.json", registry.export())
        return rows

    def status(self):
        from .resources import CostLedger
        state = self.state
        state["gap_factor"] = None if state["best_factor_floor"] is None else .05 - state["best_factor_floor"]
        state["gap_combination"] = None if state["best_combination_floor"] is None else .10 - state["best_combination_floor"]
        state["cost"] = CostLedger(self.root).summary()
        return state

    def stop(self):
        (self.root / "stop.request").write_text("user_requested", encoding="utf-8")
        self._state(stop_requested=True)
        self.event("stop_requested")
        return self.status()

    def _resource_ready(self):
        from .resources import ResourceGuard
        # Context construction and per-job temporary arrays must fit ABOVE the reserve.
        extra = 2 * 1024**3 if self._context is None else 1024**3
        report = ResourceGuard(self.root).check(required_memory_bytes=extra)
        write_json(self.root / "resources_latest.json", report)
        if not report["allowed"]:
            self._state(phase="waiting", blocker={"kind": "resources", "details": report})
            return False
        return True

    def context(self):
        if self._context is None:
            from .numeric import NumericContext
            from quanta_agents.factor_research.adapters import load_research_panel
            panel = load_research_panel(**self.config["data"], cache_dir=self.root / "cache/panel")
            self._context = NumericContext(panel, cache_dir=self.root / "cache/numeric")
            self._context.register([spec_of(r) for r in read(self.version_root / "catalog.json")])
        return self._context

    def _save_frame(self, folder, frame):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "scores.npz"
        if not path.exists():
            temporary = folder / "scores.tmp.npz"
            np.savez_compressed(temporary, scores=frame.to_numpy(dtype=float),
                                dates=np.asarray(frame.index.strftime("%Y-%m-%d"), dtype="U10"),
                                columns=np.asarray(frame.columns, dtype=str))
            temporary.replace(path)
        return path

    def _factor_one(self, row):
        ctx = self.context()
        fid = row["factor_id"]
        folder = self.version_root / "factors" / fid
        path = folder / "report.json"
        if path.exists():
            return read(path)
        from .resources import CostLedger
        spec = spec_of(row)
        once(folder / "registration.json", row)
        with CostLedger(self.root).measure(self.state["version"] + ":factor:" + fid, kind="numeric_factor"):
            report = ctx.evaluate_factor(spec, start="2019-01-01", end="2024-12-31")
            report["entity_type"] = row["entity_type"]
            scores = ctx.compute(spec)
            score_path = self._save_frame(folder, scores)
            arr = scores.where(ctx.pool).to_numpy(dtype="<f8", copy=True)
            arr[np.isnan(arr)] = np.nan
            from .lineage import value_identity
            value_pin = value_identity(scores, sample_mask=ctx.pool)
            value_id = value_pin["values_sha256"]
            report["score_value_sha256"] = value_id
            summaries = read(self.version_root / "summaries.json")
            duplicates = [key for key, value in summaries.items() if value.get("score_value_sha256") == value_id]
            report["numeric_duplicate_of"] = duplicates[0] if duplicates else None
            # A small fixed training signature diagnoses redundancy; never substitutes for increment evidence.
            train = scores.loc["2016-01-01":"2018-12-31"].rank(axis=1, pct=True).to_numpy().ravel()
            positions = np.linspace(0, len(train) - 1, 4096, dtype=int)
            signature = train[positions]
            comparisons = []
            for other_id, other in self._signatures().items():
                common = np.isfinite(signature) & np.isfinite(other)
                if common.sum() < 100 or np.std(signature[common]) == 0 or np.std(other[common]) == 0:
                    continue
                correlation = float(np.corrcoef(signature[common], other[common])[0, 1])
                if abs(correlation) >= .98:
                    mask_id = hashlib.sha256(positions.tobytes() + common.tobytes()).hexdigest()
                    comparisons.append({"factor_id": other_id, "correlation": correlation,
                        "paired_sample_cells": int(common.sum()), "common_sample_sha256": mask_id,
                        "sample": "4096_fixed_training_grid_cells_2016_2018", "threshold": .98,
                        "not_independent_increment_evidence": True})
            report["high_correlation_ids"] = [r["factor_id"] for r in comparisons]
            report["high_correlation_pairs"] = comparisons
            report["pool_redundancy_rule"] = "retain one representative unless paired complementary evidence passes"
            np.save(folder / "training_signature.npy", signature, allow_pickle=False)
            self._signature_cache[fid] = signature
            report["redundancy_signature"] = "4096_fixed_training_cells_sampled_rank_correlation_diagnostic_only"
            once(folder / "package.json", {"entity_type": row["entity_type"], "entity_id": fid,
                "scores_file": "scores.npz", "scores_sha256": sha(score_path),
                "factor": spec.to_dict(), "claimed_direction": report["direction"], "lineage": row["lineage"],
                "registration_file": "registration.json", "registration_sha256": sha(folder / "registration.json")})
            once(path, report)
            return report

    def _recount(self):
        rows = read(self.version_root / "catalog.json")
        reports = read(self.version_root / "summaries.json")
        combos = read(self.version_root / "combination_summaries.json")
        references = [r["factor_id"] for r in read(self.root / "reference_catalog.json")]
        control_reports = {key: value for key, value in combos.items() if _dense46_control(value.get("spec"), references)}
        control_attempts = set(control_reports)
        for path in (self.version_root / "failures").glob("*.json"):
            failed = read(path)
            if failed.get("stage") == "numeric_combination" and _dense46_control(failed.get("spec"), references):
                control_attempts.add(path.stem)
        counted = [r for r in rows if r["factor_id"] in reports and not reports[r["factor_id"]].get("numeric_duplicate_of")]
        single_ids = {r["factor_id"] for r in rows if r["entity_type"] == "single_factor"}
        floors = [annual_floor(r) for key, r in reports.items() if key in single_ids]
        cfloors = [annual_floor(r) for r in combos.values()]
        cfloors += [annual_floor(r) for key, r in reports.items() if key not in single_ids]
        self._state(primary_evaluated=sum(r["origin"] != "control" and r["entity_type"] == "single_factor" for r in counted),
            new_evaluated=sum(r["origin"] == "new" and r["entity_type"] == "single_factor" for r in counted),
            reference_evaluated=sum(r["origin"] == "reference" for r in counted),
            inherited_evaluated=sum(r["origin"] == "inherited" for r in counted),
            controls_evaluated=sum(r["origin"] == "control" for r in counted),
            numeric_duplicates=sum(bool(r.get("numeric_duplicate_of")) for r in reports.values()),
            combinations_evaluated=sum(r.get("status") == "evaluated" and not r.get("numeric_duplicate_of")
                                       and key not in control_reports for key, r in combos.items()),
            combination_controls_evaluated=sum(r.get("status") == "evaluated" and not r.get("numeric_duplicate_of") for r in control_reports.values()),
            combination_controls_attempted=len(control_attempts),
            best_factor_floor=max((v for v in floors if v is not None), default=None),
            best_combination_floor=max((v for v in cfloors if v is not None), default=None))

    def factor_batch(self, limit=50):
        if not self._resource_ready():
            return self.status()
        rows = execution_queue(read(self.version_root / "catalog.json"))
        summaries = read(self.version_root / "summaries.json")
        completed = 0
        self._state(phase="factor_batch", blocker=None)
        for row in rows:
            fid = row["factor_id"]
            if fid in summaries or (self.version_root / "failures" / (fid + ".json")).exists():
                continue
            if completed >= limit or self.stopped() or not self._resource_ready():
                break
            if self.state["numeric_wall_seconds"] >= self.config["budget"]["max_numeric_seconds"]:
                break
            start, cpu = time.perf_counter(), time.process_time()
            try:
                report = self._factor_one(row)
                summaries[fid] = {k: v for k, v in report.items() if k != "daily"}
                write_json(self.version_root / "summaries.json", summaries)
                if row["origin"] != "control" and row["entity_type"] == "single_factor" and not report.get("numeric_duplicate_of"):
                    completed += 1
                self.event("factor_evaluated", factor_id=fid, origin=row["origin"], worst_year_ic=annual_floor(report), duplicate=report.get("numeric_duplicate_of"))
            except Exception as exc:
                folder = self.version_root / "failures"
                folder.mkdir(exist_ok=True)
                once(folder / (fid + ".json"), {"registration": row, "error": repr(exc), "stage": "numeric_factor"})
                self._state(failures=self.state["failures"] + 1)
                self.event("factor_failed", factor_id=fid, error=repr(exc))
            finally:
                self._state(numeric_wall_seconds=self.state["numeric_wall_seconds"] + time.perf_counter() - start,
                            numeric_cpu_seconds=self.state["numeric_cpu_seconds"] + time.process_time() - cpu)
                self._recount()
            if self.state["primary_evaluated"] >= self.config["budget"]["max_factors"]:
                break
        self._state(phase="batch_evaluated" if self.state["phase"] != "waiting" else "waiting")
        return self.status()

    def calibrate(self):
        """Small real panel cache/prefix comparison precedes any large batch."""
        if not self._resource_ready():
            return self.status()
        import threading
        import psutil
        from .resources import ResourceGuard, CostLedger
        from quanta_agents.factor_research.adapters import factor_panel
        from quanta_agents.factor_research.evaluation import FactorEvaluator
        start, cpu = time.perf_counter(), time.process_time()
        snapshots = []
        done = threading.Event()
        def sample():
            while not done.wait(.1):
                proc = psutil.Process()
                memory = proc.memory_info()
                snapshots.append({"private": getattr(memory, "private", memory.rss),
                                  "rss": memory.rss, "available": psutil.virtual_memory().available})
        thread = threading.Thread(target=sample, daemon=True); thread.start()
        checks = []
        try:
            ctx = self.context()
            selected = read(self.root / "reference_catalog.json")[:3]
            prefix = FactorEvaluator(factor_panel(ctx.evaluator.panel, end="2020-12-31"),
                                    train_start="2016-01-01", train_end="2018-12-31")
            for row in selected:
                spec = spec_of(row)
                cached = ctx.compute(spec)
                direct = ctx.evaluator.compute(spec, use_cache=False)
                truncated = prefix.compute(spec)
                np.testing.assert_allclose(cached, direct, rtol=1e-12, atol=1e-12, equal_nan=True)
                np.testing.assert_allclose(cached.loc[truncated.index], truncated, rtol=1e-12, atol=1e-12, equal_nan=True)
                evidence = ctx.evaluate_factor(spec, start="2019-01-01", end="2024-12-31")
                checks.append({"factor_id": spec.factor_id, "cache_parity": True, "prefix_parity": True,
                               "annual": evidence["annual"], "numeric_end": str(ctx.dates[-1].date())})
            result = {"passed": True, "single_process_completed": True, "checks": checks,
                      "wall_seconds": time.perf_counter() - start, "cpu_seconds": time.process_time() - cpu,
                      "worker_peak_private_bytes": max((r["private"] for r in snapshots), default=0),
                      "minimum_observed_available_memory_bytes": min((r["available"] for r in snapshots), default=0),
                      "memory_semantics": "sampled at 100ms; not OS lifetime peak", "numeric_2025_loaded": False}
            result["worker_decision"] = ResourceGuard(self.root).workers_after_calibration(result)
            write_json(self.root / "calibration.json", result)
            CostLedger(self.root).record("V10A_calibration", kind="calibration", wall_seconds=result["wall_seconds"],
                cpu_seconds=result["cpu_seconds"], peak_memory_bytes=result["worker_peak_private_bytes"])
            self.event("calibration_passed", workers=result["worker_decision"]["workers"], wall_seconds=result["wall_seconds"])
            return result
        finally:
            done.set(); thread.join()

    def _signatures(self):
        if self._signature_cache is None:
            self._signature_cache = {p.parent.name: np.load(p, allow_pickle=False) for p in (self.version_root / "factors").glob("*/training_signature.npy")}
        return self._signature_cache

    def _combination_execution(self, specs, completed_reports, declaration_path):
        """Freeze a scheduling-only permutation without replacing old declarations."""
        from .selection import execution_order
        by_id = {spec.combination_id: spec for spec in specs}
        if len(by_id) != len(specs):
            raise ValueError("Combination declarations must contain unique existing IDs")
        path = declaration_path.with_name(f"{self.state['batch']:04d}_combination_execution_v2.json")
        specification_hashes = {key: digest(spec.to_dict()) for key, spec in by_id.items()}
        if path.exists():
            record = read(path)
        else:
            ordered = execution_order(specs, completed_reports)
            ids = [spec.combination_id for spec in ordered]
            if len(ids) != len(specs) or set(ids) != set(by_id):
                raise ValueError("Execution scheduler must return every registered ID exactly once")
            if any(digest(spec.to_dict()) != specification_hashes[spec.combination_id] for spec in ordered):
                raise ValueError("Execution scheduling cannot change registered specification contents")
            failures = sorted(p.stem for p in (self.version_root / "failures").glob("*.json") if p.stem in by_id)
            record = {"version": "combination_execution_v2", "batch": self.state["batch"],
                "declaration_path": str(declaration_path), "declaration_sha256": sha(declaration_path),
                "specification_sha256": specification_hashes, "ordered_combination_ids": ids,
                "historical_completed_ids": sorted(completed_reports),
                "historical_completed_statuses": {key: {"status": report.get("status"), "numeric_duplicate_of": report.get("numeric_duplicate_of")}
                                                    for key, report in sorted(completed_reports.items())},
                "historical_completed_reports_sha256": digest(completed_reports),
                "historical_failure_ids": failures,
                "semantics": "coverage-based execution permutation only; no score selection or specification change; frozen on first use"}
            once(path, record)
        ids = record.get("ordered_combination_ids", [])
        if (record.get("version") != "combination_execution_v2" or record.get("batch") != self.state["batch"] or
            record.get("declaration_sha256") != sha(declaration_path) or record.get("specification_sha256") != specification_hashes or
            len(ids) != len(specs) or set(ids) != set(by_id)):
            raise ValueError("Frozen combination execution order or original declarations changed")
        return [by_id[key] for key in ids]

    def combination_batch(self, limit=40):
        from .combination import CombinationEvaluator, CombinationSpec
        from .resources import CostLedger
        if not self._resource_ready():
            return self.status()
        ctx = self.context()
        rows = read(self.version_root / "catalog.json")
        summaries = read(self.version_root / "summaries.json")
        combined = read(self.version_root / "combination_summaries.json")
        refs = [r["factor_id"] for r in read(self.root / "reference_catalog.json")]
        pool = factor_pool(rows, summaries)
        batch = self.state["batch"]
        declaration_path = self.version_root / "batches" / f"{batch:04d}_combinations.json"
        if declaration_path.exists():
            specs = [CombinationSpec.from_dict(row) for row in read(declaration_path)]
        else:
            specs = combination_specs(pool, summaries, self._signatures(), refs, offset=max(0, batch - 5))
            once(declaration_path, [s.to_dict() for s in specs])
        evaluator = CombinationEvaluator(ctx, cache_dir=self.root / "cache/combination")
        completed = 0
        self._state(phase="combination_batch", blocker=None)
        for spec in self._combination_execution(specs, combined, declaration_path):
            cid = spec.combination_id
            if cid in combined or (self.version_root / "failures" / (cid + ".json")).exists():
                continue
            if completed >= limit or self.stopped() or not self._resource_ready():
                break
            if self.state["combinations_evaluated"] >= self.config["budget"]["max_combinations"] or self.state["numeric_wall_seconds"] >= self.config["budget"]["max_numeric_seconds"]:
                break
            start, cpu = time.perf_counter(), time.process_time()
            folder = self.version_root / "combinations" / cid
            try:
                if (folder / "report.json").exists():
                    report = read(folder / "report.json")
                else:
                    with CostLedger(self.root).measure(self.state["version"] + ":combination:" + cid, kind="numeric_combination"):
                        once(folder / "preregistered_spec.json", spec.to_dict())
                        report, predictions = evaluator.evaluate(spec, start="2019-01-01", end="2024-12-31")
                        from .lineage import value_identity
                        score_identity = value_identity(predictions, sample_mask=ctx.pool)
                        report["score_value_sha256"] = score_identity["values_sha256"]
                        twins = [key for key, value in combined.items() if value.get("score_value_sha256") == score_identity["values_sha256"]]
                        report["numeric_duplicate_of"] = twins[0] if twins else None
                        scores_path = self._save_frame(folder, predictions)
                        once(folder / "package.json", {"entity_type": "combination", "entity_id": cid,
                            "scores_file": "scores.npz", "scores_sha256": sha(scores_path),
                            "registration_file": "preregistered_spec.json", "registration_sha256": sha(folder / "preregistered_spec.json"),
                            "spec": spec.to_dict(), "models_by_interval": report["models_by_interval"],
                            "factors": [r["spec"] for r in rows if r["factor_id"] in spec.candidate_ids or r["factor_id"] in spec.feature_ids],
                            "lineage": {"transformation_kind": "supervised_predictor" if spec.method == "ridge" else "registered_factor_aggregation",
                                        "aggregation_of_registered_factors": list(spec.feature_ids), "supervised": spec.method == "ridge"}})
                        once(folder / "report.json", report)
                combined[cid] = {k: v for k, v in report.items() if k not in ("daily", "models_by_interval")}
                write_json(self.version_root / "combination_summaries.json", combined)
                completed += report.get("status") == "evaluated" and not report.get("numeric_duplicate_of") and not _dense46_control(spec, refs)
                self.event("combination_evaluated", combination_id=cid, members=len(spec.feature_ids), worst_year_ic=annual_floor(report))
            except Exception as exc:
                once(self.version_root / "failures" / (cid + ".json"), {"spec": spec.to_dict(), "error": repr(exc), "stage": "numeric_combination"})
                self._state(failures=self.state["failures"] + 1)
                self.event("combination_failed", combination_id=cid, error=repr(exc))
            finally:
                self._state(numeric_wall_seconds=self.state["numeric_wall_seconds"] + time.perf_counter() - start,
                            numeric_cpu_seconds=self.state["numeric_cpu_seconds"] + time.process_time() - cpu)
                self._recount()
        return self.status()

    def complement_batch(self, maximum=6):
        """Fixed baseline for a whole batch; train-only inner folds retain main effects."""
        from .combination import CombinationSpec, CombinationEvaluator
        from .selection import memberships, complement_candidates
        from .lineage import LineageRegistry
        from .resources import CostLedger
        ctx = self.context()
        rows = read(self.version_root / "catalog.json")
        summaries = read(self.version_root / "summaries.json")
        pool = factor_pool(rows, summaries)
        member_sets = memberships(pool, summaries, self._signatures(), sizes=(4,))
        if not member_sets:
            return 0
        base_ids = member_sets[0]["feature_ids"]
        inner_ids = tuple(r["factor_id"] for r in read(self.root / "reference_catalog.json") if r["spec"]["name"] in ("F1", "F2", "F3", "F7"))
        batch_dir = self.version_root / "batches" / f"{self.state['batch']:04d}_increment"
        declaration_path = batch_dir / "baseline.json"
        if declaration_path.exists():
            declaration = read(declaration_path)
            base_ids, inner_ids = tuple(declaration["base_ids"]), tuple(declaration["inner_baseline_ids"])
            by_id = {r["factor_id"]: r for r in rows}
            candidates = [by_id[fid] for fid in declaration["candidate_ids"]]
        else:
            candidates = complement_candidates(execution_queue(rows), summaries, base_ids, limit=maximum)
            declaration = {"base_ids": base_ids, "inner_baseline_ids": inner_ids, "candidate_ids": [r["factor_id"] for r in candidates],
                "folds": self.config["complementarity"]["folds"], "selection": "training quality/complement/condition round-robin",
                "previously_passed": [r["factor_id"] for r in candidates if summaries[r["factor_id"]].get("complementarity_passed")]}
            once(declaration_path, declaration)
        evaluator = CombinationEvaluator(ctx, cache_dir=self.root / "cache/combination")
        passed = 0
        for row in candidates:
            if self.stopped() or self.state["numeric_wall_seconds"] >= self.config["budget"]["max_numeric_seconds"]:
                break
            path = batch_dir / (row["factor_id"] + ".json")
            if path.exists():
                result = read(path)
            else:
                if not self._resource_ready():
                    break
                effects = set(base_ids)
                control_ids = set()
                for control in row.get("controls", []):
                    formula = control["expression_template"].format(**row.get("parameters", {}))
                    control_spec = FactorSpec(control["name"], formula)
                    ctx.register([control_spec]); effects.add(control_spec.factor_id); control_ids.add(control_spec.factor_id)
                effects.discard(row["factor_id"])
                inner_effects = control_ids | set(inner_ids)
                inner_effects.discard(row["factor_id"])
                base = CombinationSpec("batch_baseline_with_main_effects", tuple(sorted(effects)))
                augmented = CombinationSpec("batch_baseline_plus_candidate", tuple(sorted(effects | {row["factor_id"]})))
                inner_base = CombinationSpec("prespecified_inner_baseline_with_main_effects", tuple(sorted(inner_effects)))
                inner_augmented = CombinationSpec("prespecified_inner_baseline_plus_candidate", tuple(sorted(inner_effects | {row["factor_id"]})))
                begin, cpu = time.perf_counter(), time.process_time()
                try:
                    with CostLedger(self.root).measure(f"{self.state['version']}:increment:{self.state['batch']}:{row['factor_id']}", kind="numeric_increment"):
                        folds = [evaluator.paired_increment(inner_base, inner_augmented, start=f[2], end=f[3], fit_start=f[0], fit_end=f[1])
                                 for f in self.config["complementarity"]["folds"]]
                        exposed = evaluator.paired_increment(base, augmented, start="2019-01-01", end="2024-12-31")
                    deltas = [r.get("paired_delta_pearson_ic") for r in folds]
                    sufficient = all(d is not None for d in deltas) and all(r.get("status") == "evaluated" and r.get("valid_days", 0) >= 60 for r in folds)
                    result = {"factor_id": row["factor_id"], "base_ids": list(effects), "folds": folds,
                              "inner_baseline_ids": list(inner_effects), "exposed_history_increment": exposed,
                              "passed": bool(sufficient and min(deltas) >= 0 and np.mean(deltas) >= .001),
                              "mean_delta": float(np.mean(deltas)) if sufficient else None}
                except Exception as exc:
                    result = {"factor_id": row["factor_id"], "passed": False, "mean_delta": None, "error": repr(exc)}
                finally:
                    self._state(numeric_wall_seconds=self.state["numeric_wall_seconds"] + time.perf_counter() - begin,
                                numeric_cpu_seconds=self.state["numeric_cpu_seconds"] + time.process_time() - cpu)
                once(path, result)
            summary = summaries[row["factor_id"]]
            was = row["factor_id"] in declaration["previously_passed"]
            summary.update(complementarity_checked=True, complementarity_passed=result["passed"],
                           complementarity_delta=result["mean_delta"], complementarity_evidence=str(path))
            passed += bool(result["passed"] and not was)
            write_json(self.version_root / "summaries.json", summaries)
        return passed

    def _review_complement(self, row, report):
        """Read the actual paired experiment, not the factor's unrelated IC mask."""
        reference = report.get("complementarity_evidence")
        result = {"factor_id": row["factor_id"], "checked": bool(report.get("complementarity_checked")),
                  "reported_passed": report.get("complementarity_passed"), "reported_delta": report.get("complementarity_delta"),
                  "recorded": False, "source": None, "folds": [], "common_sample_ids": []}
        if not reference:
            return result
        path = Path(reference).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            result["unavailable_reason"] = "paired evidence source missing or outside campaign"
            return result
        paired = read(path)
        def compact(value):
            fields = ("status", "valid_days", "common_cells", "sample_mask_id", "baseline_id", "augmented_id",
                      "target", "evaluation_target", "paired_delta_pearson_ic", "baseline_original_pearson_ic",
                      "baseline_common_pearson_ic", "augmented_pearson_ic", "coverage_selection_delta_pearson_ic",
                      "common_refit_delta_pearson_ic", "semantics")
            return {**{key: value.get(key) for key in fields if key in value},
                    "model_availability": {key: _fit_availability(models) for key, models in value.get("models", {}).items()}}
        result.update(recorded=True, source={"path": str(path), "sha256": sha(path)},
                      passed=paired.get("passed"), mean_delta=paired.get("mean_delta"), error=paired.get("error"),
                      base_ids=paired.get("base_ids", []), inner_baseline_ids=paired.get("inner_baseline_ids", []),
                      folds=[compact(fold) for fold in paired.get("folds", [])],
                      exposed_history_increment=compact(paired.get("exposed_history_increment", {})))
        result["common_sample_ids"] = [fold["sample_mask_id"] for fold in paired.get("folds", []) if fold.get("sample_mask_id")]
        if result["common_sample_ids"]:
            result["common_sample_bundle_sha256"] = digest(result["common_sample_ids"])
        return result

    def _review_combinations(self, reports, known, references, leaders):
        """All declared/attempted coverage and unavailable controls stay visible."""
        from .combination import CombinationSpec
        declarations, failures, declaration_sources = {}, {}, []
        for path in sorted((self.version_root / "batches").glob("*_combinations.json")):
            declaration_sources.append({"path": str(path), "sha256": sha(path)})
            for spec in read(path):
                cid = spec.get("combination_id") or CombinationSpec.from_dict(spec).combination_id
                declarations.setdefault(cid, spec)
        for cid, report in reports.items():
            if report.get("spec"):
                declarations.setdefault(cid, report["spec"])
        for path in sorted((self.version_root / "failures").glob("*.json")):
            failed = read(path)
            if failed.get("stage") == "numeric_combination":
                failures[path.stem] = {**failed, "source": {"path": str(path), "sha256": sha(path)}}
                if failed.get("spec"):
                    declarations.setdefault(path.stem, failed["spec"])
        dimension_groups, member_groups = {}, {}
        outcome_counts = Counter()
        incomplete = []
        for cid in sorted(set(declarations) | set(reports) | set(failures)):
            spec, report = declarations.get(cid, {}), reports.get(cid, {})
            members = tuple(sorted(spec.get("feature_ids", [])))
            control = _dense46_control(spec, references)
            if cid in reports:
                outcome = "duplicate" if report.get("numeric_duplicate_of") else "successful" if report.get("status") == "evaluated" else "fit_unavailable"
            else:
                outcome = "implementation_failed" if cid in failures else "pending"
            outcome_counts[outcome] += 1
            dimensions = {"size": len(members), "method": spec.get("method"), "target": spec.get("target"),
                          "update": spec.get("update_rule"), "ridge_lambda": spec.get("ridge_lambda") if spec.get("method") == "ridge" else None,
                          "control": control}
            key = digest(dimensions)
            group = dimension_groups.setdefault(key, {**dimensions, "counts": Counter(), "declared_members": set(), "attempted_members": set(), "successful_members": set()})
            member_id = digest(members)
            group["counts"][outcome] += 1
            group["declared_members"].add(member_id)
            if outcome != "pending":
                group["attempted_members"].add(member_id)
            if outcome == "successful":
                group["successful_members"].add(member_id)
            member_group = member_groups.setdefault(member_id, {"member_set_id": member_id, "feature_ids": list(members), "size": len(members), "control": control, "counts": Counter()})
            member_group["counts"][outcome] += 1
            if outcome in {"fit_unavailable", "implementation_failed", "duplicate"}:
                incomplete.append({"combination_id": cid, "dimensions": dimensions, "status": report.get("status", outcome),
                    "numeric_duplicate_of": report.get("numeric_duplicate_of"), "failure": failures.get(cid),
                    "spec": spec, "member_set_id": member_id})
        coverage = {"declaration_sources": declaration_sources, "outcomes": dict(outcome_counts),
            "semantics": "counts are complete unique registered specs, not years or weight updates; unavailable and controls remain separate",
            "equal_direction_regularization": "not applicable; stored target/lambda cannot create another numerical model",
            "dimensions": [{**{key: value for key, value in group.items() if key not in {"counts", "declared_members", "attempted_members", "successful_members"}},
                **dict(group["counts"]), "declared": sum(group["counts"].values()),
                "distinct_member_sets_declared": len(group["declared_members"]), "distinct_member_sets_attempted": len(group["attempted_members"]),
                "distinct_member_sets_successful": len(group["successful_members"])} for group in dimension_groups.values()],
            "member_sets": [{**group, "counts": dict(group["counts"])} for group in member_groups.values()]}
        coverage["distinct_member_sets"] = len(member_groups)
        coverage["ordinary_successful"] = sum(group["counts"]["successful"] for group in dimension_groups.values() if not group["control"])
        coverage["control_successful"] = sum(group["counts"]["successful"] for group in dimension_groups.values() if group["control"])
        needed = set()
        def evidence(cid, *, folder=None, report=None, scope="current_version"):
            report = reports.get(cid, {}) if report is None else report
            folder = self.version_root / "combinations" / cid if folder is None else folder
            path = folder / "report.json"
            saved = read(path) if path.is_file() else report
            spec = saved.get("spec") or declarations.get(cid, {})
            members = list(spec.get("feature_ids", []))
            needed.update(members)
            return {"combination_id": cid, "spec": spec, "member_definition_refs": members,
                    "missing_member_definitions": [fid for fid in members if fid not in known],
                      "status": saved.get("status", "implementation_failed" if cid in failures else "not_attempted"),
                      "failure": failures.get(cid), "numeric_duplicate_of": saved.get("numeric_duplicate_of"),
                    "fit_availability": _fit_availability(saved.get("models_by_interval")),
                    "source": {"path": str(path), "sha256": sha(path)} if path.is_file() else None,
                    "scope": scope, "control": _dense46_control(spec, references)}
        leading = {cid: evidence(cid) for cid in leaders}
        for item in incomplete:
            actual = evidence(item["combination_id"])
            item.update(fit_availability=actual["fit_availability"], source=actual["source"])
        control_ids = [cid for cid, spec in declarations.items() if _dense46_control(spec, references)]
        pending_controls = [cid for cid in control_ids if cid not in reports and cid not in failures]
        # Full failed/attempted controls remain visible. One declared example plus
        # all pending IDs and the complete dimensional counts avoids repeating
        # the same 46 definitions for every unattempted lambda/window variant.
        controls = [evidence(cid) for cid in control_ids if cid not in pending_controls]
        if pending_controls:
            controls.append({**evidence(pending_controls[0]), "unattempted_control_ids": pending_controls,
                             "unattempted_control_count": len(pending_controls)})
        # A calibration failure is evidence, but never formal completed coverage.
        for summary_path in sorted((self.root / "verification").glob("combination_calibration_*/summary.json")):
            summary = read(summary_path)
            for item in summary.get("results", []):
                if item.get("member_count") == 46:
                    diagnostic = evidence(item["combination_id"], folder=summary_path.parent / item["combination_id"], report=item, scope="calibration_not_study_counted")
                    diagnostic["study_counted"] = False
                    controls.append(diagnostic)
        return {"leading": leading, "coverage": coverage, "unavailable_or_duplicate": incomplete,
                "controls": controls, "definition_ids": sorted(needed)}

    def _review_previous_versions(self):
        snapshots = []
        for path in (self.root / "versions").glob("*/framework_close_snapshot.json"):
            if path.parent == self.version_root:
                continue
            snapshot = read(path)
            state = snapshot.get("state", {})
            snapshots.append((state.get("updated_utc", ""), path.stat().st_mtime_ns, path, snapshot))
        previous = []
        count_keys = ("primary_evaluated", "new_evaluated", "reference_evaluated", "inherited_evaluated",
                      "controls_evaluated", "combinations_evaluated", "combination_controls_evaluated",
                      "combination_controls_attempted", "numeric_duplicates", "canonical_duplicates", "failures")
        for _, _, path, snapshot in sorted(snapshots, key=lambda item: item[:2], reverse=True)[:2]:
            state = snapshot.get("state", {})
            previous.append({"version": state.get("version", path.parent.name),
                "source": {"path": str(path), "sha256": sha(path)},
                "scale_completed": snapshot.get("scale_completed"), "version_scale_status": state.get("version_scale_status"),
                "counts": {key: state.get(key) for key in count_keys},
                "best_factor_floor": state.get("best_factor_floor"), "best_combination_floor": state.get("best_combination_floor"),
                "numeric_wall_seconds": state.get("numeric_wall_seconds"), "numeric_cpu_seconds": state.get("numeric_cpu_seconds"),
                "cumulative_cost_at_close": snapshot.get("cost"),
                "review_source": {"path": snapshot.get("review_path"), "sha256": snapshot.get("review_sha256")},
                "summaries_sha256": snapshot.get("summaries_sha256"),
                "combination_summaries_sha256": snapshot.get("combination_summaries_sha256")})
        return {"status": "available" if previous else "unavailable", "previous_versions": previous,
            "cost_semantics": "Close-snapshot ledger cost is cumulative across versions; numeric wall/CPU in state is per version. Unknown values stay unknown; do not call cumulative cost a per-version cost.",
            "comparison_limit": "Only supplied frozen-protocol snapshots support direct comparisons. V9A used a different study scope and is not a matched baseline." if previous else
                                "No prior framework_close_snapshot exists under this frozen study protocol. V9A is a different study scope; no same-protocol efficiency improvement is established."}

    def _review_definitions(self, known, requested):
        """Resolve recursive ancestry once without copying nested legacy metadata."""
        known = dict(known)
        aliases = {row["spec"].get("name"): fid for fid, row in known.items()}
        definitions, pending = {}, set(requested)
        catalogue_source = {"path": str(self.version_root / "catalog.json"), "sha256": sha(self.version_root / "catalog.json")}
        lineage_keys = ("transformation_kind", "supervised", "aggregation_of_registered_factors", "aggregate_members",
                        "matched_registered_ids", "classification_reason", "acceptance_route", "transformation", "revision_kind", "repair_of")
        while pending:
            fid = min(pending)
            pending.remove(fid)
            if fid in definitions or fid not in known:
                continue
            row, spec = known[fid], known[fid]["spec"]
            metadata = spec.get("metadata", {})
            original_lineage = row.get("lineage") or {}
            lineage = {key: original_lineage[key] for key in lineage_keys if key in original_lineage}
            parent_refs = []
            for index, parent in enumerate(original_lineage.get("parent_definitions", [])):
                parent_spec = parent.get("spec", parent)
                parent_id = spec_of(parent_spec).factor_id
                if parent_id not in known:
                    parent_metadata = parent_spec.get("metadata", {})
                    known[parent_id] = {"factor_id": parent_id, "spec": parent_spec,
                        "roles": parent.get("roles", parent_metadata.get("roles", [])),
                        "parents": parent_spec.get("parents", []), "origin": "registered_lineage_parent",
                        "source": parent.get("source") or parent_metadata.get("source") or parent_metadata.get("source_inventory"),
                        "lineage": parent.get("lineage", parent_metadata.get("lineage", {})),
                        "entity_type": parent.get("entity_type", parent_metadata.get("entity_type")),
                        "registration_locator": {"owner_definition_ref": fid, "field": f"lineage.parent_definitions[{index}]"}}
                    aliases[parent_spec.get("name")] = parent_id
                parent_refs.append(parent_id)
            unresolved = []
            for parent_id in row.get("parents", spec.get("parents", [])):
                resolved = parent_id if parent_id in known else aliases.get(parent_id)
                if resolved:
                    parent_refs.append(resolved)
                else:
                    unresolved.append(parent_id)
            lineage["parent_definition_refs"] = sorted(set(parent_refs))
            if unresolved:
                lineage["unresolved_parent_ids"] = sorted(set(unresolved))
            lineage["full_registered_lineage_sha256"] = digest(original_lineage)
            pending.update(parent_refs)
            definitions[fid] = {"factor_id": fid, "name": spec.get("name"),
                "spec": {key: spec[key] for key in ("name", "expression", "version", "parents", "language") if key in spec},
                "full_registered_spec_sha256": digest(spec), "roles": row.get("roles", []), "route": row.get("route"),
                "parents": row.get("parents", spec.get("parents", [])), "mechanism": row.get("mechanism"),
                "entity_type": row.get("entity_type"), "lineage": lineage, "origin": row.get("origin"),
                "source": _source_reference(row.get("source") or metadata.get("source_inventory") or metadata.get("source")),
                "source_id": metadata.get("source_id"), "catalogue_source": catalogue_source}
            if row.get("registration_locator"):
                definitions[fid]["registration_locator"] = row["registration_locator"]
        return definitions

    def review(self):
        from .lineage import select_parents
        reports = read(self.version_root / "summaries.json")
        combos = read(self.version_root / "combination_summaries.json")
        rows = read(self.version_root / "catalog.json")
        reference_rows = read(self.root / "reference_catalog.json")
        known = {r["factor_id"]: r for r in reference_rows + rows}
        ranked = sorted(reports, key=lambda key: annual_floor(reports[key]) if annual_floor(reports[key]) is not None else -2, reverse=True)
        cranked = sorted(combos, key=lambda key: annual_floor(combos[key]) if annual_floor(combos[key]) is not None else -2, reverse=True)
        increments = {r["factor_id"]: self._review_complement(r, reports[r["factor_id"]]) for r in rows
                      if r["factor_id"] in reports and reports[r["factor_id"]].get("complementarity_checked")}
        parent_rows = [{**r, "evidence_id": r["factor_id"], "evidence": {
            "years": list(range(2019, 2025)), "worst_ic": annual_floor(reports[r["factor_id"]]),
            "incremental": {"passed": reports[r["factor_id"]].get("complementarity_passed"),
                            "delta_ic": reports[r["factor_id"]].get("complementarity_delta"),
                            "common_sample_sha256": increments.get(r["factor_id"], {}).get("common_sample_bundle_sha256")}}}
            for r in rows if r["factor_id"] in reports]
        parents = select_parents(parent_rows, limit=12)
        def compact(report):
            return {"annual": [{k: r.get(k) for k in ("year", "mean_pearson_ic", "valid_days", "evaluation_coverage")} for r in report.get("annual", [])],
                    "direction_fit": report.get("direction_fit"), "summary": {k: report.get("summary", {}).get(k) for k in ("mean_pearson_ic", "mean_rank_ic", "mean_top40_relative_pool")},
                    "numeric_duplicate_of": report.get("numeric_duplicate_of"),
                    "complementarity_passed": report.get("complementarity_passed"), "complementarity_delta": report.get("complementarity_delta")}
        combination_evidence = self._review_combinations(combos, known, [r["factor_id"] for r in reference_rows], cranked[:8])
        definition_ids = set(ranked[:10]) | set(combination_evidence["definition_ids"]) | {parent["parent_id"] for parent in parents}
        for increment in increments.values():
            definition_ids.update(increment.get("base_ids", []))
            definition_ids.update(increment.get("inner_baseline_ids", []))
        definitions = self._review_definitions(known, definition_ids)
        leading_factors = {fid: {**compact(reports[fid]), **definitions.get(fid, {"factor_id": fid, "definition_missing": True})} for fid in ranked[:10]}
        leading_combinations = {cid: {**compact(combos[cid]), **combination_evidence["leading"][cid]} for cid in cranked[:8]}
        coverage_id = "combination_coverage:" + digest(combination_evidence["coverage"])
        context = {"state": self.status(), "program_next_action": version_decision(self.state, self.config["budget"]),
            "leading_factors": leading_factors, "leading_combinations": leading_combinations,
            "factor_definitions": definitions,
            "definition_contract": "Every member_definition_refs, selected_parents.parent_id and lineage.parent_definition_refs resolves in the shared factor_definitions unless explicitly listed as missing. Original parent labels without a resolvable definition are identified separately. Source/spec/lineage hashes and registration locators retain complete original records; nested legacy metadata is not copied into the review.",
            "missing_factor_definitions": sorted(definition_ids - set(definitions)),
            "combination_coverage": {"evidence_id": coverage_id, **combination_evidence["coverage"]},
            "unavailable_or_duplicate_combinations": combination_evidence["unavailable_or_duplicate"],
            "combination_controls": combination_evidence["controls"], "complementarity_evidence": increments,
            "selected_parents": parents,
            "cross_version_evidence": self._review_previous_versions(),
            "mechanism_counts": {route: sum(r.get("route") == route and r["factor_id"] in reports for r in rows) for route in sorted({r.get("route", "unknown") for r in rows})},
            "failures": [read(p) for p in list((self.version_root / "failures").glob("*.json"))[:5]],
            "evidence_ids": sorted(set(ranked[:10]) | set(cranked[:8]) | set(increments) | {coverage_id} |
                                   {item["combination_id"] for item in combination_evidence["controls"] + combination_evidence["unavailable_or_duplicate"]}),
            "request": "Answer every review question using actual IDs and formulas/member definitions. Distinguish planned coverage from actually attempted size/method/target/update/lambda and distinct member sets. Discuss missing comparisons, unavailable dense46 controls, failed fits and duplicates explicitly. Calibration is not formal study count. Inspect actual paired increment folds/common sample IDs and fit availability, not marginal factor masks. Propose a falsifiable change only justified by the supplied evidence. Never call incomplete scale a completed study."}
        action = f"{self.state['version']}_batch{self.state['batch']:04d}_review"
        decision = self._model(action, "review", context)
        if decision is not None:
            write_json(self.version_root / "batches" / f"{self.state['batch']:04d}_parents.json", parents)
            self._state(phase="reviewed", blocker=None)
            self.event("batch_reviewed", batch=self.state["batch"], next_action=decision["next_action"])
        return self.status()

    def _audit(self, folder):
        """Every framework version invokes the ORIGINAL frozen acceptance process."""
        import subprocess, sys, os
        script = self.root / "workspaces/V10A/scripts/audit_factor_campaign_v10.py"
        command = [sys.executable, str(script), "audit", "--authority", str(self.root / "acceptance"), "--package", str(folder)]
        child_env = dict(os.environ, PYTHONIOENCODING="utf-8")
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env)
        write_json(folder / "audit_process.json", {"args": command, "returncode": completed.returncode,
                                                   "stdout": completed.stdout, "stderr": completed.stderr})
        if completed.returncode:
            raise RuntimeError("Independent acceptance process failed: " + (completed.stderr or completed.stdout)[-1500:])
        result = read(folder / "independent_acceptance.json")
        from .resources import CostLedger
        CostLedger(self.root).record("acceptance:" + folder.name + ":" + str(time.time_ns()), kind="independent_acceptance",
                                    wall_seconds=result["cost"]["wall_seconds"], cpu_seconds=result["cost"]["cpu_seconds"])
        self._state(numeric_wall_seconds=self.state["numeric_wall_seconds"] + result["cost"]["wall_seconds"],
                    numeric_cpu_seconds=self.state["numeric_cpu_seconds"] + result["cost"]["cpu_seconds"])
        return result

    def verify_contenders(self, *, leaders=False):
        if not (self.root / "acceptance/authority.json").exists():
            raise RuntimeError("Independent acceptance authority has not been frozen")
        reports = read(self.version_root / "summaries.json")
        kinds = {r["factor_id"]: r["entity_type"] for r in read(self.version_root / "catalog.json")}
        combinations = read(self.version_root / "combination_summaries.json")
        contenders = [(self.version_root / "factors" / k, r) for k, r in reports.items() if descriptive_pass(r, kinds[k])]
        contenders += [(self.version_root / "combinations" / k, r) for k, r in combinations.items() if descriptive_pass(r, "combination")]
        if leaders:
            for name, data in (("factors", reports), ("combinations", combinations)):
                if data:
                    key = max(data, key=lambda k: annual_floor(data[k]) if annual_floor(data[k]) is not None else -2)
                    contenders.append((self.version_root / name / key, data[key]))
        # Release retained panels before independent process loading.
        self._context = None; gc.collect()
        seen = set()
        for folder, report in contenders:
            if folder in seen:
                continue
            seen.add(folder)
            result = self._audit(folder)
            if result["verdict"]["accepted"] and result.get("source_verified") and result.get("replay_verified"):
                self._state(historical_target_met=True, phase="historical_target_accepted", winner=str(folder), blocker=None)
                self.event("historical_target_accepted", winner=str(folder))
                return True
        return False

    def resume(self):
        (self.root / "stop.request").unlink(missing_ok=True)
        self._state(stop_requested=False, blocker=None)
        return self.run()

    def advance(self):
        from .framework_host import advance_campaign
        self._context = None; gc.collect()
        return advance_campaign(self)

    def repair_failures(self):
        from .lineage import build_revision
        rows = read(self.version_root / "catalog.json")
        known = {r["factor_id"]: r for r in rows}
        for path in sorted((self.version_root / "failures").glob("*.json")):
            failure = read(path)
            if failure.get("stage") != "numeric_factor":
                continue
            fid = failure["registration"]["factor_id"]
            if any(r["spec"].get("metadata", {}).get("repair_of") == fid for r in rows):
                continue
            original = known[fid]
            action = self.state["version"] + "_repair_" + fid[:24]
            response = self._model(action, "repair", {"failure": failure, "evidence_ids": [str(path)],
                "request": "Repair the implementation failure only. Retain information fields and mechanism; parent_id must equal the failed factor ID. Explain if it is an information-support failure that cannot be repaired by formula changes."})
            if response is None:
                return False
            if response["parent_id"] != fid:
                once(path.with_suffix(".repair_rejected.json"), {"reason": "repair_parent_mismatch", "response": response})
                continue
            try:
                spec = build_revision(spec_of(original), response["expression"], reason=response["reason"],
                                      repair_number=1, evidence_ids=response["evidence_ids"])
                revised = {**deepcopy(original), "factor_id": spec.factor_id, "spec": spec.to_dict(),
                           "parents": [fid], "origin": "new", "source": {"repair_action": action, "failed_artifact": str(path)}}
                rows, _ = merge_catalog(rows, [revised])
                write_json(self.version_root / "catalog.json", self._bind_lineage(rows))
                self.event("implementation_repair_admitted", original=fid, repaired=spec.factor_id)
            except ValueError as exc:
                once(path.with_suffix(".repair_rejected.json"), {"reason": str(exc), "response": response})
        return True

    def repair_controls(self):
        """One real repair decision per failed control template, then range expansion."""
        from .candidates import validate_control_repair
        initial = read(self.version_root / "initial_expansion.json")
        groups = {}
        for attempt in initial["model_attempts"]:
            if attempt["status"] == "implementation_rejected" and attempt.get("control"):
                groups.setdefault((attempt["family_id"], attempt["control_name"]), []).append(attempt)
        for (family_id, control_name), failures in groups.items():
            action = self.state["version"] + "_control_repair_" + digest([family_id, control_name])[:16]
            applied = self.version_root / "control_repairs" / (action + ".json")
            if applied.exists():
                continue
            family = deepcopy(failures[0]["family"])
            broken = next(c for c in family["controls"] if c["name"] == control_name)
            response = self._model(action, "repair", {"parent_id": family_id,
                "broken_control": broken, "parameters": family["parameters"],
                "primary_template": family["expression_template"], "error": failures[0]["error"],
                "evidence_ids": ["initial_expansion:" + family_id + ":" + control_name],
                "request": "Repair only this control template's syntax, preserving exactly its economic meaning, operands and named {parameter} placeholders. Return corrected TEMPLATE in expression and parent_id equal to supplied family ID. Do not change main formula or windows. Program expands every window and retains original failed attempts."})
            if response is None:
                return False
            if response["parent_id"] != family_id:
                self._state(phase="waiting", blocker={"kind": "repair_identity_mismatch", "action": action})
                return False
            original_template = broken["expression_template"]
            try:
                validate_control_repair(original_template, response["expression"])
            except ValueError as exc:
                once(applied.with_name(action + ".rejected.json"), {"response": response, "reason": str(exc)})
                self._state(phase="waiting", blocker={"kind": "control_repair_changed_meaning", "action": action})
                return False
            broken["expression_template"] = response["expression"]
            target_family = {**family, "controls": [broken]}
            repaired_rows, attempts = expand([target_family], origin={"kind": "actual_control_repair", "action_id": action})
            bad = [a for a in attempts if a["status"] == "implementation_rejected"]
            if bad:
                once(applied.with_name(action + ".rejected.json"), {"response": response, "attempts": attempts})
                self._state(phase="waiting", blocker={"kind": "control_repair_still_invalid", "action": action})
                return False
            catalog = read(self.version_root / "catalog.json")
            for row in catalog:
                if row["family_id"] == family_id:
                    for control in row.get("controls", []):
                        if control["name"] == control_name:
                            control["expression_template"] = response["expression"]
                    row["control_repair"] = {"action_id": action, "original_template": original_template,
                                             "corrected_template": response["expression"]}
            controls = [r for r in repaired_rows if r["origin"] == "control"]
            catalog, duplicates = merge_catalog(catalog, controls)
            write_json(self.version_root / "catalog.json", self._bind_lineage(catalog))
            once(applied, {"action_id": action, "original_template": original_template, "response": response,
                           "attempts": attempts, "canonical_duplicates": duplicates, "initial_failures_preserved": len(failures)})
            self.event("control_template_repaired", family_id=family_id, retained_failed_attempts=len(failures))
        return True

    def run(self):
        with exclusive_lock(self.root / "campaign.lock"):
            if self.state["historical_target_met"]:
                return self.status()
            if self.state.get("version_scale_status", "").startswith("review_"):
                return self.advance()
            if not (self.version_root / "initial_expansion.json").exists():
                self.prepare()
                if self.state["phase"] == "waiting":
                    return self.status()
            if not self.repair_controls():
                return self.status()
            if not (self.root / "calibration.json").exists():
                self.calibrate()
            while not self.stopped():
                if not self._resource_ready():
                    return self.status()
                before = self.state
                batch = before["batch"]
                batch_file = self.version_root / "batches" / f"{batch:04d}_start.json"
                if batch_file.exists():
                    before = read(batch_file)
                else:
                    once(batch_file, before)
                budget = self.config["budget"]
                factor_target = min(budget["minimum_factors"], before["primary_evaluated"] + 50) if before["primary_evaluated"] < budget["minimum_factors"] else before["primary_evaluated"] + budget["factor_extension"]
                self.factor_batch(max(0, factor_target - self.state["primary_evaluated"]))
                if self.stopped():
                    self._state(phase="stopped"); return self.status()
                if self.state["phase"] == "waiting":
                    return self.status()
                complements = self.complement_batch()
                combo_target = min(budget["minimum_combinations"], before["combinations_evaluated"] + 40) if before["combinations_evaluated"] < budget["minimum_combinations"] else before["combinations_evaluated"] + budget["combination_extension"]
                self.combination_batch(max(0, combo_target - self.state["combinations_evaluated"]))
                if self.stopped():
                    self._state(phase="stopped"); return self.status()
                if self.state["phase"] == "waiting":
                    return self.status()
                if self.verify_contenders():
                    return self.status()
                complete_before = before["primary_evaluated"] >= budget["minimum_factors"] and before["combinations_evaluated"] >= budget["minimum_combinations"]
                gains = [self.state[k] - before[k] for k in ("best_factor_floor", "best_combination_floor") if self.state[k] is not None and before[k] is not None]
                stagnant = complete_before and gains and max(gains) < .001 and complements == 0
                self._state(plateau_extensions=before["plateau_extensions"] + 1 if stagnant else 0)
                if not self.repair_failures():
                    return self.status()
                self.review()
                from .report import render_campaign
                render_campaign(self)
                if self.state["phase"] == "waiting":
                    return self.status()
                decision = version_decision(self.state, budget)
                if decision.startswith("review_"):
                    self.verify_contenders(leaders=True)
                    self._state(phase="version_review_complete", version_scale_status=decision)
                    return self.advance()
                if self.state["primary_evaluated"] == before["primary_evaluated"] and self.state["combinations_evaluated"] == before["combinations_evaluated"]:
                    # A depleted or failing queue needs structural diagnosis, not a busy infinite loop.
                    self._state(phase="version_review_complete", version_scale_status="review_incomplete_scale", blocker={"kind": "candidate_queue_exhausted"})
                    return self.advance()
                self._state(batch=batch + 1, phase="ready_next_batch")
            self._state(phase="stopped")
            return self.status()
