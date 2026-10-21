"""Admit fit-only information assessments and freeze controlled applications.

Only propose_information_application explicitly invokes the researcher. The
reader/verifier and response compiler never compute scores or run an account.
Prior full account failures remain exposed; new post-2020 factor values do not
enter the researcher view. Nothing here certifies independent financial success.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import math
from pathlib import Path

from .expansion import CYCLE as FACTOR_CYCLE, CALL as FACTOR_CALL, closed_observations
from .factors import canonical_expression
from .library import FactorLibrary
from .portfolio import PortfolioSpec
from .portfolio_study import file_hash, read
from .research import PROTOCOL, ROOT, TEXT, call_researcher, dump, object_schema, save_once

VERSION = "v6_information_application_v1"
CYCLE = "04_information_application"
CALL = "05_information_application_design"
SOURCE_NAMES = ("information_application.py", "portfolio.py", "research.py", "gateway.py", "expansion.py", "library.py", "factors.py")
CONTROLS = ("components", "top_n", "membership_buffer", "weighting", "schedule", "market_filter", "crowding_gate_factor_key")
SUPPORT = ["supported_in_scope", "limited_in_scope", "unsupported_in_scope", "not_evaluable", "not_this_role"]
COMPONENT_SCHEMA = object_schema({"factor_key": TEXT, "weight": {"type": "number"}})
ASSESSMENT_SCHEMA = object_schema({
    "factor_key": TEXT, "role": {"type": "string", "enum": ["return_prediction", "risk_information", "conditional_gate"]},
    "evidence_status": {"type": "string", "enum": ["evaluated", "failed", "duplicate"]},
    "return_support": {"type": "string", "enum": SUPPORT}, "risk_support": {"type": "string", "enum": SUPPORT},
    "incremental_support": {"type": "string", "enum": ["supported_in_scope", "limited_in_scope", "unsupported_in_scope", "not_evaluable"]},
    "application": {"type": "string", "enum": ["none", "return_rank", "inverse_volatility_control"]},
    "evidence_refs": {"type": "array", "items": TEXT}, "reason": TEXT,
    "control_limitations": TEXT, "falsification": TEXT,
    "risk_review": object_schema({key: TEXT for key in ("future_vol", "future_downside", "future_entry_max_loss",
        "annual_stability", "common_sample_and_controls", "bootstrap_uncertainty")}),
})
PORTFOLIO_SCHEMA = object_schema({
    "name": TEXT, "components": {"type": "array", "minItems": 1, "maxItems": 4, "items": COMPONENT_SCHEMA},
    "top_n": {"type": "integer", "enum": [20, 40, 60]}, "membership_buffer": {"type": "integer"},
    "weighting": {"type": "string", "enum": ["equal", "inverse_volatility"]},
    "schedule": {"type": "string", "enum": ["weekly_last_session", "every_20_sessions"]},
    "market_filter": {"type": "string", "enum": ["none", "trend60", "trend120"]},
    "crowding_gate_factor_key": {"type": "string", "enum": ["", "F6"]},
    "risk_information_factor_key": TEXT, "control_against": TEXT,
    "changed_control": {"type": "string", "enum": ["baseline", *CONTROLS]},
    "hypothesis": TEXT, "information_application": TEXT, "falsification": TEXT,
})
SCHEMA = object_schema({
    "factor_assessments": {"type": "array", "items": ASSESSMENT_SCHEMA},
    "portfolios": {"type": "array", "maxItems": 3, "items": PORTFOLIO_SCHEMA},
    "old_account_failure_and_cost_diagnosis": TEXT, "competing_explanations": TEXT,
    "selection_and_stopping": TEXT, "exposure_and_limitations": TEXT,
})


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _proof(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": file_hash(path)}


def _verify(proofs):
    for proof in proofs:
        _require(Path(proof["path"]).is_file() and file_hash(proof["path"]) == proof["sha256"],
                 "frozen input changed: " + proof["path"])


def _identity(root, stage):
    folder = root / "model_calls" / stage
    receipt = read(folder / "admitted_receipt.json")
    identity = receipt.get("runtime_identity", {})
    _require(identity.get("verified") is True and identity.get("model") == "gpt-6-astra"
             and identity.get("effort") == "xhigh" and receipt.get("model") == "gpt-6-astra"
             and receipt.get("effort") == "xhigh", "researcher identity is not admitted")
    bindings = receipt.get("artifact_sha256", {})
    _require({"response.json", "runtime_session.jsonl", "prompt.txt"} <= set(bindings), "incomplete original model artifact bindings")
    proofs = [_proof(folder / "admitted_receipt.json")]
    for name, expected in bindings.items():
        path = (folder / name).resolve()
        _require(path.parent == folder.resolve() and path.is_file() and file_hash(path) == expected,
                 "original model artifact changed: " + name)
        proofs.append(_proof(path))
    _require(read(folder / "response.json") == receipt.get("response"), "saved response differs from admission")
    return receipt, proofs


def _fit_only(value):
    """Reject explicit result-date escapes even if a top-level flag is false."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "year":
                # Saved by_horizon_and_year uses year:null for the entire
                # already-purged fit period; it is not an unknown extra year.
                _require(child is None or (type(child) is int and 2016 <= child <= 2020), "non-fit factor year")
            elif key in {"date", "signal_date", "last_allowed_signal_date"}:
                _require(isinstance(child, str) and "2016-01-01" <= child[:10] <= "2020-12-31", "non-fit factor date")
            elif key == "date_range":
                _require(child == ["2016-01-01", "2020-12-31"], "non-fit factor date range")
            elif key in {"full_range_aggregates_reused", "risk_labels_registered_as_signal_fields", "2021_2024_result_values_included"}:
                _require(child is False, "forbidden factor-view result or label access")
            _fit_only(child)
    elif isinstance(value, list):
        for child in value:
            _fit_only(child)


