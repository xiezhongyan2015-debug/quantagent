"""Finite structure proposals, parameter expansion and executable falsifiers.

The builtins are this run's saved Astra proposal, not an autonomous random
formula generator or a claim of novelty. No market results are read here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.factors import FactorSpec
from .contracts import (ControlSpec, ExpandedCandidate, ExpansionResult,
                        FactorFamily, FalsifierSpec, JointConstraint, ParameterRange,
                        canonical_json, digest, required_fields)

ORIGIN = {"kind": "llm_structural_proposal_saved_as_code_and_json", "model": "gpt-6-astra",
          "reasoning_effort": "xhigh", "agent": "/root/generation_control",
          "generated_utc": "2026-09-12T15:16:12Z", "market_results_visible_this_run": False,
          "source": "current V6 DSL, exact old-library definitions, and pre-existing design/postmortem reports",
          "receipt": "parent task must bind actual local agent session receipt; token cost not invented"}
_SOURCE = {"path": "output/research/meta_v9_20260909/library_inventory.json",
           "sha256": "91ef8fec1153132fe18e93ebe6306168246b07895031770faf375a4a55527ced"}
_GAP = "log(close / open) - log(open / lag(close, 1))"
_CLV = "where(high > low, (2 * close - high - low) / (high - low), 0)"


def _fixed_falsifier() -> tuple[FalsifierSpec, ...]:
    return (FalsifierSpec("baseline_gain", "fixed_baseline_increment", "paired_delta_pearson_ic", ()),)


def build_builtin_proposals() -> tuple[FactorFamily, ...]:
    """12 existing + 12 window-only + 12 structural primary attempts, frozen.

    Matched controls are additional declared computations, never another arm's
    primary attempt. Formula duplicates retain all their arm memberships.
    """
    exact = [
        ("F1", "pct_change(lag(close, 20), 100)", "return"),
        ("F2", "pct_change(close, 5)", "return"),
        ("F3", f"rolling_mean({_GAP}, 20)", "return"),
        ("F4", f"rolling_mean({_CLV}, 5)", "return"),
        ("F5", "rolling_sum(where(pct_change(close, 1) < 0, pct_change(close, 1) * pct_change(close, 1), 0), 20) / rolling_sum(pct_change(close, 1) * pct_change(close, 1), 20)", "return"),
        ("F6", "rolling_mean(volume, 5) / lag(rolling_mean(volume, 60), 5)", "return"),
        ("F7", "rolling_std(pct_change(close, 1), 20)", "risk"),
        ("F8", "rolling_corr(pct_change(close, 1), log(volume), 20)", "return"),
        ("HF0091", "rolling_mean(abs(pct_change(close, 1)) / amount, 20)", "return"),
        ("calendar.standard.intraday_return", "where(abs(open) > 1e-12, close / open, 0 / 0) - 1", "return"),
        ("calendar.standard.overnight_return", "where(abs(lag(close, 1)) > 1e-12, open / lag(close, 1), 0 / 0) - 1", "return"),
        ("calendar.standard.short_reversal", "where(abs(lag(close, 20)) > 1e-12, close / lag(close, 20), 0 / 0) - 1", "return"),
    ]
    families = [FactorFamily(
        family_id=f"existing.{key}", arm="existing_library", mechanism="Exact frozen executable library definition.",
        expected_relation="Research control; no new mechanism or five-day success is assumed.",
        expression_template=expression, parameters={}, constraints=(), controls=(),
        falsifiers=_fixed_falsifier(), parents=(key,), modification="existing_definition",
        available_fields=tuple(sorted(required_fields(expression))), role=role,
        provenance={"kind": "exact_existing_definition", "source": _SOURCE, "source_id": key,
                    "prior_history_exposed": True, "primary_horizon_changed_for_common_comparison": True})
        for key, expression, role in exact]
    for key, template, parent in [
        ("price_change", "pct_change(close, {w})", "F2"),
        ("session_disagreement", f"rolling_mean({_GAP}, {{w}})", "F3"),
        ("close_location", f"rolling_mean({_CLV}, {{w}})", "F4"),
    ]:
        families.append(FactorFamily(
            family_id=f"rule.{key}", arm="rule_perturbation", mechanism="Change only the parent's trailing window.",
            expected_relation="A prespecified simple window grid controls for parameter search without structural reasoning.",
            expression_template=template, parameters={"w": ParameterRange((3, 5, 10, 20), "trailing sessions")},
            constraints=(), controls=(), falsifiers=_fixed_falsifier(), parents=(parent,), modification="window_only",
            available_fields=tuple(sorted(required_fields(template.format(w=3)))),
            provenance={"kind": "deterministic_window_grid", "source": _SOURCE, "llm_calls_per_point": 0}))
    windows = {"short": ParameterRange((3, 5), "recent daily pressure horizon"),
               "long": ParameterRange((20, 40), "trailing reference regime")}
    constraints = (JointConstraint("short", "lt", "long"),)
    difference = f"rolling_mean({_GAP}, {{short}})"
    volatility = "rolling_std(pct_change(close, 1), {long})"
    families.append(FactorFamily(
        "structure.session_disagreement_scaled", "llm_structure",
        "Within-day price discovery and overnight repricing can carry different persistence. Volatility scaling tests whether relative session disagreement adds information beyond a return-scale effect.",
        "Expected positive: sustained intraday-minus-overnight strength scaled by trailing volatility predicts next-open five-session returns; exact sign is learned on 2016-2018 only and then fixed.",
        f"({difference}) / ({volatility})", windows, constraints,
        (ControlSpec("unscaled_disagreement", difference, "Remove volatility scaling while preserving both sessions."),
         ControlSpec("scaled_total_return", f"rolling_mean(log(close / open) + log(open / lag(close, 1)), {{short}}) / ({volatility})", "Replace session disagreement by total return at the same windows.")),
        (FalsifierSpec("beyond_unscaled", "paired_ic_gain", "paired_delta_pearson_ic", ("unscaled_disagreement",)),
         FalsifierSpec("beyond_total_return", "paired_ic_gain", "paired_delta_pearson_ic", ("scaled_total_return",))),
        ("F3", "F7"), "structural", ("open", "close"), provenance=ORIGIN))
    reversal = "-(cs_rank(pct_change(close, {short})) - 0.5)"
    activity = "cs_rank(rolling_mean(amount, {short}) / rolling_mean(amount, {long})) - 0.5"
    families.append(FactorFamily(
        "structure.activity_reversal", "llm_structure",
        "Temporary price pressure may reverse more strongly when recent traded amount is unusually high relative to its own trailing regime. Amount is a noisy activity proxy, not identified informed or institutional flow.",
        "Expected positive interaction: relative high-activity losers outperform low-activity losers; retaining both main effects distinguishes interaction from ordinary reversal or liquidity exposure.",
        f"({reversal}) * ({activity})", windows, constraints,
        (ControlSpec("reversal", reversal, "Recent return rank, unchanged cross-sectional pool.", True),
         ControlSpec("activity", activity, "Same activity state without the price interaction.", True)),
        (FalsifierSpec("interaction_gain", "interaction_increment", "paired_delta_pearson_ic", ("reversal", "activity")),),
        ("F2", "F6", "HF0091"), "structural", ("close", "amount"), role="interaction", provenance=ORIGIN))
    pressure = f"rolling_mean({_CLV}, {{short}})"
    compression = "1 - rolling_std(pct_change(close, 1), {short}) / rolling_std(pct_change(close, 1), {long})"
    families.append(FactorFamily(
        "structure.compressed_close_pressure", "llm_structure",
        "Repeated closes near the daily high may contain more persistent buying pressure when recent return volatility has compressed relative to its trailing regime. The interaction can fail if pressure already exhausts demand or compression signals illiquidity.",
        "Expected positive interaction between close-location pressure and volatility compression; B+pressure+compression is the required comparison before adding their product.",
        f"({pressure}) * ({compression})", windows, constraints,
        (ControlSpec("pressure", pressure, "Remove compression, preserve close-location signal.", True),
         ControlSpec("compression", compression, "Remove directional pressure, preserve volatility state.", True)),
        (FalsifierSpec("interaction_gain", "interaction_increment", "paired_delta_pearson_ic", ("pressure", "compression")),),
        ("F4", "F7"), "structural", ("high", "low", "close"), role="interaction", provenance=ORIGIN))
    return tuple(families)


def require_generation_phase(phase: str) -> None:
    if phase not in {"proposal", "development"}:
        raise ValueError("generation, refinement and implementation repair are closed after candidate freeze")


def expand_proposals(families: Iterable[FactorFamily], *, max_candidates: int = 72,
                     max_attempts: int = 256, phase: str = "development") -> ExpansionResult:
    require_generation_phase(phase)
    if type(max_candidates) is not int or not 1 <= max_candidates <= 72:
        raise ValueError("candidate budget must be an integer in [1,72]")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 512:
        raise ValueError("attempt budget must be an integer in [1,512]")
    families = tuple(families)
    if len(families) > 48:
        raise ValueError("at most 48 families may be enumerated in one batch")
    if len({f.family_id for f in families}) != len(families):
        raise ValueError("family ids must be unique")
    candidates: dict[str, ExpandedCandidate] = {}
    attempts = []
    # Round robin parameter depth; family order is frozen in the saved proposal.
    grids = [f.parameter_points() for f in families]
    if sum(len(g) * (1 + len(f.controls)) for f, g in zip(families, grids)) > 512:
        raise ValueError("all enumerated primary/control attempts exceed hard bound of 512")
    admitted_attempts = 0
    for offset in range(max(map(len, grids), default=0)):
        for family, points in zip(families, grids):
            if offset >= len(points):
                continue
            parameters = points[offset]
            identity = {"proposal_id": family.proposal_id, "parameters": parameters}
            primary_trial = digest({**identity, "kind": "candidate"})
            controls = {c.control_id: FactorSpec(f"{family.family_id}.{c.control_id}",
                        c.expression_template.format(**parameters)) for c in family.controls}
            control_ids = {k: v.factor_id for k, v in controls.items()}
            main_ids = tuple(control_ids[c.control_id] for c in family.controls if c.main_effect)
            metadata = {"family_id": family.family_id, "proposal_id": family.proposal_id,
                        "arm": family.arm, "role": family.role, "primary_horizon": family.primary_horizon,
                        "expected_direction": family.expected_direction, "direction_policy": "fit_2016_2018_then_fixed",
                        "fit_transform": "none_unsupervised_formula", "modification": family.modification,
                        "parameters": parameters, "provenance": dict(family.provenance)}
            spec = FactorSpec(family.family_id, family.expression_template.format(**parameters),
                              parents=family.parents, metadata=metadata)
            group = [ExpandedCandidate(spec, family.family_id, family.arm, "candidate", parameters,
                                       control_ids, main_ids, family.falsifiers, primary_trial, family.proposal_id)]
            for key, control_spec in controls.items():
                group.append(ExpandedCandidate(FactorSpec(control_spec.name, control_spec.expression,
                    parents=(spec.factor_id,), metadata={**metadata, "arm": "matched_control", "control_name": key}),
                    family.family_id, "matched_control", "control", parameters, {}, (), (),
                    digest({**identity, "kind": "control", "name": key}), family.proposal_id))
            missing = {c.spec.factor_id for c in group} - set(candidates)
            blocked = (len(candidates) + len(missing) > max_candidates or admitted_attempts + len(group) > max_attempts)
            if not blocked:
                admitted_attempts += len(group)
            # A partial group is never admitted: all matched controls must remain executable.
            for candidate in group:
                duplicate = candidate.spec.factor_id in candidates
                status = "budget_rejected" if blocked else "duplicate" if duplicate else "admitted"
                attempts.append({"trial_id": candidate.trial_id, "primary_trial_id": primary_trial,
                    "family_id": family.family_id, "proposal_id": family.proposal_id, "arm": candidate.arm,
                    "kind": candidate.kind, "parameters": parameters, "factor_id": candidate.spec.factor_id,
                    "status": status, "duplicate_of": candidates[candidate.spec.factor_id].trial_id if duplicate else None,
                    "counts_as_primary_attempt": candidate.kind == "candidate",
                    "control_ids": candidate.control_ids, "main_effect_ids": list(candidate.main_effect_ids)})
                if not blocked and not duplicate:
                    candidates[candidate.spec.factor_id] = candidate
    summary = {"primary_attempts_by_arm": dict(Counter(a["arm"] for a in attempts if a["kind"] == "candidate")),
               "all_attempts_by_arm": dict(Counter(a["arm"] for a in attempts)),
               "status_counts": dict(Counter(a["status"] for a in attempts)),
               "unique_formulas": len(candidates), "attempts": len(attempts),
               "proposed_attempts": len(attempts), "admitted_attempts_including_duplicates": admitted_attempts,
               "matched_control_attempts": sum(a["kind"] == "control" for a in attempts),
               "max_candidates": max_candidates, "max_attempts": max_attempts,
               "attempt_budget_semantics": "max_attempts caps computational admission including duplicates; rejected proposals are preserved separately and total enumeration is capped at 512",
               "counting": "duplicates and rejected groups remain in denominator; controls are separate overhead",
               "formula_memberships": {fid: sorted({a["arm"] for a in attempts if a["factor_id"] == fid}) for fid in candidates}}
    return ExpansionResult(tuple(candidates.values()), tuple(attempts), summary)


def save_proposals(path: Path, families: Iterable[FactorFamily]) -> dict:
    """Create an immutable proposal artifact. Existing different files are refused."""
    families = tuple(families)
    payload = {"version": "v9a_proposals_v1", "families": [f.to_dict() for f in families]}
    payload["sha256"] = digest(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError("refusing to overwrite a different frozen proposal")
    else:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    return payload


def load_proposals(path: Path) -> tuple[FactorFamily, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if (set(payload) != {"version", "families", "sha256"} or payload["version"] != "v9a_proposals_v1"
            or payload["sha256"] != digest({k: v for k, v in payload.items() if k != "sha256"})):
        raise ValueError("proposal artifact schema or content hash mismatch")
    return tuple(FactorFamily.from_dict(f) for f in payload["families"])


def evaluate_falsifiers(candidate: ExpandedCandidate, observations: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Require evaluator identity/sample bindings; missing comparisons stay unknown.

    Observations contain trial_id/factor_id/falsifier_id/metric/split,
    control_ids (actual formula identities), sample_mask_id, paired_days, value.
    The evaluator supplies paired numbers; this function never recomputes IC.
    """
    observations = tuple(observations)
    indexed = {r["falsifier_id"]: r for r in observations}
    if len(indexed) != len(observations):
        raise ValueError("duplicate falsifier observation")
    if set(indexed) - {f.falsifier_id for f in candidate.falsifiers}:
        raise ValueError("observation contains undeclared falsifier")
    result = []
    for falsifier in candidate.falsifiers:
        row = indexed.get(falsifier.falsifier_id)
        if row is None:
            result.append({"falsifier_id": falsifier.falsifier_id, "status": "not_evaluable", "reason": "missing_comparison"})
            continue
        expected_controls = sorted(candidate.control_ids[k] for k in falsifier.control_refs)
        if (row.get("trial_id") != candidate.trial_id or row.get("factor_id") != candidate.spec.factor_id
                or row.get("metric") != falsifier.metric or row.get("split") != falsifier.split
                or sorted(row.get("control_ids", [])) != expected_controls or not row.get("sample_mask_id")):
            raise ValueError("falsifier evidence identity, control or common-sample mismatch")
        verdict = falsifier.verdict(row.get("value"), row.get("paired_days"))
        direction = mechanism_direction_status(candidate, row.get("fitted_direction"))
        result.append({"falsifier_id": falsifier.falsifier_id,
            "status": verdict, **direction,
            "mechanism_support": "supported" if verdict == "supported" and direction["mechanism_direction_status"] == "consistent" else "not_supported",
            "value": row.get("value"), "threshold": falsifier.threshold,
            "paired_days": row.get("paired_days"), "sample_mask_id": row["sample_mask_id"]})
    return result


