# Build from repository root: docker build -f src/agent-orchestrator/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY packages/journey-mcp /build/journey-mcp
COPY packages/journey-sessions /build/journey-sessions
COPY src/agent-orchestrator /build/agent-orchestrator
RUN pip install --no-cache-dir /build/journey-mcp /build/journey-sessions /build/agent-orchestrator
CMD ["uvicorn", "agent_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
