"""Configurable closing decisions; the runtime must persist and reserve atomically.

No I/O, model invocation, final generation or historical-unknown exceptions.
Token amounts are nominal admission protection, not a supplier billing guarantee.
"""
from dataclasses import dataclass
import math

FINAL_ACTION = "submit_research_report"
MODES = frozenset({"explore", "close_only", "blocked", "terminal_without_submission"})


@dataclass(frozen=True)
class ClosingPolicy:
    task_tokens: int = 600_000
    stage_tokens: int = 2_400_000
    task_calls: int = 17
    stage_calls: int = 68
    call_reserve: int = 80_000
    closing_reserve: int = 80_000
    closing_seconds: int = 3_600

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("Policy limits must be positive integers")
        if self.task_tokens > self.stage_tokens or self.task_calls > self.stage_calls:
            raise ValueError("Stage limits cannot be below task limits")
        if self.closing_reserve < self.call_reserve:
            raise ValueError("Closing reserve must cover nominal call admission")


@dataclass(frozen=True)
class BudgetState:
    task_exposure: int
    stage_exposure: int
    task_calls_used: int
    stage_calls_used: int
    finals_remaining: int
    now_epoch: float
    deadline_epoch: float
    unknown: bool = False
    pending: bool = False
    evidence_valid: bool = True


@dataclass(frozen=True)
class ClosingDecision:
    mode: str
    reasons: tuple[str, ...]


def decide(policy: ClosingPolicy, state: BudgetState, previous_mode="explore"):
    """Exposures include all paid usage and unsettled reservations in this scope.

    Callers must derive evidence flags from trusted saved records; these flags
    alone do not attest source, time, permission or provider identity.
    """
    blocked = lambda why: ClosingDecision("blocked", (why,))
    if type(policy) is not ClosingPolicy or type(state) is not BudgetState:
        return blocked("invalid_input_type")
    if type(previous_mode) is not str or previous_mode not in MODES:
        return blocked("invalid_previous_mode")
    counts = (state.task_exposure, state.stage_exposure, state.task_calls_used,
              state.stage_calls_used, state.finals_remaining)
    if any(type(value) is not int or value < 0 for value in counts):
        return blocked("invalid_counts")
    if (state.task_exposure > state.stage_exposure or state.task_calls_used > state.stage_calls_used
            or state.finals_remaining not in (0, 1)):
        return blocked("inconsistent_counts")
    if any(type(value) is not bool for value in (state.unknown, state.pending, state.evidence_valid)):
        return blocked("invalid_evidence_flags")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in (state.now_epoch, state.deadline_epoch)):
        return blocked("invalid_time")
    if state.unknown or state.pending or not state.evidence_valid or previous_mode == "blocked":
        return blocked("unresolved_evidence_or_prior_block")
    if previous_mode == "terminal_without_submission":
        return ClosingDecision(previous_mode, ("prior_terminal",))
    token_room = min(policy.task_tokens - state.task_exposure,
                     policy.stage_tokens - state.stage_exposure)
    call_room = min(policy.task_calls - state.task_calls_used,
                    policy.stage_calls - state.stage_calls_used)
    seconds = state.deadline_epoch - state.now_epoch
    if token_room < policy.closing_reserve or call_room < 1 or state.finals_remaining != 1 or seconds <= 0:
        return ClosingDecision("terminal_without_submission", ("closing_opportunity_unavailable",))
    reasons = []
    if previous_mode == "close_only": reasons.append("closing_is_monotone")
    if token_room < policy.call_reserve + policy.closing_reserve: reasons.append("protect_closing_tokens")
    if call_room < 2: reasons.append("protect_closing_call")
    if seconds <= policy.closing_seconds: reasons.append("protect_closing_time")
    return ClosingDecision("close_only" if reasons else "explore", tuple(reasons))


def allowed_actions(decision: ClosingDecision, registered_actions: tuple[str, ...]):
    """Produce the public action menu; the dispatch boundary must also enforce it."""
    if (type(decision) is not ClosingDecision or decision.mode not in MODES
            or type(registered_actions) is not tuple or not 1 <= len(registered_actions) <= 32
            or any(type(action) is not str or not action for action in registered_actions)
            or len(set(registered_actions)) != len(registered_actions)
            or FINAL_ACTION not in registered_actions):
        raise ValueError("A validated decision and registered final action are required")
    if decision.mode == "explore":
        return registered_actions
    if decision.mode == "close_only":
        return (FINAL_ACTION,)
    return ()
