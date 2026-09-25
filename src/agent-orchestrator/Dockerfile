# Build from repository root: docker build -f src/agent-orchestrator/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY src/pyproject.toml /build/cloud-journey-agents/pyproject.toml
COPY src/cloud_journey_agents /build/cloud-journey-agents/cloud_journey_agents
COPY src/agent-orchestrator /build/agent-orchestrator
RUN pip install --no-cache-dir "/build/cloud-journey-agents[chat]" /build/agent-orchestrator
CMD ["uvicorn", "agent_orchestrator.server:app", "--host", "0.0.0.0", "--port", "8080"]
