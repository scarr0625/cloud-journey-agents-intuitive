# Build from repository root: docker build -f src/agent-app-factory/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY packages/journey-mcp /build/journey-mcp
COPY packages/journey-durability /build/journey-durability
COPY src/agent-app-factory /build/agent-app-factory
RUN pip install --no-cache-dir /build/journey-mcp /build/journey-durability /build/agent-app-factory
ENTRYPOINT ["python", "-m", "agent_app_factory.main"]
