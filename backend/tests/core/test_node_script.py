"""Tests for Phase 2 node script generation: prerequisite-path flattening, structural
validation of the generated script, and the generation entry point with the LLM
mocked (no real OpenAI calls).
"""
import pytest

from app.core import node_script
from app.core.node_script import (
    NodeScriptError,
    generate_node_script,
    prerequisite_path,
    to_scenario_dict,
    validate_script,
)
from app.core.runner import ADAPTIVE_TYPES
from app.core.scenarios import normalize_scenarios

GREETING = {"id": "greeting", "name": "Greeting", "type": "conversation", "purpose": "Welcome"}
AUTH = {
    "id": "authentication", "name": "Caller Authentication", "type": "validation",
    "purpose": "Verify the caller's identity", "expected_inputs": ["name", "date_of_birth"],
}
ACCOUNT_HELP = {
    "id": "account_help", "name": "Account Help", "type": "task",
    "purpose": "Help the authenticated caller with their account",
}
NODES = [GREETING, AUTH, ACCOUNT_HELP]
EDGES = [
    {"from": "greeting", "to": "authentication"},
    {"from": "authentication", "to": "account_help"},
]


# ---------------------------------------------------------------------------
# flow_node must never enter the existing adaptive scripted fallback.
# ---------------------------------------------------------------------------
def test_flow_node_type_is_not_adaptive():
    assert node_script.NODE_TEST_TYPE == "flow_node"
    assert node_script.NODE_TEST_TYPE not in ADAPTIVE_TYPES


# ---------------------------------------------------------------------------
# Prerequisite path calculation.
# ---------------------------------------------------------------------------
def test_root_node_has_no_prerequisites():
    assert prerequisite_path(NODES, EDGES, "greeting") == []


def test_middle_node_prerequisite_is_just_its_parent():
    assert prerequisite_path(NODES, EDGES, "authentication") == [GREETING]


def test_deep_node_prerequisite_chain_is_root_first():
    result = prerequisite_path(NODES, EDGES, "account_help")
    assert result == [GREETING, AUTH]


def test_unknown_node_has_no_prerequisites():
    assert prerequisite_path(NODES, EDGES, "does_not_exist") == []


def test_cycle_does_not_hang():
    cyclic_edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
    cyclic_nodes = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    result = prerequisite_path(cyclic_nodes, cyclic_edges, "a")
    assert result == [{"id": "b", "name": "B"}]  # stops instead of looping forever


# ---------------------------------------------------------------------------
# Structural validation of a (generated or hand-edited) script.
# ---------------------------------------------------------------------------
def test_validate_script_accepts_well_formed_turns():
    turns = validate_script([
        {"expected_agent_behavior": "Asks for name.", "caller_line": "My name is Rahul."},
        {"expected_agent_behavior": "Asks again.", "caller_line": "Sorry, my name is Srija."},
    ])
    assert len(turns) == 2
    assert turns[0]["caller_line"] == "My name is Rahul."


def test_validate_script_rejects_empty_list():
    with pytest.raises(NodeScriptError):
        validate_script([])


def test_validate_script_rejects_non_list():
    with pytest.raises(NodeScriptError):
        validate_script({"not": "a list"})


def test_validate_script_rejects_missing_caller_line():
    with pytest.raises(NodeScriptError) as exc:
        validate_script([{"expected_agent_behavior": "Asks for name."}])
    assert any("caller_line" in e for e in exc.value.errors)


def test_validate_script_rejects_missing_expected_behavior():
    with pytest.raises(NodeScriptError) as exc:
        validate_script([{"caller_line": "Hi."}])
    assert any("expected_agent_behavior" in e for e in exc.value.errors)


def test_validate_script_rejects_empty_caller_line():
    with pytest.raises(NodeScriptError):
        validate_script([{"expected_agent_behavior": "x", "caller_line": "   "}])


def test_validate_script_rejects_non_string_fields():
    with pytest.raises(NodeScriptError):
        validate_script([{"expected_agent_behavior": "x", "caller_line": 5}])


def test_validate_script_rejects_too_many_turns():
    turns = [{"expected_agent_behavior": "x", "caller_line": "y"} for _ in range(20)]
    with pytest.raises(NodeScriptError):
        validate_script(turns)


