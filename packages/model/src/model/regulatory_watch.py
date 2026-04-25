"""
Helpers for monitoring regulatory news.

Prediction markets operate in a dynamic legal landscape. To stay compliant,
the system must track relevant announcements from regulators and lawmakers.
This module provides a minimal helper function that filters a list of news
headlines or articles for keywords related to prediction-market regulation.
In production this would hook into a news API or RSS feed and maintain
state between runs. Here it simply performs substring matching on supplied
strings.

Functions:

* :func:`filter_regulatory_news` – filter a list of news items by a list of
  keywords.
"""

from __future__ import annotations

from typing import Iterable, List


def filter_regulatory_news(
    news_items: Iterable[str], keywords: Iterable[str] | None = None
) -> List[str]:
    """Return news items containing any of the specified keywords.

    Parameters
    ----------
    news_items:
        An iterable of news headlines or article snippets.
    keywords:
        Keywords or phrases to look for. If omitted, defaults to a
        small set of phrases commonly associated with prediction-market
        regulation (e.g. ``["prediction market", "gambling", "CFTC", "SEC"]``).

    Returns
    -------
    List[str]
        A list of news items that match at least one keyword. Matching is
        case-insensitive.
    """
    if keywords is None:
        keywords = ["prediction market", "gambling", "CFTC", "SEC", "FCA", "regulation"]
    kw = [k.lower() for k in keywords]
    results: List[str] = []
    for item in news_items:
        text = str(item).lower()
        if any(word in text for word in kw):
            results.append(item)
    return results