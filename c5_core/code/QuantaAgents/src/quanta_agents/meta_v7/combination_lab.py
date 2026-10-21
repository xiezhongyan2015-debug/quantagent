"""Frozen, expanding-window combination research over the existing account engine.

Each fold fits on its own information prefix, then executes separate validation
accounts. These are exposed development folds, not independent research history.
No fold result chooses a method, lambda, direction, or the final full-train fit.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.portfolio import AccountPolicy
from quanta_agents.research_kernel.compiler import validate_strategy
from quanta_agents.research_kernel.store import clean, digest, exclusive_lock
from .execution import execute_strategy, execution_pool
from .temporal import scope_panel, scope_frames

VERSION = "v9_combination_lab_v1"
_TABLES = ("daily", "trades", "targets", "annual")


def _date(value):
    day = pd.Timestamp(value)
    if pd.isna(day) or day.tz is not None or day != day.normalize():
        raise ValueError("finite timezone-free session date required")
    return day


def _fold_plan(dates, train_start, train_end, folds, horizon):
    start, end = _date(train_start), _date(train_end)
    if start >= end or type(horizon) is not int or not 1 <= horizon <= 2520:
        raise ValueError("ordered training range and bounded positive horizon required")
    if (not isinstance(dates, pd.DatetimeIndex) or dates.empty or dates.tz is not None
            or dates.hasnans or not dates.is_unique or not dates.is_monotonic_increasing):
        raise ValueError("ordered unique daily panel calendar required")
    if end > dates[-1] or start < dates[0]:
        raise ValueError("training range is not covered by panel")
    if not isinstance(folds, (list, tuple)) or not 1 <= len(folds) <= 32:
        raise ValueError("provide 1..32 explicit expanding-window folds")
    result, last_validation, last_fit = [], None, None
    for item in folds:
        if not isinstance(item, dict) or set(item) != {"fit_end", "validation_start", "validation_end"}:
            raise ValueError("fold requires exactly fit_end, validation_start, validation_end")
        fit, vs, ve = (_date(item[k]) for k in ("fit_end", "validation_start", "validation_end"))
        if not start < fit < vs <= ve <= end:
            raise ValueError("fold fit/validation must be ordered and within training end")
        if last_validation is not None and (vs <= last_validation or fit <= last_fit):
            raise ValueError("folds must have increasing fit cutoffs and nonoverlapping validation ranges")
        fit_dates = dates[(dates >= start) & (dates <= fit)]
        valid_dates = dates[(dates >= vs) & (dates <= ve)]
        if len(fit_dates) <= horizon + 1 or valid_dates.empty or dates.get_indexer([valid_dates[0]])[0] == 0:
            raise ValueError("fold has insufficient fit labels or validation sessions")
        result.append({k: _date(v).date().isoformat() for k, v in item.items()})
        last_fit, last_validation = fit, ve
    return start.date().isoformat(), end.date().isoformat(), result


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError("combination lab cancelled")


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(clean(value), stream, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)


def _immutable_json(path, value):
    value = clean(value)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError("saved frozen document differs: " + path.name)
    else:
        _write(path, value)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sources():
    here = Path(__file__).resolve()
    package = here.parent.parent
    paths = [here, *[here.with_name(x) for x in ("combination.py", "attribution.py", "temporal.py", "execution.py")],
             package / "research_kernel/compiler.py", package / "research_kernel/execution.py",
             package / "meta_v6/portfolio.py"]
    return {str(p.relative_to(package)): _sha(p) for p in paths}


def _frame_identity(frames):
    result = {}
    for name, frame in sorted(frames.items()):
        h = hashlib.sha256()
        h.update(json.dumps(list(frame.columns), ensure_ascii=False).encode())
        h.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
        result[name] = h.hexdigest()
    return result


def _labels(panel, horizon):
    opening = panel.fields["open"].where(lambda x: np.isfinite(x) & x.gt(0))
    if "open_observed" in panel.fields:
        opening = opening.where(panel.fields["open_observed"].eq(1))
    value = opening.shift(-horizon - 1) / opening.shift(-1) - 1
    return value.where(np.isfinite(value))


def _fit(panel, frames, start, end, horizon, ridge_lambda, selection, pairs, return_candidate_ids):
    from .combination import fit_combinations
    scoped = scope_panel(panel, end=end)
    scores = scope_frames(frames, scoped)
    # Labels are created only after the numeric prefix has been isolated.
    return fit_combinations(scores, execution_pool(scoped), _labels(scoped, horizon),
        train_start=start, train_end=end, horizon_sessions=horizon,
        ridge_lambda=ridge_lambda, selection=selection, interaction_pairs=pairs,
        return_candidate_ids=return_candidate_ids)


def _specs(fit, allocation):
    from .combination import compile_to_strategy_expr
    return {method: validate_strategy({"name": "v9_" + method,
        "score": compile_to_strategy_expr(fit, model=method),
        "allocation": deepcopy(allocation or {})}) for method in fit["models"]}


def _error(exc):
    return {"type": type(exc).__name__, "message": str(exc),
            **({"fit_audit": clean(exc.audit)} if hasattr(exc, "audit") else {})}


def _read_account(folder, expected_identity):
    """Reuse only an exact, complete manifest; a corrupted success fails closed."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    required = {"account.json", "frozen.json", *(key + ".parquet" for key in _TABLES)}
    if (manifest.get("version") != VERSION or manifest.get("identity") != expected_identity
            or set(manifest.get("files", {})) != required):
        raise ValueError("saved account identity or file set differs")
    for name, expected in manifest["files"].items():
        if _sha(folder / name) != expected:
            raise ValueError("saved account artifact hash differs: " + name)
    if digest(json.loads((folder / "frozen.json").read_text(encoding="utf-8"))) != expected_identity:
        raise ValueError("saved frozen account binding differs")
    account = json.loads((folder / "account.json").read_text(encoding="utf-8"))
    attrs = account.pop("table_attributes")
    for key in _TABLES:
        account[key] = pd.read_parquet(folder / (key + ".parquet"))
        account[key].attrs = attrs[key]
    return account


