import numpy as np

from verification.metrics import (
    anomaly_correlation_coefficient,
    brier_score,
    brier_skill_score,
    contingency_table,
    crps_ensemble,
    crps_skill_score,
    equitable_threat_score,
    false_alarm_ratio,
    frequency_bias,
    mean_absolute_error,
    mean_bias,
    probability_of_detection,
    ranked_probability_score,
    rank_histogram,
    reliability_diagram,
    reliability_resolution_uncertainty,
    roc_curve_auc,
    root_mean_squared_error,
    sharpness_histogram,
    spread_error,
)


def test_perfect_probability_forecast_gives_brier_score_zero():
    forecast = np.array([1.0, 0.0, 1.0, 0.0])
    observed = np.array([1.0, 0.0, 1.0, 0.0])
    assert brier_score(forecast, observed) == 0.0


def test_worst_possible_forecast_gives_brier_score_one():
    forecast = np.array([0.0, 1.0])
    observed = np.array([1.0, 0.0])
    assert brier_score(forecast, observed) == 1.0


def test_forecast_equal_to_climatology_gives_bss_near_zero():
    rng = np.random.default_rng(0)
    observed = rng.integers(0, 2, size=200).astype(float)
    clim_rate = observed.mean()
    forecast = np.full(200, clim_rate)
    bss = brier_skill_score(forecast, observed, reference_prob=clim_rate)
    assert abs(bss) < 1e-9  # forecast IS the reference -> exactly zero, not just "near"


def test_bss_positive_when_forecast_better_than_reference():
    observed = np.array([1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0])
    forecast = observed.copy()  # perfect
    reference = np.full(8, 0.5)  # uninformative climatology
    bss = brier_skill_score(forecast, observed, reference_prob=reference)
    assert bss == 1.0  # BS_forecast=0 -> BSS=1


def test_reliability_diagram_perfect_forecast_matches_diagonal():
    forecast = np.array([0.0, 0.0, 1.0, 1.0])
    observed = np.array([0.0, 0.0, 1.0, 1.0])
    diag = reliability_diagram(forecast, observed, n_bins=10)
    valid = ~np.isnan(diag["bin_center_forecast"])
    np.testing.assert_allclose(diag["bin_center_forecast"][valid], diag["observed_frequency"][valid])


def test_reliability_resolution_uncertainty_decomposition_sums_to_brier_score():
    # The Murphy (1973) decomposition is only EXACT when the forecast is constant
    # within each bin (it's an approximation otherwise, since "reliability" compares
    # the bin's mean forecast to the bin's observed frequency, not each raw p_i) —
    # so use forecasts already quantized to the bin centers used by n_bins=10.
    rng = np.random.default_rng(1)
    bin_values = np.arange(10) / 10.0 + 0.05  # 0.05, 0.15, ..., 0.95: one per bin
    forecast = rng.choice(bin_values, size=500)
    observed = (rng.uniform(0, 1, size=500) < forecast).astype(float)
    decomp = reliability_resolution_uncertainty(forecast, observed, n_bins=10)
    bs = brier_score(forecast, observed)
    assert abs(decomp["brier_score"] - bs) < 1e-9
    assert abs((decomp["reliability"] - decomp["resolution"] + decomp["uncertainty"]) - bs) < 1e-9


def test_sharpness_histogram_counts_sum_to_total():
    forecast = np.array([0.05, 0.15, 0.5, 0.95, 0.5, 0.5])
    hist = sharpness_histogram(forecast, n_bins=10)
    assert hist["counts"].sum() == 6
    assert abs(hist["fraction"].sum() - 1.0) < 1e-9


def test_roc_auc_perfect_discriminator_is_one():
    forecast = np.array([0.1, 0.2, 0.8, 0.9])
    observed = np.array([0.0, 0.0, 1.0, 1.0])
    result = roc_curve_auc(forecast, observed)
    assert result["auc"] == 1.0


def test_roc_auc_random_forecast_near_half():
    rng = np.random.default_rng(2)
    forecast = rng.uniform(0, 1, size=2000)
    observed = rng.integers(0, 2, size=2000).astype(float)
    result = roc_curve_auc(forecast, observed)
    assert abs(result["auc"] - 0.5) < 0.05


