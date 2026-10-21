"""Independent, resumable factor study with frozen confirmation access."""
from __future__ import annotations

from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import shutil
import time

from quanta_agents.meta_v6.factors import FactorSpec
from quanta_agents.meta_v7.ledger import ProjectLedger
from quanta_agents.research_kernel.store import clean, digest, exclusive_lock, write_json
from .protocol import VERSION, ARMS, validate_protocol


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def once(path, value):
    path = Path(path)
    value = clean(value)
    if path.exists():
        if read(path) != value:
            raise ValueError("immutable evidence differs: " + str(path))
    else:
        write_json(path, value)
    return value


def factor_spec(row):
    raw = row.get("spec", row)
    return FactorSpec(**{k: raw[k] for k in ("name", "expression", "version", "parents", "metadata") if k in raw})


def source_paths():
    package = Path(__file__).resolve().parents[1]
    return sorted({*Path(__file__).parent.glob("*.py"), package / "__init__.py",
                   *[package / "meta_v6" / n for n in ("data.py", "factors.py")],
                   *[package / "meta_v7" / n for n in ("combination.py", "ledger.py", "temporal.py")],
                   *[package / "research_kernel" / n for n in ("universe.py", "compiler.py", "store.py", "assets.py")],
                   package / "meta" / "factor_algebra.py"})


