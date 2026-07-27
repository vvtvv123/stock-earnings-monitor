from __future__ import annotations

from typing import Any

DEFAULT_MIN_EPS_GROWTH_PCT = 5.0
DEFAULT_MIN_REVENUE_GROWTH_PCT = 1.0


def _pct_growth(actual: float | None, prior: float | None) -> float | None:
    if actual is None or prior is None or prior == 0:
        return None
    return (actual / prior - 1) * 100


def score_growth(
    eps_actual: float | None,
    eps_prior: float | None,
    revenue_actual: float | None,
    revenue_prior: float | None,
    min_eps_growth_pct: float = DEFAULT_MIN_EPS_GROWTH_PCT,
    min_revenue_growth_pct: float = DEFAULT_MIN_REVENUE_GROWTH_PCT,
) -> dict[str, Any]:
    """Score a freshly reported actual against the ticker's own prior reported period."""
    eps_growth_pct = _pct_growth(eps_actual, eps_prior)
    revenue_growth_pct = _pct_growth(revenue_actual, revenue_prior)
    eps_growth_ok = eps_growth_pct is not None and eps_growth_pct >= min_eps_growth_pct
    revenue_growth_ok = revenue_growth_pct is not None and revenue_growth_pct >= min_revenue_growth_pct
    return {
        "eps_growth_pct": eps_growth_pct,
        "revenue_growth_pct": revenue_growth_pct,
        "eps_growth_ok": eps_growth_ok,
        "revenue_growth_ok": revenue_growth_ok,
        "alert": eps_growth_ok and revenue_growth_ok,
    }
