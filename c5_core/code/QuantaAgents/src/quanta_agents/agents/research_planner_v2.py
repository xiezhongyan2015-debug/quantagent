from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from jinja2 import Template

from quanta_agents.llm import llm_client
from quanta_agents.prompt_loader import load_agent_prompt_dict
from quanta_agents.calculation_contract import required_date_offsets
from quanta_agents.semantic.metadata import (
    MetadataManager,
    normalize_historical_index_universe,
)
from quanta_agents.web_research import WebResearchClient


class ResearchPlannerV2:
    """先建立竞争解释，再从开发期证据中挑选一个可执行问题。"""

    name = "ResearchPlannerV2"
    _prompt_id = "research_planner_v2"
    _dataset_path = (
        Path(__file__).resolve().parents[1] / "prompts" / "semantic" / "datasets.yaml"
    )
    _categories = {
        "numeric_definition",
        "mechanism_signal",
        "applicability",
        "condition_necessity",
        "trade_timing",
    }
    _parameter_value_token = "__SELECTED_VALUE__"
    _numeric_literal_pattern = re.compile(
        r"(?<![A-Za-z0-9_.])"
        r"(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
        r"\s*(?P<percent>[%％])?"
        r"(?![A-Za-z0-9_.])"
    )
    _natural_numeric_values = (-1.0, 0.0, 1.0)
    _development_summary_keys = (
        "report_kind",
        "period_kind",
        "seen_period",
        "event_horizon_minutes",
        "event_horizon_source",
        "event_return_columns",
        "event_success_metric",
        "event_success_metric_value",
        "development_period",
        "train_period",
        "fold_count",
        "passed_fold_count",
        "fold_pass_ratio",
        "median_annual_return",
        "cash_comparison_annual_return",
        "median_excess_return_over_cash",
        "beats_cash_in_all_folds",
        "median_sharpe",
        "worst_fold_sharpe",
        "worst_fold_drawdown",
        "total_trade_count",
        "cost_stress_median_sharpe",
        "cost_stress_complete",
        "cost_stress_passed",
        "transaction_cost_rule",
        "double_cost_rule",
        "delay_stress_median_sharpe",
        "delay_stress_complete",
        "delay_stress_passed",
        "trial_count",
        "deflated_sharpe_probability",
        "deflated_sharpe_check_active",
        "passed",
        "failure_reasons",
        "candidate_id",
        "epoch_index",
        "gap_trading_days",
        "date_source",
    )
    _development_fold_keys = (
        "name",
        "period_kind",
        "seen_period",
        "event_horizon_minutes",
        "event_horizon_source",
        "event_return_columns",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "annual_return",
        "sharpe",
        "max_drawdown",
        "max_ddpercent",
        "trade_count",
        "win_rate",
        "profit_loss_ratio",
        "sample_size",
        "skew",
        "kurtosis",
        "event_gross_mean_return",
        "event_gross_up_rate",
        "event_joint_minute_hit_rate",
        "event_net_mean_return",
        "event_net_median_return",
        "event_net_win_rate",
        "event_success_column",
        "event_success_rate",
        "event_success_metric",
        "event_success_metric_value",
        "passed",
        "summary",
        "error",
    )
    _development_fold_groups = (
        "folds",
        "cost_stress_folds",
        "delay_stress_folds",
    )
    _report_field_names = {
        "development_report",
        "research_report",
        "selected_research_report",
        "selected_development_report",
        "confirmation_report",
        "report",
    }
    _large_backtest_fields = {
        "recompute_records",
        "output_weights",
        "output_weights_df",
        "target_weights",
        "weights",
        "equity",
        "equity_curve",
        "trades",
        "trade_records",
        "daily_results",
        "train_data_bundle",
        "validate_data_bundle",
        "_target_weights_df",
        "_daily_df",
        "_trades_df",
    }

    def __init__(
        self,
        llm: Any | None = None,
        web_client: Any | None = None,
        *,
        max_candidate_attempts: int = 3,
    ) -> None:
        self.llm = llm or llm_client
        self.web_client = web_client or WebResearchClient()
        self.max_candidate_attempts = max(1, int(max_candidate_attempts))

    @staticmethod
    def _render(text: object, **values: object) -> str:
        return Template(str(text or "")).render(**values).strip()

    @staticmethod
    def _parse_json_object(raw: object) -> dict[str, Any]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("研究规划结果不是 JSON 对象")
        parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("研究规划结果必须是 JSON 对象")
        return parsed

    @staticmethod
    def _require_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 必须是非空文字")
        return value.strip()

    @staticmethod
    def _require_text_list(
        value: object,
        field: str,
        *,
        minimum: int = 1,
        maximum: int | None = None,
    ) -> list[str]:
        if not isinstance(value, list):
            raise ValueError(f"{field} 必须是数组")
        result = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if len(result) < minimum:
            raise ValueError(f"{field} 至少需要 {minimum} 项")
        if maximum is not None and len(result) > maximum:
            raise ValueError(f"{field} 最多只能有 {maximum} 项")
        return result

    @classmethod
    def _collect_numeric_values(cls, value: Any) -> set[float]:
        values: set[float] = set()
        if value is None or isinstance(value, bool):
            return values
        if isinstance(value, (int, float)):
            number = float(value)
            if math.isfinite(number):
                values.add(number)
            return values
        if isinstance(value, str):
            for match in cls._numeric_literal_pattern.finditer(value):
                number = float(match.group("number"))
                if match.group("percent"):
                    number /= 100.0
                if math.isfinite(number):
                    values.add(number)
            return values
        if isinstance(value, Mapping):
            for item in value.values():
                values.update(cls._collect_numeric_values(item))
            return values
        if isinstance(value, (list, tuple, set)):
            for item in value:
                values.update(cls._collect_numeric_values(item))
        return values

    @staticmethod
    def _numeric_value_is_known(value: float, known_values: set[float]) -> bool:
        return any(
            math.isclose(value, known, rel_tol=1e-12, abs_tol=1e-12)
            for known in known_values
        )

    @classmethod
    def _new_numeric_rule_values(
        cls,
        *,
        candidate: Mapping[str, Any],
        source_text: str,
        accepted_strategy: Mapping[str, Any],
    ) -> list[float]:
        known_values = set(cls._natural_numeric_values)
        known_values.update(cls._collect_numeric_values(source_text))
        known_values.update(cls._collect_numeric_values(accepted_strategy))

        proposed_values: set[float] = set()
        for field_name in ("strategy_modification", "hypothesis"):
            proposed_values.update(cls._collect_numeric_values(candidate.get(field_name)))
        return sorted(
            value
            for value in proposed_values
            if not cls._numeric_value_is_known(value, known_values)
        )

    @staticmethod
    def _format_numeric_values(values: list[float]) -> str:
        return ", ".join(format(value, ".12g") for value in values)

    @classmethod
    def _normalize_calculation_contracts(cls, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ValueError("calculation_contracts 必须是非空数组")
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                raise ValueError(f"calculation_contracts[{index}] 必须是对象")
            prefix = f"calculation_contracts[{index}]"
            contract_id = cls._require_text(raw.get("contract_id"), f"{prefix}.contract_id")
            if contract_id in seen_ids:
                raise ValueError(f"重复的 calculation_contract.contract_id: {contract_id}")
            seen_ids.add(contract_id)
            date_basis = cls._require_text(raw.get("date_basis"), f"{prefix}.date_basis")
            if date_basis not in {
                "shared_market_trading_calendar",
                "per_symbol_observation_sequence",
            }:
                raise ValueError(
                    f"{prefix}.date_basis 只能是 shared_market_trading_calendar "
                    "或 per_symbol_observation_sequence"
                )
            required_dates = cls._require_text_list(
                raw.get("required_dates"), f"{prefix}.required_dates"
            )
            date_offsets = required_date_offsets(required_dates)
            required_value_columns = cls._require_text_list(
                raw.get("required_value_columns"),
                f"{prefix}.required_value_columns",
            )
            for field in ("allow_missing_intermediate_rows", "use_common_sample"):
                if not isinstance(raw.get(field), bool):
                    raise ValueError(f"{prefix}.{field} 必须是布尔值")
            empty_sample_action = cls._require_text(
                raw.get("empty_sample_action"), f"{prefix}.empty_sample_action"
            )
            if empty_sample_action not in {"condition_false", "skip_date", "raise_error"}:
                raise ValueError(
                    f"{prefix}.empty_sample_action 只能是 condition_false、skip_date 或 raise_error"
                )
            normalized.append(
                {
                    "contract_id": contract_id,
                    "table_key": cls._require_text(raw.get("table_key"), f"{prefix}.table_key"),
                    "date_basis": date_basis,
                    "required_dates": [item.lower() for item in required_dates],
                    "required_offsets": date_offsets,
                    "required_value_columns": required_value_columns,
                    "allow_missing_intermediate_rows": raw[
                        "allow_missing_intermediate_rows"
                    ],
                    "use_common_sample": raw["use_common_sample"],
                    "empty_sample_action": empty_sample_action,
                }
            )
        return normalized

    @staticmethod
    def _describes_shared_market_date_sample(value: Mapping[str, object]) -> bool:
        text = " ".join(
            str(value.get(field, ""))
            for field in ("research_question", "strategy_modification", "hypothesis")
        ).lower()
        has_offset = re.search(r"(?<![a-z0-9_])t\s*-\s*\d+", text) is not None
        has_shared_sample = any(
            phrase in text
            for phrase in (
                "统一市场交易日",
                "共同股票",
                "同一批股票",
                "共同样本",
                "共同计算范围",
                "common sample",
                "shared market trading day",
            )
        )
        return has_offset and has_shared_sample

    @staticmethod
    def _explicitly_uses_shared_market_dates(value: Mapping[str, object]) -> bool:
        text = " ".join(
            str(value.get(field, ""))
            for field in ("research_question", "strategy_modification", "hypothesis")
        ).lower()
        return "统一市场交易日" in text or (
            re.search(r"同时具有\s*t\s*日", text) is not None
            and re.search(r"t\s*-\s*\d+\s*日", text) is not None
        )

    @classmethod
    def _validate_theories(cls, value: dict[str, Any]) -> dict[str, Any]:
        raw_theories = value.get("theories")
        if not isinstance(raw_theories, list) or len(raw_theories) < 4:
            raise ValueError("theories 至少需要三个竞争解释和一个纯偶然解释")

        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        null_count = 0
        for index, raw in enumerate(raw_theories):
            if not isinstance(raw, dict):
                raise ValueError(f"theories[{index}] 必须是对象")
            theory_id = cls._require_text(raw.get("theory_id"), f"theories[{index}].theory_id")
            if theory_id in seen_ids:
                raise ValueError(f"重复的 theory_id: {theory_id}")
            seen_ids.add(theory_id)
            kind = cls._require_text(raw.get("kind"), f"theories[{index}].kind").lower()
            if kind not in {"causal", "null"}:
                raise ValueError(f"theories[{index}].kind 只能是 causal 或 null")
            if kind == "null":
                null_count += 1
            normalized.append(
                {
                    **raw,
                    "theory_id": theory_id,
                    "kind": kind,
                    "phenomenon_summary": cls._require_text(
                        raw.get("phenomenon_summary"),
                        f"theories[{index}].phenomenon_summary",
                    ),
                    "actor": cls._require_text(raw.get("actor"), f"theories[{index}].actor"),
                    "objective": cls._require_text(
                        raw.get("objective"), f"theories[{index}].objective"
                    ),
                    "constraints": cls._require_text_list(
                        raw.get("constraints"), f"theories[{index}].constraints"
                    ),
                    "sequence": cls._require_text_list(
                        raw.get("sequence"), f"theories[{index}].sequence", minimum=2
                    ),
                    "extra_predictions": cls._require_text_list(
                        raw.get("extra_predictions"),
                        f"theories[{index}].extra_predictions",
                        minimum=2,
                    ),
                    "counterexample": cls._require_text(
                        raw.get("counterexample"), f"theories[{index}].counterexample"
                    ),
                    "search_queries": cls._require_text_list(
                        raw.get("search_queries"),
                        f"theories[{index}].search_queries",
                        minimum=1,
                    ),
                    "evidence_source_ids": [
                        item.strip()
                        for item in raw.get("evidence_source_ids", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    if isinstance(raw.get("evidence_source_ids", []), list)
                    else [],
                }
            )
        if null_count != 1:
            raise ValueError("theories 必须恰好包含一个 kind=null 的纯偶然解释")

        return {
            **value,
            "theories": normalized,
            "shared_observations": cls._require_text_list(
                value.get("shared_observations", []),
                "shared_observations",
                minimum=1,
            ),
        }

    @staticmethod
    def _source_id(item: dict[str, Any]) -> str:
        content_hash = str(item.get("content_sha256", "")).strip()
        if content_hash:
            return content_hash[:16]
        return str(item.get("url", "")).strip()

    @staticmethod
    def _sanitize_web_result(result: object) -> dict[str, Any]:
        """只把截止日前可用的资料文字交给模型。"""

        if not isinstance(result, dict):
            return {"passed": False, "status": "unavailable", "result_count": 0}

        raw_results = result.get("results")
        if isinstance(raw_results, list):
            allowed_item_fields = (
                "query",
                "provider",
                "title",
                "url",
                "published_at",
                "retrieved_at",
                "source_cutoff_date",
                "summary",
                "content_sha256",
                "cutoff_status",
                "usable_for_evidence",
            )
            usable_results: list[dict[str, Any]] = []
            newly_excluded: dict[str, int] = {}
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                cutoff_status = str(item.get("cutoff_status") or "")
                item_is_usable = (
                    item.get("usable_for_evidence") is True
                    and cutoff_status not in {"after_cutoff", "unknown"}
                )
                if item_is_usable:
                    usable_results.append(
                        {key: item.get(key) for key in allowed_item_fields if key in item}
                    )
                    continue
                cutoff_status = cutoff_status or "unknown"
                newly_excluded[cutoff_status] = newly_excluded.get(cutoff_status, 0) + 1

            raw_excluded = result.get("excluded_by_cutoff")
            excluded_by_cutoff: dict[str, int] = {}
            if isinstance(raw_excluded, dict):
                for key, value in raw_excluded.items():
                    try:
                        count = max(0, int(value))
                    except (TypeError, ValueError):
                        continue
                    if count:
                        excluded_by_cutoff[str(key)] = count
            for key, count in newly_excluded.items():
                excluded_by_cutoff[key] = max(excluded_by_cutoff.get(key, 0), count)

            try:
                stated_excluded_count = max(0, int(result.get("excluded_result_count", 0)))
            except (TypeError, ValueError):
                stated_excluded_count = 0
            excluded_count = max(
                stated_excluded_count,
                sum(excluded_by_cutoff.values()),
                sum(newly_excluded.values()),
            )
            if usable_results:
                status = "ok"
            elif excluded_count:
                status = "filtered_by_cutoff"
            else:
                status = str(result.get("status") or "empty")

            provider_status: dict[str, dict[str, Any]] = {}
            raw_provider_status = result.get("provider_status")
            if isinstance(raw_provider_status, dict):
                for provider, raw_status in raw_provider_status.items():
                    if not isinstance(raw_status, dict):
                        continue
                    provider_status[str(provider)] = {
                        key: raw_status.get(key)
                        for key in (
                            "status",
                            "result_count",
                            "usable_result_count",
                            "excluded_result_count",
                        )
                        if key in raw_status
                    }

            return {
                "passed": bool(usable_results),
                "status": status,
                "query": str(result.get("query") or ""),
                "source_cutoff_date": result.get("source_cutoff_date"),
                "retrieved_at": result.get("retrieved_at"),
                "provider_status": provider_status,
                "results": usable_results,
                "usable_result_count": len(usable_results),
                "excluded_result_count": excluded_count,
                "excluded_by_cutoff": excluded_by_cutoff,
            }

        open_cutoff_status = str(result.get("cutoff_status") or "")
        open_is_usable = (
            result.get("usable_for_evidence") is True
            and open_cutoff_status not in {"after_cutoff", "unknown"}
        )
        if open_is_usable:
            allowed_open_fields = (
                "passed",
                "status",
                "query",
                "title",
                "url",
                "published_at",
                "retrieved_at",
                "source_cutoff_date",
                "summary",
                "content_sha256",
                "content",
                "cutoff_status",
                "usable_for_evidence",
                "content_is_untrusted",
                "notice",
            )
            return {
                key: result.get(key) for key in allowed_open_fields if key in result
            }

        excluded_count = 0
        try:
            excluded_count = max(0, int(result.get("excluded_result_count", 0)))
        except (TypeError, ValueError):
            pass
        cutoff_status = str(result.get("cutoff_status") or "")
        if cutoff_status in {"after_cutoff", "unknown"}:
            excluded_count = max(excluded_count, 1)
        return {
            "passed": False,
            "status": "filtered_by_cutoff"
            if excluded_count
            else str(result.get("status") or "unavailable"),
            "cutoff_status": cutoff_status,
            "excluded_result_count": excluded_count,
        }

    @classmethod
    def _collect_sources(cls, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(item: object) -> None:
            if not isinstance(item, dict):
                return
            if item.get("usable_for_evidence") is not True or str(
                item.get("cutoff_status") or ""
            ) in {"after_cutoff", "unknown"}:
                return
            url = str(item.get("url", "")).strip()
            if not url:
                return
            source_id = cls._source_id(item)
            key = source_id or url
            if key in seen:
                return
            seen.add(key)
            collected.append(
                {
                    "source_id": source_id,
                    "title": str(item.get("title", "")).strip(),
                    "url": url,
                    "published_at": item.get("published_at"),
                    "retrieved_at": item.get("retrieved_at"),
                    "summary": str(item.get("summary", "")).strip(),
                    "content_sha256": str(item.get("content_sha256", "")).strip(),
                    "cutoff_status": str(item.get("cutoff_status", "")).strip(),
                    "usable_for_evidence": item.get("usable_for_evidence") is True,
                }
            )

        for event in events:
            if not isinstance(event, dict):
                continue
            result = event.get("result")
            if not isinstance(result, dict):
                continue
            raw_results = result.get("results")
            if isinstance(raw_results, list):
                for item in raw_results:
                    add(item)
            add(result)
        return collected

    @classmethod
    def _summarize_web_events(
        cls,
        events: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            safe_result = cls._sanitize_web_result(event.get("result"))
            raw_results = safe_result.get("results")
            usable_count = len(raw_results) if isinstance(raw_results, list) else int(
                safe_result.get("usable_for_evidence") is True
            )
            summaries.append(
                {
                    "tool_name": str(event.get("tool_name") or ""),
                    "result": {
                        "passed": safe_result.get("passed") is True,
                        "status": str(safe_result.get("status") or ""),
                        "usable_result_count": usable_count,
                        "excluded_result_count": int(
                            safe_result.get("excluded_result_count") or 0
                        ),
                    },
                }
            )
        return summaries

    @classmethod
    def _limit_evidence_references(
        cls,
        theory_book: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> None:
        usable: dict[str, str] = {}
        for source in sources:
            if source.get("usable_for_evidence") is not True:
                continue
            source_id = str(source.get("source_id", "")).strip()
            full_hash = str(source.get("content_sha256", "")).strip()
            url = str(source.get("url", "")).strip()
            for key in (source_id, full_hash, url):
                if key:
                    usable[key] = source_id

        for theory in theory_book.get("theories", []):
            if not isinstance(theory, dict):
                continue
            normalized: list[str] = []
            for reference in theory.get("evidence_source_ids", []):
                source_id = usable.get(str(reference).strip())
                if source_id and source_id not in normalized:
                    normalized.append(source_id)
            theory["evidence_source_ids"] = normalized
            theory["external_evidence_status"] = "usable" if normalized else "unverified"

    def _web_tools(
        self,
        source_cutoff_date: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "搜索公开网页和论文资料。搜索词由研究者自己提出。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "open_url",
                    "description": "读取公开网页的正文、发布日期和资料摘要。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

        def search_web(query: str, max_results: int = 6) -> dict[str, Any]:
            return self._sanitize_web_result(
                self.web_client.search_web(
                    query,
                    max_results=max_results,
                    source_cutoff_date=source_cutoff_date,
                )
            )

        def open_url(url: str, query: str = "") -> dict[str, Any]:
            return self._sanitize_web_result(
                self.web_client.open_url(
                    url,
                    query=query,
                    source_cutoff_date=source_cutoff_date,
                )
            )

        return tools, {"search_web": search_web, "open_url": open_url}

    def build_theories(
        self,
        source_text: str,
        *,
        source_cutoff_date: str,
        interpreted_input: dict[str, Any] | None = None,
        available_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """在读取回测结果之前，建立可被区分的解释和额外预期。"""

        clean_source = self._require_text(source_text, "source_text")
        prompt = load_agent_prompt_dict(self._prompt_id)
        system_prompt = self._render(
            prompt.get("theory_system_prompt"),
            source_cutoff_date=source_cutoff_date,
        )
        user_prompt = self._render(
            prompt.get("theory_user_prompt"),
            source_text=clean_source,
            interpreted_input_json=json.dumps(
                interpreted_input or {}, ensure_ascii=False, indent=2, default=str
            ),
            available_fields_json=json.dumps(
                available_fields or [], ensure_ascii=False, default=str
            ),
            source_cutoff_date=source_cutoff_date,
        )
        tools, handlers = self._web_tools(source_cutoff_date)
        raw, events = self.llm.complete_with_tools(
            system_prompt,
            user_prompt,
            tools,
            handlers,
            temperature=0.25,
            max_tokens=7000,
            max_tool_rounds=10,
            require_json_object=True,
            final_json_instruction="请只返回提示中规定的一个 JSON 对象。",
            role=self.name,
            reasoning_effort="ultra",
        )
        validation_error = ""
        result: dict[str, Any] | None = None
        for attempt in range(1, self.max_candidate_attempts + 1):
            try:
                result = self._validate_theories(self._parse_json_object(raw))
                break
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                validation_error = str(exc)
                if attempt >= self.max_candidate_attempts:
                    raise
                # 搜索已经完成。这里只修正结构，不再调用工具，避免重复查网。
                raw = self.llm.complete(
                    system_prompt,
                    user_prompt
                    + "\n\n【已完成的可用资料】\n"
                    + json.dumps(
                        self._collect_sources(events),
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                    + "\n\n上一次理论JSON未通过检查，请保留有效解释并补齐或删去不完整项目。"
                    + f"\n检查结果：{validation_error}"
                    + "\n只返回完整JSON，不要再次搜索。",
                    temperature=0.0,
                    max_tokens=7000,
                    role=self.name,
                    reasoning_effort="ultra",
                )
        if result is None:
            raise ValueError(f"没有得到完整理论记录: {validation_error}")
        sources = self._collect_sources(events)
        self._limit_evidence_references(result, sources)
        result["research_sources"] = sources
        result["web_tool_events"] = self._summarize_web_events(events)
        result["source_cutoff_date"] = source_cutoff_date
        result["stage"] = "pre_backtest_theory_building"
        return result

    @classmethod
    def _default_available_data(cls) -> list[dict[str, Any]]:
        parsed = yaml.safe_load(cls._dataset_path.read_text(encoding="utf-8"))
        datasets = parsed.get("datasets", []) if isinstance(parsed, dict) else []
        return [item for item in datasets if isinstance(item, dict)]

    @staticmethod
    def _available_data_index(
        available_data: list[dict[str, Any]],
    ) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for item in available_data:
            if not isinstance(item, dict):
                continue
            table_key = str(item.get("table_key", "")).strip()
            if not table_key:
                continue
            fields: set[str] = set()
            raw_fields = item.get("fields", [])
            if isinstance(raw_fields, list):
                for field in raw_fields:
                    if isinstance(field, str) and field.strip():
                        fields.add(field.strip())
                    elif isinstance(field, dict):
                        name = field.get("name")
                        if isinstance(name, str) and name.strip():
                            fields.add(name.strip())
            for key in ("symbol_column", "datetime_column"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    fields.add(value.strip())
            result[table_key] = fields
        return result

    @classmethod
    def _valid_category(cls, category: str) -> bool:
        return category in cls._categories or bool(
            re.fullmatch(r"extra_[a-z][a-z0-9_]{0,47}", category)
        )

    @classmethod
    def _candidate_is_implementable(
        cls,
        candidate: dict[str, Any],
        available_data: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        index = cls._available_data_index(available_data)
        required_data = candidate.get("required_data")
        if not isinstance(required_data, list) or not required_data:
            return False, "required_data 必须是非空数组"
        for position, item in enumerate(required_data):
            if not isinstance(item, dict):
                return False, f"required_data[{position}] 不是对象"
            table_key = str(item.get("table_key", "")).strip()
            if table_key not in index:
                return False, f"数据表不可用: {table_key or '<empty>'}"
            raw_fields = item.get("fields", [])
            if raw_fields is None:
                raw_fields = []
            if not isinstance(raw_fields, list):
                return False, f"required_data[{position}].fields 不是数组"
            fields = [str(field).strip() for field in raw_fields if str(field).strip()]
            missing = sorted(set(fields) - index[table_key])
            if missing:
                return False, f"{table_key} 缺少字段: {', '.join(missing)}"
            if str(item.get("type", "")).strip() in {"time_series", "panel"}:
                universe = item.get("universe")
                if not isinstance(universe, dict):
                    return False, f"required_data[{position}].universe 必须是对象"
                universe_type = str(universe.get("type", "")).strip()
                if universe_type not in {
                    "named_pool",
                    "symbol_list",
                    "dataset_defined",
                }:
                    return False, (
                        f"required_data[{position}].universe.type 不可用: "
                        f"{universe_type or '<empty>'}"
                    )
                universe_value = universe.get("value")
                if universe_type == "dataset_defined":
                    if str(universe_value or "").strip() != table_key:
                        return False, (
                            f"required_data[{position}] 的 dataset_defined 必须指向同一张表 "
                            f"{table_key}；若要研究某类股票，请改用这张表可计算的特征条件"
                        )
                elif universe_type == "named_pool":
                    pool_name = str(universe_value or "").strip()
                    manager = MetadataManager.from_yaml_files()
                    pool = manager.get_universe(pool_name) if pool_name else None
                    historical_membership = (
                        pool.get("historical_membership")
                        if isinstance(pool, dict)
                        else None
                    )
                    if (
                        not pool_name
                        or normalize_historical_index_universe(historical_membership)
                        is None
                    ):
                        available_pools = (
                            ", ".join(manager.list_historical_universes()) or "无"
                        )
                        return False, (
                            f"股票范围 {pool_name or '<empty>'} 目前不能解析；"
                            f"可解析范围为 {available_pools}。请改用可解析范围，或改成"
                            "dataset_defined 并用现有字段定义可计算的股票特征"
                        )
                elif universe_type == "symbol_list":
                    if not isinstance(universe_value, list) or not universe_value:
                        return False, (
                            f"required_data[{position}].universe.value 必须是非空股票代码数组"
                        )
                    symbols = [
                        str(symbol).strip()
                        for symbol in universe_value
                        if isinstance(symbol, str) and str(symbol).strip()
                    ]
                    if len(symbols) != len(universe_value) or any(
                        not re.fullmatch(
                            r"(?:\d{6}\.(?:SZ|SH|SZSE|SSE)|(?:sz|sh)\d{6})",
                            symbol,
                            flags=re.IGNORECASE,
                        )
                        for symbol in symbols
                    ):
                        return False, (
                            f"required_data[{position}].universe.value 含有不能解析的股票代码"
                        )

        contracts = candidate.get("calculation_contracts")
        if isinstance(contracts, list):
            required_tables = {
                str(item.get("table_key", "")).strip()
                for item in required_data
                if isinstance(item, dict)
            }
            for position, contract in enumerate(contracts):
                if not isinstance(contract, dict):
                    return False, f"calculation_contracts[{position}] 不是对象"
                table_key = str(contract.get("table_key", "")).strip()
                if table_key not in required_tables:
                    return False, (
                        f"calculation_contracts[{position}].table_key 必须出现在 required_data: "
                        f"{table_key or '<empty>'}"
                    )
                fields = {
                    str(item).strip()
                    for item in contract.get("required_value_columns", [])
                    if str(item).strip()
                }
                missing = sorted(fields - index.get(table_key, set()))
                if missing:
                    return False, (
                        f"calculation_contracts[{position}] 使用了不存在的字段: "
                        + ", ".join(missing)
                    )

        backtest = candidate.get("backtest_datasets")
        if not isinstance(backtest, list) or not backtest:
            return False, "backtest_datasets 必须是非空数组"
        valid_backtest = set(MetadataManager.from_yaml_files().list_backtest_datasets())
        invalid = [str(item) for item in backtest if str(item).strip() not in valid_backtest]
        if invalid:
            return False, f"回测数据不可用: {', '.join(invalid)}"
        return True, ""

    @classmethod
    def _validate_candidate_shape(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("candidate_mode") != "modify_one_rule":
            raise ValueError("candidate_mode 必须是 modify_one_rule")
        category = cls._require_text(value.get("category"), "category")
        if not cls._valid_category(category):
            raise ValueError(f"未知研究类别: {category}")
        changed_fields = cls._require_text_list(
            value.get("changed_fields"), "changed_fields", minimum=1, maximum=3
        )
        predicted_changes = value.get("predicted_changes")
        if not isinstance(predicted_changes, list) or not predicted_changes:
            raise ValueError("predicted_changes 必须是非空数组")
        for index, item in enumerate(predicted_changes):
            if not isinstance(item, dict):
                raise ValueError(f"predicted_changes[{index}] 必须是对象")
            cls._require_text(item.get("metric"), f"predicted_changes[{index}].metric")
            cls._require_text(item.get("direction"), f"predicted_changes[{index}].direction")
            cls._require_text(item.get("reason"), f"predicted_changes[{index}].reason")

        normalized = {
            **value,
            "candidate_mode": "modify_one_rule",
            "category": category,
            "mechanism_id": cls._require_text(value.get("mechanism_id"), "mechanism_id"),
            "research_question": cls._require_text(
                value.get("research_question"), "research_question"
            ),
            "change_kind": cls._require_text(value.get("change_kind"), "change_kind"),
            "changed_fields": changed_fields,
            "strategy_modification": cls._require_text(
                value.get("strategy_modification"), "strategy_modification"
            ),
            "hypothesis": cls._require_text(value.get("hypothesis"), "hypothesis"),
            "predicted_changes": predicted_changes,
            "judgment_is_wrong_if": cls._require_text(
                value.get("judgment_is_wrong_if"), "judgment_is_wrong_if"
            ),
        }
        if category == "mechanism_signal":
            signal_definition = value.get("new_signal_definition")
            if not isinstance(signal_definition, dict) or not signal_definition:
                raise ValueError("mechanism_signal 必须提供 new_signal_definition 对象")
            comparison = value.get("mechanism_comparison")
            if not isinstance(comparison, dict):
                raise ValueError("mechanism_signal 必须提供 mechanism_comparison")
            required_variants = {"added_to_base", "signal_only"}
            missing_variants = required_variants - set(comparison)
            if missing_variants:
                raise ValueError(
                    "mechanism_comparison 缺少可回测候选: "
                    + ", ".join(sorted(missing_variants))
                )
            allowed_variants = required_variants | {"replace_related_rule"}
            extra_variants = set(comparison) - allowed_variants
            if extra_variants:
                raise ValueError(
                    "mechanism_comparison 含有未知候选: "
                    + ", ".join(sorted(extra_variants))
                )
            mechanism_id = normalized["mechanism_id"]
            complete_variant_fields = {
                "candidate_mode",
                "category",
                "mechanism_id",
                "research_question",
                "change_kind",
                "changed_fields",
                "strategy_modification",
                "hypothesis",
                "required_data",
                "backtest_datasets",
                "predicted_changes",
                "judgment_is_wrong_if",
                "new_signal_definition",
                "variant_mode",
            }
            for variant_name, raw_variant in comparison.items():
                if not isinstance(raw_variant, dict):
                    raise ValueError(
                        f"mechanism_comparison.{variant_name} 必须是完整候选对象"
                    )
                if raw_variant.get("variant_mode") != variant_name:
                    raise ValueError(
                        f"mechanism_comparison.{variant_name}.variant_mode 必须同名"
                    )
                if raw_variant.get("mechanism_id") != mechanism_id:
                    raise ValueError("机制对照候选必须使用同一个 mechanism_id")
                missing_fields = complete_variant_fields - set(raw_variant)
                if missing_fields:
                    raise ValueError(
                        f"mechanism_comparison.{variant_name} 不是完整候选，缺少: "
                        + ", ".join(sorted(missing_fields))
                    )
                if raw_variant.get("candidate_mode") != "modify_one_rule":
                    raise ValueError(
                        f"mechanism_comparison.{variant_name}.candidate_mode 必须是 modify_one_rule"
                    )
                if raw_variant.get("category") != "mechanism_signal":
                    raise ValueError(
                        f"mechanism_comparison.{variant_name}.category 必须是 mechanism_signal"
                    )
                if raw_variant.get("new_signal_definition") != signal_definition:
                    raise ValueError("机制对照候选必须保持同一个新信号定义")
                cls._require_text(
                    raw_variant.get("hypothesis"),
                    f"mechanism_comparison.{variant_name}.hypothesis",
                )
                cls._require_text(
                    raw_variant.get("strategy_modification"),
                    f"mechanism_comparison.{variant_name}.strategy_modification",
                )
                cls._require_text(
                    raw_variant.get("research_question"),
                    f"mechanism_comparison.{variant_name}.research_question",
                )
                cls._require_text(
                    raw_variant.get("change_kind"),
                    f"mechanism_comparison.{variant_name}.change_kind",
                )
                cls._require_text_list(
                    raw_variant.get("changed_fields"),
                    f"mechanism_comparison.{variant_name}.changed_fields",
                    minimum=1,
                    maximum=3,
                )
                if not isinstance(raw_variant.get("predicted_changes"), list) or not raw_variant.get(
                    "predicted_changes"
                ):
                    raise ValueError(
                        f"mechanism_comparison.{variant_name}.predicted_changes 必须是非空数组"
                    )
                cls._require_text(
                    raw_variant.get("judgment_is_wrong_if"),
                    f"mechanism_comparison.{variant_name}.judgment_is_wrong_if",
                )
                if not isinstance(raw_variant.get("required_data"), list) or not raw_variant.get(
                    "required_data"
                ):
                    raise ValueError(
                        f"mechanism_comparison.{variant_name}.required_data 必须是非空数组"
                    )
                if not isinstance(raw_variant.get("backtest_datasets"), list) or not raw_variant.get(
                    "backtest_datasets"
                ):
                    raise ValueError(
                        f"mechanism_comparison.{variant_name}.backtest_datasets 必须是非空数组"
                    )
            main_variant = comparison["added_to_base"]
            for field in (
                "candidate_mode",
                "category",
                "mechanism_id",
                "research_question",
                "change_kind",
                "changed_fields",
                "hypothesis",
                "strategy_modification",
                "required_data",
                "backtest_datasets",
                "predicted_changes",
                "judgment_is_wrong_if",
                "new_signal_definition",
            ):
                if main_variant.get(field) != value.get(field):
                    raise ValueError(
                        f"mechanism_comparison.added_to_base.{field} 必须与主候选完全相同"
                    )
            normalized["new_signal_definition"] = deepcopy(signal_definition)
            normalized["mechanism_comparison"] = deepcopy(comparison)
            normalized["variant_mode"] = "added_to_base"
        raw_contracts = value.get("calculation_contracts")
        if raw_contracts is not None:
            normalized["calculation_contracts"] = cls._normalize_calculation_contracts(
                raw_contracts
            )
            if cls._describes_shared_market_date_sample(value) and not any(
                contract["use_common_sample"] is True
                for contract in normalized["calculation_contracts"]
            ):
                raise ValueError(
                    "候选说明要求共同股票样本，calculation_contracts 必须记录 use_common_sample=true"
                )
            if cls._explicitly_uses_shared_market_dates(value) and not any(
                contract["date_basis"] == "shared_market_trading_calendar"
                for contract in normalized["calculation_contracts"]
            ):
                raise ValueError(
                    "候选把 t-k 写成共同的明确日期，date_basis 必须是 "
                    "shared_market_trading_calendar"
                )
        elif cls._describes_shared_market_date_sample(value):
            raise ValueError(
                "候选使用统一市场交易日 t-k 和共同股票样本，必须提供 calculation_contracts"
            )
        parameter_probe = value.get("parameter_probe")
        if parameter_probe is not None:
            if not isinstance(parameter_probe, dict):
                raise ValueError("parameter_probe 必须是对象或省略")
            param_name = cls._require_text(
                parameter_probe.get("param_name"), "parameter_probe.param_name"
            )
            raw_values = parameter_probe.get("values")
            if not isinstance(raw_values, list) or not 3 <= len(raw_values) <= 5:
                raise ValueError("parameter_probe.values 必须包含3到5个相邻数值")
            values: list[float] = []
            for raw in raw_values:
                if isinstance(raw, bool):
                    raise ValueError("parameter_probe.values 只能是有限数值")
                try:
                    number = float(raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError("parameter_probe.values 只能是有限数值") from exc
                if not math.isfinite(number):
                    raise ValueError("parameter_probe.values 只能是有限数值")
                if number not in values:
                    values.append(number)
            if len(values) < 3:
                raise ValueError("parameter_probe.values 去重后至少需要3个数值")
            normalized["parameter_probe"] = {
                "param_name": param_name,
                "values": values,
            }
        if normalized["change_kind"] == "threshold":
            if "parameter_probe" not in normalized:
                raise ValueError("数值门槛问题必须先提供 parameter_probe")
            if cls._parameter_value_token not in normalized["strategy_modification"]:
                raise ValueError(
                    "数值门槛的 strategy_modification 必须用 __SELECTED_VALUE__ 标出唯一待选值"
                )
            if cls._parameter_value_token not in normalized["hypothesis"]:
                raise ValueError(
                    "数值门槛的 hypothesis 必须用 __SELECTED_VALUE__ 标出唯一待选值"
                )
        if value.get("decision") in {"request_data", "diagnose_only", "stop"}:
            raise ValueError("V2 正式候选只能返回一个可回测的修改")
        return normalized

    @classmethod
    def _remove_final_period_data(cls, value: object) -> object:
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, dict) and str(item.get("period_kind", "")).lower() in {
                    "final",
                    "final_test",
                }:
                    continue
                result.append(cls._remove_final_period_data(item))
            return result
        if isinstance(value, dict):
            if str(value.get("period_kind", "")).lower() in {"final", "final_test"}:
                return {}
            result: dict[str, object] = {}
            for key, item in value.items():
                normalized_key = str(key).lower()
                if normalized_key in {
                    "final_report",
                    "final_test_report",
                    "final_test_result",
                    "held_out_report",
                }:
                    continue
                result[str(key)] = cls._remove_final_period_data(item)
            return result
        return value

    @classmethod
    def compact_development_report(
        cls,
        value: Mapping[str, object] | object,
    ) -> dict[str, object]:
        """只保留研究判断会用到的开发期指标。"""

        cleaned = cls._remove_final_period_data(value)
        if not isinstance(cleaned, dict) or not cleaned:
            return {}
        period_kind = str(cleaned.get("period_kind", "")).strip().lower()
        if period_kind in {"final", "final_test"}:
            return {}

        compact = {
            key: deepcopy(cleaned[key])
            for key in cls._development_summary_keys
            if key in cleaned
        }
        reasons = compact.get("failure_reasons")
        if isinstance(reasons, list):
            compact["failure_reasons"] = [
                str(item)[:500]
                for item in reasons[:20]
            ]

        for group_name in cls._development_fold_groups:
            raw_folds = cleaned.get(group_name)
            if not isinstance(raw_folds, list):
                continue
            folds: list[dict[str, object]] = []
            for raw_fold in raw_folds[:32]:
                if not isinstance(raw_fold, dict):
                    continue
                fold = {
                    key: deepcopy(raw_fold[key])
                    for key in cls._development_fold_keys
                    if key in raw_fold
                }
                for text_key in ("summary", "error"):
                    if text_key in fold:
                        fold[text_key] = str(fold[text_key])[:1000]
                folds.append(fold)
            compact[group_name] = folds
        return compact

    @classmethod
    def _compact_history_value(cls, value: object, *, field_name: str = "") -> object:
        if field_name in cls._report_field_names and isinstance(value, Mapping):
            return cls.compact_development_report(value)
        if isinstance(value, list):
            return [cls._compact_history_value(item) for item in value]
        if isinstance(value, Mapping):
            result: dict[str, object] = {}
            for key, item in value.items():
                name = str(key)
                if name in cls._large_backtest_fields:
                    continue
                result[name] = cls._compact_history_value(item, field_name=name)
            return result
        return deepcopy(value)

    @classmethod
    def compact_candidate_history(
        cls,
        value: list[dict[str, Any]] | object,
    ) -> list[dict[str, object]]:
        """缩小候选记录，并排除最终测试资料和大块回测明细。"""

        cleaned = cls._remove_final_period_data(value)
        if not isinstance(cleaned, list):
            return []
        compact: list[dict[str, object]] = []
        for item in cleaned:
            if not isinstance(item, Mapping):
                continue
            row = cls._compact_history_value(item)
            if isinstance(row, dict) and row:
                compact.append(row)
        return compact

    def propose_candidate(
        self,
        *,
        source_text: str,
        theory_book: dict[str, Any],
        accepted_strategy: dict[str, Any],
        development_report: dict[str, Any],
        candidate_history: list[dict[str, Any]],
        covered_categories: list[str] | None = None,
        available_data: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """根据开发期结果挑一个问题；数据不够时改挑可计算的问题。"""

        clean_source = self._require_text(source_text, "source_text")
        data_catalog = available_data or self._default_available_data()
        covered = {
            str(item).strip()
            for item in (covered_categories or [])
            if str(item).strip() in self._categories
        }
        uncovered = self._categories - covered
        prompt = load_agent_prompt_dict(self._prompt_id)
        system_prompt = self._render(prompt.get("candidate_system_prompt"))
        base_values = {
            "source_text": clean_source,
            "theory_book_json": json.dumps(theory_book, ensure_ascii=False, indent=2, default=str),
            "accepted_strategy_json": json.dumps(
                accepted_strategy, ensure_ascii=False, indent=2, default=str
            ),
            "development_report_json": json.dumps(
                self.compact_development_report(development_report),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "candidate_history_json": json.dumps(
                self.compact_candidate_history(candidate_history),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "covered_categories_json": json.dumps(
                covered_categories or [], ensure_ascii=False
            ),
            "available_data_json": json.dumps(
                data_catalog, ensure_ascii=False, indent=2, default=str
            ),
            "available_universes_json": json.dumps(
                MetadataManager.from_yaml_files().list_historical_universes(),
                ensure_ascii=False,
            ),
        }

        last_error = ""
        for attempt in range(1, self.max_candidate_attempts + 1):
            user_prompt = self._render(
                prompt.get("candidate_user_prompt"),
                **base_values,
                previous_rejection=last_error,
            )
            raw = self.llm.complete(
                system_prompt,
                user_prompt,
                temperature=0.15,
                max_tokens=7000,
                role=self.name,
                reasoning_effort="ultra",
            )
            try:
                candidate = self._validate_candidate_shape(self._parse_json_object(raw))
                new_numeric_values = self._new_numeric_rule_values(
                    candidate=candidate,
                    source_text=clean_source,
                    accepted_strategy=accepted_strategy,
                )
                if new_numeric_values:
                    formatted_values = self._format_numeric_values(new_numeric_values)
                    raise ValueError(
                        "候选引入此前采用版本和用户原始条件中没有的新数值界线: "
                        f"{formatted_values}。不能作为普通单候选直接回测；请改为 "
                        "change_kind=threshold，用 parameter_probe 提供3到5个相邻值，"
                        "给出清晰的 param_name，并在 strategy_modification 与 hypothesis "
                        f"中用 {self._parameter_value_token} 代替该数值。"
                    )
                valid_theory_ids = {
                    str(item.get("theory_id", "")).strip()
                    for item in theory_book.get("theories", [])
                    if isinstance(item, dict)
                }
                if candidate["mechanism_id"] not in valid_theory_ids:
                    raise ValueError("mechanism_id 不在事先建立的解释清单中")
                if uncovered and candidate["category"] not in uncovered:
                    raise ValueError(
                        "仍有未正式检查的思考维度，本次须从这些维度中选择: "
                        + ", ".join(sorted(uncovered))
                    )
                implementable, reason = self._candidate_is_implementable(
                    candidate, data_catalog
                )
                if not implementable:
                    raise ValueError(reason)
                if candidate["change_kind"] == "threshold":
                    probe = candidate.get("parameter_probe", {})
                    param_name = str(probe.get("param_name", "")).strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", param_name):
                        raise ValueError(
                            "parameter_probe.param_name 必须是清晰的英文参数名，"
                            "可以是当前已有参数，也可以是本候选新增参数"
                        )
                if candidate["category"] == "mechanism_signal":
                    comparison = candidate["mechanism_comparison"]
                    for variant_name, variant in comparison.items():
                        implementable, reason = self._candidate_is_implementable(
                            variant, data_catalog
                        )
                        if not implementable:
                            raise ValueError(
                                f"mechanism_comparison.{variant_name} 不可回测: {reason}"
                            )
                candidate["planner_attempt"] = attempt
                candidate["data_feasibility"] = "confirmed"
                return candidate
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)

        raise ValueError(f"没有得到可执行的单问题候选: {last_error}")

    def finalize_parameter_probe(
        self,
        *,
        planned_candidate: dict[str, Any],
        accepted_strategy: dict[str, Any],
        probe_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """看完相邻数值比较后，决定是否值得做一次正式确认。"""

        probe = planned_candidate.get("parameter_probe")
        if not isinstance(probe, dict):
            raise ValueError("planned_candidate 缺少 parameter_probe")
        values = probe.get("values")
        if not isinstance(values, list):
            raise ValueError("parameter_probe.values 缺失")

        prompt = load_agent_prompt_dict(self._prompt_id)
        system_prompt = self._render(prompt.get("probe_judge_system_prompt"))
        user_prompt = self._render(
            prompt.get("probe_judge_user_prompt"),
            planned_candidate_json=json.dumps(
                planned_candidate, ensure_ascii=False, indent=2, default=str
            ),
            accepted_strategy_json=json.dumps(
                accepted_strategy, ensure_ascii=False, indent=2, default=str
            ),
            probe_results_json=json.dumps(
                self.compact_candidate_history(probe_results),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )
        raw = self.llm.complete(
            system_prompt,
            user_prompt,
            temperature=0.0,
            max_tokens=7000,
            role=self.name,
            reasoning_effort="ultra",
        )
        result = self._parse_json_object(raw)
        decision = self._require_text(result.get("decision"), "decision")
        if decision not in {"formal_test", "reject_probe"}:
            raise ValueError("decision 只能是 formal_test 或 reject_probe")
        result["decision"] = decision
        result["parameter_shape"] = self._require_text(
            result.get("parameter_shape"), "parameter_shape"
        )
        result["reason"] = self._require_text(result.get("reason"), "reason")
        if decision == "reject_probe":
            return result

        selected_value = result.get("selected_value")
        try:
            selected_number = float(selected_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("selected_value 必须是探查过的数值") from exc
        numeric_values = [float(item) for item in values]
        if selected_number not in numeric_values:
            raise ValueError("selected_value 必须来自 parameter_probe.values")
        token = self._parameter_value_token
        hypothesis = str(planned_candidate.get("hypothesis", ""))
        modification = str(planned_candidate.get("strategy_modification", ""))
        if token not in hypothesis or token not in modification:
            raise ValueError("原计划没有在完整规则中标出唯一待选值")
        selected_text = str(int(selected_number)) if selected_number.is_integer() else repr(selected_number)
        formal_candidate = deepcopy(planned_candidate)
        formal_candidate["hypothesis"] = hypothesis.replace(token, selected_text)
        formal_candidate["strategy_modification"] = modification.replace(token, selected_text)
        formal_candidate["selected_parameter"] = {
            "param_name": str(probe.get("param_name", "")).strip(),
            "value": selected_number,
        }
        for field in ("research_question", "change_kind", "changed_fields", "required_data"):
            if formal_candidate.get(field) != planned_candidate.get(field):
                raise ValueError(f"正式候选不得改变 {field}")
        if token in formal_candidate["hypothesis"] or token in formal_candidate["strategy_modification"]:
            raise ValueError("正式规则没有实际采用选中的数值")
        result["selected_value"] = selected_number
        result["formal_candidate"] = formal_candidate
        return result
