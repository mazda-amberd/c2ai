# Athena Backend

This document explains how to set up and run the **Athena backend** locally.

## Prerequisites

- **Python**: 3.10+ (see `pyproject.toml`)
- **Poetry** installed
- A database available/configured (see your `.env` for DB settings)

## Setup & Run

Clone or pull the Athena project from (https://github.com/Inferaim/athena) the **`dev`** branch in Git.

> All commands below must be run from:
>
> ```bash
> cd athena/backend
> ```

### 1) Install dependencies

Install the required Python libraries:

```bash
poetry install
```

### 2) Activate the virtual environment

Activate the `(athena-venv)` virtual environment created by Poetry.

Typical Poetry workflow options:

- Spawn a shell inside the venv:

```bash
poetry shell
```

- Or run commands directly without activating the shell using `poetry run ...`.

### 3) Initialize the Athena database

For a **fresh local database**, the init script recreates the DB, runs all migrations, and seeds the admin user:

```bash
poetry run python src/init_db.py
```

### 4) Run database migrations

If the database already exists (e.g. a shared staging DB, or you just pulled new changes), apply any pending migrations without touching existing data:

```bash
poetry run python src/migrate.py
```

**Existing databases that already match the baseline schema** (created before migrations were introduced) should be stamped once so the runner knows they are at head:

```bash
poetry run python src/migrate.py --stamp
```

After that, running `src/migrate.py` without `--stamp` will only apply genuinely new migrations going forward.

> New schema changes must be added as a numbered `.sql` file in `backend/db/migrations/` (e.g. `0002_add_foo.sql`). Never modify an already-applied migration file.

### 5) Generate / update `ATHENA_AUTH_SECRET`

Generate and print a new JWT signing secret:

```bash
poetry run python src/service/auth/generate_secret.py
```

You will see output like:

```text
ATHENA_AUTH_SECRET="XXXXXXX"
```

Copy the entire printed line and paste it into:

- `athena/backend/.env`

#### Optional: auto-write to `.env`

You can also write/update the secret automatically:

```bash
poetry run python src/service/auth/generate_secret.py --write --env-file .env
```

### 6) Start the Athena service

Start the FastAPI service:

```bash
poetry run python main.py
```

By default it uses `APP_HOST` and `APP_PORT` from your environment (see `main.py`).

## Grafana (k8s) and metrics

Cluster model: workloads are standard **Kubernetes Deployments** whose pods carry a `label_tier` Kubernetes label. Tier membership is determined by `kube_deployment_labels{label_tier=~"..."}`. Pod → Deployment mapping: pod → ReplicaSet (`kube_pod_owner`) → Deployment (`kube_replicaset_owner`). GPU metrics from `ray_node_gpus_utilization` (DCGM is not available on this cluster). See `docs/grafana-k8s-exploration.md` for full exploration notes.

| Variable | Purpose | Default |
|----------|---------|---------|
| `GRAFANA_API_URL` | Grafana datasource query endpoint | — (required) |
| `GRAFANA_API_TOKEN` | Grafana bearer token | — (required) |
| `GRAFANA_PROMETHEUS_DATASOURCE_UID` | Prometheus datasource UID | `prometheus` |
| `ATHENA_FINANCIAL_INGESTION_ENABLED` | Run the in-process gateway cost scheduler | `true` |
| `ATHENA_FINANCIAL_POLL_SECONDS` | Delay between gateway counter polls | `3600` |

**Deployment logs (Loki)** — `GET /api/logs/deployment` uses the same Grafana `GRAFANA_API_URL` with a Loki datasource:

| Variable | Purpose | Default |
|----------|---------|---------|
| `GRAFANA_LOKI_DATASOURCE_UID` | Loki datasource UID for `/api/ds/query` (must match Grafana; a wrong UID yields HTTP 404 *Data source not found* from Grafana) | — (required for logs) |
| `GRAFANA_LOKI_MAX_LINES` | Max log lines per request | `2000` |
| `GRAFANA_LOKI_NAMESPACE_LABEL` | LogQL stream label for instance / namespace | `namespace` |
| `GRAFANA_LOKI_DEPLOYMENT_LABEL` | LogQL stream label for app / deployment name | `deployment` |
| `GRAFANA_LOKI_TIER_LABEL` | Optional; when set, tier query param adds `tierN` to the selector | unset |

Per-tier **label_tier** overrides (Prometheus RE2, defaults match cluster layout):

| Variable | Default |
|----------|---------|
| `ATHENA_TIER1_LABEL_REGEX` | `tier1` |
| `ATHENA_TIER2_LABEL_REGEX` | `tier2` |
| `ATHENA_TIER3_LABEL_REGEX` | `tier3\|prod` |

**GPU (Ray)** — tier totals use one Prometheus query: `avg by (label_tier)(label_replace(...) or ...)` over `ray_node_gpus_utilization` with no app/namespace/deployment filters. Per-app GPU uses the panel-18 token × tier query (`get_gpu_per_app_query()`): unfiltered `kube_deployment_labels` and `llm_total_tokens_total` so all apps contribute. Override Ray cluster names per tier if they differ from defaults:

| Variable | Purpose | Default |
|----------|---------|---------|
| `ATHENA_TIER1_GPU_CLUSTER` / `ATHENA_TIER2_GPU_CLUSTER` / `ATHENA_TIER3_GPU_CLUSTER` | Literal `ray_io_cluster` value matched in each `label_replace` arm | `qwen-5254d` / `qwen-pq9sc` / `qwen-l8dnl` |

**Unit normalisation** — raw Prometheus values are converted to 0–100% for UI thresholds:

| Variable | Purpose | Default |
|----------|---------|---------|
| `ATHENA_CPU_CORES_CAP` | Total CPU cores → 100% denominator | `8` |
| `ATHENA_MEMORY_GB_CAP` | Total memory GB → 100% denominator | `80` |
| `ATHENA_EXCLUDED_DEPLOYMENTS` | Comma-separated deployment names to hide from the instance list (case-insensitive) | `nginx` |

GPU (`ray_node_gpus_utilization`): tier totals are one instant query with a `label_tier` dimension (`tier1`–`tier3`); values are in the 0–num_gpus scale; the UI shows that value with a `%` suffix. Per-app GPU is the panel-18 token×tier query (`get_gpu_per_app_query()`); instances without panel-18 attribution show 0.

Successful `GET /api/metrics` calls upsert the `application_instances` table (created by migrations — run `poetry run python src/migrate.py` before starting the server).

## Using Postman

Use Postman with the appropriate HTTP endpoints to interact with the Athena service.

All endpoints except `POST /auth/login` require a valid JWT (`Authorization: Bearer <token>` header or cookie).

### Financial tracking

`GET /api/financial/costs` reads stored historical cost records. Optional query
parameters are `start_date`, `end_date`, `cost_type` (`private_llm`,
`public_api`, or `both`), `tier` (1-4), and an exact `source_namespace`.
Start and end dates are inclusive UTC calendar dates and must be supplied
together. When omitted, the current calendar month through today is used.
Without `tier`, the response contains the Cluster total and all Tier totals;
with `tier`, it contains that Tier total and its per-application totals.
Monetary values are returned as decimal strings in USD and are not recalculated
using the current rate.

The scheduler queries the Grafana Prometheus datasource once at startup and then
hourly. The first successful poll stores a database checkpoint and does not
create a charge. Later polls query the exact checkpoint interval with
`increase(…)` over the gateway counters. Because the gateway reports the caller
pod IP as `requested_host`, each query joins it to `kube_pod_info.pod_ip` to
derive `source_namespace`, and Athena then obtains each namespace's Kubernetes
tier label over the same interval.

One poll prices both cost sources from that same interval, split by the
gateway's `provider` label so a request is never charged twice:

* **Private LLM** (`provider="vllm"`, our own GPU-hosted models). The measured
  request-duration seconds from `llm_duration_seconds_sum` are billable GPU
  time, priced as `GPU-hours × stored hourly GPU rate` (initially `$2.50`).
  This remains an estimate, as Story 10.2 allows for v1.
* **Public API** (every other provider — OpenAI, Azure OpenAI, Anthropic).
  `llm_input_tokens_total` and `llm_output_tokens_total` are queried per
  `source_namespace`, `provider` and `model`, then priced exactly as
  `tokens × the provider's published per-million rate`, with input and output
  billed at their own rates. No estimation is involved.

Seeded public rates (per million tokens, migration
`0017_public_api_token_rates`):

| Provider | Model | Input | Output |
|----------|-------|-------|--------|
| `openai` / `azure-openai` | `gpt-4o` | `$2.50` | `$10.00` |
| `anthropic` | `claude-sonnet-4-6` | `$3.00` | `$15.00` |

Rates are effective-dated: changing a price closes the current row and inserts a
new one, so previously calculated costs keep the rate they were charged at. The
cost and applied rate are stored immutably for historical display. Usage of a
model with no configured rate is logged and skipped rather than failing the
whole poll — add its rate (via `configure_public_api_rate`, or a migration
following the `0017` pattern) before that model goes into use.

If a poll interval is shorter than the Prometheus scrape interval,
`increase(...)` can temporarily return no series. In that case Athena keeps the
checkpoint unchanged and retries with a larger accumulated interval on the next
poll, preventing usage from being skipped.

The source-namespace Grafana dashboard is a UI over this datasource; Athena
calls `/api/ds/query` rather than downloading dashboard HTML. Prometheus must
scrape the gateway counters with their `requested_host`, `provider` and `model`
labels alongside `kube_pod_info.pod_ip`; calls that cannot be mapped to a
non-host-network Kubernetes pod are intentionally left unattributed. A
PostgreSQL transaction advisory lock ensures only one Athena replica queries and
persists a checkpoint interval at a time.

### Auth & users

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/login` | Generate a JWT (optionally set cookie) |
| `GET` | `/users/` | List users |
| `POST` | `/users/` | Create a new user |
| `PATCH` | `/users?user_name=username` | Update a user |
| `DELETE` | `/users?user_name=username` | Delete a user |

### Deployments & pipeline

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/deploy` | Trigger a new deployment (ada-deploy); returns `PipelineRunOut` |
| `POST` | `/api/deploy/update` | Trigger an in-place update (ada-update); returns `PipelineRunOut` |
| `POST` | `/api/deploy/terminate` | Terminate a live deployment (ada-terminate); returns `PipelineRunOut` |
| `GET` | `/api/pipeline/active` | All active operations across all instances |
| `GET` | `/api/pipeline/status?subdomain=…` | Live status for the latest run on a subdomain |
| `GET` | `/api/pipeline/history?subdomain=…` | Past runs for a subdomain (DB only, no GitHub API calls) |
| `POST` | `/api/pipeline/cancel` | Cancel a linked GitHub Actions run (owner of run only) |
| `GET` | `/api/github/branches?repo=…` | List branches in an Inferaim GitHub repository |

### Registered application deployment tracking

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/registered-applications/deployments?tier=…` | Paginated deployment instance history |
| `GET` | `/api/registered-applications/deployments/{id}` | Current progress, GitHub Actions jobs/steps, stored configuration, and event history |
| `POST` | `/api/registered-applications/deployments/{id}/rollback` | Redispatch the stored configuration (admin only) |
| `POST` | `/api/registered-applications/deployments/{id}/upgrade` | Trigger the type-specific in-place upgrade workflow |
| `POST` | `/api/registered-applications/deployments/{id}/terminate` | Confirm and trigger the type-specific termination workflow |
| `POST` | `/api/registered-applications/deployments/{id}/progress` | Pipeline progress callback |
| `POST` | `/api/registered-applications/{application_id}/deployments` | Deploy a registered GitHub workflow using its saved connection and workflow configuration |
| `POST` | `/api/registered-applications/{application_id}/tiers/{tier}/deployments` | Deploy a registered container template into the Tier supplied by the URL |

The GitHub deployment request contains only the deployment name, Tier context,
and registered workflow parameter values. Removed container overrides and secret
reference fields are rejected by the strict request schema:

```json
{
  "instance_name": "release-production",
  "tier": 2,
  "parameters": {}
}
```

The container deployment request is intentionally limited to deployment-specific
values:

```json
{
  "instance_name": "chat-service-tier-1",
  "version": "2.0.0"
}
```

Athena validates the exact image tag using the encrypted registry credential,
then constructs the deployment configuration from the registered template. The
client cannot override the target Tier, image repository, registry, container
port, pull policy, resources, scaling, storage, environment variables, or public
exposure setting. The generated hostname is
`<instance_name>.amberd.ai`. Decrypted registry credentials are used only for
the outbound registry validation call and are never persisted in deployment
history, included in workflow inputs, or returned by the API.

The shared upgrade request is `{"version": "2.0.0"}` and is accepted only for a
running deployment. For containers, Athena validates the exact registry tag,
copies the stored configuration, replaces only `container.image_tag`, and
dispatches the configured update workflow. Configure
`CONTAINER_UPGRADE_REPOSITORY` and `CONTAINER_UPGRADE_WORKFLOW`; they default to
the container deployment repository and `container-update.yml`. For GitHub
Workflow applications, Athena records `github.version` and passes the version as
the `branch` input to the predefined Amberd `ada-update.yaml` workflow. Upgrade
progress uses status `updating` until the pipeline reports `completed` with
status `running`, or reports `failed`. DNS remains unchanged during upgrades.

Termination requires `{"confirmation": "<instance-name>"}` and accepts running
or failed deployments of either supported type. Container cleanup uses
`CONTAINER_TERMINATION_REPOSITORY` and `CONTAINER_TERMINATION_WORKFLOW`; they
default to the container deployment repository and `container-terminate.yml`.
The cleanup pipeline receives the immutable deployment configuration, including
the provider-neutral DNS identity. It reports `terminating` while deleting
Kubernetes resources, then `configuring_dns` while deleting the DNS record, and
finally `completed` with status `terminated`. Athena preserves the hostname for
history and records DNS status as `deleted`.

GitHub Workflow termination dispatches the predefined Amberd
`ada-terminate.yaml` workflow with the deployment ID and instance name. It uses
the same `terminating` and `terminated` history states but has no Athena-managed
DNS stage.

For every GitHub-dispatched deploy, upgrade, or termination, the deployment
detail endpoint resolves the matching workflow run and returns its jobs as
progress categories with the ordered GitHub steps nested below each job. Athena
also synchronizes a completed run to `running`, `terminated`, or `failed` when a
pipeline progress callback was not sent. GitHub credentials therefore need
Actions **read** permission in addition to Actions **write** permission.

#### Registered-application production configuration

Review these variables before enabling registered-application deployments in a
shared environment:

| Variable | Required when | Purpose / default |
|----------|---------------|-------------------|
| `ATHENA_CREDENTIAL_ENCRYPTION_KEY` | GitHub, container-registry, or LLM credentials are stored | High-entropy key used by PostgreSQL `pgcrypto` to encrypt all stored credential values at rest |
| `GITHUB_PAT` or `GITHUB_TOKEN` | A legacy connection ID or predefined workflow is dispatched | Fallback token with access to the configured repositories and Actions workflows |
| `DEVOPS_BRANCH` | Optional | Ref containing container workflows; defaults to `main` |
| `CONTAINER_DEPLOYMENT_REPOSITORY` | Optional | Container deployment workflow repository; defaults to `amberd-ai/devops` |
| `CONTAINER_DEPLOYMENT_WORKFLOW` | Optional | Container deployment workflow; defaults to `container-deploy.yml` |
| `CONTAINER_UPGRADE_REPOSITORY` | Optional | Upgrade workflow repository; defaults to the deployment repository |
| `CONTAINER_UPGRADE_WORKFLOW` | Optional | Upgrade workflow; defaults to `container-update.yml` |
| `CONTAINER_TERMINATION_REPOSITORY` | Optional | Termination workflow repository; defaults to the deployment repository |
| `CONTAINER_TERMINATION_WORKFLOW` | Optional | Termination workflow; defaults to `container-terminate.yml` |
| `DEPLOYMENT_CALLBACK_TOKEN` | Progress callbacks are enabled | Shared secret required in `X-Athena-Deployment-Token` |
| `CONTAINER_SECRET_PROVIDER_URL` | Managed container secrets are enabled | External write-only secret broker base URL |
| `CONTAINER_SECRET_PROVIDER_TOKEN` | Secret broker requires authentication | Bearer token sent only to the secret broker |
| `REGISTRY_CREDENTIAL_PROVIDER_URL` | Private container registries are enabled | Resolves opaque registry credential IDs |
| `REGISTRY_CREDENTIAL_PROVIDER_TOKEN` | Credential provider requires authentication | Bearer token sent only to the credential provider |
| `DOCKER_HUB_API_URL` | Optional | Docker Hub API override; defaults to `https://hub.docker.com` |

Keep tokens in the deployment platform's protected secret store. Do not commit
them to Athena configuration files or workflow inputs.

### Container image tags

Administrators can discover deployment-ready tags for the current version of a
registered container application:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/registered-applications/{application_id}/image-tags?limit=100` | List normalized Docker Hub tags and image references |

Docker Hub is the first supported registry adapter. Public repositories are read
without credentials. When the application has a `registry_credential` reference,
configure the external credential provider with:

| Variable | Purpose |
|----------|---------|
| `REGISTRY_CREDENTIAL_PROVIDER_URL` | Base URL of the provider that resolves opaque registry credential IDs |
| `REGISTRY_CREDENTIAL_PROVIDER_TOKEN` | Bearer token used only for credential-provider requests |
| `DOCKER_HUB_API_URL` | Optional Docker Hub API override; defaults to `https://hub.docker.com` |

Athena calls
`GET {REGISTRY_CREDENTIAL_PROVIDER_URL}/credentials/{credential_id}` and expects
`{"identifier": "...", "secret": "..."}`. The secret should be a read-only
Docker Hub personal access token where possible. Athena exchanges it through
Docker Hub's short-lived access-token endpoint, uses the bearer token for tag
discovery, and never returns or persists either credential. Unsupported
registries return a validation error until a dedicated adapter is added.

### Legacy managed-secret metadata endpoints

Managed-secret endpoints require an administrator token and are available only
for registered applications whose type is `containerized`:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/registered-applications/{application_id}/secrets` | List secret metadata and opaque references |
| `POST` | `/api/registered-applications/{application_id}/secrets` | Create secret metadata and write the value to the configured provider |
| `PATCH` | `/api/registered-applications/{application_id}/secrets/{secret_id}` | Update metadata and optionally rotate the write-only value |
| `DELETE` | `/api/registered-applications/{application_id}/secrets/{secret_id}` | Delete provider material and soft-delete metadata |

Configure the external write-only secret broker with:

| Variable | Purpose |
|----------|---------|
| `CONTAINER_SECRET_PROVIDER_URL` | Base URL of the secret broker used by Athena |
| `CONTAINER_SECRET_PROVIDER_TOKEN` | Bearer token used only for broker requests |

Athena calls `PUT {CONTAINER_SECRET_PROVIDER_URL}/secrets/{secret_id}` with the
application ID, Kubernetes-safe secret name, environment variable, and the
write-only `secret_value`. The broker must return `{"reference": "..."}`.
Deletion calls the same resource with `DELETE` and the opaque reference.

These endpoints are retained for compatibility with existing stored metadata,
but managed secrets are not part of the updated registration or deployment UI
and cannot be attached through either current deployment contract. Secret values
are never stored in Athena's database, deployment configuration, API responses,
or application logs. Athena persists only the name, environment variable, audit
metadata, and provider reference. Existing managed-secret rows must still be
deleted before their container application can be deleted, preventing orphaned
provider material.

The progress callback requires `X-Athena-Deployment-Token` to match the backend
`DEPLOYMENT_CALLBACK_TOKEN`. Configure the same value as a protected secret in
the deployment pipeline. Athena already passes `deployment_id` to every
registered-application workflow dispatch.

Progress callbacks use this body:

```json
{
  "current_step": "waiting_for_rollout",
  "status": "deploying",
  "message": "Waiting for workload readiness."
}
```

Supported steps are `validating_configuration`, `creating_namespace`,
`applying_resources`, `waiting_for_rollout`, `verifying_deployment`,
`configuring_dns`, and `completed`. Report a failure with step/status `failed` and a non-empty
`failure_reason`. Report completion with step `completed` and status `running`.

Containerized registered-application deployments require a unique lowercase DNS
label. Athena generates `<subdomain>.amberd.ai`, stores that hostname with the
deployment, and includes a provider-neutral `dns` object in the dispatched
pipeline configuration. The container pipeline must create the DNS record during
the `configuring_dns` stage and only report `completed` after both the workload
and DNS are ready. GitHub Workflow deployments do not receive DNS configuration
and cannot report the `configuring_dns` stage. DNS-provider credentials and
provider-specific record management remain owned by the deployment pipeline.

### Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/metrics` | Fetch CPU / memory / GPU metrics for all tiers from Grafana |

### Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/logs/deployment` | Deployment logs from Grafana Loki |

`GET /api/logs/deployment` query parameters:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `subdomain` | Yes | Instance identifier (3-63 chars, lowercase alphanumeric + hyphens) |
| `deployment` | Yes | Application / workload name |
| `tier` | No | 1-based tier index (1–4); adds a tier label filter when `GRAFANA_LOKI_TIER_LABEL` is set |
| `from` | No | ISO8601 inclusive lower time bound (defaults to 15 min ago) |
| `to` | No | ISO8601 inclusive upper time bound (defaults to now) |
| `search` | No | Server-side filter: free text (`\|=`) plus `level:info`-style tokens (`\|~`) |
| `limit` | No | Max log lines per response (1–2000); enables cursor paging |
| `cursor` | No | Opaque continuation token from the previous `next_cursor` |
| `tail` | No | `true` = newest-first (backward) paging for live tail; `cursor` references the oldest line returned |

## Troubleshooting

### GitHub application version tags

The registration UI calls `github.repository` **Workflow Repository**. The optional
`github.code_repository` uses the same `owner/repository` format and saved GitHub
connection. Apply migration `0016_github_code_repository` to existing databases.

`GET /api/registered-applications/{application_id}/github-tags` (admin-only) returns
`{ application_id, repository, branches, tags, items }`. It reads up to 200 branches
and 200 tags from Code Repository when configured, otherwise Workflow Repository.
`items` is the de-duplicated combined compatibility list. An empty Code Repository
result does not fall back to a different repository. Access errors
are reported separately from an empty branch/tag list; the connection needs read
access to whichever repository supplies the refs.

The deployment wizard requires a branch or tag selection and submits it as
`version`. The backend maps that selected ref to the GitHub workflow's `branch`
input, overriding any older registered `branch` default. The workflow repository
and its configured `ref` are unchanged. Older API callers may omit `version` to
preserve their existing registered parameter behavior; template version numbers
are not Git refs.

- If you change `.env` values, restart the server.
- If you see import errors, confirm you are running from `athena/backend` and using `poetry run ...`.
