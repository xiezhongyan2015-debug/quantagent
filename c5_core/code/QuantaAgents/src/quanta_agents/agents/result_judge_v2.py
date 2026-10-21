from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from jinja2 import Template

from quanta_agents.config import get_agent_max_retries
from quanta_agents.exceptions import AgentExecutionError
from quanta_agents.llm import llm_client
from quanta_agents.prompt_loader import load_agent_prompt_dict


class ResultJudgeV2:
    """先做固定数值比较，再让模型解释研究结果。"""

    name = "ResultJudgeV2"
    evidence_results = {"supported", "mixed", "rejected", "unknown"}
    sample_assessments = {"adequate", "inadequate", "unknown"}
    required_model_keys = {
        "calculation_valid",
        "sample_adequacy",
        "evidence_result",
        "adopted",
        "observed_deltas",
        "mechanism_updates",
        "next_action",
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
    def _finite_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @classmethod
    def _metric(cls, report: Mapping[str, object], key: str) -> float | None:
        return cls._finite_number(report.get(key))

    @classmethod
    def _fold_count(cls, report: Mapping[str, object]) -> int | None:
        raw_count = cls._finite_number(report.get("fold_count"))
        if raw_count is not None and raw_count >= 0 and raw_count.is_integer():
            return int(raw_count)
        folds = report.get("folds")
        if isinstance(folds, list):
            return len(folds)
        return None

    @staticmethod
    def _boolean_setting(
        sample_info: Mapping[str, object],
        key: str,
        default: bool,
    ) -> bool:
        value = sample_info.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return default

    @classmethod
    def compare_reports(
        cls,
        baseline_report: Mapping[str, object],
        candidate_report: Mapping[str, object],
        sample_info: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """用固定规则比较两个完整开发期报告。"""

        sample_info = dict(sample_info or {})
        metric_keys = (
            "median_sharpe",
            "worst_fold_sharpe",
            "median_annual_return",
            "worst_fold_drawdown",
            "total_trade_count",
            "cost_stress_median_sharpe",
            "delay_stress_median_sharpe",
        )
        baseline_values = {
            key: cls._metric(baseline_report, key) for key in metric_keys
        }
        candidate_values = {
            key: cls._metric(candidate_report, key) for key in metric_keys
        }

        required_keys = list(metric_keys[:5])
        require_cost = cls._boolean_setting(sample_info, "require_cost_stress", True)
        require_delay = cls._boolean_setting(sample_info, "require_delay_stress", True)
        if require_cost:
            required_keys.append("cost_stress_median_sharpe")
        if require_delay:
            required_keys.append("delay_stress_median_sharpe")

        missing_metrics = [
            f"baseline.{key}"
            for key in required_keys
            if baseline_values[key] is None
        ]
        missing_metrics.extend(
            f"candidate.{key}"
            for key in required_keys
            if candidate_values[key] is None
        )
        period_kind = candidate_report.get("period_kind")
        period_is_development = period_kind in {None, "", "development"}
        reported_calculation_valid = sample_info.get(
            "candidate_calculation_valid",
            sample_info.get("calculation_valid", True),
        )
        calculation_valid = (
            not missing_metrics
            and period_is_development
            and reported_calculation_valid is True
        )

        deltas: dict[str, float | None] = {}
        for key in (
            "median_sharpe",
            "worst_fold_sharpe",
            "median_annual_return",
            "total_trade_count",
            "cost_stress_median_sharpe",
            "delay_stress_median_sharpe",
        ):
            baseline_value = baseline_values[key]
            candidate_value = candidate_values[key]
            deltas[f"{key}_delta"] = (
                candidate_value - baseline_value
                if baseline_value is not None and candidate_value is not None
                else None
            )

        baseline_drawdown = baseline_values["worst_fold_drawdown"]
        candidate_drawdown = candidate_values["worst_fold_drawdown"]
        deltas["worst_fold_drawdown_abs_delta"] = (
            abs(candidate_drawdown) - abs(baseline_drawdown)
            if baseline_drawdown is not None and candidate_drawdown is not None
            else None
        )

        candidate_fold_count = cls._fold_count(candidate_report)
        try:
            min_fold_count = max(int(sample_info.get("min_fold_count", 3)), 1)
        except (TypeError, ValueError):
            min_fold_count = 3
        try:
            min_trade_count = max(int(sample_info.get("min_trade_count", 1)), 1)
        except (TypeError, ValueError):
            min_trade_count = 1
        candidate_trades = candidate_values["total_trade_count"]
        sample_adequate = (
            candidate_fold_count is not None
            and candidate_fold_count >= min_fold_count
            and candidate_trades is not None
            and candidate_trades >= min_trade_count
        )

        cost_complete = (
            not require_cost
            or (
                candidate_report.get("cost_stress_complete") is True
                and candidate_values["cost_stress_median_sharpe"] is not None
            )
        )
        delay_complete = (
            not require_delay
            or (
                candidate_report.get("delay_stress_complete") is True
                and candidate_values["delay_stress_median_sharpe"] is not None
            )
        )
        complete_development_report = (
            calculation_valid and sample_adequate and cost_complete and delay_complete
        )

        def setting(name: str, default: float) -> float:
            value = cls._finite_number(sample_info.get(name))
            return default if value is None else value

        median_gain = deltas["median_sharpe_delta"]
        worst_gain = deltas["worst_fold_sharpe_delta"]
        annual_gain = deltas["median_annual_return_delta"]
        drawdown_change = deltas["worst_fold_drawdown_abs_delta"]
        cost_gain = deltas["cost_stress_median_sharpe_delta"]
        delay_gain = deltas["delay_stress_median_sharpe_delta"]

        meaningful_gain = bool(
            calculation_valid
            and (
                (median_gain is not None and median_gain >= setting("min_relative_sharpe_gain", 0.10))
                or (worst_gain is not None and worst_gain >= setting("min_worst_sharpe_gain", 0.20))
                or (annual_gain is not None and annual_gain >= setting("min_annual_return_gain", 0.01))
                or (drawdown_change is not None and drawdown_change <= -setting("min_drawdown_improvement", 0.02))
            )
        )
        no_material_harm = bool(
            calculation_valid
            and worst_gain is not None
            and worst_gain >= -setting("max_worst_sharpe_loss", 0.10)
            and annual_gain is not None
            and annual_gain >= -setting("max_annual_return_loss", 0.01)
            and drawdown_change is not None
            and drawdown_change <= setting("max_drawdown_worsening", 0.02)
            and (not require_cost or (cost_gain is not None and cost_gain >= -setting("max_cost_stress_loss", 0.10)))
            and (not require_delay or (delay_gain is not None and delay_gain >= -setting("max_delay_stress_loss", 0.10)))
        )

        baseline_trades = baseline_values["total_trade_count"]
        minimum_trade_fraction = setting("min_baseline_trade_fraction", 0.50)
        trade_count_preserved = bool(
            candidate_trades is not None
            and (
                baseline_trades is None
                or baseline_trades <= 0
                or candidate_trades >= baseline_trades * minimum_trade_fraction
            )
        )
        relative_improvement = bool(
            complete_development_report
            and meaningful_gain
            and no_material_harm
            and trade_count_preserved
        )

        candidate_passed = candidate_report.get("passed") is True
        stable_candidate = bool(
            complete_development_report
            and candidate_passed
            and (not require_cost or candidate_report.get("cost_stress_passed") is True)
            and (not require_delay or candidate_report.get("delay_stress_passed") is True)
        )
        baseline_passed = baseline_report.get("passed") is True
        program_adoption_eligible = bool(
            relative_improvement
            or (stable_candidate and not baseline_passed)
        )

        reasons: list[str] = []
        if missing_metrics:
            reasons.append("缺少固定比较所需指标")
        if not period_is_development:
            reasons.append("候选报告不是开发期报告")
        if reported_calculation_valid is not True:
            reasons.append("候选计算没有通过固定检查")
        if not sample_adequate:
            reasons.append("样本数量不足")
        if not cost_complete:
            reasons.append("费用加倍结果不完整")
        if not delay_complete:
            reasons.append("推迟一日结果不完整")
        if complete_development_report and not meaningful_gain:
            reasons.append("没有达到相对改善幅度")
        if complete_development_report and not no_material_harm:
            reasons.append("至少一个关键指标明显变差")
        if complete_development_report and not trade_count_preserved:
            reasons.append("交易数量下降过多")

        return {
            "baseline_metrics": baseline_values,
            "candidate_metrics": candidate_values,
            "observed_deltas": deltas,
            "missing_metrics": missing_metrics,
            "calculation_valid": calculation_valid,
            "candidate_fold_count": candidate_fold_count,
            "minimum_fold_count": min_fold_count,
            "candidate_trade_count": candidate_trades,
            "minimum_trade_count": min_trade_count,
            "sample_adequate": sample_adequate,
            "cost_stress_required": require_cost,
            "delay_stress_required": require_delay,
            "complete_development_report": complete_development_report,
            "meaningful_gain": meaningful_gain,
            "no_material_harm": no_material_harm,
            "trade_count_preserved": trade_count_preserved,
            "relative_improvement": relative_improvement,
            "baseline_passed": baseline_passed,
            "candidate_passed": candidate_passed,
            "stable_candidate": stable_candidate,
            "program_adoption_eligible": program_adoption_eligible,
            "reasons": reasons,
        }

    @classmethod
    def _validate_model_output(cls, parsed: dict[str, Any]) -> dict[str, Any]:
        actual_keys = set(parsed)
        if actual_keys != cls.required_model_keys:
            missing = sorted(cls.required_model_keys - actual_keys)
            extra = sorted(actual_keys - cls.required_model_keys)
            raise ValueError(f"模型字段不符合要求；缺少={missing}，多出={extra}")
        if not isinstance(parsed.get("calculation_valid"), bool):
            raise ValueError("calculation_valid 必须是布尔值")
        if parsed.get("sample_adequacy") not in cls.sample_assessments:
            raise ValueError("sample_adequacy 值不符合要求")
        if parsed.get("evidence_result") not in cls.evidence_results:
            raise ValueError("evidence_result 值不符合要求")
        if not isinstance(parsed.get("adopted"), bool):
            raise ValueError("adopted 必须是布尔值")
        if not isinstance(parsed.get("observed_deltas"), dict):
            raise ValueError("observed_deltas 必须是对象")
        updates = parsed.get("mechanism_updates")
        if not isinstance(updates, list) or any(
            not isinstance(item, dict) for item in updates
        ):
            raise ValueError("mechanism_updates 必须是对象数组")
        if not str(parsed.get("next_action", "")).strip():
            raise ValueError("next_action 不能为空")
        return dict(parsed)

    @staticmethod
    def _render(template_text: str, values: Mapping[str, object]) -> str:
        return Template(template_text).render(**values).strip()

    def run(
        self,
        baseline_report: Mapping[str, object],
        candidate_report: Mapping[str, object],
        prior_prediction: Mapping[str, object] | list[object] | str,
        sample_info: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        fixed = self.compare_reports(
            baseline_report,
            candidate_report,
            sample_info,
        )
        prompt_config = load_agent_prompt_dict("result_judge_v2")
        system_prompt = str(prompt_config.get("system_prompt", "")).strip()
        user_template = str(prompt_config.get("user_prompt", "")).strip()
        if not system_prompt or not user_template:
            raise ValueError("result_judge_v2 提示词缺少内容")
        user_prompt = self._render(
            user_template,
            {
                "baseline_report_json": json.dumps(
                    dict(baseline_report), ensure_ascii=False, indent=2, default=str
                ),
                "candidate_report_json": json.dumps(
                    dict(candidate_report), ensure_ascii=False, indent=2, default=str
                ),
                "prior_prediction_json": json.dumps(
                    prior_prediction, ensure_ascii=False, indent=2, default=str
                ),
                "sample_info_json": json.dumps(
                    dict(sample_info or {}), ensure_ascii=False, indent=2, default=str
                ),
                "fixed_comparison_json": json.dumps(
                    fixed, ensure_ascii=False, indent=2, default=str
                ),
            },
        )

        last_error: Exception | None = None
        retry_note = ""
        model_result: dict[str, Any] | None = None
        for _attempt in range(1, self.max_retries + 1):
            try:
                text = self.client.complete(  # type: ignore[attr-defined]
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + retry_note,
                    temperature=0.0,
                    max_tokens=3000,
                    role="result_judge_v2",
                    reasoning_effort="high",
                    multi_agent=False,
                )
                model_result = self._validate_model_output(
                    self._extract_json_object(str(text))
                )
                break
            except Exception as exc:
                last_error = exc
                retry_note = (
                    "\n\n上一次输出没有通过检查。请只重新输出完整 JSON。"
                    f"\n检查结果：{exc}"
                )

        if model_result is None:
            raise AgentExecutionError(
                self.name,
                self.max_retries,
                str(last_error or "无法判断结果"),
                cause=last_error,
            )

        final_calculation_valid = bool(
            fixed["calculation_valid"] and model_result["calculation_valid"]
        )
        model_sample_assessment = str(model_result["sample_adequacy"])
        model_sample_adequate = model_sample_assessment == "adequate"
        final_sample_adequate = bool(
            fixed["sample_adequate"] and model_sample_adequate
        )
        final_sample_assessment = (
            "inadequate"
            if not fixed["sample_adequate"]
            else model_sample_assessment
        )
        evidence_result = str(model_result["evidence_result"])
        if not final_calculation_valid or not final_sample_adequate:
            evidence_result = "unknown"

        adopted = bool(
            fixed["program_adoption_eligible"]
            and final_calculation_valid
            and final_sample_adequate
            and model_result["adopted"]
            and evidence_result in {"supported", "mixed"}
        )
        stable_strategy = bool(
            fixed["stable_candidate"]
            and final_calculation_valid
            and final_sample_adequate
        )
        adoption_kind = (
            "stable_strategy"
            if adopted and stable_strategy
            else "relative_improvement"
            if adopted
            else "not_adopted"
        )

        return {
            "calculation_valid": final_calculation_valid,
            "sample_adequacy": final_sample_assessment,
            "evidence_result": evidence_result,
            "adopted": adopted,
            "adoption_kind": adoption_kind,
            "stable_strategy": stable_strategy,
            "observed_deltas": dict(fixed["observed_deltas"]),
            "mechanism_updates": list(model_result["mechanism_updates"]),
            "next_action": str(model_result["next_action"]).strip(),
            "fixed_comparison": fixed,
            "model_assessment": model_result,
        }