def _cost_decomposition(observations):
    """Restate saved account totals, never invent a cost-free backtest."""
    rows = []
    keys = ("fees", "slippage", "turnover", "mean_exposure", "trade_count", "blocked_orders",
            "stale_held_sessions", "max_stale_fraction", "terminal_inventory_value", "terminal_liquidation_cost_estimate")
    for item in observations["training_observations"]:
        for stress, summary in item.get("results", {}).items():
            rows.append({"scope": "prior_fit_2016_2020", "candidate": item["candidate"], "stress": stress,
                         **{key: summary.get(key) for key in keys}})
    for stress, item in observations["temporal_observations"].items():
        summary = item["full_continuous_account"]
        rows.append({"scope": "prior_continuous_2016_2024", "candidate": observations["selected"], "stress": stress,
                     **{key: summary.get(key) for key in keys}})
    for row in rows:
        row["missing_fields"] = [key for key in keys if row[key] is None]
        row["reported_cost_total"] = (row["fees"] + row["slippage"]
            if all(type(row[key]) in (int, float) and math.isfinite(row[key]) for key in ("fees", "slippage")) else None)
    return {"rows": rows, "complete_saved_totals": bool(rows) and all(not r["missing_fields"] for r in rows),
            "limits": "Fees include recorded commission/levy aggregate; slippage is separate. Liquidation is an unexecuted estimate. Adding costs back does not reconstruct gross account returns or causal attribution."}


