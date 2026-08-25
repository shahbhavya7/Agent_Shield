"""Central config: load environment once, expose as simple module-level constants."""
import os

import yaml
from dotenv import load_dotenv

# Load backend/.env (next to this app package's parent dir). Safe if the file is missing.
load_dotenv()

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
SAMPLE_BOT_URL: str = os.getenv("SAMPLE_BOT_URL", "http://localhost:8001/chat")
# LLM-backed RAG agent (real, non-deterministic test target) on :8002.
SAMPLE_RAG_BOT_URL: str = os.getenv("SAMPLE_RAG_BOT_URL", "http://localhost:8002/chat")

# PostgreSQL connection string. Override in .env to point at another host/db.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql://localhost:5432/agentshield"
)

# Demo safety: cap scenarios per run so a live run finishes well under ~90s.
MAX_SCENARIOS: int = int(os.getenv("MAX_SCENARIOS", "10"))

# --- Temporal ----------------------------------------------------------------
# Where the Temporal server is. `temporal server start-dev` listens on 7233 by default
# and serves its Web UI on 8233. Nothing in the app requires Temporal yet — these only
# take effect for the worker and for code that explicitly asks for a client.
TEMPORAL_ADDRESS: str = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE: str = os.getenv("TEMPORAL_NAMESPACE", "default")
# The queue a worker polls and a client targets. One queue for now; splitting it later
# (e.g. cheap DB work vs. expensive LLM work) is a worker-config change, not a code one.
TEMPORAL_TASK_QUEUE: str = os.getenv("TEMPORAL_TASK_QUEUE", "agentshield")

# --- Concurrency -------------------------------------------------------------
# How many selected agents are crash-tested at the same time. Each one is an
# independent run, so this is the width of the parallel fan-out.
AGENT_CONCURRENCY: int = int(os.getenv("AGENT_CONCURRENCY", "3"))
# Ceiling on in-flight scenario/judge/fix work across the WHOLE process, shared by
# every run in flight. Without this, N parallel agents would each open their own
# window onto the LLM and the agents under test.
WORK_CONCURRENCY: int = int(os.getenv("WORK_CONCURRENCY", "6"))

# Rough $ per 1K tokens for the agent-under-test, used to estimate run cost.
# Default is a blended gpt-4o-mini rate; override per model via .env.
PRICE_PER_1K_TOKENS: float = float(os.getenv("PRICE_PER_1K_TOKENS", "0.0004"))

# --- Agent name mapping (backend/mapping.yaml) -------------------------------
# Maps the internal agent identifier used in the repo (Domain.key, or the module name
# for the standalone sample_rag_bot) to the agent's actual / display name.
MAPPING_PATH: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mapping.yaml")


def _load_agent_names() -> dict[str, str]:
    """Read mapping.yaml once at import. A missing/invalid file is non-fatal."""
    try:
        with open(MAPPING_PATH) as f:
            data = yaml.safe_load(f) or {}
        agents = data.get("agents") or {}
        return {str(k): str(v) for k, v in agents.items()}
    except Exception as e:
        print(f"[config] could not read {MAPPING_PATH} ({e}); using internal names as-is")
        return {}


AGENT_NAMES: dict[str, str] = _load_agent_names()


def agent_display_name(key: str) -> str:
    """The agent's mapped name, falling back to the internal identifier if unmapped."""
    return AGENT_NAMES.get(key, key)


# --- Customer inventory (backend/inventory.yaml) -----------------------------
# Which agents are onboarded for each customer. Agent entries are the same internal
# identifiers used as mapping.yaml keys, so display names come from AGENT_NAMES.
INVENTORY_PATH: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "inventory.yaml")


def _load_inventory() -> list[dict]:
    """Read inventory.yaml once at import. A missing/invalid file is non-fatal."""
    try:
        with open(INVENTORY_PATH) as f:
            data = yaml.safe_load(f) or {}
        customers = data.get("customers") or []
        return [
            {"name": str(c.get("name", "")), "agents": [str(a) for a in (c.get("agents") or [])]}
            for c in customers
        ]
    except Exception as e:
        print(f"[config] could not read {INVENTORY_PATH} ({e}); inventory is empty")
        return []


INVENTORY: list[dict] = _load_inventory()


# Where the sample agents from backend/sample_agents/ listen, keyed by the same internal
# identifier used in mapping.yaml / inventory.yaml. Used to seed their `agents` rows.
SAMPLE_AGENT_URLS: dict[str, str] = {
    "banking_bot": os.getenv("BANKING_BOT_URL", "http://localhost:8003/chat"),
    "hr_bot": os.getenv("HR_BOT_URL", "http://localhost:8004/chat"),
    "insurance_bot": os.getenv("INSURANCE_BOT_URL", "http://localhost:8005/chat"),
    "sample_rag_bot": SAMPLE_RAG_BOT_URL,
}
