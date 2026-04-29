from datetime import date, datetime, timezone

import numpy as np

from app.forecast.cdf import samples_to_cdf
from app.polymarket import Bin, WeatherEvent
from app.pricing import _binary_kelly_fraction, compute_recommendations


def _mk_event(bins):
    return WeatherEvent(
        event_id="test",
        slug="test-event",
        title="Highest temperature in Singapore on April 29, 2026",
        station_code="WSSS",
        target_date=date(2026, 4, 29),
        end_date=datetime(2026, 4, 30, tzinfo=timezone.utc),
        bins=bins,
    )


def _bin(lo, hi, ask, depth=500.0):
    return Bin(
        bin_lo=lo, bin_hi=hi, label=f"{int(lo)}-{int(hi-1)}",
        yes_token_id="0xtok", bid=max(0.0, ask - 0.02), ask=ask,
        depth_usd=depth, market_id="m", condition_id="c",
    )


def test_kelly_zero_when_no_edge():
    assert _binary_kelly_fraction(0.5, 0.5) == 0.0
    assert _binary_kelly_fraction(0.4, 0.5) == 0.0


def test_kelly_positive_with_edge():
    f = _binary_kelly_fraction(0.6, 0.4)
    assert 0.5 < f < 1.0


def test_recommendations_only_above_threshold():
    samples = np.linspace(31, 33, 80)  # mass concentrated 31-33
    cdf = samples_to_cdf(samples, sigma_inflate_c=0.3)
    bins = [
        _bin(28, 30, ask=0.10),  # we'll have low p, ask is reasonable -> negative edge
        _bin(30, 32, ask=0.20),  # we'll have decent p
        _bin(32, 34, ask=0.20),  # we'll have decent p
        _bin(34, 36, ask=0.10),
    ]
    ev = _mk_event(bins)
    recs = compute_recommendations(
        cdf, ev,
        bankroll_usd=1000, fee=0.0125, kelly_frac=0.25, edge_min=0.03, min_depth_usd=100,
    )
    # all positive-edge recs should have edge >= 0.03
    for r in recs:
        assert r.edge >= 0.03
        assert r.stake_usd > 0


def test_recommendations_respect_per_event_cap():
    samples = np.linspace(31, 33, 80)
    cdf = samples_to_cdf(samples, sigma_inflate_c=0.3)
    bins = [_bin(31 + i, 31 + i + 1, ask=0.05, depth=1000) for i in range(2)]
    ev = _mk_event(bins)
    recs = compute_recommendations(
        cdf, ev,
        bankroll_usd=1000, fee=0.0125, kelly_frac=0.25, edge_min=0.03, min_depth_usd=100,
        per_event_cap_pct=0.05,
    )
    if recs:
        total = sum(r.stake_usd for r in recs)
        assert total <= 0.05 * 1000 + 1e-6


def test_recommendations_skip_thin_book():
    samples = np.linspace(31, 33, 80)
    cdf = samples_to_cdf(samples, sigma_inflate_c=0.3)
    bins = [_bin(31, 32, ask=0.05, depth=10)]   # below 50 default min
    ev = _mk_event(bins)
    recs = compute_recommendations(cdf, ev, min_depth_usd=50, edge_min=0.03)
    assert recs == []
