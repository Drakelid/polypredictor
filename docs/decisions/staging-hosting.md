# Staging Hosting Decision

Date: 2026-04-25

## Decision

Use Render for the first staging environment.

The staging topology is:

- `polypredictor-web-staging`: Next.js dashboard, Docker build from `apps/web/Dockerfile`
- `polypredictor-api-staging`: FastAPI API, Docker build from `services/api/Dockerfile`
- `polypredictor-ingest-discovery-staging`: Gamma discovery worker, Docker build from `services/ingest/Dockerfile`
- `polypredictor-ingest-clob-staging`: CLOB polling worker, same ingest image with a different command
- Render Postgres for user-scoped state
- Render Key Value for Redis-backed DLQ/shared runtime state
- External managed ClickHouse for time-series storage

`render.yaml` is the canonical staging blueprint. It intentionally leaves
ClickHouse and paid/provider API credentials as `sync: false` secrets, because
those should be provisioned manually per environment.

## Rationale

Render is a better fit than Fly.io for M0/M8 staging because the repository can
describe the app, API, worker processes, Postgres, and Redis-like key-value
store in one blueprint. That keeps the first staging target easy to recreate and
review in code.

The tradeoff is that ClickHouse remains external for now. That is acceptable for
staging because the app already expects ClickHouse connection settings from the
environment, and production will likely use a managed ClickHouse provider rather
than self-hosting it on the app platform.

## Deployment Flow

1. Create a Render Blueprint from `render.yaml`.
2. Fill the `sync: false` values in Render:
   - `CLICKHOUSE_HOST`
   - `CLICKHOUSE_USER`
   - `CLICKHOUSE_PASSWORD`
   - `NEXT_PUBLIC_API_BASE`
   - `API_BASE`
   - optional provider credentials such as CME, Glassnode, and Dune
3. Configure GitHub environment `staging`.
4. Add deploy-hook secrets:
   - `RENDER_DEPLOY_HOOK_API_STAGING`
   - `RENDER_DEPLOY_HOOK_WEB_STAGING`
   - `RENDER_DEPLOY_HOOK_INGEST_DISCOVERY_STAGING`
   - `RENDER_DEPLOY_HOOK_INGEST_CLOB_STAGING`
5. On a successful `main` CI run, `.github/workflows/deploy-staging.yml`
   triggers each configured Render deploy hook.

## Validation

After deploy:

- API health: `/healthz`
- Full status: `/v1/status`
- Web status page: `/status`
- Source telemetry: `/v1/source-health?include_timeseries=true`

The GitHub deploy workflow is safe before secrets exist: it reports missing
deploy hooks and skips those services.
