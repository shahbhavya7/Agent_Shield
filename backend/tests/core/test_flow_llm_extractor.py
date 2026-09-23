"""Tests for the LLM-assisted flow extraction fallback (Part 12 CASE 7-8).

The LLM is mocked throughout — no real OpenAI calls. Validates that:
  - a well-formed model response produces a usable normalized flow;
  - malformed/invalid/hallucinated model output is rejected by the SAME validation
    rules the deterministic path uses (never trusted blindly).
"""
import pytest

from app.core import flow_llm_extractor
from app.core.flow_llm_extractor import FlowExtractionError, extract_flow_with_llm


async def test_llm_extraction_returns_usable_flow(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        assert json_mode is True
        return {
            "agent_name": "Renewal Bot",
            "nodes": [
                {"id": "greeting", "name": "Greeting", "purpose": "Welcome the caller"},
                {"id": "renewal", "name": "Renewal", "purpose": "Process the policy renewal"},
            ],
            "edges": [{"from": "greeting", "to": "renewal"}],
        }

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    result = await extract_flow_with_llm("some ambiguous source text", "yaml")
    assert result["extraction_method"] == "llm"
    assert result["source_format"] == "yaml"
    assert len(result["nodes"]) == 2
    assert result["edges"] == [{"from": "greeting", "to": "renewal"}]


async def test_llm_extraction_rejects_non_dict_result(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return "not a dict"

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    with pytest.raises(FlowExtractionError):
        await extract_flow_with_llm("source", "json")


async def test_llm_extraction_rejects_empty_nodes(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    with pytest.raises(FlowExtractionError):
        await extract_flow_with_llm("source", "json")


async def test_llm_extraction_rejects_duplicate_node_ids(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {
            "nodes": [{"id": "a", "name": "A"}, {"id": "a", "name": "A again"}],
            "edges": [],
        }

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    with pytest.raises(FlowExtractionError):
        await extract_flow_with_llm("source", "json")


async def test_llm_extraction_rejects_edge_to_unknown_node(monkeypatch):
    """A hallucinated edge pointing at a node the model didn't actually return must be
    rejected — the LLM path uses the STRICT edge validator, unlike the lenient inline
    scan on the deterministic side, since this is a fully model-declared edges list.
    """
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {
            "nodes": [{"id": "a", "name": "A"}],
            "edges": [{"from": "a", "to": "does_not_exist"}],
        }

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    with pytest.raises(FlowExtractionError):
        await extract_flow_with_llm("source", "json")


async def test_llm_extraction_rejects_malformed_node_shape(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {"nodes": "not-a-list", "edges": []}

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    with pytest.raises(FlowExtractionError):
        await extract_flow_with_llm("source", "json")


async def test_llm_extraction_truncates_pathologically_large_source(monkeypatch):
    seen = {}

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        seen["content_len"] = len(messages[0]["content"])
        return {"nodes": [{"id": "a", "name": "A"}], "edges": []}

    monkeypatch.setattr(flow_llm_extractor, "chat", fake_chat)
    huge = "x" * (flow_llm_extractor.MAX_SOURCE_CHARS + 5000)
    await extract_flow_with_llm(huge, "json")
    assert seen["content_len"] == flow_llm_extractor.MAX_SOURCE_CHARS