def _retain(account, folder, identity, frozen):
    if any(not isinstance(account.get(key), pd.DataFrame) for key in _TABLES):
        raise ValueError("account omitted required raw daily/trades/targets/annual table")
    plain = {key: value for key, value in account.items() if key not in _TABLES}
    plain["table_attributes"] = {key: account[key].attrs for key in _TABLES}
    for key in _TABLES:
        value = account[key].copy(deep=False)
        value.attrs = {}
        value.to_parquet(folder / (key + ".parquet"))
    _write(folder / "account.json", plain)
    _write(folder / "frozen.json", frozen)
    files = {name: _sha(folder / name) for name in
             ["account.json", "frozen.json", *(key + ".parquet" for key in _TABLES)]}
    _write(folder / "manifest.json", {"version": VERSION, "identity": identity, "files": files})


def _account_record(account, folder, identity, reused):
    return {"status": "completed", "identity": identity, "reused": reused,
            "summary": clean(account["summary"]), "raw_retained": folder is not None,
            "artifact_dir": str(folder) if folder else None,
            "manifest_sha256": _sha(folder / "manifest.json") if folder else None}


def _run_account(folder, identity, frozen, phase, run, before_account, cancelled, on_event):
    if folder is not None:
        if (folder / "manifest.json").exists():
            account = _read_account(folder, identity)
            return _account_record(account, folder, identity, True), account
        if folder.exists():
            # An execution may have started before a process interruption. Never
            # reset its budget or overwrite its partial artifacts automatically.
            failure = folder / "failure.json"
            if failure.exists():
                old = json.loads(failure.read_text(encoding="utf-8"))
                if old.get("identity") != identity:
                    raise ValueError("saved failed account identity differs")
                return {**old, "reused": True}, None
            if (folder / "started.json").exists():
                prior = json.loads((folder / "started.json").read_text(encoding="utf-8"))
                if prior.get("identity") != identity:
                    raise ValueError("saved incomplete account identity differs")
            return {"status": "incomplete_previous_attempt", "identity": identity,
                    "reused": True, "artifact_dir": str(folder), "summary": None}, None
        folder.mkdir(parents=True, exist_ok=False)
    reserved, started, terminal_status = False, False, "failed"
    try:
        _cancel(cancelled)
        if before_account is not None and before_account(phase, identity) is False:
            raise RuntimeError("account budget reservation refused")
        reserved = True
        if folder is not None:
            _write(folder / "started.json", {"identity": identity, "phase": phase,
                    "budget_reserved": True, "frozen": frozen})
        if on_event is not None:
            on_event({"event": "account_started", "phase": phase, "identity": identity})
        started = True
        account = run()
        _cancel(cancelled)
        if folder is not None:
            _retain(account, folder, identity, frozen)
        terminal_status = "completed"
        return _account_record(account, folder, identity, False), account
    except Exception as exc:
        terminal_status = "cancelled" if isinstance(exc, InterruptedError) else "failed"
        record = {"status": "cancelled" if isinstance(exc, InterruptedError) else "failed",
            "identity": identity, "reused": False, "budget_reserved": reserved,
            "execution_started": started, "summary": None, "error": _error(exc),
            "artifact_dir": str(folder) if folder else None}
        if folder is not None:
            _write(folder / "failure.json", record)
        if isinstance(exc, InterruptedError):
            raise
        return record, None
    finally:
        if on_event is not None:
            on_event({"event": "account_finished", "phase": phase, "identity": identity,
                      "status": terminal_status, "budget_reserved": reserved,
                      "execution_started": started})


