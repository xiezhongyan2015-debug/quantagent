from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from quanta_agents.experiment_runner import _parse_experiment_spec, _spec_to_payload
from quanta_agents.research_diagnostics import build_diagnostic_request
from quanta_agents.state import (
    WorkflowState,
    init_state,
    period_starts_after,
    periods_overlap,
)
from quanta_agents.trace_logger import write_trace_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RETRYABLE_FINAL_TEST_ERROR = (
    "backtest_error: "
    "strategy_output.output_weights_df.datetime_column is required"
)


def _read_trace_payload(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"运行记录不是对象: {path}")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError(f"运行记录缺少 payload: {path}")
    return data, payload


def _latest(paths: list[Path], label: str) -> Path:
    if not paths:
        raise FileNotFoundError(f"没有找到{label}")
    return sorted(paths, key=lambda value: (value.stat().st_mtime_ns, str(value)))[-1]


def _literal_dict_from_code(code: str, variable_name: str) -> dict[str, object]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            targets = [node.target]
            value = node.value
        if value is None or not any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in targets
        ):
            continue
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _literal_params_from_code(code: str) -> dict[str, object]:
    return _literal_dict_from_code(code, "params")


def _cap_required_data(
    required_data: object,
    *,
    latest_allowed_date: str,
) -> list[dict[str, object]]:
    if not isinstance(required_data, list):
        return []
    capped: list[dict[str, object]] = []
    for item in required_data:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        time_range = copied.get("time_range")
        if isinstance(time_range, dict):
            normalized = dict(time_range)
            end = normalized.get("end")
            if isinstance(end, str) and end[:10] > latest_allowed_date:
                normalized["end"] = latest_allowed_date
            copied["time_range"] = normalized
        capped.append(copied)
    return capped


def _hide_final_dates(text: str) -> str:
    return (
        text.replace("2025-01-01至2025-12-31为最终回测期", "最终回测期在恢复研究中隐藏且不得读取")
        .replace("2025-01-01", "[最终时期已隐藏]")
        .replace("2025-12-31", "[最终时期已隐藏]")
    )


def _backtest_feedback_from_last_development_candidate(
    candidate_records: list[dict[str, object]],
) -> str:
    development_records = [
        record
        for record in candidate_records
        if str(record.get("period_kind", "")).strip() == "development"
    ]
    if not development_records:
        raise FileNotFoundError("旧实验没有已完成的开发期候选记录")

    result = development_records[-1].get("result", {})
    return str(result.get("summary", "")) if isinstance(result, dict) else ""


