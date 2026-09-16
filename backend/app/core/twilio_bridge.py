"""Twilio voice transport — Phase 3A: ONE CALL PER TEST TURN (no persistent session).

Bridges a text scenario turn onto a REAL PHONE CALL via Twilio Programmable Voice +
Media Streams. Unlike http_json/websocket, Twilio's Media Streams protocol is a
continuous, unsegmented mulaw/8kHz audio pipe with no "end of turn" signal — the real
engineering content of this module is translating that into the same discrete
"one turn's audio in, one turn's audio out" shape every other transport already uses.

    _call_via_twilio(agent, message, history, faults)
        1. TTS the tester's text (REUSED, unchanged: app.core.llm.text_to_speech)
        2. convert WAV -> mulaw/8kHz (this module, pure Python, no audioop — see below)
        3. originate an outbound call via the Twilio REST API (twilio SDK), pointing its
           TwiML webhook at THIS process with a fresh, unguessable correlation key
        4. wait for that call's Media Stream to connect back to /twilio/media and
           deliver the tester's audio, then buffer the agent's reply until a simple
           silence/timeout heuristic says it has stopped speaking
        5. convert the buffered mulaw back to WAV, STT it (REUSED, unchanged)
        6. hang up the call (this is call-per-turn — every call is torn down after
           exactly one exchange) and return {"reply": text, "trace": dict}

Correlation (app.routers.twilio <-> this module) is a single in-memory dict, keyed by
a random per-turn `conv_key` generated here and threaded through Twilio's own request
plumbing (the webhook's query string, then the <Stream> custom parameter, then the
Media Stream's `start.customParameters`) — no new database table. Entries are created
BEFORE the outbound REST call is placed (so a webhook/stream that arrives immediately
can never race ahead of us), and always cleaned up in a `finally`.

Failure handling mirrors every other transport: any failure (bad phone number, Twilio
API error, the call never connects, a malformed Media Stream message, empty/invalid
audio, STT failure) resolves to the same AGENT_ERROR_SENTINEL ("<error>") the rest of
the system already knows how to score as a system failure — no Judge change needed.

NO audioop: it is deprecated and removed in Python 3.13. mu-law encode/decode here is
a small, self-contained implementation of the standard ITU-T G.711 algorithm.
"""
import asyncio
import base64
import io
import re
import struct
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any, Optional

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from app.config import PUBLIC_BASE_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
from app.core.llm import speech_to_text, text_to_speech

# Matches app.core.judge.AGENT_ERROR_SENTINEL / app.core.voice_caller.AGENT_ERROR_SENTINEL
# exactly. Duplicated as a literal rather than imported, same tradeoff already made
# between judge.py and voice_caller.py — importing from voice_caller.py here would be
# circular (voice_caller dispatches INTO this module).
AGENT_ERROR_SENTINEL = "<error>"

# --- Telephony / timing constants (POC-tuned, not production-calibrated) -----
SAMPLE_RATE_HZ = 8000                       # Twilio Media Streams' fixed rate
FRAME_MS = 20                                # Twilio's own frame duration
FRAME_BYTES = SAMPLE_RATE_HZ * FRAME_MS // 1000  # 160 bytes/frame (8-bit mu-law, mono)
SEND_PACING_S = FRAME_MS / 1000.0            # real-time pacing between outbound frames

# Silence/timeout heuristic for "the agent stopped talking" — NOT production-grade VAD.
# Twilio streams audio continuously regardless of loudness, so this is a simple energy
# threshold over decoded PCM16, not silence-vs-no-frames. Both constants would need
# real tuning against a real phone line before this is anything but a POC.
SILENCE_RMS_THRESHOLD = 400                  # rough |PCM16| magnitude
SILENCE_DURATION_S = 1.5                     # how long "quiet" must persist to end a turn
MAX_LISTEN_S = 20.0                          # hard safety cap regardless of silence

# Bounds the WHOLE call: ring + answer + webhook + stream-connect + speak + listen.
# Generous relative to http_json/websocket's 30s, because a real phone call's
# ring/answer alone can take several seconds before any of our own logic even starts.
CALL_TIMEOUT_S = 60.0

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # loose E.164 check


