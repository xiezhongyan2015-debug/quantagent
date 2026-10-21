"""Descriptive arithmetic on declared frozen slots; no readers or certification.

Saved inputs must already be checked by the caller. Audit bindings are retained
references, never verified here. A zero success indicator does not imply a loss.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import re

VERSION = "frozen_slot_statistics_v1"
ARCHITECTURES = ("v4_evidence_workflow", "strong_single", "fixed_template_search")
CLASSIFICATIONS = frozenset(("valid_success", "valid_nonsuccess", "correct_abstention",
    "incorrect_positive", "not_started", "invalid_final", "missing_final",
    "incomplete_account", "no_trades", "budget_stop", "error",
    "unrecovered_failure", "contamination", "outcome_unknown"))
UNKNOWN = CLASSIFICATIONS - {"valid_success", "valid_nonsuccess", "correct_abstention",
                             "incorrect_positive", "no_trades"}
SLOT_KEYS = frozenset(("slot_id", "case_id", "case_definition_sha256", "family", "role",
    "repeat", "architecture", "calendar_sha256", "initial_capital_cny"))
OUTCOME_KEYS = frozenset(("slot_id", "classification", "reason", "outcome_unknown",
    "sharpe_rf2", "audit_binding", "shared_shock_ids", "provider_version", "costs"))


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 16384 and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _shocks(value):
    _need(isinstance(value, list) and len(value) <= 864 and all(_text(x) for x in value), "invalid shared_shock_ids")
    _need(len(value) == len(set(value)), "duplicate shared_shock_ids")


def _number(value):
    _need(type(value) in (str, int, float) and len(str(value)) <= 100, "invalid decimal")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    _need(number.is_finite() and abs(number.as_tuple().exponent) <= 100, "nonfinite/oversized decimal")
    return Fraction(number)


def _mean(values):
    return sum(values, Fraction()) / len(values) if values else None


def _string(value):
    return str(value) if value is not None else None


def _aggregate(rows, field):
    cases, families = defaultdict(list), defaultdict(list)
    for row in rows:
        cases[row["case_id"]].append(Fraction(row[field]))
    case_rates = {case: _mean(values) for case, values in cases.items()}
    for case, value in case_rates.items():
        families[next(row["family"] for row in rows if row["case_id"] == case)].append(value)
    family_rates = {family: _mean(values) for family, values in families.items()}
    return (_string(_mean([Fraction(row[field]) for row in rows])),
            _string(_mean(list(case_rates.values()))), _string(_mean(list(family_rates.values()))),
            {key: _string(value) for key, value in case_rates.items()},
            {key: _string(value) for key, value in family_rates.items()})


def summarize_frozen_slots(manifest, outcomes):
    """Return exact-rational descriptive summaries, preserving every declared slot.

    Input schema is versioned and closed. All (case, repeat) triples must include
    the three architectures. Outcomes may be missing, but cannot add slots. This
    function does not establish that the supplied manifest was actually frozen.
    """
    required = {"version", "input_kind", "statistical_draft_sha256", "slots",
                "original_order", "shared_shock_ids"}
    _need(isinstance(manifest, dict) and set(manifest) == required, "invalid manifest fields")
    _need(manifest["version"] == VERSION, "unsupported manifest version")
    _need(manifest["input_kind"] in ("generated_fixture", "caller_validated_saved_records"), "invalid input_kind")
    _need(_hash(manifest["statistical_draft_sha256"]), "invalid statistical draft hash")
    _shocks(manifest["shared_shock_ids"])
    slots, order = manifest["slots"], manifest["original_order"]
    _need(isinstance(slots, list) and 0 < len(slots) <= 864, "invalid original slots")
    by_id, definitions, case_metadata, triples = {}, {}, {}, defaultdict(set)
    for slot in slots:
        _need(isinstance(slot, dict) and set(slot) == SLOT_KEYS, "invalid slot fields")
        _need(all(_text(slot[key]) for key in ("slot_id", "case_id", "family")), "invalid slot identity")
        sid, case = slot["slot_id"], slot["case_id"]
        _need(sid not in by_id, "duplicate slot_id")
        _need(_hash(slot["case_definition_sha256"]) and _hash(slot["calendar_sha256"]), "invalid slot hash")
        _need(slot["role"] in ("real_research", "negative_control"), "invalid role")
        _need(type(slot["repeat"]) is int and slot["repeat"] >= 1, "invalid repeat")
        _need(slot["architecture"] in ARCHITECTURES, "invalid architecture")
        capital = _number(slot["initial_capital_cny"])
        _need(capital > 0, "nonpositive original capital")
        metadata = (slot["case_definition_sha256"], slot["family"], slot["role"], slot["calendar_sha256"], capital)
        _need(case_metadata.setdefault(case, metadata) == metadata, "case metadata mismatch")
        _need(definitions.setdefault(metadata[0], case) == case, "aliased case definition")
        key = (case, slot["repeat"])
        _need(slot["architecture"] not in triples[key], "duplicate original architecture start")
        triples[key].add(slot["architecture"])
        by_id[sid] = slot
    _need(all(arches == set(ARCHITECTURES) for arches in triples.values()), "incomplete original architecture triple")
    _need(isinstance(order, list) and len(order) == len(by_id) and all(_text(x) for x in order) and
          len(set(order)) == len(order) and set(order) == set(by_id), "invalid original_order")
    _need(isinstance(outcomes, list) and len(outcomes) <= len(slots), "invalid outcomes list/count")
    supplied = {}
    for outcome in outcomes:
        _need(isinstance(outcome, dict) and set(outcome) <= OUTCOME_KEYS and
              {"slot_id", "classification", "reason"} <= set(outcome), "invalid outcome fields")
        sid = outcome["slot_id"]
        _need(_text(sid) and sid in by_id and sid not in supplied, "unknown or duplicate outcome slot")
        _need(_text(outcome["classification"]) and outcome["classification"] in CLASSIFICATIONS and _text(outcome["reason"]), "invalid classification/reason")
        _need("outcome_unknown" not in outcome or type(outcome["outcome_unknown"]) is bool, "invalid outcome_unknown")
        _shocks(outcome.get("shared_shock_ids", []))
        _need(outcome.get("provider_version") is None or _text(outcome["provider_version"]), "invalid provider_version")
        _need(isinstance(outcome.get("costs", {}), dict), "invalid costs")
        if outcome.get("sharpe_rf2") is not None:
            _number(outcome["sharpe_rf2"])
        binding = outcome.get("audit_binding")
        _need(binding is None or (isinstance(binding, dict) and set(binding) == {"reference", "sha256"}
              and _text(binding["reference"]) and _hash(binding["sha256"])), "invalid audit reference")
        supplied[sid] = outcome
    # Reject non-JSON payloads, including nonfinite values hidden inside costs.
    canonical = json.dumps([manifest, outcomes], sort_keys=True, ensure_ascii=False, allow_nan=False)
    rows = []
    for sid in order:
        outcome = supplied.get(sid, {"classification": "missing_final", "reason": "outcome not supplied"})
        row = deepcopy(dict(by_id[sid], **{key: value for key, value in outcome.items() if key != "slot_id"}))
        row["source_outcome_present"] = sid in supplied
        row["outcome_unknown"] = outcome.get("outcome_unknown", row["classification"] in UNKNOWN)
        reference_present = manifest["input_kind"] == "generated_fixture" or outcome.get("audit_binding") is not None
        eligible = reference_present and not row["outcome_unknown"]
        row["success"] = int(row["role"] == "real_research" and eligible and row["classification"] == "valid_success")
        row["correct_abstention"] = int(row["role"] == "negative_control" and eligible and row["classification"] == "correct_abstention")
        row["counting_reason"] = "declared_classification" if eligible else "unknown_outcome" if row["outcome_unknown"] else "missing_audit_reference"
        row["sharpe_eligible"] = bool(row["role"] == "real_research" and eligible and
            row["classification"] in ("valid_success", "valid_nonsuccess") and outcome.get("sharpe_rf2") is not None)
        rows.append(row)
    architectures, controls, pairs = {}, {}, {}
    for arch in ARCHITECTURES:
        real = [row for row in rows if row["architecture"] == arch and row["role"] == "real_research"]
        raw, case_equal, family_equal, case_rates, family_rates = _aggregate(real, "success")
        sharpes = sorted(_number(row["sharpe_rf2"]) for row in real if row["sharpe_eligible"])
        middle = len(sharpes) // 2
        median = _mean(sharpes[middle - 1:middle + 1]) if sharpes and not len(sharpes) % 2 else sharpes[middle] if sharpes else None
        architectures[arch] = dict(original_real_slots=len(real), successes=sum(row["success"] for row in real),
            raw_success_rate=raw, case_equal_success_rate=case_equal, family_equal_success_rate=family_equal,
            case_success_rates=case_rates, family_success_rates=family_rates, eligible_sharpe_count=len(sharpes),
            eligible_sharpe_rf2_median=_string(median), missing_outcomes=sum(not row["source_outcome_present"] for row in real),
            outcome_unknown_slots=sum(row["outcome_unknown"] for row in real), classification_counts=dict(Counter(row["classification"] for row in real)))
        control = [row for row in rows if row["architecture"] == arch and row["role"] == "negative_control"]
        controls[arch] = dict(original_control_slots=len(control), correct_abstentions=sum(row["correct_abstention"] for row in control),
            correct_abstention_rate=_string(_mean([Fraction(row["correct_abstention"]) for row in control])))
    indexed = {(row["case_id"], row["repeat"], row["architecture"]): row for row in rows}
    for baseline in ARCHITECTURES[1:]:
        paired = []
        for v4 in (row for row in rows if row["architecture"] == ARCHITECTURES[0] and row["role"] == "real_research"):
            other = indexed[(v4["case_id"], v4["repeat"], baseline)]
            paired.append(dict(case_id=v4["case_id"], family=v4["family"], repeat=v4["repeat"],
                v4_slot_id=v4["slot_id"], baseline_slot_id=other["slot_id"], difference=v4["success"] - other["success"],
                v4_success=v4["success"], baseline_success=other["success"],
                v4_outcome_unknown=v4["outcome_unknown"], baseline_outcome_unknown=other["outcome_unknown"],
                v4_source_outcome_present=v4["source_outcome_present"], baseline_source_outcome_present=other["source_outcome_present"]))
        raw, case_equal, family_equal, case_rates, family_rates = _aggregate(paired, "difference")
        pairs[baseline] = dict(original_pairs=len(paired), raw_mean_difference=raw,
            case_equal_mean_difference=case_equal, family_equal_mean_difference=family_equal,
            case_mean_differences=case_rates, family_mean_differences=family_rates, pairs=paired)
    return dict(version=VERSION, input_kind=manifest["input_kind"], input_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        statistical_draft_sha256=manifest["statistical_draft_sha256"], original_order=deepcopy(order), slot_outcomes=rows,
        original_slots=len(rows), original_real_slots=sum(row["role"] == "real_research" for row in rows),
        architectures=architectures, paired_gains=pairs, negative_controls=controls,
        shared_shock_ids=deepcopy(manifest["shared_shock_ids"]), primary_weighting=None, lower_bound=None,
        lower_bound_status="not_evaluable", formal_financial_accepted=False, formal_architecture_improvement_accepted=False,
        input_authentication_performed=False, audit_bindings_verified=False,
        limitations=["Caller classifications and audit references are not authenticated or financially re-audited here.",
            "Original slots mean all slots declared by this manifest; freeze custody is not verified here.",
            "Zero success means insufficient valid-success evidence, not a negative economic return.",
            "Repeated starts, shared dates, budgets and provider shocks are not independent samples.",
            "Raw, case-equal and family-equal summaries are descriptive; no primary weighting or formal lower bound is selected."])