def _load_context(root):
    root = Path(root).resolve()
    factor_folder = root / "cycles" / FACTOR_CYCLE
    _require(read(root / "protocol.json") == PROTOCOL, "original protocol changed")
    receipt, proofs = _identity(root, FACTOR_CALL)
    index = read(factor_folder / "factor_index.json")
    _require(index.get("status") == "completed", "factor stage must be terminal before model consumption")
    jobs = [factor_folder / "jobs/01_factor_execution/job_result.json"]
    _require(all(path.is_file() for path in jobs), "factor stage requires its terminal supervised job")
    for path in jobs:
        job = read(path)
        _require(job.get("status") == "completed" and job.get("all_owned_processes_exited") is True
                 and job.get("primary_exit_code") == 0, "factor job is incomplete or has live owned children")
    fit_path = factor_folder / "fit_factor_view.json"
    _require(file_hash(fit_path) == index["fit_factor_view_sha256"], "factor fit view hash changed")
    fit = read(fit_path)
    prior_fit_path, prior_index_path = root / "fit_factor_view.json", root / "factor_index.json"
    prior_fit, prior_index = read(prior_fit_path), read(prior_index_path)
    _require(file_hash(prior_fit_path) == prior_index["fit_factor_view_sha256"]
             and len(prior_fit["factors"]) == 9 and index["prior_factors"] == prior_index["factors"]
             and fit["prior_factor_keys"] == [r["factor_key"] for r in prior_fit["factors"]]
             and [r for r in fit["factors"] if r["factor_key"] in fit["prior_factor_keys"]] == prior_fit["factors"],
             "original nine factor attempts/evidence must remain unchanged")
    scope = fit.get("scope", {})
    _require(scope.get("date_range") == ["2016-01-01", "2020-12-31"] and scope.get("purged_signal_sessions") == 21
             and fit.get("2021_2024_result_values_included") is False
             and fit.get("next_model_may_read_full_range_artifacts") is False, "factor view is not purged fit-only evidence")
    # Source paths and exposure provenance describe the full archive, not factor
    # result observations. Only actual factor diagnostics are date-inspected.
    _fit_only(fit["factors"])
    declaration = read(factor_folder / "factor_declaration.json")
    intent = read(factor_folder / "factor_execution_intent.json")
    _require(index["intent_sha256"] == file_hash(factor_folder / "factor_execution_intent.json")
             and index["factor_declaration_sha256"] == file_hash(factor_folder / "factor_declaration.json")
             and fit["factor_declaration_sha256"] == index["factor_declaration_sha256"]
             and declaration["source_receipt_sha256"] == proofs[0]["sha256"]
             and declaration["model_factors"] == receipt["response"]["factors"], "factor declaration/admission binding changed")
    originals = {item["factor_key"]: item["original"] for item in intent["declarations"]}
    rows = {item["factor_key"]: item for item in fit["factors"]}
    _require(len(rows) == len(fit["factors"]) and set(fit["new_factor_keys"]) == set(originals)
             and set(originals) == {item["factor_key"] for item in index["factors"]}, "factor identity/count mismatch")
    for result in index["factors"]:
        key = result["factor_key"]
        row, original = rows[key], originals[key]
        _require(row["status"] == result["status"] and row["status"] in {"evaluated", "failed", "duplicate"}
                 and all(row.get(k) == original[k] for k in ("name", "direction"))
                 and canonical_expression(row["expression"]) == canonical_expression(original["expression"]),
                 "factor result differs from registered identity")
        if "role" in row:
            _require(row["role"] == original["role"], "factor role changed")
        else:  # Duplicate rows retain role in the immutable declaration.
            row["role"] = original["role"]
        row["declared_primary_horizon"] = original["primary_horizon"]
    _require(index["library_snapshot_id"] == fit["library_snapshot_id"], "factor library snapshot differs")
    snapshot_path = factor_folder / "output_library_snapshot.json"
    snapshot = read(snapshot_path)
    _require(snapshot["snapshot_id"] == fit["library_snapshot_id"] and snapshot["snapshot_hash"] == fit["library_snapshot_hash"], "saved library snapshot differs")
    observations = closed_observations(root)
    _require(observations == read(factor_folder / "prior_account_observations.json"), "prior closed account observations changed")
    prior_folder = root / "cycles/02_evidence_led_combinations"
    old_declaration = read(prior_folder / "combination_declaration.json")
    old_rejection = read(root / "model_calls/02_combination_admission/admitted_receipt.json")
    _require(old_rejection.get("runtime_identity", {}).get("verified") is True
             and {d["candidate"] for d in old_rejection["response"]["decisions"]} == set("ABCD")
             and all(d["decision"] == "reject" for d in old_rejection["response"]["decisions"])
             and old_declaration["cumulative_proposals"] == observations["previous_proposals_retained"] == 6,
             "original six proposal attempts must remain explicit")
    observations = {**observations, "original_rejections": old_rejection["response"],
                    "prior_combination_declaration": old_declaration,
                    "cost_decomposition": _cost_decomposition(observations)}
    supplement_proofs = _supplements(factor_folder, fit, observations, index, jobs[0])
    metadata_paths = [root / "protocol.json", prior_fit_path, prior_index_path, fit_path, snapshot_path, *jobs,
        root / "model_calls/02_combination_admission/admitted_receipt.json", prior_folder / "combination_declaration.json",
        prior_folder / "selection.json", prior_folder / "temporal_stage_result.json"]
    metadata_paths += [factor_folder / name for name in ("factor_index.json", "factor_declaration.json", "factor_execution_intent.json", "prior_account_observations.json")]
    proofs += [_proof(path) for path in metadata_paths] + supplement_proofs
    # Hash code only, not numeric score/parquet archives. The worker's completed
    # manifest already binds those archives; they are not model input here.
    code_proofs = [p for p in index["source_proofs"] if Path(p["path"]).suffix == ".py"]
    _verify(code_proofs)
    proofs += code_proofs
    return root, fit, observations, proofs


