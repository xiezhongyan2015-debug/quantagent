from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

try:
    import pandas as pd  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    pd = None

from quanta_agents.semantic.metadata import DolphinDBConnector, MetadataManager
from quanta_agents.semantic import load_required_data, resolve_universe_symbols
from quanta_agents.config import get_max_strategy_validate_rounds
from quanta_agents.period_data import (
    combine_historical_membership_data,
    filter_membership_for_period,
    is_dated_membership_item,
)
from quanta_agents.research_evaluation import (
    EvaluationFold,
    build_development_folds,
    daily_return_moments,
    summarize_development_results,
)
from quanta_agents.state import (
    BacktestResult,
    DEFAULT_EVENT_SUCCESS_METRIC,
    WorkflowState,
    resolve_development_period,
    resolve_final_test_period,
    resolve_quality_gate,
    select_event_success_value,
)
from quanta_agents.strategy_code_policy import (
    compile_strategy_definitions,
    validate_generated_strategy_code,
)
from quanta_agents.trace_logger import get_trace_run_dir, print_agent_progress, write_trace_json


class StrategyTester:
    name = "StrategyTester"

    REQUIRED_PLUGIN_BY_TEMPLATE_FAMILY = {
        "portfolio": "vnpy_portfoliostrategy",
    }

    BASE_CLASS_CANDIDATES = {
        "StrategyTemplate": {"StrategyTemplate", "PortfolioStrategyTemplate"},
        "PortfolioStrategyTemplate": {"StrategyTemplate", "PortfolioStrategyTemplate"},
    }

    EXCHANGE_SUFFIX_ALIASES = {
        "SZ": ("SZSE", "SZ"),
        "SZSE": ("SZSE", "SZ"),
        "SH": ("SSE", "SH"),
        "SSE": ("SSE", "SH"),
    }

    BACKTEST_SUFFIX_CANONICAL = {
        "SZ": "SZSE",
        "SZSE": "SZSE",
        "SH": "SSE",
        "SSE": "SSE",
    }

    @classmethod
    def _runtime_env_snapshot(cls) -> dict[str, object]:
        module_presence: dict[str, bool] = {}
        for name in cls.REQUIRED_PLUGIN_BY_TEMPLATE_FAMILY.values():
            module_presence[name] = importlib.util.find_spec(name) is not None
        backtest_db_backend = cls._resolve_backtest_db_backend()
        configured_timezone = os.getenv("DOLPHINDB_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
        return {
            "python_executable": sys.executable,
            "python_version": sys.version.split(" ", maxsplit=1)[0],
            "module_presence": module_presence,
            "backtest_db_backend": backtest_db_backend,
            "configured_timezone": configured_timezone,
        }

    @staticmethod
    def _resolve_backtest_db_backend(backtest_dataset_specs: list[dict[str, object]] | None = None) -> str:
        _ = backtest_dataset_specs
        backend = os.getenv(
            "QUANTA_BACKTEST_DB_BACKEND",
            os.getenv("QUANTA_DATA_ENGINE", "dolphindb"),
        ).strip().lower()
        if backend in {"sqlite", "dolphindb", "parquet", "event_parquet"}:
            return backend
        return "dolphindb"

    @staticmethod
    def _resolve_project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _resolve_vnpy_sqlite_db_path(cls) -> str:
        raw_path = os.getenv("SQLITE_BACKTEST_DB_PATH", "./vnpy_bar.sqlite3").strip()
        db_path = Path(raw_path).expanduser()
        if not db_path.is_absolute():
            db_path = cls._resolve_project_root() / db_path
        return str(db_path)

    @staticmethod
    def _resolve_dolphindb_backtest_db_path() -> str:
        raw_name = os.getenv("DOLPHINDB_BACKTEST_DB", "dfs://vnpy_bar_db").strip()
        if raw_name.startswith("dfs://"):
            return raw_name
        return f"dfs://{raw_name}"

    @classmethod
    def _resolve_backtest_table_configs(
        cls,
        backtest_dataset_specs: list[dict[str, object]] | None,
        backend: str,
    ) -> list[dict[str, str]]:
        configs: list[dict[str, str]] = []
        seen: set[str] = set()

        for spec in backtest_dataset_specs or []:
            if not isinstance(spec, dict):
                continue

            table_name = cls._resolve_backtest_table_name(spec, backend)
            if table_name in seen:
                continue
            seen.add(table_name)

            interval_value = str(spec.get("interval", "")).strip()

            configs.append(
                {
                    "table_name": table_name,
                    "symbol_column": "symbol",
                    "exchange_column": "exchange",
                    "datetime_column": "datetime",
                    "interval_column": "interval",
                    "interval_value": interval_value,
                }
            )

        return configs

    @staticmethod
    def _resolve_interval_filter_values(interval: Any, configured_interval: str | None = None) -> list[str]:
        values: list[str] = []
        interval_value = str(getattr(interval, "value", "")).strip()
        if interval_value:
            values.append(interval_value)
        if isinstance(configured_interval, str) and configured_interval.strip():
            configured = configured_interval.strip()
            if configured not in values:
                values.append(configured)
        return values

    @classmethod
    def _resolve_backtest_dataset_specs(
        cls,
        state: WorkflowState,
        strategy_result: dict[str, object],
    ) -> list[dict[str, object]]:
        raw_keys = strategy_result.get("backtest_datasets")
        if not isinstance(raw_keys, list) or not raw_keys:
            hypothesis_meta = state.get("hypothesis_generation_meta", {})
            if isinstance(hypothesis_meta, dict):
                raw_keys = hypothesis_meta.get("backtest_datasets")

        dataset_keys = [
            str(item).strip()
            for item in raw_keys
            if isinstance(item, str) and str(item).strip()
        ] if isinstance(raw_keys, list) else []

        if not dataset_keys:
            raise RuntimeError("backtest_datasets is required and must be a non-empty list")

        metadata_manager = MetadataManager.from_yaml_files()
        resolved_specs: list[dict[str, object]] = []
        for key in dataset_keys:
            spec = metadata_manager.get_backtest_dataset(key)
            if not isinstance(spec, dict):
                raise RuntimeError(f"unknown backtest dataset key: {key}")
            resolved_specs.append(spec)

        return resolved_specs

    @staticmethod
    def _resolve_backtest_table_name(backtest_dataset_spec: dict[str, object] | None, backend: str) -> str:
        table_name = ""
        if isinstance(backtest_dataset_spec, dict):
            value = backtest_dataset_spec.get("table_key")
            if isinstance(value, str):
                table_name = value.strip()

        if table_name:
            return table_name

        raise RuntimeError("backtest_datasets entries must define table_key")

    @staticmethod
    def _resolve_vt_symbol(experiment_spec: dict[str, object]) -> str:
        vt_symbols = StrategyTester._resolve_vt_symbols(experiment_spec)
        if vt_symbols:
            return vt_symbols[0]

        return "000001.SZSE"

    @staticmethod
    def _resolve_vt_symbols(experiment_spec: dict[str, object]) -> list[str]:
        resolved: list[str] = []
        seen: set[str] = set()

        def add_symbol(value: object) -> None:
            if not isinstance(value, str):
                return
            symbol = value.strip()
            if not symbol or symbol in seen:
                return
            seen.add(symbol)
            resolved.append(symbol)

        universe = experiment_spec.get("universe")
        for item in resolve_universe_symbols(universe):
            add_symbol(item)

        if not resolved:
            resolved.append("000001.SZSE")

        resolved = [StrategyTester._normalize_vt_symbol_for_backtest(symbol) for symbol in resolved]

        return resolved

    @classmethod
    def _normalize_vt_symbol_for_backtest(cls, value: object) -> str:
        text = str(value).strip()
        if not text or "." not in text:
            return text
        symbol, suffix = cls._split_vt_symbol(text)
        if not symbol:
            return text
        canonical_suffix = cls.BACKTEST_SUFFIX_CANONICAL.get(suffix, suffix)
        return f"{symbol}.{canonical_suffix}"

    @staticmethod
    def _split_vt_symbol(value: object) -> tuple[str, str]:
        text = str(value).strip()
        if not text:
            return "", ""
        if "." not in text:
            return text.upper(), ""
        symbol, suffix = text.rsplit(".", maxsplit=1)
        return symbol.strip().upper(), suffix.strip().upper()

    @classmethod
    def _build_available_symbol_index(cls, available_vt_symbols: list[str]) -> tuple[set[str], dict[str, list[str]]]:
        available_lookup: set[str] = set()
        available_by_symbol: dict[str, list[str]] = {}
        for item in available_vt_symbols:
            raw = str(item).strip()
            if not raw:
                continue
            available_lookup.add(raw)
            symbol, _ = cls._split_vt_symbol(raw)
            if not symbol:
                continue
            candidates = available_by_symbol.setdefault(symbol, [])
            if raw not in candidates:
                candidates.append(raw)
        return available_lookup, available_by_symbol

    @classmethod
    def _map_symbol_to_available(
        cls,
        symbol: str,
        available_lookup: set[str],
        available_by_symbol: dict[str, list[str]],
    ) -> str:
        raw = cls._normalize_vt_symbol_for_backtest(symbol)
        if not raw:
            return raw
        if raw in available_lookup:
            return raw

        symbol_code, suffix = cls._split_vt_symbol(raw)
        if not symbol_code:
            return raw

        candidates = available_by_symbol.get(symbol_code, [])
        if not candidates:
            return raw
        if len(candidates) == 1:
            return candidates[0]

        priority_suffixes = cls.EXCHANGE_SUFFIX_ALIASES.get(suffix, (suffix,)) if suffix else ()
        if priority_suffixes:
            for preferred_suffix in priority_suffixes:
                for candidate in candidates:
                    _, candidate_suffix = cls._split_vt_symbol(candidate)
                    if candidate_suffix == preferred_suffix:
                        return candidate

        return candidates[0]

    @classmethod
    def _align_symbols_to_available(
        cls,
        symbols: list[str],
        available_vt_symbols: list[str],
    ) -> tuple[list[str], dict[str, str]]:
        if not symbols or not available_vt_symbols:
            return symbols, {}

        available_lookup, available_by_symbol = cls._build_available_symbol_index(available_vt_symbols)
        resolved: list[str] = []
        seen: set[str] = set()
        mapping: dict[str, str] = {}

        for item in symbols:
            raw = cls._normalize_vt_symbol_for_backtest(item)
            if not raw:
                continue
            mapped = cls._map_symbol_to_available(raw, available_lookup, available_by_symbol)
            mapping[raw] = mapped
            if mapped in seen:
                continue
            seen.add(mapped)
            resolved.append(mapped)

        return resolved, mapping

    @staticmethod
    def _intersect_vt_symbols(vt_symbols: list[str], available_vt_symbols: list[str]) -> list[str]:
        available = {symbol.strip() for symbol in available_vt_symbols if isinstance(symbol, str) and symbol.strip()}
        intersected: list[str] = []
        seen: set[str] = set()
        for symbol in vt_symbols:
            raw_symbol = str(symbol).strip()
            if raw_symbol in available and raw_symbol not in seen:
                seen.add(raw_symbol)
                intersected.append(raw_symbol)
        return intersected

    @staticmethod
    def _load_available_bar_vt_symbols(
        backtest_db_backend: str | None = None,
        backtest_dataset_specs: list[dict[str, object]] | None = None,
    ) -> list[str]:
        backend = (backtest_db_backend or StrategyTester._resolve_backtest_db_backend()).strip().lower()
        if backtest_dataset_specs:
            resolved: list[str] = []
            seen: set[str] = set()
            for spec in backtest_dataset_specs:
                if backend == "sqlite":
                    current = StrategyTester._load_available_bar_vt_symbols_from_sqlite(dataset_spec=spec)
                else:
                    current = StrategyTester._load_available_bar_vt_symbols_from_dolphindb(dataset_spec=spec)
                for symbol in current:
                    raw_symbol = str(symbol).strip()
                    if not raw_symbol or raw_symbol in seen:
                        continue
                    seen.add(raw_symbol)
                    resolved.append(raw_symbol)
            return resolved

        if backend == "sqlite":
            return StrategyTester._load_available_bar_vt_symbols_from_sqlite()
        return StrategyTester._load_available_bar_vt_symbols_from_dolphindb()

    @staticmethod
    def _load_available_bar_vt_symbols_from_dolphindb(dataset_spec: dict[str, object] | None = None) -> list[str]:
        connector = DolphinDBConnector()
        backend = "dolphindb"
        table_name = StrategyTester._resolve_backtest_table_name(dataset_spec, backend)
        db_ref = StrategyTester._resolve_dolphindb_backtest_db_path()
        query = (
            f'select distinct symbol as symbol, exchange as exchange '
            f'from loadTable("{db_ref}", "{table_name}")'
        )
        df = connector.execute_query(query)
        if pd is None or not isinstance(df, pd.DataFrame):
            return []
        if df.empty or not {"symbol", "exchange"}.issubset(df.columns):
            return []

        symbols: list[str] = []
        for row in df[["symbol", "exchange"]].dropna().itertuples(index=False):
            symbol = str(getattr(row, "symbol")).strip()
            exchange = str(getattr(row, "exchange")).strip()
            if not symbol or not exchange:
                continue
            vt_symbol = symbol if symbol.endswith((".SSE", ".SZSE")) else f"{symbol}.{exchange}"
            if vt_symbol not in symbols:
                symbols.append(vt_symbol)
        return symbols

    @staticmethod
    def _load_available_bar_vt_symbols_from_sqlite(dataset_spec: dict[str, object] | None = None) -> list[str]:
        db_path = StrategyTester._resolve_vnpy_sqlite_db_path()
        bar_table = StrategyTester._resolve_backtest_table_name(dataset_spec, "sqlite")

        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                (
                    f"SELECT DISTINCT symbol, exchange "
                    f"FROM {bar_table} "
                    f"WHERE symbol IS NOT NULL AND exchange IS NOT NULL"
                )
            )
            rows = cursor.fetchall()

        symbols: list[str] = []
        for row in rows:
            symbol = str(row[0]).strip()
            exchange = str(row[1]).strip()
            if not symbol or not exchange:
                continue
            vt_symbol = symbol if symbol.endswith((".SSE", ".SZSE")) else f"{symbol}.{exchange}"
            if vt_symbol not in symbols:
                symbols.append(vt_symbol)
        return symbols

    @staticmethod
    def _parse_datetime(value: object, default_value: datetime) -> datetime:
        if isinstance(value, str) and value.strip():
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(value.strip(), fmt)
                except ValueError:
                    continue
        return default_value

    @staticmethod
    def _resolve_backtest_window(
        experiment_spec: dict[str, object],
        evaluation_stage: str = "development",
    ) -> tuple[datetime, datetime]:
        period = (
            resolve_final_test_period(experiment_spec)
            if evaluation_stage == "final_test"
            else resolve_development_period(experiment_spec)
        )
        if evaluation_stage != "final_test" and not period:
            legacy_start = experiment_spec.get("backtest_start")
            legacy_end = experiment_spec.get("backtest_end")
            if (
                isinstance(legacy_start, str)
                and legacy_start.strip()
                and isinstance(legacy_end, str)
                and legacy_end.strip()
            ):
                period = {
                    "start": legacy_start.strip(),
                    "end": legacy_end.strip(),
                }
        if not period:
            required = (
                "final_test_start/final_test_end (or backtest_start/backtest_end)"
                if evaluation_stage == "final_test"
                else "development_start/development_end (or validate_start/validate_end)"
            )
            raise ValueError(f"{evaluation_stage} requires explicit {required}")
        invalid_date = datetime(1900, 1, 1)
        start = StrategyTester._parse_datetime(period.get("start"), invalid_date)
        end = StrategyTester._parse_datetime(period.get("end"), invalid_date)
        if start == invalid_date or end == invalid_date:
            raise ValueError(f"{evaluation_stage} period contains an invalid date")
        if end <= start:
            raise ValueError(f"{evaluation_stage} period end must be after its start")
        return start, end

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
    def _resolve_period_text(*values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _reload_required_bundle(
        cls,
        required_data_items: list[dict[str, object]],
        start_text: str,
        end_text: str,
    ) -> dict[str, Any]:
        scoped_required_data: list[dict[str, object]] = []
        for item in required_data_items:
            scoped_item = deepcopy(item)
            if scoped_item.get("type") in {"time_series", "auxiliary", "panel"}:
                scoped_item["time_range"] = {"start": start_text, "end": end_text}
            scoped_required_data.append(scoped_item)

        loaded = load_required_data(scoped_required_data)
        bundle = cls._build_data_bundle(loaded)
        for item in scoped_required_data:
            if not is_dated_membership_item(item):
                continue
            table_key = str(item.get("table_key", item.get("name", ""))).strip()
            if table_key and table_key in bundle:
                bundle[table_key] = filter_membership_for_period(
                    bundle[table_key],
                    start_text,
                    end_text,
                )
        return bundle

    @classmethod
    def _recompute_output_weights_for_backtest(
        cls,
        state: WorkflowState,
        strategy_result: dict[str, object],
        experiment_spec: dict[str, object],
        backtest_start: datetime,
        backtest_end: datetime,
        training_end: datetime | str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        if not isinstance(strategy_result, dict) or not strategy_result:
            raise RuntimeError("strategy_result is empty; cannot recompute output_weights")

        required_data_items_raw = strategy_result.get("required_data")
        if not isinstance(required_data_items_raw, list) or not required_data_items_raw:
            raise RuntimeError("strategy_result.required_data is missing or empty")
        required_data_items: list[dict[str, object]] = []
        for item in required_data_items_raw:
            if isinstance(item, dict):
                required_data_items.append(item)
        if not required_data_items:
            raise RuntimeError("strategy_result.required_data has no valid entries")

        train_period = strategy_result.get("train_period")
        train_start = cls._resolve_period_text(
            experiment_spec.get("train_start"),
            train_period.get("start") if isinstance(train_period, dict) else None,
        )
        default_training_end = cls._resolve_period_text(
            experiment_spec.get("train_end"),
            train_period.get("end") if isinstance(train_period, dict) else None,
        )
        if isinstance(training_end, datetime):
            resolved_training_end = training_end.strftime("%Y-%m-%d")
        else:
            resolved_training_end = cls._resolve_period_text(training_end, default_training_end)
        if not train_start or not resolved_training_end:
            raise RuntimeError("train_start/training_end is required to recompute output_weights")

        backtest_start_text = backtest_start.strftime("%Y-%m-%d")
        backtest_end_text = backtest_end.strftime("%Y-%m-%d")

        recompute_train_bundle = cls._reload_required_bundle(
            required_data_items,
            train_start,
            resolved_training_end,
        )
        recompute_validate_bundle = cls._reload_required_bundle(required_data_items, backtest_start_text, backtest_end_text)

        code_text = str(state.get("strategy_code") or state.get("code_text") or "")
        if not code_text.strip():
            raise RuntimeError("missing strategy code; cannot execute output_weights")

        try:
            import numpy as np  # type: ignore[import-not-found]
        except Exception:
            np = None  # type: ignore[assignment]

        namespace: dict[str, Any] = {
            "__name__": "__generated_strategy__",
            "json": json,
            "np": np,
            "pd": pd,
            "universe": strategy_result.get("universe", experiment_spec.get("universe", [])),
            "train_data_bundle": recompute_train_bundle,
            "validate_data_bundle": recompute_validate_bundle,
        }
        event_mode = str(experiment_spec.get("backtest_mode", "")).strip().lower() == "event_parquet"
        validate_generated_strategy_code(code_text, event_mode=event_mode)
        exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102

        output_weights_fn = namespace.get("output_weights")
        if not callable(output_weights_fn):
            raise RuntimeError("strategy code missing callable output_weights")

        frozen_params = strategy_result.get("params")
        if "params" in strategy_result and isinstance(frozen_params, dict):
            call_params = deepcopy(frozen_params)
            params_source = "strategy_result.params"
        else:
            generated_params = namespace.get("params", {})
            call_params = deepcopy(generated_params) if isinstance(generated_params, dict) else {}
            params_source = "strategy_code.params"
        for start_key, end_key in (
            ("validation_start", "validation_end"),
            ("validate_start", "validate_end"),
            ("development_start", "development_end"),
            ("backtest_start", "backtest_end"),
            ("test_start", "test_end"),
        ):
            if start_key in call_params or end_key in call_params:
                call_params[start_key] = backtest_start_text
                call_params[end_key] = backtest_end_text
        date_replacements = {
            "train_start": train_start,
            "train_end": resolved_training_end,
            "training_end": resolved_training_end,
            "first_decision_date": resolved_training_end,
            "first_execution_date": backtest_start_text,
            "final_execution_date": backtest_end_text,
        }
        for key, value in date_replacements.items():
            if key in call_params:
                call_params[key] = value
        # 生成代码有时会从模块变量 params 读取日期，同时也会接收函数参数。
        # 两处都替换成这次实际检查的时期，避免把开发期日期带入最终测试。
        namespace["params"] = call_params
        expected_params_text = repr(call_params)
        output_weights_df = output_weights_fn(recompute_train_bundle, recompute_validate_bundle, call_params)
        if repr(call_params) != expected_params_text:
            raise RuntimeError("output_weights must not modify frozen strategy params")

        if pd is not None and not isinstance(output_weights_df, pd.DataFrame):
            raise RuntimeError("output_weights(...) did not return a pandas DataFrame")

        recompute_meta: dict[str, Any] = {
            "train_range": {"start": train_start, "end": resolved_training_end},
            "validate_range": {"start": backtest_start_text, "end": backtest_end_text},
            "params_source": params_source,
        }
        membership_df = combine_historical_membership_data(
            recompute_validate_bundle,
            strategy_result.get("required_data", []),
        )
        if membership_df is not None:
            recompute_meta["_membership_df"] = membership_df
        if pd is not None and isinstance(output_weights_df, pd.DataFrame):
            preview = [str(col) for col in list(output_weights_df.columns)[:16]]
            if len(output_weights_df.columns) > 16:
                preview.append(f"... (+{len(output_weights_df.columns) - 16} more)")
            datetime_column = cls._resolve_output_weights_time_column(strategy_result, output_weights_df)
            recompute_meta["output_weights_summary"] = {
                "shape": [int(output_weights_df.shape[0]), int(output_weights_df.shape[1])],
                "columns_preview": preview,
            }
            recompute_meta["output_weights_activity"] = cls._summarize_output_weights_activity(
                output_weights_df,
                datetime_column=datetime_column,
            )

        return output_weights_df, recompute_meta

    @staticmethod
    def _has_development_periods(experiment_spec: dict[str, object]) -> bool:
        required = (
            "train_start",
            "train_end",
            "validate_start",
            "validate_end",
        )
        return all(
            isinstance(experiment_spec.get(key), str)
            and bool(str(experiment_spec.get(key)).strip())
            for key in required
        )

    @classmethod
    def _requests_fixed_development_checks(
        cls,
        experiment_spec: dict[str, object],
    ) -> bool:
        fold_count = int(
            cls._resolve_float_config(experiment_spec, "development_folds", 1.0)
        )
        return (
            fold_count > 1
            or (
                "run_cost_stress" in experiment_spec
                and cls._resolve_bool_config(experiment_spec, "run_cost_stress", False)
            )
            or (
                "run_delay_stress" in experiment_spec
                and cls._resolve_bool_config(experiment_spec, "run_delay_stress", False)
            )
        )

    @classmethod
    def _delay_output_weights_one_day(
        cls,
        output_weights_df: Any,
        strategy_result: dict[str, object],
        market_dates: list[object] | Any,
    ) -> Any:
        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            return output_weights_df
        delayed = output_weights_df.copy()
        datetime_column = cls._resolve_output_weights_time_column(
            strategy_result,
            delayed,
        )
        calendar = pd.DatetimeIndex(pd.to_datetime(list(market_dates), errors="coerce"))
        calendar = calendar[~calendar.isna()].tz_localize(None).normalize().unique().sort_values()
        if calendar.empty:
            raise RuntimeError("one-day delay test needs actual market dates")

        if isinstance(datetime_column, str) and datetime_column in delayed.columns:
            signal_dates = pd.to_datetime(delayed[datetime_column], errors="coerce")
            if bool(signal_dates.isna().any()):
                raise RuntimeError("one-day delay test found an invalid signal date")
            normalized_dates = pd.DatetimeIndex(signal_dates).tz_localize(None).normalize()
            positions = calendar.searchsorted(normalized_dates, side="right")
            keep = positions < len(calendar)
            delayed = delayed.loc[keep].copy()
            delayed[datetime_column] = pd.Series(
                calendar.take(positions[keep]).to_numpy(dtype="datetime64[ns]"),
                index=delayed.index,
                dtype="datetime64[ns]",
            )
            return delayed.sort_values(datetime_column, kind="stable").reset_index(drop=True)

        if isinstance(delayed.index, pd.DatetimeIndex):
            signal_dates = delayed.index.tz_localize(None).normalize()
            positions = calendar.searchsorted(signal_dates, side="right")
            keep = positions < len(calendar)
            delayed = delayed.loc[keep].copy()
            delayed.index = calendar.take(positions[keep])
            return delayed.sort_index(kind="stable")

        raise RuntimeError("one-day delay test needs a date column or DatetimeIndex")

    @staticmethod
    def _prior_candidate_sharpes(state: WorkflowState) -> list[float]:
        values: list[float] = []
        records = state.get("candidate_records", [])
        if not isinstance(records, list):
            return values
        for record in records:
            if not isinstance(record, dict) or record.get("period_kind") != "development":
                continue
            result = record.get("result")
            if not isinstance(result, dict):
                continue
            value = result.get("sharpe")
            if isinstance(value, (int, float)):
                values.append(float(value))
        return values

    @staticmethod
    def _fold_result_record(
        fold: EvaluationFold,
        result: BacktestResult,
        stats: dict[str, Any],
        *,
        artifacts: dict[str, str] | None = None,
    ) -> dict[str, object]:
        record = fold.to_dict()
        record.update(
            {
                "annual_return": result.annual_return,
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
                "max_ddpercent": result.max_ddpercent,
                "trade_count": result.trade_count,
                "win_rate": result.win_rate,
                "profit_loss_ratio": result.profit_loss_ratio,
                "event_gross_up_rate": result.event_gross_up_rate,
                "event_joint_minute_hit_rate": result.event_joint_minute_hit_rate,
                "event_success_rate": result.event_success_rate,
                "event_success_column": result.event_success_column,
                "event_success_metric": result.event_success_metric,
                "event_success_metric_value": result.event_success_metric_value,
                "passed": result.passed,
                "summary": result.summary,
                "artifacts": dict(artifacts or {}),
            }
        )
        debug = stats.get("_backtest_debug", {})
        if isinstance(debug, dict) and isinstance(
            debug.get("transaction_cost_rule"), dict
        ):
            record["transaction_cost_rule"] = deepcopy(
                debug["transaction_cost_rule"]
            )
        record.update(daily_return_moments(stats))
        return record

    @staticmethod
    def _failed_stress_record(
        fold: EvaluationFold,
        test_name: str,
        error: Exception,
    ) -> dict[str, object]:
        record = fold.to_dict()
        record.update(
            {
                "annual_return": 0.0,
                "sharpe": -1_000_000.0,
                "max_drawdown": 0.0,
                "max_ddpercent": None,
                "trade_count": 0,
                "passed": False,
                "summary": f"{test_name} failed: {type(error).__name__}: {error}",
                "sample_size": 0,
                "skew": 0.0,
                "kurtosis": 3.0,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return record

    @staticmethod
    def _aggregate_development_result(report: dict[str, object]) -> BacktestResult:
        fold_records = report.get("folds", [])
        max_drawdowns: list[float] = []
        if isinstance(fold_records, list):
            for record in fold_records:
                if not isinstance(record, dict):
                    continue
                value = record.get("max_drawdown")
                if isinstance(value, (int, float)):
                    max_drawdowns.append(float(value))
        max_drawdown = (
            -max(abs(value) for value in max_drawdowns)
            if max_drawdowns
            else 0.0
        )
        failure_reasons = report.get("failure_reasons", [])
        reason_text = "; ".join(
            str(item) for item in failure_reasons
        ) if isinstance(failure_reasons, list) else ""
        summary = (
            "Development evaluation: "
            f"folds={int(report.get('fold_count', 0))}, "
            f"passed_folds={int(report.get('passed_fold_count', 0))}, "
            f"median_return={float(report.get('median_annual_return', 0.0)):.2%}, "
            f"median_sharpe={float(report.get('median_sharpe', 0.0)):.2f}, "
            f"worst_sharpe={float(report.get('worst_fold_sharpe', 0.0)):.2f}, "
            f"passed={bool(report.get('passed', False))}"
        )
        if reason_text:
            summary = f"{summary}; reasons={reason_text}"
        worst_drawdown = report.get("worst_fold_drawdown")
        return BacktestResult(
            annual_return=float(report.get("median_annual_return", 0.0)),
            sharpe=float(report.get("median_sharpe", 0.0)),
            max_drawdown=max_drawdown,
            passed=bool(report.get("passed", False)),
            summary=summary,
            max_ddpercent=(
                float(worst_drawdown)
                if isinstance(worst_drawdown, (int, float))
                else None
            ),
            trade_count=int(report.get("total_trade_count", 0)),
        )

    @classmethod
    def _run_local_parquet_development(
        cls,
        state: WorkflowState,
        strategy_result: dict[str, object],
        experiment_spec: dict[str, object],
    ) -> WorkflowState:
        from quanta_agents.local_parquet_backtest import (
            load_market_dates,
            prepare_target_weight_backtest_market,
            run_target_weight_backtest,
        )

        if pd is None:
            raise RuntimeError("pandas is required for local Parquet development tests")

        development_period = resolve_development_period(experiment_spec)
        if not development_period:
            raise RuntimeError("development tests require validate_start and validate_end")
        reference_weights = strategy_result.get("output_weights")
        if not isinstance(reference_weights, pd.DataFrame) or reference_weights.empty:
            raise RuntimeError("development tests require non-empty output_weights")
        reference_time_column = cls._resolve_output_weights_time_column(
            strategy_result,
            reference_weights,
        )
        reference_codes = cls._extract_tradable_weight_columns(
            reference_weights,
            reference_time_column,
        )
        if not reference_codes:
            raise RuntimeError("development tests could not find any stock weight columns")
        trading_dates = load_market_dates(
            None,
            reference_codes,
            development_period["start"],
            development_period["end"],
        )
        gap_days = int(cls._resolve_float_config(experiment_spec, "gap_trading_days", 10.0))
        folds = build_development_folds(
            experiment_spec,
            trading_dates=trading_dates,
        )
        default_buy_cost = cls._resolve_float_config(
            experiment_spec,
            "commission_rate",
            0.0003,
        )
        buy_cost = cls._resolve_float_config(experiment_spec, "buy_cost", default_buy_cost)
        sell_cost = cls._resolve_float_config(
            experiment_spec,
            "sell_cost",
            default_buy_cost + 0.0005,
        )
        (
            sell_cost_before_change,
            sell_cost_change_date,
        ) = cls._resolve_sell_cost_change_config(experiment_spec)
        env_slippage = cls._coerce_optional_float(os.getenv("QUANTA_BACKTEST_SLIPPAGE"))
        slippage = cls._resolve_float_config(
            experiment_spec,
            "slippage",
            env_slippage if env_slippage is not None else 0.0,
        )
        lot_size = int(cls._resolve_float_config(experiment_spec, "lot_size", 0.0))
        capital = cls._resolve_float_config(experiment_spec, "capital", 1_000_000.0)
        run_cost_stress = cls._resolve_bool_config(
            experiment_spec,
            "run_cost_stress",
            True,
        )
        run_delay_stress = cls._resolve_bool_config(
            experiment_spec,
            "run_delay_stress",
            True,
        )
        validate_data_bundle = strategy_result.get("validate_data_bundle", {})
        fallback_membership_df = None
        # 入选候选会清空大数据表；此时成分表由下面的逐折重算重新加载。
        if isinstance(validate_data_bundle, dict) and validate_data_bundle:
            fallback_membership_df = combine_historical_membership_data(
                validate_data_bundle,
                strategy_result.get("required_data", []),
            )

        base_records: list[dict[str, object]] = []
        cost_records: list[dict[str, object]] = []
        delay_records: list[dict[str, object]] = []
        recompute_records: list[dict[str, object]] = []

        state["selected_template"] = "LocalParquetTargetWeight"
        state["selected_base_class"] = "TargetWeightBacktest"
        for fold in folds:
            start = cls._parse_datetime(fold.test_start, datetime.now() - timedelta(days=365))
            end = cls._parse_datetime(fold.test_end, datetime.now())
            output_weights_df, recompute_meta = cls._recompute_output_weights_for_backtest(
                state,
                strategy_result,
                experiment_spec,
                start,
                end,
                training_end=fold.train_end,
            )
            membership_df = recompute_meta.pop(
                "_membership_df",
                fallback_membership_df,
            )
            fold_strategy_result = dict(strategy_result)
            fold_strategy_result["output_weights"] = output_weights_df
            cls._resolve_template_from_output_weights_df(fold_strategy_result)
            prepared_market = prepare_target_weight_backtest_market(
                output_weights_df,
                start=start,
                end=end,
            )

            stats = run_target_weight_backtest(
                output_weights_df,
                start=start,
                end=end,
                capital=capital,
                buy_cost=buy_cost,
                sell_cost=sell_cost,
                slippage=slippage,
                lot_size=lot_size,
                membership_df=membership_df,
                prepared_market=prepared_market,
                sell_cost_before_change=sell_cost_before_change,
                sell_cost_change_date=sell_cost_change_date,
            )
            stats["_target_weights_df"] = output_weights_df
            result = cls._stats_to_result(stats, experiment_spec)
            artifacts = cls._safe_export_backtest_artifacts(
                state=state,
                stats=stats,
                backtest_result=result,
                template_family="local_parquet",
                experiment_spec=experiment_spec,
                output_subdir=f"development_folds/{fold.name}/base",
            )
            base_records.append(
                cls._fold_result_record(
                    fold,
                    result,
                    stats,
                    artifacts=artifacts,
                )
            )
            recompute_records.append({"fold": fold.to_dict(), "details": recompute_meta})

            if run_cost_stress:
                try:
                    cost_stats = run_target_weight_backtest(
                        output_weights_df,
                        start=start,
                        end=end,
                        capital=capital,
                        buy_cost=buy_cost * 2.0,
                        sell_cost=sell_cost * 2.0,
                        slippage=slippage * 2.0,
                        lot_size=lot_size,
                        membership_df=membership_df,
                        prepared_market=prepared_market,
                        sell_cost_before_change=(
                            sell_cost_before_change * 2.0
                            if sell_cost_before_change is not None
                            else None
                        ),
                        sell_cost_change_date=sell_cost_change_date,
                    )
                    cost_result = cls._stats_to_result(cost_stats, experiment_spec)
                    cost_records.append(cls._fold_result_record(fold, cost_result, cost_stats))
                except Exception as exc:
                    cost_records.append(cls._failed_stress_record(fold, "double-cost test", exc))

            if run_delay_stress:
                try:
                    delayed_weights = cls._delay_output_weights_one_day(
                        output_weights_df,
                        fold_strategy_result,
                        trading_dates,
                    )
                    if delayed_weights.empty:
                        raise RuntimeError("no signal remains after a one-market-day delay")
                    delay_stats = run_target_weight_backtest(
                        delayed_weights,
                        start=start,
                        end=end,
                        capital=capital,
                        buy_cost=buy_cost,
                        sell_cost=sell_cost,
                        slippage=slippage,
                        lot_size=lot_size,
                        membership_df=membership_df,
                        prepared_market=prepared_market,
                        sell_cost_before_change=sell_cost_before_change,
                        sell_cost_change_date=sell_cost_change_date,
                    )
                    delay_result = cls._stats_to_result(delay_stats, experiment_spec)
                    delay_records.append(cls._fold_result_record(fold, delay_result, delay_stats))
                except Exception as exc:
                    delay_records.append(cls._failed_stress_record(fold, "one-day-delay test", exc))

            # 下一折日期不同，不保留上一折的大块行情。
            prepared_market = None

        report = summarize_development_results(
            base_records,
            experiment_spec,
            cost_stress_records=cost_records,
            delay_stress_records=delay_records,
            prior_candidate_sharpes=cls._prior_candidate_sharpes(state),
        )
        report.update(
            {
                "candidate_id": str(state.get("current_candidate_id", "")),
                "epoch_index": int(state.get("epoch_index", 1)),
                "gap_trading_days": gap_days,
                "date_source": "local_parquet_market_dates",
                "recompute_records": recompute_records,
                "transaction_cost_rule": (
                    deepcopy(base_records[0].get("transaction_cost_rule"))
                    if base_records
                    else None
                ),
                "double_cost_rule": (
                    deepcopy(cost_records[0].get("transaction_cost_rule"))
                    if cost_records
                    else None
                ),
            }
        )
        aggregate_result = cls._aggregate_development_result(report)
        state["development_report"] = report  # type: ignore[typeddict-unknown-key]
        state["test_result"] = aggregate_result
        write_trace_json(
            state,
            agent_name=cls.name,
            stage="development_evaluation",
            payload=report,
        )
        state["history"].append(
            f"Epoch {state['epoch_index']}: StrategyTester ran {len(folds)} time-forward development folds"
        )
        state["phase"] = "test"
        return state

    @staticmethod
    def _resolve_vnpy_setting_path() -> Path:
        return Path.home() / ".vntrader" / "vt_setting.json"

    @classmethod
    def _ensure_vnpy_database_settings(
        cls,
        backtest_db_backend: str | None = None,
    ) -> Path:
        backend = (backtest_db_backend or cls._resolve_backtest_db_backend()).strip().lower()
        setting_path = cls._resolve_vnpy_setting_path()
        setting_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            raw_settings = json.loads(setting_path.read_text(encoding="utf-8"))
        except Exception:
            raw_settings = {}

        if not isinstance(raw_settings, dict):
            raw_settings = {}

        updated_settings = dict(raw_settings)
        if backend == "sqlite":
            updated_settings.update(
                {
                    "database.name": "sqlite",
                    "database.database": cls._resolve_vnpy_sqlite_db_path(),
                }
            )
        else:
            updated_settings.update(
                {
                    "database.name": "dolphindb",
                    "database.database": cls._resolve_dolphindb_backtest_db_path().replace("dfs://", ""),
                    "database.host": os.getenv("DOLPHINDB_HOST", "localhost").strip(),
                    "database.port": int(os.getenv("DOLPHINDB_PORT", "8848")),
                    "database.user": os.getenv("DOLPHINDB_USER", "admin").strip(),
                    "database.password": os.getenv("DOLPHINDB_PASSWORD", "").strip(),
                    "database.timezone": os.getenv("DOLPHINDB_TIMEZONE", "Asia/Shanghai").strip(),
                }
            )

        if updated_settings != raw_settings:
            setting_path.write_text(json.dumps(updated_settings, ensure_ascii=False, indent=4), encoding="utf-8")

        return setting_path

    @classmethod
    def _ensure_vnpy_dolphindb_settings(cls) -> Path:
        return cls._ensure_vnpy_database_settings(backtest_db_backend="dolphindb")

    @classmethod
    def _ensure_vnpy_sqlite_compatibility(
        cls,
        backtest_dataset_specs: list[dict[str, object]] | None = None,
    ) -> None:
        try:
            package = importlib.import_module("vnpy_sqlite")
            database_module = importlib.import_module("vnpy_sqlite.sqlite_database")
        except Exception:
            return

        database_class = getattr(database_module, "SqliteDatabase", None)
        if not isinstance(database_class, type):
            return

        table_configs = cls._resolve_backtest_table_configs(backtest_dataset_specs, "sqlite")
        if not table_configs:
            return

        db_path = cls._resolve_vnpy_sqlite_db_path()

        class CompatibleSqliteDatabase(database_class):
            def __init__(self) -> None:
                super().__init__()
                self.db_path = db_path

            def load_bar_data(
                self,
                symbol: str,
                exchange: Any,
                interval: Any,
                start: datetime,
                end: datetime,
            ) -> list[Any]:
                trader_database = importlib.import_module("vnpy.trader.database")
                trader_object = importlib.import_module("vnpy.trader.object")
                DB_TZ = getattr(trader_database, "DB_TZ")
                bar_data_cls = getattr(trader_object, "BarData")

                def _parse_dt(value: Any) -> datetime:
                    if isinstance(value, datetime):
                        dt = value
                    elif pd is not None:
                        ts = pd.Timestamp(str(value))
                        dt = ts.to_pydatetime()
                    else:
                        dt = datetime.fromisoformat(str(value))

                    if dt.tzinfo is None:
                        return dt
                    return dt.astimezone(DB_TZ).replace(tzinfo=None)

                def _fetch_rows(conn: sqlite3.Connection, table_config: dict[str, str]) -> list[sqlite3.Row]:
                    symbol_column = table_config["symbol_column"]
                    exchange_column = table_config["exchange_column"]
                    datetime_column = table_config["datetime_column"]
                    interval_column = table_config["interval_column"]
                    table_name = table_config["table_name"]
                    interval_values = cls._resolve_interval_filter_values(interval, table_config.get("interval_value"))

                    base_sql = (
                        f"SELECT * FROM {table_name} "
                        f"WHERE {symbol_column} = ? AND {exchange_column} = ? "
                        f"AND {datetime_column} >= ? AND {datetime_column} <= ?"
                    )
                    base_params: list[Any] = [symbol, exchange.value, start, end]

                    if interval_values:
                        placeholders = ", ".join("?" for _ in interval_values)
                        interval_sql = f"{base_sql} AND {interval_column} IN ({placeholders}) ORDER BY {datetime_column}"
                        try:
                            return list(conn.execute(interval_sql, [*base_params, *interval_values]).fetchall())
                        except sqlite3.OperationalError:
                            pass

                    return list(conn.execute(f"{base_sql} ORDER BY {datetime_column}", base_params).fetchall())

                bars: list[Any] = []
                seen_datetimes: set[datetime] = set()
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    for table_config in table_configs:
                        for row in _fetch_rows(conn, table_config):
                            dt = _parse_dt(row[table_config["datetime_column"]])
                            if dt in seen_datetimes:
                                continue
                            seen_datetimes.add(dt)
                            bars.append(
                                bar_data_cls(
                                    symbol=symbol,
                                    exchange=exchange,
                                    datetime=dt,
                                    interval=interval,
                                    volume=float(row["volume"]),
                                    turnover=float(row["turnover"]),
                                    open_interest=float(row["open_interest"]),
                                    open_price=float(row["open_price"]),
                                    high_price=float(row["high_price"]),
                                    low_price=float(row["low_price"]),
                                    close_price=float(row["close_price"]),
                                    gateway_name="DB",
                                )
                            )

                bars.sort(key=lambda item: item.datetime)
                return bars

        setattr(database_module, "SqliteDatabase", CompatibleSqliteDatabase)
        setattr(package, "Database", CompatibleSqliteDatabase)

    @classmethod
    def _ensure_vnpy_dolphindb_compatibility(
        cls,
        backtest_dataset_specs: list[dict[str, object]] | None = None,
    ) -> None:
        try:
            package = importlib.import_module("vnpy_dolphindb")
            database_module = importlib.import_module("vnpy_dolphindb.dolphindb_database")
        except Exception:
            return

        database_class = getattr(database_module, "DolphindbDatabase", None)
        if not isinstance(database_class, type):
            return
        if "get_tick_overview" in getattr(database_class, "__dict__", {}):
            # Keep going so we can still redirect the history library path.
            pass

        bar_db_path = cls._resolve_dolphindb_backtest_db_path()
        table_configs = cls._resolve_backtest_table_configs(backtest_dataset_specs, "dolphindb")
        if not table_configs:
            table_configs = [
                {
                    "table_name": "bar",
                    "symbol_column": "symbol",
                    "exchange_column": "exchange",
                    "datetime_column": "datetime",
                    "interval_column": "interval",
                    "interval_value": "",
                }
            ]

        class CompatibleDolphindbDatabase(database_class):
            def __init__(self) -> None:
                super().__init__()
                self.db_path = bar_db_path

            def load_bar_data(
                self,
                symbol: str,
                exchange: Any,
                interval: Any,
                start: datetime,
                end: datetime,
            ) -> list[Any]:
                trader_database = importlib.import_module("vnpy.trader.database")
                trader_object = importlib.import_module("vnpy.trader.object")
                DB_TZ = getattr(trader_database, "DB_TZ")
                bar_data_cls = getattr(trader_object, "BarData")

                def _quote(value: str) -> str:
                    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                    return f'"{escaped}"'

                def _as_float(value: Any) -> float:
                    try:
                        return float(value)
                    except Exception:
                        return 0.0

                def _load_table_df(table_config: dict[str, str]) -> Any:
                    db_path = _quote(self.db_path)
                    table_name = _quote(table_config["table_name"])
                    symbol_column = table_config["symbol_column"]
                    exchange_column = table_config["exchange_column"]
                    datetime_column = table_config["datetime_column"]
                    interval_column = table_config["interval_column"]
                    start_text = start.strftime("%Y.%m.%dT%H:%M:%S.%f")
                    end_text = end.strftime("%Y.%m.%dT%H:%M:%S.%f")
                    interval_values = cls._resolve_interval_filter_values(interval, table_config.get("interval_value"))

                    filters = [
                        f'{symbol_column}="{symbol}"',
                        f'{exchange_column}="{exchange.value}"',
                        f"{datetime_column}>={start_text}",
                        f"{datetime_column}<={end_text}",
                    ]
                    if interval_values:
                        interval_filter = " or ".join(f'{interval_column}="{value}"' for value in interval_values)
                        filters.append(f"({interval_filter})")

                    query = (
                        f"select * "
                        f"from loadTable({db_path}, {table_name}) "
                        f"where {' and '.join(filters)}"
                    )
                    try:
                        return self.session.run(query)
                    except Exception:
                        fallback_query = (
                            f"select * "
                            f"from loadTable({db_path}, {table_name}) "
                            f'where {symbol_column}="{symbol}" and {exchange_column}="{exchange.value}" '
                            f"and {datetime_column}>={start_text} and {datetime_column}<={end_text}"
                        )
                        return self.session.run(fallback_query)

                frames: list[Any] = []
                for table_config in table_configs:
                    df = _load_table_df(table_config)
                    if pd is None or not isinstance(df, pd.DataFrame) or df.empty:
                        continue
                    frames.append(df)
                if pd is None or not frames:
                    return []
                df = pd.concat(frames, ignore_index=True)
                if "datetime" in df.columns:
                    df = df.drop_duplicates(subset=["datetime"], keep="first").sort_values("datetime", kind="stable")

                bars: list[Any] = []
                for row in df.itertuples(index=False):
                    if pd is None:
                        return []
                    ts = pd.Timestamp(str(row.datetime))
                    if ts.tzinfo is None:
                        ts = ts.tz_localize(DB_TZ)
                    else:
                        ts = ts.tz_convert(DB_TZ)
                    dt = ts.tz_localize(None).to_pydatetime()
                    bars.append(
                        bar_data_cls(
                            symbol=symbol,
                            exchange=exchange,
                            datetime=dt,
                            interval=interval,
                            volume=_as_float(row.volume),
                            turnover=_as_float(row.turnover),
                            open_interest=_as_float(row.open_interest),
                            open_price=_as_float(row.open_price),
                            high_price=_as_float(row.high_price),
                            low_price=_as_float(row.low_price),
                            close_price=_as_float(row.close_price),
                            gateway_name="DB",
                        )
                    )

                return bars

            def get_tick_overview(self) -> list[Any]:
                return []

        setattr(database_module, "DolphindbDatabase", CompatibleDolphindbDatabase)
        setattr(package, "Database", CompatibleDolphindbDatabase)

    @staticmethod
    def _resolve_interval_name(experiment_spec: dict[str, object]) -> str:
        raw = experiment_spec.get("bar_interval")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
        return "1d"

    @staticmethod
    def _resolve_float_config(experiment_spec: dict[str, object], key: str, default: float) -> float:
        raw = experiment_spec.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                return float(raw.strip())
            except ValueError:
                return default
        return default

    @classmethod
    def _resolve_sell_cost_change_config(
        cls,
        experiment_spec: dict[str, object],
    ) -> tuple[float | None, object | None]:
        raw_rate = experiment_spec.get("sell_cost_before_change")
        raw_date = experiment_spec.get("sell_cost_change_date")
        if raw_rate is None and raw_date is None:
            return None, None
        if raw_rate is None or raw_date is None:
            raise RuntimeError(
                "sell_cost_before_change 和 sell_cost_change_date 必须同时提供"
            )
        old_rate = cls._coerce_optional_float(raw_rate)
        if old_rate is None or not 0 <= old_rate < 1:
            raise RuntimeError("sell_cost_before_change 必须在 0 到 1 之间")
        if not isinstance(raw_date, str) or not raw_date.strip():
            raise RuntimeError("sell_cost_change_date 必须是有效日期")
        return old_rate, raw_date.strip()

    @classmethod
    def _resolve_event_horizon_minutes(
        cls,
        experiment_spec: dict[str, object],
        strategy_result: dict[str, object],
    ) -> tuple[int, str]:
        from quanta_agents.event_parquet_backtest import SUPPORTED_HORIZONS

        params = strategy_result.get("params", {})
        if isinstance(params, dict) and "event_horizon_minutes" in params:
            raw = params["event_horizon_minutes"]
            source = "strategy_result.params"
        else:
            raw = experiment_spec.get("event_horizon_minutes", 5)
            source = "experiment_spec.event_horizon_minutes"
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not float(raw).is_integer()
        ):
            raise RuntimeError("event_horizon_minutes 必须是整数 5、10 或 20")
        resolved = int(raw)
        if resolved not in SUPPORTED_HORIZONS:
            raise RuntimeError("event_horizon_minutes 只允许 5、10 或 20")
        return resolved, source

    @staticmethod
    def _resolve_bool_config(experiment_spec: dict[str, object], key: str, default: bool) -> bool:
        raw = experiment_spec.get(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str) and raw.strip():
            value = raw.strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
        return default

    @classmethod
    def _load_strategy_class_by_template(
        cls,
        code_text: str,
        expected_base_class: str | None,
    ) -> type:
        namespace: dict[str, object] = {
            "__name__": "__generated_strategy__",
            # Keep a default injected universe so generated code that references it at import time does not fail.
            "universe": [],
        }
        exec(compile(code_text, "generated_strategy.py", "exec"), namespace)  # noqa: S102

        if not expected_base_class:
            raise RuntimeError("selected_base_class is required for strategy class resolution")

        candidates = cls.BASE_CLASS_CANDIDATES.get(expected_base_class.strip(), {expected_base_class.strip()})
        matched: list[type] = []
        for value in namespace.values():
            if not isinstance(value, type):
                continue

            # Only consider classes defined in the generated code snippet.
            if value.__module__ != "__generated_strategy__":
                continue

            # Skip template base classes themselves; we need a concrete strategy subclass.
            if value.__name__ in candidates:
                continue

            has_on_bar = callable(getattr(value, "on_bar", None))
            has_on_bars = callable(getattr(value, "on_bars", None))
            if not (has_on_bar or has_on_bars):
                continue

            mro_names = {base.__name__ for base in value.__mro__}
            if mro_names.intersection(candidates):
                matched.append(value)

        if len(matched) == 1:
            return matched[0]

        if len(matched) > 1:
            class_names = ", ".join(sorted(item.__name__ for item in matched))
            raise RuntimeError(
                f"Ambiguous strategy classes for selected_base_class='{expected_base_class}': {class_names}"
            )

        raise RuntimeError(
            f"No strategy class matches selected_base_class='{expected_base_class}'. "
            "Ensure generated class inherits the expected vn.py template base class."
        )

    @staticmethod
    def _resolve_output_weights_time_column(strategy_result: dict[str, object], output_weights_df: Any) -> str | None:
        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            return None

        strategy_output = strategy_result.get("strategy_output")
        if isinstance(strategy_output, dict):
            output_meta = strategy_output.get("output_weights_df")
            if isinstance(output_meta, dict):
                value = output_meta.get("datetime_column")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _summarize_output_weights_activity(output_weights_df: Any, datetime_column: str | None = None) -> dict[str, Any]:
        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            return {"status": "unavailable"}

        tradable_columns = StrategyTester._extract_tradable_weight_columns(output_weights_df, datetime_column)
        if not tradable_columns:
            return {
                "status": "no_tradable_columns",
                "row_count": int(len(output_weights_df)),
            }

        numeric_weights = output_weights_df[tradable_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        abs_sum = numeric_weights.abs().sum(axis=1)
        active_mask = abs_sum > 0
        zero_only_columns = [
            column
            for column in tradable_columns
            if bool((numeric_weights[column].abs() == 0).all())
        ]
        nonzero_counts = {
            column: int((numeric_weights[column].abs() > 0).sum())
            for column in tradable_columns
        }
        nonzero_ratio = {
            column: float((numeric_weights[column].abs() > 0).mean())
            for column in tradable_columns
        }

        sample_active_dates: list[str] = []
        if bool(active_mask.any()):
            active_rows = output_weights_df.loc[active_mask]
            if isinstance(datetime_column, str) and datetime_column.strip() and datetime_column in active_rows.columns:
                sample_values = active_rows[datetime_column].head(5).tolist()
            else:
                sample_values = list(active_rows.index[:5])
            sample_active_dates = [str(value) for value in sample_values]

        return {
            "status": "ok",
            "row_count": int(len(output_weights_df)),
            "tradable_column_count": int(len(tradable_columns)),
            "rows_with_nonzero_weights": int(active_mask.sum()),
            "rows_with_all_zero_weights": int((~active_mask).sum()),
            "active_row_ratio": float(active_mask.mean()),
            "zero_only_columns": zero_only_columns,
            "nonzero_counts": nonzero_counts,
            "nonzero_ratio": nonzero_ratio,
            "sample_active_dates": sample_active_dates,
        }

    @staticmethod
    def _normalize_output_weights_df(raw: Any, time_column: str | None = None) -> Any:
        if pd is None or not isinstance(raw, pd.DataFrame):
            return raw

        weights_df = raw.copy()
        if weights_df.empty:
            return weights_df
        if isinstance(time_column, str) and time_column.strip() and time_column in weights_df.columns:
            resolved = time_column.strip()
            weights_df[resolved] = pd.to_datetime(weights_df[resolved], errors="coerce")
            weights_df = weights_df.dropna(subset=[resolved]).set_index(resolved)

        try:
            if isinstance(weights_df.index, pd.DatetimeIndex):
                weights_df = weights_df.sort_index(kind="stable")
        except Exception:
            pass

        ignored_columns = {"cash"}
        if isinstance(time_column, str) and time_column.strip():
            ignored_columns.add(time_column.strip().lower())
        for column in weights_df.columns:
            column_text = str(column).strip()
            if not column_text or column_text.lower() in ignored_columns:
                continue

        return weights_df

    @staticmethod
    def _extract_tradable_weight_columns(output_weights_df: Any, datetime_column: str | None = None) -> list[str]:
        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            return []

        ignored = {"cash"}
        if isinstance(datetime_column, str) and datetime_column.strip():
            ignored.add(datetime_column.strip().lower())
        columns: list[str] = []
        for column in output_weights_df.columns:
            name = str(column).strip()
            if not name or name.lower() in ignored:
                continue
            if name not in columns:
                columns.append(name)
        return columns

    @staticmethod
    def _validate_event_long_only_weights(
        output_weights_df: Any,
        tradable_columns: list[str],
    ) -> None:
        """分钟 A 股事件每次重算后都重新检查权重。"""

        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            raise RuntimeError("event output_weights must be a pandas DataFrame")
        if "cash" not in output_weights_df.columns:
            raise RuntimeError("event output_weights must include cash")

        import numpy as np

        cash = pd.to_numeric(output_weights_df["cash"], errors="coerce")
        if bool(cash.isna().any()):
            raise RuntimeError("event output_weights contain non-numeric or missing values")
        cash_values = cash.to_numpy(dtype=float, copy=False)
        if not bool(np.isfinite(cash_values).all()):
            raise RuntimeError("event output_weights contain non-finite values")
        if bool(((cash < -1e-12) | (cash > 1.0 + 1e-12)).any()):
            raise RuntimeError("event cash weight must stay between 0 and 1")

        asset_sums = np.zeros(len(output_weights_df), dtype=float)
        for column in tradable_columns:
            series = output_weights_df[column]
            if isinstance(series.dtype, pd.SparseDtype):
                sparse_array = series.array
                fill = pd.to_numeric(
                    pd.Series([sparse_array.fill_value]),
                    errors="coerce",
                ).iloc[0]
                values = pd.to_numeric(
                    pd.Series(sparse_array.sp_values),
                    errors="coerce",
                ).to_numpy(dtype=float, copy=False)
                if pd.isna(fill) or not bool(np.isfinite(values).all()):
                    raise RuntimeError("event output_weights contain non-numeric or missing values")
                fill_value = float(fill)
                if not np.isfinite(fill_value):
                    raise RuntimeError("event output_weights contain non-finite values")
                if fill_value < -1e-12 or bool((values < -1e-12).any()):
                    raise RuntimeError("A-share minute event weights cannot be negative")
                asset_sums += fill_value
                positions = sparse_array.sp_index.to_int_index().indices
                if len(positions):
                    asset_sums[positions] += values - fill_value
                continue

            numeric_series = pd.to_numeric(series, errors="coerce")
            if bool(numeric_series.isna().any()):
                raise RuntimeError("event output_weights contain non-numeric or missing values")
            values = numeric_series.to_numpy(dtype=float, copy=False)
            if not bool(np.isfinite(values).all()):
                raise RuntimeError("event output_weights contain non-finite values")
            if bool((values < -1e-12).any()):
                raise RuntimeError("A-share minute event weights cannot be negative")
            asset_sums += values

        row_sums = asset_sums + cash_values
        if bool((np.abs(row_sums - 1.0) > 1e-6).any()):
            raise RuntimeError("event stock weights plus cash must equal 1")

    @classmethod
    def _resolve_template_from_output_weights_df(cls, strategy_result: dict[str, object]) -> tuple[str, str, Any, list[str]]:
        output_weights_df = strategy_result.get("output_weights") if isinstance(strategy_result, dict) else None
        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            raise RuntimeError("strategy_result.output_weights must be a pandas DataFrame")

        resolved_time_column = cls._resolve_output_weights_time_column(strategy_result, output_weights_df)
        if not resolved_time_column:
            raise RuntimeError("strategy_output.output_weights_df.datetime_column is required")
        if resolved_time_column not in output_weights_df.columns:
            raise RuntimeError(f"output_weights_df missing datetime column: {resolved_time_column}")
        normalized_df = cls._normalize_output_weights_df(output_weights_df, time_column=resolved_time_column)
        if pd is None or not isinstance(normalized_df, pd.DataFrame) or normalized_df.empty:
            raise RuntimeError("strategy_result.output_weights must be a non-empty DataFrame")

        tradable_columns = cls._extract_tradable_weight_columns(normalized_df, datetime_column=resolved_time_column)
        if not tradable_columns:
            raise RuntimeError("output_weights_df has no tradable instrument columns")

        return "portfolio", "StrategyTemplate", normalized_df, tradable_columns

    @classmethod
    def _align_output_weights_symbols_to_available(
        cls,
        output_weights_df: Any,
        tradable_columns: list[str],
        available_vt_symbols: list[str],
    ) -> tuple[Any, list[str], dict[str, Any]]:
        if pd is None or not isinstance(output_weights_df, pd.DataFrame):
            return output_weights_df, tradable_columns, {"status": "skipped_not_dataframe"}
        if not tradable_columns:
            return output_weights_df, tradable_columns, {"status": "skipped_no_tradable_columns"}
        if not available_vt_symbols:
            return output_weights_df, tradable_columns, {"status": "skipped_no_available_symbols"}

        available_lookup, available_by_symbol = cls._build_available_symbol_index(available_vt_symbols)
        aligned_df = output_weights_df.copy()

        groups: dict[str, list[str]] = {}
        column_mapping: dict[str, str] = {}
        for column in tradable_columns:
            source = str(column)
            target = cls._map_symbol_to_available(source, available_lookup, available_by_symbol)
            column_mapping[source] = target
            grouped = groups.setdefault(target, [])
            if source not in grouped:
                grouped.append(source)

        renamed_columns: list[dict[str, str]] = []
        merged_columns: list[dict[str, object]] = []
        for target, sources in groups.items():
            existing_sources = [name for name in sources if name in aligned_df.columns]
            if not existing_sources:
                continue

            needs_merge = len(existing_sources) > 1 or (
                len(existing_sources) == 1 and existing_sources[0] != target and target in aligned_df.columns
            )

            if needs_merge:
                merge_sources = list(existing_sources)
                if target in aligned_df.columns and target not in merge_sources:
                    merge_sources = [target, *merge_sources]
                merged = aligned_df[merge_sources].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)
                aligned_df[target] = merged
                drop_columns = [name for name in merge_sources if name != target]
                if drop_columns:
                    aligned_df = aligned_df.drop(columns=drop_columns)
                merged_columns.append({"target": target, "sources": merge_sources})
                continue

            source = existing_sources[0]
            if source != target:
                aligned_df = aligned_df.rename(columns={source: target})
                renamed_columns.append({"from": source, "to": target})

        aligned_columns: list[str] = []
        seen: set[str] = set()
        for column in tradable_columns:
            target = column_mapping.get(str(column), str(column))
            if target in aligned_df.columns and target not in seen:
                seen.add(target)
                aligned_columns.append(target)

        if not aligned_columns:
            aligned_columns = cls._extract_tradable_weight_columns(aligned_df)

        return aligned_df, aligned_columns, {
            "status": "ok",
            "column_mapping": column_mapping,
            "renamed_columns": renamed_columns,
            "merged_columns": merged_columns,
            "aligned_tradable_columns": aligned_columns,
        }

    @staticmethod
    def _build_fixed_portfolio_strategy_code() -> str:
        return (
            "from __future__ import annotations\n"
            "\n"
            "from typing import Any\n"
            "from datetime import datetime\n"
            "\n"
            "try:\n"
            "    from vnpy_portfoliostrategy import StrategyTemplate\n"
            "    from vnpy.trader.object import BarData, OrderData, TradeData\n"
            "except Exception:\n"
            "    class StrategyTemplate:\n"
            "        def __init__(self, *args, **kwargs):\n"
            "            pass\n"
            "\n"
            "    class BarData:\n"
            "        pass\n"
            "\n"
            "    class TradeData:\n"
            "        pass\n"
            "\n"
            "    class OrderData:\n"
            "        pass\n"
            "\n"
            "\n"
            "class CapitalTracker:\n"
            "    \"\"\"Track real-time equity and commission costs.\"\"\"\n"
            "    def __init__(self, initial_capital: float):\n"
            "        self.initial_capital = float(initial_capital)\n"
            "        self.equity = float(initial_capital)\n"
            "        self.total_commission = 0.0\n"
            "        self.trade_count = 0\n"
            "        self.trades = []\n"
            "\n"
            "    def on_trade(self, trade: TradeData):\n"
            "        \"\"\"Update equity and commission from trade.\"\"\"\n"
            "        try:\n"
            "            pnl = float(getattr(trade, \"pnl\", 0.0))\n"
            "            commission = float(getattr(trade, \"commission\", 0.0))\n"
            "            self.equity += pnl\n"
            "            self.total_commission += commission\n"
            "            self.trade_count += 1\n"
            "            self.trades.append({\n"
            "                'datetime': getattr(trade, 'datetime', datetime.now()),\n"
            "                'symbol': getattr(trade, 'symbol', 'unknown'),\n"
            "                'direction': getattr(trade, 'direction', 'unknown'),\n"
            "                'volume': getattr(trade, 'volume', 0),\n"
            "                'price': getattr(trade, 'price', 0.0),\n"
            "                'pnl': pnl,\n"
            "                'commission': commission\n"
            "            })\n"
            "        except Exception:\n"
            "            pass\n"
            "\n"
            "    def get_equity(self) -> float:\n"
            "        return self.equity\n"
            "\n"
            "    def get_total_commission(self) -> float:\n"
            "        return self.total_commission\n"
            "\n"
            "    def get_return(self) -> float:\n"
            "        if self.initial_capital <= 0:\n"
            "            return 0.0\n"
            "        return (self.equity - self.initial_capital) / self.initial_capital\n"
            "\n"
            "\n"
            "class Strategy(StrategyTemplate):\n"
            "    author = \"quanta_agents\"\n"
            "\n"
            "    def __init__(self, strategy_engine, strategy_name, vt_symbols, setting):\n"
            "        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)\n"
            "        self.params = setting.get(\"params\", {})\n"
            "        self.weights_df = setting.get(\"output_weights_df\")\n"
            "        self._date_to_idx = {}\n"
            "        self._use_date_align = False\n"
            "        self.debug_trace_limit = 50\n"
            "        self.debug_stats = {\n"
            "            'row_idx_missing': 0,\n"
            "            'symbol_not_in_vt': 0,\n"
            "            'bar_missing': 0,\n"
            "            'zero_target_pos': 0,\n"
            "            'nonzero_target_pos': 0,\n"
            "            'rebalance_calls': 0,\n"
            "        }\n"
            "        self.debug_nonzero_targets = []\n"
            "        capital = float(setting.get(\"capital\", 1_000_000))\n"
            "        self.market_at_next_open = bool(setting.get(\"market_at_next_open\", True))\n"
            "        self.capital_tracker = CapitalTracker(capital)\n"
            "\n"
            "    def on_init(self):\n"
            "        if self.weights_df is None:\n"
            "            raise ValueError(\"missing output_weights_df in strategy settings\")\n"
            "        self.weights_df = self.weights_df.sort_index()\n"
            "\n"
            "        try:\n"
            "            import pandas as pd\n"
            "            if isinstance(self.weights_df.index, pd.DatetimeIndex):\n"
            "                self._date_to_idx = {\n"
            "                    pd.Timestamp(dt).date(): i\n"
            "                    for i, dt in enumerate(self.weights_df.index)\n"
            "                }\n"
            "                self._use_date_align = True\n"
            "        except Exception:\n"
            "            self._use_date_align = False\n"
            "\n"
            "    def on_start(self):\n"
            "        pass\n"
            "\n"
            "    def on_stop(self):\n"
            "        pass\n"
            "\n"
            "    def _weight_to_target_pos(self, target_weight: float, price: float) -> int:\n"
            "        if price <= 0:\n"
            "            return 0\n"
            "        equity = float(self.capital_tracker.get_equity())\n"
            "        target_value = equity * float(target_weight)\n"
            "        if target_value == 0:\n"
            "            return 0\n"
            "        lot_size = 100\n"
            "        volume = int(abs(target_value) / price / lot_size) * lot_size\n"
            "        if volume <= 0:\n"
            "            return 0\n"
            "        return volume if target_value > 0 else -volume\n"
            "\n"
            "    def calculate_price(self, vt_symbol: str, direction: Any, reference: float) -> float:\n"
            "        if not self.market_at_next_open:\n"
            "            return reference\n"
            "\n"
            "        # Use aggressive limit prices to emulate next-bar open market execution.\n"
            "        # BacktestingEngine will match at next bar open as the best price when cross conditions are met.\n"
            "        text_candidates = []\n"
            "        for candidate in (direction, getattr(direction, \"value\", None), getattr(direction, \"name\", None)):\n"
            "            if candidate is None:\n"
            "                continue\n"
            "            try:\n"
            "                text_candidates.append(str(candidate).lower())\n"
            "            except Exception:\n"
            "                continue\n"
            "        direction_text = \"|\".join(text_candidates)\n"
            "\n"
            "        is_long = any(token in direction_text for token in (\"long\", \"buy\", \"多\"))\n"
            "        is_short = any(token in direction_text for token in (\"short\", \"sell\", \"空\"))\n"
            "\n"
            "        if is_long and not is_short:\n"
            "            base = float(reference) if float(reference) > 0 else 1.0\n"
            "            return base * 1000.0\n"
            "\n"
            "        if is_short:\n"
            "            # Extremely low short limit ensures crossing at next bar.\n"
            "            return 0.0\n"
            "\n"
            "        # Unknown direction text: default to aggressive long-side crossing price instead of zero.\n"
            "        base = float(reference) if float(reference) > 0 else 1.0\n"
            "        return base * 1000.0\n"
            "\n"
            "    def on_bars(self, bars: dict[str, BarData]):\n"
            "        row_idx = None\n"
            "\n"
            "        if self._use_date_align and self._date_to_idx:\n"
            "            sample_bar = next(iter(bars.values()))\n"
            "            bar_date = sample_bar.datetime.date()\n"
            "            if bar_date in self._date_to_idx:\n"
            "                row_idx = self._date_to_idx[bar_date]\n"
            "            else:\n"
            "                candidates = [d for d in self._date_to_idx if d <= bar_date]\n"
            "                if candidates:\n"
            "                    row_idx = self._date_to_idx[max(candidates)]\n"
            "\n"
            "        if row_idx is None:\n"
            "            self.debug_stats['row_idx_missing'] += 1\n"
            "            return\n"
            "\n"
            "        row = self.weights_df.iloc[row_idx]\n"
            "        for symbol, target_weight in row.items():\n"
            "            symbol_text = str(symbol).strip()\n"
            "            symbol_lower = symbol_text.lower()\n"
            "            if symbol_lower in {\"cash\", \"date\", \"datetime\"}:\n"
            "                continue\n"
            "            if symbol_text not in self.vt_symbols:\n"
            "                self.debug_stats['symbol_not_in_vt'] += 1\n"
            "                continue\n"
            "            bar = bars.get(symbol_text)\n"
            "            if bar is None:\n"
            "                self.debug_stats['bar_missing'] += 1\n"
            "                continue\n"
            "            price = float(getattr(bar, \"close_price\", 0.0) or getattr(bar, \"price\", 0.0) or 0.0)\n"
            "            target_pos = self._weight_to_target_pos(float(target_weight), price)\n"
            "            if target_pos == 0:\n"
            "                self.debug_stats['zero_target_pos'] += 1\n"
            "            else:\n"
            "                self.debug_stats['nonzero_target_pos'] += 1\n"
            "                if len(self.debug_nonzero_targets) < self.debug_trace_limit:\n"
            "                    self.debug_nonzero_targets.append({\n"
            "                        'datetime': str(getattr(bar, 'datetime', '')),\n"
            "                        'symbol': symbol_text,\n"
            "                        'target_weight': float(target_weight),\n"
            "                        'target_pos': int(target_pos),\n"
            "                        'price': float(price),\n"
            "                    })\n"
            "            self.set_target(symbol_text, target_pos)\n"
            "\n"
            "        self.debug_stats['rebalance_calls'] += 1\n"
            "        self.rebalance_portfolio(bars)\n"
            "\n"
            "    def update_trade(self, trade: TradeData):\n"
            "        base_update_trade = getattr(super(), \"update_trade\", None)\n"
            "        if callable(base_update_trade):\n"
            "            base_update_trade(trade)\n"
            "        self.capital_tracker.on_trade(trade)\n"
            "\n"
            "    def on_order(self, order: OrderData):\n"
            "        pass\n"
        )

    @classmethod
    def _build_fixed_strategy_code(cls, template_family: str) -> str:
        if template_family == "portfolio":
            return cls._build_fixed_portfolio_strategy_code()
        raise RuntimeError(f"unsupported template_family for fixed strategy generation: {template_family}")

    @staticmethod
    def _resolve_interval(interval_name: str) -> Any:
        from vnpy.trader.constant import Interval  # type: ignore[import-not-found]

        mapping = {
            "1m": Interval.MINUTE,
            "5m": Interval.MINUTE,
            "15m": Interval.MINUTE,
            "30m": Interval.MINUTE,
            "60m": Interval.HOUR,
            "1h": Interval.HOUR,
            "1d": Interval.DAILY,
            "1w": Interval.WEEKLY,
            "tick": Interval.TICK,
        }
        return mapping.get(interval_name, Interval.DAILY)

    @staticmethod
    def _estimate_portfolio_replay_points(engine: Any) -> int:
        """Estimate number of replay steps after warmup for portfolio backtesting."""
        dts_raw = getattr(engine, "dts", None)
        if not dts_raw:
            return 0

        dts = sorted(list(dts_raw))
        warmup_days = int(getattr(engine, "days", 0) or 0)
        if warmup_days <= 0:
            return len(dts)

        day_count = 0
        current_dt: datetime | None = None
        warmup_end_ix = len(dts)

        for ix, dt in enumerate(dts):
            if current_dt and dt.day != current_dt.day:
                day_count += 1
                if day_count >= warmup_days:
                    warmup_end_ix = ix
                    break
            current_dt = dt

        remaining = len(dts) - warmup_end_ix
        return remaining if remaining > 0 else 0

    @classmethod
    def _attach_portfolio_progress_hook(cls, engine: Any) -> None:
        """Emit replay progress updates during portfolio history playback."""
        total_points = cls._estimate_portfolio_replay_points(engine)
        if total_points <= 0:
            return

        original_new_bars = getattr(engine, "new_bars", None)
        strategy = getattr(engine, "strategy", None)
        output = getattr(engine, "output", None)
        if not callable(original_new_bars) or strategy is None or not callable(output):
            return

        state = {
            "processed": 0,
            "next_checkpoint": 5,
        }

        def wrapped_new_bars(dt: Any) -> None:
            original_new_bars(dt)

            if not bool(getattr(strategy, "trading", False)):
                return

            state["processed"] += 1
            processed = state["processed"]
            checkpoint = state["next_checkpoint"]
            if processed < total_points and processed * 100 < checkpoint * total_points:
                return

            percent = min(int(processed * 100 / max(total_points, 1)), 100)
            output(f"回放进度: {percent}% ({processed}/{total_points})")
            while state["next_checkpoint"] <= percent:
                state["next_checkpoint"] += 5

        setattr(engine, "new_bars", wrapped_new_bars)

    @classmethod
    def _run_engine_backtest(
        cls,
        *,
        engine_module: str,
        strategy_class: type,
        vt_symbol: str,
        vt_symbols: list[str] | None = None,
        interval_name: str,
        start: datetime,
        end: datetime,
        experiment_spec: dict[str, object],
        strategy_settings: dict[str, object] | None = None,
        contract_settings: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, Any]:
        module = __import__(engine_module, fromlist=["BacktestingEngine"])
        BacktestingEngine = getattr(module, "BacktestingEngine")
        engine = BacktestingEngine()

        rate = cls._resolve_float_config(experiment_spec, "commission_rate", 0.0003)
        slippage = cls._resolve_float_config(experiment_spec, "slippage", 0.0)
        size = cls._resolve_float_config(experiment_spec, "contract_size", 1.0)
        pricetick = cls._resolve_float_config(experiment_spec, "price_tick", 0.01)
        capital = cls._resolve_float_config(experiment_spec, "capital", 1_000_000.0)

        interval = cls._resolve_interval(interval_name)
        if vt_symbols is not None:
            contract_settings = contract_settings or {}

            def _contract_value(symbol: str, key: str, default_value: float) -> float:
                raw_contract = contract_settings.get(symbol, {})
                if isinstance(raw_contract, dict):
                    value = raw_contract.get(key, default_value)
                    if isinstance(value, (int, float)):
                        return float(value)
                return float(default_value)

            rates = {symbol: _contract_value(symbol, "long_rate", rate) for symbol in vt_symbols}
            slippages = {symbol: 0.0 for symbol in vt_symbols}
            sizes = {symbol: _contract_value(symbol, "size", size) for symbol in vt_symbols}
            priceticks = {symbol: _contract_value(symbol, "pricetick", pricetick) for symbol in vt_symbols}
            for symbol in vt_symbols:
                slippages[symbol] = _contract_value(symbol, "short_rate", slippage)

            engine.set_parameters(
                vt_symbols=vt_symbols,
                interval=interval,
                start=start,
                rates=rates,
                slippages=slippages,
                sizes=sizes,
                priceticks=priceticks,
                capital=capital,
                end=end,
            )
        else:
            engine.set_parameters(
                vt_symbol=vt_symbol,
                interval=interval,
                start=start,
                end=end,
                rate=rate,
                slippage=slippage,
                size=size,
                pricetick=pricetick,
                capital=capital,
            )
        engine.add_strategy(strategy_class, strategy_settings or {})
        engine.load_data()
        if engine_module == "vnpy_portfoliostrategy.backtesting":
            cls._attach_portfolio_progress_hook(engine)
        engine.run_backtesting()
        daily_df = engine.calculate_result()
        stats = engine.calculate_statistics(output=False)
        if isinstance(stats, dict):
            stats["_daily_df"] = daily_df
            strategy = getattr(engine, "strategy", None)
            if strategy is not None:
                debug_stats = getattr(strategy, "debug_stats", None)
                debug_nonzero_targets = getattr(strategy, "debug_nonzero_targets", None)
                stats["_strategy_debug"] = {
                    "debug_stats": debug_stats if isinstance(debug_stats, dict) else {},
                    "nonzero_target_samples": debug_nonzero_targets if isinstance(debug_nonzero_targets, list) else [],
                }
            stats["_engine_debug"] = {
                "order_count": len(getattr(engine, "orders", {})) if hasattr(engine, "orders") else None,
                "trade_count": len(getattr(engine, "trades", {})) if hasattr(engine, "trades") else None,
                "limit_order_count": len(getattr(engine, "limit_orders", {})) if hasattr(engine, "limit_orders") else None,
                "daily_result_count": len(getattr(engine, "daily_results", {})) if hasattr(engine, "daily_results") else None,
                "daily_df_rows": int(len(daily_df)) if pd is not None and isinstance(daily_df, pd.DataFrame) else None,
            }
        return stats

    @staticmethod
    def _resolve_backtest_output_dir(state: WorkflowState) -> Path:
        run_dir = get_trace_run_dir(state)
        epoch_index = int(state.get("epoch_index", 1))
        output_dir = run_dir / f"epoch_{epoch_index:03d}" / "strategytester" / "backtest_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @staticmethod
    def _normalize_daily_df_for_export(daily_df: Any) -> Any:
        if pd is None:
            return None
        if daily_df is None:
            return None
        if isinstance(daily_df, pd.DataFrame):
            normalized = daily_df.copy()
        elif isinstance(daily_df, list) and all(isinstance(item, dict) for item in daily_df):
            normalized = pd.DataFrame(daily_df)
        else:
            return None

        if normalized.empty:
            return normalized

        if isinstance(normalized.index, pd.DatetimeIndex) and "date" not in normalized.columns:
            normalized = normalized.reset_index()
            column_names = list(normalized.columns)
            if column_names:
                column_names[0] = "date"
                normalized.columns = column_names
        elif "datetime" in normalized.columns and "date" not in normalized.columns:
            normalized = normalized.rename(columns={"datetime": "date"})
        elif "trade_date" in normalized.columns and "date" not in normalized.columns:
            normalized = normalized.rename(columns={"trade_date": "date"})

        if "date" not in normalized.columns:
            probe = normalized.reset_index()
            if len(probe.columns) > len(normalized.columns):
                first_col = str(probe.columns[0])
                candidate = pd.to_datetime(probe[first_col], errors="coerce")
                if isinstance(candidate, pd.Series):
                    has_valid_date = bool(candidate.notna().any())
                else:
                    has_valid_date = bool(pd.notna(candidate))
                if has_valid_date:
                    probe = probe.rename(columns={first_col: "date"})
                    normalized = probe

        if "date" in normalized.columns:
            normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
            normalized = normalized.sort_values("date", kind="stable")

        return normalized.reset_index(drop=True)

    @classmethod
    def _export_backtest_artifacts(
        cls,
        *,
        state: WorkflowState,
        stats: dict[str, Any],
        backtest_result: BacktestResult,
        template_family: str,
        experiment_spec: dict[str, object],
        output_subdir: str | None = None,
    ) -> dict[str, str]:
        output_dir = cls._resolve_backtest_output_dir(state)
        if isinstance(output_subdir, str) and output_subdir.strip():
            safe_parts = [
                part
                for part in Path(output_subdir.strip()).parts
                if part not in {"", ".", ".."}
                and ":" not in part
                and part not in {"/", "\\"}
            ]
            if safe_parts:
                output_dir = output_dir.joinpath(*safe_parts)
                output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}

        summary_path = output_dir / "performance_summary.json"
        summary_payload = {
            "template_family": template_family,
            "summary": backtest_result.summary,
            "annual_return": backtest_result.annual_return,
            "sharpe": backtest_result.sharpe,
            "max_drawdown": backtest_result.max_drawdown,
            "max_ddpercent": backtest_result.max_ddpercent,
            "event_gross_up_rate": backtest_result.event_gross_up_rate,
            "event_joint_minute_hit_rate": backtest_result.event_joint_minute_hit_rate,
            "event_success_rate": backtest_result.event_success_rate,
            "event_success_column": backtest_result.event_success_column,
            "event_success_metric": backtest_result.event_success_metric,
            "event_success_metric_value": backtest_result.event_success_metric_value,
            "evaluation_requirements": deepcopy(
                experiment_spec.get("evaluation", {})
                if isinstance(experiment_spec.get("evaluation", {}), dict)
                else {}
            ),
            "passed": backtest_result.passed,
            "stats": {
                key: value
                for key, value in stats.items()
                if key != "_daily_df" and isinstance(value, (str, int, float, bool, type(None)))
            },
        }
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["performance_summary"] = str(summary_path)

        if pd is None:
            return paths

        daily_df = cls._normalize_daily_df_for_export(stats.get("_daily_df"))
        if daily_df is None or not isinstance(daily_df, pd.DataFrame) or daily_df.empty:
            return paths

        daily_equity = pd.DataFrame()
        if "date" in daily_df.columns:
            daily_equity["date"] = daily_df["date"]

        if "balance" in daily_df.columns:
            daily_equity["equity"] = pd.to_numeric(daily_df["balance"], errors="coerce")
        elif "net_pnl" in daily_df.columns:
            capital = cls._resolve_float_config(experiment_spec, "capital", 1_000_000.0)
            pnl_series = pd.to_numeric(daily_df["net_pnl"], errors="coerce")
            if not isinstance(pnl_series, pd.Series):
                pnl_series = pd.Series([pnl_series], dtype="float64")
            cumulative_pnl = pnl_series.fillna(0.0).astype(float).cumsum()
            daily_equity["equity"] = cumulative_pnl.add(float(capital))

        for optional_col in ("net_pnl", "drawdown", "return", "turnover", "commission", "slippage"):
            if optional_col in daily_df.columns:
                daily_equity[optional_col] = pd.to_numeric(daily_df[optional_col], errors="coerce")

        if not daily_equity.empty:
            daily_equity_path = output_dir / "daily_equity.csv"
            daily_equity.to_csv(daily_equity_path, index=False)
            paths["daily_equity"] = str(daily_equity_path)

            if "equity" in daily_equity.columns:
                try:
                    import matplotlib  # type: ignore[import-not-found]
                    import matplotlib.dates as mdates  # type: ignore[import-not-found]
                    import matplotlib.pyplot as plt  # type: ignore[import-not-found]

                    matplotlib.use("Agg", force=True)
                    fig, axis = plt.subplots(figsize=(10, 4))
                    x = daily_equity["date"] if "date" in daily_equity.columns else range(len(daily_equity))
                    axis.plot(x, daily_equity["equity"], linewidth=1.2)
                    if "date" in daily_equity.columns:
                        locator = mdates.AutoDateLocator()
                        formatter = mdates.ConciseDateFormatter(locator)
                        axis.xaxis.set_major_locator(locator)
                        axis.xaxis.set_major_formatter(formatter)
                    axis.set_title("Backtest Equity Curve")
                    axis.set_xlabel("Date")
                    axis.set_ylabel("Equity")
                    axis.grid(alpha=0.25)
                    fig.tight_layout()
                    equity_png = output_dir / "equity_curve.png"
                    fig.savefig(str(equity_png), dpi=150)
                    plt.close(fig)
                    paths["equity_curve_png"] = str(equity_png)
                except Exception:
                    pass

        for stats_key, filename, path_key in (
            ("_trades_df", "trades.csv", "trades"),
            ("_target_weights_df", "target_weights.csv", "target_weights"),
        ):
            raw_frame = stats.get(stats_key)
            if not isinstance(raw_frame, pd.DataFrame) or raw_frame.empty:
                continue
            if stats_key == "_target_weights_df" and template_family == "event_parquet":
                from quanta_agents.event_parquet_backtest import _prepare_weight_signals

                export_frame = _prepare_weight_signals(raw_frame, None)
                output_path = output_dir / "target_weights_nonzero.csv"
                export_frame.to_csv(output_path, index=False)
                paths[path_key] = str(output_path)
                continue
            export_frame = raw_frame.copy()
            if isinstance(export_frame.index, pd.DatetimeIndex):
                index_name = export_frame.index.name or "datetime"
                if index_name not in export_frame.columns:
                    export_frame = export_frame.reset_index(names=index_name)
            output_path = output_dir / filename
            export_frame.to_csv(output_path, index=False)
            paths[path_key] = str(output_path)

        position_columns = [
            column
            for column in daily_df.columns
            if column.lower() in {"pos", "position", "end_pos", "net_pos", "holding", "holdings"}
        ]
        if position_columns:
            daily_positions = pd.DataFrame()
            if "date" in daily_df.columns:
                daily_positions["date"] = daily_df["date"]
            for column in position_columns:
                daily_positions[column] = pd.to_numeric(daily_df[column], errors="coerce")
            daily_positions_path = output_dir / "daily_positions.csv"
            daily_positions.to_csv(daily_positions_path, index=False)
            paths["daily_positions"] = str(daily_positions_path)

        return paths

    @classmethod
    def _safe_export_backtest_artifacts(
        cls,
        *,
        state: WorkflowState,
        stats: dict[str, Any],
        backtest_result: BacktestResult,
        template_family: str,
        experiment_spec: dict[str, object],
        output_subdir: str | None = None,
    ) -> dict[str, str]:
        """保存文件失败时保留已经算出的回测结果。"""
        try:
            return cls._export_backtest_artifacts(
                state=state,
                stats=stats,
                backtest_result=backtest_result,
                template_family=template_family,
                experiment_spec=experiment_spec,
                output_subdir=output_subdir,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            state["history"].append(
                f"Epoch {state['epoch_index']}: backtest files could not be saved ({error})"
            )
            try:
                write_trace_json(
                    state,
                    agent_name=cls.name,
                    stage="backtest_artifact_error",
                    payload={
                        "template_family": template_family,
                        "output_subdir": output_subdir or "",
                        "error": error,
                    },
                )
            except Exception:
                pass
            return {"artifact_error": error}

    @staticmethod
    def _to_ratio(value: object) -> float:
        if not isinstance(value, (int, float)):
            return 0.0
        raw = float(value)
        return raw / 100.0 if abs(raw) > 1.0 else raw

    @staticmethod
    def _infer_returns_are_percent(stats: dict[str, Any]) -> bool | None:
        capital = StrategyTester._coerce_optional_float(stats.get("capital"))
        end_balance = StrategyTester._coerce_optional_float(stats.get("end_balance"))
        total_return = StrategyTester._coerce_optional_float(stats.get("total_return"))
        if capital is None or end_balance is None or total_return is None or capital == 0:
            return None

        derived_ratio = (end_balance - capital) / capital
        tolerance = 1e-6
        if abs(total_return - derived_ratio) <= tolerance:
            return False
        if abs((total_return / 100.0) - derived_ratio) <= tolerance:
            return True
        return None

    @classmethod
    def _normalize_return_metric(cls, value: object, returns_are_percent: bool | None) -> float:
        if not isinstance(value, (int, float)):
            return 0.0
        raw = float(value)
        if returns_are_percent is True:
            return raw / 100.0
        if returns_are_percent is False:
            return raw
        return cls._to_ratio(raw)

    @staticmethod
    def _coerce_optional_float(value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_optional_metric(stats: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if key in stats:
                value = StrategyTester._coerce_optional_float(stats.get(key))
                if value is not None:
                    return value
        return None

    @classmethod
    def _resolve_max_ddpercent(cls, stats: dict[str, Any], experiment_spec: dict[str, object]) -> float:
        if pd is not None:
            daily_df = cls._normalize_daily_df_for_export(stats.get("_daily_df"))
            if isinstance(daily_df, pd.DataFrame) and not daily_df.empty:
                if "balance" in daily_df.columns:
                    equity_series = pd.to_numeric(daily_df["balance"], errors="coerce")
                    if isinstance(equity_series, pd.Series):
                        equity = equity_series.dropna().astype(float)
                    else:
                        equity = pd.Series([equity_series], dtype="float64").dropna().astype(float)
                elif "net_pnl" in daily_df.columns:
                    capital = cls._resolve_float_config(experiment_spec, "capital", 1_000_000.0)
                    pnl = pd.to_numeric(daily_df["net_pnl"], errors="coerce")
                    if not isinstance(pnl, pd.Series):
                        pnl = pd.Series([pnl], dtype="float64")
                    pnl = pnl.fillna(0.0).astype(float)
                    equity = pnl.cumsum().add(float(capital)).dropna().astype(float)
                else:
                    equity = None

                if equity is not None and not equity.empty:
                    high_watermark = equity.cummax()
                    valid_mask = high_watermark > 0
                    if bool(valid_mask.any()):
                        valid_equity = equity[valid_mask]
                        valid_hwm = high_watermark[valid_mask]
                        drawdown_ratio = (valid_equity - valid_hwm) / valid_hwm
                        if isinstance(drawdown_ratio, pd.Series) and not drawdown_ratio.empty:
                            return abs(float(drawdown_ratio.min()))
                        if isinstance(drawdown_ratio, (int, float)):
                            return abs(float(drawdown_ratio))

        ddpercent = cls._coerce_optional_float(stats.get("max_ddpercent"))
        if ddpercent is not None:
            raw = abs(float(ddpercent))
            return raw / 100.0 if raw > 1.0 else raw

        return 0.0

    @classmethod
    def _stats_to_result(cls, stats: dict[str, Any], experiment_spec: dict[str, object]) -> BacktestResult:
        sharpe = float(stats.get("sharpe_ratio", 0.0))
        returns_are_percent = cls._infer_returns_are_percent(stats)

        annual_return = cls._normalize_return_metric(stats.get("annual_return", 0.0), returns_are_percent)
        if annual_return == 0.0:
            annual_return = cls._normalize_return_metric(stats.get("total_return", 0.0), returns_are_percent)

        max_drawdown_amount = cls._coerce_optional_float(stats.get("max_drawdown"))
        if max_drawdown_amount is None:
            max_drawdown_amount = 0.0

        max_ddpercent = cls._resolve_max_ddpercent(stats, experiment_spec)

        trade_count_raw = cls._extract_optional_metric(
            stats,
            (
                "total_trade_count",
                "trade_count",
                "total_trades",
            ),
        )
        trade_count = int(trade_count_raw) if trade_count_raw is not None else None
        event_net_mean = cls._coerce_optional_float(stats.get("event_net_mean_return"))
        event_gross_up_rate = cls._coerce_optional_float(
            stats.get("event_gross_up_rate")
        )
        event_success_rate = cls._coerce_optional_float(stats.get("event_success_rate"))
        event_success_column_raw = stats.get("event_success_column")
        event_success_column = (
            str(event_success_column_raw)
            if isinstance(event_success_column_raw, str)
            else None
        )
        event_joint_minute_hit_rate = cls._coerce_optional_float(
            stats.get("event_joint_minute_hit_rate")
        )
        if (
            event_joint_minute_hit_rate is None
            and event_success_column == "joint_minute_hit"
        ):
            event_joint_minute_hit_rate = event_success_rate

        gate = resolve_quality_gate(experiment_spec)
        evaluation = experiment_spec.get("evaluation", {})
        configured_success_metric = (
            str(evaluation.get("event_success_metric", DEFAULT_EVENT_SUCCESS_METRIC))
            if isinstance(evaluation, dict)
            else DEFAULT_EVENT_SUCCESS_METRIC
        )
        event_success_metric_value, event_success_metric_label = (
            select_event_success_value(
                configured_success_metric,
                legacy_rate=event_success_rate,
                legacy_column=event_success_column,
                gross_up_rate=event_gross_up_rate,
                joint_minute_hit_rate=event_joint_minute_hit_rate,
            )
        )
        if gate is None:
            passed = (
                annual_return > 0.0
                and sharpe > 0.0
                and max_ddpercent <= 0.25
                and (trade_count is None or trade_count > 0)
            )
        else:
            passed = True
            if gate.min_sharpe is not None:
                passed = passed and sharpe >= gate.min_sharpe
            if gate.min_annual_return is not None:
                passed = passed and annual_return >= gate.min_annual_return
            if gate.max_drawdown is not None:
                passed = passed and max_ddpercent <= gate.max_drawdown
            if gate.min_trade_count is not None:
                passed = passed and (
                    trade_count is not None and trade_count >= gate.min_trade_count
                )
            if gate.min_event_net_mean is not None:
                passed = passed and (
                    event_net_mean is not None
                    and event_net_mean >= gate.min_event_net_mean
                )
            if gate.min_event_success_rate is not None:
                passed = passed and (
                    event_success_metric_value is not None
                    and event_success_metric_value >= gate.min_event_success_rate
                )

        gate_summary = (
            "default_gate(min_return>0, min_sharpe>0, max_drawdown=25%, trades>0_if_reported)"
        )
        if gate is not None:
            parts: list[str] = []
            if gate.min_sharpe is not None:
                parts.append(f"min_sharpe={gate.min_sharpe:.2f}")
            if gate.min_annual_return is not None:
                parts.append(f"min_return={gate.min_annual_return:.2%}")
            if gate.max_drawdown is not None:
                parts.append(f"max_drawdown={gate.max_drawdown:.2%}")
            if gate.min_trade_count is not None:
                parts.append(f"min_trade_count={gate.min_trade_count}")
            if gate.min_event_net_mean is not None:
                parts.append(f"min_event_net_mean={gate.min_event_net_mean:.4%}")
            if gate.min_event_success_rate is not None:
                parts.append(
                    f"min_event_success_rate={gate.min_event_success_rate:.2%}"
                    f"(metric={gate.event_success_metric}:{event_success_metric_label})"
                )
            gate_summary = f"gate({', '.join(parts)})" if parts else "gate(no limits configured)"

        summary = (
            f"Backtest engine metrics: annual_return={annual_return:.2%}, "
            f"sharpe={sharpe:.2f}, max_drawdown={max_drawdown_amount:.2f}, max_ddpercent={max_ddpercent:.2%}, "
            f"{gate_summary}, "
            f"passed={passed}"
        )
        event_gross_mean = cls._coerce_optional_float(stats.get("event_gross_mean_return"))
        event_net_median = cls._coerce_optional_float(stats.get("event_net_median_return"))
        event_success_ci_low = cls._coerce_optional_float(stats.get("event_success_ci_low"))
        event_success_ci_high = cls._coerce_optional_float(stats.get("event_success_ci_high"))
        if any(
            value is not None
            for value in (
                event_net_mean,
                event_gross_up_rate,
                event_joint_minute_hit_rate,
                event_success_rate,
            )
        ):
            event_parts = [
                f"event_count={trade_count if trade_count is not None else 0}",
                "interpretation=event_study_only_due_to_A_share_T_plus_1",
            ]
            if event_gross_mean is not None:
                event_parts.append(f"event_gross_mean={event_gross_mean:.4%}")
            if event_net_mean is not None:
                event_parts.append(f"event_net_mean={event_net_mean:.4%}")
            if event_net_median is not None:
                event_parts.append(f"event_net_median={event_net_median:.4%}")
            if event_gross_up_rate is not None:
                event_parts.append(
                    f"gross_up_rate={event_gross_up_rate:.2%}"
                )
            if event_joint_minute_hit_rate is not None:
                event_parts.append(
                    f"joint_minute_hit={event_joint_minute_hit_rate:.2%}"
                )
            if event_success_rate is not None:
                label = str(event_success_column or "event_success")
                if not (
                    label == "joint_minute_hit"
                    and event_joint_minute_hit_rate == event_success_rate
                ):
                    event_parts.append(f"{label}={event_success_rate:.2%}")
                if (
                    configured_success_metric != "gross_up_rate"
                    and event_success_ci_low is not None
                    and event_success_ci_high is not None
                ):
                    event_parts.append(
                        f"success_95pct_range=[{event_success_ci_low:.2%}, {event_success_ci_high:.2%}]"
                    )
            event_parts.append(
                "event_gate_metric="
                f"{configured_success_metric}:{event_success_metric_label}"
            )
            if event_success_metric_value is not None:
                event_parts.append(
                    f"event_gate_value={event_success_metric_value:.2%}"
                )
            summary = f"{summary}, {', '.join(event_parts)}"

        win_rate_raw = cls._extract_optional_metric(
            stats,
            (
                "win_rate",
                "winning_rate",
                "win_ratio",
            ),
        )
        win_rate = cls._to_ratio(win_rate_raw) if win_rate_raw is not None else None

        profit_loss_ratio = cls._extract_optional_metric(
            stats,
            (
                "profit_loss_ratio",
                "pnl_ratio",
                "win_loss_ratio",
            ),
        )

        return BacktestResult(
            annual_return=annual_return,
            sharpe=sharpe,
            max_drawdown=max_drawdown_amount,
            passed=passed,
            summary=summary,
            max_ddpercent=max_ddpercent,
            trade_count=trade_count,
            win_rate=win_rate,
            profit_loss_ratio=profit_loss_ratio,
            event_net_mean_return=event_net_mean,
            event_gross_up_rate=event_gross_up_rate,
            event_joint_minute_hit_rate=event_joint_minute_hit_rate,
            event_success_rate=event_success_rate,
            event_success_column=event_success_column,
            event_success_metric=configured_success_metric,
            event_success_metric_value=event_success_metric_value,
        )

    @classmethod
    def _build_event_development_report(
        cls,
        *,
        stats: dict[str, Any],
        backtest_result: BacktestResult,
        experiment_spec: dict[str, object],
        start: datetime,
        end: datetime,
    ) -> dict[str, object]:
        metric_keys = (
            "capital",
            "end_balance",
            "total_return",
            "annual_return",
            "annual_volatility",
            "sharpe_ratio",
            "max_drawdown",
            "max_ddpercent",
            "total_trade_count",
            "total_commission",
            "total_slippage",
            "win_rate",
            "profit_loss_ratio",
            "event_gross_mean_return",
            "event_gross_median_return",
            "event_gross_up_rate",
            "event_net_mean_return",
            "event_net_median_return",
            "event_net_win_rate",
            "event_cost_rate_mean",
            "event_success_column",
            "event_success_observation_count",
            "event_success_positive_count",
            "event_success_rate",
            "event_success_ci_low",
            "event_success_ci_high",
            "event_joint_minute_hit_rate",
            "event_joint_minute_hit_count",
            "event_joint_minute_hit_positive_count",
        )
        overall = {
            key: deepcopy(stats.get(key))
            for key in metric_keys
            if key in stats
        }
        overall.update(
            {
                "event_success_metric": backtest_result.event_success_metric,
                "event_success_metric_value": backtest_result.event_success_metric_value,
            }
        )
        debug_source = stats.get("_backtest_debug", {})
        debug = deepcopy(debug_source) if isinstance(debug_source, dict) else {}
        flow_count_keys = (
            "raw_event_count",
            "valid_return_event_count",
            "invalid_return_event_count",
            "nonzero_signal_count",
            "matched_before_cooldown_count",
            "matched_signal_count",
            "unmatched_signal_count",
            "cooldown_removed_count",
            "final_event_count",
        )
        flow_counts = {
            key: debug.get(key)
            for key in flow_count_keys
            if key in debug
        }
        period = {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
        }
        train_period = {
            "start": str(experiment_spec.get("train_start", "")),
            "end": str(experiment_spec.get("train_end", "")),
        }
        evaluation_requirements = experiment_spec.get("evaluation", {})
        if not isinstance(evaluation_requirements, dict):
            evaluation_requirements = {}

        fold_record = {
            "name": "development",
            "event_horizon_minutes": debug.get("holding_minutes"),
            "event_horizon_source": stats.get("event_horizon_source"),
            "event_return_columns": deepcopy(debug.get("event_return_columns", [])),
            "test_start": period["start"],
            "test_end": period["end"],
            "annual_return": backtest_result.annual_return,
            "sharpe": backtest_result.sharpe,
            "max_ddpercent": backtest_result.max_ddpercent,
            "trade_count": backtest_result.trade_count,
            "win_rate": backtest_result.win_rate,
            "profit_loss_ratio": backtest_result.profit_loss_ratio,
            "event_gross_mean_return": stats.get("event_gross_mean_return"),
            "event_gross_up_rate": stats.get("event_gross_up_rate"),
            "event_joint_minute_hit_rate": stats.get(
                "event_joint_minute_hit_rate"
            ),
            "event_net_mean_return": stats.get("event_net_mean_return"),
            "event_net_median_return": stats.get("event_net_median_return"),
            "event_net_win_rate": stats.get("event_net_win_rate"),
            "event_success_column": stats.get("event_success_column"),
            "event_success_rate": stats.get("event_success_rate"),
            "event_success_metric": backtest_result.event_success_metric,
            "event_success_metric_value": backtest_result.event_success_metric_value,
            "passed": backtest_result.passed,
            "summary": backtest_result.summary,
        }
        return {
            "report_kind": "event_parquet",
            "period_kind": "development",
            "event_horizon_minutes": debug.get("holding_minutes"),
            "event_horizon_source": stats.get("event_horizon_source"),
            "event_return_columns": deepcopy(debug.get("event_return_columns", [])),
            "event_success_metric": backtest_result.event_success_metric,
            "event_success_metric_value": backtest_result.event_success_metric_value,
            "development_period": period,
            "train_period": train_period,
            "fold_count": 1,
            "passed_fold_count": 1 if backtest_result.passed else 0,
            "fold_pass_ratio": 1.0 if backtest_result.passed else 0.0,
            "passed": backtest_result.passed,
            "failure_reasons": [] if backtest_result.passed else [backtest_result.summary],
            "evaluation_requirements": deepcopy(evaluation_requirements),
            "definitions": {
                "event_gross_up_rate": "gross_return > 0",
                "event_net_win_rate": "net_return > 0",
                "event_success_rate": (
                    f"{stats.get('event_success_column')} == true"
                    if stats.get("event_success_column")
                    else "没有可用结果标签"
                ),
                "event_joint_minute_hit_rate": "joint_minute_hit == true",
                "event_success_metric": (
                    "evaluation.event_success_metric 指定用于验收的比例；"
                    "event_success_metric_value 是实际比较值"
                ),
                "interpretation": "A股分钟事件研究，不代表当天可以买入并卖出",
            },
            "flow_counts": flow_counts,
            "overall": overall,
            "by_year": deepcopy(stats.get("yearly_event_stats", [])),
            "by_month": deepcopy(stats.get("monthly_event_stats", [])),
            "cost_scenarios": deepcopy(stats.get("cost_scenarios", [])),
            "backtest_debug": debug,
            "folds": [fold_record],
        }

    @staticmethod
    def _route_backtest_failure(state: WorkflowState, error: str) -> None:
        state["test_result"] = None
        state["validation_result"] = {}
        state["validation_summary"] = {
            "passed": False,
            "stats_summary": f"Backtest failed: {error}",
            "suggestions": [f"Fix the strategy so the backtest can run: {error}"],
        }
        state["backtest_feedback"] = f"backtest_error: {error}"

        current_round = max(int(state.get("strategy_validate_round", 1)), 1)
        max_rounds = get_max_strategy_validate_rounds()
        if current_round < max_rounds:
            state["strategy_validate_round"] = current_round + 1
            state["phase"] = "strategy"
            return

        state["phase"] = "done"
        state["manager_notes"] = (
            "Backtest still failed after all allowed code-writing rounds; "
            f"the technical error was not sent to research optimization: {error}"
        )

    def run(self, state: WorkflowState) -> WorkflowState:
        print_agent_progress(state, agent_name=self.name, message="starting")
        state["test_result"] = None
        state["backtest_debug"] = {}  # type: ignore[typeddict-unknown-key]
        runtime_env = self._runtime_env_snapshot()
        strategy_result = state.get("strategy_result", {})
        strategy_result_dict = strategy_result if isinstance(strategy_result, dict) else {}
        template_family = "unknown"
        expected_base_class = ""

        try:
            backtest_dataset_specs = self._resolve_backtest_dataset_specs(state, strategy_result_dict)
            backtest_db_backend = self._resolve_backtest_db_backend(backtest_dataset_specs=backtest_dataset_specs)
            experiment_spec = state["experiment_spec"]
            evaluation_stage = str(state.get("evaluation_stage", "development"))
            if (
                evaluation_stage == "development"
                and backtest_db_backend != "parquet"
                and self._requests_fixed_development_checks(experiment_spec)
            ):
                raise RuntimeError(
                    "development_folds, run_cost_stress, and run_delay_stress "
                    "currently require the local daily Parquet backtest"
                )
            if (
                evaluation_stage == "development"
                and backtest_db_backend == "parquet"
                and self._requests_fixed_development_checks(experiment_spec)
                and not self._has_development_periods(experiment_spec)
            ):
                raise RuntimeError(
                    "time-forward development checks require train_start, train_end, "
                    "validate_start, and validate_end"
                )
            if backtest_db_backend not in {"parquet", "event_parquet"}:
                self._ensure_vnpy_database_settings(
                    backtest_db_backend=backtest_db_backend,
                )
                if backtest_db_backend == "dolphindb":
                    self._ensure_vnpy_dolphindb_compatibility(backtest_dataset_specs=backtest_dataset_specs)
                elif backtest_db_backend == "sqlite":
                    self._ensure_vnpy_sqlite_compatibility(backtest_dataset_specs=backtest_dataset_specs)
            if (
                backtest_db_backend == "parquet"
                and evaluation_stage == "development"
                and self._has_development_periods(experiment_spec)
                and self._resolve_bool_config(
                    experiment_spec,
                    "walk_forward_development",
                    True,
                )
            ):
                print_agent_progress(
                    state,
                    agent_name=self.name,
                    message="running time-forward local Parquet development tests",
                )
                return self._run_local_parquet_development(
                    state,
                    strategy_result_dict,
                    experiment_spec,
                )
            start, end = self._resolve_backtest_window(
                experiment_spec,
                evaluation_stage,
            )
            strategy_result_for_test = dict(strategy_result) if isinstance(strategy_result, dict) else {}
            recomputed_output_weights, recompute_meta = self._recompute_output_weights_for_backtest(
                state,
                strategy_result_for_test,
                experiment_spec,
                start,
                end,
            )
            evaluation_membership_df = recompute_meta.pop("_membership_df", None)
            strategy_result_for_test["output_weights"] = recomputed_output_weights

            template_family, expected_base_class, output_weights_df, tradable_columns = self._resolve_template_from_output_weights_df(
                strategy_result_for_test
            )

            if backtest_db_backend == "event_parquet":
                from quanta_agents.event_parquet_backtest import run_event_parquet_backtest

                self._validate_event_long_only_weights(
                    output_weights_df,
                    tradable_columns,
                )

                event_path = os.getenv("QUANTA_EVENT_FEATURES_PATH", "").strip()
                if not event_path:
                    raise RuntimeError("QUANTA_EVENT_FEATURES_PATH 未设置，无法运行分钟事件回测")

                template_family = "event_parquet"
                state["selected_template"] = "EventParquetTargetWeight"
                state["selected_base_class"] = "TargetWeightEventBacktest"
                default_buy_cost = self._resolve_float_config(experiment_spec, "commission_rate", 0.0003)
                buy_cost = self._resolve_float_config(experiment_spec, "buy_cost", default_buy_cost)
                sell_cost = self._resolve_float_config(
                    experiment_spec,
                    "sell_cost",
                    default_buy_cost + 0.0005,
                )
                slippage = self._resolve_float_config(experiment_spec, "slippage", 0.0)
                capital = self._resolve_float_config(experiment_spec, "capital", 1_000_000.0)
                horizon_minutes, horizon_source = self._resolve_event_horizon_minutes(
                    experiment_spec,
                    strategy_result_for_test,
                )
                cooldown_minutes = int(
                    self._resolve_float_config(experiment_spec, "event_cooldown_minutes", 20.0)
                )

                print_agent_progress(
                    state,
                    agent_name=self.name,
                    message="running local minute-event Parquet backtest",
                )
                stats = run_event_parquet_backtest(
                    output_weights_df,
                    event_path=event_path,
                    start=start,
                    end=end,
                    horizon_minutes=horizon_minutes,
                    capital=capital,
                    buy_cost=buy_cost,
                    sell_cost=sell_cost,
                    slippage=slippage,
                    cooldown_minutes=cooldown_minutes,
                )
                stats["event_horizon_minutes"] = horizon_minutes
                stats["event_horizon_source"] = horizon_source
                backtest_debug = stats.get("_backtest_debug", {})
                if isinstance(backtest_debug, dict):
                    backtest_debug["holding_minutes"] = horizon_minutes
                    backtest_debug["holding_minutes_source"] = horizon_source
                    state["backtest_debug"] = deepcopy(backtest_debug)  # type: ignore[typeddict-unknown-key]
                stats["_target_weights_df"] = output_weights_df
                backtest_result = self._stats_to_result(stats, experiment_spec)
                state["test_result"] = backtest_result
                if evaluation_stage == "development":
                    development_report = self._build_event_development_report(
                        stats=stats,
                        backtest_result=backtest_result,
                        experiment_spec=experiment_spec,
                        start=start,
                        end=end,
                    )
                    state["development_report"] = development_report
                    write_trace_json(
                        state,
                        agent_name=self.name,
                        stage="development_evaluation",
                        payload=development_report,
                    )
                print_agent_progress(
                    state,
                    agent_name=self.name,
                    message=(
                        "minute-event backtest completed: "
                        f"annual_return={backtest_result.annual_return:.1%}, "
                        f"sharpe={backtest_result.sharpe:.2f}, "
                        f"trades={backtest_result.trade_count or 0}, "
                        f"passed={backtest_result.passed}"
                    ),
                )
                artifact_paths = self._safe_export_backtest_artifacts(
                    state=state,
                    stats=stats,
                    backtest_result=backtest_result,
                    template_family=template_family,
                    experiment_spec=experiment_spec,
                    output_subdir=evaluation_stage,
                )
                write_trace_json(
                    state,
                    agent_name=self.name,
                    stage="backtest_result",
                    payload={
                        "template_family": template_family,
                        "summary": backtest_result.summary,
                        "annual_return": backtest_result.annual_return,
                        "sharpe": backtest_result.sharpe,
                        "max_drawdown": backtest_result.max_drawdown,
                        "max_ddpercent": backtest_result.max_ddpercent,
                        "trade_count": backtest_result.trade_count,
                        "event_gross_up_rate": backtest_result.event_gross_up_rate,
                        "event_joint_minute_hit_rate": (
                            backtest_result.event_joint_minute_hit_rate
                        ),
                        "event_success_rate": backtest_result.event_success_rate,
                        "event_success_column": backtest_result.event_success_column,
                        "event_success_metric": backtest_result.event_success_metric,
                        "event_success_metric_value": (
                            backtest_result.event_success_metric_value
                        ),
                        "passed": backtest_result.passed,
                        "event_horizon_minutes": horizon_minutes,
                        "event_horizon_source": horizon_source,
                        "artifacts": artifact_paths,
                        "backtest_debug": stats.get("_backtest_debug", {}),
                    },
                )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: StrategyTester ran local minute-event Parquet backtest"
                )
                state["phase"] = "test"
                return state

            if backtest_db_backend == "parquet":
                from quanta_agents.local_parquet_backtest import run_target_weight_backtest

                template_family = "local_parquet"
                state["selected_template"] = "LocalParquetTargetWeight"
                state["selected_base_class"] = "TargetWeightBacktest"
                default_buy_cost = self._resolve_float_config(experiment_spec, "commission_rate", 0.0003)
                buy_cost = self._resolve_float_config(experiment_spec, "buy_cost", default_buy_cost)
                sell_cost = self._resolve_float_config(
                    experiment_spec,
                    "sell_cost",
                    default_buy_cost + 0.0005,
                )
                (
                    sell_cost_before_change,
                    sell_cost_change_date,
                ) = self._resolve_sell_cost_change_config(experiment_spec)
                env_slippage = self._coerce_optional_float(
                    os.getenv("QUANTA_BACKTEST_SLIPPAGE")
                )
                default_slippage = env_slippage if env_slippage is not None else 0.0
                slippage = self._resolve_float_config(
                    experiment_spec,
                    "slippage",
                    default_slippage,
                )
                lot_size = int(self._resolve_float_config(experiment_spec, "lot_size", 0.0))
                capital = self._resolve_float_config(experiment_spec, "capital", 1_000_000.0)
                membership_df = evaluation_membership_df
                if membership_df is None:
                    validate_data_bundle = strategy_result_for_test.get("validate_data_bundle", {})
                    membership_df = combine_historical_membership_data(
                        validate_data_bundle,
                        strategy_result_for_test.get("required_data", []),
                    )

                print_agent_progress(state, agent_name=self.name, message="running local Parquet target-weight backtest")
                stats = run_target_weight_backtest(
                    output_weights_df,
                    start=start,
                    end=end,
                    capital=capital,
                    buy_cost=buy_cost,
                    sell_cost=sell_cost,
                    slippage=slippage,
                    lot_size=lot_size,
                    membership_df=membership_df,
                    sell_cost_before_change=sell_cost_before_change,
                    sell_cost_change_date=sell_cost_change_date,
                )
                backtest_debug = stats.get("_backtest_debug", {})
                if isinstance(backtest_debug, dict):
                    state["backtest_debug"] = deepcopy(backtest_debug)  # type: ignore[typeddict-unknown-key]
                stats["_target_weights_df"] = output_weights_df
                backtest_result = self._stats_to_result(stats, experiment_spec)
                state["test_result"] = backtest_result
                print_agent_progress(
                    state,
                    agent_name=self.name,
                    message=f"backtest completed: annual_return={backtest_result.annual_return:.1%}, sharpe={backtest_result.sharpe:.2f}, passed={backtest_result.passed}",
                )
                artifact_paths = self._safe_export_backtest_artifacts(
                    state=state,
                    stats=stats,
                    backtest_result=backtest_result,
                    template_family=template_family,
                    experiment_spec=experiment_spec,
                    output_subdir=evaluation_stage,
                )
                write_trace_json(
                    state,
                    agent_name=self.name,
                    stage="backtest_result",
                    payload={
                        "template_family": template_family,
                        "summary": backtest_result.summary,
                        "annual_return": backtest_result.annual_return,
                        "sharpe": backtest_result.sharpe,
                        "max_drawdown": backtest_result.max_drawdown,
                        "max_ddpercent": backtest_result.max_ddpercent,
                        "passed": backtest_result.passed,
                        "artifacts": artifact_paths,
                        "backtest_debug": stats.get("_backtest_debug", {}) if isinstance(stats, dict) else {},
                    },
                )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: StrategyTester ran local Parquet target-weight backtest"
                )
                state["phase"] = "test"
                return state

            code = self._build_fixed_strategy_code(template_family)
            state["code_text"] = code
            state["selected_template"] = "PortfolioStrategyTemplate"
            state["selected_base_class"] = expected_base_class
            expected_base_class = expected_base_class.strip()

            write_trace_json(
                state,
                agent_name=self.name,
                stage="recomputed_output_weights",
                payload=recompute_meta,
            )

            strategy_class = self._load_strategy_class_by_template(code, expected_base_class)
            vt_symbols = self._resolve_vt_symbols(experiment_spec)
            available_vt_symbols = self._load_available_bar_vt_symbols(
                backtest_db_backend=backtest_db_backend,
                backtest_dataset_specs=backtest_dataset_specs,
            )

            if available_vt_symbols:
                vt_symbols, universe_symbol_mapping = self._align_symbols_to_available(vt_symbols, available_vt_symbols)
                vt_symbols = self._intersect_vt_symbols(vt_symbols, available_vt_symbols)
                output_weights_df, tradable_columns, output_weights_symbol_alignment = self._align_output_weights_symbols_to_available(
                    output_weights_df,
                    tradable_columns,
                    available_vt_symbols,
                )
                write_trace_json(
                    state,
                    agent_name=self.name,
                    stage="symbol_alignment",
                    payload={
                        "universe_symbol_mapping": universe_symbol_mapping,
                        "output_weights_symbol_alignment": output_weights_symbol_alignment,
                    },
                )
            if not vt_symbols:
                raise RuntimeError("No overlap between selected universe and vnpy_bar_db available symbols")
            vt_symbol = vt_symbols[0]
            interval_name = self._resolve_interval_name(experiment_spec)
            strategy_settings: dict[str, object] = {
                "output_weights_df": output_weights_df,
                "tradable_columns": tradable_columns,
                "market_at_next_open": self._resolve_bool_config(experiment_spec, "market_at_next_open", True),
            }

            print_agent_progress(state, agent_name=self.name, message=f"running {template_family} backtest")
            importlib.import_module("vnpy_portfoliostrategy")
            stats = self._run_engine_backtest(
                engine_module="vnpy_portfoliostrategy.backtesting",
                strategy_class=strategy_class,
                vt_symbol=vt_symbol,
                vt_symbols=vt_symbols,
                interval_name=interval_name,
                start=start,
                end=end,
                experiment_spec=experiment_spec,
                strategy_settings=strategy_settings,
            )

            _backtest_result = self._stats_to_result(stats, experiment_spec)
            state["test_result"] = _backtest_result
            print_agent_progress(
                state,
                agent_name=self.name,
                message=f"backtest completed: annual_return={_backtest_result.annual_return:.1%}, sharpe={_backtest_result.sharpe:.2f}, passed={_backtest_result.passed}",
            )
            artifact_paths = self._safe_export_backtest_artifacts(
                state=state,
                stats=stats,
                backtest_result=_backtest_result,
                template_family=template_family,
                experiment_spec=experiment_spec,
                output_subdir=evaluation_stage,
            )
            write_trace_json(
                state,
                agent_name=self.name,
                stage="backtest_result",
                payload={
                    "template_family": template_family,
                    "summary": _backtest_result.summary,
                    "annual_return": _backtest_result.annual_return,
                    "sharpe": _backtest_result.sharpe,
                    "max_drawdown": _backtest_result.max_drawdown,
                    "max_ddpercent": _backtest_result.max_ddpercent,
                    "passed": _backtest_result.passed,
                    "artifacts": artifact_paths,
                    "strategy_debug": stats.get("_strategy_debug", {}) if isinstance(stats, dict) else {},
                    "engine_debug": stats.get("_engine_debug", {}) if isinstance(stats, dict) else {},
                },
            )
            state["history"].append(
                f"Epoch {state['epoch_index']}: StrategyTester ran vn.py {template_family} backtest engine"
            )
        except Exception as exc:
            print_agent_progress(state, agent_name=self.name, message=f"backtest failed: {exc}")
            write_trace_json(
                state,
                agent_name=self.name,
                stage="backtest_error",
                payload={
                    "template_family": template_family,
                    "expected_base_class": expected_base_class,
                    "error": str(exc),
                    "runtime_env": runtime_env,
                },
            )
            state["history"].append(
                f"Epoch {state['epoch_index']}: StrategyTester backtest failed ({exc})"
            )
            self._route_backtest_failure(state, str(exc))
            return state

        state["phase"] = "test"
        return state
