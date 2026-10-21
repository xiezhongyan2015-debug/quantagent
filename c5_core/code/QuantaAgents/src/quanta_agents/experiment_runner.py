from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from uuid import uuid4

import yaml

from quanta_agents.config import get_max_epochs
from quanta_agents.exceptions import AgentExecutionError
from quanta_agents.semantic import get_universe_list
from quanta_agents.state import EVENT_SUCCESS_METRICS, WorkflowState
from quanta_agents.workflow import run_workflow


_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PASSTHROUGH_KEYS = {
    "experiment_id",
    "experiment_name",
    "bar_interval",
    "backtest_mode",
    "event_horizon_minutes",
    "event_cooldown_minutes",
    "commission_rate",
    "buy_cost",
    "sell_cost",
    "sell_cost_before_change",
    "sell_cost_change_date",
    "slippage",
    "lot_size",
    "capital",
    "allow_final_test",
    "run_final_test",
    "development_start",
    "development_end",
    "final_test_start",
    "final_test_end",
    "holdout_start",
    "holdout_end",
    "development_folds",
    "development_fold_count",
    "gap_trading_days",
    "min_development_fold_days",
    "run_cost_stress",
    "run_delay_stress",
    "walk_forward_development",
    "require_all_folds_beat_cash",
    "min_fold_pass_ratio",
    "min_worst_fold_sharpe",
    "min_cost_stress_sharpe",
    "min_delay_stress_sharpe",
    "min_trials_for_deflated_sharpe",
    "min_deflated_sharpe_probability",
    "max_single_change_ratio",
    "initial_research_before_strategy",
    "max_diagnostic_rounds",
    "min_formal_candidates_before_stop",
    "research_selection_start",
    "research_selection_end",
    "research_confirmation_start",
    "research_confirmation_end",
    "stop_required_diagnostics",
}

_BOOLEAN_RUNTIME_KEYS = {
    "allow_final_test",
    "run_final_test",
    "run_cost_stress",
    "run_delay_stress",
    "walk_forward_development",
    "require_all_folds_beat_cash",
    "initial_research_before_strategy",
}
_INTEGER_RUNTIME_LIMITS = {
    "development_folds": (1, 12),
    "development_fold_count": (1, 12),
    "gap_trading_days": (0, 252),
    "min_development_fold_days": (1, 252),
    "min_trials_for_deflated_sharpe": (1, 10_000),
    "max_diagnostic_rounds": (1, 10),
    "min_formal_candidates_before_stop": (1, 100),
}
_RATIO_RUNTIME_KEYS = {
    "min_fold_pass_ratio",
    "min_deflated_sharpe_probability",
    "max_single_change_ratio",
}
_RUNTIME_ENV_KEYS = {
    "QUANTA_RUN_FINAL_TEST": "run_final_test",
    "QUANTA_ALLOW_FINAL_TEST": "allow_final_test",
    "QUANTA_DEVELOPMENT_FOLDS": "development_folds",
    "QUANTA_GAP_TRADING_DAYS": "gap_trading_days",
    "QUANTA_RUN_COST_STRESS": "run_cost_stress",
    "QUANTA_RUN_DELAY_STRESS": "run_delay_stress",
    "QUANTA_MIN_FOLD_PASS_RATIO": "min_fold_pass_ratio",
}


