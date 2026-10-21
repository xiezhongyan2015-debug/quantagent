"""V5 evidence actions use V4's tool receipts, quotas and recovery paths."""
from __future__ import annotations
import json
from pathlib import Path
import time

from quanta_agents.meta_v3.ledger import digest, need
from quanta_agents.meta_v3.research_tools import ResearchTools, save_once
from quanta_agents.meta_v3 import tool_resources, evidence_views
from .analytics import evaluate_bundle, compare_profiles
from .reflection import validate_reflection, next_actions, public_contract
from .execution_adapter import saved_path
from .registry import admitted_bundle, file_hash

ACTIONS = ("inspect_stability", "profile_execution", "record_stability_reflection",
           "compare_stability_revision", "record_revision_review", "register_stability_revision")


def action_index(actions):
    """Bounded identity projection; repeated cells/counterevidence stay in original reflection."""
    keep = {"action", "action_id", "priority", "issue_ids", "profile_hash", "scope_id", "split", "unresolved_issue_ids",
            "execution_authorized", "formal_target_success", "experiment_id", "reflection_hash", "ready", "block_reason",
            "requires_new_saved_evidence", "holdout_release_authorized", "prerequisite"}
    return [{**{k: v for k, v in a.items() if k in keep}, "full_action_sha256": digest(a),
             "detail_query": "inspect_stability table=reflection_responses|reflection_experiments"} for a in actions]


