"""/runs API — launch a crash-test run and poll its progress."""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.orchestrator import prepare_scenarios, start_run
from app.core.scenarios import normalize_scenarios
from app.db import (
    build_conversation_payload,
    get_agent,
    get_conversations_for_run,
    get_run,
    insert_run,
    run_counts,
)

router = APIRouter(prefix="/runs", tags=["runs"])


class GenerateScenarios(BaseModel):
    agent_id: int
    tests: list[str] = []
    guidance: str = ""   # optional user domain focus / edge cases
    knowledge: str = ""  # optional uploaded agent docs (authoritative ground truth)


class CreateRun(GenerateScenarios):
    # The user-reviewed, finalized test suite. When present it is executed verbatim and
    # NO scenarios are generated. Empty => generate (original single-shot behaviour).
    scenarios: list[dict] = []


@router.post("/scenarios")
async def generate_run_scenarios(body: GenerateScenarios) -> dict:
    """Stage 1 — generate the test suite for review. Executes nothing, persists nothing."""
    agent = get_agent(body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent {body.agent_id} not found")
    scenarios = await prepare_scenarios(dict(agent), body.tests, body.guidance, body.knowledge)
    return {"scenarios": scenarios}


@router.post("")
async def create_run(body: CreateRun) -> dict:
    """Insert a run row, launch the orchestrator in the background, return the id now."""
    if get_agent(body.agent_id) is None:
        raise HTTPException(status_code=404, detail=f"agent {body.agent_id} not found")
    # Sanitize the reviewed suite (it may contain user edits/additions).
    reviewed = normalize_scenarios(body.scenarios)
    if body.scenarios and not reviewed:
        raise HTTPException(status_code=400, detail="the submitted test suite is empty or invalid")
    run_id = insert_run(body.agent_id)
    # Fire-and-forget; the run progresses while the client polls GET /runs/{id}.
    asyncio.create_task(
        start_run(run_id, body.agent_id, body.tests, body.guidance, body.knowledge, reviewed)
    )
    return {"run_id": run_id}


@router.get("/demo/report")
def get_demo_report() -> dict:
    """Static, always-available sample report (demo insurance — no DB, no OpenAI)."""
    from app.demo_report import DEMO_REPORT

    return DEMO_REPORT


@router.get("/{run_id}")
def get_run_status(run_id: int) -> dict:
    """Dashboard poll target: status + reliability score (Phase 3) + progress counts."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return {
        "run_id": run_id,
        "status": run["status"],
        "reliability_score": run["reliability_score"],
        "counts": run_counts(run_id),
    }


@router.get("/{run_id}/report")
def get_report(run_id: int) -> dict:
    """Full nested report: run + score + breakdown + every conversation (messages + trace)."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    run = dict(run)
    breakdown = None
    if run.get("breakdown_json"):
        try:
            breakdown = json.loads(run["breakdown_json"])
        except (json.JSONDecodeError, TypeError):
            breakdown = None

    conversations = [
        build_conversation_payload(c["id"]) for c in get_conversations_for_run(run_id)
    ]

    return {
        "run": {
            "id": run["id"],
            "agent_id": run["agent_id"],
            "status": run["status"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        },
        "reliability_score": run["reliability_score"],
        "breakdown": breakdown,
        "conversations": [c for c in conversations if c],
    }
