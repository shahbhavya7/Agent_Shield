"""Run orchestrator — owns the lifecycle of a single run.

Phase 2:  runner (generate scenarios -> play each, record traces).
Phase 3:  will extend with judge -> explain_and_fix(failures only) -> scoring.
"""
import asyncio

from app.core.fixer import explain_and_fix
from app.core.judge import judge_conversation
from app.core.runner import run_scenario
from app.core.scenarios import generate_scenarios
from app.core.scoring import compute
from app.db import (
    get_agent,
    get_conversation,
    get_conversations_for_run,
    insert_scenario,
    update_run,
)

CONCURRENCY = 3


async def prepare_scenarios(
    agent: dict, selected_types: list[str], guidance: str = "", knowledge: str = ""
) -> list[dict]:
    """Stage 1 (generate only): resolve the agent's domain, then build the scenario bank.

    Pure generation — nothing is persisted and nothing is executed. Used both by the
    review endpoint (POST /runs/scenarios) and by start_run when no reviewed suite is given.
    """
    # Resolve the agent's domain profile. Priority: uploaded docs > description >
    # auto-discovery (probe the agent and infer what it does).
    description = agent.get("description") or ""
    if not (knowledge and knowledge.strip()):
        from app.core.discover import discover_agent, looks_generic

        if looks_generic(description):
            print(f"[orchestrator] no docs/description — auto-discovering agent {agent.get('id')}")
            description = await discover_agent(agent)

    # Generate scenarios (falls back to hardcoded bank on LLM failure).
    scenarios = await generate_scenarios(
        description or "A customer-support agent.",
        selected_types,
        guidance,
        knowledge,
    )
    # Demo safety: cap the number of scenarios so a live run stays fast.
    from app.config import MAX_SCENARIOS

    return scenarios[:MAX_SCENARIOS]


async def start_run(
    run_id: int,
    agent_id: int,
    selected_types: list[str],
    guidance: str = "",
    knowledge: str = "",
    scenarios: list[dict] | None = None,
) -> None:
    """Drive one run to completion in the background. Never raises to the caller.

    `scenarios` is the user-reviewed, finalized test suite. When given, it is executed
    as-is and NO generation happens. When omitted, scenarios are generated first
    (the original single-shot behaviour).
    """
    try:
        agent = get_agent(agent_id)
        if agent is None:
            update_run(run_id, status="error", finished=True)
            return
        agent = dict(agent)

        # 1) Use the reviewed suite if the client supplied one; otherwise generate.
        if not scenarios:
            scenarios = await prepare_scenarios(agent, selected_types, guidance, knowledge)
        else:
            scenarios = [dict(s) for s in scenarios]

        # 2) Persist scenario rows; keep the db id on each dict for the runner.
        for s in scenarios:
            s["_id"] = insert_scenario(run_id, s)

        # 3) Play all scenarios concurrently (bounded).
        sem = asyncio.Semaphore(CONCURRENCY)

        async def _guarded(scenario: dict) -> None:
            async with sem:
                try:
                    await run_scenario(run_id, scenario, agent)
                except Exception as e:
                    # One bad scenario must never fail the whole run.
                    print(f"[orchestrator] scenario '{scenario.get('title')}' failed: {e}")

        await asyncio.gather(*(_guarded(s) for s in scenarios))

        # 4) Judge every conversation (bounded concurrency).
        convs = get_conversations_for_run(run_id)

        async def _judge(conv) -> None:
            async with sem:
                try:
                    await judge_conversation(conv)
                except Exception as e:
                    print(f"[orchestrator] judge failed for conv {conv['id']}: {e}")

        await asyncio.gather(*(_judge(c) for c in convs))

        # 5) Explain + suggest fix — FAILURES ONLY (the pass/fail branch).
        failures = [c for c in get_conversations_for_run(run_id) if c["verdict"] == "fail"]

        async def _fix(conv) -> None:
            async with sem:
                try:
                    await explain_and_fix(conv)
                except Exception as e:
                    print(f"[orchestrator] fix failed for conv {conv['id']}: {e}")

        await asyncio.gather(*(_fix(c) for c in failures))

        # 6) Score + finalize.
        compute(run_id)
        update_run(run_id, status="done", finished=True)
    except Exception as e:
        print(f"[orchestrator] run {run_id} errored: {e}")
        update_run(run_id, status="error", finished=True)


async def replay_conversation(conversation_id: int) -> int | None:
    """Re-run the scenario behind a conversation (new conversation), judge + fix, return its id."""
    import json as _json

    from app.db import get_scenario

    conv = get_conversation(conversation_id)
    if conv is None:
        return None
    conv = dict(conv)
    scenario_row = get_scenario(conv["scenario_id"])
    if scenario_row is None:
        return None
    s = dict(scenario_row)
    scenario = {
        "_id": s["id"],
        "title": s["title"],
        "user_goal": s["user_goal"],
        "test_type": s["test_type"],
        "assigned_fault": s["assigned_fault"],
        "expected_behavior": s["expected_behavior"],
        "seed_turns": _json.loads(s["seed_turns_json"] or "[]"),
    }

    agent_row = get_agent(_run_agent_id(conv["run_id"]))
    if agent_row is None:
        return None
    agent = dict(agent_row)

    new_conv_id = await run_scenario(conv["run_id"], scenario, agent)
    judged = await judge_conversation(get_conversation(new_conv_id))
    if judged.get("verdict") == "fail":
        await explain_and_fix(get_conversation(new_conv_id))
    return new_conv_id


def _run_agent_id(run_id: int) -> int:
    from app.db import get_run

    run = get_run(run_id)
    return run["agent_id"] if run else 0
