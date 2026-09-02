"""Black-box HTTP adapter — the ONLY way AgentShield touches an agent-under-test.

Given an `agents` row (endpoint_url + request_template + response_path + optional
auth_header), render the request, POST it, and pull `reply` + `trace` back out. It never
imports the agent's code — everything goes over HTTP. Errors degrade gracefully so one
bad turn can't crash a run.
"""
import json
from typing import Any, Optional

import httpx

from app.config import INVENTORY, SAMPLE_AGENT_URLS, SAMPLE_RAG_BOT_URL, agent_display_name
from app.db import (
    get_agent_by_kind,
    get_agent_by_name,
    get_or_create_customer,
    get_or_create_customer_agent,
    insert_agent,
    update_agent_description,
)

# The standard request template for our sample RAG agent. Placeholders:
#   {message} -> JSON-escaped string (sits inside quotes)
#   {history} -> JSON array          (no quotes)
#   {faults}  -> JSON array          (no quotes)
DEFAULT_REQUEST_TEMPLATE = '{"message":"{message}","history":{history},"faults":{faults}}'

# Domain descriptions for the agents in inventory.yaml. This is the ONLY thing the scenario
# generator knows about an agent when no docs are uploaded, so a placeholder here means test
# cases get invented from general knowledge instead of the agent's actual domain.
SAMPLE_AGENT_DESCRIPTIONS: dict[str, str] = {
    "banking_bot": (
        "NorthBank's retail banking support assistant. Answers customer questions about "
        "wire/ACH transfer limits, overdraft fees, card replacement, fraud reporting, loan "
        "rates, mobile deposits, statements, and support hours."
    ),
    "hr_bot": (
        "PeopleDesk, an internal HR assistant for employees. Answers questions about paid "
        "time off, sick and parental leave, the 401(k) match, payroll schedule, remote-work "
        "policy, expense reimbursement, and benefits enrollment."
    ),
    "insurance_bot": (
        "SafeGuard's auto and home insurance claims assistant. Answers policyholder questions "
        "about claim filing windows, deductibles, claim processing times, roadside assistance, "
        "glass repair, rental car coverage, premium grace periods, and total-loss valuation."
    ),
    "airline_bot": (
        "SkyRoute Airways' flight support assistant. Answers passenger questions about "
        "carry-on and checked baggage allowances and fees, flight change fees, cancellation "
        "and refund rules, check-in and gate cut-off times, seat selection charges, the "
        "SkyMiles loyalty programme, and delay compensation."
    ),
}
# Internal identifier of the sample RAG agent — matches its /health "service" value and
# its key in mapping.yaml.
SAMPLE_RAG_BOT_KEY = "sample_rag_bot"
DEFAULT_TIMEOUT_S = 30.0

# Agent-cooperative faults (only our sample RAG agent honors these — they travel in the
# request body and the agent decides how to behave).
AGENT_FAULTS = {"tool_timeout", "stale_doc", "injection"}

# System / transport faults. AgentShield simulates these itself at the HTTP layer, so they
# work against ANY agent (black box) — the endpoint never even gets a well-formed call.
SYSTEM_FAULTS = {"api_unreachable", "api_error", "api_timeout", "malformed_response"}

_SYSTEM_FAULT_TRACE = {
    "api_unreachable": "connection refused — agent endpoint unreachable",
    "api_error": "HTTP 500 — agent returned an internal server error",
    "api_timeout": "request timed out — agent did not respond in time",
    "malformed_response": "malformed response — agent returned invalid / non-JSON body",
}


def _render_body(template: str, message: str, history: list, faults: list) -> dict:
    """Substitute placeholders into the JSON template and parse to a dict.

    Falls back to a canonical body if the template can't be rendered/parsed, so a
    malformed custom template never kills the run.
    """
    try:
        body_str = (
            template
            .replace("{message}", json.dumps(message)[1:-1])   # escaped, no surrounding quotes
            .replace("{history}", json.dumps(history))
            .replace("{faults}", json.dumps(faults))
        )
        return json.loads(body_str)
    except (json.JSONDecodeError, ValueError):
        return {"message": message, "history": history, "faults": faults}


def _extract_by_path(data: Any, dot_path: str) -> Any:
    """Walk a dot-path like 'reply' or 'data.choices.0.text' through dicts/lists."""
    cur = data
    for part in dot_path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


