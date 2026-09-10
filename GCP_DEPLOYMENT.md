# Deploy the Orchestrator to Cloud Run

The FastAPI application in `orchestrator_agent/main.py` is the only deployment
entry point. No Python deployment script or Agent Runtime object is required.
Journey state remains in Cloud SQL; HTTP conversation sessions are process-local
and may be recreated, while a Journey remains recoverable by Journey ID or APM ID.

## 1. Set command variables

Run these commands in PowerShell and replace the example values:

```powershell
$PROJECT_ID = "your-project-id"
$REGION = "us-central1"
$SERVICE = "cloud-journey-orchestrator"
$RUNTIME_SA = "cloud-journey-orchestrator@$PROJECT_ID.iam.gserviceaccount.com"
$CLOUD_SQL_INSTANCE = "${PROJECT_ID}:${REGION}:journey-db"
$INVENTORY_AGENT_URL = "https://inventory-agent-url"
$APM_AGENT_URL = "https://apm-agent-url"
$OAUTH_CLIENT_ID = "your-google-web-client-id.apps.googleusercontent.com"

gcloud config set project $PROJECT_ID
```

## 2. Enable APIs and create the runtime identity

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com

gcloud iam service-accounts create cloud-journey-orchestrator --display-name="Cloud Journey Orchestrator"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$RUNTIME_SA" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$RUNTIME_SA" --role="roles/cloudsql.client"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$RUNTIME_SA" --role="roles/secretmanager.secretAccessor"
```

Grant this service account `roles/run.invoker` on each private downstream Cloud
Run specialist:

```powershell
gcloud run services add-iam-policy-binding inventory-agent --region=$REGION --member="serviceAccount:$RUNTIME_SA" --role="roles/run.invoker"
gcloud run services add-iam-policy-binding apm-agent --region=$REGION --member="serviceAccount:$RUNTIME_SA" --role="roles/run.invoker"
```

## 3. Store the database password

Create the secret once, then add the database password as a secret version. Do
not put the password in the deployment command or repository.

```powershell
gcloud secrets create journey-db-password --replication-policy="automatic"
gcloud secrets versions add journey-db-password --data-file="PATH_TO_PASSWORD_FILE"
```

Skip the first command if the secret already exists. Delete the temporary local
password file after adding the version.

## 4. Deploy directly from source

The command overrides the Python buildpack start command so the nested FastAPI
module is launched explicitly:

```powershell
$ENV_VARS = "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,ORCHESTRATOR_MODEL=gemini-2.5-flash,CLOUD_SQL_INSTANCE=$CLOUD_SQL_INSTANCE,CLOUD_SQL_IP_TYPE=PUBLIC,CLOUD_SQL_IAM_AUTH=false,DB_USER=journey,DB_NAME=durable_journey,INVENTORY_AGENT_URL=$INVENTORY_AGENT_URL,APM_AGENT_URL=$APM_AGENT_URL,OAUTH_CLIENT_ID=$OAUTH_CLIENT_ID,ALLOWED_USER_DOMAINS=example.com,REQUEST_TIMEOUT_SECONDS=90"

gcloud run deploy $SERVICE `
  --source=. `
  --region=$REGION `
  --service-account=$RUNTIME_SA `
  --command=uvicorn `
  --args="orchestrator_agent.main:app,--host=0.0.0.0,--port=8080" `
  --set-env-vars=$ENV_VARS `
  --set-secrets=DB_PASSWORD=journey-db-password:latest `
  --allow-unauthenticated
```

`--allow-unauthenticated` permits the browser to load `/playground`; configure
`OAUTH_CLIENT_ID` and `ALLOWED_USER_DOMAINS` so `/v1/query` still requires a
verified Google user. If the service is API-only behind an authenticated gateway,
use `--no-allow-unauthenticated` instead.

For IAM database authentication, set `CLOUD_SQL_IAM_AUTH=true`, omit
`--set-secrets`, and grant the runtime service account
`roles/cloudsql.instanceUser` plus the required PostgreSQL privileges.

## 5. Verify

```powershell
$SERVICE_URL = gcloud run services describe $SERVICE --region=$REGION --format="value(status.url)"
Invoke-RestMethod "$SERVICE_URL/health"
```

Before using Journey tools, an administrator must add the signed-in user's stable
Google `sub` to an application group. The subject must come from a trusted Google
identity or directory source, never from chat input:

```sql
INSERT INTO access_group_members (group_id, user_subject)
VALUES ('GROUP_1', 'VERIFIED_GOOGLE_SUBJECT')
ON CONFLICT DO NOTHING;
```

The raw ID token is never written to this table, ADK session state, Journey
context, or audit history.

After registering the subject, open `$SERVICE_URL/playground` and start with:

```text
Start a durable Cloud Journey for APM 100401.
```
