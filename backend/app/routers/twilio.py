"""/twilio inbound surface — the first inbound endpoints AgentShield exposes.

Everything Twilio-protocol-specific (TwiML generation, Media Stream event handling,
correlation, audio conversion) lives in app.core.twilio_bridge; this router is
deliberately thin — FastAPI wiring only.

    POST /twilio/twiml   Twilio's webhook, fetched when the outbound call we placed
                         is answered. Returns TwiML connecting the call's audio to
                         our Media Stream.
    WS   /twilio/media   Twilio's Media Stream connection for that same call.

Security note (Phase 3A / POC): full Twilio request-signature validation
(X-Twilio-Signature) needs the exact POST form body, which needs `python-multipart` —
a second new dependency beyond the one this phase authorized (`twilio`). Rather than
add it, this endpoint relies on `conv_key` itself being an unguessable, single-use,
per-call token (a fresh uuid4, known only to us and to Twilio via the private REST
call we made) as its access control: a request without a valid, live `conv_key` is
rejected outright. This is weaker than real HMAC signature verification and is NOT
sufficient for a production deployment — documented here as a known Phase 3A
limitation, not silently glossed over.
"""
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response

from app.core.twilio_bridge import build_twiml, handle_media_stream, session_exists

router = APIRouter(prefix="/twilio", tags=["twilio"])


@router.post("/twiml")
async def twiml(request: Request) -> Response:
    conv_key = request.query_params.get("conv_key")
    if not conv_key or not session_exists(conv_key):
        # No live session waiting on this key — reject rather than connect a stream
        # nothing will ever consume. See the security note above.
        return Response(status_code=403, content="unknown or expired conv_key")
    return Response(content=build_twiml(conv_key), media_type="application/xml")


@router.websocket("/media")
async def media(websocket: WebSocket) -> None:
    await websocket.accept()
    await handle_media_stream(websocket)
