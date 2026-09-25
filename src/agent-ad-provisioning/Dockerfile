# Build from repository root: docker build -f src/agent-ad-provisioning/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY src/pyproject.toml /build/cloud-journey-agents/pyproject.toml
COPY src/cloud_journey_agents /build/cloud-journey-agents/cloud_journey_agents
COPY src/agent-ad-provisioning /build/agent-ad-provisioning
RUN pip install --no-cache-dir "/build/cloud-journey-agents[batch]" /build/agent-ad-provisioning
ENTRYPOINT ["python", "-m", "agent_ad_provisioning.server"]
