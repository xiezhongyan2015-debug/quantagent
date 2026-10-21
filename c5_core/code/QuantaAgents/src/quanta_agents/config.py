from __future__ import annotations

import os


DEFAULT_AGENT_MAX_RETRIES = 10
DEFAULT_MAX_EPOCHS = 5
DEFAULT_MAX_STRATEGY_VALIDATE_ROUNDS = 6


def get_agent_max_retries(agent_name: str) -> int:
    specific_key = f"{agent_name.upper()}_MAX_RETRIES"
    specific_value = os.getenv(specific_key)
    if specific_value is not None:
        return _parse_positive_int(specific_value, specific_key)

    shared_value = os.getenv("AGENT_MAX_RETRIES")
    if shared_value is not None:
        return _parse_positive_int(shared_value, "AGENT_MAX_RETRIES")

    return DEFAULT_AGENT_MAX_RETRIES


def get_max_epochs() -> int:
    raw_value = os.getenv("MAX_EPOCHS")
    if raw_value is None:
        return DEFAULT_MAX_EPOCHS
    return _parse_positive_int(raw_value, "MAX_EPOCHS")


def get_max_strategy_validate_rounds() -> int:
    raw_value = os.getenv("MAX_STRATEGY_VALIDATE_ROUNDS")
    if raw_value is None:
        return DEFAULT_MAX_STRATEGY_VALIDATE_ROUNDS
    return _parse_positive_int(raw_value, "MAX_STRATEGY_VALIDATE_ROUNDS")


def _parse_positive_int(raw_value: str, key: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer, got: {raw_value}") from exc

    if value < 1:
        raise RuntimeError(f"{key} must be >= 1, got: {value}")

    return value
