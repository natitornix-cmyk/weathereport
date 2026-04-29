from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class CDF:
    """Continuous CDF over daily-max temperature, modeled as a Gaussian KDE.

    Internally we keep the kernel centers and bandwidth so we can evaluate
    F(t) cheaply and resample if needed.
    """

    centers: np.ndarray   # shape (n,)
    bandwidth: float

    def __call__(self, t: float | np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        # F(t) = mean over kernels of Phi((t - c_i) / h)
        z = (t[..., None] - self.centers) / self.bandwidth
        return norm.cdf(z).mean(axis=-1)

    def prob_between(self, lo: float, hi: float) -> float:
        return float(self(hi) - self(lo))

    @property
    def mean(self) -> float:
        return float(self.centers.mean())

    @property
    def std(self) -> float:
        # Variance of mixture of normals = mean of variances + var of means
        var = self.bandwidth ** 2 + float(self.centers.var())
        return float(np.sqrt(var))

    def quantile(self, q: float) -> float:
        # Numerical inversion via bisection on a wide bracket.
        lo = float(self.centers.min()) - 6 * self.bandwidth
        hi = float(self.centers.max()) + 6 * self.bandwidth
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if float(self(mid)) < q:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def samples_to_cdf(samples: np.ndarray, sigma_inflate_c: float = 0.5) -> CDF:
    """Build a Gaussian-KDE CDF from ensemble daily-max samples.

    Bandwidth uses Silverman's rule, then we add `sigma_inflate_c` in quadrature
    to compensate for known under-dispersion of raw ensembles.
    """
    samples = np.asarray(samples, dtype=float)
    if samples.size == 0:
        raise ValueError("empty samples")
    n = samples.size
    s = float(samples.std(ddof=1)) if n > 1 else 1.0
    # Silverman's rule (Gaussian reference)
    h_silver = 1.06 * max(s, 1e-3) * n ** (-1 / 5)
    h = float(np.sqrt(h_silver ** 2 + sigma_inflate_c ** 2))
    return CDF(centers=samples.copy(), bandwidth=max(h, 1e-3))


def bayes_update_with_running_max(
    samples: np.ndarray,
    running_max_c: float,
    remaining_uncertainty_c: float,
    sigma_inflate_c: float = 0.2,
) -> CDF:
    """Refine the daily-max distribution given the running observed max so far.

    Method: for each ensemble member, the *true* daily max must be at least
    `running_max_c` (today already hit it) and at most that plus the plausible
    upside in the remaining hours, modeled as `running_max_c + N(0, remaining)`.
    Bayes update via importance reweighting by the indicator that the member's
    sampled max is consistent with the obs (>= running_max - small slack).
    Then samples_to_cdf with smaller dispersion bump (we have data now).
    """
    samples = np.asarray(samples, dtype=float)
    slack = 0.5  # member-vs-station bias slack in °C
    # Keep members already at or above the observed running max...
    keep = samples >= (running_max_c - slack)
    if keep.sum() < 5:
        # Fallback: shift floor up to the observed max.
        adjusted = np.maximum(samples, running_max_c)
    else:
        adjusted = samples[keep]
    # Add upside noise representing remaining-day uncertainty.
    rng = np.random.default_rng(0)
    upside = rng.normal(0.0, max(remaining_uncertainty_c, 0.05), size=adjusted.size)
    candidates = np.maximum(adjusted, running_max_c) + np.maximum(upside, 0.0) * 0.5
    return samples_to_cdf(candidates, sigma_inflate_c=sigma_inflate_c)
