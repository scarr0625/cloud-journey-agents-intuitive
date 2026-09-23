# Workflow ownership

Production Google Workflows starts the three independently deployed Cloud Run
Jobs and waits for their executions. Jobs do not invoke one another.

1. APM validation; advance only on business success.
2. AD submission, using the AD job with --mode submit.
3. AD polling, using the same AD job with --mode poll. Schedule another invocation
   while MyAccess remains pending, retaining the existing request ID.
4. App Factory validation. Readiness or a validation-error result ends this flow;
   downstream provisioning is outside the checkpoint scope.

All jobs receive journey_id and workflow_run_id. A successful Cloud Run execution
can leave an operation WAITING. Cloud Run does not return stdout JSON as the job
execution API response: the production workflow must inspect the client's
business-status API to decide whether to poll again or advance. Integrate that
read with the client's existing Workflows/Data API authentication contract.
This repository does not invent or deploy that client-specific API endpoint.

Exit 2 indicates a terminal negative business outcome. Other failures require a
retry/reconciliation policy; after a killed job, respect the checkpoint lease.

local_workflow.py is a local simulator, not a Google Workflows deployment. It
launches the installed agent entry points in separate processes and reads their
stdout JSON. Set JOURNEY_WORKFLOW_BACKEND=local to use the preserved business
simulator instead of MCP. Its polling is bounded by --max-polls; rerunning it
resumes saved work.
