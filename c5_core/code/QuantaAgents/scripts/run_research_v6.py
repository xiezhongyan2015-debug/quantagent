"""Execute explicit V6 research stages; every stage retains its own evidence."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quanta_agents.meta_v6.research import (
    DEFAULT_RUN, HYPOTHESIS_SCHEMA, call_researcher, hypothesis_prompt, initialize_run,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["initialize", "hypotheses", "factors", "_factors_worker",
                                           "combinations", "accounts", "_accounts_worker", "design_cycle",
                                           "temporal", "_temporal_worker", "cycle_accounts",
                                           "_cycle_accounts_worker", "cycle_temporal", "_cycle_temporal_worker",
                                           "expansion_hypotheses", "expansion_factors", "_expansion_factors_worker",
                                           "application_design", "recover_application_references", "application_accounts", "_application_accounts_worker",
                                           "application_temporal", "_application_temporal_worker"])
    parser.add_argument("--root", type=Path, default=DEFAULT_RUN)
    args = parser.parse_args()
    initialize_run(args.root)
    if args.action == "hypotheses":
        receipt = call_researcher(args.root, "01_hypotheses", hypothesis_prompt(), HYPOTHESIS_SCHEMA)
        print(json.dumps({"stage": "01_hypotheses", "usage": receipt.get("usage"),
                          "model": receipt.get("model"), "effort": receipt.get("effort"),
                          "factors": receipt["response"]["factors"]}, ensure_ascii=False))
    elif args.action == "_factors_worker":
        from quanta_agents.meta_v6.study import evaluate_factor_stage
        result = evaluate_factor_stage(args.root)
        print(json.dumps({"completed_factor_stage": True, "index_path": str(args.root / "factor_index.json")}, ensure_ascii=False))
    elif args.action == "combinations":
        from quanta_agents.meta_v6.portfolio_study import admit_combinations
        result = admit_combinations(args.root)
        print(json.dumps(result["response"], ensure_ascii=False))
    elif args.action == "design_cycle":
        from quanta_agents.meta_v6.cycles import design_combinations
        print(json.dumps(design_combinations(args.root), ensure_ascii=False))
    elif args.action == "expansion_hypotheses":
        from quanta_agents.meta_v6.expansion import propose_expansion
        print(json.dumps(propose_expansion(args.root), ensure_ascii=False))
    elif args.action == "application_design":
        from quanta_agents.meta_v6.information_application import propose_information_application
        print(json.dumps(propose_information_application(args.root), ensure_ascii=False))
    elif args.action == "recover_application_references":
        from quanta_agents.meta_v6.application_reference_recovery import recover_application_references
        result = recover_application_references(args.root)
        print(json.dumps({"status": result.get("status"), "new_model_calls": 0,
                          "recovered": True}, ensure_ascii=False))
    elif args.action in {"_application_accounts_worker", "_application_temporal_worker"}:
        from quanta_agents.meta_v6.application_execution import (
            evaluate_application_accounts, evaluate_application_temporal,
        )
        evaluate = (evaluate_application_accounts if args.action == "_application_accounts_worker"
                    else evaluate_application_temporal)
        result = evaluate(args.root)
        print(json.dumps({"status": result["status"], "cycle_id": result.get("cycle_id"),
                          "selected": result.get("selected"), "financial_success": False}, ensure_ascii=False))
    elif args.action == "_temporal_worker":
        from quanta_agents.meta_v6.temporal import evaluate_temporal
        print(json.dumps(evaluate_temporal(args.root), ensure_ascii=False))
    elif args.action == "_accounts_worker":
        from quanta_agents.meta_v6.portfolio_study import evaluate_accounts
        print(json.dumps(evaluate_accounts(args.root), ensure_ascii=False))
    elif args.action == "_cycle_accounts_worker":
        from quanta_agents.meta_v6.cycle_execution import evaluate_cycle_accounts
        print(json.dumps(evaluate_cycle_accounts(args.root), ensure_ascii=False))
    elif args.action == "_cycle_temporal_worker":
        from quanta_agents.meta_v6.cycle_execution import evaluate_cycle_temporal
        print(json.dumps(evaluate_cycle_temporal(args.root), ensure_ascii=False))
    elif args.action == "_expansion_factors_worker":
        from quanta_agents.meta_v6.expansion_factor_study import evaluate_expansion_factors
        result = evaluate_expansion_factors(args.root)
        print(json.dumps({"status": result["status"], "cycle_id": result["cycle_id"],
                          "factors": [{"factor_key": row["factor_key"], "status": row["status"]}
                                      for row in result["factors"]]}, ensure_ascii=False))
    elif args.action in {"factors", "accounts", "temporal", "cycle_accounts", "cycle_temporal", "expansion_factors",
                         "application_accounts", "application_temporal"}:
        from quanta_agents.meta_v6.jobs import run_job
        stage = {"factors": "02_factor_execution", "accounts": "04_account_execution",
                 "temporal": "05_temporal_execution", "cycle_accounts": "01_account_execution",
                 "cycle_temporal": "02_temporal_execution", "expansion_factors": "01_factor_execution",
                 "application_accounts": "01_account_execution", "application_temporal": "02_temporal_execution"}[args.action]
        if args.action.startswith("cycle_"):
            from quanta_agents.meta_v6.cycles import CYCLE
            job_dir = args.root / "cycles" / CYCLE / "jobs" / stage
        elif args.action == "expansion_factors":
            from quanta_agents.meta_v6.expansion import CYCLE
            job_dir = args.root / "cycles" / CYCLE / "jobs" / stage
        elif args.action.startswith("application_"):
            from quanta_agents.meta_v6.information_application import CYCLE
            job_dir = args.root / "cycles" / CYCLE / "jobs" / stage
        else:
            job_dir = args.root / "jobs" / stage
        if (job_dir / "job_result.json").exists():
            result = json.loads((job_dir / "job_result.json").read_text(encoding="utf-8"))
        else:
            result = run_job([sys.executable, str(Path(__file__).resolve()), "_" + args.action + "_worker",
                              "--root", str(args.root.resolve())], cwd=ROOT, output_dir=job_dir,
                             timeout_seconds=900, max_output_bytes=128 * 1024**2,
                             env={"PYTHONIOENCODING": "utf-8"})
        print(json.dumps(result, ensure_ascii=False))
        if result["status"] != "completed":
            raise SystemExit(1)
    else:
        print(str(args.root / "protocol.json"))


if __name__ == "__main__":
    main()
