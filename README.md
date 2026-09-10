# Durable Cloud Journey Orchestrator PoC

A main Google ADK orchestrator that routes application and infrastructure
questions to specialist agents and directly owns a PostgreSQL-backed Journey
lifecycle. The durable Journey is a capability of the orchestrator, not an
independent agent. Chat sessions and the ADK process remain disposable: every
status response can be rebuilt from `journeys` and the append-only
`journey_events` table.

```text
User / HTTP playground
          |
          v
Main Orchestrator
    |           |-------------------------------|
    v           v                               v
APM Agent   Asset Inventory Agent   Durable Journey capability
                                                |
                                                v
                                  Cloud SQL state + audit history
```

## How the PoC works: a normal sample flow

Think of a Journey as one durable business request for one application, identified
by its APM ID. The chat is only the conversational front end. PostgreSQL, not the
chat history or the agent process, owns the request's current state, collected
application facts, proposed plan, access group, and audit history.

Here is the happy-path example using the seeded demo data:

1. **Sam signs in with Google and starts APM `100401`.** The HTTP layer verifies
   Sam's Google token and binds its stable subject to the ADK session. PostgreSQL
   confirms that subject belongs to `GROUP_1` and APM `100401` is assigned there.
2. **The Journey is created and its APM is validated.** PostgreSQL stores a new
   Journey with a generated ID such as `J-12AB34CD`, its owning group, requester,
   state, and version. Starting `100401` again does not create a second Journey;
   an authorized group member receives the existing one.
3. **Cloud Compass gathers application knowledge.** Sam describes the application,
   environments, dependencies, data classification, and availability needs. Those
   facts are saved in the Journey's durable context, so they survive a browser,
   agent, or service restart.
4. **Cloud Compass creates a proposed plan.** The PoC saves the plan and advances
   the Journey to `WAITING_FOR_APPROVAL`. No infrastructure has been created; all
   discovery, MCP, provisioning, and Cloud Build activity in this PoC is simulated.
5. **A separate reviewer makes the decision.** Cloud Compass deliberately has no
   approve or reject tool. The external approval backend verifies that `reviewer`
   belongs to `CLOUD_JOURNEY_APPROVERS` and did not request the Journey, then writes
   either `APPROVED` or `REJECTED` to the same database.
6. **An approved Journey resumes.** Cloud Compass observes the durable approval and
   simulates identity provisioning, App Factory preparation, Cloud Build, and
   deployment validation. Each checkpoint is committed before the next one begins.
7. **The Journey can be recovered later.** If Cloud Run is restarted, another
   verified subject in `GROUP_1` can retrieve APM `100401`. A verified member of
   `GROUP_2` receives the same non-disclosing response as for an unknown APM ID.

In short:

```text
Conversation
    -> database authorization
    -> durable discovery and planning
    -> independent human approval
    -> resumable simulated execution
    -> durable completion and audit history
```

### Main design points

**Group-based Journey authorization.** Access is based on the intersection of two
database relationships: the caller's rows in `access_group_members` and the APM's
row in `apm_group_assignments`. The resulting group is copied to
`journeys.access_group_id` when the Journey is created. Every Journey-specific ADK
read or change checks that durable group boundary. `owner_subject` records who
created the Journey for audit purposes, but it is not the access-control rule;
members of the same group intentionally share access.

**Privacy-preserving lookup behavior.** Cross-group and nonexistent APM lookups use
the same denial message. The response does not expose whether another group's APM
or Journey exists, nor its ID, requester, state, context, plan, or history.

**A separate approval authorization boundary.** Project access and approval power
are different permissions. `GROUP_1` or `GROUP_2` membership grants access to the
group's Journeys. Only `CLOUD_JOURNEY_APPROVERS` membership grants approval-backend
access, and a requester cannot approve their own Journey. Keeping approve/reject
out of the agent tools demonstrates separation of duties instead of relying on a
prompt instruction as a security boundary.

