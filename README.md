# C2AI

C2AI (internally *Athena*) is Amberd's application management platform:
register an application once, deploy it into any tier as independently
tracked instances, and operate it — tier dashboards, logs, AI-assisted
troubleshooting, and cost tracking. One C2AI instance runs per customer,
potentially inside the customer's own data center.

| Directory | Contents |
|---|---|
| [`backend/`](backend/README.md) | FastAPI service (`c2ai` package), SQL migrations, tests |
| [`frontend/`](frontend/README.md) | React + Vite UI |

## Quick start (local)

Prerequisites: Python 3.11+, Node 20.19+ (or 22.12+), PostgreSQL 14+.

No database server handy? `scripts/local-db.sh start` runs a project-local
PostgreSQL on port 55432 (data in `.local/`, git-ignored); use
`DATABASE_URL=postgresql+asyncpg://c2ai@127.0.0.1:55432/c2ai`.

```bash
# Backend
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e . --no-deps   # pinned, tested versions
cp .env.example .env                       # set DATABASE_URL at minimum
python -m c2ai.auth.generate_secret --write
python -m c2ai.db.init_db --yes            # fresh DB + migrations + admin user
python main.py                             # API on http://localhost:8007

# Frontend (second terminal)
cd frontend
npm ci
npm run dev                                # UI on http://localhost:5173
```

Sign in as `admin@amberd.ai` with the password `admin@amberd.ai`. The API
creates that account on any database with no users; add real accounts, then
delete it.

For a single-process deployment, `npm run build` in `frontend/` and start the
backend: it serves `frontend/dist` on the same origin as the API.

## Tests

```bash
cd backend && pytest && ruff check c2ai tests scripts main.py
cd frontend && npm test && npm run build
```

## Product requirements

The backend implements the Athena PRDs: *Application Registration &
Deployment* (with the final EPIC 3-8 scope: amberd.ai-only DNS for container
apps, managed container secrets, and version-only upgrade/terminate for both
application types), *Application Troubleshooting*, and *Cost & Financial
Tracking*. See [backend/README.md](backend/README.md) for how each maps to
endpoints and pipelines.
