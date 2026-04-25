# Data Retention And Cold Archive

## Policy

Point-in-time market, quote, orderbook, trade, holder, smart-money, signal, and
feature rows are retained hot in ClickHouse for two years unless a table has an
explicitly longer retention window for audit or model-monitoring reasons.

ClickHouse TTL clauses enforce the hot-retention boundary. Before TTL removes
old partitions, operators run a cold-archive planning job and export the listed
partitions to object storage.

## Configuration

- `RETENTION_YEARS=2`
- `RETENTION_COLD_ARCHIVE_AFTER_YEARS=1`
- `RETENTION_COLD_ARCHIVE_URI=s3://...` or equivalent managed object-store URI

The archive URI is intentionally not committed. It is environment-specific and
should be configured in staging/production secrets.

## Procedure

1. Run `make retention-archive-plan`.
2. Review `artifacts/retention/archive-manifest.json`.
3. Export each listed `(table, partition_id)` to cold storage using the managed
   ClickHouse provider's supported export or backup operation.
4. Record the manifest location and object-store prefix in the incident/change
   log.
5. Let ClickHouse TTL remove hot partitions normally.

## Safety Rules

- Do not run manual `ALTER TABLE ... DROP PARTITION` for retention. TTL owns hot
  deletion.
- Do not archive a partition unless the manifest lists the table, partition, row
  count, and observed-time range.
- Keep `market_resolutions` append-only and outside the two-year TTL unless a
  separate legal retention decision changes it.
- Backtests must read hot ClickHouse first; cold archive restore is an explicit
  operator action, not an automatic query fallback.
