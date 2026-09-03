"""Prove the Temporal wiring end-to-end: server reachable, worker polling, round trip OK.

Prereqs (two terminals):
    1) temporal server start-dev
    2) .venv/bin/python -m app.temporal.worker
Then:
    .venv/bin/python scripts/temporal_smoke.py
"""
import asyncio
import os
import sys

# Allow "from app...." when run as a script from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import TEMPORAL_ADDRESS, TEMPORAL_TASK_QUEUE  # noqa: E402
from app.temporal.client import get_client  # noqa: E402
from app.temporal.health import HealthWorkflow  # noqa: E402


async def main() -> None:
    client = await get_client()
    print(f"connected to {TEMPORAL_ADDRESS}")

    result = await client.execute_workflow(
        HealthWorkflow.run,
        "agentshield",
        id="temporal-smoke",
        task_queue=TEMPORAL_TASK_QUEUE,
    )
    print("workflow returned:", result)
    assert result.startswith("pong:agentshield"), f"unexpected result: {result}"
    # Sync activities must land on the worker's thread pool, never its event loop.
    assert "thread: activity" in result, (
        f"sync activity did not run on the activity thread pool: {result}"
    )
    print("TEMPORAL SMOKE TEST PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
