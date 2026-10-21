from __future__ import annotations

import ast
import contextlib
import difflib
import io
import json
import os
import re
import traceback
import warnings
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from quanta_agents.config import get_agent_max_retries
from quanta_agents.calculation_contract import (
    check_calculation_contract_call_performance,
)
from quanta_agents.exceptions import AgentExecutionError
from quanta_agents.llm import llm_client
from quanta_agents.period_data import filter_membership_for_period, is_dated_membership_item
from quanta_agents.prompt_loader import load_agent_prompt
from quanta_agents.semantic import MetadataManager, get_universe_list, load_required_data
from quanta_agents.semantic.metadata import (
    historical_index_membership_table,
    normalize_historical_index_universe,
    resolve_csi300_membership_path,
    resolve_historical_index_membership_path,
)
from quanta_agents.state import WorkflowState, get_hypothesis
from quanta_agents.strategy_code_policy import (
    FORBIDDEN_EVENT_RESULT_FIELDS,
    validate_generated_strategy_code,
)
from quanta_agents.trace_logger import get_agent_trace_dir, print_agent_progress, write_trace_json, write_trace_text


_MAX_CODEACT_STEPS = 10
_MAX_OUTPUT_CHARS = 4000
_MAX_CODE_DIFF_CHARS = 12000


