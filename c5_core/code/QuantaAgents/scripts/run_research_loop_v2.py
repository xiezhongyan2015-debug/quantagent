from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from quanta_agents.input_loader_v2 import load_v2_request
from quanta_agents.v2_runtime import default_cn_daily_v2_profile, default_parquet_glob
from quanta_agents.workflow_v2 import run_research_loop_v2


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从一句话或一份研报运行研究循环V2")
    parser.add_argument("input_file", help="只含一句话或source_file的YAML")
    parser.add_argument("--experiment-id", default="", help="可选的实验编号")
    parser.add_argument("--max-candidates", type=int, default=None, help="正式候选上限")
    parser.add_argument(
        "--initial-universe",
        default="dataset_defined",
        choices=("dataset_defined", "csiall", "csi1000", "csi800", "csi500", "csi300"),
        help="基准使用的初始股票范围；研究过程仍可比较其他历史股票范围",
    )
    parser.add_argument("--parquet-glob", default="", help="本地日线parquet路径")
    parser.add_argument(
        "--data-manifest",
        default="",
        help="可选的资料说明JSON；未指定时会检查parquet目录中的manifest.json",
    )
    parser.add_argument("--skip-final", action="store_true", help="确认期通过后也不运行最终期")
    parser.add_argument(
        "--resume-from-result",
        default="",
        help="从已完成的V2基准结果继续，不重复计算基准开发期",
    )
    parser.add_argument(
        "--resume-probe-from",
        default="",
        help="继续未完成的相邻数值比较，复用已经算完的数值",
    )
    parser.add_argument(
        "--resume-selected-from-result",
        default="",
        help="从已完成运行的研究期入选版本继续，不重跑旧候选",
    )
    parser.add_argument(
        "--additional-candidates",
        type=int,
        default=None,
        help="从研究期入选版本继续时新增的候选数量，默认 4；0 表示不新增候选，直接检查确认期",
    )
    parser.add_argument(
        "--allow-stopped-selected-resume",
        action="store_true",
        help="明确允许从已停止、但入选记录和代码检查完整的结果继续",
    )
    parser.add_argument(
        "--recheck-after-rule-fix-from-result",
        default="",
        help=(
            "显式启用规则修复重跑：读取旧001基准与004已选代码，"
            "用当前检查修正后重新计算开发期，再从008继续"
        ),
    )
    return parser.parse_args()


def _apply_initial_universe(
    profile: dict[str, object],
    initial_universe: str,
) -> None:
    value = str(initial_universe).strip().lower()
    universe = profile.get("universe")
    if not isinstance(universe, dict):
        raise ValueError("运行设置缺少 universe")
    if value == "dataset_defined":
        return

    allowed = {"csiall", "csi1000", "csi800", "csi500", "csi300"}
    if value not in allowed:
        raise ValueError(f"不支持的初始股票范围: {initial_universe}")
    universe["description"] = (
        f"使用 {value} 的逐日历史成分；这是运行范围，不是用户条件"
    )
    universe["required_data"] = {
        "type": "named_pool",
        "value": value,
    }
    universe["experiment_entry"] = {
        "symbols": [],
        "asset": "中国股票",
        "description": f"股票由 {value} 的逐日历史成分决定",
        "type": "named_pool",
        "value": value,
    }


def _apply_data_manifest(
    profile: dict[str, object],
    parquet_glob: str,
    explicit_path: str = "",
) -> str | None:
    raw_path = explicit_path.strip()
    if raw_path:
        manifest_path = Path(raw_path).expanduser()
    else:
        parquet_path = Path(parquet_glob).expanduser()
        manifest_path = (
            parquet_path / "manifest.json"
            if parquet_path.is_dir()
            else parquet_path.parent / "manifest.json"
        )
        if not manifest_path.is_file():
            return None

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    fields = manifest.get("available_fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("资料说明缺少非空的 available_fields")
    normalized_fields = [str(field).strip() for field in fields if str(field).strip()]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(normalized_fields):
        missing = ", ".join(sorted(required.difference(normalized_fields)))
        raise ValueError(f"资料说明缺少运行所需字段: {missing}")
    data = profile.get("data")
    if not isinstance(data, dict):
        raise ValueError("运行设置缺少 data")
    data["available_fields"] = normalized_fields
    data["manifest_path"] = str(manifest_path.resolve())
    data["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    data["manifest_summary"] = {
        key: manifest.get(key)
        for key in (
            "source_root",
            "date_range",
            "actual_date_range",
            "stock_filter",
            "stock_count",
            "stock_file_count",
            "row_count",
        )
        if key in manifest
    }
    return str(manifest_path.resolve())


def main() -> int:
    _configure_utf8_output()
    args = _arguments()
    request = load_v2_request(args.input_file)
    profile = default_cn_daily_v2_profile()
    _apply_initial_universe(profile, args.initial_universe)
    if args.max_candidates is not None:
        if args.max_candidates < 1:
            raise ValueError("--max-candidates 必须大于0")
        profile["research_budget"]["max_formal_candidates"] = args.max_candidates

    parquet_glob = args.parquet_glob.strip() or default_parquet_glob(PROJECT_ROOT)
    _apply_data_manifest(profile, parquet_glob, args.data_manifest)
    os.environ["QUANTA_DATA_ENGINE"] = "parquet"
    os.environ["QUANTA_BACKTEST_DB_BACKEND"] = "parquet"
    os.environ["QUANTA_PARQUET_DATA_GLOB"] = parquet_glob
    os.environ["QUANTA_PARQUET_ROOT"] = parquet_glob

    result = run_research_loop_v2(
        request["source_text"],
        runtime_profile=profile,
        experiment_id=args.experiment_id.strip() or None,
        run_final_if_confirmed=not args.skip_final,
        resume_from_result=args.resume_from_result.strip() or None,
        resume_probe_from=args.resume_probe_from.strip() or None,
        resume_selected_from_result=(
            args.resume_selected_from_result.strip() or None
        ),
        recheck_after_rule_fix_from_result=(
            args.recheck_after_rule_fix_from_result.strip() or None
        ),
        additional_candidates=args.additional_candidates,
        allow_stopped_selected_resume=args.allow_stopped_selected_resume,
    )
    summary = {
        "experiment_id": result.get("experiment_id"),
        "selected_candidate_id": result.get("selected_candidate_id"),
        "confirmation_stable": result.get("confirmation_stable"),
        "final_test_report": result.get("final_test_report"),
        "observer_conclusion": result.get("observer_audit", {}).get("observer_conclusion"),
        "result_path": result.get("result_path"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
