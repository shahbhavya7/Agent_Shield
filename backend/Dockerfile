# AgentShield backend image.
#
# Shared by 8 compose services: backend (FastAPI), worker (Temporal worker), and all 6
# sample agents (sample_rag_bot + sample_agents/*). They all live under backend/, share
# backend/requirements.txt, and differ only in which module is run — so one image is
# built once and reused, with docker-compose.yml overriding `command:` per service.
#
# No application code here differs from running `uvicorn ...`/`python -m ...` locally;
# --host 0.0.0.0 (set in docker-compose.yml commands, not here) is the only Docker-specific
# adjustment, needed so other containers/published ports can reach the process at all.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Default: the backend API. Other services (worker, sample agents) override this in
# docker-compose.yml.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
