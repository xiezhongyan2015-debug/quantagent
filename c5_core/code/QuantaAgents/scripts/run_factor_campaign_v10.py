"""Account-free continuing research entry point."""
from pathlib import Path
import argparse
import json
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.factor_campaign.campaign import Campaign


def main():
    parser = argparse.ArgumentParser(description="V10A factor/combination campaign")
    parser.add_argument("command", choices=["init", "start", "prepare", "calibrate", "run", "resume", "status", "stop", "factor-batch", "combination-batch", "review", "advance"])
    parser.add_argument("--root", default="F:/V10A_Factor_Research")
    parser.add_argument("--source-root", default="/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    active_path = Path(args.root) / "active_framework.json"
    if active_path.exists() and args.command not in ("stop", "status"):
        active = json.loads(active_path.read_text(encoding="utf-8"))
        workspace = Path(active["workspace"]).resolve()
        current = Path(__file__).resolve().parents[1]
        if workspace != current:
            if not workspace.is_relative_to(Path(args.root).resolve()):
                raise ValueError("Active framework must remain in the campaign's isolated artifacts")
            os.execv(sys.executable, [sys.executable, str(workspace / "scripts/run_factor_campaign_v10.py"), *sys.argv[1:]])
    study = Campaign(args.root)
    if args.command == "init":
        result = study.initialize(args.source_root)
    elif args.command == "start":
        study.initialize(args.source_root)
        result = study.run()
    elif args.command == "prepare":
        result = study.prepare()
    elif args.command == "status":
        result = study.status()
    elif args.command == "stop":
        result = study.stop()
    elif args.command == "factor-batch":
        result = study.factor_batch(args.limit)
    elif args.command == "combination-batch":
        result = study.combination_batch(args.limit)
    else:
        result = getattr(study, args.command.replace("-", "_"))()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if result.get("phase") == "framework_activated_restart_required" and args.command in ("start", "run", "resume", "advance"):
        workspace = Path(result["active_workspace"])
        os.execv(sys.executable, [sys.executable, str(workspace / "scripts/run_factor_campaign_v10.py"), "resume", "--root", args.root])


if __name__ == "__main__":
    main()
