"""Call recording — reuses the REAL audio already produced/transmitted by the voice
pipeline to build one WAV file per voice conversation, instead of synthesizing a
separate "recording" of the test.

What actually gets recorded, per voice_protocol (see app.core.voice_caller's module
docstring for the wire contract each one speaks):

  http_json / websocket -> BOTH sides' real audio: the tester's line, TTS'd and
      ACTUALLY POSTed/sent to the agent (app.core.llm.text_to_speech's output — not a
      separate synthesis just for this feature), and the agent's own returned audio
      bytes, ACTUALLY received back. Captured in app.core.voice_caller right where
      those bytes already exist, in _call_via_http_json / _call_via_websocket.

  twilio -> BOTH sides' real audio: the mu-law frames actually sent down the phone
      line, and the mu-law bytes actually captured back from the live Media Stream.
      Captured in app.core.twilio_bridge.handle_media_stream(), where both already
      exist (`request.frames` and the reply `buffer`).

  native_ws -> AGENT AUDIO ONLY. This protocol is text-only (`simulated_utterance`)
      on the caller side — the target's own pipeline never processes a caller
      utterance as audio in this mode, so no real caller audio ever exists to
      record, and none is fabricated here. The agent's own spoken reply DOES exist
      on the wire as binary WebSocket frames (PCM16 mono 16kHz, no header — the
      target's own wire format), and app.core.voice_native_ws now captures those
      real frames (see its module docstring for exactly how) and hands them to
      append_pcm16() below. A native_ws recording is therefore real, but ONE-SIDED
      — marked agent_only=True end to end (this module's _Recording.agent_only,
      app.db.conversations.recording_agent_only, and the API's
      "recording_agent_only" field) precisely so nothing downstream ever presents
      an agent-only file as a full two-sided call.

  chat modality -> never applicable; no audio exists anywhere in a chat run.

Turns are appended IN THE ORDER THEY HAPPENED and concatenated sequentially into one
mono WAV per conversation — an accurate representation of a turn-based test (each side
speaks once per turn, never simultaneously), not an approximation of a real duplex call.

One conversation's audio is buffered in memory only while its scenario is running.
finalize_recording() — called exactly once, unconditionally, from
app.core.voice_caller.close_voice_session(), the SAME guaranteed cleanup hook every
voice protocol already uses regardless of success or failure — writes it to disk and
clears the buffer. A conversation that never produced any audio (native_ws, a chat
scenario, or a voice scenario that failed before its first TTS/receive completed)
writes nothing and finalize_recording() returns None, so nothing false is ever
persisted or served.
"""
import os
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional

from app.config import RECORDINGS_DIR
# Reusing the EXISTING PCM/WAV/mu-law helpers Twilio recording already needed, rather
# than duplicating audio-format code — see that module's docstring for why there is no
# audioop dependency. Importing its "private" helpers here is deliberate: this module
# and twilio_bridge both need the same decode/resample/encode primitives, and moving
# them to a third shared module would touch twilio_bridge.py for a pure rename, which
# the recording feature doesn't need to risk.
from app.core.twilio_bridge import (
    SAMPLE_RATE_HZ as _TWILIO_RATE_HZ,
    _pcm16_to_wav_bytes,
    _resample_pcm16,
    _wav_bytes_to_pcm16,
    mulaw_to_pcm16,
)

# The single internal rate every recording is normalized to before concatenation, so a
# conversation mixing (say) OpenAI TTS's own rate and Twilio's 8kHz mu-law still
# produces one consistent, correctly-pitched WAV rather than something garbled.
CANONICAL_RATE_HZ = 16000


class FinalizedRecording(NamedTuple):
    """finalize_recording()'s result. `agent_only` is True iff every segment ever
    appended for this conversation came from append_pcm16() (native_ws) — the ONLY
    caller of that function; append_wav()/append_mulaw() (http_json/websocket/
    twilio) always represent both sides, so a conversation using those is never
    agent_only. A conversation only ever uses ONE voice_protocol, so this never
    needs to reconcile a mix."""
    path: str
    agent_only: bool


@dataclass
class _Recording:
    session_key: Any
    segments: list[bytes] = field(default_factory=list)  # PCM16 mono @ CANONICAL_RATE_HZ, in order
    # True once any segment has come from append_pcm16() (native_ws, agent-only
    # audio). See FinalizedRecording.agent_only for why this never needs to be
    # reconciled against the other append_*() functions.
    agent_only: bool = False


