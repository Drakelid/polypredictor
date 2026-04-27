# Decision: X Data – Scraper vs Paid API

*Date: 2026‑04‑25 · Decided: 2026‑04‑27*

## Decision

**Use the Twitter/X Basic API** at $100/month.

The Basic tier provides the [filtered-stream endpoint](https://developer.twitter.com/en/docs/twitter-api/tweets/filtered-stream/introduction)
which delivers a real-time stream of tweets matching operator-defined rules.
This is exactly the access pattern we need for KOL-list ingestion: a small
set of tracked accounts and hashtags per market category, delivered as a
push stream with no polling cost.

## Rationale

1. **Compliance** — Scraping would breach X's Terms of Service and create
   unacceptable legal exposure for a commercial product. No scraping.

2. **Reliability** — The Basic API has well-defined rate limits that
   integrate with our existing token-bucket infrastructure. A scraper
   needs brittle HTML/JS parsing that breaks on every UI change.

3. **Engagement metrics** — The v2 API response payload includes
   `public_metrics` (impression_count, retweet_count, like_count) which
   are used as the reach proxy in `structured_sentiment.reach_adjusted_volume`.
   These are unavailable to scrapers.

4. **Cost is bounded** — At $100/month the filtered-stream endpoint provides
   500,000 tweet reads. Our tracked KOL set (~200 accounts across crypto /
   finance categories) generates well under that cap.

## Implementation scope

* `services/ingest/src/ingest/workers/x_ingest.py` — filtered-stream
  worker, reconnects with backoff, applies `kol_loader` index, writes to
  `external_events` in the same schema used by `reddit_ingest`.
* Config file: `services/ingest/data/x_stream_rules.example.json` —
  documents the `add_rules` payload format for the Twitter/X v2 API.
* Settings: `X_BEARER_TOKEN`, `X_FILTERED_STREAM_RULES_FILE`.
* The same `external_events` downstream path (social features, KOL
  credibility, decay weights) applies unchanged.

## Out of scope for v1

* Historical tweet archive (Academic Research tier required — deferred).
* Quote-tweet / thread enrichment — stream-only, future work.
* Sentiment from replies — deferred to v1.1 once volume data confirms value.