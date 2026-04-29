from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class Station:
    code: str            # ICAO, e.g. WSSS, KLGA
    name: str            # human label
    lat: float
    lon: float
    tz: str              # IANA timezone of the local "day"
    polymarket_keyword: str   # substring to match Polymarket event title


# Phase 1 stations. Add more rows to extend coverage (LA = KLAX, ORD, MIA, ...).
STATIONS: dict[str, Station] = {
    "WSSS": Station(
        code="WSSS",
        name="Singapore Changi",
        lat=1.3521,
        lon=103.9940,
        tz="Asia/Singapore",
        polymarket_keyword="singapore",
    ),
    "KLGA": Station(
        code="KLGA",
        name="New York LaGuardia",
        lat=40.7769,
        lon=-73.8740,
        tz="America/New_York",
        polymarket_keyword="nyc",
    ),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    bankroll_usd: float = 500.0
    kelly_fraction: float = 0.25
    edge_threshold: float = 0.03           # 3 cents minimum edge after fee
    min_depth_usd: float = 50.0            # ignore bins with thinner book
    polymarket_taker_fee: float = 0.0125   # 1.25%
    sigma_inflate_c: float = 0.5           # default dispersion bump (degrees C)

    stations: str = "WSSS,KLGA"
    tz_primary: str = "Asia/Singapore"

    polymarket_gamma_base: str = "https://gamma-api.polymarket.com"
    polymarket_clob_base: str = "https://clob.polymarket.com"

    data_dir: str = "./data"
    debug: bool = False

    @property
    def active_stations(self) -> list[Station]:
        codes = [s.strip().upper() for s in self.stations.split(",") if s.strip()]
        return [STATIONS[c] for c in codes if c in STATIONS]

    @property
    def db_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / "weathereport.db"


settings = Settings()
