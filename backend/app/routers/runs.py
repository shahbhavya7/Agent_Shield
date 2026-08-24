"""/runs API — launch crash-test runs (one agent or many in parallel) and poll them."""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.orchestrator import prepare_scenarios, start_run_group
from app.core.scenarios import normalize_scenarios
from app.db import (
    build_conversation_payload,
    default_customer_agent,
    get_agent,
    get_conversations_for_run,
    get_customer_agent,
    get_run,
    get_runs_for_group,
    insert_run,
    insert_run_group,
    replace_test_cases,
    run_counts,
    run_group_exists,
)

router = APIRouter(prefix="/runs", tags=["runs"])

# Background runs are fire-and-forget, but asyncio only holds a weak reference to a task.
# Without a strong reference here the event loop can garbage-collect a run mid-flight —
# which shows up as a run that silently stops progressing. Tasks remove themselves.
_BACKGROUND: set[asyncio.Task] = set()


def _launch(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


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


class RunTarget(BaseModel):
    """One agent to crash-test in a batch, with its own reviewed suite.

    Each selected agent carries its own test cases, because a suite belongs to a
    customer-agent combination rather than to the batch.
    """
    agent_id: int
    customer_agent_id: int | None = None
    scenarios: list[dict] = []


class CreateRunGroup(BaseModel):
    targets: list[RunTarget]
    tests: list[str] = []
    guidance: str = ""
    knowledge: str = ""


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


def _launch_group(
    targets: list[RunTarget], tests: list[str], guidance: str, knowledge: str
) -> dict:
    """Validate + persist every target, insert one run each, then run the batch.

    All validation happens before any run row is inserted, so a bad target rejects the
    whole request instead of leaving half a batch running.
    """
    if not targets:
        raise HTTPException(status_code=400, detail="no agents were selected")

    prepared: list[tuple[RunTarget, list[dict], list[str]]] = []
    for t in targets:
        if get_agent(t.agent_id) is None:
            raise HTTPException(status_code=404, detail=f"agent {t.agent_id} not found")
        reviewed, sources = _normalize_reviewed(t.scenarios)
        if t.scenarios and not reviewed:
            raise HTTPException(
                status_code=400,
                detail=f"the submitted test suite for agent {t.agent_id} is empty or invalid",
            )
        prepared.append((t, reviewed, sources))

    group_id = insert_run_group()
    launched: list[dict] = []
    orchestrator_targets: list[dict] = []
    for t, reviewed, sources in prepared:
        # Persist exactly what is about to run, against its customer-agent combination.
        if reviewed:
            _save_reviewed(t.agent_id, t.customer_agent_id, reviewed, sources)
        run_id = insert_run(t.agent_id, group_id, t.customer_agent_id)
        launched.append({
            "run_id": run_id,
            "agent_id": t.agent_id,
            "customer_agent_id": t.customer_agent_id,
        })
        orchestrator_targets.append({
            "run_id": run_id, "agent_id": t.agent_id, "scenarios": reviewed,
        })

    # Fire-and-forget; the batch progresses while the client polls GET /runs/group/{id}.
    _launch(start_run_group(orchestrator_targets, tests, guidance, knowledge))
    return {"group_id": group_id, "runs": launched}


@router.post("/group")
async def create_run_group(body: CreateRunGroup) -> dict:
    """Crash-test every selected agent in parallel — one run each, one group over them."""
    return _launch_group(body.targets, body.tests, body.guidance, body.knowledge)


@router.post("")
async def create_run(body: CreateRun) -> dict:
    """Single-agent run. A batch of one, so both paths share the same execution code."""
    target = RunTarget(
        agent_id=body.agent_id,
        customer_agent_id=body.customer_agent_id,
        scenarios=body.scenarios,
    )
    result = _launch_group([target], body.tests, body.guidance, body.knowledge)
    return {"run_id": result["runs"][0]["run_id"], "group_id": result["group_id"]}


# NOTE: the /group routes must stay above GET /{run_id} — FastAPI matches in declaration
# order, so "group" would otherwise be parsed as a run id.
@router.get("/group/{group_id}")
def get_run_group_status(group_id: int) -> dict:
    """Batch poll target: one entry per agent, plus the aggregate the dashboard waits on."""
    if not run_group_exists(group_id):
        raise HTTPException(status_code=404, detail=f"run group {group_id} not found")

    runs = [dict(r) for r in get_runs_for_group(group_id)]
    entries = [{
        "run_id": r["id"],
        "agent_id": r["agent_id"],
        "agent_name": r.get("agent_name"),
        "customer_name": r.get("customer_name"),
        "customer_agent_id": r.get("customer_agent_id"),
        "status": r["status"],
        "reliability_score": r["reliability_score"],
        "counts": run_counts(r["id"]),
    } for r in runs]

    # The batch is done when no run is still in flight; individual failures stay visible
    # per agent rather than failing the whole batch.
    finished = [e for e in entries if e["status"] in ("done", "error")]
    status = "done" if entries and len(finished) == len(entries) else "running"
    return {
        "group_id": group_id,
        "status": status,
        "total": len(entries),
        "finished": len(finished),
        "errored": sum(1 for e in entries if e["status"] == "error"),
        "runs": entries,
    }


@router.get("/group/{group_id}/report")
def get_group_report(group_id: int) -> dict:
    """Every agent's full report in one response, in the order the batch was launched."""
    if not run_group_exists(group_id):
        raise HTTPException(status_code=404, detail=f"run group {group_id} not found")

    reports = []
    for r in get_runs_for_group(group_id):
        r = dict(r)
        payload = _report_payload(r)
        payload["agent_id"] = r["agent_id"]
        payload["agent_name"] = r.get("agent_name")
        payload["customer_name"] = r.get("customer_name")
        payload["customer_agent_id"] = r.get("customer_agent_id")
        reports.append(payload)
    return {"group_id": group_id, "reports": reports}


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


def _report_payload(run: dict) -> dict:
    """Full nested report for one run: run + score + breakdown + conversations."""
    breakdown = None
    if run.get("breakdown_json"):
        try:
            breakdown = json.loads(run["breakdown_json"])
        except (json.JSONDecodeError, TypeError):
            breakdown = None

    conversations = [
        build_conversation_payload(c["id"]) for c in get_conversations_for_run(run["id"])
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


@router.get("/{run_id}/report")
def get_report(run_id: int) -> dict:
    """One agent's report. The group endpoint returns the same shape, once per agent."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return _report_payload(dict(run))
