from __future__ import annotations

from dataclasses import dataclass

from .config import settings
from .forecast.cdf import CDF
from .polymarket import Bin, WeatherEvent


@dataclass
class Recommendation:
    event: WeatherEvent
    bin: Bin
    our_prob: float
    market_ask: float
    edge: float                 # net of taker fee, in probability points
    stake_usd: float
    polymarket_url: str


def _binary_kelly_fraction(p: float, ask: float) -> float:
    """Kelly fraction for a YES contract bought at `ask` with true prob `p`.

    Payout per dollar staked at price a: 1/a if YES, 0 if NO. So bankroll
    fraction is f = (p*(1-a) - (1-p)*a) / (a*(1-a)). Clamp to [0, 1].
    """
    if ask <= 0 or ask >= 1:
        return 0.0
    num = p * (1 - ask) - (1 - p) * ask
    if num <= 0:
        return 0.0
    den = ask * (1 - ask)
    f = num / den
    return max(0.0, min(1.0, f))


def compute_recommendations(
    cdf: CDF,
    event: WeatherEvent,
    bankroll_usd: float | None = None,
    fee: float | None = None,
    kelly_frac: float | None = None,
    edge_min: float | None = None,
    min_depth_usd: float | None = None,
    per_event_cap_pct: float = 0.05,
    per_bin_cap_pct: float = 0.02,
) -> list[Recommendation]:
    """Score every bin in `event`, return only those with positive edge above threshold."""
    bankroll_usd = bankroll_usd if bankroll_usd is not None else settings.bankroll_usd
    fee = fee if fee is not None else settings.polymarket_taker_fee
    kelly_frac = kelly_frac if kelly_frac is not None else settings.kelly_fraction
    edge_min = edge_min if edge_min is not None else settings.edge_threshold
    min_depth_usd = min_depth_usd if min_depth_usd is not None else settings.min_depth_usd

    recs: list[Recommendation] = []
    raw_stakes: list[float] = []
    for b in event.bins:
        p = cdf.prob_between(b.bin_lo, b.bin_hi)
        p = max(0.0, min(1.0, float(p)))
        ask = b.ask
        if ask <= 0 or ask >= 1:
            raw_stakes.append(0.0)
            continue
        edge = p - ask - fee * ask
        if edge < edge_min:
            raw_stakes.append(0.0)
            continue
        if b.depth_usd < min_depth_usd:
            raw_stakes.append(0.0)
            continue
        f = _binary_kelly_fraction(p, ask) * kelly_frac
        stake = f * bankroll_usd
        per_bin_cap = per_bin_cap_pct * bankroll_usd
        stake = min(stake, per_bin_cap)
        if stake <= 0:
            raw_stakes.append(0.0)
            continue
        raw_stakes.append(stake)
        recs.append(
            Recommendation(
                event=event,
                bin=b,
                our_prob=p,
                market_ask=ask,
                edge=edge,
                stake_usd=stake,
                polymarket_url=event.url,
            )
        )

    # Per-event cap: rescale all positive stakes if their sum exceeds the cap.
    if recs:
        per_event_cap = per_event_cap_pct * bankroll_usd
        total = sum(r.stake_usd for r in recs)
        if total > per_event_cap:
            scale = per_event_cap / total
            for r in recs:
                r.stake_usd *= scale

    recs.sort(key=lambda r: r.edge, reverse=True)
    return recs
