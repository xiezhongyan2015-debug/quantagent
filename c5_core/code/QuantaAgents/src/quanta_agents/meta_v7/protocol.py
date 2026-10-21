"""Small decisions with automatically delivered factor and paired evidence."""
from __future__ import annotations
import json
from quanta_agents.research_kernel.protocol import CONTRACT as BASE_CONTRACT, validate_action as old_validate
from quanta_agents.research_kernel.store import serial

ACTIONS = ["query_assets", "get_evidence", "register_factor", "evaluate_factors", "propose_batch",
           "review_batch", "revise_batch", "review_validation", "request_extension", "stop"]
ACTION_SCHEMA = {"type": "object", "properties": {"action": {"type": "string", "enum": ACTIONS},
    "reason": {"type": "string"}, "payload_json": {"type": "string"}},
    "required": ["action", "reason", "payload_json"], "additionalProperties": False}

CONTRACT = BASE_CONTRACT + """
V7 stage rules override the older workflow above. Factor evidence is computed BEFORE portfolio proposals.
evaluate_factors: {ids:[registeredID,...],horizons:[5,20],pairs:[{left:ID,right:ID},...]}.
This queues only diagnostics, no account. The software delivers IC, coverage, role diagnostics, correlations,
conditional IC and declared interactions automatically. Any covered factor can participate, regardless of IC.
No need to query already attached results. Read factors' expressions and correlated neighbors before inventing.
Choose a falsifiable combination and controls from the evidence; simple return-ranking is not the only role.
Once accounts finish you MUST review before the next proposal.
review_batch: {batch_id,verdict:'revise'|'reject'|'freeze_for_validation'|'stop',conclusion,
candidate_run_id:runID or null}. Explain which paired result supports/contradicts the mechanism, including
coverage, costs, annual stability and correlated information. Revise/reject lets you test another hypothesis.
freeze_for_validation selects exactly ONE completed training arm; identical frozen spec is evaluated later.
After validation is unlocked, no new training candidates in this study. review_validation:{job_id,conclusion}
closes research. Validation is a time split, never automatically independent; obey recorded exposure status.
Preserve one final model call for reviewing results, do not use it to ask for another research batch.
When closing_only is true, review_batch must stop/reject or review_validation must close; do not freeze another
candidate without a remaining call to review it. Stop after evidence falsifies the idea if more work is unwarranted.
Model still owns hypotheses, pair selection, interpretation and novel capability requests. No hidden winner
selection or automatic profitable-strategy claim. Full records remain available by evidence id.
"""

V8_CONTRACT = """
V8 stage rules override the older workflow above, with the SAME strategy language and numeric engine.
Compute factor evidence before proposing portfolios; any covered factor can participate regardless of IC.
evaluate_factors:{ids:[registeredID,...],horizons:[5,20],pairs:[{left:ID,right:ID},...]}; no account is run.
After accounts finish, review before the next proposal. review_batch:{batch_id,
verdict:'revise'|'reject'|'freeze_for_validation'|'stop',conclusion,candidate_run_id:runID or null}.
Explain the mechanism's paired support or contradiction, coverage, costs and annual stability.
freeze_for_validation selects one completed arm unchanged. Once unlocked, no new training candidates.
After it finishes, review_validation:{job_id,conclusion} closes the study. Validation is not automatically
independent; obey recorded exposure. Preserve a final model call for review; closing_only prohibits new
batches and freezing without a remaining validation-review call. Stop/reject unsupported hypotheses.
For review_batch verdict revise include revision_plan:{parent_run_id,allowed_paths:[...],hypothesis,falsifier}.
Select one completed run in that batch. allowed_paths are /score, /gate, /risk_score, or an exact
/allocation/<field>. The software freezes its full original specification. Keep allowed_paths minimal.
Then revise_batch:{parent_run_id,review_batch_id,changes:[{path,value},...],name:optional,controls:[...]}.
Only declared paths may change; all other values are inherited. Do not query or retype the stored strategy
just to revise it. The latest revise verdict requires this bound action, not a free-form propose_batch.
Use reject to end that hypothesis if a separate new mechanism is justified; its failed evidence stays.
Evidence support references carry n/missing/coverage and degeneracy. A missing IC is unknown, not zero.
IC and risk correlations are descriptive; weak marginal IC never automatically rejects a condition.
Post-cap inverse-volatility weights may leave cash. A drawdown change with changed exposure does not
identify incremental risk information. Software changes/domains describe differences, not causal effects.
"""


