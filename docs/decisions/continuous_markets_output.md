# Decision: Continuous Markets — Single Probability vs Distribution Output

*Date: 2026‑04‑27*

## Decision

**Expose both**: a single `model_prob` scalar *and* an optional
`distribution_samples` percentile grid on the detail endpoint.

## Rationale

The PRD §6.1 classifies `range` and `multi_outcome` markets as types where
the underlying prediction is inherently distributional (e.g. "will BTC be
between $80k and $90k?"). A single probability suffices for the binary
framing of the displayed market, but the *distribution* is informative for:

1. **Calibration** — the model's uncertainty about where the variable lands
   is wider than for a simple binary event; displaying bands without
   distribution samples hides this.

2. **Ensemble inputs** — sibling-market consistency checks and the softmax
   baseline both operate on distributions internally; surfacing the grid
   lets the UI (and future API callers) validate model output.

3. **Backward compatibility** — callers only reading `model_prob` are
   unaffected; `distribution_samples` is an additive field.

## Format

`distribution_samples` is a list of `[percentile, value]` pairs at 25 evenly
spaced quantiles (0.02 → 0.98), compatible with `percentile_grid()` in
`packages/model/src/model/distribution_utils.py`.

For `threshold` and `long_tail_binary` markets the field is `null` — those
types produce a single probability naturally.

For `range` markets the distribution is a Gaussian centered on the baseline
mid-point with σ derived from the conformal band width.

For `multi_outcome` markets the distribution is the softmax vector
interpolated onto the grid.

## Implementation

* `distribution_utils.py` — add `percentile_grid(samples, n_points=25)`
* `markets.py` — compute `distribution_samples` for `range` /
  `multi_outcome`, attach to `MarketDetailRow`
* `main.py` — include `distribution_samples` in `/v1/markets/{id}/model`
* `api.ts` — add `distribution_samples?: [number, number][] | null` to
  `MarketModel` type
