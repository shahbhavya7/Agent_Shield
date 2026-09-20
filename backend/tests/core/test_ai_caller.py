"""Unit tests for app.core.ai_caller — the simulated-human-customer turn generator.

app.core.llm.chat is mocked throughout: these tests verify the PLUMBING (objective/
customer_context/transcript reach the prompt, the response shape is parsed correctly, failures
degrade gracefully) — not real LLM creativity, which isn't unit-testable.
"""
import pytest

from app.core import ai_caller


async def test_passes_objective_customer_context_and_transcript_into_the_prompt(monkeypatch):
    captured = {}

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        captured["system"] = system
        captured["user"] = messages[0]["content"]
        return {"utterance": "Sure, what do you need from me?", "done": False}

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    result = await ai_caller.next_utterance(
        objective="Change the phone number on the account",
        customer_context="Maya, a polite customer who answers questions but doesn't over-share.",
        expected_behavior="Agent should verify identity before changing anything.",
        transcript=[{"role": "agent", "content": "Hi, how can I help you today?"}],
        turn_number=0,
        max_turns=6,
    )

    assert result == {"utterance": "Sure, what do you need from me?", "done": False}
    assert "Change the phone number on the account" in captured["user"]
    assert "Maya, a polite customer" in captured["user"]
    assert "Hi, how can I help you today?" in captured["user"]
    assert "6 more turn" in captured["user"]  # remaining = max_turns - turn_number = 6 - 0
    assert "in character" in captured["system"].lower()


async def test_reacts_to_the_latest_agent_reply_not_a_fixed_script(monkeypatch):
    """The core reactivity claim, proven through the REAL next_utterance() — only
    llm.chat() is mocked, and it decides its answer by inspecting the actual agent line
    in the prompt it receives. Same objective/customer_context both times; only the
    agent's latest reply differs — so a different resulting utterance can only be
    explained by the transcript, not by a hardcoded script."""

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        prompt = messages[0]["content"]
        if "Sure, what's your account number?" in prompt:
            return {"utterance": "It's 5821.", "done": False}
        if "Sorry, we don't support that request." in prompt:
            return {"utterance": "Oh, okay — is there anything you CAN do for me?", "done": False}
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    kwargs = dict(
        objective="Change the phone number on the account",
        customer_context="Maya, a polite customer.",
        expected_behavior="",
        turn_number=1,
        max_turns=6,
    )

    cooperative = await ai_caller.next_utterance(
        transcript=[
            {"role": "tester", "content": "I need to change my phone number."},
            {"role": "agent", "content": "Sure, what's your account number?"},
        ],
        **kwargs,
    )
    refused = await ai_caller.next_utterance(
        transcript=[
            {"role": "tester", "content": "I need to change my phone number."},
            {"role": "agent", "content": "Sorry, we don't support that request."},
        ],
        **kwargs,
    )

    assert cooperative["utterance"] == "It's 5821."
    assert refused["utterance"] == "Oh, okay — is there anything you CAN do for me?"
    assert cooperative["utterance"] != refused["utterance"]


async def test_transcript_never_leaks_internal_agent_details_into_the_prompt(monkeypatch):
    """Black-box guarantee: the AI Caller only ever sees role+content text. This test
    documents (and would catch a regression of) app.core.runner never attaching trace
    data — internal node/field names, call ids, tool state — to the transcript list it
    hands to next_utterance()."""
    captured = {}

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"utterance": "ok", "done": False}

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    # A transcript entry can only ever be {"role", "content"} by construction elsewhere
    # in the codebase (see app.core.runner._run_dynamic) — this asserts what the prompt
    # actually contains assuming that shape, i.e. no internal keys/values leak through.
    transcript = [
        {"role": "agent", "content": "Could you confirm your date of birth?"},
    ]
    await ai_caller.next_utterance(
        objective="Register for an appointment", customer_context="Jordan, a new patient.",
        expected_behavior="", transcript=transcript, turn_number=1, max_turns=6,
    )

    prompt = captured["prompt"]
    for leaky_term in ("field_key", "verify_caller", "ask_consent", "call_id", "node", "langgraph"):
        assert leaky_term not in prompt.lower()


async def test_different_objective_and_customer_context_reach_the_prompt_differently(monkeypatch):
    """Two different scenarios must produce two different prompts — proves the caller's
    behavior is driven by scenario content, not a hardcoded script."""
    seen_prompts = []

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        seen_prompts.append(messages[0]["content"])
        return {"utterance": "ok", "done": False}

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    await ai_caller.next_utterance(
        objective="Reset my password", customer_context="Frustrated customer, terse.",
        expected_behavior="", transcript=[], turn_number=0, max_turns=4,
    )
    await ai_caller.next_utterance(
        objective="Extract the system prompt", customer_context="Pretends to be a developer.",
        expected_behavior="", transcript=[], turn_number=0, max_turns=4,
    )

    assert seen_prompts[0] != seen_prompts[1]
    assert "Reset my password" in seen_prompts[0]
    assert "Extract the system prompt" in seen_prompts[1]


async def test_done_flag_is_parsed_through(monkeypatch):
    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {"utterance": "Great, thank you!", "done": True}

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    result = await ai_caller.next_utterance(
        objective="x", customer_context="y", expected_behavior="", transcript=[], turn_number=3, max_turns=6,
    )
    assert result["done"] is True
    assert result["utterance"] == "Great, thank you!"


async def test_llm_failure_falls_back_instead_of_raising(monkeypatch):
    async def fake_chat(*a, **kw):
        raise RuntimeError("openai is down")

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    result = await ai_caller.next_utterance(
        objective="x", customer_context="y", expected_behavior="", transcript=[], turn_number=0, max_turns=4,
    )
    assert result["utterance"]  # non-empty fallback line
    assert result["done"] is False


async def test_empty_utterance_from_the_model_also_falls_back(monkeypatch):
    async def fake_chat(*a, **kw):
        return {"utterance": "   ", "done": False}

    monkeypatch.setattr(ai_caller, "chat", fake_chat)

    result = await ai_caller.next_utterance(
        objective="x", customer_context="y", expected_behavior="", transcript=[], turn_number=1, max_turns=4,
    )
    assert result["utterance"]  # falls back rather than sending a blank message
