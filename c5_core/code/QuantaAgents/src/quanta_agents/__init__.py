"""QuantaAgents package."""

__all__ = ["build_workflow"]


def __getattr__(name):
    # Numerical research imports must not initialize the unrelated agent graph.
    if name == "build_workflow":
        from quanta_agents.workflow import build_workflow
        return build_workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