**Database-backed durability.** `journeys` is the latest-state projection,
`journey_events` is the actor-aware append-only history, and `journey_operations`
records command outcomes. ADK session state contains only the verified Google
subject, email, and display name; it never contains the raw ID token and is not the
source of Journey truth. After a restart, status is rebuilt from PostgreSQL.

**Controlled state changes and concurrency.** All state changes go through one
transition map. A database row lock and version check prevent conflicting actions
from both succeeding—for example, simultaneous approval and rejection. The state
update and its audit event commit in the same transaction.

**One Journey per APM ID.** A database uniqueness constraint makes an APM ID global
across chats and users. Repeated or concurrent starts by the authorized group return
the same Journey rather than creating parallel business requests.

**Clear PoC limits.** Google authenticates the user, while business authorization
still depends on administrator-managed `access_group_members` rows keyed by the
verified Google subject. Integrations and resource changes remain simulated. A
production implementation should synchronize groups from a trusted directory,
connect the external systems, and use a callback, event, or durable timer for long
approval waits.

### Verified Google identity boundary

The Cloud Run HTTP orchestrator verifies a Google Identity Services ID token,
including its audience, verified email, and stable `sub` claim. The verified
claims are injected into ADK session state by server code and are not model tool
arguments:

```text
Browser sends Google ID token in X-User-Authorization
    -> HTTP layer verifies the token for this OAuth client
    -> verified Google sub becomes ADK user_id
    -> sub, verified email, and display name enter model-hidden session state
    -> Journey tools verify state sub == ToolContext.user_id
    -> access_group_members is queried with the verified sub
    -> the APM-to-group mapping decides access
```

`start_journey` has no user-name parameter, and there is no identity-selection
tool. A prompt such as "I am another user" cannot change authorization. Google
authentication alone grants no APM access: an administrator must map the verified
subject to a business group in PostgreSQL. Raw Google tokens are request-scoped
and are not stored in ADK state, Journey context, or audit events.

Google identity also does not by itself establish the PoC's business groups. After
sign-in, the backend still needs a trusted source for APM access membership—either
the existing `access_group_members` table keyed by Google subject, or a separately
authorized and cached lookup/sync from the organization's group directory. The same
principle applies to `CLOUD_JOURNEY_APPROVERS`: approval membership must come from a
trusted backend claim or directory, never from chat text.

The external approval service must apply the same rule: approval membership must
come from a trusted backend claim or directory, never from chat text.

## What is implemented

- Central transition validation in `orchestrator_agent/app/cloud_journey/state_machine.py`
- PostgreSQL `SELECT ... FOR UPDATE` plus a version-guarded update
- One transaction per transition, numeric versions, and complete actor-aware audit history
- Resumable architecture-aligned discovery, identity, App Factory, Cloud Build,
  and deployment-validation stages
- Segregated approval boundary based on `CLOUD_JOURNEY_APPROVERS` membership
- `journey_operations` records for command outcomes and future idempotency/retry work
- Globally unique APM IDs enforced by the database
- Database-backed group-to-APM authorization keyed by verified Google subjects
- One `orchestrator_agent.app.main.root_agent` composing two specialist-routing tools
  with eight durable Journey lifecycle tools
- Self-contained unit/acceptance tests, including process restart and conflicting actions

No real cloud resources are provisioned and no OAuth tokens are stored.

For a Cloud Run source deployment using direct `gcloud` commands and the existing
Cloud SQL database, follow [GCP_DEPLOYMENT.md](GCP_DEPLOYMENT.md).

## Prerequisites

- Python 3.11 or newer
- A Cloud SQL for PostgreSQL 14+ instance
- Cloud SQL Auth Proxy v2 and the PostgreSQL `psql` client
- A Gemini API key, or Vertex AI application credentials

## Local setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Start the Cloud SQL Auth Proxy in a separate PowerShell terminal:

```powershell
.\cloud-sql-proxy.exe --port 5432 PROJECT_ID:REGION:INSTANCE_NAME
```

Set the database connection in `.env`, using the password for the Cloud SQL
application database user:

