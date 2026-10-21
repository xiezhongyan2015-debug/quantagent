"""Chronological validation and paired uncertainty; neither certifies independence.

Training end is an outcome-information cutoff.  A label for signal D enters at
D+1 and exits at D+1+h, so the last h+1 training signals are purged.  Embargo is
a separate optional calendar-session gap, not a substitute for label purging.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from quanta_agents.research_kernel.compiler import validate_strategy, strategy_id
from quanta_agents.research_kernel.evidence import compact_account
from quanta_agents.research_kernel.store import clean
from .execution import execute_strategy

VERSION = "v7_chronological_validation_v1"


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return int(value)


def _date(value):
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tz is not None or result != result.normalize():
        raise ValueError("split dates must be finite timezone-free session dates")
    return result


def _calendar(dates):
    if (not isinstance(dates, pd.DatetimeIndex) or dates.empty or dates.hasnans
            or dates.tz is not None or not dates.is_unique or not dates.is_monotonic_increasing
            or not dates.equals(dates.normalize())):
        raise ValueError("dates must be a nonempty, ordered, unique daily calendar")


def validate_split_plan(plan: dict, dates: pd.DatetimeIndex) -> dict:
    """Validate calendar separation and return the exact maximum-horizon purge.

    The plan can use calendar bounds that are not trading days.  Effective
    sessions, label-safe signal dates and the actual gap are reported explicitly.
    Account PnL still uses the complete training account range; the purge applies
    to supervised labels, not to deleting the account's final realized days.
    """
    required = {"train_start", "train_end", "validation_start", "validation_end",
                "embargo_sessions", "max_label_horizon"}
    if not isinstance(plan, dict) or set(plan) != required:
        raise ValueError("split plan must contain exactly the six documented keys")
    _calendar(dates)
    bounds = {key: _date(plan[key]) for key in required - {"embargo_sessions", "max_label_horizon"}}
    ts, te, vs, ve = (bounds[key] for key in ("train_start", "train_end", "validation_start", "validation_end"))
    if not ts <= te < vs <= ve:
        raise ValueError("training and validation must be ordered and nonoverlapping")
    horizon = _integer(plan["max_label_horizon"], "max_label_horizon", 1, 2520)
    embargo = _integer(plan["embargo_sessions"], "embargo_sessions", 0, 100000)
    train_pos = np.flatnonzero((dates >= ts) & (dates <= te))
    validation_pos = np.flatnonzero((dates >= vs) & (dates <= ve))
    if len(train_pos) <= horizon + 1 or not len(validation_pos):
        raise ValueError("split lacks training labels or validation sessions")
    gap = int(validation_pos[0] - train_pos[-1] - 1)
    if gap < embargo:
        raise ValueError("actual session gap is smaller than declared embargo")
    safe = train_pos[train_pos + horizon + 1 <= train_pos[-1]]
    purged = train_pos[train_pos + horizon + 1 > train_pos[-1]]
    normalized = {key: value.date().isoformat() for key, value in bounds.items()}
    normalized.update(embargo_sessions=embargo, max_label_horizon=horizon)
    return {"version": VERSION, "plan": normalized,
            "train": {"start": str(dates[train_pos[0]].date()), "end": str(dates[train_pos[-1]].date()), "sessions": len(train_pos)},
            "validation": {"start": str(dates[validation_pos[0]].date()), "end": str(dates[validation_pos[-1]].date()), "sessions": len(validation_pos)},
            "actual_embargo_sessions": gap,
            "label_safe_train_end": str(dates[safe[-1]].date()),
            "label_safe_train_signal_dates": [str(dates[i].date()) for i in safe],
            "purged_train_signal_dates": [str(dates[i].date()) for i in purged],
            "label_semantics": "signal D, entry D+1, exit D+1+h; exit must be on or before train_end",
            "validation_previous_signal_session": str(dates[validation_pos[0] - 1].date()),
            "account_training_days_purged": False,
            "temporal_not_independence_claim": True,
            "independence_requires_external_exposure_ledger": True}


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError("validation cancelled")


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(clean(value), stream, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)


def _retain_account(account, folder, spec):
    """Preserve original observations before deriving the prompt-sized summary."""
    artifact = None
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=False)
        for key in ("daily", "trades", "annual", "targets"):
            if key not in account:
                if key == "targets":
                    continue
                raise ValueError("account omitted required original table: " + key)
            export = account[key].copy(deep=False)
            export.attrs = {}
            export.to_parquet(folder / (key + ".parquet"))
            if account[key].attrs:
                _write_json(folder / (key + "_attributes.json"), account[key].attrs)
        for key in ("summary", "policy", "diagnostics", "temporal_scope"):
            if key in account:
                _write_json(folder / (key + ".json"), account[key])
        _write_json(folder / "frozen_spec.json", spec)
        proofs = [{"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                  for path in sorted(folder.iterdir()) if path.is_file()]
        _write_json(folder / "artifacts.json", {"artifacts": proofs})
        artifact = {"folder": str(folder.resolve()), "manifest_path": str((folder / "artifacts.json").resolve()),
                    "manifest_sha256": hashlib.sha256((folder / "artifacts.json").read_bytes()).hexdigest()}
    return {**compact_account(account), "raw_artifacts": artifact, "raw_retained": folder is not None}


def evaluate_frozen_candidates(panel, frames, specs, plan, *, policy=None, cancelled=None, artifact_dir=None,
                               before_account=None) -> dict:
    """Evaluate declared specs, select on training only, then report validation.

    Both accounts start with the same policy capital.  This is a split account
    comparison, not a claim of continuous capital across the train/test boundary.
    Rolling signals retain prior history; the execution layer only fills using
    prior-session signals.  All validation results are descriptive diagnostics;
    they cannot change the already frozen training selection.
    before_account(phase, strategy_id) is a budget reservation hook, with no
    financial results or mutable specification passed to it.  Refusal is retained
    as a phase failure and prevents that account from starting.
    """
    split = validate_split_plan(plan, panel.dates)
    if not isinstance(specs, (list, tuple)) or not specs:
        raise ValueError("at least one frozen candidate is required")
    normalized = [validate_strategy(deepcopy(spec)) for spec in specs]
    ids = [strategy_id(spec) for spec in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate economic candidates are not independent trials")
    frozen_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    output = Path(artifact_dir).resolve() if artifact_dir is not None else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=False)
        sources = [Path(__file__), Path(__file__).with_name("execution.py"), Path(__file__).with_name("temporal.py")]
        _write_json(output / "intent.json", {"version": VERSION, "plan": split["plan"], "specs": normalized,
                    "source_proofs": [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources],
                    "frozen_specs_sha256": hashlib.sha256(frozen_json.encode()).hexdigest(),
                    "raw_retention_requested": True, "implicit_retry_allowed": False})
    records = [{"strategy_id": sid, "normalized_spec": spec, "train": None, "validation": None,
                "status": "declared"} for sid, spec in zip(ids, normalized)]
    for record in records:
        _cancel(cancelled)
        try:
            if before_account is not None:
                before_account("train", record["strategy_id"])
            account = execute_strategy(panel, frames, deepcopy(record["normalized_spec"]),
                start=split["plan"]["train_start"], end=split["plan"]["train_end"], policy=policy, cancelled=cancelled)
            record["train"] = _retain_account(account, output / record["strategy_id"] / "train" if output else None,
                                              record["normalized_spec"])
            record["status"] = "train_completed"
        except InterruptedError:
            raise
        except Exception as exc:
            record.update(status="train_failed", error={"type": type(exc).__name__, "message": str(exc)})
            if output:
                _write_json(output / (record["strategy_id"] + "_train_failure.json"), record["error"])
    ranked = []
    for ordinal, record in enumerate(records):
        summary = (record["train"] or {}).get("summary", {})
        sharpe, turnover = summary.get("sharpe"), summary.get("turnover")
        if sharpe is not None and np.isfinite(sharpe):
            ranked.append((float(sharpe), -float(turnover) if turnover is not None and np.isfinite(turnover) else -math.inf,
                           -ordinal, record["strategy_id"]))
    selected = max(ranked)[-1] if ranked else None
    # Selection is fixed here, before the first validation call.
    selection = {"strategy_id": selected, "criterion": "maximum full-period training net Sharpe, then lower turnover, then declaration order",
                 "frozen_before_validation": True, "validation_used_for_selection": False}
    if output:
        _write_json(output / "selection.json", selection)
    for record in records:
        if record["status"] != "train_completed":
            continue
        _cancel(cancelled)
        try:
            if before_account is not None:
                before_account("validation", record["strategy_id"])
            account = execute_strategy(panel, frames, deepcopy(record["normalized_spec"]),
                start=split["plan"]["validation_start"], end=split["plan"]["validation_end"], policy=policy, cancelled=cancelled)
            record["validation"] = _retain_account(account, output / record["strategy_id"] / "validation" if output else None,
                                                   record["normalized_spec"])
            record["status"] = "completed"
        except InterruptedError:
            raise
        except Exception as exc:
            record.update(status="validation_failed", error={"type": type(exc).__name__, "message": str(exc)})
            if output:
                _write_json(output / (record["strategy_id"] + "_validation_failure.json"), record["error"])
    report = {"version": VERSION, "split": split, "frozen_specs_sha256": hashlib.sha256(frozen_json.encode()).hexdigest(),
            "selection": selection, "candidates": records,
            "account_capital_semantics": "separate train and validation accounts, each initialized with identical policy capital",
            "temporal_not_independence_claim": True, "formal_financial_success": False,
            "raw_retention_requested": output is not None, "validation_reselection_performed": False}
    if output:
        _write_json(output / "result.json", report)
    return clean(report)


def paired_sharpe_bootstrap(candidate_returns, baseline_returns, *, block_sessions, repetitions, seed,
                            confidence=.95, risk_free_rate=.02, annualization=252, cancelled=None):
    """Paired moving-block CI; shared blocks retain dependence across candidates.

    Missing sessions stay in the calendar during sampling.  Each contrast uses
    the same finite paired rows for both Sharpes.  A multi-column input also
    receives a simultaneous sup-norm bootstrap interval over this predeclared
    family, not a correction for unrecorded adaptive trials or selected winners.
    """
    candidates = candidate_returns.to_frame("candidate") if isinstance(candidate_returns, pd.Series) else candidate_returns
    if not isinstance(candidates, pd.DataFrame) or not isinstance(baseline_returns, pd.Series):
        raise ValueError("candidate Series/DataFrame and baseline Series required")
    _calendar(candidates.index)
    if not candidates.index.equals(baseline_returns.index) or not candidates.columns.is_unique or candidates.shape[1] == 0:
        raise ValueError("paired returns require exact calendar alignment and unique candidates")
    block = _integer(block_sessions, "block_sessions", 1, len(candidates))
    reps = _integer(repetitions, "repetitions", 20, 10000)
    rng_seed = _integer(seed, "seed", 0, 2**63 - 1)
    if not 0 < confidence < 1 or not np.isfinite(risk_free_rate) or risk_free_rate <= -1 or annualization <= 1:
        raise ValueError("invalid confidence, risk-free rate or annualization")
    if len(candidates) < 2 * block:
        raise ValueError("at least two blocks of calendar history are required")
    matrix = candidates.to_numpy(dtype=float)
    baseline = baseline_returns.to_numpy(dtype=float)
    rf = (1 + risk_free_rate)**(1 / annualization) - 1

    def deltas(indices):
        values, counts = [], []
        for column in matrix.T:
            a, b = column[indices], baseline[indices]
            paired = np.isfinite(a) & np.isfinite(b)
            a, b = a[paired], b[paired]
            counts.append(len(a))
            if len(a) < 3 or a.std(ddof=1) <= 0 or b.std(ddof=1) <= 0:
                values.append(np.nan)
            else:
                values.append(((a.mean() - rf) / a.std(ddof=1) - (b.mean() - rf) / b.std(ddof=1)) * np.sqrt(annualization))
        return np.asarray(values), counts

    n = len(candidates)
    point, counts = deltas(np.arange(n))
    rng = np.random.default_rng(rng_seed)
    samples = np.empty((reps, len(point)))
    for rep in range(reps):
        _cancel(cancelled)
        starts = rng.integers(0, n - block + 1, size=math.ceil(n / block))
        indices = (starts[:, None] + np.arange(block)).ravel()[:n]
        samples[rep] = deltas(indices)[0]
    rows = []
    for i, name in enumerate(candidates.columns):
        available = samples[:, i][np.isfinite(samples[:, i])]
        evaluable = np.isfinite(point[i]) and len(available) >= math.ceil(.9 * reps)
        interval = np.quantile(available, [(1-confidence)/2, (1+confidence)/2]).tolist() if evaluable else None
        rows.append({"candidate": str(name), "paired_sessions": counts[i], "sharpe_increment": float(point[i]) if np.isfinite(point[i]) else None,
                     "percentile_interval": interval, "valid_repetitions": len(available), "status": "computed" if evaluable else "not_evaluable"})
    jointly_valid = np.isfinite(samples).all(axis=1)
    critical = None
    if np.isfinite(point).all() and jointly_valid.sum() >= math.ceil(.9 * reps):
        critical = float(np.quantile(np.abs(samples[jointly_valid] - point).max(axis=1), confidence))
    for i, row in enumerate(rows):
        row["simultaneous_family_interval"] = [float(point[i]-critical), float(point[i]+critical)] if critical is not None else None
    return {"version": "paired_moving_block_sharpe_v1", "contrasts": rows,
            "parameters": {"block_sessions": block, "repetitions": reps, "seed": rng_seed, "confidence": confidence,
                           "annualization": annualization, "risk_free_rate": risk_free_rate},
            "calendar_sessions": n, "missing_dates_removed_before_blocks": False,
            "same_blocks_for_all_candidates": True, "independent_trial_count_used": None,
            "multiple_testing_scope": "simultaneous sup-norm interval for the supplied predeclared comparison family only",
            "selection_adjusted": False, "temporal_not_independence_claim": True,
            "formal_financial_success": False}
