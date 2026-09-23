"""Tests for the Phase 1 flow parser: JSON/YAML -> normalized {agent_name, nodes, edges}.

Pure unit tests — no DB, no FastAPI — matching this module's role as validation logic
kept separate from the router (app.routers.flows).
"""
import json

import pytest

from app.core.flow_parser import FlowAmbiguousError, FlowParseError, parse_flow

VALID_YAML = """
agent:
  name: CustomerSupportVoiceAgent

nodes:
  - id: greeting
    name: Greeting
    type: conversation
    purpose: Welcome the customer

  - id: authentication
    name: Caller Authentication
    type: validation
    purpose: Verify the caller's identity
    expected_inputs:
      - name
      - date_of_birth

  - id: account_help
    name: Account Help
    type: task
    purpose: Help the authenticated caller with their account

edges:
  - from: greeting
    to: authentication
  - from: authentication
    to: account_help
"""

VALID_JSON = json.dumps({
    "agent_name": "SupportBot",
    "nodes": [
        {"id": "greeting", "name": "Greeting", "type": "conversation", "purpose": "Welcome"},
        {"id": "help", "name": "Help", "type": "task", "purpose": "Help the caller"},
    ],
    "edges": [{"from": "greeting", "to": "help"}],
})


def test_valid_yaml_parses_nodes_and_edges():
    result = parse_flow(VALID_YAML, "yaml")
    assert result["agent_name"] == "CustomerSupportVoiceAgent"
    assert result["source_format"] == "yaml"
    assert [n["id"] for n in result["nodes"]] == ["greeting", "authentication", "account_help"]
    assert result["nodes"][1]["expected_inputs"] == ["name", "date_of_birth"]
    assert result["edges"] == [
        {"from": "greeting", "to": "authentication"},
        {"from": "authentication", "to": "account_help"},
    ]


def test_valid_json_parses():
    result = parse_flow(VALID_JSON, "json")
    assert result["agent_name"] == "SupportBot"
    assert result["source_format"] == "json"
    assert len(result["nodes"]) == 2


def test_format_auto_detection_without_hint_json():
    result = parse_flow(VALID_JSON, None)
    assert result["agent_name"] == "SupportBot"
    assert result["source_format"] == "json"


def test_format_auto_detection_without_hint_yaml():
    result = parse_flow(VALID_YAML, None)
    assert result["source_format"] == "yaml"


def test_invalid_json_raises_with_clear_error():
    with pytest.raises(FlowParseError) as exc:
        parse_flow("{not valid json", "json")
    assert exc.value.errors


def test_invalid_yaml_raises_with_clear_error():
    with pytest.raises(FlowParseError):
        parse_flow("nodes: [unterminated", "yaml")


def test_neither_json_nor_yaml_raises():
    # A tab character makes this invalid YAML too, so auto-detect fails both ways.
    with pytest.raises(FlowParseError):
        parse_flow("{\tinvalid: [", None)


def test_empty_file_rejected():
    with pytest.raises(FlowParseError):
        parse_flow("   ", "json")


def test_non_object_top_level_rejected():
    with pytest.raises(FlowParseError):
        parse_flow(json.dumps(["not", "an", "object"]), "json")


def test_no_nodes_rejected():
    raw = {"agent_name": "X", "nodes": [], "edges": []}
    with pytest.raises(FlowParseError) as exc:
        parse_flow(json.dumps(raw), "json")
    assert any("at least one node" in e for e in exc.value.errors)


def test_duplicate_node_id_rejected():
    raw = {
        "agent_name": "X",
        "nodes": [
            {"id": "a", "name": "A"},
            {"id": "a", "name": "A again"},
        ],
        "edges": [],
    }
    with pytest.raises(FlowParseError) as exc:
        parse_flow(json.dumps(raw), "json")
    assert any("Duplicate node id" in e for e in exc.value.errors)


def test_edge_referencing_unknown_node_rejected():
    raw = {
        "agent_name": "X",
        "nodes": [{"id": "a", "name": "A"}],
        "edges": [{"from": "a", "to": "ghost"}],
    }
    with pytest.raises(FlowParseError) as exc:
        parse_flow(json.dumps(raw), "json")
    assert any("unknown node id" in e for e in exc.value.errors)


def test_missing_required_node_field_rejected():
    raw = {"agent_name": "X", "nodes": [{"id": "a"}], "edges": []}
    with pytest.raises(FlowParseError) as exc:
        parse_flow(json.dumps(raw), "json")
    assert any("missing required field" in e for e in exc.value.errors)


