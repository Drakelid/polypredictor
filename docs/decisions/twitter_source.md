# Decision: X Data – Scraper vs Paid API

*Date: 2026‑04‑25*

## Context

The product roadmap (see §11.3) identifies an open question on whether to
integrate Twitter (now X) data via a paid API or through custom scraping. This
document captures the trade‑offs considered at this stage. No final decision
has been made; the implementation path remains deferred.

## Options

### Option 1: Use a paid API

**Pros:**

* Licensed access ensures compliance with X’s terms of service.
* Higher reliability and data quality (rate limits are clear, fewer captchas).
* Rich metadata and official engagement metrics (impressions, likes) are
  available.
* Less engineering effort on maintaining parsers and anti‑bot countermeasures.

**Cons:**

* Cost scales with usage and may be significant for high‑volume ingestion.
* Vendor lock‑in and dependence on API policy changes.
* Paid tiers may still omit certain endpoints (e.g. historical archives).

### Option 2: Build a scraper

**Pros:**

* Zero direct licensing cost beyond engineering time.
* Full control over what data is captured.
* Can be adapted to changes in platform UI faster than waiting for API updates.

**Cons:**

* Potential violation of X’s terms of service; legal risk must be assessed.
* Fragile: UI changes and anti‑bot measures can break the scraper.
* Requires ongoing maintenance and operational oversight.
* Limited access to engagement metrics not visible on the public web.

## Preliminary recommendation

Given the regulatory and operational risks of scraping, the paid API is
preferred if the budget allows. Scraping may be explored as a stopgap for
low‑volume non‑commercial use, but legal counsel should approve. Further
analysis of API pricing and expected call volumes is needed before committing.