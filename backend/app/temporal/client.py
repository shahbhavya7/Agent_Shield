"""Temporal client — the one place that knows how to reach the server.

Kept separate from the worker so FastAPI can start workflows without importing any worker
code, and so the address/namespace live in exactly one place.

The connection is made lazily and then cached. Two deliberate consequences:

* FastAPI still boots with no Temporal server running. Connecting at startup would make
  the API refuse to start whenever the workflow engine is down, which is a worse failure
  than a single endpoint returning 503.
* Only the first request that needs Temporal pays the gRPC handshake. The client is
  safe to share and reconnects internally, so there is nothing to pool or recycle.
"""
import asyncio

from temporalio.client import Client

from app.config import TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE

_client: Client | None = None
_lock = asyncio.Lock()


async def get_client() -> Client:
    """Connect to the Temporal server, reusing the connection after the first call.

    Raises if the server is unreachable — callers decide what that means for them. A
    failed connection is not cached, so the next call retries rather than being stuck.
    """
    global _client
    if _client is not None:
        return _client
    async with _lock:
        # Re-check: another request may have connected while this one waited.
        if _client is None:
            _client = await Client.connect(
                TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE
            )
    return _client


async def reset_client() -> None:
    """Drop the cached connection. For tests, and for reconnecting after a config change."""
    global _client
    async with _lock:
        _client = None