def _supplements(folder, fit, observations, index, job_path):
    """Require the saved post-result intervals and actual-path annual costs."""
    cost_protocol_path = folder / "account_cost_attribution_protocol.json"
    cost_path = folder / "prior_account_cost_attribution.json"
    cost_protocol, cost = read(cost_protocol_path), read(cost_path)
    _require(cost["protocol_sha256"] == file_hash(cost_protocol_path)
             and cost.get("financial_success") is False and cost.get("new_account_executions") == 0,
             "saved cost attribution protocol/result binding changed")
    expected = {(view, year) for view, end in (("R1_fit", 2020), ("R2_fit", 2020), ("R2_continuous", 2024))
                for year in range(2016, end + 1)}
    _require(len(cost["annual"]) == len(expected) and {(r["view"], r["year"]) for r in cost["annual"]} == expected,
             "actual-path cost attribution must retain all nineteen account years")
    _verify(cost["sources"])
    observations["annual_cost_attribution"] = {"protocol": cost_protocol, "result": cost}
    protocol_path, result_path = folder / "return_ic_bootstrap_protocol.json", folder / "return_ic_bootstrap.json"
    protocol, result = read(protocol_path), read(result_path)
    _require(result.get("status") == "completed" and result["protocol_sha256"] == file_hash(protocol_path)
             and result["factor_index_sha256"] == file_hash(folder / "factor_index.json")
             and result["fit_factor_view_sha256"] == index["fit_factor_view_sha256"]
             and result["factor_job_result_sha256"] == file_hash(job_path), "return IC supplement lacks completed upstream binding")
    _require(result["scope"] == protocol["scope"] and result["method"] == protocol["method"]
             and result["source_proofs"] == protocol["source_proofs"]
             and result["proposal_timing"] == protocol["proposal_timing"], "return IC supplement differs from its declaration")
    scope, method, timing = result["scope"], result["method"], result["proposal_timing"]
    _require(scope["date_range"] == ["2016-01-01", "2020-12-02"] and scope["fit_signal_sessions"] == 1197
             and scope["new_2021_2024_values_read"] is False and scope["2025_values_read"] is False
             and all(method[k] == v for k, v in {"block_length": 20, "repetitions": 1000, "seed": 20260909, "confidence": .95, "horizons": [1, 5, 20]}.items())
             and timing["post_hoc_supplement"] is True and timing["pre_results_preregistration_claimed"] is False,
             "return IC intervals changed scope, fixed method or post-result timing")
    mapping = {r["factor_key"]: r for r in fit["factors"]}
    _require(result["factor_count"] == len(result["factors"]) == len(mapping)
             and {r["factor_key"] for r in result["factors"]} == set(mapping), "supplement dropped factor attempts")
    for row in result["factors"]:
        original = mapping[row["factor_key"]]
        _require(row["direction"] == original["direction"] and row["source_factor_status"] == original["status"],
                 "supplement changed direction or trial status")
    _verify(result["source_proofs"])
    # Include every result row and limitation; only file-proof verbosity and the
    # 1197 literal calendar dates stay in their hash-bound original artifacts.
    fit["return_ic_bootstrap_supplement"] = {k: deepcopy(v) for k, v in result.items() if k not in {"source_proofs", "runtime"}}
    fit["return_ic_bootstrap_supplement"]["scope"].pop("fit_calendar_dates", None)
    return [_proof(p) for p in (cost_protocol_path, cost_path, protocol_path, result_path)] + cost["sources"] + result["source_proofs"]


def _protocol(fit, observations, proofs):
    return {"version": VERSION, "cycle_id": CYCLE, "model_call": CALL,
        "factor_model_scope": ["2016-01-01", "2020-12-31"], "purged_signal_sessions": 21,
        "prior_account_observation_scope": ["2016-01-01", "2024-12-31"],
        "all_data_exposure": "2015-2024 previously exposed development; all earlier hypotheses, rejected portfolios and account failures retained",
        "new_2021_2024_factor_values_allowed": False, "new_2025_numeric_data_allowed": False,
        "independent_holdout": False, "new_portfolio_proposals_max": 3, "prior_proposals_retained": 6,
        "library_snapshot_id": fit["library_snapshot_id"], "library_snapshot_hash": fit["library_snapshot_hash"],
        "new_factor_directions_allowed": False, "window_sweeps_allowed": False,
        "top_n": [20, 40, 60], "membership_buffer": "0 or 2*top_n", "weighting": ["equal", "inverse_volatility"],
        "schedule": ["weekly_last_session", "every_20_sessions"],
        "schedule_semantics": "Weekly final scheduled session close, or fixed20 sessions anchored at the session preceding account start; execution at next open. Fixed20 is a newly declared application trial, not optimized or calendar-month equivalent.",
        "market_filter": ["none", "trend60", "trend120"], "crowding_gate": ["", "F6"],
        "control_design": "First proposal is baseline; each later proposal changes exactly one declared control relative to it. Coupled top_n and buffer=2N changes count separately.",
        "risk_application": "F7 only through existing inverse_volatility weighting (20-session daily close-return std, ddof1); never a ranking component, return predictor or market-timing proof. Require an otherwise identical equal-weight baseline.",
        "risk_weighting_cash": "Stock weights are clipped at .05 without redistributing excess; especially top20 may reduce total exposure. The matched contrast measures the whole risk-weighting application, not pure cross-sectional diversification.",
        "holding_horizon": "Fixed20sessions is not automatically compatible with short-horizon information such as F2 primary5; explain horizon mismatch in hypothesis and falsification instead of extending holding solely to reduce costs.",
        "risk_rules": "All portfolio controls remain untested application hypotheses. High exposure or future-risk association does not establish trend-filter timing information.",
        "partial_controls": "May support a limited application hypothesis; cannot claim complete incremental evidence. Failed/duplicate factors are retained and unavailable as new active discoveries.",
        "max_components": 4, "capital": 1000000., "gross_exposure": 1., "max_stock_weight": .05,
        "account_policy": deepcopy(PROTOCOL["account"]), "evaluation": deepcopy(PROTOCOL["evaluation"]),
        "selection": "Highest mean all-complete-year net Sharpe on 2016-2020 among candidates passing base, slippage_x2 and capacity_half after estimated liquidation; lower turnover tie-break, then declaration order. Freeze winner before new 2021-2024 account diagnostics; no reselect or yearly reset.",
        "financial_success": False, "execution_certified": False, "automatic_retry": False,
        "new_model_calls_max": 1, "input_artifacts": proofs,
        "source_sha256": {name: file_hash(Path(__file__).with_name(name)) for name in SOURCE_NAMES}}


