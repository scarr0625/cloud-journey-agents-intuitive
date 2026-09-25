# Schwab MCP tool implementation request

Please provide five business batch capabilities, three durable-state tools, and
five session-state tools, and confirm how existing read tools cover the Assistant's
two status lookups. **All database access must occur on the Schwab side through
MCP. Agent service accounts have no direct database permissions.** Existing
services may be reused behind these capabilities.

The [Schwab implementation reference](references/schwab-mcp/README.md) contains
executable persistence handlers, generated input schemas, SQL migration links,
request/response examples, authorization rules, and transaction/retry requirements.
It is server-side reference code for your existing authenticated MCP host.

This request is based on the agent code in this repository and the five tool
names supplied by the Schwab team. Their live descriptions, input/output schemas,
and authorization behavior have not been inspected. Proposed names below are
integration names for agreement, not claims that those tools already exist.

## 1. Existing tools and reuse opportunities

The supplied list is `GetCurrentUserJourneys`, `GetlourneysByApmld`,
`GetJourneyStages`, `GetApplicationEnvironments`, and `GetUserRoles`.
`GetJourneysByApmId` is used below as the assumed spelling of
`GetlourneysByApmld`; please confirm the exact advertised name and casing.

| Existing tool | Potential reuse, subject to schema confirmation | What to confirm |
| --- | --- | --- |
| `GetCurrentUserJourneys` | Authorized Journey discovery or summary data | How current-user identity is verified, returned fields, and pagination. The current Assistant does not directly call a Journey-list tool. |
| `GetJourneysByApmId` | Assistant lookup by APM ID | Argument name/format, per-Journey authorization, response shape, and whether one APM can return multiple Journeys. |
| `GetJourneyStages` | Assistant status by Journey ID, if it returns that Journey's actual progress | Whether it accepts a Journey ID and returns instance state/results, or only lists stage definitions. Stage definitions alone do not provide current Journey status. |
| `GetApplicationEnvironments` | Input to App Factory readiness checks or an existing business validator | Environment identifiers and schema; this lookup alone does not perform or persist readiness validation. |
| `GetUserRoles` | Supporting role information for existing authorization logic | How roles are bound to verified identity and applied to the requested Journey. Returning role names alone is not an authorization decision. |

Please return the actual tool catalog with descriptions, input schemas, available
output schemas, and representative successful/error responses. A name match alone
does not establish that a capability meets the contracts below.

## 2. Required batch tool list

All listed arguments are required strings. `operation_key` accepts only the
three values in section 3. Tools resolve the Journey's APM, application, inventory,
environment, and ownership data through Schwab's authoritative business services.
The current agents do not submit those records as tool arguments.

| Proposed Schwab tool name | Current agent-side name | Required arguments | Caller | Behavior and side effects |
| --- | --- | --- | --- | --- |
| `GetJourneyOperation` | `get_journey_operation` | `journey_id`, `operation_key` | All three batch agents, limited to their permitted operation | Read an already persisted business outcome so an invocation can recover after restart. No business write. |
| `ValidateApm` | `validate_apm` | `journey_id`, `idempotency_key` | `agent-apm-validation` | Run authoritative APM checks and persist the validation result, permitted Journey transition, and audit. Business write even though its name says validate. |
| `SubmitAdProvisioning` | `submit_ad_provisioning` | `journey_id`, `idempotency_key` | `agent-ad-provisioning` | Submit or reconcile one MyAccess/AD request and persist its external request ID and business outcome. External action and business write. |
| `PollAdProvisioning` | `poll_ad_provisioning` | `journey_id`, `request_id` | `agent-ad-provisioning` | Observe an existing request once and persist the observation and any permitted business transition. Business write; no new provisioning request. |
| `ValidateAppFactory` | `validate_app_factory` | `journey_id`, `idempotency_key` | `agent-app-factory` | Validate readiness and schema, then persist readiness or validation failure. Business write; downstream provisioning is outside this request. |

The proposed PascalCase names match the style of the supplied catalog. The
current client calls the snake_case names literally. Before integration, agree
either server aliases or an agent-side mapping of names, argument names, and
result envelopes. No such mapping has been applied by this document.

### GetJourneyOperation

Purpose: read the persisted outcome of one Journey operation before executing or
resuming it. This is essential when an earlier worker completed business work but
stopped before saving its durable checkpoint.

Example arguments:

