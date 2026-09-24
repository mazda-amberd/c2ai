# C2AI backend (Athena service)

FastAPI service behind the C2AI UI: application registration and deployment,
tier dashboards (Grafana/Prometheus), deployment logs (Loki), AI
troubleshooting, and cost tracking. Python 3.11+, PostgreSQL 14+ (with
`pgcrypto` and `uuid-ossp`).

## Layout

```
backend/
  main.py                 # python main.py  -> uvicorn on APP_HOST:APP_PORT
  c2ai/
    app.py                # create_app(): routers, CORS, error handlers, SPA
    api/                  # HTTP routes (thin: validate, call services/crud, map)
    auth/                 # JWT + cookie helpers, auth dependencies
    clients/              # GitHub, Grafana, container registry, secret broker
    constants/            # PromQL catalogues, tiers, lifecycle enums
    core/                 # exceptions, handlers, logging, SPA serving, bg tasks
    crud/                 # database access
    db/                   # engine/session, migration runner, dev bootstrap
    llm/                  # vLLM chat model + troubleshooting prompt
    models/               # SQLAlchemy ORM models
    schemas/              # Pydantic request/response contracts
    services/             # domain logic (deploy payloads, metrics, costs, ...)
  migrations/NNNN_*.sql   # forward-only SQL migrations
  scripts/                # operational helpers
  tests/                  # pytest suite (no database or network needed)
```

## Setup

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e . --no-deps   # pinned, tested versions
cp .env.example .env            # then fill in DATABASE_URL, secrets, Grafana, GitHub
python -m c2ai.auth.generate_secret --write   # sets ATHENA_AUTH_SECRET in .env
```

### Database

A fresh local database (drops and recreates the database in `DATABASE_URL`,
applies every migration, seeds `admin` / `$C2AI_ADMIN_PASSWORD`):

```bash
python -m c2ai.db.init_db --yes
```

Any existing database (staging, production, or after pulling new code):

```bash
python -m c2ai.db.migrate
```

A database that already matches the schema but predates the migration table
is stamped once with `python -m c2ai.db.migrate --stamp`. Schema changes are
new numbered files in `migrations/`; never edit an applied migration.

### Run

```bash
python main.py                  # http://localhost:8007
```

If `../frontend/dist` exists (run `npm run build` in `frontend/`), the UI is
served from the same origin; otherwise run the Vite dev server (port 5173),
which calls the API on port 8007.

### Test and lint

```bash
pytest                          # ~670 tests, fully mocked
ruff check c2ai tests scripts main.py
```

## Configuration

Every variable, with defaults, is listed in [`.env.example`](.env.example).
The ones that must be set in any real environment:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` (`LOCAL_DATABASE_URL` still accepted) |
| `ATHENA_AUTH_SECRET` | JWT signing secret |
| `ATHENA_CREDENTIAL_ENCRYPTION_KEY` | pgcrypto key for stored GitHub/registry/LLM credentials |
| `GITHUB_PAT` | Token for Amberd's predefined workflows (Actions read + write) |
| `GRAFANA_API_URL`, `GRAFANA_API_TOKEN` | Grafana `/api/ds/query` endpoint and token |
| `GRAFANA_LOKI_DATASOURCE_UID` | Loki datasource for logs and troubleshooting |
| `DEPLOYMENT_CALLBACK_TOKEN` | Shared secret for pipeline progress callbacks |
| `VLLM_ENDPOINT` | OpenAI-compatible endpoint for AI troubleshooting |

## Authentication and roles

`POST /auth/login` issues a JWT (also set as an HttpOnly cookie). Sessions
last `ATHENA_TOKEN_TTL_SECONDS` (default 7 days); a client may ask for a
different lifetime but never more than `ATHENA_TOKEN_MAX_TTL_SECONDS`
(default 30 days).

Every authenticated request re-reads the user from the database, so deleting
a user or removing admin rights takes effect immediately. Endpoints return
**401** when the session is missing/invalid (the UI signs out) and **403**
when a signed-in user lacks admin rights. Admin rights come from the stored
`metadata.user_type == "Admin"`.

| Method | Path | Access |
|---|---|---|
| `POST` | `/auth/login`, `/auth/logout` | public |
| `GET` | `/auth/whoami` | signed in |
| `GET/POST/PATCH/DELETE` | `/users/` | admin |
| `PATCH` | `/users/update_password` | signed in (own password) |
| `POST` | `/users/reset_password/{user}` | admin |

The bootstrap `admin` account sees every user; other admins see the users
they created.

## Registered applications (PRD: Registration & Deployment, EPICs 3-8)