def initialize_application(root):
    root, fit, observations, proofs = _load_context(root)
    folder = root / "cycles" / CYCLE
    protocol = _protocol(fit, observations, proofs)
    save_once(folder / "protocol.json", protocol)
    save_once(folder / "admitted_fit_factor_view.json", fit)
    save_once(folder / "prior_account_observations.json", observations)
    return folder, protocol, fit, observations


def application_prompt(protocol, fit, observations):
    return """You are the actual QuantaAgents researcher gpt-6-astra/xhigh. Return concise Chinese JSON.
Assess information BEFORE proposing applications. Every new_factor_key, including failed/duplicate attempts,
needs exactly one factor assessment with its original role. The supplied original account failures and saved
fees/slippage/turnover/capacity/stale-inventory/terminal-cost observations are mandatory evidence, not optional
background. Separate measured observations from causal explanations and untested application hypotheses.

All new numerical factor evidence is purged 2016-2020. Do not read files or request new 2021-2024 factor tables,
archives, labels, account results or 2025 data. Earlier 2016-2024 ACCOUNT failures are already exposed and supplied.
All 2015-2024 remains exposed development; seed identity, another cycle or bootstrap does not reset selection
bias or provide independent market evidence. Preserve original signs, horizons and all previous attempts.

F7 is risk_information. Judge future_vol, future_downside and future_entry_max_loss with the COMPLETE supplied
risk fit summary, every year, quantiles, common samples, controls and fixed date-block confidence intervals.
Return IC alone cannot reject this role, and positive risk prediction is not expected-return prediction.
Use evidence_refs as exact JSON pointers into FIT FACTOR VIEW (e.g. /factors/10/risk_information/annual).
For evaluated risk include pointers to summary, annual, bootstrap, quantiles, common_sample and comparison.
Partial/missing controls prohibit a supported complete-increment claim, but may motivate a limited hypothesis.
Residual/incremental IC is a descriptive statistic, not a generated or validated tradable residual score.
Only saved raw factor scores and their original direction may enter portfolios; never turn a negative-control
regression interpretation into a direction flip, or use a residual statistic as a new alpha operand.
The saved return-IC block intervals are a post-result descriptive supplement proposed after root saw HF0280's
fit mean, not before-results registration or multiple-testing correction. Missing/failed/residual-CI gaps stay
visible. F7 is still judged by its separate risk evidence, not rejected solely on return IC or its interval.
Failed or duplicate attempts cannot be active new discoveries. Unsupported or missing evidence permits zero
applications. Old F3/F4 rejected mechanisms cannot be silently restored as ranking components.

Propose zero to THREE simple applications using registered evaluated information and original signs only.
Components: at most four distinct return-ranking factors, positive weights summing to one. Risk factors NEVER
enter components. F7 may ONLY motivate the existing inverse_volatility stock weighting using fixed trailing20
close-return std; name it with risk_information_factor_key=F7 and include an otherwise identical equal baseline.
Do not invent a risk tilt, new gate, direction/window scan or special years/stocks. No F7 market-timing claim.
Optional original crowding gate is F6 only. Top20/40/60, buffer0 or2N, weekly or fixed20session schedule, and
none/trend60/trend120 filters are the complete controls. Each later proposal changes EXACTLY ONE control from
the first; control_against is the first proposal name, changed_control names the sole field. The first uses
control_against='' and changed_control='baseline'. Its name is unique. market_filter is an unverified risk-rule
trial; prior high market exposure/return volatility alone cannot establish useful market timing.
Inverse-volatility weights are capped at .05 with no redistribution: leftover capital remains cash, especially
with top20. The contrast is the whole weighting application, not pure cross-sectional diversification. A fixed20
schedule may mismatch F2's primary5 information; address this in hypothesis/falsification, not only cost savings.

Explain mechanism, matched comparison and falsification without inventing account results. Original costs,
continuous capital, all losing years and fixed success criteria remain. A new declaration is not an executed
account, and exposed-factor fit support is not financial success.
""" + "\nFROZEN APPLICATION PROTOCOL\n" + dump(protocol) + "\nACTUAL OLD ACCOUNT FAILURE AND COSTS\n" + dump(observations) + "\nFIT FACTOR VIEW\n" + dump(fit)


