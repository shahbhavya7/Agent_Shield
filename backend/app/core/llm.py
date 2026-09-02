"""Async OpenAI wrapper, isolated so the LLM provider can be swapped later.

Everything that talks to the model goes through chat(). Callers never import
`openai` directly.
"""
import json
from typing import Any, Optional

from openai import AsyncOpenAI

from app.config import LLM_MODEL, OPENAI_API_KEY

# Single shared async client. If the key is empty this still constructs; the
# error only surfaces on an actual call, which is what we want for smoke tests.
_client = AsyncOpenAI(api_key=OPENAI_API_KEY or "missing-key")

# Fixed seed for callers that need a repeatable sample (the Judge). OpenAI treats `seed`
# as best-effort: combined with temperature=0 it makes repeated calls on identical input
# reproduce, but it is not a hard guarantee.
FIXED_SEED = 42


def _strip_fences(text: str) -> str:
    """Defensively remove ```json ... ``` fences some models emit despite json_mode."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


async def chat(
    system: str,
    messages: list[dict[str, str]],
    json_mode: bool = False,
    temperature: float = 0.2,
    model: Optional[str] = None,
    seed: Optional[int] = None,
) -> Any:
    """Call the chat model.

    Returns a parsed dict when json_mode=True, otherwise the raw string.
    On a JSON parse failure it retries once before raising.
    """
    model = model or LLM_MODEL
    full_messages = [{"role": "system", "content": system}, *messages]

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "temperature": temperature,
    }
    if seed is not None:
        kwargs["seed"] = seed
    if json_mode:
        # OpenAI requires the word "json" somewhere in the prompt for this mode.
        if "json" not in system.lower():
            full_messages[0]["content"] = system + "\n\nRespond ONLY with valid json."
        kwargs["response_format"] = {"type": "json_object"}

    async def _once() -> str:
        resp = await _client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    raw = await _once()

    if not json_mode:
        return raw

    for attempt in range(2):
        try:
            return json.loads(_strip_fences(raw))
        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                raw = await _once()  # one retry
                continue
            raise
