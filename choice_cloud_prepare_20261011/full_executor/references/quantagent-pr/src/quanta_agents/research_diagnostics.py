from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import glob
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd

from quanta_agents.event_parquet_backtest import SUPPORTED_HORIZONS
from quanta_agents.state import WorkflowState, periods_overlap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_MINUTE_DATA_DIR = Path(
    r"D:\A股 1min 数据 2000-2026年\分钟数据_前复权_Parquet\data"
)

SUPPORTED_DIAGNOSTIC_IDS = frozenset(
    {
        "period_metrics",
        "rule_waterfall",
        "feature_screening",
        "low_ma5_distance_screening",
        "event_response_curve",
        "next_pulse_mechanism",
        "core_variant_comparison",
        "execution_price_audit",
        "flow_reconciliation",
        "signal_weight_summary",
        "cooldown_comparison",
        "return_cost_distribution",
        "account_separation",
    }
)

DEFAULT_EVENT_DIAGNOSTICS = (
    "period_metrics",
    "rule_waterfall",
    "feature_screening",
    "execution_price_audit",
    "flow_reconciliation",
    "signal_weight_summary",
    "cooldown_comparison",
    "return_cost_distribution",
    "account_separation",
)


LOW_MA5_SAFE_FEATURES = (
    "trigger_low_to_prev5_close_ma5_distance",
    "trigger_low_to_live_close_ma5_distance",
)
LOW_MA5_ANALYSIS_ONLY_FIELD = (
    "analysis_only_full_day_low_to_close_ma5_distance"
)


def _latest_development_candidate_id(state: WorkflowState) -> str:
    records = state.get("candidate_records", [])
    if isinstance(records, list):
        for record in reversed(records):
            if not isinstance(record, dict):
                continue
            if str(record.get("period_kind", "development")) != "development":
                continue
            candidate_id = str(record.get("candidate_id", "")).strip()
            if candidate_id:
                return candidate_id
    epoch = max(int(state.get("epoch_index", 1)) - 1, 1)
    return f"candidate_{epoch:03d}"


def _diagnostic_id_from_text(value: str) -> str | None:
    text = value.strip().lower()
    if not text:
        return None
    if any(token in text for token in ("核心变体", "规则组合", "variant comparison")):
        return "core_variant_comparison"
    if any(token in text for token in ("年度", "月度", "按年", "按月", "period")):
        return "period_metrics"
    if any(token in text for token in ("逐步", "past_up_count", "筛选条件", "waterfall")):
        return "rule_waterfall"
    if any(token in text for token in ("五日均线", "最低价距离", "low_ma5", "ma5 distance")):
        return "low_ma5_distance_screening"
    if any(token in text for token in ("其他特征", "单变量", "特征筛选", "feature")):
        return "feature_screening"
    if any(token in text for token in ("响应曲线", "5/10/20", "response curve")):
        return "event_response_curve"
    if any(token in text for token in ("下一批机制", "volume_hit", "next_up", "next pulse")):
        return "next_pulse_mechanism"
    if any(token in text for token in ("取价", "午休", "停牌", "涨跌停", "缺价", "price")):
        return "execution_price_audit"
    if any(token in text for token in ("35813", "35811", "28147", "数量差异", "逐项核对")):
        return "flow_reconciliation"
    if any(token in text for token in ("目标比例", "现金比例", "实际买卖", "成交金额", "同时出现")):
        return "signal_weight_summary"
    if any(token in text for token in ("冷却", "限制前后", "cooldown")):
        return "cooldown_comparison"
    if any(token in text for token in ("收益分布", "极端值", "盈亏平衡费用", "费用分项")):
        return "return_cost_distribution"
    if any(token in text for token in ("账户收益", "事件统计", "t+1")):
        return "account_separation"
    return None


def normalize_diagnostic_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        diagnostic_id: str | None = None
        reason = ""
        parameters: dict[str, object] = {}
        decision_if_positive = ""
        decision_if_negative = ""
        if isinstance(item, str):
            reason = item.strip()
            diagnostic_id = _diagnostic_id_from_text(reason)
        elif isinstance(item, dict):
            raw_id = item.get("id", item.get("diagnostic_id"))
            if isinstance(raw_id, str):
                diagnostic_id = raw_id.strip()
            raw_reason = item.get("reason", item.get("purpose", ""))
            if isinstance(raw_reason, str):
                reason = raw_reason.strip()
            raw_parameters = item.get("parameters", {})
            if isinstance(raw_parameters, dict):
                parameters = dict(raw_parameters)
            raw_positive = item.get("decision_if_positive", "")
            if isinstance(raw_positive, str):
                decision_if_positive = raw_positive.strip()
            raw_negative = item.get("decision_if_negative", "")
            if isinstance(raw_negative, str):
                decision_if_negative = raw_negative.strip()

        if diagnostic_id not in SUPPORTED_DIAGNOSTIC_IDS:
            continue
        if diagnostic_id in seen:
            continue
        seen.add(diagnostic_id)
        normalized_item: dict[str, object] = {
            "id": diagnostic_id,
            "reason": reason,
            "parameters": parameters,
        }
        if decision_if_positive:
            normalized_item["decision_if_positive"] = decision_if_positive
        if decision_if_negative:
            normalized_item["decision_if_negative"] = decision_if_negative
        normalized.append(normalized_item)
    return normalized


def build_diagnostic_request(
    state: WorkflowState,
    requested: object,
) -> dict[str, object]:
    items = normalize_diagnostic_items(requested)
    if not items:
        items = [
            {"id": diagnostic_id, "reason": "标准分钟事件诊断", "parameters": {}}
            for diagnostic_id in DEFAULT_EVENT_DIAGNOSTICS
        ]
    elif any(item.get("id") == "rule_waterfall" for item in items) and not any(
        item.get("id") == "feature_screening" for item in items
    ):
        items.append(
            {
                "id": "feature_screening",
                "reason": "停止前检查其他可在决策时获得的单变量简单条件",
                "parameters": {},
            }
        )

    source_candidate_id = _latest_development_candidate_id(state)
    next_round = int(state.get("diagnostic_round", 0)) + 1
    configured_periods = _configured_research_periods(state)
    allowed_periods = (
        configured_periods
        if configured_periods
        else {
            "train": {
                "start": dict(state.get("experiment_spec", {})).get("train_start", ""),
                "end": dict(state.get("experiment_spec", {})).get("train_end", ""),
            },
            "development": dict(state.get("development_period", {})),
        }
    )
    request = {
        "request_id": (
            f"{source_candidate_id}_epoch_{int(state.get('epoch_index', 1)):03d}"
            f"_diagnostic_{next_round:02d}"
        ),
        "source_candidate_id": source_candidate_id,
        "research_epoch": int(state.get("epoch_index", 1)),
        "items": items,
        "allowed_periods": allowed_periods,
    }
    research_stage = str(state.get("research_stage", "")).strip().lower()
    if research_stage:
        request["research_stage"] = research_stage
    elif bool(state.get("initial_research", False)):
        request["research_stage"] = "initial"
    return request


def _literal_params_from_code(code: str) -> dict[str, object]:
    if not code.strip():
        return {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets: Iterable[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            targets = (node.target,)
            value = node.value
        if value is None or not any(
            isinstance(target, ast.Name) and target.id == "params" for target in targets
        ):
            continue
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _resolve_rule_params(state: WorkflowState) -> dict[str, object]:
    strategy_result = state.get("strategy_result", {})
    if isinstance(strategy_result, dict):
        params = strategy_result.get("params", {})
        if isinstance(params, dict) and params:
            return dict(params)
    code = str(state.get("strategy_code", "") or state.get("code_text", ""))
    return _literal_params_from_code(code)


def _resolve_event_horizon_minutes(
    params: dict[str, object],
    spec: dict[str, object],
) -> tuple[int, str]:
    if "event_horizon_minutes" in params:
        raw = params["event_horizon_minutes"]
        source = "strategy_result.params"
    else:
        raw = spec.get("event_horizon_minutes", 5)
        source = "experiment_spec.event_horizon_minutes"
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not float(raw).is_integer()
    ):
        raise ValueError("event_horizon_minutes 必须是整数 5、10 或 20")
    resolved = int(raw)
    if resolved not in SUPPORTED_HORIZONS:
        raise ValueError("event_horizon_minutes 只允许 5、10 或 20")
    return resolved, source


def _resolve_event_paths() -> list[str]:
    raw = (
        os.getenv("QUANTA_EVENT_FEATURES_PATH", "").strip()
        or os.getenv("QUANTA_MINUTE_EVENT_FEATURES_PATH", "").strip()
    )
    if not raw:
        raw = str(PROJECT_ROOT / "data_cache" / "periodic_active_buying" / "minute_events_2022_2025.parquet")
    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.is_dir():
        path = path / "*.parquet"
    matches = sorted(glob.iglob(str(path)))
    if not matches:
        raise FileNotFoundError(f"没有找到分钟事件文件: {path}")
    return matches


def _period_from_state(state: WorkflowState, kind: str) -> dict[str, str]:
    if kind == "train":
        spec = state.get("experiment_spec", {})
        if not isinstance(spec, dict):
            return {}
        start = spec.get("train_start")
        end = spec.get("train_end")
    else:
        period = state.get("development_period", {})
        if not isinstance(period, dict):
            return {}
        start = period.get("start")
        end = period.get("end")
    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return {}
    return {"start": start[:10], "end": end[:10]}


def _configured_research_periods(
    state: WorkflowState,
) -> dict[str, dict[str, str]]:
    spec = state.get("experiment_spec", {})
    if not isinstance(spec, dict):
        return {}
    keys = (
        "research_selection_start",
        "research_selection_end",
        "research_confirmation_start",
        "research_confirmation_end",
    )
    values = {key: spec.get(key) for key in keys}
    configured = {
        key: isinstance(value, str) and bool(value.strip())
        for key, value in values.items()
    }
    if not any(configured.values()):
        return {}
    if not all(configured.values()):
        missing = [key for key, present in configured.items() if not present]
        raise ValueError(
            "固定研究选择期和确认期必须同时配置四个日期字段: "
            + ", ".join(missing)
        )

    periods = {
        "selection": {
            "start": str(values["research_selection_start"])[:10],
            "end": str(values["research_selection_end"])[:10],
        },
        "confirmation": {
            "start": str(values["research_confirmation_start"])[:10],
            "end": str(values["research_confirmation_end"])[:10],
        },
    }
    for name, period in periods.items():
        try:
            start = pd.Timestamp(period["start"]).normalize()
            end = pd.Timestamp(period["end"]).normalize()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}日期格式无效") from exc
        if start > end:
            raise ValueError(f"{name}开始日期不能晚于结束日期")
    if periods_overlap(periods["selection"], periods["confirmation"]):
        raise ValueError("固定研究选择期和确认期不能重叠")
    final_period = state.get("final_test_period", {})
    if isinstance(final_period, dict) and final_period:
        if any(
            periods_overlap(period, final_period)
            for period in periods.values()
        ):
            raise ValueError("固定研究选择期或确认期与最终时期重叠，拒绝补算")
    return periods


def _validate_research_periods(state: WorkflowState) -> dict[str, dict[str, str]]:
    train = _period_from_state(state, "train")
    development = _period_from_state(state, "development")
    if not train or not development:
        raise ValueError("训练期或开发期日期不完整")
    final_period = state.get("final_test_period", {})
    if isinstance(final_period, dict) and final_period:
        if periods_overlap(train, final_period) or periods_overlap(development, final_period):
            raise ValueError("训练期或开发期与最终时期重叠，拒绝补算")
    return {"train": train, "development": development}


def _initial_research_requested(
    state: WorkflowState,
    request: dict[str, object],
    items: list[dict[str, object]],
) -> bool:
    spec = state.get("experiment_spec", {})
    if not isinstance(spec, dict):
        spec = {}
    stage_values = [
        request.get("research_stage"),
        state.get("research_stage"),
        spec.get("research_stage"),
    ]
    for item in items:
        parameters = item.get("parameters", {})
        if isinstance(parameters, dict):
            stage_values.append(parameters.get("research_stage"))
    if any(
        str(value).strip().lower() in {"initial", "initial_research"}
        for value in stage_values
    ):
        return True
    return bool(
        state.get("initial_research", False)
        or state.get("is_initial_research", False)
    )


