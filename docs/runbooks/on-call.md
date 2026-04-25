# On-Call Runbook

## Scope

This runbook covers staging and beta production incidents for API availability,
web availability, ingestion failures, model disable events, drift alerts, and
provider outages.

## Primary Checks

Use these in order:

1. Web status page: `/status`
2. API load-balancer health: `/healthz`
3. Full API status: `/v1/status`
4. Source health: `/v1/source-health?include_timeseries=true`
5. Drift monitor: `/v1/drift-monitor`

## Severity

- `SEV-1`: API or dashboard down for all users, credential leak suspected, or
  model output known to be invalid across multiple market types.
- `SEV-2`: Ingestion degraded for a P0/P1 source, source failure rate above 1%,
  model auto-disable triggered for a market type, or journal auto-sync broken.
- `SEV-3`: Single provider intermittent failures, isolated UI regression, or
  delayed non-critical worker.

## Triage

1. Check `/v1/status` for component state.
2. If Postgres is down, disable user-writing features at the edge if available
   and avoid restarts that can amplify connection storms.
3. If ClickHouse is down, expect market/model reads to degrade. Pause ingest
   workers before replaying DLQ jobs.
4. If a source failure rate exceeds 1%, check provider status/rate limits and
   reduce cadence before increasing retries.
5. If a model type auto-disables, leave fallback-to-market enabled until a
   retrain or data fix is verified.

## Common Actions

- Redeploy staging: run the `Deploy staging` workflow manually.
- Rebuild drift metrics: `make drift-monitor`
- Rebuild signal ablation: `make signal-ablation`
- Rebuild privacy aggregates: `make dp-aggregates`
- Inspect source health: call `/v1/source-health?lookback_hours=24`
- Inspect feature drift: call `/v1/drift-monitor`

## Escalation

Escalate immediately for:

- Any evidence that encrypted CLOB credentials were exposed.
- Any API bug that writes journal calls to the wrong user.
- Any model path that serves post-resolution data as if it were point-in-time.
- Any provider terms or legal notice related to data ingestion.

## Recovery Criteria

An incident is resolved when:

- `/v1/status` is `operational` or the degraded component is documented.
- The affected source has a trailing failure rate below 1% or a mitigation is in
  place.
- User-facing fallbacks are confirmed: market-implied prior for disabled models,
  no stale alert spam, and journal writes either working or visibly disabled.
- A short note is added to the incident log with cause, mitigation, and follow-up.
