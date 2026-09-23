"""Router-level tests for POST /flows/{flow_id}/nodes/{node_id}/test (Phase 3: save a
reviewed node script as a real test_cases row).

Isolated FastAPI app + monkeypatched app.db calls, same convention as the other
routers/test_flows*.py files. Uses the REAL app.core.scenarios.normalize_scenarios so
the flow_node conversion is exercised end-to-end, not mocked away.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import flows as flows_router

NODES = [
    {"id": "greeting", "name": "Greeting", "type": "conversation", "purpose": "Welcome"},
    {
        "id": "authentication", "name": "Caller Authentication", "type": "validation",
        "purpose": "Verify identity",
    },
]
FLOW_ROW = {
    "id": 1, "agent_id": 42, "name": "flow.json", "source_format": "json", "created_at": "t",
    "nodes_json": json.dumps(NODES), "edges_json": "[]",
}

VALID_SCRIPT = [
    {"expected_agent_behavior": "Agent asks for the caller's name.", "caller_line": "My name is Rahul."},
    {"expected_agent_behavior": "Agent rejects/clarifies and asks again.", "caller_line": "Sorry, my name is Srija."},
]


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(flows_router.router)
    monkeypatch.setattr(
        flows_router, "get_agent_flow", lambda flow_id: (FLOW_ROW if flow_id == 1 else None)
    )
    monkeypatch.setattr(flows_router, "get_customer_agent_by_agent_id", lambda agent_id: None)
    monkeypatch.setattr(flows_router, "default_customer_agent", lambda agent_id: 99)
    return TestClient(app)


# A. Save a valid node script.
def test_save_valid_node_test(client, monkeypatch):
    captured = {}

    def fake_insert(customer_agent_id, tc, source, flow_id, node_id, node_script_json):
        captured.update(
            customer_agent_id=customer_agent_id, tc=tc, source=source,
            flow_id=flow_id, node_id=node_id, node_script_json=node_script_json,
        )
        return 123

    monkeypatch.setattr(flows_router, "insert_test_case", fake_insert)

    res = client.post(
        "/flows/1/nodes/authentication/test",
        json={"test_goal": "Verify identity handling.", "script": VALID_SCRIPT},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["test_id"] == 123
    assert body["flow_id"] == 1
    assert body["node_id"] == "authentication"
    assert body["node_name"] == "Caller Authentication"
    assert body["customer_agent_id"] == 99
    assert body["script"][0]["caller_line"] == "My name is Rahul."

    # E. flow_id / node_id / node_script_json are what actually got persisted.
    assert captured["flow_id"] == 1
    assert captured["node_id"] == "authentication"
    assert captured["source"] == "user"
    stored_script = json.loads(captured["node_script_json"])
    assert stored_script == VALID_SCRIPT

    # F. correct conversion onto the test_cases row.
    tc = captured["tc"]
    assert tc["test_type"] == "flow_node"
    assert tc["assigned_fault"] == "none"
    assert tc["seed_turns"] == ["My name is Rahul.", "Sorry, my name is Srija."]
    assert "1. Expected behavior: Agent asks for the caller's name." in tc["expected_behavior"]
    assert "2. Expected behavior: Agent rejects/clarifies and asks again." in tc["expected_behavior"]


# B. Reject malformed node script.
def test_save_rejects_wrong_top_level_type(client):
    """A non-list `script` is rejected at the request-validation layer (Pydantic) —
    still a clear 422, just before reaching our own validate_script() error shape.
    """
    res = client.post(
        "/flows/1/nodes/authentication/test",
        json={"test_goal": "goal", "script": "not-a-list"},
    )
    assert res.status_code == 422


def test_save_rejects_turns_missing_both_fields(client):
    """A list of turns that IS the right shape but semantically malformed hits our own
    validate_script() and returns our {"errors": [...]} shape.
    """
    res = client.post(
        "/flows/1/nodes/authentication/test",
        json={"test_goal": "goal", "script": [{"not": "a valid turn"}]},
    )
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]


def test_save_rejects_empty_script(client):
    res = client.post(
        "/flows/1/nodes/authentication/test", json={"test_goal": "goal", "script": []}
    )
    assert res.status_code == 422


# C. Reject empty caller line.
def test_save_rejects_empty_caller_line(client):
    res = client.post(
        "/flows/1/nodes/authentication/test",
        json={"test_goal": "goal", "script": [{"expected_agent_behavior": "x", "caller_line": "   "}]},
    )
    assert res.status_code == 422
    assert any("caller_line" in e for e in res.json()["detail"]["errors"])


# D. Reject empty/missing expected agent behavior.
def test_save_rejects_missing_expected_behavior(client):
    res = client.post(
        "/flows/1/nodes/authentication/test",
        json={"test_goal": "goal", "script": [{"caller_line": "Hi."}]},
    )
    assert res.status_code == 422
    assert any("expected_agent_behavior" in e for e in res.json()["detail"]["errors"])


def test_save_rejects_missing_test_goal(client):
    res = client.post(
        "/flows/1/nodes/authentication/test", json={"test_goal": "  ", "script": VALID_SCRIPT}
    )
    assert res.status_code == 422


def test_save_missing_flow_404s(client):
    res = client.post(
        "/flows/999/nodes/authentication/test",
        json={"test_goal": "goal", "script": VALID_SCRIPT},
    )
    assert res.status_code == 404


def test_save_missing_node_404s(client):
    res = client.post(
        "/flows/1/nodes/does_not_exist/test",
        json={"test_goal": "goal", "script": VALID_SCRIPT},
    )
    assert res.status_code == 404


def test_save_prefers_existing_customer_agent_over_minting_a_new_one(client, monkeypatch):
    monkeypatch.setattr(
        flows_router, "get_customer_agent_by_agent_id",
        lambda agent_id: {"id": 7, "agent_id": agent_id},
    )
    minted = {"called": False}

    def fail_if_called(agent_id):
        minted["called"] = True
        return 999

    monkeypatch.setattr(flows_router, "default_customer_agent", fail_if_called)
    monkeypatch.setattr(flows_router, "insert_test_case", lambda *a, **kw: 1)

    res = client.post(
        "/flows/1/nodes/authentication/test",
        json={"test_goal": "goal", "script": VALID_SCRIPT},
    )
    assert res.status_code == 200
    assert res.json()["customer_agent_id"] == 7
    assert minted["called"] is False
