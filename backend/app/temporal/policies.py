"""Per-activity timeouts and retry policies, in one reviewable place.

Timeouts and retries belong to the *caller*, not the activity — in Temporal they are
arguments to ``workflow.execute_activity``. Collecting them here means the numbers can be
reviewed together, and a workflow just says::

    await workflow.execute_activity(load_agent, agent_id, **options_for(load_agent))

Three rules shaped every entry below.

**Retry only what is proven safe to repeat.** A retry is a re-execution, so it is only
correct where re-execution converges instead of duplicating. Every retrying activity below
records the evidence for that in an `idempotency:` note — not an assumption, a check that
was run. The one activity that is deliberately NOT idempotent (`replay_scenario`, which
must create a new conversation every time) is the one configured never to retry.

**Retry external services, back off, and don't retry your own bugs.** The failures worth
retrying are transient and not our fault: a dropped database connection, a provider rate
limit, a 5xx. Every policy uses exponential backoff. `ValueError`/`KeyError`/`TypeError`
are marked non-retryable on the paid activities, because a bad-data crash will crash
identically on attempt two — it just costs more. Provider auth errors likewise: a rejected
API key does not become accepted by waiting.

**Cap the total, not just the attempt.** `start_to_close_timeout` bounds one attempt;
`schedule_to_close_timeout` bounds the whole sequence including backoff waits, so a
pathological retry loop cannot hold a work slot indefinitely. Each cap is set above the
worst realistic case (attempts x timeout + backoff) with margin, so it never truncates
legitimate work.

Still deliberately NOT set: ``heartbeat_timeout``. A heartbeat timeout without matching
``activity.heartbeat()`` calls in the body would fail healthy long activities. When the
framework starts testing real-time calling agents, `play_scenario` needs to heartbeat
first; the timeout goes in at the same time, not before.
"""
from datetime import timedelta
from typing import Any, Callable

from temporalio.common import RetryPolicy

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
    play_voice_scenario,
    prepare_scenarios,
    replay_scenario,
)

# Errors that will fail identically on every attempt. Temporal matches on the exception
# class name, so these are strings.
_OUR_BUGS = ["ValueError", "KeyError", "TypeError", "AttributeError"]
# A rejected credential does not become accepted by waiting. openai SDK class names.
_PROVIDER_REFUSALS = ["AuthenticationError", "PermissionDeniedError", "NotFoundError"]


# --- retry shapes, named by intent rather than by their numbers ---------------

# A quick query. Worth retrying: the realistic failure is a dropped connection, and
# db.py opens a fresh one per call, so attempt two usually just works.
_DB = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
    non_retryable_error_types=_OUR_BUGS,
)

# Must land, or the run is stranded in "running" forever with nothing to correct it.
# The most persistent policy here, and it is cheap to repeat.
_MUST_LAND = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=_OUR_BUGS,
)

# An LLM call. Rate limits and provider 5xx are the common failures and both clear on
# their own, so back off further before trying again.
_LLM = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    non_retryable_error_types=[*_OUR_BUGS, *_PROVIDER_REFUSALS],
)

# Multi-turn work that has already spent tokens. Retry once, not twice: a second full
# replay of a conversation costs more than the result is usually worth.
_EXPENSIVE = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=2,
    non_retryable_error_types=[*_OUR_BUGS, *_PROVIDER_REFUSALS],
)

# Never retry. For work whose whole purpose is to create something new, where a second
# attempt would create a second copy.
_NEVER = RetryPolicy(maximum_attempts=1)


