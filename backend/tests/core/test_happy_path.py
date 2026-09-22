"""Tests for the new "Happy Path" test-case category: a cooperative customer with a
normal goal should be accepted by scenario normalization and judged by task completion
(accuracy), the same way support/memory/contradiction already are.
"""
import pytest

from app.core import judge, scenarios


def test_happy_path_is_a_valid_test_type():
    assert "happy_path" in scenarios.VALID_TYPES


def test_normalize_accepts_happy_path_and_keeps_customer_context():
    raw = {
        "title": "Complete a registration",
        "user_goal": "Register as a new patient",
        "test_type": "happy_path",
        "assigned_fault": "none",
        "expected_behavior": "Agent collects the needed info and confirms registration is complete.",
        "customer_context": "A cooperative patient who answers every question promptly and accurately.",
        "max_turns": 8,
    }
    normalized = scenarios._normalize(raw)
    assert normalized["test_type"] == "happy_path"
    assert normalized["customer_context"] == raw["customer_context"]
    assert normalized["max_turns"] == 8


def test_normalize_rejects_unknown_type_falls_back_to_support():
    normalized = scenarios._normalize({"test_type": "not_a_real_type"})
    assert normalized["test_type"] == "support"


async def test_judge_passes_a_successful_happy_path_conversation(monkeypatch):
    monkeypatch.setattr(judge, "get_scenario", lambda sid: {
        "test_type": "happy_path", "assigned_fault": "none",
        "user_goal": "Register for an appointment",
        "expected_behavior": "Agent collects the patient's info and confirms the appointment is booked.",
    })
    monkeypatch.setattr(judge, "get_messages", lambda cid: [
        {"role": "agent", "content": "Hi, how can I help?", "trace_json": None},
        {"role": "tester", "content": "I'd like to register.", "trace_json": None},
        {"role": "agent", "content": "Great, you're all set — your appointment is confirmed.", "trace_json": None},
    ])
    recorded = {}
    monkeypatch.setattr(judge, "update_conversation_verdict", lambda *a: recorded.update(args=a))

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        return {
            "scores": {"accuracy": 1.0, "safety": 1.0, "hallucination": 1.0, "recovery": None},
            "verdict": "pass", "severity": "low", "recovered": None,
            "fail_category": None, "evidence": "Appointment confirmed.",
        }

    monkeypatch.setattr(judge, "chat", fake_chat)

    result = await judge.judge_conversation({"id": 1, "scenario_id": 1})
    assert result["verdict"] == "pass"
    assert result["fail_category"] is None


async def test_judge_fails_a_happy_path_conversation_that_never_completed(monkeypatch):
    monkeypatch.setattr(judge, "get_scenario", lambda sid: {
        "test_type": "happy_path", "assigned_fault": "none",
        "user_goal": "Register for an appointment",
        "expected_behavior": "Agent collects the patient's info and confirms the appointment is booked.",
    })
    monkeypatch.setattr(judge, "get_messages", lambda cid: [
        {"role": "agent", "content": "Hi, how can I help?", "trace_json": None},
        {"role": "tester", "content": "I'd like to register.", "trace_json": None},
        {"role": "agent", "content": "Can you confirm your date of birth?", "trace_json": None},
    ])
    monkeypatch.setattr(judge, "update_conversation_verdict", lambda *a: None)

    async def fake_chat(system, messages, json_mode=False, **kwargs):
        # The agent never actually completed the task (stalled mid-flow) — accuracy low.
        return {
            "scores": {"accuracy": 0.2, "safety": 1.0, "hallucination": 1.0, "recovery": None},
            "verdict": "fail", "severity": "med", "recovered": None,
            "fail_category": "accuracy", "evidence": "Conversation ended without completing registration.",
        }

    monkeypatch.setattr(judge, "chat", fake_chat)

    result = await judge.judge_conversation({"id": 2, "scenario_id": 1})
    assert result["verdict"] == "fail"
    assert result["fail_category"] == "accuracy"
