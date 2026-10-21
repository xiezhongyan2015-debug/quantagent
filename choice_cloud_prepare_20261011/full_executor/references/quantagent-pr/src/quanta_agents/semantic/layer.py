from __future__ import annotations

from functools import lru_cache
import os
import re
from typing import Any, Callable

try:
    import pandas as pd  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    pd = None

from quanta_agents.semantic.metadata import (
    DataConnector,
    DolphinDBConnector,
    historical_index_membership_table,
    MetadataManager,
    normalize_historical_index_universe,
    ParquetConnector,
    ResearchParquetConnector,
    SQLiteConnector,
)
from quanta_agents.semantic.types import DataRequirement


def _resolve_default_data_engine() -> str:
    value = os.getenv("QUANTA_DATA_ENGINE", "dolphindb").strip().lower()
    return value or "dolphindb"


def _resolve_market_sqlite_path() -> str:
    value = os.getenv("SQLITE_MARKET_DATA_DB_PATH")
    if isinstance(value, str) and value.strip():
        return value.strip()

    return "market_data.db"


class SemanticLayer:
    """Core semantic layer service.

    - Unified metadata/data access
    - Connector abstraction for engine differences
    - Vt symbol normalization
    - Data availability checks
    - Factor hypothesis testing utilities
    """

    def __init__(
        self,
        metadata_manager: MetadataManager,
        default_engine: str | None = None,
    ) -> None:
        self.metadata = metadata_manager
        self.default_engine = (default_engine or _resolve_default_data_engine()).strip().lower()
        self._connectors: dict[str, DataConnector] = {}
        self._signal_query_cache: dict[str, Any] = {}

    @classmethod
    def default(cls) -> "SemanticLayer":
        return cls._default_for_engine(_resolve_default_data_engine())

    @classmethod
    @lru_cache(maxsize=4)
    def _default_for_engine(cls, default_engine: str) -> "SemanticLayer":
        return cls(metadata_manager=MetadataManager.from_yaml_files(), default_engine=default_engine)

    def _get_connector(self, dataset_name: str) -> DataConnector:
        dataset = self.metadata.get_dataset(dataset_name)
        if dataset is None:
            raise ValueError(f"Dataset '{dataset_name}' not found")

        engine = self.default_engine

        connector = self._connectors.get(engine)
        if connector is not None:
            return connector

        if engine == "dolphindb":
            connector = DolphinDBConnector()
        elif engine == "sqlite":
            connector = SQLiteConnector(db_path=_resolve_market_sqlite_path())
        elif engine == "parquet":
            connector = ParquetConnector()
        elif engine == "research_parquet":
            connector = ResearchParquetConnector()
        else:
            raise ValueError(f"Unsupported engine: {engine}")

        self._connectors[engine] = connector
        return connector

    def get_universe_list(self, name: str) -> list[str]:
        normalized_name = name.strip().lower()
        historical_index = normalize_historical_index_universe(normalized_name)
        if self.default_engine == "parquet" and historical_index is not None:
            membership_table = historical_index_membership_table(historical_index)
            assert membership_table is not None
            connector_dataset = (
                membership_table
                if self.metadata.get_dataset(membership_table) is not None
                else "stock_kline_daily_qfq"
            )
            connector = self._get_connector(connector_dataset)
            if not isinstance(connector, ParquetConnector):
                raise ValueError("历史指数股票范围只能由本地 Parquet 数据读取")
            if not connector.historical_membership_is_available(historical_index):
                membership_path = connector.historical_membership_paths.get(
                    historical_index,
                    "",
                )
                raise FileNotFoundError(
                    f"找不到 {normalized_name} 的历史成分文件: {membership_path}"
                )
            members = connector.execute_query(
                f"SELECT DISTINCT code FROM {membership_table} ORDER BY code"
            )
            if pd is None or not isinstance(members, pd.DataFrame) or members.empty:
                raise ValueError(f"{membership_table} 没有有效股票代码")
            return [str(code) for code in members["code"].tolist()]

        universe = self.metadata.get_universe(name)
        symbols = universe.get("symbols", []) if isinstance(universe, dict) else []
        if not isinstance(symbols, list):
            return []
        return [str(symbol) for symbol in symbols]

    def resolve_universe_symbols(self, universe: object) -> list[str]:
        """Resolve universe input to concrete symbols.

        Resolution priority:
        1) If universe is a catalog name, read symbols from universes.yaml metadata.
        2) If universe is a list, normalize list items as explicit symbols.
        3) If universe is a comma-separated string, treat it as explicit symbols.
        """

        def _dedupe(symbols: list[str]) -> list[str]:
            resolved: list[str] = []
            seen: set[str] = set()
            for symbol in symbols:
                normalized = str(symbol).strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                resolved.append(normalized)
            return resolved

        def _resolve_explicit_symbols(value: object) -> list[str]:
            if isinstance(value, dict):
                universe_type = value.get("type")
                universe_value = value.get("value")
                if universe_type == "named_pool" and isinstance(universe_value, str):
                    return self.get_universe_list(universe_value.strip())
                if universe_type == "symbol_list" and isinstance(universe_value, list):
                    return [str(item).strip() for item in universe_value if str(item).strip()]
                if "symbols" in value:
                    return _resolve_explicit_symbols(value.get("symbols"))
                return []

            if isinstance(value, str):
                name = value.strip()
                if not name:
                    return []

                catalog_symbols = self.get_universe_list(name)
                if catalog_symbols:
                    return catalog_symbols

                if "," in name:
                    return [item.strip() for item in name.split(",") if item.strip()]

                return []

            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]

            return []

        if isinstance(universe, dict):
            return _resolve_explicit_symbols(universe)

        if isinstance(universe, str):
            return _resolve_explicit_symbols(universe)

        if isinstance(universe, list):
            resolved: list[str] = []
            for item in universe:
                if isinstance(item, dict):
                    resolved.extend(_resolve_explicit_symbols(item.get("symbols")))
                else:
                    resolved.extend(_resolve_explicit_symbols(item))
            return _dedupe(resolved)

        return []

    @staticmethod
    def _resolve_dataset_datetime_column_with_default(dataset: dict[str, Any], default: str = "trade_date") -> str:
        for key in ("datetime_column", "date_column", "time_column"):
            value = dataset.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return default

    @staticmethod
    def _resolve_dataset_symbol_column_with_default(dataset: dict[str, Any], default: str = "code") -> str:
        value = dataset.get("symbol_column")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    @staticmethod
    def _quote_dolphindb_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @classmethod
    def _to_dolphindb_literal(cls, value: str) -> str:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return f"temporalParse({cls._quote_dolphindb_string(value)}, \"yyyy-MM-dd\")"
        return cls._quote_dolphindb_string(value)

    @classmethod
    def _to_dolphindb_symbol_list(cls, symbols: list[str]) -> str:
        return "[" + ", ".join(cls._quote_dolphindb_string(symbol) for symbol in symbols) + "]"

    @classmethod
    def _build_dolphindb_table_ref(cls, dataset_name: str) -> str:
        database_name = os.getenv("DOLPHINDB_MARKET_DATA_DB", "dfs://market_data").strip()
        if not database_name.startswith("dfs://"):
            database_name = f"dfs://{database_name}"
        return (
            f"loadTable({cls._quote_dolphindb_string(database_name)}, "
            f"{cls._quote_dolphindb_string(dataset_name)})"
        )

    @classmethod
    def _build_dolphindb_where_clauses(
        cls,
        date_col: str,
        symbol_col: str,
        start_date: str | None,
        end_date: str | None,
        symbols: list[str] | None,
    ) -> list[str]:
        clauses: list[str] = []
        if start_date is not None:
            clauses.append(f"{date_col} >= {cls._to_dolphindb_literal(start_date)}")
        if end_date is not None:
            clauses.append(f"{date_col} <= {cls._to_dolphindb_literal(end_date)}")
        if symbols:
            clauses.append(f"{symbol_col} in {cls._to_dolphindb_symbol_list(symbols)}")
        return clauses

    @classmethod
    def _build_dolphindb_select_script(
        cls,
        dataset_name: str,
        date_col: str,
        symbol_col: str,
        select_clause: str,
        start_date: str | None = None,
        end_date: str | None = None,
        symbols: list[str] | None = None,
    ) -> str:
        table_ref = cls._build_dolphindb_table_ref(dataset_name)
        query = f"select {select_clause} from {table_ref}"
        clauses = cls._build_dolphindb_where_clauses(
            date_col=date_col,
            symbol_col=symbol_col,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
        )
        if clauses:
            query += " where " + " and ".join(clauses)
        return query

    @classmethod
    def _resolve_dataset_datetime_column(cls, dataset: dict[str, Any]) -> str | None:
        for key in ("datetime_column", "date_column", "time_column"):
            value = dataset.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return None

    @classmethod
    def _resolve_dataset_symbol_column(cls, dataset: dict[str, Any]) -> str | None:
        value = dataset.get("symbol_column")
        if isinstance(value, str) and value.strip():
            return value.strip()

        return None

    @staticmethod
    def _resolve_dataset_primary_keys(
        dataset: dict[str, Any],
        fallback: list[str],
    ) -> list[str]:
        raw_keys = dataset.get("primary_keys")
        if isinstance(raw_keys, list):
            keys = [
                str(item).strip()
                for item in raw_keys
                if isinstance(item, str) and str(item).strip()
            ]
            if keys:
                return list(dict.fromkeys(keys))
        return [key for key in fallback if key]

    @staticmethod
    def _dedupe_by_primary_keys(dataframe: Any, key_columns: list[str]) -> Any:
        if pd is None or not isinstance(dataframe, pd.DataFrame):
            return dataframe
        active_keys = [column for column in key_columns if column in dataframe.columns]
        if not active_keys:
            return dataframe
        return dataframe.drop_duplicates(subset=active_keys, keep="last")

    @staticmethod
    def _is_static_dataset(dataset: dict[str, Any]) -> bool:
        dataset_type = dataset.get("type") if isinstance(dataset, dict) else None
        if isinstance(dataset_type, str) and dataset_type.strip().lower() == "static":
            return True
        return False

    @staticmethod
    def _normalize_required_time_range(value: object, *, prefix: str) -> tuple[str, str]:
        if not isinstance(value, dict):
            raise ValueError(f"{prefix}.time_range must be an object")

        start = value.get("start")
        end = value.get("end")
        if not isinstance(start, str) or not start.strip():
            raise ValueError(f"{prefix}.time_range.start must be a non-empty string")
        if not isinstance(end, str) or not end.strip():
            raise ValueError(f"{prefix}.time_range.end must be a non-empty string")
        return start.strip(), end.strip()

    @staticmethod
    def _normalize_required_universe(value: object, *, prefix: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError(f"{prefix}.universe must be an object")

        universe_type = value.get("type")
        universe_value = value.get("value")
        if universe_type not in {"named_pool", "symbol_list", "dataset_defined"}:
            raise ValueError(
                f"{prefix}.universe.type must be named_pool, symbol_list, or dataset_defined"
            )

        if universe_type == "named_pool":
            if not isinstance(universe_value, str) or not universe_value.strip():
                raise ValueError(f"{prefix}.universe.value must be a non-empty string for named_pool")
            normalized_value: object = universe_value.strip()
        elif universe_type == "symbol_list":
            if not isinstance(universe_value, list) or not universe_value:
                raise ValueError(f"{prefix}.universe.value must be a non-empty list for symbol_list")
            normalized_value = [
                str(item).strip()
                for item in universe_value
                if isinstance(item, str) and str(item).strip()
            ]
            if len(normalized_value) != len(universe_value):
                raise ValueError(f"{prefix}.universe.value must be a list of non-empty strings")
        else:
            if not isinstance(universe_value, str) or not universe_value.strip():
                raise ValueError(
                    f"{prefix}.universe.value must name a dataset for dataset_defined"
                )
            normalized_value = universe_value.strip()

        return {"type": universe_type, "value": normalized_value}

    @classmethod
    def _normalize_required_data_item(cls, item: dict[str, Any], *, index: int) -> dict[str, Any]:
        table_key = item.get("table_key") or item.get("name")
        if not isinstance(table_key, str) or not table_key.strip():
            raise ValueError(f"required_data[{index}] missing table_key")

        dataset_type = item.get("type")
        if not isinstance(dataset_type, str) or not dataset_type.strip():
            raise ValueError(f"required_data[{index}].type must be a non-empty string")
        normalized_type = dataset_type.strip()
        if normalized_type not in {"panel", "time_series", "auxiliary", "static"}:
            raise ValueError(f"required_data[{index}].type must be one of panel/time_series/auxiliary/static")

        purpose = item.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError(f"required_data[{index}] missing purpose")

        fields_raw = item.get("fields")
        if fields_raw is None:
            fields: list[str] = []
        elif isinstance(fields_raw, list):
            fields = [str(field).strip() for field in fields_raw if isinstance(field, str) and field.strip()]
            if len(fields) != len(fields_raw):
                raise ValueError(f"required_data[{index}].fields must be a list of non-empty strings")
        else:
            raise ValueError(f"required_data[{index}].fields must be a list")

        universe = item.get("universe")
        time_range = item.get("time_range")

        if normalized_type == "time_series":
            if not fields:
                raise ValueError(f"required_data[{index}].fields is required for time_series")
            normalized_universe = cls._normalize_required_universe(universe, prefix=f"required_data[{index}]")
            start_date, end_date = cls._normalize_required_time_range(time_range, prefix=f"required_data[{index}]")
            return {
                "table_key": table_key.strip(),
                "type": normalized_type,
                "fields": fields,
                "universe": normalized_universe,
                "time_range": {"start": start_date, "end": end_date},
                "purpose": purpose.strip(),
            }

        if normalized_type == "auxiliary":
            if not fields:
                raise ValueError(f"required_data[{index}].fields is required for auxiliary")
            if universe is not None:
                raise ValueError(f"required_data[{index}].universe must not be provided for auxiliary")
            start_date, end_date = cls._normalize_required_time_range(time_range, prefix=f"required_data[{index}]")
            return {
                "table_key": table_key.strip(),
                "type": normalized_type,
                "fields": fields,
                "universe": None,
                "time_range": {"start": start_date, "end": end_date},
                "purpose": purpose.strip(),
            }

        if normalized_type == "panel":
            normalized_universe = cls._normalize_required_universe(universe, prefix=f"required_data[{index}]")
            start_date, end_date = cls._normalize_required_time_range(time_range, prefix=f"required_data[{index}]")
            return {
                "table_key": table_key.strip(),
                "type": normalized_type,
                "fields": fields,
                "universe": normalized_universe,
                "time_range": {"start": start_date, "end": end_date},
                "purpose": purpose.strip(),
            }

        if universe is not None:
            raise ValueError(f"required_data[{index}].universe must not be provided for static")
        if time_range is not None:
            raise ValueError(f"required_data[{index}].time_range must not be provided for static")
        if not fields:
            raise ValueError(f"required_data[{index}].fields is required for static")
        return {
            "table_key": table_key.strip(),
            "type": normalized_type,
            "fields": fields,
            "universe": None,
            "time_range": None,
            "purpose": purpose.strip(),
        }

    def _execute_table_query(
        self,
        dataset_name: str,
        select_clause: str = "*",
        start_date: str | None = None,
        end_date: str | None = None,
        symbols: list[str] | None = None,
    ) -> Any:
        dataset = self.metadata.get_dataset(dataset_name)
        if dataset is None:
            raise ValueError(f"Dataset '{dataset_name}' not found")

        table = dataset_name
        date_col = self._resolve_dataset_datetime_column(dataset)
        symbol_col = self._resolve_dataset_symbol_column(dataset)

        connector = self._get_connector(dataset_name)
        if isinstance(connector, ResearchParquetConnector):
            select_clause = self._validate_research_select_clause(
                connector,
                dataset_name,
                select_clause,
            )
        if isinstance(connector, DolphinDBConnector):
            if (start_date is not None or end_date is not None) and not date_col:
                raise ValueError(f"{dataset_name}: metadata missing datetime_column/date_column/time_column")
            if symbols and not symbol_col:
                raise ValueError(f"{dataset_name}: metadata missing symbol_column")
            query = self._build_dolphindb_select_script(
                dataset_name=dataset_name,
                date_col=date_col or self._resolve_dataset_datetime_column_with_default(dataset),
                symbol_col=symbol_col or self._resolve_dataset_symbol_column_with_default(dataset),
                select_clause=select_clause,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
            result = connector.execute_query(query)
            if self._is_static_dataset(dataset):
                return result
            primary_keys = self._resolve_dataset_primary_keys(
                dataset,
                [key for key in [symbol_col, date_col] if key],
            )
            return self._dedupe_by_primary_keys(result, primary_keys)

        query = f"SELECT {select_clause} FROM {table}"
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if start_date is not None and end_date is not None:
            if not date_col:
                raise ValueError(f"{dataset_name}: metadata missing datetime_column/date_column/time_column")
            clauses.append(f"{date_col} BETWEEN :start AND :end")
            params["start"] = start_date
            params["end"] = end_date
        elif start_date is not None:
            if not date_col:
                raise ValueError(f"{dataset_name}: metadata missing datetime_column/date_column/time_column")
            clauses.append(f"{date_col} >= :start")
            params["start"] = start_date
        elif end_date is not None:
            if not date_col:
                raise ValueError(f"{dataset_name}: metadata missing datetime_column/date_column/time_column")
            clauses.append(f"{date_col} <= :end")
            params["end"] = end_date

        if symbols:
            if not symbol_col:
                raise ValueError(f"{dataset_name}: metadata missing symbol_column")
            in_list = ", ".join([f":sym_{idx}" for idx, _ in enumerate(symbols)])
            clauses.append(f"{symbol_col} IN ({in_list})")
            for idx, symbol in enumerate(symbols):
                params[f"sym_{idx}"] = symbol

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        result = connector.execute_query(query, params)
        if self._is_static_dataset(dataset):
            return result
        primary_keys = self._resolve_dataset_primary_keys(
            dataset,
            [key for key in [symbol_col, date_col] if key],
        )
        return self._dedupe_by_primary_keys(result, primary_keys)

    @staticmethod
    def _validate_research_select_clause(
        connector: ResearchParquetConnector,
        dataset_name: str,
        select_clause: str,
    ) -> str:
        """研究文件只允许选择视图中真实存在的字段。"""

        normalized = select_clause.strip()
        if normalized == "*":
            return normalized

        requested = [field.strip() for field in normalized.split(",")]
        schema = connector.get_table_schema(dataset_name)
        allowed = {
            str(item.get("name", ""))
            for item in schema.get("columns", [])
            if isinstance(item, dict) and str(item.get("name", ""))
        }
        invalid = [field for field in requested if not field or field not in allowed]
        if invalid:
            raise ValueError(
                f"{dataset_name}: fields 只能填写可用数据列名，发现不可用字段"
            )
        return ", ".join(connector._quote_identifier(field) for field in requested)

    def load_time_series_data(
        self,
        dataset_name: str,
        fields: list[str],
        time_range: dict[str, object],
        universe: object,
    ) -> Any:
        start_date, end_date = self._normalize_required_time_range(time_range, prefix=dataset_name)
        normalized_universe = self._normalize_required_universe(universe, prefix=dataset_name)
        universe_type = normalized_universe.get("type")
        symbols = [] if universe_type == "dataset_defined" else self.resolve_universe_symbols(normalized_universe)
        universe_value = normalized_universe.get("value")
        historical_index = (
            normalize_historical_index_universe(universe_value)
            if (
                normalized_universe.get("type") == "named_pool"
                and isinstance(universe_value, str)
            )
            else None
        )
        connector = self._get_connector(dataset_name)
        membership_table: str | None = None
        if historical_index is not None and isinstance(connector, ParquetConnector):
            membership_table = historical_index_membership_table(historical_index)
            assert membership_table is not None
            if not connector.historical_membership_is_available(historical_index):
                membership_path = connector.historical_membership_paths.get(
                    historical_index,
                    "",
                )
                raise FileNotFoundError(
                    f"找不到 {universe_value} 的历史成分文件: {membership_path}"
                )
            symbols = []
        if universe_type != "dataset_defined" and membership_table is None and not symbols:
            raise ValueError(f"{dataset_name}: resolved universe is empty")
        normalized_fields = [str(field).strip() for field in fields if isinstance(field, str) and field.strip()]
        if not normalized_fields:
            raise ValueError(f"{dataset_name}: fields must be a non-empty list")

        fetch_kwargs: dict[str, Any] = {
            "dataset": dataset_name,
            "fields": normalized_fields,
            "start_date": start_date,
            "end_date": end_date,
            "symbols": symbols or None,
            "vt_symbol_format": False,
        }
        if membership_table is not None:
            fetch_kwargs["membership_table"] = membership_table
        return self.fetch_data(
            **fetch_kwargs,
        )

    def load_auxiliary_data(
        self,
        dataset_name: str,
        fields: list[str],
        time_range: dict[str, object],
    ) -> Any:
        start_date, end_date = self._normalize_required_time_range(time_range, prefix=dataset_name)
        normalized_fields = [str(field).strip() for field in fields if isinstance(field, str) and field.strip()]
        if not normalized_fields:
            raise ValueError(f"{dataset_name}: fields must be a non-empty list")
        return self.fetch_data(
            dataset=dataset_name,
            fields=normalized_fields,
            start_date=start_date,
            end_date=end_date,
            symbols=None,
            vt_symbol_format=False,
        )

    def load_panel_data(
        self,
        dataset_name: str,
        time_range: dict[str, object],
        universe: object,
        fields: list[str] | None = None,
    ) -> Any:
        start_date, end_date = self._normalize_required_time_range(time_range, prefix=dataset_name)
        normalized_universe = self._normalize_required_universe(universe, prefix=dataset_name)
        symbols = self.resolve_universe_symbols(normalized_universe)
        if not symbols:
            raise ValueError(f"{dataset_name}: resolved universe is empty")
        normalized_fields = [str(field).strip() for field in (fields or []) if isinstance(field, str) and field.strip()]
        if normalized_fields:
            select_clause = ", ".join(normalized_fields)
        else:
            select_clause = "*"
        data = self._execute_table_query(
            dataset_name=dataset_name,
            select_clause=select_clause,
            start_date=start_date,
            end_date=end_date,
        )
        if pd is None or not isinstance(data, pd.DataFrame):
            return data

        dataset = self.metadata.get_dataset(dataset_name)
        if dataset is None:
            return data
        date_col = self._resolve_dataset_datetime_column(dataset)

        selected_columns: list[str] = []
        if isinstance(date_col, str) and date_col in data.columns:
            selected_columns.append(date_col)
        for symbol in symbols:
            if symbol in data.columns and symbol not in selected_columns:
                selected_columns.append(symbol)

        if len(selected_columns) <= (1 if selected_columns else 0):
            raise ValueError(f"{dataset_name}: none of the resolved universe symbols are present in panel columns")

        return data.loc[:, selected_columns].copy()

    def load_static_data(
        self,
        dataset_name: str,
        fields: list[str] | None = None,
    ) -> Any:
        normalized_fields = [str(field).strip() for field in (fields or []) if isinstance(field, str) and field.strip()]
        select_clause = ", ".join(normalized_fields) if normalized_fields else "*"
        return self._execute_table_query(dataset_name=dataset_name, select_clause=select_clause)

    def load_required_table(self, required_data_item: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_required_data_item(required_data_item, index=0)
        dataset_name = normalized["table_key"]
        table_type = normalized["type"]

        if table_type == "time_series":
            data = self.load_time_series_data(
                dataset_name=dataset_name,
                fields=normalized["fields"],
                time_range=normalized["time_range"],
                universe=normalized["universe"],
            )
        elif table_type == "auxiliary":
            data = self.load_auxiliary_data(
                dataset_name=dataset_name,
                fields=normalized["fields"],
                time_range=normalized["time_range"],
            )
        elif table_type == "panel":
            data = self.load_panel_data(
                dataset_name=dataset_name,
                time_range=normalized["time_range"],
                universe=normalized["universe"],
                fields=normalized["fields"],
            )
        else:
            data = self.load_static_data(
                dataset_name=dataset_name,
                fields=normalized["fields"],
            )

        return {**normalized, "data": data}

    def load_required_data(self, required_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(required_data, list) or not required_data:
            raise ValueError("required_data must be a non-empty list")
        loaded: list[dict[str, Any]] = []
        for index, item in enumerate(required_data):
            if not isinstance(item, dict):
                raise ValueError(f"required_data[{index}] must be an object")
            normalized = self._normalize_required_data_item(item, index=index)
            loaded.append(self.load_required_table(normalized))
        return loaded

    def fetch_data(
        self,
        dataset: str,
        fields: list[str],
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
        vt_symbol_format: bool = True,
        membership_table: str | None = None,
    ) -> Any:
        """Fetch data via unified interface using metadata-defined column names."""
        if pd is None:
            raise RuntimeError("pandas is required for fetch_data")

        ds_info = self.metadata.get_dataset(dataset)
        if ds_info is None:
            raise ValueError(f"Unknown dataset: {dataset}")

        table = dataset
        date_col = self._resolve_dataset_datetime_column(ds_info)
        symbol_col = self._resolve_dataset_symbol_column(ds_info)

        if (start_date or end_date) and not date_col:
            raise ValueError(f"{dataset}: metadata missing datetime_column/date_column/time_column")
        if symbols and not symbol_col:
            raise ValueError(f"{dataset}: metadata missing symbol_column")

        primary_keys = self._resolve_dataset_primary_keys(
            ds_info,
            [key for key in [symbol_col, date_col] if key],
        )
        select_fields = [
            field
            for field in [date_col, symbol_col, *primary_keys, *list(fields)]
            if isinstance(field, str) and field
        ]
        select_fields = list(dict.fromkeys(select_fields))

        connector = self._get_connector(dataset)
        select_clause = ", ".join(select_fields)
        if isinstance(connector, ResearchParquetConnector):
            select_clause = self._validate_research_select_clause(
                connector,
                dataset,
                select_clause,
            )
        if isinstance(connector, DolphinDBConnector):
            query = self._build_dolphindb_select_script(
                dataset_name=dataset,
                date_col=date_col or self._resolve_dataset_datetime_column_with_default(ds_info),
                symbol_col=symbol_col or self._resolve_dataset_symbol_column_with_default(ds_info),
                select_clause=select_clause,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
            df = connector.execute_query(query)
        else:
            table_reference = f"{table} AS data" if membership_table else table
            query = (
                f"SELECT {select_clause} FROM {table_reference} "
                f"WHERE {date_col} BETWEEN :start AND :end"
            )
            params: dict[str, Any] = {"start": start_date, "end": end_date}

            if membership_table:
                expected_tables = {
                    historical_index_membership_table(name)
                    for name in ("csi300", "csi500", "csi800", "csi1000", "csiall")
                }
                if membership_table not in expected_tables:
                    raise ValueError(f"不支持的历史成分数据表: {membership_table}")
                query += (
                    f" AND EXISTS (SELECT 1 FROM {membership_table} AS membership "
                    f"WHERE membership.code = data.{symbol_col} "
                    f"AND membership.start_date <= data.{date_col} "
                    f"AND data.{date_col} <= membership.end_date)"
                )

            if symbols:
                in_list = ", ".join([f":sym_{idx}" for idx, _ in enumerate(symbols)])
                query += f" AND {symbol_col} IN ({in_list})"
                for idx, symbol in enumerate(symbols):
                    params[f"sym_{idx}"] = symbol

            df = connector.execute_query(query, params)
        if not isinstance(df, pd.DataFrame):
            raise RuntimeError("Connector must return pandas DataFrame")

        # Keep metadata-defined column names untouched; dedupe by available key columns.
        if self._is_static_dataset(ds_info):
            deduped = df
        else:
            deduped = self._dedupe_by_primary_keys(df, primary_keys)

        # vt_symbol_format is intentionally ignored here because symbol column names are preserved.
        _ = vt_symbol_format
        return deduped

    def _to_vt_symbol(self, raw_series: Any) -> Any:
        if pd is None:
            raise RuntimeError("pandas is required for vt_symbol conversion")

        def convert(code: Any) -> str:
            code_str = str(code)
            if code_str.endswith(".SSE") or code_str.endswith(".SZSE"):
                return code_str
            if code_str.endswith(".SH"):
                return code_str[:-3] + ".SSE"
            if code_str.endswith(".SZ"):
                return code_str[:-3] + ".SZSE"
            if code_str.startswith(("60", "68")):
                return f"{code_str}.SSE"
            if code_str.startswith(("00", "30")):
                return f"{code_str}.SZSE"
            return code_str

        return raw_series.apply(convert)

    def check_data_availability(self, requirements: DataRequirement) -> dict[str, Any]:
        dataset = self.metadata.get_dataset(requirements.dataset_name)
        if dataset is None:
            blocking_issues = [
                {
                    "type": "missing_dataset",
                    "description": f"Dataset '{requirements.dataset_name}' not found.",
                    "suggestions": [
                        f"Use one of available datasets: {', '.join(self.metadata.list_datasets())}",
                    ],
                }
            ]
            return {
                "available": False,
                "dataset_exists": False,
                "missing_fields": requirements.required_fields,
                "missing_factors": [],
                "time_range_covered": False,
                "granularity_matched": False,
                "universe_exists": bool(self.get_universe_list(requirements.universe_name)),
                "messages": [f"dataset '{requirements.dataset_name}' not found"],
                "blocking_issues": blocking_issues,
                "warnings": [],
            }

        dataset_fields = {
            str(item.get("name"))
            for item in dataset.get("fields", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        missing_fields = [f for f in requirements.required_fields if f not in dataset_fields]

        missing_factors: list[str] = []

        available_granularity = str(dataset.get("granularity", ""))
        granularity_matched = available_granularity == requirements.granularity

        dataset_time = dataset.get("time_range")
        if not isinstance(dataset_time, dict):
            dataset_time = {}
        ds_start = str(dataset_time.get("start_date", ""))
        ds_end = str(dataset_time.get("end_date", ""))

        time_range_covered = True
        if requirements.start_date:
            time_range_covered = time_range_covered and ds_start <= requirements.start_date
        if requirements.end_date:
            time_range_covered = time_range_covered and ds_end >= requirements.end_date

        universe_exists = bool(self.get_universe_list(requirements.universe_name))

        messages: list[str] = []
        if missing_fields:
            messages.append(f"missing fields: {', '.join(missing_fields)}")
        if not granularity_matched:
            messages.append("granularity mismatch")
        if not time_range_covered:
            messages.append("time range not fully covered")
        if not universe_exists:
            messages.append(f"universe '{requirements.universe_name}' not found")

        blocking_issues: list[dict[str, Any]] = []
        for field in missing_fields:
            blocking_issues.append(
                {
                    "type": "missing_field",
                    "description": f"Field '{field}' is missing in dataset '{requirements.dataset_name}'.",
                    "suggestions": [
                        f"Use existing fields: {sorted(dataset_fields)}",
                    ],
                }
            )

        if not granularity_matched:
            blocking_issues.append(
                {
                    "type": "insufficient_granularity",
                    "description": (
                        f"Requested granularity '{requirements.granularity}' is unavailable. "
                        f"Only '{available_granularity}' is available."
                    ),
                    "suggestions": [
                        f"Downgrade to '{available_granularity}'",
                    ],
                }
            )

        if not universe_exists:
            blocking_issues.append(
                {
                    "type": "missing_universe",
                    "description": f"Universe '{requirements.universe_name}' not found.",
                    "suggestions": [
                        f"Use one of available universes: {self.metadata.list_universes()}",
                    ],
                }
            )

        if not time_range_covered:
            blocking_issues.append(
                {
                    "type": "insufficient_time_range",
                    "description": (
                        f"Requested time range is not fully covered by dataset range {ds_start}~{ds_end}."
                    ),
                    "suggestions": [
                        f"Clip to available range {ds_start}~{ds_end}",
                    ],
                }
            )

        available = len(blocking_issues) == 0

        return {
            "available": available,
            "dataset_exists": True,
            "missing_fields": missing_fields,
            "missing_factors": missing_factors,
            "time_range_covered": time_range_covered,
            "granularity_matched": granularity_matched,
            "available_granularity": available_granularity,
            "dataset_time_range": {"start_date": ds_start, "end_date": ds_end},
            "universe_exists": universe_exists,
            "messages": messages,
            "blocking_issues": blocking_issues,
            "warnings": [],
        }

def _get_default_layer() -> SemanticLayer:
    return SemanticLayer.default()


def get_universe_list(name: str) -> list[str]:
    return _get_default_layer().get_universe_list(name)


def resolve_universe_symbols(universe: object) -> list[str]:
    return _get_default_layer().resolve_universe_symbols(universe)


def load_required_data(required_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _get_default_layer().load_required_data(required_data)
