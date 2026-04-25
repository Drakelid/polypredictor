"""
Heuristic structured sentiment features for news and social signals.

This module implements simple, deterministic functions to compute a handful of
structured features described in the product requirements document (§6.3). The
true system is expected to rely on richer context (e.g. semantic embeddings,
per‑user historical performance, and real impression counts) and is out of
scope for this stub. Instead, we provide reasonable heuristics so that the
pipeline can be exercised end‑to‑end during development and testing.

Available functions:

* :func:`novelty_score` – quantifies how dissimilar a new headline or post is
  relative to recent history. A score of 1.0 means completely novel; a score
  near 0.0 means the content is very similar to recent messages.

* :func:`credibility_weight` – looks up a per‑author credibility weight. In
  production this would depend on a rolling measure of how often the poster’s
  signals were predictive of market edges; here it defaults to 1.0 unless a
  supplied mapping provides a custom weight.

* :func:`reach_adjusted_volume` – computes a proxy for the amount of “eyeballs”
  a set of messages receive. Real implementations would use platform‐specific
  impression counts or follower counts; this stub simply sums the provided
  impression values.

* :func:`sentiment_dispersion` – returns the population standard deviation of
  sentiment scores across messages. Higher dispersion indicates disagreement
  among sources; low dispersion indicates consensus. When fewer than two
  scores are present, dispersion is zero by definition.

* :func:`tone_shift` – compares the current sentiment level to a rolling
  baseline. A positive value means sentiment has become more positive than
  usual; a negative value indicates a downturn.

These heuristics can be extended or swapped out as more data becomes available.
"""

from __future__ import annotations

import statistics
from typing import Dict, Iterable, List, Tuple


def novelty_score(history: Iterable[str], new_message: str) -> float:
    """Return a novelty score in [0, 1] for ``new_message`` relative to a history.

    The novelty score is defined as 1 minus the maximum Jaccard similarity
    between the set of lowercased whitespace‑separated tokens in ``new_message``
    and each message in ``history``. If ``history`` is empty, the score is
    1.0 (completely novel). The Jaccard similarity between two sets ``A`` and
    ``B`` is ``|A ∩ B| / |A ∪ B|``.

    Parameters
    ----------
    history:
        An iterable of prior messages (e.g. tweets or news headlines). These
        need not be unique; duplicates do not change the result.
    new_message:
        The message to evaluate for novelty.

    Returns
    -------
    float
        A score between 0 and 1 where higher values indicate greater
        dissimilarity from the history.
    """
    new_set = set(new_message.lower().split())
    # If there is no history to compare against, the message is fully novel.
    if not new_set:
        return 0.0  # empty messages have zero novelty
    try:
        history_iter = iter(history)
    except TypeError:
        raise TypeError("history must be iterable")
    max_sim = 0.0
    # Compute the maximum Jaccard similarity against all prior messages.
    for msg in history_iter:
        hist_tokens = set(str(msg).lower().split())
        union = new_set | hist_tokens
        if not union:
            # Both sets empty; similarity undefined; treat as perfect overlap.
            sim = 1.0
        else:
            intersection = new_set & hist_tokens
            sim = len(intersection) / len(union)
        if sim > max_sim:
            max_sim = sim
        # Early exit if we hit perfect similarity.
        if max_sim == 1.0:
            break
    # Novelty is the complement of similarity.
    return 1.0 - max_sim


def credibility_weight(
    author: str, author_weights: Dict[str, float] | None = None, default: float = 1.0
) -> float:
    """Return a credibility weight for a poster.

    In a full implementation this would combine historical accuracy of a
    key‑opinion leader’s posts with other contextual factors (e.g. follower
    count, verified status). Here it simply returns a value from the
    ``author_weights`` mapping or ``default`` when the author is unknown.

    Parameters
    ----------
    author:
        Identifier of the poster (username, address, etc.).
    author_weights:
        Optional mapping from author identifiers to custom credibility weights.
    default:
        Default weight to return when the author is absent from
        ``author_weights``.
    Returns
    -------
    float
        Credibility weight for the given author.
    """
    if author_weights is None:
        return default
    return float(author_weights.get(author, default))


def reach_adjusted_volume(messages: Iterable[Tuple[str, int]]) -> float:
    """Compute a simple proxy for reach‑adjusted volume.

    Each element in ``messages`` is expected to be a tuple of ``(text,
    impressions)`` where ``impressions`` is an integer representing how many
    people likely saw the message (for example, retweets × follower count). A
    more sophisticated implementation could deduplicate overlapping audiences,
    weight by engagement, or adjust for bot detection. This stub just sums
    the numeric impressions.

    Returns
    -------
    float
        The total impressions across all messages. Returns 0 when the input is
        empty.
    """
    total = 0.0
    for _, impressions in messages:
        try:
            total += float(impressions)
        except (TypeError, ValueError):
            continue  # ignore malformed impression counts
    return total


def sentiment_dispersion(sentiment_scores: Iterable[float]) -> float:
    """Return the population standard deviation of sentiment scores.

    The dispersion captures how widely opinions vary across sources. If there
    are fewer than two sentiment scores, the dispersion is zero by definition.

    Parameters
    ----------
    sentiment_scores:
        An iterable of numerical sentiment scores (e.g. from −1 to 1). Missing
        or non‑numeric values are ignored.

    Returns
    -------
    float
        Standard deviation of the valid scores, or 0.0 if insufficient data.
    """
    # Filter out non‑numeric entries.
    valid_scores: List[float] = []
    for s in sentiment_scores:
        try:
            valid_scores.append(float(s))
        except (TypeError, ValueError):
            continue
    if len(valid_scores) < 2:
        return 0.0
    # statistics.pstdev computes population stdev (denominator N).
    return float(statistics.pstdev(valid_scores))


def tone_shift(current_sentiment: float, baseline_sentiment: float) -> float:
    """Compute the difference between current sentiment and its baseline.

    A positive result means sentiment has become more positive than the baseline;
    negative means sentiment is lower. NaN inputs propagate to a NaN result.

    Parameters
    ----------
    current_sentiment:
        The present aggregate sentiment score.
    baseline_sentiment:
        A rolling baseline sentiment, such as a 7‑day or 30‑day moving average.

    Returns
    -------
    float
        The shift in sentiment relative to the baseline.
    """
    try:
        return float(current_sentiment) - float(baseline_sentiment)
    except (TypeError, ValueError):
        return float('nan')