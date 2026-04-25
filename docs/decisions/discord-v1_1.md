# Discord Deferred To v1.1

Date: 2026-04-24

Status: accepted

## Decision

Discord ingestion is explicitly deferred from v1 to v1.1.

## Why

- Per-server auth and token handling add support and security overhead that is not justified before the public RSS / social pipeline is stable.
- The v1 PRD already classifies Discord as `P2`, behind Polymarket-native signals, news RSS, event-time data, and the X/Twitter path.
- The existing `external_events` schema is already sufficient for Discord messages once the source decision is revisited.

## v1 Stub Contract

- No Discord worker runs in v1.
- Any future Discord ingest should write immutable rows into `external_events` with:
  - `event_kind='social'`
  - `source='discord:<server_or_channel>'`
  - `source_id` set to the native Discord message id
  - `source_uri` set to the canonical message URL when available
- Message edits / deletions must land as new rows, not updates.

## Revisit Criteria

- X/Twitter path chosen and stable
- RSS/news ingest live
- A concrete auth model is selected for private-server access
