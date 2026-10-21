"""Fresh V5 development research with explicit evidence and V4 execution limits."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.ledger import need
from quanta_agents.meta_v5.runtime import V5Runtime, create_stage


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "run", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--root", required=True)
        if name == "create":
            sub.add_argument("--spec", required=True, help="Controller JSON with tasks, contracts, bundle paths, explicit policies and identity route")
    args = parser.parse_args()
    if args.command == "create":
        spec = read(args.spec)
        need(set(spec) == {"tasks", "controller_policy", "stability_policy", "bundle_paths", "closing_policy", "duration_seconds", "identity_route"}, "exact V5 specification required")
        need(type(spec["duration_seconds"]) in (int, float) and 0 < spec["duration_seconds"] <= 86400, "bounded fresh development duration")
        runtime = create_stage(args.root, tasks=spec["tasks"], controller_policy=spec["controller_policy"],
            stability_policy=spec["stability_policy"], bundles=[read(p) for p in spec["bundle_paths"]],
            policy=ClosingPolicy(**spec["closing_policy"]), deadline_epoch=time.time()+spec["duration_seconds"],
            identity_route=spec["identity_route"])
        result = {"created": True, "root": str(runtime.root), "model_called": False,
                  "formal_target_success": False, "tasks": list(runtime.plan["tasks"])}
    elif args.command == "status":
        result = V5Runtime(args.root).inspect_work()
    else:
        runtime = V5Runtime(args.root)
        runtime.verify_inputs(global_only=True)
        # This entry deliberately cannot register or claim a fair-study slot.
        # Any future study integration needs an explicit cost-admission adapter.
        result = runtime.run()
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
