from __future__ import annotations

import importlib
import glob
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    np = None

try:
    import pandas as pd  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    pd = None


load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INDEX_MEMBERSHIP_DIR = Path("D:/qlib_data/qlib_bin/instruments")
HISTORICAL_INDEX_UNIVERSE_ALIASES = {
    "hs300": "csi300",
    "csi300": "csi300",
    "csi500": "csi500",
    "csi800": "csi800",
    "csi1000": "csi1000",
    "csiall": "csiall",
}
HISTORICAL_INDEX_UNIVERSES = tuple(
    dict.fromkeys(HISTORICAL_INDEX_UNIVERSE_ALIASES.values())
)
DEFAULT_CSI300_MEMBERSHIP_PATH = DEFAULT_INDEX_MEMBERSHIP_DIR / "csi300.txt"


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DATASET_CATALOG_PATH = PROMPTS_DIR / "semantic" / "datasets.yaml"
UNIVERSE_CATALOG_PATH = PROMPTS_DIR / "semantic" / "universes.yaml"
BACKTEST_CATALOG_PATH = PROMPTS_DIR / "semantic" / "backtest.yaml"


def normalize_historical_index_universe(name: object) -> str | None:
    """把可用别名转成对应的 Qlib 历史指数文件名。"""

    normalized = str(name or "").strip().lower()
    return HISTORICAL_INDEX_UNIVERSE_ALIASES.get(normalized)


def historical_index_membership_table(name: object) -> str | None:
    """返回命名股票范围对应的历史成分数据表。"""

    canonical_name = normalize_historical_index_universe(name)
    if canonical_name is None:
        return None
    return f"{canonical_name}_membership"


def resolve_historical_index_membership_path(
    name: object,
    path: str | None = None,
) -> Path:
    """找到命名指数的历史成分文件，允许用环境变量改位置。"""

    canonical_name = normalize_historical_index_universe(name)
    if canonical_name is None:
        raise ValueError(f"不支持的历史指数股票范围: {name}")
    env_prefix = canonical_name.upper()
    raw_path = (
        path
        or os.getenv(f"QUANTA_{env_prefix}_MEMBERSHIP_FILE")
        or os.getenv(f"QUANTA_{env_prefix}_MEMBERSHIP_PATH")
    )
    default_path = DEFAULT_INDEX_MEMBERSHIP_DIR / f"{canonical_name}.txt"
    resolved = Path(raw_path).expanduser() if raw_path else default_path
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return resolved


def resolve_csi300_membership_path(path: str | None = None) -> Path:
    """兼容原有沪深300历史成分路径函数。"""

    return resolve_historical_index_membership_path("csi300", path)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    if isinstance(parsed, dict):
        return parsed
    return {}