```json
{"journey_id":"J-123","operation_key":"ad-provisioning"}
```

Return the operation result object in section 3 if a record exists. Return JSON
`null` only when the Journey is authorized and no outcome for that operation has
been recorded yet. A missing/unauthorized Journey, invalid operation key, or failed
read must be an error, not `null`. A read failure must not look like permission to
repeat a business action.

The read must expose committed results promptly enough for recovery and retain
terminal negative outcomes as well as successful ones. It must not submit an
external request, infer completion solely from an agent checkpoint, or update the
business state as a side effect of reading.

### ValidateApm

Purpose: validate the Journey's APM identity and associated business requirements
using Schwab's authoritative sources and rules.

Example arguments:

```json
{"journey_id":"J-123","idempotency_key":"J-123:apm-validation"}
```

The server loads the Journey and its APM data, enforces authorization and allowed
prerequisites, performs the existing APM validation, and persists the result before
returning it. The exact validation rules remain Schwab-owned; the local simulator
is not a specification for Schwab's production checks.

Return `operation_key: "apm-validation"`, outcome `VALID` or `INVALID`, and a
nonempty `result_reference`. Include actionable validation findings in the
recommended `result` field. Repeating the same logical request must return the
recorded result without duplicating business transitions or audit events.

### SubmitAdProvisioning

Purpose: initiate one AD provisioning request through the approved MyAccess
integration and establish a persistent reference for later polling.

Example arguments:

```json
{"journey_id":"J-123","idempotency_key":"J-123:ad-provisioning"}
```

The server verifies prerequisites, resolves the provisioning input from Journey
business data, and submits or reconciles the request. It saves the provider's
request ID and the business operation result before acknowledging that result.

Return `operation_key: "ad-provisioning"`, normally outcome `PENDING`, and a
nonempty `external_reference` containing the MyAccess request ID. A replay may
return the same request's current terminal outcome instead. Every returned AD
outcome must retain that request ID, including failure, rejection, or cancellation.
If submission fails before any provider request ID exists, return a tool error;
the current adapter cannot accept a recorded AD outcome without that reference.

Idempotency must cover both sequential retries and concurrent duplicate calls.
If a provider accepted a request but the response or local commit was lost, the
server must reconcile that request before attempting another submission. A local
database transaction alone does not make an external submission idempotent.

### PollAdProvisioning

Purpose: observe and record the status of the specific request submitted earlier.

Example arguments:

```json
{"journey_id":"J-123","request_id":"MA-123"}
```

Verify that the request belongs to the supplied Journey and authorized workload.
Perform one bounded observation, persist its result and any allowed Journey
transition, and return. Pending approval is handled through later invocations;
this tool must not wait through the full human-approval lifecycle.

Return `operation_key: "ad-provisioning"` and one of `PENDING`, `PROVISIONED`,
`REJECTED`, `FAILED`, or `CANCELLED`. `external_reference` must exactly equal the
input `request_id`, including terminal responses. Repeated polls must not create
new requests or repeat an already applied business transition. A terminal result
can be returned from the persisted business record.

### ValidateAppFactory

Purpose: validate that the Journey is ready for App Factory and that its required
inputs conform to the applicable schema.

Example arguments:

```json
{"journey_id":"J-123","idempotency_key":"J-123:app-factory-validation"}
```

The server verifies business prerequisites, including the required AD outcome,
and loads the authoritative inventory, environment, and schema information.
Existing environment tools/services may supply data for these checks. Schwab
defines the actual readiness rules and schema version.

Persist and return `operation_key: "app-factory-validation"`, outcome
`READY_TO_PROVISION` or `VALIDATION_ERROR`, and a nonempty `result_reference`.
Include field-level findings and the evaluated schema/version in `result` when
available. Repeating the same logical request returns its recorded result. This
capability stops at readiness; it does not launch the downstream provisioning flow.

## 3. Business operation response contract

The five batch tools use this common result format. The only successful absence
response is `null` from `GetJourneyOperation` as described above.