Registration is global; deployment is always tier-scoped. All routes below
require an admin.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/registered-applications` | Catalog: search, type/status filters, optional `tier` facet, sort, paging |
| `POST` | `/api/registered-applications/github` | Register a GitHub Workflow application (version 1) |
| `POST` | `/api/registered-applications/container` | Register a Containerized application (version 1) |
| `GET` | `/api/registered-applications/{id}` | Current version (credentials never returned) |
| `DELETE` | `/api/registered-applications/{id}` | Blocked while instances or managed secrets exist; the error names each remaining instance and tier |
| `GET` | `/api/registered-applications/{id}/github-tags` | Branches and tags for the version picker |
| `GET` | `/api/registered-applications/{id}/image-tags` | Registry tags (Docker Hub, GHCR, ECR, private v2) |
| `POST` | `/api/registered-applications/{id}/deployments` | Deploy a GitHub Workflow app into `tier` |
| `POST` | `/api/registered-applications/{id}/tiers/{tier}/deployments` | Deploy a container app into the path tier |
| `GET` | `/api/registered-applications/deployments` | Instance history (filters: tier, application, instance, status) |
| `GET` | `/api/registered-applications/deployments/{id}` | Progress, GitHub jobs/steps, events |
| `POST` | `.../deployments/{id}/upgrade` | `{"version": "..."}` — type-specific in-place upgrade |
| `POST` | `.../deployments/{id}/rollback` | Return to the previous version (see below) |
| `POST` | `.../deployments/{id}/terminate` | `{"confirmation": "<instance name>"}` |
| `POST` | `.../deployments/{id}/progress` | Pipeline callback (`X-Athena-Deployment-Token`) |
| `GET/POST/PATCH/DELETE` | `/api/registered-applications/{id}/secrets[/{secret_id}]` | Managed container secrets (write-only values) |
| `GET/POST` | `/api/github-connections`, `/api/github-connections/validate` | Reusable GitHub connections (tokens encrypted, never returned) |
| `GET` | `/api/registered-applications/llm-models[/pricing]` | Model suggestions and pricing availability |

**Deploying.** GitHub Workflow apps dispatch the registered workflow
(`workflow_dispatch` inputs or `repository_dispatch` `client_payload`) using
the saved GitHub connection; the chosen branch/tag is sent as the `branch`
input. Container apps dispatch the container pipeline with the registered
template, the chosen image tag, `<instance_name>.amberd.ai` as the hostname,
and the application's managed secrets as provider *references* (name,
environment variable, reference). Decrypted registry/LLM credentials are used
only for the outbound calls and are never stored in deployment history.

**Upgrading.** Container upgrades validate the tag in the registry and run
the container update workflow; GitHub apps run Amberd's predefined
`ada-update.yaml` with the version as `branch`. The configuration the
instance ran before the upgrade is kept.

**Rolling back.** After an upgrade, rollback dispatches the in-place upgrade
pipeline back to the previous version and swaps the stored configurations
(so rolling back twice rolls forward). An instance that was never upgraded
is redeployed from its stored configuration with the same credentials as the
original deployment.

**Progress callbacks** (from the pipeline):

```json
{"current_step": "waiting_for_rollout", "status": "deploying", "message": "Waiting for readiness."}
```

Steps: `validating_configuration`, `creating_namespace`, `applying_resources`,
`waiting_for_rollout`, `verifying_deployment`, `configuring_dns` (container
deployments/terminations only), `completed`, `failed`. Report failure with
step and status `failed` plus `failure_reason`; completion with step
`completed` and status `running` (or `terminated` for terminations). Upgrades
keep status `updating` until they complete. When a callback never arrives,
the GitHub run's final conclusion is applied instead.

## Legacy ADA pipeline

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/deploy`, `/api/deploy/update`, `/api/deploy/move-tier`, `/api/deploy/terminate` | Dispatch Amberd's `ada-*` workflows |
| `GET` | `/api/pipeline/active`, `/api/pipeline/status`, `/api/pipeline/history` | Live and historical status (registered deployments included in `active`) |
| `POST` | `/api/pipeline/cancel` | Cancel your own run |
| `GET` | `/api/github/branches`, `/api/github/tags` | Refs in `GITHUB_REPO_OWNER/<repo>` |

One operation runs per subdomain at a time. If GitHub rejects a dispatch the
run is released immediately, so it does not block the subdomain.

## Metrics, logs, troubleshooting

* `GET /api/metrics` — tier dashboard (per-instance CPU/memory/GPU; refreshes
  the `application_instances` snapshot the legacy guards use).
* `GET /api/v2/metrics?level=cluster|tier|application`,
  `GET /api/v2/metrics/application?application=<ns>/<deployment>` — windowed
  metrics (`range=1m..2d` or `from`/`to`), degraded per metric instead of
  failing whole.
* `GET /api/logs/deployment` — Loki logs with search (`level:error` tokens),
  forward paging, and live tail (`tail=true`).
* `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/report.pdf` — AI
  troubleshooting report (logs + Kubernetes events + selected PromQL), owned
  by the requesting user, kept 15 minutes.

Cluster model: workloads are Kubernetes Deployments labelled with
`label_tier`; GPU comes from `ray_node_gpus_utilization`. Tier label regexes,
Ray cluster names, and unit caps are configurable (see `.env.example` and
`c2ai/constants/prometheus.py`). `python scripts/check_grafana_queries.py`
runs the dashboard queries against a live Grafana.

## Cost tracking

`GET /api/financial/costs` (signed in) returns stored costs for the cluster or
one tier, filterable by date range, cost type (`public_api`, `private_llm`,
`both`), and namespace. Values are decimal strings in USD and are never
recalculated with newer rates.

An in-process scheduler (`ATHENA_FINANCIAL_INGESTION_ENABLED`, every
`ATHENA_FINANCIAL_POLL_SECONDS`) reads the LLM gateway counters from
Prometheus for the exact interval since its checkpoint, attributing each call
to a namespace through the caller pod IP:

* **Private LLM** (`provider="vllm"`): request-seconds x the effective-dated
  GPU hourly rate (seeded at $2.50) — an estimate, as v1 allows.
* **Public API** (every other provider): input and output tokens x that
  model's published per-million rates (`financial_rates`, seeded by
  migrations 0017/0019). Usage of an unpriced model is logged and skipped.

A PostgreSQL advisory lock ensures one replica ingests at a time. Rates are
effective-dated: to change a price, close the current row and insert a new one.
