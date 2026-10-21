"""V9.1 decision-triggered range research with persistent full-account batches."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import gc
import math
import os
import shutil
import time

import numpy as np
import pandas as pd
import psutil

from quanta_agents.meta_v6.portfolio import AccountPolicy
from quanta_agents.research_kernel.store import clean, digest, exclusive_lock, serial, write_json
from .study import V9Study, once, read, sha
from .ranges import VERSION, coarse_candidates, objective, range_summary, sample_range, validate_range
from .batch_compute import build_cache, verify_cache, init_worker, evaluate_job


def default_range_config(legacy_root, root):
    old = read(Path(legacy_root) / "config.json")
    return {"version": VERSION, "study_id": "v9_range_search_20260909", "project_root": old["project_root"],
            "legacy_root": str(Path(legacy_root).resolve()), "library_snapshot": old["library_snapshot"],
            "account_policy": old["account_policy"], "train_start": "2016-01-01", "train_end": "2020-12-31",
            "screen_end": "2018-12-31", "diagnostic_start": "2021-01-01", "diagnostic_end": "2024-12-31",
            "data": old["data"], "seed": 910202609, "samples_per_range": 96, "refinement_rounds": 2,
            "max_ranges": 6, "workers": 8, "minimum_memory_bytes": 2 * 1024**3,
            "budget": {"max_model_calls": 3, "max_accounts": 4000, "max_context_bytes": 14000,
                "max_response_bytes": 14000, "model_timeout_seconds": 900, "account_timeout_seconds": 900,
                "total_wall_seconds": 14400, "minimum_free_bytes": 3 * 1024**3},
            "goal": {"metric": "mean_full_year_sharpe", "threshold": 1., "comparison": "strictly_greater",
                     "net_of_costs": True, "risk_free_rate": old["account_policy"]["risk_free_rate"]},
            "limitations": ["2015-2024 already exposed to prior project research; no independent holdout",
                "2016-2020 is search data; 2021-2024 opened only after final parameter freeze in this run",
                "No 2025 numerical access; no text signals or newly invented factors",
                "Full existing adjusted-unit account, source/fill certification remains unavailable",
                "Broad correlated search is not a set of independent trials"]}


def source_pins():
    package = Path(__file__).resolve().parents[1]
    paths = [*Path(__file__).parent.glob("*.py"), package / "meta_v6/portfolio.py",
             package / "meta_v6/data.py", package / "meta_v6/gateway.py", package / "meta_v6/factors.py",
             package / "meta_v7/temporal.py", package / "meta_v7/execution.py",
             package / "meta_v7/assets.py", package / "research_kernel/assets.py",
             package / "research_kernel/compiler.py", package / "research_kernel/execution.py",
             package / "research_kernel/store.py", package / "meta/factor_algebra.py"]
    return {str(p): sha(p) for p in paths}


def compact_row(row):
    return {"id": row["id"], "spec": row["spec"],
            "annual_mean_sharpe": round(row["summary"]["mean_full_year_sharpe"], 5),
            "worst_year_sharpe": round(row["summary"]["worst_full_year_sharpe"], 5),
            "return": round(row["summary"]["return"], 5),
            "drawdown": round(row["summary"]["max_drawdown"], 5),
            "turnover": round(row["summary"]["turnover"], 3)}


class RangeStudy(V9Study):
    def initialize_range(self, config):
        self.root.mkdir(parents=True, exist_ok=True)
        if config["version"] != VERSION or not config["train_start"] < config["screen_end"] < config["train_end"] < config["diagnostic_start"] < config["diagnostic_end"]:
            raise ValueError("Ordered search and diagnostic dates required")
        if config["diagnostic_end"] > "2024-12-31":
            raise ValueError("This protocol reserves 2025 numerical data")
        if not 1 <= config["workers"] <= 24 or not 1 <= config["refinement_rounds"] <= 4:
            raise ValueError("Bounded parallelism and rounds required")
        inventory = read(config["library_snapshot"]["path"])
        if sha(config["library_snapshot"]["path"]) != config["library_snapshot"]["sha256"]:
            raise ValueError("Existing library snapshot changed")
        once(self.root / "config.json", config)
        once(self.root / "definitions.json", inventory["executable_definitions"])
        once(self.root / "source_pins.json", source_pins())
        self.project.register_study(config["study_id"], config)
        self.project.record_exposure(config["study_id"], start="2015-01-01", end="2024-12-31",
            role="previously_exposed_development", evidence_id="inherited_project_exposure",
            reason="Prior V7-V9 exposure retained; directory change does not create independent history")
        return self.status()

    def event(self, kind, **values):
        row = {"utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **clean(values)}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(serial(row) + "\n")
        print(serial(row), flush=True)

    def prepare_cache(self, diagnostic=False):
        from quanta_agents.meta_v6.data import load_market_panel
        from quanta_agents.research_kernel.assets import AssetRegistry
        cache = self.root / ("diagnostic_arrays" if diagnostic else "training_arrays")
        self.guard()
        self.verify()
        if (cache / "manifest.json").exists():
            verify_cache(cache)
            return cache
        if diagnostic and not (self.root / "frozen_candidate.json").exists():
            raise ValueError("Diagnostic data requires a frozen candidate")
        end = self.config["diagnostic_end"] if diagnostic else self.config["train_end"]
        self.project.record_exposure(self.config["study_id"], start="2015-01-01", end=end,
            role="exposed_diagnostic" if diagnostic else "range_search_development", evidence_id="arrays_" + end,
            reason="Register numeric access before loading source data")
        data = {**self.config["data"], "end": end, "authorized_end": end}
        self.event("prepare_arrays", end=end)
        panel = load_market_panel(**data)
        registry = AssetRegistry(Path(self.config["legacy_root"]) / "assets")
        ids = [r["id"] for r in read(self.root / "definitions.json")]
        frames, metrics = registry.resolve(ids, panel)
        if set(frames) != set(ids):
            self.event("unavailable_factors", details=metrics.get("unavailable"))
        binding = {"panel_fingerprint": panel.fingerprint(), "library_sha256": self.config["library_snapshot"]["sha256"],
                   "actual_start": str(panel.dates[0].date()), "actual_end": str(panel.dates[-1].date()),
                   "policy": self.config["account_policy"], "source_pins_sha256": sha(self.root / "source_pins.json")}
        manifest = build_cache(cache, panel, frames, AccountPolicy(**self.config["account_policy"]), binding)
        self.event("arrays_ready", available_factors=len(frames), cache_bytes=sum(p.stat().st_size for p in cache.glob("*.npy")),
                   factor_cache_hits=metrics.get("cache_hits"), free_memory=psutil.virtual_memory().available)
        del panel, frames
        gc.collect()
        return cache

    def rows(self):
        return [read(p) for p in (self.root / "accounts").glob("*/result.json")]

    def batch(self, name, specs, cache, start, end, *, cost_multiplier=1., benchmark=False, workers=None):
        """A bounded inflight queue. Completion/failure is durable before refill."""
        self.guard()
        self.verify()
        jobs = []
        unique = {}
        cache_hash = sha(Path(cache) / "manifest.json")
        for spec in specs:
            identity = digest({"spec": spec, "start": start, "end": end, "cache": cache_hash,
                               "cost_multiplier": cost_multiplier, "benchmark": benchmark})
            unique[identity] = {"id": identity, "spec": spec, "start": start, "end": end,
                "cost_multiplier": cost_multiplier, "benchmark": benchmark,
                "folder": str(self.root / "accounts" / identity), "study_root": str(self.root)}
        once(self.root / "batches" / (name + ".json"), {"name": name, "ids": list(unique),
            "cache_sha256": cache_hash, "start": start, "end": end, "cost_multiplier": cost_multiplier, "benchmark": benchmark})
        results = {}
        for identity, job in unique.items():
            folder = Path(job["folder"])
            if (folder / "result.json").exists():
                row = read(folder / "result.json")
                if row["id"] != identity:
                    raise ValueError("Saved account identity mismatch")
                # Verify saved raw output when reusing, not just a status marker.
                for file, expected in row.get("raw_files", {}).items():
                    if sha(folder / file) != expected:
                        raise ValueError("Saved account artifact changed")
                results[identity] = row
            elif (folder / "started.json").exists():
                raise RuntimeError("Incomplete prior attempt needs an explicit recovery record: " + identity)
            else:
                jobs.append(job)
        if not jobs:
            return [results[i] for i in unique]
        max_workers = workers or self.config["workers"]
        calibration = self.root / "calibration.json"
        private = read(calibration)["worker_private_bytes"] if calibration.exists() else 512 * 1024**2
        safe = max(1, int((psutil.virtual_memory().available - self.config["minimum_memory_bytes"]) / max(private * 1.4, 256 * 1024**2)))
        max_workers = min(max_workers, safe)
        self.event("batch_started", name=name, new_accounts=len(jobs), reused=len(results), workers=max_workers)
        began = time.perf_counter()
        last_update = 0.
        with ProcessPoolExecutor(max_workers=max_workers, initializer=init_worker, initargs=(str(cache),)) as executor:
            pending = {}
            cursor = 0
            while cursor < len(jobs) or pending:
                self.guard()
                while cursor < len(jobs) and len(pending) < max_workers:
                    if psutil.virtual_memory().available < self.config["minimum_memory_bytes"] and pending:
                        break
                    job = jobs[cursor]
                    count = len(list((self.root / "accounts").glob("*/started.json")))
                    if count >= self.config["budget"]["max_accounts"]:
                        raise InterruptedError("Frozen full-account budget exhausted")
                    once(Path(job["folder"]) / "started.json", {"id": job["id"], "spec": job["spec"],
                        "batch": name, "start": start, "end": end, "epoch": time.time()})
                    self.project.record_trial(self.config["study_id"], job["id"] + "_started", "range_account", job,
                                              status="started", evidence={"batch": name})
                    pending[executor.submit(evaluate_job, job)] = job
                    cursor += 1
                done, _ = wait(pending, timeout=2, return_when=FIRST_COMPLETED)
                for future in done:
                    job = pending.pop(future)
                    row = future.result()
                    results[row["id"]] = row
                    self.project.record_trial(self.config["study_id"], job["id"] + "_" + row["status"], "range_account", job,
                        status=row["status"], evidence={"result_sha256": sha(Path(job["folder"]) / "result.json")})
                now = time.perf_counter()
                if now - last_update > 20:
                    write_json(self.root / "progress.json", {"batch": name, "done": len(results), "total": len(unique),
                        "active": len(pending), "workers": max_workers, "seconds": now - began,
                        "free_memory": psutil.virtual_memory().available})
                    self.event("batch_progress", name=name, done=len(results), total=len(unique), active=len(pending))
                    last_update = now
        self.event("batch_finished", name=name, total=len(results), seconds=time.perf_counter() - began,
                   failures=sum(r["status"] != "completed" for r in results.values()))
        return [results[i] for i in unique]

    def calibrate(self, cache):
        path = self.root / "calibration.json"
        if path.exists():
            return read(path)
        ids = sorted(read(Path(cache) / "manifest.json")["ranks"])
        rows = self.batch("calibration", coarse_candidates(ids)[:3], cache,
                          self.config["train_start"], self.config["screen_end"], workers=1)
        if any(r["status"] != "completed" for r in rows):
            raise RuntimeError("Calibration account failed")
        result = {"worker_private_bytes": max(r["worker_private_bytes"] for r in rows),
                  "median_account_seconds": float(np.median([r["seconds"] for r in rows])),
                  "measured_accounts": len(rows), "free_memory_bytes": psutil.virtual_memory().available,
                  "logical_cpus": os.cpu_count()}
        once(path, result)
        self.event("calibrated", **result)
        return result

    def _schema(self, phase):
        def obj(properties):
            return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
        string = {"type": "string"}
        interval = lambda typ: {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": typ}}
        if phase == "range_decision":
            factor = obj({"id": string, "direction": {"type": "integer", "enum": [-1, 1]}, "weight": interval("number")})
            space = obj({"name": string, "hypothesis": string, "falsifier": string,
                "factors": {"type": "array", "items": factor, "minItems": 1, "maxItems": 4},
                "top_n": interval("integer"), "rebalance_sessions": interval("integer"),
                "weighting": {"type": "array", "items": {"type": "string", "enum": ["equal", "inverse_volatility"]}},
                "market_filter": {"type": "array", "items": {"type": "string", "enum": ["none", "trend60", "trend120"]}},
                "buffer_multiple": {"type": "array", "items": {"type": "number", "enum": [0, 1.5, 2, 3]}}})
            return obj({"decision_reason": string, "ranges": {"type": "array", "items": space,
                        "minItems": 2, "maxItems": self.config["max_ranges"]}})
        return obj({"conclusion": string, "goal_met_in_development": {"type": "boolean"},
                    "goal_met_in_diagnostic": {"type": "boolean"}, "main_failure_or_support": string,
                    "recommended_status": {"type": "string", "enum": ["reject", "research_candidate_only"]}})

    def decision_packet(self, rows, cache):
        definitions = {r["id"]: r for r in read(self.root / "definitions.json")}
        by_factor = {}
        for row in sorted(rows, key=lambda r: (-objective(r), r["id"])):
            if math.isfinite(objective(row)):
                factor = next(iter(row["spec"]["factor_weights"]))
                if factor not in by_factor:
                    by_factor[factor] = compact_row(row)
        # Fixed compact columns; raw manifests are indexed, never pasted.
        evidence = []
        for factor, r in by_factor.items():
            s = r["spec"]
            evidence.append([factor, definitions[factor]["name"], 1 if s["factor_weights"][factor] > 0 else -1,
                r["annual_mean_sharpe"], r["worst_year_sharpe"], r["drawdown"], r["turnover"], s["top_n"],
                s["rebalance_sessions"], s["market_filter"]])
        return {"decision": "choose economic mechanisms and parameter RANGES after systematic coarse search",
            "period": [self.config["train_start"], self.config["screen_end"]], "accounts": len(rows),
            "failed": sum(r["status"] != "completed" for r in rows), "goal": self.config["goal"],
            "columns": ["factor", "name", "direction", "mean_annual_net_sharpe", "worst_year_sharpe", "drawdown", "turnover", "top_n", "hold", "filter"],
            "factor_frontier": evidence, "limits": self.config["limitations"],
            "numerical_search": {"samples_per_range": self.config["samples_per_range"], "automatic_rounds": self.config["refinement_rounds"],
                "top_n_bounds": [5, 100], "hold_bounds": [2, 120], "weight_bounds": [.05, 1.]},
            "archive": {"batches": str(self.root / "batches"), "accounts": str(self.root / "accounts")}}

    def decide_ranges(self, coarse, cache):
        path = self.root / "range_decision.json"
        if path.exists():
            return read(path)["ranges"]
        packet = self.decision_packet(coarse, cache)
        result = self.model_call("range_decision", packet,
            "程序已完成全库双向账户扫描。只在此研究方向节点需要你。提出2至6个可证伪的候选范围，"
            "包括简单单因子对照、不同经济机制组合和至少一个低换手假设；不要把低相关等同增益。"
            "利用表格而非只追最高值；所有因子必须来自表格，风险角色也可作为选股假设检验。"
            "每个范围1至4因子，方向为+1或-1，weight是[下界,上界]，程序归一化。"
            "top_n和rebalance_sessions必须是整数区间且至少一项不退化；weighting、market_filter、buffer_multiple为允许选项子集。"
            "控制总回答长度，hypothesis/falsifier各一句，程序自行采样、排名、缩小范围、创建对照并冻结最终候选。")
        spaces = result["response"]["ranges"]
        available = set(read(Path(cache) / "manifest.json")["ranks"])
        for space in spaces:
            validate_range(space, available)
        if len({s["name"] for s in spaces}) != len(spaces):
            raise ValueError("Range names must be unique")
        once(path, result["response"])
        return spaces

    def search(self, cache):
        ids = sorted(read(Path(cache) / "manifest.json")["ranks"])
        coarse = self.batch("coarse_library", coarse_candidates(ids), cache,
                            self.config["train_start"], self.config["screen_end"])
        spaces = self.decide_ranges(coarse, cache)
        # Every factor's best coarse account receives the same full-date budget.
        leaders = {}
        for row in sorted(coarse, key=lambda r: (-objective(r), r["id"])):
            if math.isfinite(objective(row)):
                factor = next(iter(row["spec"]["factor_weights"]))
                leaders.setdefault(factor, row["spec"])
        full = self.batch("full_date_factor_controls", list(leaders.values()), cache,
                          self.config["train_start"], self.config["train_end"])
        reports = []
        for turn in range(self.config["refinement_rounds"]):
            next_spaces = []
            for i, space in enumerate(spaces):
                samples = sample_range(space, self.config["samples_per_range"], self.config["seed"] + turn * 101 + i)
                rows = self.batch(f"range_{turn}_{i}", samples, cache, self.config["train_start"], self.config["train_end"])
                full.extend(rows)
                report = range_summary(space, rows)
                reports.append(report)
                next_spaces.append(report["next_range"])
            once(self.root / f"round_{turn}.json", {"range_reports": reports[-len(spaces):],
                "llm_calls_inside_round": 0, "mechanical_next_ranges": next_spaces})
            spaces = next_spaces
        # One-factor removals and turnover controls are automatic, not model actions.
        ranked = sorted({r["id"]: r for r in full}.values(), key=lambda r: (-objective(r), r["id"]))
        if not ranked or not math.isfinite(objective(ranked[0])):
            raise RuntimeError("No complete search candidate")
        candidate = ranked[0]
        controls = []
        for factor in candidate["spec"]["factor_weights"]:
            weights = {k: v for k, v in candidate["spec"]["factor_weights"].items() if k != factor}
            if weights:
                controls.append({**candidate["spec"], "factor_weights": weights})
        controls.extend([{**candidate["spec"], "membership_buffer": 0},
                         {**candidate["spec"], "market_filter": "none"},
                         {**candidate["spec"], "weighting": "equal"}])
        ablation = self.batch("automatic_controls", controls, cache, self.config["train_start"], self.config["train_end"])
        full.extend(ablation)
        ranked = sorted({r["id"]: r for r in full}.values(), key=lambda r: (-objective(r), r["id"]))
        candidate = ranked[0]
        # The rule is frozen before any later numerical data is opened.
        once(self.root / "frozen_candidate.json", {"candidate": candidate, "selection_rule": "max frozen objective over complete full-period accounts; deterministic id ties",
            "search_trials_full_period": len(ranked), "coarse_trials": len(coarse), "range_reports": reports,
            "final_parameter_ranges": spaces, "top_accounts": [compact_row(r) for r in ranked[:10] if math.isfinite(objective(r))],
            "independent_holdout": False})
        return candidate

    def diagnostic(self, candidate):
        cache = self.prepare_cache(diagnostic=True)
        cfg = self.config
        specs = [candidate["spec"]]
        normal = self.batch("diagnostic_candidate", specs, cache, cfg["diagnostic_start"], cfg["diagnostic_end"])[0]
        stress = self.batch("diagnostic_double_cost", specs, cache, cfg["diagnostic_start"], cfg["diagnostic_end"], cost_multiplier=2.)[0]
        benchmark = self.batch("diagnostic_pool", specs, cache, cfg["diagnostic_start"], cfg["diagnostic_end"], benchmark=True)[0]
        report = {"candidate": normal, "double_cost": stress, "same_pool_benchmark": benchmark,
                  "independent_holdout": False, "selection_after_diagnostic": False}
        once(self.root / "diagnostic_report.json", report)
        return report

    def close_range(self, candidate, diagnostic):
        rows = self.rows()
        good = lambda r: r["status"] == "completed" and r["summary"].get("mean_full_year_sharpe") is not None
        dev_met = good(candidate) and candidate["summary"]["mean_full_year_sharpe"] > 1
        late = diagnostic["candidate"]
        late_met = good(late) and late["summary"]["mean_full_year_sharpe"] > 1
        packet = {"development": compact_row(candidate), "diagnostic": {k: compact_row(v) if good(v) else v
            for k, v in diagnostic.items() if isinstance(v, dict)}, "goal": self.config["goal"],
            "program_goal_flags": {"development": dev_met, "diagnostic": late_met},
            "all_account_attempts": len(list((self.root / "accounts").glob("*/started.json"))),
            "failed_accounts": sum(r["status"] != "completed" for r in rows),
            "range_evidence": [{k: v for k, v in r.items() if k != "next_range"} for r in read(self.root / "frozen_candidate.json")["range_reports"]],
            "limitations": self.config["limitations"]}
        review = self.model_call("final_review", packet,
            "这是冻结后结论节点。解释自动范围搜索与机械对照得到的结果，简短判断目标是否在各期间达到。"
            "不得修改规格、回查后期重新选择、声称独立留出或把广泛搜索的极值当盈利证明。")
        result = {"version": VERSION, "engineering_run_complete": True,
            "development_goal_met": dev_met, "diagnostic_goal_met": late_met,
            "independent_profitability_proven": False, "candidate": candidate, "diagnostic": diagnostic,
            "review": review["response"], "usage": self.usage(), "account_attempts": len(rows),
            "completed_accounts": sum(r["status"] == "completed" for r in rows),
            "failed_accounts": sum(r["status"] != "completed" for r in rows),
            "model_calls": len(list((self.root / "model_calls").glob("*/started.json"))),
            "llm_calls_per_completed_account": len(list((self.root / "model_calls").glob("*/started.json"))) / max(1, sum(r["status"] == "completed" for r in rows)),
            "limitations": self.config["limitations"]}
        once(self.root / "closed.json", result)
        self.event("study_closed", development_goal_met=dev_met, diagnostic_goal_met=late_met,
                   completed_accounts=result["completed_accounts"], model_calls=result["model_calls"])
        return result

    def run(self):
        with exclusive_lock(self.root / "study.lock"):
            if (self.root / "closed.json").exists():
                return read(self.root / "closed.json")
            once(self.root / "started.json", read(self.root / "started.json") if (self.root / "started.json").exists() else {"epoch": time.time()})
            self.verify()
            cache = self.prepare_cache()
            self.calibrate(cache)
            candidate = (read(self.root / "frozen_candidate.json")["candidate"] if (self.root / "frozen_candidate.json").exists() else self.search(cache))
            diagnostic = read(self.root / "diagnostic_report.json") if (self.root / "diagnostic_report.json").exists() else self.diagnostic(candidate)
            return self.close_range(candidate, diagnostic)

    def status(self):
        return {"version": VERSION, "root": str(self.root), "closed": (self.root / "closed.json").exists(),
                "progress": read(self.root / "progress.json") if (self.root / "progress.json").exists() else None,
                "account_attempts": len(list((self.root / "accounts").glob("*/started.json"))),
                "account_results": len(list((self.root / "accounts").glob("*/result.json"))),
                "model_calls": len(list((self.root / "model_calls").glob("*/started.json")))}