class StrategyAgent:
    """Generate executable Python code from strategy logic and required data."""

    name = "StrategyAgent"

    def __init__(self) -> None:
        self.max_retries = get_agent_max_retries(self.name)

    @staticmethod
    def _extract_previous_strategy_code(state: WorkflowState) -> tuple[str, str]:
        for state_key in ("strategy_code", "code_text"):
            raw_code = state.get(state_key)
            if isinstance(raw_code, str) and raw_code.strip():
                return raw_code.strip(), f"WorkflowState.{state_key}（进入本轮前保存的策略版本）"
        return "", "当前状态没有上一版策略代码，本轮是首次生成"

    @staticmethod
    def _extract_previous_code_diff(state: WorkflowState) -> str:
        meta = state.get("strategy_generation_meta")
        if not isinstance(meta, dict):
            return ""
        code_diff = meta.get("code_diff")
        if not isinstance(code_diff, str) or not code_diff.strip():
            return ""
        return code_diff.strip()[:_MAX_CODE_DIFF_CHARS]

    @staticmethod
    def _build_code_change_summary(
        strategy_context: dict[str, object],
        validation_summary: dict[str, object],
    ) -> str:
        parts: list[str] = []
        strategy_modification = str(strategy_context.get("strategy_modification", "")).strip()
        if strategy_modification:
            parts.append(f"策略修改要求：{strategy_modification}")
        if validation_summary:
            parts.append("只修复上一轮统计验证中有明确证据的问题，保留未被否定的规则和代码。")
        if not parts:
            parts.append("没有指定额外变化；实现原始策略逻辑，并避免加入无依据的新规则。")
        return "\n".join(parts)

    @staticmethod
    def _build_code_diff(previous_code: str, current_code: str) -> str:
        if not previous_code.strip():
            return ""
        diff_lines = difflib.unified_diff(
            previous_code.splitlines(),
            current_code.splitlines(),
            fromfile="previous_strategy.py",
            tofile="generated_strategy.py",
            lineterm="",
            n=3,
        )
        return "\n".join(diff_lines)

    @staticmethod
    def _code_change_ratio(previous_code: str, current_code: str) -> float:
        previous_lines = previous_code.splitlines()
        current_lines = current_code.splitlines()
        if not previous_lines:
            return 1.0 if current_lines else 0.0
        matcher = difflib.SequenceMatcher(
            None,
            previous_lines,
            current_lines,
            autojunk=False,
        )
        return 1.0 - float(matcher.ratio())

    @staticmethod
    def _parse_strategy_meta(state: WorkflowState) -> dict[str, object]:
        meta = state.get("strategy_generation_meta")
        if isinstance(meta, dict) and meta:
            return meta
        # Fallback to hypothesis_generation_meta
        meta = state.get("hypothesis_generation_meta")
        if isinstance(meta, dict) and meta:
            return meta
        raise AgentExecutionError(
            agent_name=StrategyAgent.name,
            attempts=1,
            message="missing hypothesis metadata; HypothesisAgent must run first",
        )

    @staticmethod
    def _build_strategy_generation_meta(strategy_context: dict[str, Any], strategy_result: dict[str, Any]) -> dict[str, Any]:
        train_period = strategy_result.get("train_period") or {
            "start": strategy_context.get("train_start"),
            "end": strategy_context.get("train_end"),
        }
        validate_period = strategy_result.get("validate_period") or {
            "start": strategy_context.get("validate_start"),
            "end": strategy_context.get("validate_end"),
        }

        return {
            "status": "ok",
            "hypothesis": strategy_context.get("hypothesis", ""),
            "train_period": train_period,
            "validate_period": validate_period,
            "required_data": strategy_context.get("required_data", []),
            "backtest_datasets": strategy_context.get("backtest_datasets", []),
            "calculation_contracts": strategy_context.get("calculation_contracts", []),
            "research_category": strategy_context.get("research_category", ""),
            "mechanism_id": strategy_context.get("mechanism_id", ""),
            "variant_mode": strategy_context.get("variant_mode", ""),
            "new_signal_definition": deepcopy(
                strategy_context.get("new_signal_definition", {})
            ),
        }

    @staticmethod
    def _resolve_train_period(state: WorkflowState) -> tuple[str, str]:
        spec = state.get("experiment_spec", {})
        start = str(spec.get("train_start", "")).strip()
        end = str(spec.get("train_end", "")).strip()
        if start and end:
            return start, end
        raise AgentExecutionError(
            agent_name=StrategyAgent.name,
            attempts=1,
            message="training period is missing from experiment_spec",
        )

    @staticmethod
    def _resolve_validate_period(state: WorkflowState) -> tuple[str, str]:
        spec = state.get("experiment_spec", {})
        start = str(spec.get("validate_start", "")).strip()
        end = str(spec.get("validate_end", "")).strip()
        if start and end:
            return start, end

        return StrategyAgent._resolve_train_period(state)

    @classmethod
    def _prepare_required_data(
        cls,
        required_data: object,
        period_start: str,
        period_end: str,
    ) -> list[dict[str, object]]:
        if not isinstance(required_data, list) or not required_data:
            raise AgentExecutionError(
                agent_name=StrategyAgent.name,
                attempts=1,
                message="hypothesis required_data must be a non-empty list",
            )

        prepared: list[dict[str, object]] = []
        for index, item in enumerate(required_data):
            if not isinstance(item, dict):
                raise AgentExecutionError(
                    agent_name=StrategyAgent.name,
                    attempts=1,
                    message=f"required_data[{index}] must be an object",
                )
            normalized = deepcopy(item)
            table_key = str(normalized.get("table_key", normalized.get("name", ""))).strip()
            if not table_key:
                raise AgentExecutionError(
                    agent_name=StrategyAgent.name,
                    attempts=1,
                    message=f"required_data[{index}] missing table_key",
                )
            normalized["table_key"] = table_key

            if table_key == "periodic_minute_events":
                fields = normalized.get("fields")
                if isinstance(fields, list):
                    forbidden_fields = set(FORBIDDEN_EVENT_RESULT_FIELDS) | {
                        "next_spike_position"
                    }
                    normalized_fields = [
                        field
                        for field in fields
                        if isinstance(field, str)
                        and field not in forbidden_fields
                        and not field.strip().lower().startswith("analysis_only_")
                    ]
                    if not normalized_fields:
                        raise AgentExecutionError(
                            agent_name=StrategyAgent.name,
                            attempts=1,
                            message=(
                                "periodic_minute_events required_data 只包含禁止提供给策略的结果字段"
                            ),
                        )
                    normalized["fields"] = normalized_fields

            if isinstance(normalized.get("time_range"), dict):
                normalized["time_range"] = {"start": period_start, "end": period_end}
            elif normalized.get("type") in {"time_series", "auxiliary", "panel"}:
                normalized["time_range"] = {"start": period_start, "end": period_end}

            prepared.append(normalized)

        historical_indexes = {
            canonical_name
            for item in prepared
            if isinstance(item.get("universe"), dict)
            and item["universe"].get("type") == "named_pool"
            and (
                canonical_name := normalize_historical_index_universe(
                    item["universe"].get("value")
                )
            )
            is not None
        }
        if (
            historical_indexes
            and os.getenv("QUANTA_DATA_ENGINE", "").strip().lower() == "parquet"
        ):
            existing_tables = {
                str(item.get("table_key", "")).strip()
                for item in prepared
            }
            for index_name in sorted(historical_indexes):
                membership_table = historical_index_membership_table(index_name)
                assert membership_table is not None
                if membership_table in existing_tables:
                    continue
                membership_path = (
                    resolve_csi300_membership_path()
                    if index_name == "csi300"
                    else resolve_historical_index_membership_path(index_name)
                )
                if not membership_path.is_file():
                    raise AgentExecutionError(
                        agent_name=StrategyAgent.name,
                        attempts=1,
                        message=(
                            f"找不到 {index_name} 历史成分文件: {membership_path}；"
                            "不能改用当前静态名单"
                        ),
                    )
                prepared.append(
                    {
                        "table_key": membership_table,
                        "type": "static",
                        "fields": ["code", "start_date", "end_date"],
                        "purpose": (
                            f"形成新建或增加目标时按决定日限制 {index_name} 历史成分；"
                            "下一实际执行日由固定回测程序检查。固定计划开始后，"
                            "持有期内退出指数不改变原目标，且不能使用期末静态名单"
                        ),
                    }
                )
                existing_tables.add(membership_table)

        return prepared

    @staticmethod
    def _preview_columns(dataframe: Any, limit: int = 12) -> str:
        if not hasattr(dataframe, "columns"):
            return "[]"
        try:
            columns = [str(col) for col in list(dataframe.columns)]
        except Exception:
            return "[]"
        if len(columns) <= limit:
            return json.dumps(columns, ensure_ascii=False)
        preview = columns[:limit]
        return json.dumps(preview, ensure_ascii=False) + f" ... (+{len(columns) - limit} more)"

    @staticmethod
    def _describe_dataframe(dataframe: Any) -> str:
        if dataframe is None:
            return "missing"
        shape = getattr(dataframe, "shape", None)
        shape_text = f"shape={shape}" if isinstance(shape, tuple) and len(shape) == 2 else "shape=unknown"
        index_name = type(getattr(dataframe, "index", None)).__name__
        columns_text = StrategyAgent._preview_columns(dataframe)
        return f"{shape_text}, index={index_name}, columns={columns_text}"

    @staticmethod
    def _shape_text(dataframe: Any) -> str:
        shape = getattr(dataframe, "shape", None)
        if isinstance(shape, tuple) and len(shape) == 2:
            return str(shape)
        return "unknown"

    @staticmethod
    def _structure_text(dataframe: Any) -> str:
        if dataframe is None:
            return "missing"
        index_name = type(getattr(dataframe, "index", None)).__name__
        columns_text = StrategyAgent._preview_columns(dataframe)
        return f"index={index_name}, columns={columns_text}"

    @staticmethod
    def _describe_bundle_schema(train_df: Any, validate_df: Any) -> str:
        train_structure = StrategyAgent._structure_text(train_df)
        validate_structure = StrategyAgent._structure_text(validate_df)
        train_shape = StrategyAgent._shape_text(train_df)
        validate_shape = StrategyAgent._shape_text(validate_df)

        if train_structure == validate_structure:
            return f"{train_structure}; train_shape={train_shape}; validate_shape={validate_shape}"

        return (
            f"train_{train_structure}; validate_{validate_structure}; "
            f"train_shape={train_shape}; validate_shape={validate_shape}"
        )

    @staticmethod
    def _layout_hint(dataset_type: str, symbol_column: str, datetime_column: str) -> str:
        if dataset_type == "time_series":
            return (
                f"long-format DataFrame; recommended columns include {symbol_column}, {datetime_column}, "
                "and requested value fields"
            )
        if dataset_type == "panel":
            return (
                f"wide-format DataFrame; recommended index is {datetime_column}, columns are symbols, values are factor/price matrix"
            )
        if dataset_type == "auxiliary":
            return f"time-series DataFrame without symbol dimension; recommended index/column includes {datetime_column}"
        if dataset_type == "static":
            return f"static DataFrame without time slicing; recommended columns include {symbol_column} and static attributes"
        return "DataFrame layout depends on dataset type"

    @staticmethod
    def _build_required_data_descriptions(
        required_data_items: list[dict[str, object]],
        train_data_bundle: dict[str, Any],
        validate_data_bundle: dict[str, Any],
        metadata_manager: MetadataManager | None,
    ) -> str:
        lines: list[str] = []
        for index, item in enumerate(required_data_items, start=1):
            table_key = str(item.get("table_key", "")).strip()
            requested_type = str(item.get("type", "")).strip() or "time_series"
            fields = item.get("fields")
            purpose = str(item.get("purpose", "")).strip()
            universe = item.get("universe")

            dataset_meta = metadata_manager.get_dataset(table_key) if metadata_manager is not None else None
            meta_type = str(dataset_meta.get("type", "")).strip() if isinstance(dataset_meta, dict) else ""
            dataset_type = requested_type or meta_type or "time_series"
            description = str(dataset_meta.get("description", "")).strip() if isinstance(dataset_meta, dict) else ""
            symbol_column = ""
            datetime_column = ""
            if isinstance(dataset_meta, dict):
                symbol_column = str(dataset_meta.get("symbol_column", "")).strip()
                datetime_column = str(dataset_meta.get("datetime_column", "")).strip()

            requested_fields_text: Any = fields
            if isinstance(fields, list):
                meaning_map: dict[str, str] = {}
                if isinstance(dataset_meta, dict):
                    raw_fields = dataset_meta.get("fields")
                    if isinstance(raw_fields, list):
                        for raw_field in raw_fields:
                            if not isinstance(raw_field, dict):
                                continue
                            field_name = str(raw_field.get("name", "")).strip()
                            meaning = str(raw_field.get("meaning", "")).strip()
                            if field_name and meaning:
                                meaning_map[field_name] = meaning
                requested_fields_text = [
                    f"{field}({meaning_map[field]})" if isinstance(field, str) and field in meaning_map else field
                    for field in fields
                ]

            train_df = train_data_bundle.get(table_key)
            validate_df = validate_data_bundle.get(table_key)

            lines.append(
                f"{index}. table_key={table_key}, type={dataset_type}, purpose={purpose}; "
                f"requested_fields={json.dumps(requested_fields_text, ensure_ascii=False)}, universe={json.dumps(universe, ensure_ascii=False)}"
            )
            if description:
                lines.append(f"   dataset_description={description}")
            schema_parts: list[str] = []
            if symbol_column:
                schema_parts.append(f"symbol_column={symbol_column}")
            if datetime_column:
                schema_parts.append(f"datetime_column={datetime_column}")
            if schema_parts:
                lines.append(f"   schema_hint: {', '.join(schema_parts)}")
            else:
                lines.append("   schema_hint: no explicit symbol_column/datetime_column in metadata")
            lines.append(f"   data_bundle_schema: {StrategyAgent._describe_bundle_schema(train_df, validate_df)}")

        return "\n".join(lines)

    @staticmethod
    def _normalize_universe_entries(experiment_spec: dict[str, Any], metadata_manager: MetadataManager | None) -> list[dict[str, Any]]:
        raw_universe = experiment_spec.get("universe", [])
        if not isinstance(raw_universe, list) or not raw_universe:
            return []

        normalized_entries: list[dict[str, Any]] = []
        seen_symbols: set[str] = set()

        for item in raw_universe:
            if not isinstance(item, dict):
                continue

            asset = str(item.get("asset", "")).strip()
            if not asset:
                continue

            raw_symbols = item.get("symbols")
            symbols: list[str] = []
            universe_type = str(item.get("type", "")).strip()
            named_pool = ""
            if universe_type == "named_pool" and isinstance(item.get("value"), str):
                named_pool = str(item.get("value", "")).strip()
            if not named_pool and isinstance(item.get("named_pool"), str):
                named_pool = str(item.get("named_pool", "")).strip()

            if isinstance(raw_symbols, list):
                symbols = [str(symbol).strip() for symbol in raw_symbols if isinstance(symbol, str) and str(symbol).strip()]
            elif isinstance(raw_symbols, str):
                symbols_text = raw_symbols.strip()
                if symbols_text:
                    if not named_pool:
                        named_pool = symbols_text
                    symbols = get_universe_list(named_pool)
                    if not symbols:
                        symbols = [part.strip() for part in symbols_text.split(",") if part.strip()]
                        if "," in symbols_text and universe_type != "named_pool":
                            named_pool = ""

            if not symbols and named_pool and not isinstance(raw_symbols, str):
                symbols = get_universe_list(named_pool)

            deduped_symbols: list[str] = []
            for symbol in symbols:
                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                deduped_symbols.append(symbol)

            dataset = str(item.get("dataset", "")).strip()
            if universe_type == "dataset_defined" and dataset:
                normalized_entry: dict[str, Any] = {
                    "symbols": deduped_symbols,
                    "asset": asset,
                    "type": universe_type,
                    "dataset": dataset,
                }
                description = str(item.get("description", "")).strip()
                if description:
                    normalized_entry["description"] = description
                normalized_entries.append(normalized_entry)
                continue

            if named_pool:
                # 命名股票范围可能由历史成分资料决定，没有静态代码时也要保留名称和市场。
                normalized_entry = {
                    "symbols": deduped_symbols,
                    "asset": asset,
                    "type": "named_pool",
                    "named_pool": named_pool,
                }
                description = str(item.get("description", "")).strip()
                if description:
                    normalized_entry["description"] = description
                normalized_entries.append(normalized_entry)
                continue

            if not deduped_symbols:
                continue

            normalized_entries.append({"symbols": deduped_symbols, "asset": asset})

        return normalized_entries

    @staticmethod
    def _build_data_bundle(loaded_required_data: list[dict[str, object]]) -> dict[str, Any]:
        bundle: dict[str, Any] = {}
        for index, item in enumerate(loaded_required_data, start=1):
            table_key = str(item.get("table_key", "")).strip()
            if not table_key:
                table_key = f"required_data_{index}"
            bundle[table_key] = item.get("data")
        return bundle

    @staticmethod
    def _load_required_inputs(
        required_data_items: list[dict[str, object]],
        train_start: str,
        train_end: str,
        validate_start: str,
        validate_end: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        train_required_data: list[dict[str, object]] = []
        validate_required_data: list[dict[str, object]] = []
        for item in required_data_items:
            train_item = deepcopy(item)
            validate_item = deepcopy(item)
            if train_item.get("type") in {"time_series", "auxiliary", "panel"}:
                train_item["time_range"] = {"start": train_start, "end": train_end}
                validate_item["time_range"] = {"start": validate_start, "end": validate_end}
            train_required_data.append(train_item)
            validate_required_data.append(validate_item)

        train_loaded = load_required_data(train_required_data)
        validate_loaded = load_required_data(validate_required_data)
        train_bundle = StrategyAgent._build_data_bundle(train_loaded)
        validate_bundle = StrategyAgent._build_data_bundle(validate_loaded)
        for item in required_data_items:
            if not is_dated_membership_item(item):
                continue
            table_key = str(item.get("table_key", item.get("name", ""))).strip()
            if table_key in train_bundle:
                train_bundle[table_key] = filter_membership_for_period(
                    train_bundle[table_key],
                    train_start,
                    train_end,
                )
            if table_key in validate_bundle:
                validate_bundle[table_key] = filter_membership_for_period(
                    validate_bundle[table_key],
                    validate_start,
                    validate_end,
                )
        return train_bundle, validate_bundle

    @staticmethod
    def _build_strategy_context(state: WorkflowState) -> dict[str, object]:
        hypothesis_meta = StrategyAgent._parse_strategy_meta(state)
        hypothesis = str(hypothesis_meta.get("hypothesis", get_hypothesis(state))).strip()
        raw_backtest_datasets = hypothesis_meta.get("backtest_datasets", [])
        backtest_datasets = [
            str(item).strip()
            for item in raw_backtest_datasets
            if isinstance(item, str) and str(item).strip()
        ] if isinstance(raw_backtest_datasets, list) else []
        train_start, train_end = StrategyAgent._resolve_train_period(state)
        validate_start, validate_end = StrategyAgent._resolve_validate_period(state)
        required_data = StrategyAgent._prepare_required_data(
            hypothesis_meta.get("required_data"),
            train_start,
            train_end,
        )
        train_data_bundle, validate_data_bundle = StrategyAgent._load_required_inputs(
            required_data,
            train_start,
            train_end,
            validate_start,
            validate_end,
        )
        metadata_manager: MetadataManager | None = None
        try:
            metadata_manager = MetadataManager.from_yaml_files()
        except Exception:
            metadata_manager = None
        universe = StrategyAgent._normalize_universe_entries(state.get("experiment_spec", {}), metadata_manager)
        return {
            "hypothesis": hypothesis,
            "required_data": required_data,
            "universe": universe,
            "train_data_bundle": train_data_bundle,
            "validate_data_bundle": validate_data_bundle,
            "required_data_descriptions": StrategyAgent._build_required_data_descriptions(
                required_data,
                train_data_bundle,
                validate_data_bundle,
                metadata_manager,
            ),
            "train_start": train_start,
            "train_end": train_end,
            "validate_start": validate_start,
            "validate_end": validate_end,
            "strategy_modification": str(hypothesis_meta.get("strategy_modification", "")).strip(),
            "candidate_mode": str(hypothesis_meta.get("candidate_mode", "baseline")).strip() or "baseline",
            "research_category": str(hypothesis_meta.get("category", "")).strip(),
            "mechanism_id": str(hypothesis_meta.get("mechanism_id", "")).strip(),
            "variant_mode": str(hypothesis_meta.get("variant_mode", "")).strip(),
            "new_signal_definition": deepcopy(
                hypothesis_meta.get("new_signal_definition", {})
            ),
            "mechanism_signal_reference_source": str(
                hypothesis_meta.get("_mechanism_signal_reference_source", "")
            ).strip(),
            "calculation_contracts": deepcopy(
                hypothesis_meta.get("calculation_contracts", [])
            ),
            "missing_concepts": hypothesis_meta.get("missing_concepts", []),
            "backtest_datasets": backtest_datasets,
            "experiment_spec": state.get("experiment_spec", {}),
        }

    @staticmethod
    def _build_sandbox_namespace(strategy_context: dict[str, object]) -> dict[str, Any]:
        try:
            import numpy as np
        except Exception:
            np = None  # type: ignore[assignment]

        try:
            import pandas as pd
        except Exception:
            pd = None  # type: ignore[assignment]

        namespace: dict[str, Any] = {
            "__name__": "__generated_strategy__",
            "json": json,
            "np": np,
            "pd": pd,
            "strategy_context": strategy_context,
            "hypothesis": strategy_context.get("hypothesis", ""),
            "required_data_items": strategy_context.get("required_data", []),
            "universe": strategy_context.get("universe", []),
            "train_data_bundle": strategy_context.get("train_data_bundle", {}),
            "validate_data_bundle": strategy_context.get("validate_data_bundle", {}),
            "required_data_descriptions": strategy_context.get("required_data_descriptions", ""),
            "train_start": strategy_context.get("train_start", ""),
            "train_end": strategy_context.get("train_end", ""),
            "validate_start": strategy_context.get("validate_start", ""),
            "validate_end": strategy_context.get("validate_end", ""),
            "strategy_modification": strategy_context.get("strategy_modification", ""),
            "calculation_contracts": strategy_context.get("calculation_contracts", []),
            "research_category": strategy_context.get("research_category", ""),
            "mechanism_id": strategy_context.get("mechanism_id", ""),
            "variant_mode": strategy_context.get("variant_mode", ""),
            "new_signal_definition": strategy_context.get("new_signal_definition", {}),
            "missing_concepts": strategy_context.get("missing_concepts", []),
        }

        return namespace

    @staticmethod
    def _write_code_trace(state: WorkflowState, code: str, step: int) -> None:
        step_dir = get_agent_trace_dir(state, agent_name="strategyagent")
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        (step_dir / f"step_{step:02d}_{ts}.py").write_text(code, encoding="utf-8")

    @staticmethod
    def _execute_code(code: str, namespace: dict[str, Any]) -> tuple[str, bool]:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(code, "generated_strategy.py", "exec"), namespace)  # noqa: S102
            out = stdout_buf.getvalue()
            err = stderr_buf.getvalue()
            combined = (f"stdout:\n{out}\n" if out else "") + (f"stderr:\n{err}\n" if err else "")
            return (combined.strip() or "(no output)"), True
        except Exception:
            out = stdout_buf.getvalue()
            err = traceback.format_exc()
            combined = (f"stdout:\n{out}\n" if out else "") + f"Error:\n{err}"
            return combined.strip()[:_MAX_OUTPUT_CHARS], False

    @staticmethod
    def _extract_code_block(text: str) -> str | None:
        match = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        stripped = text.strip()
        if re.match(r"^(import |from |def |class |[A-Za-z_]\w*\s*=)", stripped):
            return stripped
        return None

    @staticmethod
    def _summarize_execution_error(output: str) -> str:
        text = output.strip()
        if not text:
            return "code execution failed"
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        exception_index: int | None = None
        exception_line: str | None = None
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(Error|Exception):\s*", line) or line.startswith("Exception:"):
                exception_index = idx
                exception_line = line
                break

        if exception_line is not None:
            frame_line: str | None = None
            generated_frames: list[str] = []
            if exception_index is not None:
                for idx in range(exception_index - 1, -1, -1):
                    line = lines[idx]
                    if line.startswith("File \""):
                        if "generated_strategy.py" in line:
                            generated_frames.append(line)
                        frame_line = line
                        if len(generated_frames) >= 3:
                            break

            # Prefer user-code frames over library internals so retries target the real bug site.
            if generated_frames:
                generated_frames.reverse()
                chain = " <- ".join(generated_frames)
                return f"code execution failed: {exception_line} ({chain})"

            if frame_line is not None:
                return f"code execution failed: {exception_line} ({frame_line})"
            return f"code execution failed: {exception_line}"
        return "code execution failed"

    @staticmethod
    def _build_error_code_context(code: str, validation_message: str, radius: int = 6) -> str:
        if not code.strip() or not validation_message.strip():
            return ""

        matches = re.findall(r'generated_strategy\.py", line (\d+)', validation_message)
        if not matches:
            return ""

        try:
            line_no = int(matches[-1])
        except Exception:
            return ""

        lines = code.splitlines()
        if not lines:
            return ""

        target_index = max(line_no - 1, 0)
        start = max(target_index - radius, 0)
        end = min(target_index + radius + 1, len(lines))
        snippet_lines = [f"{idx + 1:04d}: {lines[idx]}" for idx in range(start, end)]
        if not snippet_lines:
            return ""

        return "traceback附近代码片段:\n" + "\n".join(snippet_lines)

    @staticmethod
    def _build_retry_instruction(
        code: str,
        previous_code: str | None,
        validation_message: str,
        output: str,
        repeated_error_count: int,
    ) -> str:
        instruction_lines = [
            "请修正代码，必须定义 output_weights 函数，并调用它生成 output_weights_df。",
            "禁止使用占位符：不得将 output_weights_df 设为 None、空DataFrame，或仅保留“外部调用时赋值”注释。",
            "同时必须定义 strategy_next_phase=validate 和非空的 strategy_decision_reason。",
            "同时 strategy_output 必须是元信息字典：包含 output_weights.description/function_name/datetime_column，若包含中间变量条目，则每项都要有 description/function_name/expected_characteristics/datetime_column。",
            f"执行结果摘要:\n{output[:_MAX_OUTPUT_CHARS]}",
            f"校验结果摘要:\n{validation_message}",
            "本轮修正要求：优先修改 traceback 指向的局部位置，不要保留会再次触发同一异常的代码路径。",
        ]

        code_context = StrategyAgent._build_error_code_context(code, validation_message)
        if code_context:
            instruction_lines.append(code_context)

        if "Length mismatch" in validation_message:
            instruction_lines.append(
                "检测到 pandas 列数不匹配错误（Length mismatch）。请优先修复 groupby/apply/reset_index 后的列重命名："
                "不要硬编码 columns 列表长度；先打印或检查 DataFrame 实际列数，"
                "或改用 transform/rename 仅重命名新增列，确保重命名前后列数一致。"
            )

        if "IndexError: list index out of range" in validation_message:
            instruction_lines.append(
                "检测到列表下标越界（IndexError: list index out of range）。"
                "若代码使用 `for idx, row in df.iterrows()` 后再用 idx 访问任意列表/数组元素（如 `some_list[idx]`），"
                "请注意 iterrows 的 idx 是原 DataFrame 索引标签，不一定从 0 连续递增，"
                "会导致列表越界。请改为 `for pos, (_, row) in enumerate(df.iterrows())` 使用 pos 访问列表，"
                "或先 `df = df.reset_index(drop=True)` 再按位置索引。"
            )

        if "KeyError: 'code'" in validation_message:
            instruction_lines.append(
                "检测到列缺失错误（KeyError: 'code'）。请优先按以下顺序修复："
                "1) 在进入策略计算前显式校验必需列是否存在（至少包含 `code` 与时间列），不存在时 raise ValueError 并给出当前列名；"
                "2) 对 `groupby(...).apply(...)` 场景，禁止依赖 apply 后再从结果里回填分组键；"
                "3) 若必须保留分组键，请改为不丢分组列（例如移除 include_groups=False），"
                "或使用 `reset_index()` 从 MultiIndex 恢复分组键，再做后续列选择；"
                "4) 禁止使用易错的“按位置补列”写法（如从另一个 DataFrame 按 index 强行回填 `code`）。"
            )

        if (
            "calculation_contract_sample 性能检查不通过" in validation_message
            or "calculation_contract_sample 性能检查不通过" in output
        ):
            instruction_lines.append(
                "检测到循环中反复处理整张历史行情。请在循环外先把日线按规范化交易日期"
                "建立可重复使用的字典或日期索引；循环内根据 contract.required_offsets "
                "只取 t 与 t-k 对应的小表，合并后再传给 calculation_contract_sample。"
                "禁止在日期循环内把整张 history 直接传入，也禁止在循环内反复对整张表"
                "执行 copy、sort_values、loc/isin 或 groupby。"
                "如果 required_offsets=[0]，可以直接传按日期 groupby 得到的单日 date_rows。"
            )

        normalized_validation_message = validation_message.lower()
        if (
            "row_sum_mismatch" in normalized_validation_message
            or "do not use abs(weights)" in normalized_validation_message
            or "gross_abs_sum" in normalized_validation_message
        ):
            instruction_lines.append(
                "检测到权重归一化口径错误。请按语义约束修正："
                "将所有非现金资产列按原始符号直接求和（多头为正、空头为负），再与 cash 相加后应约等于 1（容差 1e-6）。"
                "禁止使用绝对值权重和做归一化或校验（例如先对仓位取绝对值再求和）。"
                "若存在多空仓位，绝对值口径会把多空都当正数，导致净敞口被错误缩放。"
            )

        if previous_code is not None and code.strip() == previous_code.strip():
            instruction_lines.append(
                "检测到本轮代码与上一轮完全一致；必须针对上述错误做实质修改，禁止原样重发。"
            )

        if repeated_error_count >= 2:
            instruction_lines.append(
                f"检测到同一错误已连续出现 {repeated_error_count} 次。"
                "必须只修改 traceback 对应局部逻辑并明确消除触发条件；"
                "禁止进行与报错无关的重写。"
            )
        if repeated_error_count >= 3:
            instruction_lines.append(
                "连续多轮同错未消除：请先在代码中加入最小可复现的边界保护，"
                "再恢复原有业务逻辑，确保本轮至少能通过执行阶段。"
            )

        return "\n\n".join(instruction_lines)

    @staticmethod
    def _contains_output_weights_call(code: str) -> bool:
        try:
            module = ast.parse(code)
        except SyntaxError:
            return False

        def _is_output_weights_call(value: ast.AST) -> bool:
            return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "output_weights"

        for node in ast.walk(module):
            if isinstance(node, ast.Assign):
                has_target = any(isinstance(target, ast.Name) and target.id == "output_weights_df" for target in node.targets)
                if has_target and _is_output_weights_call(node.value):
                    return True
            if isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "output_weights_df" and node.value is not None:
                    if _is_output_weights_call(node.value):
                        return True
        return False

    @staticmethod
    def _is_main_guard(node: ast.If) -> bool:
        """Detect `if __name__ == "__main__":` blocks."""

        test = node.test
        if not isinstance(test, ast.Compare):
            return False
        if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
            return False
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return False
        if len(test.comparators) != 1:
            return False
        comparator = test.comparators[0]
        return isinstance(comparator, ast.Constant) and comparator.value == "__main__"

    @classmethod
    def _contains_output_weights_assignment_under_main_guard(cls, code: str) -> bool:
        """Detect `output_weights_df = ...` assignment inside __main__ guard.

        In sandbox execution, __name__ is not '__main__', so this assignment never runs.
        """

        try:
            module = ast.parse(code)
        except SyntaxError:
            return False

        for node in ast.walk(module):
            if not isinstance(node, ast.If) or not cls._is_main_guard(node):
                continue

            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name) and target.id == "output_weights_df":
                            return True
                if isinstance(child, ast.AnnAssign):
                    target = child.target
                    if isinstance(target, ast.Name) and target.id == "output_weights_df":
                        return True

        return False

    @staticmethod
    def _resolve_next_phase(namespace: dict[str, Any], has_validation_summary: bool) -> tuple[str, str]:
        raw_phase = namespace.get("strategy_next_phase")
        raw_reason = namespace.get("strategy_decision_reason")
        reason = str(raw_reason).strip() if isinstance(raw_reason, str) else ""
        _ = has_validation_summary

        if isinstance(raw_phase, str) and raw_phase.strip().lower() == "test":
            reason = reason or "策略代码请求进入测试"
            return "validate", f"{reason}；新生成或修改的代码必须重新完成统计验证"

        return "validate", reason or "新生成或修改的代码必须先完成统计验证"

    @staticmethod
    def _supported_event_horizon(value: object, *, allow_text: bool = False) -> int | None:
        if isinstance(value, bool):
            return None
        if allow_text and isinstance(value, str) and re.fullmatch(r"(?:5|10|20)(?:\.0+)?", value.strip()):
            value = float(value.strip())
        if not isinstance(value, (int, float)) or value not in {5, 10, 20}:
            return None
        return int(value)

    @staticmethod
    def _approved_event_horizon(strategy_context: dict[str, object]) -> int | None:
        horizon_subject = (
            r"(?:事件(?:回测|评估|评价|收益)?(?:时间|时长|期限|窗口)|"
            r"持有(?:时间|时长|期限|窗口)|评价(?:时间|时点|期限|窗口)|"
            r"收益(?:时间|时长|期限|窗口))"
        )
        change_action = (
            r"(?:设为|改为|改到|改至|调整为|调整至|延长至|缩短至|"
            r"采用|使用|固定为)"
        )
        technical_pattern = re.compile(
            r"[`'\"]*event_horizon_minutes[`'\"]*\s*"
            r"(?:从\s*(?:5|10|20)\s*(?:分钟)?\s*)?"
            rf"(?:=|:|{change_action})\s*"
            r"[`'\"]*(5|10|20)(?:\.0+)?[`'\"]*\s*(?:分钟)?",
            flags=re.IGNORECASE,
        )
        natural_change_pattern = re.compile(
            rf"{horizon_subject}\s*"
            r"(?:从\s*(?:5|10|20)\s*分钟\s*)?"
            rf"(?:明确)?{change_action}\s*"
            r"(5|10|20)\s*分钟"
        )
        trigger_anchor_pattern = re.compile(
            rf"{horizon_subject}\s*(?:明确)?{change_action}\s*"
            r"(?:以\s*)?(?:trigger(?:_ts)?\s*后\s*)?"
            r"第?\s*(5|10|20)\s*分钟(?:的)?(?:收盘(?:价)?)?"
            r"(?:进行)?(?:评价|评估|退出|离场)?",
            flags=re.IGNORECASE,
        )
        complete_trigger_definition_pattern = re.compile(
            r"(?:以|按|使用)\s*trigger(?:_ts)?\s*后(?:的)?第\s*"
            r"(5|10|20)\s*分钟(?:的)?收盘(?:价)?\s*"
            r"(?:作为|进行)?(?:评价|评估|退出|离场)",
            flags=re.IGNORECASE,
        )
        approval_pattern = re.compile(
            r"(?:明确批准|批准)\s*(?:采用|使用)?\s*(5|10|20)\s*分钟\s*"
            rf"(?:的)?{horizon_subject}"
        )
        negative_before = re.compile(
            r"(?:拒绝|否决|不予批准|未批准|尚未批准|没有批准|禁止|不得|"
            r"不应|不宜|不允许|不建议|不采用|不使用|不(?:将|把))"
            r"[^，。；;！？!?]{0,24}$"
        )
        comparison_before = re.compile(
            r"(?:仅(?:用于|供)?|只是)?(?:比较|对比|诊断|试算|测试)\s*"
            r"(?:将|把)?\s*$"
        )
        negative_after = re.compile(
            r"^\s*(?:的)?(?:方案|改法|修改)?\s*(?:被|遭)?"
            r"(?:拒绝|否决|不予批准|未获批准|不采用|不使用)"
        )

        approved: set[int] = set()
        rejected: set[int] = set()
        for field in ("strategy_modification", "hypothesis"):
            text = str(strategy_context.get(field, "")).strip()
            if not text:
                continue
            for pattern in (
                technical_pattern,
                natural_change_pattern,
                trigger_anchor_pattern,
                complete_trigger_definition_pattern,
                approval_pattern,
            ):
                for match in pattern.finditer(text):
                    clause_start = max(
                        text.rfind(separator, 0, match.start())
                        for separator in ("，", "。", "；", ";", "！", "？", "!", "?")
                    )
                    clause_end_candidates = [
                        position
                        for separator in ("，", "。", "；", ";", "！", "？", "!", "?")
                        if (position := text.find(separator, match.end())) >= 0
                    ]
                    clause_end = (
                        min(clause_end_candidates)
                        if clause_end_candidates
                        else len(text)
                    )
                    prefix = text[clause_start + 1:match.start()]
                    suffix = text[match.end():clause_end]
                    horizon = int(match.group(1))
                    if (
                        negative_before.search(prefix)
                        or comparison_before.search(prefix)
                        or negative_after.search(suffix)
                    ):
                        rejected.add(horizon)
                        continue
                    approved.add(horizon)

        if approved & rejected:
            return None
        approved.difference_update(rejected)
        if len(approved) == 1:
            return approved.pop()
        return None

    @classmethod
    def _validate_event_horizon_params(
        cls,
        namespace: dict[str, Any],
        strategy_context: dict[str, object],
    ) -> tuple[bool, str]:
        experiment_spec = strategy_context.get("experiment_spec", {})
        if not isinstance(experiment_spec, dict):
            experiment_spec = {}
        configured = cls._supported_event_horizon(
            experiment_spec.get("event_horizon_minutes", 5),
            allow_text=True,
        )
        if configured is None:
            return False, "experiment_spec.event_horizon_minutes 必须是 5、10 或 20"

        params = namespace.get("params")
        if not isinstance(params, dict) or "event_horizon_minutes" not in params:
            return False, "event_parquet 的 params 必须显式包含 event_horizon_minutes"
        actual = cls._supported_event_horizon(params.get("event_horizon_minutes"))
        if actual is None:
            return False, "params.event_horizon_minutes 必须是数值 5、10 或 20"

        approved = cls._approved_event_horizon(strategy_context)
        expected = approved if approved is not None else configured
        if actual != expected:
            if approved is None:
                return False, (
                    f"params.event_horizon_minutes 应使用 experiment_spec 的 {configured}；"
                    "只有研究说明明确批准后才能改为其他时长"
                )
            return False, (
                f"研究说明明确批准的 event_horizon_minutes 是 {approved}，"
                f"代码 params 中却是 {actual}"
            )

        return True, "ok"

    @classmethod
    def _validate_output(cls, code: str, namespace: dict[str, Any]) -> tuple[bool, str]:
        pd_module = namespace.get("pd")
        if pd_module is None or not hasattr(pd_module, "DataFrame"):
            return False, "pandas is required in the sandbox"
        dataframe_type = getattr(pd_module, "DataFrame")

        output_weights_fn = namespace.get("output_weights")
        if not callable(output_weights_fn):
            return False, "missing output_weights function"

        if cls._contains_output_weights_assignment_under_main_guard(code):
            return False, (
                "output_weights_df assignment is under `if __name__ == \"__main__\"`; "
                "move it to top-level so sandbox execution can create output_weights_df"
            )

        if not cls._contains_output_weights_call(code):
            return False, "output_weights_df must be produced by calling output_weights(...)"

        output_weights_df = namespace.get("output_weights_df")
        if output_weights_df is None or not isinstance(output_weights_df, dataframe_type):
            return False, "missing output_weights_df DataFrame"
        output_weights_df = cast(Any, output_weights_df)
        if output_weights_df.empty:
            return False, "output_weights_df must not be empty"
        if "cash" not in output_weights_df.columns:
            return False, "output_weights_df must include a cash column"

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always", RuntimeWarning)
            numeric_df = output_weights_df.select_dtypes(include="number")

        runtime_warning_messages: list[str] = []
        for warning_record in caught_warnings:
            if issubclass(warning_record.category, RuntimeWarning):
                message = str(warning_record.message).strip()
                if message and message not in runtime_warning_messages:
                    runtime_warning_messages.append(message)
        if runtime_warning_messages:
            summary = " | ".join(runtime_warning_messages)
            return False, f"runtime warning during output_weights_df validation: {summary}"

        if "cash" not in numeric_df.columns:
            return False, "output_weights_df cash column must be numeric"

        cash_values = numeric_df["cash"].astype(float)
        if bool(((cash_values < -1e-12) | (cash_values > 1.0 + 1e-12)).any()):
            return False, "output_weights_df cash must stay between 0 and 1"

        weight_cols = [str(col) for col in numeric_df.columns if str(col) != "cash"]
        sparse_scan = None
        if any(
            isinstance(numeric_df[column].dtype, pd_module.SparseDtype)
            for column in numeric_df.columns
        ):
            from quanta_agents.agents.validate_agent import OutputWeightsRuleChecker

            sparse_scan = OutputWeightsRuleChecker._scan_sparse_weight_columns(
                numeric_df,
                [str(column) for column in numeric_df.columns],
            )
            if sparse_scan.get("numeric") is not True:
                return False, "output_weights_df contains non-numeric values"
            if sparse_scan.get("finite") is not True:
                return False, "output_weights_df contains non-finite values"
            signed_sums = pd_module.Series(
                sparse_scan["row_sums"],
                index=numeric_df.index,
            )
            gross_abs_sums = pd_module.Series(
                sparse_scan["gross_abs_sums"],
                index=numeric_df.index,
            )
        else:
            signed_sums = numeric_df[weight_cols].sum(axis=1) + numeric_df["cash"]
            gross_abs_sums = numeric_df[weight_cols].abs().sum(axis=1) + numeric_df["cash"]

        if hasattr(signed_sums, "isna") and bool(signed_sums.isna().any()):
            return False, "output_weights_df contains non-numeric values"

        bad_mask = (signed_sums - 1.0).abs() > 1e-6
        if bool(bad_mask.any()):
            bad_indices = list(signed_sums.index[bad_mask])
            preview_indices = bad_indices[:3]
            preview_parts: list[str] = []
            for bad_idx in preview_indices:
                signed_val = float(signed_sums.loc[bad_idx])
                gross_abs_val = float(gross_abs_sums.loc[bad_idx])
                cash_val = float(numeric_df.loc[bad_idx, "cash"])
                preview_parts.append(
                    f"idx={bad_idx}: signed_sum={signed_val:.6f}, gross_abs_sum={gross_abs_val:.6f}, cash={cash_val:.6f}"
                )

            abs_norm_like_count = int((((gross_abs_sums - 1.0).abs() <= 1e-6) & bad_mask).sum())
            abs_hint = ""
            if abs_norm_like_count > 0:
                abs_hint = (
                    f" detected {abs_norm_like_count} rows where gross_abs_sum≈1 but signed_sum!=1; "
                    "likely using abs(weights) normalization."
                )

            preview_text = "; ".join(preview_parts) if preview_parts else "no preview"
            return (
                False,
                "row_sum_mismatch: each row must satisfy signed_sum(weights)+cash=1 (do NOT use abs(weights)). "
                f"examples: {preview_text}; total_bad_rows={len(bad_indices)}.{abs_hint}"
            )

        strategy_output = namespace.get("strategy_output")
        if not isinstance(strategy_output, dict) or not strategy_output:
            return False, "missing strategy_output dict"

        output_weights_meta = strategy_output.get("output_weights_df")
        output_meta_key = "output_weights_df"
        if not isinstance(output_weights_meta, dict):
            return False, "strategy_output.output_weights_df must be an object"
        output_desc = output_weights_meta.get("description")
        output_fn_name = output_weights_meta.get("function_name")
        output_datetime_column = output_weights_meta.get("datetime_column")
        if not isinstance(output_desc, str) or not output_desc.strip():
            return False, f"strategy_output.{output_meta_key}.description must be a non-empty string"
        if not isinstance(output_fn_name, str) or not output_fn_name.strip():
            return False, f"strategy_output.{output_meta_key}.function_name must be a non-empty string"
        if output_fn_name.strip() != "output_weights":
            return False, f"strategy_output.{output_meta_key}.function_name must be output_weights"
        if not isinstance(output_datetime_column, str) or not output_datetime_column.strip():
            return False, f"strategy_output.{output_meta_key}.datetime_column must be a non-empty string"
        output_datetime_column = output_datetime_column.strip()
        if output_datetime_column not in output_weights_df.columns:
            return False, f"output_weights_df missing datetime column: {output_datetime_column}"

        strategy_context = namespace.get("strategy_context")
        experiment_spec = (
            strategy_context.get("experiment_spec", {})
            if isinstance(strategy_context, dict)
            else {}
        )
        event_mode = (
            isinstance(experiment_spec, dict)
            and str(experiment_spec.get("backtest_mode", "")).strip().lower()
            == "event_parquet"
        )
        if event_mode:
            horizon_valid, horizon_message = cls._validate_event_horizon_params(
                namespace,
                strategy_context,
            )
            if not horizon_valid:
                return False, horizon_message
            if output_datetime_column != "trigger_ts":
                return False, "event_parquet output_weights_df must use trigger_ts"
            parsed_times = pd_module.to_datetime(
                output_weights_df[output_datetime_column], errors="coerce"
            )
            if bool(parsed_times.isna().any()):
                return False, "output_weights_df trigger_ts contains invalid values"
            if bool((parsed_times == parsed_times.dt.normalize()).all()):
                return False, "event_parquet trigger_ts must retain minute time"
            event_weight_columns = [
                column
                for column in numeric_df.columns
                if str(column) != "cash"
            ]
            if event_weight_columns:
                if isinstance(sparse_scan, dict):
                    non_negative_by_column = sparse_scan.get(
                        "non_negative_by_column",
                        {},
                    )
                    has_negative_weight = not all(
                        non_negative_by_column.get(str(column)) is True
                        for column in event_weight_columns
                    )
                else:
                    has_negative_weight = bool(
                        (numeric_df[event_weight_columns] < -1e-12).any(axis=None)
                    )
                if has_negative_weight:
                    return False, "A-share minute event weights cannot be negative"

        strategy_next_phase = namespace.get("strategy_next_phase")
        if not isinstance(strategy_next_phase, str) or strategy_next_phase.strip().lower() != "validate":
            return False, "strategy_next_phase must be validate; strategy code cannot skip validation"

        strategy_decision_reason = namespace.get("strategy_decision_reason")
        if not isinstance(strategy_decision_reason, str) or not strategy_decision_reason.strip():
            return False, "strategy_decision_reason must be a non-empty string"

        intermediate_keys = [str(name) for name in strategy_output.keys() if str(name) != "output_weights_df"]

        for key in intermediate_keys:
            item = strategy_output.get(key)
            if not isinstance(item, dict):
                return False, f"strategy_output.{key} must be an object"
            description = item.get("description")
            function_name = item.get("function_name")
            expected = item.get("expected_characteristics")
            datetime_column = item.get("datetime_column")
            if not isinstance(description, str) or not description.strip():
                return False, f"strategy_output.{key}.description must be a non-empty string"
            if not isinstance(function_name, str) or not function_name.strip():
                return False, f"strategy_output.{key}.function_name must be a non-empty string"
            if not isinstance(expected, list) or not expected:
                return False, f"strategy_output.{key}.expected_characteristics must be a non-empty list"
            if any(not isinstance(characteristic, str) or not characteristic.strip() for characteristic in expected):
                return False, f"strategy_output.{key}.expected_characteristics must be a list of non-empty strings"
            if not isinstance(datetime_column, str) or not datetime_column.strip():
                return False, f"strategy_output.{key}.datetime_column must be a non-empty string"

        return True, "ok"

    def run(self, state: WorkflowState) -> WorkflowState:
        print_agent_progress(state, agent_name=self.name, message="starting")
        strategy_context = self._build_strategy_context(state)
        namespace = self._build_sandbox_namespace(strategy_context)
        validation_summary_obj = state.get("validation_summary", {})
        if not isinstance(validation_summary_obj, dict):
            validation_summary_obj = {}
        validation_summary_json = json.dumps(validation_summary_obj, ensure_ascii=False, indent=2)
        has_validation_summary = bool(validation_summary_obj)
        previous_strategy_code, previous_strategy_source = self._extract_previous_strategy_code(state)
        previous_code_diff = self._extract_previous_code_diff(state)
        code_change_summary = self._build_code_change_summary(strategy_context, validation_summary_obj)
        experiment_spec = strategy_context.get("experiment_spec", {})
        if not isinstance(experiment_spec, dict):
            experiment_spec = {}
        backtest_mode = str(experiment_spec.get("backtest_mode", "")).strip().lower()
        position_plan_contract = experiment_spec.get("position_plan_contract", {})
        if not isinstance(position_plan_contract, dict):
            position_plan_contract = {}
        market_day_continuity_contract = experiment_spec.get(
            "market_day_continuity_contract", {}
        )
        if not isinstance(market_day_continuity_contract, dict):
            market_day_continuity_contract = {}
        event_horizon_minutes = self._supported_event_horizon(
            experiment_spec.get("event_horizon_minutes", 5),
            allow_text=True,
        ) or 5

        system_prompt = load_agent_prompt(
            "strategy_agent",
            "system_prompt",
        )
        user_prompt = load_agent_prompt(
            "strategy_agent",
            "user_prompt_template",
            hypothesis=strategy_context.get("hypothesis", ""),
            strategy_modification=strategy_context.get("strategy_modification", ""),
            required_data_descriptions=strategy_context.get("required_data_descriptions", ""),
            calculation_contracts_json=json.dumps(
                strategy_context.get("calculation_contracts", []),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            research_category=strategy_context.get("research_category", ""),
            mechanism_id=strategy_context.get("mechanism_id", ""),
            variant_mode=strategy_context.get("variant_mode", ""),
            new_signal_definition_json=json.dumps(
                strategy_context.get("new_signal_definition", {}),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            mechanism_signal_reference_source=strategy_context.get(
                "mechanism_signal_reference_source", ""
            ),
            validation_summary_json=validation_summary_json,
            previous_strategy_code=previous_strategy_code,
            previous_strategy_source=previous_strategy_source,
            previous_code_diff=previous_code_diff,
            code_change_summary=code_change_summary,
            backtest_mode=backtest_mode,
            position_plan_contract_json=(
                json.dumps(
                    position_plan_contract,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                if position_plan_contract
                else ""
            ),
            market_day_continuity_contract_json=(
                json.dumps(
                    market_day_continuity_contract,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                if market_day_continuity_contract
                else ""
            ),
            event_horizon_minutes=event_horizon_minutes,
        )

        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        all_code_parts: list[str] = []
        last_error: Exception | None = None
        last_validation_message: str | None = None
        repeated_error_count = 0
        max_steps = min(self.max_retries, _MAX_CODEACT_STEPS)
        accepted_code: str | None = None

        for step in range(1, max_steps + 1):
            try:
                print_agent_progress(state, agent_name=self.name, message=f"codeact step {step}/{max_steps}")
                write_trace_text(
                    state,
                    agent_name=self.name,
                    stage="llm_input",
                    text="\n\n".join(
                        [
                            f"[{str(message.get('role', 'unknown')).strip()}]\n{str(message.get('content', ''))}"
                            for message in messages
                        ]
                    ),
                    attempt=step,
                )
                response = llm_client.complete_messages(
                    messages,
                    temperature=0.15,
                    max_tokens=10000,
                    role="strategy",
                )
                messages.append({"role": "assistant", "content": response})
                write_trace_text(
                    state,
                    agent_name=self.name,
                    stage="llm_output",
                    text=response,
                    attempt=step,
                )

                code = self._extract_code_block(response)
                if code:
                    self._write_code_trace(state, code, step)
                    all_code_parts.append(code)
                    experiment_spec = strategy_context.get("experiment_spec", {})
                    event_mode = (
                        isinstance(experiment_spec, dict)
                        and str(experiment_spec.get("backtest_mode", "")).strip().lower()
                        == "event_parquet"
                    )
                    try:
                        validate_generated_strategy_code(code, event_mode=event_mode)
                        check_calculation_contract_call_performance(code)
                    except ValueError as exc:
                        output, success = f"Error:\n{exc}", False
                    else:
                        output, success = self._execute_code(code, namespace)
                    if success:
                        valid, validation_message = self._validate_output(code, namespace)
                    else:
                        valid = False
                        validation_message = self._summarize_execution_error(output)
                    candidate_mode = str(
                        strategy_context.get("candidate_mode", "baseline")
                    ).strip()
                    if success and valid and previous_strategy_code:
                        change_ratio = self._code_change_ratio(
                            previous_strategy_code,
                            code,
                        )
                        if change_ratio == 0.0 and candidate_mode != "baseline":
                            valid = False
                            validation_message = (
                                "本轮候选代码与上一版完全相同；必须完成候选类型要求的实际修改。"
                            )
                        elif candidate_mode == "modify_one_rule":
                            experiment_spec = strategy_context.get("experiment_spec", {})
                            raw_limit = (
                                experiment_spec.get("max_single_change_ratio", 0.35)
                                if isinstance(experiment_spec, dict)
                                else 0.35
                            )
                            try:
                                change_limit = float(raw_limit)
                            except (TypeError, ValueError):
                                change_limit = 0.35
                            change_limit = min(1.0, max(0.05, change_limit))
                            if change_ratio > change_limit:
                                valid = False
                                validation_message = (
                                    f"modify_one_rule 只能做局部修改；当前代码变化比例 {change_ratio:.1%} "
                                    f"超过允许的 {change_limit:.1%}。请保留无关代码。"
                                )
                    write_trace_json(
                        state,
                        agent_name=self.name,
                        stage="code_execution",
                        payload={
                            "step": step,
                            "success": success,
                            "validation_passed": valid,
                            "validation_message": validation_message,
                            "output": output[:_MAX_OUTPUT_CHARS],
                        },
                        attempt=step,
                    )
                    if success and valid:
                        accepted_code = code
                        break

                    if validation_message == last_validation_message:
                        repeated_error_count += 1
                    else:
                        repeated_error_count = 1
                        last_validation_message = validation_message

                    previous_code = all_code_parts[-2] if len(all_code_parts) >= 2 else None
                    messages.append(
                        {
                            "role": "user",
                            "content": self._build_retry_instruction(
                                code=code,
                                previous_code=previous_code,
                                validation_message=validation_message,
                                output=output,
                                repeated_error_count=repeated_error_count,
                            ),
                        }
                    )
                    continue

                raise ValueError("LLM response must contain a Python code block")

            except Exception as exc:
                last_error = exc
                write_trace_json(
                    state,
                    agent_name=self.name,
                    stage="step_error",
                    payload={"step": step, "error": str(exc)},
                    attempt=step,
                )
                messages.append({"role": "user", "content": f"步骤出错: {exc}\n请修正后重试。"})

        full_code = accepted_code.strip() if isinstance(accepted_code, str) else ""
        if not full_code:
            raise AgentExecutionError(
                agent_name=self.name,
                attempts=max_steps,
                message=(
                    str(last_error)
                    if last_error is not None
                    else last_validation_message or "StrategyAgent did not generate valid executable code"
                ),
            )

        output_weights = namespace.get("output_weights_df")
        strategy_output = namespace.get("strategy_output")
        if not isinstance(strategy_output, dict):
            raise AgentExecutionError(
                agent_name=self.name,
                attempts=max_steps,
                message="generated code did not define strategy_output dict",
            )

        strategy_result = {
            "output_weights": output_weights,
            "strategy_output": strategy_output,
            "params": namespace.get("params", {}),
            "required_data": strategy_context.get("required_data", []),
            "required_data_descriptions": strategy_context.get(
                "required_data_descriptions",
                "",
            ),
            "universe": strategy_context.get("universe", []),
            "backtest_datasets": strategy_context.get("backtest_datasets", []),
            "calculation_contracts": strategy_context.get("calculation_contracts", []),
            "train_data_bundle": strategy_context.get("train_data_bundle", {}),
            "validate_data_bundle": strategy_context.get("validate_data_bundle", {}),
            "train_period": {"start": strategy_context.get("train_start"), "end": strategy_context.get("train_end")},
            "validate_period": {"start": strategy_context.get("validate_start"), "end": strategy_context.get("validate_end")},
        }

        strategy_generation_meta = StrategyAgent._build_strategy_generation_meta(strategy_context, strategy_result)
        next_phase, decision_reason = self._resolve_next_phase(namespace, has_validation_summary)
        code_diff = self._build_code_diff(previous_strategy_code, full_code)
        strategy_generation_meta["strategy_next_phase"] = next_phase
        strategy_generation_meta["strategy_decision_reason"] = decision_reason
        strategy_generation_meta["based_on_previous_strategy"] = bool(previous_strategy_code)
        strategy_generation_meta["previous_strategy_source"] = previous_strategy_source
        strategy_generation_meta["requested_code_changes"] = code_change_summary
        strategy_generation_meta["code_diff"] = code_diff[:_MAX_CODE_DIFF_CHARS]
        strategy_generation_meta["candidate_mode"] = str(
            strategy_context.get("candidate_mode", "baseline")
        )
        strategy_generation_meta["code_change_ratio"] = self._code_change_ratio(
            previous_strategy_code,
            full_code,
        ) if previous_strategy_code else None
        strategy_generation_meta["validation_required"] = True

        if previous_strategy_code:
            write_trace_text(
                state,
                agent_name=self.name,
                stage="code_diff",
                text=code_diff or "上一版与本轮生成代码完全相同；仍需重新验证。",
            )

        state["strategy_code"] = full_code
        state["code_text"] = full_code
        state["strategy_result"] = strategy_result
        state["test_result"] = None
        state["validation_result"] = {}
        state["validation_summary"] = {}
        state["validation_feedback"] = {}
        state["strategy_generation_meta"] = strategy_generation_meta
        state["selected_template"] = "codeact_strategy"
        state["selected_base_class"] = "CodeActStrategy"
        state["phase"] = cast(Any, next_phase)
        state["history"].append(
            f"Epoch {state['epoch_index']}: StrategyAgent generated code and routed to {next_phase} ({len(strategy_output)} variables)"
        )
        write_trace_json(
            state,
            agent_name=self.name,
            stage="structured_output",
            payload={
                "strategy_generation_meta": strategy_generation_meta,
                "strategy_next_phase": next_phase,
                "strategy_decision_reason": decision_reason,
                "universe": strategy_result.get("universe", []),
                "validate_period": strategy_result.get("validate_period"),
            },
        )
        print_agent_progress(state, agent_name=self.name, message="completed")
        return state