def validate_action(response, max_bytes=16000):
    if isinstance(response, dict) and response.get("action") in {"evaluate_factors", "review_batch", "review_validation", "revise_batch"}:
        # Reuse strict envelope, duplicate-key and size validation through a
        # payload-free legacy action; parse actual payload with the same hook.
        from quanta_agents.research_kernel.protocol import _unique
        old_validate({**response, "action": "stop", "payload_json": "{}"}, max_bytes)
        if len(serial(response).encode("utf-8")) > max_bytes or not isinstance(response["payload_json"], str):
            raise ValueError("Bounded string payload required")
        payload = json.loads(response["payload_json"], object_pairs_hook=_unique,
            parse_constant=lambda v: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        action = response["action"]
        allowed = {"evaluate_factors": {"ids", "horizons", "pairs"},
                   "review_batch": {"batch_id", "verdict", "conclusion", "candidate_run_id", "revision_plan"},
                   "revise_batch": {"parent_run_id", "review_batch_id", "changes", "name", "controls"},
                   "review_validation": {"job_id", "conclusion"}}[action]
        required = {"evaluate_factors": {"ids"}, "review_batch": {"batch_id", "verdict", "conclusion"},
                    "review_validation": {"job_id", "conclusion"},
                    "revise_batch": {"parent_run_id", "review_batch_id", "changes"}}[action]
        if not isinstance(payload, dict) or set(payload) - allowed or not required <= set(payload):
            raise ValueError("Unknown or missing V7 action fields")
        return action, payload
    return old_validate(response, max_bytes)


def compact_factors(report):
    return {"scope": report["scope"], "factors": {k: {
        "roles": v["roles"], "coverage": {k: v["coverage"].get(k) for k in ("fraction", "pool_cells", "observed_cells", "missing_cells")},
        "ic": {h: {m: s.get(m) for m in ("mean_ic", "observed_days", "missing_days", "annual")}
               for h, s in v["horizons"].items()},
        "risk": {h: {metric: data.get("mean_ic") for metric, data in metrics.items()}
                 for h, metrics in v["risk"].items()}} for k, v in report["factors"].items()},
        "correlations": [{k: r.get(k) for k in ("left", "right", "mean_ic", "observed_days")} for r in report["correlations"]],
        "conditions": [{k: r.get(k) for k in ("left", "right", "horizon", "condition", "mean_ic", "observed_days")} for r in report["conditions"]],
        "interactions": [{"left": r["left"], "right": r["right"], "status": r["status"],
            "ic": {h: {k: v[k]["mean_ic"] for k in ("raw_ic", "partial_ic")} for h, v in r.get("horizons", {}).items()}}
            for r in report["interactions"]], "limitations": report["limitations"]}


def build_context(kernel, *, after_revision=0):
    state = kernel.status()
    v8 = kernel.research_profile == "v8"
    if v8:
        from .decision_evidence import compact_factors_v8
        from .revisions import control_identifiability
    factors = []
    known_ids = set()
    for job in state["factor_jobs"][-3:]:
        if job["status"] == "completed":
            report = kernel.store.evidence(job["evidence_id"], limit=100, max_bytes=2000000)["value"]
            known_ids.update(report["factors"])
            factors.append({"evidence_id": job["evidence_id"],
                            **(compact_factors_v8(report) if v8 else compact_factors(report))})
    catalogue = [{k: kernel.assets.get(i).get(k) for k in ("id", "name", "expression", "roles", "status")}
                 for i in sorted(known_ids)]
    batches = []
    for row in kernel.store.rows("SELECT DISTINCT batch_id FROM attempts ORDER BY rowid DESC LIMIT 2"):
        eid = kernel.store.meta("batch_evidence:" + row["batch_id"])
        if eid:
            batch = kernel.store.evidence(eid, limit=100, max_bytes=200000)["value"]
            for arm in batch["arms"]:
                if v8 and arm.get("strategy"):
                    control = control_identifiability(arm["strategy"], arm.get("summary"))
                    arm["control_identifiability"] = {key: control[key] for key in (
                        "cap_can_reduce_gross_exposure", "nonuniform_weights_at_or_below_full_allocation_cap")}
                if arm["role"] != "proposal":
                    arm.pop("strategy", None)
                if "annual" in arm:
                    arm["annual"] = [{k: r.get(k) for k in ("year", "sharpe", "return", "mean_exposure", "calendar_complete")} for r in arm["annual"]]
                for key in ("summary", "delta_from_proposal"):
                    if key in arm:
                        arm[key] = {k: v for k, v in arm[key].items() if k in {
                            "return", "sharpe", "mean_full_year_sharpe", "worst_full_year_sharpe", "full_years",
                            "max_drawdown", "fees", "slippage", "turnover", "mean_exposure", "trade_count",
                            "blocked_orders", "max_stale_fraction", "formal_target_success"}}
            batches.append({"evidence_id": eid, **batch})
    validation = []
    for job in state["validation_jobs"]:
        if job["evidence_id"]:
            report = kernel.store.evidence(job["evidence_id"], limit=100, max_bytes=2000000)["value"]
            if "candidates" in report:
                compact = {k: report.get(k) for k in ("selection", "account_capital_semantics", "project_exposure_before_unlock", "independent_holdout_claim", "formal_financial_success")}
                exposure = compact.get("project_exposure_before_unlock")
                if isinstance(exposure, dict):
                    compact["project_exposure_before_unlock"] = {"allowed": exposure.get("allowed"), "reason": exposure.get("reason"),
                        "prior_exposure_count": len(exposure.get("prior_exposures", [])), "full_records_in_evidence": True}
                compact["candidates"] = [{"strategy_id": r["strategy_id"], "normalized_spec": r.get("normalized_spec"), "status": r["status"], "error": r.get("error"),
                    **{phase: {k: (r.get(phase) or {}).get(k) for k in ("summary", "annual", "execution", "limitations")}
                       for phase in ("train", "validation")}} for r in report["candidates"]]
            else:
                compact = report
            validation.append({"job_id": job["id"], "evidence_id": job["evidence_id"], "report": compact})
    events = kernel.store.rows("SELECT seq,kind,payload FROM events WHERE seq>? AND (kind LIKE 'action_%' OR kind IN ('batch_review','validation_review')) ORDER BY seq DESC LIMIT 3", (after_revision,))
    recent = [{"kind": r["kind"], "data": json.loads(r["payload"])} for r in events]
    # A review needs the tested structure, its original hypothesis and paired
    # outcomes. Repeating the whole discovery catalogue here spent the bounded
    # prompt before the researcher could diagnose the already completed batch.
    reviewing = bool(state["unreviewed_batches"] or state["validation_jobs"])
    if reviewing:
        used = set()
        from quanta_agents.research_kernel.controller import _factor_ids
        for batch in batches:
            for arm in batch["arms"]:
                if arm.get("strategy"):
                    used.update(_factor_ids(arm["strategy"]))
        if v8 and not state["validation_jobs"]:
            selected_reports = []
            for r in factors:
                original = kernel.store.evidence(r["evidence_id"], limit=100, max_bytes=2000000)["value"]
                selected = sorted(used & set(original["factors"]))
                if selected:
                    selected_reports.append({"evidence_id": r["evidence_id"], **compact_factors_v8(
                        original, detail="selection", ids=selected)})
            factors = selected_reports
        else:
            factors = [{"evidence_id": r["evidence_id"], "scope": r["scope"],
                        "factor_ids": sorted(r["factors"]), "pointer": "/factors",
                        "status": "full_training_diagnostics_retained_for_targeted_query"} for r in factors]
        catalogue = [r for r in catalogue if r["id"] in used]
        if state["validation_jobs"]:
            catalogue = []  # The frozen validated spec is in the report itself.
    for batch in batches:
        for event in kernel.store.rows("SELECT payload FROM events WHERE kind='batch_registered' ORDER BY seq DESC"):
            proposal = json.loads(event["payload"])
            if proposal["batch_id"] == batch["batch_id"]:
                batch["hypothesis"] = proposal.get("reason", "")
                break
        reviewed = kernel.store.rows("SELECT review FROM batch_reviews WHERE batch_id=?", (batch["batch_id"],))
        if reviewed:
            batch["review"] = json.loads(reviewed[0]["review"])
            batch.pop("paired_uncertainty", None)
            batch.pop("hypothesis", None)
            # A completed review carries its conclusion into selection. The
            # next hypothesis should not pay again for every old annual row.
            for arm in batch["arms"]:
                for key in ("strategy", "annual", "execution", "delta_from_proposal"):
                    arm.pop(key, None)
                if v8:
                    arm.pop("control_identifiability", None)
                if "summary" in arm:
                    arm["summary"] = {k: arm["summary"].get(k) for k in
                        ("return", "sharpe", "mean_full_year_sharpe", "worst_full_year_sharpe", "mean_exposure", "turnover")}
            batch["detail_in_full_evidence"] = True
    if v8:
        reviews = {batch["batch_id"]: batch["review"] for batch in batches if "review" in batch}
        for event in recent:
            if event["kind"] not in {"batch_review", "action_review_batch"}:
                continue
            data = event["data"]
            result = data.get("result", data)
            bound = reviews.get(result.get("batch_id")) if isinstance(result, dict) else None
            if bound and all(bound.get(k) == value for k, value in result.items()):
                pointer = {"batch_id": result["batch_id"], "detail": "paired_results.review"}
                if "result" in data:
                    event["data"] = {**data, "result": pointer}
                else:
                    event["data"] = pointer
    trials, exposures = [], []
    for destination, method in ((trials, kernel.project.list_trials), (exposures, kernel.project.list_exposures)):
        while True:
            page = method(limit=1000, offset=len(destination))
            destination.extend(page)
            if len(page) < 1000:
                break
    ledger_id = kernel.store.put_evidence("v7_research_ledger", {"project": kernel.project.summary(),
        "stage_evidence": kernel.store.rows("SELECT * FROM stage_evidence"),
        "trials": trials, "exposures": exposures,
        "batch_reviews": kernel.store.rows("SELECT * FROM batch_reviews"),
        "runs": kernel.store.rows("SELECT id,status,evidence_id,error FROM runs"),
        "model_calls": kernel.store.rows("SELECT id,status,usage,error FROM model_calls"),
        "attempts": kernel.store.rows("SELECT * FROM attempts"), "events": recent})
    packet = {"state": state, "objective": kernel.config["objective"], "factor_evidence": factors,
              "catalogue": catalogue, "paired_results": batches, "validation_results": validation,
              "recent_actions": recent, "full_ledger_evidence_id": ledger_id,
              "interpretation": "All numeric evidence is exposed; a time split alone cannot remove repeated-selection bias."}
    if v8:
        packet["control_interpretation"] = (
            "Weights normalize before per-stock caps without redistribution. Cap flags describe possible cash effects, "
            "not observed matched exposure. Weighting/gate comparisons alone do not identify incremental risk information; "
            "inspect paired exposures, holdings, turnover and costs. Never use realized mean exposure as a hindsight trading control.")
    maximum = kernel.config["budget"]["max_context_bytes"]
    def render():
        return (BASE_CONTRACT + V8_CONTRACT if v8 else CONTRACT) + "\nRESEARCH_STATE_JSON\n" + serial(packet)
    prompt = render()
    if len(prompt.encode("utf-8")) > maximum and not v8:
        for report in factors:
            for factor in report.get("factors", {}).values():
                for stats in factor.get("ic", {}).values():
                    stats.pop("annual", None)
        packet["detail_in_full_evidence"] = ["factor annual IC"]
        prompt = render()
    if len(prompt.encode("utf-8")) > maximum:
        for report in factors:
            if "correlations" in report:
                key = (lambda r: abs(r[2] or 0)) if v8 else (lambda r: abs(r.get("mean_ic") or 0))
                report["correlations"] = sorted(report["correlations"], key=key, reverse=True)[:16]
        packet.setdefault("detail_in_full_evidence", []).append("remaining correlations")
        prompt = render()
    if len(prompt.encode("utf-8")) > maximum:
        # Explicitly index oversize query responses; never quietly drop failures.
        for event in recent:
            if len(serial(event).encode("utf-8")) > 1200:
                eid = kernel.store.put_evidence("oversize_action_result", event)
                event["data"] = {"full_evidence_id": eid, "status": "narrow_query_required"}
        prompt = render()
    if len(prompt.encode("utf-8")) > maximum:
        raise ValueError("Mandatory V7 evidence exceeds context budget; reduce factor batch size or request a narrower report")
    return {"prompt": prompt, "utf8_bytes": len(prompt.encode("utf-8")), "revision": state["revision"], "packet": packet}
