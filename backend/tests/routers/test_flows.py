"""Router-level tests for POST/GET /agents/{id}/flows and GET /flows/{id}.

Builds a minimal FastAPI app around just this router (no startup event, no real
Postgres) and monkeypatches the DB calls at the names app.routers.flows imported them
under — the same convention app.core.judge's tests already use for app.db functions.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import flows as flows_router

VALID_JSON_BODY = {
    "content": (
        '{"agent_name": "SupportBot", '
        '"nodes": [{"id": "greeting", "name": "Greeting", "type": "conversation", '
        '"purpose": "Welcome"}], '
        '"edges": []}'
    ),
    "filename": "flow.json",
}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(flows_router.router)
    monkeypatch.setattr(
        flows_router, "get_agent",
        lambda agent_id: ({"id": agent_id} if agent_id == 1 else None),
    )
    return TestClient(app)


def test_upload_valid_json_flow_returns_nodes(client, monkeypatch):
    stored = {}
    monkeypatch.setattr(
        flows_router, "insert_agent_flow",
        lambda **kw: (stored.update(kw), 42)[1],
    )
    res = client.post("/agents/1/flows", json=VALID_JSON_BODY)
    assert res.status_code == 200
    body = res.json()
    assert body["flow_id"] == 42
    assert body["agent_name"] == "SupportBot"
    assert body["source_format"] == "json"
    assert body["nodes"][0]["id"] == "greeting"
    assert stored["agent_id"] == 1
    assert stored["source_format"] == "json"


def test_upload_valid_yaml_flow_returns_nodes(client, monkeypatch):
    monkeypatch.setattr(flows_router, "insert_agent_flow", lambda **kw: 7)
    yaml_body = {
        "content": "agent:\n  name: Bot\nnodes:\n  - id: a\n    name: A\nedges: []\n",
        "filename": "flow.yaml",
    }
    res = client.post("/agents/1/flows", json=yaml_body)
    assert res.status_code == 200
    body = res.json()
    assert body["source_format"] == "yaml"
    assert body["agent_name"] == "Bot"


def test_upload_unknown_agent_404s(client):
    res = client.post("/agents/999/flows", json=VALID_JSON_BODY)
    assert res.status_code == 404


def test_upload_invalid_json_returns_422_with_errors(client):
    res = client.post(
        "/agents/1/flows",
        json={"content": "{not valid", "filename": "flow.json"},
    )
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]


def test_upload_duplicate_node_id_returns_422(client):
    body = {
        "content": (
            '{"agent_name": "X", "nodes": ['
            '{"id": "a", "name": "A"}, {"id": "a", "name": "A2"}], "edges": []}'
        ),
        "filename": "flow.json",
    }
    res = client.post("/agents/1/flows", json=body)
    assert res.status_code == 422
    assert any("Duplicate" in e for e in res.json()["detail"]["errors"])


def test_upload_edge_referencing_unknown_node_returns_422(client):
    body = {
        "content": (
            '{"agent_name": "X", "nodes": [{"id": "a", "name": "A"}], '
            '"edges": [{"from": "a", "to": "ghost"}]}'
        ),
        "filename": "flow.json",
    }
    res = client.post("/agents/1/flows", json=body)
    assert res.status_code == 422
    assert any("unknown node id" in e for e in res.json()["detail"]["errors"])


def test_list_flows(client, monkeypatch):
    monkeypatch.setattr(
        flows_router, "list_agent_flows",
        lambda agent_id: [
            {"id": 1, "agent_id": agent_id, "name": "v1", "source_format": "json", "created_at": "t"},
        ],
    )
    res = client.get("/agents/1/flows")
    assert res.status_code == 200
    assert res.json()["flows"][0]["id"] == 1


def test_list_flows_unknown_agent_404s(client):
    res = client.get("/agents/999/flows")
    assert res.status_code == 404


def test_get_single_flow(client, monkeypatch):
    monkeypatch.setattr(
        flows_router, "get_agent_flow",
        lambda flow_id: {
            "id": flow_id, "agent_id": 1, "name": "v1", "source_format": "json",
            "created_at": "t", "nodes_json": '[{"id":"a","name":"A"}]', "edges_json": "[]",
        },
    )
    res = client.get("/flows/7")
    assert res.status_code == 200
    body = res.json()
    assert body["nodes"][0]["id"] == "a"
    assert body["edges"] == []


def test_get_single_flow_not_found(client, monkeypatch):
    monkeypatch.setattr(flows_router, "get_agent_flow", lambda flow_id: None)
    res = client.get("/flows/999")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Flexible ingestion end-to-end: ambiguous file -> LLM fallback (mocked) -> success.
# ---------------------------------------------------------------------------
AMBIGUOUS_BODY = {
    "content": '{"modules": {"auth": true}, "configuration": [1, 2, 3]}',
    "filename": "flow.json",
}


def test_upload_ambiguous_file_falls_back_to_llm_and_succeeds(client, monkeypatch):
    captured = {}

    async def fake_llm_extract(content, fmt):
        return {
            "agent_name": "Recovered",
            "nodes": [{"id": "a", "name": "A", "type": "", "purpose": "", "expected_inputs": [], "source_path": None}],
            "edges": [],
            "source_format": "json",
            "extraction_method": "llm",
        }

    monkeypatch.setattr(flows_router, "extract_flow_with_llm", fake_llm_extract)
    monkeypatch.setattr(
        flows_router, "insert_agent_flow",
        lambda **kw: (captured.update(kw), 55)[1],
    )
    res = client.post("/agents/1/flows", json=AMBIGUOUS_BODY)
    assert res.status_code == 200
    body = res.json()
    assert body["flow_id"] == 55
    assert body["extraction_method"] == "llm"
    assert captured["extraction_method"] == "llm"


def test_upload_ambiguous_file_llm_also_fails_returns_diagnostics(client, monkeypatch):
    from app.core.flow_llm_extractor import FlowExtractionError

    async def failing_llm_extract(content, fmt):
        raise FlowExtractionError(["LLM could not identify any conversation nodes in this file."])

    monkeypatch.setattr(flows_router, "extract_flow_with_llm", failing_llm_extract)
    res = client.post("/agents/1/flows", json=AMBIGUOUS_BODY)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "could not confidently identify" in detail["message"].lower()
    assert "modules" in detail["diagnostics"]["top_level_keys"]
    assert detail["errors"]
