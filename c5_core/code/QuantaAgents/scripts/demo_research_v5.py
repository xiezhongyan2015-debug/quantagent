"""Exercise actual V5 catalog/tools with generated evidence and saved declarations.

No model, network, historical market reader, strategy execution, or holdout
release is called. The hand-authored reflection is a synthetic wiring fixture,
not a controller-generated explanation for a real financial result.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from quanta_agents.meta_v3.ledger import digest
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v5.registry import Catalog, prepare_catalog, write_catalog, file_hash
from quanta_agents.meta_v5.tools import V5ResearchTools, ACTIONS


def fixture_policy():
    return {"version": "v5_stability_policy_v1", "expected_years": [2020, 2021],
        "annualization": 252, "risk_free_rate": .02, "min_sessions_per_year": 2,
        "min_paired_cell_fraction": 1., "min_unit_coverage_fraction": 1.,
        "min_positive_excess_year_fraction": .5, "min_positive_excess_unit_fraction": .5,
        "max_worst_year_excess_loss": .05, "max_drawdown": .5,
        "max_positive_pnl_concentration": 1., "max_stale_fraction": 0.,
        "require_known_fees": True, "require_exposure": True, "require_execution_certified": False}


def fixture_bundle(source):
    def series(values):
        return {"nav": values, "exposure": [.8] * 4, "fees": [0.] * 4, "stale": [0] * 4}
    return {"version": "v5_stability_bundle_v1", "candidate_id": "synthetic_difference", "program_hash": "a" * 64,
        "scope_id": "synthetic_shared_development", "split": "development", "expected_units": ["synthetic_a", "synthetic_b"],
        "pairs": [{"unit_id": code, "unit_kind": "stock", "initial_nav": 100.,
                   "calendar": ["2020-01-02", "2020-12-31", "2021-01-04", "2021-12-31"],
                   "candidate": series(values), "benchmark": series([100., 110., 115., 120.])}
                  for code, values in (("synthetic_a", [110., 120., 110., 100.]), ("synthetic_b", [110., 120., 130., 140.]))],
        "provenance": {"accounting_mode": "generated_engineering_continuous_nav", "execution_certified": False,
                       "account_currency": "CNY", "fee_unit": "CNY", "external_cash_flows": "none_after_initial",
                       "exposed": True, "source_class": "generated_engineering", "source_hashes": {str(source): file_hash(source)},
                       "cost_policy": {"kind": "explicit_generated_zero_fee_fixture"},
                       "source_scope_identity": {"kind": "synthetic_v5_tool_workflow"}}}


def create_demo_tools(root):
    """Real Catalog and ResearchTools initialization; only generated fixture data."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / "generation.json"
    save_once(source, {"source_class": "generated_engineering", "market_reads": 0,
                       "purpose": "fixed generated positive and negative yearly cells for interface testing"})
    prepared = prepare_catalog([fixture_bundle(source)], fixture_policy())
    identity = write_catalog(root, prepared)
    catalog = Catalog(root, identity)
    spec = importlib.util.spec_from_file_location("_v5_demo_generated_v4_case", PROJECT / "scripts/prepare_v3_calibration.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    case = generator.prepare(root / "generated_case", flat=True)
    contract = {"action_limits": {action: (64 if action == "inspect_stability" else 4) for action in ACTIONS},
                "required_scope_id": "synthetic_shared_development", "execution_unit_id": "synthetic_v4",
                "execution_scope_id": "synthetic_saved_execution", "benchmark_program_hashes": []}
    tools = V5ResearchTools(root, "workflow", case, [], catalog=catalog, v5_contract=contract)
    return tools, catalog, identity


def saved_call(tools, action, args, call_id=None):
    """Use the real tool dispatcher and retain a controller-style settled history."""
    call_id = call_id or f"workflow_{len(tools.history) + 1:03d}"
    row = {"id": call_id, "response": {"action": action, "arguments_json": json.dumps(args)}, "result": None}
    try:
        row["result"] = tools.execute(call_id, action, args)
    except Exception as exc:
        row.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        tools.history.append(row)
        raise
    row["status"] = "applied"
    tools.history.append(row)
    return row["result"]


def read_pages(tools, profile_id, tables=("summary", "cells", "issues"), limit=1):
    deliveries = []
    for table in tables:
        offset = 0
        while offset is not None:
            result = saved_call(tools, "inspect_stability", {"profile_id": profile_id, "table": table, "offset": offset, "limit": limit})
            public = result["public"]
            if public.get("delivery_blocked"):
                raise ValueError("A whole row could not be delivered; do not silently advance its cursor")
            artifact = tools._prior(result["evidence_id"], "inspect_stability")
            deliveries.append({"evidence_id": result["evidence_id"], "table": table, "offset": offset,
                               "next_offset": public["next_offset"], "delivered_ids": artifact["delivered_ids"],
                               "artifact_hash": result["artifact_hash"]})
            nxt = public["next_offset"]
            if nxt is not None and nxt <= offset:
                raise AssertionError("Page cursor did not progress")
            offset = nxt
    return deliveries


def fixture_reflection(profile):
    """Generated research declaration exercises the contract, not scientific truth."""
    ids = [cell["cell_id"] for cell in profile["cells"]]
    evidence, responses, experiments = [], [], []
    for n, issue in enumerate(profile["issues"]):
        eid, xid, aid, bid = f"fact_{n}", f"test_{n}", f"exposure_{n}", f"unknown_{n}"
        evidence.append({"evidence_id": eid, "source": "issue", "source_id": issue["id"], "field": "message", "reported_value": issue["message"]})
        responses.append({"issue_id": issue["id"], "cell_ids": issue["cell_ids"], "status": "provisional_explanation",
            "statement": "Synthetic fixture: distinguish exposure differences from an unidentified interaction.",
            "explanations": [{"explanation_id": aid, "statement": "Synthetic competing hypothesis: exposure accounts for the difference.", "evidence_ids": [eid]},
                             {"explanation_id": bid, "statement": "Synthetic competing hypothesis: an unidentified interaction remains after matching.", "evidence_ids": []}],
            "unknowns": ["No market mechanism has been established by this engineering fixture."],
            "counterevidence": {"state": "present", "evidence_ids": [eid], "note": "Preserve the original issue and affected cells."},
            "next_step": {"kind": "experiment", "experiment_id": xid}})
        experiments.append({"experiment_id": xid, "kind": "discriminating_test", "scope_id": profile["scope_id"], "split": "development",
            "retained_cell_ids": ids, "issue_ids": [issue["id"]], "explanation_ids": [aid, bid], "evidence_ids": [eid],
            "controls": [{"control_id": "original_candidate", "purpose": "Keep the original full generated path."},
                         {"control_id": "same_scope_benchmark", "purpose": "Retain the shared generated benchmark."}],
            "procedure": "Synthetic proposed test: compare exposure-matched residuals in every original cell.",
            "completion_criteria": "Save all matched and unmatched generated cells and signed residuals.",
            "prediction": {"statement": "Residual differences attenuate if exposure suffices.", "observable": "Paired residual differences with fixed exposure strata.",
                           "decision_rule": "A persistent residual refutes exposure as a sufficient explanation.", "supports_explanation_id": aid, "refutes_explanation_id": bid},
            "tradeable_condition": {"expression": None, "timing": "previous_completed_session", "status": "unvalidated_hypothesis_only"},
            "on_failure": "retain_unresolved"})
    return {"version": "v5_reflection_declaration_v1", **{k: profile[k] for k in ("candidate_id", "program_hash", "scope_id", "profile_hash", "split")},
            "retained_cell_ids": ids, "evidence": evidence, "issue_responses": responses, "experiments": experiments,
            "conclusion": {"text": "Controller-authored generated workflow fixture only; no real research explanation or model capability is claimed.",
                           "mechanism_status": "unresolved", "causal_mechanism_identified": False, "formal_target_success": False, "experiments_executed": False}}


def run_demo(root):
    tools, catalog, identity = create_demo_tools(root)
    candidate_id = "synthetic_difference"
    profile = catalog.profiles()[candidate_id]
    declaration = fixture_reflection(profile)
    rejected = None
    try:
        saved_call(tools, "record_stability_reflection", {"profile_id": candidate_id, "declaration": declaration})
    except Exception as exc:
        rejected = {"type": type(exc).__name__, "message": str(exc)}
    if rejected is None:
        raise AssertionError("Uninspected evidence was allowed to become a reflection")
    pages = read_pages(tools, candidate_id)
    result = saved_call(tools, "record_stability_reflection", {"profile_id": candidate_id, "declaration": declaration})
    artifact = tools._prior(result["evidence_id"], "record_stability_reflection")
    assert artifact["profile_hash"] == profile["profile_hash"]
    assert tools._reflection(profile)[0] == result["evidence_id"]
    state = tools.research_state()
    assert all(not action["execution_authorized"] for row in state["candidates"] for action in row["next_actions"])
    report = {"version": "v5_generated_workflow_demo_v1", "status": "completed_engineering_only",
              "model_calls": 0, "network_calls": 0, "market_value_reads": 0, "strategy_executions": 0,
              "catalog_identity": identity, "profile_hash": profile["profile_hash"], "cell_count": len(profile["cells"]),
              "issue_count": len(profile["issues"]), "missing_read_reflection_rejected": rejected,
              "page_deliveries": pages, "reflection_evidence_id": result["evidence_id"],
              "reflection_hash": artifact["reflection_hash"], "research_state": state,
              "formal_target_success": False, "causal_mechanism_identified": False,
              "limitations": ["Generated dates and NAV values test API wiring, not investment skill.",
                              "The hand-authored reflection is synthetic; the controller must not author a model's real scientific explanation.",
                              "No declared experiment was executed and no issue was scientifically resolved."]}
    save_once(Path(root) / "workflow_history.json", tools.history)
    save_once(Path(root) / "demo_report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New isolated output directory; existing directories are not overwritten")
    args = parser.parse_args()
    result = run_demo(args.root)
    print(json.dumps({"status": result["status"], "report": str(args.root.resolve() / "demo_report.json"),
                      "cells": result["cell_count"], "issues": result["issue_count"], "model_calls": 0}, ensure_ascii=False, indent=2))
