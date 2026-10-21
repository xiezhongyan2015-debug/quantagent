"""Offline, source-pinned recovery of the first call05 reference rejection.

The paid response, original validator and protocol are immutable. A separately
verified reference projection is used only to satisfy that old compiler's narrow
reference grammar. Every decision and every original output reference is retained.
No model, market loader, factor computation, or account executor is called here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re

from . import information_application as original

VERSION = "v6_application_reference_recovery_v1"
ORIGINAL_VALIDATOR_SHA256 = "4be6799762f4064b8e850f8a97cdae23527f12e5c981e9896e4a259a6070d273"
INTENT = "reference_recovery_intent.json"
RECEIPT = "reference_recovery_receipt.json"


def _hash(value):
    return hashlib.sha256(original.dump(value).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _resolve(value, pointer):
    """Strict JSON pointer; reject negative/aliased array indices and escapes."""
    _require(isinstance(pointer, str) and pointer.startswith("/"), "invalid evidence pointer")
    tokens = pointer.split("/")[1:]
    try:
        for token in tokens:
            _require(re.search(r"~(?![01])", token) is None, "invalid pointer escape")
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                _require(re.fullmatch(r"0|[1-9][0-9]*", token) is not None, "noncanonical array reference")
                value = value[int(token)]
            else:
                _require(isinstance(value, dict), "reference descends through a scalar")
                value = value[token]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("unresolved recovery evidence pointer: " + pointer) from exc
    return value


def _decisions(response):
    value = deepcopy(response)
    for item in value["factor_assessments"]:
        item.pop("evidence_refs")
    return value


def project_reference_response(response, fit):
    """Pure same-factor/global-limit whitelist, not an economic correction."""
    original._schema(response, original.SCHEMA)
    projected = deepcopy(response)
    mapping = {row["factor_key"]: row for row in fit["factors"]}
    _require(len(mapping) == len(fit["factors"]), "ambiguous fit identities")
    checks = []
    for before, after in zip(response["factor_assessments"], projected["factor_assessments"]):
        key = before["factor_key"]
        _require(key in mapping, "unknown assessed factor")
        retained = []
        for pointer in before["evidence_refs"]:
            if pointer.startswith("/factors/"):
                # The unchanged old compiler checks these, including same-row
                # ownership and the complete required F7 risk reference set.
                retained.append(pointer)
                continue
            kind, target_key = None, None
            if pointer == "/hf0280_source_limitations":
                _require(key == "HF0280", "HF source limitations belong only to HF0280")
                kind, target_key = "hf0280_source_limitations", key
            elif pointer == "/return_ic_bootstrap_supplement/proposal_timing":
                kind = "global_post_result_ci_timing"
            elif re.fullmatch(r"/return_ic_bootstrap_supplement/factors/(0|[1-9][0-9]*)", pointer):
                row = _resolve(fit, pointer)
                _require(isinstance(row, dict) and row.get("factor_key") == key,
                         "supplemental CI reference cites another factor")
                _require(row.get("factor_id") == mapping[key].get("factor_id")
                         and row.get("direction") == mapping[key].get("direction")
                         and row.get("source_factor_status") == mapping[key].get("status"),
                         "supplemental CI row identity/direction/status differs")
                kind, target_key = "same_factor_saved_return_ci", key
            else:
                raise ValueError("reference is outside this recovery whitelist: " + pointer)
            value = _resolve(fit, pointer)
            if kind == "global_post_result_ci_timing":
                _require(isinstance(value, dict) and value.get("post_hoc_supplement") is True
                         and value.get("pre_results_preregistration_claimed") is False,
                         "global CI reference does not retain post-result timing")
            if kind == "hf0280_source_limitations":
                _require(isinstance(value, list) and bool(value) and all(isinstance(v, str) and v for v in value),
                         "HF source limitations must be the supplied nonempty text list")
            checks.append({"factor_key": key, "pointer": pointer, "kind": kind, "target_factor_key": target_key,
                           "resolved_value_sha256": _hash(value), "resolved_type": type(value).__name__,
                           "independently_verified": True, "removed_only_from_compiler_projection": True})
        after["evidence_refs"] = retained
    _require(bool(checks), "this recovery requires an actual allowed supplemental reference")
    _require(_decisions(projected) == _decisions(response), "reference projection changed an economic decision")
    return projected, checks


def _context(root):
    root = Path(root).resolve()
    folder = root / "cycles" / original.CYCLE
    validator = Path(original.__file__).resolve()
    _require(original.file_hash(validator) == ORIGINAL_VALIDATOR_SHA256, "original validator is not the frozen 4be version")
    protocol = original.read(folder / "protocol.json")
    _require(protocol["source_sha256"]["information_application.py"] == ORIGINAL_VALIDATOR_SHA256,
             "original protocol validator identity changed")
    original._verify(protocol["input_artifacts"])
    # These are the already-delivered prompt views. Do not call _load_context,
    # closed_observations, or deserialize native account/factor data archives.
    fit = original.read(folder / "admitted_fit_factor_view.json")
    observations = original.read(folder / "prior_account_observations.json")
    receipt = original._application_receipt(root, protocol, fit, observations)
    failure_path = folder / "application_validation_failure.json"
    failure = original.read(failure_path)
    receipt_path = root / "model_calls" / original.CALL / "admitted_receipt.json"
    _require(failure == {"type": "ValueError", "error": "assessment cites another factor",
        "source_receipt_sha256": original.file_hash(receipt_path), "all_proposals_retained_in_original_receipt": True,
        "automatic_retry": False, "account_executions": 0}, "not the exact retained reference-only validation failure")
    # Reproduce the original rejection, instead of trusting its error label.
    try:
        original.validate_application_response(receipt["response"], fit, protocol)
    except ValueError as exc:
        _require(str(exc) == failure["error"], "original compiler now fails for a different reason")
    else:
        raise ValueError("original response does not need this reference recovery")
    _, model_proofs = original._identity(root, original.CALL)
    paths = [folder / name for name in ("protocol.json", "admitted_fit_factor_view.json", "prior_account_observations.json",
                                        "application_validation_failure.json", "model_exposure.json")]
    proofs = protocol["input_artifacts"] + [original._proof(p) for p in paths] + model_proofs
    sources = [original._proof(Path(__file__).resolve()), original._proof(validator)]
    return root, folder, protocol, fit, receipt, proofs, sources


def _intent(root, folder, protocol, fit, receipt, proofs, sources):
    projection, checks = project_reference_response(receipt["response"], fit)
    value = {"version": VERSION, "cycle_id": original.CYCLE, "kind": "offline_reference_only_recovery",
        "original_validator_sha256": ORIGINAL_VALIDATOR_SHA256, "source_proofs": sources, "input_artifacts": proofs,
        "original_response_sha256": _hash(receipt["response"]), "projection_sha256": _hash(projection),
        "economic_decisions_sha256": _hash(_decisions(receipt["response"])), "extra_reference_checks": checks,
        "allowed_extra_references": ["same-factor exact CI row", "HF0280-only source limitations", "global post-result CI proposal_timing"],
        "original_prompt_and_response_preserved": True, "original_validation_failure_preserved": True,
        "economic_decisions_unchanged": True, "metadata_only_reference_repair": True,
        "new_financial_observations": 0, "candidate_modifications": 0, "model_calls": 0, "account_executions": 0,
        "automatic_retry": False, "financial_success": False}
    return value, projection


def _outputs(root, folder, protocol, fit, receipt, projection):
    application, combination = original._declarations(root, folder, protocol, fit, projection)
    application["factor_assessments"] = deepcopy(receipt["response"]["factor_assessments"])
    _require([row["proposal"] for row in application["proposal_records"]] == receipt["response"]["portfolios"],
             "compiled economic proposals differ from the paid response")
    combination["application_declaration_sha256"] = _hash(application)
    return application, combination


def _receipt(folder, intent, application, combination):
    return {"version": VERSION, "status": "completed", "intent_sha256": original.file_hash(folder / INTENT),
        "input_artifacts": intent["input_artifacts"], "source_proofs": intent["source_proofs"],
        "original_validator_sha256": ORIGINAL_VALIDATOR_SHA256,
        "original_receipt_sha256": application["source_receipt_sha256"],
        "original_failure_sha256": original.file_hash(folder / "application_validation_failure.json"),
        "extra_reference_checks": intent["extra_reference_checks"], "projection_sha256": intent["projection_sha256"],
        "economic_decisions_sha256": intent["economic_decisions_sha256"], "economic_decisions_unchanged": True,
        "original_assessment_refs_restored": True, "artifacts": [original._proof(folder / name) for name in
            ("application_declaration.json", "combination_declaration.json")],
        "new_proposals": combination["new_proposals"], "model_calls": 0, "account_executions": 0,
        "metadata_only_reference_repair": True, "new_financial_observations": 0, "candidate_modifications": 0,
        "original_validation_failure_preserved": True, "financial_success": False}


def recover_application_references(root):
    """One explicit offline attempt; an unfinished recovery is never retried."""
    folder = Path(root).resolve() / "cycles" / original.CYCLE
    if (folder / RECEIPT).exists():
        return verify_recovered_application_declaration(root)[2]
    _require(not (folder / INTENT).exists(), "unfinished offline recovery exists; inspect its original state, do not retry")
    _require(not any((folder / name).exists() for name in ("application_declaration.json", "combination_declaration.json")),
             "recovery must not replace an existing declaration")
    context = _context(root)
    root, folder, protocol, fit, receipt, proofs, sources = context
    intent, projection = _intent(*context)
    original.save_once(folder / INTENT, intent)
    try:
        application, combination = _outputs(root, folder, protocol, fit, receipt, projection)
        original._verify(proofs + sources)
        original.save_once(folder / "application_declaration.json", application)
        original.save_once(folder / "combination_declaration.json", combination)
        original.save_once(folder / RECEIPT, _receipt(folder, intent, application, combination))
    except Exception as exc:
        original.save_once(folder / "reference_recovery_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "intent_sha256": original.file_hash(folder / INTENT), "original_failure_preserved": True,
            "automatic_retry": False, "model_calls": 0, "account_executions": 0})
        raise
    return combination


def verify_recovered_application_declaration(root):
    """Read-only account handoff; exact replay of the frozen offline recovery."""
    context = _context(root)
    root, folder, protocol, fit, receipt, proofs, sources = context
    intent, projection = _intent(*context)
    _require(original.read(folder / INTENT) == intent, "reference recovery intent/input/source changed")
    application, combination = _outputs(root, folder, protocol, fit, receipt, projection)
    _require(original.read(folder / "application_declaration.json") == application
             and original.read(folder / "combination_declaration.json") == combination
             and original.file_hash(folder / "application_declaration.json") == combination["application_declaration_sha256"],
             "recovered declaration differs from the original paid economic decisions or references")
    _require(original.read(folder / RECEIPT) == _receipt(folder, intent, application, combination),
             "reference recovery completion proof changed")
    return folder, protocol, combination, fit
