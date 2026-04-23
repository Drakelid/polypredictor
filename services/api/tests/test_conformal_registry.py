from __future__ import annotations

from api.conformal_registry import load_conformal_registry
from model import SplitConformalRegistry


def test_load_conformal_registry_reads_json_artifact(tmp_path) -> None:
    path = tmp_path / "conformal.json"
    path.write_text(
        '{"coverage":0.8,"cells":{"threshold:1d_7d":{"key":"threshold:1d_7d","quantile":0.12,"sample_count":18}}}',
        encoding="utf-8",
    )

    registry = load_conformal_registry(str(path))

    assert isinstance(registry, SplitConformalRegistry)
    assert registry.cells["threshold:1d_7d"].quantile == 0.12
