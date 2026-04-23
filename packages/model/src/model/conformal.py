"""Split conformal intervals for binary probabilities.

The registry stores absolute-residual quantiles conditioned on a Mondrian key:

* market type
* time-to-resolution bucket

The serving path can then widen intervals by the baseline uncertainty
multiplier and a placeholder resolution-risk multiplier.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .types import MarketType


@dataclass(frozen=True)
class ConformalSample:
    market_type: MarketType
    time_to_resolution_s: float | None
    predicted_prob: float
    outcome: int
    asked_at: float


@dataclass(frozen=True)
class PurgedFold:
    train_indices: list[int]
    calibration_indices: list[int]


@dataclass(frozen=True)
class ConformalCell:
    key: str
    quantile: float
    sample_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "quantile": self.quantile,
            "sample_count": self.sample_count,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ConformalCell:
        return cls(
            key=str(payload["key"]),
            quantile=float(payload["quantile"]),
            sample_count=int(payload["sample_count"]),
        )


@dataclass(frozen=True)
class SplitConformalRegistry:
    coverage: float
    cells: dict[str, ConformalCell]

    def cell_for(
        self,
        *,
        market_type: MarketType,
        time_to_resolution_s: float | None,
    ) -> ConformalCell | None:
        bucket = ttr_bucket(time_to_resolution_s)
        keys = [
            mondrian_key(market_type, bucket),
            mondrian_key(market_type, "any"),
            mondrian_key(None, "any"),
        ]
        for key in keys:
            cell = self.cells.get(key)
            if cell is not None:
                return cell
        return None

    def interval(
        self,
        *,
        predicted_prob: float,
        market_type: MarketType,
        time_to_resolution_s: float | None,
        uncertainty_multiplier: float = 1.0,
        resolution_risk_multiplier: float = 1.0,
    ) -> tuple[float, float] | None:
        cell = self.cell_for(
            market_type=market_type,
            time_to_resolution_s=time_to_resolution_s,
        )
        if cell is None:
            return None
        width = float(cell.quantile) * max(uncertainty_multiplier, 0.0) * max(
            resolution_risk_multiplier, 0.0
        )
        p = _clip_probability(predicted_prob)
        return max(0.0, p - width), min(1.0, p + width)

    def to_dict(self) -> dict[str, object]:
        return {
            "coverage": self.coverage,
            "cells": {
                key: cell.to_dict()
                for key, cell in sorted(self.cells.items(), key=lambda item: item[0])
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SplitConformalRegistry:
        raw_cells = payload.get("cells", {})
        cells: dict[str, ConformalCell] = {}
        if isinstance(raw_cells, dict):
            for key, value in raw_cells.items():
                if not isinstance(value, dict):
                    continue
                cells[str(key)] = ConformalCell.from_dict(value)
        return cls(
            coverage=float(payload.get("coverage", 0.8)),
            cells=cells,
        )

    @classmethod
    def from_json(cls, payload: str) -> SplitConformalRegistry:
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            return cls(coverage=0.8, cells={})
        return cls.from_dict(parsed)


def ttr_bucket(time_to_resolution_s: float | None) -> str:
    if time_to_resolution_s is None:
        return "unknown"
    if time_to_resolution_s < 6 * 3600:
        return "lt_6h"
    if time_to_resolution_s < 24 * 3600:
        return "6h_24h"
    if time_to_resolution_s < 7 * 24 * 3600:
        return "1d_7d"
    return "gt_7d"


def mondrian_key(market_type: MarketType | None, bucket: str) -> str:
    mt = market_type.value if market_type is not None else "any"
    return f"{mt}:{bucket}"


def fit_split_conformal(
    calibration_samples: list[ConformalSample],
    *,
    coverage: float = 0.8,
) -> SplitConformalRegistry:
    grouped: dict[str, list[float]] = {}
    by_type: dict[str, list[float]] = {}
    global_scores: list[float] = []

    for sample in calibration_samples:
        residual = abs(_clip_probability(sample.predicted_prob) - float(sample.outcome))
        bucket = ttr_bucket(sample.time_to_resolution_s)
        exact_key = mondrian_key(sample.market_type, bucket)
        type_key = mondrian_key(sample.market_type, "any")
        grouped.setdefault(exact_key, []).append(residual)
        by_type.setdefault(type_key, []).append(residual)
        global_scores.append(residual)

    cells: dict[str, ConformalCell] = {}
    for key, scores in grouped.items():
        cells[key] = ConformalCell(
            key=key,
            quantile=_conformal_quantile(scores, coverage),
            sample_count=len(scores),
        )
    for key, scores in by_type.items():
        cells[key] = ConformalCell(
            key=key,
            quantile=_conformal_quantile(scores, coverage),
            sample_count=len(scores),
        )
    if global_scores:
        global_key = mondrian_key(None, "any")
        cells[global_key] = ConformalCell(
            key=global_key,
            quantile=_conformal_quantile(global_scores, coverage),
            sample_count=len(global_scores),
        )
    return SplitConformalRegistry(coverage=coverage, cells=cells)


def purged_embargo_splits(
    samples: list[ConformalSample],
    *,
    n_splits: int = 5,
    purge_window_s: float = 0.0,
    embargo_s: float = 0.0,
) -> list[PurgedFold]:
    if not samples:
        return []
    ordered = sorted(enumerate(samples), key=lambda item: item[1].asked_at)
    ordered_indices = [idx for idx, _ in ordered]
    ordered_times = [sample.asked_at for _, sample in ordered]
    n = len(samples)
    n_splits = max(1, min(n_splits, n))
    fold_sizes = [n // n_splits] * n_splits
    for idx in range(n % n_splits):
        fold_sizes[idx] += 1

    folds: list[PurgedFold] = []
    start = 0
    for fold_size in fold_sizes:
        end = start + fold_size
        calibration_positions = list(range(start, end))
        calibration_indices = [ordered_indices[pos] for pos in calibration_positions]
        calibration_start = ordered_times[start]
        calibration_end = ordered_times[end - 1]
        train_indices: list[int] = []
        for pos, (original_idx, sample) in enumerate(ordered):
            if start <= pos < end:
                continue
            if sample.asked_at >= calibration_start - purge_window_s and sample.asked_at <= calibration_end + embargo_s:
                continue
            train_indices.append(original_idx)
        folds.append(PurgedFold(train_indices=train_indices, calibration_indices=calibration_indices))
        start = end
    return folds


def fit_split_conformal_from_folds(
    samples: list[ConformalSample],
    *,
    coverage: float = 0.8,
    n_splits: int = 5,
    purge_window_s: float = 0.0,
    embargo_s: float = 0.0,
) -> SplitConformalRegistry:
    folds = purged_embargo_splits(
        samples,
        n_splits=n_splits,
        purge_window_s=purge_window_s,
        embargo_s=embargo_s,
    )
    calibration_union: list[ConformalSample] = []
    seen: set[int] = set()
    for fold in folds:
        for idx in fold.calibration_indices:
            if idx in seen:
                continue
            seen.add(idx)
            calibration_union.append(samples[idx])
    return fit_split_conformal(calibration_union, coverage=coverage)


def _conformal_quantile(scores: list[float], coverage: float) -> float:
    if not scores:
        return 0.5
    ordered = sorted(float(score) for score in scores)
    rank = math.ceil((len(ordered) + 1) * coverage) - 1
    rank = min(max(rank, 0), len(ordered) - 1)
    return ordered[rank]


def _clip_probability(probability: float) -> float:
    return min(max(float(probability), 1e-6), 1.0 - 1e-6)
