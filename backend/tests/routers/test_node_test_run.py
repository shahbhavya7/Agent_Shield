"""Router-level tests for POST /flows/{flow_id}/nodes/{node_id}/tests/{test_id}/run
(Phase 3: start a saved node test through the EXISTING Temporal run mechanism).

Isolated FastAPI app + monkeypatched app.db and Temporal client calls — no real
Temporal server, no real Postgres.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import flows as flows_router
from app.temporal.workflows import AgentTestInput, RunGroupInput, RunGroupWorkflow

FLOW_ROW = {
    "id": 1, "agent_id": 42, "name": "flow.json", "source_format": "json", "created_at": "t",
    "nodes_json": json.dumps([{"id": "authentication", "name": "Caller Authentication"}]),
    "edges_json": "[]",
}
AGENT_ROW = {"id": 42, "name": "Voice Bot", "modality": "voice"}
TEST_CASE_ROW = {
    "id": 5, "customer_agent_id": 99, "flow_id": 1, "node_id": "authentication",
    "title": "Flow node: Caller Authentication",
    "user_goal": "Verify identity handling.",
    "test_type": "flow_node", "assigned_fault": "none",
    "expected_behavior": "1. Expected behavior: Agent asks for name.",
    "seed_turns_json": json.dumps(["My name is Rahul.", "Sorry, my name is Srija."]),
    "node_script_json": json.dumps([
        {"expected_agent_behavior": "Agent asks for name.", "caller_line": "My name is Rahul."},
    ]),
}


class FakeTemporalClient:
    def __init__(self):
        self.calls = []

    async def start_workflow(self, workflow_fn, workflow_input, id, task_queue):
        self.calls.append({
            "workflow_fn": workflow_fn, "input": workflow_input,
            "id": id, "task_queue": task_queue,
        })


@pytest.fixture
def fake_client():
    return FakeTemporalClient()


@pytest.fixture
def client(monkeypatch, fake_client):
    app = FastAPI()
    app.include_router(flows_router.router)
    monkeypatch.setattr(
        flows_router, "get_agent_flow", lambda flow_id: (FLOW_ROW if flow_id == 1 else None)
    )
    monkeypatch.setattr(
        flows_router, "get_agent", lambda agent_id: (AGENT_ROW if agent_id == 42 else None)
    )
    monkeypatch.setattr(
        flows_router, "get_test_case", lambda test_id: (TEST_CASE_ROW if test_id == 5 else None)
    )
    monkeypatch.setattr(flows_router, "insert_run_group", lambda: 77)
    monkeypatch.setattr(flows_router, "insert_run", lambda agent_id, group_id, ca_id: 501)

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(flows_router, "get_client", fake_get_client)
    return TestClient(app)


# H. Starting a node test invokes the EXISTING run mechanism (RunGroupWorkflow), and
# returns the existing run_id/group_id for the frontend to poll.
def test_run_node_test_invokes_existing_run_group_workflow(client, fake_client):
    res = client.post("/flows/1/nodes/authentication/tests/5/run")
    assert res.status_code == 200
    body = res.json()
    assert body == {"run_id": 501, "group_id": 77, "flow_id": 1, "node_id": "authentication", "test_id": 5}

    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["workflow_fn"] is RunGroupWorkflow.run
    assert call["task_queue"]
    assert call["id"] == "agentshield-run-group-77"

    group_input = call["input"]
    assert isinstance(group_input, RunGroupInput)
    assert group_input.group_id == 77
    assert len(group_input.targets) == 1

    target = group_input.targets[0]
    assert isinstance(target, AgentTestInput)
    assert target.run_id == 501
    assert target.agent_id == 42
    assert target.scenarios is not None and len(target.scenarios) == 1

    scenario = target.scenarios[0]
    # I. Caller lines preserved exactly — the same list stored on the test case.
    assert scenario["seed_turns"] == ["My name is Rahul.", "Sorry, my name is Srija."]
    assert scenario["test_type"] == "flow_node"
    assert scenario["assigned_fault"] == "none"
    assert scenario["customer_context"] == ""  # never the dynamic AI Caller
    assert scenario["flow_id"] == 1
    assert scenario["node_id"] == "authentication"


def test_run_missing_flow_404s(client):
    res = client.post("/flows/999/nodes/authentication/tests/5/run")
    assert res.status_code == 404


def test_run_missing_test_404s(client):
    res = client.post("/flows/1/nodes/authentication/tests/999/run")
    assert res.status_code == 404


def test_run_test_belonging_to_a_different_node_404s(client):
    res = client.post("/flows/1/nodes/some_other_node/tests/5/run")
    assert res.status_code == 404


def test_run_missing_agent_404s(client, monkeypatch):
    monkeypatch.setattr(flows_router, "get_agent", lambda agent_id: None)
    res = client.post("/flows/1/nodes/authentication/tests/5/run")
    assert res.status_code == 404


def test_run_temporal_unreachable_returns_503_and_marks_run_errored(client, monkeypatch):
    async def failing_get_client():
        raise ConnectionError("no temporal server")

    monkeypatch.setattr(flows_router, "get_client", failing_get_client)
    recorded = {}
    monkeypatch.setattr(
        flows_router, "update_run",
        lambda run_id, status=None, finished=False: recorded.update(run_id=run_id, status=status, finished=finished),
    )

    res = client.post("/flows/1/nodes/authentication/tests/5/run")
    assert res.status_code == 503
    assert recorded == {"run_id": 501, "status": "error", "finished": True}