class MetadataManager:
    """Manage semantic metadata loaded from YAML catalogs."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata
        self._build_indices()

    @classmethod
    def from_yaml_files(
        cls,
        dataset_path: Path = DATASET_CATALOG_PATH,
        universe_path: Path = UNIVERSE_CATALOG_PATH,
        backtest_path: Path = BACKTEST_CATALOG_PATH,
    ) -> "MetadataManager":
        datasets_raw = _load_yaml(dataset_path)
        universes_raw = _load_yaml(universe_path)
        backtest_raw = _load_yaml(backtest_path)

        metadata = {
            "datasets": datasets_raw.get("datasets", []) if isinstance(datasets_raw, dict) else [],
            "universes": universes_raw.get("universes", {}) if isinstance(universes_raw, dict) else {},
            "backtest_datasets": (
                backtest_raw.get("backtest_datasets", [])
                if isinstance(backtest_raw, dict)
                else []
            ),
        }
        return cls(metadata)

    def _build_indices(self) -> None:
        datasets_raw = self.metadata.get("datasets", [])
        universes_raw = self.metadata.get("universes", {})
        backtest_raw = self.metadata.get("backtest_datasets", [])

        self.datasets = {
            str(item.get("name") or item.get("table_key") or "").strip(): {
                **item,
                "table_key": str(item.get("table_key") or item.get("name") or "").strip(),
            }
            for item in datasets_raw
            if isinstance(item, dict)
            and isinstance(item.get("name") or item.get("table_key"), str)
            and str(item.get("name") or item.get("table_key")).strip()
        }
        self.universes = universes_raw if isinstance(universes_raw, dict) else {}
        self.backtest_datasets = {
            str(item.get("table_key") or item.get("key") or item.get("name") or "").strip(): item
            for item in backtest_raw
            if isinstance(item, dict)
            and isinstance(item.get("table_key") or item.get("key") or item.get("name"), str)
            and str(item.get("table_key") or item.get("key") or item.get("name")).strip()
        }

    def get_dataset(self, name: str) -> dict[str, Any] | None:
        normalized_name = name.strip()
        if not normalized_name:
            return None
        dataset = self.datasets.get(normalized_name)
        if dataset is not None:
            return dataset
        for item in self.datasets.values():
            if not isinstance(item, dict):
                continue
            table_key = item.get("table_key")
            if isinstance(table_key, str) and table_key.strip() == normalized_name:
                return item
        return None

    def get_universe(self, name: str) -> dict[str, Any] | None:
        normalized_name = name.strip().lower()
        if not normalized_name:
            return None

        for key, item in self.universes.items():
            if not isinstance(item, dict):
                continue

            normalized_key = str(key).strip().lower()
            if normalized_key == normalized_name:
                return item

        return None

    def list_datasets(self) -> list[str]:
        return sorted(self.datasets.keys())

    def list_universes(self) -> list[str]:
        return sorted(self.universes.keys())

    def list_historical_universes(self) -> list[str]:
        """只返回有逐日历史成分文件定义的命名股票范围。"""

        result: list[str] = []
        for key, item in self.universes.items():
            if not isinstance(item, dict):
                continue
            membership_name = item.get("historical_membership")
            if normalize_historical_index_universe(membership_name) is not None:
                result.append(str(key))
        return sorted(result)

    def get_backtest_dataset(self, key: str) -> dict[str, Any] | None:
        normalized_key = key.strip()
        if not normalized_key:
            return None
        return self.backtest_datasets.get(normalized_key)

    def list_backtest_datasets(self) -> list[str]:
        return sorted(self.backtest_datasets.keys())


class DataConnector:
    """Abstract data connector interface."""

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError

    def get_table_schema(self, table: str) -> dict[str, Any]:
        raise NotImplementedError


class DolphinDBConnector(DataConnector):
    """DolphinDB connector backed by the official Python SDK."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self.host = host or os.getenv("DOLPHINDB_HOST", "localhost").strip()
        raw_port = str(port) if port is not None else os.getenv("DOLPHINDB_PORT", "8848")
        self.port = int(raw_port)
        self.user = user or os.getenv("DOLPHINDB_USER", "admin").strip()
        self.password = password or os.getenv("DOLPHINDB_PASSWORD", "").strip()
        self._session: Any | None = None

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session

        try:
            dolphindb = importlib.import_module("dolphindb")
        except Exception as exc:  # pragma: no cover - depends on local SDK install
            raise RuntimeError(
                "Failed to import DolphinDB Python SDK. Install and verify the 'dolphindb' package."
            ) from exc

        try:
            session = dolphindb.session()
            session.connect(self.host, self.port, self.user, self.password)
            # Load ta module for technical indicators (RSI, MACD, Bollinger Bands)
            session.run("use ta")
        except Exception as exc:  # pragma: no cover - depends on local DB availability
            raise RuntimeError(
                f"Failed to connect to DolphinDB at {self.host}:{self.port} as {self.user}"
            ) from exc

        self._session = session
        return session

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            raise ValueError("DolphinDBConnector expects a fully rendered script without params")
        session = self._get_session()
        return session.run(query)

    def get_table_schema(self, table: str) -> dict[str, Any]:
        session = self._get_session()
        return {"table": table, "schema": session.run(f"schema({table})")}

    def __del__(self) -> None:
        session = getattr(self, "_session", None)
        if session is None:
            return
        close = getattr(session, "close", None)
        if callable(close):
            close()


class SQLiteConnector(DataConnector):
    """SQLite connector for local prototype and small datasets."""

    def __init__(self, db_path: str | None = None) -> None:
        env_db_path = os.getenv("SQLITE_MARKET_DATA_DB_PATH")
        raw_db_path = (db_path or env_db_path or "market_data.db").strip()
        resolved_db_path = Path(raw_db_path).expanduser()
        if not resolved_db_path.is_absolute():
            resolved_db_path = PROJECT_ROOT / resolved_db_path
        self.db_path = str(resolved_db_path)
        self.conn = sqlite3.connect(self.db_path)

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        if pd is None:
            raise RuntimeError("pandas is required to use SQLiteConnector")
        return pd.read_sql_query(query, self.conn, params=params)

    def get_table_schema(self, table: str) -> dict[str, Any]:
        if pd is None:
            raise RuntimeError("pandas is required to use SQLiteConnector")
        query = "PRAGMA table_info(%s)" % table
        schema_df = pd.read_sql_query(query, self.conn)
        return {"table": table, "columns": schema_df.to_dict(orient="records")}

    def __del__(self) -> None:
        if hasattr(self, "conn"):
            self.conn.close()


