"""Router-level tests for POST /flows/{flow_id}/nodes/{node_id}/script (Phase 2).

Isolated FastAPI app + monkeypatched app.db/app.core.node_script calls, same
convention as tests/routers/test_flows.py — no Postgres, no real OpenAI calls.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.node_script import NodeScriptError
from app.routers import flows as flows_router

NODES = [
    {"id": "greeting", "name": "Greeting", "type": "conversation", "purpose": "Welcome"},
    {
        "id": "authentication", "name": "Caller Authentication", "type": "validation",
        "purpose": "Verify identity", "expected_inputs": ["name", "date_of_birth"],
    },
]
EDGES = [{"from": "greeting", "to": "authentication"}]

FLOW_ROW = {
    "id": 1, "agent_id": 1, "name": "flow.json", "source_format": "json", "created_at": "t",
    "nodes_json": json.dumps(NODES), "edges_json": json.dumps(EDGES),
}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(flows_router.router)
    monkeypatch.setattr(
        flows_router, "get_agent_flow",
        lambda flow_id: (FLOW_ROW if flow_id == 1 else None),
    )
    return TestClient(app)


def test_generate_script_for_valid_node(client, monkeypatch):
    seen = {}

    async def fake_generate(node, prereqs, hint):
        seen["node"] = node
        seen["prereqs"] = prereqs
        seen["hint"] = hint
        return {
            "test_goal": "Verify identity handling.",
            "script": [{"expected_agent_behavior": "Asks for name.", "caller_line": "My name is Rahul."}],
        }

    monkeypatch.setattr(flows_router, "generate_node_script", fake_generate)
    res = client.post("/flows/1/nodes/authentication/script", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["flow_id"] == 1
    assert body["node_id"] == "authentication"
    assert body["node_name"] == "Caller Authentication"
    assert body["test_goal"] == "Verify identity handling."
    assert body["script"][0]["caller_line"] == "My name is Rahul."
    # the authentication node's only prerequisite is the greeting node
    assert [p["id"] for p in seen["prereqs"]] == ["greeting"]
    assert seen["node"]["id"] == "authentication"


def test_generate_script_passes_through_user_goal_hint(client, monkeypatch):
    seen = {}

    async def fake_generate(node, prereqs, hint):
        seen["hint"] = hint
        return {"test_goal": "g", "script": [{"expected_agent_behavior": "x", "caller_line": "y"}]}

    monkeypatch.setattr(flows_router, "generate_node_script", fake_generate)
    res = client.post(
        "/flows/1/nodes/authentication/script",
        json={"test_goal": "test an incorrect name first"},
    )
    assert res.status_code == 200
    assert seen["hint"] == "test an incorrect name first"


def test_generate_script_missing_flow_404s(client):
    res = client.post("/flows/999/nodes/authentication/script", json={})
    assert res.status_code == 404


def test_generate_script_missing_node_404s(client):
    res = client.post("/flows/1/nodes/does_not_exist/script", json={})
    assert res.status_code == 404


def test_generate_script_invalid_model_output_returns_422(client, monkeypatch):
    async def fake_generate(node, prereqs, hint):
        raise NodeScriptError(["Model did not return a non-empty 'test_goal' string."])

    monkeypatch.setattr(flows_router, "generate_node_script", fake_generate)
    res = client.post("/flows/1/nodes/authentication/script", json={})
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]


def test_generate_script_root_node_has_no_prerequisites(client, monkeypatch):
    seen = {}

    async def fake_generate(node, prereqs, hint):
        seen["prereqs"] = prereqs
        return {"test_goal": "g", "script": [{"expected_agent_behavior": "x", "caller_line": "y"}]}

    monkeypatch.setattr(flows_router, "generate_node_script", fake_generate)
    res = client.post("/flows/1/nodes/greeting/script", json={})
    assert res.status_code == 200
    assert seen["prereqs"] == []