```text
DATABASE_URL=postgresql+psycopg://journey:URL_ENCODED_PASSWORD@127.0.0.1:5432/durable_journey
```

### Recreate the Cloud SQL database from scratch

The repository includes a baseline migration followed by three incremental
migrations. Stop any running agent that uses this database first. With the Cloud
SQL Auth Proxy listening on `127.0.0.1:5432`, permanently delete and recreate
only the `durable_journey` database by running:

```powershell
.\scripts\recreate_cloud_sql_database.ps1 `
    -AdminUser postgres `
    -ApplicationUser journey `
    -DatabaseName durable_journey
```

`postgres` must be a Cloud SQL database administrator that can drop/create
databases, and the `journey` database user must already exist on the Cloud SQL
instance. `psql` prompts for their database passwords. The script verifies the
proxy connection and requires typing `durable_journey` before deletion.

For non-interactive disposable environments only, add `-Force`:

```powershell
.\scripts\recreate_cloud_sql_database.ps1 `
    -AdminUser postgres `
    -ApplicationUser journey `
    -DatabaseName durable_journey `
    -Force
```

The migration order is:

1. `000_initial_schema.sql` — creates the Journey and normalized group-access
   tables.
2. `001_apm_uniqueness_and_ownership.sql` — safely preserves the previous
   upgrade path and database uniqueness boundary.
3. `002_group_apm_authorization.sql` — creates and seeds `access_groups`,
   `access_group_members`, and `apm_group_assignments`, then assigns each
   Journey an `access_group_id`.
4. `003_architecture_aligned_states.sql` — updates active Journey projections
   from the original PoC state names to the architecture-aligned names. It
   preserves history and appends an auditable migration transition.

After recreation, start the agent:

```powershell
uvicorn orchestrator_agent.app.main:app --reload
```

Open `http://127.0.0.1:8000/playground`, then test an allowed request with
`Start a journey with APM ID 100401.` To test denial, sign in with a verified
subject mapped to `GROUP_2` and try APM `100401`.

The Auth Proxy exposes the local TCP endpoint; it does not create a local Docker
database. The application can create missing PoC tables on its first tool call,
but the migration sequence above is the reproducible setup path.

Set either `GOOGLE_API_KEY` for Google AI Studio or the Vertex AI variables shown
in `.env.example`.

## Run tests

```powershell
pytest
```

Tests use a temporary SQLite database so they need no external service. Runtime
configuration defaults to PostgreSQL, and the same transition code executes
`SELECT ... FOR UPDATE`; the optimistic version predicate adds protection on
backends that do not implement row locks.

## Verified-subject group authorization and privacy boundary

An APM ID identifies one Journey globally, not one Journey per chat session. The
database has a unique constraint on `journeys.apm_id`, so two simultaneous agent
requests cannot create separate Journeys for `100401`.

The verified Google `sub` is the only user key accepted by Journey tools. It is
injected at the HTTP boundary and cross-checked against `ToolContext.user_id`;
neither value is exposed as a model-callable argument.

The normalized authorization tables are the source of truth:

- `access_groups` defines groups.
- `access_group_members` maps verified Google subjects to groups.
- `apm_group_assignments` maps APM IDs to groups.
- `journeys.access_group_id` records the owning group durably.

Fresh test databases retain these placeholder subjects so policy behavior is
repeatable. A deployed database must use real verified Google subjects instead:

| Test group | Placeholder subjects | Available APM IDs |
|---|---|---|
| `GROUP_1` | `sam`, `ivan`, `adi` | `100401`, `100402` |
| `GROUP_2` | `abdur`, `ajir` | `100403`, `100404` |

`journeys.owner_subject` records the verified Google subject for audit. Every ADK
read or change checks `journeys.access_group_id` against that subject's current
database membership.

This gives the intended behavior:

- A verified same-group member can recover the Journey with its APM ID in a new
  session.
- Starting the same APM ID again as a same-group member returns the existing durable
  Journey instead of creating another row.
- A different-group user receives the same denial for a cross-group APM ID as for
  an unmapped APM ID. No Journey ID,
  requester, status, history, or plan is returned.
