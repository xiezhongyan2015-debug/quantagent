"""Small standalone entry point; no account, strategy or provider dependency."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .protocol import default_protocol
from .study import FactorStudy, factor_spec, read, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description="V9A bounded, standalone single-factor research")
    parser.add_argument("command", choices=("init", "status", "calibrate", "develop", "compare", "revise", "freeze", "confirm", "report", "run"))
    parser.add_argument("--root", default="F:/V9A_Factor_Research_20260912/study")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--proposals")
    args = parser.parse_args(argv)
    study = FactorStudy(args.root)
    if args.command == "init":
        from .generation import build_builtin_proposals
        repo = Path(__file__).resolve().parents[3]
        inventory = read(repo / "output/research/meta_v9_20260909/library_inventory.json")
        rows = inventory["executable_definitions"]
        baseline = [factor_spec(next(r for r in rows if r["id"] == name)) for name in ("F2", "F3", "F7")]
        cfg = default_protocol(repo / "output/research/meta_v7_project")
        receipt = Path(args.root).resolve().parent / "receipts" / "team_receipts.json"
        result = study.initialize(cfg, build_builtin_proposals(), baseline,
            origin={"kind": "native_codex_subagent_saved_proposal", "receipt_path": str(receipt),
                    "receipt_sha256": sha(receipt) if receipt.exists() else None,
                    "model": "gpt-6-astra", "reasoning_effort": "xhigh",
                    "agent_path": "/root/generation_control", "no_gateway_call_per_parameter": True})
    elif args.command == "revise":
        from .generation import load_proposals
        if not args.proposals:
            parser.error("revise requires --proposals")
        result = study.revise(load_proposals(Path(args.proposals)), origin={"path": str(Path(args.proposals).resolve()),
            "sha256": sha(args.proposals), "model": "gpt-6-astra", "reasoning_effort": "xhigh",
            "agent_path": "/root/generation_control", "feedback_scope": "2016-2020_development_only"})
    elif args.command == "calibrate":
        result = study.develop(limit=args.limit)
    elif args.command == "report":
        from .report import summarize
        report = summarize(study)
        result = {"report": str(study.root / "REPORT.zh-CN.md"), "historical_target_met": report["historical_target_met"], "state": report["state"]}
    elif args.command == "run":
        # A single frozen proposal batch can run unattended; a second native
        # model structure revision is admitted explicitly before freeze.
        if study.state["phase"] == "development":
            study.develop(); study.compare(); study.freeze()
        if study.state["phase"] in ("frozen", "confirmation"):
            study.confirm()
        from .report import summarize
        summarize(study)
        result = study.status()
    else:
        result = getattr(study, args.command)()
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
