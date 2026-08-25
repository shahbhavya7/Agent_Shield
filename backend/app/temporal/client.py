"""Temporal client — the one place that knows how to reach the server.

Kept separate from the worker so that FastAPI can eventually start workflows without
importing any worker code, and so the address/namespace live in exactly one place.
"""
from temporalio.client import Client

from app.config import TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE


async def get_client() -> Client:
    """Connect to the Temporal server. Raises if it is not reachable.

    Deliberately NOT called at FastAPI startup: the app must keep booting (and running
    tests) with no Temporal server present, because nothing depends on it yet.
    """
    return await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
