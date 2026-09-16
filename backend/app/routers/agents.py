"""/agents API — list the seeded sample agent + register your own (black-box) agent."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.adapter import DEFAULT_REQUEST_TEMPLATE
from app.db import (
    get_agent,
    get_agent_by_name_and_endpoint,
    insert_agent,
    list_agents,
    set_agent_knowledge,
    update_agent_description,
)

router = APIRouter(prefix="/agents", tags=["agents"])


class RegisterAgent(BaseModel):
    name: str
    endpoint_url: str
    response_path: str = "reply"
    request_template: str = DEFAULT_REQUEST_TEMPLATE
    auth_header: str | None = None
    description: str | None = None
    # "chat" (default, text/HTTP) or "voice" — which execution path the Temporal
    # workflow uses for runs against this agent.
    modality: str = "chat"
    # Which wire protocol a voice-modality agent speaks. Only "http_json" (the
    # existing TTS -> HTTP -> STT contract) is implemented; irrelevant for chat.
    voice_protocol: str = "http_json"


class AgentKnowledge(BaseModel):
    """The agent's docs, read as text in the browser on the Connect step."""
    text: str
    name: str | None = None


def _row_to_dict(row) -> dict:
    r = dict(row)
    return {
        "id": r["id"], "name": r["name"], "kind": r["kind"],
        "modality": r.get("modality") or "chat",
        "voice_protocol": r.get("voice_protocol") or "http_json",
        "endpoint_url": r["endpoint_url"], "response_path": r["response_path"],
        "description": r["description"], "created_at": r["created_at"],
        "knowledge_name": r.get("knowledge_name"),
        "knowledge_chars": len(r.get("knowledge") or ""),
    }


@router.get("")
def get_agents() -> dict:
    return {"agents": [_row_to_dict(a) for a in list_agents()]}


@router.get("/{agent_id}")
def get_one(agent_id: int) -> dict:
    a = get_agent(agent_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    return _row_to_dict(a)


@router.post("")
def register(body: RegisterAgent) -> dict:
    """Register a custom (black-box) agent. Faults won't be injected; trace = whatever it returns.

    Re-registering the same name+endpoint returns the existing agent instead of inserting a
    duplicate, so reconnecting keeps the agent's customer context and saved test cases.
    """
    existing = get_agent_by_name_and_endpoint(body.name, body.endpoint_url)
    if existing:
        if body.description:
            update_agent_description(existing["id"], body.description)
        return {"agent_id": existing["id"]}

    agent_id = insert_agent(
        name=body.name,
        kind="custom",
        endpoint_url=body.endpoint_url,
        response_path=body.response_path,
        request_template=body.request_template,
        auth_header=body.auth_header,
        description=body.description,
        modality=body.modality,
        voice_protocol=body.voice_protocol,
    )
    return {"agent_id": agent_id}


@router.post("/{agent_id}/probe")
async def probe(agent_id: int) -> dict:
    """Real connectivity check: send one trivial message through the adapter and report back.

    Powers the UI's 'Verify Connection' step — it genuinely reaches the endpoint.
    """
    from app.core.adapter import send

    a = get_agent(agent_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    result = await send(a, "Connectivity test from AgentShield. Please reply 'ok'.", [], [])
    reply = result.get("reply", "")
    trace = result.get("trace", {}) or {}
    ok = reply != "<error>" and not trace.get("error")
    return {"ok": ok, "reply": reply[:300], "error": trace.get("error")}


@router.post("/{agent_id}/discover")
async def discover(agent_id: int) -> dict:
    """Auto-discover what the agent does by probing it; save + return the inferred description.

    Used when the user uploads no docs and gives no description — AgentShield figures it out.
    """
    from app.core.discover import discover_agent

    a = get_agent(agent_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    description = await discover_agent(a)
    update_agent_description(agent_id, description)
    return {"description": description}


@router.post("/{agent_id}/knowledge")
def save_knowledge(agent_id: int, body: AgentKnowledge) -> dict:
    """Attach the agent's docs, so every later run for it generates grounded test cases.

    Takes the text the browser already read from the file — no server-side file parsing.
    """
    if get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    set_agent_knowledge(agent_id, body.text, body.name)
    return {"agent_id": agent_id, "knowledge_name": body.name, "chars": len(body.text)}
