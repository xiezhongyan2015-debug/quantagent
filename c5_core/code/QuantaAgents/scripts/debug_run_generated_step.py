from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Keep this script runnable without real OpenAI dependency.
if "openai" not in sys.modules:
    openai_stub = ModuleType("openai")

    class _DummyOpenAI:  # pragma: no cover - local debug helper
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda *a, **k: None))

    setattr(openai_stub, "OpenAI", _DummyOpenAI)
    sys.modules["openai"] = openai_stub

from quanta_agents.agents.strategy_agent import StrategyAgent


DEFAULT_CODE_FILE = PROJECT_ROOT / "experiment_traces" / "unknown_experiment" / "epochs_1" / "epoch_001" / "round_001" / "agents" / "strategy" / "generated_code" / "step_01_20260630_081336_559496.py"
DEFAULT_REQUIRED_DATA_FILE = PROJECT_ROOT / "experiment_traces" / "unknown_experiment" / "epochs_1" / "epoch_001" / "round_001" / "agents" / "hypothesisagent" / "structured_output" / "attempt_01_20260630_081319_859990.json"


def _load_required_data(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required_data = payload.get("payload", {}).get("required_data", [])
    if not isinstance(required_data, list):
        raise ValueError("required_data is not a list")
    return required_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug-run a generated strategy step script with real data bundles")
    parser.add_argument("--code-file", type=Path, default=DEFAULT_CODE_FILE)
    parser.add_argument("--required-data-file", type=Path, default=DEFAULT_REQUIRED_DATA_FILE)
    parser.add_argument("--train-start", type=str, default="2020-01-01")
    parser.add_argument("--train-end", type=str, default="2022-12-31")
    parser.add_argument("--validate-start", type=str, default="2023-01-01")
    parser.add_argument("--validate-end", type=str, default="2023-12-31")
    args = parser.parse_args()

    code_file = args.code_file.expanduser().resolve()
    required_data_file = args.required_data_file.expanduser().resolve()

    required_data = _load_required_data(required_data_file)
    prepared = StrategyAgent._prepare_required_data(required_data, args.train_start, args.train_end)
    _, _, train_bundle, validate_bundle = StrategyAgent._load_required_inputs(
        prepared,
        args.train_start,
        args.train_end,
        args.validate_start,
        args.validate_end,
    )

    # generated code expects these names in global namespace
    namespace: dict[str, Any] = {
        "train_data_bundle": train_bundle,
        "validate_data_bundle": validate_bundle,
    }

    code = code_file.read_text(encoding="utf-8")

    print(f"Running code file: {code_file}")
    print(f"Using required_data file: {required_data_file}")

    try:
        exec(compile(code, "generated_strategy.py", "exec"), namespace)  # noqa: S102
        print("Execution succeeded")
        return 0
    except Exception as exc:
        print("Execution failed with exception:")
        print(f"  {type(exc).__name__}: {exc}")
        print("\nTraceback:")
        print(traceback.format_exc())

        # Extra diagnostics for the common duplicate-index failure.
        table_key = "stock_kline_daily_qfq"
        validate_data = validate_bundle.get(table_key)
        if validate_data is None:
            print(f"No validate_data found for table_key={table_key}")
            return 1

        print("\n[Diagnostics] validate_data basic info")
        print(f"  shape={getattr(validate_data, 'shape', None)}")
        print(f"  columns={list(validate_data.columns)}")

        if {"symbol", "date"}.issubset(set(validate_data.columns)):
            dup_pair_mask = validate_data.duplicated(subset=["symbol", "date"], keep=False)
            dup_pair_count = int(dup_pair_mask.sum())
            print(f"  duplicated rows on (symbol, date): {dup_pair_count}")
            if dup_pair_count > 0:
                sample = validate_data.loc[dup_pair_mask, ["symbol", "date"]].head(10)
                print("  sample duplicated (symbol, date):")
                print(sample.to_string(index=False))

            per_symbol_dup = (
                validate_data.groupby("symbol")["date"]
                .apply(lambda s: int(s.duplicated(keep=False).sum()))
                .sort_values(ascending=False)
            )
            print("  duplicated date counts per symbol (top 10):")
            print(per_symbol_dup.head(10).to_string())

            # Minimal reproduction of the assignment failure pattern used in step_01.
            first_symbol = str(validate_data["symbol"].iloc[0])
            symbol_data = validate_data[validate_data["symbol"] == first_symbol].copy()
            symbol_data = symbol_data.set_index("date")
            print(f"\n[Diagnostics] first symbol={first_symbol}, symbol_data rows={len(symbol_data)}")
            print(f"  unique date count={symbol_data.index.nunique()}")

            result = validate_data[["date"]].drop_duplicates().set_index("date")
            try:
                result["tmp"] = symbol_data["close"]
                print("  assignment reproduction: success")
            except Exception as assign_exc:
                print("  assignment reproduction: failed")
                print(f"    {type(assign_exc).__name__}: {assign_exc}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
