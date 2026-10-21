from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Keep this runnable even if the OpenAI dependency is absent in a local shell.
if "openai" not in sys.modules:
    openai_stub = ModuleType("openai")

    class _DummyOpenAI:  # pragma: no cover - local debug helper
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda *a, **k: None))

    setattr(openai_stub, "OpenAI", _DummyOpenAI)
    sys.modules["openai"] = openai_stub

from quanta_agents.agents.strategy_agent import StrategyAgent
from quanta_agents.agents.validate_agent import ValidateAgent
from quanta_agents.semantic import MetadataManager, load_required_data


ROUND_PATTERN = re.compile(r"^round_(\d+)$")
EXPERIMENT_PATTERN = re.compile(r"exp_(idea_\d+)_")


def _load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def _load_yaml_file(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML file must contain a mapping: {path}")
    return payload


def _find_ancestor(path: Path, predicate) -> Path | None:
    for candidate in [path, *path.parents]:
        if predicate(candidate):
            return candidate
    return None


def _resolve_experiment_spec_path(code_file: Path) -> Path | None:
    trace_root = _find_ancestor(code_file, lambda candidate: candidate.name.startswith("exp_idea_"))
    if trace_root is None:
        return None

    match = EXPERIMENT_PATTERN.search(trace_root.name)
    if match is None:
        return None

    experiment_name = f"{match.group(1)}.yaml"
    candidate = PROJECT_ROOT / "experiments" / experiment_name
    return candidate if candidate.exists() else None


def _resolve_experiment_spec(code_file: Path, overrides: dict[str, str]) -> dict[str, Any]:
    spec_path_text = overrides.get("experiment_spec_path", "").strip()
    if spec_path_text:
        spec_path = Path(spec_path_text).expanduser().resolve()
        return _load_yaml_file(spec_path)

    spec_path = _resolve_experiment_spec_path(code_file)
    if spec_path is not None:
        return _load_yaml_file(spec_path)

    return {}


def _resolve_periods(experiment_spec: dict[str, Any], overrides: dict[str, str]) -> dict[str, str]:
    def _pick(key: str) -> str:
        value = overrides.get(key, "").strip()
        if value:
            return value
        candidate = experiment_spec.get(key, "")
        return str(candidate).strip()

    return {
        "train_start": _pick("train_start"),
        "train_end": _pick("train_end"),
        "validate_start": _pick("validate_start"),
        "validate_end": _pick("validate_end"),
        "backtest_start": _pick("backtest_start"),
        "backtest_end": _pick("backtest_end"),
    }


def _round_number(path: Path) -> int:
    match = ROUND_PATTERN.match(path.name)
    if match is None:
        return -1
    return int(match.group(1))


def _find_round_dir(code_file: Path, agent_name: str) -> Path | None:
    round_dir = _find_ancestor(code_file, lambda candidate: ROUND_PATTERN.match(candidate.name) is not None)
    if round_dir is None:
        return None

    parent = round_dir.parent
    if parent.name != agent_name:
        return None
    return round_dir


def _find_latest_round_dir(agent_root: Path) -> Path | None:
    if not agent_root.exists():
        return None

    round_dirs = [item for item in agent_root.iterdir() if item.is_dir() and ROUND_PATTERN.match(item.name)]
    if not round_dirs:
        return None
    return max(round_dirs, key=_round_number)


def _find_latest_round_dir_with_structured_output(agent_root: Path) -> Path | None:
    if not agent_root.exists():
        return None

    round_dirs = sorted(
        [item for item in agent_root.iterdir() if item.is_dir() and ROUND_PATTERN.match(item.name)],
        key=_round_number,
        reverse=True,
    )
    for round_dir in round_dirs:
        if _find_structured_output_json(round_dir) is not None:
            return round_dir
    return None


def _find_structured_output_json(round_dir: Path) -> Path | None:
    structured_dir = round_dir / "structured_output"
    if not structured_dir.exists():
        return None

    json_files = sorted(structured_dir.glob("*.json"))
    if not json_files:
        return None
    return json_files[-1]


def _find_python_code_file(round_dir: Path) -> Path | None:
    candidates = sorted(round_dir.glob("*.py"))
    if candidates:
        return candidates[-1]

    nested_candidates = sorted(round_dir.rglob("*.py"))
    if nested_candidates:
        return nested_candidates[-1]
    return None


def _load_required_data_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Backward/forward compatibility:
    # - Some traces store required_data at payload.required_data
    # - Some store it under payload.strategy_generation_meta.required_data
    required_data = payload.get("required_data")
    if not isinstance(required_data, list) or not required_data:
        strategy_meta = payload.get("strategy_generation_meta")
        if isinstance(strategy_meta, dict):
            required_data = strategy_meta.get("required_data")

    if not isinstance(required_data, list) or not required_data:
        return []
    return [item for item in required_data if isinstance(item, dict)]


def _load_required_data_from_file(path_text: str) -> list[dict[str, Any]]:
    if not path_text.strip():
        return []
    path = Path(path_text).expanduser().resolve()
    payload = _load_json_file(path)
    required_data = payload.get("payload", {}).get("required_data", [])
    if not isinstance(required_data, list):
        raise ValueError(f"required_data is not a list in {path}")
    return [item for item in required_data if isinstance(item, dict)]


def _load_data_bundles(
    required_data: list[dict[str, Any]],
    periods: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not required_data:
        raise ValueError("required_data is empty; cannot load framework-equivalent data bundles")

    train_required_data = []
    validate_required_data = []
    for item in required_data:
        train_item = dict(item)
        validate_item = dict(item)
        if train_item.get("type") in {"time_series", "auxiliary", "panel"}:
            train_item["time_range"] = {"start": periods["train_start"], "end": periods["train_end"]}
            validate_item["time_range"] = {"start": periods["validate_start"], "end": periods["validate_end"]}
        train_required_data.append(train_item)
        validate_required_data.append(validate_item)

    train_loaded = load_required_data(train_required_data)
    validate_loaded = load_required_data(validate_required_data)
    return StrategyAgent._build_data_bundle(train_loaded), StrategyAgent._build_data_bundle(validate_loaded)


def _build_strategy_context(
    strategy_meta: dict[str, Any],
    required_data: list[dict[str, Any]],
    train_bundle: dict[str, Any],
    validate_bundle: dict[str, Any],
    periods: dict[str, str],
    experiment_spec: dict[str, Any],
) -> dict[str, Any]:
    metadata_manager: MetadataManager | None = None
    try:
        metadata_manager = MetadataManager.from_yaml_files()
    except Exception:
        metadata_manager = None

    required_data_descriptions = ""
    if metadata_manager is not None and required_data:
        try:
            required_data_descriptions = StrategyAgent._build_required_data_descriptions(
                required_data,
                train_bundle,
                validate_bundle,
                metadata_manager,
            )
        except Exception:
            required_data_descriptions = ""

    return {
        "hypothesis": strategy_meta.get("hypothesis", ""),
        "required_data": required_data,
        "train_data_bundle": train_bundle,
        "validate_data_bundle": validate_bundle,
        "required_data_descriptions": required_data_descriptions,
        "train_start": periods["train_start"],
        "train_end": periods["train_end"],
        "validate_start": periods["validate_start"],
        "validate_end": periods["validate_end"],
        "strategy_modification": str(strategy_meta.get("strategy_modification", "")).strip(),
        "missing_concepts": strategy_meta.get("missing_concepts", []),
        "backtest_datasets": strategy_meta.get("backtest_datasets", []),
        "experiment_spec": experiment_spec,
    }


def _build_validate_context(
    strategy_code: str,
    strategy_meta: dict[str, Any],
    strategy_output: dict[str, Any],
    output_weights_df: Any,
    required_data: list[dict[str, Any]],
    train_bundle: dict[str, Any],
    validate_bundle: dict[str, Any],
    periods: dict[str, str],
) -> dict[str, Any]:
    return {
        "strategy_code": strategy_code,
        "strategy_output": strategy_output,
        "output_weights_df": output_weights_df,
        "train_data_bundle": train_bundle,
        "validate_data_bundle": validate_bundle,
        "required_data": required_data,
        "experiment_periods": {
            "train_start": periods["train_start"],
            "train_end": periods["train_end"],
            "validate_start": periods["validate_start"],
            "validate_end": periods["validate_end"],
            "backtest_start": periods["backtest_start"],
            "backtest_end": periods["backtest_end"],
        },
        "strategy_generation_meta": strategy_meta,
    }


def _run_strategy_file(code_file: Path, args: argparse.Namespace) -> int:
    round_dir = _find_round_dir(code_file, "strategyagent")
    if round_dir is None:
        raise ValueError(f"cannot infer strategyagent round directory from {code_file}")

    structured_file = _find_structured_output_json(round_dir)
    if structured_file is None:
        raise ValueError(f"cannot find structured_output json under {round_dir}")

    structured_payload = _load_json_file(structured_file)
    payload = structured_payload.get("payload", {})
    strategy_meta = payload.get("strategy_generation_meta", {})
    if not isinstance(strategy_meta, dict):
        strategy_meta = {}

    experiment_spec = _resolve_experiment_spec(code_file, vars(args))
    periods = _resolve_periods(experiment_spec, vars(args))
    if not periods["train_start"] or not periods["train_end"] or not periods["validate_start"] or not periods["validate_end"]:
        raise ValueError("missing train/validate periods; pass overrides or place the file under an experiment trace with idea config")

    required_data = _load_required_data_from_payload(payload)
    if not required_data:
        required_data = _load_required_data_from_file(args.required_data_file)
    if not required_data:
        raise ValueError("strategy mode requires required_data from structured_output or --required-data-file")

    train_bundle, validate_bundle = _load_data_bundles(required_data, periods)
    strategy_context = _build_strategy_context(strategy_meta, required_data, train_bundle, validate_bundle, periods, experiment_spec)
    namespace = StrategyAgent._build_sandbox_namespace(strategy_context)

    code = code_file.read_text(encoding="utf-8")
    print("[sandbox] mode=strategy")
    print(f"[sandbox] code_file={code_file}")
    print(f"[sandbox] structured_output={structured_file}")
    print(f"[sandbox] train_period={periods['train_start']}..{periods['train_end']}")
    print(f"[sandbox] validate_period={periods['validate_start']}..{periods['validate_end']}")

    exec(compile(code, str(code_file), "exec"), namespace)  # noqa: S102
    return 0


def _resolve_strategy_code_file(code_file: Path, args: argparse.Namespace) -> Path:
    explicit = str(args.strategy_code_file or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    validate_round_dir = _find_round_dir(code_file, "validateagent")
    if validate_round_dir is None:
        raise ValueError(f"cannot infer validateagent round directory from {code_file}")

    epoch_dir = validate_round_dir.parent.parent
    strategy_root = epoch_dir / "strategyagent"
    latest_strategy_round = _find_latest_round_dir_with_structured_output(strategy_root)
    if latest_strategy_round is None:
        raise ValueError(f"cannot find strategyagent round with structured_output under {strategy_root}")

    code_candidate = _find_python_code_file(latest_strategy_round)
    if code_candidate is None:
        raise ValueError(f"cannot find python code file under {latest_strategy_round}")
    return code_candidate


def _run_validate_file(code_file: Path, args: argparse.Namespace) -> int:
    validate_round_dir = _find_round_dir(code_file, "validateagent")
    if validate_round_dir is None:
        raise ValueError(f"cannot infer validateagent round directory from {code_file}")

    validate_structured_file = _find_structured_output_json(validate_round_dir)
    if validate_structured_file is None:
        raise ValueError(f"cannot find validate structured_output json under {validate_round_dir}")

    strategy_code_file = _resolve_strategy_code_file(code_file, args)
    strategy_round_dir = _find_round_dir(strategy_code_file, "strategyagent")
    if strategy_round_dir is None:
        raise ValueError(f"cannot infer strategyagent round directory from {strategy_code_file}")

    strategy_structured_file = _find_structured_output_json(strategy_round_dir)
    if strategy_structured_file is None:
        raise ValueError(f"cannot find strategy structured_output json under {strategy_round_dir}")

    validate_payload = _load_json_file(validate_structured_file).get("payload", {})
    strategy_payload = _load_json_file(strategy_structured_file).get("payload", {})

    strategy_meta = strategy_payload.get("strategy_generation_meta", {})
    if not isinstance(strategy_meta, dict):
        strategy_meta = {}

    experiment_spec = _resolve_experiment_spec(code_file, vars(args))
    periods = _resolve_periods(experiment_spec, vars(args))
    if not periods["train_start"] or not periods["train_end"] or not periods["validate_start"] or not periods["validate_end"]:
        raise ValueError("missing train/validate periods; pass overrides or place the file under an experiment trace with idea config")

    required_data = _load_required_data_from_payload(strategy_payload)
    if not required_data:
        required_data = _load_required_data_from_payload(validate_payload)
    if not required_data:
        required_data = _load_required_data_from_file(args.required_data_file)
    if not required_data:
        raise ValueError("validate mode requires required_data from structured_output or --required-data-file")

    train_bundle, validate_bundle = _load_data_bundles(required_data, periods)

    strategy_code = strategy_code_file.read_text(encoding="utf-8")
    strategy_output = validate_payload.get("strategy_output", {})
    if not isinstance(strategy_output, dict):
        strategy_output = {}

    strategy_context = _build_validate_context(
        strategy_code=strategy_code,
        strategy_meta=strategy_meta,
        strategy_output={**strategy_output, **(strategy_payload if isinstance(strategy_payload, dict) else {})},
        output_weights_df=strategy_payload.get("output_weights_df"),
        required_data=required_data,
        train_bundle=train_bundle,
        validate_bundle=validate_bundle,
        periods=periods,
    )

    namespace, bootstrap_notes = ValidateAgent._build_script_sandbox_namespace(strategy_context)

    code = code_file.read_text(encoding="utf-8")
    print("[sandbox] mode=validate")
    print(f"[sandbox] code_file={code_file}")
    print(f"[sandbox] validate_structured_output={validate_structured_file}")
    print(f"[sandbox] strategy_code_file={strategy_code_file}")
    print(f"[sandbox] strategy_structured_output={strategy_structured_file}")
    print(f"[sandbox] train_period={periods['train_start']}..{periods['train_end']}")
    print(f"[sandbox] validate_period={periods['validate_start']}..{periods['validate_end']}")
    if bootstrap_notes:
        print("[sandbox] bootstrap_notes:")
        for note in bootstrap_notes:
            print(f"  - {note}")

    exec(compile(code, str(code_file), "exec"), namespace)  # noqa: S102
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a strategyagent/validateagent generated Python file inside a framework-equivalent sandbox",
    )
    parser.add_argument("--code-file", type=Path, required=True, help="Generated Python file to execute")
    parser.add_argument("--mode", choices=["auto", "strategy", "validate"], default="auto")
    parser.add_argument("--experiment-spec-path", type=str, default="", help="Optional YAML experiment spec override")
    parser.add_argument("--train-start", type=str, default="")
    parser.add_argument("--train-end", type=str, default="")
    parser.add_argument("--validate-start", type=str, default="")
    parser.add_argument("--validate-end", type=str, default="")
    parser.add_argument("--backtest-start", type=str, default="")
    parser.add_argument("--backtest-end", type=str, default="")
    parser.add_argument("--required-data-file", type=str, default="", help="Fallback structured_output JSON containing required_data")
    parser.add_argument("--strategy-code-file", type=str, default="", help="Optional explicit strategy code file for validate mode")
    args = parser.parse_args()

    code_file = args.code_file.expanduser().resolve()
    if not code_file.exists():
        raise FileNotFoundError(code_file)

    mode = args.mode
    if mode == "auto":
        if _find_round_dir(code_file, "validateagent") is not None:
            mode = "validate"
        elif _find_round_dir(code_file, "strategyagent") is not None:
            mode = "strategy"
        else:
            raise ValueError("cannot infer mode from code file path; pass --mode strategy or --mode validate")

    if mode == "strategy":
        return _run_strategy_file(code_file, args)
    return _run_validate_file(code_file, args)


if __name__ == "__main__":
    raise SystemExit(main())