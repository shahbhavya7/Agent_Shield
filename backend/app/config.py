"""Central config: load environment once, expose as simple module-level constants."""
import os
from dotenv import load_dotenv

# Load backend/.env (next to this app package's parent dir). Safe if the file is missing.
load_dotenv()

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
SAMPLE_BOT_URL: str = os.getenv("SAMPLE_BOT_URL", "http://localhost:8001/chat")
# LLM-backed RAG agent (real, non-deterministic test target) on :8002.
SAMPLE_RAG_BOT_URL: str = os.getenv("SAMPLE_RAG_BOT_URL", "http://localhost:8002/chat")

# Path to the SQLite file (backend/agentshield.db regardless of CWD).
DB_PATH: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agentshield.db")

# Demo safety: cap scenarios per run so a live run finishes well under ~90s.
MAX_SCENARIOS: int = int(os.getenv("MAX_SCENARIOS", "10"))

# Rough $ per 1K tokens for the agent-under-test, used to estimate run cost.
# Default is a blended gpt-4o-mini rate; override per model via .env.
PRICE_PER_1K_TOKENS: float = float(os.getenv("PRICE_PER_1K_TOKENS", "0.0004"))
