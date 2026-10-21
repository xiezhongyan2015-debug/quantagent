"""Independent V10A source freeze and final package replay entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.factor_acceptance_v10.oracle import (
    audit_package, freeze_authority, freeze_oracle, freeze_registry, verify_oracle,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze-oracle", "verify", "freeze-source", "freeze-registry", "audit"))
    parser.add_argument("--authority", default="F:/V10A_Factor_Research/acceptance")
    parser.add_argument("--request", help="JSON file with fixed source request")
    parser.add_argument("--registry", help="JSON list of full executable FactorSpec definitions")
    parser.add_argument("--package")
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze-oracle":
            result = freeze_oracle(args.authority)
        elif args.command == "verify":
            result = verify_oracle(args.authority)
        elif args.command == "freeze-source":
            if not args.request:
                parser.error("--request is required")
            result = freeze_authority(args.authority, json.loads(Path(args.request).read_text(encoding="utf-8")))
        elif args.command == "freeze-registry":
            if not args.registry:
                parser.error("--registry is required")
            rows = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            definitions = [row.get("spec", row) for row in rows]
            result = freeze_registry(args.authority, definitions, source_path=args.registry)
        else:
            if not args.package:
                parser.error("--package is required")
            result = audit_package(args.package, args.authority)
        compact = {key: value for key, value in result.items() if key not in ("daily", "dates", "columns", "source_provenance", "source_file_pins", "definitions")}
        print(json.dumps(compact, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, FileNotFoundError, OSError, KeyError, TypeError) as exc:
        blocked = {"status": "blocked", "accepted": False, "reason": str(exc),
                   "action": args.command, "resumable": True, "evaluator_defect_must_not_relax_protocol": True}
        if args.command == "audit" and args.package and Path(args.package).is_dir():
            (Path(args.package) / "independent_acceptance_blocked.json").write_text(
                json.dumps(blocked, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(blocked, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
