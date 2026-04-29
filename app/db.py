from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel, create_engine

from .config import settings


class ForecastLog(SQLModel, table=True):
    """One row per (station, target_date, run_time): summary of ensemble forecast."""

    id: Optional[int] = Field(default=None, primary_key=True)
    station: str = Field(index=True)
    target_date: date = Field(index=True)
    run_time: datetime = Field(index=True)
    n_members: int
    mean_c: float
    std_c: float
    p10_c: float
    p50_c: float
    p90_c: float
    samples_json: str   # serialized list[float] for re-fitting


class Observation(SQLModel, table=True):
    """Resolved daily-high observation per station per local date."""

    id: Optional[int] = Field(default=None, primary_key=True)
    station: str = Field(index=True)
    target_date: date = Field(index=True)
    high_c: float
    source: str         # "metar", "mss", "manual"
    recorded_at: datetime


class MarketSnapshot(SQLModel, table=True):
    """One row per (event, bin, snapshot_time)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: str = Field(index=True)
    event_slug: str
    event_title: str
    station: str = Field(index=True)
    target_date: date = Field(index=True)
    bin_lo: float
    bin_hi: float
    bin_label: str
    yes_token_id: str
    bid: float
    ask: float
    depth_usd: float
    snapshot_time: datetime = Field(index=True)


class Recommendation(SQLModel, table=True):
    """Ranked rec emitted by the pricing pass."""

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: str = Field(index=True)
    event_slug: str
    event_title: str
    station: str
    target_date: date
    bin_lo: float
    bin_hi: float
    bin_label: str
    our_prob: float
    market_ask: float
    edge: float
    stake_usd: float
    yes_token_id: str
    polymarket_url: str
    created_at: datetime = Field(index=True)


class AlertSent(SQLModel, table=True):
    """Dedup key = event_id + bin_label + hour bucket."""

    id: Optional[int] = Field(default=None, primary_key=True)
    dedup_key: str = Field(index=True, unique=True)
    sent_at: datetime


_engine = None


def engine():
    global _engine
    if _engine is None:
        url = f"sqlite:///{settings.db_path}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
    return _engine
