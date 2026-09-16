"""AgentShield backend entrypoint: FastAPI app + CORS + /health.

Routers are included as later phases add them (currently none).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_schema

app = FastAPI(title="AgentShield", version="0.1.0")

# Wide-open CORS — this is a local hackathon MVP, no auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    # Make sure the DB + tables exist before any request lands.
    init_schema()
    # Seed the sample RAG agent so runs can target agent_id immediately.
    from app.core.adapter import seed_inventory, seed_sample_agent

    seed_sample_agent()
    # Customers + their agents from backend/inventory.yaml (after the sample agent).
    seed_inventory()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


from app.routers import agents, conversations, inventory, runs, twilio  # noqa: E402

app.include_router(runs.router)
app.include_router(agents.router)
app.include_router(conversations.router)
app.include_router(inventory.router)
app.include_router(twilio.router)