class FactorStudy:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @property
    def config(self):
        cfg = validate_protocol(read(self.root / "protocol.json"))
        pin = self.root / "protocol_identity.json"
        if pin.exists() and read(pin)["protocol_sha256"] != sha(self.root / "protocol.json"):
            raise ValueError("frozen protocol content changed")
        return cfg

    @property
    def project(self):
        return ProjectLedger(Path(self.config["project_root"]) / "ledger")

    @property
    def state(self):
        return read(self.root / "state.json")

    def _state(self, **changes):
        write_json(self.root / "state.json", {**self.state, **changes})

    @property
    def expansion_path(self):
        return self.root / ("revision_expansion.json" if self.state.get("revision_admitted") else "expansion.json")

    @property
    def expansion(self):
        return read(self.expansion_path)

    def event(self, kind, **details):
        row = clean({"utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **details})
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        print(json.dumps(row, ensure_ascii=True, allow_nan=False), flush=True)

    def initialize(self, protocol, families, baseline_specs, *, origin):
        from .generation import expand_proposals
        cfg = validate_protocol(protocol)
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.root / "study.lock"):
            if (self.root / "state.json").exists():
                if self.config != cfg:
                    raise ValueError("study already exists with another protocol")
                return self.status()
            expansion = expand_proposals(families, max_candidates=cfg["budget"]["max_unique_candidates"],
                                         max_attempts=cfg["budget"]["max_expansion_attempts"], phase="development")
            if expansion.summary["primary_attempts_by_arm"] != {arm: cfg["budget"]["main_attempts_per_arm"] for arm in ARMS}:
                raise ValueError("initial comparison requires exactly the frozen equal-arm attempt budget")
            once(self.root / "protocol.json", cfg)
            once(self.root / "protocol_identity.json", {"protocol_sha256": sha(self.root / "protocol.json")})
            once(self.root / "proposals.json", {"families": [f.to_dict() for f in families], "origin": origin})
            once(self.root / "baseline.json", [s.to_dict() for s in baseline_specs])
            once(self.root / "expansion.json", {"candidates": [c.to_dict() for c in expansion.candidates],
                                                "attempts": list(expansion.attempts), "summary": expansion.summary})
            pins = {str(p): sha(p) for p in source_paths()}
            once(self.root / "source_pins.json", pins)
            for path in pins:
                p = Path(path)
                relative = p.relative_to(Path(__file__).resolve().parents[2])
                destination = self.root / "source_snapshot" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, destination)
            for path in (cfg["data"]["calendar_path"], cfg["data"]["membership_path"]):
                once(self.root / (Path(path).name + ".source.json"), {"path": path, "sha256": sha(path)})
            from quanta_agents.meta_v6.data import historical_universe
            once(self.root / "symbols.json", historical_universe(cfg["data"]["membership_path"],
                 start=cfg["data"]["start"], end=cfg["confirmation_end"]))
            from quanta_agents.meta_v6.data import inspect_parquet_sources
            once(self.root / "source_inventory.json", inspect_parquet_sources(cfg["data"]["data_root"], read(self.root / "symbols.json")))
            source_manifest = Path(cfg["data"]["data_root"]) / "manifest.json"
            once(self.root / "data_source_manifest.json", {"path": str(source_manifest),
                 "sha256": sha(source_manifest) if source_manifest.exists() else None})
            once(self.root / "immutable_inputs.json", {name: sha(self.root / name) for name in (
                "proposals.json", "baseline.json", "expansion.json", "symbols.json", "source_inventory.json",
                "data_source_manifest.json", "source_pins.json")})
            self.project.register_study(cfg["study_id"], cfg)
            self.project.record_exposure(cfg["study_id"], start="2015-01-01", end="2024-12-31",
                role="previously_exposed_development", evidence_id="imported_history_v9a",
                reason="Prior project research exposed 2015-2024; new workflow does not restore independence")
            write_json(self.root / "state.json", {"version": VERSION, "phase": "development", "confirmation_accesses": 0,
                "development_batches": 0, "unique_evaluations_started": 0, "incremental_started": 0,
                "numeric_wall_seconds": 0., "numeric_cpu_seconds": 0., "closed": False})
            self.event("initialized", study_id=cfg["study_id"], expansion=expansion.summary)
            return self.status()

    def guard(self, phase=None):
        cfg, state = self.config, self.state
        if phase is not None and state["phase"] != phase:
            raise ValueError("operation forbidden after phase transition: " + state["phase"])
        if phase == "development" and (self.root / "frozen_candidates.json").exists():
            raise ValueError("generation/development is permanently closed after candidate freeze")
        freeze_pin = self.root / "frozen_identity.json"
        if freeze_pin.exists() and read(freeze_pin)["sha256"] != sha(self.root / "frozen_candidates.json"):
            raise ValueError("frozen candidate formulas/directions changed")
        if (self.root / "cancel.request").exists():
            raise InterruptedError("explicit cancellation requested")
        if shutil.disk_usage(self.root).free < cfg["budget"]["min_free_disk_bytes"]:
            raise InterruptedError("disk reserve reached; raw artifacts retained")
        if state["numeric_wall_seconds"] >= cfg["budget"]["wall_seconds"]:
            raise InterruptedError("frozen numeric wall budget exhausted")
        active_since = getattr(self, "_active_numeric_since", None)
        if active_since is not None and state["numeric_wall_seconds"] + time.monotonic() - active_since >= cfg["budget"]["wall_seconds"]:
            raise InterruptedError("active numeric operation exhausted wall budget")
        for path, expected in read(self.root / "source_pins.json").items():
            if sha(path) != expected:
                raise ValueError("source changed after protocol freeze: " + path)
        pins_path = self.root / "immutable_inputs.json"
        if pins_path.exists():
            for name, expected in read(pins_path).items():
                if sha(self.root / name) != expected:
                    raise ValueError("frozen study input changed: " + name)
        if state.get("revision_admitted"):
            for name, expected in read(self.root / "revision_identity.json").items():
                if sha(self.root / name) != expected:
                    raise ValueError("frozen revision input changed: " + name)
        import psutil
        memory = psutil.Process().memory_info()
        private = getattr(memory, "private", memory.rss)
        if private > cfg["budget"]["max_worker_private_bytes"]:
            raise InterruptedError("worker private-memory budget reached")
        artifact_bytes = sum(p.stat().st_size for p in self.root.parent.rglob("*") if p.is_file())
        if artifact_bytes > cfg["budget"]["max_artifact_bytes"]:
            raise InterruptedError("artifact storage budget reached at between-trial guard")

    def _verify_sources(self):
        from quanta_agents.meta_v6.data import inspect_parquet_sources
        cfg = self.config
        for key in ("calendar_path", "membership_path"):
            path = cfg["data"][key]
            if sha(path) != read(self.root / (Path(path).name + ".source.json"))["sha256"]:
                raise ValueError("frozen calendar/membership source changed")
        manifest = read(self.root / "data_source_manifest.json")
        if manifest["sha256"] is not None and sha(manifest["path"]) != manifest["sha256"]:
            raise ValueError("upstream manifest changed")
        actual = inspect_parquet_sources(cfg["data"]["data_root"], read(self.root / "symbols.json"))
        if actual != read(self.root / "source_inventory.json"):
            raise ValueError("frozen market source snapshot changed before numeric access")

    def _load(self, phase):
        from .adapters import load_research_panel
        cfg = self.config
        end = cfg["development_end"] if phase == "development" else cfg["confirmation_end"]
        self.guard(phase)
        if phase == "confirmation" and not (self.root / "confirmation_admission.json").exists():
            raise ValueError("confirmation requires recorded admission before numeric read")
        self._verify_sources()
        kwargs = {**{k: v for k, v in cfg["data"].items() if k != "optional_fields"}, "end": end,
                  "symbols": read(self.root / "symbols.json"),
                  "cache_dir": str(self.root.parent / "panel_cache")}
        self.event("panel_load_started", phase=phase, numeric_end=end)
        panel = load_research_panel(**kwargs)
        once(self.root / (phase + "_data_manifest.json"), {"fingerprint": panel.fingerprint(), "provenance": panel.provenance})
        write_json(self.root / (phase + "_load_resources.json"), panel.load_metrics)
        self.event("panel_loaded", phase=phase, **panel.load_metrics)
        self.project.record_exposure(cfg["study_id"], start=cfg["data"]["start"], end=end,
            role=phase, evidence_id=phase + "_panel", reason="Frozen V9A numerical evaluation")
        return panel

    def _evaluator(self, panel):
        from .evaluation import FactorEvaluator
        cfg = self.config
        return FactorEvaluator(panel, train_start=cfg["fit_start"], train_end=cfg["fit_end"],
            horizon=cfg["label"]["horizon"], cache_dir=self.root.parent / "factor_cache",
            min_cross_section=cfg["target"]["min_cross_section"],
            **{k: cfg["uncertainty"][k] for k in ("block_sessions", "bootstrap_samples", "seed")})

    def develop(self, *, limit=None):
        """Execute/resume the frozen proposal queue; every failed start counts."""
        def work():
            self.guard("development")
            cfg = self.config
            rows = self.expansion["candidates"]
            pending = [r for r in rows if not (self.root / "development" / (factor_spec(r).factor_id + ".json")).exists()]
            if limit is not None:
                if type(limit) is not int or limit < 1:
                    raise ValueError("positive calibration batch size required")
                pending = pending[:limit]
            if not pending:
                return self.status()
            panel = self._load("development")
            evaluator = self._evaluator(panel)
            self._state(development_batches=max(1, self.state["development_batches"]))
            for row in pending:
                self.guard("development")
                state = self.state
                spec = factor_spec(row)
                start_record = self.root / "starts" / (spec.factor_id + ".json")
                reserved = len(list((self.root / "starts").glob("*.json")))
                if not start_record.exists() and reserved >= cfg["budget"]["max_unique_candidates"]:
                    raise InterruptedError("unique candidate evaluation budget exhausted")
                if not start_record.exists():
                    once(start_record, {"factor_id": spec.factor_id, "utc": datetime.now(timezone.utc).isoformat()})
                self._state(unique_evaluations_started=len(list((self.root / "starts").glob("*.json"))))
                self.event("factor_started", factor_id=spec.factor_id, name=spec.name, arm=row["arm"])
                start, cpu = time.perf_counter(), time.process_time()
                try:
                    train = evaluator.evaluate(spec, start=cfg["fit_start"], end=cfg["fit_end"])
                    development = evaluator.evaluate(spec, start=cfg["development_start"], end=cfg["development_end"],
                                                     direction=train["direction"])
                    result = {"status": "evaluated", "candidate": row, "factor_id": spec.factor_id,
                              "train": train, "development": development}
                except Exception as exc:
                    result = {"status": "implementation_failed", "candidate": row, "factor_id": spec.factor_id,
                              "error_type": type(exc).__name__, "error": str(exc)}
                import psutil
                mem = psutil.Process().memory_info()
                result["resources"] = {"wall_seconds": time.perf_counter() - start,
                    "cpu_seconds": time.process_time() - cpu, "private_bytes": getattr(mem, "private", mem.rss),
                    "peak_working_set_bytes": getattr(mem, "peak_wset", None), "cache": evaluator.cache.info}
                once(self.root / "development" / (spec.factor_id + ".json"), result)
                self.project.record_trial(cfg["study_id"], "dev_" + spec.factor_id, "single_factor",
                    {"factor_id": spec.factor_id, "spec": spec.to_dict(), "split": "2016-2018_fit_2019-2020_development",
                     "protocol_id": digest(cfg)}, status=result["status"],
                    evidence={"path": str(self.root / "development" / (spec.factor_id + ".json"))})
                self.event("factor_finished", factor_id=spec.factor_id, status=result["status"],
                    development=result.get("development", {}).get("summary", {}), resources=result["resources"])
            del evaluator, panel
            gc.collect()
            self.development_summary()
            return self.status()
        with exclusive_lock(self.root / "study.lock"):
            return self._timed(work)

    def _valid_years(self, report, years):
        rows = {r["year"]: r for r in report.get("annual", [])}
        target = self.config["target"]
        reasons = []
        for year in years:
            row = rows.get(year)
            if row is None:
                reasons.append(f"{year}:missing_year")
                continue
            if row.get("mean_pearson_ic") is None:
                reasons.append(f"{year}:unknown_IC")
            if row.get("valid_days", 0) < target["min_valid_days_per_year"]:
                reasons.append(f"{year}:insufficient_days")
            if (row.get("evaluation_coverage") or 0.) < target["min_coverage_per_year"]:
                reasons.append(f"{year}:insufficient_coverage")
            if not row.get("requested_full_natural_year"):
                reasons.append(f"{year}:partial_year")
        return not reasons, reasons

    def development_summary(self):
        expansion = self.expansion
        records, ranked = [], []
        for row in expansion["candidates"]:
            spec = factor_spec(row)
            path = self.root / "development" / (spec.factor_id + ".json")
            if not path.exists():
                continue
            record = read(path)
            summary = {"factor_id": spec.factor_id, "name": spec.name, "arm": row["arm"],
                       "kind": row["kind"], "family_id": row["family_id"], "status": record["status"]}
            summary["primary_arms"] = sorted({a["arm"] for a in expansion["attempts"]
                if a["factor_id"] == spec.factor_id and a["kind"] == "candidate" and a["status"] in ("admitted", "duplicate")})
            if record["status"] == "evaluated":
                report = record["development"]
                valid, reasons = self._valid_years(report, (2019, 2020))
                means = [r["mean_pearson_ic"] for r in report["annual"]]
                summary.update(eligible_for_selection=valid, reasons=reasons,
                    direction=report["direction"], annual=report["annual"], summary=report["summary"],
                    worst_year_pearson=min(means) if all(v is not None for v in means) and len(means) == 2 else None)
                if valid and summary["primary_arms"]:
                    ranked.append(summary)
            records.append(summary)
        ranked.sort(key=lambda r: (-r["worst_year_pearson"], -r["summary"]["mean_pearson_ic"], r["factor_id"]))
        result = {"stage": "development_only", "confirmation_feedback_present": False,
                  "attempts": expansion["summary"], "records": records, "ranking": ranked,
                  "main_arms": {arm: {"evaluated_unique": sum(arm in r["primary_arms"] for r in records),
                                      "eligible": sum(arm in r["primary_arms"] for r in ranked)} for arm in ARMS}}
        write_json(self.root / "development_summary.json", result)
        return result

    def select_candidates(self):
        self.guard("development")
        summary = self.development_summary()
        expected = len(self.expansion["candidates"])
        if len(summary["records"]) != expected:
            raise ValueError("finish every frozen candidate before selection")
        ranking = summary["ranking"]
        selected, seen = [], set()
        for arm in ARMS:
            best = next((r for r in ranking if arm in r["primary_arms"]), None)
            if best and best["factor_id"] not in seen:
                selected.append(best); seen.add(best["factor_id"])
        for row in ranking[:self.config["selection"]["global_best"]]:
            if row["factor_id"] not in seen:
                selected.append(row); seen.add(row["factor_id"])
        return selected

    def _increments(self, evaluator, selected, phase):
        cfg = self.config
        all_rows = {factor_spec(r).factor_id: r for r in self.expansion["candidates"]}
        baseline = [factor_spec(r) for r in read(self.root / "baseline.json")]
        start = cfg["development_start"] if phase == "development" else cfg["confirmation_start"]
        end = cfg["development_end"] if phase == "development" else cfg["confirmation_end"]
        for selected_row in selected:
            identity = selected_row["factor_id"]
            path = self.root / (phase + "_incremental") / (identity + ".json")
            if path.exists():
                continue
            self.guard(phase)
            state = self.state
            reservation = self.root / "incremental_starts" / (phase + "_" + identity + ".json")
            reservations = len(list((self.root / "incremental_starts").glob("*.json")))
            if not reservation.exists() and reservations >= cfg["budget"]["max_incremental_evaluations"]:
                raise InterruptedError("paired incremental budget exhausted")
            if not reservation.exists():
                once(reservation, {"factor_id": identity, "phase": phase})
            self._state(incremental_started=len(list((self.root / "incremental_starts").glob("*.json"))))
            row = all_rows[identity]
            main_effects = [factor_spec(all_rows[k]) for k in row.get("main_effect_ids", ())]
            spec = factor_spec(row)
            started = time.perf_counter()
            try:
                result = evaluator.incremental(spec, baseline, start=start, end=end,
                    main_effects=main_effects, ridge_lambda=cfg["baseline"]["ridge_lambda"])
            except Exception as exc:
                result = {"status": "implementation_failed", "error": str(exc), "error_type": type(exc).__name__}
            once(path, {"factor_id": identity, "phase": phase, "result": result,
                        "wall_seconds": time.perf_counter() - started})
            self.event("incremental_finished", phase=phase, factor_id=identity,
                       status=result.get("status"), delta=result.get("paired_delta_pearson_ic"))

    def compare(self):
        """Same fixed predictor budget for all primary formulas, plus controls."""
        def work():
            self.guard("development")
            cfg = self.config
            expansion = self.expansion
            summary = self.development_summary()
            if len(summary["records"]) != len(expansion["candidates"]):
                raise ValueError("comparison requires completed formula queue")
            primaries = [r for r in summary["records"] if r["status"] == "evaluated" and r["primary_arms"]]
            panel = self._load("development")
            evaluator = self._evaluator(panel)
            self._increments(evaluator, primaries, "development")
            from .contracts import FactorFamily, ExpandedCandidate, FalsifierSpec
            from .generation import evaluate_falsifiers
            family_rows = read(self.root / "proposals.json")["families"]
            if self.state.get("revision_admitted"):
                family_rows += read(self.root / "revision_proposals.json")["families"]
            families = {r["family_id"]: FactorFamily.from_dict(r) for r in family_rows}
            specs = {factor_spec(r).factor_id: factor_spec(r) for r in expansion["candidates"]}
            outcomes = []
            for attempt in expansion["attempts"]:
                if attempt["kind"] != "candidate" or attempt["status"] not in ("admitted", "duplicate"):
                    continue
                family = families[attempt["family_id"]]
                fid = attempt["factor_id"]
                candidate = ExpandedCandidate(specs[fid], family.family_id, family.arm, "candidate",
                    attempt["parameters"], attempt["control_ids"], tuple(attempt["main_effect_ids"]),
                    family.falsifiers, attempt["trial_id"], attempt["proposal_id"])
                observations = []
                for falsifier in family.falsifiers:
                    ids = [attempt["control_ids"][key] for key in falsifier.control_refs]
                    evidence_path = self.root / "paired_controls" / (attempt["trial_id"] + "_" + falsifier.falsifier_id + ".json")
                    if falsifier.kind == "paired_ic_gain":
                        if evidence_path.exists():
                            measured = read(evidence_path)
                        else:
                            try:
                                measured = evaluator.paired_factor_comparison(specs[fid], specs[ids[0]],
                                    start=cfg["development_start"], end=cfg["development_end"])
                            except Exception as exc:
                                measured = {"status": "implementation_failed", "error_type": type(exc).__name__,
                                            "error": str(exc), "paired_delta_pearson_ic": None}
                            once(evidence_path, measured)
                    else:
                        source = self.root / "development_incremental" / (fid + ".json")
                        measured = read(source)["result"] if source.exists() else {}
                        if sorted(measured.get("main_effect_ids", [])) != sorted(attempt["main_effect_ids"]):
                            measured = {"status": "not_evaluable", "reason": "shared_formula_has_different_declared_main_effects"}
                    if measured.get("sample_mask_id"):
                        observations.append({"trial_id": attempt["trial_id"], "factor_id": fid,
                            "falsifier_id": falsifier.falsifier_id, "metric": falsifier.metric,
                            "split": "development_check", "control_ids": ids,
                            "sample_mask_id": measured["sample_mask_id"], "paired_days": measured.get("valid_days", 0),
                            "value": measured.get("paired_delta_pearson_ic"),
                            "fitted_direction": evaluator.fit_direction(specs[fid])["direction"] or None})
                outcomes.append({"trial_id": attempt["trial_id"], "factor_id": fid, "arm": attempt["arm"],
                    "family_id": family.family_id, "parameters": attempt["parameters"],
                    "observations": observations, "falsifiers": evaluate_falsifiers(candidate, observations)})
            name = "revision_comparisons.json" if self.state.get("revision_admitted") else "initial_comparisons.json"
            if not self.state.get("revision_admitted"):
                once(self.root / "initial_development_summary.json", summary)
            once(self.root / name, {"phase": "development_only", "outcomes": outcomes,
                "no_confirmation_feedback": True, "baseline": cfg["baseline"],
                "comparison_limit": "same main attempts; structural matched controls are additional measured overhead"})
            del panel, evaluator
            gc.collect()
            self.event("development_comparisons_completed", attempts=len(outcomes))
            return {"path": str(self.root / name), "attempts": len(outcomes)}
        with exclusive_lock(self.root / "study.lock"):
            return self._timed(work)

    def revise(self, families, *, origin):
        """Admit one dev-feedback structural revision without rewriting round one."""
        from .contracts import FactorFamily
        from .generation import expand_proposals
        with exclusive_lock(self.root / "study.lock"):
            self.guard("development")
            cfg = self.config
            if self.state.get("revision_admitted") or cfg["budget"]["max_development_batches"] < 2:
                raise ValueError("structural revision budget already consumed")
            if not (self.root / "initial_comparisons.json").exists():
                raise ValueError("structural revision requires measured initial comparisons")
            if sum(len(f.parameter_points()) for f in families) > cfg["budget"]["max_revision_main_attempts"]:
                raise ValueError("revision primary attempt budget exceeded")
            old = [FactorFamily.from_dict(r) for r in read(self.root / "proposals.json")["families"]]
            old_ids = {f.family_id for f in old}
            if not families or any(f.family_id in old_ids or f.arm != "llm_structure" for f in families):
                raise ValueError("revision requires new named structural families")
            from collections import Counter
            addition = expand_proposals(families, max_candidates=cfg["budget"]["max_unique_candidates"],
                                       max_attempts=cfg["budget"]["max_expansion_attempts"])
            old_expansion = self.expansion
            candidates = {factor_spec(r).factor_id: r for r in old_expansion["candidates"]}
            new_candidates = {c.spec.factor_id: c.to_dict() for c in addition.candidates}
            attempts = list(old_expansion["attempts"])
            groups = {}
            for attempt in addition.attempts:
                groups.setdefault(attempt["primary_trial_id"], []).append(attempt)
            for group in groups.values():
                new_ids = {a["factor_id"] for a in group} - set(candidates)
                blocked = (len(candidates) + len(new_ids) > cfg["budget"]["max_unique_candidates"]
                           or len(attempts) + len(group) > cfg["budget"]["max_expansion_attempts"]
                           or any(fid not in new_candidates for fid in new_ids))
                for attempt in group:
                    item = dict(attempt)
                    fid = item["factor_id"]
                    item["status"] = "budget_rejected" if blocked else "duplicate" if fid in candidates else "admitted"
                    item["duplicate_of"] = candidates[fid]["trial_id"] if fid in candidates else None
                    attempts.append(item)
                    if not blocked and fid not in candidates:
                        candidates[fid] = new_candidates[fid]
            summary = {**old_expansion["summary"], "primary_attempts_by_arm": dict(Counter(a["arm"] for a in attempts if a["kind"] == "candidate")),
                "all_attempts_by_arm": dict(Counter(a["arm"] for a in attempts)), "status_counts": dict(Counter(a["status"] for a in attempts)),
                "unique_formulas": len(candidates), "attempts": len(attempts), "proposed_attempts": len(attempts),
                "admitted_attempts_including_duplicates": sum(a["status"] in ("admitted", "duplicate") for a in attempts),
                "matched_control_attempts": sum(a["kind"] == "control" for a in attempts),
                "formula_memberships": {fid: sorted({a["arm"] for a in attempts if a["factor_id"] == fid and a["status"] != "budget_rejected"}) for fid in candidates},
                "initial_canonical_records_preserved": True, "initial_fair_comparison": "initial_* artifacts only; extra structural budget is separate"}
            once(self.root / "revision_proposals.json", {"families": [f.to_dict() for f in families], "origin": origin,
                "feedback_sha256": sha(self.root / "initial_comparisons.json"),
                "initial_development_summary_sha256": sha(self.root / "development_summary.json")})
            once(self.root / "revision_expansion.json", {"candidates": list(candidates.values()), "attempts": attempts, "summary": summary})
            once(self.root / "revision_identity.json", {name: sha(self.root / name) for name in
                ("revision_proposals.json", "revision_expansion.json")})
            # Original canonical records remain first in expansion order. Their
            # results and all initial comparison artifacts remain immutable.
            self._state(revision_admitted=True, development_batches=2)
            self.event("structural_revision_admitted", summary=summary)
            return summary

    def freeze(self):
        def work():
            self.guard("development")
            selected = self.select_candidates()
            if selected:
                panel = self._load("development")
                evaluator = self._evaluator(panel)
                self._increments(evaluator, selected, "development")
                del panel, evaluator
                gc.collect()
            selection = {"selected": selected, "selection_policy": self.config["selection"],
                         "protocol_id": digest(self.config), "source_pins": read(self.root / "source_pins.json"),
                         "expansion_path": str(self.expansion_path), "expansion_sha256": sha(self.expansion_path),
                         "development_summary_sha256": sha(self.root / "development_summary.json"),
                         "formula_and_direction_frozen": True, "independent_validation": False}
            once(self.root / "frozen_candidates.json", selection)
            once(self.root / "frozen_identity.json", {"sha256": sha(self.root / "frozen_candidates.json")})
            self._state(phase="frozen")
            self.event("candidates_frozen", count=len(selected))
            return selection
        with exclusive_lock(self.root / "study.lock"):
            return self._timed(work)

    def confirm(self):
        def work():
            cfg, state = self.config, self.state
            if state["phase"] == "frozen":
                self.guard("frozen")
                if state["confirmation_accesses"] >= cfg["budget"]["max_confirmation_accesses"]:
                    raise ValueError("confirmation access budget exhausted")
                path = self.root / "confirmation_admission.json"
                if path.exists():
                    admission = read(path)
                    exposure = admission["exposure_record"]
                    if exposure["study_id"] != cfg["study_id"] or exposure["start"] != cfg["confirmation_start"] or exposure["end"] != cfg["confirmation_end"]:
                        raise ValueError("existing confirmation admission does not bind to frozen study")
                else:
                    # Persist intent before ledger admission. If the process
                    # stops after admission, recover the same immutable event.
                    once(self.root / "confirmation_intent.json", {"protocol_id": digest(cfg),
                         "frozen_sha256": sha(self.root / "frozen_candidates.json")})
                    prior = [r for r in self.project.list_exposures(limit=10000, study_id=cfg["study_id"])
                        if r.get("evidence_id") == "v9a_frozen_confirmation_once"]
                    if prior:
                        admission = {"exposure_record": prior[0], "require_independent": False,
                                     "admission_recorded_before_read": True, "recovered_from_project_ledger": True}
                    else:
                        admission = self.project.begin_validation(cfg["study_id"], start=cfg["confirmation_start"],
                            end=cfg["confirmation_end"], evidence_id="v9a_frozen_confirmation_once",
                            reason="Previously exposed diagnostic after formula/direction/parameter freeze", require_independent=False)
                    once(path, admission)
                self._state(phase="confirmation", confirmation_accesses=state["confirmation_accesses"] + 1)
            self.guard("confirmation")
            selected = read(self.root / "frozen_candidates.json")["selected"]
            all_rows = {factor_spec(r).factor_id: r for r in self.expansion["candidates"]}
            if selected:
                panel = self._load("confirmation")
                evaluator = self._evaluator(panel)
                for row in selected:
                    self.guard("confirmation")
                    identity = row["factor_id"]
                    path = self.root / "confirmation" / (identity + ".json")
                    if path.exists():
                        continue
                    spec = factor_spec(all_rows[identity])
                    try:
                        report = evaluator.evaluate(spec, start=cfg["confirmation_start"], end=cfg["confirmation_end"], direction=row["direction"])
                        passed, reasons = self._valid_years(report, range(2021, 2025))
                        annuals = row["annual"] + report["annual"]
                        met = passed and all(r["mean_pearson_ic"] is not None and r["mean_pearson_ic"] >= cfg["target"]["annual_threshold"] for r in annuals)
                        result = {"status": "evaluated", "factor_id": identity, "candidate": all_rows[identity], "report": report,
                            "historical_target_met": met, "confirmation_support_complete": passed,
                            "support_failures": reasons, "independent_stability_proven": False, "profitability_proven": False}
                    except Exception as exc:
                        report, met = {"annual": []}, False
                        result = {"status": "implementation_failed", "factor_id": identity, "candidate": all_rows[identity],
                                  "report": report, "error": str(exc), "error_type": type(exc).__name__,
                                  "historical_target_met": False, "independent_stability_proven": False, "profitability_proven": False}
                    once(path, result)
                    self.event("confirmation_factor_finished", factor_id=identity, historical_target_met=met,
                               annual=[{k: r[k] for k in ("year", "mean_pearson_ic", "mean_rank_ic", "valid_days")} for r in report["annual"]])
                self._increments(evaluator, selected, "confirmation")
                from .export import export_bundles
                export_bundles(self, evaluator, all_rows, selected)
                del evaluator, panel
                gc.collect()
            self._state(phase="complete", closed=True)
            self.event("research_closed", selected=len(selected))
            return self.status()
        with exclusive_lock(self.root / "study.lock"):
            return self._timed(work)

    def _timed(self, operation):
        start, cpu = time.monotonic(), time.process_time()
        self._active_numeric_since = start
        try:
            return operation()
        finally:
            state = self.state
            self._state(numeric_wall_seconds=state["numeric_wall_seconds"] + time.monotonic() - start,
                        numeric_cpu_seconds=state["numeric_cpu_seconds"] + time.process_time() - cpu)
            self._active_numeric_since = None

    def status(self):
        return {"root": str(self.root), **self.state,
                "development_records": len(list((self.root / "development").glob("*.json"))),
                "confirmation_records": len(list((self.root / "confirmation").glob("*.json"))),
                "independent_stability_proven": False, "profitability_proven": False}
