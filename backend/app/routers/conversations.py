"""/conversations API — replay a single scenario live to verify a fix, and serve a
completed voice conversation's call recording (app.core.recording).
"""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.orchestrator import replay_conversation
from app.db import build_conversation_payload, get_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/{conversation_id}/replay")
async def replay(conversation_id: int) -> dict:
    """Re-run this conversation's scenario as a NEW conversation, judge + fix, return it."""
    if get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail=f"conversation {conversation_id} not found")
    new_id = await replay_conversation(conversation_id)
    if new_id is None:
        raise HTTPException(status_code=500, detail="replay failed")
    return build_conversation_payload(new_id)


@router.get("/{conversation_id}/recording")
def get_recording(conversation_id: int) -> FileResponse:
    """Serve this conversation's WAV recording, if one was produced.

    Only a voice conversation whose transport actually transmits real audio
    (http_json, websocket, twilio) has one — a chat conversation, or a native_ws
    voice conversation (that protocol is text-only; see app.core.recording's
    docstring), never produces a recording, and this 404s for those rather than
    serving nothing silently or fabricating one.
    """
    conv = get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"conversation {conversation_id} not found")
    path = conv.get("recording_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no recording available for this conversation")
    return FileResponse(path, media_type="audio/wav", filename=os.path.basename(path))
