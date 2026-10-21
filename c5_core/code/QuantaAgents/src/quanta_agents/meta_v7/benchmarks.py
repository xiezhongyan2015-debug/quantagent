"""Reproducible synthetic discovery tasks and equal-budget software baselines.

These are deterministic algorithm comparisons, not claims about model ability
or tradable alpha.  A model pilot must keep ``truth`` and validation labels out
of its prompt, freeze its decisions, and then use ``score_discovery`` offline.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations
import json
import time

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.data import MarketPanel
from .validation import validate_split_plan

VERSION = "v7_synthetic_discovery_benchmark_v1"
MODES = ("fixed_soft", "factor_lab")
KINDS = ("interaction", "redundancy", "null")
DEFAULT_BUDGET = {"max_evaluations": 10, "max_discoveries": 1, "min_absolute_train_ic": .08}


def _candidate(kind, ids):
    return kind + ":" + ":".join(sorted(ids))


def make_synthetic_task(seed, kind="interaction", train_sessions=160, validation_sessions=80, stocks=32):
    """Return panel, opaque raw frames, isolated labels, split plan and hidden truth.

    Signal D predicts the open D+1 to D+2 return.  The product task uses balanced
    independent signs: both marginal population ICs are zero while their product
    is predictive.  The redundant task has two noisy views of one latent source.
    All seeds use exactly the same preregistered construction and effect sizes.
    Labels whose exits exceed a split cutoff remain NaN, never backfilled.
    """
    for key, value, low in (("seed", seed, 0), ("train_sessions", train_sessions, 20),
                            ("validation_sessions", validation_sessions, 20), ("stocks", stocks, 16)):
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < low:
            raise ValueError(f"invalid {key}")
    if stocks % 4 or kind not in KINDS:
        raise ValueError("stocks must be divisible by four; unknown task kind")
    rng = np.random.default_rng(seed)
    warmup = 130
    n = warmup + train_sessions + validation_sessions
    dates = pd.bdate_range("2018-01-01", periods=n)
    columns = [f"sh{600000+i:06d}" for i in range(stocks)]
    x = np.empty((n, stocks))
    y = np.empty_like(x)
    pattern = np.tile(np.array([[-1., -1.], [-1., 1.], [1., -1.], [1., 1.]]), (stocks//4, 1))
    for i in range(n):
        shuffled = pattern[rng.permutation(stocks)]
        x[i], y[i] = shuffled.T
    raw = [x, y, rng.normal(size=x.shape), rng.normal(size=x.shape)]
    noise = rng.normal(0, .004, size=x.shape)
    if kind == "interaction":
        returns = .008 * x * y + noise
    elif kind == "redundancy":
        latent = rng.normal(size=x.shape)
        raw[0], raw[1] = latent, latent + rng.normal(0, .025, size=x.shape)
        returns = .008 * latent + noise
    else:
        raw = [rng.normal(size=x.shape) for _ in range(4)]
        returns = noise
    returns = np.clip(returns, -.04, .04)
    ids = [f"A{i+1}" for i in rng.permutation(4)]
    frames = {key: pd.DataFrame(value, index=dates, columns=columns) for key, value in zip(ids, raw)}
    # No same-day prices reveal the return label.  D's signal affects only D+2.
    daily = np.zeros_like(returns)
    daily[2:] = returns[:-2]
    opening = pd.DataFrame(10 * np.cumprod(1 + daily, axis=0), index=dates, columns=columns)
    ones = opening * 0 + 1.
    fields = {"open": opening, "close": opening.copy(), "high": opening * 1.002, "low": opening * .998,
              "raw_open": opening.copy(), "raw_close": opening.copy(),
              "raw_prev_close": opening.shift().fillna(opening.iloc[0]),
              "volume": ones * 1_000_000., "amount": ones * 100_000_000., "adjustment_factor": ones,
              "is_st": ones * 0, "is_delisting": ones * 0, "open_observed": ones.copy()}
    # Raw score fields are causal fixture inputs, enabling AssetRegistry clients.
    fields.update({"feature_" + key.lower(): frame.copy() for key, frame in frames.items()})
    config = {"version": VERSION, "seed": int(seed), "kind": kind, "train_sessions": int(train_sessions),
              "validation_sessions": int(validation_sessions), "stocks": int(stocks)}
    task_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    panel = MarketPanel(fields, opening.notna(), {"synthetic_task_id": task_id, "real_market_evidence": False})
    te = warmup + train_sessions - 1
    split_plan = {"train_start": str(dates[warmup].date()), "train_end": str(dates[te].date()),
                  "validation_start": str(dates[te+1].date()), "validation_end": str(dates[-1].date()),
                  "embargo_sessions": 0, "max_label_horizon": 1}
    split = validate_split_plan(split_plan, dates)
    labels = opening.shift(-2) / opening.shift(-1) - 1
    train_labels = labels.loc[split_plan["train_start"]:split_plan["train_end"]].copy()
    train_labels.iloc[-2:] = np.nan
    validation_labels = labels.loc[split_plan["validation_start"]:split_plan["validation_end"]].copy()
    universe = ["factor:"+key for key in sorted(ids)]
    universe += [_candidate("interaction", pair) for pair in combinations(sorted(ids), 2)]
    universe += [_candidate("linear", pair) for pair in combinations(sorted(ids), 2)]
    positives = []
    if kind == "interaction":
        positives = [{"group": "one_product_source", "candidate_ids": [_candidate("interaction", ids[:2])]}]
    elif kind == "redundancy":
        positives = [{"group": "one_latent_source", "candidate_ids": ["factor:"+ids[0], "factor:"+ids[1], _candidate("linear", ids[:2])]}]
    truth = {"task_id": task_id, "kind": kind, "discovery_groups": positives, "candidate_universe": universe,
             "marginal_population_ic_zero": kind == "interaction", "independent_sources": len(positives),
             "not_real_alpha": True}
    public = {"version": VERSION, "task_id": task_id, "factor_ids": sorted(ids), "split_plan": deepcopy(split_plan),
              "factor_field_mapping": {key: "feature_"+key.lower() for key in ids},
              "label_semantics": "signal D, next open entry D+1, exit open D+2",
              "synthetic": True, "hidden_truth_included": False,
              "instructions": "Use training evidence only; freeze up to one discovery or abstain before validation is revealed."}
    return {"task_id": task_id, "panel": panel, "frames": frames, "split_plan": split_plan,
            "split": split, "train_labels": train_labels, "validation_labels": validation_labels,
            "public": public, "truth": truth, "generation_config": config}


def score_discovery(predictions, truth):
    """Score frozen canonical discovery IDs; correlated aliases count once.

    The benchmark ontology is finite.  Out-of-ontology predictions are explicit
    false positives here, not a proof an arbitrary expression is economically
    useless.  No holdout numbers enter this structural score.
    """
    if not isinstance(predictions, (list, tuple)) or not all(isinstance(p, str) for p in predictions):
        raise ValueError("predictions must be canonical discovery ID strings")
    groups = truth["discovery_groups"]
    unique = list(dict.fromkeys(predictions))
    valid = {value for group in groups for value in group["candidate_ids"]}
    matched = [group["group"] for group in groups if set(group["candidate_ids"]) & set(unique)]
    wrong = [value for value in unique if value not in valid]
    correct_count = sum(value in valid for value in unique)
    negatives = set(truth.get("candidate_universe", [])) - valid
    return {"discovered_sources": len(matched), "expected_sources": len(groups),
            "missed_sources": len(groups)-len(matched), "false_positive_count": len(wrong),
            "false_positive_ids": wrong, "matched_groups": matched,
            "redundant_discoveries": max(0, correct_count-len(matched)) + len(predictions)-len(unique),
            "precision": len(matched)/(len(matched)+len(wrong)) if matched or wrong else None,
            "recall": len(matched)/len(groups) if groups else None,
            "false_positive_rate": len(set(wrong) & negatives)/len(negatives) if negatives else None,
            "correct_null_rejection": not groups and not unique,
            "all_discoveries_rejected": not unique, "real_alpha_claim": False}


def _daily_ic(score, labels):
    score = score.loc[labels.index]
    values = []
    for (_, x), (_, y) in zip(score.iterrows(), labels.iterrows()):
        mask = np.isfinite(x) & np.isfinite(y)
        a, b = x[mask].rank(), y[mask].rank()
        if len(a) >= 8 and a.nunique() > 1 and b.nunique() > 1:
            values.append(float(a.corr(b)))
    return {"mean_ic": float(np.mean(values)) if values else None, "dates": len(values)}


def run_deterministic_benchmark(task, mode="fixed_soft", budget=None):
    """Same training evaluation cap, one frozen choice, one holdout evaluation.

    fixed_soft tests singles plus fixed equally weighted rank mixtures;
    factor_lab tests singles plus centered-rank products.  This deliberately
    compares two software candidate menus, never two researchers or LLMs.
    """
    if mode not in MODES:
        raise ValueError("unknown deterministic mode")
    limits = deepcopy(DEFAULT_BUDGET if budget is None else budget)
    if set(limits) != set(DEFAULT_BUDGET):
        raise ValueError("budget requires exactly the documented keys")
    evaluations = limits["max_evaluations"]
    if isinstance(evaluations, bool) or not isinstance(evaluations, int) or not 1 <= evaluations <= 10:
        raise ValueError("max_evaluations must be 1..10")
    if limits["max_discoveries"] != 1 or isinstance(limits["max_discoveries"], bool):
        raise ValueError("this benchmark predeclares exactly one available discovery slot")
    threshold = limits["min_absolute_train_ic"]
    if isinstance(threshold, bool) or not np.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("train IC threshold must be fixed in (0,1)")
    started = time.perf_counter()
    ids = sorted(task["frames"])
    # Slice before numerical ranking: holdout values are not even transformed
    # while a training candidate is selected.
    train = task["train_labels"]
    ranks = {key: task["frames"][key].loc[train.index].rank(axis=1, pct=True) for key in ids}
    menu = [("factor:"+key, (key,)) for key in ids]
    family = "linear" if mode == "fixed_soft" else "interaction"
    menu += [(_candidate(family, pair), pair) for pair in combinations(ids, 2)]

    def expression(keys, ranked):
        if len(keys) == 1:
            return ranked[keys[0]]
        a, b = (ranked[key] for key in keys)
        return (a+b)/2 if mode == "fixed_soft" else (a-.5)*(b-.5)

    records = []
    for key, members in menu[:evaluations]:
        records.append({"candidate_id": key, "members": list(members), **_daily_ic(expression(members, ranks), train)})
    available = [row for row in records if row["mean_ic"] is not None and abs(row["mean_ic"]) >= threshold]
    selected = max(available, key=lambda row: (abs(row["mean_ic"]), row["candidate_id"])) if available else None
    # No validation values are read before the frozen selection above.
    frozen = deepcopy(selected)
    validation = None
    if frozen is not None:
        labels = task["validation_labels"]
        val_ranks = {key: task["frames"][key].loc[labels.index].rank(axis=1, pct=True) for key in frozen["members"]}
        direction = 1 if frozen["mean_ic"] > 0 else -1
        frozen["direction"] = direction
        validation = _daily_ic(expression(frozen["members"], val_ranks)*direction, labels)
    discoveries = [] if frozen is None else [frozen["candidate_id"]]
    return {"version": VERSION, "task_id": task["task_id"], "mode": mode,
            "comparison_kind": "deterministic candidate-menu software comparison, not model capability A/B",
            "budget": limits, "training_evaluations": len(records), "validation_evaluations": int(frozen is not None),
            "training_candidates": records, "frozen_selection": frozen, "validation": validation,
            "validation_used_for_selection": False, "discovery": score_discovery(discoveries, task["truth"]),
            "model_calls": None, "model_tokens": None, "model_usage": "not_applicable_no_model",
            "account_executions": None, "account_usage": "not_applicable_signal_benchmark",
            "elapsed_seconds": time.perf_counter()-started, "formal_financial_success": False}
