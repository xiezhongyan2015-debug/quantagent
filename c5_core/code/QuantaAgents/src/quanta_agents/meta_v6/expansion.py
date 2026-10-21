"""Register information expansion after the frozen first combination fails.

All preceding observations and their exposure remain part of the research
history. The model proposes factors before their values are inspected. Portfolio
applications are deliberately deferred until those factor tests are available.
"""
from pathlib import Path
from dataclasses import fields

import pandas as pd

from .factors import FactorSpec, _parse
from .library import FactorLibrary
from .portfolio import AccountPolicy
from .portfolio_study import file_hash, read
from .research import PROTOCOL, ROOT, TEXT, call_researcher, dump, object_schema, save_once

CYCLE = "03_factor_information_expansion"
CALL = "04_information_expansion_hypotheses"
PRIOR_CYCLE = "02_evidence_led_combinations"
HF0280_FIELD = "hf0280_triple_ols20_v1"
ALLOWED_FIELDS = frozenset({"open", "high", "low", "close", "volume", "amount"})
NEW_FACTOR_SCHEMA = object_schema({
    "name": TEXT, "expression": TEXT,
    "direction": {"type": "integer", "enum": [-1, 1]},
    "primary_horizon": {"type": "integer", "enum": [1, 5, 20]},
    "role": {"type": "string", "enum": ["return_prediction", "risk_information", "conditional_gate"]},
    "hypothesis": TEXT, "incremental_role": TEXT, "falsification": TEXT,
})
SCHEMA = object_schema({
    "diagnosis": TEXT, "competing_explanations": TEXT,
    "factors": {"type": "array", "maxItems": 3, "items": NEW_FACTOR_SCHEMA},
    "hf0280_review": TEXT, "application_diagnostics_to_run": TEXT,
    "future_combination_principles": TEXT, "selection_and_stopping": TEXT,
})
HF0280_SEED = {
    "name": "HF0280_增强时间重心偏离20日", "expression": HF0280_FIELD,
    "direction": 1, "primary_horizon": 20, "role": "return_prediction",
    "hypothesis": "Original calendar seed: three daily raw-value cross-sectional OLS residualizations, then a complete 20-session mean; higher preferred. Horizon20 is this stage's declaration, not an original measured result.",
    "incremental_role": "Test original intraday timing information conditional on early/late and overnight returns, distinct from price-only momentum and reversal.",
    "falsification": "Insufficient causal source coverage, unstable oriented prediction, redundancy with existing information or absent net account increment leaves the hypothesis unsupported.",
    "origin": "experiments/factor_calendar_daily_hf0280_research_sharpe15.yaml:7",
}


def _verify_artifacts(items):
    for item in items:
        path = Path(item["path"])
        if not path.is_file() or file_hash(path) != item["sha256"]:
            raise ValueError("previous research artifact changed: " + str(path))


def _verify_prior_inputs(root, items):
    """Use the archived executed sources, never demand old live files match."""
    manifest = read(root / "preparation/source_archive_before_target_serialization_fix/manifest.json")
    rows = manifest["source_files"]
    archives = {str(Path(row["original_path"]).resolve()): row for row in rows}
    if len(rows) != len(archives) or not rows:
        raise ValueError("previous source archive identities are incomplete")
    for item in items:
        path = Path(item["path"])
        if path.suffix != ".py":
            _verify_artifacts([item])
            continue
        row = archives.get(str(path.resolve()))
        saved = Path(row["archive_path"]) if row else None
        if (row is None or row["sha256"] != item["sha256"] or not saved.is_file()
                or file_hash(saved) != item["sha256"] or saved.stat().st_size != item["bytes"]):
            raise ValueError("previous executed source archive changed: " + str(path))


