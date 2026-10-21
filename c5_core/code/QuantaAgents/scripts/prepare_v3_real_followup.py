"""Freeze one derived development check after the first real final; no model call."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.evidence_views import compact_events, horizon_summary, input_coverage
from quanta_agents.meta_v3.kernel import ROOT, module
from quanta_agents.meta_v3.ledger import Ledger, digest, serial, worker_lease
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins


def main():
    parent_root = ROOT / "experiment_traces/meta_framework_v3/real_research_001"
    root = ROOT / "experiment_traces/meta_framework_v3/real_research_002"
    manifest_file = ROOT / "docs/research/meta_framework_v3_handoff/real_research_manifest_002.json"
    parent = Ledger(parent_root)
    previous = json.loads((parent_root / "plan.json").read_text(encoding="utf-8"))
    task_id = "real_activity_001"
    with worker_lease(parent_root):
        status = parent.status(task_id)
        assert status["terminal"] == "submitted" and status["unknown_or_pending_reserve"] == 0
        assert all(c["status"] in ("applied", "failed") for c in status["calls"])
        verifier = module("meta.codex_gateway").verify_saved_completion
        for call in status["calls"]:
            c = parent.call(call["id"])
            checked = verifier(parent_root / "calls" / c["id"], expected_prompt_hash=c["intent"]["prompt_hash"],
                expected_schema=c["intent"]["schema"], expected_artifact_sha256=c["receipt"]["artifact_sha256"])
            assert checked["response"] == c["receipt"]["response"] and checked["usage"] == c["receipt"]["usage"]
        for file, pin in previous["provenance"]["source_pins"].items():
            assert hashlib.sha256((parent_root / "source_snapshot" / file).read_bytes()).hexdigest() == pin
        report = parent_root / "audits/saved_001/real_activity_001_model_report.md"
        audit = parent_root / "audits/saved_001/audit.json"
        saved_audit = json.loads(audit.read_text(encoding="utf-8"))
        final_args = json.loads(parent.call(status["final_call"])["receipt"]["response"]["arguments_json"])
        assert saved_audit["authentic_reports"][task_id]["report"] == final_args
        assert final_args["outcome"] == "abstain" and not saved_audit["executions"]
        assert status["known_tokens"] == saved_audit["known_tokens"] == 171824
        case = previous["tasks"][task_id]["case"]
        original_case_hash = digest(case)
        for path in (report, audit):
            case["evidence_sources"].append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        case["description"] += " Derived development after a closed genuine abstention; same exposed prices and account scope, not a new independent case."
        policy = ClosingPolicy(task_calls=12, stage_calls=12, stage_tokens=600000)
        manifest = {"kind": "real_saved_development_002", "created_at": datetime.now(timezone.utc).isoformat(),
            "parent_root": str(parent_root), "parent_final_call": status["final_call"],
            "parent_case_hash": original_case_hash, "case_hash": digest(case),
            "purpose": "One derived check of a proposed but unexecuted idea after explicit budget and compact evidence fixes. No reopening or replacement of the original eight-call final.",
            "model": "gpt-6-astra", "effort": "xhigh", "maximum_model_calls": 12,
            "nominal_token_budget": 600000, "maximum_candidates": 3, "candidate_subattempts_each": 3,
            "deadline_epoch": previous["deadline_epoch"], "original_full_capital": case["initial_cash"],
            "selection": "Same previously exposed two stocks and sixteen sessions. No outcome-based row removal or new source scan.",
            "changes": ["Actual remaining policy and shared call/token/time room shown to model", "Complete aggregate view and optional compact event pages; no model summarizer or discarded originals"],
            "evaluation": ["Retain actual autonomous actions and final whether execute or abstain", "If execution occurs, reconcile full cash, fees, slippage, positions and rejections from saved events", "No tiny-sample formal score or claim of architecture superiority"],
            "stop_conditions": ["One final attempt", "Unresolved cost or child state blocks without paid retry", "Source drift, frozen deadline or admission budget", "One derived check only; no automatic repeat-until-execution/success"],
            "formal_success_denominator": 0, "old_v3_known_tokens": 564943,
            "old_real_known_tokens": 171824, "old_v2_known_tokens": 491954, "old_v2_unknown_reserve": 80000,
            "old_campaign_resume_authorized": False, "preparer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        save_once(manifest_file, manifest)
        # Offline saved-evidence replay verifies the information change without
        # rescanning prices or computing a new strategy/horizon outcome.
        evidence_path = parent_root / "tools/real_activity_001/real_activity_001_003_5f59e073f618/artifact.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        events = evidence["tables"]["events"]
        views = {"summary": horizon_summary(evidence), "events": compact_events({"rows": events,
            "offset": 0, "total_rows": len(events), "next_offset": None}), "coverage": input_coverage(case)}
        assert all(len(serial(v).encode()) < 8000 for v in views.values())
        assert len(views["events"]["rows"]) == 13 and sum(not x[4] for x in views["events"]["rows"]) == 3
        assert sum(x["rows"] for x in views["coverage"] if x["kind"] == "execution_obligation_coverage") == 192
        replay_path = ROOT / "experiment_traces/meta_framework_v3/validation/evidence_views_001/saved_replay.json"
        save_once(replay_path, {"parent_artifact_sha256": digest(evidence), "views": views,
            "new_model_calls": 0, "new_strategy_trials": 0, "formal_success": False})
        tasks = {"real_derived_activity_002": {"idea": "根据给定研究报告，核查已提出的成交活跃度与价格变化规则能否覆盖交易成本。自主选择必要查证、程序开发和对照，按完整资金及成本交付结论；证据不足可以弃权。",
            "documents": [{"name": "Prior genuine model final - exposed development, not validation", "text": report.read_text(encoding="utf-8"), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}],
            "case": case, "case_hash": digest(case)}}
        Ledger.create(root, policy=policy, tasks=tasks, deadline_epoch=previous["deadline_epoch"],
            provenance={"source_pins": source_pins(), "preregistered_protocol": str(manifest_file),
                "preregistered_protocol_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
                "old_v2_unknown_reserve": 80000, "old_v2_known_tokens": 491954,
                "prior_v3_known_tokens": 564943, "old_campaign_resume_authorized": False,
                "exposure": "One derived check on same exposed real development; original final retained. Not independent validation."})
        ResearchRuntime(root).verify_inputs()
        print(json.dumps({"root": str(root), "manifest": str(manifest_file), "maximum_calls": 12,
                          "source_pins_verified": True, "same_original_deadline": previous["deadline_epoch"]}))


if __name__ == "__main__":
    main()
