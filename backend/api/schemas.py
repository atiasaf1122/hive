"""Pydantic request/response models for the HIVE API."""
from __future__ import annotations

from pydantic import BaseModel

from backend.models import DEFAULT_MODEL


class CreateSessionRequest(BaseModel):
    task: str
    model: str = DEFAULT_MODEL
    approval_mode: str = "full-auto"
    project_path: str | None = None
    max_turns: int = 20


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str = "starting"


class ApproveRequest(BaseModel):
    approved: bool
    team_composition: dict | None = None
    # correlation_id identifies the specific interrupt — multiple approvals
    # can be in flight for one session (invariant #5). Optional only for
    # transitional UI clients that haven't yet been updated; new code MUST
    # send it. When missing, the backend falls back to the session's single
    # latest pending approval if exactly one exists, else 400.
    correlation_id: str | None = None


class MessageRequest(BaseModel):
    text: str
    agent_id: str = "orchestrator"
    urgency: str = "question"
    # E3: routing override from the composer mode selector.
    task_shape: str = "auto"     # auto | solo | swarm | chat


class AgentInfo(BaseModel):
    agent_id: str
    role: str
    model: str
    status: str


class SessionInfo(BaseModel):
    session_id: str
    name: str
    status: str
    approval_mode: str = "full-auto"
    created_at: str = ""
    last_active: str = ""
    agents: list[AgentInfo] = []
