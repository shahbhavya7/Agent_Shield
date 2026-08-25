"""/inventory API — the customer -> agents combinations, and their stored test cases.

Sample combinations come from backend/inventory.yaml (seeded into PostgreSQL at startup);
agents connected through "Connect Your AI Agent" are appended straight from PostgreSQL,
which is the source of truth for them. Each agent carries its `customer_agent_id` — the id
everything downstream keys off, because the same agent onboarded for two customers is two
separate testing contexts.
"""
from fastapi import APIRouter, HTTPException

from app.config import INVENTORY, agent_display_name
from app.db import get_customer_agent, get_test_cases, list_customer_agents

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
def get_inventory() -> dict:
    """Customers with their onboarded agents (internal key, display name, and DB ids).

    Seeded customer-agent pairs come from inventory.yaml. Agents connected through
    "Connect Your AI Agent" are appended from PostgreSQL so saved test cases stay
    reachable after a reload.
    """
    all_combos = list_customer_agents()
    combos = {(c["customer_name"], c["agent_name"]): c for c in all_combos}
    customers = []
    emitted_combo_ids: set[int] = set()
    for c in INVENTORY:
        agents = []
        for key in c["agents"]:
            name = agent_display_name(key)
            combo = combos.get((c["name"], name))
            if combo:
                emitted_combo_ids.add(combo["id"])
            agents.append({
                "key": key,
                "name": name,
                "customer_agent_id": combo["id"] if combo else None,
                "agent_id": combo["agent_id"] if combo else None,
            })
        customers.append({"name": c["name"], "agents": agents})

    # Agents connected through "Connect Your AI Agent" exist only in PostgreSQL — they have
    # no inventory.yaml entry — so append every combination the seeded loop didn't emit.
    # Their display name is the agents.name column, so mapping.yaml needs no entry either.
    dynamic: dict[str, list[dict]] = {}
    for combo in all_combos:
        if combo["id"] in emitted_combo_ids:
            continue
        dynamic.setdefault(combo["customer_name"], []).append({
            "key": f"customer-agent-{combo['id']}",
            "name": combo["agent_name"],
            "customer_agent_id": combo["id"],
            "agent_id": combo["agent_id"],
        })
    customers.extend({"name": name, "agents": agents} for name, agents in dynamic.items())
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