def _method_summary(folds, methods):
    output = {}
    for method in methods:
        rows = [fold["accounts"][method] for fold in folds]
        completed = [row for row in rows if row["status"] == "completed"]
        metrics = {}
        for key in ("return", "sharpe", "max_drawdown", "mean_exposure", "turnover"):
            values = [row["summary"].get(key) for row in completed]
            values = [float(value) for value in values if isinstance(value, (int, float)) and np.isfinite(value)]
            metrics[key] = {"arithmetic_mean_across_separate_accounts": float(np.mean(values)) if values else None,
                            "available_folds": len(values)}
        output[method] = {"declared_folds": len(rows), "completed_folds": len(completed),
            "failed_or_unavailable_folds": len(rows) - len(completed), "metrics": metrics,
            "fold_statuses": [row["status"] for row in rows], "continuous_equity_curve_created": False}
    return output


def evaluate_combination_lab(panel, frames, *, train_start, train_end, folds,
        horizon_sessions=5, ridge_lambda=.1, selection=None, interaction_pairs=None,
        allocation=None, policy=None, artifact_dir=None, before_account=None,
        cancelled=None, on_event=None, return_candidate_ids=None):
    """Fit each prefix, execute its frozen methods and benchmark, retain all outcomes.

    ``before_account(phase, identity)`` runs before each new execution and may
    refuse by raising (or returning False). Exact verified successes are reused;
    previous failures and partial attempts are retained without implicit retry.
    """
    if not isinstance(frames, Mapping) or not frames:
        raise ValueError("registered factor frames required")
    start, end, plan = _fold_plan(panel.dates, train_start, train_end, folds, horizon_sessions)
    if type(ridge_lambda) not in (int, float) or not np.isfinite(ridge_lambda) or ridge_lambda <= 0:
        raise ValueError("ridge_lambda must be finite and positive")
    policy = policy if policy is not None else AccountPolicy()
    if not isinstance(policy, AccountPolicy):
        raise ValueError("policy must be AccountPolicy")
    # Reject unsupported allocation before starting fitting or reserving budget.
    allocation = validate_strategy({"name": "allocation_contract", "score": {"op": "constant", "value": 1},
                                    "allocation": allocation or {}})["allocation"]
    if allocation["membership_buffer"] != 0:
        raise ValueError("combination lab comparable pool benchmark requires zero membership buffer")
    sources = _sources()
    intent = clean({"version": VERSION, "train_start": start, "train_end": end, "folds": plan,
        "horizon_sessions": horizon_sessions, "ridge_lambda": ridge_lambda,
        "selection": selection, "interaction_pairs": interaction_pairs,
        "return_candidate_ids": return_candidate_ids,
        "allocation": allocation, "policy": asdict(policy), "source_identity": sources,
        "factor_ids": sorted(frames), "fold_results_select_winner": False})
    output = Path(artifact_dir).resolve() if artifact_dir is not None else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(output / "lab.lock"):
            return _evaluate(panel, frames, intent, output, policy, before_account, cancelled, on_event)
    return _evaluate(panel, frames, intent, None, policy, before_account, cancelled, on_event)