def _prior_factors(root, index):
    declaration = read(root / "factor_declaration.json")
    rows = declaration["declarations"]
    keys = [row["factor_key"] for row in rows]
    model_count = sum(row["origin"] == "admitted_model_hypothesis" for row in rows)
    seed_count = sum(row["origin"] == "original_seed" for row in rows)
    index_keys = [row["factor_key"] for row in index["factors"]]
    evaluated = sum(row["status"] == "evaluated" for row in index["factors"])
    if (index["factor_declaration_sha256"] != file_hash(root / "factor_declaration.json")
            or len(keys) != len(set(keys)) or len(index_keys) != len(set(index_keys))
            or set(keys) != set(index_keys) or model_count != 6 or seed_count != 3
            or len(rows) != 9 or index["factor_count"] != len(rows) or index["evaluated"] != evaluated):
        raise ValueError("previous factor declaration/index counts or identities changed")
    return rows, model_count, evaluated


def closed_observations(root):
    root = Path(root)
    folder = root / "cycles" / PRIOR_CYCLE
    selection = read(folder / "selection.json")
    result = read(folder / "temporal_stage_result.json")
    fit_intent = read(folder / "account_execution_intent.json")
    temporal_intent = read(folder / "temporal_intent.json")
    if (selection != read(folder / "account_stage_result.json")
            or selection["status"] != "completed" or result["status"] != "completed"
            or result["selected"] != selection["selected"] or result["winner_reselected"] is not False
            or result["selection_sha256"] != file_hash(folder / "selection.json")
            or selection["cumulative_portfolio_proposals"] != 6):
        raise ValueError("prior combination cycle is not consistently closed")
    base = {item.name: PROTOCOL["account"][item.name] for item in fields(AccountPolicy)}
    policies = {stress: dict(base) for stress in ("base", "slippage_x2", "capacity_half")}
    policies["slippage_x2"]["slippage"] *= 2
    policies["capacity_half"]["max_prior_day_amount_fraction"] /= 2
    if (selection["intent_sha256"] != file_hash(folder / "account_execution_intent.json")
            or result["intent_sha256"] != file_hash(folder / "temporal_intent.json")
            or fit_intent["scope"] != ["2016-01-01", "2020-12-31"]
            or temporal_intent["scope"] != ["2016-01-01", "2024-12-31"]
            or temporal_intent["fit_comparison"] != fit_intent["scope"]
            or temporal_intent["temporal_segment"] != ["2021-01-01", "2024-12-31"]
            or temporal_intent["selection_sha256"] != file_hash(folder / "selection.json")
            or temporal_intent["selected"] != selection["selected"]
            or fit_intent["policies"] != policies or temporal_intent["policies"] != policies
            or temporal_intent["winner_reselected"] is not False
            or temporal_intent["annual_account_reset"] is not False
            or result["annual_account_reset"] is not False
            or result["new_account_executions"] != 3
            or result["financial_success"] is not False or result["independent_holdout"] is not False):
        raise ValueError("prior continuous account intent, policy or scope changed")
    _verify_prior_inputs(root, fit_intent["input_artifacts"])
    _verify_prior_inputs(root, temporal_intent["input_artifacts"])
    for name in ("01_account_execution", "02_temporal_execution"):
        job = read(folder / "jobs" / name / "job_result.json")
        if job["status"] != "completed" or job["all_owned_processes_exited"] is not True or job["primary_exit_code"] != 0:
            raise ValueError("prior job is not terminal with all owned processes exited")
    _verify_artifacts(selection["artifacts"])
    _verify_artifacts(result["artifacts"])
    if set(result["observations"]) != {"base", "slippage_x2", "capacity_half"}:
        raise ValueError("prior cost stresses are incomplete")
    saved_paths = {str(Path(item["path"]).resolve()) for item in result["artifacts"]}
    fit_paths = {str(Path(item["path"]).resolve()) for item in selection["artifacts"]}
    for stress, observation in result["observations"].items():
        account = folder / "accounts/temporal" / selection["selected"] / stress
        fit = folder / "accounts/fit" / selection["selected"] / stress
        required = [account / name for name in ("prefix_check.json", "summary.json", "policy.json", "annual.parquet")]
        if not all(str(path.resolve()) in saved_paths for path in required) or str((fit / "daily.parquet").resolve()) not in fit_paths:
            raise ValueError("prior native account proofs are incomplete")
        prefix = read(account / "prefix_check.json")
        native = read(account / "summary.json")
        annual = pd.read_parquet(account / "annual.parquet").astype(object)
        annual = annual.where(pd.notna(annual), None).to_dict("records")
        if (observation["fit_prefix_check"] != prefix or prefix["all_fields_exact"] is not True
                or prefix["passed"] is not True or prefix["sessions"] != 1218
                or prefix["saved_fit_daily_sha256"] != file_hash(fit / "daily.parquet")
                or read(account / "policy.json") != policies[stress]
                or any(observation["full_continuous_account"].get(k) != v for k, v in native.items())
                or observation["full_continuous_account"]["formal_target_success"] is not False
                or observation["annual"] != annual or [row["year"] for row in annual] != list(range(2016, 2025))
                or not all(row["full_calendar_year"] is True and row["calendar_complete"] is True for row in annual)):
            raise ValueError("prior continuous account evidence is incomplete")
    return {
        "cycle": PRIOR_CYCLE, "selected": selection["selected"],
        "training_observations": selection["training_observations"],
        "temporal_observations": result["observations"],
        "selection_sha256": file_hash(folder / "selection.json"),
        "temporal_result_sha256": file_hash(folder / "temporal_stage_result.json"),
        "account_results_seen_before_this_generation": "2016-2024; therefore future reuse is exposed development",
        "previous_proposals_retained": 6, "financial_success": False,
    }


