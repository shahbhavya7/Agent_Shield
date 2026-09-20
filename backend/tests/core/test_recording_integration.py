"""Integration: a multi-turn http_json voice run produces exactly ONE recording
containing the complete caller/agent conversation, finalized via
app.core.voice_caller.close_voice_session() — the SAME guaranteed cleanup hook every
voice protocol already uses — on both a fully successful run and one where a turn
fails partway through.

Only text_to_speech/speech_to_text/http_send are mocked (real, synthetic WAV bytes
stand in for the LLM's actual audio) — app.core.runner.run_scenario(),
app.core.voice_caller.call_voice_agent()/close_voice_session(), and
app.core.recording itself all run for real, exactly as they would in production.
DB writes are faked in-memory (same pattern as test_run_scenario_native_ws.py).
"""
import base64
import io
import os
import struct
import wave

import pytest

import app.db as db_module
from app.core import recording, runner, voice_caller


def make_wav(num_samples: int, rate: int = 24000, value: int = 1000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{num_samples}h", *([value] * num_samples)))
    return buf.getvalue()


class _FakeDB:
    def __init__(self):
        self.messages: dict[int, list[dict]] = {}
        self._next_id = 1
        self._by_key: dict[tuple, int] = {}

    def get_or_create_conversation(self, run_id, scenario_id, idem_key=None):
        key = idem_key or (run_id, scenario_id, self._next_id)
        if key in self._by_key:
            return self._by_key[key]
        cid = self._next_id
        self._next_id += 1
        self._by_key[key] = cid
        self.messages[cid] = []
        return cid

    def clear_messages(self, conversation_id):
        self.messages[conversation_id] = []
        return 0

    def insert_message(self, conversation_id, turn_index, role, content, trace):
        self.messages[conversation_id].append(
            {"turn_index": turn_index, "role": role, "content": content, "trace": trace}
        )
        return len(self.messages[conversation_id])


@pytest.fixture()
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(runner, "get_or_create_conversation", db.get_or_create_conversation)
    monkeypatch.setattr(runner, "clear_messages", db.clear_messages)
    monkeypatch.setattr(runner, "insert_message", db.insert_message)
    return db


@pytest.fixture()
def recorded_paths(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        db_module, "set_conversation_recording",
        lambda cid, path, agent_only=False: calls.append((cid, path, agent_only)),
    )
    return calls


@pytest.fixture(autouse=True)
def _isolate_recordings(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "RECORDINGS_DIR", str(tmp_path))
    recording._RECORDINGS.clear()
    yield
    recording._RECORDINGS.clear()


async def _fake_tts(text: str) -> bytes:
    return make_wav(2400)  # 0.1s per caller line, regardless of text


async def _fake_stt(audio: bytes) -> str:
    return "ok, got it"


def _agent_reply_wav_b64(num_samples: int = 1600) -> str:
    return base64.b64encode(make_wav(num_samples)).decode("ascii")


async def test_multi_turn_http_json_run_produces_one_complete_recording(monkeypatch, fake_db, recorded_paths):
    async def fake_http_send(agent, audio_in_b64, history, faults):
        return {"reply": _agent_reply_wav_b64(), "trace": {}}

    monkeypatch.setattr(voice_caller, "text_to_speech", _fake_tts)
    monkeypatch.setattr(voice_caller, "speech_to_text", _fake_stt)
    monkeypatch.setattr(voice_caller, "http_send", fake_http_send)

    scenario = {
        "_id": 1, "test_type": "support", "assigned_fault": "none",
        "seed_turns": ["Hello, I need help.", "Great, thanks for that."],
    }
    agent = {"voice_protocol": "http_json"}

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=agent,
        send_fn=voice_caller.call_voice_agent, close_fn=voice_caller.close_voice_session,
    )

    # Exactly one recording file for this conversation.
    files = os.listdir(recording.RECORDINGS_DIR)
    assert files == [f"conversation-{conv_id}.wav"]

    # It contains all 4 real audio segments (2 caller TTS + 2 agent replies).
    with wave.open(os.path.join(recording.RECORDINGS_DIR, files[0]), "rb") as w:
        assert w.getframerate() == recording.CANONICAL_RATE_HZ
        total_frames = w.getnframes()
    per_turn = int(2400 * recording.CANONICAL_RATE_HZ / 24000)  # caller segment, resampled
    agent_per_turn = int(1600 * recording.CANONICAL_RATE_HZ / 24000)  # agent segment, resampled
    expected = 2 * per_turn + 2 * agent_per_turn
    assert abs(total_frames - expected) <= 8

    # Persisted against the right conversation id, marked as NOT agent-only (both
    # sides are real here), and no memory leak afterward.
    assert recorded_paths == [(conv_id, os.path.join(recording.RECORDINGS_DIR, files[0]), False)]
    assert conv_id not in recording._RECORDINGS


