from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm
from sqlmodel import Session, select

from .db import ForecastLog, Observation, engine


@dataclass
class NHGRParams:
    """Non-homogeneous Gaussian regression: obs ~ N(a + b*mu, c + d*var)."""

    a: float = 0.0
    b: float = 1.0
    c: float = 0.0
    d: float = 1.0

    @classmethod
    def identity(cls) -> "NHGRParams":
        return cls()

    def apply_to_samples(self, samples: np.ndarray) -> np.ndarray:
        """Return calibrated samples by location-scale transform of the empirical members.

        We map:  samples' = (a + b*mu) + sqrt(c + d*sigma^2) / sigma * (samples - mu)
        which preserves shape but rescales the mean and spread to NHGR's predicted
        mean and variance.
        """
        samples = np.asarray(samples, dtype=float)
        mu = float(samples.mean())
        sigma = float(samples.std(ddof=1)) if samples.size > 1 else 1e-3
        target_mean = self.a + self.b * mu
        target_var = max(self.c + self.d * sigma ** 2, 1e-6)
        scale = np.sqrt(target_var) / max(sigma, 1e-3)
        return target_mean + (samples - mu) * scale


def fit_nhgr(history: list[tuple[float, float, float]]) -> NHGRParams:
    """Fit NHGR by 2-step OLS:
    1) regress obs on mu => (a, b)
    2) regress squared residuals on var => (c, d)

    `history` is list of (mu, var, observed).
    """
    if len(history) < 60:
        return NHGRParams.identity()
    arr = np.asarray(history, dtype=float)
    mu = arr[:, 0]
    var = arr[:, 1]
    obs = arr[:, 2]

    X = np.column_stack([np.ones_like(mu), mu])
    coef, *_ = np.linalg.lstsq(X, obs, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    pred = a + b * mu
    resid_sq = (obs - pred) ** 2

    Xv = np.column_stack([np.ones_like(var), var])
    coef_v, *_ = np.linalg.lstsq(Xv, resid_sq, rcond=None)
    c, d = float(coef_v[0]), float(coef_v[1])
    # Guard against degenerate negatives
    if d <= 0:
        d = 1.0
    if c < 0:
        c = 0.0
    return NHGRParams(a=a, b=b, c=c, d=d)


def load_history(station: str) -> list[tuple[float, float, float]]:
    """Pull (mu, var, observed) tuples for past forecasts at this station."""
    out: list[tuple[float, float, float]] = []
    with Session(engine()) as ses:
        forecasts = ses.exec(
            select(ForecastLog).where(ForecastLog.station == station)
        ).all()
        if not forecasts:
            return out
        obs_rows = ses.exec(
            select(Observation).where(Observation.station == station)
        ).all()
        obs_map = {o.target_date: o.high_c for o in obs_rows}
        for f in forecasts:
            obs = obs_map.get(f.target_date)
            if obs is None:
                continue
            out.append((f.mean_c, f.std_c ** 2, obs))
    return out


def crps_gaussian(mu: np.ndarray, sigma: np.ndarray, obs: np.ndarray) -> float:
    """Closed-form CRPS for a Gaussian forecast (Gneiting & Raftery 2007)."""
    sigma = np.maximum(sigma, 1e-6)
    z = (obs - mu) / sigma
    return float(np.mean(sigma * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi))))


def evaluate_calibration(history: list[tuple[float, float, float]], params: NHGRParams) -> dict:
    """CRPS before vs after calibration."""
    if not history:
        return {"n": 0}
    arr = np.asarray(history, dtype=float)
    mu_raw = arr[:, 0]
    var_raw = arr[:, 1]
    obs = arr[:, 2]

    crps_raw = crps_gaussian(mu_raw, np.sqrt(var_raw), obs)
    mu_cal = params.a + params.b * mu_raw
    var_cal = np.maximum(params.c + params.d * var_raw, 1e-6)
    crps_cal = crps_gaussian(mu_cal, np.sqrt(var_cal), obs)
    return {
        "n": len(history),
        "crps_raw": crps_raw,
        "crps_calibrated": crps_cal,
        "improvement_pct": 100.0 * (crps_raw - crps_cal) / max(crps_raw, 1e-6),
    }