# --- per-activity options ----------------------------------------------------
# Keyed by activity name (what @activity.defn registers), so a workflow can look options
# up either by the function or by its registered name.
_OPTIONS: dict[str, dict[str, Any]] = {
    # ---- reads. Nothing to duplicate, so retrying is free of consequence. ----
    "load_agent": {
        # idempotency: read-only SELECT.
        "start_to_close_timeout": timedelta(seconds=10),
        "schedule_to_close_timeout": timedelta(seconds=90),
        "retry_policy": _DB,
    },
    "list_conversation_ids": {
        # idempotency: read-only SELECT.
        "start_to_close_timeout": timedelta(seconds=10),
        "schedule_to_close_timeout": timedelta(seconds=90),
        "retry_policy": _DB,
    },
    "list_failed_conversation_ids": {
        # idempotency: read-only SELECT.
        "start_to_close_timeout": timedelta(seconds=10),
        "schedule_to_close_timeout": timedelta(seconds=90),
        "retry_policy": _DB,
    },
    "load_replay_context": {
        # idempotency: read-only SELECTs.
        "start_to_close_timeout": timedelta(seconds=10),
        "schedule_to_close_timeout": timedelta(seconds=90),
        "retry_policy": _DB,
    },

    # ---- writes. Each retries only because its convergence was actually tested. ----
    "persist_suite": {
        # idempotency: VERIFIED. replace_scenarios deletes the run's scenarios before
        # inserting, so calling it twice with the same suite leaves the same row count
        # (checked: two calls with a 2-scenario suite -> 2 rows, not 4). Titles are
        # LLM-generated and collide within a run, which is why replacement — not an
        # upsert on a natural key — is what makes this safe.
        "start_to_close_timeout": timedelta(seconds=30),
        "schedule_to_close_timeout": timedelta(minutes=2),
        "retry_policy": _DB,
    },
    "finalize_run": {
        # idempotency: VERIFIED. Reads every conversation and writes one score; the score
        # is a pure function of those conversations (checked: re-running it on a finished
        # run reproduced the same 100.0). A repeat rewrites identical values.
        "start_to_close_timeout": timedelta(seconds=30),
        "schedule_to_close_timeout": timedelta(minutes=2),
        "retry_policy": _DB,
    },
    "fail_run": {
        # idempotency: a single UPDATE to fixed values. Repeating it only rewrites
        # finished_at, which no logic reads for correctness.
        "start_to_close_timeout": timedelta(seconds=10),
        "schedule_to_close_timeout": timedelta(minutes=3),
        "retry_policy": _MUST_LAND,
    },

    # ---- external services: the LLM. ----
    "prepare_scenarios": {
        # idempotency: persists nothing at all — pure generation. A retry costs tokens and
        # returns a different suite, which is fine because nothing has been written yet.
        # Slow: generation plus agent auto-discovery when the agent has no docs, which is
        # several sequential LLM and HTTP calls.
        "start_to_close_timeout": timedelta(minutes=3),
        "schedule_to_close_timeout": timedelta(minutes=12),
        "retry_policy": _LLM,
    },
    "judge_conversation_by_id": {
        # idempotency: VERIFIED by construction — one UPDATE of the verdict columns on one
        # conversation. A retry overwrites its own previous verdict; it cannot accumulate.
        "start_to_close_timeout": timedelta(minutes=2),
        "schedule_to_close_timeout": timedelta(minutes=10),
        "retry_policy": _LLM,
    },
    "explain_conversation_by_id": {
        # idempotency: VERIFIED by construction — one UPDATE of explanation/suggested_fix.
        "start_to_close_timeout": timedelta(minutes=2),
        "schedule_to_close_timeout": timedelta(minutes=10),
        "retry_policy": _LLM,
    },

    # ---- external services: the agent under test. ----
    "play_scenario": {
        # idempotency: VERIFIED. Keyed on (run, scenario) via conversations.idem_key, so a
        # retry resolves to the same conversation, and clear_messages wipes the transcript
        # first so a shorter second attempt cannot strand tail turns (checked: 3 turns then
        # a 2-turn retry -> 2 turns, not 5). A unique index on (conversation_id,
        # turn_index) blocks a concurrent double-write.
        #
        # Worth knowing: the HTTP adapter swallows transport errors and records them as a
        # failed turn rather than raising, so a transient agent outage does NOT reach this
        # retry policy — it lands in the trace as data. That is existing behaviour, and it
        # is what makes injected api_* faults work. This policy therefore covers the LLM
        # follow-up call and the database writes inside the activity.
        "start_to_close_timeout": timedelta(minutes=5),
        "schedule_to_close_timeout": timedelta(minutes=15),
        "retry_policy": _EXPENSIVE,
    },
    "play_voice_scenario": {
        # idempotency: VERIFIED — same run_scenario() as play_scenario, keyed the same
        # way (conversations.idem_key), so the same guarantees apply (see play_scenario's
        # note above). Phase 2B: each turn now also does a TTS + STT round trip (via
        # app.core.voice_caller) around the same HTTP call chat uses — a few extra
        # seconds per turn, still comfortably inside this budget for a short scenario.
        # Revisit if scenarios grow long enough to approach it, or once a real
        # streaming/telephony transport has different failure characteristics (e.g. it
        # needs to heartbeat — see the module docstring).
        "start_to_close_timeout": timedelta(minutes=5),
        "schedule_to_close_timeout": timedelta(minutes=15),
        "retry_policy": _EXPENSIVE,
    },
    "replay_scenario": {
        # idempotency: NONE, deliberately. It exists to produce a NEW conversation, so a
        # retry would leave a spurious extra one behind. Exactly one attempt; a failed
        # replay is surfaced to the user instead of silently duplicated.
        "start_to_close_timeout": timedelta(minutes=5),
        "schedule_to_close_timeout": timedelta(minutes=6),
        "retry_policy": _NEVER,
    },
}


def options_for(activity: Callable[..., Any] | str) -> dict[str, Any]:
    """The execute_activity keyword arguments for one activity.

    Accepts the function itself or its registered name. Raises on an unknown activity
    rather than silently handing back Temporal's defaults (no timeout, infinite retries),
    which for an LLM-backed activity would mean retrying a paid call forever.
    """
    name = activity if isinstance(activity, str) else getattr(
        activity, "__temporal_activity_definition", None
    )
    if not isinstance(activity, str):
        name = name.name if name is not None else activity.__name__

    if name not in _OPTIONS:
        raise KeyError(
            f"no timeout/retry policy defined for activity '{name}' — add one to "
            "app/temporal/policies.py rather than falling back to Temporal's defaults"
        )
    return dict(_OPTIONS[name])


# Every activity the worker registers, so the worker has one source of truth and a policy
# gap shows up as a startup failure rather than at runtime.
ALL_ACTIVITIES: list[Callable[..., Any]] = [
    load_agent,
    prepare_scenarios,
    persist_suite,
    play_scenario,
    play_voice_scenario,
    replay_scenario,
    judge_conversation_by_id,
    explain_conversation_by_id,
    list_conversation_ids,
    list_failed_conversation_ids,
    finalize_run,
    fail_run,
    load_replay_context,
]
