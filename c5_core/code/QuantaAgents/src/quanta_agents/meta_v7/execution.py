"""V7 prefix-scoped execution over the unchanged daily account implementation."""
from __future__ import annotations

from quanta_agents.research_kernel.execution import (
    _execution_pool as _legacy_execution_pool,
    execute_strategy as _legacy_execute_strategy,
)
from .temporal import scope_frames, scope_panel


def execution_pool(panel):
    """Exactly the pool used by both V7 factor diagnostics and execution."""
    return _legacy_execution_pool(panel)


def execute_strategy(panel, frames, spec, *, start, end, policy=None, cancelled=None, cost_multiplier=1):
    scoped = scope_panel(panel, end=end)
    scores = scope_frames(frames, scoped)
    result = _legacy_execute_strategy(scoped, scores, spec, start=start, end=end, policy=policy,
                                     cancelled=cancelled, cost_multiplier=cost_multiplier)
    result["temporal_scope"] = {**scoped.provenance["temporal_scope"],
                                "panel_fingerprint": scoped.fingerprint()}
    return result