| Field | Type | Requirement |
| --- | --- | --- |
| `journey_id` | string | Required; exactly matches the requested Journey. |
| `operation_key` | string | Required; one of the three keys below, matching the invoked operation. |
| `outcome` | string | Required; an allowed business outcome for that operation. |
| `result_reference` | string | Required and nonempty; identifies an already persisted business result, including pending or negative results. It need not be a URL. |
| `external_reference` | string or null | Required and nonempty for every AD result; null or omitted for APM/App Factory. Must remain the original AD request ID. |
| `journey_state` | string | Recommended; Schwab's authoritative Journey state after the operation. |
| `journey_version` | integer or agreed version type | Recommended; business record version for diagnostics and concurrency. Not currently interpreted by the checkpoint adapter. |
| `updated_at` | timestamp string | Recommended; when the business result was recorded, with an explicit timezone. |
| `result` | object | Recommended; relevant findings, validation errors, readiness details, or provider observations. Exclude credentials. |

Example persisted AD result:

```json
{
  "journey_id": "J-123",
  "operation_key": "ad-provisioning",
  "outcome": "PENDING",
  "result_reference": "journey-result:J-123:ad-provisioning",
  "external_reference": "MA-123"
}
```

The adapter interprets outcomes as follows. Schwab returns the **business
outcome**; the agent derives its checkpoint fields from that value.

| `operation_key` | `outcome` | Derived checkpoint stage | Derived checkpoint status | Derived `successful` |
| --- | --- | --- | --- | --- |
| `apm-validation` | `VALID` | `APM_VALIDATION` | `COMPLETED` | true |
| `apm-validation` | `INVALID` | `APM_VALIDATION` | `COMPLETED` | false |
| `ad-provisioning` | `PENDING` | `AD_POLLING` | `WAITING` | true; orchestration must still wait |
| `ad-provisioning` | `PROVISIONED` | `AD_POLLING` | `COMPLETED` | true |
| `ad-provisioning` | `REJECTED`, `FAILED`, `CANCELLED` | `AD_POLLING` | `COMPLETED` | false |
| `app-factory-validation` | `READY_TO_PROVISION` | `APP_FACTORY_VALIDATION` | `COMPLETED` | true |
| `app-factory-validation` | `VALIDATION_ERROR` | `APP_FACTORY_VALIDATION` | `COMPLETED` | false |

These are the current adapter's accepted values. Map Schwab's existing business
statuses into this contract rather than renaming its internal state machine.
New outcomes or asynchronous APM/App Factory validation would require an agreed
adapter/workflow change; those operations currently expect a final result.

## 4. Assistant status reads: confirm reuse before adding tools

Two logical read capabilities are needed. They can be backed by the existing tools
through an adapter if those tools expose the necessary authorized business data.

| Logical capability currently called by the Assistant | Required input | Reuse candidate | Requested response |
| --- | --- | --- | --- |
| `get_journey_status` | `journey_id` | `GetJourneyStages` and any existing Journey detail capability | Authorized business status for that Journey, including recorded operation outcomes and relevant dependency status. If the existing tools cannot supply it, add a thin `GetJourneyStatus` endpoint. |
| `get_journey_status_by_apm_id` | `apm_id` | `GetJourneysByApmId` | Authorized Journey status resolved from the APM ID, with the same useful progress details. Reuse this existing tool wherever its schema permits. |

For these reads, a useful normalized object contains `journey_id`, `apm_id`,
current business state, stage progress, operation outcomes, relevant external
dependency status, and an observation/update time. These are requested reporting
fields; the current Assistant enforces only that its tool result is an object.
Field mappings should preserve Schwab's existing response data and meanings.

If the existing APM lookup returns multiple Journeys, agree the selection behavior
and return the authorized choices in an object envelope. Do not silently select
the first Journey or forward a raw array: the current tool wrapper rejects arrays.
Verify whether an existing stage tool lists definitions or actual Journey progress.

Both reads require verified end-user authorization and must not perform business
writes. `GetCurrentUserJourneys` and `GetUserRoles` may support the existing UI or
authorization implementation, but are not new calls required by this agent code.

## 5. Authorization and ownership

| Calling workload | Allowed MCP capabilities |
| --- | --- |
| Assistant | Authorized status by Journey/APM ID and its own application/user sessions, with delegated end-user identity. |
| Orchestrator | Its own application/user session tools through MCP; delegates business questions to Assistant. |
| APM Validation | Read `apm-validation` results, invoke `ValidateApm`, and claim/save/finish only its checkpoints. |
| AD Provisioning | Read `ad-provisioning` results, submit/poll only requests owned by the Journey, and claim/save/finish only its checkpoints. |
| App Factory | Read `app-factory-validation` results, invoke `ValidateAppFactory`, and claim/save/finish only its checkpoints. |

