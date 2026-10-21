from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from jinja2 import Template

from quanta_agents.config import get_agent_max_retries
from quanta_agents.exceptions import AgentExecutionError
from quanta_agents.llm import llm_client
from quanta_agents.prompt_loader import load_agent_prompt_dict


class InputInterpreterV2:
    """把一句研究想法整理为带来源的基准输入，不负责提出改进。"""

    name = "InputInterpreterV2"
    schema_version = "input_interpretation_v2"
    allowed_sources = {"user", "runtime", "default"}
    allowed_condition_operators = {"==", "!=", "<", "<=", ">", ">="}
    required_top_level_keys = {
        "schema_version",
        "original_text",
        "explicit_conditions",
        "ambiguous_items",
        "unknown_items",
        "temporary_baseline_decisions",
        "automatic_mode",
    }

    def __init__(
        self,
        *,
        client: object | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.client = client or llm_client
        self.max_retries = (
            max_retries
            if isinstance(max_retries, int) and max_retries > 0
            else get_agent_max_retries(self.name)
        )

    @staticmethod
    def _render(template_text: str, values: Mapping[str, object]) -> str:
        return Template(template_text).render(**values).strip()

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()

        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("输出中没有完整的 JSON 对象")

        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("输出必须是 JSON 对象")
        return parsed

    @staticmethod
    def _runtime_value(runtime_profile: Mapping[str, object], path: str) -> object:
        current: object = runtime_profile
        for part in path.split("."):
            if not part or not isinstance(current, Mapping) or part not in current:
                raise ValueError(f"runtime_key 在运行设置中不存在: {path}")
            current = current[part]
        return current

    @classmethod
    def _validate_source(cls, value: object, location: str) -> str:
        source = str(value or "").strip()
        if source not in cls.allowed_sources:
            raise ValueError(
                f"{location}.source 只能是 user、runtime 或 default"
            )
        return source

    @staticmethod
    def _validate_quote(
        item: Mapping[str, object],
        *,
        source_text: str,
        location: str,
    ) -> None:
        quote = str(item.get("evidence_quote", "")).strip()
        if not quote:
            raise ValueError(f"{location} 缺少 evidence_quote")
        if quote not in source_text:
            raise ValueError(f"{location}.evidence_quote 不在用户原文中")

    @staticmethod
    def _validate_item_ids(
        items: object,
        *,
        field_name: str,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        if not isinstance(items, list):
            raise ValueError(f"{field_name} 必须是数组")

        normalized: list[dict[str, Any]] = []
        item_ids: set[str] = set()
        for index, raw_item in enumerate(items):
            if not isinstance(raw_item, dict):
                raise ValueError(f"{field_name}[{index}] 必须是对象")
            item_id = str(raw_item.get("id", "")).strip()
            if not item_id:
                raise ValueError(f"{field_name}[{index}] 缺少 id")
            if item_id in item_ids:
                raise ValueError(f"{field_name} 中存在重复 id: {item_id}")
            item_ids.add(item_id)
            normalized.append(dict(raw_item))
        return normalized, item_ids

    @classmethod
    def _validate_output(
        cls,
        parsed: dict[str, Any],
        *,
        source_text: str,
        runtime_profile: Mapping[str, object],
        automatic_research: bool,
    ) -> dict[str, Any]:
        actual_keys = set(parsed)
        if actual_keys != cls.required_top_level_keys:
            missing = sorted(cls.required_top_level_keys - actual_keys)
            extra = sorted(actual_keys - cls.required_top_level_keys)
            raise ValueError(f"顶层字段不符合要求；缺少={missing}，多出={extra}")

        if parsed.get("schema_version") != cls.schema_version:
            raise ValueError(f"schema_version 必须是 {cls.schema_version}")
        if parsed.get("original_text") != source_text:
            raise ValueError("original_text 必须逐字保留用户原文")

        explicit, explicit_ids = cls._validate_item_ids(
            parsed.get("explicit_conditions"),
            field_name="explicit_conditions",
        )
        ambiguous, ambiguous_ids = cls._validate_item_ids(
            parsed.get("ambiguous_items"),
            field_name="ambiguous_items",
        )
        unknown, unknown_ids = cls._validate_item_ids(
            parsed.get("unknown_items"),
            field_name="unknown_items",
        )
        decisions, decision_ids = cls._validate_item_ids(
            parsed.get("temporary_baseline_decisions"),
            field_name="temporary_baseline_decisions",
        )

        all_ids = explicit_ids | ambiguous_ids | unknown_ids | decision_ids
        expected_count = (
            len(explicit_ids) + len(ambiguous_ids) + len(unknown_ids) + len(decision_ids)
        )
        if len(all_ids) != expected_count:
            raise ValueError("不同数组之间的 id 也不能重复")

        for index, item in enumerate(explicit):
            location = f"explicit_conditions[{index}]"
            if cls._validate_source(item.get("source"), location) != "user":
                raise ValueError(f"{location}.source 必须是 user")
            cls._validate_quote(item, source_text=source_text, location=location)
            statement = str(item.get("statement", "")).strip()
            evidence_quote = str(item.get("evidence_quote", "")).strip()
            if not statement:
                raise ValueError(f"{location} 缺少 statement")
            if statement != evidence_quote:
                raise ValueError(
                    f"{location}.statement 必须逐字等于 evidence_quote，避免改写时增加条件"
                )
            condition_spec = item.get("condition_spec")
            if not isinstance(condition_spec, dict):
                raise ValueError(f"{location}.condition_spec 必须是对象")
            allowed_spec_keys = {"field", "operator", "value", "unit"}
            extra_keys = set(condition_spec) - allowed_spec_keys
            if extra_keys:
                raise ValueError(
                    f"{location}.condition_spec 含有未允许字段: {sorted(extra_keys)}"
                )
            field = str(condition_spec.get("field", "")).strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", field):
                raise ValueError(
                    f"{location}.condition_spec.field 必须是稳定的小写英文字段名"
                )
            operator = str(condition_spec.get("operator", "")).strip()
            if operator not in cls.allowed_condition_operators:
                raise ValueError(
                    f"{location}.condition_spec.operator 不受支持: {operator or '<empty>'}"
                )
            if "value" not in condition_spec or condition_spec.get("value") is None:
                raise ValueError(f"{location}.condition_spec 缺少 value")
            spec_value = condition_spec.get("value")
            if not isinstance(spec_value, (bool, int, float)):
                raise ValueError(
                    f"{location}.condition_spec.value 必须是可直接比较的JSON数值或布尔值"
                )
            if isinstance(spec_value, float) and not math.isfinite(spec_value):
                raise ValueError(f"{location}.condition_spec.value 必须是有限数值")
            if "unit" in condition_spec and not str(condition_spec.get("unit", "")).strip():
                raise ValueError(f"{location}.condition_spec.unit 不能为空")

        for index, item in enumerate(ambiguous):
            location = f"ambiguous_items[{index}]"
            if cls._validate_source(item.get("source"), location) != "user":
                raise ValueError(f"{location}.source 必须是 user")
            cls._validate_quote(item, source_text=source_text, location=location)
            if not str(item.get("item", "")).strip() or not str(
                item.get("reason", "")
            ).strip():
                raise ValueError(f"{location} 必须包含 item 和 reason")

        for index, item in enumerate(unknown):
            location = f"unknown_items[{index}]"
            source = cls._validate_source(item.get("source"), location)
            if not str(item.get("item", "")).strip() or not str(
                item.get("reason", "")
            ).strip():
                raise ValueError(f"{location} 必须包含 item 和 reason")
            if source == "user":
                cls._validate_quote(item, source_text=source_text, location=location)
            elif source == "runtime":
                runtime_key = str(item.get("runtime_key", "")).strip()
                if not runtime_key:
                    raise ValueError(f"{location} 缺少 runtime_key")
                cls._runtime_value(runtime_profile, runtime_key)

        resolvable_ids = ambiguous_ids | unknown_ids
        resolved_ids: set[str] = set()
        for index, item in enumerate(decisions):
            location = f"temporary_baseline_decisions[{index}]"
            source = cls._validate_source(item.get("source"), location)
            resolves_item_id = str(item.get("resolves_item_id", "")).strip()
            if resolves_item_id not in resolvable_ids:
                raise ValueError(
                    f"{location}.resolves_item_id 必须对应已记录的含糊项或未知项"
                )
            resolved_ids.add(resolves_item_id)
            decision_field = str(item.get("field", "")).strip()
            if not decision_field or "value" not in item:
                raise ValueError(f"{location} 必须包含 field 和 value")
            if not str(item.get("reason", "")).strip():
                raise ValueError(f"{location} 缺少 reason")
            fixed_execution_fields = {"exit_execution", "missing_bar_policy"}
            if decision_field in fixed_execution_fields:
                runtime_execution = runtime_profile.get("execution")
                if not isinstance(runtime_execution, Mapping) or (
                    decision_field not in runtime_execution
                ):
                    raise ValueError(f"运行设置缺少 execution.{decision_field}")
                if item.get("value") != runtime_execution[decision_field]:
                    raise ValueError(
                        f"{location}.value 必须与运行设置 "
                        f"execution.{decision_field} 一致"
                    )
            if source == "user":
                cls._validate_quote(item, source_text=source_text, location=location)
            elif source == "runtime":
                runtime_key = str(item.get("runtime_key", "")).strip()
                if not runtime_key:
                    raise ValueError(f"{location} 缺少 runtime_key")
                runtime_value = cls._runtime_value(runtime_profile, runtime_key)
                if item.get("value") != runtime_value:
                    raise ValueError(
                        f"{location}.value 与 runtime_key 对应的运行设置不一致"
                    )

        automatic_mode = parsed.get("automatic_mode")
        if not isinstance(automatic_mode, dict):
            raise ValueError("automatic_mode 必须是对象")
        expected_enabled = bool(automatic_research)
        if automatic_mode.get("enabled") is not expected_enabled:
            raise ValueError("automatic_mode.enabled 与调用参数不一致")
        can_continue = automatic_mode.get("can_continue")
        if not isinstance(can_continue, bool):
            raise ValueError("automatic_mode.can_continue 必须是布尔值")
        if expected_enabled and not can_continue:
            raise ValueError("自动研究模式不得因含糊项或未知项停住")
        if expected_enabled and str(automatic_mode.get("blocking_reason", "")).strip():
            raise ValueError("自动研究模式不得填写 blocking_reason")
        if expected_enabled and resolved_ids != resolvable_ids:
            missing_decisions = sorted(resolvable_ids - resolved_ids)
            raise ValueError(
                "自动研究模式下，每个含糊项和未知项都必须有一个可追溯的临时决定: "
                + ", ".join(missing_decisions)
            )

        normalized = dict(parsed)
        normalized["explicit_conditions"] = explicit
        normalized["ambiguous_items"] = ambiguous
        normalized["unknown_items"] = unknown
        normalized["temporary_baseline_decisions"] = decisions
        normalized["automatic_mode"] = dict(automatic_mode)
        return normalized

    def run(
        self,
        source_text: str,
        runtime_profile: Mapping[str, object] | None = None,
        *,
        automatic_research: bool = True,
    ) -> dict[str, Any]:
        source_text = str(source_text or "")
        if not source_text.strip():
            raise ValueError("source_text 不能为空")

        runtime_profile = dict(runtime_profile or {})
        prompt_config = load_agent_prompt_dict("input_interpreter_v2")
        system_prompt = str(prompt_config.get("system_prompt", "")).strip()
        user_prompt_template = str(prompt_config.get("user_prompt", "")).strip()
        if not system_prompt or not user_prompt_template:
            raise ValueError("input_interpreter_v2 提示词缺少 system_prompt 或 user_prompt")

        base_user_prompt = self._render(
            user_prompt_template,
            {
                "source_text_json": json.dumps(source_text, ensure_ascii=False),
                "runtime_profile_json": json.dumps(
                    runtime_profile,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "automatic_research_json": json.dumps(bool(automatic_research)),
            },
        )

        last_error: Exception | None = None
        retry_note = ""
        for _attempt in range(1, self.max_retries + 1):
            active_user_prompt = base_user_prompt + retry_note
            try:
                text = self.client.complete(  # type: ignore[attr-defined]
                    system_prompt=system_prompt,
                    user_prompt=active_user_prompt,
                    temperature=0.0,
                    max_tokens=3500,
                    role="input_interpreter_v2",
                    reasoning_effort="high",
                    multi_agent=False,
                )
                parsed = self._extract_json_object(str(text))
                return self._validate_output(
                    parsed,
                    source_text=source_text,
                    runtime_profile=runtime_profile,
                    automatic_research=automatic_research,
                )
            except Exception as exc:
                last_error = exc
                retry_note = (
                    "\n\n上一次输出没有通过检查。请重新输出完整 JSON，不要解释。"
                    f"\n检查结果：{exc}"
                )

        raise AgentExecutionError(
            self.name,
            self.max_retries,
            str(last_error or "无法整理输入"),
            cause=last_error,
        )