_RECORDINGS: dict[Any, _Recording] = {}


def _get_or_create(session_key: Any) -> _Recording:
    rec = _RECORDINGS.get(session_key)
    if rec is None:
        rec = _Recording(session_key=session_key)
        _RECORDINGS[session_key] = rec
    return rec


def append_wav(session_key: Any, wav_bytes: bytes) -> None:
    """Append one turn's REAL, already-transmitted WAV audio (http_json/websocket) to
    the in-progress recording for this conversation.

    Never raises: a decode failure on one turn's audio must not break the test
    itself — it only silently skips that turn's contribution to the recording.
    """
    if session_key is None or not wav_bytes:
        return
    try:
        pcm, rate = _wav_bytes_to_pcm16(wav_bytes)
        pcm = _resample_pcm16(pcm, rate, CANONICAL_RATE_HZ)
    except Exception:
        return
    _get_or_create(session_key).segments.append(pcm)


def append_mulaw(session_key: Any, mulaw_bytes: bytes) -> None:
    """Append one turn's REAL, already-transmitted mu-law audio (Twilio) to the
    in-progress recording. Same never-raises contract as append_wav()."""
    if session_key is None or not mulaw_bytes:
        return
    try:
        pcm = mulaw_to_pcm16(mulaw_bytes)
        pcm = _resample_pcm16(pcm, _TWILIO_RATE_HZ, CANONICAL_RATE_HZ)
    except Exception:
        return
    _get_or_create(session_key).segments.append(pcm)


def append_pcm16(session_key: Any, pcm_bytes: bytes, rate: int = 16000) -> None:
    """Append one native_ws agent turn's REAL, already-received raw PCM16 audio
    (mono, no header — the target's own wire format; see app.core.voice_native_ws)
    to the in-progress recording.

    Unlike append_wav()/append_mulaw(), there is no format to decode here — the
    bytes already ARE PCM16 — so this only resamples to CANONICAL_RATE_HZ (reusing
    the exact same helper both of those use) before appending. The ONLY caller of
    this function is the native_ws transport, and native_ws never calls
    append_wav()/append_mulaw() for the same conversation — see
    FinalizedRecording.agent_only for why that makes marking every append_pcm16()
    conversation "agent_only" unconditionally correct, with no reconciliation
    needed against the other two functions. Same never-raises contract as the
    other append_*() functions.
    """
    if session_key is None or not pcm_bytes:
        return
    try:
        pcm = _resample_pcm16(pcm_bytes, rate, CANONICAL_RATE_HZ)
    except Exception:
        return
    rec = _get_or_create(session_key)
    rec.segments.append(pcm)
    rec.agent_only = True


def finalize_recording(session_key: Any) -> Optional[FinalizedRecording]:
    """Write whatever was recorded for this conversation to disk as one WAV file and
    clear its in-memory buffer.

    Called exactly once, unconditionally, from
    app.core.voice_caller.close_voice_session() regardless of how the scenario
    ended — so a partial recording (a scenario that failed mid-way) is still saved
    rather than lost, and the in-memory buffer never leaks across runs either way.

    Returns a FinalizedRecording(path, agent_only), or None if nothing was ever
    recorded for this conversation (a chat scenario, or a voice scenario that
    failed before its first turn's audio existed) — nothing is written in that case.
    """
    rec = _RECORDINGS.pop(session_key, None)
    if rec is None or not rec.segments:
        return None
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    path = os.path.join(RECORDINGS_DIR, f"conversation-{session_key}.wav")
    pcm = b"".join(rec.segments)
    with open(path, "wb") as f:
        f.write(_pcm16_to_wav_bytes(pcm, CANONICAL_RATE_HZ))
    return FinalizedRecording(path=path, agent_only=rec.agent_only)


def discard_recording(session_key: Any) -> None:
    """Drop any in-progress recording for this conversation without writing it.

    Not called by any production path today — finalize_recording() already covers
    both success and failure via run_scenario()'s guaranteed close_fn. Kept as the
    explicit "abandon this" counterpart for a caller that genuinely wants one.
    """
    _RECORDINGS.pop(session_key, None)