def _split_initial_train_period(train: dict[str, str]) -> dict[str, dict[str, str]]:
    start = pd.Timestamp(train["start"]).normalize()
    end = pd.Timestamp(train["end"]).normalize()
    if start >= end:
        raise ValueError("初始研究至少需要两个自然日的训练期")

    if start.year < end.year:
        internal_start = pd.Timestamp(year=end.year, month=1, day=1)
        if internal_start <= start:
            internal_start = start + pd.Timedelta(days=1)
    else:
        total_days = int((end - start).days) + 1
        internal_start = start + pd.Timedelta(days=max(1, total_days // 2))
    if internal_start > end:
        raise ValueError("训练期太短，无法拆成选择期和内部检查期")

    selection_end = internal_start - pd.Timedelta(days=1)
    return {
        "selection": {
            "start": start.strftime("%Y-%m-%d"),
            "end": selection_end.strftime("%Y-%m-%d"),
        },
        "internal_check": {
            "start": internal_start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        },
    }


def _initial_research_periods(state: WorkflowState) -> dict[str, dict[str, str]]:
    train = _period_from_state(state, "train")
    if not train:
        raise ValueError("训练期日期不完整")
    final_period = state.get("final_test_period", {})
    if isinstance(final_period, dict) and final_period and periods_overlap(train, final_period):
        raise ValueError("训练期与最终时期重叠，拒绝补算")
    return _split_initial_train_period(train)


def _selection_and_evaluation_names(
    frames: dict[str, pd.DataFrame],
) -> tuple[str, str]:
    if "selection" in frames and "confirmation" in frames:
        return "selection", "confirmation"
    if "selection" in frames and "internal_check" in frames:
        return "selection", "internal_check"
    return "train", "development"


def _read_event_frame(
    paths: list[str],
    *,
    start: str,
    end: str,
    horizon_minutes: int,
) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.parquet as pq

    preferred_return = f"forward_{horizon_minutes}m"
    requested = {
        "candidate_id",
        "date",
        "code",
        "session_no",
        "current_position",
        "trigger_ts",
        "predicted_ts",
        "trigger_position",
        "predicted_position",
        "next_spike_position",
        "past_up_count",
        "step_up_count",
        "period",
        "gap_min",
        "gap_max",
        "return_stability",
        "amount_cv",
        "volume_z_mean",
        "volume_z_cv",
        "no_early_spike",
        "pre_window_up",
        "quiet_net_return",
        "quiet_abs_mean",
        "pulse_return_sum",
        "rise_concentration",
        "pulse_close_position_mean",
        "pulse1_position",
        "pulse1_return",
        "pulse1_amount",
        "pulse2_position",
        "pulse2_return",
        "pulse2_amount",
        "pulse3_position",
        "pulse3_return",
        "pulse3_amount",
        "pulse4_position",
        "pulse4_return",
        "pulse4_amount",
        "pulse5_position",
        "pulse5_return",
        "pulse5_amount",
        *LOW_MA5_SAFE_FEATURES,
        LOW_MA5_ANALYSIS_ONLY_FIELD,
        "target_minute_return",
        "forward_5m",
        "forward_10m",
        "forward_20m",
        "volume_hit",
        "next_up",
        "joint_minute_hit",
    }
    start_date = pd.Timestamp(start).normalize()
    end_exclusive = pd.Timestamp(end).normalize() + pd.Timedelta(days=1)
    frames: list[pd.DataFrame] = []
    for path in paths:
        schema = pq.read_schema(path)
        available = set(schema.names)
        columns = sorted(requested & available)
        if "trigger_ts" not in available:
            raise ValueError("分钟事件文件缺少 trigger_ts")
        trigger_type = schema.field("trigger_ts").type
        if pa.types.is_timestamp(trigger_type) or pa.types.is_date(trigger_type):
            lower: object = start_date.to_pydatetime()
            upper: object = end_exclusive.to_pydatetime()
        else:
            lower = start_date.strftime("%Y-%m-%d 00:00:00")
            upper = end_exclusive.strftime("%Y-%m-%d 00:00:00")
        frames.append(
            pd.read_parquet(
                path,
                columns=columns,
                filters=[("trigger_ts", ">=", lower), ("trigger_ts", "<", upper)],
            )
        )
    frame = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0].copy()
    required = {"candidate_id", "code", "trigger_ts", preferred_return}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"分钟事件文件缺少诊断字段: {', '.join(missing)}")
    frame["trigger_ts"] = pd.to_datetime(frame["trigger_ts"], errors="coerce")
    frame = frame.loc[frame["trigger_ts"].notna()].copy()
    frame["date"] = frame["trigger_ts"].dt.normalize()
    frame["code"] = frame["code"].astype(str).str.strip().str.lower()
    for column in (
        "forward_5m",
        "forward_10m",
        "forward_20m",
        *LOW_MA5_SAFE_FEATURES,
        LOW_MA5_ANALYSIS_ONLY_FIELD,
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "gap_min" in frame.columns and "gap_max" in frame.columns:
        frame["gap_spread"] = (
            pd.to_numeric(frame["gap_max"], errors="coerce")
            - pd.to_numeric(frame["gap_min"], errors="coerce")
        )
    frame["gross_return"] = pd.to_numeric(frame[preferred_return], errors="coerce")
    return frame.reset_index(drop=True)


def _daily_mean_interval(frame: pd.DataFrame) -> tuple[float | None, float | None]:
    valid = frame.loc[frame["gross_return"].notna(), ["date", "gross_return"]]
    if valid.empty:
        return None, None
    daily = valid.groupby("date", sort=True)["gross_return"].agg(["sum", "count"])
    center = float(valid["gross_return"].mean())
    if len(daily) <= 1:
        return center, center
    sums = daily["sum"].to_numpy(dtype=float)
    counts = daily["count"].to_numpy(dtype=float)
    generator = np.random.default_rng(20_260_810 + len(valid) + len(daily))
    indices = generator.integers(0, len(daily), size=(2_000, len(daily)))
    sampled_means = sums[indices].sum(axis=1) / counts[indices].sum(axis=1)
    low, high = np.quantile(sampled_means, [0.025, 0.975])
    return float(low), float(high)


def _summarize_returns(frame: pd.DataFrame, cost_rate: float) -> dict[str, object]:
    gross = pd.to_numeric(frame["gross_return"], errors="coerce")
    valid_frame = frame.loc[gross.notna()].copy()
    gross = pd.to_numeric(valid_frame["gross_return"], errors="coerce")
    net = gross - float(cost_rate)
    ci_low, ci_high = _daily_mean_interval(valid_frame)
    labels = pd.Series(dtype=float)
    if "joint_minute_hit" in valid_frame.columns:
        labels = pd.to_numeric(valid_frame["joint_minute_hit"], errors="coerce").dropna()
    return {
        "event_count": int(len(frame)),
        "valid_return_count": int(len(valid_frame)),
        "invalid_return_count": int(len(frame) - len(valid_frame)),
        "gross_mean_return": float(gross.mean()) if len(gross) else None,
        "gross_median_return": float(gross.median()) if len(gross) else None,
        "gross_up_rate": float((gross > 0).mean()) if len(gross) else None,
        "gross_mean_ci95_daily_cluster": [ci_low, ci_high],
        "net_mean_return": float(net.mean()) if len(net) else None,
        "net_median_return": float(net.median()) if len(net) else None,
        "net_win_rate": float((net > 0).mean()) if len(net) else None,
        "cost_rate": float(cost_rate),
        "label_column": "joint_minute_hit" if len(labels) else None,
        "label_observation_count": int(len(labels)),
        "label_positive_count": int(labels.astype(bool).sum()) if len(labels) else 0,
        "label_positive_rate": float(labels.astype(bool).mean()) if len(labels) else None,
    }


def _summarize_by_period(
    frame: pd.DataFrame,
    *,
    frequency: str,
    cost_rate: float,
) -> list[dict[str, object]]:
    source = frame.copy()
    if frequency == "year":
        source["_period"] = source["date"].dt.strftime("%Y")
    elif frequency == "month":
        source["_period"] = source["date"].dt.strftime("%Y-%m")
    else:
        raise ValueError(f"不支持的统计频率: {frequency}")
    rows: list[dict[str, object]] = []
    for period, group in source.groupby("_period", sort=True):
        row = {"period": str(period)}
        row.update(_summarize_returns(group, cost_rate))
        rows.append(row)
    return rows


def _rule_masks(frame: pd.DataFrame, params: dict[str, object]) -> list[tuple[str, pd.Series]]:
    mask = pd.Series(True, index=frame.index)
    masks: list[tuple[str, pd.Series]] = [("all_candidates", mask.copy())]

    if "generic_selection_conditions" in params:
        conditions = _normalise_generic_selection_conditions(params)
        for condition in conditions:
            feature = str(condition["feature"])
            if feature not in frame.columns:
                raise ValueError(f"分钟事件文件缺少策略筛选字段: {feature}")
            mask &= _condition_mask(frame, condition)
            value = condition["value"]
            value_text = (
                str(value).lower()
                if isinstance(value, bool)
                else f"{float(value):g}"
            )
            masks.append(
                (
                    f"{feature}{condition['operator']}{value_text}",
                    mask.copy(),
                )
            )
        return masks

    minimum = params.get("minimum_past_up_count")
    if isinstance(minimum, (int, float)) and "past_up_count" in frame.columns:
        values = pd.to_numeric(frame["past_up_count"], errors="coerce")
        mask = mask & values.ge(float(minimum))
        masks.append((f"past_up_count>={minimum:g}", mask.copy()))

    no_early = params.get("required_no_early_spike")
    if isinstance(no_early, bool) and "no_early_spike" in frame.columns:
        mask = mask & frame["no_early_spike"].eq(no_early).fillna(False)
        masks.append((f"no_early_spike={str(no_early).lower()}", mask.copy()))

    pre_window = params.get("required_pre_window_up")
    if isinstance(pre_window, bool) and "pre_window_up" in frame.columns:
        mask = mask & frame["pre_window_up"].eq(pre_window).fillna(False)
        masks.append((f"pre_window_up={str(pre_window).lower()}", mask.copy()))

    pulse5_threshold = params.get("pulse5_return_threshold")
    if (
        isinstance(pulse5_threshold, (int, float))
        and not isinstance(pulse5_threshold, bool)
        and math.isfinite(float(pulse5_threshold))
        and "pulse5_return" in frame.columns
    ):
        values = pd.to_numeric(frame["pulse5_return"], errors="coerce")
        mask = mask & values.le(float(pulse5_threshold))
        masks.append((f"pulse5_return<={float(pulse5_threshold):g}", mask.copy()))

    pulse1_position_threshold = params.get("pulse1_position_threshold")
    if (
        isinstance(pulse1_position_threshold, (int, float))
        and not isinstance(pulse1_position_threshold, bool)
        and math.isfinite(float(pulse1_position_threshold))
        and "pulse1_position" in frame.columns
    ):
        values = pd.to_numeric(frame["pulse1_position"], errors="coerce")
        mask = mask & values.ge(float(pulse1_position_threshold))
        masks.append(
            (
                f"pulse1_position>={float(pulse1_position_threshold):g}",
                mask.copy(),
            )
        )
    return masks


def _selected_frame(frame: pd.DataFrame, params: dict[str, object]) -> pd.DataFrame:
    masks = _rule_masks(frame, params)
    return frame.loc[masks[-1][1]].copy()


def _apply_cooldown(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(
        ["date", "code", "trigger_ts", "candidate_id"], kind="stable"
    )
    if minutes <= 0:
        return ordered.reset_index(drop=True)
    duration = pd.Timedelta(minutes=int(minutes))
    last_kept: dict[tuple[pd.Timestamp, str], pd.Timestamp] = {}
    keep: list[int] = []
    for index, row in ordered.iterrows():
        key = (pd.Timestamp(row["date"]), str(row["code"]))
        timestamp = pd.Timestamp(row["trigger_ts"])
        previous = last_kept.get(key)
        if previous is None or timestamp - previous >= duration:
            keep.append(index)
            last_kept[key] = timestamp
    return ordered.loc[keep].reset_index(drop=True)


def _configured_cost(spec: dict[str, object]) -> tuple[float, dict[str, float]]:
    buy = float(spec.get("buy_cost", spec.get("commission_rate", 0.0003)))
    sell = float(spec.get("sell_cost", buy + 0.0005))
    slippage = float(spec.get("slippage", 0.0))
    return buy + sell + 2.0 * slippage, {
        "buy_cost_rate": buy,
        "sell_cost_rate": sell,
        "slippage_rate_each_side": slippage,
    }


def _rule_waterfall(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    cost_rate: float,
) -> dict[str, object]:
    periods: dict[str, object] = {}
    for name, frame in frames.items():
        steps: list[dict[str, object]] = []
        for rule_name, mask in _rule_masks(frame, params):
            row = {"rule": rule_name}
            row.update(_summarize_returns(frame.loc[mask], cost_rate))
            steps.append(row)
        periods[name] = {"steps": steps}
    return {
        "id": "rule_waterfall",
        "status": "completed",
        "periods": periods,
        "selection_fields_only": _strategy_selection_fields(params),
        "outcome_fields_used_for_selection": [],
    }


def _period_metrics(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    *,
    cost_rate: float,
    cooldown_minutes: int,
) -> dict[str, object]:
    periods: dict[str, object] = {}
    for name, frame in frames.items():
        selected = _selected_frame(frame, params)
        selected = selected.loc[selected["gross_return"].notna()].copy()
        selected = selected.drop_duplicates(["trigger_ts", "code"], keep="first")
        selected = _apply_cooldown(selected, cooldown_minutes)
        periods[name] = {
            "overall": _summarize_returns(selected, cost_rate),
            "by_year": _summarize_by_period(
                selected, frequency="year", cost_rate=cost_rate
            ),
            "by_month": _summarize_by_period(
                selected, frequency="month", cost_rate=cost_rate
            ),
        }
    return {
        "id": "period_metrics",
        "status": "completed",
        "cooldown_minutes": cooldown_minutes,
        "periods": periods,
    }


_SCREENING_FEATURES = (
    "session_no",
    "current_position",
    "trigger_position",
    "period",
    "gap_min",
    "gap_max",
    "past_up_count",
    "step_up_count",
    "return_stability",
    "amount_cv",
    "volume_z_mean",
    "volume_z_cv",
    "no_early_spike",
    "pre_window_up",
    "quiet_net_return",
    "quiet_abs_mean",
    "pulse_return_sum",
    "rise_concentration",
    "pulse_close_position_mean",
    "pulse1_position",
    "pulse1_return",
    "pulse1_amount",
    "pulse2_position",
    "pulse2_return",
    "pulse2_amount",
    "pulse3_position",
    "pulse3_return",
    "pulse3_amount",
    "pulse4_position",
    "pulse4_return",
    "pulse4_amount",
    "pulse5_position",
    "pulse5_return",
    "pulse5_amount",
    *LOW_MA5_SAFE_FEATURES,
)

_OUTCOME_FIELDS = frozenset(
    {
        "volume_hit",
        "next_up",
        "joint_minute_hit",
        "next_spike_position",
        "target_minute_return",
        "forward_5m",
        "forward_10m",
        "forward_20m",
        "gross_return",
        LOW_MA5_ANALYSIS_ONLY_FIELD,
    }
)
_CORE_DERIVED_FEATURES = ("gap_spread",)
_CORE_ALLOWED_FEATURES = frozenset((*_SCREENING_FEATURES, *_CORE_DERIVED_FEATURES))
_CORE_BOOLEAN_FEATURES = frozenset({"no_early_spike", "pre_window_up"})
_CORE_ALLOWED_OPERATORS = frozenset({"==", "<=", ">="})
_MAX_CORE_VARIANTS = 5
_MAX_CORE_CONDITIONS = 5


def _normalise_generic_selection_conditions(
    params: dict[str, object],
) -> list[dict[str, object]]:
    raw_conditions = params.get("generic_selection_conditions")
    if not isinstance(raw_conditions, list):
        raise ValueError("params.generic_selection_conditions 必须是列表")

    conditions: list[dict[str, object]] = []
    required_keys = {"feature", "operator", "value"}
    for index, raw_condition in enumerate(raw_conditions):
        item_name = f"params.generic_selection_conditions[{index}]"
        if not isinstance(raw_condition, dict):
            raise ValueError(f"{item_name} 必须是字典")
        if set(raw_condition) != required_keys:
            raise ValueError(
                f"{item_name} 必须且只能包含 feature/operator/value"
            )

        feature_value = raw_condition.get("feature")
        if not isinstance(feature_value, str) or not feature_value.strip():
            raise ValueError(f"{item_name}.feature 必须是非空字符串")
        feature = feature_value.strip()
        if feature in _OUTCOME_FIELDS or feature not in _CORE_ALLOWED_FEATURES:
            raise ValueError(f"{item_name} 使用了不允许的条件字段: {feature}")

        operator = raw_condition.get("operator")
        if not isinstance(operator, str) or operator not in _CORE_ALLOWED_OPERATORS:
            raise ValueError(f"{item_name} 使用了不允许的运算符: {operator}")

        value = raw_condition["value"]
        if feature in _CORE_BOOLEAN_FEATURES:
            if operator != "==" or not isinstance(value, bool):
                raise ValueError(f"{item_name} 的布尔字段只允许与 true/false 比较")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{item_name} 的数值字段必须与有限数字比较")
            if not math.isfinite(float(value)):
                raise ValueError(f"{item_name} 的数值条件不能使用无穷或空值")

        conditions.append(
            {"feature": feature, "operator": operator, "value": value}
        )
    return conditions


_DEFAULT_CORE_VARIANTS: tuple[dict[str, object], ...] = (
    {
        "name": "current_baseline",
        "conditions": [
            {"feature": "past_up_count", "operator": ">=", "value": 4},
            {"feature": "no_early_spike", "operator": "==", "value": True},
            {"feature": "pre_window_up", "operator": "==", "value": True},
        ],
    },
    {
        "name": "positive_stable_pulses",
        "conditions": [
            {"feature": "past_up_count", "operator": ">=", "value": 4},
            {"feature": "return_stability", "operator": "<=", "value": 0.5},
            {"feature": "pulse_return_sum", "operator": ">=", "value": 0.0},
        ],
    },
    {
        "name": "persistent_price_acceptance",
        "conditions": [
            {"feature": "step_up_count", "operator": ">=", "value": 4},
            {"feature": "pulse_return_sum", "operator": ">=", "value": 0.0},
            {
                "feature": "pulse_close_position_mean",
                "operator": ">=",
                "value": 0.6,
            },
        ],
    },
    {
        "name": "strong_consistent_volume",
        "conditions": [
            {"feature": "volume_z_mean", "operator": ">=", "value": 2.0},
            {"feature": "volume_z_cv", "operator": "<=", "value": 0.5},
            {"feature": "amount_cv", "operator": "<=", "value": 0.5},
        ],
    },
    {
        "name": "exact_rhythm_confirmation",
        "conditions": [
            {"feature": "gap_spread", "operator": "==", "value": 0.0},
            {"feature": "past_up_count", "operator": ">=", "value": 4},
            {"feature": "no_early_spike", "operator": "==", "value": True},
            {"feature": "pre_window_up", "operator": "==", "value": True},
        ],
    },
)


def _strategy_selection_fields(params: dict[str, object]) -> list[str]:
    if "generic_selection_conditions" in params:
        fields: list[str] = []
        for condition in _normalise_generic_selection_conditions(params):
            feature = str(condition["feature"])
            if feature not in fields:
                fields.append(feature)
        return fields

    fields: list[str] = []
    if isinstance(params.get("minimum_past_up_count"), (int, float)):
        fields.append("past_up_count")
    if isinstance(params.get("required_no_early_spike"), bool):
        fields.append("no_early_spike")
    if isinstance(params.get("required_pre_window_up"), bool):
        fields.append("pre_window_up")
    pulse5_threshold = params.get("pulse5_return_threshold")
    if (
        isinstance(pulse5_threshold, (int, float))
        and not isinstance(pulse5_threshold, bool)
        and math.isfinite(float(pulse5_threshold))
    ):
        fields.append("pulse5_return")
    pulse1_position_threshold = params.get("pulse1_position_threshold")
    if (
        isinstance(pulse1_position_threshold, (int, float))
        and not isinstance(pulse1_position_threshold, bool)
        and math.isfinite(float(pulse1_position_threshold))
    ):
        fields.append("pulse1_position")
    return fields


def _normalise_core_variants(parameters: dict[str, object]) -> list[dict[str, object]]:
    raw_variants = parameters.get("variants", _DEFAULT_CORE_VARIANTS)
    if not isinstance(raw_variants, (list, tuple)):
        raise ValueError("core_variant_comparison.variants 必须是列表")
    if not raw_variants or len(raw_variants) > _MAX_CORE_VARIANTS:
        raise ValueError("核心规则组合数量必须在1到5之间")

    variants: list[dict[str, object]] = []
    names: set[str] = set()
    for raw_variant in raw_variants:
        if not isinstance(raw_variant, dict):
            raise ValueError("每个核心规则组合必须是字典")
        unknown_variant_keys = set(raw_variant) - {"name", "conditions"}
        if unknown_variant_keys:
            raise ValueError(
                f"核心规则组合含有不允许的字段: {sorted(unknown_variant_keys)}"
            )
        name = raw_variant.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("每个核心规则组合必须有1到80字符的名称")
        name = name.strip()
        if name in names:
            raise ValueError(f"核心规则组合名称重复: {name}")
        names.add(name)

        raw_conditions = raw_variant.get("conditions")
        if not isinstance(raw_conditions, list):
            raise ValueError(f"{name}.conditions 必须是列表")
        if not raw_conditions or len(raw_conditions) > _MAX_CORE_CONDITIONS:
            raise ValueError(f"{name} 的条件数量必须在1到5之间")

        conditions: list[dict[str, object]] = []
        for raw_condition in raw_conditions:
            if not isinstance(raw_condition, dict):
                raise ValueError(f"{name} 的每个条件必须是字典")
            unknown_condition_keys = set(raw_condition) - {
                "field",
                "feature",
                "operator",
                "value",
            }
            if unknown_condition_keys:
                raise ValueError(
                    f"{name} 的条件含有不允许的字段: {sorted(unknown_condition_keys)}"
                )
            if "field" in raw_condition and "feature" in raw_condition:
                raise ValueError(f"{name} 的条件不能同时填写 field 和 feature")
            field_value = raw_condition.get("feature", raw_condition.get("field"))
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"{name} 的条件缺少 feature")
            feature = field_value.strip()
            if feature in _OUTCOME_FIELDS or feature not in _CORE_ALLOWED_FEATURES:
                raise ValueError(f"{name} 使用了不允许的条件字段: {feature}")
            operator = raw_condition.get("operator")
            if not isinstance(operator, str) or operator not in _CORE_ALLOWED_OPERATORS:
                raise ValueError(f"{name} 使用了不允许的运算符: {operator}")
            if "value" not in raw_condition:
                raise ValueError(f"{name} 的条件缺少 value")
            value = raw_condition["value"]
            if feature in _CORE_BOOLEAN_FEATURES:
                if operator != "==" or not isinstance(value, bool):
                    raise ValueError(f"{name} 的布尔字段只允许与 true/false 比较")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{name} 的数值字段必须与有限数字比较")
                if not math.isfinite(float(value)):
                    raise ValueError(f"{name} 的数值条件不能使用无穷或空值")
            conditions.append(
                {"feature": feature, "operator": str(operator), "value": value}
            )
        variants.append({"name": name, "conditions": conditions})
    return variants


def _condition_mask(frame: pd.DataFrame, condition: dict[str, object]) -> pd.Series:
    feature = str(condition["feature"])
    if feature not in frame.columns:
        return pd.Series(False, index=frame.index)
    operator = str(condition["operator"])
    value = condition["value"]
    if operator == "==":
        return frame[feature].eq(value).fillna(False)
    numeric = pd.to_numeric(frame[feature], errors="coerce")
    threshold = float(value)
    if operator == "<=":
        return numeric.le(threshold).fillna(False)
    if operator == ">=":
        return numeric.ge(threshold).fillna(False)
    raise ValueError(f"不支持的筛选符号: {operator}")


def _training_feature_conditions(train: pd.DataFrame) -> list[dict[str, object]]:
    conditions: list[dict[str, object]] = []
    seen: set[tuple[str, str, object]] = set()
    for feature in _SCREENING_FEATURES:
        if feature not in train.columns:
            continue
        source = train[feature]
        non_null = source.dropna()
        if non_null.empty:
            continue

        is_boolean = feature in {"no_early_spike", "pre_window_up"}
        unique_values = non_null.drop_duplicates()
        if is_boolean:
            candidates = [
                {"feature": feature, "operator": "==", "value": bool(value)}
                for value in sorted(unique_values.astype(bool).tolist())
            ]
        elif feature in {
            "session_no",
            "period",
            "gap_min",
            "gap_max",
            "past_up_count",
            "step_up_count",
        } and len(unique_values) <= 20:
            candidates = [
                {"feature": feature, "operator": "==", "value": float(value)}
                for value in sorted(pd.to_numeric(unique_values, errors="coerce").dropna().tolist())
            ]
        else:
            numeric = pd.to_numeric(non_null, errors="coerce").dropna()
            if numeric.empty:
                continue
            thresholds = sorted(
                {
                    float(numeric.quantile(level))
                    for level in (
                        0.01,
                        0.02,
                        0.05,
                        0.10,
                        0.20,
                        0.30,
                        0.50,
                        0.70,
                        0.80,
                        0.90,
                        0.95,
                        0.98,
                        0.99,
                    )
                }
            )
            candidates = [
                {"feature": feature, "operator": operator, "value": threshold}
                for threshold in thresholds
                for operator in ("<=", ">=")
            ]

        for condition in candidates:
            key = (
                str(condition["feature"]),
                str(condition["operator"]),
                condition["value"],
            )
            if key in seen:
                continue
            seen.add(key)
            conditions.append(condition)
    return conditions


def _feature_screening(
    frames: dict[str, pd.DataFrame],
    *,
    cost_rate: float,
    cooldown_minutes: int,
) -> dict[str, object]:
    selection_name, evaluation_name = _selection_and_evaluation_names(frames)
    train = frames[selection_name]
    development = frames[evaluation_name]
    valid_train = train.loc[train["gross_return"].notna()].copy()
    minimum_count = max(1_000, int(math.ceil(len(valid_train) * 0.01)))
    ranked: list[dict[str, object]] = []
    for condition in _training_feature_conditions(valid_train):
        mask = _condition_mask(valid_train, condition)
        count = int(mask.sum())
        if count < minimum_count:
            continue
        gross = pd.to_numeric(valid_train.loc[mask, "gross_return"], errors="coerce").dropna()
        if gross.empty:
            continue
        ranked.append(
            {
                "condition": condition,
                "training_event_count_before_cooldown": count,
                "training_gross_mean_before_cooldown": float(gross.mean()),
            }
        )
    ranked.sort(
        key=lambda item: (
            float(item["training_gross_mean_before_cooldown"]),
            int(item["training_event_count_before_cooldown"]),
        ),
        reverse=True,
    )

    finalists: list[dict[str, object]] = []
    for item in ranked[:12]:
        condition = item["condition"]
        train_selected = valid_train.loc[
            _condition_mask(valid_train, condition)
        ].drop_duplicates(["trigger_ts", "code"], keep="first")
        development_selected = development.loc[
            development["gross_return"].notna()
            & _condition_mask(development, condition)
        ].drop_duplicates(["trigger_ts", "code"], keep="first")
        train_summary = _summarize_returns(train_selected, cost_rate)
        development_summary = _summarize_returns(development_selected, cost_rate)
        finalists.append(
            {
                **item,
                "training": train_summary,
                "fixed_evaluation": development_summary,
                evaluation_name: development_summary,
                "training_covers_configured_cost": (
                    isinstance(train_summary.get("gross_mean_return"), (int, float))
                    and float(train_summary["gross_mean_return"]) >= cost_rate
                ),
            }
        )

    best_training_mean = (
        max(
            float(item["training"]["gross_mean_return"])
            for item in finalists
            if isinstance(
                item.get("training", {}).get("gross_mean_return"),
                (int, float),
            )
        )
        if finalists
        else None
    )
    return {
        "id": "feature_screening",
        "status": "completed",
        "method": (
            f"只用{selection_name}生成布尔、分类和固定分位阈值的单变量条件；"
            f"按{selection_name}毛收益排序后，才在{evaluation_name}计算同一条件。"
        ),
        "selection_period_name": selection_name,
        "fixed_evaluation_period_name": evaluation_name,
        "selection_fields": [
            feature for feature in _SCREENING_FEATURES if feature in train.columns
        ],
        "forbidden_outcome_fields": [
            "volume_hit",
            "next_up",
            "joint_minute_hit",
            "next_spike_position",
            "target_minute_return",
            "forward_5m",
            "forward_10m",
            "forward_20m",
            LOW_MA5_ANALYSIS_ONLY_FIELD,
        ],
        "minimum_training_event_count": minimum_count,
        "tested_condition_count": int(len(ranked)),
        "configured_cost_rate": float(cost_rate),
        "configured_cooldown_minutes_not_applied": int(cooldown_minutes),
        "best_training_gross_mean_return": best_training_mean,
        "any_training_candidate_covers_cost": any(
            item["training_covers_configured_cost"] for item in finalists
        ),
        "top_training_selected_candidates": finalists,
        "warning": (
            f"{evaluation_name}结果只用于检验{selection_name}选出的条件，不能反过来重新挑阈值；"
            "本项在20分钟限制之前比较原始事件。"
        ),
    }


_LOW_MA5_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


def _low_ma5_baseline_source(
    frame: pd.DataFrame,
    params: dict[str, object],
) -> pd.DataFrame:
    selected = _selected_frame(frame, params)
    return selected.loc[selected["gross_return"].notna()].copy()


def _finalize_low_ma5_events(
    frame: pd.DataFrame,
    cooldown_minutes: int,
) -> pd.DataFrame:
    selected = frame.drop_duplicates(["trigger_ts", "code"], keep="first")
    return _apply_cooldown(selected, cooldown_minutes)


def _low_ma5_monthly_stability(
    frame: pd.DataFrame,
    cost_rate: float,
) -> dict[str, object]:
    rows = _summarize_by_period(
        frame,
        frequency="month",
        cost_rate=cost_rate,
    )

    def values(key: str) -> list[float]:
        numbers: list[float] = []
        for row in rows:
            value = row.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                numbers.append(float(value))
        return numbers

    def median(numbers: list[float]) -> float | None:
        if not numbers:
            return None
        ordered = sorted(numbers)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    gross_means = values("gross_mean_return")
    net_means = values("net_mean_return")
    return {
        "month_count": len(rows),
        "gross_positive_month_count": sum(value > 0 for value in gross_means),
        "net_positive_month_count": sum(value > 0 for value in net_means),
        "gross_mean_return_min": min(gross_means) if gross_means else None,
        "gross_mean_return_median": median(gross_means),
        "gross_mean_return_max": max(gross_means) if gross_means else None,
        "net_mean_return_min": min(net_means) if net_means else None,
        "net_mean_return_median": median(net_means),
        "net_mean_return_max": max(net_means) if net_means else None,
    }


def _low_ma5_metrics(
    frame: pd.DataFrame,
    cost_rate: float,
) -> dict[str, object]:
    summary = _summarize_returns(frame, cost_rate)
    metrics = {
        key: summary.get(key)
        for key in (
            "event_count",
            "gross_mean_return",
            "net_mean_return",
            "gross_up_rate",
        )
    }
    metrics["monthly_stability"] = _low_ma5_monthly_stability(
        frame,
        cost_rate,
    )
    return metrics


def _low_ma5_metric_changes(
    candidate: dict[str, object],
    baseline: dict[str, object],
) -> dict[str, object]:
    changes: dict[str, object] = {}
    candidate_count = candidate.get("event_count")
    baseline_count = baseline.get("event_count")
    if isinstance(candidate_count, int) and isinstance(baseline_count, int):
        changes["event_count_change"] = candidate_count - baseline_count
        changes["event_count_retention_rate"] = (
            float(candidate_count) / float(baseline_count)
            if baseline_count > 0
            else None
        )
    for key in ("gross_mean_return", "net_mean_return", "gross_up_rate"):
        candidate_value = candidate.get(key)
        baseline_value = baseline.get(key)
        changes[f"{key}_change"] = (
            float(candidate_value) - float(baseline_value)
            if isinstance(candidate_value, (int, float))
            and isinstance(baseline_value, (int, float))
            else None
        )
    return changes


def _low_ma5_full_day_bins(
    selection_source: pd.DataFrame,
    confirmation_source: pd.DataFrame,
    *,
    cost_rate: float,
    cooldown_minutes: int,
) -> dict[str, object]:
    field = LOW_MA5_ANALYSIS_ONLY_FIELD
    if field not in selection_source.columns or field not in confirmation_source.columns:
        return {
            "field": field,
            "status": "unavailable",
            "strategy_eligible": False,
            "can_nominate_candidate": False,
            "reason": "事件文件缺少完整日分析字段。",
        }

    selection_values = pd.to_numeric(selection_source[field], errors="coerce")
    selection_values = selection_values.loc[
        selection_values.notna() & np.isfinite(selection_values)
    ]
    confirmation_values = pd.to_numeric(
        confirmation_source[field], errors="coerce"
    )
    if selection_values.empty:
        return {
            "field": field,
            "status": "unavailable",
            "strategy_eligible": False,
            "can_nominate_candidate": False,
            "reason": "选择期完整日分析字段没有有效数值。",
        }

    boundaries = sorted(
        {
            float(selection_values.quantile(level))
            for level in _LOW_MA5_QUANTILES
        }
    )
    edges: list[float | None] = [None, *boundaries, None]
    bins: list[dict[str, object]] = []
    for index in range(len(edges) - 1):
        lower = edges[index]
        upper = edges[index + 1]

        def bin_mask(values: pd.Series) -> pd.Series:
            numeric = pd.to_numeric(values, errors="coerce")
            mask = numeric.notna() & np.isfinite(numeric)
            if lower is not None:
                mask &= numeric.gt(lower)
            if upper is not None:
                mask &= numeric.le(upper)
            return mask.fillna(False)

        selection_bin = _finalize_low_ma5_events(
            selection_source.loc[bin_mask(selection_source[field])].copy(),
            cooldown_minutes,
        )
        confirmation_bin = _finalize_low_ma5_events(
            confirmation_source.loc[bin_mask(confirmation_source[field])].copy(),
            cooldown_minutes,
        )
        bins.append(
            {
                "lower_exclusive": lower,
                "upper_inclusive": upper,
                "selection": _low_ma5_metrics(selection_bin, cost_rate),
                "confirmation": _low_ma5_metrics(confirmation_bin, cost_rate),
                "strategy_eligible": False,
                "can_nominate_candidate": False,
            }
        )

    return {
        "field": field,
        "status": "descriptive_only",
        "strategy_eligible": False,
        "can_nominate_candidate": False,
        "selection_non_null_count": int(len(selection_values)),
        "confirmation_non_null_count": int(
            (confirmation_values.notna() & np.isfinite(confirmation_values)).sum()
        ),
        "fixed_selection_quantile_boundaries": boundaries,
        "bins": bins,
        "reason": (
            "该字段使用触发时刻之后才完整可见的当日最低价或收盘价，"
            "只能说明事后分组现象，不能提名策略候选。"
        ),
    }


def _low_ma5_distance_screening(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    *,
    cost_rate: float,
    cooldown_minutes: int,
    horizon_minutes: int,
    source_candidate_id: str,
) -> dict[str, object]:
    selection_name, confirmation_name = _selection_and_evaluation_names(frames)
    selection_source = _low_ma5_baseline_source(frames[selection_name], params)
    confirmation_source = _low_ma5_baseline_source(
        frames[confirmation_name], params
    )
    selection_baseline = _finalize_low_ma5_events(
        selection_source, cooldown_minutes
    )
    confirmation_baseline = _finalize_low_ma5_events(
        confirmation_source, cooldown_minutes
    )
    selection_baseline_metrics = _low_ma5_metrics(
        selection_baseline, cost_rate
    )
    confirmation_baseline_metrics = _low_ma5_metrics(
        confirmation_baseline, cost_rate
    )
    minimum_count = max(
        100,
        int(math.ceil(len(selection_baseline) * 0.05)),
    )

    missing_safe_fields = [
        field
        for field in LOW_MA5_SAFE_FEATURES
        if field not in selection_source.columns
        or field not in confirmation_source.columns
    ]
    candidates: list[dict[str, object]] = []
    seen: set[tuple[str, str, float]] = set()
    tested_condition_count = 0
    discarded_small_sample_count = 0
    field_coverage: dict[str, object] = {}
    for field in LOW_MA5_SAFE_FEATURES:
        if field in missing_safe_fields:
            field_coverage[field] = {"status": "missing"}
            continue
        selection_values = pd.to_numeric(selection_source[field], errors="coerce")
        selection_valid = selection_values.notna() & np.isfinite(selection_values)
        confirmation_values = pd.to_numeric(
            confirmation_source[field], errors="coerce"
        )
        confirmation_valid = confirmation_values.notna() & np.isfinite(
            confirmation_values
        )
        field_coverage[field] = {
            "status": "available",
            "selection_non_null_count": int(selection_valid.sum()),
            "selection_non_null_rate": (
                float(selection_valid.mean()) if len(selection_valid) else None
            ),
            "confirmation_non_null_count": int(confirmation_valid.sum()),
            "confirmation_non_null_rate": (
                float(confirmation_valid.mean())
                if len(confirmation_valid)
                else None
            ),
        }
        valid_values = selection_values.loc[selection_valid]
        if valid_values.empty:
            continue
        for quantile_level in _LOW_MA5_QUANTILES:
            threshold = float(valid_values.quantile(quantile_level))
            for operator in ("<=", ">="):
                key = (field, operator, threshold)
                if key in seen:
                    continue
                seen.add(key)
                tested_condition_count += 1
                condition = {
                    "feature": field,
                    "operator": operator,
                    "value": threshold,
                }
                selection_events = _finalize_low_ma5_events(
                    selection_source.loc[
                        _condition_mask(selection_source, condition)
                    ].copy(),
                    cooldown_minutes,
                )
                if len(selection_events) < minimum_count:
                    discarded_small_sample_count += 1
                    continue
                confirmation_events = _finalize_low_ma5_events(
                    confirmation_source.loc[
                        _condition_mask(confirmation_source, condition)
                    ].copy(),
                    cooldown_minutes,
                )
                selection_metrics = _low_ma5_metrics(
                    selection_events, cost_rate
                )
                confirmation_metrics = _low_ma5_metrics(
                    confirmation_events, cost_rate
                )
                candidates.append(
                    {
                        "condition": condition,
                        "selection_quantile_level": quantile_level,
                        "selection": selection_metrics,
                        "confirmation": confirmation_metrics,
                        "changes_from_baseline": {
                            "selection": _low_ma5_metric_changes(
                                selection_metrics,
                                selection_baseline_metrics,
                            ),
                            "confirmation": _low_ma5_metric_changes(
                                confirmation_metrics,
                                confirmation_baseline_metrics,
                            ),
                        },
                    }
                )

    def candidate_rank_key(item: dict[str, object]) -> tuple[float, int]:
        selection = item.get("selection", {})
        if not isinstance(selection, dict):
            return -math.inf, 0
        gross_mean = selection.get("gross_mean_return")
        event_count = selection.get("event_count")
        return (
            float(gross_mean)
            if isinstance(gross_mean, (int, float))
            else -math.inf,
            int(event_count) if isinstance(event_count, int) else 0,
        )

    candidates.sort(key=candidate_rank_key, reverse=True)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["selection_rank"] = rank

    full_day_analysis = _low_ma5_full_day_bins(
        selection_source,
        confirmation_source,
        cost_rate=cost_rate,
        cooldown_minutes=cooldown_minutes,
    )
    missing_fields = list(missing_safe_fields)
    if full_day_analysis.get("status") == "unavailable":
        missing_fields.append(LOW_MA5_ANALYSIS_ONLY_FIELD)
    return {
        "id": "low_ma5_distance_screening",
        "status": "partial" if missing_fields else "completed",
        "source_candidate_id": source_candidate_id,
        "baseline_strategy_parameters": dict(params),
        "selection_period_name": selection_name,
        "confirmation_period_name": confirmation_name,
        "event_horizon_minutes": int(horizon_minutes),
        "return_column": f"forward_{horizon_minutes}m",
        "cooldown_minutes": int(cooldown_minutes),
        "processing_order": [
            "apply_current_candidate_rules",
            "drop_missing_actual_horizon_return",
            "drop_duplicate_trigger_ts_and_code",
            "apply_same_stock_day_cooldown",
        ],
        "safe_fields": list(LOW_MA5_SAFE_FEATURES),
        "forbidden_analysis_only_fields": [LOW_MA5_ANALYSIS_ONLY_FIELD],
        "fixed_selection_quantile_levels": list(_LOW_MA5_QUANTILES),
        "minimum_selection_event_count": minimum_count,
        "minimum_selection_event_count_rule": (
            "max(100, ceil(selection_baseline_event_count * 0.05))"
        ),
        "field_coverage": field_coverage,
        "baseline": {
            "selection": selection_baseline_metrics,
            "confirmation": confirmation_baseline_metrics,
        },
        "tested_condition_count": tested_condition_count,
        "eligible_condition_count": len(candidates),
        "discarded_small_sample_count": discarded_small_sample_count,
        "training_ranked_candidates": candidates,
        "full_day_descriptive_analysis": full_day_analysis,
        "missing_fields": missing_fields,
        "outcome_or_post_trigger_fields_used_for_selection": [],
        "warning": (
            f"阈值和排名只使用{selection_name}；{confirmation_name}只按原阈值检查。"
            "完整日字段包含触发后的信息，任何结果都不能用于提名候选。"
        ),
    }


def _session_boundary_mask(
    frame: pd.DataFrame,
    horizon_minutes: int,
) -> pd.Series:
    if "session_no" not in frame.columns or "trigger_position" not in frame.columns:
        return pd.Series(False, index=frame.index, dtype=bool)
    session_no = pd.to_numeric(frame["session_no"], errors="coerce")
    trigger_position = pd.to_numeric(frame["trigger_position"], errors="coerce")
    session_upper = session_no.map({0: 120, 1: 240})
    return (
        trigger_position.add(int(horizon_minutes)).ge(session_upper).fillna(False)
    )


def _prepare_selected_horizon_frame(
    selected: pd.DataFrame,
    *,
    return_column: str,
    horizon_minutes: int,
    cooldown_minutes: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    if return_column not in selected.columns:
        raise ValueError(f"分钟事件文件缺少收益字段: {return_column}")

    source = selected.copy()
    numeric_return = pd.to_numeric(source[return_column], errors="coerce")
    valid_return = numeric_return.notna() & np.isfinite(numeric_return)
    boundary = _session_boundary_mask(source, horizon_minutes)
    invalid_return = ~valid_return
    boundary_invalid = invalid_return & boundary
    other_invalid = invalid_return & ~boundary

    valid = source.loc[valid_return].copy()
    valid["gross_return"] = numeric_return.loc[valid_return].astype(float)
    before_deduplication_count = int(len(valid))
    valid = valid.drop_duplicates(["trigger_ts", "code"], keep="first")
    before_cooldown_count = int(len(valid))
    prepared = _apply_cooldown(valid, cooldown_minutes)
    final_count = int(len(prepared))
    return prepared, {
        "selected_event_count_before_return_availability": int(len(source)),
        "valid_return_event_count": before_deduplication_count,
        "invalid_return_event_count": int(invalid_return.sum()),
        "boundary_invalid_return_count": int(boundary_invalid.sum()),
        "other_invalid_return_count": int(other_invalid.sum()),
        "duplicate_removed_count": before_deduplication_count - before_cooldown_count,
        "before_cooldown_count": before_cooldown_count,
        "cooldown_removed_count": before_cooldown_count - final_count,
        "final_event_count": final_count,
    }


def _prepare_strategy_horizon_frame(
    frame: pd.DataFrame,
    params: dict[str, object],
    *,
    return_column: str,
    horizon_minutes: int,
    cooldown_minutes: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    return _prepare_selected_horizon_frame(
        _selected_frame(frame, params),
        return_column=return_column,
        horizon_minutes=horizon_minutes,
        cooldown_minutes=cooldown_minutes,
    )


def _return_only_summary(
    frame: pd.DataFrame,
    return_column: str,
    cost_rate: float,
) -> dict[str, object]:
    source = frame.copy()
    source["gross_return"] = pd.to_numeric(source[return_column], errors="coerce")
    summary = _summarize_returns(source, cost_rate)
    for key in (
        "label_column",
        "label_observation_count",
        "label_positive_count",
        "label_positive_rate",
    ):
        summary.pop(key, None)
    return summary


def _return_column_by_year(
    frame: pd.DataFrame,
    return_column: str,
    cost_rate: float,
) -> list[dict[str, object]]:
    source = frame.copy()
    source["gross_return"] = pd.to_numeric(source[return_column], errors="coerce")
    rows = _summarize_by_period(source, frequency="year", cost_rate=cost_rate)
    for row in rows:
        for key in (
            "label_column",
            "label_observation_count",
            "label_positive_count",
            "label_positive_rate",
        ):
            row.pop(key, None)
    return rows


def _event_response_selection_conditions(
    params: dict[str, object],
) -> list[dict[str, object]]:
    if "generic_selection_conditions" in params:
        return [dict(item) for item in _normalise_generic_selection_conditions(params)]

    conditions: list[dict[str, object]] = []
    legacy_fields = (
        ("minimum_past_up_count", "past_up_count", ">="),
        ("required_no_early_spike", "no_early_spike", "=="),
        ("required_pre_window_up", "pre_window_up", "=="),
        ("pulse5_return_threshold", "pulse5_return", "<="),
        ("pulse1_position_threshold", "pulse1_position", ">="),
    )
    for parameter, feature, operator in legacy_fields:
        value = params.get(parameter)
        if isinstance(value, bool) or (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            conditions.append(
                {"feature": feature, "operator": operator, "value": value}
            )
    return conditions


_SELECTION_CONDITION_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(==|<=|>=)\s*"
    r"(true|false|[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][-+]?\d+)?)\s*$",
    flags=re.IGNORECASE,
)


def _parse_requested_selection_condition(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        candidate = dict(raw)
    elif isinstance(raw, str):
        match = _SELECTION_CONDITION_PATTERN.fullmatch(raw)
        if match is None:
            raise ValueError(
                "selection_condition 必须写成 feature<=数字、feature>=数字、"
                "feature==数字或布尔字段==true/false"
            )
        value_text = match.group(3)
        if value_text.lower() in {"true", "false"}:
            value: object = value_text.lower() == "true"
        else:
            value = float(value_text)
        candidate = {
            "feature": match.group(1),
            "operator": match.group(2),
            "value": value,
        }
    else:
        raise ValueError("selection_condition 必须是单条条件字符串或字典")

    conditions = _normalise_generic_selection_conditions(
        {"generic_selection_conditions": [candidate]}
    )
    return conditions[0]


def _resolve_requested_diagnostic_rule(
    params: dict[str, object],
    parameters: dict[str, object],
    *,
    base_source: str,
) -> tuple[
    dict[str, object] | None,
    str,
    list[dict[str, object]],
    list[str],
]:
    request_source = "diagnostic_request.parameters.selection_condition"
    if "selection_conditions" in parameters and "selection_condition" not in parameters:
        return None, request_source, [], [
            "不支持 parameters.selection_conditions；单条覆盖必须使用 selection_condition。"
        ]
    if "selection_condition" not in parameters:
        return (
            dict(params),
            base_source,
            _event_response_selection_conditions(params),
            [],
        )
    try:
        condition = _parse_requested_selection_condition(
            parameters.get("selection_condition")
        )
    except ValueError as exc:
        return None, request_source, [], [str(exc)]

    overridden = dict(params)
    overridden["generic_selection_conditions"] = [condition]
    return overridden, request_source, [condition], []


def _event_response_candidate_rule(
    params: dict[str, object],
    parameters: dict[str, object],
    *,
    current_candidate_id: str,
    candidate_records: object,
) -> tuple[dict[str, object] | None, dict[str, object], list[str]]:
    problems: list[str] = []
    raw_candidate_ids = parameters.get("candidate_ids")
    if raw_candidate_ids is None:
        requested_candidate_ids = (
            [current_candidate_id] if current_candidate_id else []
        )
    elif isinstance(raw_candidate_ids, list):
        requested_candidate_ids = []
        for value in raw_candidate_ids:
            candidate_id = str(value).strip()
            if candidate_id and candidate_id not in requested_candidate_ids:
                requested_candidate_ids.append(candidate_id)
    else:
        requested_candidate_ids = []
        problems.append("candidate_ids 必须是候选编号列表。")

    if len(requested_candidate_ids) > 1:
        problems.append("event_response_curve 一次只能按一个候选的规则计算。")
        target_candidate_id = ""
    else:
        target_candidate_id = (
            requested_candidate_ids[0]
            if requested_candidate_ids
            else current_candidate_id
        )

    rule_params: dict[str, object] | None = None
    rule_source = ""
    if target_candidate_id == current_candidate_id or not target_candidate_id:
        rule_params = dict(params)
        rule_source = "strategy_result.params"
    elif isinstance(candidate_records, list):
        for record in reversed(candidate_records):
            if not isinstance(record, dict):
                continue
            if str(record.get("candidate_id", "")).strip() != target_candidate_id:
                continue
            if str(record.get("period_kind", "development")) != "development":
                continue
            stored = record.get("strategy_params")
            if isinstance(stored, dict):
                rule_params = dict(stored)
                rule_source = "candidate_records.strategy_params"
                break

    if rule_params is None and target_candidate_id:
        problems.append(
            f"找不到候选 {target_candidate_id} 的 strategy_params，不能改用其他候选规则。"
        )

    scope = {
        "current_candidate_id": current_candidate_id,
        "requested_candidate_ids": requested_candidate_ids,
        "evaluated_candidate_id": target_candidate_id if rule_params is not None else "",
        "rule_source": rule_source,
        "rule_found": rule_params is not None,
    }
    return rule_params, scope, problems


def _raw_minute_data_directory() -> Path:
    for name in ("QUANTA_RAW_MINUTE_DATA_DIR", "QUANTA_RAW_MINUTE_DIR"):
        value = os.getenv(name, "").strip()
        if value:
            return Path(value).expanduser()
    return DEFAULT_RAW_MINUTE_DATA_DIR


def _diagnostic_minute_positions(timestamps: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(timestamps, errors="coerce")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    minutes = parsed.dt.hour * 60 + parsed.dt.minute
    positions = pd.Series(-1, index=timestamps.index, dtype="int16")
    morning = minutes.between(9 * 60 + 31, 11 * 60 + 30)
    afternoon = minutes.between(13 * 60 + 1, 15 * 60)
    positions.loc[morning] = (minutes.loc[morning] - (9 * 60 + 31)).astype(
        "int16"
    )
    positions.loc[afternoon] = (
        120 + minutes.loc[afternoon] - (13 * 60 + 1)
    ).astype("int16")
    return positions


def _read_raw_minute_prices(
    path: Path,
    *,
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pq.read_schema(path)
    required = {"datetime", "open", "close"}
    missing = required - set(schema.names)
    if missing:
        raise ValueError("原始分钟文件缺少字段: " + ", ".join(sorted(missing)))
    datetime_type = schema.field("datetime").type
    if pa.types.is_timestamp(datetime_type) or pa.types.is_date(datetime_type):
        lower: object = start.to_pydatetime()
        upper: object = end_exclusive.to_pydatetime()
    else:
        lower = start.strftime("%Y-%m-%d 00:00:00")
        upper = end_exclusive.strftime("%Y-%m-%d 00:00:00")
    table = pq.read_table(
        path,
        columns=["datetime", "open", "close"],
        filters=[("datetime", ">=", lower), ("datetime", "<", upper)],
        use_threads=False,
    )
    frame = table.to_pandas()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    if getattr(frame["datetime"].dt, "tz", None) is not None:
        frame["datetime"] = frame["datetime"].dt.tz_localize(None)
    frame = frame.dropna(subset=["datetime"])
    frame["_date"] = frame["datetime"].dt.normalize()
    frame["_position"] = _diagnostic_minute_positions(frame["datetime"])
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.loc[frame["_position"].between(0, 239)].drop_duplicates(
        ["_date", "_position"], keep="last"
    )


def _raw_anchor_returns_for_code(
    code: str,
    events: pd.DataFrame,
    *,
    raw_directory: Path,
    holding_minutes: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if not code or any(token in code for token in ("/", "\\", ":")):
        raise ValueError(f"股票代码不能对应分钟文件: {code!r}")
    path = raw_directory / f"{code}.parquet"
    if not path.is_file():
        return pd.DataFrame(), {"missing_file": str(path)}

    anchor_times = pd.concat(
        [
            pd.to_datetime(events["trigger_ts"], errors="coerce"),
            pd.to_datetime(events["predicted_ts"], errors="coerce"),
        ],
        ignore_index=True,
    ).dropna()
    if anchor_times.empty:
        return pd.DataFrame(), {"loaded_file": str(path), "row_count": 0}
    start = pd.Timestamp(anchor_times.min()).normalize()
    end_exclusive = pd.Timestamp(anchor_times.max()).normalize() + pd.Timedelta(days=1)
    prices = _read_raw_minute_prices(path, start=start, end_exclusive=end_exclusive)
    indexed = prices.set_index(["_date", "_position"])[["open", "close"]]

    output = events[["_anchor_row_id"]].copy()
    mismatch_counts: dict[str, int] = {}
    for anchor_name, timestamp_field, position_field in (
        ("trigger", "trigger_ts", "trigger_position"),
        ("predicted", "predicted_ts", "predicted_position"),
    ):
        timestamps = pd.to_datetime(events[timestamp_field], errors="coerce")
        positions = _diagnostic_minute_positions(timestamps)
        stored_positions = pd.to_numeric(events[position_field], errors="coerce")
        comparable = positions.ge(0) & stored_positions.notna()
        mismatch_counts[f"{anchor_name}_timestamp_position_mismatch_count"] = int(
            (positions.loc[comparable].astype(float) != stored_positions.loc[comparable]).sum()
        )

        upper = np.where(positions.to_numpy() < 120, 120, 240)
        eligible = positions.ge(0).to_numpy() & (
            positions.to_numpy() + holding_minutes < upper
        )
        dates = timestamps.dt.normalize()
        entry_keys = pd.MultiIndex.from_arrays(
            [dates, positions + 1], names=["_date", "_position"]
        )
        exit_keys = pd.MultiIndex.from_arrays(
            [dates, positions + holding_minutes], names=["_date", "_position"]
        )
        entry = pd.to_numeric(indexed["open"].reindex(entry_keys), errors="coerce").to_numpy()
        exit_price = pd.to_numeric(
            indexed["close"].reindex(exit_keys), errors="coerce"
        ).to_numpy()
        valid = eligible & np.isfinite(entry) & np.isfinite(exit_price) & (entry > 0)
        returns = np.full(len(events), np.nan, dtype=float)
        returns[valid] = exit_price[valid] / entry[valid] - 1.0
        output[f"_{anchor_name}_anchor_5m"] = returns
        output[f"_{anchor_name}_boundary_valid"] = eligible

    return output, {
        "loaded_file": str(path),
        "raw_row_count": int(len(prices)),
        **mismatch_counts,
    }


def _load_raw_anchor_returns(
    selected: pd.DataFrame,
    *,
    raw_directory: Path,
    holding_minutes: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    source = selected.reset_index(drop=True).copy()
    source["_anchor_row_id"] = np.arange(len(source), dtype=np.int64)
    source["_trigger_anchor_5m"] = np.nan
    source["_predicted_anchor_5m"] = np.nan
    source["_trigger_boundary_valid"] = False
    source["_predicted_boundary_valid"] = False
    if source.empty:
        return source, {
            "status": "completed",
            "requested_stock_file_count": 0,
            "loaded_stock_file_count": 0,
            "missing_stock_file_count": 0,
            "read_error_count": 0,
            "worker_count": 0,
        }

    raw_workers = os.getenv("QUANTA_DIAGNOSTIC_RAW_PRICE_WORKERS", "4").strip()
    try:
        worker_count = max(1, min(int(raw_workers), 8))
    except ValueError:
        worker_count = 4
    groups = {
        str(code): group.copy()
        for code, group in source.groupby("code", sort=False, dropna=False)
    }
    worker_count = min(worker_count, max(len(groups), 1))
    missing_files: list[str] = []
    read_errors: list[str] = []
    loaded_count = 0
    raw_row_count = 0
    trigger_mismatches = 0
    predicted_mismatches = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _raw_anchor_returns_for_code,
                code,
                group,
                raw_directory=raw_directory,
                holding_minutes=holding_minutes,
            ): (code, group)
            for code, group in groups.items()
        }
        for future in as_completed(futures):
            code, group = futures[future]
            try:
                values, info = future.result()
            except Exception as exc:  # 单只股票失败不应丢掉其他股票的结果
                read_errors.append(f"{code}: {type(exc).__name__}: {exc}")
                continue
            missing_file = str(info.get("missing_file", "")).strip()
            if missing_file:
                missing_files.append(missing_file)
                continue
            loaded_count += 1
            raw_row_count += int(info.get("raw_row_count", 0))
            trigger_mismatches += int(
                info.get("trigger_timestamp_position_mismatch_count", 0)
            )
            predicted_mismatches += int(
                info.get("predicted_timestamp_position_mismatch_count", 0)
            )
            if values.empty:
                continue
            row_ids = values["_anchor_row_id"].astype(int).to_numpy()
            for column in (
                "_trigger_anchor_5m",
                "_predicted_anchor_5m",
                "_trigger_boundary_valid",
                "_predicted_boundary_valid",
            ):
                source.loc[row_ids, column] = values[column].to_numpy()

    status = "partial" if missing_files or read_errors else "completed"
    return source, {
        "status": status,
        "raw_minute_directory": str(raw_directory),
        "requested_stock_file_count": int(len(groups)),
        "loaded_stock_file_count": int(loaded_count),
        "missing_stock_file_count": int(len(missing_files)),
        "missing_stock_files": missing_files[:50],
        "read_error_count": int(len(read_errors)),
        "read_errors": read_errors[:50],
        "raw_price_row_count": int(raw_row_count),
        "worker_count": int(worker_count),
        "trigger_timestamp_position_mismatch_count": int(trigger_mismatches),
        "predicted_timestamp_position_mismatch_count": int(predicted_mismatches),
    }


def _anchor_5m_period_summary(
    frame: pd.DataFrame,
    *,
    cost_rate: float,
    report_by_year: bool,
) -> dict[str, object]:
    trigger_column = "_trigger_anchor_5m"
    predicted_column = "_predicted_anchor_5m"
    trigger = pd.to_numeric(frame[trigger_column], errors="coerce")
    predicted = pd.to_numeric(frame[predicted_column], errors="coerce")
    paired = trigger.notna() & predicted.notna()
    difference = predicted.loc[paired] - trigger.loc[paired]
    stored_comparison: dict[str, object] = {"comparison_count": 0}
    if "forward_5m" in frame.columns:
        stored = pd.to_numeric(frame["forward_5m"], errors="coerce")
        comparable = trigger.notna() & stored.notna()
        absolute = (trigger.loc[comparable] - stored.loc[comparable]).abs()
        stored_comparison = {
            "comparison_count": int(comparable.sum()),
            "maximum_absolute_difference": (
                float(absolute.max()) if len(absolute) else None
            ),
            "difference_over_1e_12_count": int(absolute.gt(1e-12).sum()),
        }
    result: dict[str, object] = {
        "selected_event_count_before_return_availability": int(len(frame)),
        "trigger_anchor_5m": _return_only_summary(
            frame, trigger_column, cost_rate
        ),
        "predicted_anchor_5m": _return_only_summary(
            frame, predicted_column, cost_rate
        ),
        "paired_event_count": int(paired.sum()),
        "predicted_minus_trigger_5m": _simple_return_summary(difference),
        "trigger_raw_vs_stored_forward_5m": stored_comparison,
        "unavailable_counts": {
            "trigger_session_boundary": int(
                (~frame["_trigger_boundary_valid"].astype(bool)).sum()
            ),
            "predicted_session_boundary": int(
                (~frame["_predicted_boundary_valid"].astype(bool)).sum()
            ),
            "trigger_missing_exact_price": int(
                (frame["_trigger_boundary_valid"].astype(bool) & trigger.isna()).sum()
            ),
            "predicted_missing_exact_price": int(
                (frame["_predicted_boundary_valid"].astype(bool) & predicted.isna()).sum()
            ),
        },
    }
    by_year: list[dict[str, object]] = []
    if report_by_year:
        years = pd.to_datetime(frame["date"], errors="coerce").dt.year
        for year in sorted(int(value) for value in years.dropna().unique()):
            yearly = _anchor_5m_period_summary(
                frame.loc[years.eq(year)],
                cost_rate=cost_rate,
                report_by_year=False,
            )
            yearly["year"] = year
            by_year.append(yearly)
    result["by_year"] = by_year
    return result


def _event_response_curve(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    parameters: dict[str, object],
    *,
    cost_rate: float,
    cooldown_minutes: int,
    current_candidate_id: str,
    candidate_records: object,
) -> dict[str, object]:
    rule_params, candidate_scope, problems = _event_response_candidate_rule(
        params,
        parameters,
        current_candidate_id=current_candidate_id,
        candidate_records=candidate_records,
    )
    base_rule_source = str(candidate_scope.get("rule_source", ""))
    actual_selection_conditions: list[dict[str, object]] = []
    rule_source = base_rule_source
    if rule_params is not None:
        (
            rule_params,
            rule_source,
            actual_selection_conditions,
            rule_problems,
        ) = _resolve_requested_diagnostic_rule(
            rule_params,
            parameters,
            base_source=base_rule_source,
        )
        problems.extend(rule_problems)
        candidate_scope["base_rule_found"] = bool(candidate_scope.get("rule_found"))
        candidate_scope["base_rule_source"] = base_rule_source
        candidate_scope["rule_source"] = rule_source
        candidate_scope["rule_found"] = rule_params is not None
        candidate_scope["rule_applied"] = rule_params is not None
    requested_cooldown = parameters.get("cooldown_minutes", cooldown_minutes)
    if (
        isinstance(requested_cooldown, int)
        and not isinstance(requested_cooldown, bool)
        and requested_cooldown >= 0
    ):
        applied_cooldown = int(requested_cooldown)
    else:
        applied_cooldown = int(cooldown_minutes)
        problems.append(
            f"cooldown_minutes 无效，改用实验设置的 {applied_cooldown} 分钟。"
        )

    anchor_requested = "anchor_field" in parameters
    anchor_field = str(parameters.get("anchor_field", "")).strip()
    report_by_year = parameters.get("report_by_year", True) is not False
    if rule_params is None:
        return {
            "id": "event_response_curve",
            "status": "partial",
            "periods": {},
            "candidate_scope": candidate_scope,
            "anchor_comparison": {
                "status": "partial" if anchor_requested else "not_requested",
                "reason": problems[-1] if problems else "找不到候选规则。",
            },
            "configured_cooldown_minutes": int(cooldown_minutes),
            "applied_cooldown_minutes": applied_cooldown,
            "rule_source": rule_source,
            "selection_fields_only": [],
            "selection_conditions": actual_selection_conditions,
            "outcome_fields_used_for_selection": [],
            "unsupported_requests": problems,
        }

    periods: dict[str, object] = {}
    selected_frames: dict[str, pd.DataFrame] = {}
    missing_horizons: set[int] = set()
    for period_name, frame in frames.items():
        selected_before_returns = _selected_frame(frame, rule_params)
        horizon_results: dict[str, object] = {}
        prepared_by_horizon: dict[int, pd.DataFrame] = {}
        for minutes in (5, 10, 20):
            column = f"forward_{minutes}m"
            if column not in selected_before_returns.columns:
                missing_horizons.add(minutes)
                continue
            selected, flow_counts = _prepare_selected_horizon_frame(
                selected_before_returns,
                return_column=column,
                horizon_minutes=minutes,
                cooldown_minutes=applied_cooldown,
            )
            prepared_by_horizon[minutes] = selected
            horizon_results[f"{minutes}m"] = {
                "return_column": column,
                "flow_counts": flow_counts,
                "overall": _return_only_summary(selected, column, cost_rate),
                "by_year": _return_column_by_year(selected, column, cost_rate),
            }
        selected_frames[period_name] = prepared_by_horizon.get(
            5,
            selected_before_returns.drop_duplicates(
                ["trigger_ts", "code"], keep="first"
            ),
        )

        paired_comparisons: dict[str, object] = {}
        for left_minutes, right_minutes in ((5, 10), (5, 20), (10, 20)):
            left_column = f"forward_{left_minutes}m"
            right_column = f"forward_{right_minutes}m"
            if (
                left_column not in selected_before_returns.columns
                or right_column not in selected_before_returns.columns
            ):
                continue
            left_return = pd.to_numeric(
                selected_before_returns[left_column], errors="coerce"
            )
            right_return = pd.to_numeric(
                selected_before_returns[right_column], errors="coerce"
            )
            left_valid = left_return.notna() & np.isfinite(left_return)
            right_valid = right_return.notna() & np.isfinite(right_return)
            paired_valid = left_valid & right_valid
            paired = selected_before_returns.loc[paired_valid].copy()
            before_deduplication_count = int(len(paired))
            paired = paired.drop_duplicates(
                ["trigger_ts", "code"], keep="first"
            )
            before_cooldown_count = int(len(paired))
            paired = _apply_cooldown(paired, applied_cooldown)
            paired_count = int(len(paired))
            difference = (
                pd.to_numeric(paired[right_column], errors="coerce")
                - pd.to_numeric(paired[left_column], errors="coerce")
            )
            paired_comparisons[f"{left_minutes}m_vs_{right_minutes}m"] = {
                "left_horizon_minutes": left_minutes,
                "right_horizon_minutes": right_minutes,
                "flow_counts": {
                    "selected_event_count_before_return_availability": int(
                        len(selected_before_returns)
                    ),
                    "left_invalid_return_count": int((~left_valid).sum()),
                    "right_invalid_return_count": int((~right_valid).sum()),
                    "either_invalid_return_count": int((~paired_valid).sum()),
                    "both_valid_before_deduplication_count": before_deduplication_count,
                    "duplicate_removed_count": (
                        before_deduplication_count - before_cooldown_count
                    ),
                    "before_cooldown_count": before_cooldown_count,
                    "cooldown_removed_count": before_cooldown_count - paired_count,
                    "paired_event_count": paired_count,
                },
                "left": _return_only_summary(paired, left_column, cost_rate),
                "right": _return_only_summary(paired, right_column, cost_rate),
                "right_minus_left": _simple_return_summary(difference),
            }
        periods[period_name] = {
            "selected_event_count_before_return_availability": int(
                len(selected_before_returns)
            ),
            "horizons": horizon_results,
            "paired_comparisons": paired_comparisons,
        }

    anchor_comparison: dict[str, object] = {"status": "not_requested"}
    if anchor_requested:
        anchor_problems: list[str] = []
        comparison_anchor = str(
            parameters.get("comparison_anchor_field", "trigger_ts")
        ).strip()
        entry_offset = parameters.get("entry_offset_minutes", 1)
        requested_horizons = parameters.get("return_horizons_minutes", [5])
        if anchor_field != "predicted_ts":
            anchor_problems.append(
                "原始分钟价格比较目前只支持 anchor_field=predicted_ts。"
            )
        if comparison_anchor != "trigger_ts":
            anchor_problems.append(
                "原始分钟价格比较目前只支持 comparison_anchor_field=trigger_ts。"
            )
        if entry_offset != 1:
            anchor_problems.append("原始分钟价格比较目前只支持下一分钟开盘进入。")
        if not isinstance(requested_horizons, list) or 5 not in requested_horizons:
            anchor_problems.append("原始分钟价格比较需要请求5分钟收益。")
        if 5 in missing_horizons:
            anchor_problems.append(
                "分钟事件文件缺少 forward_5m，无法按正式回测顺序选出原始价格比较样本。"
            )

        raw_directory = _raw_minute_data_directory()
        if not raw_directory.is_dir():
            anchor_problems.append(f"找不到原始分钟目录: {raw_directory}")
        required_fields = {
            "date",
            "code",
            "trigger_ts",
            "predicted_ts",
            "trigger_position",
            "predicted_position",
        }
        missing_anchor_fields = sorted(
            {
                field
                for selected in selected_frames.values()
                for field in required_fields - set(selected.columns)
            }
        )
        if missing_anchor_fields:
            anchor_problems.append(
                "分钟事件文件缺少锚点字段: " + ", ".join(missing_anchor_fields)
            )

        if anchor_problems:
            problems.extend(anchor_problems)
            anchor_comparison = {
                "status": "partial",
                "anchor_field": anchor_field,
                "comparison_anchor_field": comparison_anchor,
                "return_horizon_minutes": 5,
                "problems": anchor_problems,
            }
        else:
            combined_parts: list[pd.DataFrame] = []
            for period_name, selected in selected_frames.items():
                part = selected.copy()
                part["_diagnostic_period"] = period_name
                combined_parts.append(part)
            combined = pd.concat(combined_parts, ignore_index=True)
            priced, raw_info = _load_raw_anchor_returns(
                combined,
                raw_directory=raw_directory,
                holding_minutes=5,
            )
            anchor_periods = {
                period_name: _anchor_5m_period_summary(
                    priced.loc[priced["_diagnostic_period"].eq(period_name)],
                    cost_rate=cost_rate,
                    report_by_year=report_by_year,
                )
                for period_name in selected_frames
            }
            anchor_comparison = {
                "status": str(raw_info["status"]),
                "anchor_field": "predicted_ts",
                "comparison_anchor_field": "trigger_ts",
                "entry_offset_minutes": 1,
                "return_horizon_minutes": 5,
                "return_definition": (
                    "各锚点下一交易分钟的开盘价进入，锚点后第5个交易分钟的收盘价退出；"
                    "上午和下午分别计算，不跨午休或交易日。"
                ),
                "price_adjustment": "前复权",
                "periods": anchor_periods,
                "raw_price_read": raw_info,
            }
            if raw_info["status"] == "partial":
                problems.append("部分股票的原始分钟文件缺失或读取失败。")

    return {
        "id": "event_response_curve",
        "status": (
            "partial"
            if missing_horizons or problems or anchor_comparison["status"] == "partial"
            else "completed"
        ),
        "periods": periods,
        "candidate_scope": candidate_scope,
        "anchor_comparison": anchor_comparison,
        "requested_horizons_minutes": [5, 10, 20],
        "missing_horizons_minutes": sorted(missing_horizons),
        "configured_cooldown_minutes": int(cooldown_minutes),
        "applied_cooldown_minutes": applied_cooldown,
        "rule_source": rule_source,
        "selection_fields_only": _strategy_selection_fields(rule_params),
        "selection_conditions": actual_selection_conditions,
        "outcome_fields_used_for_selection": [],
        "unsupported_requests": problems,
        "note": (
            "三个期限不参与规则筛选；每个期限先删除该期限无收益事件，再按同股同刻去重并执行冷却限制。"
            "paired_comparisons 只比较两边收益都存在的同一批事件。"
        ),
    }


def _nullable_boolean(frame: pd.DataFrame, column: str) -> pd.Series:
    result = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    if column not in frame.columns:
        return result
    source = frame[column]
    if pd.api.types.is_bool_dtype(source.dtype):
        return source.astype("boolean")
    numeric = pd.to_numeric(source, errors="coerce")
    numeric_mask = numeric.isin([0, 1])
    result.loc[numeric_mask] = numeric.loc[numeric_mask].astype(bool)
    text = source.astype("string").str.strip().str.lower()
    result.loc[text.eq("true")] = True
    result.loc[text.eq("false")] = False
    return result


def _boolean_rate(values: pd.Series) -> dict[str, object]:
    valid = values.dropna().astype(bool)
    positive_count = int(valid.sum())
    observation_count = int(len(valid))
    return {
        "observation_count": observation_count,
        "positive_count": positive_count,
        "rate": (
            float(positive_count / observation_count) if observation_count else None
        ),
    }


def _next_pulse_period_summary(frame: pd.DataFrame) -> dict[str, object]:
    volume_hit = _nullable_boolean(frame, "volume_hit")
    next_up = _nullable_boolean(frame, "next_up")
    stored_joint = _nullable_boolean(frame, "joint_minute_hit")

    conditional_mask = volume_hit.eq(True) & next_up.notna()
    conditional_values = next_up.loc[conditional_mask]
    computed_joint = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    computed_available = volume_hit.notna() & next_up.notna()
    computed_joint.loc[computed_available] = (
        volume_hit.loc[computed_available].astype(bool)
        & next_up.loc[computed_available].astype(bool)
    )
    comparable = stored_joint.notna() & computed_joint.notna()
    mismatch_count = int(
        (
            stored_joint.loc[comparable].astype(bool)
            != computed_joint.loc[comparable].astype(bool)
        ).sum()
    )
    return {
        "event_count": int(len(frame)),
        "volume_hit": _boolean_rate(volume_hit),
        "next_up": _boolean_rate(next_up),
        "next_up_given_volume_hit": _boolean_rate(conditional_values),
        "joint": _boolean_rate(stored_joint),
        "computed_joint": _boolean_rate(computed_joint),
        "stored_joint_comparison_count": int(comparable.sum()),
        "stored_joint_mismatch_count": mismatch_count,
    }


_NEXT_PULSE_RETURN_COLUMNS = (
    "target_minute_return",
    "forward_5m",
    "forward_10m",
    "forward_20m",
)


def _simple_return_summary(values: pd.Series) -> dict[str, object]:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.loc[numeric.notna() & np.isfinite(numeric)]
    return {
        "sample_count": int(len(valid)),
        "mean_return": float(valid.mean()) if len(valid) else None,
        "median_return": float(valid.median()) if len(valid) else None,
        "up_rate": float(valid.gt(0).mean()) if len(valid) else None,
    }


def _volume_hit_return_groups(frame: pd.DataFrame) -> dict[str, object]:
    volume_hit = _nullable_boolean(frame, "volume_hit")
    groups: dict[str, object] = {}
    for label, expected in (("volume_hit_true", True), ("volume_hit_false", False)):
        group = frame.loc[volume_hit.eq(expected)].copy()
        returns: dict[str, object] = {}
        for column in _NEXT_PULSE_RETURN_COLUMNS:
            values = (
                group[column]
                if column in group.columns
                else pd.Series(dtype=float)
            )
            returns[column] = _simple_return_summary(values)
        groups[label] = {
            "group_event_count": int(len(group)),
            "returns": returns,
        }
    return {
        "groups": groups,
        "volume_hit_unknown_event_count": int(volume_hit.isna().sum()),
    }


def _volume_hit_return_comparison(
    frame: pd.DataFrame,
    *,
    report_by_year: bool,
) -> dict[str, object]:
    result = _volume_hit_return_groups(frame)
    by_year: list[dict[str, object]] = []
    if report_by_year:
        years = pd.to_datetime(frame["date"], errors="coerce").dt.year
        for year in sorted(int(value) for value in years.dropna().unique()):
            row = {"year": year}
            row.update(_volume_hit_return_groups(frame.loc[years.eq(year)]))
            by_year.append(row)
    result["by_year"] = by_year
    return result


def _next_pulse_mechanism(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    parameters: dict[str, object],
    *,
    cooldown_minutes: int,
    horizon_minutes: int,
    current_candidate_id: str,
) -> dict[str, object]:
    raw_candidate_ids = parameters.get("candidate_ids")
    requested_candidate_ids: list[str] = []
    unsupported_requests: list[str] = []
    if raw_candidate_ids is None:
        requested_candidate_ids = [current_candidate_id] if current_candidate_id else []
    elif isinstance(raw_candidate_ids, list):
        for value in raw_candidate_ids:
            candidate_id = str(value).strip()
            if candidate_id and candidate_id not in requested_candidate_ids:
                requested_candidate_ids.append(candidate_id)
    else:
        unsupported_requests.append("candidate_ids 必须是候选编号列表。")

    unsupported_candidate_ids = [
        candidate_id
        for candidate_id in requested_candidate_ids
        if candidate_id != current_candidate_id
    ]
    if unsupported_candidate_ids:
        unsupported_requests.append(
            "当前诊断只能使用当前候选的策略条件，不能重建这些候选："
            + ", ".join(unsupported_candidate_ids)
        )
    evaluate_current = not requested_candidate_ids or current_candidate_id in requested_candidate_ids

    requested_cooldown = parameters.get("cooldown_minutes", cooldown_minutes)
    if (
        isinstance(requested_cooldown, int)
        and not isinstance(requested_cooldown, bool)
        and requested_cooldown >= 0
    ):
        applied_cooldown = int(requested_cooldown)
    else:
        applied_cooldown = int(cooldown_minutes)
        unsupported_requests.append(
            f"cooldown_minutes 无效，改用实验设置的 {applied_cooldown} 分钟。"
        )

    report_by_year = parameters.get("report_by_year", True) is not False
    compare_scheduled_entry = (
        parameters.get("compare_scheduled_entry_with_trigger_entry") is True
    )
    anchor_field = str(parameters.get("anchor_field", "predicted_ts")).strip()
    holding_minutes = parameters.get("holding_minutes", 5)
    requested_anchor_evaluation: dict[str, object]
    if compare_scheduled_entry:
        requested_anchor_evaluation = {
            "status": "unavailable",
            "anchor_field": anchor_field,
            "holding_minutes": holding_minutes,
            "requested_return_definition": (
                f"{anchor_field} 下一分钟开盘买入，持有 {holding_minutes} 分钟"
            ),
            "reason": "事件表没有保存该进场点的开盘价和对应离场价，不能从现有结果列反推。",
            "required_data": [
                "原始分钟开盘价",
                "原始分钟收盘价",
                anchor_field,
            ],
        }
        unsupported_requests.append(
            f"无法计算以 {anchor_field} 下一分钟开盘价为起点的收益；需要读取原始分钟价格。"
        )
    else:
        requested_anchor_evaluation = {"status": "not_requested"}

    candidate_scope = {
        "current_candidate_id": current_candidate_id,
        "requested_candidate_ids": requested_candidate_ids,
        "evaluated_candidate_ids": [current_candidate_id] if evaluate_current else [],
        "unsupported_candidate_ids": unsupported_candidate_ids,
    }
    if not evaluate_current:
        return {
            "id": "next_pulse_mechanism",
            "status": "unsupported",
            "periods": {},
            "candidate_scope": candidate_scope,
            "requested_anchor_evaluation": requested_anchor_evaluation,
            "unsupported_requests": unsupported_requests,
            "selection_fields_only": _strategy_selection_fields(params),
            "outcome_fields_used_for_selection": [],
            "post_event_grouping_warning": (
                "volume_hit 是事件发生后的结果，只能用于分组评价，不能作为策略信号。"
            ),
        }

    periods: dict[str, object] = {}
    missing_fields: set[str] = set()
    current_return_column = f"forward_{horizon_minutes}m"
    required = {
        "volume_hit",
        "next_up",
        "joint_minute_hit",
        *_NEXT_PULSE_RETURN_COLUMNS,
    }
    for period_name, frame in frames.items():
        missing_fields.update(required - set(frame.columns))
        selected, flow_counts = _prepare_strategy_horizon_frame(
            frame,
            params,
            return_column=current_return_column,
            horizon_minutes=horizon_minutes,
            cooldown_minutes=applied_cooldown,
        )
        period_result = _next_pulse_period_summary(selected)
        period_result["flow_counts"] = flow_counts
        period_result["post_event_return_comparison"] = (
            _volume_hit_return_comparison(selected, report_by_year=report_by_year)
        )
        periods[period_name] = period_result
    status = "partial" if missing_fields or unsupported_requests else "completed"
    return {
        "id": "next_pulse_mechanism",
        "status": status,
        "periods": periods,
        "missing_fields": sorted(missing_fields),
        "configured_cooldown_minutes": int(cooldown_minutes),
        "applied_cooldown_minutes": applied_cooldown,
        "event_horizon_minutes": int(horizon_minutes),
        "current_return_column": current_return_column,
        "report_by_year": report_by_year,
        "candidate_scope": candidate_scope,
        "requested_anchor_evaluation": requested_anchor_evaluation,
        "unsupported_requests": unsupported_requests,
        "selection_fields_only": _strategy_selection_fields(params),
        "outcome_fields_used_for_selection": [],
        "post_event_grouping_warning": (
            "volume_hit 是事件发生后的结果，只能用于分组评价，不能作为策略信号。"
        ),
        "definitions": {
            "volume_hit": "下一次放量出现在预计时点前后1分钟内。",
            "next_up": "第五批之后的下一次实际放量分钟上涨。",
            "next_up_given_volume_hit": "只在volume_hit为真时计算next_up比例。",
            "joint": "事件文件保存的volume_hit且next_up结果。",
        },
    }


def _core_variant_selected_frame(
    frame: pd.DataFrame,
    conditions: list[dict[str, object]],
    *,
    cooldown_minutes: int,
    horizon_minutes: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    mask = pd.Series(True, index=frame.index)
    for condition in conditions:
        feature = str(condition["feature"])
        if feature not in frame.columns:
            raise ValueError(f"分钟事件文件缺少核心规则字段: {feature}")
        mask &= _condition_mask(frame, condition)
    return _prepare_selected_horizon_frame(
        frame.loc[mask],
        return_column=f"forward_{horizon_minutes}m",
        horizon_minutes=horizon_minutes,
        cooldown_minutes=cooldown_minutes,
    )


def _core_variant_comparison(
    frames: dict[str, pd.DataFrame],
    parameters: dict[str, object],
    *,
    cost_rate: float,
    cooldown_minutes: int,
    horizon_minutes: int,
) -> dict[str, object]:
    variants = _normalise_core_variants(parameters)
    selection_name, evaluation_name = _selection_and_evaluation_names(frames)
    selection_frame = frames[selection_name]
    evaluation_frame = frames[evaluation_name]
    _, selection_reference_flow = _prepare_selected_horizon_frame(
        selection_frame,
        return_column=f"forward_{horizon_minutes}m",
        horizon_minutes=horizon_minutes,
        cooldown_minutes=cooldown_minutes,
    )
    valid_selection_count = int(
        selection_reference_flow["final_event_count"]
    )
    minimum_count = max(1, int(math.ceil(valid_selection_count * 0.01)))

    training_rows: list[dict[str, object]] = []
    for variant in variants:
        name = str(variant["name"])
        conditions = list(variant["conditions"])
        selected, flow_counts = _core_variant_selected_frame(
            selection_frame,
            conditions,
            cooldown_minutes=cooldown_minutes,
            horizon_minutes=horizon_minutes,
        )
        overall = _summarize_returns(selected, cost_rate)
        valid_count = int(overall["valid_return_count"])
        training_rows.append(
            {
                "name": name,
                "conditions": conditions,
                "selected_fraction_of_valid_events": (
                    float(valid_count / valid_selection_count)
                    if valid_selection_count
                    else None
                ),
                "eligible_for_selection": valid_count >= minimum_count,
                "flow_counts": flow_counts,
                "overall": overall,
                "by_year": _summarize_by_period(
                    selected, frequency="year", cost_rate=cost_rate
                ),
            }
        )

    eligible_rows = [
        row
        for row in training_rows
        if row["eligible_for_selection"]
        and isinstance(row["overall"].get("gross_mean_return"), (int, float))
    ]
    eligible_rows.sort(
        key=lambda row: (
            float(row["overall"]["gross_mean_return"]),
            int(row["overall"]["valid_return_count"]),
        ),
        reverse=True,
    )
    selected_name = str(eligible_rows[0]["name"]) if eligible_rows else ""
    selected_variant = next(
        (variant for variant in variants if variant["name"] == selected_name), None
    )

    fixed_evaluation: dict[str, object] | None = None
    if selected_variant is not None:
        conditions = list(selected_variant["conditions"])
        selected, flow_counts = _core_variant_selected_frame(
            evaluation_frame,
            conditions,
            cooldown_minutes=cooldown_minutes,
            horizon_minutes=horizon_minutes,
        )
        fixed_evaluation = {
            "period_name": evaluation_name,
            "name": selected_name,
            "conditions": conditions,
            "flow_counts": flow_counts,
            "overall": _summarize_returns(selected, cost_rate),
            "by_year": _summarize_by_period(
                selected, frequency="year", cost_rate=cost_rate
            ),
        }

    return {
        "id": "core_variant_comparison",
        "status": "completed" if selected_variant is not None else "partial",
        "selection_period_name": selection_name,
        "fixed_evaluation_period_name": evaluation_name,
        "training_selection": {
            "criterion": "gross_mean_return",
            "minimum_event_fraction": 0.01,
            "minimum_event_count": minimum_count,
            "reference_flow_counts": selection_reference_flow,
            "ranked_variant_names": [str(row["name"]) for row in eligible_rows],
            "selected_variant_name": selected_name or None,
            "variants": training_rows,
        },
        "fixed_evaluation": fixed_evaluation,
        "configured_cooldown_minutes": int(cooldown_minutes),
        "event_horizon_minutes": int(horizon_minutes),
        "return_column": f"forward_{horizon_minutes}m",
        "allowed_condition_fields": sorted(_CORE_ALLOWED_FEATURES),
        "allowed_operators": sorted(_CORE_ALLOWED_OPERATORS),
        "outcome_fields_used_for_selection": [],
        "warning": (
            f"只按{selection_name}选一个规则；{evaluation_name}只复核该规则，"
            "不能根据复核结果改选其他规则。"
        ),
    }


def _flow_reconciliation(
    development: pd.DataFrame,
    params: dict[str, object],
    *,
    cooldown_minutes: int,
) -> dict[str, object]:
    selected = _selected_frame(development, params)
    selected_keys = selected[["trigger_ts", "code"]].drop_duplicates()
    valid_all = development.loc[development["gross_return"].notna()].copy()
    matched = valid_all.merge(
        selected_keys,
        how="inner",
        on=["trigger_ts", "code"],
        validate="many_to_one",
    )
    valid_selected_keys = selected.loc[
        selected["gross_return"].notna(), ["trigger_ts", "code"]
    ].drop_duplicates()
    duplicate_mask = matched.duplicated(["trigger_ts", "code"], keep=False)
    duplicate_details: list[dict[str, object]] = []
    for (timestamp, code), group in matched.loc[duplicate_mask].groupby(
        ["trigger_ts", "code"], sort=True
    ):
        duplicate_details.append(
            {
                "trigger_ts": pd.Timestamp(timestamp).isoformat(),
                "code": str(code),
                "candidate_ids": [str(value) for value in group["candidate_id"].tolist()],
                "row_count": int(len(group)),
            }
        )
    after_cooldown = _apply_cooldown(matched, cooldown_minutes)
    return {
        "id": "flow_reconciliation",
        "status": "completed",
        "selected_signal_count": int(len(selected_keys)),
        "matched_unique_signal_count": int(len(valid_selected_keys)),
        "unmatched_signal_count": int(len(selected_keys) - len(valid_selected_keys)),
        "matched_event_row_count": int(len(matched)),
        "extra_event_rows_from_same_stock_time": int(len(matched) - len(valid_selected_keys)),
        "cooldown_removed_event_rows": int(len(matched) - len(after_cooldown)),
        "final_event_row_count": int(len(after_cooldown)),
        "duplicate_stock_time_details": duplicate_details[:20],
        "explanation": (
            "同一股票和同一时刻可能对应多个 candidate_id。目标比例只有股票和时间，"
            "因此一次唯一信号会匹配多条事件记录。"
        ),
    }


def _cooldown_comparison(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    *,
    cost_rate: float,
    cooldown_minutes: int,
) -> dict[str, object]:
    periods: dict[str, object] = {}
    for name, frame in frames.items():
        selected = _selected_frame(frame, params)
        selected = selected.loc[selected["gross_return"].notna()].copy()
        selected = selected.drop_duplicates(["trigger_ts", "code"], keep="first")
        without = _apply_cooldown(selected, 0)
        configured = _apply_cooldown(selected, cooldown_minutes)
        periods[name] = {
            "without_cooldown": _summarize_returns(without, cost_rate),
            "configured_cooldown": _summarize_returns(configured, cost_rate),
            "removed_count": int(len(without) - len(configured)),
        }
    return {
        "id": "cooldown_comparison",
        "status": "completed",
        "configured_minutes": cooldown_minutes,
        "periods": periods,
    }


def _execution_price_audit(
    frames: dict[str, pd.DataFrame],
    params: dict[str, object],
    parameters: dict[str, object],
    *,
    horizon_minutes: int,
) -> dict[str, object]:
    (
        rule_params,
        rule_source,
        selection_conditions,
        rule_problems,
    ) = _resolve_requested_diagnostic_rule(
        params,
        parameters,
        base_source="strategy_result.params",
    )
    if rule_params is None:
        return {
            "id": "execution_price_audit",
            "status": "partial",
            "periods": {},
            "rule_source": rule_source,
            "selection_fields_only": [],
            "selection_conditions": selection_conditions,
            "unsupported_requests": rule_problems,
            "limitations": ["请求的筛选条件无效，本项未运行。"],
        }

    def ratio(count: int, total: int) -> float | None:
        return float(count / total) if total else None

    def count_signals(signals: pd.DataFrame) -> dict[str, object]:
        selected_count = int(len(signals))
        valid_count = int(signals["_valid_price"].sum())
        unmatched_count = selected_count - valid_count
        boundary_count = int(signals["_boundary_invalid"].sum())
        other_count = int(signals["_other_unmatched"].sum())
        return {
            "selected_signal_count": selected_count,
            "valid_price_signal_count": valid_count,
            "valid_price_ratio": ratio(valid_count, selected_count),
            "unmatched_signal_count": unmatched_count,
            "unmatched_signal_ratio": ratio(unmatched_count, selected_count),
            "boundary_invalid_signal_count": boundary_count,
            "boundary_invalid_ratio": ratio(boundary_count, selected_count),
            "boundary_share_of_unmatched": ratio(boundary_count, unmatched_count),
            "other_unmatched_signal_count": other_count,
            "other_unmatched_ratio": ratio(other_count, selected_count),
            "other_share_of_unmatched": ratio(other_count, unmatched_count),
        }

    periods: dict[str, object] = {}
    for name, frame in frames.items():
        selected = _selected_frame(frame, rule_params)
        selected["_valid_price"] = np.isfinite(
            pd.to_numeric(selected["gross_return"], errors="coerce")
        )
        selected["_session_no"] = (
            pd.to_numeric(selected["session_no"], errors="coerce")
            if "session_no" in selected.columns
            else np.nan
        )
        selected["_trigger_position"] = (
            pd.to_numeric(selected["trigger_position"], errors="coerce")
            if "trigger_position" in selected.columns
            else np.nan
        )
        session_upper = selected["_session_no"].map({0: 120, 1: 240})
        selected["_at_session_boundary"] = (
            selected["_trigger_position"]
            .add(horizon_minutes)
            .ge(session_upper)
            .fillna(False)
        )
        signals = (
            selected.groupby(["trigger_ts", "code"], as_index=False, sort=True)
            .agg(
                _valid_price=("_valid_price", "any"),
                _at_session_boundary=("_at_session_boundary", "any"),
                session_no=("_session_no", "first"),
                trigger_position=("_trigger_position", "first"),
            )
        )
        signals["_boundary_invalid"] = (
            ~signals["_valid_price"] & signals["_at_session_boundary"]
        )
        signals["_other_unmatched"] = (
            ~signals["_valid_price"] & ~signals["_at_session_boundary"]
        )
        signal_counts = count_signals(signals)

        by_session: list[dict[str, object]] = []
        session_names = {0: "morning", 1: "afternoon"}
        signals["_session_group"] = signals["session_no"].map(session_names).fillna(
            "unknown"
        )
        for session_name in ("morning", "afternoon", "unknown"):
            session_signals = signals.loc[signals["_session_group"].eq(session_name)]
            if session_signals.empty:
                continue
            session_no = next(
                (value for value, label in session_names.items() if label == session_name),
                None,
            )
            session_row: dict[str, object] = {
                "session_no": session_no,
                "session_name": session_name,
            }
            session_row.update(count_signals(session_signals))
            by_session.append(session_row)

        unmatched = signals.loc[~signals["_valid_price"]].copy()
        unmatched_by_position: list[dict[str, object]] = []
        position_values = sorted(
            {
                float(value)
                for value in unmatched["trigger_position"].dropna().tolist()
            }
        )
        for position in [*position_values, None]:
            if position is None:
                position_signals = unmatched.loc[unmatched["trigger_position"].isna()]
            else:
                position_signals = unmatched.loc[
                    unmatched["trigger_position"].eq(position)
                ]
            if position_signals.empty:
                continue
            position_count = int(len(position_signals))
            boundary_count = int(position_signals["_boundary_invalid"].sum())
            other_count = int(position_signals["_other_unmatched"].sum())
            display_position: int | float | None = position
            if position is not None and float(position).is_integer():
                display_position = int(position)
            unmatched_by_position.append(
                {
                    "trigger_position": display_position,
                    "unmatched_signal_count": position_count,
                    "ratio_of_selected": ratio(position_count, int(len(signals))),
                    "ratio_of_unmatched": ratio(position_count, int(len(unmatched))),
                    "boundary_invalid_signal_count": boundary_count,
                    "other_unmatched_signal_count": other_count,
                }
            )

        period_result: dict[str, object] = {
            "source_event_row_count": int(len(frame)),
            "selected_event_row_count": int(len(selected)),
            "duplicate_selected_event_row_count": int(len(selected) - len(signals)),
            **signal_counts,
            "by_session": by_session,
            "unmatched_by_trigger_position": unmatched_by_position,
            # 保留旧字段，数值改为当前策略入选后的唯一信号口径。
            "event_count": int(len(signals)),
            "invalid_return_count": int(signal_counts["unmatched_signal_count"]),
            "session_boundary_count": int(
                signal_counts["boundary_invalid_signal_count"]
            ),
            "price_or_source_missing_count": int(
                signal_counts["other_unmatched_signal_count"]
            ),
        }
        periods[name] = period_result
    return {
        "id": "execution_price_audit",
        "status": "partial",
        "rule_source": rule_source,
        "entry_definition": "trigger_position + 1 的分钟开盘价",
        "exit_definition": f"trigger_position + {horizon_minutes} 的分钟收盘价",
        "selection_fields_only": _strategy_selection_fields(rule_params),
        "selection_conditions": selection_conditions,
        "unsupported_requests": rule_problems,
        "count_unit": "按 trigger_ts + code 去重后的唯一信号",
        "boundary_rules": {
            "morning": f"trigger_position + {horizon_minutes} >= 120",
            "afternoon": f"trigger_position + {horizon_minutes} >= 240",
        },
        "periods": periods,
        "limitations": [
            "事件文件没有保存进场价和离场价，不能逐笔复核价格。",
            "没有停牌、涨跌停和证券状态资料，不能可靠区分这些原因。",
        ],
    }


def _signal_weight_summary(
    development: pd.DataFrame,
    params: dict[str, object],
) -> dict[str, object]:
    selected = _selected_frame(development, params)
    selected = selected.drop_duplicates(["trigger_ts", "code"], keep="first")
    per_time = selected.groupby("trigger_ts", sort=True)["code"].nunique()
    weights = 1.0 / per_time.astype(float) if len(per_time) else pd.Series(dtype=float)
    return {
        "id": "signal_weight_summary",
        "status": "partial",
        "selected_signal_count": int(len(selected)),
        "trigger_count": int(len(per_time)),
        "stocks_per_trigger": {
            "mean": float(per_time.mean()) if len(per_time) else None,
            "median": float(per_time.median()) if len(per_time) else None,
            "p95": float(per_time.quantile(0.95)) if len(per_time) else None,
            "max": int(per_time.max()) if len(per_time) else None,
        },
        "implied_equal_weight": {
            "mean": float(weights.mean()) if len(weights) else None,
            "median": float(weights.median()) if len(weights) else None,
            "min": float(weights.min()) if len(weights) else None,
            "max": float(weights.max()) if len(weights) else None,
        },
        "cash_rule": "有信号时股票比例合计为1、现金为0；无信号时现金为1。",
        "limitations": [
            "当前事件研究没有真实订单、持仓和成交金额，不能计算真实交易来源及小额成交。"
        ],
    }


def _return_cost_distribution(
    development: pd.DataFrame,
    params: dict[str, object],
    parameters: dict[str, object],
    *,
    cost_rate: float,
    cost_parts: dict[str, float],
    cooldown_minutes: int,
) -> dict[str, object]:
    (
        rule_params,
        rule_source,
        selection_conditions,
        rule_problems,
    ) = _resolve_requested_diagnostic_rule(
        params,
        parameters,
        base_source="strategy_result.params",
    )
    if rule_params is None:
        return {
            "id": "return_cost_distribution",
            "status": "partial",
            "rule_source": rule_source,
            "selection_fields_only": [],
            "selection_conditions": selection_conditions,
            "unsupported_requests": rule_problems,
            "limitations": ["请求的筛选条件无效，本项未运行。"],
        }

    selected = _selected_frame(development, rule_params)
    selected = selected.loc[selected["gross_return"].notna()].copy()
    selected = selected.drop_duplicates(["trigger_ts", "code"], keep="first")
    selected = _apply_cooldown(selected, cooldown_minutes)
    gross = pd.to_numeric(selected["gross_return"], errors="coerce").dropna()
    quantiles = {
        f"p{int(level * 100):02d}": float(gross.quantile(level))
        for level in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
    } if len(gross) else {}
    trimmed = gross.loc[
        gross.between(gross.quantile(0.01), gross.quantile(0.99), inclusive="both")
    ] if len(gross) else gross
    gross_mean = float(gross.mean()) if len(gross) else None
    return {
        "id": "return_cost_distribution",
        "status": "completed",
        "rule_source": rule_source,
        "selection_fields_only": _strategy_selection_fields(rule_params),
        "selection_conditions": selection_conditions,
        "unsupported_requests": rule_problems,
        "event_count": int(len(gross)),
        "gross_quantiles": quantiles,
        "gross_mean_return": gross_mean,
        "gross_trimmed_1pct_mean_return": float(trimmed.mean()) if len(trimmed) else None,
        "configured_cost_rate": float(cost_rate),
        "cost_parts": cost_parts,
        "breakeven_cost_rate": gross_mean,
        "zero_cost_mean_return": gross_mean,
        "configured_cost_mean_return": (
            gross_mean - cost_rate if gross_mean is not None else None
        ),
        "double_cost_mean_return": (
            gross_mean - 2.0 * cost_rate if gross_mean is not None else None
        ),
    }


def _account_separation(horizon_minutes: int) -> dict[str, object]:
    return {
        "id": "account_separation",
        "status": "partial",
        "event_result_use": (
            f"用于判断确认后的{horizon_minutes}分钟价格现象及其是否覆盖单次费用。"
        ),
        "account_result_use": "当前不能用于判断可交易账户收益。",
        "limitations": [
            "A股当天买入不能当天卖出，当前没有符合T+1的离场规则。",
            "当前没有真实订单、可成交数量、持仓和证券状态资料。",
        ],
    }


def build_event_diagnostic_report(state: WorkflowState) -> dict[str, object]:
    request = state.get("diagnostic_request", {})
    if not isinstance(request, dict) or not request:
        raise ValueError("缺少诊断请求")
    items = normalize_diagnostic_items(request.get("items", []))
    if not items:
        items = [
            {"id": value, "reason": "", "parameters": {}}
            for value in DEFAULT_EVENT_DIAGNOSTICS
        ]
    initial_research = _initial_research_requested(state, request, items)
    configured_periods = _configured_research_periods(state)
    if configured_periods:
        periods = configured_periods
    elif initial_research:
        periods = _initial_research_periods(state)
    else:
        periods = _validate_research_periods(state)
    spec = state.get("experiment_spec", {})
    if not isinstance(spec, dict):
        spec = {}
    params = _resolve_rule_params(state)
    horizon_minutes, horizon_source = _resolve_event_horizon_minutes(params, spec)
    cooldown_minutes = int(spec.get("event_cooldown_minutes", 20))
    cost_rate, cost_parts = _configured_cost(spec)
    paths = _resolve_event_paths()
    frames = {
        name: _read_event_frame(
            paths,
            start=period["start"],
            end=period["end"],
            horizon_minutes=horizon_minutes,
        )
        for name, period in periods.items()
    }
    if "generic_selection_conditions" in params:
        _normalise_generic_selection_conditions(params)
    requested_ids = [str(item["id"]) for item in items]
    item_parameters = {
        str(item["id"]): (
            dict(item.get("parameters", {}))
            if isinstance(item.get("parameters", {}), dict)
            else {}
        )
        for item in items
    }
    _, evaluation_name = _selection_and_evaluation_names(frames)

    builders: dict[str, Any] = {
        "period_metrics": lambda: _period_metrics(
            frames,
            params,
            cost_rate=cost_rate,
            cooldown_minutes=cooldown_minutes,
        ),
        "rule_waterfall": lambda: _rule_waterfall(frames, params, cost_rate),
        "feature_screening": lambda: _feature_screening(
            frames,
            cost_rate=cost_rate,
            cooldown_minutes=cooldown_minutes,
        ),
        "low_ma5_distance_screening": lambda: _low_ma5_distance_screening(
            frames,
            params,
            cost_rate=cost_rate,
            cooldown_minutes=cooldown_minutes,
            horizon_minutes=horizon_minutes,
            source_candidate_id=str(
                request.get("source_candidate_id", "")
            ).strip(),
        ),
        "event_response_curve": lambda: _event_response_curve(
            frames,
            params,
            item_parameters["event_response_curve"],
            cost_rate=cost_rate,
            cooldown_minutes=cooldown_minutes,
            current_candidate_id=str(request.get("source_candidate_id", "")).strip(),
            candidate_records=state.get("candidate_records", []),
        ),
        "next_pulse_mechanism": lambda: _next_pulse_mechanism(
            frames,
            params,
            item_parameters["next_pulse_mechanism"],
            cooldown_minutes=cooldown_minutes,
            horizon_minutes=horizon_minutes,
            current_candidate_id=str(request.get("source_candidate_id", "")).strip(),
        ),
        "core_variant_comparison": lambda: _core_variant_comparison(
            frames,
            item_parameters["core_variant_comparison"],
            cost_rate=cost_rate,
            cooldown_minutes=cooldown_minutes,
            horizon_minutes=horizon_minutes,
        ),
        "execution_price_audit": lambda: _execution_price_audit(
            frames,
            params,
            item_parameters["execution_price_audit"],
            horizon_minutes=horizon_minutes,
        ),
        "flow_reconciliation": lambda: _flow_reconciliation(
            frames[evaluation_name], params, cooldown_minutes=cooldown_minutes
        ),
        "signal_weight_summary": lambda: _signal_weight_summary(
            frames[evaluation_name], params
        ),
        "cooldown_comparison": lambda: _cooldown_comparison(
            frames,
            params,
            cost_rate=cost_rate,
            cooldown_minutes=cooldown_minutes,
        ),
        "return_cost_distribution": lambda: _return_cost_distribution(
            frames[evaluation_name],
            params,
            item_parameters["return_cost_distribution"],
            cost_rate=cost_rate,
            cost_parts=cost_parts,
            cooldown_minutes=cooldown_minutes,
        ),
        "account_separation": lambda: _account_separation(horizon_minutes),
    }
    results = [builders[diagnostic_id]() for diagnostic_id in requested_ids]
    partial = [
        str(item.get("id", ""))
        for item in results
        if isinstance(item, dict) and item.get("status") == "partial"
    ]
    unsupported_requests: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        values = result.get("unsupported_requests", [])
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if text and text not in unsupported_requests:
                unsupported_requests.append(text)
    if configured_periods:
        configured_stage = str(
            request.get("research_stage")
            or state.get("research_stage")
            or spec.get("research_stage")
            or "configured"
        ).strip().lower()
        period_access = {
            "research_stage": configured_stage,
            "period_source": "experiment_spec.research_selection_confirmation",
            "selection": periods["selection"],
            "confirmation": periods["confirmation"],
            "development_read": False,
            "final_period_read": False,
        }
    elif initial_research:
        period_access = {
            "research_stage": "initial",
            "selection": periods["selection"],
            "internal_check": periods["internal_check"],
            "development_read": False,
            "final_period_read": False,
        }
    else:
        period_access = {
            "research_stage": "development",
            "train": periods["train"],
            "development": periods["development"],
            "development_read": True,
            "final_period_read": False,
        }
    return {
        "report_version": 1,
        "request_id": str(request.get("request_id", "")),
        "source_candidate_id": str(request.get("source_candidate_id", "")),
        "research_epoch": int(state.get("epoch_index", 1)),
        "diagnostic_round": int(state.get("diagnostic_round", 0)) + 1,
        "status": (
            "completed_with_limits"
            if partial or unsupported_requests
            else "completed"
        ),
        "period_access": period_access,
        "strategy_parameters": params,
        "event_horizon_minutes": horizon_minutes,
        "event_horizon_source": horizon_source,
        "results": results,
        "partial_results": partial,
        "unsupported_requests": unsupported_requests,
        "generated_at": datetime.now().astimezone().isoformat(),
    }


def run_research_diagnostics(state: WorkflowState) -> dict[str, object]:
    spec = state.get("experiment_spec", {})
    mode = str(spec.get("backtest_mode", "")).strip().lower() if isinstance(spec, dict) else ""
    if mode != "event_parquet":
        return {
            "report_version": 1,
            "request_id": str(state.get("diagnostic_request", {}).get("request_id", "")),
            "source_candidate_id": _latest_development_candidate_id(state),
            "research_epoch": int(state.get("epoch_index", 1)),
            "diagnostic_round": int(state.get("diagnostic_round", 0)) + 1,
            "status": "unsupported",
            "period_access": {"final_period_read": False},
            "results": [],
            "partial_results": [],
            "unsupported_requests": ["当前只支持 event_parquet 的固定诊断。"],
        }
    return build_event_diagnostic_report(state)
