"""What an analysis cost (#18): list prices, the arithmetic, and what is shown."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pipelinemd.cost import (
    PRICES,
    PRICES_AS_OF,
    Cost,
    call_cost,
    cost_of,
    describe_cost,
    format_usd,
    price_for,
    run_cost,
)
from pipelinemd.diagnose import DEFAULT_MODEL
from pipelinemd.distill import distill
from pipelinemd.models import (
    Category,
    Citation,
    Confidence,
    Diagnosis,
    JobRef,
    Report,
    Usage,
)

MILLION = 1_000_000


def _report(**changes: object) -> Report:
    base = Report(job=JobRef(name="build"), distilled=distill("ERROR: Job failed: exit code 1\n"))
    return replace(base, **changes)  # type: ignore[arg-type]


def _diagnosis(model: str = "claude-opus-5", **tokens: int) -> Diagnosis:
    return Diagnosis(
        summary="s",
        root_cause="r",
        confidence=Confidence.HIGH,
        category=Category.DEPENDENCY,
        citations=(Citation(line_number=1, text="x"),),
        model=model,
        **tokens,  # type: ignore[arg-type]
    )


# -- the table ---------------------------------------------------------------


def test_the_default_model_has_a_price() -> None:
    """Otherwise every analysis run with the defaults would show an unknown cost."""
    assert price_for(DEFAULT_MODEL) is not None


@pytest.mark.parametrize("model", sorted(PRICES))
def test_every_row_has_the_pricing_pages_shape(model: str) -> None:
    """A transposed column is the likeliest typo in a hand-copied table.

    5-minute cache writes are 1.25x input for every model the page lists; a
    cache read is cheaper than input, and output dearer.
    """
    price = PRICES[model]
    assert price.cache_write == pytest.approx(price.input * 1.25)
    assert price.cache_read < price.input < price.output


def test_a_dated_snapshot_is_priced_as_its_model() -> None:
    assert price_for("claude-haiku-4-5-20251001") == PRICES["claude-haiku-4-5"]


def test_an_unknown_model_has_no_price_rather_than_a_neighbours() -> None:
    assert price_for("claude-opus-9") is None
    assert price_for("claude-opus-5-extra") is None


# -- the arithmetic ----------------------------------------------------------


def test_each_kind_of_token_is_billed_at_its_own_rate() -> None:
    """A million of each, so the result is the row itself, summed."""
    usage = Usage(
        model="claude-opus-5",
        input_tokens=MILLION,
        output_tokens=MILLION,
        cache_creation_input_tokens=MILLION,
        cache_read_input_tokens=MILLION,
    )
    assert call_cost(usage) == pytest.approx(5.00 + 25.00 + 6.25 + 0.50)


@pytest.mark.parametrize(
    ("field", "rate"),
    [
        ("input_tokens", 5.00),
        ("output_tokens", 25.00),
        ("cache_creation_input_tokens", 6.25),
        ("cache_read_input_tokens", 0.50),
    ],
)
def test_no_rate_is_borrowed_from_another_column(field: str, rate: float) -> None:
    usage = Usage(model="claude-opus-5", **{field: MILLION})  # type: ignore[arg-type]
    assert call_cost(usage) == pytest.approx(rate)


def test_a_typical_diagnosis() -> None:
    """5,210 in and 1,034 out on Opus 5: about five cents."""
    usage = Usage(model="claude-opus-5", input_tokens=5210, output_tokens=1034)
    assert call_cost(usage) == pytest.approx(0.02605 + 0.02585)


def test_an_unpriced_call_has_no_cost() -> None:
    assert call_cost(Usage(model="claude-opus-9", input_tokens=10)) is None


# -- per analysis ------------------------------------------------------------


def test_rules_only_costs_nothing_and_says_so() -> None:
    cost = cost_of(_report())
    assert cost.calls == ()
    assert cost.usd == 0.0
    assert isinstance(cost.usd, float), "JSON would otherwise carry the integer 0"
    assert describe_cost(cost) == "cost $0 — rules only, no model call"


def test_the_diagnosis_call_is_counted() -> None:
    cost = cost_of(_report(diagnosis=_diagnosis(input_tokens=1000, output_tokens=100)))
    assert cost.usd == pytest.approx(0.005 + 0.0025)
    assert cost.discarded == 0


def test_a_discarded_call_is_still_counted() -> None:
    """A diagnosis rejected for citing nothing real was billed all the same."""
    report = _report(discarded_calls=(Usage(model="claude-opus-5", input_tokens=1000),))
    cost = cost_of(report)
    assert cost.usd == pytest.approx(0.005)
    assert cost.discarded == 1
    assert "no diagnosis came of it" in describe_cost(cost)


def test_discarded_and_kept_calls_add_up() -> None:
    report = _report(
        discarded_calls=(Usage(model="claude-opus-5", input_tokens=1000),),
        diagnosis=_diagnosis(input_tokens=1000),
    )
    cost = cost_of(report)
    assert cost.usd == pytest.approx(0.010)
    assert "2 calls, 1 discarded" in describe_cost(cost)


def test_one_unpriced_call_makes_the_total_unknown_not_partial() -> None:
    """A partial sum would understate the cost and look like a whole one."""
    cost = Cost(
        calls=(
            Usage(model="claude-opus-5", input_tokens=1000),
            Usage(model="claude-opus-9", input_tokens=1000),
        )
    )
    assert cost.usd is None
    assert cost.unpriced_models == ("claude-opus-9",)
    assert "est. cost unknown" in describe_cost(cost)
    assert "no price on file for claude-opus-9" in describe_cost(cost)


def test_input_tokens_shown_include_the_cached_ones() -> None:
    """What the model was shown, which is what a reader means by "tokens in"."""
    usage = Usage(
        model="claude-opus-5",
        input_tokens=100,
        cache_creation_input_tokens=20,
        cache_read_input_tokens=3,
        output_tokens=7,
    )
    cost = Cost(calls=(usage,))
    assert (cost.input_tokens, cost.output_tokens) == (123, 7)


def test_the_cost_line_names_the_model_and_the_tokens() -> None:
    cost = cost_of(_report(diagnosis=_diagnosis(input_tokens=5210, output_tokens=1034)))
    assert describe_cost(cost) == (
        "est. cost $0.0519 · claude-opus-5 · 5,210 in / 1,034 out tokens"
        f" · prices as of {PRICES_AS_OF}"
    )


def test_the_cost_line_dates_its_prices() -> None:
    """A pasted report carries no other clue to how old the estimate is."""
    cost = Cost(calls=(Usage(model="claude-opus-5", input_tokens=10),))
    assert describe_cost(cost).endswith(f"prices as of {PRICES_AS_OF}")


def test_several_calls_with_none_discarded_say_only_how_many() -> None:
    call = Usage(model="claude-opus-5", input_tokens=10)
    assert " · 2 calls · " in describe_cost(Cost(calls=(call, call)))


def test_a_run_pools_its_reports_calls() -> None:
    priced = _report(diagnosis=_diagnosis(input_tokens=1000))
    discarded = _report(discarded_calls=(Usage(model="claude-opus-5", input_tokens=1000),))
    run = run_cost([priced, discarded, _report()])
    assert len(run.calls) == 2
    assert run.discarded == 1
    assert run.usd == pytest.approx(0.010)


def test_a_run_with_one_unpriced_report_has_no_total() -> None:
    priced = _report(diagnosis=_diagnosis(input_tokens=1000))
    unpriced = _report(diagnosis=_diagnosis(model="claude-opus-9", input_tokens=1000))
    assert cost_of(priced).usd is not None
    assert run_cost([priced, unpriced]).usd is None


@pytest.mark.parametrize(
    ("usd", "shown"),
    [
        (None, "unknown"),
        (0.0, "$0"),
        (0.00004, "<$0.0001"),
        (0.0519, "$0.0519"),
        (1.5, "$1.5000"),
    ],
)
def test_format_usd(usd: float | None, shown: str) -> None:
    assert format_usd(usd) == shown
