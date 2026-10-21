from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

import yaml
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quanta_agents.agents.hypothesis_agent import HypothesisAgent
from quanta_agents.agents.strategy_agent import StrategyAgent
import quanta_agents.agents.strategy_agent as strategy_agent_module
from quanta_agents.llm import llm_client
from quanta_agents.state import WorkflowState, init_state


DEFAULT_EXPERIMENT_FILE = PROJECT_ROOT / "experiments" / "idea_03.yaml"


def _load_experiment_spec(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"experiment file must be a YAML mapping: {path}")

    spec: dict[str, object] = dict(data)
    if "user_idea" not in spec and "initial_idea" in spec:
        spec["user_idea"] = spec["initial_idea"]
    if "user_idea" not in spec:
        raise ValueError(f"experiment file missing user_idea/initial_idea: {path}")
    return spec


def _as_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _build_state(experiment_spec: dict[str, object], max_epochs: int) -> WorkflowState:
    user_idea = str(experiment_spec.get("user_idea", "")).strip()
    if not user_idea:
        raise ValueError("experiment spec user_idea cannot be empty")
    return init_state(
        user_idea=user_idea,
        max_epochs=max_epochs,
        experiment_spec=experiment_spec,
    )


def _summarize_output_weights(output_weights: Any) -> dict[str, object]:
    if output_weights is None or not hasattr(output_weights, "shape"):
        return {"available": False}

    row_sums = output_weights.sum(axis=1, numeric_only=True)
    sample = []
    try:
        sample = [float(value) for value in row_sums.head(5).tolist()]
    except Exception:
        sample = []

    return {
        "available": True,
        "shape": list(output_weights.shape),
        "columns": [str(column) for column in list(output_weights.columns)],
        "row_sum_sample": sample,
    }


def _build_synthetic_required_data(required_data: list[dict[str, object]]) -> list[dict[str, object]]:
    dates = pd.date_range("2023-01-01", periods=40, freq="D")
    symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
    loaded: list[dict[str, object]] = []

    for item in required_data:
        table_key = str(item.get("table_key", "required_data")).strip() or "required_data"
        data_type = str(item.get("type", "time_series")).strip()
        raw_fields = item.get("fields")
        if isinstance(raw_fields, list):
            fields = [str(field).strip() for field in raw_fields if isinstance(field, str) and field.strip()]
        else:
            fields = []

        if data_type == "static":
            rows = []
            for symbol in symbols:
                row = {field: f"{field}_{symbol}" for field in fields}
                row.setdefault("code", symbol)
                rows.append(row)
            data = pd.DataFrame(rows)
        elif data_type == "panel":
            data = pd.DataFrame(
                {
                    "trade_date": dates,
                    **{
                        symbol: range(index, index + len(dates))
                        for index, symbol in enumerate(symbols, start=1)
                    },
                }
            )
        else:
            rows = []
            for symbol_index, symbol in enumerate(symbols, start=1):
                for date_index, date in enumerate(dates, start=1):
                    row: dict[str, object] = {
                        "date": date,
                        "symbol": symbol,
                        "open": float(date_index + symbol_index),
                        "high": float(date_index + symbol_index + 1),
                        "low": float(date_index + symbol_index - 1),
                        "close": float(date_index + symbol_index + 0.5),
                        "volume": float(1000 + date_index * 10 + symbol_index),
                    }
                    for field in fields:
                        row.setdefault(field, row.get(field, 0.0))
                    rows.append(row)
            data = pd.DataFrame(rows)

        loaded.append(
            {
                "table_key": table_key,
                "type": data_type,
                "fields": fields,
                "data": data,
            }
        )

    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run only HypothesisAgent -> StrategyAgent with real LLM calls.",
    )
    parser.add_argument(
        "--experiment-file",
        type=Path,
        default=DEFAULT_EXPERIMENT_FILE,
        help=f"Path to an experiment YAML file (default: {DEFAULT_EXPERIMENT_FILE})",
    )
    parser.add_argument(
        "--max-epochs",
        type=int,
        default=1,
        help="Initial state max_epochs value",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path to write the final state summary as JSON",
    )
    args = parser.parse_args()

    if not llm_client.enabled:
        raise SystemExit(
            "OPENAI_API_KEY is not configured. Set OPENAI_API_KEY (and optionally OPENAI_BASE_URL/OPENAI_MODEL) before running this script."
        )

    experiment_file = args.experiment_file.expanduser().resolve()
    if not experiment_file.exists():
        raise SystemExit(f"Experiment file not found: {experiment_file}")

    experiment_spec = _load_experiment_spec(experiment_file)
    max_epochs = _as_int(args.max_epochs, 1)
    state = _build_state(experiment_spec, max_epochs=max_epochs)

    hypothesis_agent = HypothesisAgent()
    strategy_agent = StrategyAgent()

    print(f"[1/2] Running HypothesisAgent on {experiment_file}")
    state = hypothesis_agent.run(state)
    print("[1/2] hypothesis:")
    print(state.get("hypothesis", ""))
    print()

    print("[2/2] Running StrategyAgent")
    try:
        state = strategy_agent.run(state)
    except Exception as exc:
        message = str(exc)
        if "Failed to connect to DolphinDB" not in message and "resolved universe is empty" not in message and "Unknown dataset" not in message:
            raise

        original_loader = strategy_agent_module.load_required_data

        def _synthetic_loader(required_data: list[dict[str, object]]) -> list[dict[str, object]]:
            return _build_synthetic_required_data(required_data)

        strategy_agent_module.load_required_data = _synthetic_loader
        try:
            print("[2/2] Falling back to synthetic required_data because the DolphinDB backend is unavailable")
            state = strategy_agent.run(state)
        finally:
            strategy_agent_module.load_required_data = original_loader

    strategy_result = state.get("strategy_result", {})
    output_weights = strategy_result.get("output_weights") if isinstance(strategy_result, dict) else None

    summary = {
        "experiment_file": str(experiment_file),
        "hypothesis": state.get("hypothesis", ""),
        "hypothesis_generation_meta": state.get("hypothesis_generation_meta", ""),
        "strategy_generation_meta": state.get("strategy_generation_meta", ""),
        "strategy_result_keys": list(strategy_result.keys()) if isinstance(strategy_result, dict) else [],
        "strategy_output_keys": list(cast(dict[str, object], strategy_result.get("strategy_output", {})).keys())
        if isinstance(strategy_result, dict) and isinstance(strategy_result.get("strategy_output"), dict)
        else [],
        "output_weights_summary": _summarize_output_weights(output_weights),
    }

    print("[2/2] strategy_result summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Summary written to {args.output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
