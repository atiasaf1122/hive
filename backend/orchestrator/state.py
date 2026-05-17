"""LangGraph GraphState — Phase 3: approval modes extension."""
from __future__ import annotations

from typing import Annotated, Any
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages


class AgentResult(TypedDict):
    agent_id: str
    status: str          # 'completed' | 'failed' | 'cancelled' | 'crashed'
    text_output: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None


class WorkerInput(TypedDict):
    """Input fed to each individual worker node via LangGraph Send."""
    session_id: str
    task: str
    agent_id: str
    role: str
    model: str
    worktree_path: str
    max_turns: int


class GraphState(TypedDict):
    # Session identity
    session_id: str
    task: str
    project_path: str

    # Phase 1: single worker config (kept for backwards compat)
    agent_id: str
    model: str
    worktree_path: str
    max_turns: int

    # Phase 2: multi-agent
    team_composition: dict | None        # raw JSON from Planner
    spawn_plan: dict | None              # serialised SpawnPlan
    worker_results: dict[str, AgentResult]  # agent_id -> result
    review_report: dict | None           # serialised ReviewReport

    # Phase 3: approval
    approval_mode: str     # 'full-auto' | 'checkpoint' | 'manual'
    approval_rejected: bool

    # Final output
    result: AgentResult | None

    # LangGraph messages channel
    messages: Annotated[list[Any], add_messages]
