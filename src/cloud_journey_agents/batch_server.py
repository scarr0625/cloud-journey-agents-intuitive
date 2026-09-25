"""Compatibility imports for the PoC's earlier batch HTTP and CLI entry points.

The implementation lives in durability/server.py. It exposes health
routes and POST /v1/run for service deployments, plus a CLI that performs
one invocation and exits. Both paths use the same durable executor.

This file only re-exports those helpers; it does not start a web server or
bind a port. Service deployments use Uvicorn to serve the agent's app,
while job deployments run the agent's server module as a CLI.

New agent integrations import durability.server directly. Keep the main
repository's batch_server.py when copying durability into that repository.
"""

from .durability.server import (
    JobExecutor, RunRequest, create_batch_app, execute_job, run_job,
)

__all__ = ["JobExecutor", "RunRequest", "create_batch_app", "execute_job", "run_job"]
