"""Persistent V9 method study; prices, expressions and accounts stay shared.

A method study separates declaration, development evaluation, frozen choice and
exposed temporal diagnosis. No provider calls or market reads happen on import.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from quanta_agents.research_kernel.store import clean, digest, exclusive_lock, serial, write_json
from quanta_agents.meta_v6.portfolio import AccountPolicy
from quanta_agents.meta_v7.ledger import ProjectLedger

VERSION = "meta_v9.0.1"
METHODS = ("equal_rank", "ridge", "ridge_augmented", "ridge_interactions")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def once(path, value):
    path = Path(path)
    value = clean(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read(path) != value:
            raise ValueError("Immutable record differs: " + str(path))
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(serial(value))
    return value


def source_files():
    package = Path(__file__).resolve().parents[1]
    return sorted([*Path(__file__).parent.glob("*.py"),
        *[package / "meta_v7" / name for name in (
            "combination.py", "combination_lab.py", "attribution.py", "execution.py", "temporal.py", "ledger.py")],
        *[package / "research_kernel" / name for name in ("compiler.py", "assets.py", "execution.py", "store.py")],
        *[package / "meta_v6" / name for name in ("factors.py", "data.py", "portfolio.py", "gateway.py")],
        package / "meta" / "factor_algebra.py"])


def default_config(project_root):
    return {"version": VERSION, "study_id": "v9_library_combination_20260909",
        "project_root": str(Path(project_root).resolve()),
        "train_start": "2016-01-01", "train_end": "2020-12-31", "declaration_end": "2017-12-31",
        "diagnostic_start": "2021-01-01", "diagnostic_end": "2024-12-31",
        "folds": [{"fit_end": f"{year-1}-12-31", "validation_start": f"{year}-01-01",
                   "validation_end": f"{year}-12-31"} for year in (2018, 2019, 2020)],
        "horizon_sessions": 5, "ridge_lambda": .1,
        "selection": {"max_factors": 6, "min_coverage": .6, "min_abs_mean_ic": 0.,
            "min_ic_days": 60, "min_annual_days": 20, "min_stable_year_fraction": .6,
            "min_usable_years": 1, "max_abs_correlation": .9, "min_cross_section": 5},
        "allocation": {"top_n": 40, "gross_exposure": 1., "max_stock_weight": .05,
            "weighting": "equal", "rebalance_sessions": 5, "rebalance_schedule": "sessions", "membership_buffer": 0},
        "account_policy": asdict(AccountPolicy()),
        "budget": {"max_model_calls": 3, "max_accounts": 18, "max_context_bytes": 24000,
            "max_response_bytes": 12000, "model_timeout_seconds": 900, "account_timeout_seconds": 900,
            "total_wall_seconds": 5400, "minimum_free_bytes": 2 * 1024**3},
        "limitations": ["All 2015-2024 dates have prior project exposure", "No 2025 numerical access",
            "Inner chronological folds participate in method selection and are not independent OOS",
            "No randomized V8/V9 architecture comparison or profitability guarantee"]}


class V9Study:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @property
    def config(self):
        return read(self.root / "config.json")

    @property
    def project(self):
        return ProjectLedger(Path(self.config["project_root"]) / "ledger")

    def initialize(self, config, library_inventory, data):
        self.root.mkdir(parents=True, exist_ok=True)
        cfg = json.loads(serial(config))
        if cfg["version"] != VERSION or cfg["train_end"] >= cfg["diagnostic_start"]:
            raise ValueError("Version and ordered train/diagnostic split required")
        for key in ("train_start", "train_end", "declaration_end", "diagnostic_start", "diagnostic_end"):
            if date.fromisoformat(cfg[key]).isoformat() != cfg[key]:
                raise ValueError("Daily ISO dates required")
        if not cfg["train_start"] <= cfg["declaration_end"] < cfg["train_end"] < cfg["diagnostic_start"] <= cfg["diagnostic_end"]:
            raise ValueError("Ordered declaration, training and diagnostic ranges required")
        if not cfg["folds"] or cfg["declaration_end"] > min(f["fit_end"] for f in cfg["folds"]):
            raise ValueError("Mechanism declaration must precede every development validation fold")
        if set(cfg["budget"]) != set(default_config(cfg["project_root"])["budget"]) or any(
                type(v) is not int or v <= 0 for v in cfg["budget"].values()):
            raise ValueError("Known positive integer study budgets required")
        if cfg["diagnostic_end"] > "2024-12-31":
            raise ValueError("This study has no 2025 numeric authorization")
        inventory = read(library_inventory)
        definitions = inventory["executable_definitions"]
        # Catalogue status cannot substitute for actual field/coverage checks.
        cfg["library_snapshot"] = {"path": str(Path(library_inventory).resolve()), "sha256": sha(library_inventory)}
        cfg["asset_ids"] = sorted(row["id"] for row in definitions)
        cfg["return_candidate_ids"] = sorted(row["id"] for row in definitions if "return" in row.get("roles", []))
        cfg["data"] = {**data, "start": "2015-01-01", "end": cfg["train_end"],
            "authorized_start": "2015-01-01", "authorized_end": cfg["train_end"],
            "cache_dir": str(self.root / "panel_cache")}
        once(self.root / "config.json", cfg)
        once(self.root / "definitions.json", definitions)
        self.project.register_study(cfg["study_id"], cfg)
        self.project.record_exposure(cfg["study_id"], start="2015-01-01", end="2024-12-31",
            role="previously_exposed_development", evidence_id="imported_project_history",
            reason="Prior V5-V8 research exposed this range; a new directory does not restore independence")
        return self.status()

    def event(self, kind, **values):
        if values.get("detail", {}).get("event") == "account_finished":
            self._account_started = None
            detail = values["detail"]
            if detail.get("budget_reserved"):
                for path in sorted((self.root / "account_starts").glob("*.json"), reverse=True):
                    started = read(path)
                    if started["identity"] == detail["identity"] and started["phase"] == detail["phase"]:
                        spec = {k: started[k] for k in ("attempt_id", "phase", "identity")}
                        self.project.record_trial(self.config["study_id"], started["attempt_id"] + "_" + detail["status"],
                            "combination_account", spec, status=detail["status"],
                            evidence={"study_root": str(self.root), "identity": detail["identity"]})
                        break
        row = {"utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **clean(values)}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(serial(row) + "\n")
        print(serial(row), flush=True)

    def guard(self):
        if (self.root / "cancel.request").exists():
            raise InterruptedError("V9 study cancellation requested")
        if shutil.disk_usage(self.root).free < self.config["budget"]["minimum_free_bytes"]:
            raise InterruptedError("V9 disk reserve reached; saved artifacts retained")
        started = self.root / "started.json"
        if started.exists() and time.time() - read(started)["epoch"] > self.config["budget"]["total_wall_seconds"]:
            raise InterruptedError("Frozen V9 wall-clock budget exhausted")

    def cancelled(self):
        try:
            self.guard()
            active = getattr(self, "_account_started", None)
            return bool(active and time.monotonic() - active > self.config["budget"]["account_timeout_seconds"])
        except InterruptedError:
            return True

    def _registry(self):
        from quanta_agents.research_kernel.assets import AssetRegistry
        registry = AssetRegistry(self.root / "assets")
        for row in read(self.root / "definitions.json"):
            registry.register({key: row[key] for key in ("id", "name", "expression", "roles", "source", "metadata") if key in row})
        return registry

    def load_training(self):
        from quanta_agents.meta_v6.data import load_market_panel
        self.guard()
        self.event("load_training", end=self.config["train_end"])
        panel = load_market_panel(**self.config["data"])
        registry = self._registry()
        frames, metrics = registry.resolve(self.config["asset_ids"], panel)
        self.event("training_material_resolved", available=len(frames), cache_hits=metrics["cache_hits"],
                   computed=metrics["computed"], unavailable=metrics["unavailable"])
        path = self.root / "material_resolution.json"
        if not path.exists():
            once(path, {"panel_fingerprint": panel.fingerprint(), "metrics": metrics,
                "resolved_ids": sorted(frames), "actual_numeric_end": str(panel.dates[-1].date())})
        elif read(path)["panel_fingerprint"] != panel.fingerprint() or read(path)["resolved_ids"] != sorted(frames):
            raise ValueError("Frozen training material changed")
        self.project.record_exposure(self.config["study_id"], start=self.config["train_start"], end=self.config["train_end"],
            role="training", evidence_id="v9_training_material", reason="Frozen library material for chronological development study")
        return panel, frames

    def prepare(self):
        """Only the early training segment is summarized to the mechanism model."""
        from quanta_agents.meta_v7.combination import fit_combinations
        from quanta_agents.meta_v7.execution import execution_pool
        from quanta_agents.meta_v7.temporal import scope_panel, scope_frames
        path = self.root / "early_fit.json"
        provenance_path = self.root / "early_fit_provenance.json"
        inputs = {str(p): sha(p) for p in [*source_files(), self.root / "config.json", self.root / "definitions.json"]}
        if path.exists() and provenance_path.exists():
            provenance = read(provenance_path)
            if provenance["inputs"] != inputs or provenance["artifact_sha256"] != sha(path):
                raise ValueError("Early evidence differs from frozen computation provenance")
            return read(path)
        panel, frames = self.load_training()
        early = scope_panel(panel, end=self.config["declaration_end"])
        frames = scope_frames(frames, early)
        opening = early.fields["open"]
        h = self.config["horizon_sessions"]
        labels = opening.shift(-h-1) / opening.shift(-1) - 1
        if "open_observed" in early.fields:
            labels = labels.where(early.fields["open_observed"].shift(-1).eq(1) & early.fields["open_observed"].shift(-h-1).eq(1))
        fitted = fit_combinations(frames, execution_pool(early), labels,
            train_start=self.config["train_start"], train_end=self.config["declaration_end"],
            horizon_sessions=h, ridge_lambda=self.config["ridge_lambda"], selection=self.config["selection"],
            return_candidate_ids=sorted(set(self.config["return_candidate_ids"]) & set(frames)))
        if any(sha(p) != value for p, value in inputs.items()):
            raise ValueError("Preparation inputs changed while computing")
        prior_unbound = path.exists()
        once(path, fitted)
        once(provenance_path, {"inputs": inputs, "artifact_sha256": sha(path),
            "previous_unbound_result_recomputed_and_equal": prior_unbound})
        self.event("early_evidence_ready", end=self.config["declaration_end"])
        return fitted

    def seal(self):
        pins = {str(p): sha(p) for p in [*source_files(), self.root / "config.json", self.root / "definitions.json"]}
        return once(self.root / "source_pins.json", pins)

    def verify(self):
        pins = read(self.root / "source_pins.json")
        for path, expected in pins.items():
            if sha(path) != expected:
                raise ValueError("Study implementation changed after freeze: " + path)
        if sha(self.config["library_snapshot"]["path"]) != self.config["library_snapshot"]["sha256"]:
            raise ValueError("Library definition snapshot changed")

    def reserve_account(self, phase, identity):
        self.guard()
        self.verify()
        folder = self.root / "account_starts"
        folder.mkdir(exist_ok=True)
        count = len(list(folder.glob("*.json")))
        if count >= self.config["budget"]["max_accounts"]:
            raise ValueError("Frozen account execution budget exhausted")
        attempt = f"account_{count+1:03d}"
        once(folder / (attempt + ".json"), {"attempt_id": attempt, "phase": phase,
            "identity": identity, "epoch": time.time()})
        self._account_started = time.monotonic()
        self.project.record_trial(self.config["study_id"], attempt, "combination_account",
            {"attempt_id": attempt, "phase": phase, "identity": identity}, status="started")
        self.event("account_started", attempt=attempt, phase=phase)

    def _schema(self, phase):
        def obj(properties):
            return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
        string = {"type": "string"}
        if phase == "declare":
            return obj({"hypothesis": string, "falsifier": string,
                "pairs": {"type": "array", "items": obj({"left": string, "right": string, "mechanism": string})}})
        if phase == "select":
            return obj({"method": {"type": "string", "enum": [*METHODS, "reject"]},
                "conclusion": string, "evidence_for_choice": {"type": "array", "items": string}, "remaining_uncertainty": string})
        return obj({"conclusion": string, "robust_strategy_supported": {"type": "boolean"},
            "recommended_status": {"type": "string", "enum": ["reject", "research_candidate_only"]}})

    def model_call(self, phase, packet, instruction):
        from quanta_agents.meta_v6.gateway import CodexGateway, verify_saved_completion, capture_saved_session
        self.guard()
        self.verify()
        self._account_started = None
        folder = self.root / "model_calls" / phase
        folder.mkdir(parents=True, exist_ok=True)
        result_file = folder / "result.json"
        prompt = ("你是 QuantaAgents V9 研究者。用中文返回规定的短JSON，不写代码或调用工具。"
            "数值由冻结程序计算；不得编造收益、把已曝光数据称独立留出或改变协议。\n" + instruction +
            "\n冻结证据JSON：\n" + serial(packet))
        if len(prompt.encode("utf-8")) > self.config["budget"]["max_context_bytes"]:
            raise ValueError("V9 model context exceeds frozen byte limit")
        prompt_path = folder / "frozen_prompt.txt"
        if prompt_path.exists() and prompt_path.read_text(encoding="utf-8") != prompt:
            raise ValueError("Frozen model input changed")
        if not prompt_path.exists():
            prompt_path.write_text(prompt, encoding="utf-8")
        once(folder / "schema.json", self._schema(phase))
        if result_file.exists():
            return read(result_file)
        used = (folder / "started.json").exists()
        count = len(list((self.root / "model_calls").glob("*/started.json")))
        if not used and count >= self.config["budget"]["max_model_calls"]:
            raise ValueError("Frozen model-call budget exhausted")
        started = time.perf_counter()
        try:
            if not used:
                once(folder / "started.json", {"phase": phase, "epoch": time.time(), "retry": False})
                self._model_trial(phase, "started")
                self.event("model_started", phase=phase, prompt_bytes=len(prompt.encode("utf-8")))
                try:
                    receipt = CodexGateway(timeout_seconds=self.config["budget"]["model_timeout_seconds"]).run(
                        prompt=prompt, schema=self._schema(phase), workdir=folder / "call", on_event=lambda e: None,
                        cancelled=self.cancelled)
                except Exception as exc:
                    once(folder / "gateway_failure.json", {"error": str(exc), "usage": getattr(exc, "usage", {}), "retry": False})
                    receipt = verify_saved_completion(folder / "call")
            else:
                receipt = verify_saved_completion(folder / "call")
            if not receipt.get("runtime_identity", {}).get("verified"):
                capture_saved_session(folder / "call", receipt)
                receipt = verify_saved_completion(folder / "call")
            if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or not receipt.get("runtime_identity", {}).get("verified"):
                raise ValueError("Required local model identity not verified")
            if len(serial(receipt["response"]).encode("utf-8")) > self.config["budget"]["max_response_bytes"]:
                raise ValueError("Model response exceeds post-generation admission limit")
            if (folder / "call" / "prompt.txt").read_text(encoding="utf-8") != prompt:
                raise ValueError("Saved provider prompt differs from frozen study prompt")
            once(folder / "verified_receipt.json", receipt)
            result = {"phase": phase, "status": "verified", "response": receipt["response"],
                "usage": receipt.get("usage", {}), "seconds": time.perf_counter() - started, "offline_recovery": used}
            once(result_file, result)
            self._model_trial(phase, "verified", {"path": str(result_file), "sha256": sha(result_file)})
            self.event("model_finished", phase=phase, usage=result["usage"])
            return result
        except Exception as exc:
            once(folder / ("failure_" + uuid4().hex + ".json"), {"error": str(exc), "offline_recovery_only": True})
            raise

    def _model_trial(self, phase, status, evidence=None):
        folder = self.root / "model_calls" / phase
        self.project.record_trial(self.config["study_id"], "model_" + phase + "_" + status, "research_decision",
            {"attempt_id": "model_" + phase, "phase": phase, "prompt_sha256": sha(folder / "frozen_prompt.txt"),
             "model": "gpt-6-astra", "effort": "xhigh"}, status=status, evidence=evidence)

    def declare(self, *, decision=None):
        if (self.root / "declaration.json").exists():
            return read(self.root / "declaration.json")
        early = self.prepare()
        if decision is None:
            definitions = read(self.root / "definitions.json")
            packet = {"scope_end": self.config["declaration_end"],
                "library": [{k: row[k] for k in ("id", "name", "expression", "roles")} for row in definitions],
                "screen_columns": ["id", "coverage", "IC", "days", "direction", "selected", "annual_IC", "reasons"],
                "early_screening": [[row["factor_id"], row["coverage"], row.get("mean_ic"), row.get("observed_ic_days"),
                    row.get("direction"), row.get("selected"),
                    [[a["year"], a["mean_ic"], a["observed_days"]] for a in row.get("annual", [])], row.get("reasons")]
                    for row in early["factor_selection"]],
                "fixed_method": "rank equal / ridge main / ridge augmented main / ridge pair interaction; lambda=.1; 40 stocks equal weights",
                "limitations": self.config["limitations"]}
            decision = self.model_call("declare", packet,
                "依据材料声明最多2对有辨别力的交互及统一可证伪假设。可用低边际IC辅助因子；不要只选高IC。"
                "left/right必须是库ID且不同。pair会得到包含相同两端主效应的对照。无值得检验的配对可返回空列表。"
                "不得使用2018以后的表现选择配对。") ["response"]
        if set(decision) != {"hypothesis", "falsifier", "pairs"} or not decision["hypothesis"].strip() or not decision["falsifier"].strip():
            raise ValueError("Declaration needs hypothesis, falsifier and explicit pairs")
        pairs = decision["pairs"]
        if not isinstance(pairs, list) or len(pairs) > 2:
            raise ValueError("At most two predeclared pairs")
        seen = set()
        for pair in pairs:
            if set(pair) != {"left", "right", "mechanism"} or pair["left"] == pair["right"] or not pair["mechanism"].strip():
                raise ValueError("Invalid pair declaration")
            if not {pair["left"], pair["right"]} <= set(self.config["asset_ids"]):
                raise ValueError("Pair uses an unfrozen factor")
            key = tuple(sorted([pair["left"], pair["right"]]))
            if key in seen:
                raise ValueError("Duplicate unordered pair")
            seen.add(key)
        return once(self.root / "declaration.json", decision)

    def evaluate(self):
        from quanta_agents.meta_v7.combination_lab import evaluate_combination_lab
        path = self.root / "development_report.json"
        if path.exists():
            self.verify()
            return read(path)
        declaration = read(self.root / "declaration.json")
        self.verify()
        panel, frames = self.load_training()
        report = evaluate_combination_lab(panel, frames, train_start=self.config["train_start"], train_end=self.config["train_end"],
            folds=self.config["folds"], horizon_sessions=self.config["horizon_sessions"], ridge_lambda=self.config["ridge_lambda"],
            selection=self.config["selection"], interaction_pairs=[(p["left"], p["right"]) for p in declaration["pairs"]],
            return_candidate_ids=sorted(set(self.config["return_candidate_ids"]) & set(frames)), allocation=self.config["allocation"],
            policy=AccountPolicy(**self.config["account_policy"]), artifact_dir=self.root / "development",
            before_account=self.reserve_account, cancelled=self.cancelled, on_event=lambda e: self.event("development", detail=e))
        self._account_started = None
        once(path, report)
        del panel, frames
        gc.collect()
        self.event("development_finished")
        return report

    def selection_packet(self, report):
        def interaction_terms(fit):
            model = ((fit or {}).get("models") or {}).get("ridge_interactions") or {}
            return [{"factor_ids": feature["factor_ids"], "coefficient": coefficient, "feature_scale": scale}
                    for feature, coefficient, scale in zip(model.get("features", []),
                        model.get("coefficients", []), model.get("feature_scales", []))
                    if feature.get("kind") == "centered_rank_product"]
        def summary(row):
            return {key: row.get(key) for key in (
                "return", "sharpe", "max_drawdown", "mean_exposure", "turnover", "fees", "slippage", "trade_count")}
        return {"declaration": read(self.root / "declaration.json"), "methods": report["methods"],
            "folds": [{"plan": fold["plan"], "fit_status": fold["fit_status"],
                "selected_factors": (fold.get("fit") or {}).get("selected_factors"),
                "interaction_terms": interaction_terms(fold.get("fit")),
                "accounts": {method: {"status": account["status"], "summary": summary(account.get("summary") or {})}
                             for method, account in fold["accounts"].items()},
                "relative_to_pool": {method: {"net_excess": a.get("net_excess"), "exposure_matched": a.get("exposure_matched")}
                                     for method, a in fold["attribution"].items()},
                "interaction_control": fold.get("interaction_control")}
                      for fold in report["folds"]],
            "final_selected_factors": (report.get("final_fit") or {}).get("selected_factors"),
            "final_fit_status": report.get("final_fit_status"),
            "final_model_status": {method: value.get("status") for method, value in
                                   ((report.get("final_fit") or {}).get("model_status") or {}).items()},
            "available_specs": sorted(report.get("final_strategy_specs") or {}),
            "final_interaction_terms": interaction_terms(report.get("final_fit")),
            "interaction_coefficient_semantics": "Coefficient multiplies standardized product of centered percentile ranks; raw product slope=coefficient/feature_scale; conditional association, not causal effect",
            "account_denominator": report["account_denominator"], "limitations": report["limitations"],
            "instruction_boundary": "No 2021-2024 values have been read by this V9 study; all presented folds are development selection data"}

    def select(self, *, decision=None):
        path = self.root / "selection.json"
        if path.exists():
            return read(path)
        report = read(self.root / "development_report.json")
        # All fitted recipes are delivered even if the researcher declines promotion.
        once(self.root / "candidate_strategies.json", report["final_strategy_specs"])
        if decision is None:
            decision = self.model_call("select", self.selection_packet(report),
                "根据全部时间折及同池净收益比较选择一个方法进入已曝光后期诊断，或reject。"
                "只能选全部时间折完成且final_fit成功的方法。复杂方法需要相对简单方法的具体支持；"
                "交互增量应比较ridge_interactions与ridge_augmented，不能把辅助主效应增量算成交互。"
                "没有足够晋级依据可reject；不能按一年的最高收益选择，不得声称独立样本外。"
                "选择只用于冻结研究候选，完整规格由程序继承，不重写策略。") ["response"]
        required = {"method", "conclusion", "evidence_for_choice", "remaining_uncertainty"}
        if set(decision) != required or decision["method"] not in (*METHODS, "reject") or not decision["conclusion"].strip():
            raise ValueError("Invalid method selection")
        method = decision["method"]
        if method != "reject":
            summary = report["methods"].get(method, {})
            if summary.get("completed_folds") != len(self.config["folds"]) or method not in report["final_strategy_specs"]:
                raise ValueError("Selected method lacks complete development and final fitting")
            spec = report["final_strategy_specs"][method]
            once(self.root / "selected_strategy.json", {"version": VERSION, "method": method,
                "strategy": spec, "strategy_sha256": digest(spec),
                "fit_artifact_sha256": report["final_fit"]["artifact_sha256"],
                "fit_input_sha256": report["final_fit"]["fit_input_sha256"],
                "training_end": self.config["train_end"], "selection": decision,
                "status": "frozen_research_candidate_not_validated_profitability"})
        return once(path, decision)

    def diagnose(self):
        from quanta_agents.meta_v6.data import load_market_panel
        from quanta_agents.meta_v7.attribution import equal_pool_benchmark, compare_accounts
        from quanta_agents.meta_v7.combination_lab import _run_account
        from quanta_agents.meta_v7.execution import execute_strategy
        from quanta_agents.research_kernel.execution import strategy_factor_ids
        path = self.root / "diagnostic_report.json"
        if path.exists():
            self.verify()
            return read(path)
        selected = read(self.root / "selection.json")
        if selected["method"] == "reject":
            return {"status": "not_unlocked_rejected_in_development"}
        self.verify()
        chosen = read(self.root / "selected_strategy.json")
        if digest(chosen["strategy"]) != chosen["strategy_sha256"]:
            raise ValueError("Frozen selected strategy changed")
        cfg = self.config
        once(self.root / "diagnostic_unlocked.json", {"method": chosen["method"],
            "strategy_sha256": chosen["strategy_sha256"], "start": cfg["diagnostic_start"], "end": cfg["diagnostic_end"],
            "exposure": "previously_exposed_temporal_diagnostic", "reselection_allowed": False})
        exposure = self.project.begin_validation(cfg["study_id"], start=cfg["diagnostic_start"], end=cfg["diagnostic_end"],
            evidence_id="v9_frozen_temporal_diagnostic", reason="Frozen method selected only from development evidence",
            require_independent=False)
        self.guard()
        self.event("diagnostic_unlocked", method=chosen["method"], end=cfg["diagnostic_end"])
        data = {**cfg["data"], "end": cfg["diagnostic_end"], "authorized_end": cfg["diagnostic_end"]}
        panel = load_market_panel(**data)
        frames, cache = self._registry().resolve(sorted(strategy_factor_ids(chosen["strategy"])), panel)
        if not cache["complete"]:
            raise ValueError("Selected factor values unavailable in diagnostic range")
        policy = AccountPolicy(**cfg["account_policy"])
        records, accounts = {}, {}
        for name in ("candidate", "equal_pool_benchmark", "candidate_double_cost"):
            multiplier = 2. if name == "candidate_double_cost" else 1.
            frozen = {"name": name, "chosen": chosen, "policy": cfg["account_policy"], "cost_multiplier": multiplier,
                "scope": [cfg["diagnostic_start"], cfg["diagnostic_end"]], "panel_fingerprint": panel.fingerprint(),
                "source_pins_sha256": sha(self.root / "source_pins.json")}
            identity = digest(frozen)
            def execute(name=name, multiplier=multiplier):
                if name == "equal_pool_benchmark":
                    return equal_pool_benchmark(panel, start=cfg["diagnostic_start"], end=cfg["diagnostic_end"],
                        allocation=cfg["allocation"], policy=policy, cancelled=self.cancelled)
                return execute_strategy(panel, frames, chosen["strategy"], start=cfg["diagnostic_start"], end=cfg["diagnostic_end"],
                    policy=policy, cost_multiplier=multiplier, cancelled=self.cancelled)
            record, account = _run_account(self.root / "diagnostic" / name, identity, frozen, "diagnostic:" + name,
                execute, self.reserve_account, self.cancelled, lambda e: self.event("diagnostic", detail=e))
            records[name] = record
            if account is not None:
                accounts[name] = account
        self._account_started = None
        attribution = (compare_accounts(accounts["candidate"], accounts["equal_pool_benchmark"])
            if {"candidate", "equal_pool_benchmark"} <= set(accounts) else None)
        result = {"version": VERSION, "method": chosen["method"], "status": "completed" if len(accounts) == 3 else "completed_with_failures",
            "scope": [cfg["diagnostic_start"], cfg["diagnostic_end"]], "accounts": records,
            "relative_to_pool": attribution, "prior_exposure": exposure,
            "cost_stress": {"multiplier": 2., "separate_account": True, "selection_uses_stress": False},
            "independent_holdout": False, "reselection_allowed": False, "formal_financial_success": False}
        once(path, result)
        self.event("diagnostic_finished", status=result["status"])
        return result

    def close(self, *, decision=None):
        if (self.root / "closed.json").exists():
            return read(self.root / "closed.json")
        selected = read(self.root / "selection.json")
        if selected["method"] == "reject":
            decision = {"conclusion": selected["conclusion"], "robust_strategy_supported": False,
                        "recommended_status": "reject"}
        elif not (self.root / "diagnostic_report.json").exists():
            raise ValueError("Review requires retained diagnostic results")
        elif decision is None:
            report = read(self.root / "diagnostic_report.json")
            packet = {"selection": selected, "scope": report["scope"],
                "accounts": {k: {"status": v["status"], "summary": v.get("summary")}
                             for k, v in report["accounts"].items()},
                "relative_to_pool": report["relative_to_pool"], "cost_stress": report["cost_stress"],
                "limitations": self.config["limitations"]}
            decision = self.model_call("review", packet,
                "复盘已冻结方法的后期诊断，明确绝对收益、相对基准、仓位/成本及2倍成本压力。"
                "不允许修改策略或重选，不得把beta截距称因果alpha。无独立留出，最多research_candidate_only；"
                "失败则reject，不能为完成任务宣称盈利。") ["response"]
        if set(decision) != {"conclusion", "robust_strategy_supported", "recommended_status"}:
            raise ValueError("Invalid closing review")
        if (not isinstance(decision["conclusion"], str) or not decision["conclusion"].strip()
                or type(decision["robust_strategy_supported"]) is not bool
                or decision["recommended_status"] not in {"reject", "research_candidate_only"}):
            raise ValueError("Substantive bounded closing review required")
        result = {"version": VERSION, "review": decision, "status": "closed", "independent_profitability_proven": False,
            "v8_superiority_proven": False, "new_2025_values": False, "counts": self.status(), "usage": self.usage()}
        once(self.root / "closed.json", result)
        self.event("study_closed", recommended_status=decision["recommended_status"])
        return result

    def usage(self):
        totals = {"input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "cached_input_tokens": 0}
        unknown, calls = [], []
        for path in sorted((self.root / "model_calls").glob("*/started.json")):
            result = path.parent / "verified_receipt.json"
            failure = path.parent / "gateway_failure.json"
            usage = read(result).get("usage", {}) if result.exists() else (read(failure).get("usage", {}) if failure.exists() else {})
            if not all(k in usage for k in ("input_tokens", "output_tokens")):
                unknown.append(path.parent.name)
            for key in totals:
                if isinstance(usage.get(key), int):
                    totals[key] += usage[key]
            calls.append({"phase": path.parent.name, "usage": usage, "verified": result.exists()})
        return {**totals, "total_tokens": totals["input_tokens"] + totals["output_tokens"],
            "unknown_usage_calls": unknown, "calls": calls,
            "reasoning_is_output_subset": True, "v7_v8_same_task_comparison": False}

    def run(self):
        with exclusive_lock(self.root / "study.lock"):
            if (self.root / "closed.json").exists():
                return read(self.root / "closed.json")
            if not (self.root / "started.json").exists():
                once(self.root / "started.json", {"epoch": time.time()})
            self.seal()
            self.prepare()
            self.declare()
            self.evaluate()
            self.select()
            self.diagnose()
            return self.close()

    def status(self):
        stages = ("config", "early_fit", "declaration", "development_report", "selection", "diagnostic_report", "closed")
        return {"version": VERSION, "root": str(self.root),
            "stages": {stage: (self.root / (stage + ".json")).exists() for stage in stages},
            "account_starts": len(list((self.root / "account_starts").glob("*.json"))),
            "model_calls": len(list((self.root / "model_calls").glob("*/started.json"))),
            "independent_profitability_proven": False, "v8_superiority_proven": False}