def test_multiple_errors_all_reported_together():
    raw = {
        "agent_name": "X",
        "nodes": [{"id": "a", "name": "A"}, {"id": "a", "name": "A2"}],
        "edges": [{"from": "a", "to": "ghost"}],
    }
    with pytest.raises(FlowParseError) as exc:
        parse_flow(json.dumps(raw), "json")
    assert len(exc.value.errors) >= 2


# ---------------------------------------------------------------------------
# Flexible ingestion (tolerant deterministic extraction) — Part 12 CASE 1-6.
# ---------------------------------------------------------------------------
def test_case1_literal_nodes_key_still_detected():
    raw = {"nodes": [{"id": "greeting", "name": "Greeting", "purpose": "Welcome caller"}]}
    result = parse_flow(json.dumps(raw), "json")
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "greeting"
    assert result["extraction_method"] == "deterministic"


def test_case2_states_key_recognized_as_nodes():
    raw = {"states": [{"name": "authentication", "prompt": "Verify caller"}]}
    result = parse_flow(json.dumps(raw), "json")
    assert len(result["nodes"]) == 1
    node = result["nodes"][0]
    assert node["name"] == "authentication"
    assert node["purpose"] == "Verify caller"  # "prompt" resolved via PURPOSE_ALIASES
    assert node["source_path"] == "states[0]"


def test_case3_nested_wrapper_flow_steps():
    raw = {"flow": {"steps": [
        {"id": "greeting", "title": "Greeting"},
        {"id": "auth", "title": "Authentication"},
    ]}}
    result = parse_flow(json.dumps(raw), "json")
    assert len(result["nodes"]) == 2
    assert result["nodes"][0]["name"] == "Greeting"  # "title" resolved via NAME_ALIASES
    assert result["nodes"][0]["source_path"] == "flow.steps[0]"


def test_case4_deeply_nested_wrapper_config_workflow_states():
    raw = {"config": {"workflow": {"states": [
        {"name": "greeting"},
        {"name": "authentication"},
    ]}}}
    result = parse_flow(json.dumps(raw), "json")
    assert len(result["nodes"]) == 2
    assert {n["name"] for n in result["nodes"]} == {"greeting", "authentication"}
    assert result["nodes"][0]["source_path"] == "config.workflow.states[0]"


def test_case5_explicit_inline_transition_next_field():
    raw = {"states": [
        {"id": "greeting", "next": "authentication"},
        {"id": "authentication"},
    ]}
    result = parse_flow(json.dumps(raw), "json")
    assert result["edges"] == [{"from": "greeting", "to": "authentication"}]


def test_case6_no_explicit_transitions_yields_no_edges():
    raw = {"states": [{"id": "greeting"}, {"id": "authentication"}]}
    result = parse_flow(json.dumps(raw), "json")
    assert result["edges"] == []  # never inferred from ordering


def test_inline_edge_to_unknown_target_is_dropped_not_fatal():
    raw = {"states": [{"id": "greeting", "next": "does_not_exist"}]}
    result = parse_flow(json.dumps(raw), "json")
    assert result["edges"] == []
    assert len(result["nodes"]) == 1  # upload still succeeds


def test_case7_ambiguous_structure_raises_flow_ambiguous_error():
    raw = {"agent": {"name": "X"}, "modules": {"auth": True}, "configuration": [1, 2, 3]}
    with pytest.raises(FlowAmbiguousError) as exc:
        parse_flow(json.dumps(raw), "json")
    assert "agent" in exc.value.diagnostics["top_level_keys"]
    assert "modules" in exc.value.diagnostics["top_level_keys"]


def test_wrapper_search_is_bounded_not_infinite():
    # config -> workflow -> extra -> states is 3 wrapper hops; "extra" isn't a
    # recognized wrapper key at all, so this must NOT be found (bounded + whitelisted).
    raw = {"config": {"workflow": {"extra": {"states": [{"name": "x"}]}}}}
    with pytest.raises(FlowAmbiguousError):
        parse_flow(json.dumps(raw), "json")


def test_array_of_non_node_like_objects_is_not_a_false_positive():
    # An array of dicts with none of the id/name/purpose aliases must not be mistaken
    # for a node collection.
    raw = {"records": [{"email": "a@example.com"}, {"email": "b@example.com"}]}
    with pytest.raises(FlowAmbiguousError):
        parse_flow(json.dumps(raw), "json")
