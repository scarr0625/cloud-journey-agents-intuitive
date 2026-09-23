# Build from repository root: docker build -f src/agent-ad-provisioning/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY packages/journey-mcp /build/journey-mcp
COPY packages/journey-durability /build/journey-durability
COPY src/agent-ad-provisioning /build/agent-ad-provisioning
RUN pip install --no-cache-dir /build/journey-mcp /build/journey-durability /build/agent-ad-provisioning
ENTRYPOINT ["python", "-m", "agent_ad_provisioning.main"]
