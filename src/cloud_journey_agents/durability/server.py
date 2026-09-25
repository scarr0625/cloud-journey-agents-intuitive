"""Expose one durable invocation as either a CLI job or an HTTP service.

A job invocation runs once, emits a JSON result, and exits. A service
deployment instead uses Uvicorn to serve the FastAPI app on its configured
port; constructing the app here does not itself open a listening socket.
Health routes are available before any batch operation is requested.

POST /v1/run and the CLI both call the agent's durable executor, keeping
recovery behavior consistent across deployment modes. A busy operation
becomes HTTP 409. The CLI exits with code 2 for a negative business result;
exit 0 can still mean WAITING and require a later invocation.

Cloud Run IAM protects the service endpoint. This optional adapter is part
of the durability copy set and does not import the main repo's batch server.
"""

import argparse
from dataclasses import asdict
from functools import partial
import json
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .contracts import BatchWorkflow, JobResult
from .runtime import execute_job
from ..config import load_config
from .checkpoints import OperationBusy
from ..logs import configure_logging
from ..mcp import McpError


class JobExecutor(Protocol):
    """An agent's durability module binds execution to its own workflow."""

    def __call__(
        self, journey_id: str, workflow_run_id: str, *, mode: str = "resume"
    ) -> JobResult: ...


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    journey_id: str = Field(min_length=1, max_length=32, pattern=r"\S")
    workflow_run_id: str = Field(min_length=1, max_length=256, pattern=r"\S")
    mode: str = "resume"


def run_job(
    workflow: BatchWorkflow,
    argv: list[str] | None = None,
    *,
    executor: JobExecutor | None = None,
) -> None:
    load_config()
    configure_logging()
    parser = argparse.ArgumentParser(
        description=f"Run one {workflow.agent.value} invocation"
    )
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    if len(workflow.modes) > 1:
        parser.add_argument("--mode", choices=workflow.modes, default="resume")
    args = parser.parse_args(argv)
    execute = executor if executor is not None else partial(execute_job, workflow)
    result = execute(
        args.journey_id,
        args.workflow_run_id,
        mode=getattr(args, "mode", "resume"),
    )
    print(json.dumps(asdict(result)))
    if not result.successful:
        raise SystemExit(2)


def create_batch_app(
    workflow: BatchWorkflow, *, executor: JobExecutor | None = None
) -> FastAPI:
    """Cloud Run IAM protects /v1/run; the agent identity is fixed by its image."""
    load_config()
    configure_logging()
    execute = executor if executor is not None else partial(execute_job, workflow)
    app = FastAPI(title=workflow.agent.value)

    @app.get("/health")
    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/v1/run", response_model=JobResult)
    def run(request: RunRequest):
        if request.mode not in workflow.modes:
            raise HTTPException(
                status_code=422, detail="Unsupported mode for this agent"
            )
        try:
            return execute(
                request.journey_id, request.workflow_run_id, mode=request.mode
            )
        except OperationBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except McpError as exc:
            raise HTTPException(status_code=503, detail="MCP business or persistence service is unavailable") from exc

    return app
