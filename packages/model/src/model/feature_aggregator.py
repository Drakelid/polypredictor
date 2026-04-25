"""
Aggregate multiple feature families into a unified input for the model.

The model architecture combines signals from social sentiment, event timing,
resolution risk, on-chain data, etc. This module defines a simple helper
function that merges these inputs into a single dictionary. In a production
system this would include feature normalization and conflict resolution;
here we simply overlay dictionaries, later entries overriding earlier ones on
key collisions.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


def aggregate_features(*feature_dicts: Mapping[str, Any]) -> Dict[str, Any]:
    """Combine multiple feature dictionaries.

    Parameters
    ----------
    *feature_dicts:
        Arbitrary number of mappings representing different feature families.

    Returns
    -------
    Dict[str, Any]
        A single dictionary containing the union of all keys. If the same
        feature appears in multiple inputs, the value from the rightmost
        dictionary is used.
    """
    aggregated: Dict[str, Any] = {}
    for d in feature_dicts:
        if not isinstance(d, Mapping):
            continue
        aggregated.update(d)
    return aggregated