def _schema(value, schema, path="response"):
    kind = schema["type"]
    if kind == "object":
        _require(isinstance(value, dict) and set(value) == set(schema["properties"]), path + " has missing/extra fields")
        for key, child in schema["properties"].items():
            _schema(value[key], child, path + "." + key)
    elif kind == "array":
        _require(isinstance(value, list) and schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000), path + " invalid list size")
        for child in value:
            _schema(child, schema["items"], path)
    elif kind == "string":
        _require(isinstance(value, str), path + " must be text")
    elif kind in {"number", "integer"}:
        _require(type(value) in ((int,) if kind == "integer" else (int, float)) and math.isfinite(value), path + " invalid number")
    if "enum" in schema:
        _require(value in schema["enum"], path + " outside declared choices")


def _pointer(fit, pointer):
    _require(pointer.startswith("/factors/"), "evidence must refer to fit factor diagnostics")
    value = fit
    try:
        for part in pointer.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError("unresolved fit evidence pointer") from exc
    return value


def validate_application_response(response, fit, protocol):
    """Pure exact compiler for researcher assessments; no financial execution."""
    _schema(response, SCHEMA)
    mapping = {row["factor_key"]: row for row in fit["factors"]}
    assessments = {item["factor_key"]: item for item in response["factor_assessments"]}
    _require(len(assessments) == len(response["factor_assessments"]) and set(assessments) == set(fit["new_factor_keys"]), "every new attempt requires one role assessment")
    for key, item in assessments.items():
        factor = mapping[key]
        _require(item["role"] == factor["role"] and item["evidence_status"] == factor["status"], "role or evidence status rewritten")
        index = fit["factors"].index(factor)
        prefix = f"/factors/{index}/"
        for pointer in item["evidence_refs"]:
            _require(pointer.startswith(prefix), "assessment cites another factor")
            _pointer(fit, pointer)
        _require(all(item[field].strip() for field in ("reason", "control_limitations", "falsification")), "assessment reasoning is required")
        if factor["status"] != "evaluated":
            _require(item["application"] == "none" and item["incremental_support"] == "not_evaluable"
                     and item["return_support"] in {"not_evaluable", "not_this_role"}
                     and item["risk_support"] in {"not_evaluable", "not_this_role"}, "failed/duplicate factor cannot be admitted as new evidence")
            continue
        _require(bool(item["evidence_refs"]), "evaluated assessment requires fit citations")
        if factor.get("comparison_complete") is not True:
            _require(item["incremental_support"] != "supported_in_scope", "partial controls cannot certify complete increment")
        if factor["role"] == "risk_information":
            risk = factor.get("risk_information", {})
            required = {prefix + "risk_information/" + name for name in ("summary", "annual", "bootstrap", "quantiles", "common_sample", "comparison")}
            _require(risk.get("status") == "evaluated" and required <= set(item["evidence_refs"])
                     and all(value.strip() for value in item["risk_review"].values()), "risk role needs its complete risk fit review, not only return IC")
            if risk["comparison"].get("comparison_complete") is not True:
                _require(item["incremental_support"] != "supported_in_scope", "partial risk controls cannot certify complete increment")
            _require(item["application"] in {"none", "inverse_volatility_control"}, "risk information cannot become return ranking")
            if item["application"] != "none":
                _require(key == "F7" and canonical_expression(factor["expression"]) == canonical_expression("rolling_std(pct_change(close,1),20)")
                         and factor["direction"] == -1 and factor["declared_primary_horizon"] == 20
                         and item["risk_support"] in {"supported_in_scope", "limited_in_scope"}, "only the original F7 fixed20 risk-weighting comparison is supported")
        elif item["application"] != "none":
            _require(factor["role"] == "return_prediction" and item["application"] == "return_rank"
                     and item["return_support"] in {"supported_in_scope", "limited_in_scope"}, "application lacks its declared information support")
    proposals, specs, records, economic_ids = response["portfolios"], [], [], set()
    _require(len(proposals) <= protocol["new_portfolio_proposals_max"], "proposal allocation exceeded")
    names = [p["name"] for p in proposals]
    _require(len(set(names)) == len(names) and all(n.strip() for n in names), "unique proposal names required")
    normalized = []
    for i, proposal in enumerate(proposals):
        top, buffer = proposal["top_n"], proposal["membership_buffer"]
        _require(buffer in {0, 2 * top}, "invalid holding buffer")
        components, weights = proposal["components"], {}
        _require(len({c["factor_key"] for c in components}) == len(components), "duplicate components")
        _require(math.isclose(sum(c["weight"] for c in components), 1., rel_tol=0., abs_tol=1e-10), "component weights must sum to one")
        for component in components:
            key, weight = component["factor_key"], component["weight"]
            _require(key in mapping and key not in {"F3", "F4"} and 0 < weight <= 1, "unregistered/rejected/direction-reversed component")
            factor = mapping[key]
            _require(factor["status"] == "evaluated" and factor.get("role") != "risk_information", "unavailable or risk-only factor cannot rank stocks")
            if key in assessments:
                _require(assessments[key]["application"] == "return_rank", "new factor not admitted for return ranking")
            _require(factor["name"] not in weights and type(factor["direction"]) is int and factor["direction"] in {-1, 1}, "ambiguous factor identity/direction")
            weights[factor["name"]] = weight * factor["direction"]
        gate = proposal["crowding_gate_factor_key"]
        _require(not gate or (gate in mapping and mapping[gate]["status"] == "evaluated"), "original gate unavailable")
        risk_key = proposal["risk_information_factor_key"]
        _require(proposal["weighting"] != "inverse_volatility" or risk_key == "F7",
                 "inverse volatility requires the explicit F7 supported-risk/equal-baseline comparison")
        _require(not risk_key or (risk_key == "F7" and risk_key in assessments
            and assessments[risk_key]["application"] == "inverse_volatility_control" and proposal["weighting"] == "inverse_volatility"), "unregistered risk application")
        control = {key: deepcopy(proposal[key]) for key in CONTROLS}
        control["components"] = sorted(components, key=lambda c: c["factor_key"])
        if i == 0:
            _require(proposal["control_against"] == "" and proposal["changed_control"] == "baseline", "first proposal must be explicit baseline")
        else:
            differences = [key for key in CONTROLS if control[key] != normalized[0][key]]
            _require(proposal["control_against"] == names[0] and differences == [proposal["changed_control"]], "comparison changes more than one control or duplicates baseline")
        if risk_key:
            _require(i > 0 and proposal["changed_control"] == "weighting" and proposals[0]["weighting"] == "equal", "F7 application requires an otherwise identical equal-weight baseline")
        normalized.append(control)
        economic_id = hashlib.sha256(dump(control).encode()).hexdigest()
        _require(economic_id not in economic_ids, "duplicate economic application")
        economic_ids.add(economic_id)
        spec = PortfolioSpec(name="I" + str(i + 1), factor_weights=weights, top_n=top, gross_exposure=1., max_stock_weight=.05,
            weighting=proposal["weighting"], market_filter=proposal["market_filter"], membership_buffer=buffer,
            rebalance_schedule="sessions" if proposal["schedule"] == "every_20_sessions" else "weekly_last_session",
            rebalance_sessions=20 if proposal["schedule"] == "every_20_sessions" else 5,
            crowding_gate_factor=mapping[gate]["name"] if gate else "",
            metadata={"model_name": proposal["name"], "hypothesis": proposal["hypothesis"], "falsification": proposal["falsification"],
                "information_application": proposal["information_application"], "risk_information_factor_key": risk_key,
                "control_against": "I1" if i else "", "changed_control": proposal["changed_control"],
                "all_controls_are_untested_applications": True, "cycle_id": CYCLE, "source_call": CALL})
        specs.append(asdict(spec))
        records.append({"candidate": spec.name, "economic_application_id": economic_id, "proposal": deepcopy(proposal),
                        "counts_as_new_attempt": True, "account_executed": False})
    return {"specs": specs, "factor_assessments": deepcopy(response["factor_assessments"]), "proposal_records": records}


