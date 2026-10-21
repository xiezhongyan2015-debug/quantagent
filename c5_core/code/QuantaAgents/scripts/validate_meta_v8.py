"""Frozen four-call interface pilot; generated tasks and saved real training evidence.

Never loads market prices or reruns old accounts. Preparation and scoring are
offline. Only the explicit run command invokes the existing pinned gateway.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quanta_agents.research_kernel.store import clean, digest, exclusive_lock, serial, write_json

DEFAULT = ROOT / "output/research/meta_v8_20260909/model_pilot"
CANDIDATES = ["factor:A", "factor:B", "interaction:A:B"]
SEEDS = [20260909, 20260910]
KINDS = ["weak_interaction", "redundancy", "null", "sparse"]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def once(path, value):
    path = Path(path)
    if path.exists():
        raise ValueError("Refusing to overwrite frozen evidence: " + str(path))
    write_json(path, value)


def _generated(seed, kind):
    import numpy as np
    import pandas as pd
    from quanta_agents.meta_v6.data import MarketPanel
    from quanta_agents.meta_v7.factor_lab import evaluate_factors
    rng = np.random.default_rng(seed)
    warm, train, valid, stocks = 130, 120, 120, 32
    dates = pd.bdate_range("2018-01-01", periods=warm+train+valid)
    columns = [f"sh{600000+i}" for i in range(stocks)]
    x, y = (rng.normal(size=(len(dates), stocks)) for _ in range(2))
    noise = rng.normal(0, .02, size=x.shape)
    if kind in {"weak_interaction", "sparse"}:
        response = .0015*x*y+noise
    elif kind == "redundancy":
        y = x+rng.normal(0, .04, size=x.shape)
        response = .002*x+noise
    else:
        response = noise
    daily = np.zeros_like(response)
    daily[2:] = np.clip(response[:-2], -.15, .15)
    opening = pd.DataFrame(10*np.cumprod(1+daily, axis=0), index=dates, columns=columns)
    ones = opening*0+1
    fields = {"open": opening, "close": opening.copy(), "high": opening*1.002, "low": opening*.998,
              "raw_open": opening.copy(), "raw_close": opening.copy(),
              "raw_prev_close": opening.shift().fillna(opening.iloc[0]),
              "volume": ones*1e6, "amount": ones*1e8, "adjustment_factor": ones,
              "is_st": ones*0, "is_delisting": ones*0, "open_observed": ones}
    a, b = pd.DataFrame(x.copy(), index=dates, columns=columns), pd.DataFrame(y.copy(), index=dates, columns=columns)
    # One fixed visible training day; not chosen by its realized IC.
    if kind == "sparse":
        a.iloc[warm:warm+train] = np.nan
        b.iloc[warm:warm+train] = np.nan
        a.iloc[warm+10], b.iloc[warm+10] = x[warm+10], y[warm+10]
    identity = digest({"seed": seed, "kind": kind, "version": 1})[:16]
    panel = MarketPanel(fields, opening.notna(), {"synthetic_only": True, "task_id": identity})
    report = evaluate_factors(panel, {"A": a, "B": b}, start=str(dates[warm].date()),
        end=str(dates[warm+train-1].date()), horizons=[1], pairs=[{"left": "A", "right": "B"}])
    labels = opening.shift(-2)/opening.shift(-1)-1
    hidden = {"a": a.iloc[warm+train:].to_numpy(), "b": b.iloc[warm+train:].to_numpy(),
              "labels": labels.iloc[warm+train:].to_numpy()}
    truth = {"task_id": identity, "seed": seed, "kind": kind,
             "expected": ["interaction:A:B"] if kind == "weak_interaction" else
                         ["factor:A", "factor:B"] if kind == "redundancy" else [],
             "sparse_policy": "Insufficient training support: abstain even if a latent construction exists",
             "independent_market_sample": False}
    return identity, report, hidden, truth


def _schema(kind, task_ids=None):
    string = {"type": "string"}
    if kind == "generated":
        item = {"type": "object", "additionalProperties": False,
                "required": ["task_id", "candidate_id", "direction", "reason"], "properties": {
            "task_id": {"type": "string", "enum": task_ids},
            "candidate_id": {"type": "string", "enum": ["abstain"]+CANDIDATES},
            "direction": {"type": "integer", "enum": [-1, 1]}, "reason": string}}
        return {"type": "object", "additionalProperties": False, "required": ["tasks", "limitations"],
                "properties": {"tasks": {"type": "array", "minItems": len(task_ids),
                    "maxItems": len(task_ids), "items": item}, "limitations": string}}
    return {"type": "object", "additionalProperties": False,
            "required": ["risk_increment_identified", "cross_round_single_change", "changed_domains",
                         "parent_run_id", "strategy_json", "changes_json", "reason"], "properties": {
        "risk_increment_identified": {"type": "boolean"}, "cross_round_single_change": {"type": "boolean"},
        "changed_domains": {"type": "array", "items": {"type": "string", "enum":
            ["prediction", "risk", "selection_scope", "exposure", "rebalance"]}},
        "parent_run_id": string, "strategy_json": string, "changes_json": string, "reason": string}}


def _real_common():
    folder = ROOT / "output/research/meta_v7_acceptance_20260909/research_study"
    db = sqlite3.connect((folder / "study.sqlite").as_uri()+"?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        runs = [dict(r) for r in db.execute("SELECT id,spec,evidence_id,status FROM runs ORDER BY rowid")]
        if len(runs) != 6 or any(r["status"] != "completed" or
                json.loads(r["spec"])["identity"]["scope"] != ["2016-01-01", "2020-12-31"] for r in runs):
            raise ValueError("Expected the six completed frozen 2016-2020 training runs")
        arms = []
        source_hashes = {str(folder/"study.sqlite"): sha(folder/"study.sqlite"),
                         str(folder/"config.json"): sha(folder/"config.json")}
        for run in runs:
            meta = dict(db.execute("SELECT path,sha256 FROM evidence WHERE id=?", (run["evidence_id"],)).fetchone())
            path = folder/meta["path"]
            if sha(path) != meta["sha256"]:
                raise ValueError("Saved real training evidence changed")
            report = read(path)
            source_hashes[str(path)] = sha(path)
            arms.append({"run_id": run["id"], "strategy": json.loads(run["spec"])["strategy"],
                "summary": {k: report["summary"].get(k) for k in
                    ("return", "sharpe", "max_drawdown", "mean_exposure", "turnover", "fees", "slippage")}})
        parent = next(a for a in arms if a["strategy"]["metadata"]["kernel_control_role"] == "omit_2")
        return {"scope": {"start": "2016-01-01", "end": "2020-12-31", "exposed": True},
                "arms": arms, "requested_parent_run_id": parent["run_id"], "source_hashes": source_hashes,
                "account_policy": read(folder/"config.json")["account_policy"],
                "account_executions_requested": 0, "later_period_values_present": False}
    finally:
        db.close()


def prepare(output, project_root=None):
    import numpy as np
    from quanta_agents.meta_v7.protocol import compact_factors
    from quanta_agents.meta_v7.decision_evidence import compact_factors_v8
    output.mkdir(parents=True, exist_ok=True)
    if (output/"intent.json").exists():
        raise ValueError("Already frozen; use run/report or another new directory")
    protocol = {"version": "v8_interface_pilot_1", "seeds": SEEDS, "kinds": KINDS,
        "project_root": str(Path(project_root or ROOT/"output/research/meta_v7_project").resolve()),
        "study_id": "v8_interface_"+digest(str(output))[:20],
        "model": "gpt-6-astra", "effort": "xhigh", "maximum_calls": 4, "retries": 0,
        "max_context_bytes": 32000, "max_response_bytes": 12000, "timeout_seconds": 900,
        "total_wall_seconds": 3900, "market_account_executions": 0,
        "generated": {"warmup": 130, "train_days": 120, "validation_days": 120, "stocks": 32,
            "weak_product_coefficient": .0015, "redundant_coefficient": .002, "noise_sd": .02,
            "sparse_fixed_training_day": 10},
        "score_policy": "Report all structural discoveries, abstentions, false positives and heldout signed IC; no statistical superiority threshold from this small dependent pilot.",
        "comparability": "Generated cells differ only in software evidence views. Real cells share exact data and parent specification but differ in full-spec versus bound-patch operation: capability change, not pure presentation causality.",
        "order": [["generated", "v7"], ["generated", "v8"], ["real", "v8"], ["real", "v7"]],
        "limits": ["Two generated seeds are not independent market samples", "One saved market study is reused",
                   "No 2025 values, no new market account, no holdout profitability claim",
                   "Output byte bound is checked after generation; not a provider token ceiling"]}
    once(output/"protocol.json", protocol)
    ledger = _ledger(protocol)
    ledger.register_study(protocol["study_id"], {"protocol": protocol,
        "training_scope": {"start": "2016-01-01", "end": "2020-12-31"},
        "reuses_study": "v7_real_research_20260909", "independent_holdout": False})
    ledger.record_exposure(protocol["study_id"], start="2016-01-01", end="2020-12-31",
        role="previously_exposed_development", evidence_id="v7_saved_training",
        reason="Saved training evidence reused for an interface comparison; no new independent market sample")
    evidence, truths = [], []
    for seed in SEEDS:
        for kind in KINDS:
            key, report, hidden, truth = _generated(seed, kind)
            once(output/"common"/(key+".json"), report)
            (output/"hidden").mkdir(exist_ok=True)
            np.savez_compressed(output/"hidden"/(key+".npz"), **hidden)
            evidence.append((key, report))
            truths.append(truth)
    once(output/"hidden_truth.json", truths)
    common = _real_common()
    once(output/"real_common.json", common)
    sources = [Path(__file__).resolve()] + list((ROOT/"src/quanta_agents/meta_v7").glob("*.py"))
    sources += [ROOT/"src/quanta_agents/meta_v6/gateway.py", ROOT/"src/quanta_agents/meta_v3/codex_session_gateway.py",
                ROOT/"src/quanta_agents/meta_v6/portfolio.py", ROOT/"src/quanta_agents/research_kernel/compiler.py"]
    cells = []
    for index, (kind, mode) in enumerate(protocol["order"]):
        if kind == "generated":
            body = [{"task_id": key, "evidence": (compact_factors(report) if mode == "v7" else compact_factors_v8(report))}
                    for key, report in evidence]
            prompt = ("Judge each opaque generated research task using ONLY its supplied training evidence. "
                "Choose at most one candidate or abstain; candidate IDs are factor:A, factor:B, interaction:A:B. "
                "The interaction is (percentile_rank(A)-.5)*(percentile_rank(B)-.5). Choose direction explicitly. "
                "Weak marginal IC does not rule out conditional information. Correlated proxies are not independent discoveries. "
                "Missing and sparse evidence permit abstention; do not infer hidden means or the number/types of real sources. "
                "These are exposed descriptive statistics, without a universal IC threshold or multiplicity correction. "
                "No validation values or construction are available. Freeze one choice per task. Use no tools; reply concise JSON.\n"+serial(body))
            schema = _schema(kind, [key for key, _ in evidence])
        else:
            body = deepcopy(common)
            body.pop("source_hashes")
            prompt = ("Review the saved real 2016-2020 training study below. This is a constrained research decision, not a new backtest. "
                "Does its inverse-volatility versus equal-weight comparison identify risk information independent of exposure? "
                "Is round two a single economic change relative to round one's omit_2 parent? Report changed domains. "
                "Then materialize exactly this predeclared next diagnostic: start from requested_parent_run_id (omit_2), change ONLY "
                "allocation.weighting to equal and risk_score to null. Keep score, holding count, cap, gross exposure and calendar identical. "
                "Do not choose a parent by returns. This requested candidate is a counterfactual specification check; no execution is authorized. "
                "Both interfaces expose the same complete parent and all saved training arms. No later-period values are available. ")
            if mode == "v7":
                prompt += "Use the V7 full-spec operation: put the complete derived normalized StrategySpec in strategy_json and [] in changes_json. "
            else:
                prompt += ("Use the V8 bound revision: put {} in strategy_json. changes_json must encode a JSON array "
                    "using exact JSON Pointer paths (leading /, not dots). The only allowed paths and values are "
                    "/allocation/weighting = equal and /risk_score = null. Example array: "
                    '[{"path":"/allocation/weighting","value":"equal"},{"path":"/risk_score","value":null}]. '
                    "Software inherits every other parent field. ")
            prompt += "Use no tools; reply concise JSON.\n"+serial(body)
            schema = _schema(kind)
        if len(prompt.encode()) > protocol["max_context_bytes"]:
            raise ValueError(f"Cell {kind}/{mode} exceeds input budget before any paid call")
        folder = output/"cells"/f"{index+1:02d}_{kind}_{mode}"
        folder.mkdir(parents=True)
        (folder/"frozen_prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
        once(folder/"schema.json", schema)
        cells.append({"cell": folder.name, "kind": kind, "mode": mode, "prompt_bytes": len(prompt.encode())})
    pins = {str(p): sha(p) for p in sources}
    pins.update(common.get("source_hashes", {}))
    pins.update({str(p): sha(p) for p in output.rglob("*") if p.is_file()})
    once(output/"intent.json", {"protocol": protocol, "cells": cells, "pins": pins})
    return {"status": "prepared", "cells": cells, "calls": 0}


def _verify(intent):
    for filename, expected in intent["pins"].items():
        if sha(filename) != expected:
            raise ValueError("Frozen source/input changed: "+filename)


def _ledger(protocol):
    from quanta_agents.meta_v7.ledger import ProjectLedger
    return ProjectLedger(Path(protocol["project_root"])/"ledger")


def _trial(intent, cell, state, result=None):
    protocol = intent["protocol"]
    _ledger(protocol).record_trial(protocol["study_id"],
        "v8_"+digest([protocol["study_id"], cell["cell"], state])[:36],
        "interface_decision", {"attempt_id": cell["cell"], "mode": cell["mode"], "kind": cell["kind"],
            "maximum_new_accounts": 0, "independent_sample": False}, status=state,
        evidence=result)


def run(output):
    with exclusive_lock(output/"pilot.lock"):
        return _run(output)


def _run(output):
    from quanta_agents.meta_v6.gateway import CodexGateway, verify_saved_completion, capture_saved_session
    intent = read(output/"intent.json")
    _verify(intent)
    if not (output/"started.json").exists():
        once(output/"started.json", {"epoch": time.time(), "utc": datetime.now(timezone.utc).isoformat()})
    deadline = read(output/"started.json")["epoch"]+intent["protocol"]["total_wall_seconds"]
    for cell in intent["cells"]:
        folder = output/"cells"/cell["cell"]
        if (folder/"result.json").exists():
            continue
        used = (folder/"call_started.json").exists()
        if not used and (time.time() >= deadline or (output/"cancel.request").exists()):
            once(folder/"result.json", {**cell, "status": "not_started_budget_or_cancel", "usage": {}})
            continue
        _verify(intent)
        started = time.perf_counter()
        try:
            if used:
                receipt = verify_saved_completion(folder/"call")
                if not receipt.get("runtime_identity", {}).get("verified"):
                    capture_saved_session(folder/"call", receipt)
                    receipt = verify_saved_completion(folder/"call")
            else:
                once(folder/"call_started.json", {"epoch": time.time(), "attempt": 1})
                _trial(intent, cell, "started")
                print(serial({"cell": cell["cell"], "status": "started"}), flush=True)
                try:
                    receipt = CodexGateway(timeout_seconds=max(1, min(900, int(deadline-time.time())))).run(
                        prompt=(folder/"frozen_prompt.txt").read_text(encoding="utf-8"), schema=read(folder/"schema.json"),
                        workdir=folder/"call", on_event=lambda e: None,
                        cancelled=lambda: time.time() >= deadline or (output/"cancel.request").exists())
                except Exception as exc:
                    once(folder/"gateway_failure.json", {"error": str(exc), "usage": getattr(exc, "usage", {}), "retry": False})
                    receipt = verify_saved_completion(folder/"call")
                    if not receipt.get("runtime_identity", {}).get("verified"):
                        capture_saved_session(folder/"call", receipt)
                        receipt = verify_saved_completion(folder/"call")
            if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or not receipt.get("runtime_identity", {}).get("verified"):
                raise ValueError("Pinned local runtime identity not verified")
            if (folder/"verified_receipt.json").exists():
                saved_receipt = read(folder/"verified_receipt.json")
                if any(saved_receipt.get(k) != receipt.get(k) for k in ("response", "usage", "model", "effort")):
                    raise ValueError("Previously verified receipt differs from saved completion")
            else:
                once(folder/"verified_receipt.json", receipt)
            if (folder/"call/prompt.txt").read_text(encoding="utf-8") != (folder/"frozen_prompt.txt").read_text(encoding="utf-8"):
                raise ValueError("Prompt changed in transport")
            if len(serial(receipt["response"]).encode()) > 12000:
                raise ValueError("Post-generation output admission limit exceeded")
            _verify(intent)
            outcome = {**cell, "status": "admitted", "response": receipt["response"], "usage": receipt.get("usage", {}),
                       "seconds": time.perf_counter()-started if not used else None,
                       "offline_recovery_seconds": time.perf_counter()-started if used else None,
                       "elapsed_since_dispatch_seconds": time.time()-read(folder/"call_started.json")["epoch"],
                       "runtime_verified": True}
        except Exception as exc:
            usage = read(folder/"verified_receipt.json").get("usage", {}) if (folder/"verified_receipt.json").exists() else (
                read(folder/"gateway_failure.json").get("usage", {}) if (folder/"gateway_failure.json").exists() else {})
            outcome = {**cell, "status": "failed", "error": str(exc), "usage": usage,
                       "seconds": time.perf_counter()-started, "retry": False}
        once(folder/"result.json", outcome)
        _trial(intent, cell, outcome["status"], {"path": str(folder/"result.json"), "sha256": sha(folder/"result.json")})
        print(serial({k: outcome.get(k) for k in ("cell", "status", "seconds", "error")}), flush=True)
    return report(output)


def recover(output):
    """Offline-only recovery; preserve every initial failure and never dispatch."""
    from quanta_agents.meta_v6.gateway import verify_saved_completion, capture_saved_session
    with exclusive_lock(output/"pilot.lock"):
        intent = read(output/"intent.json")
        _verify(intent)
        for cell in intent["cells"]:
            folder = output/"cells"/cell["cell"]
            if not (folder/"call_started.json").exists() or (folder/"recovery_result.json").exists():
                continue
            initial = read(folder/"result.json") if (folder/"result.json").exists() else {}
            if initial.get("status") == "admitted":
                continue
            started = time.perf_counter()
            try:
                receipt = verify_saved_completion(folder/"call")
                if not receipt.get("runtime_identity", {}).get("verified"):
                    capture_saved_session(folder/"call", receipt)
                    receipt = verify_saved_completion(folder/"call")
                if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or not receipt.get("runtime_identity", {}).get("verified"):
                    raise ValueError("Saved runtime is not verified")
                if (folder/"call/prompt.txt").read_text(encoding="utf-8") != (folder/"frozen_prompt.txt").read_text(encoding="utf-8"):
                    raise ValueError("Saved prompt differs from frozen prompt")
                if len(serial(receipt["response"]).encode()) > 12000:
                    raise ValueError("Saved response exceeds admission limit")
                _verify(intent)
                once(folder/"recovery_result.json", {**cell, "status": "admitted", "response": receipt["response"],
                    "usage": receipt.get("usage", {}), "seconds": initial.get("seconds"),
                    "offline_recovery_seconds": time.perf_counter()-started, "runtime_verified": True,
                    "initial_failure_retained": bool(initial), "new_model_calls": 0})
                _trial(intent, cell, "recovered", {"path": str(folder/"recovery_result.json"),
                    "sha256": sha(folder/"recovery_result.json")})
            except Exception as exc:
                folder.joinpath("recovery_failures").mkdir(exist_ok=True)
                once(folder/"recovery_failures"/(str(time.time_ns())+".json"),
                     {"error": str(exc), "offline_only": True, "new_model_calls": 0})
        return report(output)


def _score_generated(response, truths, output):
    import numpy as np
    import pandas as pd
    from quanta_agents.meta_v7.factor_lab import _ic
    rows = response.get("tasks", [])
    if len(rows) != len(truths) or {r["task_id"] for r in rows} != {r["task_id"] for r in truths}:
        raise ValueError("Missing/duplicate/unknown generated task IDs")
    scored = []
    for truth in truths:
        row = next(r for r in rows if r["task_id"] == truth["task_id"])
        choice = row["candidate_id"]
        if choice not in ["abstain"]+CANDIDATES or type(row["direction"]) is not int or row["direction"] not in (-1, 1):
            raise ValueError("Invalid choice/direction")
        signed_ic = None
        if choice != "abstain":
            with np.load(output/"hidden"/(truth["task_id"]+".npz"), allow_pickle=False) as values:
                a, b, labels = (pd.DataFrame(values[k]) for k in ("a", "b", "labels"))
            a.index = b.index = labels.index = pd.bdate_range("2020-01-01", periods=len(a))
            ranks = [f.rank(axis=1, pct=True) for f in (a, b)]
            signal = ranks[0] if choice == "factor:A" else ranks[1] if choice == "factor:B" else (ranks[0]-.5)*(ranks[1]-.5)
            signed_ic = clean(_ic(signal*row["direction"], labels)["mean_ic"])
        scored.append({**truth, "choice": choice, "direction": row["direction"],
            "structural_source_match": bool(truth["expected"]) and choice in truth["expected"],
            "direction_correct": row["direction"] == 1 if choice in truth["expected"] else None,
            "discovery": bool(truth["expected"]) and choice in truth["expected"] and row["direction"] == 1,
            "false_positive": choice != "abstain" and choice not in truth["expected"],
            "correct_abstention": not truth["expected"] and choice == "abstain",
            "missed_source": bool(truth["expected"]) and choice not in truth["expected"],
            "signed_validation_ic": signed_ic, "reason": row.get("reason")})
    return scored


def _score_real(response, mode, common):
    from quanta_agents.research_kernel.compiler import validate_strategy
    from quanta_agents.meta_v7.revisions import _differences
    parent = next(a for a in common["arms"] if a["run_id"] == common["requested_parent_run_id"])
    expected = deepcopy(parent["strategy"])
    expected["allocation"]["weighting"], expected["risk_score"] = "equal", None
    if mode == "v7":
        spec = validate_strategy(json.loads(response["strategy_json"]))
        route_valid = json.loads(response["changes_json"]) == []
    else:
        spec = deepcopy(parent["strategy"])
        changes = json.loads(response["changes_json"])
        route_valid = json.loads(response["strategy_json"]) == {} and len(changes) == 2
        seen = set()
        for row in changes:
            path = row["path"]
            if path not in {"/allocation/weighting", "/risk_score"} or path in seen or set(row) != {"path", "value"}:
                raise ValueError("Invalid real revision patch")
            seen.add(path)
            if path == "/risk_score": spec["risk_score"] = row["value"]
            else: spec["allocation"]["weighting"] = row["value"]
        spec = validate_strategy(spec)
    economic = lambda s: {k: v for k, v in s.items() if k not in {"name", "metadata"}}
    differences = _differences(economic(validate_strategy(expected)), economic(spec))
    return {"risk_attribution_correct": response["risk_increment_identified"] is False,
            "cross_round_attribution_correct": response["cross_round_single_change"] is False,
            "changed_domains_complete": {"risk", "selection_scope"} <= set(response["changed_domains"]),
            "prediction_syntax_change_reported": "prediction" in response["changed_domains"],
            "score_scaling_note": "0.5*rank(F1) and rank(F1) give the same ranking; syntax change alone does not establish new predictive information",
            "parent_correct": response["parent_run_id"] == parent["run_id"],
            "revision_economically_exact": not differences, "route_valid": route_valid,
            "unexpected_changes": differences, "derived_spec": spec, "account_executions": 0}


def report(output):
    intent = read(output/"intent.json")
    if any(not any((output/"cells"/c["cell"]/name).exists() for name in
        ("result.json", "recovery_result.json")) for c in intent["cells"]):
        raise ValueError("All four cells must be terminal before hidden scoring")
    _verify(intent)
    truths, common = read(output/"hidden_truth.json"), read(output/"real_common.json")
    cells = []
    for cell in intent["cells"]:
        folder = output/"cells"/cell["cell"]
        result = read(folder/("recovery_result.json" if (folder/"recovery_result.json").exists() else "result.json"))
        if result["status"] == "admitted":
            try:
                result["evaluation"] = (_score_generated(result["response"], truths, output) if cell["kind"] == "generated"
                                        else _score_real(result["response"], cell["mode"], common))
            except Exception as exc:
                result["status"] = "invalid_decision"
                result["scoring_error"] = str(exc)
        cells.append(result)
    totals = {}
    for mode in ("v7", "v8"):
        rows = [r for r in cells if r["mode"] == mode]
        totals[mode] = {key: sum(r.get("usage", {}).get(key, 0) or 0 for r in rows) for key in
                        ("input_tokens", "output_tokens", "reasoning_output_tokens", "cached_input_tokens")}
        totals[mode]["unknown_usage_cells"] = sum(any(r.get("usage", {}).get(k) is None for k in
            ("input_tokens", "output_tokens")) for r in rows)
        totals[mode]["usage_totals_are_complete"] = totals[mode]["unknown_usage_cells"] == 0
        totals[mode]["token_sum_semantics"] = "known subtotal; missing usage is counted separately, not assumed free"
        totals[mode]["seconds"] = sum(r.get("seconds") or 0 for r in rows)
        totals[mode]["unknown_original_duration_cells"] = sum(r.get("seconds") is None for r in rows)
        totals[mode]["total_tokens"] = totals[mode]["input_tokens"]+totals[mode]["output_tokens"]
    result = {"status": "completed_with_all_outcomes_retained", "cells": cells, "usage_totals": totals,
        "actual_calls_started": sum((output/"cells"/c["cell"]/"call_started.json").exists() for c in intent["cells"]),
        "new_market_accounts": 0, "real_research_type": "real model decisions on saved 2016-2020 market evidence",
        "general_discovery_superiority_proven": False, "financial_success": False,
        "limitations": intent["protocol"]["limits"], "intent_sha256": sha(output/"intent.json")}
    write_json(output/"report.json", result)
    return {"status": result["status"], "actual_calls_started": result["actual_calls_started"],
            "usage_totals": totals, "outcomes": [{"cell": r["cell"], "status": r["status"]} for r in cells]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run", "recover", "report"])
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    result = prepare(args.output.resolve(), args.project_root) if args.command == "prepare" else globals()[args.command](args.output.resolve())
    print(serial(result))
