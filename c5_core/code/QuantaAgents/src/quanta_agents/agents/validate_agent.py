from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import io
import json
import re
import traceback
from datetime import datetime, timezone
from typing import Any

from quanta_agents.config import get_agent_max_retries, get_max_strategy_validate_rounds
from quanta_agents.llm import llm_client
from quanta_agents.period_data import combine_historical_membership_data
from quanta_agents.prompt_loader import load_agent_prompt
from quanta_agents.state import WorkflowState, derive_experiment_periods, get_hypothesis
from quanta_agents.strategy_code_policy import validate_generated_strategy_code
from quanta_agents.trace_logger import get_agent_trace_dir, print_agent_progress, write_trace_json, write_trace_text

_MAX_CODEACT_STEPS = 12
_MAX_OUTPUT_CHARS = 5000

_SCRIPT_SUMMARY_VAR_NAME = "validation_result"
_SCRIPT_REQUIRED_KEYS = (
    "intermediate_variable_checks",
)

_ANALYSIS_REQUIRED_KEYS = (
    "passed",
    "stats_summary",
    "output_weights_check",
    "intermediate_variable_checks",
    "hypothesis_consistency_check",
    "future_function_check",
    "suggestions",
)

_FORBIDDEN_VALIDATION_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "asyncio",
        "builtins",
        "concurrent",
        "ctypes",
        "ftplib",
        "http",
        "importlib",
        "multiprocessing",
        "paramiko",
        "requests",
        "runpy",
        "shutil",
        "smtplib",
        "socket",
        "sqlite3",
        "subprocess",
        "sys",
        "telnetlib",
        "tempfile",
        "urllib",
        "urllib3",
        "webbrowser",
        "zipfile",
    }
)

_FORBIDDEN_VALIDATION_CALL_NAMES = frozenset(
    {
        "Popen",
        "ExcelWriter",
        "HDFStore",
        "call",
        "check_call",
        "check_output",
        "chmod",
        "chown",
        "copy2",
        "copyfile",
        "copytree",
        "create_connection",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "exit",
        "fork",
        "forkpty",
        "input",
        "kill",
        "makedirs",
        "mkdir",
        "move",
        "open_connection",
        "popen",
        "putenv",
        "quit",
        "read_clipboard",
        "read_fwf",
        "read_hdf",
        "read_html",
        "read_sas",
        "read_stata",
        "read_table",
        "read_xml",
        "remove",
        "removedirs",
        "rename",
        "replace",
        "rmdir",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "startfile",
        "system",
        "to_csv",
        "to_clipboard",
        "to_excel",
        "to_feather",
        "to_hdf",
        "to_json",
        "to_orc",
        "to_parquet",
        "to_pickle",
        "to_sql",
        "to_stata",
        "to_xml",
        "touch",
        "unlink",
        "unsetenv",
        "urlopen",
        "urlretrieve",
        "write_bytes",
        "write_text",
    }
)

_FORBIDDEN_NUMPY_FILE_CALLS = frozenset(
    {
        "fromfile",
        "genfromtxt",
        "load",
        "loadtxt",
        "memmap",
        "save",
        "savetxt",
        "savez",
        "savez_compressed",
        "tofile",
    }
)