async def test_recording_survives_a_turn_failure_and_still_cleans_up(monkeypatch, fake_db, recorded_paths):
    """Turn 1 succeeds (both sides recorded); turn 2's agent call fails (the existing,
    real failure mode — adapter.send() returns the error sentinel, it never raises).
    Turn 2's caller line was still actually spoken before the failure, so it's still
    in the recording — this documents what really happened, not a sanitized version
    of it. close_fn still finalizes exactly once, with no leaked buffer."""
    call_count = {"n": 0}

    async def flaky_http_send(agent, audio_in_b64, history, faults):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"reply": _agent_reply_wav_b64(), "trace": {}}
        return {"reply": "<error>", "trace": {"error": "simulated transport failure"}}

    monkeypatch.setattr(voice_caller, "text_to_speech", _fake_tts)
    monkeypatch.setattr(voice_caller, "speech_to_text", _fake_stt)
    monkeypatch.setattr(voice_caller, "http_send", flaky_http_send)

    scenario = {
        "_id": 2, "test_type": "support", "assigned_fault": "none",
        "seed_turns": ["First message", "Second message"],
    }
    agent = {"voice_protocol": "http_json"}

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=agent,
        send_fn=voice_caller.call_voice_agent, close_fn=voice_caller.close_voice_session,
    )

    # Transcript shows the failure, exactly like any other transport error.
    transcript = fake_db.messages[conv_id]
    assert transcript[3]["content"] == "<error>"

    # A recording still got written — turn 1's audio, plus turn 2's caller line.
    files = os.listdir(recording.RECORDINGS_DIR)
    assert files == [f"conversation-{conv_id}.wav"]
    with wave.open(os.path.join(recording.RECORDINGS_DIR, files[0]), "rb") as w:
        assert w.getnframes() > 0

    # Cleanup happened exactly once, and nothing leaked in memory afterward.
    assert len(recorded_paths) == 1
    assert conv_id not in recording._RECORDINGS


async def test_no_recording_when_every_turn_fails_before_any_audio_exists(monkeypatch, fake_db, recorded_paths):
    """If TTS itself never succeeds, no real audio was ever produced — no recording
    should be fabricated, and close_fn must still complete cleanly."""
    async def failing_tts(text: str) -> bytes:
        raise RuntimeError("TTS service down")

    monkeypatch.setattr(voice_caller, "text_to_speech", failing_tts)

    scenario = {"_id": 3, "test_type": "support", "assigned_fault": "none", "seed_turns": ["Hi"]}
    agent = {"voice_protocol": "http_json"}

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=agent,
        send_fn=voice_caller.call_voice_agent, close_fn=voice_caller.close_voice_session,
    )

    assert os.listdir(recording.RECORDINGS_DIR) == []
    assert recorded_paths == []  # set_conversation_recording never called
    assert conv_id not in recording._RECORDINGS


async def test_native_ws_scenario_with_no_audio_frames_produces_no_recording(monkeypatch, fake_db, recorded_paths):
    """A native_ws target that (like the fake here) never actually sends any binary
    frames leaves nothing real to record — no recording is fabricated for it."""
    from tests.core.fakes_native_ws import FakeAsyncClient, FakeConnect, FakeWebSocket, turn_frame
    import app.core.voice_native_ws as nws

    nws._SESSIONS.clear()
    ws = FakeWebSocket(incoming=[
        turn_frame("agent", "hi"),
        turn_frame("caller", "hello"),
        turn_frame("agent", "how can I help"),
    ])
    connect = FakeConnect([ws])
    calls_log: list[dict] = []
    http_client = FakeAsyncClient(calls_log, call_id="call-rec-test")
    monkeypatch.setattr(nws.websockets, "connect", connect)
    monkeypatch.setattr(nws.httpx, "AsyncClient", http_client.factory())

    scenario = {"_id": 4, "test_type": "support", "assigned_fault": "none", "seed_turns": ["hello"]}
    agent = {"voice_protocol": "native_ws", "endpoint_url": "http://localhost:8000"}

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=agent,
        send_fn=voice_caller.call_voice_agent, close_fn=voice_caller.close_voice_session,
    )

    assert os.listdir(recording.RECORDINGS_DIR) == []
    assert recorded_paths == []
    assert conv_id not in recording._RECORDINGS
    nws._SESSIONS.clear()