- The database uniqueness constraint remains the final duplicate guard during
  concurrent requests.

For an existing database, run
[`orchestrator_agent/migrations/001_apm_uniqueness_and_ownership.sql`](orchestrator_agent/migrations/001_apm_uniqueness_and_ownership.sql),
then [`orchestrator_agent/migrations/002_group_apm_authorization.sql`](orchestrator_agent/migrations/002_group_apm_authorization.sql),
and finally [`orchestrator_agent/migrations/003_architecture_aligned_states.sql`](orchestrator_agent/migrations/003_architecture_aligned_states.sql)
before starting this version. The legacy `apm_group_access` table is no longer
read or seeded by the application.

## Approval boundary

Cloud Compass has no approve or reject control. It creates knowledge and plans,
then stops at `WAITING_FOR_APPROVAL`. A separate approval backend owns the human
decision and writes `APPROVED` or `REJECTED` to PostgreSQL.

The local simulator models that backend with these identities:

| User | Business role | Simulated AD groups | Cloud Compass access | Approval-backend access |
|---|---|---|---|---|
| `sam`, `ivan`, `adi` | `PROJECT_OWNER` | `GROUP_1` | APMs `100401`, `100402` | None |
| `abdur`, `ajir` | `PROJECT_OWNER` | `GROUP_2` | APMs `100403`, `100404` | None |
| `reviewer` | `REVIEWER` | `CLOUD_JOURNEY_APPROVERS` | General knowledge only | Approve/reject others' requests |
| `developer` | `DEVELOPER` | none | General knowledge only | None |

Cloud Compass can only poll the database and observe the backend decision. It can
resume provisioning after it reads `APPROVED`; it cannot create that state. The
simulator waits 60 seconds by default. A real seven-day approval must use an
event/callback or durable workflow timer rather than keeping an ADK request open.

## Architecture-aligned Journey states

The state machine records recoverable business progress, not every infrastructure
hop. SSO authentication and xAPI security-context validation occur before a
Journey is created. Agent Gateway routing, Model Armor, MCP clients, model calls,
and session-state reads are control-plane or request-level activity; they should
emit telemetry, but they are not durable Journey states. The Graph Workflow
Orchestrator coordinates the following durable states in Cloud SQL:

| Phase | Durable states | Architecture owner or boundary |
|---|---|---|
| Request and validation | `CREATED` -> `VALIDATING_APM` -> `APM_VALIDATED` | Graph Workflow Orchestrator and APM Validation Agent |
| Discovery | `DISCOVERING_CLOUD_SERVICES` -> `COLLECTING_ASSET_INVENTORY` -> `ASSET_INVENTORY_COMPLETE` | Cloud Services Agent through the Google Asset Inventory MCP server |
| Planning | `GENERATING_PLAN` -> `WAITING_FOR_APPROVAL` | Graph Workflow Orchestrator |
| Human decision | `WAITING_FOR_APPROVAL` -> `APPROVED` or `REJECTED` | External approval backend; Cloud Compass can only observe the result |
| Approved execution preparation | `APPROVED` -> `PROVISIONING_AGENT_IDENTITY` -> `AGENT_IDENTITY_READY` -> `PREPARING_APP_FACTORY` -> `APP_FACTORY_READY` | AD Provisioning Agent/MyAccess MCP, then App Factory Helper Agent |
| Build and verification | `SUBMITTING_CLOUD_BUILD` -> `CLOUD_BUILD_RUNNING` -> `VALIDATING_DEPLOYMENT` -> `COMPLETED` | App Factory Helper Agent through the Cloud Build MCP server |
| Recovery | Any processing state -> `FAILED` -> `RETRYING` -> a processing state | Graph Workflow Orchestrator, using durable state and event history |

`APM_VALIDATED`, `ASSET_INVENTORY_COMPLETE`, `AGENT_IDENTITY_READY`, and
`APP_FACTORY_READY` are stable resume checkpoints. The other nonterminal states
represent work in progress. `COMPLETED` and `REJECTED` are terminal. The PoC
simulates all MCP and provisioning operations and marks their audit metadata with
`simulated=true`.

