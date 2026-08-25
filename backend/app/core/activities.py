"""Activity boundary — every unit of external I/O that a run performs, named in one place.

Split out from the orchestrator so that workflow-level logic (sequencing, fan-out,
failure policy) holds no I/O of its own. Each function below is exactly one unit of work
against the outside world: PostgreSQL, the LLM, or the agent under test.

Every activity here is:
  * addressed by plain serializable arguments — ints, dicts, lists — and never by a live
    database row, because a row cannot cross a process boundary;
  * idempotent, so re-executing it converges instead of duplicating (see the idem_key
    work in app.db);
  * a single unit of work, so it can later carry its own timeout and retry policy.

These are the functions that become Temporal Activities. The ones marked SYNC BODY block
on psycopg and would be registered to run on a thread pool; the rest are already
genuinely awaitable. Nothing here decides *when* it runs — that is the orchestrator's job.
"""
import json as _json

from app.config import MAX_SCENARIOS
from app.core.fixer import explain_and_fix
from app.core.judge import judge_conversation
from app.core.runner import run_scenario
from app.core.scenarios import generate_scenarios
from app.core.scoring import compute
from app.db import (
    get_agent,
    get_conversation,
    get_conversations_for_run,
    get_run,
    get_scenario,
    replace_scenarios,
    update_run,
)


# ---------------------------------------------------------------------------
# Agent + suite
# ---------------------------------------------------------------------------
async def load_agent(agent_id: int) -> dict | None:
    """SYNC BODY. The agent row as a plain dict, or None if it no longer exists."""
    agent = get_agent(agent_id)
    return dict(agent) if agent is not None else None


async def prepare_scenarios(
    agent: dict, selected_types: list[str], guidance: str = "", knowledge: str = ""
) -> list[dict]:
    """LLM (+ HTTP when auto-discovering). Build the scenario bank for one agent.

    Pure generation — nothing is persisted and nothing is executed. Used both by the
    review endpoint (POST /runs/scenarios) and by a run that was given no reviewed suite.
    """
    # Resolve the agent's domain profile. Priority: uploaded docs > description >
    # auto-discovery (probe the agent and infer what it does).
    # Docs uploaded on the Connect step are stored on the agent, so a caller that has no
    # docs in hand (e.g. the Existing Agent path) still generates grounded test cases.
    knowledge = knowledge or (agent.get("knowledge") or "")
    description = agent.get("description") or ""
    if not (knowledge and knowledge.strip()):
        # Imported here rather than at module scope: discovery is the uncommon path and
        # pulls in the HTTP adapter.
        from app.core.discover import discover_agent, looks_generic

        if looks_generic(description):
            print(f"[activities] no docs/description — auto-discovering agent {agent.get('id')}")
            description = await discover_agent(agent)

    # Generate scenarios (falls back to a hardcoded bank on LLM failure).
    scenarios = await generate_scenarios(
        description or "A customer-support agent.",
        selected_types,
        guidance,
        knowledge,
    )
    # Demo safety: cap the number of scenarios so a live run stays fast.
    return scenarios[:MAX_SCENARIOS]


async def persist_suite(run_id: int, scenarios: list[dict]) -> list[int]:
    """SYNC BODY. Make `scenarios` the run's scenario set; returns ids in input order.

    Replaces rather than appends, so re-executing cannot duplicate the suite.
    """
    return replace_scenarios(run_id, scenarios)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
async def play_scenario(run_id: int, scenario: dict, agent: dict) -> int:
    """HTTP + LLM + DB. Play one scenario against the agent; returns its conversation id.

    Keyed on (run, scenario), so re-executing reuses the same conversation and rewrites
    its transcript instead of leaving a half-finished orphan behind — an orphan would be
    scored as a free pass.
    """
    return await run_scenario(
        run_id, scenario, agent,
        idem_key=f"run:{run_id}:scenario:{scenario['_id']}",
    )


async def replay_scenario(run_id: int, scenario: dict, agent: dict) -> int:
    """HTTP + LLM + DB. Play a scenario as a deliberately NEW conversation.

    Unkeyed on purpose: replay exists to produce a second, independent conversation for
    a scenario that already has one, so the caller can compare before and after a fix.
    """
    return await run_scenario(run_id, scenario, agent, idem_key=None)


async def judge_conversation_by_id(conversation_id: int) -> dict:
    """DB + LLM. Score one conversation and persist its verdict.

    Takes an id rather than a row: a psycopg row is not serializable across a process
    boundary, so the row is loaded inside the activity.
    """
    conv = get_conversation(conversation_id)
    if conv is None:
        return {}
    return await judge_conversation(conv)


async def explain_conversation_by_id(conversation_id: int) -> dict:
    """DB + LLM. Explain one failure and suggest a fix. Id in, for the same reason."""
    conv = get_conversation(conversation_id)
    if conv is None:
        return {}
    return await explain_and_fix(conv)


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------
async def list_conversation_ids(run_id: int) -> list[int]:
    """SYNC BODY. Every conversation in the run, oldest first."""
    return [c["id"] for c in get_conversations_for_run(run_id)]


async def list_failed_conversation_ids(run_id: int) -> list[int]:
    """SYNC BODY. Only the conversations the judge failed — the explain+fix branch."""
    return [c["id"] for c in get_conversations_for_run(run_id) if c["verdict"] == "fail"]


async def finalize_run(run_id: int) -> dict:
    """SYNC BODY. Score the run, persist the breakdown, and mark it done."""
    result = compute(run_id)
    update_run(run_id, status="done", finished=True)
    return result


async def fail_run(run_id: int) -> None:
    """SYNC BODY. Mark a run as errored so it never hangs in 'running'."""
    update_run(run_id, status="error", finished=True)


# ---------------------------------------------------------------------------
# Replay context
# ---------------------------------------------------------------------------
async def load_replay_context(conversation_id: int) -> dict | None:
    """SYNC BODY. Everything a replay needs, in one round trip.

    Returns ``{"run_id", "scenario", "agent"}`` — the scenario shaped exactly as the
    runner expects it — or None if any part of the chain is missing.
    """
    conv = get_conversation(conversation_id)
    if conv is None:
        return None
    conv = dict(conv)

    scenario_row = get_scenario(conv["scenario_id"])
    if scenario_row is None:
        return None
    s = dict(scenario_row)

    run = get_run(conv["run_id"])
    agent_row = get_agent(run["agent_id"]) if run else None
    if agent_row is None:
        return None

    return {
        "run_id": conv["run_id"],
        "scenario": {
            "_id": s["id"],
            "title": s["title"],
            "user_goal": s["user_goal"],
            "test_type": s["test_type"],
            "assigned_fault": s["assigned_fault"],
            "expected_behavior": s["expected_behavior"],
            "seed_turns": _json.loads(s["seed_turns_json"] or "[]"),
        },
        "agent": dict(agent_row),
    }