def test_roc_auc_undefined_with_single_class_returns_nan():
    forecast = np.array([0.1, 0.5, 0.9])
    observed = np.array([0.0, 0.0, 0.0])
    result = roc_curve_auc(forecast, observed)
    assert np.isnan(result["auc"])


def test_ranked_probability_score_perfect_forecast_is_zero():
    forecast = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    observed = np.array([0, 1, 2])
    assert ranked_probability_score(forecast, observed) == 0.0


def test_ranked_probability_score_worst_case_categorical_extremes():
    # forecast certain of category 0, but truth is always category 2 (max distance)
    forecast = np.array([[1.0, 0.0, 0.0]])
    observed = np.array([2])
    rps = ranked_probability_score(forecast, observed)
    assert rps == 2.0  # (1-0)^2 + (1-0)^2 + (1-1)^2 = 2, matches 3-category max


def test_crps_reduces_to_mae_for_single_member_ensemble():
    ensemble = np.array([[5.0], [10.0], [15.0]])
    observed = np.array([7.0, 8.0, 20.0])
    crps = crps_ensemble(ensemble, observed)
    mae = mean_absolute_error(ensemble[:, 0], observed)
    np.testing.assert_allclose(crps.mean(), mae)


def test_crps_zero_when_all_members_equal_observation():
    ensemble = np.full((3, 10), 5.0)
    observed = np.full(3, 5.0)
    crps = crps_ensemble(ensemble, observed)
    np.testing.assert_allclose(crps, 0.0, atol=1e-10)


def test_crps_skill_score_positive_when_forecast_beats_reference():
    ensemble_f = np.tile(np.array([4.9, 5.0, 5.1]), (5, 1))
    ensemble_ref = np.tile(np.array([0.0, 5.0, 10.0]), (5, 1))
    observed = np.full(5, 5.0)
    crps_f = crps_ensemble(ensemble_f, observed)
    crps_r = crps_ensemble(ensemble_ref, observed)
    assert crps_skill_score(crps_f, crps_r) > 0


def test_rank_histogram_counts_total_matches_n_years():
    ensemble = np.array([[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]], dtype=float)
    observed = np.array([0.5, 1.5, 2.5, 3.5])  # below, between, between, above all members
    hist = rank_histogram(ensemble, observed)
    assert hist.sum() == 4
    assert hist[0] == 1  # below all members -> rank 0
    assert hist[-1] == 1  # above all members -> rank = n_members


def test_spread_error_positive_correlation_when_higher_spread_means_higher_error():
    rng = np.random.default_rng(3)
    n = 200
    spread_true = rng.uniform(1, 10, size=n)
    ens_mean = rng.uniform(0, 100, size=n)
    errors = spread_true + rng.normal(0, 0.5, size=n)
    ensemble = ens_mean[:, None] + np.stack(
        [rng.normal(0, s, size=10) for s in spread_true]
    )
    observed = ens_mean + errors
    result = spread_error(ensemble, observed)
    assert result["spread_error_correlation"] > 0.3


def test_mean_bias_zero_for_unbiased_forecast():
    forecast = np.array([10.0, 20.0, 30.0])
    observed = np.array([10.0, 20.0, 30.0])
    assert mean_bias(forecast, observed) == 0.0
    assert mean_absolute_error(forecast, observed) == 0.0
    assert root_mean_squared_error(forecast, observed) == 0.0


def test_anomaly_correlation_coefficient_perfect_when_anomalies_match():
    forecast = np.array([10.0, 20.0, 5.0, 15.0])
    observed = np.array([10.0, 20.0, 5.0, 15.0])
    clim = 12.5
    assert abs(anomaly_correlation_coefficient(forecast, observed, clim) - 1.0) < 1e-9


def test_contingency_table_and_ets_perfect_forecast():
    forecast = np.array([1, 1, 0, 0])
    observed = np.array([1, 1, 0, 0])
    table = contingency_table(forecast, observed)
    assert table == {"hits": 2, "misses": 0, "false_alarms": 0, "correct_negatives": 2}
    assert equitable_threat_score(table) == 1.0
    assert probability_of_detection(table) == 1.0
    assert false_alarm_ratio(table) == 0.0
    assert frequency_bias(table) == 1.0


def test_contingency_table_all_misses_gives_pod_zero():
    forecast = np.array([0, 0, 0])
    observed = np.array([1, 1, 1])
    table = contingency_table(forecast, observed)
    assert probability_of_detection(table) == 0.0