# ---------------------------------------------------------------------------
# generate_node_script — LLM mocked, no real OpenAI calls.
# ---------------------------------------------------------------------------
async def test_generate_node_script_returns_goal_and_script(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        assert json_mode is True
        return {
            "test_goal": "Verify the agent rejects an incorrect name before accepting the correct one.",
            "script": [
                {"expected_agent_behavior": "Agent asks for the caller's name.", "caller_line": "My name is Rahul."},
                {"expected_agent_behavior": "Agent rejects/clarifies and asks again.", "caller_line": "Sorry, my name is Srija."},
                {"expected_agent_behavior": "Agent asks for date of birth.", "caller_line": "My date of birth is 15th March 2002."},
            ],
        }

    monkeypatch.setattr(node_script, "chat", fake_chat)
    result = await generate_node_script(AUTH, [GREETING], "test an incorrect name first")
    assert result["test_goal"].startswith("Verify")
    assert len(result["script"]) == 3
    assert result["script"][0]["caller_line"] == "My name is Rahul."


async def test_generate_node_script_preserves_caller_lines_exactly(monkeypatch):
    exact_line = "My name is  Rahul.   "  # deliberately odd whitespace to check trimming only

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {
            "test_goal": "Verify something specific about this node.",
            "script": [{"expected_agent_behavior": "Does something.", "caller_line": exact_line}],
        }

    monkeypatch.setattr(node_script, "chat", fake_chat)
    result = await generate_node_script(AUTH, [], None)
    assert result["script"][0]["caller_line"] == exact_line.strip()


async def test_generate_node_script_rejects_malformed_output(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {"test_goal": "A goal", "script": "not-a-list"}

    monkeypatch.setattr(node_script, "chat", fake_chat)
    with pytest.raises(NodeScriptError):
        await generate_node_script(AUTH, [], None)


async def test_generate_node_script_rejects_missing_test_goal(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {"script": [{"expected_agent_behavior": "x", "caller_line": "y"}]}

    monkeypatch.setattr(node_script, "chat", fake_chat)
    with pytest.raises(NodeScriptError):
        await generate_node_script(AUTH, [], None)


async def test_generate_node_script_rejects_non_dict_result(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return "not a dict"

    monkeypatch.setattr(node_script, "chat", fake_chat)
    with pytest.raises(NodeScriptError):
        await generate_node_script(AUTH, [], None)


# ---------------------------------------------------------------------------
# to_scenario_dict — the flattening into the EXISTING scenario/test-case shape
# (Phase 3). This is what makes a node script runnable by the unmodified existing
# scripted runner and Judge.
# ---------------------------------------------------------------------------
NODE_SCRIPT = [
    {"expected_agent_behavior": "Agent asks for the caller's name.", "caller_line": "My name is Rahul."},
    {"expected_agent_behavior": "Agent rejects/clarifies and asks again.", "caller_line": "Sorry, my name is Srija."},
    {"expected_agent_behavior": "Agent asks for date of birth.", "caller_line": "My date of birth is 15th March 2002."},
]


def test_to_scenario_dict_produces_seed_turns_in_order():
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "Verify identity handling.", NODE_SCRIPT)
    assert d["seed_turns"] == [
        "My name is Rahul.",
        "Sorry, my name is Srija.",
        "My date of birth is 15th March 2002.",
    ]


def test_to_scenario_dict_caller_lines_preserved_exactly_not_rewritten():
    exact = "My name is Rahul."
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "goal", [
        {"expected_agent_behavior": "x", "caller_line": exact}
    ])
    assert d["seed_turns"][0] == exact  # byte-for-byte, no normalization/rewriting


def test_to_scenario_dict_expected_behavior_is_numbered():
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "goal", NODE_SCRIPT)
    assert "1. Expected behavior: Agent asks for the caller's name." in d["expected_behavior"]
    assert "2. Expected behavior: Agent rejects/clarifies and asks again." in d["expected_behavior"]
    assert "3. Expected behavior: Agent asks for date of birth." in d["expected_behavior"]


def test_to_scenario_dict_test_type_and_fault():
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "goal", NODE_SCRIPT)
    assert d["test_type"] == "flow_node"
    assert d["assigned_fault"] == "none"
    assert d["flow_id"] == 1
    assert d["node_id"] == "authentication"


def test_to_scenario_dict_customer_context_empty_forces_scripted_mode():
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "goal", NODE_SCRIPT)
    assert d["customer_context"] == ""


def test_to_scenario_dict_user_goal_carries_the_test_goal_for_the_judge():
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "Verify identity handling.", NODE_SCRIPT)
    assert d["user_goal"] == "Verify identity handling."


def test_to_scenario_dict_survives_normalize_scenarios_with_flow_node_type_intact():
    """The existing save/run paths run every reviewed scenario through
    app.core.scenarios.normalize_scenarios(). It must not downgrade "flow_node" back
    to "support", and seed_turns must not be truncated (MAX_SCRIPT_TURNS is kept <=
    the 5-turn cap normalize_scenarios applies to every scenario).
    """
    d = to_scenario_dict(1, "authentication", "Caller Authentication", "goal", NODE_SCRIPT)
    normalized = normalize_scenarios([d])[0]
    assert normalized["test_type"] == "flow_node"
    assert normalized["assigned_fault"] == "none"
    assert normalized["seed_turns"] == d["seed_turns"]
    assert normalized["customer_context"] == ""


async def test_prerequisites_are_woven_into_the_prompt_in_order(monkeypatch):
    seen = {}

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        seen["prompt"] = messages[0]["content"]
        return {
            "test_goal": "goal",
            "script": [{"expected_agent_behavior": "x", "caller_line": "y"}],
        }

    monkeypatch.setattr(node_script, "chat", fake_chat)
    await generate_node_script(ACCOUNT_HELP, [GREETING, AUTH], None)
    prompt = seen["prompt"]
    assert prompt.index("Greeting") < prompt.index("Caller Authentication")
    assert "Account Help" in prompt  # the target node itself is described too