async def send(
    agent: Any,  # an `agents` row (dict) with the agent columns
    message: str,
    history: Optional[list] = None,
    faults: Optional[list] = None,
) -> dict:
    """Send one turn to the agent. Returns {"reply": str, "trace": dict}.

    On timeout / HTTP / parse error, returns a sentinel reply + an error trace instead
    of raising — the runner records it like any other turn.
    """
    history = history or []
    faults = faults or []
    agent = dict(agent)  # accept a row or a plain dict uniformly

    # System/transport faults are simulated here — the agent is never called, so this works
    # for any black-box endpoint. Record the failure like a normal (bad) turn.
    sys_fault = next((f for f in faults if f in SYSTEM_FAULTS), None)
    if sys_fault:
        return {
            "reply": "<error>",
            "trace": {"error": _SYSTEM_FAULT_TRACE[sys_fault], "system_fault": sys_fault, "latency_ms": 0, "tokens": 0},
        }

    # Only agent-cooperative faults are forwarded to the agent's own API.
    faults = [f for f in faults if f in AGENT_FAULTS]

    template = agent.get("request_template") or DEFAULT_REQUEST_TEMPLATE
    response_path = agent.get("response_path") or "reply"
    url = agent.get("endpoint_url")
    headers = {"content-type": "application/json"}
    if agent.get("auth_header"):
        # Stored as a single "Header: value" string.
        name, _, value = agent["auth_header"].partition(":")
        if name and value:
            headers[name.strip()] = value.strip()

    body = _render_body(template, message, history, faults)

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return {"reply": "<error>", "trace": {"error": "timeout"}}
    except httpx.HTTPStatusError as e:
        return {"reply": "<error>", "trace": {"error": f"http {e.response.status_code}"}}
    except Exception as e:  # connection refused, bad JSON, etc.
        return {"reply": "<error>", "trace": {"error": str(e)}}

    reply = _extract_by_path(data, response_path)
    if reply is None:
        reply = json.dumps(data)  # last resort: show what came back
    trace = data.get("trace", {}) if isinstance(data, dict) else {}
    return {"reply": str(reply), "trace": trace}


def seed_sample_agent() -> int:
    """Ensure the RAG customer-support agent exists as the sample `agents` row.

    Idempotent — returns the existing sample agent id if already seeded.
    """
    existing = get_agent_by_kind("sample")
    if existing:
        return existing["id"]
    return insert_agent(
        # Internal identifier -> actual name, resolved via backend/mapping.yaml.
        name=agent_display_name(SAMPLE_RAG_BOT_KEY),
        kind="sample",
        endpoint_url=SAMPLE_RAG_BOT_URL,
        response_path="reply",
        request_template=DEFAULT_REQUEST_TEMPLATE,
        description=(
            "A standalone RAG-based customer-support agent (gpt-4o-mini) for an online "
            "store. Answers questions about returns, refunds, shipping, warranty, "
            "support hours, orders, cancellations, and payment methods."
        ),
    )


def seed_inventory() -> int:
    """Seed customers + their agents from backend/inventory.yaml. Idempotent.

    One `agents` row per distinct agent (matched by name, so NorthBank Support is stored
    once) and one `customer_agents` row per customer-agent pair — which is what makes
    "NorthBank Support for Customer 1" and "…for Customer 2" separate testing contexts.

    Call AFTER seed_sample_agent(): that one claims the first kind='sample' row.
    """
    pairs = 0
    for customer in INVENTORY:
        customer_id = get_or_create_customer(customer["name"])
        for key in customer["agents"]:
            name = agent_display_name(key)
            agent = get_agent_by_name(name)
            if agent and key in SAMPLE_AGENT_DESCRIPTIONS:
                # Seeding matches on name, so an already-seeded row keeps whatever
                # description it was created with — refresh it from the map.
                update_agent_description(agent["id"], SAMPLE_AGENT_DESCRIPTIONS[key])
            agent_id = agent["id"] if agent else insert_agent(
                name=name,
                kind="sample",
                endpoint_url=SAMPLE_AGENT_URLS.get(key, ""),
                response_path="reply",
                request_template=DEFAULT_REQUEST_TEMPLATE,
                description=SAMPLE_AGENT_DESCRIPTIONS.get(key, f"Sample agent '{key}'."),
            )
            get_or_create_customer_agent(customer_id, agent_id)
            pairs += 1
    print(f"[db] inventory seeded: {pairs} customer-agent combinations")
    return pairs
