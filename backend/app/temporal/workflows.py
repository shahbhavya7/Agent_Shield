"""Temporal workflows — the durable form of the orchestration in app.core.orchestrator.

    RunGroupWorkflow   parent: one "Run Selected Agents" click. Starts one child per
                       agent, bounded by agent_concurrency, and waits for them all.
    AgentTestWorkflow  child: one agent, one run. The same six stages as `start_run`.

A child workflow per agent — rather than one workflow with a fan-out of activities — is
what makes the agents genuinely independent: each gets its own durable history, its own
retry envelope, its own entry in the Temporal UI, and can be resumed, cancelled or
inspected without touching its siblings.

Nothing in here does I/O. Workflow code is replayed from history to rebuild state, so it
must be deterministic: every external call is an activity, and there is no clock, no
randomness and no database access at this level. The control flow is a direct translation
of the asyncio orchestrator — same stages, same order, same "log it and carry on" policy
for an individual failure.

The imports below go through `workflow.unsafe.imports_passed_through()` because the
workflow sandbox otherwise re-imports every module it sees, and these pull in openai,
psycopg and httpx. Passing them through is safe precisely because workflow code only ever
references the activity *functions*, never calls them.
"""
import asyncio
from dataclasses import dataclass, field

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.core.activities import (
        explain_conversation_by_id,
        fail_run,
        finalize_run,
        judge_conversation_by_id,
        list_conversation_ids,
        list_failed_conversation_ids,
        load_agent,
        persist_suite,
        play_scenario,
        play_voice_scenario,
        prepare_scenarios,
    )
    from app.temporal.policies import options_for

# A literal, not app.config.AGENT_CONCURRENCY. A workflow default must be stable across
# replays, and an environment variable is not: if it changed between the original run and
# a replay, the workflow would take a different path. The caller passes the configured
# value in explicitly (see app.config.AGENT_CONCURRENCY), which is what makes it tunable
# without making it non-deterministic.
DEFAULT_AGENT_CONCURRENCY = 3
# One child's slice of the worker's activity pool. A literal for the same replay-safety
# reason as above; the router computes the real value from WORK_CONCURRENCY and how many
# agents are actually running, and passes it in. The shares of concurrently-running
# children sum to the worker ceiling, so this divides the pool rather than multiplying it.
DEFAULT_WORK_SHARE = 2


@dataclass
class AgentTestInput:
    """One agent to crash-test, and the reviewed suite to test it with.

    `run_id` is inserted by the caller before the workflow starts, so the client already
    has an id to poll and every activity has a row to write to.
    """
    run_id: int
    agent_id: int
    selected_types: list[str] = field(default_factory=list)
    guidance: str = ""
    knowledge: str = ""
    # The user-reviewed suite. When present it is executed verbatim and nothing is
    # generated — same contract as start_run's `scenarios` argument.
    scenarios: list[dict] | None = None
    # How many of this run's activities may be in flight at once — its fair share of the
    # worker's pool, so sibling agents progress together instead of queueing behind it.
    work_share: int = DEFAULT_WORK_SHARE


@dataclass
class RunGroupInput:
    """A batch of agents launched together, and how many may run at once."""
    group_id: int
    targets: list[AgentTestInput] = field(default_factory=list)
    agent_concurrency: int = DEFAULT_AGENT_CONCURRENCY


async def _bounded_gather(
    factories: list, labels: list[str], limit: int
) -> None:
    """Run every item concurrently, at most `limit` at a time, logging failures.

    Two jobs in one place.

    *Failure policy* — the workflow-side equivalent of the orchestrator's `_fan_out`: one
    bad scenario must never fail the rest of the run. By the time an exception reaches
    here the activity has already exhausted its retry policy, so unlike the asyncio
    version a merely transient error no longer loses the result. Cancellation is the one
    thing re-raised, so a cancelled workflow is never silently swallowed.

    *Fair share* — `limit` is this child's slice of the worker's activity pool. It matters
    that the activity is CREATED inside the semaphore, not merely awaited there: Temporal
    schedules an activity when its coroutine is awaited, so building them all up front
    would queue the whole batch regardless of the limit. Hence factories rather than
    coroutines.

    Without this limit the pool is claimed first-come-first-served, and a child that wins
    the scheduling race by milliseconds takes every slot — which is what made two "parallel"
    agents run one after the other.
    """
    sem = asyncio.Semaphore(max(1, limit))

    async def _one(make, label: str) -> None:
        async with sem:
            try:
                await make()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                workflow.logger.warning("%s failed: %s", label, e)

    await asyncio.gather(*(_one(m, l) for m, l in zip(factories, labels)))


