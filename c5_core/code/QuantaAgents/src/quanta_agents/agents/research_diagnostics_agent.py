from __future__ import annotations

from quanta_agents.research_diagnostics import run_research_diagnostics
from quanta_agents.state import WorkflowState
from quanta_agents.trace_logger import print_agent_progress, write_trace_json


class ResearchDiagnosticsAgent:
    """根据研究请求，用固定程序补算开发证据。"""

    name = "ResearchDiagnosticsAgent"

    def run(self, state: WorkflowState) -> WorkflowState:
        print_agent_progress(state, agent_name=self.name, message="starting")
        report = run_research_diagnostics(state)
        state["diagnostic_round"] = int(state.get("diagnostic_round", 0)) + 1
        state["diagnostic_report"] = report
        records = state.setdefault("diagnostic_records", [])
        records.append(report)
        state["phase"] = "hypothesis"
        state["manager_notes"] = (
            "Research diagnostics completed and returned to the research optimizer."
        )
        state["history"].append(
            f"Epoch {state['epoch_index']}: fixed diagnostics round "
            f"{state['diagnostic_round']} completed without consuming a strategy trial"
        )
        write_trace_json(
            state,
            agent_name=self.name,
            stage="diagnostic_report",
            payload=report,
        )
        print_agent_progress(state, agent_name=self.name, message="completed")
        return state
