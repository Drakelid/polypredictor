from __future__ import annotations

from api.ensemble_registry import load_ensemble_registry
from model import EnsembleRegistry


def test_load_ensemble_registry_reads_json_artifact(tmp_path) -> None:
    path = tmp_path / "ensemble.json"
    path.write_text(
        '{"models":{"threshold":{"market_type":"threshold","linear_means":{"p_base_logit":0.0,"market_mid_logit":0.0},"linear_scales":{"p_base_logit":1.0,"market_mid_logit":1.0},"booster_means":{},"booster_scales":{},"linear_intercept":0.0,"linear_weights":{"p_base_logit":1.0,"market_mid_logit":0.0},"stumps":[],"calibrator":{"upper_bounds":[1.0],"values":[0.5]},"market_mid_weight_cap":0.35}}}',
        encoding="utf-8",
    )

    registry = load_ensemble_registry(str(path))

    assert isinstance(registry, EnsembleRegistry)
    assert registry.model_for_type("threshold") is not None
