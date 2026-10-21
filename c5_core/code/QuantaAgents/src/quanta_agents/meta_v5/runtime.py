"""V5 research decisions over the unchanged V4 execution, closing and ledger."""
from copy import deepcopy
import json
from pathlib import Path

from quanta_agents.meta_v3.ledger import Ledger, digest, need, serial
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins, verify_case_sources
from quanta_agents.meta_v3.controller_plan import validate as validate_controller, HARNESS_VERSION
from quanta_agents.meta_v3.kernel import ROOT
from . import VERSION
from .analytics import validate_policy
from .registry import Catalog, catalog_identity, prepare_catalog, write_catalog, file_hash
from .tools import V5ResearchTools, ACTIONS


def v5_source_pins():
    files = sorted(Path(__file__).parent.glob("*.py")) + [ROOT / "scripts/run_research_v5.py"]
    return {str(p.relative_to(ROOT)).replace("\\", "/"): file_hash(p) for p in files}


def validate_task_contract(value):
    keys = {"action_limits", "required_scope_id", "execution_scope_id", "execution_unit_id",
            "benchmark_program_hashes", "revision_baseline_profile_id", "priority"}
    need(type(value) is dict and set(value) in (keys, keys | {"unit_execution"}), "complete explicit V5 task contract required")
    if "unit_execution" in value:
        from .unit_execution import validate_policy as validate_unit_policy
        validate_unit_policy(value["unit_execution"])
    need(type(value["action_limits"]) is dict and set(value["action_limits"]) == set(ACTIONS) and
         all(type(n) is int and 0 <= n <= 256 for n in value["action_limits"].values()), "bounded V5 action allocation required")
    for key in ("required_scope_id", "execution_scope_id", "execution_unit_id"):
        need(type(value[key]) is str and 0 < len(value[key]) <= 100 and ":" not in value[key], "frozen V5 scope identity required")
    need(value["revision_baseline_profile_id"] is None or type(value["revision_baseline_profile_id"]) is str,
         "baseline profile must be frozen or explicitly absent")
    hashes = value["benchmark_program_hashes"]
    need(type(hashes) is list and len(hashes) <= 16 and len(hashes) == len(set(hashes)) and
         all(type(h) is str and len(h) == 64 and all(c in "0123456789abcdef" for c in h) for h in hashes), "frozen benchmark program hashes required")
    need(type(value["priority"]) is int and 0 <= value["priority"] <= 1000, "bounded task tie-break priority required")
    return value


def create_stage(root, *, tasks, controller_policy, stability_policy, bundles, policy,
                 deadline_epoch, identity_route):
    """Fresh development stage only. Preparing evidence makes no model call."""
    need(not Path(root).exists(), "V5 requires a fresh root; old campaigns are immutable")
    tasks = deepcopy(tasks)
    validate_policy(stability_policy)
    validate_controller(controller_policy, tasks)
    for task in tasks.values():
        need("imported_history" not in task and "permitted_actions" not in task, "V5 cannot relabel an old stage or recovery scope")
        validate_task_contract(task["v5"])
        if "unit_execution" in task["v5"]:
            from .unit_execution import validate_policy as validate_unit_policy
            validate_unit_policy(task["v5"]["unit_execution"], task["case"])
        verify_case_sources(task)
    prepared = prepare_catalog(bundles, stability_policy)
    profiles = {e["candidate_id"]: e["profile"] for e in prepared["entries"]}
    for task in tasks.values():
        baseline = task["v5"]["revision_baseline_profile_id"]
        matching = [p for p in profiles.values() if p["scope_id"] == task["v5"]["required_scope_id"]]
        need(not matching or baseline is not None, "existing scope evidence requires a frozen revision baseline")
        if baseline is not None:
            need(baseline in profiles and profiles[baseline]["scope_id"] == task["v5"]["required_scope_id"],
                 "revision baseline must be in the required frozen scope")
    provenance = {"source_pins": source_pins(), "v5_source_pins": v5_source_pins(),
        "controller_policy": controller_policy, "harness_version": HARNESS_VERSION,
        "runtime_identity_route": identity_route, "old_campaign_resume_authorized": False,
        "exposure": "V5 exposed development only; synthetic and saved real evidence remain distinguished.",
        "v5_contract": {"version": VERSION, "catalog_identity": catalog_identity(prepared),
            "formal_release_authority": False, "fair_comparison_admitted": False,
            "preparation_cost_scope": "Measured separately in v5_catalog; not an admitted common-cost V4/V5 experiment."}}
    ledger = Ledger.create(root, policy=policy, tasks=tasks, deadline_epoch=deadline_epoch, provenance=provenance)
    write_catalog(root, prepared)
    runtime = V5Runtime(root)
    runtime.verify_inputs(global_only=True)
    return runtime


