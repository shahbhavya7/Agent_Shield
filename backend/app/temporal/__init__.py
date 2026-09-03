"""Temporal wiring — client, worker, and (later) the workflows/activities registry.

Nothing here is on the request path yet. The existing asyncio orchestration in
`app.core.orchestrator` is still what runs a test; this package only establishes the
connection and the worker process so the migration has somewhere to land.
"""
