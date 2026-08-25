"""Trivial workflows whose only job is to prove the wiring works.

Wiring checks, not part of the product. Between them they confirm that the server is up,
that the worker is polling the right task queue, that a workflow can dispatch an activity
and get a result back, and — via thread_probe — that SYNC activities really are dispatched
to the worker's thread pool rather than run on its event loop. Delete once real workflows
exist.
"""
import threading
from datetime import timedelta

from temporalio import activity, workflow


@activity.defn
async def ping(name: str) -> str:
    """The smallest possible activity — no I/O, so it can never fail for external reasons."""
    return f"pong:{name}"


@activity.defn
def thread_probe() -> str:
    """A SYNC activity that reports which thread ran it.

    Deliberately a plain `def`, exactly like the psycopg-bound activities. If Temporal is
    handling sync activities correctly this returns a worker-pool thread name (prefix
    "activity"), proving those activities never execute on the event loop.
    """
    return threading.current_thread().name


@workflow.defn
class HealthWorkflow:
    """Dispatches one activity and returns its result."""

    @workflow.run
    async def run(self, name: str) -> str:
        pong = await workflow.execute_activity(
            ping,
            name,
            start_to_close_timeout=timedelta(seconds=10),
        )
        # Same call shape, but a sync activity — so the result tells us where sync bodies
        # actually run.
        thread = await workflow.execute_activity(
            thread_probe,
            start_to_close_timeout=timedelta(seconds=10),
        )
        return f"{pong} (sync activity ran on thread: {thread})"
