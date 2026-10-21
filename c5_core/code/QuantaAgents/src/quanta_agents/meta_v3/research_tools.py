"""Bounded controller tools backed by pinned causal and raw-share kernels.

Synthetic calibration and bounded real saved development are separate input
classes. Historical execution certification is never inferred from either.
"""
from datetime import datetime
import gzip
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import pandas as pd

from .kernel import module
from .ledger import digest, need, serial, worker_lease
from . import evidence_views
from . import research_iteration
from . import research_batch
from . import tool_resources
from .program_execution import develop as execute_program

ACTIONS = ("inspect_inputs", "diagnose_horizons", "develop_strategy", "inspect_execution",
           "read_evidence", "submit_research_report") + research_iteration.NEW_ACTIONS
LIMITS = {"inspect_inputs": 3, "diagnose_horizons": 3, "develop_strategy": 3,
          "inspect_execution": 3, "read_evidence": 4}


def save_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(serial(value))
        stream.flush()
        import os
        os.fsync(stream.fileno())


class ResearchTools:
    def __init__(self, root, task_id, case, history, imported=None, permitted_actions=None):
        from .target_schedule import validate_case_policy
        validate_case_policy(case)
        from .conditional_risk_derivation import verify_case as verify_conditional_derivation
        self.conditional_derivation_check = verify_conditional_derivation(case)
        self.root, self.task_id, self.case, self.history = Path(root), task_id, case, history
        self.report_policy = case.get('report_policy')
        need(self.report_policy is None or self.report_policy == {'version': 'claim_support_v1'},
             'unknown frozen report policy')
        if 'experiment_design_policy' in case:
            from .experiment_design import VERSION
            need(case['experiment_design_policy'] == {'version': VERSION},
                 'unknown frozen experiment design policy')
        self.limits=research_iteration.action_limits(case,LIMITS)
        self.program = module("meta.factor_strategy_program")
        self.development = module("meta.strategy_development")
        self.raw = module("raw_saved_research")
        self.horizon = module("meta.horizon_diagnostics")
        need(case["research_class"] in ("synthetic_calibration", "real_saved_development"), "unregistered research class")
        if case["research_class"] == "real_saved_development":
            from . import real_program
            self.program = real_program
            real_program.matrices(case["decision_fixture"])
            need(all(x["status"] != "fixture_complete" for x in case["raw_source_bindings"]["obligations"]), "real source cannot claim fixture completeness")
            backend = case.get("execution_backend")
            need(backend in (None, "v3_streamed_001", "v3_l2_cash_001", "v3_structural_001"), "unknown real execution backend")
            if backend == 'v3_l2_cash_001':
                from . import l2_saved_execution
                l2_saved_execution.validate_case(case)
                self.raw=l2_saved_execution
            elif backend == "v3_structural_001":
                from . import structural_execution
                self.raw = structural_execution
            elif backend == "v3_streamed_001":
                from . import saved_execution
                self.raw = saved_execution
            else:
                need(len(case["decision_fixture"]["codes"]) <= 2 and len(case["decision_fixture"]["calendar"]) <= 16,
                     "longer scope requires explicit streamed execution admission")
            if backend != 'v3_l2_cash_001':
                need(case['decision_fixture']['kind']=='exposed_real_decision_table','L2 input requires its explicit execution backend')
        self.program.fixture_axes(case["decision_fixture"])
        self.folder = self.root / "tools" / task_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.imported = imported
        self.permitted_actions = permitted_actions
        if permitted_actions is not None:
            need(type(permitted_actions) is list and 'submit_research_report' in permitted_actions and
                 set(permitted_actions) <= {'inspect_inputs','inspect_batch','inspect_execution','diagnose_execution','read_evidence','submit_research_report'},
                 'saved recovery permits only evidence reads, attribution and final')
        self.batch_folder=self.root/'batches'/task_id
        if any(self.limits[a] for a in research_batch.ACTIONS):research_batch.policy(case)

    def menu(self):
        counts = {a: sum(x["response"] is not None and x["response"]["action"] == a for x in self.history) for a in self.limits}
        unavailable={'diagnose_horizons'} if self.case.get('execution_backend')=='v3_l2_cash_001' else set()
        if self.case.get('execution_backend')=='v3_l2_cash_001' and not self.limits['diagnose_execution']:unavailable.add('read_evidence')
        if research_batch.unresolved(self.batch_folder):unavailable.update(('register_batch','develop_strategy','diagnose_horizons'))
        return tool_resources.allowed(self.root, tuple(a for a in ACTIONS if a not in unavailable and (self.permitted_actions is None or a in self.permitted_actions) and (a not in counts or counts[a] < self.limits[a])))

    def contract(self):
        from .target_schedule import public_contract as schedule_contract
        program_contract = self.development.public_contract()
        if self.case["research_class"] == "real_saved_development":
            program_contract["research_class"] = "real_saved_development"
            program_contract["limitations"] = ["Explicitly supplied real stocks and exposed development sessions; no independent OOS result.",
                "Actual historical source arrival and fills remain unverified. Full cash is never resized."]
        contract={"research_class": self.case["research_class"], "case_description": self.case["description"],
            "input_evidence_id":"input:"+digest(self.case),
            "input_evidence_semantics":"Frozen supplied case, available before any tool call. May support abstention only; it is not an evaluated strategy or a proof of historical source completeness.",
            "symbols": self.case["decision_fixture"]["codes"], "sessions": self.case["decision_fixture"]["calendar"],
            "fields": self.case["decision_fixture"]["fields"], "initial_cash": self.case["initial_cash"],
            "execution_backend": self.case.get("execution_backend", "frozen_small_scope"),
            "execution_resource_limits": self.raw.RESOURCE_BUDGET,
            "limits": self.limits, "program_contract": program_contract,
            "actions": {
                "inspect_inputs": {"arguments": {"table": "coverage|fields|eligibility|execution_obligations", "offset": "integer>=0", "limit": "integer 1..32"},
                    "returns": "coverage groups all supplied fields and obligations with counts, notes and source hashes; raw tables remain available."},
                "diagnose_horizons": {"arguments": {"filter_expression": "causal expression <=2000 characters",
                    "feature_expression": "causal expression <=2000 characters", "horizons": "1..3 distinct integers, each 1..10"},
                    "returns": "Gross open t+1 to open t+h+1 on a common sample; feature high/low median groups and same-date other-symbol matches are descriptive, not portfolio returns or causal proof. Every horizon, group and matched comparison is registered."},
                "develop_strategy": {"arguments": {"program": "exact factor_strategy_program_v1 object defined by program_contract"},
                    "returns": "Freezes program and full targets, then executes full cash/raw shares, T+1, fees, partial fills and rejection paths. Three requested subattempts per candidate. No arbitrary Python."},
                "inspect_execution": {"arguments": {"evidence_id": "prior develop_strategy call ID", "table": "daily_compact|trades_compact|daily|trades|rejections|failure", "offset": "integer>=0", "limit": "integer 1..32"},
                    "returns": "Use daily_compact/trades_compact for accounting. Returns whole rows fitting context bytes; limit is an upper bound. Follow exact next_offset until null; never infer a short page means completion. All source rows remain preserved. A single oversized row reports blocked delivery without advancing."},
                "read_evidence": {"arguments": {"evidence_id": "prior diagnose_horizons call ID", "table": "events_compact|events|pre_signal_exclusions", "offset": "integer>=0", "limit": "integer 1..32"},
                    "returns": "events_compact retains outcomes and exclusions in a columnar view. Returns whole rows fitting context bytes; limit is an upper bound. Follow exact next_offset until null; a short page is not completion. events retains full metadata; a single oversized row reports blocked delivery without advancing."},
                "submit_research_report": {"arguments": {"outcome": "abstain|strategy_for_development", "conclusion": "nonempty string <=8000 characters",
                    "evidence_ids": "1..16 prior settled call IDs from this task", "program_evidence_id": "prior successful develop_strategy call ID or null for abstention",
                    "limitations": "1..16 nonempty strings <=2000 each", "next_step": "nonempty string <=2000", "falsifiers": "1..16 nonempty strings <=2000 each"},
                    "rules": "A development strategy requires successful funded execution and its saved program; abstention requires evidence. No synthetic or tiny development score can be a certified net OOS strategy. Final opportunity consumed even for invalid report; no controller-written final."}},
            "limitations": ["Research class: " + self.case["research_class"] + "; no formal strategy success or historical market certification.",
                "All dates and endpoint prices are exposed development data. No independent market sample count.",
                "Cost and capacity parameters are declared simulation, never real brokerage or real book evidence.",
                "Full originals retained; public tool summaries are bounded and evidence pages remain available."]}
        scheduling = schedule_contract(self.case)
        if scheduling is not None:
            contract['target_schedule'] = scheduling
            contract['actions']['develop_strategy']['returns'] += (
                ' This case dispatches targets only at its fixed five-session anchors and terminal liquidation. '
                'Other sessions retain actual shares; full daily NAV and all execution constraints remain.')
        if self.report_policy is not None:
            from .claim_support import contract as claim_contract
            contract['actions']['submit_research_report']['arguments']['claims'] = claim_contract()
            contract['actions']['submit_research_report']['rules'] += (
                ' V4 requires 1..16 explicit claims. Numeric contradictions and unsupported causal extrapolation are flagged separately from legal submission. Correct numeric tuples do not certify free-text conclusions; substantive report acceptance remains unverified.')
        if self.case.get('execution_backend')=='v3_l2_cash_001':
            for action in ('diagnose_horizons','read_evidence'):contract['actions'].pop(action)
            contract['actions']['develop_strategy']['returns']='Freeze causal previous-session targets; size integer limit IOC orders from the pre-dispatch quote and prior complete NAV. Persist actual scenario fills, per-order fees, T+1, dynamic notice rights, tax, rejections and every cash day. No automatic renormalization or use of later proceeds for same-clock orders.'
            contract['actions']['inspect_execution']['arguments']['table']='daily_compact|trades_compact|daily|trades|rejections|failure'
            contract['actions']['inspect_execution']['returns']='Whole original rows with exact next_offset. Each trade row contains the frozen order, target and saved account receipt; daily rows contain full valuation and rights/tax evidence. Failed-run partial originals are available.'
            contract['limitations'][2]='Finite selected L2 observations and explicitly frozen assumptions; actual source arrival, brokerage contracts and missing corporate actions are not certified. A fully rejected run is execution evidence for abstention, not a funded strategy. Daily-open horizon diagnostics are unavailable because this source does not supply a validated open-price series.'
        if any(self.limits[a] for a in research_iteration.NEW_ACTIONS):
            contract['research_opportunity_policy']='Per-action bounds are frozen in this task, not guaranteed call slots. Shared task/stage tokens, time, unknown-call blocking and final reserve still apply. Original, controls and revisions all count; no free reruns.'
            contract['actions'].update({
                'diagnose_execution':{'arguments':{'evidence_ids':'1..4 distinct prior develop_strategy IDs; first is reference'},
                    'returns':'Full-capital saved-path attribution: costs, turnover, cash/marked exposure, sale-then-buy counts, concentration, failures and aligned-program differences. Failed accounts retain unvalued fills, cash, holdings and rights separately; final NAV/return stay unknown. Does not choose a causal mechanism or rerun anything.'},
                'register_experiment':{'arguments':{'baseline_evidence_id':'prior develop_strategy ID','diagnosis_evidence_id':'prior diagnose_execution ID including baseline',
                    'mechanism_hypotheses':'1..6 competing explanations, <=1500 characters each','distinguishing_prediction':'observable contrast <=2000 characters',
                    'structural_change':'proposed signal/portfolio/risk/trend or other supported change <=2000','revision_program':'exact valid strategy program',
                    'success_rule':'preregistered comparison criterion <=2000','failure_rule':'falsifying result <=2000',
                    'contrast_checks':'Optional 1..6 objects {id,hypothesis_index,metric,operator,threshold}. Unique id; zero-based hypothesis index; metric from contrast_metric_units; operator lt|lte|eq|gte|gt; threshold is a decimal string (at most 15 integer and 12 fractional digits). Each check concerns revision minus baseline, frozen before execution.'},
                    'returns':'Frozen baseline, diagnosis and revision-program bindings before execution. A rule in a saved ordinary result, including a failed account, or an already registered batch cannot receive a new prospective revision label; cite its original evidence. Narrative-only changes cannot register as new rules. Exact executable duplicates do not execute again. No general semantic-equivalence claim. Not a successful iteration until evaluated; supported current inputs only.'},
                'request_research_extension':{'arguments':{'evidence_ids':'1..8 local settled IDs','problem':'<=4000 characters','request_kind':'data|universe|benchmark|tool|asset_class',
                    'requested_fields':'Required for data requests:1..8 distinct proposed field names, machine-checked against delivered additions; omit for other kinds',
                    'specification':'needed scope, fields and semantics <=4000','expected_information_gain':'how this distinguishes mechanisms <=4000',
                    'requested_resource_bounds':'exact nonnegative integer fields symbols,sessions,model_calls,download_bytes,wall_seconds'},
                    'returns':'Saved request for controller admission of a new bounded scope. Request does not grant resources, change the current scope or load new data.'}})
            contract['actions']['develop_strategy']['arguments']['experiment_evidence_id']='Optional prior register_experiment ID; program must exactly match its frozen revision. Evaluation automatically compares saved account results against its baseline.'
            from .experiment_contrast import METRICS
            contract['actions']['register_experiment']['contrast_metric_units']=dict(METRICS)
            contract['actions']['register_experiment']['contrast_interpretation']='Declared checks are reported as matched, contradicted or unevaluable against the full frozen cash/calendar. Missing data is never zero or a pass. Matching observations does not identify a cause, evaluate prose criteria or certify a successful strategy.'
            if self.case.get('experiment_design_policy') is not None:
                contract['actions']['register_experiment']['design_review'] = {
                    **self.case['experiment_design_policy'], 'warning_only': True,
                    'returns': 'design_review checks declared scalar conjunctions for contradictions, duplicate or redundant conditions, and equivalent predictions before execution. No checks means not evaluated. Warnings do not reject registration or grant new opportunities.',
                    'limitations': 'Independent real scalar coordinates only; no assessment of prose, feasible market states, controls, causal identification, statistical power or strategy success. Different numeric predictions do not establish a mechanism.'}
            contract['actions']['read_evidence']={'arguments':{'evidence_id':'prior diagnose_execution ID, or diagnose_horizons ID on daily-data tasks','table':'attribution_accounts|attribution_monthly|attribution_pairs|attribution_reentries|attribution_daily; daily-data horizon evidence also supports events_compact|events|pre_signal_exclusions',
                'offset':'integer>=0','limit':'integer1..32'},'returns':'Whole contiguous attribution rows with exact next_offset. Additional partial-account tables: attribution_unvalued_trades, attribution_rights, attribution_lots, attribution_unresolved_reentries. Original horizon event tables remain available on daily-data tasks.'}
            if any(self.limits[a] for a in research_batch.ACTIONS):
                contract['batch_policy']=research_batch.policy(self.case)
                contract['actions'].update({
                    'register_batch':{'arguments':{'families':'1..4 family objects; exact fields below','comparisons':'0..16 objects {kind,families,prediction}; kinds incremental_condition,parameter_neighborhood,alternative_digits,alternative_explanation,generator_control',
                        'selection_rule':'<=2000 characters; frozen criterion, no automatic winner','stop_rule':'continue_settled_failures|stop_on_first_failure'},
                        'family_fields':{'id':'identifier','priority':'integer1..4','origin':'model_intuition|fixed|random','mechanism_status':'hypothesis|unknown_anomaly','mechanism':'<=1500 characters; unknown is allowed',
                            'role':'original|single_condition|combination|alternative|baseline','program_template':'exact strategy program with {{parameter}} only inside factor/weight expressions',
                            'parameters':'object of <=4 named axes; each1..8 finite numbers; Cartesian expansion <=32 for entire batch','digit_fields':'list of referenced prederived digit fields; requires exact controller provenance and alternative controls'},
                        'returns':'Freezes all expanded programs, data/cost/field semantics, complexity, requested comparisons and per-candidate/per-scan reservations before execution. Invalid candidates and duplicate parameter values remain counted and visible. No model or execution inside registration.'},
                    'execute_batch':{'arguments':{'registration_evidence_id':'prior register_batch ID'},
                        'returns':'One model tool action dispatches the already reserved candidates through the same raw-share mechanics. No model calls per candidate. All results/failures/unknowns/not-started/reuse references preserved. Full results require inspect_batch table=results to completion. Unknown worker outcomes block new scans; recovery only reconciles saved work.'},
                    'inspect_batch':{'arguments':{'registration_evidence_id':'prior register_batch ID','candidate_id':'null for batch tables or c001-style candidate ID',
                        'table':'batch: candidates|results|comparisons; candidate: daily|trades|failure|attribution','offset':'integer>=0','limit':'integer1..32'},
                        'returns':'Exact contiguous whole-row pagination, all failures included. Read the entire results table matching the cited execution snapshot before selecting a batch strategy. Digit fields are prederived controlled inputs; arbitrary modulo/Python is unavailable.'}})
                contract['actions']['submit_research_report']['arguments']['batch_candidate_id']='Optional only for a strategy selected from execute_batch evidence: c001-style ID; all batch result rows must have been read. Reuses the existing funded artifact without rerunning.'
                contract['actions']['submit_research_report']['arguments']['program_evidence_id']='For an ordinary strategy: prior successful develop_strategy call ID, with batch_candidate_id omitted. For a batch strategy: prior execute_batch call ID, with batch_candidate_id set to its c001-style candidate ID. For abstention: null, with batch_candidate_id omitted.'
                contract['actions']['diagnose_execution']['arguments']['evidence_ids']='1..4 candidate references: prior develop_strategy ID or prior execute_batch ID followed by /c001; first is the reference'
                contract['actions']['register_experiment']['arguments']['baseline_evidence_id']='prior develop_strategy ID or execute_batch_ID/c001; must be in the cited diagnosis'
                contract['actions']['inspect_execution']['arguments']['evidence_id']='prior develop_strategy ID or execute_batch_ID/c001; reads the saved artifact without rerunning'
                contract['batch_budget_interpretation']='Candidate and scan-cell reservations include ordinary strategies and horizon diagnostics. Output bytes are a polling stop threshold, not an OS hard cap. Generator origin is a declaration; fair fixed/random-arm provenance still needs separately pinned generator code/seed and a common evaluation protocol.'
            for action in research_iteration.NEW_ACTIONS:
                if not self.limits[action]:contract['actions'].pop(action,None)
        if self.permitted_actions is not None:
            contract['actions']={k:v for k,v in contract['actions'].items() if k in self.permitted_actions}
            contract['saved_recovery_policy']='Only saved evidence inspection, accounting attribution and authentic final are available. Prior actions still count against their original limits. No new candidate, scan, registration, acquisition or budget extension. Original interrupted delivery remains recorded.'
        resources = tool_resources.status(self.root)
        if resources['integrated']:
            contract['trial_tool_resources']={k:v for k,v in resources.items() if k!='entries'}
            contract['trial_tool_resource_semantics']='Shared trial logical reservations and per-application measurements; failures and duplicates remain charged. Do not add these overlapping timings to whole-process job counters.'
        from .process_envelope import public_status
        process=public_status(self.root)
        if process['integrated']:contract['trial_process_resources']=process
        if self.conditional_derivation_check is not None:
            from .conditional_risk_derivation import public_contract as derivation_contract
            contract['conditional_risk_derivation'] = derivation_contract(self.case)
            contract['evidence_class'] = 'generated_engineering'
            contract['limitations'].insert(0, 'GENERATED ENGINEERING ONLY: the real adapter is exercised with generated inputs; this is not market research or an admitted real task.')
            contract['conditional_derivation_resource_scope'] = 'Bounded preparation and repeated numerical verification are not a registered fair-study resource protocol. Whole engineering-script measurements must include them; no formal architecture comparison is admitted.'
        return contract

    def _frames(self):
        fixture = self.case["decision_fixture"]
        days, codes = fixture["calendar"], fixture["codes"]
        index = pd.DatetimeIndex(days)
        fields = {x["name"]: pd.DataFrame(float("nan"), index=index, columns=codes) for x in fixture["fields"]}
        eligible = pd.DataFrame(False, index=index, columns=codes)
        for x in fixture["field_rows"]:
            cutoff = datetime.fromisoformat(x["session"] + "T15:10:00+08:00")
            if max(datetime.fromisoformat(x["available_at"]), datetime.fromisoformat(x["effective_at"])) <= cutoff:
                fields[x["field"]].loc[pd.Timestamp(x["session"]), x["symbol"]] = x["value"]
        for x in fixture["eligibility_rows"]:
            cutoff = datetime.fromisoformat(x["session"] + "T15:10:00+08:00")
            eligible.loc[pd.Timestamp(x["session"]), x["symbol"]] = x["eligible"] and max(
                datetime.fromisoformat(x["available_at"]), datetime.fromisoformat(x["effective_at"])) <= cutoff
        opening = pd.DataFrame(float("nan"), index=index, columns=codes)
        for artifact in self.case["raw_source_bindings"]["source_artifacts"]:
            path = Path(artifact["root"]) / artifact["rows_file"]
            need(path.stat().st_size <= self.raw.MAX_COMPRESSED, "source compressed bound")
            content = path.read_bytes()
            need(hashlib.sha256(content).hexdigest() == artifact["rows_sha256"], "source drift")
            expanded = gzip.decompress(content)
            need(len(expanded) <= self.raw.MAX_DECOMPRESSED, "source expansion bound")
            for row in json.loads(expanded):
                if row["date"] in days:
                    value = row["raw_open"]
                    opening.loc[pd.Timestamp(row["date"]), artifact["code"]] = float(value) if value is not None else float("nan")
        return fields, eligible, opening

    @staticmethod
    def _page(rows, args):
        offset, limit = args["offset"], args["limit"]
        need(type(offset) is int and 0 <= offset <= len(rows) and type(limit) is int and 1 <= limit <= 32, "page bound")
        return {"rows": rows[offset:offset + limit], "offset": offset, "total_rows": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    def _prior(self, evidence_id, expected_action):
        rows = [x for x in self.history if x["id"] == evidence_id and x["result"] is not None and x["response"]["action"] == expected_action]
        need(len(rows) == 1, "evidence is not a settled result of this task/action")
        path = self.folder / evidence_id / "artifact.json"
        if self.imported and any(x["id"] == evidence_id for x in self.imported["rows"]):
            path = Path(self.imported["root"]) / "tools" / self.imported["task_id"] / evidence_id / "artifact.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        need(digest(value) == rows[0]["result"]["artifact_hash"], "saved tool evidence changed")
        return value

    def _candidate(self,reference):
        """A batch execution ID/candidate ID references its saved raw artifact."""
        if '/' not in reference:return self._prior(reference,'develop_strategy')
        parts=reference.split('/');need(len(parts)==2,'candidate reference must be execution_id/c001')
        eid,candidate_id=parts;execution=self._prior(eid,'execute_batch')
        need(any(r['candidate_id']==candidate_id and r['status'] in ('completed','failed','reused') for r in execution['candidates']),
             'batch reference must identify a saved executed candidate')
        registration=self._prior(execution['batch_id'],'register_batch')
        return self._batch_candidate(execution['batch_id'],registration,candidate_id)

    def _imported_batch(self,batch_id):
        return self.imported is not None and any(x['id']==batch_id for x in self.imported['rows'])

    def _batch_candidate(self,batch_id,registration,candidate_id):
        imported=self._imported_batch(batch_id)
        folder=(Path(self.imported['root'])/'batches'/self.imported['task_id'] if imported else self.batch_folder)/batch_id
        need(research_batch.read(folder/'registration.json')==registration,'saved batch registration drift')
        def refuse_write(*_):need(False,'imported candidate receipt missing; read-only recovery cannot repair parent')
        return research_batch.artifact_for(folder,registration,candidate_id,refuse_write if imported else save_once)

    def _batch_rows(self,batch_id,registration):
        if not self._imported_batch(batch_id):return self._run_batch(batch_id,execute_new=False)['candidates']
        executions=[self._prior(x['id'],'execute_batch') for x in self.imported['rows']
                    if (x.get('response') or {}).get('action')=='execute_batch' and (x.get('result') or {}).get('artifact_hash')]
        executions=[x for x in executions if x['batch_id']==batch_id]
        need(bool(executions) and executions[-1]['registration_hash']==digest(registration),'saved imported batch execution missing or changed')
        return executions[-1]['candidates']

    def execute(self, call_id, action, args):
        return tool_resources.execute(self,call_id,action,args,self._execute)

    def _execute(self, call_id, action, args):
        need(self.permitted_actions is None or action in self.permitted_actions,'action forbidden in saved-only recovery')
        prior_count=sum(x.get('id')!=call_id and x.get('response') is not None and x['response']['action']==action for x in self.history)
        need(action in self.limits and prior_count<self.limits[action],'action unavailable or frozen opportunity bound exhausted')
        if self.case.get('execution_backend')=='v3_l2_cash_001':
            need(action!='diagnose_horizons','unavailable daily-open diagnostic on L2 observations')
        folder = self.folder / call_id
        folder.mkdir(exist_ok=False)
        save_once(folder / "request.json", {"action": action, "arguments": args})
        if action=='diagnose_execution':
            need(set(args)=={'evidence_ids'} and type(args['evidence_ids']) is list and 1<=len(args['evidence_ids'])<=4 and len(set(args['evidence_ids']))==len(args['evidence_ids']),'bounded attribution evidence IDs')
            artifact=research_iteration.diagnose([(eid,self._candidate(eid)) for eid in args['evidence_ids']],self.case['decision_fixture']['calendar'])
            public=research_iteration.public_diagnosis(artifact)
        elif action=='register_experiment':
            baseline=self._candidate(args['baseline_evidence_id'])
            diagnosis=self._prior(args['diagnosis_evidence_id'],'diagnose_execution')
            artifact=research_iteration.register_experiment(args,baseline,diagnosis,self.program.validate_program,self.case['decision_fixture']['fields'])
            self._require_new_executable(args['revision_program'],folder)
            if self.case.get('experiment_design_policy') is not None:
                from .experiment_design import review_experiment_design
                artifact['design_review'] = review_experiment_design(artifact['declaration'])
            public=artifact
        elif action=='request_research_extension':
            known={x['id'] for x in self.history if x.get('result') is not None}
            artifact=research_iteration.extension_request(args,known,digest(self.case));public=artifact
        elif action=='register_batch':
            need(not research_batch.unresolved(self.batch_folder),'unresolved batch blocks new research')
            self.batch_folder.mkdir(parents=True,exist_ok=True)
            with worker_lease(self.batch_folder):
                target=self.batch_folder/call_id;target.mkdir(exist_ok=False)
                artifact=research_batch.register(target,args,self.case,self.program.validate_program,save_once,
                    ordinary_candidates=self._ordinary_candidates(),other_scan_cells=self._diagnostic_reservations()['scan_cells'],
                    other_comparisons=self._diagnostic_reservations()['comparisons'])
            public={'batch_id':call_id,'reserved_candidates':artifact['reserved_candidates'],'reserved_scan_cells':artifact['reserved_scan_cells'],
                'validation_failures':sum(r['validation_error'] is not None for r in artifact['candidates']),
                'selection_rule':args['selection_rule'],'stop_rule':args['stop_rule'],
                'read_all':'inspect_batch registration_evidence_id='+call_id+' table=candidates; all candidates preserved; no execution yet'}
        elif action=='execute_batch':
            need(set(args)=={'registration_evidence_id'},'exact batch execution fields')
            artifact=self._run_batch(args['registration_evidence_id'],execute_new=True)
            public=self._batch_public(artifact)
        elif action=='inspect_batch':
            need(set(args)=={'registration_evidence_id','candidate_id','table','offset','limit'},'exact batch evidence fields')
            batch_id=args['registration_evidence_id'];registration=self._prior(batch_id,'register_batch')
            target=self.batch_folder/batch_id
            if args['candidate_id'] is None:
                need(args['table'] in ('candidates','results','comparisons'),'batch table')
                rows=registration['candidates'] if args['table']=='candidates' else registration['declaration']['comparisons'] if args['table']=='comparisons' else self._batch_rows(batch_id,registration)
            else:
                need(args['table'] in ('daily','trades','failure','attribution'),'candidate table')
                prior=self._batch_candidate(batch_id,registration,args['candidate_id'])
                raw=prior.get('raw') or {};body=raw.get('result') or raw.get('partial') or {}
                rows=[research_iteration.account_attribution(args['candidate_id'],prior,self.case['decision_fixture']['calendar'])] if args['table']=='attribution' else [{'error':raw.get('error'),'status':raw.get('status'),'final_snapshot':body.get('final_snapshot')}] if args['table']=='failure' else body.get(args['table'],[])
            artifact=self._page(rows,args)
            if args['candidate_id'] is None and args['table']=='results':
                artifact['batch_results_hash']=digest(rows);artifact['registration_evidence_id']=batch_id
            artifact,public=evidence_views.page_for_context(artifact,lambda p:p,
                lambda p,v:{'evidence_id':call_id,'action':action,'artifact_hash':digest(p),'public':v,'execution_valid':False,'formal_target_success':False},args['limit'])
        elif action == "inspect_inputs":
            need(set(args) == {"table", "offset", "limit"}, "inspect exact arguments")
            rows = {"coverage": evidence_views.input_coverage(self.case), "fields": self.case["decision_fixture"]["field_rows"],
                    "eligibility": self.case["decision_fixture"]["eligibility_rows"],
                    "execution_obligations": self.case["raw_source_bindings"]["obligations"]}.get(args["table"])
            need(rows is not None, "unknown input table")
            artifact = self._page(rows, args)
            public = artifact
        elif action == "diagnose_horizons":
            need(not research_batch.unresolved(self.batch_folder),'unresolved batch blocks new research')
            if 'batch_policy' in self.case:
                reserve=self._diagnostic_reservations(extra=(call_id,args))
                research_batch.check_ordinary_budget(self.batch_folder,self.case,self._ordinary_candidates(),reserve['scan_cells'])
            need(set(args) == {"filter_expression", "feature_expression", "horizons"}, "diagnostic exact arguments")
            need(type(args["horizons"]) is list and 1 <= len(args["horizons"]) <= 3 and
                 all(type(h) is int and 1 <= h <= 10 for h in args["horizons"]), "horizon bounds")
            # Requested subattempts survive failure and precede numeric work.
            save_once(folder / "subattempts.json", {"horizons": args["horizons"],
                "comparisons_per_horizon": ["all", "feature_low", "feature_high", "same_date_other_symbol"],
                "independent_research_samples": 0})
            fields, eligible, opening = self._frames()
            algebra = module("meta.factor_algebra")
            for expr in (args["filter_expression"], args["feature_expression"]):
                need(type(expr) is str and 0 < len(expr) <= 2000, "expression length")
            filters = algebra.evaluate_expression(args["filter_expression"], fields, rank_universe=eligible)
            feature = algebra.evaluate_expression(args["feature_expression"], fields, rank_universe=eligible)
            days = self.case["decision_fixture"]["calendar"]
            identity = {"case_id": self.task_id, "split": "development", "datahash": digest(self.case),
                "strategy_hash": digest(args), "source_hash": digest(self.raw.engine_sources()),
                "start": days[0], "end": days[-1], "loaded_through": days[-1]}
            artifact = self.horizon.build_horizon_diagnostics(opening=opening, eligible=eligible,
                scores=feature, filters=filters, feature=feature, identity=identity,
                expression=args["feature_expression"], horizons=args["horizons"])
            common = [x for x in artifact["tables"]["events"] if x["included_common_sample"]]
            median = float(pd.Series([x["feature"] for x in common], dtype=float).median()) if common else None
            groups = []
            for h in args["horizons"]:
                rows = []
                for event in common:
                    other = [c for c in opening.columns if c != event["symbol"] and bool(eligible.loc[pd.Timestamp(event["signal_date"]), c])]
                    controls = []
                    for code in other:
                        start, end = event["entry_session_index"], event["outcomes"][str(h)]["exit_session_index"]
                        a, b = opening.iloc[start][code], opening.iloc[end][code]
                        if pd.notna(a) and pd.notna(b) and a > 0 and b > 0:
                            controls.append(float(b / a - 1))
                    rows.append({"event_id": event["event_id"], "signal_date": event["signal_date"],
                        "group": "high" if event["feature"] > median else "low", "return": event["outcomes"][str(h)]["gross_return"],
                        "matched_other_symbol": sum(controls) / len(controls) if controls else None})
                mean = lambda xs: sum(xs) / len(xs) if xs else None
                groups.append({"horizon": h, "feature_median": median, "low_mean": mean([x["return"] for x in rows if x["group"] == "low"]),
                    "high_mean": mean([x["return"] for x in rows if x["group"] == "high"]),
                    "matched_pairs": sum(x["matched_other_symbol"] is not None for x in rows),
                    "matched_mean_difference": mean([x["return"] - x["matched_other_symbol"] for x in rows if x["matched_other_symbol"] is not None]),
                    "rows": rows})
            artifact["descriptive_conditioning"] = groups
            public = {"horizon_summary": evidence_views.horizon_summary(artifact),
                      "caution": "Median grouping is descriptive development analysis; same-date control may also satisfy the signal. Tiny correlated sample, no causal or net-profit claim."}
        elif action == "develop_strategy":
            need(set(args) in ({'program'},{'program','experiment_evidence_id'}), "develop exact arguments")
            need(not research_batch.unresolved(self.batch_folder),'unresolved batch blocks new research')
            if 'batch_policy' in self.case:
                research_batch.check_ordinary_budget(self.batch_folder,self.case,self._ordinary_candidates(exclude=call_id)+1,self._diagnostic_reservations()['scan_cells'])
            if 'experiment_evidence_id' in args:
                experiment=self._prior(args['experiment_evidence_id'],'register_experiment')
                need(experiment['revision_program_hash']==digest(args['program']),'program differs from preregistered revision')
            self.program.validate_program(args['program'],public_field_contract=self.case['decision_fixture']['fields'])
            self._require_new_executable(args['program'],folder)
            save_once(folder / "subattempts.json", {"candidate_count": 1, "internal_variants": 1,
                "requested": ["compile", "target_generation", "raw_mechanical_execution"]})
            if self.case.get('execution_backend')=='v3_l2_cash_001':
                artifact=execute_program(folder,self.case,args['program'],save_once)
                self._attach_experiment(artifact,args)
                public=self.raw.public_result(artifact['raw'],self.case,args['program'])
                if 'experiment' in artifact:public['experiment']=artifact['experiment']['public']
                save_once(folder/'artifact.json',artifact)
                result={'evidence_id':call_id,'action':action,'artifact_hash':digest(artifact),'public':public,
                    'execution_valid':False,'formal_target_success':False}
                save_once(folder/'result.json',result)
                return result
            # Fresh bounded mechanical child belongs to this already reserved
            # research action; it neither resets nor extends research budgets.
            artifact=execute_program(folder,self.case,args['program'],save_once)
            result,raw_result=artifact['workbench'],artifact['raw']
            self._attach_experiment(artifact,args)
            complete = raw_result["result"] if raw_result else None
            public = {"program_hash": digest(args["program"]), "workbench_status": result["status"],
                "raw_status": raw_result["status"] if raw_result else None,
                "full_initial_cash": self.case["initial_cash"], "costs_applied": complete is not None,
                "cost_source": "declared simulation on " + self.case["research_class"], "execution_valid": False, "formal_target_success": False,
                "error": raw_result["error"] if raw_result else result.get("error"),
                "final_snapshot": complete["final_snapshot"] if complete else None,
                "daily": complete["daily"] if complete else None,
                "trades": complete["trades"] if complete else None,
                "valuation_complete_through": raw_result.get("valuation_complete_through") if raw_result else None}
            if self.case["research_class"] == "real_saved_development" and complete:
                from decimal import Decimal
                public["cash_summary"] = {"initial_cash": complete["initial_cash"],
                    "final_cash": complete["final_snapshot"]["cash"], "fees_paid": complete["final_snapshot"]["fees_paid"],
                    "slippage_paid": str(sum((Decimal(t["slippage_amount"]) for t in complete["trades"]), Decimal(0))),
                    "positions": {c: sum(l["quantity"] for l in complete["final_snapshot"]["lots"].values() if l["symbol"] == c)
                                  for c in self.case["decision_fixture"]["codes"]}}
                public["table_counts"] = {k: len(complete[k]) for k in ("daily", "trades", "rejections")}
                public["account_summary"] = evidence_views.account_summary(complete)
                public["daily"] = {"original_preserved": True, "query": "inspect_execution table=daily"}
                public["trades"] = {"original_preserved": True, "query": "inspect_execution table=trades"}
                public["final_snapshot"] = {k:v for k,v in complete["final_snapshot"].items() if k != "lots"}
            if 'experiment' in artifact:public['experiment']=artifact['experiment']['public']
        elif action in ("inspect_execution", "read_evidence"):
            need(set(args) == {"evidence_id", "table", "offset", "limit"}, "page exact arguments")
            if action == "read_evidence":
                if args['table'].startswith('attribution_'):
                    prior=self._prior(args['evidence_id'],'diagnose_execution')
                    table=args['table'];need(table in ('attribution_accounts','attribution_monthly','attribution_pairs','attribution_reentries','attribution_daily',
                        'attribution_unvalued_trades','attribution_unresolved_reentries','attribution_rights','attribution_lots'),'attribution table invalid')
                    if table=='attribution_accounts':rows=research_iteration.public_diagnosis(prior)['accounts']
                    elif table=='attribution_pairs':rows=[{'reference_evidence_id':p['reference_evidence_id'],'comparison_evidence_id':p['comparison_evidence_id'],**r} for p in prior['pairs'] for r in p.get('daily',[])]
                    else:
                        field=table.removeprefix('attribution_')
                        rows=[{'evidence_id':a['evidence_id'],**r} for a in prior['accounts'] for r in a.get(field,[])]
                else:
                    prior = self._prior(args["evidence_id"], "diagnose_horizons")
                    need(args["table"] in ("events_compact", "events", "pre_signal_exclusions"), "event table invalid")
                    rows = prior["tables"]["events" if args["table"] == "events_compact" else args["table"]]
            else:
                prior = self._candidate(args["evidence_id"])
                if self.case.get('execution_backend')=='v3_l2_cash_001':
                    need(args['table'] in ('daily_compact','trades_compact','daily','trades','rejections','failure'),'L2 accounting table unavailable')
                need(args["table"] in ("daily_compact", "trades_compact", "daily", "trades", "rejections", "failure"), "execution table invalid")
                raw = prior["raw"]
                if not raw:
                    rows = [{"error": prior["workbench"].get("error"), "raw_execution_started": False}] if args["table"] == "failure" else []
                elif args["table"] == "failure":
                    rows = [{"error": raw.get("error"), "raw_status": raw["status"],
                             "valuation_complete_through": raw.get("valuation_complete_through"),
                             "pending_event_intent": raw.get("pending_event_intent"),
                             "partial_original_sha256": digest(raw.get("partial")),
                             "partial_preserved_in_original": raw.get("partial") is not None,
                             "execution_valid": False, "formal_target_success": False}]
                else:
                    original_table = args["table"].removesuffix("_compact")
                    body=raw.get('result') or (raw.get('partial') if self.case.get('execution_backend')=='v3_l2_cash_001' else {}) or {}
                    rows = body.get(original_table, [])
            artifact = self._page(rows, args)
            def project(page):
                if action == "read_evidence" and args["table"] == "events_compact":
                    return evidence_views.compact_events(page)
                if action == "inspect_execution" and args["table"] in ("daily_compact", "trades_compact"):
                    if self.case.get('execution_backend')=='v3_l2_cash_001':
                        return self.raw.compact(page,args['table'])
                    return evidence_views.compact_execution(page, args["table"])
                return page
            def wrap(page, view):
                return {"evidence_id":call_id, "action":action, "artifact_hash":digest(page), "public":view,
                        "execution_valid":False, "formal_target_success":False}
            artifact, public = evidence_views.page_for_context(artifact, project, wrap, args["limit"])
        else:
            raise ValueError("unregistered research action")
        if self.conditional_derivation_check is not None:
            public.update(evidence_class='generated_engineering', real_research=False)
        save_once(folder / "artifact.json", artifact)
        result = {"evidence_id": call_id, "action": action, "artifact_hash": digest(artifact), "public": public,
                  "execution_valid": False, "formal_target_success": False}
        save_once(folder / "result.json", result)
        return result

    def _require_new_executable(self,program,folder):
        """Use original task evidence for both prospective labels and execution."""
        executable=research_iteration.executable_program_hash(program)
        duplicate=research_batch.find_recorded(self.batch_folder,self.case,executable)
        if duplicate:
            save_once(folder/'duplicate_reference.json',{'requested_program_hash':digest(program),
                'executable_program_hash':executable,'reuse_batch_candidate':duplicate,'new_execution':False})
            need(False,'identical executable rules recorded in batch; cite the batch candidate instead of new preregistration or execution')
        for prior in self.history:
            if (prior.get('response') or {}).get('action')!='develop_strategy' or not (prior.get('result') or {}).get('artifact_hash'):continue
            previous=self._prior(prior['id'],'develop_strategy')
            if research_iteration.executable_program_hash(previous['program'])==executable:
                save_once(folder/'duplicate_reference.json',{'requested_program_hash':digest(program),
                    'executable_program_hash':executable,'reuse_evidence_id':prior['id'],'new_execution':False})
                need(False,'identical executable rules already recorded; reference '+prior['id']+' instead of new preregistration or execution')

    def _attach_experiment(self,artifact,args):
        if 'experiment_evidence_id' not in args:return
        experiment=self._prior(args['experiment_evidence_id'],'register_experiment')
        baseline_id=experiment['declaration']['baseline_evidence_id']
        baseline=self._candidate(baseline_id)
        attribution=research_iteration.diagnose([(baseline_id,baseline),('registered_revision',artifact)],self.case['decision_fixture']['calendar'])
        artifact['experiment']={'registration_evidence_id':args['experiment_evidence_id'],'registration_hash':digest(experiment),
            'baseline_evidence_id':baseline_id,'declared_success_rule':experiment['declaration']['success_rule'],
            'declared_failure_rule':experiment['declaration']['failure_rule'],'attribution':attribution,
            'public':{'registration_evidence_id':args['experiment_evidence_id'],'baseline_evidence_id':baseline_id,
                'comparison':research_iteration.public_diagnosis(attribution),'criterion_judgment':'Model must judge the preregistered criteria from these facts; no automatic favorable verdict',
                'formal_target_success':False}}
        if 'contrast_checks' in experiment['declaration']:
            from .experiment_contrast import evaluate
            result=evaluate(experiment,attribution,self.case['decision_fixture']['calendar'],self.case['initial_cash'])
            artifact['experiment']['declared_check_results']=result
            artifact['experiment']['public']['declared_check_results']=result

    def _ordinary_candidates(self,exclude=None):
        return sum(r.get('id')!=exclude and (r.get('response') or {}).get('action')=='develop_strategy' for r in self.history)

    def _diagnostic_reservations(self,extra=None):
        requests=[]
        for row in self.history:
            if (row.get('response') or {}).get('action')!='diagnose_horizons' or (extra and row['id']==extra[0]):continue
            try:requests.append(json.loads(row['response']['arguments_json']))
            except (ValueError,KeyError):requests.append({})
        if extra:requests.append(extra[1])
        symbols=len(self.case['decision_fixture']['codes']);stockdays=symbols*len(self.case['decision_fixture']['calendar'])
        counts=[min(3,len(r['horizons'])) if type(r.get('horizons')) is list and r['horizons'] else 3 for r in requests]
        return {'comparisons':sum(counts)*4,'scan_cells':sum(stockdays*(2+n*(4+symbols)) for n in counts),
            'definition':'Conservative reservation for two expression matrices, four summaries and up to all other-symbol comparisons per horizon; actual retained sample is separately reported.'}

    @staticmethod
    def _batch_public(artifact):
        # Whole summary is paged when needed; never silently retain only winners.
        return {k:v for k,v in artifact.items() if k!='candidates'}|{
            'status_counts':dict(Counter(r['status'] for r in artifact['candidates'])),
            'all_candidate_results_query':'inspect_batch table=results candidate_id=null; follow next_offset to null'}

    def _run_batch(self,batch_id,*,execute_new):
        registration=self._prior(batch_id,'register_batch')
        from .runtime import source_pins
        deadline=time.time()+registration['policy']['max_wall_seconds']
        plan_path=self.root/'plan.json'
        if plan_path.exists():
            plan=json.loads(plan_path.read_text(encoding='utf-8'))
            deadline=min(deadline,plan['deadline_epoch']-plan['policy']['closing_seconds'])
        known=[]
        for row in self.history:
            if (row.get('response') or {}).get('action')=='develop_strategy' and (row.get('result') or {}).get('artifact_hash'):
                a=self._prior(row['id'],'develop_strategy');known.append((row['id'],a))
        with worker_lease(self.batch_folder):
            return research_batch.run(self.batch_folder/batch_id,registration,self.case,save_once,
                deadline_epoch=deadline,source_pins=source_pins(),execute_new=execute_new,ordinary_evidence=known)

    def recover_batch_call(self,call_id,args):
        folder=self.folder/call_id
        need(json.loads((folder/'request.json').read_text(encoding='utf-8'))=={'action':'execute_batch','arguments':args},'saved batch request changed')
        if (folder/'result.json').exists():return json.loads((folder/'result.json').read_text(encoding='utf-8'))
        if (folder/'artifact.json').exists():artifact=json.loads((folder/'artifact.json').read_text(encoding='utf-8'))
        else:
            artifact=self._run_batch(args['registration_evidence_id'],execute_new=False);save_once(folder/'artifact.json',artifact)
        result={'evidence_id':call_id,'action':'execute_batch','artifact_hash':digest(artifact),'public':self._batch_public(artifact),'execution_valid':False,'formal_target_success':False}
        save_once(folder/'result.json',result);return result

    def final(self, args):
        required={"outcome", "conclusion", "evidence_ids", "program_evidence_id", "limitations", "next_step", "falsifiers"}
        if self.report_policy is not None:
            required.add('claims')
        need(type(args) is dict and set(args) in (required,required|{'batch_candidate_id'}), "final exact fields")
        need(args["outcome"] in ("abstain", "strategy_for_development"), "final outcome invalid")
        for key, maximum in (("conclusion", 8000), ("next_step", 2000)):
            need(type(args[key]) is str and 0 < len(args[key]) <= maximum, "final text bound: " + key)
        for key in ("evidence_ids", "limitations", "falsifiers"):
            need(type(args[key]) is list and 1 <= len(args[key]) <= 16 and
                 all(type(x) is str and 0 < len(x) <= 2000 for x in args[key]), "final list bound: " + key)
        known = {x["id"]: x for x in self.history if x["result"] is not None}
        input_id = 'input:'+digest(self.case)
        need(all(x in known or (args['outcome']=='abstain' and x==input_id) for x in args["evidence_ids"]), "foreign or unknown final evidence")
        selected = args["program_evidence_id"]
        if args["outcome"] == "strategy_for_development":
            need(selected in args['evidence_ids'],'strategy must bind its evaluated program')
            if 'batch_candidate_id' in args:
                evidence=self._prior(selected,'execute_batch');batch_id=evidence['batch_id']
                need(any(r['candidate_id']==args['batch_candidate_id'] and r['status'] in ('completed','reused') for r in evidence['candidates']),'selected candidate not completed in cited batch evidence')
                registration=self._prior(batch_id,'register_batch')
                covered=set()
                for row in self.history:
                    if (row.get('response') or {}).get('action')!='inspect_batch' or not (row.get('result') or {}).get('artifact_hash'):continue
                    page=self._prior(row['id'],'inspect_batch')
                    if page.get('registration_evidence_id')==batch_id and page.get('batch_results_hash')==digest(evidence['candidates']):
                        public=row['result'].get('public',{})
                        if not public.get('delivery_blocked'):
                            covered.update(range(page['offset'],page['offset']+public.get('returned_rows',0)))
                need(covered==set(range(len(evidence['candidates']))),'all batch result rows must be read before selecting a candidate')
                value=self._batch_candidate(batch_id,registration,args['batch_candidate_id'])
            else:
                need(known[selected]['response']['action']=='develop_strategy','strategy must bind its evaluated program')
                value = self._prior(selected, "develop_strategy")
            need(value["raw"] is not None and value["raw"]["status"] == "completed_mechanical", "strategy has no completed funded execution")
            trades=value['raw']['result'].get('trades',[])
            need(any(t.get('receipt',{}).get('filled_quantity',t.get('quantity',0))>0 for t in trades),'strategy has no funded fills; keep all-cash/rejected evidence for abstention or comparison')
        else:
            need(selected is None and 'batch_candidate_id' not in args, "abstention has no selected strategy")
        result = {"model_report": args, "legal_submission": True, "research_class": self.case["research_class"],
                  "execution_valid": False, "formal_target_success": False, "target_achieved": False}
        if self.report_policy is not None:
            from .claim_support import review_claims
            cited = {}
            for eid in args['evidence_ids']:
                if eid == input_id:
                    cited[eid] = {'action': 'frozen_input', 'public': self.case}
                else:
                    row = known[eid]
                    # Auxiliary custody fields are controller-owned and always
                    # replaced, never inherited from a model or saved public dict.
                    cited[eid] = {k: v for k, v in row['result'].items()
                                  if k not in ('_registered_artifact', '_registered_arguments')}
                    if row['result'].get('artifact_hash'):
                        cited[eid]['_registered_artifact'] = self._prior(eid, row['response']['action'])
                        if row['response']['action'] == 'inspect_batch':
                            from .report_arguments import decode, VERSION
                            cited[eid]['_registered_arguments'] = decode(row['response'], {'version': VERSION})[0]
            result['claim_support'] = review_claims(args['claims'], cited,
                research_class=self.case['research_class'], report_text=args['conclusion'], frozen_case=self.case)
            result['substantive_report_accepted'] = None
            result['substantive_report_status'] = 'requires_evidence_review'
        return result
