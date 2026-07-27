"""Print a generated scenario bank so you can eyeball the mix + shape.

Run from backend/ (venv active, real OPENAI_API_KEY in .env):
    python scripts/gen_scenarios.py
To test the fallback: temporarily break the key in .env and re-run — you should still
get a full bank (no crash).
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.scenarios import generate_scenarios  # noqa: E402

DESCRIPTION = (
    "A RAG-based customer-support agent for an online store. Answers questions about "
    "returns, refunds, shipping, warranty, support hours, orders, cancellations, payments."
)


async def main() -> None:
    scenarios = await generate_scenarios(DESCRIPTION, selected_types=None)
    print(f"\nGenerated {len(scenarios)} scenarios\n" + "=" * 60)
    for i, s in enumerate(scenarios, 1):
        print(f"\n[{i}] {s['title']}")
        print(f"    type={s['test_type']}  fault={s['assigned_fault']}")
        print(f"    goal: {s['user_goal']}")
        print(f"    expected: {s['expected_behavior']}")
        print(f"    seed_turns ({len(s['seed_turns'])}):")
        for t in s["seed_turns"]:
            print(f"      - {t}")

    print("\n" + "=" * 60)
    print("test_type mix:", dict(Counter(s["test_type"] for s in scenarios)))
    print("fault mix:    ", dict(Counter(s["assigned_fault"] for s in scenarios)))
    inj = sum(1 for s in scenarios if s["test_type"] == "injection")
    mem = sum(1 for s in scenarios if s["test_type"] == "memory")
    print(f"\nSanity: injection={inj} (want >=2), memory={mem} (want >=1)")


if __name__ == "__main__":
    asyncio.run(main())
