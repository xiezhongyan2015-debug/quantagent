"""Evidence-bound research declarations and deterministic follow-up scheduling.

This module validates identity, completeness and typed claims. It does not
certify the truth of prose or identify a causal mechanism. A self-consistent
profile hash is not source authentication: callers must obtain profiles from
the controller's trusted development registry, never directly from the model.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from quanta_agents.meta.factor_algebra import validate_expression


DECLARATION_VERSION = "v5_reflection_declaration_v1"
RECORD_VERSION = "v5_reflection_record_v1"
CAUSAL_FIELDS = ("open", "high", "low", "close", "volume", "amount", "float_market_cap", "total_market_cap")
CELL_FIELDS = {"unit_id", "year", "sessions", "complete", "excess_return", "quality_flags"} | {
    f"{arm}.{metric}" for arm in ("candidate_metrics", "benchmark_metrics")
    for metric in ("return", "sharpe", "max_drawdown", "average_exposure", "fees")
}
ISSUE_FIELDS = {"severity", "code", "cell_ids", "message"}


def _digest(value: Any) -> str:
    try:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Only finite JSON values are allowed") from exc
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _list(item: dict, minimum: int = 0) -> dict:
    return {"type": "array", "items": item, "minItems": minimum}


TEXT = {"type": "string", "minLength": 1, "maxLength": 12000}
ID = {"type": "string", "minLength": 1, "maxLength": 256}
IDS = _list(ID)
FALSE = {"const": False}
EVIDENCE_SCHEMA = _object({"evidence_id": ID, "source": {"enum": ["cell", "issue"]},
                           "source_id": ID, "field": ID, "reported_value": {}})
EXPLANATION_SCHEMA = _object({"explanation_id": ID, "statement": TEXT, "evidence_ids": IDS})
COUNTEREVIDENCE_SCHEMA = _object({"state": {"enum": ["present", "not_yet_assessed", "none_identified"]},
                                "evidence_ids": IDS, "note": TEXT})
PREDICTION_SCHEMA = _object({"statement": TEXT, "observable": TEXT, "decision_rule": TEXT,
                            "supports_explanation_id": ID, "refutes_explanation_id": ID})
EXPERIMENT_SCHEMA = _object({
    "experiment_id": ID, "kind": {"enum": ["source_completion", "discriminating_test"]},
    "scope_id": ID, "split": {"const": "development"}, "retained_cell_ids": IDS,
    "issue_ids": _list(ID, 1), "explanation_ids": IDS, "evidence_ids": IDS,
    "controls": _list(_object({"control_id": ID, "purpose": TEXT}), 2),
    "procedure": TEXT, "completion_criteria": TEXT,
    "prediction": {"anyOf": [PREDICTION_SCHEMA, {"type": "null"}]},
    "tradeable_condition": _object({"expression": {"anyOf": [TEXT, {"type": "null"}]},
                                      "timing": {"const": "previous_completed_session"},
                                      "status": {"const": "unvalidated_hypothesis_only"}}),
    "on_failure": {"enum": ["retain_unresolved", "stop"]},
})
RESPONSE_SCHEMA = _object({
    "issue_id": ID, "cell_ids": IDS,
    "status": {"enum": ["unresolved", "data_gap", "provisional_explanation", "stopped"]},
    "statement": TEXT, "explanations": _list(EXPLANATION_SCHEMA), "unknowns": _list(TEXT, 1),
    "counterevidence": COUNTEREVIDENCE_SCHEMA,
    "next_step": {"anyOf": [
        _object({"kind": {"const": "experiment"}, "experiment_id": ID}),
        _object({"kind": {"const": "stop"}, "reason": TEXT}),
    ]},
})
DECLARATION_SCHEMA = _object({
    "version": {"const": DECLARATION_VERSION}, "candidate_id": ID, "program_hash": ID,
    "scope_id": ID, "profile_hash": ID, "split": {"const": "development"},
    "retained_cell_ids": IDS, "evidence": _list(EVIDENCE_SCHEMA),
    "issue_responses": _list(RESPONSE_SCHEMA), "experiments": _list(EXPERIMENT_SCHEMA),
    "conclusion": _object({"text": TEXT, "mechanism_status": {"const": "unresolved"},
                            "causal_mechanism_identified": FALSE, "formal_target_success": FALSE,
                            "experiments_executed": FALSE}),
})


def public_contract() -> dict:
    """Return a JSON schema plus the semantic limits of this declaration API."""
    return deepcopy({
        "version": DECLARATION_VERSION, "declaration_schema": DECLARATION_SCHEMA,
        "trusted_input": "Controller-registered development profile; matching a model-supplied hash alone is not provenance.",
        "evidence_cell_fields": sorted(CELL_FIELDS), "evidence_issue_fields": sorted(ISSUE_FIELDS),
        "discriminating_control_ids": ["original_candidate", "same_scope_benchmark"],
        "source_completion_control_ids": ["source_identity", "full_scope_coverage"],
        "tradeable_condition_fields": list(CAUSAL_FIELDS),
        "uncertainty": "Unknown mechanisms may remain unresolved or stop. Text completeness never means identified mechanism or executed experiment.",
        "scope": "All profile cells and issues are retained; no new data split, costs, benchmark, calendar or source scope is authorized.",
    })


def _schema(value: Any, schema: dict, path: str) -> None:
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _schema(value, option, path)
                return
            except ValueError:
                pass
        raise ValueError(f"{path}: does not match the permitted alternatives")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(f"{path}: invalid fixed claim")
    if "enum" in schema and (not isinstance(value, str) or value not in schema["enum"]):
        raise ValueError(f"{path}: unsupported value")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ValueError(f"{path}: must contain exactly {schema['required']}")
        for name, item in value.items():
            _schema(item, schema["properties"][name], f"{path}.{name}")
    elif kind == "array":
        if not isinstance(value, list) or len(value) < schema["minItems"]:
            raise ValueError(f"{path}: incomplete list")
        for i, item in enumerate(value):
            _schema(item, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if not isinstance(value, str) or not value.strip() or not schema["minLength"] <= len(value) <= schema["maxLength"]:
            raise ValueError(f"{path}: nonempty bounded text required")
    elif kind == "null" and value is not None:
        raise ValueError(f"{path}: must be null")


def _index(rows: list, id_key: str, label: str) -> dict:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(id_key), str) or not row[id_key].strip() or row[id_key] in result:
            raise ValueError(f"{label} has invalid or duplicate identity")
        result[row[id_key]] = row
    return result


def _profile(profile: dict) -> tuple[dict, dict]:
    if not isinstance(profile, dict):
        raise ValueError("profile must be a controller-registered object")
    required = ("candidate_id", "program_hash", "scope_id", "profile_hash", "split", "comparison_identity", "development_eligible", "formal_target_success")
    if any(k not in profile for k in required):
        raise ValueError("profile lacks frozen identity or research scope")
    if profile["split"] != "development" or profile["formal_target_success"] is not False:
        raise ValueError("reflection consumes development profiles only; no formal success")
    if type(profile["development_eligible"]) is not bool:
        raise ValueError("profile eligibility must be explicit boolean")
    expected = _digest({k: v for k, v in profile.items() if k != "profile_hash"})
    if profile["profile_hash"] != expected:
        raise ValueError("profile hash mismatch")
    cells, issues = _index(profile.get("cells"), "cell_id", "profile cells"), _index(profile.get("issues"), "id", "profile issues")
    for issue in issues.values():
        refs = issue.get("cell_ids")
        if not isinstance(refs, list) or any(not isinstance(c, str) for c in refs) or len(refs) != len(set(refs)) or any(c not in cells for c in refs):
            raise ValueError("profile issue contains nonexistent or duplicate cell")
    return cells, issues


def _references(ids: list, known: dict, label: str, *, require_nonempty=False) -> None:
    if (require_nonempty and not ids) or len(ids) != len(set(ids)) or any(i not in known for i in ids):
        raise ValueError(f"{label}: unknown, duplicate or missing references")


def _evidence_ref(reference: dict, cells: dict, issues: dict) -> dict:
    _schema(reference, EVIDENCE_SCHEMA, "evidence")
    source, allowed = (cells, CELL_FIELDS) if reference["source"] == "cell" else (issues, ISSUE_FIELDS)
    if reference["source_id"] not in source or reference["field"] not in allowed:
        raise ValueError("evidence references nonexistent cell/issue or unsupported field")
    value = source[reference["source_id"]]
    for part in reference["field"].split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("evidence field is absent, not known zero")
        value = value[part]
    if _digest(value) != _digest(reference["reported_value"]):
        raise ValueError("evidence reported_value differs from the saved profile")
    return deepcopy(reference)


def validate_evidence_ref(reference: dict, profile: dict) -> dict:
    """Validate a structured factual claim against a registered profile field."""
    _digest(reference)
    cells, issues = _profile(profile)
    return _evidence_ref(reference, cells, issues)


# A response's evidence reference is the same typed claim, not a prose verifier.
validate_response_ref = validate_evidence_ref


def validate_reflection(declaration: dict, profile: dict) -> dict:
    cells, issues = _profile(profile)
    _digest(declaration)
    _schema(declaration, DECLARATION_SCHEMA, "declaration")
    for field in ("candidate_id", "program_hash", "scope_id", "profile_hash", "split"):
        if declaration[field] != profile[field]:
            raise ValueError(f"reflection {field} does not bind the current profile")
    if declaration["retained_cell_ids"] != list(cells):
        raise ValueError("reflection cannot drop, reorder or replace frozen cells")
    evidence = _index(declaration["evidence"], "evidence_id", "evidence")
    for item in evidence.values():
        _evidence_ref(item, cells, issues)
    responses = _index(declaration["issue_responses"], "issue_id", "issue responses")
    if set(responses) != set(issues):
        raise ValueError("every original issue requires exactly one response; no dropped issue")
    experiments = _index(declaration["experiments"], "experiment_id", "experiments")
    explanations, explanation_issue = {}, {}
    for issue_id, response in responses.items():
        if response["cell_ids"] != issues[issue_id]["cell_ids"]:
            raise ValueError("issue response must retain every affected cell")
        items = _index(response["explanations"], "explanation_id", "competing explanations")
        if response["status"] == "provisional_explanation" and len(items) < 2:
            raise ValueError("a provisional explanation needs competing explanations")
        for explanation_id, item in items.items():
            if explanation_id in explanations:
                raise ValueError("explanation ids must be globally unique")
            _references(item["evidence_ids"], evidence, "explanation evidence")
            explanations[explanation_id], explanation_issue[explanation_id] = item, issue_id
        counter = response["counterevidence"]
        _references(counter["evidence_ids"], evidence, "counterevidence", require_nonempty=counter["state"] == "present")
        step = response["next_step"]
        if (response["status"] == "stopped") != (step["kind"] == "stop"):
            raise ValueError("stopped issue requires explicit stop reason; active issue requires experiment")
        if step["kind"] == "experiment" and step["experiment_id"] not in experiments:
            raise ValueError("issue next experiment does not exist")
    used = set()
    for experiment_id, experiment in experiments.items():
        if experiment["scope_id"] != profile["scope_id"] or experiment["retained_cell_ids"] != list(cells):
            raise ValueError("experiment must retain the full original development scope")
        _references(experiment["issue_ids"], issues, "experiment issues", require_nonempty=True)
        _references(experiment["explanation_ids"], explanations, "experiment explanations")
        _references(experiment["evidence_ids"], evidence, "experiment evidence")
        if any(explanation_issue[e] not in experiment["issue_ids"] for e in experiment["explanation_ids"]):
            raise ValueError("experiment explanation belongs to an unrelated issue")
        for issue_id in experiment["issue_ids"]:
            step = responses[issue_id]["next_step"]
            if step["kind"] != "experiment" or step["experiment_id"] != experiment_id:
                raise ValueError("experiment and issue next-step bindings disagree")
            used.add(experiment_id)
        controls = _index(experiment["controls"], "control_id", "experiment controls")
        source_completion = experiment["kind"] == "source_completion"
        required_controls = {"source_identity", "full_scope_coverage"} if source_completion else {"original_candidate", "same_scope_benchmark"}
        if not required_controls.issubset(controls):
            raise ValueError("experiment is missing mandatory controls")
        prediction = experiment["prediction"]
        if source_completion:
            if prediction is not None or experiment["tradeable_condition"]["expression"] is not None:
                raise ValueError("source completion cannot pretend to test a trading mechanism")
        else:
            if len(experiment["explanation_ids"]) < 2 or prediction is None:
                raise ValueError("discriminating experiment requires competing explanations and a falsifiable prediction")
            refs = [prediction["supports_explanation_id"], prediction["refutes_explanation_id"]]
            _references(refs, {e: explanations[e] for e in experiment["explanation_ids"]}, "prediction alternatives", require_nonempty=True)
            _references(experiment["evidence_ids"], evidence, "discriminating evidence", require_nonempty=True)
        expression = experiment["tradeable_condition"]["expression"]
        if expression is not None:
            # No date/year/stock identity fields or future operators are available.
            # New mechanism ideas may remain in prose until tools support them.
            validate_expression(expression, CAUSAL_FIELDS)
    if used != set(experiments):
        raise ValueError("unbound experiment cannot be scheduled")
    record = {
        "version": RECORD_VERSION,
        **{k: profile[k] for k in ("candidate_id", "program_hash", "scope_id", "profile_hash", "split")},
        "declaration": deepcopy(declaration), "quality_validated": True,
        "semantic_truth_verified": False, "causal_mechanism_identified": False,
        "formal_target_success": False, "experiments_executed": False,
        "unresolved_issue_ids": list(issues),
        "counterevidence_state": [{"issue_id": k, **deepcopy(v["counterevidence"])} for k, v in responses.items()],
        "authority": "researcher_declaration_only; controller approval and actual experiment evidence remain separate",
    }
    record["reflection_hash"] = _digest(record)
    return record


def _coverage_issue(issue: dict, cells: dict) -> bool:
    # Exact machine issue codes and structural incompleteness, never prose matching.
    codes = {"missing_unit", "missing_expected_unit", "incomplete_cell", "missing_candidate", "missing_benchmark",
             "incomplete_pair", "source_quality", "source_quality_gap", "missing_source_hashes", "calendar_gap",
             "coverage_gap", "incomplete_coverage", "missing_year", "missing_source_identity",
             "insufficient_sessions", "candidate_nav_incomplete", "benchmark_nav_incomplete", "fees_unknown",
             "exposure_unknown", "stale_quality_unknown", "stale_quality_exceeded", "cost_policy_unknown",
             "coverage_below_policy", "execution_not_certified"}
    return issue["code"] in codes or any(cells[c].get("complete") is False for c in issue["cell_ids"])


def next_actions(profile: dict, reflection: dict | None = None) -> list[dict]:
    """Deterministic proposals only; never execute, release, or certify a candidate."""
    cells, issues = _profile(profile)
    declaration, counter = None, []
    if reflection is not None:
        if not isinstance(reflection, dict) or "declaration" not in reflection:
            raise ValueError("next_actions requires a validated reflection record")
        checked = validate_reflection(reflection["declaration"], profile)
        if _digest(reflection) != _digest(checked):
            raise ValueError("reflection record changed or belongs to an old profile")
        declaration, counter = checked["declaration"], checked["counterevidence_state"]
    actions = []
    coverage = sorted((i for i in issues if _coverage_issue(issues[i], cells)), key=lambda i: (issues[i]["code"], i))
    unresolved = sorted(issues)

    def append(action, priority, issue_ids, **extra):
        record = {"action": action, "priority": priority, "issue_ids": issue_ids,
                  "cell_ids": [c for c in cells if any(c in issues[i]["cell_ids"] for i in issue_ids)],
                  "profile_hash": profile["profile_hash"], "scope_id": profile["scope_id"], "split": "development",
                  "unresolved_issue_ids": unresolved, "counterevidence_state": deepcopy(counter),
                  "execution_authorized": False, "formal_target_success": False, **extra}
        record["action_id"] = _digest(record)
        actions.append(record)

    stopped_ids = {r["issue_id"] for r in declaration["issue_responses"] if r["status"] == "stopped"} if declaration else set()
    source_planned = {i for e in declaration["experiments"] if e["kind"] == "source_completion" for i in e["issue_ids"]} if declaration else set()
    unplanned_coverage = [i for i in coverage if i not in stopped_ids and i not in source_planned]
    if unplanned_coverage:
        append("complete_evidence", 10, unplanned_coverage, block_reason="coverage_or_source_gap", ready=True)
    if declaration is None:
        remaining = [i for i in unresolved if i not in coverage]
        if remaining:
            append("request_difference_reflection", 20, remaining, ready=not coverage)
    else:
        for experiment in sorted(declaration["experiments"], key=lambda e: (e["kind"] != "source_completion", e["experiment_id"])):
            source_completion = experiment["kind"] == "source_completion"
            append("complete_evidence" if source_completion else "run_discriminating_experiment", 10 if source_completion else 30,
                   experiment["issue_ids"], experiment_id=experiment["experiment_id"],
                   reflection_hash=reflection["reflection_hash"], ready=source_completion or not coverage,
                   requires_new_saved_evidence=True)
        stopped = [r["issue_id"] for r in declaration["issue_responses"] if r["status"] == "stopped"]
        if stopped:
            append("abstain", 40, sorted(stopped), ready=True,
                   stop_reasons=[{"issue_id": r["issue_id"], "reason": r["next_step"]["reason"]} for r in declaration["issue_responses"] if r["status"] == "stopped"])
    if not issues:
        if profile["development_eligible"]:
            append("prepare_confirmation", 50, [], ready=True, holdout_release_authorized=False,
                   prerequisite="controller must separately admit a frozen confirmation protocol; this action releases no data")
        else:
            append("abstain", 40, [], ready=True, block_reason="development_not_eligible_without_an_explained_issue")
    return sorted(actions, key=lambda a: (a["priority"], a["action"], a.get("experiment_id", ""), a["action_id"]))