class ParquetConnector(DataConnector):
    """Use DuckDB to query the local front-adjusted daily Parquet files."""

    VIEW_NAME = "stock_kline_daily_qfq"
    CSI300_MEMBERSHIP_VIEW_NAME = "csi300_membership"

    def __init__(
        self,
        base_path: str | None = None,
        csi300_membership_path: str | None = None,
        historical_membership_paths: dict[str, str] | None = None,
    ) -> None:
        raw_path = (
            base_path
            or os.getenv("QUANTA_PARQUET_DATA_GLOB")
            or os.getenv("QUANTA_PARQUET_ROOT")
            or "./data/parquet"
        ).strip()
        expanded = Path(os.path.expandvars(raw_path)).expanduser()
        if not expanded.is_absolute():
            expanded = PROJECT_ROOT / expanded
        if expanded.is_dir():
            expanded = expanded / "*.parquet"

        self.base_path = str(expanded)
        if not glob.glob(self.base_path):
            raise FileNotFoundError(
                "No Parquet files matched QUANTA_PARQUET_DATA_GLOB: "
                f"{self.base_path}"
            )

        try:
            duckdb = importlib.import_module("duckdb")
        except Exception as exc:  # pragma: no cover - depends on local package
            raise RuntimeError("duckdb is required to read local Parquet data") from exc

        self.conn = duckdb.connect(":memory:")
        escaped_path = self.base_path.replace("\\", "/").replace("'", "''")
        self.conn.execute(
            f"""
            CREATE VIEW {self.VIEW_NAME} AS
            SELECT
                CASE
                    WHEN lower(code) LIKE 'sh%' THEN substr(lower(code), 3) || '.SH'
                    WHEN lower(code) LIKE 'sz%' THEN substr(lower(code), 3) || '.SZ'
                    ELSE upper(code)
                END AS code,
                date AS trade_date,
                open,
                high,
                low,
                close,
                volume,
                amount,
                float_shares,
                total_shares,
                raw_prev_close,
                is_st,
                is_delisting,
                float_market_cap,
                total_market_cap,
                qfq_ratio,
                vwap_qfq,
                prev_close,
                gu_1m,
                gd_1m,
                rbar_up17,
                rbar_down17,
                r_0931_1000,
                r_1001_1030,
                overnight_return
            FROM read_parquet('{escaped_path}', union_by_name=true)
            """
        )

        configured_membership_paths: dict[str, str] = {}
        for key, value in (historical_membership_paths or {}).items():
            canonical_name = normalize_historical_index_universe(key)
            if canonical_name is not None and str(value).strip():
                configured_membership_paths[canonical_name] = str(value)
        if csi300_membership_path:
            configured_membership_paths["csi300"] = csi300_membership_path

        self.historical_membership_paths: dict[str, str] = {}
        self.historical_membership_available: dict[str, bool] = {}
        for index_name in HISTORICAL_INDEX_UNIVERSES:
            membership_path = resolve_historical_index_membership_path(
                index_name,
                configured_membership_paths.get(index_name),
            )
            membership_table = historical_index_membership_table(index_name)
            assert membership_table is not None
            membership_available = membership_path.is_file()
            self.historical_membership_paths[index_name] = str(membership_path)
            self.historical_membership_available[index_name] = membership_available
            if membership_available:
                escaped_membership_path = (
                    str(membership_path).replace("\\", "/").replace("'", "''")
                )
                self.conn.execute(
                    f"""
                    CREATE VIEW {membership_table} AS
                    SELECT
                        CASE
                            WHEN upper(instrument) LIKE 'SH%' THEN substr(upper(instrument), 3) || '.SH'
                            WHEN upper(instrument) LIKE 'SZ%' THEN substr(upper(instrument), 3) || '.SZ'
                            ELSE upper(instrument)
                        END AS code,
                        CAST(start_date AS DATE) AS start_date,
                        CAST(end_date AS DATE) AS end_date
                    FROM read_csv(
                        '{escaped_membership_path}',
                        delim='\t',
                        header=false,
                        columns={{'instrument': 'VARCHAR', 'start_date': 'DATE', 'end_date': 'DATE'}}
                    )
                    WHERE regexp_matches(upper(instrument), '^(SH|SZ)[0-9]{{6}}$')
                    """
                )
            else:
                self.conn.execute(
                    f"""
                    CREATE VIEW {membership_table} AS
                    SELECT
                        CAST(NULL AS VARCHAR) AS code,
                        CAST(NULL AS DATE) AS start_date,
                        CAST(NULL AS DATE) AS end_date
                    WHERE FALSE
                    """
                )

        self.csi300_membership_path = self.historical_membership_paths["csi300"]
        self.csi300_membership_available = self.historical_membership_available["csi300"]

    def historical_membership_is_available(self, name: object) -> bool:
        canonical_name = normalize_historical_index_universe(name)
        if canonical_name is None:
            return False
        return bool(self.historical_membership_available.get(canonical_name, False))

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        if pd is None:
            raise RuntimeError("pandas is required to use ParquetConnector")
        duckdb_query = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"$\1", query)
        return self.conn.execute(duckdb_query, params or {}).fetchdf()

    def get_table_schema(self, table: str) -> dict[str, Any]:
        rows = self.conn.execute(f"DESCRIBE SELECT * FROM {table}").fetchall()
        columns = [{"name": str(row[0]), "type": str(row[1])} for row in rows]
        return {
            "table": table,
            "columns": columns,
            "base_path": self.base_path,
            "csi300_membership_path": self.csi300_membership_path,
            "historical_membership_paths": dict(self.historical_membership_paths),
        }

    def __del__(self) -> None:
        conn = getattr(self, "conn", None)
        if conn is not None:
            conn.close()


