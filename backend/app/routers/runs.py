"""/runs API — launch a crash-test run and poll its progress."""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.orchestrator import prepare_scenarios, start_run
from app.core.scenarios import normalize_scenarios
from app.db import (
    build_conversation_payload,
    default_customer_agent,
    get_agent,
    get_conversations_for_run,
    get_customer_agent,
    get_run,
    insert_run,
    replace_test_cases,
    run_counts,
)

router = APIRouter(prefix="/runs", tags=["runs"])


class GenerateScenarios(BaseModel):
    agent_id: int
    tests: list[str] = []
    guidance: str = ""   # optional user domain focus / edge cases
    knowledge: str = ""  # optional uploaded agent docs (authoritative ground truth)


class GenerateOne(BaseModel):
    agent_id: int
    description: str = ""       # optional detail about the test case the user wants
    test_type: str = "support"
    assigned_fault: str = "none"


class CreateRun(GenerateScenarios):
    # The user-reviewed, finalized test suite. When present it is executed verbatim and
    # NO scenarios are generated. Empty => generate (original single-shot behaviour).
    scenarios: list[dict] = []
    # Which customer-agent combination this run belongs to. Omitted for an agent connected
    # ad-hoc, which then falls back to that agent's "Unassigned" customer context.
    customer_agent_id: int | None = None


def _normalize_reviewed(scenarios: list[dict]) -> tuple[list[dict], list[str]]:
    """Sanitize a reviewed suite, keeping each case's `source` aligned with its copy."""
    reviewed: list[dict] = []
    sources: list[str] = []
    for raw in scenarios:
        norm = normalize_scenarios([raw])
        if norm:
            reviewed.append(norm[0])
            sources.append(str(raw.get("source") or "ai"))
    return reviewed, sources


def _save_reviewed(agent_id: int, customer_agent_id: int | None,
                   reviewed: list[dict], sources: list[str]) -> int:
    """Store the suite against its customer-agent combination; returns that combination id."""
    if customer_agent_id is not None:
        if get_customer_agent(customer_agent_id) is None:
            raise HTTPException(
                status_code=404, detail=f"customer-agent {customer_agent_id} not found"
            )
        combo_id = customer_agent_id
    else:
        # Agent connected ad-hoc, with no customer chosen.
        combo_id = default_customer_agent(agent_id)
    replace_test_cases(combo_id, reviewed, sources)
    return combo_id


@router.post("/test-cases")
def save_test_cases(body: CreateRun) -> dict:
    """Save the reviewed suite without running it. Same storage path as POST /runs."""
    if get_agent(body.agent_id) is None:
        raise HTTPException(status_code=404, detail=f"agent {body.agent_id} not found")
    reviewed, sources = _normalize_reviewed(body.scenarios)
    if not reviewed:
        raise HTTPException(status_code=400, detail="the submitted test suite is empty or invalid")
    combo_id = _save_reviewed(body.agent_id, body.customer_agent_id, reviewed, sources)
    return {"customer_agent_id": combo_id, "saved": len(reviewed)}


@router.post("/scenarios")
async def generate_run_scenarios(body: GenerateScenarios) -> dict:
    """Stage 1 — generate the test suite for review. Executes nothing, persists nothing."""
    agent = get_agent(body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent {body.agent_id} not found")
    scenarios = await prepare_scenarios(dict(agent), body.tests, body.guidance, body.knowledge)
    return {"scenarios": scenarios}


@router.post("/scenarios/one")
async def generate_one_scenario(body: GenerateOne) -> dict:
    """Generate a SINGLE test case from the user's description, for the Add Test Case dialog.

    Reuses the same generation pipeline as the full suite — the description becomes the
    guidance — then returns the one case that best matches the requested category.
    """
    agent = get_agent(body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent {body.agent_id} not found")

    scenarios = await prepare_scenarios(dict(agent), [body.test_type], body.description, "")
    if not scenarios:
        raise HTTPException(status_code=502, detail="could not generate a test case")

    match = next((s for s in scenarios if s.get("test_type") == body.test_type), scenarios[0])
    # Honour the fault the user picked in the dialog.
    if body.assigned_fault and body.assigned_fault != "none":
        match = {**match, "assigned_fault": body.assigned_fault}
    return {"scenario": match}


@router.post("")
async def create_run(body: CreateRun) -> dict:
    """Insert a run row, launch the orchestrator in the background, return the id now."""
    if get_agent(body.agent_id) is None:
        raise HTTPException(status_code=404, detail=f"agent {body.agent_id} not found")
    # Sanitize the reviewed suite (it may contain user edits/additions).
    reviewed, sources = _normalize_reviewed(body.scenarios)
    if body.scenarios and not reviewed:
        raise HTTPException(status_code=400, detail="the submitted test suite is empty or invalid")

    # Persist exactly what is about to run, against its customer-agent combination.
    if reviewed:
        _save_reviewed(body.agent_id, body.customer_agent_id, reviewed, sources)

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