def mechanism_direction_status(candidate: ExpandedCandidate, fitted_direction: int | None) -> dict:
    """Empirical sign selection cannot silently rescue the opposite mechanism."""
    expected = candidate.spec.metadata.get("expected_direction")
    if fitted_direction is not None and (type(fitted_direction) is not int or fitted_direction not in (-1, 1)):
        raise ValueError("fitted direction must be -1, +1 or unknown")
    status = ("not_evaluable" if fitted_direction is None or expected not in (-1, 1)
              else "consistent" if fitted_direction == expected else "contradicted")
    return {"expected_direction": expected, "fitted_direction": fitted_direction,
            "mechanism_direction_status": status}


def repair_candidate(candidate: ExpandedCandidate, expression: str, *, reason: str,
                     repair_number: int, executor: Callable[[FactorSpec], pd.DataFrame],
                     phase: str = "development", max_repairs: int = 2) -> ExpandedCandidate:
    """Bounded implementation repair; changed formulas get new identities and run.

    Caller owns causal/parity checks and the persistent study lock. This function
    cannot certify those properties from a single returned value matrix.
    """
    require_generation_phase(phase)
    if type(max_repairs) is not int or not 0 <= max_repairs <= 2:
        raise ValueError("hard implementation repair limit is two")
    if type(repair_number) is not int or not 1 <= repair_number <= max_repairs or not reason.strip():
        raise ValueError("repair requires nonempty reason and an in-budget attempt number")
    metadata = {**candidate.spec.metadata, "repair_of": candidate.trial_id, "repair_reason": reason,
                "repair_number": repair_number, "implementation_status": "execution_checked_causal_recheck_pending"}
    spec = FactorSpec(candidate.spec.name, expression, version=f"repair{repair_number}",
                      parents=(candidate.spec.factor_id,), metadata=metadata)
    if required_fields(spec.expression) - required_fields(candidate.spec.expression):
        raise ValueError("implementation repair cannot add undeclared data fields")
    if spec.factor_id == candidate.spec.factor_id:
        raise ValueError("repair must change the actual executable expression")
    values = executor(spec)  # Critically re-execute the repaired formula, never reuse old acceptance.
    if (not isinstance(values, pd.DataFrame) or values.empty or not values.index.is_unique
            or not values.columns.is_unique or not values.index.is_monotonic_increasing):
        raise ValueError("repaired implementation must return an aligned chronological DataFrame")
    finite = values.where(np.isfinite(values))
    if not finite.notna().any().any() or not finite.nunique(axis=1).gt(1).any():
        raise ValueError("repair output is nonfinite or cross-sectionally degenerate")
    return replace(candidate, spec=spec,
        trial_id=digest({"parent_trial": candidate.trial_id, "spec_id": spec.spec_id, "repair_number": repair_number}))