def initialize_expansion(root):
    root = Path(root).resolve()
    folder = root / "cycles" / CYCLE
    observations = closed_observations(root)
    if read(root / "protocol.json") != PROTOCOL:
        raise ValueError("original protocol changed")
    index = read(root / "factor_index.json")
    if index["fit_factor_view_sha256"] != file_hash(root / "fit_factor_view.json"):
        raise ValueError("original fit factor evidence changed")
    _, prior_model_count, evaluated = _prior_factors(root, index)
    registry = read(root / "preparation/hf0280_source_registration.json")
    if registry["market_value_arrays_read"] is not False:
        raise ValueError("auxiliary source registration is not metadata-only")
    protocol = {
        "cycle_id": CYCLE, "reason": "expand distinct factor information after frozen combination failed the complete-year objective",
        "prior_observations": {key: observations[key] for key in
            ("selection_sha256", "temporal_result_sha256", "previous_proposals_retained")},
        "prior_factors_evaluated": evaluated, "prior_model_factor_hypotheses": prior_model_count,
        "new_original_seed": HF0280_SEED, "new_model_factor_hypotheses_max": 3,
        "factor_evaluations_max": 4, "cumulative_model_factor_hypotheses_max": 9,
        "new_portfolio_proposals_before_factor_results": 0,
        "new_factor_windows_or_direction_sweeps": False,
        "numeric_scope": ["2015-01-01", "2024-12-31"],
        "model_factor_selection_view": ["2016-01-01", "2020-12-31"],
        "purge_signal_sessions_at_fit_end": 21,
        "factor_screen": PROTOCOL["factor_screen"],
        "all_data_exposure": "2015-2024 previously exposed development; prior 2021-2024 account failure is explicitly supplied to this generation",
        "independent_holdout": False, "new_2025_numeric_data_allowed": False,
        "prior_fit_view_sha256": file_hash(root / "fit_factor_view.json"),
        "prior_factor_index_sha256": file_hash(root / "factor_index.json"),
        "prior_library_snapshot_id": index["library_snapshot_id"],
        "auxiliary_registration_id": registry["registration_id"],
        "auxiliary_registration_sha256": file_hash(root / "preparation/hf0280_source_registration.json"),
        "hf0280_operator_sha256": file_hash(Path(__file__).with_name("cross_sectional.py")),
        "seed_formula_sha256": file_hash(ROOT / "experiments/factor_calendar_daily_hf0280_research_sharpe15.yaml"),
        "generation_source_sha256": {name: file_hash(Path(__file__).with_name(name)) for name in
            ("expansion.py", "factors.py", "research.py", "gateway.py")},
        "prior_source_archive_sha256": file_hash(root / "preparation/source_archive_before_target_serialization_fix/manifest.json"),
        "account_policy_and_target": "original costs, continuous capital and arithmetic mean of all complete-year net Sharpes; no changed success definition",
        "automatic_retry": False, "financial_success": False,
    }
    save_once(folder / "protocol.json", protocol)
    save_once(folder / "prior_account_observations.json", observations)
    return folder, protocol, observations


