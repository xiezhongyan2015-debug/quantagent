"""V9 CLI: a bounded method study over existing library and account engines."""
import argparse
import json
from pathlib import Path

from .study import V9Study, default_config, read
from quanta_agents.research_kernel.store import serial


def main(argv=None):
    parser = argparse.ArgumentParser(description="QuantaAgents V9 factor combination research")
    parser.add_argument("command", choices=["init", "prepare", "run", "status"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--library-inventory")
    parser.add_argument("--data-config")
    parser.add_argument("--project-root")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    study = V9Study(args.root)
    if args.command == "init":
        if not all((args.library_inventory, args.data_config, args.project_root)):
            parser.error("init requires library inventory, existing data config and project root")
        cfg = read(args.config) if args.config else default_config(args.project_root)
        result = study.initialize(cfg, args.library_inventory, read(args.data_config)["data"])
    elif args.command == "prepare":
        result = study.prepare()
        result = {"status": result["status"], "selected_factors": result["selected_factors"],
                  "evidence": str(study.root / "early_fit.json")}
    elif args.command == "run":
        result = study.run()
    else:
        result = {**study.status(), "usage": study.usage()}
    print(serial(result), flush=True)
    return result


if __name__ == "__main__":
    main()