async def test_native_ws_multi_turn_run_produces_one_agent_only_recording(monkeypatch, fake_db, recorded_paths):
    """A native_ws target that DOES send real binary audio (PCM16, exactly like the
    live Maya target) gets a real, playable, agent-only recording spanning every
    turn of the scenario — never a separate recording per turn.

    Uses a purpose-built gated fake rather than the shared FakeWebSocket: a real
    target can't stream turn N's audio before we've sent turn N's
    simulated_utterance, but the shared fake has no sense of time — its whole
    queue exists upfront — so the bounded post-turn drain would otherwise
    (unrealistically) reach past the end of one turn's frames into the next
    turn's, which haven't "happened" yet on a real connection.
    """
    from tests.core.fakes_native_ws import FakeAsyncClient, FakeConnect, turn_frame
    import app.core.voice_native_ws as nws

    class _GatedFakeWebSocket:
        """`greeting_frames` are available immediately (nothing sent yet — matches
        the target speaking first, unprompted). Each further group in `turn_groups`
        is revealed only after the matching send() — modeling one real turn's
        request/response causality at a time."""

        def __init__(self, greeting_frames, turn_groups):
            self._queue = list(greeting_frames)
            self._turn_groups = list(turn_groups)
            self.sent: list[str] = []

        async def send(self, data):
            self.sent.append(data)
            if self._turn_groups:
                self._queue.extend(self._turn_groups.pop(0))

        async def recv(self):
            if not self._queue:
                raise RuntimeError("gated fake: nothing queued for this turn yet")
            return self._queue.pop(0)

        async def close(self, code=1000, reason=""):
            pass

    nws._SESSIONS.clear()
    turn1_audio = struct.pack("<800h", *([600] * 800))   # 0.05s @ 16kHz
    turn2_audio = struct.pack("<1600h", *([600] * 1600))  # 0.1s @ 16kHz
    ws = _GatedFakeWebSocket(
        greeting_frames=[turn_frame("agent", "hi, this is Maya")],
        turn_groups=[
            [turn1_audio, turn_frame("agent", "hi, first reply")],
            [turn2_audio, turn_frame("agent", "second reply")],
        ],
    )
    connect = FakeConnect([ws])
    calls_log: list[dict] = []
    http_client = FakeAsyncClient(calls_log, call_id="call-rec-multi")
    monkeypatch.setattr(nws.websockets, "connect", connect)
    monkeypatch.setattr(nws.httpx, "AsyncClient", http_client.factory())

    scenario = {
        "_id": 5, "test_type": "support", "assigned_fault": "none",
        "seed_turns": ["first message", "second message"],
    }
    agent = {"voice_protocol": "native_ws", "endpoint_url": "http://localhost:8000"}

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=agent,
        send_fn=voice_caller.call_voice_agent, close_fn=voice_caller.close_voice_session,
    )

    # Exactly ONE recording file for the whole scenario, not one per turn.
    files = os.listdir(recording.RECORDINGS_DIR)
    assert files == [f"conversation-{conv_id}.wav"]

    path = os.path.join(recording.RECORDINGS_DIR, files[0])
    with wave.open(path, "rb") as w:
        assert w.getframerate() == recording.CANONICAL_RATE_HZ
        assert w.getnchannels() == 1
        total_frames = w.getnframes()
    # Both turns' real audio are present (800 + 1600 raw samples, already @ 16kHz).
    assert abs(total_frames - 2400) <= 4

    # Marked agent-only end to end — never presented as a full two-sided call.
    assert recorded_paths == [(conv_id, path, True)]
    assert conv_id not in recording._RECORDINGS
    nws._SESSIONS.clear()