def expansion_prompt(protocol, observations, fit_view):
    return """You are the actual QuantaAgents strategy researcher, gpt-6-astra/xhigh. Return concise Chinese JSON.
The user wants the factor-calendar ideas developed into reasonable strategies, structured fast factor testing
before combination design, and mean full-year net Sharpe >1 without lookahead or hidden overfitting.
This is a NEW explicitly registered generation after a frozen candidate failed. All previous attempts remain.
The initial four A-D plans were rejected before accounts because F3/F4 were opposite in all five fit years.
The next two actual model-designed plans used F1 medium momentum and F2 short reversal, each weight0.5,
top20 equal-weight weekly next-open execution. R2 differed only by actual-holding rank40 buffer and won fit.
Below are actual saved account observations, not estimates. Respect poor years, costs, and stale inventory.
Do not infer causes just from total Sharpe or confuse high cross-sectional IC with executable strategy returns.

Diagnose the failure using competing, falsifiable explanations. Keep conditional/risk information distinct from
standalone return prediction. A market-risk or cost explanation is a hypothesis until matched diagnostics exist.
Do not reverse rejected F3/F4 after seeing signs, tune thresholds by year, sweep windows, delete bad years,
or relabel 2021-2024 as independent: their account results are now explicitly seen by THIS generation as well.
The 2025 numeric values remain excluded; earlier aggregate exposure was already known and is never erased.

The original HF0280 high-preferred seed will be evaluated at its exact fixed formula: gu_1m residualized on
intercept+rbar_up17+r_0931_1000+r_1001_1030+overnight_return; gd_1m on intercept+rbar_down17+the same return controls;
then down-residual on intercept+up-residual. Each regression is DAILY raw-value cross-sectional OLS on that
day's historical members with >=50 complete stocks, full rank. Complete trailing20 common sessions for final
residual mean. The operator has synthetic tests and independent review; NO actual HF0280 numeric results yet.
Its 626 of 648 files have all seven fields; 22 missing stocks stay on the fixed axis. Publication vintage is
not independently certified. Source registration is metadata-only so far. Never fabricate new minute features.

Propose at most THREE genuinely distinct additional causal factor hypotheses, or fewer with reasons.
Prior seeds ATR14-low and TEMA20-low had absolute-price confounding; Amihud20-high is illiquidity information.
Normalized risk or timing ideas may be new trials, with their costs and limitations declared. Prefer mechanisms
that add information over arbitrary numerical variants. The HF0280 seed is separate and must not be renamed
as your new factor. Any repeated existing expression will be registered as a duplicate, not a new discovery.
Available operands for new expressions: adjusted open/high/low/close, volume, amount only. No current price
adjustment factors/raw prices/capitalization vintages/stock IDs/calendar-year labels/forward labels as alpha.
Safe operators: +,-,*,/, comparisons, where(condition,x,y), abs, log, clip(x,lo,hi), lag(x,k), pct_change(x,k),
rolling_mean/std/min/max/sum(x,w), cs_rank(x), ema(x,w), rolling_corr(x,y,w), rolling_residual(y,x,w), ts_rank(x,w).
Windows are literal integers2..120; lag/change positive. rolling_residual is TIME-SERIES OLS, not HF0280's operator.
Explicitly assign direction(+1 higher preferred,-1 lower), primary_horizon in1/5/20, role, falsification and
incremental information role. Expressions should be simple. No portfolio is fixed until new factor results
exist. Explain useful application diagnostics, including turnover/cost/market exposure versus stock selection,
but do not claim those experiments were run. Do not change account economics or the goal to achieve a pass.
""" + "\nFROZEN NEW PROTOCOL\n" + dump(protocol) + "\nACTUAL PRIOR ACCOUNTS\n" + dump(observations) + "\nORIGINAL FIT FACTOR VIEW\n" + dump(fit_view)


