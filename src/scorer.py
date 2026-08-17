from __future__ import annotations

from typing import Any

DEFAULT_MIN_EPS_GROWTH_PCT = 10.0
DEFAULT_MIN_REVENUE_GROWTH_PCT = 10.0

# A same-company, one-year-apart comparison shouldn't ever be off by more
# than this multiple -- if it is, the "prior" value is almost certainly a
# data artifact (e.g. a mis-scaled/mis-tagged EDGAR fact), not real growth.
# Confirmed live: PLXS's EDGAR revenue tag returned ~$1.02M for a quarter
# where the real figure was ~$1.3B (~1280x), which without this guard
# scored as a legitimate +128,032% "alert".
MAX_PLAUSIBLE_RATIO = 20.0


def pct_change(actual: float | None, baseline: float | None) -> float | None:
    """% change of actual vs. a baseline -- used both for growth-vs-prior-period
    and beat-vs-analyst-estimate, since it's the same math either way."""
    if actual is None or baseline is None or baseline == 0:
        return None
    return (actual / baseline - 1) * 100


def is_plausible_pair(current: float | None, prior: float | None, max_ratio: float = MAX_PLAUSIBLE_RATIO) -> bool:
    """False only when both values are present, nonzero, and implausibly far
    apart in scale. True when there's nothing to compare (nothing to rule
    out) so callers can safely skip this check for missing data."""
    if current is None or prior is None:
        return True
    if prior == 0 or current == 0:
        return True
    ratio = abs(current / prior)
    return (1 / max_ratio) <= ratio <= max_ratio


def score_growth(
    eps_actual: float | None,
    eps_prior: float | None,
    revenue_actual: float | None,
    revenue_prior: float | None,
    min_eps_growth_pct: float = DEFAULT_MIN_EPS_GROWTH_PCT,
    min_revenue_growth_pct: float = DEFAULT_MIN_REVENUE_GROWTH_PCT,
) -> dict[str, Any]:
    """Score a freshly reported actual against the ticker's own prior reported period."""
    eps_growth_pct = pct_change(eps_actual, eps_prior)
    revenue_growth_pct = pct_change(revenue_actual, revenue_prior)

    # % change is only meaningful vs. a positive baseline. When prior <= 0,
    # actual/prior's sign flips in ways that don't mean "growth" -- e.g. a
    # loss widening from -$0.58 to -$1.46 divides out to +151.7%, exactly
    # backwards. Require prior > 0 before trusting the percentage at all.
    eps_growth_ok = (
        eps_prior is not None
        and eps_prior > 0
        and eps_growth_pct is not None
        and eps_growth_pct >= min_eps_growth_pct
    )
    revenue_growth_ok = (
        revenue_prior is not None
        and revenue_prior > 0
        and revenue_growth_pct is not None
        and revenue_growth_pct >= min_revenue_growth_pct
    )

    # A negative-to-positive crossing is a real, notable event that simply
    # isn't expressible as a percentage -- surfaced as its own signal rather
    # than folded into (or excluded from) the growth-% alert.
    eps_turned_profitable = eps_prior is not None and eps_prior <= 0 and eps_actual is not None and eps_actual > 0
    revenue_turned_profitable = (
        revenue_prior is not None and revenue_prior <= 0 and revenue_actual is not None and revenue_actual > 0
    )

    return {
        "eps_growth_pct": eps_growth_pct,
        "revenue_growth_pct": revenue_growth_pct,
        "eps_growth_ok": eps_growth_ok,
        "revenue_growth_ok": revenue_growth_ok,
        "alert": eps_growth_ok and revenue_growth_ok,
        "eps_turned_profitable": eps_turned_profitable,
        "revenue_turned_profitable": revenue_turned_profitable,
    }
