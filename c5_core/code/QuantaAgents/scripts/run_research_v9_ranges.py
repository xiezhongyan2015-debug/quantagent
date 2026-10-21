"""V9 range-research CLI. Keeps the older frozen method study independently usable."""
import argparse
import os
from pathlib import Path
import sys

# Process workers own numerical parallelism; avoid nested BLAS thread pools.
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quanta_agents.meta_v9.range_study import RangeStudy, default_range_config
from quanta_agents.meta_v9.study import read
from quanta_agents.research_kernel.store import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "prepare", "calibrate", "run", "status"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--legacy-root")
    parser.add_argument("--config")
    args = parser.parse_args()
    study = RangeStudy(args.root)
    if args.command == "init":
        if not args.config and not args.legacy_root:
            parser.error("init requires --legacy-root or --config")
        result = study.initialize_range(read(args.config) if args.config else default_range_config(args.legacy_root, args.root))
    elif args.command == "prepare":
        result = {"cache": str(study.prepare_cache())}
    elif args.command == "calibrate":
        result = study.calibrate(study.prepare_cache())
    elif args.command == "run":
        result = study.run()
        result = {k: v for k, v in result.items() if k not in ("candidate", "diagnostic", "review")}
    else:
        result = study.status()
    print(serial(result), flush=True)


if __name__ == "__main__":
    main()
