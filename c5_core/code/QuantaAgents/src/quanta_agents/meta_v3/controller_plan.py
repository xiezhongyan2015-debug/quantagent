"""V4 work selection over the existing V3 ledger; no new budget or authority.

The contract records why work is useful. Source checks decide local admission;
unknown calls, shared budgets, code provenance and worker ownership stay global.
Text describes a stop criterion, it is not an automatic research-quality score.
"""
from .ledger import need

VERSION = "dependency_scoped_v1"
HARNESS_VERSION = "4.0.0-dev"


def validate(policy, tasks):
    need(type(policy) is dict and set(policy) == {"version", "work"}
         and policy["version"] == VERSION, "invalid controller policy")
    work = policy["work"]
    need(type(work) is dict and set(work) == set(tasks), "work contracts must cover exactly the frozen tasks")
    for task_id, row in work.items():
        need(type(row) is dict and set(row) == {
            "question", "necessary_evidence", "depends_on", "unlocks", "stop_condition"
        }, "exact work contract fields required")
        for key in ("question", "unlocks", "stop_condition"):
            need(type(row[key]) is str and 0 < len(row[key].strip()) <= 2000,
                 "bounded substantive work contract: " + key)
        need(type(row["necessary_evidence"]) is list and 1 <= len(row["necessary_evidence"]) <= 16
             and all(type(x) is str and 0 < len(x.strip()) <= 1000 for x in row["necessary_evidence"]),
             "necessary evidence required")
        deps = row["depends_on"]
        need(type(deps) is list and all(type(x) is str for x in deps)
             and len(set(deps)) == len(deps) and set(deps) <= set(tasks)
             and task_id not in deps, "invalid work dependencies")
    visited, active = set(), set()

    def visit(task_id):
        need(task_id not in active, "cyclic work dependencies")
        if task_id in visited:
            return
        active.add(task_id)
        for dep in work[task_id]["depends_on"]:
            visit(dep)
        active.remove(task_id)
        visited.add(task_id)

    for task_id in work:
        visit(task_id)
    return policy


def view(policy, states, source_checks, identity_gate=None, extension_handoffs=None):
    """Derived scheduling evidence, never a persisted replacement for the ledger."""
    work = validate(policy, states)["work"]
    rows = {}
    for task_id, contract in work.items():
        state = states[task_id]
        unsatisfied = [dep for dep in contract["depends_on"] if states[dep]["terminal"] != "submitted"]
        check = source_checks.get(task_id, {"status": "not_checked"})
        reasons = []
        if state["terminal"]:
            status = "terminal"
        elif state["mode"] == "blocked":
            status = "blocked_shared_gate"
            reasons = state["reasons"]
        elif unsatisfied:
            status = "blocked_dependency"
            reasons = ["requires submitted research evidence from " + x for x in unsatisfied]
        elif check["status"] != "available":
            status = "blocked_task_source"
            reasons = [check.get("reason", "task sources not checked")]
        elif identity_gate is not None and identity_gate['status'] != 'available':
            status = 'blocked_runtime_identity'
            reasons = [identity_gate['reason']]
        elif (extension_handoffs or {}).get(task_id, {}).get('blocks_parent_dispatch'):
            status = 'waiting_for_extension_controller'
            reasons = [(extension_handoffs or {})[task_id]['status']]
        else:
            status = "ready"
        rows[task_id] = {"status": status, "reasons": reasons, "work_contract": contract,
                         "source_check": check, "ledger_mode": state["mode"],
                         "terminal": state["terminal"]}
    ready = [k for k, row in rows.items() if row["status"] == "ready"]
    return {"version": VERSION, "tasks": rows, "ready_tasks": ready, 'runtime_identity_gate': identity_gate,
            "next_task": ready[0] if ready else None,
            "stage_disposition": "runnable" if ready else (
                "all_tasks_terminal" if all(s["terminal"] for s in states.values()) else "blocked_partial"),
            "dependency_semantics": "A submitted report is available evidence, not accepted research quality or strategy success. No parent history is implicitly imported.",
            "stopping_semantics": "Frozen budgets and deadlines are enforced by the existing ledger; scientific stop text needs evidence-based assessment. No task or scope is renewed."}
