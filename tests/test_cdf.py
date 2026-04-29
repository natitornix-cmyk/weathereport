import numpy as np

from app.forecast.cdf import bayes_update_with_running_max, samples_to_cdf


def test_cdf_monotone_and_bounds():
    samples = np.array([28.0, 29.5, 30.1, 30.8, 31.4, 32.0, 32.6, 33.5])
    cdf = samples_to_cdf(samples, sigma_inflate_c=0.0)
    grid = np.linspace(20, 40, 200)
    vals = cdf(grid)
    assert vals[0] < 1e-3
    assert vals[-1] > 1 - 1e-3
    assert np.all(np.diff(vals) >= -1e-9), "CDF should be monotone non-decreasing"


def test_prob_between_sums_close_to_one():
    samples = np.linspace(28, 34, 80)
    cdf = samples_to_cdf(samples, sigma_inflate_c=0.5)
    bins = [(20, 28.5), (28.5, 30.5), (30.5, 32.5), (32.5, 40.0)]
    total = sum(cdf.prob_between(lo, hi) for lo, hi in bins)
    assert abs(total - 1.0) < 1e-3


def test_quantile_inverse():
    samples = np.linspace(28, 34, 80)
    cdf = samples_to_cdf(samples, sigma_inflate_c=0.3)
    q = cdf.quantile(0.5)
    assert 30.5 <= q <= 31.5


def test_bayes_update_lifts_floor():
    samples = np.array([29.0, 30.0, 31.0, 32.0, 33.0])
    refined = bayes_update_with_running_max(samples, running_max_c=32.5, remaining_uncertainty_c=0.5)
    assert refined.prob_between(-100, 32.0) < 0.05
    assert refined.prob_between(32.0, 100) > 0.9
