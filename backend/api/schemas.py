"""Pydantic request/response models for the HIVE API."""
from __future__ import annotations

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    task: str
    model: str = "claude:sonnet"
    approval_mode: str = "full-auto"
    project_path: str | None = None
    max_turns: int = 20


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str = "starting"


class ApproveRequest(BaseModel):
    approved: bool
    team_composition: dict | None = None


class MessageRequest(BaseModel):
    text: str
    agent_id: str = "orchestrator"
    urgency: str = "question"


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