Schwab must enforce these permissions on the server using the authenticated
workload, independently of client-side allowlists. Batch calls use workload
identity; the current jobs do not receive or forward an end-user token. Confirm
the approved service-to-service authorization path for these operations.

For chat reads, verify the delegated user credential and enforce Journey/group
access. Derive identity from verified authentication, never from a caller-supplied
user ID or role in tool arguments. Existing `GetUserRoles` data must be interpreted
under Schwab's authorization rules.

Current configurable transport headers are `X-Serverless-Authorization` for a
workload ID token, optional `Authorization` for a configured MCP credential, and
`X-User-Authorization` for delegated user identity. The delegated header can be
configured as `Authorization` instead. These describe the current client, not
Schwab's confirmed requirements. Agree token types, audiences, header use, and
any exchange mechanism before integration; never put credentials in tool results.

## 6. Persistence, retries, and error behavior

- Business tools must persist their result, permitted Journey transitions,
  dependency references, and appropriate audit before reporting a recorded
  outcome. A later `GetJourneyOperation` must recover that result after the agent
  process restarts. Use Schwab's existing Data API/business persistence layer.
- Current idempotency keys are `<journey_id>:<operation_key>`. Replaying a logical
  request, including concurrent requests and timeouts, must not duplicate a provider
  submission or already committed transition. Changed business input under the
  same key must not silently create new work. Revalidation/reprovisioning requires
  an agreed versioned operation identity and corresponding agent/schema changes.
- A completed negative business decision is a normal result with the appropriate
  `outcome`, not an MCP execution error. In particular, `INVALID`, `REJECTED`, and
  `VALIDATION_ERROR` must remain readable on subsequent invocations. Do not attach
  `ok: false` to a recorded business outcome: the current transport treats that
  flag as a tool failure before the outcome reaches the adapter.
- Authentication, invalid input, invalid workflow prerequisites, provider
  unavailability, and unreadable/uncommitted results must use an error response.
  The current client recognizes MCP `isError` and transport failures, and rejects
  malformed/mismatched results. Error text should be actionable without exposing
  credentials or unauthorized Journey data. Persistence conflicts use machine
  error codes; recognized transport failures on persistence mutations are retried
  once with the same mutation ID. Business retries still require the idempotency
  and reconciliation rules above.
- Calls currently have a configurable 90-second client timeout. Return AD pending
  status within a bounded call and poll later. Confirm operation latency and retry
  expectations; this is a client setting, not a verified Schwab service SLA.

The current client uses the MCP SDK's Streamable HTTP transport, checks the tool
catalog before invoking a tool, and accepts either a structured result object or
one JSON text block. Return the raw contract object to the adapter, or agree an
envelope-unwrapping change. For an absent operation, one JSON text block containing
`null` is compatible with the current client.

Identifiers also need agreement: the current durable schema supports Journey IDs
up to 32 characters and result/external references up to 256 characters. Its APM
read guard currently normalizes the PoC's `APM00####` convention, such as
`APM004001`. Confirm Schwab's actual formats before adopting these limits; adapt
the agent validation/schema if necessary instead of truncating production IDs.

## 7. Required durable and session persistence tools

Schwab MCP owns these database transactions. They are internal runtime calls,
separate from the model's business tool allowlist. See the
[full persistence contract](references/schwab-mcp/README.md) for field limits,
complete response envelopes, and implementation details.

| Exact agent-side tool name | Caller | Arguments and required behavior |
| --- | --- | --- |
| `claim_durable_operation` | Three batch agents | `journey_id`, `agent_name`, `workflow_run_id`, `mutation_id`; atomically acquire/create a checkpoint and execution lease; return full snapshot. |
| `save_durable_checkpoint` | Three batch agents | Above scope plus `checkpoint_id`, `execution_id`, `expected_version`, `current_stage`, `checkpoint_status`, nullable references/error; atomically save progress and audit; return version + 1. |
| `finish_durable_operation` | Three batch agents | Same ownership/version fields, `mutation_id`, nullable `error`; end execution and release lease while preserving progress; return version + 1. |
| `create_agent_session` | Assistant and Orchestrator runtimes | `app_name`, `user_id`, `session_id`, `mutation_id`, optional `state`; create session at revision 1; return full session envelope. |
| `get_agent_session` | Assistant and Orchestrator runtimes | `app_name`, `user_id`, `session_id`, optional event-filter `config`; return session/version or authorized absence. |
| `list_agent_sessions` | Assistant and Orchestrator runtimes | `app_name`, `user_id`; return only this user's session summaries and versions. |
| `append_agent_session_event` | Assistant and Orchestrator runtimes | Session scope, `mutation_id`, `expected_version`, complete ADK `event`; atomically commit event, state deltas, revision, and receipt. |
| `delete_agent_session` | Assistant and Orchestrator runtimes | Session scope and `mutation_id`; delete conversation and old receipts, retain deletion marker, acknowledge the exact scope. |

