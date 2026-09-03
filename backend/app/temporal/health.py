"""Trivial workflows whose only job is to prove the wiring works.

Wiring checks, not part of the product. Between them they confirm that the server is up,
that the worker is polling the right task queue, that a workflow can dispatch an activity
and get a result back, and — via thread_probe — that SYNC activities really are dispatched
to the worker's thread pool rather than run on its event loop. Delete once real workflows
exist.
"""
import asyncio
import threading
import time
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


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


# How many times the flaky probe below should fail before succeeding. Module-level so the
# probe's attempt counter survives across activity attempts within one worker process.
FLAKY_FAILURES = 2
_flaky_attempts: dict[str, int] = {}


@activity.defn
async def flaky_external_call(token: str) -> dict:
    """Fails FLAKY_FAILURES times, then succeeds — a stand-in for a rate-limited provider.

    Exists to prove the retry policies actually fire and back off, which cannot be shown
    with a real LLM call without deliberately breaking a credential. `token` scopes the
    attempt counter so repeated probes do not interfere.
    """
    _flaky_attempts[token] = _flaky_attempts.get(token, 0) + 1
    attempt = _flaky_attempts[token]
    info = activity.info()
    if attempt <= FLAKY_FAILURES:
        # A transient-looking failure. RuntimeError is not in the non-retryable list, so
        # the policy retries it — exactly as a provider 5xx or rate limit would be.
        raise RuntimeError(
            f"simulated transient provider failure (attempt {attempt} of "
            f"{FLAKY_FAILURES + 1}, temporal attempt {info.attempt})"
        )
    return {"attempts_made": attempt, "temporal_attempt": info.attempt}


@workflow.defn
class RetryProbeWorkflow:
    """Runs the flaky activity under the SAME retry shape the real LLM activities use.

    Whatever this proves about backoff and attempt counts holds for judge/explain/generate,
    because the policy is the same object shape: 3 attempts, 2s initial interval, doubling.
    """

    @workflow.run
    async def run(self, token: str) -> dict:
        started = workflow.now()
        result = await workflow.execute_activity(
            flaky_external_call,
            token,
            start_to_close_timeout=timedelta(seconds=10),
            schedule_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=3,
                non_retryable_error_types=["ValueError", "KeyError", "TypeError"],
            ),
        )
        result["elapsed_seconds"] = round((workflow.now() - started).total_seconds(), 1)
        return result


@workflow.defn
class NonRetryableProbeWorkflow:
    """The SAME always-failing activity, run with and without ValueError marked
    non-retryable — an A/B on `non_retryable_error_types`.

    Elapsed time is the measurement, taken with workflow.now() (deterministic and
    replay-safe) rather than by counting attempts: the attempt counter lives in the worker
    process and is not visible to a client. Non-retryable should finish immediately;
    retryable should spend the 1s + 2s backoff before giving up.
    """

    @workflow.run
    async def run(self, token: str, treat_as_non_retryable: bool) -> dict:
        started = workflow.now()
        outcome = "unexpectedly succeeded"
        try:
            await workflow.execute_activity(
                always_fails_with_value_error,
                token,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_attempts=3,
                    non_retryable_error_types=["ValueError"] if treat_as_non_retryable else [],
                ),
            )
        except Exception:
            outcome = "failed as expected"
        return {
            "outcome": outcome,
            "non_retryable": treat_as_non_retryable,
            "elapsed_seconds": round((workflow.now() - started).total_seconds(), 1),
        }


@activity.defn
async def always_fails_with_value_error(token: str) -> None:
    """Always raises ValueError — the class marked non-retryable above."""
    _flaky_attempts[token] = _flaky_attempts.get(token, 0) + 1
    raise ValueError("simulated bad data — retrying this would fail identically")


@activity.defn
async def slow_probe(hold_seconds: float) -> dict:
    """Occupies one activity slot for `hold_seconds`, reporting when it ran.

    The only way to *measure* max_concurrent_activities rather than assume it: run more of
    these than the ceiling allows and compute the maximum overlap from the timestamps.
    """
    start = time.time()
    await asyncio.sleep(hold_seconds)
    return {"start": start, "end": time.time()}


@workflow.defn
class ConcurrencyProbeWorkflow:
    """Fires N slow activities at once and returns when each actually ran.

    The worker's max_concurrent_activities is what decides how many overlap, so the
    returned timestamps show the real global ceiling in effect.
    """

    @workflow.run
    async def run(self, count: int, hold_seconds: float) -> list[dict]:
        return list(await asyncio.gather(*[
            workflow.execute_activity(
                slow_probe,
                hold_seconds,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            for _ in range(count)
        ]))


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
