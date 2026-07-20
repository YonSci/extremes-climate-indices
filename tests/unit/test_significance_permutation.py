import numpy as np

from significance.permutation import permutation_test_difference, permutation_test_statistic


def _correlation(f, o):
    return float(np.corrcoef(f, o)[0, 1])


def _mae(f, o):
    return float(np.mean(np.abs(f - o)))


def test_permutation_test_rejects_null_for_genuinely_skilled_forecast():
    rng = np.random.default_rng(0)
    observed = rng.normal(0, 1, size=100)
    forecast = observed + rng.normal(0, 0.1, size=100)  # strongly correlated
    result = permutation_test_statistic(_correlation, forecast, observed, n_permutations=500, seed=1, alternative="greater")
    assert result["p_value"] < 0.01


def test_permutation_test_does_not_reject_null_for_unrelated_forecast():
    rng = np.random.default_rng(2)
    observed = rng.normal(0, 1, size=100)
    forecast = rng.normal(0, 1, size=100)  # independent of observed
    result = permutation_test_statistic(_correlation, forecast, observed, n_permutations=500, seed=3, alternative="greater")
    assert result["p_value"] > 0.05


def test_permutation_test_p_value_is_valid_probability():
    rng = np.random.default_rng(4)
    observed = rng.normal(0, 1, size=50)
    forecast = rng.normal(0, 1, size=50)
    result = permutation_test_statistic(_correlation, forecast, observed, n_permutations=300, seed=5)
    assert 0.0 <= result["p_value"] <= 1.0


def test_permutation_difference_detects_genuinely_better_forecast():
    rng = np.random.default_rng(6)
    observed = rng.normal(0, 1, size=150)
    good_forecast = observed + rng.normal(0, 0.05, size=150)
    bad_forecast = rng.normal(0, 1, size=150)
    result = permutation_test_difference(_mae, good_forecast, bad_forecast, observed, n_permutations=500, seed=7, alternative="less")
    assert result["observed_difference"] < 0  # good has lower MAE
    assert result["p_value"] < 0.01


def test_permutation_difference_no_significant_difference_for_identical_forecasts():
    rng = np.random.default_rng(8)
    observed = rng.normal(0, 1, size=100)
    forecast = observed + rng.normal(0, 0.2, size=100)
    result = permutation_test_difference(_mae, forecast, forecast, observed, n_permutations=300, seed=9, alternative="two-sided")
    assert result["observed_difference"] == 0.0
    assert result["p_value"] > 0.5