For every mutation, persist an idempotent response receipt in the same database
transaction as the state change. Replay with the same ID/payload returns the
original response. Reject changed input, stale versions, expired leases, and
cross-workload or cross-user requests. Use verified credentials, never trust
`agent_name`, `app_name`, or `user_id` as authorization by themselves. Session calls
require delegated user authentication in addition to workload authentication.

Apply the [durable](migrations/durable-state/apply.sql) and
[session](migrations/session-state/apply.sql) schemas through Schwab's migration
pipeline. The server reference reflects existing tables and never creates them
at runtime. Adapt the [business schema](migrations/business-state/apply.sql) to
existing Journey/Data API tables. No agent identity receives DB IAM, database
credentials, network attachment to databases, or table privileges.

## 8. Scope and acceptance criteria

MCP owns business, durable, and session access. There is no generic SQL, arbitrary
business status setter, new Journey creation, or downstream infrastructure
provisioning tool in this request.

Before integration is accepted, demonstrate:

1. The five batch capabilities are advertised under the agreed names, with schemas
   and sample responses; both Assistant read mappings are confirmed.
2. An authorized unstarted operation returns `null`; unauthorized or failed reads
   return an error instead.
3. APM and App Factory validation persist both positive and negative outcomes,
   and repeated requests recover them without duplicate transitions.
4. Duplicate/concurrent AD submission uses one provider request. Recovery after an
   ambiguous provider response reconciles that request instead of submitting again.
5. AD polling returns pending and terminal results with the same external ID,
   rejects mismatched Journey/request pairs, and performs no new submission.
6. A business commit followed by a simulated agent interruption is recovered
   through `GetJourneyOperation`, with no repeated business action.
7. Workload and user permissions are enforced, and business-negative outcomes are
   distinguishable from transient infrastructure/tool errors.
8. All eight persistence tools are advertised; state, audit/events, and retry
   receipts commit atomically. A response lost after commit replays exactly once.
9. Concurrent claims across server replicas create one owner; stale or expired
   workers cannot change checkpoint progress or release another execution's lease.
10. Sessions survive agent restart, append events/state together, reject stale
    revisions and cross-user/app access, and cannot be resurrected by a replay
    after deletion. Temporary state and tokens are not persisted.
11. Run these checks on PostgreSQL behind the real authenticated MCP transport
    with agent database permissions absent. Verify rollback under injected failures.

Please confirm the implementation owner for each capability, reusable underlying
Data API operations, final schemas/name mapping, authorization requirements,
test-environment access, and target availability. The request is **five business
capabilities plus eight persistence tools and verification/reuse of two status
reads**. Confirm existing implementations before counting new server work.

## Traceability to the agent implementation

| Repository source | Contract derived from it |
| --- | --- |
| [guardrails.py](src/cloud_journey_agents/guardrails.py) | Exact current tool allowlists, read arguments, and APM normalization. |
| [durability/mcp_gateway.py](src/cloud_journey_agents/durability/mcp_gateway.py) | Batch tool arguments, response identity checks, outcomes, and reference requirements. |
| [durability/runtime.py](src/cloud_journey_agents/durability/runtime.py) | Recovery reads before step execution and derived checkpoint handling. |
| [Assistant tools](src/agent-assistant/app/tools.py) | The two status reads and object-response requirement. |
| [mcp.py](src/cloud_journey_agents/mcp.py) | Discovery, transport, authentication headers, timeout, and error handling. |
| [MCP_CONTRACT.md](MCP_CONTRACT.md) | Existing internal integration summary. |

The local business simulator and reference SQL illustrate persistence requirements;
they do not establish Schwab's production schemas, validation rules, or existing
tool behavior.
