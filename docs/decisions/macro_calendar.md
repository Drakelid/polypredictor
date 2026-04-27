# Decision: Macro Calendar Sources — Live RSS + Curated File-Backed Bootstrap

*Date: 2026-04-25*

## Context

PRD §6.3 / M4.3 calls for a macro-calendar layer covering FOMC press
releases, CPI, NFP, ETF deadlines, and protocol unlocks. The original
implementation was entirely file-backed via
`services/ingest/data/scheduled_events.example.json`, with the inline
note that "live official feeds still need to replace the file-backed
bootstrap".

There is no single public API that covers all five categories cleanly,
so we split the responsibility along the natural axis of *what each
upstream actually publishes well*.

## Decision

Two complementary ingestion paths, both writing into `external_events`
with `metadata.scheduled = true` so `api.event_time` picks them up as
catalysts and `api.external_events` excludes them from the general feed
by default:

### 1. Live RSS for official US macro releases

Wired through the existing RSS worker
(`services/ingest/src/ingest/workers/rss_ingest.py`) by extending
`FeedSpec` with an optional `scheduled: bool = False` flag. When set,
every row from that feed is stamped with `metadata.scheduled = true`
without changing the worker's overall shape.

Three feeds are pinned in the example config:

* `https://www.federalreserve.gov/feeds/press_monetary.xml` — Federal
  Reserve press releases on monetary policy. Each FOMC statement lands
  here as soon as it's published.
* `https://www.bls.gov/feed/cpi.rss` — Bureau of Labor Statistics CPI
  release feed.
* `https://www.bls.gov/feed/empsit.rss` — BLS Employment Situation
  release feed (covers Non-Farm Payrolls).

These are the official sources, fully public, structured as RSS, and
require no API key. They publish *the actual release announcement*
on the same minute the embargo lifts — exactly when our event-time
layer needs to know.

### 2. Curated file-backed bootstrap for forward-looking schedule + non-US catalysts

`services/ingest/src/ingest/workers/scheduled_events_ingest.py` keeps
reading `scheduled_events.example.json`. This path is the right place
for:

* **Forward-looking schedule.** A future-dated FOMC meeting can sit in
  the JSON file as a placeholder row with `consensus_value` populated
  from a manual cache, then get reconciled with the actual press-release
  RSS row when the embargo lifts.
* **ETF deadlines.** SEC filings around ETF approvals are case-specific;
  no clean public API. Operators add these manually.
* **Protocol unlocks.** TokenUnlocks-style data; no clean public source
  with the depth we need. Manually curated.

## Why not a single live calendar source?

* FRED's `release/dates` endpoint requires an API key (and is itself a
  scheduling layer, not the release event). We already use FRED for the
  numeric series themselves; pulling another endpoint via API key for
  schedule was tested and rejected as added surface for low marginal
  signal vs. the press-release RSS that's already arriving.
* Trading Economics / Investing.com / Forex Factory all have economic
  calendars but require paid plans to use programmatically.
* Scraping HTML schedule pages (BLS, Fed) is fragile and adds a
  parser-maintenance burden disproportionate to the value.

## Consequences

* Operators only need to bootstrap the JSON file with non-US catalysts
  + forward-looking placeholders. The recurring US macro releases
  (FOMC / CPI / NFP) stream in automatically.
* `api.event_time` correctly receives both paths via the same
  `metadata.scheduled = true` contract; downstream features (pre-event
  IV run-up, post-event drift) work uniformly.
* If a release embargo lifts at an unexpected time, the RSS row's
  `event_time` reflects the published timestamp — not when our worker
  ran — so PIT correctness is preserved.