### Sample durable flow

An approved Journey follows this path:

```text
CREATED
-> VALIDATING_APM
-> APM_VALIDATED
-> DISCOVERING_CLOUD_SERVICES
-> COLLECTING_ASSET_INVENTORY
-> ASSET_INVENTORY_COMPLETE
-> GENERATING_PLAN
-> WAITING_FOR_APPROVAL
-> APPROVED
-> PROVISIONING_AGENT_IDENTITY
-> AGENT_IDENTITY_READY
-> PREPARING_APP_FACTORY
-> APP_FACTORY_READY
-> SUBMITTING_CLOUD_BUILD
-> CLOUD_BUILD_RUNNING
-> VALIDATING_DEPLOYMENT
-> COMPLETED
```

If the external reviewer rejects the plan, the path ends at `REJECTED`. If a
processing step fails, the Journey moves to `FAILED`; an explicit retry moves it
through `RETRYING` and back to the selected processing state. Every arrow is
validated centrally and appended to `journey_events` in the same transaction as
the current-state update.

### Confluence-aligned business events

The durable state names above describe how the orchestrator resumes work. The
Confluence event catalog describes the business outcomes that other systems and
people care about. The PoC now records both views in `journey_events`:

- `JOURNEY_CREATED`, `STATE_TRANSITION`, and `CONTEXT_UPDATED` remain the detailed
  internal audit records.
- The CamelCase event names below are business events. They are exposed separately
  as `business_events` in Journey status responses.
- A business event and the state/context change that caused it are committed in the
  same database transaction. Consumers cannot observe the event without its durable
  result.

| Business event | Confluence description | Emitted by this PoC when |
|---|---|---|
| `JourneyStarted` | New Journey created | The unique Journey row is created |
| `JourneyDataChanged` | User/System Data updated | Inventory, plan, or checkpoint context is persisted |
| `ChecklistCalculated` | Checklist Refreshed | Asset inventory reaches `ASSET_INVENTORY_COMPLETE` |
| `GovernanceTicketCreated` | Governance Started | The proposed plan reaches `WAITING_FOR_APPROVAL` |
| `GovernanceStatusChanged` | Approval Changed | The external backend records `APPROVED` or `REJECTED` |
| `MyAccessRequestSubmitted` | Access Request Created | Execution enters `PROVISIONING_AGENT_IDENTITY` |
| `MyAccessStatusChanged` | Access Updated | The simulated agent identity reaches `AGENT_IDENTITY_READY` |
| `DependencyCompleted` | External dependency completed | Identity readiness allows App Factory preparation to begin |
| `ReadinessEvaluated` | Readiness Decision Produced | App Factory preparation reaches its ready checkpoint |
| `AppFactoryManifestPublished` | Manifest Generated | The simulated App Factory reaches `APP_FACTORY_READY` |
| `ProvisioningStarted` | Deployment Started | The simulated Cloud Build submission begins |
| `ProvisioningStatusChanged` | Deployment Changed | Cloud Build runs or deployment validation begins |
| `ProvisioningCompleted` | Deployment Finished | Deployment validation reaches `COMPLETED` |
| `JourneyTransitionedToBAU` | Journey Completed | The completed Journey transitions conceptually to BAU |

Some events can occur more than once. For example, `JourneyDataChanged` is emitted
for every durable context update and `ProvisioningStatusChanged` is emitted as the
deployment moves through its running and validation checkpoints. The business-event
metadata retains the actor, source state, target state, trigger, and relevant source
details such as a rejection reason.

## Durable Journey tools composed into the orchestrator