@workflow.defn
class AgentTestWorkflow:
    """Crash-test ONE agent: suite -> play -> judge -> explain failures -> score.

    A direct translation of `app.core.orchestrator.start_run`. Returns a summary rather
    than raising for an expected outcome (a missing agent), and re-raises for an
    unexpected one so that a genuine infrastructure failure is visible as a failed
    workflow in the Temporal UI instead of hiding inside a status column.
    """

    @workflow.run
    async def run(self, inp: AgentTestInput) -> dict:
        try:
            agent = await workflow.execute_activity(
                load_agent, inp.agent_id, **options_for(load_agent)
            )
            if agent is None:
                # A data condition, not a system failure: the workflow completes.
                await workflow.execute_activity(
                    fail_run, inp.run_id, **options_for(fail_run)
                )
                return {"run_id": inp.run_id, "status": "error", "reason": "agent not found"}

            # 1) Use the reviewed suite if one was supplied; otherwise generate.
            if inp.scenarios:
                suite = [dict(s) for s in inp.scenarios]
            else:
                suite = await workflow.execute_activity(
                    prepare_scenarios,
                    args=[agent, inp.selected_types, inp.guidance, inp.knowledge],
                    **options_for(prepare_scenarios),
                )

            # 2) Persist the suite, carrying each row's id back onto its dict.
            scenario_ids = await workflow.execute_activity(
                persist_suite, args=[inp.run_id, suite], **options_for(persist_suite)
            )
            for scenario, scenario_id in zip(suite, scenario_ids):
                scenario["_id"] = scenario_id

            # 3) Play every scenario against the agent, bounded by this run's share.
            #
            # The worker's max_concurrent_activities is still the global ceiling; this
            # share only decides how much of it ONE run may hold, so sibling agents get
            # slots too. Shares of concurrently-running children sum to the ceiling, so
            # this divides the pool rather than multiplying it.
            #
            # Modality decides which activity plays the scenario — everything else about
            # the six stages is identical. A "voice" agent is reached over the same
            # black-box HTTP JSON contract as chat, but play_voice_scenario bridges each
            # turn through TTS/STT (app.core.voice_caller) before/after that HTTP call
            # (Phase 2B), so the Judge and every later stage still only ever see text.
            # This is the only place voice testing diverges from the existing path.
            play_fn = play_voice_scenario if agent.get("modality") == "voice" else play_scenario
            await _bounded_gather(
                [
                    (lambda s=scenario: workflow.execute_activity(
                        play_fn,
                        args=[inp.run_id, s, agent],
                        **options_for(play_fn),
                    ))
                    for scenario in suite
                ],
                [f"scenario '{s.get('title')}'" for s in suite],
                inp.work_share,
            )

            # 4) Judge every conversation that resulted.
            conversation_ids = await workflow.execute_activity(
                list_conversation_ids, inp.run_id, **options_for(list_conversation_ids)
            )
            await _bounded_gather(
                [
                    (lambda c=cid: workflow.execute_activity(
                        judge_conversation_by_id,
                        c,
                        **options_for(judge_conversation_by_id),
                    ))
                    for cid in conversation_ids
                ],
                [f"judge for conv {cid}" for cid in conversation_ids],
                inp.work_share,
            )

            # 5) Explain + suggest fix — FAILURES ONLY (the pass/fail branch).
            failed_ids = await workflow.execute_activity(
                list_failed_conversation_ids,
                inp.run_id,
                **options_for(list_failed_conversation_ids),
            )
            await _bounded_gather(
                [
                    (lambda c=cid: workflow.execute_activity(
                        explain_conversation_by_id,
                        c,
                        **options_for(explain_conversation_by_id),
                    ))
                    for cid in failed_ids
                ],
                [f"fix for conv {cid}" for cid in failed_ids],
                inp.work_share,
            )

            # 6) Score + finalize.
            result = await workflow.execute_activity(
                finalize_run, inp.run_id, **options_for(finalize_run)
            )
            return {
                # finalize_run reports "error" when the agent never once answered, and the
                # group summary below counts anything other than "done" as an errored run.
                "status": result.get("status", "done"),
                "run_id": inp.run_id,
                "reliability_score": result.get("reliability_score"),
                "scenarios": len(suite),
                "conversations": len(conversation_ids),
                "failed": len(failed_ids),
            }

        except asyncio.CancelledError:
            raise
        except Exception:
            # Mark the run errored so it never hangs in "running" — the same guarantee
            # start_run gives — then re-raise so the failure is visible as a failed
            # workflow rather than only as a status column in PostgreSQL.
            await workflow.execute_activity(
                fail_run, inp.run_id, **options_for(fail_run)
            )
            raise


