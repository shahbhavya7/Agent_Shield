"""Regression: adding native_ws must not change how any existing voice_protocol
dispatches. Each branch is monkeypatched at the point voice_caller imports it, so
these tests never touch a real network, TTS/STT, or Twilio.
"""
import pytest

from app.core import voice_caller


async def test_http_json_dispatch_unchanged(monkeypatch):
    called = {}

    async def fake_http_json(agent, message, history, faults):
        called["args"] = (agent, message, history, faults)
        return {"reply": "ok", "trace": {}}

    monkeypatch.setattr(voice_caller, "_call_via_http_json", fake_http_json)

    result = await voice_caller.call_voice_agent({"voice_protocol": "http_json"}, "hi", [], [])
    assert result == {"reply": "ok", "trace": {}}
    assert called["args"] == ({"voice_protocol": "http_json"}, "hi", [], [])


async def test_default_protocol_is_still_http_json(monkeypatch):
    async def fake_http_json(agent, message, history, faults):
        return {"reply": "default-path", "trace": {}}

    monkeypatch.setattr(voice_caller, "_call_via_http_json", fake_http_json)

    result = await voice_caller.call_voice_agent({}, "hi")  # no voice_protocol key at all
    assert result["reply"] == "default-path"


async def test_websocket_dispatch_unchanged(monkeypatch):
    async def fake_ws(agent, message, history, faults):
        return {"reply": "ws-ok", "trace": {}}

    monkeypatch.setattr(voice_caller, "_call_via_websocket", fake_ws)

    result = await voice_caller.call_voice_agent({"voice_protocol": "websocket"}, "hi", [], [])
    assert result["reply"] == "ws-ok"


async def test_twilio_dispatch_unchanged(monkeypatch):
    import app.core.twilio_bridge as twilio_bridge

    async def fake_twilio(agent, message, history, faults, session_key=None, is_last_turn=False):
        return {"reply": "twilio-ok", "trace": {"session_key": session_key, "is_last_turn": is_last_turn}}

    monkeypatch.setattr(twilio_bridge, "_call_via_twilio", fake_twilio)

    result = await voice_caller.call_voice_agent(
        {"voice_protocol": "twilio"}, "hi", [], [], session_key="conv-9", is_last_turn=True,
    )
    assert result["reply"] == "twilio-ok"
    assert result["trace"] == {"session_key": "conv-9", "is_last_turn": True}


async def test_native_ws_dispatch_wired_correctly(monkeypatch):
    import app.core.voice_native_ws as native_ws

    async def fake_native(agent, message, history, faults, session_key=None, is_last_turn=False):
        return {"reply": "native-ok", "trace": {"session_key": session_key}}

    monkeypatch.setattr(native_ws, "_call_via_native_ws", fake_native)

    result = await voice_caller.call_voice_agent(
        {"voice_protocol": "native_ws"}, "hi", [], [], session_key="conv-native",
    )
    assert result["reply"] == "native-ok"
    assert result["trace"] == {"session_key": "conv-native"}


async def test_unknown_protocol_still_errors_the_same_way():
    result = await voice_caller.call_voice_agent({"voice_protocol": "not-a-real-protocol"}, "hi")
    assert result["reply"] == voice_caller.AGENT_ERROR_SENTINEL
    assert "unknown voice_protocol" in result["trace"]["error"]


async def test_close_voice_session_twilio_unchanged(monkeypatch):
    import app.core.twilio_bridge as twilio_bridge

    called = {}

    async def fake_close(session_key):
        called["session_key"] = session_key

    monkeypatch.setattr(twilio_bridge, "close_twilio_session", fake_close)

    await voice_caller.close_voice_session({"voice_protocol": "twilio"}, "conv-close-9")
    assert called["session_key"] == "conv-close-9"


async def test_close_voice_session_native_ws_wired_correctly(monkeypatch):
    import app.core.voice_native_ws as native_ws

    called = {}

    async def fake_close(session_key):
        called["session_key"] = session_key

    monkeypatch.setattr(native_ws, "close_native_ws_session", fake_close)

    await voice_caller.close_voice_session({"voice_protocol": "native_ws"}, "conv-close-native")
    assert called["session_key"] == "conv-close-native"


async def test_close_voice_session_http_json_and_websocket_are_noops():
    # Must not raise, and must not attempt to import twilio/native_ws machinery at all.
    await voice_caller.close_voice_session({"voice_protocol": "http_json"}, "conv-1")
    await voice_caller.close_voice_session({"voice_protocol": "websocket"}, "conv-2")