class OutputWeightsRuleChecker:
    """Deterministic checks for output_weights_df without LLM-generated code."""

    @staticmethod
    def _is_cn_stock_asset(asset: str) -> bool:
        text = asset.strip().lower()
        if not text:
            return False
        keywords = ("中国股票", "a股", "a-share", "ashare", "cn_stock", "china stock")
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _append_rule(results: list[dict[str, Any]], name: str, passed: bool, details: str) -> None:
        results.append({"rule": name, "passed": passed, "details": details})

    @staticmethod
    def _normalize_stock_code(value: object) -> str:
        text = str(value).strip().upper()
        if text.endswith(".SSE"):
            return text[:-4] + ".SH"
        if text.endswith(".SZSE"):
            return text[:-5] + ".SZ"
        if len(text) == 8 and text[:2] in {"SH", "SZ"} and text[2:].isdigit():
            return f"{text[2:]}.{text[:2]}"
        return text

    @staticmethod
    def _scan_sparse_weight_columns(
        output_weights_df: Any,
        weight_columns: list[str],
    ) -> dict[str, Any]:
        """逐列检查稀疏权重，避免把整张宽表展开到内存。"""

        import numpy as np
        import pandas as pd

        row_sums = np.zeros(len(output_weights_df), dtype=float)
        gross_abs_sums = np.zeros(len(output_weights_df), dtype=float)
        non_negative_by_column: dict[str, bool] = {}
        numeric = True
        finite = True

        for column in weight_columns:
            series = output_weights_df[column]
            if isinstance(series.dtype, pd.SparseDtype):
                sparse_array = series.array
                fill = pd.to_numeric(
                    pd.Series([sparse_array.fill_value]),
                    errors="coerce",
                ).iloc[0]
                stored = pd.to_numeric(
                    pd.Series(sparse_array.sp_values),
                    errors="coerce",
                )
                if pd.isna(fill) or bool(stored.isna().any()):
                    numeric = False
                    continue
                fill_value = float(fill)
                values = stored.to_numpy(dtype=float, copy=False)
                column_finite = bool(np.isfinite(fill_value)) and bool(
                    np.isfinite(values).all()
                )
                finite = finite and column_finite
                non_negative_by_column[column] = bool(
                    fill_value >= -1e-12 and (values >= -1e-12).all()
                )
                row_sums += fill_value
                gross_abs_sums += abs(fill_value)
                positions = sparse_array.sp_index.to_int_index().indices
                if len(positions):
                    row_sums[positions] += values - fill_value
                    gross_abs_sums[positions] += np.abs(values) - abs(fill_value)
                continue

            numeric_series = pd.to_numeric(series, errors="coerce")
            if bool(numeric_series.isna().any()):
                numeric = False
                continue
            values = numeric_series.to_numpy(dtype=float, copy=False)
            column_finite = bool(np.isfinite(values).all())
            finite = finite and column_finite
            non_negative_by_column[column] = bool((values >= -1e-12).all())
            row_sums += values
            gross_abs_sums += np.abs(values)

        return {
            "numeric": numeric,
            "finite": finite,
            "row_sums": row_sums,
            "gross_abs_sums": gross_abs_sums,
            "non_negative_by_column": non_negative_by_column,
        }

    @staticmethod
    def _check_historical_membership(
        output_weights_df: Any,
        datetime_column: str,
        tradable_columns: list[str],
        membership_df: Any,
        market_data_df: Any = None,
    ) -> tuple[bool, str]:
        try:
            import pandas as pd
        except Exception as exc:
            return False, f"pandas is unavailable: {exc}"

        required_columns = {"code", "start_date", "end_date"}
        if not isinstance(membership_df, pd.DataFrame) or membership_df.empty:
            return False, "historical index membership is missing or empty"
        if not required_columns.issubset(set(membership_df.columns)):
            return False, "historical index membership must contain code, start_date and end_date"
        if not datetime_column or datetime_column not in output_weights_df.columns:
            return False, "output weight date column is unavailable"

        membership = membership_df.loc[:, ["code", "start_date", "end_date"]].copy()
        membership["code"] = membership["code"].map(OutputWeightsRuleChecker._normalize_stock_code)
        membership["start_date"] = pd.to_datetime(membership["start_date"], errors="coerce").dt.normalize()
        membership["end_date"] = pd.to_datetime(membership["end_date"], errors="coerce").dt.normalize()
        membership = membership.dropna(subset=["code", "start_date", "end_date"])
        if membership.empty:
            return False, "historical index membership has no valid rows"

        intervals: dict[str, list[tuple[Any, Any]]] = {}
        for row in membership.itertuples(index=False):
            intervals.setdefault(str(row.code), []).append((row.start_date, row.end_date))

        dates = pd.to_datetime(output_weights_df[datetime_column], errors="coerce").dt.normalize()
        if bool(dates.isna().any()):
            return False, "output weight dates cannot be parsed"

        market_dates: pd.DatetimeIndex | None = None
        if (
            isinstance(market_data_df, pd.DataFrame)
            and "trade_date" in market_data_df.columns
            and not market_data_df.empty
        ):
            parsed_market_dates = pd.to_datetime(
                market_data_df["trade_date"], errors="coerce"
            ).dropna()
            if not parsed_market_dates.empty:
                market_dates = pd.DatetimeIndex(parsed_market_dates.dt.normalize().unique()).sort_values()

        weights = output_weights_df.loc[:, tradable_columns].apply(pd.to_numeric, errors="coerce")
        if bool(weights.isna().any(axis=None)):
            return False, "stock weights contain non-numeric values"

        violations: list[str] = []
        accepted_weights = pd.Series(0.0, index=weights.columns, dtype="float64")
        row_order = sorted(range(len(dates)), key=lambda index: (dates.iloc[index], index))
        for position in row_order:
            decision_date = dates.iloc[position]
            effective_date = pd.Timestamp(decision_date)
            if market_dates is not None:
                execution_position = int(
                    market_dates.searchsorted(effective_date, side="right")
                )
                if execution_position >= len(market_dates):
                    continue
                effective_date = pd.Timestamp(market_dates[execution_position])
            current_weights = weights.iloc[position]
            for column in weights.columns:
                requested_weight = float(current_weights.loc[column])
                previous_weight = float(accepted_weights.loc[column])
                if requested_weight <= previous_weight + 1e-12:
                    accepted_weights.loc[column] = requested_weight
                    continue
                code = OutputWeightsRuleChecker._normalize_stock_code(column)
                valid_intervals = intervals.get(code, [])
                valid_on_decision_date = any(
                    start <= decision_date <= end for start, end in valid_intervals
                )
                if valid_on_decision_date:
                    accepted_weights.loc[column] = requested_weight
                    continue
                violations.append(
                    f"decision={decision_date:%Y-%m-%d}:{column} "
                    f"increase {previous_weight:.10g}->{requested_weight:.10g}"
                )
                if len(violations) >= 8:
                    break
            if len(violations) >= 8:
                break

        if violations:
            return False, "non-member new/increased decision targets found: " + ", ".join(violations)
        return True, (
            "all new/increased targets belong to the index on decision dates; "
            "unchanged, reduced and closed existing targets are allowed after membership ends"
        )

    @staticmethod
    def run(
        output_weights_df: Any,
        strategy_output: dict[str, Any],
        universe: object,
        membership_df: Any = None,
        market_data_df: Any = None,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []

        output_meta = strategy_output.get("output_weights_df", {}) if isinstance(strategy_output, dict) else {}
        datetime_column = ""
        if isinstance(output_meta, dict):
            raw_datetime_column = output_meta.get("datetime_column")
            if isinstance(raw_datetime_column, str):
                datetime_column = raw_datetime_column.strip()

        if output_weights_df is None:
            OutputWeightsRuleChecker._append_rule(results, "exists", False, "output_weights_df is missing")
            return {
                "passed": False,
                "rules": results,
                "summary": "output_weights_df is missing",
            }

        if not hasattr(output_weights_df, "columns") or not hasattr(output_weights_df, "empty"):
            OutputWeightsRuleChecker._append_rule(
                results,
                "dataframe_type",
                False,
                f"output_weights_df must be a pandas DataFrame-like object, got {type(output_weights_df).__name__}",
            )
            return {
                "passed": False,
                "rules": results,
                "summary": "output_weights_df is not DataFrame-like",
            }

        OutputWeightsRuleChecker._append_rule(results, "non_empty", not bool(output_weights_df.empty), "DataFrame must not be empty")

        has_cash = "cash" in output_weights_df.columns
        OutputWeightsRuleChecker._append_rule(results, "cash_column", has_cash, "must include cash column")

        excluded_columns = {"cash"}
        if datetime_column:
            excluded_columns.add(datetime_column.strip().lower())
        tradable_columns = [
            str(col).strip()
            for col in output_weights_df.columns
            if str(col).strip() and str(col).strip().lower() not in excluded_columns
        ]
        OutputWeightsRuleChecker._append_rule(
            results,
            "has_tradable_instrument_columns",
            bool(tradable_columns),
            "output_weights_df must include at least one tradable instrument column besides cash/time",
        )

        numeric_df = None
        sparse_scan: dict[str, Any] | None = None
        weight_columns = (["cash"] if has_cash else []) + tradable_columns
        if weight_columns:
            try:
                import pandas as pd

                if any(
                    isinstance(output_weights_df[column].dtype, pd.SparseDtype)
                    for column in weight_columns
                ):
                    sparse_scan = OutputWeightsRuleChecker._scan_sparse_weight_columns(
                        output_weights_df,
                        weight_columns,
                    )
                else:
                    numeric_df = output_weights_df[weight_columns].apply(
                        pd.to_numeric,
                        errors="coerce",
                    )
            except Exception:
                numeric_df = None
                sparse_scan = None
        has_numeric = (
            bool(sparse_scan.get("numeric"))
            if isinstance(sparse_scan, dict)
            else (
                numeric_df is not None
                and hasattr(numeric_df, "shape")
                and int(numeric_df.shape[1]) == len(weight_columns)
                and bool(numeric_df.notna().all().all())
            )
        )
        OutputWeightsRuleChecker._append_rule(
            results,
            "numeric_columns",
            has_numeric,
            "all cash and instrument weights must be numeric",
        )

        if has_numeric:
            if isinstance(sparse_scan, dict):
                non_finite = bool(sparse_scan.get("finite"))
                row_sums = sparse_scan["row_sums"]
            else:
                non_finite = bool((~numeric_df.isin([float("inf"), float("-inf")])).all().all()) and bool((~numeric_df.isna()).all().all())
                row_sums = numeric_df.sum(axis=1)
            # The above condition checks there is no NaN/Inf in numeric columns.
            OutputWeightsRuleChecker._append_rule(results, "finite_numeric", non_finite, "numeric columns must not contain NaN/Inf")

            if isinstance(sparse_scan, dict):
                row_sum_ok = bool((abs(row_sums - 1.0) <= 1e-6).all())
            else:
                row_sum_ok = bool(((row_sums - 1.0).abs() <= 1e-6).all())
            OutputWeightsRuleChecker._append_rule(results, "row_sum_equals_one", row_sum_ok, "each row sum must be approximately 1")

            if not isinstance(universe, list) or not universe:
                OutputWeightsRuleChecker._append_rule(
                    results,
                    "universe_available_for_market_constraint",
                    False,
                    "universe must be a non-empty list to apply market-specific short-selling rules",
                )
            else:
                cn_symbols: set[str] = set()
                has_dataset_defined_cn_stocks = False
                parse_errors = 0
                for entry in universe:
                    if not isinstance(entry, dict):
                        parse_errors += 1
                        continue
                    asset = str(entry.get("asset", "")).strip()
                    if not OutputWeightsRuleChecker._is_cn_stock_asset(asset):
                        continue
                    if (
                        str(entry.get("type", "")).strip() == "dataset_defined"
                        and str(entry.get("dataset", "")).strip()
                    ):
                        has_dataset_defined_cn_stocks = True
                    symbols = entry.get("symbols", [])
                    if not isinstance(symbols, list):
                        parse_errors += 1
                        continue
                    for symbol in symbols:
                        if isinstance(symbol, str) and symbol.strip():
                            cn_symbols.add(symbol.strip())

                OutputWeightsRuleChecker._append_rule(
                    results,
                    "universe_parseable_for_market_constraint",
                    parse_errors == 0,
                    "universe entries must include asset and symbols list",
                )

                weight_cols = [str(col) for col in weight_columns if str(col) != "cash"]
                cn_weight_cols = (
                    weight_cols
                    if has_dataset_defined_cn_stocks
                    else [col for col in weight_cols if col in cn_symbols]
                )

                if cn_weight_cols:
                    # CN stock positions must be non-negative; other markets can keep long/short signs.
                    if isinstance(sparse_scan, dict):
                        non_negative_by_column = sparse_scan.get(
                            "non_negative_by_column",
                            {},
                        )
                        cn_non_negative = all(
                            non_negative_by_column.get(column) is True
                            for column in cn_weight_cols
                        )
                    else:
                        cn_non_negative = bool((numeric_df[cn_weight_cols] >= -1e-12).all().all())
                    OutputWeightsRuleChecker._append_rule(
                        results,
                        "cn_stock_non_negative_weights",
                        cn_non_negative,
                        "Chinese stock symbols must have non-negative weights (no short selling)",
                    )
                else:
                    OutputWeightsRuleChecker._append_rule(
                        results,
                        "cn_stock_non_negative_weights",
                        True,
                        "no Chinese stock symbols found in output weight columns",
                    )

        if not datetime_column:
            OutputWeightsRuleChecker._append_rule(
                results,
                "datetime_column_declared",
                False,
                "strategy_output.output_weights_df.datetime_column is missing",
            )
        else:
            has_dt_col = datetime_column in output_weights_df.columns
            OutputWeightsRuleChecker._append_rule(
                results,
                "datetime_column_exists",
                has_dt_col,
                f"datetime column '{datetime_column}' must exist",
            )
            if has_dt_col:
                try:
                    import pandas as pd

                    parsed = pd.to_datetime(output_weights_df[datetime_column], errors="coerce")
                    parseable = bool(parsed.notna().all())
                    OutputWeightsRuleChecker._append_rule(
                        results,
                        "datetime_parseable",
                        parseable,
                        f"datetime column '{datetime_column}' must be parseable",
                    )
                except Exception as exc:
                    OutputWeightsRuleChecker._append_rule(
                        results,
                        "datetime_parseable",
                        False,
                        f"failed to parse datetime column '{datetime_column}': {exc}",
                    )

        if membership_df is not None:
            membership_passed, membership_details = OutputWeightsRuleChecker._check_historical_membership(
                output_weights_df,
                datetime_column,
                tradable_columns,
                membership_df,
                market_data_df,
            )
            OutputWeightsRuleChecker._append_rule(
                results,
                "historical_index_membership",
                membership_passed,
                membership_details,
            )

        passed = all(bool(item.get("passed", False)) for item in results)
        failed_rules = [str(item.get("rule", "")) for item in results if not bool(item.get("passed", False))]
        summary = "all output_weights rules passed" if passed else ("failed rules: " + ", ".join(failed_rules))
        return {
            "passed": passed,
            "rules": results,
            "failed_rules": failed_rules,
            "summary": summary,
        }


class ValidateAgent:
    """Two-stage validate agent: script checks first, then structured analysis JSON."""

    name = "ValidateAgent"

    def __init__(self) -> None:
        self.max_retries = get_agent_max_retries(self.name)

    @staticmethod
    def _safe_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str, indent=2)

    @staticmethod
    def _preview_columns(dataframe: Any, limit: int = 16) -> list[str]:
        if not hasattr(dataframe, "columns"):
            return []
        try:
            cols = [str(col) for col in list(dataframe.columns)]
        except Exception:
            return []
        if len(cols) <= limit:
            return cols
        return cols[:limit] + [f"... (+{len(cols) - limit} more)"]

    @staticmethod
    def _extract_function_sources(strategy_code: str, function_names: list[str]) -> dict[str, str]:
        if not strategy_code.strip() or not function_names:
            return {}
        try:
            module = ast.parse(strategy_code)
        except SyntaxError:
            return {}

        function_nodes = {
            node.name: node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        wanted: set[str] = set()
        pending = [name for name in function_names if name in function_nodes]
        while pending:
            name = pending.pop()
            if name in wanted:
                continue
            wanted.add(name)
            node = function_nodes[name]
            for child in ast.walk(node):
                if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Name):
                    continue
                called_name = child.func.id
                if called_name in function_nodes and called_name not in wanted:
                    pending.append(called_name)

        lines = strategy_code.splitlines()
        results: dict[str, str] = {}
        for node in module.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in wanted:
                continue
            end_lineno = getattr(node, "end_lineno", None)
            if not isinstance(end_lineno, int):
                continue
            start = max(node.lineno - 1, 0)
            end = min(end_lineno, len(lines))
            snippet = "\n".join(lines[start:end]).strip()
            if snippet:
                results[node.name] = snippet
        return results

    @staticmethod
    def _summarize_data_shape(value: Any) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "type": type(value).__name__,
        }

        if value is None:
            summary["status"] = "missing"
            return summary

        shape = getattr(value, "shape", None)
        if isinstance(shape, tuple):
            summary["shape"] = [int(item) for item in shape]

        if hasattr(value, "columns"):
            summary["columns_preview"] = ValidateAgent._preview_columns(value)

        if hasattr(value, "empty"):
            try:
                summary["empty"] = bool(value.empty)
            except Exception:
                pass

        if "shape" not in summary and isinstance(value, (list, tuple, dict, set)):
            summary["length"] = len(value)

        return summary

    @staticmethod
    def _build_data_shapes(strategy_context: dict[str, Any], namespace: dict[str, Any]) -> dict[str, Any]:
        strategy_output = strategy_context.get("strategy_output", {})
        if not isinstance(strategy_output, dict):
            strategy_output = {}

        data_shapes: dict[str, Any] = {
            "output_weights_df": ValidateAgent._summarize_data_shape(namespace.get("output_weights_df")),
            "intermediate_variables": {},
        }

        for variable_name, meta in strategy_output.items():
            name_text = str(variable_name).strip()
            if not name_text or name_text == "output_weights_df":
                continue

            item: dict[str, Any] = {}
            function_name = ""
            if isinstance(meta, dict):
                raw_function_name = meta.get("function_name")
                if isinstance(raw_function_name, str):
                    function_name = raw_function_name.strip()
                if function_name:
                    item["function_name"] = function_name

            if function_name:
                try:
                    result = namespace["run_strategy_function"](function_name)
                    item["runtime"] = ValidateAgent._summarize_data_shape(result)
                except Exception as exc:
                    item["runtime_error"] = str(exc)
            else:
                item["runtime_error"] = "missing function_name in strategy_output metadata"

            data_shapes["intermediate_variables"][name_text] = item

        return data_shapes

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
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        fenced = re.search(r"```json\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
        if fenced:
            stripped = fenced.group(1).strip()

        for candidate in (stripped,):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        first = stripped.find("{")
        last = stripped.rfind("}")
        if first >= 0 and last > first:
            try:
                parsed = json.loads(stripped[first : last + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _missing_keys(payload: dict[str, Any], required_keys: tuple[str, ...]) -> list[str]:
        return [key for key in required_keys if key not in payload]

    @staticmethod
    def _strategy_code_sha256(state: WorkflowState) -> str:
        """记录本次实际验证的代码，供后续判断验证结果是否仍有效。"""
        strategy_code = str(state.get("strategy_code", "") or state.get("code_text", ""))
        return hashlib.sha256(strategy_code.encode("utf-8")).hexdigest()

    @staticmethod
    def _declared_intermediate_variables(strategy_output: object) -> dict[str, list[str]]:
        declared: dict[str, list[str]] = {}
        if not isinstance(strategy_output, dict):
            return declared

        for raw_name, raw_meta in strategy_output.items():
            name = str(raw_name).strip()
            if not name or name == "output_weights_df":
                continue
            expected: list[str] = []
            if isinstance(raw_meta, dict):
                raw_expected = raw_meta.get("expected_characteristics", [])
                if isinstance(raw_expected, list):
                    expected = [
                        str(item).strip()
                        for item in raw_expected
                        if isinstance(item, str) and item.strip()
                    ]
            declared[name] = expected
        return declared

    @staticmethod
    def _run_intermediate_variable_rules(
        strategy_output: object,
        validation_result: object,
    ) -> dict[str, Any]:
        """逐项确认中间变量的预期特征已计算且明确通过。"""
        declared = ValidateAgent._declared_intermediate_variables(strategy_output)
        raw_checks = (
            validation_result.get("intermediate_variable_checks")
            if isinstance(validation_result, dict)
            else None
        )
        variables: dict[str, Any] = {}
        failed_items: list[str] = []

        if not isinstance(raw_checks, dict):
            raw_checks = {}
            if declared:
                failed_items.append("intermediate_variable_checks:missing_or_invalid")

        for variable_name, expected_characteristics in declared.items():
            variable_result = raw_checks.get(variable_name)
            variable_failures: list[str] = []

            if not isinstance(variable_result, dict) or not variable_result:
                variable_failures.append("missing_or_empty_result")
            if not expected_characteristics:
                variable_failures.append("missing_expected_characteristics")

            for characteristic in expected_characteristics:
                characteristic_result = (
                    variable_result.get(characteristic)
                    if isinstance(variable_result, dict)
                    else None
                )
                if not isinstance(characteristic_result, dict):
                    variable_failures.append(f"{characteristic}:missing_or_invalid")
                    continue
                if "calculable" in characteristic_result:
                    calculable = characteristic_result.get("calculable")
                else:
                    calculable = characteristic_result.get("可计算")
                if "passed" in characteristic_result:
                    passed = characteristic_result.get("passed")
                else:
                    passed = characteristic_result.get("是否符合预期")
                if calculable is not True:
                    variable_failures.append(f"{characteristic}:not_calculable")
                if passed is not True:
                    variable_failures.append(f"{characteristic}:failed_or_unfinished")

            variable_passed = not variable_failures
            variables[variable_name] = {
                "passed": variable_passed,
                "failed_items": variable_failures,
            }
            failed_items.extend(
                f"{variable_name}.{item}" for item in variable_failures
            )

        return {
            "passed": not failed_items,
            "declared_variable_count": len(declared),
            "variables": variables,
            "failed_items": failed_items,
        }

    @staticmethod
    def _run_analysis_child_rules(
        strategy_output: object,
        validation_summary: object,
    ) -> dict[str, Any]:
        """独立核对分析结果的每个子项目，不采用顶层判断代替子项目。"""
        failed_items: list[str] = []
        if not isinstance(validation_summary, dict):
            return {
                "passed": False,
                "failed_items": ["validation_summary:missing_or_invalid"],
            }

        for field_name in (
            "output_weights_check",
            "hypothesis_consistency_check",
            "future_function_check",
        ):
            child = validation_summary.get(field_name)
            if not isinstance(child, dict) or child.get("passed") is not True:
                failed_items.append(f"{field_name}:failed_or_unfinished")

        declared = ValidateAgent._declared_intermediate_variables(strategy_output)
        intermediate_checks = validation_summary.get("intermediate_variable_checks")
        if not isinstance(intermediate_checks, dict):
            intermediate_checks = {}
            if declared:
                failed_items.append("intermediate_variable_checks:missing_or_invalid")

        for variable_name in declared:
            child = intermediate_checks.get(variable_name)
            if not isinstance(child, dict) or not child:
                failed_items.append(f"intermediate_variable_checks.{variable_name}:missing_or_empty")
            elif child.get("passed") is not True:
                failed_items.append(f"intermediate_variable_checks.{variable_name}:failed_or_unfinished")

        for raw_name, child in intermediate_checks.items():
            variable_name = str(raw_name).strip()
            failure_name = f"intermediate_variable_checks.{variable_name}:failed_or_unfinished"
            if (not isinstance(child, dict) or child.get("passed") is not True) and failure_name not in failed_items:
                failed_items.append(failure_name)

        return {
            "passed": not failed_items,
            "failed_items": failed_items,
        }

    @staticmethod
    def _enforce_validation_rules(
        strategy_output: object,
        validation_result: dict[str, Any],
        validation_summary: dict[str, Any],
    ) -> bool:
        """用程序结果决定最终是否通过，并把失败原因写回摘要。"""
        intermediate_rule_check = validation_result.get("intermediate_validation_check")
        if not isinstance(intermediate_rule_check, dict):
            intermediate_rule_check = ValidateAgent._run_intermediate_variable_rules(
                strategy_output,
                validation_result,
            )
            validation_result["intermediate_validation_check"] = intermediate_rule_check

        output_weights_check = validation_result.get("output_weights_check")
        output_weights_passed = bool(
            isinstance(output_weights_check, dict)
            and output_weights_check.get("passed") is True
        )
        strategy_code_policy_check = validation_result.get(
            "strategy_code_policy_check"
        )
        strategy_code_policy_passed = bool(
            not isinstance(strategy_code_policy_check, dict)
            or strategy_code_policy_check.get("passed") is True
        )
        intermediate_passed = intermediate_rule_check.get("passed") is True
        analysis_child_check = ValidateAgent._run_analysis_child_rules(
            strategy_output,
            validation_summary,
        )
        analysis_children_passed = analysis_child_check.get("passed") is True
        model_top_passed = validation_summary.get("passed") is True

        passed = (
            strategy_code_policy_passed
            and output_weights_passed
            and intermediate_passed
            and analysis_children_passed
            and model_top_passed
        )
        validation_summary["passed"] = passed
        validation_summary["program_validation_checks"] = {
            "strategy_code_policy": strategy_code_policy_check,
            "strategy_code_policy_passed": strategy_code_policy_passed,
            "output_weights_passed": output_weights_passed,
            "intermediate_variables": intermediate_rule_check,
            "analysis_children": analysis_child_check,
            "model_top_passed": model_top_passed,
        }

        suggestions = validation_summary.get("suggestions")
        if not isinstance(suggestions, list):
            suggestions = []
        additions: list[str] = []
        if not strategy_code_policy_passed:
            additions.append("事件策略代码固定检查未通过，不能继续验证。")
        if not output_weights_passed:
            additions.append("固定权重检查未通过，请先修正权重结果。")
        if not intermediate_passed:
            additions.append("存在未计算、未完成、缺失或失败的中间变量检查，请逐项补全并通过。")
        if not analysis_children_passed:
            additions.append("分析结果存在失败、缺失或未完成的子项目，不能判定整体验证通过。")
        for addition in additions:
            if addition not in suggestions:
                suggestions.append(addition)
        validation_summary["suggestions"] = suggestions
        return passed

    @staticmethod
    def _validate_model_check_code(code: str) -> None:
        """模型生成的验证代码只能计算内存中的资料，不能访问机器或外部服务。"""

        try:
            validate_generated_strategy_code(
                code,
                event_mode=True,
                forbid_result_fields=False,
            )
        except ValueError as exc:
            raise ValueError(f"验证代码安全检查失败：{exc}") from exc

        try:
            module = ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(f"验证代码语法错误：{exc}") from exc

        errors: list[str] = []
        numpy_aliases = {"np", "numpy"}

        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", maxsplit=1)[0]
                    if root in _FORBIDDEN_VALIDATION_IMPORT_ROOTS:
                        errors.append(f"禁止导入 {alias.name}")
                    if root == "numpy":
                        numpy_aliases.add(alias.asname or root)
            elif isinstance(node, ast.ImportFrom):
                root = str(node.module or "").split(".", maxsplit=1)[0]
                if root in _FORBIDDEN_VALIDATION_IMPORT_ROOTS:
                    errors.append(f"禁止从 {node.module} 导入")
            elif isinstance(node, ast.Name) and node.id in {
                "__builtins__",
                "__loader__",
                "__spec__",
            }:
                errors.append(f"禁止访问 {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in {
                "__builtins__",
                "__globals__",
                "__subclasses__",
                "f_builtins",
                "f_globals",
                "gi_frame",
            }:
                errors.append(f"禁止访问 {node.attr}")

        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue

            function_name = ""
            root_name = ""
            if isinstance(node.func, ast.Name):
                function_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
                owner: ast.AST = node.func.value
                while isinstance(owner, ast.Attribute):
                    owner = owner.value
                if isinstance(owner, ast.Name):
                    root_name = owner.id

            if function_name in _FORBIDDEN_VALIDATION_CALL_NAMES:
                errors.append(f"禁止调用 {function_name}")
            if root_name in numpy_aliases and function_name in _FORBIDDEN_NUMPY_FILE_CALLS:
                errors.append(f"禁止调用 {root_name}.{function_name}")
            if (
                function_name in {"getattr", "setattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value
                in (
                    _FORBIDDEN_VALIDATION_CALL_NAMES
                    | {
                        "__builtins__",
                        "__globals__",
                        "__subclasses__",
                        "environ",
                        "f_builtins",
                        "f_globals",
                        "gi_frame",
                    }
                )
            ):
                errors.append(f"禁止间接访问 {node.args[1].value}")

        if errors:
            unique_errors = list(dict.fromkeys(errors))
            raise ValueError("验证代码安全检查失败：" + "；".join(unique_errors[:8]))

    @staticmethod
    def _execute_model_check_code(
        code: str,
        namespace: dict[str, Any],
    ) -> tuple[str, bool]:
        try:
            ValidateAgent._validate_model_check_code(code)
        except ValueError as exc:
            return f"Error:\n{exc}", False
        return ValidateAgent._execute_code(code, namespace)

    @staticmethod
    def _execute_code(code: str, namespace: dict[str, Any]) -> tuple[str, bool]:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(code, "<validate_agent>", "exec"), namespace)  # noqa: S102
            out = stdout_buf.getvalue()
            err = stderr_buf.getvalue()
            combined = (f"stdout:\n{out}\n" if out else "") + (f"stderr:\n{err}\n" if err else "")
            return (combined.strip() or "(no output)"), True
        except BaseException:
            out = stdout_buf.getvalue()
            err = traceback.format_exc()
            combined = (f"stdout:\n{out}\n" if out else "") + f"Error:\n{err}"
            return combined.strip()[:_MAX_OUTPUT_CHARS], False

    @staticmethod
    def _detect_forbidden_redefinitions(code: str) -> str | None:
        try:
            module = ast.parse(code)
        except SyntaxError:
            return None

        forbidden_name = "run_strategy_function"
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == forbidden_name:
                return (
                    "禁止重定义 run_strategy_function：该函数由系统注入并指向真实策略函数调用入口。"
                    "请删除占位实现并直接调用注入的 run_strategy_function。"
                )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == forbidden_name:
                        return (
                            "禁止覆盖 run_strategy_function：该函数由系统注入并指向真实策略函数调用入口。"
                            "请删除覆盖代码并直接调用注入的 run_strategy_function。"
                        )
            if isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == forbidden_name:
                    return (
                        "禁止覆盖 run_strategy_function：该函数由系统注入并指向真实策略函数调用入口。"
                        "请删除覆盖代码并直接调用注入的 run_strategy_function。"
                    )
        return None

    @staticmethod
    def _error_source_context(code: str, output: str, radius: int = 3) -> str:
        lines = code.splitlines()
        line_matches = list(re.finditer(r'File "<validate_agent>", line (\d+)', output))
        if not line_matches:
            return ""
        line_no = int(line_matches[-1].group(1))
        start = max(1, line_no - radius)
        end = min(len(lines), line_no + radius)
        snippet: list[str] = []
        for idx in range(start, end + 1):
            marker = "=>" if idx == line_no else "  "
            snippet.append(f"{marker} {idx:04d}: {lines[idx - 1]}")
        return "\n".join(snippet)

    @staticmethod
    def _write_code_trace(state: WorkflowState, code: str, step: int, stage: str) -> None:
        step_dir = get_agent_trace_dir(state, agent_name="validateagent")
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        (step_dir / f"{stage}_step_{step:02d}_{ts}.py").write_text(code, encoding="utf-8")

    @staticmethod
    def _build_strategy_context(state: WorkflowState, strategy_result: dict[str, object]) -> dict[str, Any]:
        strategy_output = strategy_result.get("strategy_output")
        if not isinstance(strategy_output, dict):
            strategy_output = {}

        strategy_code = str(state.get("strategy_code", "") or state.get("code_text", "")).strip()
        function_names: list[str] = []
        for item in strategy_output.values():
            if not isinstance(item, dict):
                continue
            function_name = item.get("function_name")
            if isinstance(function_name, str) and function_name.strip():
                function_names.append(function_name.strip())

        function_sources = ValidateAgent._extract_function_sources(strategy_code, function_names)

        strategy_modification = ""
        parsed_meta = state.get("strategy_generation_meta")
        if isinstance(parsed_meta, dict):
            raw_modification = parsed_meta.get("strategy_modification", "")
            if isinstance(raw_modification, str):
                strategy_modification = raw_modification.strip()

        return {
            "hypothesis": get_hypothesis(state),
            "strategy_modification": strategy_modification,
            "strategy_output": strategy_output,
            "function_sources": function_sources,
            "strategy_code": strategy_code,
            "output_weights_df": strategy_result.get("output_weights"),
            "params": strategy_result.get("params", {}),
            "universe": strategy_result.get("universe", []),
            "train_data_bundle": strategy_result.get("train_data_bundle", {}),
            "validate_data_bundle": strategy_result.get("validate_data_bundle", {}),
            "required_data": strategy_result.get("required_data", []),
            "required_data_descriptions": strategy_result.get(
                "required_data_descriptions",
                "",
            ),
            "event_mode": (
                str(state.get("experiment_spec", {}).get("backtest_mode", "")).strip().lower()
                == "event_parquet"
                if isinstance(state.get("experiment_spec"), dict)
                else False
            ),
            "experiment_periods": derive_experiment_periods(state.get("experiment_spec", {})),
        }

    @staticmethod
    def _check_strategy_code_policy(strategy_context: dict[str, Any]) -> dict[str, object]:
        strategy_code = str(strategy_context.get("strategy_code", "")).strip()
        event_mode = bool(strategy_context.get("event_mode", False))
        if not strategy_code or not event_mode:
            return {"passed": True, "details": "事件策略代码检查不适用或没有发现问题。"}

        try:
            validate_generated_strategy_code(strategy_code, event_mode=True)
        except ValueError as exc:
            return {"passed": False, "details": str(exc)}
        return {"passed": True, "details": "事件策略代码检查通过。"}

    @staticmethod
    def _build_script_sandbox_namespace(strategy_context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        try:
            import numpy as np
        except Exception:
            np = None  # type: ignore[assignment]
        try:
            import pandas as pd
        except Exception:
            pd = None  # type: ignore[assignment]

        bootstrap_notes: list[str] = []
        namespace: dict[str, Any] = {
            "__name__": "__validate_agent__",
            "json": json,
            "inspect": inspect,
            "np": np,
            "pd": pd,
            "strategy_output": strategy_context.get("strategy_output", {}),
            "params": strategy_context.get("params", {}),
            "universe": strategy_context.get("universe", []),
            "output_weights_df": strategy_context.get("output_weights_df"),
            "train_data_bundle": strategy_context.get("train_data_bundle", {}),
            "validate_data_bundle": strategy_context.get("validate_data_bundle", {}),
            "required_data": strategy_context.get("required_data", []),
            "experiment_periods": strategy_context.get("experiment_periods", {}),
            "sandbox_bootstrap_notes": "",
        }

        strategy_code = str(strategy_context.get("strategy_code", "")).strip()
        strategy_code_policy_check = ValidateAgent._check_strategy_code_policy(
            strategy_context
        )
        namespace["strategy_code_policy_check"] = strategy_code_policy_check
        if strategy_code:
            if strategy_code_policy_check.get("passed") is not True:
                bootstrap_notes.append(
                    "strategy_code failed fixed policy checks: "
                    + str(strategy_code_policy_check.get("details", ""))
                )
            else:
                output, success = ValidateAgent._execute_code(strategy_code, namespace)
                if not success:
                    bootstrap_notes.append(f"failed to execute strategy_code in validate sandbox: {output[:1200]}")

        def run_strategy_function(function_name: str, params: dict[str, Any] | None = None) -> Any:
            fn = namespace.get(function_name)
            if not callable(fn):
                raise ValueError(f"function '{function_name}' is not callable in strategy namespace")
            call_params = params
            if call_params is None:
                candidate = namespace.get("params")
                call_params = candidate if isinstance(candidate, dict) else {}
            return fn(namespace.get("train_data_bundle", {}), namespace.get("validate_data_bundle", {}), call_params)

        namespace["run_strategy_function"] = run_strategy_function
        data_shapes = ValidateAgent._build_data_shapes(strategy_context, namespace)
        namespace["data_shapes"] = data_shapes
        namespace["sandbox_bootstrap_notes"] = "\n".join(bootstrap_notes)
        return namespace, bootstrap_notes

    def _run_script_validation_loop(
        self,
        state: WorkflowState,
        strategy_context: dict[str, Any],
        seed_namespace: dict[str, Any],
    ) -> dict[str, Any]:
        system_prompt = load_agent_prompt("validate_agent", "system_prompt")
        user_prompt = load_agent_prompt(
            "validate_agent",
            "user_prompt_template",
            hypothesis=str(strategy_context.get("hypothesis", "")),
            strategy_output_json=self._safe_json(strategy_context.get("strategy_output", {})),
            function_sources_json=self._safe_json(
                strategy_context.get("function_sources", {})
            ),
            required_data_descriptions=str(
                strategy_context.get("required_data_descriptions", "")
            ),
            data_shapes=self._safe_json(seed_namespace.get("data_shapes", {})),
        )

        base_messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        latest_feedback: str | None = None

        max_steps = min(self.max_retries, _MAX_CODEACT_STEPS)

        for step in range(1, max_steps + 1):
            print_agent_progress(state, agent_name=self.name, message=f"script-check step {step}/{max_steps}")
            messages = list(base_messages)
            if latest_feedback:
                messages.append({"role": "user", "content": latest_feedback})

            write_trace_text(
                state,
                agent_name=self.name,
                stage="script_llm_input",
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
                temperature=0.1,
                max_tokens=10000,
                role="validation",
                reasoning_effort="high",
                multi_agent=False,
            )
            write_trace_text(
                state,
                agent_name=self.name,
                stage="script_llm_output",
                text=response,
                attempt=step,
            )

            code = self._extract_code_block(response)
            if not code:
                latest_feedback = (
                    "请输出可独立执行的完整 Python 代码块。"
                    "最终必须在顶层定义 validation_result 字典。"
                )
                continue

            self._write_code_trace(state, code, step, stage="script")
            step_namespace = dict(seed_namespace)
            forbidden_reason = self._detect_forbidden_redefinitions(code)
            if forbidden_reason is not None:
                output, success = f"Error:\n{forbidden_reason}", False
            else:
                output, success = self._execute_model_check_code(
                    code,
                    step_namespace,
                )

            write_trace_json(
                state,
                agent_name=self.name,
                stage="script_code_execution",
                payload={
                    "step": step,
                    "success": success,
                    "output": output[:_MAX_OUTPUT_CHARS],
                },
                attempt=step,
            )

            summary = step_namespace.get(_SCRIPT_SUMMARY_VAR_NAME)
            if success and isinstance(summary, dict):
                missing = self._missing_keys(summary, _SCRIPT_REQUIRED_KEYS)
                if not missing:
                    return summary
                latest_feedback = (
                    "代码已运行，但 validation_result 缺少必填键: "
                    + ", ".join(missing)
                    + "。请输出完整修正版代码。"
                )
                continue

            error_context = self._error_source_context(code, output) if not success else ""
            feedback_lines = [
                "执行反馈:",
                output[:_MAX_OUTPUT_CHARS],
                "请继续输出修正版 Python 代码；必须在顶层定义 validation_result 字典，"
                "并至少包含 output_weights_check 与 intermediate_variable_checks。",
                "每一轮都必须输出可独立执行的完整代码块。",
            ]
            if error_context:
                feedback_lines.extend(["报错位置附近源码:", error_context])
            latest_feedback = "\n\n".join(feedback_lines)

        return {
            "intermediate_variable_checks": {},
        }

    @staticmethod
    def _run_output_weights_rules(strategy_result: dict[str, Any]) -> dict[str, Any]:
        output_weights_df = strategy_result.get("output_weights")
        strategy_output = strategy_result.get("strategy_output", {})
        universe = strategy_result.get("universe", [])
        validate_data_bundle = strategy_result.get("validate_data_bundle", {})
        membership_df = combine_historical_membership_data(
            validate_data_bundle,
            strategy_result.get("required_data", []),
        )
        market_data_df = (
            validate_data_bundle.get("stock_kline_daily_qfq")
            if isinstance(validate_data_bundle, dict)
            else None
        )
        if not isinstance(strategy_output, dict):
            strategy_output = {}
        return OutputWeightsRuleChecker.run(
            output_weights_df,
            strategy_output,
            universe,
            membership_df,
            market_data_df,
        )

    def _run_analysis_loop(
        self,
        state: WorkflowState,
        strategy_context: dict[str, Any],
        validation_result: dict[str, Any],
    ) -> dict[str, Any]:
        system_prompt = load_agent_prompt("analysis_agent", "system_prompt")
        user_prompt = load_agent_prompt(
            "analysis_agent",
            "user_prompt_template",
            hypothesis=str(strategy_context.get("hypothesis", "")),
            strategy_output_json=self._safe_json(strategy_context.get("strategy_output", {})),
            params_json=self._safe_json(strategy_context.get("params", {})),
            function_sources_json=self._safe_json(strategy_context.get("function_sources", {})),
            validation_result_json=self._safe_json(validation_result),
        )

        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        max_steps = min(self.max_retries, _MAX_CODEACT_STEPS)

        for step in range(1, max_steps + 1):
            print_agent_progress(state, agent_name=self.name, message=f"analysis-json step {step}/{max_steps}")
            write_trace_text(
                state,
                agent_name=self.name,
                stage="analysis_llm_input",
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
                temperature=0.1,
                max_tokens=3000,
                role="validation",
                reasoning_effort="high",
                multi_agent=False,
            )
            write_trace_text(
                state,
                agent_name=self.name,
                stage="analysis_llm_output",
                text=response,
                attempt=step,
            )

            payload = self._extract_json_object(response)
            if payload is None:
                messages.append(
                    {
                        "role": "user",
                        "content": "输出不是合法 JSON 对象。请仅输出一个 JSON 对象，不要包含 markdown 代码块或额外解释。",
                    }
                )
                continue

            missing = self._missing_keys(payload, _ANALYSIS_REQUIRED_KEYS)
            if missing:
                messages.append(
                    {
                        "role": "user",
                        "content": "JSON 缺少必填键: " + ", ".join(missing) + "。请返回完整 JSON 对象。",
                    }
                )
                continue

            if not isinstance(payload.get("passed"), bool):
                messages.append(
                    {
                        "role": "user",
                        "content": "字段 passed 必须是布尔值。请返回完整 JSON 对象。",
                    }
                )
                continue

            if not isinstance(payload.get("suggestions"), list):
                messages.append(
                    {
                        "role": "user",
                        "content": "字段 suggestions 必须是字符串列表。请返回完整 JSON 对象。",
                    }
                )
                continue

            return payload

        return {
            "passed": False,
            "stats_summary": "analysis loop exhausted without valid JSON payload",
            "output_weights_check": {"passed": False, "details": "missing analysis output"},
            "intermediate_variable_checks": {},
            "hypothesis_consistency_check": {"passed": False, "details": "missing analysis output"},
            "future_function_check": {"passed": False, "details": "missing analysis output"},
            "suggestions": ["Return one complete JSON object that matches the required schema."],
        }

    @staticmethod
    def _apply_validation_outcome(state: WorkflowState, *, passed: bool, validation_summary: dict[str, Any]) -> None:
        state["test_result"] = None
        if passed:
            state["phase"] = "test"
            state["validation_feedback"] = {}
            state["history"].append(
                f"Epoch {state['epoch_index']}: ValidateAgent passed - routing to StrategyTester"
            )
        else:
            current_round = max(int(state.get("strategy_validate_round", 1)), 1)
            max_rounds = get_max_strategy_validate_rounds()

            if current_round >= max_rounds:
                intermediate_checks = validation_summary.get("intermediate_variable_checks", {})
                if not isinstance(intermediate_checks, dict):
                    intermediate_checks = {}

                state["validation_feedback"] = {
                    "validation_summary": validation_summary,
                    "intermediate_variable_checks": intermediate_checks,
                    "suggestions": validation_summary.get("suggestions", []),
                    "future_function_check": validation_summary.get("future_function_check", {}),
                    "output_weights_check": validation_summary.get("output_weights_check", {}),
                }
                state["backtest_feedback"] = ""
                state["validation_result"] = {}
                state["phase"] = "done"
                state["manager_notes"] = (
                    "Fixed program checks still failed after the maximum code-writing retries. "
                    "The run stopped without consuming a research iteration."
                )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: fixed validation stopped after {max_rounds} code-writing rounds"
                )
                return

            state["strategy_validate_round"] = current_round + 1
            state["phase"] = "strategy"
            state["history"].append(
                f"Epoch {state['epoch_index']}: ValidateAgent failed - requesting strategy code refinement"
            )

    def run(self, state: WorkflowState) -> WorkflowState:
        print_agent_progress(state, agent_name=self.name, message="starting")

        # 每次进入验证都先丢弃旧结果，避免代码变化后沿用上一版结论。
        strategy_code_sha256 = self._strategy_code_sha256(state)
        state["validation_result"] = {}
        state["validation_summary"] = {}
        state["validation_feedback"] = {}

        strategy_result = state.get("strategy_result", {})
        if not isinstance(strategy_result, dict) or not strategy_result:
            state["validation_summary"] = {
                "passed": False,
                "strategy_code_sha256": strategy_code_sha256,
                "stats_summary": "strategy_result is empty; StrategyAgent must run first",
                "output_weights_check": {"passed": False, "details": "strategy_result missing"},
                "intermediate_variable_checks": {},
                "hypothesis_consistency_check": {"passed": False, "details": "strategy_result missing"},
                "future_function_check": {"passed": False, "details": "strategy_result missing"},
                "suggestions": ["Regenerate executable strategy code before validation."],
            }
            self._apply_validation_outcome(
                state,
                passed=False,
                validation_summary=state["validation_summary"],
            )
            return state

        output_weights_df = strategy_result.get("output_weights")
        if output_weights_df is None:
            state["validation_summary"] = {
                "passed": False,
                "strategy_code_sha256": strategy_code_sha256,
                "stats_summary": "strategy_result.output_weights is missing",
                "output_weights_check": {"passed": False, "details": "output_weights_df missing"},
                "intermediate_variable_checks": {},
                "hypothesis_consistency_check": {"passed": False, "details": "output_weights_df missing"},
                "future_function_check": {"passed": False, "details": "output_weights_df missing"},
                "suggestions": ["Ensure output_weights(...) is called and output_weights_df is assigned."],
            }
            self._apply_validation_outcome(
                state,
                passed=False,
                validation_summary=state["validation_summary"],
            )
            return state

        # 主流程只使用固定程序检查，不再调用大模型编写检查脚本或评价代码。
        strategy_context = self._build_strategy_context(state, strategy_result)
        strategy_code_policy_check = self._check_strategy_code_policy(strategy_context)
        output_weights_check = self._run_output_weights_rules(strategy_result)
        policy_passed = strategy_code_policy_check.get("passed") is True
        weights_passed = output_weights_check.get("passed") is True
        passed = policy_passed and weights_passed

        failed_items: list[str] = []
        if not policy_passed:
            failed_items.append("strategy_code_policy_check")
        if not weights_passed:
            failed_items.append("output_weights_check")

        validation_result = {
            "strategy_code_policy_check": strategy_code_policy_check,
            "output_weights_check": output_weights_check,
            "intermediate_variable_checks": {},
            "intermediate_validation_check": {
                "passed": True,
                "failed_items": [],
                "details": "主流程已取消大模型生成的中间变量检查脚本。",
            },
        }
        suggestions: list[str] = []
        if not policy_passed:
            suggestions.append(
                str(strategy_code_policy_check.get("details", "修正策略代码限制问题。"))
            )
        if not weights_passed:
            suggestions.append(
                str(output_weights_check.get("details", "修正目标比例输出。"))
            )

        validation_summary = {
            "passed": passed,
            "strategy_code_sha256": strategy_code_sha256,
            "stats_summary": (
                "固定程序检查通过。"
                if passed
                else "固定程序检查未通过：" + ", ".join(failed_items)
            ),
            "output_weights_check": output_weights_check,
            "intermediate_variable_checks": {},
            "hypothesis_consistency_check": {
                "passed": None,
                "details": "不再由大模型评价；代码撰写 Agent 必须严格执行已批准的策略说明。",
            },
            "future_function_check": {
                "passed": None,
                "details": "不再由大模型主观判断；日期规则由代码生成限制、固定输出检查和延迟测试共同检查。",
            },
            "program_validation_checks": {
                "strategy_code_policy": strategy_code_policy_check,
                "strategy_code_policy_passed": policy_passed,
                "output_weights_passed": weights_passed,
            },
            "suggestions": suggestions,
        }

        state["validation_result"] = validation_result
        state["validation_summary"] = validation_summary
        write_trace_json(
            state,
            agent_name=self.name,
            stage="structured_output",
            payload={
                "validation_result": validation_result,
                "validation_summary": validation_summary,
                "mode": "fixed_program_only",
            },
        )
        self._apply_validation_outcome(
            state,
            passed=passed,
            validation_summary=validation_summary,
        )
        print_agent_progress(
            state,
            agent_name=self.name,
            message=f"completed fixed checks (passed={passed})",
        )
        return state

        strategy_context = self._build_strategy_context(state, strategy_result)
        seed_namespace, bootstrap_notes = self._build_script_sandbox_namespace(strategy_context)
        if bootstrap_notes:
            write_trace_json(
                state,
                agent_name=self.name,
                stage="script_bootstrap_notes",
                payload={"notes": bootstrap_notes},
            )

        strategy_code_policy_check = seed_namespace.get(
            "strategy_code_policy_check",
            {"passed": True, "details": ""},
        )
        if (
            isinstance(strategy_code_policy_check, dict)
            and strategy_code_policy_check.get("passed") is not True
        ):
            details = str(strategy_code_policy_check.get("details", "")).strip()
            stop_details = details or "事件策略代码固定检查未通过。"
            validation_result = {
                "strategy_code_policy_check": strategy_code_policy_check,
                "output_weights_check": {
                    "passed": False,
                    "details": "事件策略代码固定检查未通过，权重检查未执行。",
                },
                "intermediate_variable_checks": {},
                "intermediate_validation_check": {
                    "passed": False,
                    "failed_items": ["strategy_code_policy_check:failed"],
                },
            }
            validation_summary = {
                "passed": False,
                "strategy_code_sha256": strategy_code_sha256,
                "stats_summary": stop_details,
                "output_weights_check": validation_result["output_weights_check"],
                "intermediate_variable_checks": {},
                "hypothesis_consistency_check": {
                    "passed": False,
                    "details": "事件策略代码固定检查失败后未继续验证。",
                },
                "future_function_check": {
                    "passed": False,
                    "details": stop_details,
                },
                "program_validation_checks": {
                    "strategy_code_policy": strategy_code_policy_check,
                    "strategy_code_policy_passed": False,
                },
                "suggestions": ["先修正事件策略代码中的禁止访问，再重新验证。"],
            }
            state["validation_result"] = validation_result
            state["validation_summary"] = validation_summary
            write_trace_json(
                state,
                agent_name=self.name,
                stage="structured_output",
                payload={
                    "validation_result": validation_result,
                    "validation_summary": validation_summary,
                },
            )
            self._apply_validation_outcome(
                state,
                passed=False,
                validation_summary=validation_summary,
            )
            print_agent_progress(
                state,
                agent_name=self.name,
                message="completed (passed=False; strategy code policy failed)",
            )
            return state

        validation_result = self._run_script_validation_loop(state, strategy_context, seed_namespace)
        validation_result["strategy_code_policy_check"] = strategy_code_policy_check
        validation_result["output_weights_check"] = self._run_output_weights_rules(strategy_result)
        validation_result["intermediate_validation_check"] = self._run_intermediate_variable_rules(
            strategy_context.get("strategy_output", {}),
            validation_result,
        )
        write_trace_json(
            state,
            agent_name=self.name,
            stage="output_weights_rule_check",
            payload={
                "output_weights_check": validation_result.get("output_weights_check", {}),
                "intermediate_validation_check": validation_result.get("intermediate_validation_check", {}),
            },
        )
        validation_summary = self._run_analysis_loop(state, strategy_context, validation_result)
        validation_summary["strategy_code_sha256"] = strategy_code_sha256
        passed = self._enforce_validation_rules(
            strategy_context.get("strategy_output", {}),
            validation_result,
            validation_summary,
        )

        state["validation_result"] = validation_result
        state["validation_summary"] = validation_summary

        write_trace_json(
            state,
            agent_name=self.name,
            stage="structured_output",
            payload={
                "validation_result": validation_result,
                "validation_summary": validation_summary,
            },
        )

        self._apply_validation_outcome(state, passed=passed, validation_summary=validation_summary)
        print_agent_progress(state, agent_name=self.name, message=f"completed (passed={passed})")
        return state