class ResearchParquetConnector(DataConnector):
    """读取分钟候选和 Level 2 候选等轻量研究文件。"""

    DATASET_ENV_VARS = {
        "periodic_minute_events": "QUANTA_MINUTE_EVENT_FEATURES_PATH",
        "periodic_level2_events": "QUANTA_LEVEL2_EVENT_FEATURES_PATH",
        "periodic_minute_candidates_2026": "QUANTA_MINUTE_CANDIDATES_PATH",
        "periodic_fourier_features_2026": "QUANTA_FOURIER_FEATURES_PATH",
    }
    OUTCOME_NAMES = {
        "volume_hit",
        "next_up",
        "joint_hit",
        "joint_minute_hit",
        "next_spike_position",
    }

    @classmethod
    def _is_hidden_outcome(cls, table_name: str, column_name: str) -> bool:
        normalized = column_name.strip().lower()
        if normalized.startswith("analysis_only_"):
            return True
        if table_name == "periodic_fourier_features_2026":
            return False
        return (
            normalized in cls.OUTCOME_NAMES
            or normalized.startswith("target_")
            or normalized.startswith("forward_")
            or normalized.startswith("return_5m")
            or normalized.startswith("return_10m")
            or normalized.startswith("return_20m")
        )

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def __init__(self, dataset_paths: dict[str, str] | None = None) -> None:
        configured = dict(dataset_paths or {})
        if dataset_paths is None:
            for table_name, env_name in self.DATASET_ENV_VARS.items():
                raw_path = os.getenv(env_name)
                if isinstance(raw_path, str) and raw_path.strip():
                    configured[table_name] = raw_path.strip()

        if not configured:
            expected = ", ".join(sorted(self.DATASET_ENV_VARS.values()))
            raise FileNotFoundError(f"没有配置研究数据文件，请设置以下任一变量：{expected}")

        try:
            duckdb = importlib.import_module("duckdb")
        except Exception as exc:  # pragma: no cover - 取决于本机是否安装
            raise RuntimeError("读取研究 Parquet 需要安装 duckdb") from exc

        self.conn = duckdb.connect(":memory:")
        self.dataset_paths: dict[str, str] = {}
        for table_name, raw_path in configured.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
                raise ValueError(f"研究数据表名不合法: {table_name}")

            expanded = Path(os.path.expandvars(str(raw_path))).expanduser()
            if not expanded.is_absolute():
                expanded = PROJECT_ROOT / expanded
            if expanded.is_dir():
                expanded = expanded / "*.parquet"

            resolved = str(expanded)
            if not glob.glob(resolved):
                raise FileNotFoundError(f"没有找到研究数据文件: {resolved}")

            escaped_path = resolved.replace("\\", "/").replace("'", "''")
            schema_rows = self.conn.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{escaped_path}', union_by_name=true)"
            ).fetchall()
            visible_columns = [
                str(row[0])
                for row in schema_rows
                if not self._is_hidden_outcome(table_name, str(row[0]))
            ]
            if not visible_columns:
                raise ValueError(f"研究数据没有可供策略使用的字段: {table_name}")
            select_clause = ", ".join(
                self._quote_identifier(column) for column in visible_columns
            )
            self.conn.execute(
                f"""
                CREATE VIEW {table_name} AS
                SELECT {select_clause}
                FROM read_parquet('{escaped_path}', union_by_name=true)
                """
            )
            self.dataset_paths[table_name] = resolved

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        if pd is None:
            raise RuntimeError("读取研究 Parquet 需要安装 pandas")
        duckdb_query = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"$\1", query)
        return self.conn.execute(duckdb_query, params or {}).fetchdf()

    def get_table_schema(self, table: str) -> dict[str, Any]:
        if table not in self.dataset_paths:
            raise ValueError(f"研究数据表未配置: {table}")
        rows = self.conn.execute(f"DESCRIBE SELECT * FROM {table}").fetchall()
        columns = [{"name": str(row[0]), "type": str(row[1])} for row in rows]
        return {
            "table": table,
            "columns": columns,
            "base_path": self.dataset_paths[table],
        }

    def __del__(self) -> None:
        conn = getattr(self, "conn", None)
        if conn is not None:
            conn.close()
