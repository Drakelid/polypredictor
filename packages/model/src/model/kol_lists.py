"""
KOL (Key Opinion Leader) category helpers.

Polypredictor groups social accounts into “key opinion leader” lists on a per‑
market or per‑category basis. These lists are curated manually in production,
but test pipelines still need a place to look up and filter accounts. This
module exposes a minimal set of helpers for working with KOL category lists.

Because the demo environment does not persist state or integrate with a real
database, callers must supply a mapping from category identifiers to lists of
authors. Functions accept this mapping and operate on it directly. If no
mapping is provided, lookups return an empty result.

Functions:

* :func:`get_kols_for_category` – retrieve the list of KOLs for a category.
* :func:`filter_posts_by_category` – filter a sequence of posts to those from
  KOLs assigned to a category.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple


def get_kols_for_category(
    category: str, category_kol_map: Dict[str, Sequence[str]] | None = None
) -> List[str]:
    """Return the list of key opinion leaders for the given category.

    Parameters
    ----------
    category:
        A category identifier (e.g. a market tag or sector name).
    category_kol_map:
        Optional mapping from category strings to sequences of KOL identifiers.
        When omitted or when the category is absent, an empty list is
        returned.

    Returns
    -------
    List[str]
        The authors associated with the category, or an empty list.
    """
    if not category_kol_map:
        return []
    return list(category_kol_map.get(category, []))


def filter_posts_by_category(
    posts: Iterable[Tuple[str, str]],
    category: str,
    category_kol_map: Dict[str, Sequence[str]] | None = None,
) -> List[Tuple[str, str]]:
    """Filter posts down to those whose author is a KOL for the given category.

    Each post is represented as a tuple of ``(author, text)``. The
    ``category_kol_map`` behaves as in :func:`get_kols_for_category`.

    Parameters
    ----------
    posts:
        A sequence of tuples ``(author, text)``.
    category:
        The category to filter by.
    category_kol_map:
        Mapping from category to the KOL identifiers used to filter posts.

    Returns
    -------
    List[Tuple[str, str]]
        A list of posts whose author is in the category’s KOL list.
    """
    allowed = set(get_kols_for_category(category, category_kol_map))
    if not allowed:
        return []
    return [(author, text) for author, text in posts if author in allowed]