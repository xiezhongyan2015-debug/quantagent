from __future__ import annotations

import json
from numbers import Real
from collections.abc import Mapping
from typing import Any

from jinja2 import Template

from quanta_agents.config import get_agent_max_retries
from quanta_agents.exceptions import AgentExecutionError
from quanta_agents.llm import llm_client
from quanta_agents.prompt_loader import load_agent_prompt_dict


class BaselineBuilderV2:
    """把已整理的一句话变成可执行的第一版规则。"""

    name = "BaselineBuilderV2"
    required_keys = {
        "hypothesis",
        "strategy_modification",
        "required_data",
        "backtest_datasets",
        "missing_concepts",
        "candidate_mode",
        "baseline_parameters",
        "decision_map",
    }

    def __init__(self, *, client: object | None = None, max_retries: int | None = None) -> None:
        self.client = client or llm_client
        self.max_retries = (
            max_retries
            if isinstance(max_retries, int) and max_retries > 0
            else get_agent_max_retries(self.name)
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("输出中没有完整 JSON 对象")
        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("输出必须是 JSON 对象")
        return parsed

    @classmethod
    def _same_value(cls, left: object, right: object) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return type(left) is type(right) and left == right
        if isinstance(left, Real) and isinstance(right, Real):
            return float(left) == float(right)
        return type(left) is type(right) and left == right

    @classmethod
    def _expected_decision_entries(
        cls,
        interpretation: Mapping[str, object],
    ) -> dict[str, dict[str, object]]:
        explicit = interpretation.get("explicit_conditions")
        if not isinstance(explicit, list):
            raise ValueError("输入整理结果缺少 explicit_conditions")
        decisions = interpretation.get("temporary_baseline_decisions")
        if not isinstance(decisions, list):
            raise ValueError("输入整理结果缺少 temporary_baseline_decisions")

        expected: dict[str, dict[str, object]] = {}
        for index, item in enumerate(explicit):
            if not isinstance(item, Mapping):
                raise ValueError(f"explicit_conditions[{index}] 必须是对象")
            item_id = str(item.get("id", "")).strip()
            spec = item.get("condition_spec")
            if not item_id or not isinstance(spec, Mapping):
                raise ValueError(f"explicit_conditions[{index}] 缺少编号或 condition_spec")
            field = str(spec.get("field", "")).strip()
            operator = str(spec.get("operator", "")).strip()
            if not field or not operator or "value" not in spec:
                raise ValueError(f"explicit_conditions[{index}].condition_spec 不完整")
            row: dict[str, object] = {
                "source_kind": "explicit_condition",
                "field": field,
                "operator": operator,
                "value": spec.get("value"),
            }
            if "unit" in spec:
                row["unit"] = spec.get("unit")
            expected[item_id] = row

        for index, item in enumerate(decisions):
            if not isinstance(item, Mapping):
                raise ValueError(f"temporary_baseline_decisions[{index}] 必须是对象")
            item_id = str(item.get("id", "")).strip()
            field = str(item.get("field", "")).strip()
            if not item_id or not field or "value" not in item:
                raise ValueError(f"temporary_baseline_decisions[{index}] 不完整")
            if item_id in expected:
                raise ValueError(f"输入编号重复: {item_id}")
            expected[item_id] = {
                "source_kind": "temporary_decision",
                "field": field,
                "operator": "assign",
                "value": item.get("value"),
            }
        return expected

    @classmethod
    def _validate(
        cls,
        value: dict[str, Any],
        interpretation: Mapping[str, object],
        runtime_profile: Mapping[str, object],
    ) -> dict[str, Any]:
        if set(value) != cls.required_keys:
            raise ValueError(
                f"顶层字段不正确，缺少={sorted(cls.required_keys - set(value))}，"
                f"多出={sorted(set(value) - cls.required_keys)}"
            )
        if value.get("candidate_mode") != "baseline":
            raise ValueError("第一次规则的 candidate_mode 必须是 baseline")
        hypothesis = str(value.get("hypothesis", "")).strip()
        if not hypothesis:
            raise ValueError("hypothesis 不能为空")
        modification = str(value.get("strategy_modification", "")).strip()
        if not modification:
            raise ValueError("strategy_modification 必须说明这是忠实基准，不是改进方案")

        required_data = value.get("required_data")
        if not isinstance(required_data, list) or not required_data:
            raise ValueError("required_data 必须是非空数组")
        first = required_data[0]
        if not isinstance(first, dict):
            raise ValueError("required_data[0] 必须是对象")
        data_profile = runtime_profile.get("data")
        if not isinstance(data_profile, Mapping):
            raise ValueError("运行设置缺少 data")
        expected_table = str(data_profile.get("table_key", "")).strip()
        if first.get("table_key") != expected_table:
            raise ValueError("第一张资料表必须采用运行设置指定的日线表")
        if first.get("type") != "time_series":
            raise ValueError("日线资料 type 必须是 time_series")
        universe = first.get("universe")
        if not isinstance(universe, dict):
            raise ValueError("required_data[0].universe 必须是对象")
        runtime_universe = runtime_profile.get("universe")
        if not isinstance(runtime_universe, Mapping):
            raise ValueError("运行设置缺少 universe")
        expected_required_universe = runtime_universe.get("required_data")
        if universe != expected_required_universe:
            raise ValueError("资料范围必须原样采用运行设置")

        fields = first.get("fields")
        allowed_fields = data_profile.get("available_fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError("日线字段不能为空")
        if not isinstance(allowed_fields, list) or any(field not in allowed_fields for field in fields):
            raise ValueError("required_data 使用了运行设置中没有的字段")
        if not {"open", "high", "low", "close", "volume"}.issubset(set(fields)):
            raise ValueError("基准至少需要 open/high/low/close/volume")

        datasets = value.get("backtest_datasets")
        expected_backtest = data_profile.get("backtest_dataset")
        if datasets != [expected_backtest]:
            raise ValueError("backtest_datasets 必须原样采用运行设置")

        expected_entries = cls._expected_decision_entries(interpretation)
        decision_map = value.get("decision_map")
        if not isinstance(decision_map, list):
            raise ValueError("decision_map 必须是数组")
        used_ids: set[str] = set()
        for index, item in enumerate(decision_map):
            if not isinstance(item, dict):
                raise ValueError(f"decision_map[{index}] 必须是对象")
            decision_id = str(item.get("decision_id", "")).strip()
            if decision_id not in expected_entries:
                raise ValueError(f"decision_map[{index}] 引用了不存在的输入编号")
            if decision_id in used_ids:
                raise ValueError(f"decision_map[{index}] 重复引用输入编号 {decision_id}")
            if not str(item.get("implemented_as", "")).strip():
                raise ValueError(f"decision_map[{index}] 缺少 implemented_as")
            expected = expected_entries[decision_id]
            for field in ("source_kind", "field", "operator"):
                if item.get(field) != expected.get(field):
                    raise ValueError(
                        f"decision_map[{index}].{field} 与输入 {decision_id} 不一致"
                    )
            if not cls._same_value(item.get("value"), expected.get("value")):
                raise ValueError(
                    f"decision_map[{index}].value 与输入 {decision_id} 不一致"
                )
            if "unit" in expected and item.get("unit") != expected.get("unit"):
                raise ValueError(
                    f"decision_map[{index}].unit 与输入 {decision_id} 不一致"
                )
            used_ids.add(decision_id)
        if used_ids != set(expected_entries):
            missing_ids = sorted(set(expected_entries) - used_ids)
            raise ValueError(
                "每条明确条件和每个临时基准决定都必须在 decision_map 中说明实现方法: "
                + ", ".join(missing_ids)
            )

        parameters = value.get("baseline_parameters")
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError("baseline_parameters 必须是非空对象")
        expected_by_field: dict[str, object] = {}
        for item_id, expected in expected_entries.items():
            field = str(expected["field"])
            expected_value = expected.get("value")
            if field in expected_by_field and not cls._same_value(
                expected_by_field[field], expected_value
            ):
                raise ValueError(f"输入中字段 {field} 有互相冲突的值")
            expected_by_field[field] = expected_value
            if field not in parameters:
                raise ValueError(
                    f"baseline_parameters 缺少输入 {item_id} 对应字段 {field}"
                )
            if not cls._same_value(parameters.get(field), expected_value):
                raise ValueError(
                    f"baseline_parameters.{field} 与输入 {item_id} 的 value 不一致"
                )
        missing = value.get("missing_concepts")
        if not isinstance(missing, list):
            raise ValueError("missing_concepts 必须是数组")
        return value

    def run(
        self,
        interpretation: Mapping[str, object],
        runtime_profile: Mapping[str, object],
    ) -> dict[str, Any]:
        config = load_agent_prompt_dict("baseline_builder_v2")
        system_prompt = str(config.get("system_prompt", "")).strip()
        user_template = str(config.get("user_prompt", "")).strip()
        user_prompt = Template(user_template).render(
            interpretation_json=json.dumps(
                interpretation, ensure_ascii=False, indent=2, sort_keys=True
            ),
            runtime_profile_json=json.dumps(
                runtime_profile, ensure_ascii=False, indent=2, sort_keys=True
            ),
        ).strip()

        last_error: Exception | None = None
        repair = ""
        for _attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.complete(  # type: ignore[attr-defined]
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + repair,
                    temperature=0.0,
                    max_tokens=7000,
                    role="baseline_builder_v2",
                    reasoning_effort="high",
                    multi_agent=False,
                )
                return self._validate(
                    self._extract_json(str(response)),
                    interpretation,
                    runtime_profile,
                )
            except Exception as exc:
                last_error = exc
                repair = (
                    "\n\n上一次输出未通过检查，请重新输出完整 JSON，不要解释。"
                    f"\n检查结果：{exc}"
                )
        raise AgentExecutionError(
            self.name,
            self.max_retries,
            str(last_error or "无法生成基准规则"),
            cause=last_error,
        )
