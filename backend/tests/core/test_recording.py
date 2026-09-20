"""Unit tests for app.core.recording — the WAV-building/cleanup primitives, in
isolation from any transport. Real (small, synthetic) WAV/mu-law bytes are used
throughout rather than mocks, since this module's whole job is decoding/resampling
real audio correctly.
"""
import io
import os
import struct
import wave

import pytest

from app.core import recording
from app.core.twilio_bridge import pcm16_to_mulaw


def make_wav(num_samples: int, rate: int = 24000, value: int = 1000) -> bytes:
    """A minimal valid mono 16-bit WAV of constant-value samples — real, decodable
    audio bytes, not a mock."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{num_samples}h", *([value] * num_samples)))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "RECORDINGS_DIR", str(tmp_path))
    recording._RECORDINGS.clear()
    yield
    recording._RECORDINGS.clear()


def test_append_wav_then_finalize_writes_a_valid_wav_with_all_audio():
    key = "conv-1"
    recording.append_wav(key, make_wav(2400, rate=24000))  # 0.1s @ 24kHz
    recording.append_wav(key, make_wav(1600, rate=16000))  # 0.1s @ 16kHz (different rate)

    result = recording.finalize_recording(key)
    assert result is not None
    assert os.path.exists(result.path)
    assert result.path.endswith(f"conversation-{key}.wav")
    assert result.agent_only is False  # http_json/websocket always represent both sides

    with wave.open(result.path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == recording.CANONICAL_RATE_HZ
        total_frames = w.getnframes()

    # Both segments were resampled to CANONICAL_RATE_HZ then concatenated — total
    # duration should be ~0.2s (2 x 0.1s), not merely non-zero.
    expected = int(2400 * recording.CANONICAL_RATE_HZ / 24000) + int(1600 * recording.CANONICAL_RATE_HZ / 16000)
    assert abs(total_frames - expected) <= 4  # resampling rounding tolerance


def test_append_mulaw_then_finalize_produces_canonical_rate_wav():
    key = "conv-mulaw"
    pcm = struct.pack("<800h", *([500] * 800))  # 0.1s @ 8kHz
    recording.append_mulaw(key, pcm16_to_mulaw(pcm))

    result = recording.finalize_recording(key)
    assert result is not None
    assert result.agent_only is False  # twilio always represents both sides
    with wave.open(result.path, "rb") as w:
        assert w.getframerate() == recording.CANONICAL_RATE_HZ
        assert w.getnframes() > 0


def test_append_pcm16_then_finalize_resamples_and_marks_agent_only():
    """native_ws frames are ALREADY raw PCM16 (no WAV header, no mu-law) — this is
    the simplest of the three append paths: resample straight to CANONICAL_RATE_HZ."""
    key = "conv-pcm16"
    pcm_8k = struct.pack("<800h", *([700] * 800))    # 0.1s @ 8kHz
    pcm_16k = struct.pack("<1600h", *([700] * 1600))  # 0.1s @ 16kHz (already canonical)
    recording.append_pcm16(key, pcm_8k, rate=8000)
    recording.append_pcm16(key, pcm_16k, rate=16000)

    result = recording.finalize_recording(key)
    assert result is not None
    assert result.agent_only is True  # the ONLY function native_ws ever calls

    with wave.open(result.path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == recording.CANONICAL_RATE_HZ
        total_frames = w.getnframes()

    expected = int(800 * recording.CANONICAL_RATE_HZ / 8000) + 1600  # second segment needs no resampling
    assert abs(total_frames - expected) <= 4


def test_append_pcm16_default_rate_is_16000_no_resampling_needed():
    key = "conv-pcm16-default-rate"
    pcm = struct.pack("<1600h", *([300] * 1600))  # 0.1s already @ CANONICAL_RATE_HZ
    recording.append_pcm16(key, pcm)  # rate defaults to 16000

    result = recording.finalize_recording(key)
    assert result is not None
    with wave.open(result.path, "rb") as w:
        assert w.getnframes() == 1600  # exact — no resampling distortion when already canonical


def test_append_pcm16_ignores_empty_audio_and_none_session_key():
    recording.append_pcm16("conv-pcm16-empty", b"")
    assert recording.finalize_recording("conv-pcm16-empty") is None
    recording.append_pcm16(None, struct.pack("<10h", *([1] * 10)))
    assert recording._RECORDINGS == {}


def test_wav_and_pcm16_in_the_same_conversation_is_never_agent_only():
    """Documents the invariant FinalizedRecording.agent_only relies on: it's only
    ever correct because a real conversation never mixes append_wav()/append_mulaw()
    with append_pcm16() (a conversation uses exactly one voice_protocol). If it
    somehow did, agent_only reflects "was append_pcm16 ever called", which is the
    conservative direction — never silently claiming agent_only when real caller
    audio (from append_wav) is actually present too."""
    key = "conv-mixed-hypothetical"
    recording.append_wav(key, make_wav(1600))
    recording.append_pcm16(key, struct.pack("<1600h", *([1] * 1600)))
    result = recording.finalize_recording(key)
    assert result is not None
    assert result.agent_only is True


def test_finalize_with_nothing_recorded_returns_none_and_writes_no_file(tmp_path):
    assert recording.finalize_recording("never-touched") is None
    assert os.listdir(tmp_path) == []


def test_finalize_clears_the_buffer_second_call_is_a_noop():
    """Cleanup: once finalized, the in-memory buffer for that conversation is gone —
    a second finalize (e.g. a stray duplicate close_fn call) neither writes again nor
    leaks memory."""
    key = "conv-cleanup"
    recording.append_wav(key, make_wav(1600))
    first = recording.finalize_recording(key)
    assert first is not None
    assert key not in recording._RECORDINGS

    second = recording.finalize_recording(key)
    assert second is None


def test_discard_recording_drops_buffer_without_writing():
    key = "conv-discard"
    recording.append_wav(key, make_wav(1600))
    recording.discard_recording(key)
    assert key not in recording._RECORDINGS
    assert recording.finalize_recording(key) is None


def test_append_wav_ignores_empty_or_undecodable_audio_without_raising():
    key = "conv-bad-audio"
    recording.append_wav(key, b"")            # empty
    recording.append_wav(key, b"not a wav")   # garbage — must not raise
    recording.append_mulaw(key, b"")          # empty
    # Nothing usable was ever appended, so there is nothing to finalize.
    assert recording.finalize_recording(key) is None


def test_append_wav_with_none_session_key_is_a_noop():
    recording.append_wav(None, make_wav(1600))
    assert recording._RECORDINGS == {}


def test_two_conversations_are_recorded_independently():
    recording.append_wav("conv-a", make_wav(800))
    recording.append_wav("conv-b", make_wav(1600))

    result_a = recording.finalize_recording("conv-a")
    result_b = recording.finalize_recording("conv-b")
    assert result_a.path != result_b.path

    with wave.open(result_a.path, "rb") as wa, wave.open(result_b.path, "rb") as wb:
        # conv-b had twice conv-a's raw sample count at the same input rate.
        assert wb.getnframes() > wa.getnframes()