| Tool | Purpose | Changes Journey state |
|---|---|---:|
| `start_journey(apm_id)` | Use the verified Google subject, authorize its APM mapping, and create or return the group's Journey | Yes on first call |
| `get_cloud_journey_guidance(question, journey_id)` | Answer using persisted Journey context and identify missing discovery facts | No |
| `record_application_inventory(...)` | Persist application, platform, dependency, data, and availability knowledge | Yes |
| `generate_cloud_plan(...)` | Persist a proposed target plan and submit it for independent review | Yes |
| `wait_for_external_approval(journey_id, timeout_seconds, poll_interval_seconds)` | Poll PostgreSQL for an external decision for up to two minutes | No |
| `resume_journey_after_approval(journey_id)` | Continue simulated execution only if PostgreSQL already says `APPROVED` | Yes |
| `get_journey_status(journey_id)` | Read current state, version, requester, and complete audit history | No |
| `get_journey_status_by_apm_id(apm_id)` | Recover a Journey authorized for the verified user's group | No |

Neither approval nor rejection is registered as an ADK tool. The backend-only
simulator is a separate module, `orchestrator_agent.app.cloud_journey.approval_backend`. All state changes
still pass through the central state machine. The original `continue_journey`
function remains a compatibility API for the initial PoC contract, but Cloud
Compass cannot call it.

## Run the main orchestrator locally

From the repository root, run:

```powershell
uvicorn orchestrator_agent.app.main:app --reload
```

Then open `http://127.0.0.1:8000/playground`. The response includes a session ID,
which the playground retains for multi-turn context. Journey state itself remains
in PostgreSQL and can be recovered by APM ID after that HTTP session is lost.

## Practical demo conversation

Use the messages below in order. Replace `J-XXXXXXXX` with the Journey ID returned
by the first message when starting a new chat; within one chat, “the journey”
should resolve to the prior tool result.

### Demo 1: knowledge discovery, external approval, and automatic resume

```text
Before I start, explain what Cloud Compass can help me with during an application cloud journey.

Start a Cloud Journey for APM 100401.

What do you need to know about this application before recommending a cloud plan?

The application is Customer Orders API. It is a Tier 1 business service currently
running on on-premises VMware. It has development, test, and production environments.
Its dependencies are PostgreSQL, Active Directory, and an external payment gateway.
It processes confidential customer data and requires 99.95% availability with
disaster recovery.

Based on this inventory, explain reasonable migration options and the tradeoffs
between GKE, Cloud Run, and a VM-based migration.

Create a proposed plan targeting Google Kubernetes Engine with Cloud SQL for
PostgreSQL. The objectives are improved resilience and reduced infrastructure
operations. Constraints are no more than 15 minutes of cutover downtime and all
application traffic must use private connectivity.

Show me the proposed plan and current Journey status.

Who is responsible for approving this Journey, and can approval happen here?
```

Expected checkpoints:

- Cloud Compass begins as a knowledge assistant, not as a workflow command menu.
- `sam` creates the durable request as `PROJECT_OWNER`.
- The discovery question lists the application facts still needed.
- The owner-provided inventory is persisted before any plan is generated.
- Cloud Compass explains options using the captured inventory.
- Inventory capture produces `DISCOVERING_CLOUD_SERVICES ->
  COLLECTING_ASSET_INVENTORY -> ASSET_INVENTORY_COMPLETE`.
- Plan generation produces `GENERATING_PLAN -> WAITING_FOR_APPROVAL`, version 8.
- Agent identity and App Factory preparation happen only after approval, before
  the simulated Cloud Build submission.
- Cloud Compass explains that the external approval backend owns the decision and
  no approval action is available in chat.

In a second terminal, mimic the approval backend. Replace the ID and use 60–120
seconds for the visible demo:

```powershell
python -m orchestrator_agent.app.cloud_journey.approval_backend J-XXXXXXXX --decision approve --reviewer reviewer --delay-seconds 60
```

Immediately return to Cloud Compass and send:

```text
Wait up to two minutes for the external approval decision. If the database says
APPROVED, resume the Journey and show me the completed transition history.
```

Cloud Compass polls without changing state. After the backend writes `APPROVED`,
it observes that value and invokes the separate resume tool:

