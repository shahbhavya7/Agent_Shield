"""Temporal worker entry point — the process that actually executes workflow code.

Run it from backend/ with:
    .venv/bin/python -m app.temporal.worker

It needs a Temporal server to poll:
    temporal server start-dev          # gRPC on :7233, Web UI on http://localhost:8233

The worker is a SEPARATE process from FastAPI on purpose. FastAPI serves requests and
knows nothing about Temporal; the worker owns execution. Every activity in
app.core.activities is registered here, and RunGroupWorkflow/AgentTestWorkflow orchestrate
them. Nothing calls those workflows on the request path yet — FastAPI still runs the
asyncio orchestrator — so starting or stopping this process does not affect the running
application.

Sync vs async activities is the load-bearing detail. Activities whose bodies block on
psycopg are plain `def`, and Temporal dispatches those to `activity_executor` — a thread
pool — so a slow query never stalls the event loop that is polling Temporal and driving
the awaitable LLM/HTTP activities. Had those been written `async def`, every query would
have blocked the loop instead.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from app.config import (
    AGENT_CONCURRENCY,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    TEMPORAL_TASK_QUEUE,
    WORK_CONCURRENCY,
    WORKFLOW_TASK_CONCURRENCY,
)
from app.temporal.client import get_client
from app.temporal.health import (
    ConcurrencyProbeWorkflow,
    HealthWorkflow,
    NonRetryableProbeWorkflow,
    RetryProbeWorkflow,
    always_fails_with_value_error,
    flaky_external_call,
    ping,
    slow_probe,
    thread_probe,
)
from app.temporal.policies import ALL_ACTIVITIES, options_for
from app.temporal.workflows import AgentTestWorkflow, RunGroupWorkflow

# The health check's own activities, kept apart from the real ones so it is obvious which
# are disposable wiring checks.
HEALTH_ACTIVITIES = [
    ping, thread_probe, flaky_external_call, always_fails_with_value_error, slow_probe,
]


def _check_policies() -> None:
    """Fail at startup if any registered activity has no timeout/retry policy.

    Better here than at runtime: a missing policy means Temporal's defaults, which for a
    paid LLM call is no timeout and unlimited retries.
    """
    missing = []
    for fn in ALL_ACTIVITIES:
        try:
            options_for(fn)
        except KeyError:
            missing.append(fn.__name__)
    if missing:
        raise RuntimeError(
            "activities registered with no timeout/retry policy: " + ", ".join(missing)
        )


def _describe(activities: list) -> tuple[list[str], list[str]]:
    """Split registered activities into (async, sync) by their Temporal definition."""
    async_names, sync_names = [], []
    for fn in activities:
        defn = getattr(fn, "__temporal_activity_definition", None)
        name = defn.name if defn is not None else fn.__name__
        (async_names if getattr(defn, "is_async", True) else sync_names).append(name)
    return async_names, sync_names


async def main() -> None:
    _check_policies()

    client = await get_client()
    print(
        f"[worker] connected to {TEMPORAL_ADDRESS} (namespace={TEMPORAL_NAMESPACE})",
        flush=True,
    )

    activities = [*ALL_ACTIVITIES, *HEALTH_ACTIVITIES]
    async_names, sync_names = _describe(activities)

    # Threads for the synchronous (psycopg-bound) activities. Sized to WORK_CONCURRENCY
    # so the thread pool can never become the narrower bottleneck.
    activity_executor = ThreadPoolExecutor(
        max_workers=WORK_CONCURRENCY, thread_name_prefix="activity"
    )

    # Product workflows first, then the disposable wiring/policy probes.
    workflows = [RunGroupWorkflow, AgentTestWorkflow]
    probe_workflows = [
        HealthWorkflow, RetryProbeWorkflow, NonRetryableProbeWorkflow,
        ConcurrencyProbeWorkflow,
    ]

    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[*workflows, *probe_workflows],
        activities=activities,
        # THE limit that bounds real load. Every workflow on this worker draws activity
        # slots from this one pool, so N concurrent agent tests share it rather than each
        # getting their own window onto the LLM and the agents under test. As a worker
        # setting it is a genuinely global ceiling, unlike the in-process asyncio semaphore
        # it replaces, which only ever bounded a single uvicorn process.
        max_concurrent_activities=WORK_CONCURRENCY,
        # Workflow *decisions*, not agent tests. Set generously and on purpose: a workflow
        # waiting on an activity holds no slot here, so a small value would throttle
        # scheduling without capping any work — starving concurrency instead of bounding
        # it. Independent agent-test workflows must be free to interleave here.
        max_concurrent_workflow_tasks=WORKFLOW_TASK_CONCURRENCY,
        activity_executor=activity_executor,
    )

    print(
        "[worker] workflows: " + ", ".join(w.__name__ for w in workflows)
        + "  |  probes: " + ", ".join(w.__name__ for w in probe_workflows),
        flush=True,
    )
    print(f"[worker] registered {len(activities)} activities", flush=True)
    print(f"[worker]   async (event loop): {', '.join(sorted(async_names))}", flush=True)
    print(f"[worker]   sync  (thread pool): {', '.join(sorted(sync_names))}", flush=True)
    print(
        "[worker] concurrency: "
        f"{WORK_CONCURRENCY} activities (global load ceiling), "
        f"{WORKFLOW_TASK_CONCURRENCY} workflow tasks (scheduling headroom), "
        f"{AGENT_CONCURRENCY} agents per batch (set by the caller, enforced in the workflow)",
        flush=True,
    )
    print(
        f"[worker] polling task queue '{TEMPORAL_TASK_QUEUE}' — Ctrl-C to stop",
        flush=True,
    )
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[worker] stopped", flush=True)