# ---------------------------------------------------------------------------
# mu-law <-> linear PCM16 — standard ITU-T G.711 algorithm, pure Python.
# Deliberately NOT using audioop: it is deprecated and removed in Python 3.13.
# ---------------------------------------------------------------------------
_MULAW_BIAS = 0x84
_MULAW_MAX = 0x1FFF


def _linear_to_mulaw_sample(sample: int) -> int:
    sign = 0x00
    if sample < 0:
        sample = -sample
        sign = 0x80
    sample = min(sample, _MULAW_MAX) + _MULAW_BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _mulaw_to_linear_sample(byte: int) -> int:
    byte = ~byte & 0xFF
    sign = byte & 0x80
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = ((mantissa << 3) + _MULAW_BIAS) << exponent
    sample -= _MULAW_BIAS
    return -sample if sign else sample


def pcm16_to_mulaw(pcm: bytes) -> bytes:
    """Little-endian 16-bit PCM mono -> 8-bit mu-law."""
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return bytes(_linear_to_mulaw_sample(s) for s in samples)


def mulaw_to_pcm16(mulaw: bytes) -> bytes:
    """8-bit mu-law -> little-endian 16-bit PCM mono."""
    samples = [_mulaw_to_linear_sample(b) for b in mulaw]
    return struct.pack(f"<{len(samples)}h", *samples) if samples else b""