class V5Runtime(ResearchRuntime):
    def __init__(self, root):
        super().__init__(root)
        contract = self.plan["provenance"].get("v5_contract")
        need(type(contract) is dict and contract.get("version") == VERSION,
             "not a newly frozen V5 stage; automatic V4 migration is forbidden")
        need(contract.get("formal_release_authority") is False and contract.get("fair_comparison_admitted") is False,
             "V5 development entry has no formal release or comparison authority")
        need(self.controller_policy is not None, "V5 requires the V4 scoped controller")
        self.catalog = Catalog(self.root, contract["catalog_identity"])

    def verify_inputs(self, *, task_id=None, global_only=False):
        need(self.plan["provenance"]["v5_source_pins"] == v5_source_pins(), "V5 source drift; never repin an existing stage")
        self.catalog.verify()
        for task in self.plan["tasks"].values():
            validate_task_contract(task["v5"])
            if "unit_execution" in task["v5"]:
                from .unit_execution import validate_policy as validate_unit_policy
                validate_unit_policy(task["v5"]["unit_execution"], task["case"])
        return super().verify_inputs(task_id=task_id, global_only=global_only)

    def _tools(self, task_id):
        task = self.plan["tasks"][task_id]
        return V5ResearchTools(self.root, task_id, task["case"], self._history(task_id),
                               catalog=self.catalog, v5_contract=task["v5"])

    def _scoped_prompt(self, task_id, tools, history, menu, budget, decision, handoff):
        prompt, schema = super()._scoped_prompt(task_id, tools, history, menu, budget, decision, handoff)
        value = json.loads(prompt)
        value["work_contract"] = self.controller_policy["work"][task_id]
        value["v5_scope_contract"] = self.plan["tasks"][task_id]["v5"]
        value["instructions"].extend([
            "Use the V5 research state to prioritize missing evidence and cross-year/cross-unit differences before proposing revisions.",
            "Year labels locate diagnostic evidence; trading conditions must use information available before the decision, never select successful years retroactively.",
            "Every inspected cell and issue remains in the reflection. Unresolved mechanisms may be retained, discriminated by controlled experiments, or stopped.",
            "A reflection schema pass proves reference consistency only. A proposed experiment is not executed evidence; prepare_confirmation is not a data release.",
            "Submit only development conclusions. A single high Sharpe, a synthetic pass, or this new runtime cannot establish a major discovery."])
        return serial(value), schema

    def write_status(self, *, persist=True):
        state = super().write_status(persist=False)
        state["kind"] = VERSION
        evidence = {}
        for task_id in self.plan["tasks"]:
            try:
                evidence[task_id] = self._tools(task_id).research_state()
            except (ValueError, KeyError, TypeError, OSError) as exc:
                evidence[task_id] = {"status": "unavailable", "reason": str(exc)[:2000]}
        state["v5_research_state"] = evidence
        schedule = state["work_schedule"]
        def rank(task_id):
            if evidence[task_id].get("pending_execution_profiles"):
                return (5, self.plan["tasks"][task_id]["v5"]["priority"], task_id)
            candidates = [c for c in evidence[task_id].get("candidates", []) if c["active_for_scheduling"]]
            priorities = [a["priority"] for c in candidates for a in c["next_actions"] if a.get("ready", True)]
            return (min(priorities, default=10), self.plan["tasks"][task_id]["v5"]["priority"], task_id)
        ready = schedule["ready_tasks"]
        failed = [t for t in ready if evidence[t].get("status") == "unavailable"]
        for task_id in failed:
            schedule["tasks"][task_id].update(status="blocked_v5_evidence", reasons=[evidence[task_id]["reason"]])
        ready = sorted((t for t in ready if t not in failed), key=rank)
        schedule.update(ready_tasks=ready, next_task=ready[0] if ready else None,
                        v5_priority_rule="ready evidence gaps, difference reflection, discriminating experiment; frozen tie-break; unchanged dependencies/budgets")
        if failed and not ready:
            schedule["stage_disposition"] = "blocked_partial"
        self.work_schedule = schedule
        if persist:
            path = self.root / "status.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(serial(state), encoding="utf-8")
            temporary.replace(path)
        return state