@workflow.defn
class RunGroupWorkflow:
    """Crash-test every selected agent, one child workflow each, bounded and concurrent.

    A direct translation of `app.core.orchestrator.start_run_group`. Agents are
    independent, so one child failing never cancels its siblings.
    """

    @workflow.run
    async def run(self, inp: RunGroupInput) -> dict:
        if not inp.targets:
            return {
                "group_id": inp.group_id, "total": 0,
                "succeeded": 0, "errored": [], "crashed": [], "runs": [],
            }

        # The agent-level limit, preserved from the asyncio orchestrator's _AGENTS
        # semaphore. Created per workflow instance, never at module scope: module state is
        # shared across workflow instances and workers, which would make replay
        # non-deterministic. Scoped to this group rather than the whole process — a
        # difference worth knowing, and the reason the *work* ceiling moved to the worker.
        limiter = asyncio.Semaphore(max(1, inp.agent_concurrency))

        async def _child(target: AgentTestInput) -> dict:
            async with limiter:
                return await workflow.execute_child_workflow(
                    AgentTestWorkflow.run,
                    target,
                    # Deterministic and unique per run, so re-submitting the same group
                    # cannot start a second copy of an agent's test.
                    id=f"agentshield-run-{target.run_id}",
                    task_queue=workflow.info().task_queue,
                    # One attempt only. A child that failed has already spent tokens on
                    # the scenarios it completed; re-running the whole agent test would
                    # pay for them again. Individual activities retry on their own.
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )

        results = await asyncio.gather(
            *(_child(t) for t in inp.targets), return_exceptions=True
        )

        summaries: list[dict] = []
        crashed: list[int] = []
        for target, result in zip(inp.targets, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                workflow.logger.warning(
                    "agent test for run %s failed: %s", target.run_id, result
                )
                crashed.append(target.run_id)
                # Safety net. The child marks its own run errored before re-raising, but
                # if it died before reaching that (and it gets no second attempt), this
                # keeps the run from being stranded in "running" forever. fail_run is an
                # idempotent UPDATE, so doing it twice costs nothing.
                await workflow.execute_activity(
                    fail_run, target.run_id, **options_for(fail_run)
                )
                summaries.append({"run_id": target.run_id, "status": "error"})
            else:
                summaries.append(result)

        # Counted by RUN outcome, not by child-workflow outcome. A child that completes
        # while reporting status "error" (a missing agent, say) is an errored run, and
        # saying otherwise would make the summary read better than the truth.
        errored = [s["run_id"] for s in summaries if s.get("status") != "done"]
        return {
            "group_id": inp.group_id,
            "total": len(inp.targets),
            "succeeded": len(inp.targets) - len(errored),
            "errored": errored,
            # The subset of `errored` where the child workflow itself failed, rather than
            # finishing and reporting a bad outcome. These are the infrastructure ones.
            "crashed": crashed,
            "runs": summaries,
        }
