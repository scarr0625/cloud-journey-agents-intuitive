# Required Schwab MCP contract

For the Schwab-facing implementation request, existing-tool reuse assessment, and
per-tool details, see [SCHWAB_MCP_TOOL_REQUEST.md](SCHWAB_MCP_TOOL_REQUEST.md).

All database access goes through Schwab MCP. Agents have no database credentials,
drivers configured for persistence, or database permissions. Business services
behind MCP own Journey writes; Schwab's MCP persistence handlers own durable and
session transactions. These tool names are the expected client integration
surface, not claims about Schwab's installed catalog. Agree aliases/mappings before
deploying. See the [server implementation reference](references/schwab-mcp/README.md)
for executable handlers, input schemas, SQL files, full envelopes, and error codes.

## Business tools

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

## Durable state tools

These are internal batch runtime calls, separately allowlisted from business tools.
Every mutation includes `mutation_id`. Ownership fields on save/finish are
`journey_id`, `agent_name`, `workflow_run_id`, `checkpoint_id`, `execution_id`, and
`expected_version`.

| Tool | Required arguments besides mutation ID | Optional nullable fields | Result |
| --- | --- | --- | --- |
| `claim_durable_operation` | `journey_id`, `agent_name`, `workflow_run_id` | None | `{"checkpoint": <snapshot>}` with execution, positive version, and lease |
| `save_durable_checkpoint` | Ownership fields, `current_stage`, `checkpoint_status` | `external_reference`, `result_reference`, `last_error` | Full snapshot at expected version + 1 |
| `finish_durable_operation` | Ownership fields | `error` | Full snapshot at expected version + 1, lease cleared |

Schwab verifies workload-to-agent binding and Journey authorization, atomically
claims an operation lease, and rejects stale execution/version/lease writes.
Mutation receipts commit with state/audit writes, so retrying a lost reply cannot
create another execution or checkpoint event. Agent code never sends SQL.

## Session state tools

These are internal ADK runtime calls from both chat agents, never model tools.
All calls require `app_name` and `user_id`, verified against workload and delegated
user identity. Session-specific tools also require `session_id`.

| Tool | Additional arguments | Result |
| --- | --- | --- |
| `create_agent_session` | `mutation_id`, optional `state` | `{"session": <ADK session>, "version": 1}` |
| `get_agent_session` | Optional `config` event filters | Session envelope or JSON null |
| `list_agent_sessions` | None; no session ID | `{"sessions": [<summary envelope>, ...]}` |
| `append_agent_session_event` | `mutation_id`, `expected_version`, full ADK `event` | Updated full session envelope at version + 1 |
| `delete_agent_session` | `mutation_id` | Scope fields plus `deleted: true` |

Session events, state deltas, revisions, and receipts commit atomically. ADK event
JSON uses snake_case (`by_alias=False`), pinned to ADK 2.9.2. Temporary state is
not persisted; identity claims must match verified user claims. Session IDs are
scoped by app/user; deletion prevents an old create replay from resurrecting them.

## Error and recovery boundary

Errors use MCP `isError` and `{"ok":false,"error":{"code":"...","message":"..."}}`.
Persistence clients retry recognized transport failures once with the same
mutation ID/payload. Conflicts and malformed acknowledgments fail closed.
An uncertain checkpoint save stops the invocation without writing FAILED from
a stale snapshot; a later claim reconciles committed business progress. Business
mutation idempotency remains the responsibility of Schwab's business tools.

There is no cross-database transaction, generic SQL tool, or automatic local DB
fallback. For complete atomicity, identity, retention, and deployment requirements,
use the [Schwab reference](references/schwab-mcp/README.md) and
[migration guide](migrations/README.md).