def propose_expansion(root):
    root = Path(root).resolve()
    folder, protocol, observations = initialize_expansion(root)
    exposure_path = folder / "model_exposure.json"
    if not exposure_path.exists():
        with FactorLibrary(ROOT / "output/factor_library_v6.sqlite") as library:
            event = library.record_exposure(scope={"dates": protocol["numeric_scope"], "cycle": CYCLE,
                "exposure": protocol["all_data_exposure"]}, purpose="Generate new hypotheses after reading prior complete account failure",
                library_snapshot_id=protocol["prior_library_snapshot_id"], results_revealed=True,
                used_for_selection=True, metadata={"protocol_sha256": file_hash(folder / "protocol.json"),
                    "temporal_result_sha256": observations["temporal_result_sha256"], "selection_scope": "new hypotheses; previous winner unchanged"})
            save_once(exposure_path, {"event_id": event, "new_2025_numeric_values": False,
                "previous_winner_reselected": False, "model_call": CALL})
    receipt = call_researcher(root, CALL,
        expansion_prompt(protocol, observations, read(root / "fit_factor_view.json")), SCHEMA)
    if receipt.get("runtime_identity", {}).get("verified") is not True:
        raise ValueError("new hypotheses require a verified original researcher receipt")
    factors = receipt["response"]["factors"]
    if len(factors) > 3:
        raise ValueError("new hypothesis count exceeds frozen cycle")
    old_rows, prior_count, _ = _prior_factors(root, read(root / "factor_index.json"))
    old_ids = {}
    for row in old_rows:
        old_ids.setdefault(FactorSpec(row["original"]["name"], row["original"]["expression"]).factor_id, []).append(row["factor_key"])
    trial_registration, names, new_ids = [], set(), {}
    for i, factor in enumerate(factors):
        spec = FactorSpec(factor["name"], factor["expression"])
        _parse(factor["expression"], set(ALLOWED_FIELDS))
        if factor["name"] in names or factor["name"] == HF0280_SEED["name"]:
            raise ValueError("new factor names must be unique and distinct from the fixed seed")
        names.add(factor["name"])
        if type(factor["direction"]) is not int or factor["direction"] not in {-1, 1}:
            raise ValueError("explicit original factor direction required")
        if (type(factor["primary_horizon"]) is not int or factor["primary_horizon"] not in {1, 5, 20}
                or factor["role"] not in {"return_prediction", "risk_information", "conditional_gate"}):
            raise ValueError("new factor horizon or information role is outside its declaration schema")
        prior_duplicates = old_ids.get(spec.factor_id, [])
        new_duplicates = list(new_ids.get(spec.factor_id, []))
        trial_registration.append({"declaration_index": i, "factor_id": spec.factor_id,
            "duplicate_of_prior_factor_keys": prior_duplicates, "duplicate_of_new_indices": new_duplicates,
            "status": "duplicate" if prior_duplicates or new_duplicates else "new_expression",
            "new_evaluation_results_seen_before_declaration": False,
            "prior_expression_evidence_already_seen": bool(prior_duplicates),
            "counts_as_hypothesis_attempt": True})
        new_ids.setdefault(spec.factor_id, []).append(i)
    declaration = {"cycle_id": CYCLE, "fixed_seeds": [HF0280_SEED], "model_factors": factors,
        "protocol_sha256": file_hash(folder / "protocol.json"),
        "source_receipt_sha256": file_hash(root / "model_calls" / CALL / "admitted_receipt.json"),
        "prior_model_factor_hypotheses_retained": prior_count, "new_model_factor_hypotheses": len(factors),
        "cumulative_model_factor_hypotheses": prior_count + len(factors), "new_portfolio_proposals": 0,
        "trial_registration": trial_registration,
        "new_evaluation_results_seen_before_declaration": False, "financial_success": False}
    save_once(folder / "factor_declaration.json", declaration)
    return declaration
