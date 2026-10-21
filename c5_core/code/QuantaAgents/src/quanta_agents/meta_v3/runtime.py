"""Public ordinary-input research loop; model selects every research action."""
import hashlib
import json
import os
from pathlib import Path
import time

from .kernel import FROZEN, ROOT, module
from .ledger import AdmissionBlocked, Ledger, digest, need, serial, worker_lease
from .research_tools import ResearchTools, save_once
from .context import history_context

TASK_INPUT_ERRORS = (ValueError, OSError, KeyError, TypeError)

def source_pins():
    # Pin reused libraries and the small new V3 package. No copied revisions.
    files = list(Path(__file__).parent.glob("*.py")) + list(FROZEN.glob("*.py")) + list((FROZEN / "meta").glob("*.py")) + [ROOT / "scripts/run_research_v3.py", ROOT / "scripts/run_research_v4.py", ROOT / "scripts/run_registered_producer_v3.py"]
    return {str(x.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(x.read_bytes()).hexdigest() for x in files}


def verify_case_sources(task):
    need(digest(task['case'])==task['case_hash'],'case identity changed')
    from .source_admission import preflight_case
    preflight_case(task['case'])
    from .conditional_risk_derivation import verify_case as verify_conditional_derivation
    verify_conditional_derivation(task['case'])
    if task['case'].get('execution_backend') == 'v3_structural_001':
        from .suspension_market import SuspensionMarket
        case = task['case']
        bindings = case['raw_source_bindings']
        SuspensionMarket(bindings['market_schedule'], case['decision_fixture']['codes'],
            case['decision_fixture']['calendar'], bindings.get('corporate_actions', [])).verify_sources()
    for proof in task['case'].get('evidence_sources',[]):
        need(hashlib.sha256(Path(proof['path']).read_bytes()).hexdigest()==proof['sha256'],'case evidence source drift')
    for artifact in task['case']['raw_source_bindings']['source_artifacts']:
        for file,pin in (('rows_file','rows_sha256'),('manifest_file','manifest_sha256')):
            need(hashlib.sha256((Path(artifact['root'])/artifact[file]).read_bytes()).hexdigest()==artifact[pin],'case source drift')


def action_schema(menu):
    text = lambda maximum: {"type": "string", "minLength": 1, "maxLength": maximum}
    return {"type": "object", "additionalProperties": False,
        "properties": {"action": {"type": "string", "enum": list(menu)},
            "arguments_json": text(64000), "public_summary": text(4000),
            "self_review": {"type": "object", "additionalProperties": False,
                "properties": {"assessment": text(2000), "uncertainty": text(2000),
                               "next_step": text(2000), "falsifier": text(2000)},
                "required": ["assessment", "uncertainty", "next_step", "falsifier"]}},
        "required": ["action", "arguments_json", "public_summary", "self_review"]}


def make_prompt(task, tools, history, menu, state, decision, policy):
    call_room = max(0, min(policy["task_calls"] - state["task_calls_used"],
                           policy["stage_calls"] - state["stage_calls_used"]))
    token_room = max(0, min(policy["task_tokens"] - state["task_exposure"],
                            policy["stage_tokens"] - state["stage_exposure"]))
    resources = {"snapshot_before_this_call": True, "state": state, "frozen_policy": policy,
        "call_slots_including_this_call_and_final": call_room,
        "exploration_call_slots_including_this_call": max(0, call_room - 1) if decision.mode == "explore" else 0,
        "nominal_token_room_after_final_reserve": max(0, token_room - policy["closing_reserve"]),
        "exploration_seconds_before_closing": max(0, state["deadline_epoch"] - state["now_epoch"] - policy["closing_seconds"]),
        "interpretation": "Stage room already protects unfinished siblings. Actual usage can reduce future room. These are admission limits, not a billing guarantee. Plan evidence, funded execution and report work within this remaining scope; unsupported ideas may be abandoned."}
    return serial({"role": "Autonomous A-share researcher; gpt-6-astra/xhigh",
        "instructions": [
            "Research the ordinary input using the public tools. Return exactly one allowed action and its arguments as a JSON string.",
            "You may revise hypotheses, compare limited branches, diagnose failures and stop. No mandatory hypothesis count or fixed answer.",
            "Prefer interpretable low-complexity rules when evidence supports them; complex combinations are optional. Intuition may rank anomalies whose mechanism is unknown. State unknown mechanisms instead of inventing explanations. Extra conditions need incremental controls, not merely removal of development losses.",
            "Count all searched variants, failed branches, chosen fields, horizons and universes. Today's user-specified examples are exposed development/calibration inputs, never new independent discoveries. A batch capability is available only when explicitly listed in the current public tool contract.",
            "Use evidence to develop and test a causal program where useful. Explicitly separate facts, hypotheses, contradictions and unknowns.",
            "Only controller-provided tools may execute; no shell, filesystem, browser, external tools or hidden answers.",
            "Every prior action for THIS task follows. Oversized response/result context is explicitly an incomplete hashed excerpt; originals remain preserved. Use evidence pages where permitted or state what remains unverified.",
            "When saved_result_recovery is present, status and original_application_result retain an application failure while result is the verified original producer output. This reuse does not certify financial validity, erase costs, or count as another experiment.",
            "The last nominal final opportunity is protected. close_only allows ONLY submit_research_report; invalid final or refusing to submit is a real failure.",
            "Your report must come from you and cite this task's saved evidence IDs. Legal abstention is allowed and is not a strategy success.",
            "The research class is stated in the tool contract. Synthetic calibration and tiny exposed real development are distinct. Neither proves real-cost net OOS Sharpe>1, capacity, cross-case stability or prospective results.",
            "Do not claim another action has executed until its tool result appears. Include concise public self-review, not private chain of thought."],
        "ordinary_input": task["idea"], "documents": task["documents"],
        "public_tool_contract": tools.contract(), "allowed_actions": list(menu),
        "mode": decision.mode, "mode_reasons": list(decision.reasons),
        "resources": resources, "own_complete_action_history": history_context(history)})


class ResearchRuntime:
    def __init__(self, root):
        self.ledger = Ledger(root)
        self.root = self.ledger.root
        self.plan = json.loads((self.root / "plan.json").read_text(encoding="utf-8"))
        self.gateway_module = module("meta.codex_gateway")
        self.controller_policy = self.plan['provenance'].get('controller_policy')
        if self.plan['provenance'].get('runtime_identity_route') == {'version': 'codex_session_v1'}:
            from . import codex_session_gateway
            self.gateway_module = codex_session_gateway
        self.source_checks = {}
        self.work_schedule = None
        self.identity_gate = None

    def verify_inputs(self, *, task_id=None, global_only=False):
        from .research_extension import verify_extension_stage, validate_context_policy
        from .report_arguments import validate_policy as validate_report_argument_policy
        from .saved_recovery import verify_saved_recovery
        verify_extension_stage(self.plan)
        verify_saved_recovery(self.plan,self.root)
        from .architecture_contract import validate_runtime
        validate_runtime(self.plan['provenance'], model=self.plan['model'], effort=self.plan['effort'])
        need(self.plan["provenance"]["source_pins"] == source_pins(), "research source drift; frozen scope cannot be resumed with changed source")
        if self.controller_policy is not None:
            from .controller_plan import validate
            validate(self.controller_policy, self.plan['tasks'])
        for key, validator in (('report_argument_policy', validate_report_argument_policy),
                               ('extension_context_policy', validate_context_policy)):
            policy = self.plan['provenance'].get(key)
            validator(policy)
            if policy is not None:
                need(self.controller_policy is not None, key + ' requires a V4 controller')
        if self.plan['provenance'].get('extension_handoff_policy') is not None:
            from .extension_handoff import validate_policy
            need(self.controller_policy is not None, 'extension handoff requires a V4 controller')
            validate_policy(self.plan['provenance']['extension_handoff_policy'])
        if global_only:
            return
        selected = self.plan['tasks'] if task_id is None else {task_id: self.plan['tasks'][task_id]}
        for key in selected:
            self.verify_task_inputs(key)

    def verify_task_inputs(self, task_id):
        task = self.plan['tasks'][task_id]
        verify_case_sources(task)
        from .research_iteration import action_limits
        from .research_tools import LIMITS
        action_limits(task['case'], LIMITS)
        permitted = task.get('permitted_actions')
        if permitted is not None:
            need(type(permitted) is list and 'submit_research_report' in permitted and
                 set(permitted) <= {'inspect_inputs', 'inspect_batch', 'inspect_execution',
                                   'diagnose_execution', 'read_evidence', 'submit_research_report'},
                 'saved recovery permits only evidence reads, attribution and final')
        imported = task.get("imported_history")
        if imported:
            need(digest(imported["rows"]) == imported["hash"], "imported history drift")
            parent = Ledger(imported["root"])
            need(parent.history(imported["task_id"]) == imported["rows"], "parent history drift")
            need(all(x["status"] in ("applied", "failed") for x in imported["rows"]), "cannot import unresolved history")
            for entry in imported["rows"]:
                call = parent.call(entry["id"])
                receipt = call["receipt"]
                verified = self.gateway_module.verify_saved_completion(Path(imported["root"]) / "calls" / entry["id"],
                    expected_prompt_hash=call["intent"]["prompt_hash"], expected_schema=call["intent"]["schema"],
                    expected_artifact_sha256=receipt["artifact_sha256"])
                need(verified["response"] == receipt["response"], "imported response drift")

    def _tools(self, task_id):
        task = self.plan["tasks"][task_id]
        imported = task.get("imported_history")
        return ResearchTools(self.root, task_id, task["case"], self._history(task_id), imported=imported,
                             permitted_actions=task.get('permitted_actions'))

    def _history(self, task_id):
        imported = self.plan["tasks"][task_id].get("imported_history")
        from .saved_tool_results import history_view
        return (imported["rows"] if imported else []) + history_view(self.root, task_id, self.ledger.history(task_id))

    def _arguments(self, response):
        return self._decoded_arguments(response)[0]

    def _decoded_arguments(self, response):
        from .report_arguments import decode
        policy = self.plan['provenance'].get('report_argument_policy')
        if policy is None:
            # Preserve the execution entry's original parser independently of
            # legacy child report binding, which used plain json.loads.
            args = self.gateway_module._strict_json(response['arguments_json'])
            need(type(args) is dict, 'arguments must decode to an object')
            return args, None
        return decode(response, policy)

    def _apply(self, call_id):
        call = self.ledger.call(call_id)
        tools = self._tools(call["task_id"])
        final = call["receipt"]["response"]["action"] == "submit_research_report"
        if call["status"] in ("applied", "failed"):
            return
        if self.controller_policy is not None:
            need(call['receipt'].get('model_verified') is True or
                 call['receipt'].get('runtime_identity', {}).get('verified') is True,
                 'V4 per-call runtime model/effort identity unverified; saved receipt required, no new call or tool execution')
        result_path = self.root / "calls" / call_id / "application.json"
        if call["status"] == "applying":
            # Recovery never executes a strategy, diagnostic or paid call.
            if not result_path.is_file() and not final:
                from .saved_tool_results import returned
                recovered = returned(self.root, call_id, call['task_id'])
                if recovered is not None:
                    save_once(result_path, {'model_response_hash':digest(call['receipt']['response']),
                        'result':recovered['result'],'failed':False,'saved_result_recovery':recovered['proof']})
            if not result_path.is_file() and call['receipt']['response']['action']=='execute_batch':
                response=call['receipt']['response']
                result=tools.recover_batch_call(call_id,self._arguments(response))
                save_once(result_path,{'model_response_hash':digest(response),'result':result,'failed':False})
            need(result_path.is_file(), "application interrupted without final receipt; saved evidence reconciliation required")
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            need(saved["model_response_hash"] == digest(call["receipt"]["response"]), "application response binding drift")
            result = saved["result"]
            if final and not saved["failed"]:
                args, proof = self._decoded_arguments(call["receipt"]["response"])
                need(saved.get('argument_decoding') == proof, 'saved report argument decoding proof drift')
                need(tools.final(args) == result, "saved final drift")
            elif final and 'argument_decoding' in saved:
                _, proof = self._decoded_arguments(call["receipt"]["response"])
                need(saved['argument_decoding'] == proof, 'saved failed report argument decoding proof drift')
            elif not saved["failed"]:
                from .saved_tool_results import verify_application_result
                if not verify_application_result(self.root, call_id, call['task_id'], result):
                    artifact = json.loads((tools.folder / call_id / "artifact.json").read_text(encoding="utf-8"))
                    need(digest(artifact) == result["artifact_hash"], "saved application artifact drift")
            self.ledger.finish_apply(call_id, result, failed=saved["failed"], final=final)
            return
        response = self.ledger.begin_apply(call_id)
        if response is None:
            return
        failed = False
        argument_proof = None
        try:
            need(time.time() < self.plan["deadline_epoch"], "model completed after frozen deadline")
            args, argument_proof = self._decoded_arguments(response)
            result = tools.final(args) if final else tools.execute(call_id, response["action"], args)
        except Exception as exc:
            # An existing child may contain an unresolved execution intent.
            # Never turn its I/O/unknown interruption into a free retry.
            child = tools.folder / call_id / "workbench"
            complete = None
            if child.exists() or response['action']=='execute_batch':
                from .saved_tool_results import returned
                try:
                    complete = returned(self.root, call_id, call['task_id'])
                except Exception as validation_error:
                    self.ledger.pause('unresolved tool application: '+str(exc)[:500]+
                                      '; saved result rejected: '+str(validation_error)[:500])
                    raise
            if response['action']=='execute_batch' and complete is None:
                # The durable applying call already blocks another model
                # reservation. Resume may reconcile batch evidence only.
                raise
            if child.exists() and complete is None:
                self.ledger.pause("unresolved tool application: " + str(exc)[:500])
                raise
            # An original returned receipt and every captured output resolve
            # execution uncertainty. Keep this application failure unchanged;
            # the separate history view can expose that saved result.
            failed = True
            result = {"error": type(exc).__name__ + ": " + str(exc)[:3000],
                      "execution_valid": False, "formal_target_success": False}
        saved = {"model_response_hash": digest(response), "result": result, "failed": failed}
        if argument_proof is not None:
            saved['argument_decoding'] = argument_proof
        save_once(result_path, saved)
        self.ledger.finish_apply(call_id, result, failed=failed, final=final)

    def write_status(self, *, persist=True):
        state = {"kind": "meta_framework_v3", "observed_at": time.time(),
            "tasks": {task: self.ledger.status(task) for task in self.plan["tasks"]},
            "research_goal_achieved": False,
            "worker_activity": "Use live process identity and operating-system lease; saved status is not liveness proof."}
        if self.controller_policy is not None:
            from .controller_plan import view, HARNESS_VERSION
            handoffs = {}
            if self.plan['provenance'].get('extension_handoff_policy') is not None:
                from .extension_handoff import inspect_handoff
                for task_id in state['tasks']:
                    try:
                        handoffs[task_id] = inspect_handoff(self.root, task_id)
                    except TASK_INPUT_ERRORS as exc:
                        handoffs[task_id] = {'status': 'invalid_extension_evidence',
                            'blocks_parent_dispatch': True, 'reason': str(exc)[:2000]}
                state['extension_handoffs'] = handoffs
            self.work_schedule = view(self.controller_policy, state['tasks'], self.source_checks, self.identity_gate, handoffs)
            state.update(harness_version=HARNESS_VERSION, work_schedule=self.work_schedule)
        if persist:
            path = self.root / "status.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(serial(state), encoding="utf-8")
            temporary.replace(path)
        return state

    def inspect_work(self):
        """Current read-only admission view. Does not recover, reserve or dispatch."""
        self.verify_inputs(global_only=True)
        for task_id in self.plan['tasks']:
            state = self.ledger.status(task_id)
            if state['terminal'] or state['mode'] in ('blocked', 'terminal_without_submission'):
                continue
            try:
                self.verify_task_inputs(task_id)
            except TASK_INPUT_ERRORS as exc:
                self.source_checks[task_id] = {'status': 'unavailable', 'reason': str(exc)[:2000]}
            else:
                self.source_checks[task_id] = {'status': 'available',
                    'meaning': 'Source identities verified; completeness and historical arrival remain separate.'}
        try:
            self.identity_gate = self.verify_identity_route()
        except (ValueError, OSError, KeyError) as exc:
            self.identity_gate = {'status': 'unavailable', 'reason': str(exc)[:2000]}
        return self.write_status(persist=False)

    def run(self):
        with worker_lease(self.root):
            import psutil
            process = psutil.Process(os.getpid())
            identity = {"pid": process.pid, "create_time": process.create_time(),
                        "cmdline": process.cmdline(), "stage": str(self.root), "observed_at": time.time()}
            path = self.root / "worker_identity.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(serial(identity), encoding="utf-8")
            temporary.replace(path)
            if self.controller_policy is not None:
                return self._run_scoped()
            self.verify_inputs()
            gateway = self.gateway_module.CodexGateway(timeout_seconds=3600)
            for task_id, task in self.plan["tasks"].items():
                while True:
                    self.verify_inputs()
                    from .tool_measurements import reconcile_available
                    reconcile_available(self.root)
                    state = self.ledger.status(task_id)
                    outstanding = [x for x in state["calls"] if x["status"] in ("pending", "unknown", "received", "applying")]
                    if outstanding:
                        for item in outstanding:
                            try:
                                self.ledger.receive_saved(item["id"], self.gateway_module.verify_saved_completion)
                                self._apply(item["id"])
                            except Exception:
                                self.write_status()
                                raise
                        continue
                    if state["terminal"]:
                        break
                    tools = self._tools(task_id)
                    history = self._history(task_id)
                    build = lambda menu, budget, decision: (make_prompt(task, tools, history, menu, budget, decision, self.plan["policy"]), action_schema(menu))
                    try:
                        intent = self.ledger.reserve(task_id, tools.menu(), build)
                    except AdmissionBlocked:
                        state = self.ledger.status(task_id)
                        self.write_status()
                        if state["terminal"]:
                            break
                        raise
                    self.write_status()
                    folder = self.root / "calls" / intent["intent_id"]
                    save_once(folder / "intent.json", intent)
                    try:
                        gateway.run(prompt=intent["prompt"], schema=intent["schema"], workdir=folder,
                            on_event=lambda event: None, cancelled=lambda: False)
                        self.ledger.receive_saved(intent["intent_id"], self.gateway_module.verify_saved_completion)
                    except Exception as exc:
                        self.ledger.unknown(intent["intent_id"], exc)
                        self.write_status()
                        raise
                    self._apply(intent["intent_id"])
                    self.write_status()
            return self.write_status()

    def verify_identity_route(self):
        from .identity_route import verify
        return verify(self.plan, self.gateway_module)

    def _scoped_prompt(self, task_id, tools, history, menu, budget, decision, handoff):
        from .architecture_contract import prompt_assistance
        assistance = prompt_assistance(self.plan['provenance'])
        task = self.plan['tasks'][task_id]
        prompt = json.loads(make_prompt(task, tools, history, menu, budget, decision, self.plan['policy']))
        if 'work_contract' in assistance:
            prompt['work_contract'] = self.controller_policy['work'][task_id]
        if self.plan['provenance'].get('extension_handoff_policy') is not None:
            prompt['controller_extension_followup'] = handoff
            prompt['extension_service_policy'] = self.plan['provenance'].get('extension_service_policy')
        if 'working_evidence_index' in assistance:
            from .context import bounded, working_evidence_index
            index = working_evidence_index(history, allowed_actions=menu, projection='omitted_only')
            if index['rows']:
                room = 262144 - len(serial(prompt).encode('utf-8')) - 64
                if room >= 1600:
                    prompt['working_evidence_index'] = bounded(index, min(12000, room))
                else:
                    prompt['working_evidence_index'] = {
                        'status': 'not_attached_insufficient_prompt_room',
                        'original_history_sha256': digest(history)}
        return serial(prompt), action_schema(menu)

    def _run_scoped(self):
        """Only task-source failures are local. Paid/unknown state remains shared.

        Called inside the existing OS worker lease. A bounded pass returns its
        real blocked state; it does not poll, reset budgets or revive old scopes.
        """
        gateway = None
        last_schedule = None
        while True:
            self.verify_inputs(global_only=True)
            from .tool_measurements import reconcile_available
            reconcile_available(self.root)
            states = {k: self.ledger.status(k) for k in self.plan['tasks']}
            outstanding = [x for x in next(iter(states.values()))['calls']
                           if x['status'] in ('pending', 'unknown', 'received', 'applying')]
            if outstanding:
                # A saved receipt is reconciled before any new task is admitted.
                # Missing sources on its owning task leave the shared gate shut.
                for item in outstanding:
                    try:
                        self.verify_inputs(task_id=item['task_id'])
                        self.ledger.receive_saved(item['id'], self.gateway_module.verify_saved_completion)
                        self._apply(item['id'])
                    except Exception:
                        self.write_status()
                        raise
                continue
            for task_id, state in states.items():
                if state['terminal'] or state['mode'] == 'blocked':
                    continue
                if state['mode'] == 'terminal_without_submission':
                    try:
                        self.ledger.reserve(task_id, ('submit_research_report',), None)
                    except AdmissionBlocked:
                        pass
                    states[task_id] = self.ledger.status(task_id)
                    continue
                try:
                    self.verify_task_inputs(task_id)
                except TASK_INPUT_ERRORS as exc:
                    self.source_checks[task_id] = {'status': 'unavailable',
                        'reason': type(exc).__name__ + ': ' + str(exc)[:2000]}
                else:
                    self.source_checks[task_id] = {'status': 'available',
                        'meaning': 'Existing case admission and file identity checks passed; historical completeness and arrival are not inferred.'}
            # Admission work can cross the deadline. Materialize terminal
            # states with the current ledger clock even when every source fails.
            for task_id in states:
                current = self.ledger.status(task_id)
                if not current['terminal'] and current['mode'] == 'terminal_without_submission':
                    try:
                        self.ledger.reserve(task_id, ('submit_research_report',), None)
                    except AdmissionBlocked:
                        pass
            snapshot = self.write_status()
            schedule = snapshot['work_schedule']
            schedule_hash = digest(schedule)
            if schedule_hash != last_schedule:
                with self.ledger.transaction() as db:
                    self.ledger.event(db, 'work_selection', schedule)
                last_schedule = schedule_hash
            task_id = schedule['next_task']
            if task_id is None:
                return snapshot
            if gateway is None:
                try:
                    self.identity_gate = self.verify_identity_route()
                except (ValueError, OSError, KeyError) as exc:
                    self.identity_gate = {'status': 'unavailable', 'reason': str(exc)[:2000]}
                    snapshot = self.write_status()
                    with self.ledger.transaction() as db:
                        self.ledger.event(db, 'work_selection', snapshot['work_schedule'])
                    return snapshot
            task = self.plan['tasks'][task_id]
            tools, history = self._tools(task_id), self._history(task_id)
            # reserve holds a write transaction while building the prompt.
            # Read the handoff under the existing worker lease before that
            # transaction; opening another ledger transaction would deadlock.
            handoff = None
            if self.plan['provenance'].get('extension_handoff_policy') is not None:
                from .extension_handoff import inspect_handoff
                handoff = inspect_handoff(self.root, task_id)
            def build(menu, budget, decision):
                return self._scoped_prompt(task_id, tools, history, menu, budget, decision, handoff)
            # A shared provenance failure must never be caught as a local one.
            self.verify_inputs(global_only=True)
            try:
                self.verify_task_inputs(task_id)
            except TASK_INPUT_ERRORS as exc:
                self.source_checks[task_id] = {'status': 'unavailable',
                    'reason': type(exc).__name__ + ': ' + str(exc)[:2000]}
                self.write_status()
                continue
            try:
                intent = self.ledger.reserve(task_id, tools.menu(), build)
            except AdmissionBlocked:
                state = self.ledger.status(task_id)
                self.write_status()
                if state['terminal']:
                    continue
                raise
            self.write_status()
            folder = self.root / 'calls' / intent['intent_id']
            save_once(folder / 'intent.json', intent)
            if gateway is None:
                gateway = self.gateway_module.CodexGateway(timeout_seconds=3600)
            try:
                gateway.run(prompt=intent['prompt'], schema=intent['schema'], workdir=folder,
                    on_event=lambda event: None, cancelled=lambda: False)
                self.ledger.receive_saved(intent['intent_id'], self.gateway_module.verify_saved_completion)
            except Exception as exc:
                self.ledger.unknown(intent['intent_id'], exc)
                self.write_status()
                raise
            self._apply(intent['intent_id'])
            self.write_status()