def _is_truthy_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_boolean(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _parse_bounded_integer(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        parsed = int(value.strip())
    else:
        return None
    if not minimum <= parsed <= maximum:
        return None
    return parsed


def _parse_ratio(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= parsed <= 1.0:
        return None
    return parsed


def _parse_runtime_options(data: dict[str, object]) -> dict[str, object] | None:
    options: dict[str, object] = {}
    for key in _PASSTHROUGH_KEYS:
        if key not in data:
            continue
        value = data[key]
        if key in _BOOLEAN_RUNTIME_KEYS:
            parsed_boolean = _parse_boolean(value)
            if parsed_boolean is None:
                return None
            options[key] = parsed_boolean
            continue
        if key in _INTEGER_RUNTIME_LIMITS:
            minimum, maximum = _INTEGER_RUNTIME_LIMITS[key]
            parsed_integer = _parse_bounded_integer(value, minimum, maximum)
            if parsed_integer is None:
                return None
            options[key] = parsed_integer
            continue
        if key in _RATIO_RUNTIME_KEYS:
            parsed_ratio = _parse_ratio(value)
            if parsed_ratio is None:
                return None
            options[key] = parsed_ratio
            continue
        options[key] = value
    return options


def _apply_runtime_env_overrides(experiment_spec: dict[str, object]) -> None:
    for env_name, option_name in _RUNTIME_ENV_KEYS.items():
        raw_value = os.getenv(env_name)
        if raw_value is None:
            continue
        parsed = _parse_runtime_options({option_name: raw_value})
        if parsed is None:
            raise ValueError(f"Invalid {env_name} value: {raw_value!r}")
        experiment_spec.update(parsed)

    # Reading the final period is opt-in. These defaults also protect direct
    # callers that do not go through YAML parsing.
    experiment_spec.setdefault("run_final_test", False)
    experiment_spec.setdefault("allow_final_test", False)


def _final_test_requested(experiment_spec: dict[str, object]) -> bool:
    return any(
        _parse_boolean(experiment_spec.get(key)) is True
        for key in ("run_final_test", "allow_final_test")
    )


def _result_passed(result: object) -> bool:
    if isinstance(result, dict):
        return result.get("passed") is True
    return getattr(result, "passed", None) is True


@dataclass
class ExperimentOutcome:
    """One experiment result.

    ``success`` means the requested research stage completed successfully. In
    development-only runs this means a candidate was frozen and is ready for
    the final test. When the final test was requested, it means that final test
    exists and passed.
    """

    source_file: str
    idea: str
    success: bool
    final_state: WorkflowState | None = None
    error: str = ""
    ready_for_final: bool = False
    quality_passed: bool = False
    final_test_run: bool = False


@dataclass
class ExperimentSpec:
    user_idea: str
    train_start: str
    train_end: str
    validate_start: str
    validate_end: str
    backtest_start: str
    backtest_end: str
    universe: list[dict[str, object]] | None = None
    symbols: list[str] | None = None
    evaluation: dict[str, object] | None = None
    runtime_options: dict[str, object] | None = None


def _parse_evaluation_config(data: dict[str, object]) -> dict[str, object] | None:
    evaluation = data.get("evaluation")
    if evaluation is None:
        return None
    if not isinstance(evaluation, dict):
        return None

    allowed_keys = {
        "min_sharpe_ratio",
        "max_drawdown",
        "min_return_rate",
        "min_trade_count",
        "min_event_net_mean",
        "min_event_success_rate",
    }
    parsed: dict[str, object] = {}
    for key in allowed_keys:
        value = evaluation.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed[key] = float(value)
        elif isinstance(value, str) and value.strip():
            try:
                parsed[key] = float(value.strip())
            except ValueError:
                continue

    raw_success_metric = evaluation.get("event_success_metric")
    if raw_success_metric is not None:
        if not isinstance(raw_success_metric, str):
            raise ValueError("evaluation.event_success_metric 必须是字符串")
        success_metric = raw_success_metric.strip()
        if success_metric not in EVENT_SUCCESS_METRICS:
            raise ValueError("evaluation.event_success_metric 值无效")
        parsed["event_success_metric"] = success_metric

    success_rate = parsed.get("min_event_success_rate")
    if isinstance(success_rate, (int, float)) and not 0.0 <= float(success_rate) <= 1.0:
        raise ValueError("evaluation.min_event_success_rate 必须在 0 到 1 之间")

    return parsed or None


def _normalize_symbol_list(value: object) -> list[str] | None:
    if isinstance(value, str):
        name = value.strip()
        if not name:
            return None
        catalog_symbols = get_universe_list(name)
        return catalog_symbols or None

    if isinstance(value, list):
        symbols: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return None
            symbol = item.strip()
            if not symbol:
                return None
            symbols.append(symbol)
        return symbols or None

    return None


def _parse_universe_entries(data: dict[str, object]) -> tuple[list[dict[str, object]] | None, list[str] | None]:
    universe = data.get("universe")
    if not isinstance(universe, list) or not universe:
        return None, None

    normalized_entries: list[dict[str, object]] = []
    flattened_symbols: list[str] = []
    seen_symbols: set[str] = set()

    for item in universe:
        if not isinstance(item, dict):
            return None, None

        raw_symbols = item.get("symbols")
        dataset_name = ""
        if isinstance(raw_symbols, str) and raw_symbols.strip().startswith("dataset:"):
            dataset_name = raw_symbols.strip().split(":", maxsplit=1)[1].strip()
            symbols = [] if dataset_name else None
        else:
            symbols = _normalize_symbol_list(raw_symbols)
        asset = item.get("asset")
        description = item.get("description")
        if symbols is None:
            return None, None
        if not isinstance(asset, str) or not asset.strip():
            return None, None
        if not isinstance(description, str) or not description.strip():
            return None, None

        if dataset_name:
            universe_type = "dataset_defined"
            named_pool = ""
        elif isinstance(raw_symbols, str):
            universe_type = "named_pool"
            named_pool = raw_symbols.strip()
            if not named_pool:
                return None, None
        elif isinstance(raw_symbols, list):
            universe_type = "symbol_list"
            named_pool = ""
        else:
            return None, None

        normalized_entries.append(
            {
                "symbols": symbols,
                "asset": asset.strip(),
                "description": description.strip(),
                "type": universe_type,
                "named_pool": named_pool,
                "dataset": dataset_name,
            }
        )
        for symbol in symbols:
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            flattened_symbols.append(symbol)

    return normalized_entries, flattened_symbols


def _format_universe_entry(entry: dict[str, object]) -> str:
    symbols = entry.get("symbols")
    asset = str(entry.get("asset", "")).strip()
    description = str(entry.get("description", "")).strip()

    entry_type_raw = str(entry.get("type", "")).strip()
    if entry_type_raw in {"named_pool", "symbol_list", "dataset_defined"}:
        entry_type = entry_type_raw
    elif isinstance(symbols, str):
        entry_type = "named_pool"
    else:
        entry_type = "symbol_list"

    value_text = ""
    if entry_type == "named_pool":
        named_pool = str(entry.get("named_pool", "")).strip()
        value_text = named_pool
    elif entry_type == "dataset_defined":
        value_text = str(entry.get("dataset", "")).strip()
    elif isinstance(symbols, list):
        symbol_list = [str(symbol).strip() for symbol in symbols if isinstance(symbol, str) and symbol.strip()]
        value_text = "、".join(symbol_list)

    description_text = description or "未填写"
    asset_text = asset or "未填写"
    value_display = value_text or "无"

    return f"type：{entry_type}，value：{value_display}，描述：{description_text}，资产类型：{asset_text}"


def generate_scenario(spec: ExperimentSpec) -> str:
    time_parts = [
        f"训练期：{spec.train_start}到{spec.train_end}",
        f"验证期：{spec.validate_start}到{spec.validate_end}",
        f"回测期：{spec.backtest_start}到{spec.backtest_end}",
    ]
    universe_entries = spec.universe or []
    universe_parts = [f"标的池{i + 1}：{_format_universe_entry(entry)}" for i, entry in enumerate(universe_entries)]
    if universe_parts:
        return "；".join(time_parts + universe_parts)
    return "；".join(time_parts)


def _parse_experiment_spec(data: dict[str, object]) -> ExperimentSpec | None:
    required_keys = [
        "user_idea",
        "train_start",
        "train_end",
        "validate_start",
        "validate_end",
        "backtest_start",
        "backtest_end",
    ]
    for key in required_keys:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None

    date_fields = {
        "train_start": str(data["train_start"]).strip(),
        "train_end": str(data["train_end"]).strip(),
        "validate_start": str(data["validate_start"]).strip(),
        "validate_end": str(data["validate_end"]).strip(),
        "backtest_start": str(data["backtest_start"]).strip(),
        "backtest_end": str(data["backtest_end"]).strip(),
    }

    for date_value in date_fields.values():
        if not _DATE_PATTERN.fullmatch(date_value):
            return None

    train_start = date_fields["train_start"]
    train_end = date_fields["train_end"]
    validate_start = date_fields["validate_start"]
    validate_end = date_fields["validate_end"]
    backtest_start = date_fields["backtest_start"]
    backtest_end = date_fields["backtest_end"]

    # Enforce chronological order and non-overlapping train/validate/backtest windows.
    if not (train_start <= train_end <= validate_start <= validate_end <= backtest_start <= backtest_end):
        return None
    if train_end >= validate_start or validate_end >= backtest_start:
        return None

    universe, symbols = _parse_universe_entries(data)
    if universe is None or symbols is None:
        return None

    try:
        evaluation = _parse_evaluation_config(data)
    except ValueError:
        return None
    runtime_options = _parse_runtime_options(data)
    if runtime_options is None:
        return None
    return ExperimentSpec(
        user_idea=str(data["user_idea"]).strip(),
        train_start=train_start,
        train_end=train_end,
        validate_start=validate_start,
        validate_end=validate_end,
        backtest_start=backtest_start,
        backtest_end=backtest_end,
        universe=universe,
        symbols=symbols,
        evaluation=evaluation,
        runtime_options=runtime_options or None,
    )


def _spec_to_payload(spec: ExperimentSpec) -> dict[str, object]:
    payload: dict[str, object] = {
        "user_idea": spec.user_idea,
        "train_start": spec.train_start,
        "train_end": spec.train_end,
        "validate_start": spec.validate_start,
        "validate_end": spec.validate_end,
        "backtest_start": spec.backtest_start,
        "backtest_end": spec.backtest_end,
        "scenario": generate_scenario(spec),
    }
    if spec.universe:
        payload["universe"] = spec.universe
    if spec.symbols:
        payload["symbols"] = spec.symbols
    if spec.evaluation:
        payload["evaluation"] = spec.evaluation
    if spec.runtime_options:
        payload.update(spec.runtime_options)
    payload.setdefault("run_final_test", False)
    payload.setdefault("allow_final_test", False)
    return payload


def _selected_experiment_names() -> set[str] | None:
    raw_value = os.getenv("QUANTA_EXPERIMENT_FILE")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None

    selected: set[str] = set()
    for item in re.split(r"[,;]", raw_value):
        name = Path(item.strip()).name.lower()
        if not name:
            continue
        selected.add(name)
        selected.add(Path(name).stem)
    return selected or None


def load_experiment_ideas(experiments_dir: Path) -> list[tuple[str, str, dict[str, object]]]:
    if not experiments_dir.exists() or not experiments_dir.is_dir():
        return []

    ideas: list[tuple[str, str, dict[str, object]]] = []
    selected_names = _selected_experiment_names()
    for path in sorted(experiments_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        if (
            selected_names is not None
            and path.name.lower() not in selected_names
            and path.stem.lower() not in selected_names
        ):
            continue

        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            continue

        spec = _parse_experiment_spec(data)
        if spec is not None:
            ideas.append((path.name, spec.user_idea, _spec_to_payload(spec)))

    return ideas


def _run_single_experiment(
    source_file: str,
    idea: str,
    experiment_spec: dict[str, object],
    max_epochs: int | None,
) -> ExperimentOutcome:
    effective_spec = dict(experiment_spec)
    try:
        _apply_runtime_env_overrides(effective_spec)
    except ValueError as exc:
        return ExperimentOutcome(
            source_file=source_file,
            idea=idea,
            success=False,
            error=str(exc),
        )

    final_test_requested = _final_test_requested(effective_spec)
    experiment_id = effective_spec.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        source_stem = Path(source_file).stem.replace(" ", "_")
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        effective_spec["experiment_id"] = f"exp_{source_stem}_{timestamp}_{uuid4().hex[:6]}"

    try:
        final_state = run_workflow(
            user_idea=idea,
            max_epochs=max_epochs,
            experiment_spec=effective_spec,
        )
        ready_for_final = final_state.get("ready_for_final") is True
        candidate_frozen = final_state.get("candidate_frozen") is True
        development_passed = final_state.get("quality_passed") is True
        final_result = final_state.get("final_test_result")
        final_test_run = final_result is not None

        if final_test_requested:
            final_passed = _result_passed(final_result)
            if not final_passed:
                reason = str(final_state.get("manager_notes", "")).strip()
                if not reason:
                    reason = (
                        "Final test did not pass."
                        if final_test_run
                        else "Final test was requested but no final-test result was produced."
                    )
                return ExperimentOutcome(
                    source_file=source_file,
                    idea=idea,
                    success=False,
                    final_state=final_state,
                    error=reason,
                    ready_for_final=ready_for_final,
                    quality_passed=False,
                    final_test_run=final_test_run,
                )
            return ExperimentOutcome(
                source_file=source_file,
                idea=idea,
                success=True,
                final_state=final_state,
                ready_for_final=ready_for_final,
                quality_passed=True,
                final_test_run=True,
            )

        development_completed = (
            final_state.get("phase") == "done"
            and ready_for_final
            and candidate_frozen
            and development_passed
            and final_state.get("awaiting_final_test_approval") is True
            and not final_test_run
        )
        if not development_completed:
            reason = str(final_state.get("manager_notes", "")).strip()
            if not reason:
                reason = "Development evaluation ended without a frozen candidate ready for final testing."
            return ExperimentOutcome(
                source_file=source_file,
                idea=idea,
                success=False,
                final_state=final_state,
                error=reason,
                ready_for_final=ready_for_final,
                quality_passed=False,
                final_test_run=final_test_run,
            )
        return ExperimentOutcome(
            source_file=source_file,
            idea=idea,
            success=True,
            final_state=final_state,
            ready_for_final=True,
            quality_passed=True,
            final_test_run=False,
        )
    except AgentExecutionError as exc:
        return ExperimentOutcome(
            source_file=source_file,
            idea=idea,
            success=False,
            error=f"Agent={exc.agent_name}, attempts={exc.attempts}, reason={exc}",
        )
    except Exception as exc:  # pragma: no cover
        return ExperimentOutcome(
            source_file=source_file,
            idea=idea,
            success=False,
            error=f"Unexpected error: {exc}",
        )


def run_experiments(experiments_dir: Path, max_epochs: int | None = None) -> list[ExperimentOutcome]:
    ideas = load_experiment_ideas(experiments_dir)
    if not ideas:
        return []

    effective_max_epochs = get_max_epochs() if max_epochs is None else max_epochs

    results: list[ExperimentOutcome] = []
    enable_parallel = _is_truthy_env(os.getenv("QUANTA_EXPERIMENT_PARALLEL"), default=False)

    if enable_parallel and len(ideas) > 1:
        max_workers = max(1, len(ideas))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="strategy-exp") as executor:
            future_map = {
                executor.submit(
                    _run_single_experiment,
                    source_file,
                    idea,
                    experiment_spec,
                    effective_max_epochs,
                ): source_file
                for source_file, idea, experiment_spec in ideas
            }
            for future in as_completed(future_map):
                results.append(future.result())
    else:
        for source_file, idea, experiment_spec in ideas:
            results.append(
                _run_single_experiment(
                    source_file,
                    idea,
                    experiment_spec,
                    effective_max_epochs,
                )
            )

    return sorted(results, key=lambda item: item.source_file)
