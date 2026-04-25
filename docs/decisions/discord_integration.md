# Decision: Discord Integration Timing

*Date: 2026‑04‑25*

## Context

The initial PRD proposes integrating Discord for sentiment signals and user feedback. Due to resource constraints and API rate limits, the team must decide whether to include Discord ingestion in v1 or defer it.

## Decision

Discord integration will be deferred to v1.1. Instead, sentiment ingestion in v1 will rely on RSS, Reddit, and other sources. A stub and decision note already exist in `docs/decisions/discord-v1_1.md` to capture this.

## Rationale

* Discord requires bot authorization, moderating channels, and handling a wide variety of data formats, increasing complexity.
* Existing sources provide sufficient coverage for early testing.
* Deferring allows the team to focus on core ingestion reliability and deliver v1 on schedule.

## Next Steps

* Develop a more detailed integration plan for v1.1, including which channels to monitor, rate limits, and data sanitization.
* Update documentation when the integration is ready.