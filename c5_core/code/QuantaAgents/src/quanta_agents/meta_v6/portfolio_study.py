"""Model-admitted, predeclared combination studies with temporal separation."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import time

import pandas as pd

from .data import load_market_panel
from .portfolio import AccountPolicy, DailyAccount, PortfolioSpec, target_weights
from .research import PROTOCOL, ROOT, call_researcher, dump, object_schema, save_once, TEXT


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_panel(root):
    d = PROTOCOL["data"]
    return load_market_panel(d["root"], start=d["start"], end=d["end"],
                             authorized_start=d["start"], authorized_end=d["end"],
                             calendar_path=d["calendar"], membership_path=d["membership"],
                             cache_dir=Path(root) / "preparation/market_panel_cache")


def declared_combinations(root):
    """Transcribe the already returned model plan, preserving its four arms."""
    root = Path(root)
    receipt = read(root / "model_calls/01_hypotheses/admitted_receipt.json")
    if receipt.get("runtime_identity", {}).get("verified") is not True:
        raise ValueError("hypothesis call has no verified local identity")
    response = receipt["response"]
    factors = response["factors"]
    def name(prefix):
        matches = [f for f in factors if f["name"].startswith(prefix + "_")]
        if len(matches) != 1:
            raise ValueError("this registered plan requires one declared " + prefix)
        return matches[0]
    f = {i: name("F" + str(i)) for i in range(1, 7)}
    base = {f[i]["name"]: f[i]["direction"] for i in range(1, 5)}
    variants = {"A": base, "B": {**base, "HF0091_AMIHUD20": 1},
                "C": {**base, f[5]["name"]: f[5]["direction"]},
                "D": {**base, f[5]["name"]: f[5]["direction"]}}
    specs = [PortfolioSpec(name=k, factor_weights=v, top_n=20, max_stock_weight=.05,
                           rebalance_schedule="weekly_last_session", membership_buffer=40,
                           crowding_gate_factor=f[6]["name"] if k == "D" else "",
                           metadata={"researcher_call": "01_hypotheses", "registered_arm": k})
             for k, v in variants.items()]
    declaration = {"specs": [asdict(s) for s in specs],
                   "source_plan": response["combination_plan"],
                   "source_receipt_sha256": file_hash(root / "model_calls/01_hypotheses/admitted_receipt.json"),
                   "selection": response["selection_rule"], "failures": response["failure_conditions"],
                   "compiler_notes": ["signed factor weights apply the original declared direction before ranking",
                                      "each week final scheduled session close, next session open",
                                      "buffer retains actual prior-close holdings in first 40; fill best ranks to 20",
                                      "D halves crowded rising-stock weights, retaining released cash",
                                      "120-session observed history and 20-session known positive amount are fixed implementation eligibility"],
                   "stress_reference": "risk-free growth at 2 percent annual, fixed before account results",
                   "stress_cases": ["slippage doubled only", "prior-day amount participation halved"],
                   "implementation_sha256": file_hash(Path(__file__).with_name("portfolio.py"))}
    save_once(root / "combination_declaration.json", declaration)
    return specs, declaration


ADMISSION_SCHEMA = object_schema({
    "decisions": {"type": "array", "items": object_schema({
        "candidate": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "decision": {"type": "string", "enum": ["admit_for_account_test", "reject", "needs_more_factor_evidence"]},
        "evidence_ids": {"type": "array", "items": TEXT}, "reason": TEXT})},
    "factor_review": TEXT, "precommitted_plan_review": TEXT, "unresolved_questions": TEXT,
})


def admit_combinations(root):
    root = Path(root)
    _, declaration = declared_combinations(root)
    fit = read(root / "fit_factor_view.json")
    index = read(root / "factor_index.json")
    if file_hash(root / "fit_factor_view.json") != index["fit_factor_view_sha256"]:
        raise ValueError("training factor view changed after its evidence stage")
    if file_hash(root / "factor_declaration.json") != index["factor_declaration_sha256"]:
        raise ValueError("factor declaration changed after execution")
    if fit.get("2021_2024_result_values_included") is not False:
        raise ValueError("model input does not attest the restricted fitting view")
    prompt = """You are the QuantaAgents V6 researcher (gpt-6-astra/xhigh). Respond in concise Chinese JSON.