class V5ResearchTools(ResearchTools):
    def __init__(self, *args, catalog, v5_contract, **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog, self.v5_contract = catalog, v5_contract
        self.limits.update(v5_contract["action_limits"])
        if "unit_execution" in v5_contract:
            from .unit_execution import validate_policy
            validate_policy(v5_contract["unit_execution"], self.case)

    def _saved(self, action):
        for row in self.history:
            if ((row.get("response") or {}).get("action") == action and
                    (row.get("result") or {}).get("artifact_hash") and row.get("status") != "failed"):
                yield row["id"], self._prior(row["id"], action)

    def profiles(self):
        profiles = self.catalog.profiles()
        for eid, record in self._saved("profile_execution"):
            profile = record["profile"]
            need(digest(record["bundle"]) == profile["bundle_hash"], "saved V5 profile source changed")
            need(digest({k:v for k,v in profile.items() if k != "profile_hash"}) == profile["profile_hash"], "saved V5 profile changed")
            admitted_bundle(record["bundle"])
            need(digest(self._candidate(record["execution_evidence_id"])) == record["source_artifact_hashes"][0], "original V4 execution changed")
            if record["benchmark_evidence_id"] is not None:
                need(digest(self._candidate(record["benchmark_evidence_id"])) == record["source_artifact_hashes"][1], "original V4 benchmark changed")
            profiles[eid] = profile
        return profiles

    def _profile(self, candidate_id):
        profiles = self.profiles()
        need(candidate_id in profiles, "candidate profile unavailable; request admitted evidence or profile a saved execution")
        return profiles[candidate_id]

    def _reflection(self, profile):
        matches = [(eid, a) for eid, a in self._saved("record_stability_reflection")
                   if a["profile_hash"] == profile["profile_hash"]]
        return matches[-1] if matches else None

    def research_state(self):
        rows = []
        profiles = self.profiles()
        own = [eid for eid, _ in self._saved("profile_execution")
               if profiles[eid]["scope_id"] == self.v5_contract["required_scope_id"] and
               profiles[eid]["program_hash"] not in self.v5_contract["benchmark_program_hashes"]]
        active = own[-1] if own else self.v5_contract.get("revision_baseline_profile_id")
        if active is None:
            active = next((ref for ref, p in profiles.items() if p["scope_id"] == self.v5_contract["required_scope_id"]), None)
        progress = self._iteration_progress()
        for ref, profile in profiles.items():
            if profile["scope_id"] != self.v5_contract["required_scope_id"]:
                continue
            reflection = self._reflection(profile)
            rows.append({"profile_id": ref, "candidate_id": profile["candidate_id"],
                "profile_hash": profile["profile_hash"], "program_hash": profile["program_hash"],
                "development_eligible": profile["development_eligible"],
                "issues": [{k: issue[k] for k in ("id", "severity", "code", "message") if k in issue} for issue in profile["issues"]],
                "reflection_evidence_id": reflection[0] if reflection else None, "active_for_scheduling": ref == active,
                "next_actions": action_index(next_actions(profile, reflection[1] if reflection else None))})
        return {"version": "v5_research_state_v1", "candidates": rows,
                "pending_execution_profiles": self._pending_profiles(),
                "iteration_experiments": progress,
                "searched_executable_programs": len(list(self._executed_candidates())),
                "failed_action_attempts": sum(r.get("status") == "failed" for r in self.history),
                "formal_target_success": False, "causal_mechanism_identified": False,
                "semantics": "Evidence gaps and declared experiments; no inferred truth from prose."}

    def menu(self):
        base = list(super().menu())
        for action in ACTIONS:
            count = sum((x.get("response") or {}).get("action") == action for x in self.history)
            if count < self.limits[action] and (self.permitted_actions is None or action in self.permitted_actions):
                base.append(action)
        return tool_resources.allowed(self.root, tuple(dict.fromkeys(base)))

    def contract(self):
        from quanta_agents.meta_v3.context import bounded
        contract = super().contract()
        state = self.research_state()
        contract["v5"] = {"version": "v5_research_actions_v1", "policy": self.catalog.policy,
            "state": bounded(state, 32000),
            "profile_directory": [{k: r[k] for k in ("profile_id", "profile_hash", "program_hash", "development_eligible", "reflection_evidence_id", "active_for_scheduling")}
                                  for r in state["candidates"]], "formal_release_authority": False,
            "rules": ["Only controller-admitted development profiles or verified own saved executions are available.",
                "Inspect yearly and unit-level differences, then bind a reflection before registering a structural revision.",
                "Unknown mechanisms are allowed; missing evidence is a task, not permission to invent a result.",
                "A selected development strategy needs an eligible profile, complete evidence reading and a bound reflection.",
                "Revisions must compare every original cell and explicitly review each degraded cell.",
                "No annual/reset/reinvestment changes, dropped units, changed benchmark or hidden-data access.",
                "A structured reflection is a declaration, not a verified causal explanation."]}
        contract["actions"].update({
            "inspect_stability": {"arguments": {"profile_id": "registered candidate or profile_execution ID",
                "table": "summary|cells|issues|reflection_responses|reflection_experiments|reflection_conclusion", "offset": "integer>=0", "limit": "integer1..16"}},
            "profile_execution": {"arguments": {"evidence_id": "own saved develop_strategy or execute_batch_ID/c001",
                "benchmark_evidence_id": "own saved account or null; program hash must be controller-approved"},
                "returns": "Full saved continuous account, all policy years retained; missing scope/baseline fails development qualification."},
            "record_stability_reflection": {"arguments": {"profile_id": "registered profile", "declaration": public_contract()["declaration_schema"]}},
            "compare_stability_revision": {"arguments": {"baseline_profile_id": "registered original", "revision_profile_id": "registered revision"}},
            "record_revision_review": {"arguments": {"comparison_evidence_id": "own compare_stability_revision ID",
                "responses": "one {cell_id, explanation, tradeoff_accepted:boolean} per degraded or unknown cell in review_required_cell_ids, no omissions",
                "decision": "continue_development|reject|needs_evidence"},
                "returns": "Explicit tradeoff declaration, never proof of causal truth or formal success."},
            "register_stability_revision": {"arguments": {"baseline_profile_id": "current assessed original or branch profile",
                "reflection_evidence_id": "latest bound record_stability_reflection ID", "experiment_id": "ready discriminating experiment from that reflection",
                "proposal_action": "develop_strategy|register_batch", "proposal_arguments": "exact subsequent V4 action arguments, without v5_revision_evidence_id"},
                "returns": "Frozen program/batch declaration binding. A declaration is not a tested explanation."}})
        for name in ("develop_strategy", "register_batch"):
            if name in contract["actions"]:
                contract["actions"][name]["arguments"]["v5_revision_evidence_id"] = "Required after the initial candidate/batch: prior register_stability_revision ID matching the exact proposal"
        if "unit_execution" in self.v5_contract:
            from .unit_execution import public_contract as unit_public_contract
            contract["v5"]["stock_diagnostics"] = unit_public_contract(self.v5_contract["unit_execution"])
            contract["actions"]["profile_execution"]["returns"] = "Executes/reuses bounded separately funded stock diagnostics on full original targets; all fixed stocks and years are retained. Main portfolio account remains separately reported."
        return contract

    def _read_coverage(self, profile):
        covered = {"cells": set(), "issues": set(), "summary": set()}
        for _, record in self._saved("inspect_stability"):
            if record["profile_hash"] == profile["profile_hash"] and record["table"] in covered:
                covered[record["table"]].update(record["delivered_ids"])
        need(covered["cells"] == {r["cell_id"] for r in profile["cells"]}, "all original annual/unit cells must be read")
        need(covered["issues"] == {r["id"] for r in profile["issues"]}, "all stability issues must be read")
        need("summary" in covered["summary"], "stability summary must be read")

    def _for_program(self, program_hash):
        matches = [(ref, p) for ref, p in self.profiles().items() if p["program_hash"] == program_hash]
        need(bool(matches), "V5 stability profile required for this exact program; use profile_execution or request scope extension")
        required = self.v5_contract.get("required_scope_id")
        if required:
            matches = [(ref, p) for ref, p in matches if p["scope_id"] == required]
        need(bool(matches), "candidate has no profile for the frozen required scope")
        need(len(matches) == 1, "ambiguous candidate profile; controller must freeze an unambiguous scope")
        return matches[0]

    def _development_baseline(self):
        """Controller-frozen original, or first EXECUTED non-benchmark, never first winner profiled."""
        explicit = self.v5_contract.get("revision_baseline_profile_id")
        if explicit is not None:
            return self._profile(explicit)
        for _, artifact in self._executed_candidates():
            _, profile = self._for_program(digest(artifact["program"]))
            return profile
        return None

    def _executed_candidates(self):
        seen = set()
        for row in self.history:
            if not (row.get("result") or {}).get("artifact_hash") or row.get("status") == "failed":
                continue
            action = (row.get("response") or {}).get("action")
            refs = [row["id"]] if action == "develop_strategy" else []
            if action == "execute_batch":
                batch = self._prior(row["id"], "execute_batch")
                refs = [row["id"] + "/" + c["candidate_id"] for c in batch["candidates"] if c["status"] in ("completed", "failed", "reused")]
            for ref in refs:
                artifact = self._candidate(ref)
                h = digest(artifact["program"])
                if h not in seen and h not in self.v5_contract["benchmark_program_hashes"]:
                    seen.add(h)
                    yield ref, artifact

    def _pending_profiles(self):
        profiles = self.profiles()
        known = {p["program_hash"] for p in profiles.values() if p["scope_id"] == self.v5_contract["required_scope_id"]}
        return [{"execution_evidence_id": ref, "program_hash": digest(a["program"]), "next_action": "profile_execution"}
                for ref, a in self._executed_candidates() if digest(a["program"]) not in known]

    def _require_iteration_accountability(self):
        need(not self._pending_profiles(), "all executed candidates must receive stability profiles before further search or selection")
        for _, artifact in self._executed_candidates():
            _, profile = self._for_program(digest(artifact["program"]))
            self._read_coverage(profile)
            need(self._reflection(profile) is not None, "every executed candidate requires a complete bound reflection")

    def _request_arguments(self, evidence_id):
        rows = [r for r in self.history if r["id"] == evidence_id]
        need(len(rows) == 1, "unknown own request")
        return json.loads(rows[0]["response"]["arguments_json"])

    def _check_revision_binding(self, evidence_id, action, arguments, *, already_registered_batch=False):
        record = self._prior(evidence_id, "register_stability_revision")
        need(record["proposal_action"] == action and record["proposal_hash"] == digest(arguments), "revision differs from its frozen reflection experiment proposal")
        profile = self._profile(record["baseline_profile_id"])
        latest = self._reflection(profile)
        need(latest is not None and latest[0] == record["reflection_evidence_id"], "revision reflection superseded or unavailable")
        ready = next_actions(profile, latest[1])
        need(any(a.get("experiment_id") == record["experiment_id"] and a["action"] == "run_discriminating_experiment" and a.get("ready") for a in ready),
             "declared experiment stopped or blocked by missing evidence")
        if not already_registered_batch:
            need(not any(r.get("status") == "applied" and (r.get("response") or {}).get("action") == action and
                         self._request_arguments(r["id"]).get("v5_revision_evidence_id") == evidence_id for r in self.history),
                 "bound proposal already applied; use saved evidence, not a new search")
        return record

    def _iteration_progress(self):
        records = []
        profiles = self.profiles()
        reviews = list(self._saved("record_revision_review"))
        for eid, binding in self._saved("register_stability_revision"):
            applied = [r["id"] for r in self.history if r.get("status") == "applied" and
                       (r.get("response") or {}).get("action") == binding["proposal_action"] and
                       self._request_arguments(r["id"]).get("v5_revision_evidence_id") == eid]
            execution_ids = applied
            if binding["proposal_action"] == "register_batch":
                execution_ids = [ref for ref, a in self._saved("execute_batch") if a["batch_id"] in applied]
            refs = execution_ids
            if binding["proposal_action"] == "register_batch":
                refs = [ref + "/" + c["candidate_id"] for ref in execution_ids
                        for c in self._prior(ref, "execute_batch")["candidates"] if c["status"] in ("completed", "failed", "reused")]
            assessments, missing = [], []
            baseline = self._profile(binding["baseline_profile_id"])
            for ref in refs:
                program_hash = digest(self._candidate(ref)["program"])
                if program_hash in self.v5_contract["benchmark_program_hashes"] or program_hash == baseline["program_hash"]:
                    continue
                matches = [p for p in profiles.values() if p["program_hash"] == program_hash and p["scope_id"] == baseline["scope_id"]]
                matched_reviews = [eid for p in matches for eid, review in reviews if review["baseline_profile_hash"] == baseline["profile_hash"] and
                                   review["revision_profile_hash"] == p["profile_hash"] and self._reflection(p) is not None]
                if matched_reviews:
                    assessments.extend(matched_reviews)
                else:
                    missing.append(ref)
            status = ("assessed_declaration_only" if refs and not missing and assessments else
                      "executed_results_require_assessment" if execution_ids else
                      "batch_registered_not_executed" if applied else "registered_not_applied")
            records.append({"registration_evidence_id": eid, "profile_hash": binding["profile_hash"],
                "experiment_id": binding["experiment_id"], "applied_proposal_ids": applied,
                "execution_evidence_ids": execution_ids,
                "assessment_evidence_ids": sorted(set(assessments)), "unassessed_execution_ids": missing, "status": status,
                "causal_mechanism_identified": False, "formal_target_success": False})
        return records

    def _execute(self, call_id, action, args):
        if action not in ACTIONS:
            if action in ("develop_strategy", "register_batch"):
                benchmark = action == "develop_strategy" and digest(args.get("program")) in self.v5_contract["benchmark_program_hashes"]
                if not benchmark:
                    self._require_iteration_accountability()
                baseline = None if benchmark else self._development_baseline()
                if baseline is not None and not benchmark:
                    self._read_coverage(baseline)
                    need(self._reflection(baseline) is not None, "V5 previous candidate differences must be reflected before further development")
                    need("v5_revision_evidence_id" in args, "further development requires a bound V5 reflection experiment")
                if "v5_revision_evidence_id" in args:
                    args = dict(args)
                    binding_id = args.pop("v5_revision_evidence_id")
                    self._check_revision_binding(binding_id, action, args)
                completed_batches = {a["batch_id"] for _, a in self._saved("execute_batch")}
                need(all(eid in completed_batches for eid, _ in self._saved("register_batch")), "assess or finish registered batch before opening another search")
            if action == "execute_batch":
                self._require_iteration_accountability()
                original = self._request_arguments(args["registration_evidence_id"])
                if "v5_revision_evidence_id" in original:
                    original = dict(original)
                    binding_id = original.pop("v5_revision_evidence_id")
                    self._check_revision_binding(binding_id, "register_batch", original, already_registered_batch=True)
            if action == "register_experiment":
                baseline = self._candidate(args["baseline_evidence_id"])
                _, profile = self._for_program(digest(baseline["program"]))
                self._read_coverage(profile)
                need(self._reflection(profile) is not None, "V5 bound cross-year reflection required before revision")
            return super()._execute(call_id, action, args)
        need(self.permitted_actions is None or action in self.permitted_actions, "V5 action forbidden in saved-only recovery")
        count = sum(x.get("id") != call_id and (x.get("response") or {}).get("action") == action for x in self.history)
        need(count < self.limits[action], "V5 frozen action quota exhausted")
        folder = self.folder / call_id
        folder.mkdir(exist_ok=False)
        save_once(folder / "request.json", {"action": action, "arguments": args})
        if action == "inspect_stability":
            need(set(args) == {"profile_id", "table", "offset", "limit"}, "stability page exact fields")
            profile = self._profile(args["profile_id"])
            table = args["table"]
            need(table in ("summary", "cells", "issues", "reflection_responses", "reflection_experiments", "reflection_conclusion"), "unknown stability table")
            need(type(args["offset"]) is int and args["offset"] >= 0 and type(args["limit"]) is int and 1 <= args["limit"] <= 16, "bounded stability page")
            if table.startswith("reflection_"):
                reflection = self._reflection(profile)
                need(reflection is not None, "no saved reflection for this profile")
                declaration = reflection[1]["declaration"]
                rows = ([declaration["conclusion"]] if table == "reflection_conclusion" else
                        declaration["issue_responses" if table == "reflection_responses" else "experiments"])
            else:
                rows = ([{"summary": profile["summary"], "development_eligible": profile["development_eligible"],
                          "formal_target_success": False}] if table == "summary" else profile[table])
                if table == "summary":
                    for _, prior in self._saved("profile_execution"):
                        if prior["profile"]["profile_hash"] == profile["profile_hash"] and "main_portfolio_profile" in prior:
                            rows[0]["main_portfolio_diagnostic"] = {"summary": prior["main_portfolio_profile"]["summary"],
                                "annual_cells": prior["main_portfolio_profile"]["cells"],
                                "semantics": "Original deployed full-capital portfolio, separately diagnosed; excluded from stock unit fractions and stock PnL concentration."}
            page = self._page(rows, args)
            def wrap(page, view):
                return {"evidence_id": call_id, "action": action, "artifact_hash": digest(page), "public": view,
                        "execution_valid": False, "formal_target_success": False}
            page, public = evidence_views.page_for_context(page,
                lambda p: {**p, "profile_hash": profile["profile_hash"], "table": table,
                           **({"reflection_hash": reflection[1]["reflection_hash"]} if table.startswith("reflection_") else {})}, wrap, args["limit"])
            delivered = page["rows"][:public.get("returned_rows", len(page["rows"]))] if not public.get("delivery_blocked") else []
            key = "cell_id" if table == "cells" else "id"
            artifact = {"profile_hash": profile["profile_hash"], "table": table,
                "delivered_ids": (["summary"] if delivered and table == "summary" else [] if table.startswith("reflection_") else [r[key] for r in delivered]), "page": page}
        elif action == "profile_execution":
            need(set(args) == {"evidence_id", "benchmark_evidence_id"}, "execution profile exact fields")
            artifact = self._execution_profile(call_id, args)
            public = {"profile_id": call_id, "profile_hash": artifact["profile"]["profile_hash"],
                      "summary": artifact["profile"]["summary"], "development_eligible": artifact["profile"]["development_eligible"]}
            if "main_portfolio_profile" in artifact:
                public["main_portfolio_diagnostic"] = {"summary": artifact["main_portfolio_profile"]["summary"],
                    "annual_cells": artifact["main_portfolio_profile"]["cells"],
                    "semantics": "Original deployed full-capital portfolio, separately diagnosed; excluded from stock unit fractions and stock PnL concentration."}
                public["stock_diagnostic_resources"] = {arm: {"usage": run["usage"],
                    "accounts": [{k: v for k, v in row.items() if k != "artifact"} for row in run["accounts"]]}
                    for arm, run in artifact["unit_execution_records"].items()}
        elif action == "record_stability_reflection":
            need(set(args) == {"profile_id", "declaration"}, "reflection action exact fields")
            profile = self._profile(args["profile_id"])
            self._read_coverage(profile)
            artifact = validate_reflection(args["declaration"], profile)
            public = {"profile_hash": profile["profile_hash"], "reflection_hash": artifact["reflection_hash"],
                      "quality_validated": True, "semantic_truth_verified": False, "causal_mechanism_identified": False,
                      "issue_count": len(artifact["unresolved_issue_ids"]), "formal_target_success": False,
                      "detail_query": "inspect_stability table=reflection_responses|reflection_experiments|reflection_conclusion",
                      "next_actions": action_index(next_actions(profile, artifact))}
        elif action == "compare_stability_revision":
            need(set(args) == {"baseline_profile_id", "revision_profile_id"}, "comparison exact fields")
            old, new = self._profile(args["baseline_profile_id"]), self._profile(args["revision_profile_id"])
            self._read_coverage(old); self._read_coverage(new)
            comparison = compare_profiles(old, new)
            degraded = [r["cell_id"] for r in comparison["cells"]
                        if r["status"] == "degraded" or (r["max_drawdown_change"] is not None and r["max_drawdown_change"] > 1e-12)]
            review_ids = sorted(set(degraded) | set(comparison["unknown_cell_ids"]))
            artifact = {"baseline_profile_hash": old["profile_hash"], "revision_profile_hash": new["profile_hash"],
                "comparison": comparison, "degraded_cell_ids": degraded,
                "review_required_cell_ids": review_ids, "formal_target_success": False}
            public = artifact
        elif action == "record_revision_review":
            need(set(args) == {"comparison_evidence_id", "responses", "decision"}, "revision review exact fields")
            comparison = self._prior(args["comparison_evidence_id"], "compare_stability_revision")
            responses = args["responses"]
            need(type(responses) is list, "revision responses required")
            need(len(responses) == len(comparison["review_required_cell_ids"]), "every degraded or unknown cell must be retained")
            need({r.get("cell_id") for r in responses} == set(comparison["review_required_cell_ids"]), "revision changed review cells")
            need(args["decision"] in ("continue_development", "reject", "needs_evidence"), "invalid revision decision")
            for r in responses:
                need(set(r) == {"cell_id", "explanation", "tradeoff_accepted"} and type(r["explanation"]) is str
                     and 1 <= len(r["explanation"].strip()) <= 2000 and type(r["tradeoff_accepted"]) is bool, "explicit bounded degradation response")
            if args["decision"] == "continue_development":
                need(all(r["tradeoff_accepted"] for r in responses), "unaccepted regression blocks adoption")
            artifact = {**args, "baseline_profile_hash": comparison["baseline_profile_hash"],
                        "revision_profile_hash": comparison["revision_profile_hash"], "semantic_truth_verified": False,
                        "formal_target_success": False}
            public = artifact
        else:
            need(set(args) == {"baseline_profile_id", "reflection_evidence_id", "experiment_id", "proposal_action", "proposal_arguments"}, "exact bound revision declaration required")
            self._require_iteration_accountability()
            profile = self._profile(args["baseline_profile_id"])
            need(profile["scope_id"] == self.v5_contract["required_scope_id"], "revision cannot switch required scope")
            self._read_coverage(profile)
            latest = self._reflection(profile)
            need(latest is not None and latest[0] == args["reflection_evidence_id"], "revision must cite latest bound reflection")
            need(args["proposal_action"] in ("develop_strategy", "register_batch") and type(args["proposal_arguments"]) is dict and
                 "v5_revision_evidence_id" not in args["proposal_arguments"], "bounded native proposal required")
            ready = next_actions(profile, latest[1])
            need(any(a.get("experiment_id") == args["experiment_id"] and a["action"] == "run_discriminating_experiment" and a.get("ready") for a in ready),
                 "declared experiment stopped or blocked by missing evidence")
            need(len(json.dumps(args["proposal_arguments"]).encode()) <= 64000, "proposal declaration too large")
            artifact = {**args, "profile_hash": profile["profile_hash"], "proposal_hash": digest(args["proposal_arguments"]),
                "semantic_truth_verified": False, "formal_target_success": False, "executed": False}
            public = artifact
        save_once(folder / "artifact.json", artifact)
        result = {"evidence_id": call_id, "action": action, "artifact_hash": digest(artifact),
                  "public": public, "execution_valid": False, "formal_target_success": False}
        save_once(folder / "result.json", result)
        return result

    def _execution_profile(self, call_id, args):
        raw = self._candidate(args["evidence_id"])
        benchmark = self._candidate(args["benchmark_evidence_id"]) if args["benchmark_evidence_id"] is not None else None
        if benchmark is not None:
            need(digest(benchmark["program"]) in self.v5_contract["benchmark_program_hashes"], "benchmark program was not frozen by controller")
        calendar = self.case["decision_fixture"]["calendar"]
        sources = {}
        for name, artifact in (("candidate", raw), ("benchmark", benchmark)):
            if artifact is not None:
                path = (self.folder / call_id / (name + ".verified_source.json")).resolve()
                save_once(path, artifact)
                sources[str(path)] = file_hash(path)
        unit = self.v5_contract["execution_unit_id"]
        bundle = {"version": "v5_stability_bundle_v1", "candidate_id": call_id,
            "program_hash": digest(raw["program"]), "scope_id": self.v5_contract["execution_scope_id"],
            "split": "development", "expected_units": [unit],
            "pairs": [{"unit_id": unit, "unit_kind": "portfolio", "calendar": calendar,
                "initial_nav": float(self.case["initial_cash"]), "candidate": saved_path(raw, args["evidence_id"], calendar, self.case["initial_cash"]),
                "benchmark": saved_path(benchmark, args["benchmark_evidence_id"], calendar, self.case["initial_cash"])}],
            "provenance": {"accounting_mode": "v4_saved_account_attribution", "account_currency": "CNY",
                "fee_unit": "CNY", "external_cash_flows": "none_after_initial", "execution_certified": False,
                "exposed": True, "source_class": "generated_engineering" if self.case["research_class"] == "synthetic_calibration" or self.conditional_derivation_check is not None else "saved_development",
                "source_hashes": sources, "source_scope_identity": {"case_hash": digest(self.case), "adapter": "v4_verified_saved_account_v1"},
                "cost_policy_hash": digest(self.case), "coverage_note": "Verified saved V4 continuous path; fee differences, marked exposure and explicit mark evidence; unsupported fields remain unknown."}}
        additional = {}
        if "unit_execution" in self.v5_contract:
            from .unit_execution import run_accounts, public_contract
            main_profile = evaluate_bundle(bundle, self.catalog.policy)
            policy = self.v5_contract["unit_execution"]
            deadline = time.time() + policy["max_wall_seconds_per_profile"]
            plan_path = Path(self.root) / "plan.json"
            if plan_path.is_file():
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                deadline = min(deadline, plan["deadline_epoch"] - plan["policy"]["closing_seconds"])
            runs = {}
            for arm, original in (("candidate", raw), ("benchmark", benchmark)):
                if original is not None:
                    runs[arm] = run_accounts(self.root, self.task_id, self.case, original, policy, deadline_epoch=deadline)
                    sources.update(runs[arm]["source_hashes"])
            accounts = {arm: {r["code"]: r for r in run["accounts"]} for arm, run in runs.items()}
            pairs = []
            for code in policy["codes"]:
                pair = {"unit_id": code, "unit_kind": "stock", "calendar": calendar,
                        "initial_nav": float(policy["initial_cash_per_account"])}
                for arm in ("candidate", "benchmark"):
                    saved = accounts.get(arm, {}).get(code, {}).get("artifact")
                    pair[arm] = saved_path(saved, f"{call_id}/{arm}/{code}", calendar, policy["initial_cash_per_account"])
                pairs.append(pair)
            additional = {"main_portfolio_pair": bundle["pairs"][0], "main_portfolio_profile": main_profile, "unit_execution_records": runs,
                          "unit_accounting": public_contract(policy)}
            bundle.update(expected_units=policy["codes"], pairs=pairs)
            bundle["provenance"].update(accounting_mode="v5_separately_funded_stock_accounts_original_weights",
                source_scope_identity={"case_hash": digest(self.case), "adapter": "v5_independent_stock_accounts_v1", "unit_policy_hash": digest(policy)},
                coverage_note="Each stock is an independent continuous full-capital diagnostic account, preserving its original full-universe target weights. Diagnostic capitals are not additive deployed capital. Original main portfolio is retained separately.")
        return {"bundle": bundle, "profile": evaluate_bundle(bundle, self.catalog.policy), **additional,
                "execution_evidence_id": args["evidence_id"], "benchmark_evidence_id": args["benchmark_evidence_id"],
                "source_artifact_hashes": [digest(raw), digest(benchmark)]}

    def final(self, args):
        if args.get("outcome") == "strategy_for_development":
            self._require_iteration_accountability()
            ref = args["program_evidence_id"]
            if "batch_candidate_id" in args:
                ref += "/" + args["batch_candidate_id"]
            executed = self._candidate(ref)
            _, profile = self._for_program(digest(executed["program"]))
            self._read_coverage(profile)
            reflection = self._reflection(profile)
            need(reflection is not None and reflection[0] in args["evidence_ids"], "final must cite its bound stability reflection")
            need(profile["development_eligible"] is True, "V5 development stability gates not satisfied")
            old = self._development_baseline()
            if old is not None and old["program_hash"] != profile["program_hash"]:
                self._require_revision_review(old, profile, args["evidence_ids"])
            if "experiment" in executed:
                baseline = self._candidate(executed["experiment"]["baseline_evidence_id"])
                _, old = self._for_program(digest(baseline["program"]))
                self._require_revision_review(old, profile, args["evidence_ids"])
        return {**super().final(args), "v5_stability_checked": args.get("outcome") == "strategy_for_development",
                "formal_target_success": False}

    def _require_revision_review(self, old, profile, evidence_ids):
        reviews = [(eid, r) for eid, r in self._saved("record_revision_review")
                   if r["baseline_profile_hash"] == old["profile_hash"] and r["revision_profile_hash"] == profile["profile_hash"]]
        need(bool(reviews) and reviews[-1][1]["decision"] == "continue_development"
             and reviews[-1][0] in evidence_ids, "revision requires cited complete degradation review")
