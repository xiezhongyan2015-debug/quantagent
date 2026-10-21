"""Continuous nine-year diagnostics of exactly the frozen training winner.

This is a worker entry point: the CLI's owned job supplies its hard wall limit.
No model, new selection or annual account restart occurs here. Completed native
outputs survive prefix failures. Interrupted stages require explicit recovery;
calling again never silently reruns an unfinished financial account.
"""
from __future__ import annotations

from dataclasses import asdict, fields, replace
import math
from pathlib import Path

import pandas as pd

from .portfolio import AccountPolicy, DailyAccount, PortfolioSpec, account_metrics, target_weights
from .portfolio_study import file_hash, load_panel, read, save_account
from .research import PROTOCOL, save_once

VERSION = "v6_frozen_winner_continuous_temporal_v1"
START, FIT_END, TEMPORAL_START, END = "2016-01-01", "2020-12-31", "2021-01-01", "2024-12-31"
STRESSES = ("base", "slippage_x2", "capacity_half")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _proof(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": file_hash(path), "bytes": path.stat().st_size}


def _verify(proofs):
    for proof in proofs:
        path = Path(proof["path"])
        _require(path.is_file() and file_hash(path) == proof["sha256"], "temporal input/output artifact changed: " + str(path))


def _records(frame):
    # Unknown annual Sharpe must survive strict JSON as null, never NaN/zero.
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


def _account_proofs(folder):
    folder = Path(folder).resolve()
    manifest = read(folder / "artifacts.json")
    _require({"daily.parquet", "trades.parquet", "annual.parquet", "summary.json", "policy.json"} <= set(manifest),
             "fit account artifact manifest is incomplete")
    proofs = [_proof(folder / "artifacts.json")]
    for name, expected in manifest.items():
        path = (folder / name).resolve()
        _require(path.parent == folder and path.is_file() and file_hash(path) == expected,
                 "fit account artifact changed: " + str(name))
        proofs.append(_proof(path))
    return proofs


def _prepare(root):
    """Verify all prerequisites before a diagnostic account can be dispatched."""
    selection_path = root / "selection.json"
    _require(selection_path.is_file(), "a frozen training selection is required")
    selection = read(selection_path)
    selected = selection.get("selected")
    _require(isinstance(selected, str) and bool(selected), "no frozen winner is available for temporal diagnostics")
    _require(selection.get("frozen_before_temporal_results") is True, "selection was not frozen before temporal results")
    _require(read(root / "account_stage_result.json") == selection, "training stage result does not bind the frozen selection")
    observations = [row for row in selection["training_observations"] if row["candidate"] == selected]
    _require(len(observations) == 1 and observations[0].get("stress_feasible") is True,
             "frozen winner did not pass the training stress gate")
    winner = observations[0]
    _require(read(root / "accounts/fit" / selected / "result.json") == winner,
             "frozen winner and original training result differ")
    declaration_path = root / "combination_declaration.json"
    declaration = read(declaration_path)
    candidates = [row for row in declaration["specs"] if row["name"] == selected]
    _require(len(candidates) == 1, "winner is not one unique frozen combination")
    spec = PortfolioSpec(**candidates[0])
    _require(spec.portfolio_id == winner["portfolio_id"], "winner portfolio identity changed")
    _require(file_hash(Path(__file__).with_name("portfolio.py")) == declaration["implementation_sha256"],
             "account implementation changed since the frozen combination declaration")
    execution_intent = read(root / "account_execution_intent.json")
    admission_path = root / "model_calls/02_combination_admission/admitted_receipt.json"
    _require(execution_intent["declaration_sha256"] == file_hash(declaration_path)
             and execution_intent["admission_sha256"] == file_hash(admission_path)
             and selected in execution_intent["allowed"], "training execution intent does not bind this winner")
    admission = read(admission_path)
    _require(admission.get("runtime_identity", {}).get("verified") is True,
             "original account admission lacks verified local researcher identity")
    _require(read(root / "protocol.json") == PROTOCOL, "frozen research protocol differs from the current stage")
    proofs = [_proof(path) for path in [selection_path, root / "account_stage_result.json", declaration_path,
        root / "account_execution_intent.json", admission_path, root / "protocol.json",
        root / "accounts/fit" / selected / "result.json", root / "factor_index.json"]]
    fit, policies = {}, {}
    for stress in STRESSES:
        folder = root / "accounts/fit" / selected / stress
        proofs += _account_proofs(folder)
        saved_summary, saved_policy = read(folder / "summary.json"), read(folder / "policy.json")
        _require(saved_summary == winner["results"][stress], "training summary differs from frozen selection")
        excess = saved_summary.get("net_excess_after_estimated_liquidation")
        _require(saved_summary.get("all_full_year_sharpes_available") is True and isinstance(excess, (int, float))
                 and math.isfinite(excess) and excess > 0, "original training stress is not feasible")
        policies[stress] = AccountPolicy(**saved_policy)
        fit[stress] = pd.read_parquet(folder / "daily.parquet")
    base = policies["base"]
    expected_base = {field.name: PROTOCOL["account"][field.name] for field in fields(AccountPolicy)}
    _require(asdict(base) == expected_base and base.capital == 1_000_000., "original account capital or policy changed")
    _require(policies["slippage_x2"] == replace(base, slippage=base.slippage * 2)
             and policies["capacity_half"] == replace(base, max_prior_day_amount_fraction=base.max_prior_day_amount_fraction / 2),
             "original stress policies do not match the declared independent stresses")
    panel = load_panel(root)
    _require(panel.dates.min() >= pd.Timestamp(PROTOCOL["data"]["start"]) and panel.dates.max() <= pd.Timestamp(END),
             "temporal panel may not include out-of-scope or 2025 numeric rows")
    index = read(root / "factor_index.json")
    _require(panel.fingerprint() == index["scope"]["panel_fingerprint"], "temporal panel differs from frozen factor evidence")
    for name, key in (("fit_factor_view.json", "fit_factor_view_sha256"),
                      ("factor_declaration.json", "factor_declaration_sha256")):
        _require(file_hash(root / name) == index[key], "frozen factor evidence changed: " + name)
        proofs.append(_proof(root / name))
    required = {name for name, weight in spec.factor_weights.items() if weight != 0}
    if spec.crowding_gate_factor:
        required.add(spec.crowding_gate_factor)
    scores = {}
    for name in sorted(required):
        entries = [entry for entry in index["factors"] if entry["name"] == name and entry["status"] == "evaluated"]
        _require(len(entries) == 1, "winner requires one successful frozen factor: " + name)
        entry = entries[0]
        path = Path(entry["scores_path"])
        path = (path if path.is_absolute() else root / path).resolve()
        _require(path.is_relative_to((root / "factors").resolve()), "score artifact escaped the frozen factor directory")
        matching = [p for p in entry["artifacts"] if Path(p["path"]).resolve() == path]
        _require(len(matching) == 1 and file_hash(path) == matching[0]["sha256"], "frozen factor score hash mismatch")
        _require(entry["data_fingerprint"] == index["scope"]["data_fingerprint"], "factor belongs to another data snapshot")
        scores[name] = pd.read_parquet(path)
        _require(scores[name].index.equals(panel.eligible.index) and scores[name].columns.equals(panel.eligible.columns),
                 "factor axes differ from frozen market panel")
        proofs.append(_proof(path))
    expected_fit = panel.dates[(panel.dates >= START) & (panel.dates <= FIT_END)]
    for stress, daily in fit.items():
        _require(daily.index.equals(expected_fit), "saved training daily does not cover the fixed fit calendar: " + stress)
    proofs += [_proof(Path(__file__).with_name(name)) for name in
               ("temporal.py", "portfolio.py", "portfolio_study.py", "data.py", "research.py")]
    return selection, spec, panel, scores, policies, fit, proofs


def temporal_segment(daily, policy, expected_calendar):
    """Describe a slice of one continuous account, retaining original returns."""
    prior = daily.loc[daily.index <= FIT_END]
    segment = daily.loc[(daily.index >= TEMPORAL_START) & (daily.index <= END)]
    _require(not prior.empty and not segment.empty, "continuous account lacks the fit-to-temporal boundary")
    opening_nav = float(prior.iloc[-1]["nav"])
    # This changes only the metric denominator, never account positions/cash or
    # daily return values. The first 2021 return still uses the 2020 closing NAV.
    summary, annual = account_metrics(segment, replace(policy, capital=opening_nav),
                                     start=TEMPORAL_START, end=END, expected_calendar=expected_calendar)
    summary.update({"start": TEMPORAL_START, "end": END, "opening_nav_from_2020_close": opening_nav,
                    "opening_nav_observation_date": str(prior.index[-1].date()),
                    "account_restarted": False, "returns_recomputed_or_filled": False,
                    "independent_holdout": False, "formal_target_success": False})
    return {"summary": summary, "annual": _records(annual)}


def evaluate_temporal(root):
    """Run only the frozen winner under base and its two original cost stresses."""
    root = Path(root).resolve()
    selection, spec, panel, scores, policies, fit, proofs = _prepare(root)
    intent_path = root / "temporal_intent.json"
    result_path = root / "temporal_stage_result.json"
    intent = {"version": VERSION, "selected": spec.name, "portfolio_id": spec.portfolio_id,
              "selection_sha256": file_hash(root / "selection.json"), "scope": [START, END],
              "comparison_fit_scope": [START, FIT_END], "temporal_segment": [TEMPORAL_START, END],
              "policies": {key: asdict(value) for key, value in policies.items()},
              "input_artifacts": proofs, "model_calls": 0, "winner_reselected": False,
              "annual_account_reset": False, "financial_success": False,
              "exposure": "previously_exposed_development; no 2025 numeric data"}
    if result_path.exists():
        _require(read(intent_path) == intent, "saved temporal result belongs to different frozen inputs")
        saved = read(result_path)
        _require(saved["intent_sha256"] == file_hash(intent_path), "saved temporal intent binding changed")
        _verify(saved["artifacts"])
        return saved
    _require(not intent_path.exists(), "unfinished temporal stage retained; explicit recovery required before any account rerun")
    save_once(intent_path, intent)
    cancelled = lambda: (root / "cancel.request").exists()
    observations, artifacts = {}, []
    try:
        _require(not cancelled(), "temporal stage cancellation requested before account dispatch")
        targets = target_weights(panel, scores, spec, start=START, end=END)
        for stress in STRESSES:
            _require(not cancelled(), "temporal stage cancellation requested; prior outputs retained")
            policy = policies[stress]
            account = DailyAccount(panel, policy)
            native = account.run(targets, start=START, end=END, cancelled=cancelled)
            folder = root / "accounts/temporal" / spec.name / stress
            # Persist actual full output before any diagnostic assertion. A
            # mismatch is evidence of failure, never a fabricated zero return.
            save_account(folder, native)
            expected_full = panel.dates[(panel.dates >= START) & (panel.dates <= END)]
            _require(native["daily"].index.equals(expected_full), "continuous account omitted declared calendar sessions: " + stress)
            _require(native["policy"] == asdict(policy), "continuous account returned a different capital or policy: " + stress)
            prefix = native["daily"].loc[(native["daily"].index >= START) & (native["daily"].index <= FIT_END)]
            try:
                pd.testing.assert_frame_equal(prefix, fit[stress], check_exact=True, check_like=False)
            except AssertionError as exc:
                save_once(folder / "prefix_check.json", {"passed": False, "error": str(exc)[:4000],
                          "all_fields_exact": False, "financial_validation_claimed": False})
                raise ValueError("continuous account differs from its saved training prefix: " + stress) from exc
            prefix_check = {"passed": True, "sessions": len(prefix), "columns": list(prefix.columns),
                            "all_fields_exact": True, "index_order_exact": True,
                            "comparison": "pandas assert_frame_equal check_exact=True",
                            "saved_fit_daily_sha256": file_hash(root / "accounts/fit" / spec.name / stress / "daily.parquet")}
            save_once(folder / "prefix_check.json", prefix_check)
            segment = temporal_segment(native["daily"], policy, panel.dates)
            save_once(folder / "temporal_segment.json", segment)
            summary = dict(native["summary"])
            summary["net_excess_over_rf_growth"] = 1 + summary["return"] - (1 + policy.risk_free_rate) ** (summary["sessions"] / 252)
            summary["net_excess_after_estimated_liquidation"] = (summary["net_excess_over_rf_growth"]
                                                                 - summary["terminal_liquidation_cost_estimate"] / policy.capital)
            observations[stress] = {"full_continuous_account": summary,
                                    "annual": _records(native["annual"]),
                                    "fit_prefix_check": prefix_check, "temporal_2021_2024": segment}
            artifacts += [_proof(path) for path in sorted(folder.iterdir()) if path.is_file()]
            _verify(proofs)
        result = {"version": VERSION, "status": "completed", "selected": spec.name,
                  "portfolio_id": spec.portfolio_id, "intent_sha256": file_hash(intent_path),
                  "selection_sha256": intent["selection_sha256"], "observations": observations,
                  "artifacts": artifacts, "new_account_executions": len(STRESSES), "model_calls": 0,
                  "winner_reselected": False, "annual_account_reset": False,
                  "formal_target_success": False, "financial_success": False,
                  "exposure": intent["exposure"], "independent_holdout": False}
        save_once(result_path, result)
        return result
    except Exception as exc:
        save_once(root / "temporal_failure.json", {"type": type(exc).__name__, "error": str(exc),
                  "selected": spec.name, "intent_sha256": file_hash(intent_path),
                  "completed_stresses": list(observations), "original_outputs_retained": True,
                  "financial_success": False, "automatic_retry": False})
        raise
