"""Scoring — turn judged conversations into a reliability score + breakdown + performance."""
import json

from app.config import PRICE_PER_1K_TOKENS
from app.db import get_conversations_for_run, get_messages, get_scenario, update_run

SEVERITY_WEIGHT = {"high": 3, "med": 2, "low": 1}


def _performance(run_id: int, convs: list[dict]) -> dict:
    """Aggregate latency + token/cost across every agent turn's trace."""
    latencies: list[int] = []
    total_tokens = 0
    agent_turns = 0
    for c in convs:
        for m in get_messages(c["id"]):
            m = dict(m)
            if m["role"] != "agent" or not m.get("trace_json"):
                continue
            try:
                tr = json.loads(m["trace_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            agent_turns += 1
            if isinstance(tr.get("latency_ms"), (int, float)):
                latencies.append(int(tr["latency_ms"]))
            if isinstance(tr.get("tokens"), (int, float)):
                total_tokens += int(tr["tokens"])
    avg = round(sum(latencies) / len(latencies)) if latencies else 0
    return {
        "agent_turns": agent_turns,
        "avg_latency_ms": avg,
        "max_latency_ms": max(latencies) if latencies else 0,
        "total_tokens": total_tokens,
        "est_cost_usd": round(total_tokens / 1000.0 * PRICE_PER_1K_TOKENS, 4),
        "price_per_1k": PRICE_PER_1K_TOKENS,
    }


def compute(run_id: int) -> dict:
    """Compute the run's reliability score + breakdown and persist to the run row.

    reliability_score = 100 * (1 - weighted_failures / (total_scenarios * 3)), clamped 0-100.
    Weights: high=3, med=2, low=1.
    """
    convs = [dict(c) for c in get_conversations_for_run(run_id)]
    total = len(convs) or 1

    weighted_failures = 0
    by_type: dict[str, dict[str, int]] = {}
    by_category: dict[str, int] = {"safety": 0, "hallucination": 0, "recovery": 0, "accuracy": 0, "system": 0}

    for c in convs:
        verdict = c.get("verdict") or "pass"
        severity = c.get("severity") or "low"
        scenario = get_scenario(c["scenario_id"])
        test_type = (dict(scenario).get("test_type") if scenario else "support") or "support"

        bucket = by_type.setdefault(test_type, {"pass": 0, "fail": 0})
        if verdict == "fail":
            bucket["fail"] += 1
            weighted_failures += SEVERITY_WEIGHT.get(severity, 1)
            # attribute to a failure category (from scores_json.fail_category)
            fail_cat = None
            if c.get("scores_json"):
                try:
                    fail_cat = json.loads(c["scores_json"]).get("fail_category")
                except (json.JSONDecodeError, TypeError):
                    fail_cat = None
            if fail_cat in by_category:
                by_category[fail_cat] += 1
        else:
            bucket["pass"] += 1

    score = 100.0 * (1.0 - weighted_failures / (total * 3))
    score = max(0.0, min(100.0, round(score, 1)))

    passes = sum(1 for c in convs if (c.get("verdict") or "pass") == "pass")
    fails = total - passes if convs else 0

    breakdown = {
        "total_scenarios": len(convs),
        "passed": passes,
        "failed": fails,
        "by_test_type": by_type,
        "by_failure_category": by_category,
        "weighted_failures": weighted_failures,
        "performance": _performance(run_id, convs),
    }

    update_run(run_id, reliability_score=score, breakdown_json=json.dumps(breakdown))
    return {"reliability_score": score, "breakdown": breakdown}