Review the actual structured factor evidence below, restricted to 2016-2020 with 21-session end purge.
No 2021-2024 financial results are provided. All data remains previously exposed development evidence.
Your first call already froze four combinations A-D and their falsification/selection rules. Their executable
transcription follows. Review whether it implements your plan. Do not change factor signs, weights, parameters,
ranking rules, scope or capital after these factor results. Decide for each of the four whether to admit it for
account testing, reject it, or require a specific missing factor test. Weak standalone IC alone need not reject a
conditional/risk factor. Cite actual factor/evidence identifiers; no invented significance or returns.
The engine has next-open execution, 120-session observed price history, 20-session amount availability,
long-only 100-share-equivalent lots, min commission, prior-day amount participation, price limits, explicit
missing/blocked orders and continuous cash. It is an adjusted-unit development approximation with no separate
cash dividend tax ledger and no certified live fills. Its 120-session data eligibility is an implementation
constraint disclosed before account results, not a selected parameter. If it materially changes your hypothesis,
record that issue rather than silently accepting a different strategy.
Your undefined full-period stress 'net excess' reference is fixed here before account results as cumulative
annual risk-free growth of 2 percent. Stress scenarios: only slippage doubled; separately capacity halved. Exact
stress and top20/buffer40/weekly rules are in the declaration. You can flag ambiguity or reject a candidate.
The same four arms remain the only portfolio candidates in this initial stage. No search has yet consumed
2021-2024 account results, and no final independent-validation claim is permitted.
""" + "\nREGISTERED COMBINATIONS\n" + dump(declaration) + "\nACTUAL FIT FACTOR EVIDENCE\n" + dump(fit)
    result = call_researcher(root, "02_combination_admission", prompt, ADMISSION_SCHEMA)
    decisions = result["response"]["decisions"]
    if len(decisions) != 4 or {d["candidate"] for d in decisions} != {"A", "B", "C", "D"}:
        raise ValueError("researcher must explicitly decide each of the four frozen arms")
    exposure_path = root / "combination_model_exposure.json"
    if not exposure_path.exists():
        from .library import FactorLibrary
        with FactorLibrary(index["library_path"]) as library:
            event = library.record_exposure(scope=fit["scope"], purpose="verified_combination_model_input",
                        library_snapshot_id=fit["library_snapshot_id"], results_revealed=True,
                        used_for_selection=True, metadata={"fit_view_sha256": index["fit_factor_view_sha256"],
                        "model_receipt_sha256": file_hash(root / "model_calls/02_combination_admission/admitted_receipt.json")})
            save_once(exposure_path, {"exposure_event_id": event})
    return result


def save_account(folder, result):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for key in ("daily", "trades", "annual"):
        path = folder / (key + ".parquet")
        if path.exists():
            raise ValueError("refusing to overwrite an existing financial observation")
        # Avoid propagating a large attrs dictionary into every Arrow column.
        # Live account frames remain unchanged; nonempty metadata is preserved
        # separately and covered by the existing artifacts manifest below.
        export = result[key].copy(deep=False)
        export.attrs = {}
        export.to_parquet(path)
        if result[key].attrs:
            save_once(folder / (key + "_attributes.json"), result[key].attrs)
    save_once(folder / "summary.json", result["summary"])
    save_once(folder / "policy.json", result["policy"])
    save_once(folder / "artifacts.json", {p.name: file_hash(p) for p in folder.iterdir() if p.is_file()})


def evaluate_accounts(root):
    root = Path(root)
    specs, declaration = declared_combinations(root)
    admitted = read(root / "model_calls/02_combination_admission/admitted_receipt.json")
    if admitted.get("runtime_identity", {}).get("verified") is not True:
        raise ValueError("combination admission needs verified local researcher identity")
    allowed = {d["candidate"] for d in admitted["response"]["decisions"] if d["decision"] == "admit_for_account_test"}
    if not allowed <= {s.name for s in specs}:
        raise ValueError("unregistered combination")
    # Bind the raw researcher response and fixed implementation before results.
    save_once(root / "account_execution_intent.json", {
        "admission_sha256": file_hash(root / "model_calls/02_combination_admission/admitted_receipt.json"),
        "declaration_sha256": file_hash(root / "combination_declaration.json"),
        "fit_only_initially": True, "allowed": sorted(allowed),
        "selection": "highest training mean full-year Sharpe among stress-feasible arms; lower turnover breaks exact ties",
        "temporal_diagnostics": "only the frozen selected arm; no repeated selection after temporal account results"})
    if not allowed:
        result = {"status": "no_admitted_combination", "formal_target_success": False,
                  "decisions": admitted["response"]["decisions"]}
        save_once(root / "account_stage_result.json", result)
        return result
    panel = load_panel(root)
    # Factor-index schema is mapped only through its declared score artifacts.
    index = read(root / "factor_index.json")
    if panel.fingerprint() != index["scope"]["panel_fingerprint"]:
        raise ValueError("account panel differs from the frozen factor evidence")
    if file_hash(root / "fit_factor_view.json") != index["fit_factor_view_sha256"]:
        raise ValueError("factor model view changed before account execution")
    entries = index.get("factors", index.get("entries", []))
    scores = {}
    for entry in entries:
        if entry.get("status") not in {"evaluated", "completed", "succeeded"}:
            continue
        score_path = entry.get("scores_path") or entry.get("artifacts", {}).get("scores")
        if score_path:
            path = Path(score_path)
            path = (path if path.is_absolute() else root / path).resolve()
            if not path.is_relative_to((root / "factors").resolve()):
                raise ValueError("score artifact escaped the factor evidence directory")
            proof = next((p for p in entry["artifacts"] if Path(p["path"]).resolve() == path), None)
            if proof is None or file_hash(path) != proof["sha256"]:
                raise ValueError("factor scores changed after execution")
            if entry["data_fingerprint"] != index["scope"]["data_fingerprint"]:
                raise ValueError("factor belongs to another market snapshot")
            scores[entry["name"]] = pd.read_parquet(path)
    policy = AccountPolicy()
    policies = {"base": policy, "slippage_x2": replace(policy, slippage=policy.slippage * 2),
                "capacity_half": replace(policy, max_prior_day_amount_fraction=policy.max_prior_day_amount_fraction / 2)}
    accounts = {name: DailyAccount(panel, p) for name, p in policies.items()}
    cancellation = lambda: (root / "cancel.request").exists()
    observations = []
    for spec in specs:
        if spec.name not in allowed:
            continue
        targets = target_weights(panel, scores, spec, start="2016-01-01", end="2020-12-31")
        results = {}
        for stress, account in accounts.items():
            result = account.run(targets, start="2016-01-01", end="2020-12-31", cancelled=cancellation)
            summary = result["summary"]
            summary["net_excess_over_rf_growth"] = 1 + summary["return"] - (1 + policy.risk_free_rate) ** (summary["sessions"] / 252)
            summary["net_excess_after_estimated_liquidation"] = (summary["net_excess_over_rf_growth"]
                                                               - summary["terminal_liquidation_cost_estimate"] / policy.capital)
            save_account(root / "accounts" / "fit" / spec.name / stress, result)
            results[stress] = summary
        feasible = all(r["all_full_year_sharpes_available"] and r["net_excess_after_estimated_liquidation"] > 0
                       for r in results.values())
        observations.append({"candidate": spec.name, "portfolio_id": spec.portfolio_id,
                             "stress_feasible": feasible, "results": results})
        save_once(root / "accounts" / "fit" / spec.name / "result.json", observations[-1])
    eligible = [r for r in observations if r["stress_feasible"]]
    winner = max(eligible, key=lambda r: (r["results"]["base"]["mean_full_year_sharpe"],
                                         -r["results"]["base"]["turnover"])) if eligible else None
    selection = {"selected": winner["candidate"] if winner else None, "training_observations": observations,
                 "based_only_on": "2016-2020 account results and earlier registered factor evidence",
                 "frozen_before_temporal_results": True, "created_epoch": time.time(),
                 "formal_target_success": False}
    save_once(root / "selection.json", selection)
    # Stage ends with a frozen training selection. The next explicit diagnostic
    # stage can run all nine continuous years for this same arm without resetting
    # capital or retrospectively choosing a different winner.
    save_once(root / "account_stage_result.json", selection)
    return selection
