"""Run orchestration — sequencing, fan-out, and failure policy. No I/O of its own.

Three workflows live here:

  start_run           one agent: suite -> play -> judge -> explain failures -> score
  start_run_group     N agents at once, one independent run each
  replay_conversation a single scenario re-played as a new conversation

Every external call goes through `app.core.activities`; nothing in this module touches
PostgreSQL, the LLM, or the agent under test directly. That separation is deliberate —
it is what lets these three functions become Temporal Workflows later without their
control flow changing, and it keeps the decisions about *when* work runs (and what
happens when it fails) in one readable place.
"""
import asyncio
from typing import Awaitable, Callable, Iterable, TypeVar

from app.config import AGENT_CONCURRENCY, WORK_CONCURRENCY
from app.core.activities import (
    explain_conversation_by_id,
    fail_run,
    finalize_run,
    judge_conversation_by_id,
    list_conversation_ids,
    list_failed_conversation_ids,
    load_agent,
    load_replay_context,
    persist_suite,
    play_scenario,
    prepare_scenarios,
    replay_scenario,
)

# Two levels of bounded concurrency, both process-wide.
#
#   _AGENTS  how many selected agents are crash-tested at once (the parallel fan-out).
#   _WORK    ceiling on in-flight scenario/judge/fix work across EVERY run in flight.
#
# _WORK is shared rather than per-run on purpose: a per-run semaphore would multiply by
# the number of agents, so four parallel agents would quadruple the load we put on the
# LLM and on the agents under test. Acquisition order is always _AGENTS then _WORK, and
# _WORK is only ever held around leaf work, so the pair cannot deadlock.
#
# Under Temporal these become worker configuration (max_concurrent_activities) rather
# than in-process semaphores, which is what makes them global instead of per-process.
_AGENTS = asyncio.Semaphore(AGENT_CONCURRENCY)
_WORK = asyncio.Semaphore(WORK_CONCURRENCY)

T = TypeVar("T")


async def _fan_out(
    items: Iterable[T],
    work: Callable[[T], Awaitable[object]],
    describe: Callable[[T], str],
) -> None:
    """Run `work` over every item concurrently, bounded by the shared work ceiling.

    The failure policy lives here and nowhere else: an item that raises is logged and
    skipped, never failing the rest of the batch. This single seam is where a Temporal
    RetryPolicy replaces "log and continue" — worth knowing that today's behaviour means
    a transient error silently drops that item's result.
    """
    async def _one(item: T) -> None:
        async with _WORK:
            try:
                await work(item)
            except Exception as e:
                print(f"[orchestrator] {describe(item)} failed: {e}")

    await asyncio.gather(*(_one(i) for i in items))


async def start_run(
    run_id: int,
    agent_id: int,
    selected_types: list[str],
    guidance: str = "",
    knowledge: str = "",
    scenarios: list[dict] | None = None,
) -> None:
    """Drive one run to completion. Never raises to the caller.

    `scenarios` is the user-reviewed, finalized test suite. When given it is executed
    as-is and NO generation happens; when omitted, a suite is generated first.
    """
    try:
        agent = await load_agent(agent_id)
        if agent is None:
            await fail_run(run_id)
            return

        # 1) Use the reviewed suite if the client supplied one; otherwise generate.
        suite = (
            [dict(s) for s in scenarios]
            if scenarios
            else await prepare_scenarios(agent, selected_types, guidance, knowledge)
        )

        # 2) Persist the suite, carrying each row's id back onto its dict for the runner.
        for scenario, scenario_id in zip(suite, await persist_suite(run_id, suite)):
            scenario["_id"] = scenario_id

        # 3) Play every scenario against the agent.
        await _fan_out(
            suite,
            lambda s: play_scenario(run_id, s, agent),
            lambda s: f"scenario '{s.get('title')}'",
        )

        # 4) Judge every conversation that resulted.
        await _fan_out(
            await list_conversation_ids(run_id),
            judge_conversation_by_id,
            lambda cid: f"judge for conv {cid}",
        )

        # 5) Explain + suggest fix — FAILURES ONLY (the pass/fail branch).
        await _fan_out(
            await list_failed_conversation_ids(run_id),
            explain_conversation_by_id,
            lambda cid: f"fix for conv {cid}",
        )

        # 6) Score + finalize.
        await finalize_run(run_id)
    except Exception as e:
        print(f"[orchestrator] run {run_id} errored: {e}")
        await fail_run(run_id)


async def start_run_group(
    targets: list[dict],
    selected_types: list[str],
    guidance: str = "",
    knowledge: str = "",
) -> None:
    """Drive a whole batch of runs — one per selected agent — concurrently.

    `targets` are already-inserted runs: each item is
    ``{"run_id": int, "agent_id": int, "scenarios": list[dict] | None}``.
    The router inserts the rows first so it can hand the client its ids immediately,
    then hands them here to execute.

    Agents are independent, so one agent erroring never touches the others; each run
    finalizes its own status and reliability score. Under Temporal each of these becomes
    a child workflow, which is what makes them independently resumable.
    """
    async def _one(target: dict) -> None:
        async with _AGENTS:
            run_id = target["run_id"]
            try:
                await start_run(
                    run_id,
                    target["agent_id"],
                    selected_types,
                    guidance,
                    knowledge,
                    target.get("scenarios"),
                )
            except Exception as e:
                # start_run already swallows its own failures; this is the last resort
                # so one dead agent cannot leave the batch hanging in "running".
                print(f"[orchestrator] run {run_id} in group crashed: {e}")
                await fail_run(run_id)

    await asyncio.gather(*(_one(t) for t in targets))


async def replay_conversation(conversation_id: int) -> int | None:
    """Re-play the scenario behind a conversation as a NEW conversation; returns its id.

    The same play -> judge -> (explain, if it failed) sequence as a run, over a single
    scenario. Deliberately unkeyed, so the original conversation is left intact for
    before/after comparison.
    """
    ctx = await load_replay_context(conversation_id)
    if ctx is None:
        return None

    new_conv_id = await replay_scenario(ctx["run_id"], ctx["scenario"], ctx["agent"])
    judged = await judge_conversation_by_id(new_conv_id)
    if judged.get("verdict") == "fail":
        await explain_conversation_by_id(new_conv_id)
    return new_conv_id
