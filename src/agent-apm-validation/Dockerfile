# Build from repository root: docker build -f src/agent-apm-validation/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY packages/journey-mcp /build/journey-mcp
COPY packages/journey-durability /build/journey-durability
COPY src/agent-apm-validation /build/agent-apm-validation
RUN pip install --no-cache-dir /build/journey-mcp /build/journey-durability /build/agent-apm-validation
ENTRYPOINT ["python", "-m", "agent_apm_validation.main"]
