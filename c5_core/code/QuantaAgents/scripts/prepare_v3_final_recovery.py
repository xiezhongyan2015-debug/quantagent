"""Freeze one source-bound final-only continuation within the parent's balance."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.ledger import Ledger, need, digest, serial, worker_lease
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.runtime import source_pins, ResearchRuntime, make_prompt, action_schema
from quanta_agents.meta_v3.research_tools import save_once


def prepare(parent_root, root, task_id):
    parent = Ledger(parent_root)
    with worker_lease(parent.root):
        need(not (parent.root / (task_id + '_saved_recovery_claim.json')).exists(),
             'saved recovery already claimed; legacy final-only recovery cannot claim it again')
        with parent.transaction() as db:
            stage, plan, policy = parent._plan(db)
            need(bool(stage["paused"]), "parent must be paused before deriving recovery")
            calls = [dict(x) for x in db.execute("SELECT * FROM calls")]
            need(all(x["status"] in ("applied", "failed") and x["known_tokens"] is not None for x in calls), "unresolved parent cannot be recovered")
            task_row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            need(task_row is not None and task_row["final_call"] is None, "parent already consumed final")
            need(db.execute("SELECT count(*) FROM tasks WHERE id<>? AND terminal IS NULL", (task_id,)).fetchone()[0] == 0,
                 "this recovery supports one unfinished task only")
        own = [x for x in calls if x["task_id"] == task_id]
        task_cost = sum(x["known_tokens"] for x in own)
        stage_cost = sum(x["known_tokens"] for x in calls)
        need(len(own) < policy.task_calls and len(calls) < policy.stage_calls, "no original call allowance")
        remaining = min(policy.task_tokens-task_cost, policy.stage_tokens-stage_cost)
        need(remaining >= policy.closing_reserve and time.time() < plan["deadline_epoch"], "no original closing allowance")
        history = parent.history(task_id)
        task = dict(plan["tasks"][task_id])
        audit_path = parent.root / "audits/saved_001/audit.json"
        audited = json.loads(audit_path.read_text(encoding="utf-8"))
        need(audited["known_tokens"] == stage_cost and audited["unknown_or_pending_nominal_reserve"] == 0,
             "saved arithmetic audit must cover the settled parent")
        task["documents"] = list(task["documents"]) + [{"name": "Saved arithmetic verification for this task only",
            "text": serial({"source_audit_hash": digest(audited),
                "executions": [x for x in audited["executions"] if x["task_id"] == task_id],
                "note": "Offline arithmetic only; no new strategy trial or controller-authored conclusion. Original unfinished delivery remains a failure."})}]
        task["imported_history"] = {"root": str(parent.root), "task_id": task_id, "rows": history, "hash": digest(history)}
        protocol = {"kind": "final_only_context_repair", "parent_plan_hash": digest(plan), "parent_root": str(parent.root),
            "parent_known_tokens": stage_cost, "parent_task_known_tokens": task_cost,
            "parent_calls": len(calls), "parent_task_calls": len(own),
            "original_deadline": plan["deadline_epoch"], "remaining_original_nominal_tokens": remaining,
            "max_new_calls": 1, "new_candidate_trials": 0, "original_delivery_failure_retained": True,
            "independent_market_samples": 0, "formal_success": False,
            "evaluation": "Recovery produces an authentic final or a recorded failure; it never changes the original 1/2 delivery result.",
            "stop": "unknown, invalid final, source drift, deadline; no automatic paid retry"}
        # Exclusive immutable claim prevents duplicate recovery budgets.
        save_once(parent.root / (task_id + "_final_recovery_claim.json"), {"root": str(root.resolve()), "protocol": protocol})
        job = Ledger.create(root, policy=ClosingPolicy(task_calls=1, stage_calls=1,
            task_tokens=remaining, stage_tokens=remaining), tasks={task_id: task},
            deadline_epoch=plan["deadline_epoch"], provenance={"source_pins": source_pins(),
                "old_v2_unknown_reserve": 80000, "old_v2_known_tokens": 491954,
                "old_campaign_resume_authorized": False, "preregistered_protocol": protocol})
        rt = ResearchRuntime(root)
        rt.verify_inputs()
        with job.transaction() as db:
            _, _, _, _, _, state, decision = job._state(db, task_id)
        prompt = make_prompt(task, rt._tools(task_id), rt._history(task_id), ("submit_research_report",), vars(state), decision)
        need(len(prompt.encode()) <= 262144, "recovery context still too large")
        save_once(root / "context_preflight.json", {"prompt_bytes": len(prompt.encode()), "prompt_hash": digest(prompt),
            "mode": decision.mode, "allowed_actions": ["submit_research_report"], "new_gateway_calls": 0})
        print(json.dumps({"root": str(root), "prompt_bytes": len(prompt.encode()), "mode": decision.mode,
                          "remaining_tokens": remaining, "deadline_epoch": plan["deadline_epoch"]}))


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--parent",type=Path,required=True)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--task",required=True)
    a=p.parse_args()
    prepare(a.parent,a.root,a.task)
