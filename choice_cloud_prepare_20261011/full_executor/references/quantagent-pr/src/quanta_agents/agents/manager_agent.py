from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import shutil
from pathlib import Path

from quanta_agents.state import (
    WorkflowState,
    get_hypothesis,
    period_starts_after,
    periods_overlap,
    resolve_development_period,
    resolve_final_test_period,
    resolve_quality_gate,
    select_event_success_value,
)
from quanta_agents.trace_logger import print_agent_progress, write_trace_json


class ManagerAgent:
    name = "ManagerAgent"

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = value.strip().lower()
        normalized = re.sub(r"[^a-z0-9_\-]+", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized)
        return normalized.strip("_") or "unknown_experiment"

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _resolve_source_yaml(cls, state: WorkflowState, *, project_root: Path | None = None) -> Path | None:
        root = project_root or cls._project_root()
        experiments_dir = root / "experiments"
        if not experiments_dir.exists() or not experiments_dir.is_dir():
            return None

        spec = state.get("experiment_spec", {})
        if not isinstance(spec, dict):
            return None

        source_file = spec.get("source_file")
        if isinstance(source_file, str) and source_file.strip():
            candidate = experiments_dir / source_file
            if candidate.exists() and candidate.is_file():
                return candidate

        experiment_id = str(spec.get("experiment_id", "")).strip()
        if not experiment_id:
            return None

        candidates = sorted(
            [
                path
                for path in experiments_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
            ],
            key=lambda path: len(path.stem),
            reverse=True,
        )

        for path in candidates:
            stem = path.stem.replace(" ", "_")
            if experiment_id.startswith(f"exp_{stem}_"):
                return path

        return None

    @classmethod
    def _find_latest_file(cls, directory: Path, suffix: str) -> Path | None:
        if not directory.exists() or not directory.is_dir():
            return None

        matches = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == suffix.lower()
        ]
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)

    @classmethod
    def _archive_experiment_artifacts(cls, state: WorkflowState, *, project_root: Path | None = None) -> Path | None:
        root = project_root or cls._project_root()
        spec = state.get("experiment_spec", {})
        if not isinstance(spec, dict):
            return None

        experiment_id = str(spec.get("experiment_id", "")).strip()
        if not experiment_id:
            return None

        archive_root = root / "best_experiments"
        archive_dir = archive_root / cls._slugify(experiment_id)
        archive_dir.mkdir(parents=True, exist_ok=True)

        source_yaml = cls._resolve_source_yaml(state, project_root=root)
        if source_yaml is not None:
            shutil.copy2(source_yaml, archive_dir / source_yaml.name)

        max_epochs = int(state.get("max_epochs", 1))
        epoch_index = int(state.get("epoch_index", 1))
        round_index = max(int(state.get("strategy_validate_round", 1)), 1)
        trace_root = root / "experiment_traces" / cls._slugify(experiment_id) / f"epochs_{max_epochs}"
        strategy_dir = trace_root / f"epoch_{epoch_index:03d}" / "strategyagent" / f"round_{round_index:03d}"
        structured_output_dir = strategy_dir / "structured_output"
        backtest_outputs_dir = trace_root / f"epoch_{epoch_index:03d}" / "strategytester" / "backtest_outputs"

        structured_output_json = cls._find_latest_file(structured_output_dir, ".json")
        strategy_code_py = cls._find_latest_file(strategy_dir, ".py")

        if structured_output_json is not None:
            shutil.copy2(structured_output_json, archive_dir / structured_output_json.name)
        if strategy_code_py is not None:
            shutil.copy2(strategy_code_py, archive_dir / strategy_code_py.name)
        if backtest_outputs_dir.exists() and backtest_outputs_dir.is_dir():
            shutil.copytree(backtest_outputs_dir, archive_dir / "backtest_outputs", dirs_exist_ok=True)

        return archive_dir

    @staticmethod
    def _build_backtest_feedback(result) -> str:
        trade_count_text = str(result.trade_count) if isinstance(result.trade_count, int) else "N/A"
        win_rate_text = f"{result.win_rate:.2%}" if isinstance(result.win_rate, float) else "N/A"
        profit_loss_ratio_text = (
            f"{result.profit_loss_ratio:.2f}"
            if isinstance(result.profit_loss_ratio, float)
            else "N/A"
        )
        max_ddpercent = getattr(result, "max_ddpercent", None)
        max_ddpercent_text = f"{max_ddpercent:.2%}" if isinstance(max_ddpercent, float) else "N/A"
        return (
            "backtest_metrics: "
            f"annual_return={result.annual_return:.2%}, "
            f"max_drawdown_amount={result.max_drawdown:.2f}, "
            f"max_ddpercent={max_ddpercent_text}, "
            f"sharpe={result.sharpe:.2f}, "
            f"trade_count={trade_count_text}, "
            f"win_rate={win_rate_text}, "
            f"profit_loss_ratio={profit_loss_ratio_text}; "
            f"detail={result.summary}"
        )

    @staticmethod
    def _is_explicit_true(value: object) -> bool:
        if value is True:
            return True
        return isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"}

    @classmethod
    def _final_test_allowed(cls, state: WorkflowState) -> bool:
        spec = state.get("experiment_spec", {})
        if not isinstance(spec, dict):
            return False
        return any(
            cls._is_explicit_true(spec.get(key))
            for key in ("allow_final_test", "run_final_test")
        )

    @staticmethod
    def _result_payload(result: object) -> dict[str, object]:
        return {
            "annual_return": float(getattr(result, "annual_return", 0.0)),
            "sharpe": float(getattr(result, "sharpe", 0.0)),
            "max_drawdown": float(getattr(result, "max_drawdown", 0.0)),
            "max_ddpercent": getattr(result, "max_ddpercent", None),
            "trade_count": getattr(result, "trade_count", None),
            "win_rate": getattr(result, "win_rate", None),
            "profit_loss_ratio": getattr(result, "profit_loss_ratio", None),
            "event_net_mean_return": getattr(result, "event_net_mean_return", None),
            "event_gross_up_rate": getattr(result, "event_gross_up_rate", None),
            "event_joint_minute_hit_rate": getattr(
                result, "event_joint_minute_hit_rate", None
            ),
            "event_success_rate": getattr(result, "event_success_rate", None),
            "event_success_column": getattr(result, "event_success_column", None),
            "event_success_metric": getattr(result, "event_success_metric", None),
            "event_success_metric_value": getattr(
                result, "event_success_metric_value", None
            ),
            "passed": bool(getattr(result, "passed", False)),
            "summary": str(getattr(result, "summary", "")),
        }

    @staticmethod
    def _period_for_kind(state: WorkflowState, period_kind: str) -> dict[str, str]:
        state_key = "final_test_period" if period_kind == "final_test" else "development_period"
        stored = state.get(state_key, {})
        if isinstance(stored, dict):
            start = stored.get("start")
            end = stored.get("end")
            if isinstance(start, str) and isinstance(end, str) and start and end:
                return {"start": start, "end": end}

        spec = state.get("experiment_spec", {})
        if period_kind == "final_test":
            return resolve_final_test_period(spec if isinstance(spec, dict) else {})
        return resolve_development_period(spec if isinstance(spec, dict) else {})

    @classmethod
    def _record_experiment(
        cls,
        state: WorkflowState,
        *,
        period_kind: str,
        result: object | None,
        error: str = "",
    ) -> None:
        seen_period = cls._period_for_kind(state, period_kind)
        candidate_id = str(
            state.get("current_candidate_id")
            or f"candidate_{int(state.get('epoch_index', 1)):03d}"
        )
        result_payload = cls._result_payload(result) if result is not None else {}

        seen_entry: dict[str, object] = {
            "period_kind": period_kind,
            "seen_period": dict(seen_period),
        }
        seen_periods = state.setdefault("seen_periods", [])
        if seen_entry not in seen_periods:
            seen_periods.append(seen_entry)

        hypothesis_meta = state.get("hypothesis_generation_meta", {})
        strategy_modification = (
            str(hypothesis_meta.get("strategy_modification", ""))
            if isinstance(hypothesis_meta, dict)
            else ""
        )
        candidate_mode = (
            str(hypothesis_meta.get("candidate_mode", "")).strip()
            if isinstance(hypothesis_meta, dict)
            else ""
        )
        strategy_meta = state.get("strategy_generation_meta", {})
        if not isinstance(strategy_meta, dict):
            strategy_meta = {}
        strategy_code = str(state.get("strategy_code", ""))
        strategy_code_sha256 = str(strategy_meta.get("strategy_code_sha256", "")).strip()
        if not strategy_code_sha256 and strategy_code:
            strategy_code_sha256 = hashlib.sha256(strategy_code.encode("utf-8")).hexdigest()
        code_diff = str(strategy_meta.get("code_diff", ""))
        code_source = str(
            strategy_meta.get("source", strategy_meta.get("previous_strategy_source", ""))
        )
        strategy_result = state.get("strategy_result", {})
        strategy_params = (
            deepcopy(strategy_result.get("params", {}))
            if isinstance(strategy_result, dict)
            and isinstance(strategy_result.get("params", {}), dict)
            else {}
        )
        candidate_record: dict[str, object] = {
            "candidate_id": candidate_id,
            "candidate_mode": candidate_mode,
            "strategy_code_sha256": strategy_code_sha256,
            "strategy_params": strategy_params,
            "code_diff": code_diff,
            "code_source": code_source,
            "epoch_index": int(state.get("epoch_index", 1)),
            "strategy_validate_round": int(state.get("strategy_validate_round", 1)),
            "hypothesis": get_hypothesis(state),
            "strategy_modification": strategy_modification,
            "period_kind": period_kind,
            "seen_period": dict(seen_period),
            "result": result_payload,
            "error": error,
        }
        for key in (
            "event_gross_up_rate",
            "event_joint_minute_hit_rate",
            "event_success_metric",
            "event_success_metric_value",
        ):
            candidate_record[key] = result_payload.get(key)
        event_horizon = strategy_params.get("event_horizon_minutes")
        if (
            isinstance(event_horizon, (int, float))
            and not isinstance(event_horizon, bool)
            and float(event_horizon).is_integer()
            and int(event_horizon) in {5, 10, 20}
        ):
            candidate_record["event_horizon_minutes"] = int(event_horizon)
        research_meta = state.get("hypothesis_generation_meta", {})
        if isinstance(research_meta, dict):
            candidate_record.update(
                {
                    "research_decision": research_meta.get("decision", "baseline"),
                    "selected_cause": research_meta.get("selected_cause", ""),
                    "expected_results": deepcopy(
                        research_meta.get("expected_results", [])
                    ),
                    "judgment_is_wrong_if": research_meta.get(
                        "judgment_is_wrong_if", ""
                    ),
                    "facts": deepcopy(research_meta.get("facts", [])),
                    "possible_causes": deepcopy(
                        research_meta.get("possible_causes", [])
                    ),
                    "previous_change_review": deepcopy(
                        research_meta.get("previous_change_review", {})
                    ),
                    "knowledge_record": deepcopy(
                        research_meta.get("knowledge_record", {})
                    ),
                    "objective_assessment": deepcopy(
                        research_meta.get("objective_assessment", {})
                    ),
                    "candidate_directions": deepcopy(
                        research_meta.get("candidate_directions", [])
                    ),
                    "selected_direction_id": research_meta.get(
                        "selected_candidate_id",
                        research_meta.get("selected_direction_id", ""),
                    ),
                    "untested_plans": deepcopy(
                        research_meta.get("untested_plans", [])
                    ),
                    "stop_eligibility": deepcopy(
                        research_meta.get("stop_eligibility", {})
                    ),
                }
            )
        if period_kind == "development":
            report = state.get("development_report", {})
            candidate_record["development_report"] = (
                deepcopy(report) if isinstance(report, dict) else {}
            )
        state.setdefault("candidate_records", []).append(candidate_record)

        experiment_records = state.setdefault("experiment_records", [])
        experiment_record = {
                "trial_id": f"trial_{len(experiment_records) + 1:04d}",
                "candidate_id": candidate_id,
                "candidate_mode": candidate_mode,
                "strategy_code_sha256": strategy_code_sha256,
                "code_diff": code_diff,
                "code_source": code_source,
                "epoch_index": int(state.get("epoch_index", 1)),
                "period_kind": period_kind,
                "seen_period": dict(seen_period),
                "result": result_payload,
                "error": error,
            }
        for key in (
            "event_gross_up_rate",
            "event_joint_minute_hit_rate",
            "event_success_metric",
            "event_success_metric_value",
        ):
            experiment_record[key] = result_payload.get(key)
        experiment_records.append(experiment_record)

        if period_kind == "final_test":
            state["final_test_count"] = int(state.get("final_test_count", 0)) + 1
        elif result is not None:
            state["research_trial_count"] = int(state.get("research_trial_count", 0)) + 1

        try:
            write_trace_json(
                state,
                agent_name=cls.name,
                stage="candidate_record",
                payload={
                    "candidate": candidate_record,
                    "experiment": experiment_record,
                    "research_trial_count": int(state.get("research_trial_count", 0)),
                    "technical_retry_count": int(state.get("technical_retry_count", 0)),
                    "final_test_count": int(state.get("final_test_count", 0)),
                },
            )
        except Exception:
            pass

    @staticmethod
    def _passes_development_gate(state: WorkflowState, result: object) -> bool:
        reported_passed = getattr(result, "passed", None)
        development_report = state.get("development_report", {})
        if (
            str(state.get("phase", "")).strip() != "final_test"
            and isinstance(development_report, dict)
            and str(development_report.get("period_kind", "development")).strip()
            == "development"
            and development_report.get("passed") is False
        ):
            return False
        gate = resolve_quality_gate(state.get("experiment_spec"))
        if gate is None:
            return reported_passed if isinstance(reported_passed, bool) else False

        passed = True
        if gate.min_sharpe is not None:
            passed = passed and float(getattr(result, "sharpe", 0.0)) >= gate.min_sharpe
        if gate.min_annual_return is not None:
            passed = passed and float(getattr(result, "annual_return", 0.0)) >= gate.min_annual_return
        if gate.max_drawdown is not None:
            max_ddpercent = getattr(result, "max_ddpercent", None)
            passed = passed and isinstance(max_ddpercent, (int, float)) and float(max_ddpercent) <= gate.max_drawdown
        if gate.min_trade_count is not None:
            trade_count = getattr(result, "trade_count", None)
            passed = passed and isinstance(trade_count, int) and trade_count >= gate.min_trade_count
        if gate.min_event_net_mean is not None:
            event_net_mean = getattr(result, "event_net_mean_return", None)
            passed = passed and isinstance(event_net_mean, (int, float)) and float(event_net_mean) >= gate.min_event_net_mean
        if gate.min_event_success_rate is not None:
            event_success_value, _ = select_event_success_value(
                gate.event_success_metric,
                legacy_rate=getattr(result, "event_success_rate", None),
                legacy_column=getattr(result, "event_success_column", None),
                gross_up_rate=getattr(result, "event_gross_up_rate", None),
                joint_minute_hit_rate=getattr(
                    result, "event_joint_minute_hit_rate", None
                ),
            )
            passed = passed and isinstance(
                event_success_value, (int, float)
            ) and float(event_success_value) >= gate.min_event_success_rate
        return passed

    def __init__(self) -> None:
        pass

    def dispatch(self, state: WorkflowState) -> WorkflowState:
        phase = state["phase"]
        print_agent_progress(state, agent_name=self.name, message=f"dispatch phase={phase}")

        if phase == "done":
            if not str(state.get("manager_notes", "")).strip():
                state["manager_notes"] = "Workflow completed."
            return state

        test_phases = {"test", "backtest", "final_test"}
        period_kind = "final_test" if phase == "final_test" else "development"

        if phase in test_phases and state["test_result"] is None:
            final_error = str(state.get("last_test_error", "")).strip()
            if period_kind == "final_test" and final_error:
                self._record_experiment(
                    state,
                    period_kind="final_test",
                    result=None,
                    error=final_error,
                )
                state["backtest_feedback"] = ""
                state["manager_notes"] = (
                    f"Final test could not complete: {final_error}. The result was not fed back."
                )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: ManagerAgent stopped after final-test execution error"
                )
                state["phase"] = "done"
                return state

            state["history"].append(
                f"Epoch {state['epoch_index']}: ManagerAgent routed to StrategyTester ({period_kind}, test_result is empty)"
            )
            state["manager_notes"] = f"Waiting for StrategyTester ({period_kind})."
            return state

        if phase in test_phases and state["test_result"] is not None:
            result = state["test_result"]
            self._record_experiment(state, period_kind=period_kind, result=result)

            if period_kind == "final_test":
                state["final_test_result"] = result
                state["backtest_feedback"] = ""
                state["validation_feedback"] = {}
                state["last_test_error"] = ""
                final_passed = self._passes_development_gate(state, result)
                state["final_quality_passed"] = final_passed
                if final_passed:
                    try:
                        self._archive_experiment_artifacts(state)
                    except Exception:
                        pass
                outcome = "passed" if final_passed else "did not pass"
                state["manager_notes"] = (
                    f"Final test {outcome}. Workflow completed without feeding the final result back."
                )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: ManagerAgent recorded final test and completed workflow"
                )
                state["phase"] = "done"
                return state

            gate = resolve_quality_gate(state.get("experiment_spec"))
            gate_passed = self._passes_development_gate(state, result)
            state["quality_passed"] = gate_passed

            if gate_passed:
                try:
                    self._archive_experiment_artifacts(state)
                except Exception:
                    pass
                state["candidate_frozen"] = True
                state["backtest_feedback"] = ""
                state["validation_feedback"] = {}
                state["last_test_error"] = ""

                development_period = self._period_for_kind(state, "development")
                final_period = self._period_for_kind(state, "final_test")
                if not final_period:
                    state["ready_for_final"] = False
                    state["awaiting_final_test_approval"] = False
                    state["manager_notes"] = (
                        "Development backtest passed and candidate was frozen, but no separate final period is configured."
                    )
                    state["history"].append(
                        f"Epoch {state['epoch_index']}: ManagerAgent found no locked final period"
                    )
                    state["phase"] = "done"
                    return state

                if (
                    not development_period
                    or periods_overlap(development_period, final_period)
                    or not period_starts_after(development_period, final_period)
                ):
                    state["ready_for_final"] = False
                    state["awaiting_final_test_approval"] = False
                    state["manager_notes"] = (
                        "Development backtest passed, but development and final periods overlap, are reversed, or cannot be verified. Final test was refused."
                    )
                    state["history"].append(
                        f"Epoch {state['epoch_index']}: ManagerAgent refused overlapping development/final periods"
                    )
                    state["phase"] = "done"
                    return state

                state["ready_for_final"] = True

                if not self._final_test_allowed(state):
                    state["awaiting_final_test_approval"] = True
                    state["manager_notes"] = (
                        "Development backtest passed. Candidate frozen; waiting for explicit final-test approval."
                    )
                    state["history"].append(
                        f"Epoch {state['epoch_index']}: ManagerAgent froze candidate and did not run final test without approval"
                    )
                    state["phase"] = "done"
                    return state

                state["awaiting_final_test_approval"] = False
                state["evaluation_stage"] = "final_test"
                state["test_result"] = None
                state["phase"] = "final_test"
                state["manager_notes"] = "Candidate frozen. Dispatching the approved final test."
                state["history"].append(
                    f"Epoch {state['epoch_index']}: ManagerAgent scheduled one approved final test"
                )
                return state

            max_epochs = int(state.get("max_epochs", 1))
            current_epoch = int(state.get("epoch_index", 1))
            if current_epoch >= max_epochs:
                if gate is None:
                    state["manager_notes"] = (
                        f"Backtest completed without an evaluation gate and reached max epochs ({max_epochs})."
                    )
                else:
                    state["manager_notes"] = (
                        f"Backtest did not meet evaluation before max epochs ({max_epochs})."
                    )
                state["history"].append(
                    f"Epoch {state['epoch_index']}: ManagerAgent stopped due to max_epochs"
                )
                state["phase"] = "done"
                return state

            state["backtest_feedback"] = self._build_backtest_feedback(result)
            state["validation_result"] = {}
            state["validation_summary"] = {}
            state["validation_feedback"] = {}
            # Clear consumed backtest result so the next phase=test dispatch runs StrategyTester again.
            state["test_result"] = None
            state["history"].append(
                f"Epoch {state['epoch_index']}: ManagerAgent requested new strategy epoch {current_epoch + 1}/{max_epochs}"
            )
            state["epoch_index"] = current_epoch + 1
            state["current_candidate_id"] = f"candidate_{current_epoch + 1:03d}"
            state["strategy_validate_round"] = 1
            state["evaluation_stage"] = "development"
            state["ready_for_final"] = False
            state["candidate_frozen"] = False
            state["final_quality_passed"] = False
            state["diagnostic_request"] = {}
            state["diagnostic_report"] = {}
            state["diagnostic_round"] = 0
            state["research_stage"] = "iteration"
            # Route back to hypothesis for backtest-driven refinement
            state["phase"] = "hypothesis"
            return state

        state["manager_notes"] = f"Dispatching phase={phase}"
        return state

