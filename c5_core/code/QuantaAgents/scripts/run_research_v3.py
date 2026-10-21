"""Ordinary input -> bounded V3 researcher -> trusted tools -> model final."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.ledger import Ledger, digest
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins


def create_stage(args, *, policy, tasks, provenance):
    if args.controller_plan:
        from quanta_agents.meta_v3.controller_plan import validate, HARNESS_VERSION
        controller = json.loads(Path(args.controller_plan).read_text(encoding='utf-8-sig'))
        validate(controller, tasks)
        provenance = {**provenance, 'controller_policy': controller, 'harness_version': HARNESS_VERSION,
            'extension_handoff_policy': {'version': 'yield_to_controller_v1'},
            'extension_context_policy': {'version': 'parent_programs_v1'},
            'report_argument_policy': {'version': 'report_structured_args_v2'}}
    if args.identity_route:
        provenance = {**provenance, 'runtime_identity_route':
            json.loads(Path(args.identity_route).read_text(encoding='utf-8-sig'))}
    if args.study_id:
        from quanta_agents.meta_v3.study_registry import create_stage as registered
        return registered(args.root,args.study_id,args.trial_id,policy=policy,tasks=tasks,
            duration_seconds=3600*args.hours,provenance=provenance)
    return Ledger.create(args.root,policy=policy,tasks=tasks,
        deadline_epoch=time.time()+3600*args.hours,provenance=provenance)


def main(*, require_v4=False):
    parser = argparse.ArgumentParser(description=(
        'V4 research controller over the existing ledger and execution kernel; explicit work and evidence policies.'
        if require_v4 else __doc__))
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Freeze an ordinary-input stage; no model call")
    create.add_argument("--root", required=True)
    create.add_argument("--case", required=True)
    create.add_argument("--idea", required=True)
    create.add_argument("--document", action="append", default=[])
    create.add_argument("--task-id", default="research_001")
    create.add_argument("--hours", type=float, default=6)
    create.add_argument("--calls", type=int, default=8)
    batch = sub.add_parser("create-batch", help="Freeze ordinary inputs together under one shared stage budget")
    batch.add_argument("--root", required=True)
    batch.add_argument("--manifest", required=True)
    batch.add_argument("--hours", type=float, default=6)
    batch.add_argument("--calls", type=int, default=8)
    for command in (create,batch):
        command.add_argument('--controller-plan', help='Freeze V4 question/evidence/dependency/stop contracts; task-source failures stay local')
        command.add_argument('--identity-route', help='Frozen capture route: {version: codex_session_v1}, or a legacy demonstrated {root,call_id}; each completion is verified separately')
        command.add_argument("--study-id", help="Use a previously frozen canonical study")
        command.add_argument("--trial-id", help="Claim exactly one predeclared study slot")
    freeze = sub.add_parser('study-freeze', help='Register a controller-owned study plan; no model call')
    freeze.add_argument('--plan',required=True)
    study_status = sub.add_parser('study-status',help='Read shared study costs and reserved slots')
    study_status.add_argument('--study-id',required=True)
    measurements = sub.add_parser('reconcile-tool-measurements',help='Apply original saved measurements only; never execute a model or tool')
    measurements.add_argument('--root',required=True)
    measurements.add_argument('--call-id')
    for command in ("run", "status"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--root", required=True)
    args = parser.parse_args()
    if require_v4 and args.command in ('create', 'create-batch') and not args.controller_plan:
        parser.error('V4 requires --controller-plan with a work contract for each task')
    if args.command=='reconcile-tool-measurements':
        from quanta_agents.meta_v3.tool_measurements import reconcile, reconcile_available
        print(json.dumps(reconcile(args.root,args.call_id) if args.call_id else reconcile_available(args.root),ensure_ascii=True));return
    if args.command in ('create','create-batch') and bool(args.study_id)!=bool(args.trial_id):
        parser.error('--study-id and --trial-id must be supplied together')
    if args.command in ('study-freeze','study-status'):
        from quanta_agents.meta_v3 import study_registry
        result = (study_registry.freeze(json.loads(Path(args.plan).read_text(encoding='utf-8-sig')))
                  if args.command=='study-freeze' else study_registry.snapshot(args.study_id))
        print(json.dumps(result,ensure_ascii=True)); return
    if args.command == "create-batch":
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
        tasks = {}
        for row in manifest["tasks"]:
            case = json.loads(Path(row["case_path"]).read_text(encoding="utf-8-sig"))
            if require_v4:
                from quanta_agents.meta_v3.experiment_design import VERSION
                case = {**case, 'report_policy': {'version': 'claim_support_v1'},
                        'experiment_design_policy': {'version': VERSION}}
            tasks[row["id"]] = {"idea": row["idea"], "documents": row.get("documents", []), "case": case, "case_hash": digest(case)}
        policy = ClosingPolicy(task_calls=args.calls, stage_calls=args.calls * len(tasks), stage_tokens=600000 * len(tasks))
        job = create_stage(args, policy=policy, tasks=tasks,
            provenance={"source_pins": source_pins(), "old_v2_unknown_reserve": 80000, "old_v2_known_tokens": 491954,
                "old_campaign_resume_authorized": False, "preregistered_protocol": manifest["protocol"],
                "exposure": ",".join(sorted({t["case"]["research_class"] for t in tasks.values()})) + "; exposed development, not independent financial validation"})
        print(json.dumps({k: job.status(k) for k in tasks}, ensure_ascii=True))
    elif args.command == "create":
        case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
        if require_v4:
            from quanta_agents.meta_v3.experiment_design import VERSION
            case = {**case, 'report_policy': {'version': 'claim_support_v1'},
                    'experiment_design_policy': {'version': VERSION}}
        docs = [{"name": Path(x).name, "text": Path(x).read_text(encoding="utf-8-sig")} for x in args.document]
        policy = ClosingPolicy(task_calls=args.calls, stage_calls=args.calls, stage_tokens=600000)
        job = create_stage(args, policy=policy, tasks={args.task_id: {"idea": args.idea, "documents": docs,
            "case": case, "case_hash": digest(case)}},
            provenance={"source_pins": source_pins(), "old_v2_unknown_reserve": 80000,
                        "old_v2_known_tokens": 491954, "old_campaign_resume_authorized": False,
                        "exposure": case["research_class"] + "; exposed development, not independent financial validation"})
        print(json.dumps(job.status(args.task_id), ensure_ascii=True))
    elif args.command == "run":
        if require_v4:
            plan = json.loads((Path(args.root) / 'plan.json').read_text(encoding='utf-8'))
            if 'controller_policy' not in plan['provenance']:
                parser.error('V4 run requires a newly frozen V4 work plan; old scope migration is not automatic')
        from quanta_agents.meta_v3 import process_envelope
        if process_envelope.status(args.root)['integrated'] and not process_envelope.inside(args.root):
            receipt=process_envelope.supervise(args.root)
            print(json.dumps(receipt,ensure_ascii=True))
            if receipt['exit_code']!=0:raise SystemExit(1)
        else:
            print(json.dumps(ResearchRuntime(args.root).run(), ensure_ascii=True))
    else:
        runtime = ResearchRuntime(args.root)
        print(json.dumps(runtime.inspect_work() if runtime.controller_policy is not None else
            {k: runtime.ledger.status(k) for k in runtime.plan["tasks"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