```text
WAITING_FOR_APPROVAL
-> APPROVED                   Actor: APPROVAL_BACKEND / reviewer
-> PROVISIONING_AGENT_IDENTITY Actor: AGENT / ad-provisioning-agent
-> AGENT_IDENTITY_READY        Actor: AGENT / ad-provisioning-agent
-> PREPARING_APP_FACTORY       Actor: AGENT / app-factory-helper-agent
-> APP_FACTORY_READY           Actor: AGENT / app-factory-helper-agent
-> SUBMITTING_CLOUD_BUILD     Actor: AGENT / app-factory-helper-agent
-> CLOUD_BUILD_RUNNING        Actor: AGENT / app-factory-helper-agent
-> VALIDATING_DEPLOYMENT      Actor: AGENT / app-factory-helper-agent
-> COMPLETED                  Actor: AGENT / app-factory-helper-agent
```

### Demo 2: independent reviewer rejects with a persisted reason

```text
Start a Cloud Journey for APM 100402.

The application is Partner Portal. It is a Tier 2 service on Windows VMs with
development and production environments. Dependencies are SQL Server, corporate
Active Directory, and an SMTP relay. It contains internal confidential data and
requires 99.9% availability.

Create a proposed plan targeting Compute Engine. The objective is a low-change
migration. Constraints are private connectivity and the existing Windows runtime.

Who is responsible for the decision? Confirm that I cannot reject it from Cloud Compass.
```

In the separate backend terminal:

```powershell
python -m orchestrator_agent.app.cloud_journey.approval_backend J-XXXXXXXX --decision reject --reviewer reviewer --delay-seconds 60 --reason "Network firewall design is incomplete"
```

Then ask Cloud Compass:

```text
Wait up to two minutes for the external decision and show the current status.
```

Expected final state: `REJECTED`. Cloud Compass observes it and does not resume.

### Demo 3: new-session recovery and group isolation

```text
Start a Cloud Journey for APM 100403.

The application is Reporting Service. It is Tier 2, runs on Linux VMs, has test
and production environments, depends on PostgreSQL and SFTP, contains internal
data, and requires 99.9% availability.

Create a proposed plan targeting Cloud Run with Cloud SQL. The objective is to
reduce operations effort. Constraints are private database access and a phased cutover.
```

Restart the local server or close the browser. Later, open a new conversation and
select another member of `GROUP_2` before asking for status. The new chat has no
Journey ID and no previous conversation state:

```text
I am ajir. Could you give me the current status of APM ID 100403?
```

Expected response: Cloud Compass calls `get_journey_status_by_apm_id` and reports
`WAITING_FOR_APPROVAL` with the Journey ID, captured plan, and durable history
because the verified caller's subject is mapped to `GROUP_2`.

Now open another new session and repeat as a `GROUP_1` member:

```text
Could you give me the current status of APM ID 100403?
```

Expected response:

```text
I could not find a Journey you can access for that APM ID.
```

It must not confirm that `100403` exists or reveal its Journey ID, requester,
state, plan, or history. Finally, open a new session as another `GROUP_2` member
and send:

```text
Start a Cloud Journey for APM 100403.
```

Cloud Compass returns the existing Journey with `created=false`; it does not
create a duplicate. To finish the original workflow, ask:

```text
Wait up to two minutes for an external approval of APM ID 100403. If it is
approved, resume execution.
```

The agent first resolves the group's Journey by APM ID. Run the backend simulator
with the returned Journey ID in another terminal. Cloud Compass then detects the
database update and resumes without any approval action in the chat UI.

## Data and transaction behavior

`journeys` is the current-state projection. `journey_events` is append-only and
includes the creation, every transition, denial events, actor type, actor ID, and
metadata such as rejection reasons. `journey_operations` tracks RUNNING,
COMPLETED, or FAILED command execution.

Every transition follows this sequence inside one database transaction:

1. Select the Journey row `FOR UPDATE`.
2. Validate the requested edge against the central transition map.
3. Update only when the stored state and version still match.
4. Increment the version and append the audit event.
5. Commit both changes together.

This makes concurrent approval and rejection mutually exclusive: once one
transaction commits, the other sees a changed state/version and fails.