def _evaluate(panel, frames, intent, output, policy, before_account, cancelled, on_event):
    from .attribution import equal_pool_benchmark, compare_accounts
    if output is not None:
        if (output / "intent.json").exists():
            if json.loads((output / "intent.json").read_text(encoding="utf-8")) != intent:
                raise ValueError("combination lab intent/source differs; use a new artifact directory")
        else:
            _write(output / "intent.json", intent)
    methods = ["equal_rank", "ridge"] + (["ridge_augmented", "ridge_interactions"] if intent["interaction_pairs"] else [])
    folds = []
    for index, fold in enumerate(intent["folds"]):
        _cancel(cancelled)
        row = {"index": index, "plan": fold, "fit": None, "fit_status": "failed", "accounts": {}, "attribution": {}}
        if on_event is not None:
            on_event({"event": "fit_started", "fold": index, "fit_end": fold["fit_end"]})
        try:
            fit = _fit(panel, frames, intent["train_start"], fold["fit_end"], intent["horizon_sessions"],
                       intent["ridge_lambda"], intent["selection"], intent["interaction_pairs"], intent["return_candidate_ids"])
            specs = _specs(fit, intent["allocation"])
            row.update(fit=fit, fit_status="fitted", strategy_specs=specs)
        except Exception as exc:
            if isinstance(exc, InterruptedError):
                raise
            row["fit_error"] = _error(exc)
            specs = {}
        if output is not None:
            fold_folder = output / f"fold_{index:02d}"
            fold_folder.mkdir(parents=True, exist_ok=True)
            _immutable_json(fold_folder / "fit.json", {key: value for key, value in row.items()
                                                      if key not in {"accounts", "attribution"}})
        validation_panel = scope_panel(panel, end=fold["validation_end"])
        validation_frames = scope_frames(frames, validation_panel)
        common = {"fold": fold, "panel": validation_panel.fingerprint(), "frames": _frame_identity(validation_frames),
                  "source_identity": intent["source_identity"], "policy": intent["policy"]}
        accounts = {}
        for method in methods + ["equal_pool_benchmark"]:
            _cancel(cancelled)
            if method != "equal_pool_benchmark" and method not in specs:
                row["accounts"][method] = {
                    "status": "not_run_model_unavailable" if row["fit_status"] == "fitted" else "not_run_fit_failed",
                    "model_status": (row.get("fit") or {}).get("model_status", {}).get(method),
                    "summary": None, "reused": False, "execution_started": False}
                continue
            spec = specs.get(method)
            frozen = {**common, "method": method, "strategy": spec,
                "fit": row["fit"] if spec is not None else None, "allocation": intent["allocation"]}
            identity = digest(frozen)
            folder = output / f"fold_{index:02d}" / method if output else None
            def run(method=method, spec=spec):
                if method == "equal_pool_benchmark":
                    return equal_pool_benchmark(validation_panel, start=fold["validation_start"],
                        end=fold["validation_end"], allocation=intent["allocation"], policy=policy, cancelled=cancelled)
                return execute_strategy(validation_panel, validation_frames, spec,
                    start=fold["validation_start"], end=fold["validation_end"], policy=policy, cancelled=cancelled)
            record, account = _run_account(folder, identity, frozen, f"fold_{index}:{method}", run,
                                           before_account, cancelled, on_event)
            row["accounts"][method] = record
            if account is not None:
                accounts[method] = account
        baseline = accounts.get("equal_pool_benchmark")
        for method in methods:
            if method in accounts and baseline is not None:
                try:
                    row["attribution"][method] = compare_accounts(accounts[method], baseline)
                except ValueError as exc:
                    row["attribution"][method] = {"status": "not_evaluable", "error": _error(exc)}
            else:
                row["attribution"][method] = {"status": "not_evaluable", "reason": "account_or_benchmark_unavailable"}
        if "ridge_augmented" in accounts and "ridge_interactions" in accounts:
            try:
                row["interaction_control"] = compare_accounts(accounts["ridge_interactions"], accounts["ridge_augmented"])
            except ValueError as exc:
                row["interaction_control"] = {"status": "not_evaluable", "error": _error(exc)}
        else:
            row["interaction_control"] = {"status": "not_evaluable", "reason": "interaction_control_accounts_unavailable"}
        folds.append(row)
        if on_event is not None:
            on_event({"event": "fold_completed", "fold": index,
                      "completed_accounts": sum(x["status"] == "completed" for x in row["accounts"].values())})
    _cancel(cancelled)
    try:
        final_fit = _fit(panel, frames, intent["train_start"], intent["train_end"], intent["horizon_sessions"],
                         intent["ridge_lambda"], intent["selection"], intent["interaction_pairs"], intent["return_candidate_ids"])
        final = {"final_fit_status": "fitted", "final_fit": final_fit,
                 "final_strategy_specs": _specs(final_fit, intent["allocation"])}
    except Exception as exc:
        if isinstance(exc, InterruptedError):
            raise
        final = {"final_fit_status": "failed", "final_fit": None, "final_fit_error": _error(exc),
                 "final_strategy_specs": {}}
    rows = [a for fold in folds for a in fold["accounts"].values()]
    complete = sum(a["status"] == "completed" for a in rows)
    result = clean({"version": VERSION, "status": "completed" if complete == len(rows) and final["final_fit_status"] == "fitted" else "completed_with_failures",
        "intent": intent, "folds": folds, "methods": _method_summary(folds, methods), **final,
        "account_denominator": {"declared": len(rows), "completed": complete,
            "failed_or_unavailable": len(rows) - complete, "reused_successes": sum(a["status"] == "completed" and a["reused"] for a in rows),
            "new_successes": sum(a["status"] == "completed" and not a["reused"] for a in rows)},
        "selected_method": None, "fold_results_used_for_final_fit": False,
        "account_capital_semantics": "each method and benchmark in each fold starts with identical policy capital; arithmetic fold summaries are not a continuous funded equity curve",
        "raw_retention_requested": output is not None, "source_identity": intent["source_identity"],
        "formal_financial_success": False, "independent_validation_claimed": False,
        "limitations": ["all folds are within exposed training/development history",
            "same registered definitions may already embody earlier research selection",
            "no method or hyperparameter is selected automatically from fold outcomes",
            "failure and unavailable accounts remain in the declared denominator",
            "separate fold accounts cannot be combined into a continuous compounded return"]})
    if _sources() != intent["source_identity"]:
        raise ValueError("combination lab source changed during execution")
    if output is not None:
        # Reuse changes operational counts, so retain each invocation separately.
        number = len(list(output.glob("result_*.json")))
        _write(output / f"result_{number:04d}.json", result)
    return result
