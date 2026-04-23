"""Common I/O types for baselines."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..classifier import MarketFeatures
from ..types import BaselineSource


@dataclass(frozen=True)
class BaselineInputs:
    """Everything a baseline might need.

    Not every field is used by every baseline — each baseline asserts the
    ones it requires and returns a ``None`` probability if they're missing
    rather than making something up.
    """

    features: MarketFeatures
    asked_at: float                       # wall-clock epoch seconds (PIT anchor)

    # Market microstructure (for reflexive sanity-checks, not primary input).
    market_mid: float | None = None

    # External: spot + vol + calendar.
    spot: float | None = None
    # Trailing realized volatility as annualized decimal (e.g. 0.45 = 45%).
    realized_vol: float | None = None
    # ATM implied volatility as annualized decimal (e.g. 0.6 = 60%).
    implied_vol_atm: float | None = None
    # Vol at the strike — if None, we use ATM with a slight haircut to acknowledge
    # smile/skew uncertainty in downstream interval widening.
    implied_vol_strike: float | None = None
    # Risk-free rate as annualized decimal. Defaults to 0 for crypto.
    risk_free_rate: float = 0.0

    # Consensus / FedWatch inputs for discrete-event markets.
    consensus_point: float | None = None
    consensus_stddev: float | None = None
    fedwatch_prob: float | None = None

    # For long-tail binaries: prior from embedding match + historical frequency.
    embedding_prior: float | None = None
    historical_base_rate: float | None = None

    # Multi-outcome: raw un-normalized probabilities for the sibling markets
    # (including this one). The baseline will sum-constrain them.
    multi_outcome_raw: list[float] | None = None
    multi_outcome_self_index: int | None = None


@dataclass(frozen=True)
class BaselineOutput:
    """Baseline output handed to the ensemble / UI.

    ``probability`` is on [0, 1]. ``None`` means the baseline couldn't run
    (missing inputs); the pipeline downgrades to :data:`BaselineSource.MARKET_MID`.

    ``uncertainty_multiplier`` is applied to the downstream conformal band
    width — baselines with noisier inputs (cold-start markets, MISC fallback)
    push it > 1.0.

    ``reasons`` is a short list of strings explaining the calc; surfaced in
    the trust-badge tooltip on the UI.
    """

    probability: float | None
    source: BaselineSource
    uncertainty_multiplier: float = 1.0
    reasons: list[str] = field(default_factory=list)