def _avg_abs_amplitude(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return sum(abs(s) for s in samples) / n


# ---------------------------------------------------------------------------
# WAV <-> raw PCM16 helpers (stdlib `wave` only — no new dependency).
# ---------------------------------------------------------------------------
def _wav_bytes_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    """Returns (raw little-endian PCM16 mono samples, sample_rate)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM WAV, got sampwidth={w.getsampwidth()}")
        raw = w.readframes(w.getnframes())
        rate = w.getframerate()
        channels = w.getnchannels()
    if channels > 1:
        raw = _downmix_to_mono(raw, channels)
    return raw, rate


def _downmix_to_mono(raw: bytes, channels: int) -> bytes:
    n = len(raw) // 2
    samples = struct.unpack(f"<{n}h", raw[: n * 2])
    mono = [
        sum(samples[i : i + channels]) // channels
        for i in range(0, len(samples) - len(samples) % channels, channels)
    ]
    return struct.pack(f"<{len(mono)}h", *mono)


def _pcm16_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _resample_pcm16(pcm: bytes, in_rate: int, out_rate: int) -> bytes:
    """Linear-interpolation resample. Simple and self-contained, not hi-fi — fine for
    a voice-testing POC, not for audio-quality-sensitive use."""
    if in_rate == out_rate or not pcm:
        return pcm
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    n_out = max(1, int(n * out_rate / in_rate))
    out = []
    for i in range(n_out):
        pos = i * (n - 1) / (n_out - 1) if n_out > 1 else 0.0
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        val = samples[lo] * (1 - frac) + samples[hi] * frac
        out.append(max(-32768, min(32767, int(val))))
    return struct.pack(f"<{len(out)}h", *out)


def wav_to_mulaw_frames(wav_bytes: bytes) -> list[bytes]:
    """TTS output (whatever rate) -> a list of 160-byte (20ms) 8kHz mu-law frames."""
    pcm, rate = _wav_bytes_to_pcm16(wav_bytes)
    pcm = _resample_pcm16(pcm, rate, SAMPLE_RATE_HZ)
    mulaw = pcm16_to_mulaw(pcm)
    return [mulaw[i : i + FRAME_BYTES] for i in range(0, len(mulaw), FRAME_BYTES)]


def mulaw_buffer_to_wav(mulaw: bytes) -> bytes:
    """Accumulated 8kHz mu-law audio -> a WAV file speech_to_text() can consume
    directly. No upsampling needed — Whisper handles 8kHz telephony-quality audio
    natively."""
    pcm = mulaw_to_pcm16(mulaw)
    return _pcm16_to_wav_bytes(pcm, SAMPLE_RATE_HZ)


# ---------------------------------------------------------------------------
# Correlation store — in-memory only, single-turn-per-entry, no DB table.
# ---------------------------------------------------------------------------
@dataclass
class _Session:
    """One in-flight turn. Created (and stored) BEFORE the outbound call is placed,
    so a webhook/stream that arrives immediately can never race ahead of us."""
    outbound_frames: list[bytes]
    result: "asyncio.Future[dict]"
    call_sid: Optional[str] = None


_SESSIONS: dict[str, _Session] = {}


def _twiml_url(conv_key: str) -> str:
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/twilio/twiml?conv_key={conv_key}"


def _stream_url() -> str:
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    # wss:// regardless of whether PUBLIC_BASE_URL is https:// or http://, since a
    # tunnel (ngrok) terminates TLS in front of this plain-http process either way.
    host = base.split("://", 1)[-1]
    return f"wss://{host}/twilio/media"


def session_exists(conv_key: str) -> bool:
    """Whether a turn is currently waiting on this correlation key. Used by the
    /twilio/twiml webhook to reject a request that doesn't correspond to a live,
    in-flight turn (see app.routers.twilio's security note)."""
    return conv_key in _SESSIONS


def build_twiml(conv_key: str) -> str:
    """The TwiML returned from POST /twilio/twiml — connects the call's entire audio
    to our Media Stream, passing `conv_key` through as a custom Stream parameter."""
    response = VoiceResponse()
    connect = Connect()
    stream = Stream(url=_stream_url())
    stream.parameter(name="conv_key", value=conv_key)
    connect.append(stream)
    response.append(connect)
    return str(response)


# ---------------------------------------------------------------------------
# The AI Caller entry point for voice_protocol == "twilio"
# ---------------------------------------------------------------------------
async def _call_via_twilio(
    agent: Any,
    message: str,
    history: Optional[list] = None,
    faults: Optional[list] = None,
) -> dict:
    """One voice turn = one phone call, placed, used once, hung up.

    `agent["endpoint_url"]` is reused to hold the target's E.164 phone number for this
    protocol (there is no URL involved at all) — validated explicitly, never assumed.
    """
    to_number = (agent.get("endpoint_url") or "").strip()
    if not _PHONE_RE.match(to_number):
        looks_like_url = "://" in to_number
        hint = " (this looks like a URL, not a phone number)" if looks_like_url else ""
        return {
            "reply": AGENT_ERROR_SENTINEL,
            "trace": {
                "error": (
                    f"voice_protocol 'twilio' requires agent.endpoint_url to be an "
                    f"E.164 phone number (e.g. +15551234567){hint}, got: {to_number or '(empty)'}"
                )
            },
        }

    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER and PUBLIC_BASE_URL):
        return {
            "reply": AGENT_ERROR_SENTINEL,
            "trace": {"error": "Twilio is not configured (missing TWILIO_* / PUBLIC_BASE_URL env vars)"},
        }

    # --- TTS: tester's text -> WAV -> mu-law frames ---------------------------
    try:
        audio_in_wav = await text_to_speech(message)
        outbound_frames = wav_to_mulaw_frames(audio_in_wav)
    except Exception as e:
        return {"reply": AGENT_ERROR_SENTINEL, "trace": {"error": f"tts error: {e}"}}

    conv_key = uuid.uuid4().hex
    loop = asyncio.get_event_loop()
    session = _Session(outbound_frames=outbound_frames, result=loop.create_future())
    # Stored BEFORE the REST call is placed — see _Session's docstring.
    _SESSIONS[conv_key] = session

    trace: dict = {}
    try:
        try:
            client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            call = await asyncio.to_thread(
                client.calls.create,
                to=to_number,
                from_=TWILIO_FROM_NUMBER,
                url=_twiml_url(conv_key),
            )
            session.call_sid = call.sid
            trace["call_sid"] = call.sid
        except Exception as e:
            return {"reply": AGENT_ERROR_SENTINEL, "trace": {"error": f"twilio call origination error: {e}"}}

        try:
            outcome = await asyncio.wait_for(session.result, timeout=CALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            return {
                "reply": AGENT_ERROR_SENTINEL,
                "trace": {**trace, "error": f"twilio call timed out after {CALL_TIMEOUT_S}s"},
            }

        if outcome.get("error"):
            return {"reply": AGENT_ERROR_SENTINEL, "trace": {**trace, "error": outcome["error"]}}

        mulaw_reply = outcome.get("audio") or b""
        if not mulaw_reply:
            return {"reply": AGENT_ERROR_SENTINEL, "trace": {**trace, "error": "no audio received from voice agent"}}

        # --- STT: buffered mu-law reply -> WAV -> text ------------------------
        try:
            wav_reply = mulaw_buffer_to_wav(mulaw_reply)
            transcript_out = await speech_to_text(wav_reply)
            if not transcript_out:
                raise ValueError("STT produced an empty transcript")
        except Exception as e:
            return {"reply": AGENT_ERROR_SENTINEL, "trace": {**trace, "error": f"stt error: {e}"}}

        return {"reply": transcript_out, "trace": trace}
    finally:
        _SESSIONS.pop(conv_key, None)
        # Call-per-turn: always hang up, whatever happened.
        if session.call_sid:
            try:
                client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                await asyncio.to_thread(client.calls(session.call_sid).update, status="completed")
            except Exception:
                pass  # best-effort cleanup; the call will time out on its own otherwise


# ---------------------------------------------------------------------------
# Media Stream event handling — called from app.routers.twilio's WebSocket route.
# All Twilio-protocol logic lives here; the router is a thin FastAPI adapter.
# ---------------------------------------------------------------------------
async def handle_media_stream(websocket: Any) -> None:
    """Drive one Twilio Media Stream connection end-to-end: wait for `start`, send
    the waiting session's tester audio, buffer the reply until silence/timeout, then
    resolve that session's future and close. `websocket` is a FastAPI WebSocket,
    already accepted by the caller.
    """
    stream_sid: Optional[str] = None
    session: Optional[_Session] = None
    conv_key: Optional[str] = None

    try:
        # --- wait for "start" (which carries our correlation key) -------------
        while session is None:
            msg = await websocket.receive_json()
            event = msg.get("event")
            if event == "start":
                start = msg.get("start") or {}
                stream_sid = start.get("streamSid") or msg.get("streamSid")
                conv_key = (start.get("customParameters") or {}).get("conv_key")
                session = _SESSIONS.get(conv_key) if conv_key else None
                if session is None:
                    # Unknown/stale/duplicate stream — nothing waiting on it.
                    await websocket.close()
                    return
            elif event == "stop":
                # Call ended before we ever got a usable start — nothing to do.
                await websocket.close()
                return
            # "connected" and anything else: ignore and keep waiting for "start".

        # --- send the tester's audio, paced at ~real time ---------------------
        for frame in session.outbound_frames:
            await websocket.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": _b64(frame)},
            })
            await asyncio.sleep(SEND_PACING_S)

        # --- buffer the agent's reply until silence/timeout --------------------
        buffer = bytearray()
        silence_since: Optional[float] = None
        deadline = time.monotonic() + MAX_LISTEN_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            event = msg.get("event")
            if event == "media":
                frame = _b64decode(msg["media"]["payload"])
                buffer.extend(frame)
                energy = _avg_abs_amplitude(mulaw_to_pcm16(frame))
                if energy < SILENCE_RMS_THRESHOLD:
                    if silence_since is None:
                        silence_since = time.monotonic()
                    elif time.monotonic() - silence_since >= SILENCE_DURATION_S:
                        break
                else:
                    silence_since = None
            elif event == "stop":
                break
            # ignore "mark" and anything else

        if not session.result.done():
            session.result.set_result({"audio": bytes(buffer), "error": None})
    except Exception as e:
        if session is not None and not session.result.done():
            session.result.set_result({"audio": b"", "error": f"media stream error: {e}"})
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.b64decode(data)