def _load_source_spec(source_yaml: Path) -> tuple[str, dict[str, object]]:
    raw = yaml.safe_load(source_yaml.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML 不是对象: {source_yaml}")
    parsed = _parse_experiment_spec(raw)
    if parsed is None:
        raise ValueError(f"YAML 缺少有效研究方案: {source_yaml}")
    return parsed.user_idea, _spec_to_payload(parsed)


def _trace_epoch_index(path: Path) -> int:
    for parent in (path, *path.parents):
        name = parent.name
        if not name.startswith("epoch_"):
            continue
        try:
            return int(name.removeprefix("epoch_"))
        except ValueError:
            continue
    return 0


def _candidate_with_strategy_params(
    candidate: dict[str, object],
    *,
    run_root: Path,
    trace_path: Path,
) -> dict[str, object]:
    restored = dict(candidate)
    if "strategy_params" in restored:
        return restored

    expected_sha256 = str(restored.get("strategy_code_sha256", "")).strip().lower()
    if not expected_sha256:
        return restored

    epoch_index = int(
        restored.get("epoch_index", _trace_epoch_index(trace_path))
    )
    code_paths = list(
        run_root.glob(f"epoch_{epoch_index:03d}/strategyagent/round_*/step_*.py")
    )
    for path in sorted(
        code_paths,
        key=lambda value: (value.stat().st_mtime_ns, value.name),
        reverse=True,
    ):
        code = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        actual_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256:
            continue
        restored["strategy_params"] = _literal_params_from_code(code)
        return restored

    candidate_id = str(restored.get("candidate_id", "")).strip()
    raise ValueError(
        f"候选 {candidate_id or '未知'} 的策略代码与 strategy_code_sha256 不一致，"
        "无法恢复该候选参数"
    )


def _resolve_trace_run_root(experiment_id: str) -> Path:
    experiment_root = PROJECT_ROOT / "experiment_traces" / experiment_id
    if not experiment_root.exists():
        raise FileNotFoundError(f"没有找到实验运行记录: {experiment_id}")
    return _latest(list(experiment_root.glob("epochs_*")), f"{experiment_id} 的实验轮数目录")


def _resumed_from_experiment(run_root: Path) -> str:
    paths = list(run_root.glob("epoch_*/resumeloader/restored_state/*.json"))
    if not paths:
        return ""
    _, payload = _read_trace_payload(_latest(paths, "恢复状态记录"))
    return str(payload.get("resumed_from", "")).strip()


def _trace_lineage(source_experiment_id: str) -> list[tuple[str, Path]]:
    newest_first: list[tuple[str, Path]] = []
    seen: set[str] = set()
    current_id = source_experiment_id.strip()
    while current_id:
        if current_id in seen:
            raise ValueError(f"实验恢复来源存在循环: {current_id}")
        seen.add(current_id)
        run_root = _resolve_trace_run_root(current_id)
        newest_first.append((current_id, run_root))
        current_id = _resumed_from_experiment(run_root)
    newest_first.reverse()
    return newest_first


def _completed_development_entries(
    lineage: list[tuple[str, Path]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for experiment_id, run_root in lineage:
        paths = sorted(
            run_root.glob("epoch_*/manageragent/candidate_record/*.json"),
            key=lambda value: (_trace_epoch_index(value), value.name),
        )
        for path in paths:
            _, payload = _read_trace_payload(path)
            candidate = payload.get("candidate")
            experiment = payload.get("experiment")
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("period_kind", "")).strip() != "development":
                continue
            result = candidate.get("result")
            if not isinstance(result, dict) or not result:
                continue
            candidate_id = str(candidate.get("candidate_id", "")).strip()
            if not candidate_id:
                continue
            ordered.append(
                {
                    "experiment_id": experiment_id,
                    "run_root": run_root,
                    "path": path,
                    "candidate": _candidate_with_strategy_params(
                        candidate,
                        run_root=run_root,
                        trace_path=path,
                    ),
                    "experiment": dict(experiment) if isinstance(experiment, dict) else {},
                    "manager_payload": dict(payload),
                }
            )

    positions: dict[str, int] = {}
    merged: list[dict[str, Any]] = []
    for entry in ordered:
        candidate = entry["candidate"]
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        if candidate_id in positions:
            merged[positions[candidate_id]] = entry
            continue
        positions[candidate_id] = len(merged)
        merged.append(entry)
    return merged


def _research_meta_for_candidate(
    entry: dict[str, Any],
) -> tuple[dict[str, object], Path]:
    candidate = entry["candidate"]
    epoch_index = int(candidate.get("epoch_index", _trace_epoch_index(entry["path"])))
    run_root: Path = entry["run_root"]
    paths = list(
        run_root.glob(
            f"epoch_{epoch_index:03d}/hypothesisagent/structured_output/*.json"
        )
    )
    if not paths:
        raise FileNotFoundError(
            f"候选 {candidate.get('candidate_id', '')} 缺少研究 Agent 输出"
        )

    candidate_hypothesis = str(candidate.get("hypothesis", "")).strip()
    matching: list[tuple[Path, dict[str, object]]] = []
    fallback: list[tuple[Path, dict[str, object]]] = []
    for path in paths:
        _, payload = _read_trace_payload(path)
        fallback.append((path, payload))
        if candidate_hypothesis and str(payload.get("hypothesis", "")).strip() == candidate_hypothesis:
            matching.append((path, payload))
    selected = matching or fallback
    selected_path, selected_payload = sorted(
        selected,
        key=lambda item: (item[0].stat().st_mtime_ns, item[0].name),
    )[-1]

    current_meta = dict(selected_payload)
    field_map = {
        "candidate_mode": "candidate_mode",
        "hypothesis": "hypothesis",
        "strategy_modification": "strategy_modification",
        "decision": "research_decision",
        "selected_cause": "selected_cause",
        "expected_results": "expected_results",
        "judgment_is_wrong_if": "judgment_is_wrong_if",
        "facts": "facts",
        "possible_causes": "possible_causes",
        "previous_change_review": "previous_change_review",
        "knowledge_record": "knowledge_record",
        "objective_assessment": "objective_assessment",
        "candidate_directions": "candidate_directions",
        "selected_direction_id": "selected_direction_id",
        "untested_plans": "untested_plans",
        "stop_eligibility": "stop_eligibility",
    }
    for meta_key, candidate_key in field_map.items():
        if candidate_key in candidate:
            current_meta[meta_key] = candidate[candidate_key]
    return current_meta, selected_path


def _strategy_artifacts_for_candidate(
    entry: dict[str, Any],
) -> tuple[str, dict[str, object], Path, Path]:
    candidate = entry["candidate"]
    epoch_index = int(candidate.get("epoch_index", _trace_epoch_index(entry["path"])))
    run_root: Path = entry["run_root"]
    code_paths = list(
        run_root.glob(f"epoch_{epoch_index:03d}/strategyagent/round_*/step_*.py")
    )
    if not code_paths:
        raise FileNotFoundError(
            f"候选 {candidate.get('candidate_id', '')} 缺少策略代码"
        )

    expected_sha256 = str(candidate.get("strategy_code_sha256", "")).strip()
    code_candidates: list[tuple[Path, str]] = []
    for path in code_paths:
        code = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if expected_sha256:
            actual_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
            if actual_sha256 != expected_sha256:
                continue
        code_candidates.append((path, code))
    if not code_candidates:
        raise ValueError(
            f"候选 {candidate.get('candidate_id', '')} 的策略代码与运行记录不一致"
        )
    code_path, strategy_code = sorted(
        code_candidates,
        key=lambda item: (item[0].stat().st_mtime_ns, item[0].name),
    )[-1]

    meta_paths = list(
        run_root.glob(
            f"epoch_{epoch_index:03d}/strategyagent/round_*/structured_output/*.json"
        )
    )
    strategy_meta_path = _latest(meta_paths, "上一版策略说明")
    _, strategy_meta_payload = _read_trace_payload(strategy_meta_path)
    strategy_meta = strategy_meta_payload.get("strategy_generation_meta", {})
    if not isinstance(strategy_meta, dict):
        strategy_meta = {}
    return strategy_code, dict(strategy_meta), code_path, strategy_meta_path


def _development_report_for_candidate(
    entry: dict[str, Any],
) -> tuple[dict[str, object], Path | None]:
    candidate = entry["candidate"]
    embedded = candidate.get("development_report")
    if isinstance(embedded, dict) and embedded:
        return dict(embedded), None

    epoch_index = int(candidate.get("epoch_index", _trace_epoch_index(entry["path"])))
    run_root: Path = entry["run_root"]
    path = _latest(
        list(
            run_root.glob(
                f"epoch_{epoch_index:03d}/strategytester/development_evaluation/*.json"
            )
        ),
        "开发期报告",
    )
    _, report = _read_trace_payload(path)
    return dict(report), path


def _diagnostic_records_for_lineage(
    lineage: list[tuple[str, Path]],
    *,
    source_experiment_id: str,
    source_cutoff_path: Path,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    positions: dict[str, int] = {}
    cutoff_epoch = _trace_epoch_index(source_cutoff_path)
    cutoff_mtime = source_cutoff_path.stat().st_mtime_ns
    for experiment_id, run_root in lineage:
        paths = sorted(
            run_root.glob("epoch_*/researchdiagnosticsagent/diagnostic_report/*.json"),
            key=lambda value: (_trace_epoch_index(value), value.name),
        )
        for path in paths:
            if experiment_id == source_experiment_id:
                path_epoch = _trace_epoch_index(path)
                if path_epoch > cutoff_epoch:
                    continue
                if path_epoch == cutoff_epoch and path.stat().st_mtime_ns > cutoff_mtime:
                    continue
            _, payload = _read_trace_payload(path)
            period_access = payload.get("period_access", {})
            if isinstance(period_access, dict) and period_access.get("final_period_read") is True:
                continue
            request_id = str(payload.get("request_id", "")).strip() or str(path)
            if request_id in positions:
                records[positions[request_id]] = dict(payload)
                continue
            positions[request_id] = len(records)
            records.append(dict(payload))
    return records


def _all_diagnostic_records_for_lineage(
    lineage: list[tuple[str, Path]],
) -> tuple[list[dict[str, object]], list[Path]]:
    records: list[dict[str, object]] = []
    record_paths: list[Path] = []
    positions: dict[str, int] = {}
    for _, run_root in lineage:
        paths = sorted(
            run_root.glob("epoch_*/researchdiagnosticsagent/diagnostic_report/*.json"),
            key=lambda value: (_trace_epoch_index(value), value.name),
        )
        for path in paths:
            _, payload = _read_trace_payload(path)
            period_access = payload.get("period_access", {})
            if isinstance(period_access, dict) and period_access.get("final_period_read") is True:
                continue
            request_id = str(payload.get("request_id", "")).strip() or str(path)
            if request_id in positions:
                position = positions[request_id]
                records[position] = dict(payload)
                record_paths[position] = path
                continue
            positions[request_id] = len(records)
            records.append(dict(payload))
            record_paths.append(path)
    return records, record_paths


def _latest_research_output(
    run_root: Path,
) -> tuple[dict[str, object], dict[str, object], Path]:
    paths = list(run_root.glob("epoch_*/hypothesisagent/structured_output/*.json"))
    if not paths:
        raise FileNotFoundError("来源实验没有研究 Agent 输出")
    path = sorted(
        paths,
        key=lambda value: (
            _trace_epoch_index(value),
            value.stat().st_mtime_ns,
            value.name,
        ),
    )[-1]
    wrapper, payload = _read_trace_payload(path)
    return wrapper, payload, path


def _safe_resume_spec(
    *,
    user_idea: str,
    spec: dict[str, object],
    source_experiment_id: str,
    new_experiment_id: str,
) -> dict[str, object]:
    safe_spec = dict(spec)
    for key in (
        "backtest_start",
        "backtest_end",
        "final_test_start",
        "final_test_end",
        "holdout_start",
        "holdout_end",
        "active_period_kind",
    ):
        safe_spec.pop(key, None)
    safe_spec.update(
        {
            "experiment_id": new_experiment_id,
            "resumed_from": source_experiment_id,
            "run_final_test": False,
            "allow_final_test": False,
            "max_diagnostic_rounds": max(
                4, int(safe_spec.get("max_diagnostic_rounds", 0) or 0)
            ),
            "min_formal_candidates_before_stop": max(
                2, int(safe_spec.get("min_formal_candidates_before_stop", 0) or 0)
            ),
            "user_idea": _hide_final_dates(user_idea),
            "scenario": (
                f"训练期：{safe_spec.get('train_start', '')}到{safe_spec.get('train_end', '')}；"
                f"开发期：{safe_spec.get('validate_start', '')}到{safe_spec.get('validate_end', '')}；"
                "最终测试时期未提供且禁止读取"
            ),
        }
    )
    return safe_spec


def _matching_final_test_paths(
    strategy_code_sha256: str,
) -> tuple[list[Path], list[Path]]:
    trace_root = PROJECT_ROOT / "experiment_traces"
    if not trace_root.exists():
        return [], []

    expected_sha256 = strategy_code_sha256.strip().lower()
    matched_completed: list[Path] = []
    completed_paths = trace_root.glob(
        "*/epochs_*/epoch_*/manageragent/candidate_record/*.json"
    )
    for path in sorted(completed_paths, key=str):
        try:
            _, payload = _read_trace_payload(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法检查最终测试记录: {path}: {exc}") from exc
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("period_kind", "")).strip() != "final_test":
            continue
        recorded_sha256 = str(candidate.get("strategy_code_sha256", "")).strip().lower()
        if recorded_sha256 and recorded_sha256 == expected_sha256:
            matched_completed.append(path)

    matched_started: list[Path] = []
    started_paths = trace_root.glob(
        "*/epochs_*/epoch_*/finaltestrunner/final_test_started/*.json"
    )
    for path in sorted(started_paths, key=str):
        try:
            _, payload = _read_trace_payload(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法检查最终测试启动记录: {path}: {exc}") from exc
        recorded_sha256 = str(
            payload.get("strategy_code_sha256", "")
        ).strip().lower()
        if recorded_sha256 and recorded_sha256 == expected_sha256:
            matched_started.append(path)
    return matched_completed, matched_started


def _existing_final_test_record(strategy_code_sha256: str) -> Path | None:
    """查找同一份冻结策略是否已经开始或完成最终测试。"""

    completed_paths, started_paths = _matching_final_test_paths(
        strategy_code_sha256
    )
    if completed_paths:
        return completed_paths[0]
    if started_paths:
        return started_paths[0]
    return None


def _path_is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _validate_failed_final_test_for_retry(
    *,
    failed_experiment_id: str,
    candidate_id: str,
    strategy_code_sha256: str,
    strategy_params: dict[str, object],
    final_period: dict[str, str],
) -> dict[str, object]:
    """确认旧尝试只有技术错误且从未产出最终测试结果。"""

    failed_id = failed_experiment_id.strip()
    if not failed_id:
        raise ValueError("technical_retry_of 不能为空")
    failed_root = PROJECT_ROOT / "experiment_traces" / failed_id
    if not failed_root.exists() or not failed_root.is_dir():
        raise FileNotFoundError(f"没有找到待重试的最终测试实验: {failed_id}")

    completed_paths, started_paths = _matching_final_test_paths(
        strategy_code_sha256
    )
    failed_completed = [
        path for path in completed_paths if _path_is_within(path, failed_root)
    ]
    failed_started = [
        path for path in started_paths if _path_is_within(path, failed_root)
    ]
    if len(completed_paths) != 1 or len(failed_completed) != 1:
        raise RuntimeError("该冻结策略不是只有一次失败的最终测试记录")
    if len(started_paths) != 1 or len(failed_started) != 1:
        raise RuntimeError("该冻结策略不是只有一次最终测试启动记录")

    completed_path = failed_completed[0]
    _, completed_payload = _read_trace_payload(completed_path)
    candidate = completed_payload.get("candidate")
    experiment = completed_payload.get("experiment")
    if not isinstance(candidate, dict) or not isinstance(experiment, dict):
        raise ValueError("失败记录缺少候选或实验信息")

    expected_period = {
        "start": str(final_period.get("start", ""))[:10],
        "end": str(final_period.get("end", ""))[:10],
    }
    candidate_period = candidate.get("seen_period")
    experiment_period = experiment.get("seen_period")
    for record_name, record, record_period in (
        ("候选", candidate, candidate_period),
        ("实验", experiment, experiment_period),
    ):
        if str(record.get("candidate_id", "")).strip() != candidate_id:
            raise ValueError(f"失败{record_name}记录的候选编号不一致")
        if str(record.get("period_kind", "")).strip() != "final_test":
            raise ValueError(f"失败{record_name}记录不是最终测试")
        if (
            str(record.get("strategy_code_sha256", "")).strip().lower()
            != strategy_code_sha256
        ):
            raise ValueError(f"失败{record_name}记录的代码摘要不一致")
        if record.get("result") != {}:
            raise RuntimeError(f"失败{record_name}记录已经包含测试结果，禁止重试")
        recorded_error = str(record.get("error", "")).strip()
        if not recorded_error:
            raise RuntimeError(f"失败{record_name}记录没有技术错误，禁止重试")
        if recorded_error != RETRYABLE_FINAL_TEST_ERROR:
            raise RuntimeError(
                f"失败{record_name}记录不是允许重试的时间列元数据错误"
            )
        if not isinstance(record_period, dict):
            raise ValueError(f"失败{record_name}记录缺少最终测试时期")
        normalized_period = {
            "start": str(record_period.get("start", ""))[:10],
            "end": str(record_period.get("end", ""))[:10],
        }
        if normalized_period != expected_period:
            raise ValueError(f"失败{record_name}记录的最终测试时期不一致")

    recorded_params = candidate.get("strategy_params")
    if not isinstance(recorded_params, dict) or recorded_params != strategy_params:
        raise ValueError("失败候选记录的冻结参数不一致")
    if int(completed_payload.get("final_test_count", 0)) != 1:
        raise RuntimeError("失败记录的最终测试次数不是1")

    started_path = failed_started[0]
    _, started_payload = _read_trace_payload(started_path)
    if str(started_payload.get("candidate_id", "")).strip() != candidate_id:
        raise ValueError("启动记录的候选编号不一致")
    if (
        str(started_payload.get("strategy_code_sha256", "")).strip().lower()
        != strategy_code_sha256
    ):
        raise ValueError("启动记录的代码摘要不一致")
    started_params = started_payload.get("strategy_params")
    if not isinstance(started_params, dict) or started_params != strategy_params:
        raise ValueError("启动记录的冻结参数不一致")
    started_period = started_payload.get("final_test_period")
    if not isinstance(started_period, dict):
        raise ValueError("启动记录缺少最终测试时期")
    normalized_started_period = {
        "start": str(started_period.get("start", ""))[:10],
        "end": str(started_period.get("end", ""))[:10],
    }
    if normalized_started_period != expected_period:
        raise ValueError("启动记录的最终测试时期不一致")
    if int(started_payload.get("final_test_count_before_start", -1)) != 0:
        raise RuntimeError("启动记录显示此前已经执行过最终测试")

    result_files = list(
        failed_root.glob(
            "epochs_*/epoch_*/strategytester/backtest_result/*.json"
        )
    )
    performance_files = list(
        failed_root.glob(
            "epochs_*/epoch_*/strategytester/backtest_outputs/final_test/performance_summary.json"
        )
    )
    if result_files or performance_files:
        raise RuntimeError("旧尝试已经生成最终测试结果文件，禁止重试")

    return {
        "failed_experiment_id": failed_id,
        "failed_record": str(completed_path),
        "failed_start_record": str(started_path),
        "error": str(candidate.get("error", "")).strip(),
    }


def restore_frozen_candidate_for_final_test(
    *,
    source_experiment_id: str,
    source_yaml: str | Path,
    expected_candidate_id: str,
    expected_code_sha256: str,
    new_experiment_id: str,
    max_epochs: int = 15,
    technical_retry_of: str | None = None,
) -> WorkflowState:
    """恢复已通过开发期的冻结候选，只允许执行一次最终测试。"""

    source_id = source_experiment_id.strip()
    candidate_id = expected_candidate_id.strip()
    expected_sha256 = expected_code_sha256.strip().lower()
    final_experiment_id = new_experiment_id.strip()
    if not source_id:
        raise ValueError("source_experiment_id 不能为空")
    if not candidate_id:
        raise ValueError("expected_candidate_id 不能为空")
    if (
        len(expected_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha256)
    ):
        raise ValueError("expected_code_sha256 必须是64位十六进制摘要")
    if not final_experiment_id:
        raise ValueError("new_experiment_id 不能为空")
    if final_experiment_id == source_id:
        raise ValueError("最终测试实验ID不能与来源实验相同")
    if max_epochs < 1:
        raise ValueError("max_epochs 必须大于0")

    final_experiment_root = PROJECT_ROOT / "experiment_traces" / final_experiment_id
    if final_experiment_root.exists():
        raise FileExistsError(
            f"最终测试实验目录已经存在，拒绝覆盖: {final_experiment_root}"
        )

    lineage = _trace_lineage(source_id)
    entries = _completed_development_entries(lineage)
    source_entries = [
        entry for entry in entries if entry["experiment_id"] == source_id
    ]
    if not source_entries:
        raise FileNotFoundError("来源实验没有已完成的开发期候选")

    selected_entry = source_entries[-1]
    candidate = selected_entry["candidate"]
    selected_candidate_id = str(candidate.get("candidate_id", "")).strip()
    if selected_candidate_id != candidate_id:
        raise FileNotFoundError(
            f"来源实验最新完成候选是 {selected_candidate_id or '未知'}，"
            f"不是指定的 {candidate_id}"
        )

    development_result = candidate.get("result")
    if (
        not isinstance(development_result, dict)
        or development_result.get("passed") is not True
    ):
        raise ValueError(f"候选 {candidate_id} 没有通过开发期验收")

    recorded_sha256 = str(candidate.get("strategy_code_sha256", "")).strip().lower()
    if not recorded_sha256:
        raise ValueError(f"候选 {candidate_id} 缺少策略代码摘要")
    if recorded_sha256 != expected_sha256:
        raise ValueError(
            f"候选 {candidate_id} 的策略代码摘要与明确指定值不一致"
        )

    strategy_code, strategy_meta, code_path, strategy_meta_path = (
        _strategy_artifacts_for_candidate(selected_entry)
    )
    actual_sha256 = hashlib.sha256(strategy_code.encode("utf-8")).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f"候选 {candidate_id} 的策略代码文件摘要不一致")

    frozen_params = candidate.get("strategy_params")
    if not isinstance(frozen_params, dict) or not frozen_params:
        raise ValueError(f"候选 {candidate_id} 缺少冻结参数")
    code_params = _literal_params_from_code(strategy_code)
    if not code_params or code_params != frozen_params:
        raise ValueError(f"候选 {candidate_id} 的代码参数与候选记录不一致")
    frozen_strategy_output = _literal_dict_from_code(
        strategy_code, "strategy_output"
    )
    output_weights_meta = frozen_strategy_output.get("output_weights_df")
    if not isinstance(output_weights_meta, dict) or not output_weights_meta:
        raise ValueError(f"候选 {candidate_id} 的代码缺少 strategy_output 输出说明")
    datetime_column = output_weights_meta.get("datetime_column")
    if not isinstance(datetime_column, str) or not datetime_column.strip():
        raise ValueError(f"候选 {candidate_id} 的代码缺少输出时间列说明")

    source_yaml_path = Path(source_yaml)
    if not source_yaml_path.is_absolute():
        source_yaml_path = PROJECT_ROOT / source_yaml_path
    user_idea, source_spec = _load_source_spec(source_yaml_path)
    final_start = str(source_spec.get("backtest_start", "")).strip()
    final_end = str(source_spec.get("backtest_end", "")).strip()
    if not final_start or not final_end:
        raise ValueError("YAML 缺少明确的最终测试日期")

    development_report, development_report_path = _development_report_for_candidate(
        selected_entry
    )
    development_period = development_report.get("development_period", {})
    if not isinstance(development_period, dict) or not development_period:
        development_period = candidate.get("seen_period", {})
    if not isinstance(development_period, dict):
        raise ValueError("冻结候选缺少开发期日期")
    normalized_development_period = {
        "start": str(development_period.get("start", ""))[:10],
        "end": str(development_period.get("end", ""))[:10],
    }
    final_period = {"start": final_start[:10], "end": final_end[:10]}
    if (
        not normalized_development_period["start"]
        or not normalized_development_period["end"]
        or periods_overlap(normalized_development_period, final_period)
        or not period_starts_after(normalized_development_period, final_period)
    ):
        raise ValueError("开发期与最终测试期重叠、倒置或日期无效")

    retry_source_id = (
        technical_retry_of.strip()
        if isinstance(technical_retry_of, str) and technical_retry_of.strip()
        else ""
    )
    retry_info: dict[str, object] = {}
    if retry_source_id:
        retry_info = _validate_failed_final_test_for_retry(
            failed_experiment_id=retry_source_id,
            candidate_id=candidate_id,
            strategy_code_sha256=expected_sha256,
            strategy_params=dict(frozen_params),
            final_period=final_period,
        )
    else:
        previous_final_path = _existing_final_test_record(expected_sha256)
        if previous_final_path is not None:
            raise RuntimeError(
                f"候选 {candidate_id} 已经执行过最终测试，记录位于: {previous_final_path}"
            )

    research_meta, research_meta_path = _research_meta_for_candidate(selected_entry)
    required_data = strategy_meta.get("required_data")
    if not isinstance(required_data, list) or not required_data:
        required_data = research_meta.get("required_data")
    if not isinstance(required_data, list) or not required_data:
        raise ValueError(f"候选 {candidate_id} 缺少运行所需数据说明")
    backtest_datasets = strategy_meta.get("backtest_datasets")
    if not isinstance(backtest_datasets, list) or not backtest_datasets:
        backtest_datasets = research_meta.get("backtest_datasets", [])

    spec = dict(source_spec)
    spec.update(
        {
            "experiment_id": final_experiment_id,
            "resumed_from": source_id,
            "source_file": source_yaml_path.name,
            "final_test_start": final_period["start"],
            "final_test_end": final_period["end"],
            "backtest_start": final_period["start"],
            "backtest_end": final_period["end"],
            "active_period_kind": "final_test",
            "run_final_test": True,
            "allow_final_test": True,
            "frozen_candidate_id": candidate_id,
            "frozen_strategy_code_sha256": expected_sha256,
            "final_test_attempt": 2 if retry_source_id else 1,
            "technical_retry_of": retry_source_id,
        }
    )

    candidate_epoch = int(
        candidate.get("epoch_index", _trace_epoch_index(selected_entry["path"]))
    )
    if candidate_epoch < 1 or max_epochs < candidate_epoch:
        raise ValueError(
            f"max_epochs={max_epochs} 小于冻结候选所在轮次 {candidate_epoch}"
        )

    candidate_records = [dict(entry["candidate"]) for entry in entries]
    experiment_records = [
        dict(entry["experiment"])
        for entry in entries
        if isinstance(entry.get("experiment"), dict) and entry["experiment"]
    ]
    manager_payload = selected_entry["manager_payload"]
    if int(manager_payload.get("final_test_count", 0)) != 0:
        raise RuntimeError(f"候选 {candidate_id} 已有最终测试计数")

    state = init_state(
        user_idea=user_idea,
        max_epochs=max_epochs,
        experiment_spec=spec,
    )
    state["epoch_index"] = candidate_epoch
    state["current_candidate_id"] = candidate_id
    state["phase"] = "final_test"
    state["evaluation_stage"] = "final_test"
    state["research_stage"] = "iteration"
    state["hypothesis_generation_meta"] = dict(research_meta)
    state["strategy_generation_meta"] = dict(strategy_meta)
    state["strategy_code"] = strategy_code
    state["code_text"] = strategy_code
    state["strategy_result"] = {
        "params": dict(frozen_params),
        "strategy_output": dict(frozen_strategy_output),
        "required_data": [
            dict(item) for item in required_data if isinstance(item, dict)
        ],
        "backtest_datasets": list(backtest_datasets),
    }
    if not state["strategy_result"]["required_data"]:
        raise ValueError(f"候选 {candidate_id} 的运行所需数据说明无效")
    state["development_report"] = dict(development_report)
    state["development_period"] = normalized_development_period
    state["final_test_period"] = final_period
    state["candidate_records"] = candidate_records
    state["experiment_records"] = experiment_records
    state["research_trial_count"] = max(
        len(candidate_records),
        int(manager_payload.get("research_trial_count", len(candidate_records))),
    )
    state["technical_retry_count"] = int(
        manager_payload.get("technical_retry_count", 0)
    )
    if retry_source_id:
        state["technical_retry_count"] = max(
            int(state["technical_retry_count"]), 1
        )
    state["final_test_count"] = 0
    state["test_result"] = None
    state["final_test_result"] = None
    state["quality_passed"] = True
    state["final_quality_passed"] = False
    state["ready_for_final"] = True
    state["candidate_frozen"] = True
    state["awaiting_final_test_approval"] = False
    state["strategy_validate_round"] = max(
        int(candidate.get("strategy_validate_round", 1)), 1
    )
    state["validation_result"] = {}
    state["validation_summary"] = {}
    state["validation_feedback"] = {}
    state["backtest_feedback"] = ""
    state["last_test_error"] = ""
    state["manager_notes"] = (
        f"已恢复冻结候选 {candidate_id}，"
        + (
            "只允许执行一次技术重试。"
            if retry_source_id
            else "只允许执行一次最终测试。"
        )
    )
    state["history"] = [
        f"从 {source_id} 恢复冻结候选 {candidate_id}",
        (
            f"已核对 {retry_source_id} 只有技术错误；下一步只执行一次技术重试"
            if retry_source_id
            else "冻结代码与参数检查通过；下一步只执行最终测试"
        ),
    ]
    state["seen_periods"] = [
        {
            "period_kind": "development",
            "seen_period": dict(normalized_development_period),
        }
    ]

    write_trace_json(
        state,
        agent_name="ResumeLoader",
        stage="final_test_ready",
        payload={
            "resumed_from": source_id,
            "new_experiment_id": final_experiment_id,
            "candidate_id": candidate_id,
            "epoch_index": candidate_epoch,
            "strategy_code_sha256": expected_sha256,
            "strategy_params": dict(frozen_params),
            "strategy_output": dict(frozen_strategy_output),
            "development_period": dict(normalized_development_period),
            "final_test_period": dict(final_period),
            "final_test_count": 0,
            "final_test_attempt": 2 if retry_source_id else 1,
            "technical_retry_of": retry_source_id,
            "technical_retry_validation": dict(retry_info),
            "strategy_code_source": str(code_path),
            "strategy_meta_source": str(strategy_meta_path),
            "research_meta_source": str(research_meta_path),
            "development_report_source": str(
                development_report_path or selected_entry["path"]
            ),
        },
    )
    return state


def restore_research_state_from_trace(
    *,
    source_experiment_id: str,
    source_yaml: str | Path,
    new_experiment_id: str | None = None,
    max_epochs: int = 3,
) -> WorkflowState:
    source_root = PROJECT_ROOT / "experiment_traces" / source_experiment_id
    run_root = source_root / f"epochs_{max_epochs}"
    if not run_root.exists():
        candidates = sorted(source_root.glob("epochs_*"))
        run_root = _latest(candidates, "实验轮数目录")

    source_yaml_path = Path(source_yaml)
    if not source_yaml_path.is_absolute():
        source_yaml_path = PROJECT_ROOT / source_yaml_path
    user_idea, spec = _load_source_spec(source_yaml_path)

    entries = _completed_development_entries(
        [(source_experiment_id, run_root)]
    )
    candidate_records = [dict(entry["candidate"]) for entry in entries]
    experiment_records = [
        dict(entry["experiment"])
        for entry in entries
        if isinstance(entry.get("experiment"), dict) and entry["experiment"]
    ]
    last_manager_payload = (
        dict(entries[-1]["manager_payload"])
        if entries
        else {}
    )
    development_backtest_feedback = _backtest_feedback_from_last_development_candidate(
        candidate_records
    )

    hypothesis_path = _latest(
        list(run_root.glob("epoch_*/hypothesisagent/structured_output/*.json")),
        "研究 Agent 输出",
    )
    hypothesis_wrapper, hypothesis_payload = _read_trace_payload(hypothesis_path)
    decision = str(hypothesis_payload.get("decision", "")).strip()
    if decision != "diagnose_only":
        raise ValueError("旧实验的最后研究决定不是 diagnose_only，不能用本恢复入口")

    development_path = _latest(
        list(run_root.glob("epoch_*/strategytester/development_evaluation/*.json")),
        "开发期报告",
    )
    _, development_report = _read_trace_payload(development_path)
    code_path = _latest(
        list(run_root.glob("epoch_*/strategyagent/round_*/step_*.py")),
        "上一版策略代码",
    )
    strategy_code = code_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    strategy_meta_path = _latest(
        list(run_root.glob("epoch_*/strategyagent/round_*/structured_output/*.json")),
        "上一版策略说明",
    )
    _, strategy_meta_payload = _read_trace_payload(strategy_meta_path)
    strategy_meta = strategy_meta_payload.get("strategy_generation_meta", {})
    if not isinstance(strategy_meta, dict):
        strategy_meta = {}

    epoch_index = int(hypothesis_wrapper.get("epoch_index", 2))
    development_period = development_report.get("development_period", {})
    if not isinstance(development_period, dict):
        raise ValueError("旧实验开发期报告缺少日期")
    development_end = str(development_period.get("end", ""))[:10]
    if not development_end:
        raise ValueError("旧实验开发期结束日期为空")

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    effective_new_id = (
        new_experiment_id.strip()
        if isinstance(new_experiment_id, str) and new_experiment_id.strip()
        else f"{source_experiment_id}_resume_epoch{epoch_index}_{timestamp}"
    )
    for key in (
        "backtest_start",
        "backtest_end",
        "final_test_start",
        "final_test_end",
        "holdout_start",
        "holdout_end",
        "active_period_kind",
    ):
        spec.pop(key, None)
    spec.update(
        {
            "experiment_id": effective_new_id,
            "resumed_from": source_experiment_id,
            "run_final_test": False,
            "allow_final_test": False,
            "max_diagnostic_rounds": max(
                4, int(spec.get("max_diagnostic_rounds", 0) or 0)
            ),
            "min_formal_candidates_before_stop": max(
                2, int(spec.get("min_formal_candidates_before_stop", 0) or 0)
            ),
            "user_idea": _hide_final_dates(user_idea),
            "scenario": (
                f"训练期：{spec.get('train_start', '')}到{spec.get('train_end', '')}；"
                f"开发期：{spec.get('validate_start', '')}到{spec.get('validate_end', '')}；"
                "最终测试时期未提供且禁止读取"
            ),
        }
    )

    state = init_state(
        user_idea=str(spec["user_idea"]),
        max_epochs=max_epochs,
        experiment_spec=spec,
    )
    current_meta = dict(hypothesis_payload)
    current_meta["required_data"] = _cap_required_data(
        current_meta.get("required_data", []),
        latest_allowed_date=development_end,
    )
    current_meta["hypothesis"] = _hide_final_dates(
        str(current_meta.get("hypothesis", ""))
    )

    params = _literal_params_from_code(strategy_code)
    state["epoch_index"] = epoch_index
    state["current_candidate_id"] = f"candidate_{epoch_index:03d}"
    state["phase"] = "diagnostics"
    state["research_stage"] = "iteration"
    state["hypothesis_generation_meta"] = current_meta
    state["strategy_generation_meta"] = dict(strategy_meta)
    state["strategy_code"] = strategy_code
    state["code_text"] = strategy_code
    state["strategy_result"] = {
        "params": params,
        "required_data": current_meta.get("required_data", []),
        "backtest_datasets": current_meta.get("backtest_datasets", []),
    }
    state["development_report"] = dict(development_report)
    state["development_period"] = {
        "start": str(development_period.get("start", ""))[:10],
        "end": development_end,
    }
    state["final_test_period"] = {}
    state["candidate_records"] = candidate_records
    state["experiment_records"] = experiment_records
    state["research_trial_count"] = int(last_manager_payload.get("research_trial_count", len(candidate_records)))
    state["technical_retry_count"] = int(last_manager_payload.get("technical_retry_count", 0))
    state["final_test_count"] = 0
    state["test_result"] = None
    state["final_test_result"] = None
    state["quality_passed"] = False
    state["final_quality_passed"] = False
    state["ready_for_final"] = False
    state["candidate_frozen"] = False
    state["awaiting_final_test_approval"] = False
    state["diagnostic_round"] = 0
    state["diagnostic_report"] = {}
    state["diagnostic_records"] = []
    state["diagnostic_request"] = build_diagnostic_request(
        state,
        current_meta.get("requested_diagnostics", []),
    )
    state["backtest_feedback"] = development_backtest_feedback
    state["manager_notes"] = (
        f"Restored epoch {epoch_index} from {source_experiment_id}; fixed diagnostics run first."
    )
    state["history"] = [
        f"Restored candidate history from {source_experiment_id}",
        f"Epoch {epoch_index}: resumed at diagnostics without rerunning prior candidates",
    ]
    state["seen_periods"] = [
        {
            "period_kind": str(record.get("period_kind", "development")),
            "seen_period": dict(record.get("seen_period", {})),
        }
        for record in candidate_records
        if isinstance(record.get("seen_period", {}), dict)
    ]

    write_trace_json(
        state,
        agent_name="ResumeLoader",
        stage="restored_state",
        payload={
            "resumed_from": source_experiment_id,
            "new_experiment_id": effective_new_id,
            "epoch_index": epoch_index,
            "source_candidate_id": state["diagnostic_request"].get("source_candidate_id"),
            "candidate_record_count": len(candidate_records),
            "research_trial_count": state["research_trial_count"],
            "final_period_read": False,
            "strategy_code_source": str(code_path),
            "development_report_source": str(development_path),
            "research_decision_source": str(hypothesis_path),
        },
    )
    return state


def restore_next_hypothesis_state_from_trace(
    *,
    source_experiment_id: str,
    source_yaml: str | Path,
    new_experiment_id: str | None = None,
    max_epochs: int = 5,
    expected_candidate_id: str | None = None,
) -> WorkflowState:
    """从来源实验最近完成的开发期候选继续下一轮研究。"""

    source_id = source_experiment_id.strip()
    if not source_id:
        raise ValueError("source_experiment_id 不能为空")

    lineage = _trace_lineage(source_id)
    entries = _completed_development_entries(lineage)
    source_entries = [
        entry for entry in entries if entry["experiment_id"] == source_id
    ]
    if not source_entries:
        raise FileNotFoundError("来源实验本身还没有已完成的开发期候选")
    latest_entry = source_entries[-1]
    latest_candidate = latest_entry["candidate"]
    latest_candidate_id = str(latest_candidate.get("candidate_id", "")).strip()
    expected_id = (
        expected_candidate_id.strip()
        if isinstance(expected_candidate_id, str) and expected_candidate_id.strip()
        else ""
    )
    if expected_id and latest_candidate_id != expected_id:
        raise FileNotFoundError(
            f"来源实验最近完成的是 {latest_candidate_id or '未知候选'}，"
            f"还没有找到期望的 {expected_id}"
        )
    latest_epoch = int(
        latest_candidate.get(
            "epoch_index",
            _trace_epoch_index(latest_entry["path"]),
        )
    )
    next_epoch = latest_epoch + 1
    if max_epochs < next_epoch:
        raise ValueError(
            f"max_epochs={max_epochs} 小于下一研究轮 {next_epoch}，无法继续"
        )

    source_yaml_path = Path(source_yaml)
    if not source_yaml_path.is_absolute():
        source_yaml_path = PROJECT_ROOT / source_yaml_path
    user_idea, source_spec = _load_source_spec(source_yaml_path)

    development_report, development_report_path = _development_report_for_candidate(
        latest_entry
    )
    development_period = development_report.get("development_period", {})
    if not isinstance(development_period, dict) or not development_period:
        development_period = latest_candidate.get("seen_period", {})
    if not isinstance(development_period, dict):
        raise ValueError("最近候选的开发期报告缺少日期")
    development_start = str(development_period.get("start", ""))[:10]
    development_end = str(development_period.get("end", ""))[:10]
    if not development_start or not development_end:
        raise ValueError("最近候选的开发期日期为空")
    configured_development_end = str(source_spec.get("validate_end", ""))[:10]
    if configured_development_end and development_end > configured_development_end:
        raise ValueError("最近候选的开发期报告超出了 YAML 配置的开发期")

    current_meta, research_meta_path = _research_meta_for_candidate(latest_entry)
    current_meta["required_data"] = _cap_required_data(
        current_meta.get("required_data", []),
        latest_allowed_date=development_end,
    )
    current_meta["hypothesis"] = _hide_final_dates(
        str(current_meta.get("hypothesis", latest_candidate.get("hypothesis", "")))
    )
    if not str(current_meta.get("hypothesis", "")).strip():
        raise ValueError("最近候选缺少完整策略说明")

    strategy_code, strategy_meta, code_path, strategy_meta_path = (
        _strategy_artifacts_for_candidate(latest_entry)
    )
    params = _literal_params_from_code(strategy_code)

    translation_meta: dict[str, object] = {}
    if entries:
        try:
            translation_meta, _ = _research_meta_for_candidate(entries[0])
        except (FileNotFoundError, ValueError):
            translation_meta = {}
    if translation_meta:
        translation_meta["required_data"] = _cap_required_data(
            translation_meta.get("required_data", []),
            latest_allowed_date=development_end,
        )
        translation_meta["hypothesis"] = _hide_final_dates(
            str(translation_meta.get("hypothesis", ""))
        )

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    effective_new_id = (
        new_experiment_id.strip()
        if isinstance(new_experiment_id, str) and new_experiment_id.strip()
        else f"{source_id}_resume_after_{latest_candidate_id}_{timestamp}"
    )
    if effective_new_id == source_id:
        raise ValueError("新实验 ID 不能与来源实验相同")
    spec = _safe_resume_spec(
        user_idea=user_idea,
        spec=source_spec,
        source_experiment_id=source_id,
        new_experiment_id=effective_new_id,
    )

    candidate_records = [dict(entry["candidate"]) for entry in entries]
    experiment_records = [
        dict(entry["experiment"])
        for entry in entries
        if isinstance(entry.get("experiment"), dict) and entry["experiment"]
    ]
    diagnostics = _diagnostic_records_for_lineage(
        lineage,
        source_experiment_id=source_id,
        source_cutoff_path=latest_entry["path"],
    )
    development_backtest_feedback = (
        _backtest_feedback_from_last_development_candidate(candidate_records)
    )

    state = init_state(
        user_idea=str(spec["user_idea"]),
        max_epochs=max_epochs,
        experiment_spec=spec,
    )
    state["epoch_index"] = next_epoch
    state["current_candidate_id"] = f"candidate_{next_epoch:03d}"
    state["phase"] = "hypothesis"
    state["research_stage"] = "iteration"
    state["hypothesis_generation_meta"] = current_meta
    state["translation_meta"] = translation_meta
    state["strategy_generation_meta"] = strategy_meta
    state["strategy_code"] = strategy_code
    state["code_text"] = strategy_code
    state["strategy_result"] = {
        "params": params,
        "required_data": current_meta.get("required_data", []),
        "backtest_datasets": current_meta.get("backtest_datasets", []),
    }
    state["development_report"] = dict(development_report)
    state["development_period"] = {
        "start": development_start,
        "end": development_end,
    }
    state["final_test_period"] = {}
    state["candidate_records"] = candidate_records
    state["experiment_records"] = experiment_records

    manager_payload = latest_entry["manager_payload"]
    state["research_trial_count"] = max(
        len(candidate_records),
        int(manager_payload.get("research_trial_count", len(candidate_records))),
    )
    state["technical_retry_count"] = int(
        manager_payload.get("technical_retry_count", 0)
    )
    state["final_test_count"] = 0
    state["test_result"] = None
    state["final_test_result"] = None
    state["quality_passed"] = False
    state["final_quality_passed"] = False
    state["ready_for_final"] = False
    state["candidate_frozen"] = False
    state["awaiting_final_test_approval"] = False
    state["evaluation_stage"] = "development"
    state["strategy_validate_round"] = 1
    state["validation_result"] = {}
    state["validation_summary"] = {}
    state["validation_feedback"] = {}
    state["last_test_error"] = ""
    state["diagnostic_request"] = {}
    state["diagnostic_report"] = {}
    state["diagnostic_records"] = diagnostics
    state["diagnostic_round"] = 0
    state["backtest_feedback"] = development_backtest_feedback
    state["manager_notes"] = (
        f"Restored after {latest_candidate_id} from {source_id}; "
        f"continuing at hypothesis epoch {next_epoch}."
    )
    state["history"] = [
        f"Restored development candidates from lineage ending at {source_id}",
        f"Epoch {next_epoch}: resumed at hypothesis after {latest_candidate_id}",
    ]

    seen_periods: list[dict[str, object]] = []
    for record in candidate_records:
        seen_period = record.get("seen_period", {})
        if not isinstance(seen_period, dict):
            continue
        item: dict[str, object] = {
            "period_kind": "development",
            "seen_period": dict(seen_period),
        }
        if item not in seen_periods:
            seen_periods.append(item)
    state["seen_periods"] = seen_periods

    write_trace_json(
        state,
        agent_name="ResumeLoader",
        stage="restored_state",
        payload={
            "resume_point": "next_hypothesis",
            "resumed_from": source_id,
            "lineage": [experiment_id for experiment_id, _ in lineage],
            "new_experiment_id": effective_new_id,
            "source_candidate_id": latest_candidate_id,
            "next_candidate_id": state["current_candidate_id"],
            "epoch_index": next_epoch,
            "candidate_record_count": len(candidate_records),
            "research_trial_count": state["research_trial_count"],
            "diagnostic_record_count": len(diagnostics),
            "final_period_read": False,
            "strategy_code_source": str(code_path),
            "strategy_meta_source": str(strategy_meta_path),
            "development_report_source": str(
                development_report_path or latest_entry["path"]
            ),
            "research_meta_source": str(research_meta_path),
        },
    )
    return state


def restore_latest_diagnostics_state_from_trace(
    *,
    source_experiment_id: str,
    source_yaml: str | Path,
    new_experiment_id: str | None = None,
    max_epochs: int = 5,
    expected_candidate_id: str | None = None,
) -> WorkflowState:
    """从来源实验最新的 diagnose_only 决定继续固定分析。"""

    source_id = source_experiment_id.strip()
    if not source_id:
        raise ValueError("source_experiment_id 不能为空")

    lineage = _trace_lineage(source_id)
    entries = _completed_development_entries(lineage)
    if not entries:
        raise FileNotFoundError("来源实验及其祖先没有已完成的开发期候选")
    latest_entry = entries[-1]
    latest_candidate = latest_entry["candidate"]
    latest_candidate_id = str(latest_candidate.get("candidate_id", "")).strip()
    expected_id = (
        expected_candidate_id.strip()
        if isinstance(expected_candidate_id, str) and expected_candidate_id.strip()
        else ""
    )
    if expected_id and latest_candidate_id != expected_id:
        raise FileNotFoundError(
            f"最近完成的是 {latest_candidate_id or '未知候选'}，"
            f"还没有找到期望的 {expected_id}"
        )

    source_run_root = lineage[-1][1]
    research_wrapper, research_payload, research_path = _latest_research_output(
        source_run_root
    )
    decision = str(research_payload.get("decision", "")).strip()
    if decision != "diagnose_only":
        raise ValueError("来源实验最新研究决定不是 diagnose_only")
    requested_diagnostics = research_payload.get("requested_diagnostics", [])
    if not isinstance(requested_diagnostics, list) or not requested_diagnostics:
        raise ValueError("最新 diagnose_only 输出没有 requested_diagnostics")

    research_epoch = int(
        research_wrapper.get("epoch_index", _trace_epoch_index(research_path))
    )
    latest_candidate_epoch = int(
        latest_candidate.get(
            "epoch_index",
            _trace_epoch_index(latest_entry["path"]),
        )
    )
    if research_epoch != latest_candidate_epoch + 1:
        raise ValueError(
            "最新 diagnose_only 输出与最近完成候选不是相邻研究轮，不能安全恢复"
        )
    if max_epochs < research_epoch:
        raise ValueError(
            f"max_epochs={max_epochs} 小于当前研究轮 {research_epoch}，无法继续"
        )

    source_yaml_path = Path(source_yaml)
    if not source_yaml_path.is_absolute():
        source_yaml_path = PROJECT_ROOT / source_yaml_path
    user_idea, source_spec = _load_source_spec(source_yaml_path)

    development_report, development_report_path = _development_report_for_candidate(
        latest_entry
    )
    development_period = development_report.get("development_period", {})
    if not isinstance(development_period, dict) or not development_period:
        development_period = latest_candidate.get("seen_period", {})
    if not isinstance(development_period, dict):
        raise ValueError("最近候选的开发期报告缺少日期")
    development_start = str(development_period.get("start", ""))[:10]
    development_end = str(development_period.get("end", ""))[:10]
    if not development_start or not development_end:
        raise ValueError("最近候选的开发期日期为空")
    configured_development_end = str(source_spec.get("validate_end", ""))[:10]
    if configured_development_end and development_end > configured_development_end:
        raise ValueError("最近候选的开发期报告超出了 YAML 配置的开发期")

    current_meta = dict(research_payload)
    current_meta["required_data"] = _cap_required_data(
        current_meta.get("required_data", []),
        latest_allowed_date=development_end,
    )
    current_meta["hypothesis"] = _hide_final_dates(
        str(current_meta.get("hypothesis", latest_candidate.get("hypothesis", "")))
    )
    if not str(current_meta.get("hypothesis", "")).strip():
        raise ValueError("最新 diagnose_only 输出缺少完整策略说明")

    strategy_code, strategy_meta, code_path, strategy_meta_path = (
        _strategy_artifacts_for_candidate(latest_entry)
    )
    params = _literal_params_from_code(strategy_code)

    translation_meta: dict[str, object] = {}
    try:
        translation_meta, _ = _research_meta_for_candidate(entries[0])
    except (FileNotFoundError, ValueError):
        translation_meta = {}
    if translation_meta:
        translation_meta["required_data"] = _cap_required_data(
            translation_meta.get("required_data", []),
            latest_allowed_date=development_end,
        )
        translation_meta["hypothesis"] = _hide_final_dates(
            str(translation_meta.get("hypothesis", ""))
        )

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    effective_new_id = (
        new_experiment_id.strip()
        if isinstance(new_experiment_id, str) and new_experiment_id.strip()
        else f"{source_id}_resume_diagnostics_epoch{research_epoch}_{timestamp}"
    )
    if effective_new_id == source_id:
        raise ValueError("新实验 ID 不能与来源实验相同")
    spec = _safe_resume_spec(
        user_idea=user_idea,
        spec=source_spec,
        source_experiment_id=source_id,
        new_experiment_id=effective_new_id,
    )

    candidate_records = [dict(entry["candidate"]) for entry in entries]
    experiment_records = [
        dict(entry["experiment"])
        for entry in entries
        if isinstance(entry.get("experiment"), dict) and entry["experiment"]
    ]
    diagnostics, diagnostic_paths = _all_diagnostic_records_for_lineage(lineage)
    source_epoch_diagnostic_count = sum(
        1
        for path in diagnostic_paths
        if source_run_root in path.parents
        and _trace_epoch_index(path) == research_epoch
    )
    development_backtest_feedback = (
        _backtest_feedback_from_last_development_candidate(candidate_records)
    )

    state = init_state(
        user_idea=str(spec["user_idea"]),
        max_epochs=max_epochs,
        experiment_spec=spec,
    )
    state["epoch_index"] = research_epoch
    state["current_candidate_id"] = f"candidate_{research_epoch:03d}"
    state["phase"] = "diagnostics"
    state["research_stage"] = "iteration"
    state["hypothesis_generation_meta"] = current_meta
    state["translation_meta"] = translation_meta
    state["strategy_generation_meta"] = strategy_meta
    state["strategy_code"] = strategy_code
    state["code_text"] = strategy_code
    state["strategy_result"] = {
        "params": params,
        "required_data": current_meta.get("required_data", []),
        "backtest_datasets": current_meta.get("backtest_datasets", []),
    }
    state["development_report"] = dict(development_report)
    state["development_period"] = {
        "start": development_start,
        "end": development_end,
    }
    state["final_test_period"] = {}
    state["candidate_records"] = candidate_records
    state["experiment_records"] = experiment_records

    latest_manager_payload = latest_entry["manager_payload"]
    state["research_trial_count"] = max(
        len(candidate_records),
        int(
            latest_manager_payload.get(
                "research_trial_count",
                len(candidate_records),
            )
        ),
    )
    state["technical_retry_count"] = int(
        latest_manager_payload.get("technical_retry_count", 0)
    )
    state["final_test_count"] = 0
    state["test_result"] = None
    state["final_test_result"] = None
    state["quality_passed"] = False
    state["final_quality_passed"] = False
    state["ready_for_final"] = False
    state["candidate_frozen"] = False
    state["awaiting_final_test_approval"] = False
    state["evaluation_stage"] = "development"
    state["strategy_validate_round"] = 1
    state["validation_result"] = {}
    state["validation_summary"] = {}
    state["validation_feedback"] = {}
    state["last_test_error"] = ""
    state["diagnostic_report"] = {}
    state["diagnostic_records"] = diagnostics
    state["diagnostic_round"] = source_epoch_diagnostic_count
    state["backtest_feedback"] = development_backtest_feedback
    state["diagnostic_request"] = build_diagnostic_request(
        state,
        requested_diagnostics,
    )
    state["manager_notes"] = (
        f"Restored diagnose_only request from {source_id}; "
        f"continuing fixed diagnostics in epoch {research_epoch}."
    )
    state["history"] = [
        f"Restored development candidates from lineage ending at {source_id}",
        f"Epoch {research_epoch}: resumed diagnose_only without adding a formal candidate",
    ]

    seen_periods: list[dict[str, object]] = []
    for record in candidate_records:
        seen_period = record.get("seen_period", {})
        if not isinstance(seen_period, dict):
            continue
        item: dict[str, object] = {
            "period_kind": "development",
            "seen_period": dict(seen_period),
        }
        if item not in seen_periods:
            seen_periods.append(item)
    state["seen_periods"] = seen_periods

    write_trace_json(
        state,
        agent_name="ResumeLoader",
        stage="restored_state",
        payload={
            "resume_point": "latest_diagnostics",
            "resumed_from": source_id,
            "lineage": [experiment_id for experiment_id, _ in lineage],
            "new_experiment_id": effective_new_id,
            "source_candidate_id": latest_candidate_id,
            "current_candidate_id": state["current_candidate_id"],
            "epoch_index": research_epoch,
            "candidate_record_count": len(candidate_records),
            "research_trial_count": state["research_trial_count"],
            "diagnostic_record_count": len(diagnostics),
            "requested_diagnostic_ids": [
                str(item.get("id", ""))
                for item in requested_diagnostics
                if isinstance(item, dict)
            ],
            "final_period_read": False,
            "strategy_code_source": str(code_path),
            "strategy_meta_source": str(strategy_meta_path),
            "development_report_source": str(
                development_report_path or latest_entry["path"]
            ),
            "research_meta_source": str(research_path),
        },
    )
    return state
