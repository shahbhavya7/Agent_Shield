"""AgentShield SAMPLE **voice** bot — Phase 2A standalone voice-contract target.

A separate, tiny FastAPI service (port 8008) that does real audio in -> audio out:

    WAV audio (base64) -> STT (Whisper) -> LLM reply (VOICE_MODEL) -> TTS -> WAV audio (base64)

This is a DEVELOPMENT/DEMO target, not production telephony infrastructure. It is fully
independent of AgentShield — it imports nothing from the `app` package, has its own env
loading and its own OpenAI client — exactly the same isolation `sample_bot` and
`sample_rag_bot` already use. AgentShield (once wired up in a later phase) would reach it
ONLY over HTTP via /voice-chat, exactly like any third-party voice agent.

Wire contract (deliberately the SAME envelope as the existing /chat contract, so the
existing black-box HTTP adapter needs no changes to reach it later):

    POST /voice-chat
        { message: "<base64 WAV>", history: [{role, content}], faults: [] }
        -> { reply: "<base64 WAV>", trace: { transcript_in, transcript_out,
                                              latency_ms, tokens } }

`history` stays TEXT (the transcript of prior turns), not audio — the same convention
sample_rag_bot uses for conversational context. `faults` is accepted for shape-parity
with the chat contract but is a no-op in Phase 2A; nothing reads it yet.

Run standalone (from backend/, or anywhere with the deps + OPENAI_API_KEY set):
    uvicorn sample_voice_bot.main:app --port 8008 --reload

Config via env (or a .env file in the working dir):
    OPENAI_API_KEY   (required)
    VOICE_MODEL      (default "gpt-4o-mini")   - reply-generation model
    VOICE_STT_MODEL  (default "whisper-1")     - speech-to-text model
    VOICE_TTS_MODEL  (default "tts-1")         - text-to-speech model
    VOICE_TTS_VOICE  (default "alloy")         - TTS voice preset
"""
import base64
import io
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import BaseModel

# Own config — independent of AgentShield. Loads a local .env if present.
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VOICE_MODEL = os.getenv("VOICE_MODEL", os.getenv("LLM_MODEL", "gpt-4o-mini"))
VOICE_STT_MODEL = os.getenv("VOICE_STT_MODEL", "whisper-1")
VOICE_TTS_MODEL = os.getenv("VOICE_TTS_MODEL", "tts-1")
VOICE_TTS_VOICE = os.getenv("VOICE_TTS_VOICE", "alloy")

app = FastAPI(title="AgentShield Sample Voice Agent", version="0.1.0")
_client = AsyncOpenAI(api_key=OPENAI_API_KEY or "missing-key")

# Short on purpose: replies are spoken aloud, so long answers make for a poor voice demo.
# No retrieval, no fault-injection, no secret to leak — Phase 2A only proves the audio
# round-trip works; scenario sophistication is a later phase's concern.
SYSTEM_PROMPT = (
    "You are a concise, friendly voice assistant for an online store's customer support "
    "line. Keep answers short (1-2 sentences) since they will be spoken aloud. Answer "
    "helpfully about orders, returns, refunds, shipping, and payments. If you don't know "
    "something, say so plainly and offer to connect the caller with a human agent."
)

FALLBACK_TEXT = "Sorry, I'm having trouble responding right now. Please try again shortly."


class ChatTurn(BaseModel):
    role: str
    content: str


class VoiceChatIn(BaseModel):
    message: str                       # base64-encoded WAV audio (the tester's spoken turn)
    history: list[ChatTurn] = []       # TEXT transcript of prior turns
    faults: list[str] = []             # accepted for shape-parity; no-op in Phase 2A


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "sample_voice_bot", "model": VOICE_MODEL}


async def _transcribe(audio_bytes: bytes) -> str:
    """STT: WAV bytes -> text, via OpenAI Whisper."""
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "input.wav"  # the SDK needs a filename to infer the audio format
    result = await _client.audio.transcriptions.create(
        model=VOICE_STT_MODEL, file=audio_file,
    )
    return (result.text or "").strip()


async def _synthesize(text: str) -> bytes:
    """TTS: text -> WAV bytes, via OpenAI TTS."""
    resp = await _client.audio.speech.create(
        model=VOICE_TTS_MODEL, voice=VOICE_TTS_VOICE, input=text, response_format="wav",
    )
    return resp.content


@app.post("/voice-chat")
async def voice_chat(req: VoiceChatIn) -> dict:
    start = time.time()
    trace: dict = {"transcript_in": "", "transcript_out": "", "latency_ms": 0, "tokens": 0}

    # --- decode input audio ----------------------------------------------------
    try:
        audio_in = base64.b64decode(req.message)
    except Exception as e:
        trace["error"] = f"invalid base64 audio in 'message': {e}"
        trace["latency_ms"] = int((time.time() - start) * 1000)
        return {"reply": "", "trace": trace}

    # --- STT ---------------------------------------------------------------
    try:
        transcript_in = await _transcribe(audio_in)
    except Exception as e:
        # Real voice agents fail too — surface it in the trace, degrade gracefully
        # rather than 500ing, same defensive style as sample_rag_bot.
        transcript_in = ""
        trace["error"] = f"stt error: {e}"
    trace["transcript_in"] = transcript_in

    # --- LLM reply generation -----------------------------------------------
    tokens = 0
    if transcript_in:
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in req.history:
            role = "assistant" if turn.role in ("agent", "assistant") else "user"
            messages.append({"role": role, "content": turn.content})
        messages.append({"role": "user", "content": transcript_in})

        try:
            resp = await _client.chat.completions.create(
                model=VOICE_MODEL, messages=messages, temperature=0.3,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            tokens = resp.usage.total_tokens if resp.usage else len(reply_text.split())
        except Exception as e:
            reply_text = FALLBACK_TEXT
            trace["error"] = f"llm error: {e}"
    else:
        # STT produced nothing (silence, or STT failed) — still respond so the
        # caller always gets a turn back, matching the graceful-degradation
        # convention the existing sample agents use.
        reply_text = "Sorry, I didn't catch that — could you say that again?"

    trace["transcript_out"] = reply_text
    trace["tokens"] = tokens

    # --- TTS -----------------------------------------------------------------
    try:
        audio_out = await _synthesize(reply_text)
        reply_b64 = base64.b64encode(audio_out).decode("ascii")
    except Exception as e:
        reply_b64 = ""
        trace["error"] = f"tts error: {e}"

    trace["latency_ms"] = int((time.time() - start) * 1000)
    return {"reply": reply_b64, "trace": trace}
