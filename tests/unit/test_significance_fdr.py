import numpy as np

from significance.fdr import benjamini_hochberg


def test_benjamini_hochberg_known_textbook_example():
    # sorted p-values 0.01,0.02,0.03,0.04 all <= k/m*alpha (0.01,0.02,0.03,0.04); 0.5 > 0.05.
    # -> reject the first four, not the last.
    pvalues = np.array([0.03, 0.01, 0.5, 0.04, 0.02])
    reject = benjamini_hochberg(pvalues, alpha=0.05)
    expected = np.array([True, True, False, True, True])
    np.testing.assert_array_equal(reject, expected)


def test_benjamini_hochberg_rejects_nothing_when_all_pvalues_high():
    pvalues = np.array([0.5, 0.6, 0.7, 0.8, 0.9])
    reject = benjamini_hochberg(pvalues, alpha=0.05)
    assert not reject.any()


def test_benjamini_hochberg_rejects_everything_when_all_pvalues_tiny():
    pvalues = np.array([0.0001, 0.0002, 0.0003, 0.0004])
    reject = benjamini_hochberg(pvalues, alpha=0.05)
    assert reject.all()


def test_benjamini_hochberg_excludes_nan_from_testing_and_never_rejects_them():
    pvalues = np.array([0.001, np.nan, 0.5, np.nan, 0.002])
    reject = benjamini_hochberg(pvalues, alpha=0.05)
    assert not reject[1] and not reject[3]  # NaN positions never rejected
    assert reject[0] and reject[4]  # tiny p-values among only 3 valid tests -> still rejected


def test_benjamini_hochberg_is_less_conservative_than_bonferroni_but_controls_more_than_uncorrected():
    # 100 p-values: 10 "true signals" (very small) + 90 "true nulls" (uniform under H0).
    rng = np.random.default_rng(0)
    signal_p = rng.uniform(0, 0.001, size=10)
    null_p = rng.uniform(0, 1, size=90)
    pvalues = np.concatenate([signal_p, null_p])

    uncorrected_rejections = int(np.sum(pvalues <= 0.05))
    bh_rejections = int(benjamini_hochberg(pvalues, alpha=0.05).sum())
    bonferroni_rejections = int(np.sum(pvalues <= 0.05 / 100))

    # BH should reject at least the true signals, fewer (or equal) than naive uncorrected
    # thresholding, and at least as many as the much stricter Bonferroni correction.
    assert bh_rejections >= 10
    assert bh_rejections <= uncorrected_rejections
    assert bh_rejections >= bonferroni_rejections


def test_fdr_correction_map_wraps_benjamini_hochberg_for_xarray():
    import xarray as xr

    from significance.fdr import fdr_correction_map

    pvals = xr.DataArray(
        np.array([[0.01, 0.5], [0.02, np.nan]]), dims=["lat", "lon"], coords={"lat": [1.0, 2.0], "lon": [3.0, 4.0]}
    )
    result = fdr_correction_map(pvals, alpha=0.05)
    assert result.attrs["n_tests"] == 3
    assert bool(result.isel(lat=0, lon=0)) is True
    assert bool(result.isel(lat=1, lon=1)) is False  # NaN cell never significant
