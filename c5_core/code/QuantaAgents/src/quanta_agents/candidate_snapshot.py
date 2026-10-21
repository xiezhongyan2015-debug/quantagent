from __future__ import annotations

from copy import deepcopy
from typing import Any, MutableMapping


SNAPSHOT_VERSION = 2

_SNAPSHOT_FIELDS = (
    "strategy_code",
    "code_text",
    "strategy_result",
    "strategy_generation_meta",
    "hypothesis_generation_meta",
    "development_report",
)

_EMPTY_WORK_RESULTS: dict[str, object] = {
    "test_result": None,
    "final_test_result": None,
    "validation_result": {},
    "validation_summary": {},
    "validation_feedback": {},
    "last_test_error": "",
    "backtest_feedback": "",
    "quality_passed": False,
    "final_quality_passed": False,
    "ready_for_final": False,
    "candidate_frozen": False,
    "awaiting_final_test_approval": False,
    "formal_trial_status": "",
    "calculation_status": "",
    "evidence_result": "",
    "quality_gate_passed": False,
    "adopted": False,
    "judge_result": {},
}


def _copy_strategy_result(value: object) -> object:
    """复制会被改动的小字段，大型只读表继续共用，避免内存翻倍。"""

    if not isinstance(value, dict):
        return deepcopy(value)
    copied = dict(value)
    for field in (
        "params",
        "required_data",
        "strategy_output",
        "universe",
        "backtest_datasets",
        "train_period",
        "validate_period",
    ):
        if field in copied:
            copied[field] = deepcopy(copied[field])
    for field in ("train_data_bundle", "validate_data_bundle"):
        bundle = copied.get(field)
        if isinstance(bundle, dict):
            copied[field] = dict(bundle)
    return copied


def _copy_snapshot_field(field: str, value: object) -> object:
    if field == "strategy_result":
        return _copy_strategy_result(value)
    return deepcopy(value)


def capture_candidate_snapshot(
    state: MutableMapping[str, Any],
    *,
    candidate_id: str | None = None,
    save_as_accepted: bool = True,
) -> dict[str, object]:
    """保存一个可以独立恢复的 v2 候选版本。"""

    resolved_candidate_id = str(
        candidate_id if candidate_id is not None else state.get("current_candidate_id", "")
    ).strip()
    if not resolved_candidate_id:
        raise ValueError("candidate_id 不能为空")

    snapshot: dict[str, object] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "candidate_id": resolved_candidate_id,
    }
    for field in _SNAPSHOT_FIELDS:
        snapshot[field] = _copy_snapshot_field(
            field,
            state.get(field, "" if field in {"strategy_code", "code_text"} else {}),
        )

    if save_as_accepted:
        state["accepted_snapshot"] = {
            key: _copy_snapshot_field(key, item) if key in _SNAPSHOT_FIELDS else deepcopy(item)
            for key, item in snapshot.items()
        }
        state["accepted_candidate_id"] = resolved_candidate_id

    return snapshot


def restore_candidate_snapshot(
    state: MutableMapping[str, Any],
    snapshot: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """恢复指定候选，并清除失败工作版本留下的结果。"""

    if int(snapshot.get("snapshot_version", 0)) != SNAPSHOT_VERSION:
        raise ValueError("不支持的候选快照版本")

    candidate_id = str(snapshot.get("candidate_id", "")).strip()
    if not candidate_id:
        raise ValueError("候选快照缺少 candidate_id")

    missing_fields = [field for field in _SNAPSHOT_FIELDS if field not in snapshot]
    if missing_fields:
        raise ValueError(f"候选快照缺少字段: {', '.join(missing_fields)}")

    for field in _SNAPSHOT_FIELDS:
        state[field] = _copy_snapshot_field(field, snapshot[field])
    state["current_candidate_id"] = candidate_id

    for field, empty_value in _EMPTY_WORK_RESULTS.items():
        state[field] = deepcopy(empty_value)
    state["strategy_validate_round"] = 1

    return state


def restore_accepted_snapshot(
    state: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """恢复 state 中最近保存的已采用候选。"""

    snapshot = state.get("accepted_snapshot")
    if not isinstance(snapshot, MutableMapping):
        raise ValueError("state 中没有可恢复的 accepted_snapshot")

    restored = restore_candidate_snapshot(state, snapshot)
    accepted_candidate_id = str(state.get("accepted_candidate_id", "")).strip()
    restored_candidate_id = str(restored.get("current_candidate_id", "")).strip()
    if accepted_candidate_id and accepted_candidate_id != restored_candidate_id:
        raise ValueError("accepted_candidate_id 与 accepted_snapshot 不一致")
    state["accepted_candidate_id"] = restored_candidate_id
    return restored


__all__ = [
    "SNAPSHOT_VERSION",
    "capture_candidate_snapshot",
    "restore_candidate_snapshot",
    "restore_accepted_snapshot",
]
