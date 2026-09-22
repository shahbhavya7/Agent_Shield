"""Talk to sample_voice_bot (or any http_json voice agent) from the terminal.

Types your message -> speaks it with macOS `say` -> POSTs the resulting WAV to the
agent's /voice-chat contract (the exact same base64-WAV-in/base64-WAV-out shape
AgentShield's own http_json transport uses) -> plays the agent's real spoken reply
back with `afplay`. Keeps conversation history across turns so it's a real back-
and-forth, not one-shot calls.

No extra dependencies: `say`/`afplay` are built into macOS. Uses `httpx`, already
in this project's venv.

Usage:
    .venv/bin/python talk_to_voice_bot.py
    .venv/bin/python talk_to_voice_bot.py --url http://localhost:8008/voice-chat
"""
import argparse
import base64
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

DEFAULT_URL = "http://localhost:8008/voice-chat"


def speak_to_wav(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = Path(f.name)
    subprocess.run(
        ["say", "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@24000", text],
        check=True,
    )
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


def play_wav(data: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    subprocess.run(["afplay", str(path)], check=True)
    path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"voice-chat endpoint (default: {DEFAULT_URL})")
    args = parser.parse_args()

    history: list[dict] = []
    print(f"Talking to {args.url} — type a message, or 'quit' to stop.\n")

    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message or message.lower() in ("quit", "exit"):
            break

        audio_in = speak_to_wav(message)
        body = {
            "message": base64.b64encode(audio_in).decode("ascii"),
            "history": history,
            "faults": [],
        }
        try:
            resp = httpx.post(args.url, json=body, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [error contacting agent: {e}]")
            continue

        reply_b64 = data.get("reply", "")
        trace = data.get("trace", {}) or {}
        transcript_out = trace.get("transcript_out") or "(no transcript in trace)"
        print(f"agent> {transcript_out}")

        if reply_b64:
            play_wav(base64.b64decode(reply_b64))

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": transcript_out})


if __name__ == "__main__":
    sys.exit(main())
