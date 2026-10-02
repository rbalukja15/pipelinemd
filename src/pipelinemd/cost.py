"""What one analysis cost, estimated from the tokens the API billed.

An estimate, and labelled as one wherever it is shown. The rates are Claude API
list prices for a standard request - global routing, standard speed, not
batched - copied from the pricing page on `PRICES_AS_OF`. Amazon Bedrock and
Google Cloud price separately, US-only inference (`inference_geo`) costs 1.1x,
and negotiated rates are not visible from here. None of those can be read off
a response, so the estimate does not pretend to account for them. Nor can a
call that was billed and then lost in transport - a timeout mid-response -
because no usage ever arrives for it.

A model missing from the table gets no estimate at all - `None`, shown as
"unknown" - rather than the price of whichever model looks closest. A wrong
number with a dollar sign in front of it is worse than no number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Report, Usage

PRICES_AS_OF = "2026-10-02"
PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens, in the pricing page's column order.

    `cache_write` is the 5-minute rate. pipelinemd sets no `cache_control`, so
    any cache write it is billed for is at the default duration.
    """

    input: float
    cache_write: float
    cache_read: float
    output: float


#: Current models only; retired ones are left out rather than kept up to date.
#: Cache reads are 0.1x input except where the page says otherwise - Fable 5.1
#: and Mythos 5.1 at 0.025x, Opus 5.5 at 0.05x - so the column is written out
#: per model instead of derived.
PRICES: dict[str, Price] = {
    #                    input  5m write  cache read  output
    "claude-fable-5-1": Price(10.00, 12.50, 0.25, 50.00),
    "claude-mythos-5-1": Price(10.00, 12.50, 0.25, 50.00),
    "claude-fable-5": Price(10.00, 12.50, 1.00, 50.00),
    "claude-mythos-5": Price(10.00, 12.50, 1.00, 50.00),
    "claude-opus-5-5": Price(4.00, 5.00, 0.20, 20.00),
    "claude-opus-5": Price(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-8": Price(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-7": Price(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-6": Price(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-5": Price(5.00, 6.25, 0.50, 25.00),
    "claude-sonnet-5-5": Price(2.00, 2.50, 0.20, 10.00),
    "claude-sonnet-5": Price(2.00, 2.50, 0.20, 10.00),
    "claude-sonnet-4-6": Price(3.00, 3.75, 0.30, 15.00),
    "claude-sonnet-4-5": Price(3.00, 3.75, 0.30, 15.00),
    "claude-haiku-4-5": Price(1.00, 1.25, 0.10, 5.00),
}

#: `claude-haiku-4-5-20251001` is a dated snapshot of `claude-haiku-4-5`, and
#: is billed the same. Google Cloud's `claude-opus-4-5@20251101` form is not
#: matched on purpose: Google Cloud bills separately, so `unknown` is the right
#: answer for it, not a first-party list price.
_SNAPSHOT = re.compile(r"-\d{8}$")


def price_for(model: str) -> Price | None:
    """The list price of a model, or None when it is not in the table."""
    return PRICES.get(_SNAPSHOT.sub("", model.strip()))


def call_cost(usage: Usage) -> float | None:
    """One call's estimated cost in USD, or None for a model with no price."""
    price = price_for(usage.model)
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
        + usage.output_tokens * price.output
    ) / 1_000_000


@dataclass(frozen=True, slots=True)
class Cost:
    """Every billed model call behind one analysis, and what they came to."""

    calls: tuple[Usage, ...]
    #: How many of `calls` produced no diagnosis. Paid for all the same.
    discarded: int = 0

    @property
    def usd(self) -> float | None:
        """The estimated total. Zero for rules only; None if any call is unpriced.

        A partial sum would understate the cost and look like a whole one, so
        one unpriced call makes the total unknown.
        """
        costs = [call_cost(call) for call in self.calls]
        if any(cost is None for cost in costs):
            return None
        return sum((cost for cost in costs if cost is not None), 0.0)

    @property
    def unpriced_models(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.model for c in self.calls if price_for(c.model) is None))

    @property
    def input_tokens(self) -> int:
        """Every input token, cached or not: what the model was shown."""
        return sum(
            c.input_tokens + c.cache_creation_input_tokens + c.cache_read_input_tokens
            for c in self.calls
        )

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)


def cost_of(report: Report) -> Cost:
    """Derived on demand, like the classification, so it cannot go stale."""
    calls = report.discarded_calls
    if report.diagnosis is not None:
        calls = (*calls, report.diagnosis.usage)
    return Cost(calls=tuple(calls), discarded=len(report.discarded_calls))


def run_cost(reports: list[Report] | tuple[Report, ...]) -> Cost:
    """Every call behind a run of several analyses, as one Cost.

    Pooling the calls rather than adding up per-report totals keeps the rule
    `Cost.usd` already enforces: one unpriced call anywhere makes the run's
    total unknown, instead of the partial sum a consumer adding up per-report
    figures would get.
    """
    costs = [cost_of(report) for report in reports]
    return Cost(
        calls=tuple(call for cost in costs for call in cost.calls),
        discarded=sum(cost.discarded for cost in costs),
    )


def format_usd(usd: float | None) -> str:
    """`$0.0412`; `$0` for no spend at all; `unknown` when it cannot be priced.

    Four places, because one diagnosis costs cents, not dollars.
    """
    if usd is None:
        return "unknown"
    if usd == 0:
        return "$0"
    if usd < 0.0001:
        return "<$0.0001"
    return f"${usd:.4f}"


def describe_cost(cost: Cost) -> str:
    """One line for a report.

    `est. cost $0.0412 · claude-opus-5 · 5,210 in / 1,034 out tokens · prices as of 2026-10-02`

    The date is on the line because the estimate goes stale with it, and the
    reader of a pasted report has no other way to tell how old the prices are.
    """
    if not cost.calls:
        return "cost $0 — rules only, no model call"
    parts = [
        f"est. cost {format_usd(cost.usd)}",
        " + ".join(dict.fromkeys(c.model for c in cost.calls)),
    ]
    if len(cost.calls) > 1:
        discarded = f", {cost.discarded} discarded" if cost.discarded else ""
        parts.append(f"{len(cost.calls)} calls{discarded}")
    elif cost.discarded:
        # Otherwise a report with a cost and no diagnosis reads as a mistake.
        parts.append("no diagnosis came of it")
    parts.append(f"{cost.input_tokens:,} in / {cost.output_tokens:,} out tokens")
    if cost.unpriced_models:
        parts.append(f"no price on file for {', '.join(cost.unpriced_models)}")
    parts.append(f"prices as of {PRICES_AS_OF}")
    return " · ".join(parts)
