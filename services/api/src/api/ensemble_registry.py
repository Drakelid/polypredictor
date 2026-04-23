"""Artifact loader for per-type ensemble registries."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from model import EnsembleRegistry


@lru_cache(maxsize=4)
def _load_cached_registry(resolved_path: str, mtime_ns: int) -> EnsembleRegistry:
    del mtime_ns
    payload = Path(resolved_path).read_text(encoding="utf-8")
    return EnsembleRegistry.from_json(payload)


def load_ensemble_registry(path: str | None) -> EnsembleRegistry | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.is_file():
        return None
    stat = file_path.stat()
    return _load_cached_registry(str(file_path.resolve()), stat.st_mtime_ns)
