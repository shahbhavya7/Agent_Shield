"""Smoke test the OpenAI wrapper end-to-end.

Run from the backend/ dir (venv active, .env with a real key):
    python scripts/smoke_llm.py
Expected: a parsed Python dict prints, proving the key + json_mode work.
"""
import asyncio
import os
import sys

# Allow "from app...." when run as a script from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.llm import chat  # noqa: E402


async def main() -> None:
    result = await chat(
        system="You are a test harness. Reply in json.",
        messages=[
            {
                "role": "user",
                "content": 'Return a json object with keys "ok" (true) and '
                '"message" (a short hello string).',
            }
        ],
        json_mode=True,
    )
    print("Parsed dict from OpenAI:", result)
    assert isinstance(result, dict), "Expected a dict back in json_mode"
    print("SMOKE TEST PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
