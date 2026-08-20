"""/inventory API — the customer -> agents combinations, and their stored test cases.

The combinations come from backend/inventory.yaml (seeded into PostgreSQL at startup),
so the UI's "Test Existing Agent" table is built from one source. Each agent carries its
`customer_agent_id` — the id everything downstream keys off, because the same agent
onboarded for two customers is two separate testing contexts.
"""
from fastapi import APIRouter, HTTPException

from app.config import INVENTORY, agent_display_name
from app.db import get_customer_agent, get_test_cases, list_customer_agents

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
def get_inventory() -> dict:
    """Customers with their onboarded agents (internal key, display name, and DB ids)."""
    # (customer name, agent name) -> the seeded customer_agents row.
    combos = {(c["customer_name"], c["agent_name"]): c for c in list_customer_agents()}
    customers = []
    for c in INVENTORY:
        agents = []
        for key in c["agents"]:
            name = agent_display_name(key)
            combo = combos.get((c["name"], name))
            agents.append({
                "key": key,
                "name": name,
                "customer_agent_id": combo["id"] if combo else None,
                "agent_id": combo["agent_id"] if combo else None,
            })
        customers.append({"name": c["name"], "agents": agents})
    return {"customers": customers}


@router.get("/{customer_agent_id}/test-cases")
def get_stored_test_cases(customer_agent_id: int) -> dict:
    """The test cases saved for ONE customer-agent combination (empty list if none yet).

    Feeds the same Review Test Cases UI that newly generated cases go through.
    """
    import json

    combo = get_customer_agent(customer_agent_id)
    if combo is None:
        raise HTTPException(status_code=404, detail=f"customer-agent {customer_agent_id} not found")

    cases = []
    for row in get_test_cases(customer_agent_id):
        try:
            seed_turns = json.loads(row["seed_turns_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            seed_turns = []
        cases.append({
            "title": row["title"],
            "user_goal": row["user_goal"],
            "test_type": row["test_type"],
            "assigned_fault": row["assigned_fault"],
            "expected_behavior": row["expected_behavior"],
            "seed_turns": seed_turns,
            "source": row["source"] or "ai",
        })

    return {
        "customer_agent_id": combo["id"],
        "customer_name": combo["customer_name"],
        "agent_id": combo["agent_id"],
        "agent_name": combo["agent_name"],
        "scenarios": cases,
    }
