"""Per-activity timeouts and retry policies, in one reviewable place.

Timeouts and retries belong to the *caller*, not the activity — in Temporal they are
arguments to ``workflow.execute_activity``. Collecting them here means the numbers can be
reviewed together, and a workflow just says::

    await workflow.execute_activity(load_agent, agent_id, **options_for(load_agent))

Two rules shaped every entry below.

**Only retry what is safe to repeat.** Retrying is only correct where re-execution
converges rather than duplicating, which is what the idempotency work bought us:
`persist_suite` replaces a run's scenario set, `play_scenario` is keyed on (run, scenario),
and the judge/fix activities are plain UPDATEs. The one activity that is deliberately NOT
idempotent — `replay_scenario`, which must create a new conversation every time — is the
one activity configured never to retry.

**Only spend what the work is worth.** An LLM-backed activity that has already burned
tokens gets fewer attempts than a cheap database read, because a retry there costs real
money rather than a few milliseconds.

Deliberately NOT set here:

* ``heartbeat_timeout`` — a heartbeat timeout without matching ``activity.heartbeat()``
  calls in the body would fail healthy long activities. When the framework starts testing
  real-time calling agents, `play_scenario` needs to heartbeat first; the timeout goes in
  at the same time, not before.
* ``schedule_to_close_timeout`` — an overall cap including retries. Worth adding once
  there is real data on how long a run takes end to end; guessing now would just truncate
  legitimate work.
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
    prepare_scenarios,
    replay_scenario,
)

# --- retry shapes, named by intent rather than by their numbers ---------------

# A quick query. Worth retrying: the realistic failure is a dropped connection, and
# db.py opens a fresh one per call, so attempt two usually just works.
_DB = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)

# Must land, or the run is stranded in "running" forever with nothing to correct it.
# The most persistent policy here, and it is cheap to repeat.
_MUST_LAND = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)

# An LLM call. Rate limits and 5xx from the provider are the common failures and both
# clear on their own, so back off further before trying again.
_LLM = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

# Multi-turn work that has already spent tokens. Retry once, not twice: a second full
# replay of a conversation costs more than the result is usually worth.
_EXPENSIVE = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=2,
)

# Never retry. For work whose whole purpose is to create something new, where a second
# attempt would create a second copy.
_NEVER = RetryPolicy(maximum_attempts=1)


# --- per-activity options ----------------------------------------------------
# Keyed by activity name (what @activity.defn registers), so a workflow can look options
# up either by the function or by its registered name.
_OPTIONS: dict[str, dict[str, Any]] = {
    # Cheap database reads and writes.
    "load_agent": {
        "start_to_close_timeout": timedelta(seconds=10),
        "retry_policy": _DB,
    },
    "persist_suite": {
        # Replaces the run's scenario set, so repeating it converges.
        "start_to_close_timeout": timedelta(seconds=30),
        "retry_policy": _DB,
    },
    "list_conversation_ids": {
        "start_to_close_timeout": timedelta(seconds=10),
        "retry_policy": _DB,
    },
    "list_failed_conversation_ids": {
        "start_to_close_timeout": timedelta(seconds=10),
        "retry_policy": _DB,
    },
    "load_replay_context": {
        "start_to_close_timeout": timedelta(seconds=10),
        "retry_policy": _DB,
    },
    "finalize_run": {
        # Reads every conversation, then writes the score. Deterministic given the same
        # conversations, so a repeat produces the same row.
        "start_to_close_timeout": timedelta(seconds=30),
        "retry_policy": _DB,
    },
    "fail_run": {
        "start_to_close_timeout": timedelta(seconds=10),
        "retry_policy": _MUST_LAND,
    },

    # LLM-backed work.
    "prepare_scenarios": {
        # Generation, plus agent auto-discovery when the agent has no docs or description,
        # which is several sequential LLM and HTTP calls.
        "start_to_close_timeout": timedelta(minutes=3),
        "retry_policy": _LLM,
    },
    "judge_conversation_by_id": {
        "start_to_close_timeout": timedelta(minutes=2),
        "retry_policy": _LLM,
    },
    "explain_conversation_by_id": {
        "start_to_close_timeout": timedelta(minutes=2),
        "retry_policy": _LLM,
    },

    # Playing a conversation against the agent under test — the expensive one.
    "play_scenario": {
        # Up to five tester turns, each an HTTP call to the agent (30s adapter timeout)
        # plus a possible LLM follow-up.
        "start_to_close_timeout": timedelta(minutes=5),
        "retry_policy": _EXPENSIVE,
    },
    "replay_scenario": {
        # Deliberately not idempotent: it exists to produce a NEW conversation. A retry
        # would leave a spurious extra conversation behind, so it gets exactly one attempt
        # and a failed replay is surfaced to the user instead.
        "start_to_close_timeout": timedelta(minutes=5),
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
    replay_scenario,
    judge_conversation_by_id,
    explain_conversation_by_id,
    list_conversation_ids,
    list_failed_conversation_ids,
    finalize_run,
    fail_run,
    load_replay_context,
]