def _declarations(root, folder, protocol, fit, response):
    compiled = validate_application_response(response, fit, protocol)
    common = {"version": VERSION, "cycle_id": CYCLE, "protocol_sha256": file_hash(folder / "protocol.json"),
        "source_receipt_sha256": file_hash(root / "model_calls" / CALL / "admitted_receipt.json"),
        "implementation_sha256": file_hash(Path(__file__).with_name("portfolio.py")), "source_sha256": protocol["source_sha256"],
        "input_artifacts": protocol["input_artifacts"], "fit_factor_view_sha256": file_hash(folder / "admitted_fit_factor_view.json"),
        "original_expansion_fit_view_sha256": file_hash(root / "cycles" / FACTOR_CYCLE / "fit_factor_view.json"),
        "library_snapshot_id": fit["library_snapshot_id"], "prior_proposals_retained": 6,
        "new_proposals": len(compiled["specs"]), "cumulative_proposals": 6 + len(compiled["specs"]),
        "old_account_results_used_for_design": True, "new_2021_2024_factor_results_used": False,
        "all_prior_attempts_retained": True, "financial_success": False, "account_executions": 0}
    application = {**common, "factor_assessments": compiled["factor_assessments"], "proposal_records": compiled["proposal_records"],
                   "researcher_diagnosis": {k: response[k] for k in response if k not in {"portfolios", "factor_assessments"}}}
    # Match save_once's exact encoding before the file exists.
    application_hash = hashlib.sha256(dump(application).encode("utf-8")).hexdigest()
    combination = {**common, "specs": compiled["specs"], "application_declaration_sha256": application_hash}
    return application, combination


