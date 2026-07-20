import numpy as np

from significance.bootstrap import block_bootstrap_indices, bootstrap_difference_ci, bootstrap_statistic_ci


def _mean(x):
    return float(np.mean(x))


def test_bootstrap_ci_point_estimate_matches_direct_computation():
    rng = np.random.default_rng(0)
    values = rng.normal(loc=10.0, scale=2.0, size=200)
    result = bootstrap_statistic_ci(_mean, values, n_bootstrap=500, seed=1)
    assert abs(result["point_estimate"] - np.mean(values)) < 1e-9


def test_bootstrap_ci_contains_true_mean_for_known_distribution():
    # A single 95% CI is *expected* to miss the true value ~5% of the time by construction
    # (that's what "95% coverage" means) — asserting on one random draw would be flaky.
    # Check coverage across many independent replications instead, with slack for finite-N noise.
    true_mean = 5.0
    n_trials = 40
    hits = 0
    for trial in range(n_trials):
        rng = np.random.default_rng(1000 + trial)
        values = rng.normal(loc=true_mean, scale=1.0, size=300)
        result = bootstrap_statistic_ci(_mean, values, n_bootstrap=500, alpha=0.05, seed=2000 + trial)
        if result["ci_lower"] < true_mean < result["ci_upper"]:
            hits += 1
    assert hits >= n_trials * 0.85  # nominal 95%, allow slack for a 40-trial sample


def test_bootstrap_ci_narrows_with_larger_sample_size():
    rng = np.random.default_rng(2)
    small = rng.normal(0, 1, size=20)
    large = rng.normal(0, 1, size=2000)
    small_result = bootstrap_statistic_ci(_mean, small, n_bootstrap=1000, seed=3)
    large_result = bootstrap_statistic_ci(_mean, large, n_bootstrap=1000, seed=3)
    small_width = small_result["ci_upper"] - small_result["ci_lower"]
    large_width = large_result["ci_upper"] - large_result["ci_lower"]
    assert large_width < small_width


def test_paired_bootstrap_preserves_pairing():
    # forecast that's a noisy but correlated copy of observed; metric = correlation.
    rng = np.random.default_rng(3)
    observed = rng.normal(0, 1, size=100)
    forecast = observed + rng.normal(0, 0.1, size=100)

    def _corr(f, o):
        return float(np.corrcoef(f, o)[0, 1])

    result = bootstrap_statistic_ci(_corr, forecast, observed, n_bootstrap=500, seed=4)
    # If pairing were broken by resampling forecast/observed independently, correlation
    # would collapse toward zero; since it's preserved, the CI should stay high.
    assert result["ci_lower"] > 0.8


def test_bootstrap_difference_ci_zero_when_forecasts_identical():
    rng = np.random.default_rng(4)
    observed = rng.normal(0, 1, size=150)
    forecast = observed + rng.normal(0, 0.2, size=150)

    def _mae(f, o):
        return float(np.mean(np.abs(f - o)))

    result = bootstrap_difference_ci(_mae, (forecast, observed), (forecast, observed), n_bootstrap=300, seed=5)
    assert result["point_estimate"] == 0.0
    assert result["ci_lower"] <= 0.0 <= result["ci_upper"]


def test_bootstrap_difference_ci_detects_genuinely_better_forecast():
    rng = np.random.default_rng(5)
    observed = rng.normal(0, 1, size=200)
    good_forecast = observed + rng.normal(0, 0.05, size=200)
    bad_forecast = rng.normal(0, 1, size=200)  # unrelated to observed

    def _mae(f, o):
        return float(np.mean(np.abs(f - o)))

    result = bootstrap_difference_ci(_mae, (good_forecast, observed), (bad_forecast, observed), n_bootstrap=500, seed=6)
    assert result["point_estimate"] < 0  # good forecast has lower MAE than bad
    assert result["ci_upper"] < 0  # CI excludes zero -> significant difference


def test_block_bootstrap_indices_block_size_one_covers_full_range():
    rng = np.random.default_rng(6)
    idx = block_bootstrap_indices(50, block_size=1, n_bootstrap=200, rng=rng)
    assert idx.shape == (200, 50)
    assert idx.min() >= 0
    assert idx.max() <= 49


def test_block_bootstrap_indices_preserves_contiguous_blocks():
    rng = np.random.default_rng(7)
    idx = block_bootstrap_indices(20, block_size=4, n_bootstrap=10, rng=rng)
    # within each block of 4 consecutive output positions, indices must be consecutive integers
    # (i.e. drawn as one contiguous run from the original series, not resampled independently)
    for row in idx:
        for b in range(0, 20, 4):
            block = row[b:b + 4]
            if len(block) == 4:
                assert np.all(np.diff(block) == 1)
