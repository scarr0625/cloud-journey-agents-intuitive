# Required client MCP business contract

These tool names are the expected integration surface implemented by the adapter;
they are not assertions about the client's currently installed server. Map them
to the client implementation before deploying. All business writes go through
Data API; this MCP contract does not expose checkpoint or session persistence.

| Tool | Arguments | Caller |
| --- | --- | --- |
| get_journey_status | journey_id | Assistant |
| get_journey_status_by_apm_id | apm_id | Assistant |
| get_journey_operation | journey_id, operation_key | Batch agents |
| validate_apm | journey_id, idempotency_key | APM Validation |
| submit_ad_provisioning | journey_id, idempotency_key | AD Provisioning |
| poll_ad_provisioning | journey_id, request_id | AD Provisioning |
| validate_app_factory | journey_id, idempotency_key | App Factory |

Status tools return authorized Journey business information. Operation tools
return a JSON object containing journey_id, operation_key, outcome,
result_reference, and external_reference (nullable outside AD). They may also
return journey_state, journey_version, result, and updated_at. `get_journey_operation`
returns JSON null when an authorized Journey has no recorded operation yet.
The SDK may receive this as a JSON text block; non-null objects may use structuredContent.

| Operation key | Recognized outcomes |
| --- | --- |
| apm-validation | VALID, INVALID |
| ad-provisioning | PENDING, PROVISIONED, REJECTED, FAILED, CANCELLED |
| app-factory-validation | READY_TO_PROVISION, VALIDATION_ERROR |

The adapter maps pending AD work to AD_POLLING / WAITING. Terminal business
outcomes complete the operation; negative outcomes additionally produce
successful=false and job exit code 2. Infrastructure/tool failures produce
FAILED checkpoints and propagate an exception. Poll responses must retain the
same MyAccess request ID, including terminal responses.

The Data API must commit business transitions, results, dependencies, and audit
records before a tool reports a recorded outcome. Repeating a submission's
idempotency key must return/reconcile the same provider request. The default
logical keys are journey_id:operation_key; revised business requests need a
versioned operation identity agreed with the client before adding revalidation.

MCP must independently enforce workload permissions, verified user authorization,
and Journey/request ownership. Agent allowlists provide an additional boundary.
Tool annotations or prompt instructions alone are not authorization. Tokens are
request-scoped, never stored in checkpoints or conversation state.

The client transport uses the MCP Python SDK v1 Streamable HTTP implementation.
It supports structuredContent or a single JSON text result. For a different
transport, token exchange, tool naming, or response envelope, adapt
`src/cloud_journey_agents/mcp.py` and the `McpBusinessGateway` in
`src/cloud_journey_agents/durability/mcp_gateway.py` centrally. `guardrails.py` owns the tool
allowlists and read argument checks. The transport verifies the requested tool
is advertised by MCP before calling it; missing tools fail closed.
