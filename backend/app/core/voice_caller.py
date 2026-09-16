"""AI Caller — the voice-modality "turn" function for app.core.runner.run_scenario().

Bridges a text scenario turn onto a real voice-contract endpoint (Phase 2A's
sample_voice_bot, or any other agent speaking the same /voice-chat envelope):

    text (tester turn) -> TTS -> base64 WAV
        -> app.core.adapter.send() [REUSED, unchanged HTTP infra: request templating,
           auth header, timeout/error handling, system-fault simulation]
        -> base64 WAV reply -> STT -> text (agent turn)

`call_voice_agent()` returns the exact ``{"reply": str, "trace": dict}`` shape
``adapter.send()`` already returns, so ``run_scenario()`` can call this in place of
``adapter.send()`` with NO change to its turn loop, persistence, idempotency, or
adaptive-follow-up logic. Only WHICH function plays a turn differs; everything
downstream — messages, Judge, scoring, finalization — only ever sees text, exactly as
before. Chat-modality runs never import or execute this module.

Failure handling deliberately mirrors app.core.adapter.send(): any failure in this
module (TTS, the HTTP call itself, or STT) resolves to the SAME sentinel
app.core.judge.AGENT_ERROR_SENTINEL ("<error>") that a transport failure already
produces for a chat agent, so the existing judge._endpoint_never_responded() /
_record_system_failure() machinery scores an unreachable/broken voice agent exactly
like an unreachable chat agent — no new failure category for the Judge to learn.
"""
import base64
from typing import Any, Optional

from app.core.adapter import send as http_send
from app.core.llm import speech_to_text, text_to_speech

# Matches app.core.judge.AGENT_ERROR_SENTINEL exactly — kept as a literal (not an
# import) to avoid a runner/activities -> judge import for a single string constant;
# judge.py's copy is the canonical one and asserts against this same value.
AGENT_ERROR_SENTINEL = "<error>"


async def call_voice_agent(
    agent: Any,
    message: str,
    history: Optional[list] = None,
    faults: Optional[list] = None,
) -> dict:
    """One voice turn: TTS the tester's text, POST it, STT the agent's spoken reply.

    Drop-in replacement for app.core.adapter.send() when passed as run_scenario's
    send_fn — same signature, same return shape.
    """
    # --- TTS: tester's text -> base64 WAV ------------------------------------
    try:
        audio_in = await text_to_speech(message)
        audio_in_b64 = base64.b64encode(audio_in).decode("ascii")
    except Exception as e:
        return {"reply": AGENT_ERROR_SENTINEL, "trace": {"error": f"tts error: {e}"}}

    # --- HTTP: reuse the existing black-box adapter, completely unchanged ----
    # This is what makes faults, auth headers, request templating, and system-fault
    # simulation (api_unreachable/api_error/api_timeout/malformed_response) all keep
    # working for a voice agent with zero duplicated logic.
    result = await http_send(agent, audio_in_b64, history, faults)
    reply_b64 = result.get("reply", "")
    trace: dict = dict(result.get("trace") or {})

    # A system/transport fault (or any other adapter-level failure) already comes back
    # as the same sentinel judge.py recognizes — pass it straight through unchanged
    # rather than trying to STT it as audio.
    if reply_b64 == AGENT_ERROR_SENTINEL or trace.get("error"):
        return {"reply": AGENT_ERROR_SENTINEL, "trace": trace}

    # --- STT: agent's spoken reply -> text ------------------------------------
    try:
        audio_out = base64.b64decode(reply_b64) if reply_b64 else b""
        if not audio_out:
            raise ValueError("voice agent returned no audio")
        transcript_out = await speech_to_text(audio_out)
        if not transcript_out:
            raise ValueError("STT produced an empty transcript")
    except Exception as e:
        trace["error"] = f"stt error: {e}"
        return {"reply": AGENT_ERROR_SENTINEL, "trace": trace}

    return {"reply": transcript_out, "trace": trace}
