from __future__ import annotations


class AgentExecutionError(RuntimeError):
    def __init__(self, agent_name: str, attempts: int, message: str, cause: Exception | None = None) -> None:
        super().__init__(f"{agent_name} failed after {attempts} attempts: {message}")
        self.agent_name = agent_name
        self.attempts = attempts
        self.cause = cause
