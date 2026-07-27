"""Pydantic models. Stubs for now — filled out as routers/engine land in later phases."""
from typing import Any, Literal, Optional

from pydantic import BaseModel


# ---- Shared chat shapes (used by adapter + sample bot) ----
class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatTurn] = []
    faults: list[str] = []


class Trace(BaseModel):
    tool_calls: list[dict[str, Any]] = []
    retrieved_docs: list[dict[str, Any]] = []
    latency_ms: int = 0
    tokens: int = 0


class ChatResponse(BaseModel):
    reply: str
    trace: Trace


# ---- API stubs (fleshed out in Phase 2/3) ----
class CreateRunRequest(BaseModel):
    agent_id: int
    tests: list[str] = []


class CreateRunResponse(BaseModel):
    run_id: int