def _application_receipt(root, protocol, fit, observations):
    receipt, _ = _identity(root, CALL)
    prompt = (root / "model_calls" / CALL / "prompt.txt").read_text(encoding="utf-8")
    _require(prompt == application_prompt(protocol, fit, observations), "call05 prompt is not the frozen evidence/protocol view")
    _require(protocol["source_sha256"] == {name: file_hash(Path(__file__).with_name(name)) for name in SOURCE_NAMES},
             "application source changed after declaration")
    return receipt


def propose_information_application(root):
    """Explicit model boundary. Existing paid stages are recovered, never retried."""
    root = Path(root).resolve()
    folder, protocol, fit, observations = initialize_application(root)
    exposure_path = folder / "model_exposure.json"
    if not exposure_path.exists():
        with FactorLibrary(ROOT / "output/factor_library_v6.sqlite") as library:
            snapshot = library.load_snapshot(fit["library_snapshot_id"])
            _require(snapshot["snapshot_hash"] == fit["library_snapshot_hash"], "live library snapshot changed")
            event = library.record_exposure(library_snapshot_id=fit["library_snapshot_id"],
                scope={"date_range": protocol["factor_model_scope"], "cycle": CYCLE, "exposure": protocol["all_data_exposure"]},
                purpose="Information-role assessment and controlled application after prior account failure",
                results_revealed=True, used_for_selection=True,
                metadata={"protocol_sha256": file_hash(folder / "protocol.json"), "model_call": CALL,
                          "prior_account_scope": protocol["prior_account_observation_scope"], "new_2025_values": False})
        save_once(exposure_path, {"event_id": event, "library_snapshot_id": fit["library_snapshot_id"], "model_call": CALL})
    receipt = call_researcher(root, CALL, application_prompt(protocol, fit, observations), SCHEMA)
    admitted = _application_receipt(root, protocol, fit, observations)
    _require(receipt == admitted, "returned researcher receipt differs from saved admission")
    _verify(protocol["input_artifacts"])
    try:
        application, combination = _declarations(root, folder, protocol, fit, receipt["response"])
    except Exception as exc:
        save_once(folder / "application_validation_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "source_receipt_sha256": file_hash(root / "model_calls" / CALL / "admitted_receipt.json"),
            "all_proposals_retained_in_original_receipt": True, "automatic_retry": False, "account_executions": 0})
        raise
    save_once(folder / "application_declaration.json", application)
    save_once(folder / "combination_declaration.json", combination)
    return combination


def verify_application_declaration(root):
    """Read-only account handoff: recompile exact saved call05, never call it."""
    root, fit, observations, proofs = _load_context(root)
    folder = root / "cycles" / CYCLE
    protocol = read(folder / "protocol.json")
    _require(protocol == _protocol(fit, observations, proofs), "application protocol/source/input binding changed")
    _require(read(folder / "admitted_fit_factor_view.json") == fit
             and read(folder / "prior_account_observations.json") == observations, "saved model view changed")
    receipt = _application_receipt(root, protocol, fit, observations)
    application, combination = _declarations(root, folder, protocol, fit, receipt["response"])
    _require(read(folder / "application_declaration.json") == application
             and file_hash(folder / "application_declaration.json") == combination["application_declaration_sha256"]
             and read(folder / "combination_declaration.json") == combination, "application transcription differs from admitted model output")
    return folder, protocol, combination, fit
