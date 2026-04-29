import numpy as np

from app.calibration import NHGRParams, crps_gaussian, evaluate_calibration, fit_nhgr


def test_fit_nhgr_returns_identity_on_small_history():
    h = [(30.0, 1.0, 30.5)] * 10
    p = fit_nhgr(h)
    assert p == NHGRParams.identity()


def test_fit_nhgr_recovers_known_bias():
    rng = np.random.default_rng(0)
    n = 400
    mu = rng.uniform(28, 33, size=n)
    obs = mu + 0.5 + rng.normal(0, 0.6, size=n)   # +0.5 °C bias, smaller spread
    var = np.full(n, 1.0)
    h = [(float(mu[i]), float(var[i]), float(obs[i])) for i in range(n)]
    p = fit_nhgr(h)
    # predicted mean at mu=30 should be ~30.5
    assert abs((p.a + 30.0 * p.b) - 30.5) < 0.3
    metrics = evaluate_calibration(h, p)
    assert metrics["crps_calibrated"] <= metrics["crps_raw"] + 1e-6


def test_apply_to_samples_shifts_and_scales():
    p = NHGRParams(a=1.0, b=1.0, c=0.0, d=4.0)   # mean +1, variance x4
    samples = np.array([30.0, 30.5, 31.0, 31.5, 32.0])
    out = p.apply_to_samples(samples)
    assert abs(out.mean() - (samples.mean() + 1.0)) < 1e-6
    assert out.std(ddof=1) > samples.std(ddof=1)


def test_crps_decreases_with_better_forecast():
    obs = np.array([30.0, 30.5, 30.0, 31.0])
    crps_good = crps_gaussian(obs, np.full_like(obs, 0.5), obs)
    crps_bad = crps_gaussian(obs + 5, np.full_like(obs, 0.5), obs)
    assert crps_good < crps_bad
