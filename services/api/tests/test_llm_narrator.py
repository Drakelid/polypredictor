from __future__ import annotations

from collections.abc import Sequence

from api.llm_narrator import DriverNarrationContext, narrate_top_drivers


def test_no_narrator_returns_deterministic_summaries() -> None:
    summaries = narrate_top_drivers(
        drivers=[
            ("Smart-money consensus", 0.42),
            ("24h realized vol", -0.15),
            ("Spread", 0.0),
        ],
    )
    assert summaries == [
        "Smart-money consensus pushed the ensemble higher",
        "24h realized vol pushed the ensemble lower",
        "Spread was neutral",
    ]


def test_narrator_output_passes_guardrail_when_clean() -> None:
    def _narrator(contexts: Sequence[DriverNarrationContext]) -> Sequence[str]:
        return [
            "Smart-money consensus pushed higher",
            "24h realized vol pushed lower",
            "Spread was neutral",
        ]

    summaries = narrate_top_drivers(
        drivers=[
            ("Smart-money consensus", 0.42),
            ("24h realized vol", -0.15),
            ("Spread", 0.0),
        ],
        narrator=_narrator,
    )
    # The validator strips/normalizes whitespace but otherwise returns the
    # input verbatim when it's clean.
    assert summaries[0] == "Smart-money consensus pushed higher"
    assert summaries[1] == "24h realized vol pushed lower"
    assert summaries[2] == "Spread was neutral"


def test_narrator_output_with_numeric_token_falls_back_per_line() -> None:
    """Guardrail rejects numeric tokens; that line falls back to deterministic."""

    def _bad_narrator(contexts: Sequence[DriverNarrationContext]) -> Sequence[str]:
        return [
            "Smart-money consensus added 12 bps to the score",  # has '12' + 'bps'
            "24h realized vol pushed lower",  # clean
            "Spread was neutral",  # clean
        ]

    summaries = narrate_top_drivers(
        drivers=[
            ("Smart-money consensus", 0.42),
            ("24h realized vol", -0.15),
            ("Spread", 0.0),
        ],
        narrator=_bad_narrator,
    )
    # First line was rejected by the guardrail; substituted with deterministic.
    assert summaries[0] == "Smart-money consensus pushed the ensemble higher"
    # Other lines pass through.
    assert summaries[1] == "24h realized vol pushed lower"
    assert summaries[2] == "Spread was neutral"


def test_narrator_estimation_language_is_rejected() -> None:
    """Guardrail bans probability/odds/price/forecast etc."""

    def _bad(contexts: Sequence[DriverNarrationContext]) -> Sequence[str]:
        return [
            "Smart-money consensus increased the probability",  # 'probability' banned
            "24h realized vol pushed lower",
            "Spread was neutral",
        ]

    summaries = narrate_top_drivers(
        drivers=[
            ("Smart-money consensus", 0.42),
            ("24h realized vol", -0.15),
            ("Spread", 0.0),
        ],
        narrator=_bad,
    )
    assert summaries[0] == "Smart-money consensus pushed the ensemble higher"


def test_narrator_unsupported_driver_term_is_rejected() -> None:
    """Mentioning an unsupported signal family (e.g. 'whale') without it
    being one of the supplied labels must be rejected."""

    def _bad(contexts: Sequence[DriverNarrationContext]) -> Sequence[str]:
        return [
            "Smart-money consensus and a whale move pushed higher",
        ]

    summaries = narrate_top_drivers(
        drivers=[("Smart-money consensus", 0.42)],
        narrator=_bad,
    )
    assert summaries == ["Smart-money consensus pushed the ensemble higher"]


def test_narrator_exception_falls_back_to_deterministic() -> None:
    def _broken(_contexts: Sequence[DriverNarrationContext]) -> Sequence[str]:
        raise RuntimeError("Anthropic 5xx")

    summaries = narrate_top_drivers(
        drivers=[
            ("Smart-money consensus", 0.42),
            ("Spread", 0.0),
        ],
        narrator=_broken,
    )
    assert summaries == [
        "Smart-money consensus pushed the ensemble higher",
        "Spread was neutral",
    ]


def test_narrator_returning_fewer_lines_pads_with_deterministic() -> None:
    def _short(_contexts: Sequence[DriverNarrationContext]) -> Sequence[str]:
        return ["Smart-money consensus pushed higher"]

    summaries = narrate_top_drivers(
        drivers=[
            ("Smart-money consensus", 0.42),
            ("24h realized vol", -0.15),
            ("Spread", 0.0),
        ],
        narrator=_short,
    )
    # First line came from the narrator; the rest are deterministic fillers.
    assert summaries[0] == "Smart-money consensus pushed higher"
    assert summaries[1] == "24h realized vol pushed the ensemble lower"
    assert summaries[2] == "Spread was neutral"


def test_empty_drivers_returns_empty_list() -> None:
    assert narrate_top_drivers(drivers=[]) == []
    # Even with a narrator wired in.
    assert (
        narrate_top_drivers(drivers=[], narrator=lambda _ctx: ["unused"]) == []
    )


def test_narrator_invariant_no_numeric_can_pass_through() -> None:
    """End-to-end invariant: no numeric token from the narrator can ever
    appear in the returned summaries — even sneaky ones like '$1k' or '50%'.
    """
    sneaky_outputs = [
        "Smart-money consensus added 50% to score",  # percent
        "Smart-money consensus pushed by $1k",  # dollar
        "Smart-money consensus pushed three steps higher",  # word number
    ]
    for sneaky in sneaky_outputs:
        summaries = narrate_top_drivers(
            drivers=[("Smart-money consensus", 0.42)],
            narrator=lambda _ctx, sneaky=sneaky: [sneaky],
        )
        # Each sneaky output is rejected; deterministic fallback kicks in.
        assert summaries == ["Smart-money consensus pushed the ensemble higher"]
