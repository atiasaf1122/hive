"""LangGraph GraphState — orchestrator-first, multi-turn model.

A session stays active until the user explicitly closes it. Each user message
re-enters the orchestrator, which decides whether to chat, spawn agents, or both.
The graph loops via `wait_for_user_node` which interrupts until a new message
(or close signal) arrives.
"""
from __future__ import annotations

from typing import Annotated, Any
from typing_extensions import NotRequired, TypedDict

from langgraph.graph.message import add_messages


class AgentResult(TypedDict):
    agent_id: str
    status: str          # 'completed' | 'failed' | 'cancelled' | 'crashed'
    text_output: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None
    # B3: compact Haiku summary — what the orchestrator sees in history
    # (full output stays in the events table).
    summary: NotRequired[str]
    # B4: deterministic-validator verdict. None/absent = not validated
    # (agent failed before validation, or the collector couldn't run).
    validation_passed: NotRequired[bool | None]
    validation_findings: NotRequired[list[str]]
    # D0.2: who is at fault when status='failed' —
    # 'agent' | 'infrastructure' | 'unknown'.
    failure_origin: NotRequired[str | None]


class GraphState(TypedDict):
    # Session identity
    session_id: str
    task: str            # initial user message; preserved for session naming
    project_path: str
    db_path: str         # so DB writes from inside graph nodes target the right file

    # Per-agent defaults
    agent_id: str
    model: str
    worktree_path: str
    max_turns: int

    # Multi-agent
    team_composition: dict | None        # raw JSON from orchestrator turn
    spawn_plan: dict | None              # serialised SpawnPlan
    worker_results: dict[str, AgentResult]
    review_report: dict | None

    # Approval
    approval_mode: str   # 'full-auto' | 'checkpoint' | 'manual'
    approval_rejected: bool

    # Orchestrator-first multi-turn conversation
    conversation_history: list[dict]     # [{role, content, ts}]
    # D3: compaction — compact CURRENT STATE doc replacing pruned turns.
    state_doc: str
    turns_since_compaction: int
    pending_message: str                 # the message being processed this turn
    last_response: str                   # most recent orchestrator reply
    user_closed: bool                    # set True by wait_for_user when user closes

    # Final result of the most recent turn (used by API/CLI)
    result: AgentResult | None

    # LangGraph messages channel
    messages: Annotated[list[Any], add_messages]
