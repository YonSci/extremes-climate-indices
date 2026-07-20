import numpy as np
import pytest
import xarray as xr

from significance.confidence import (
    HIGHER_CONFIDENCE,
    INSUFFICIENT_EVIDENCE,
    LOWER_CONFIDENCE,
    compute_confidence_layer,
)


def _da(value):
    return xr.DataArray(value)


def test_all_signals_strong_gives_higher_confidence():
    result = compute_confidence_layer(
        bss=_da(0.3), roc_auc=_da(0.8), ensemble_agreement=_da(0.9), statistically_significant=_da(True),
        n_hindcast_years=30,
    )
    assert int(result) == HIGHER_CONFIDENCE


def test_all_signals_weak_gives_lower_confidence_not_insufficient():
    result = compute_confidence_layer(
        bss=_da(-0.2), roc_auc=_da(0.5), ensemble_agreement=_da(0.3), statistically_significant=_da(False),
        n_hindcast_years=30,
    )
    assert int(result) == LOWER_CONFIDENCE


def test_insufficient_hindcast_years_overrides_strong_skill():
    result = compute_confidence_layer(
        bss=_da(0.5), roc_auc=_da(0.9), ensemble_agreement=_da(0.95), statistically_significant=_da(True),
        n_hindcast_years=5,  # below default min_sample_years=15
    )
    assert int(result) == INSUFFICIENT_EVIDENCE


def test_bad_observation_quality_forces_insufficient_evidence():
    result = compute_confidence_layer(
        bss=_da(0.5), roc_auc=_da(0.9), n_hindcast_years=30, observation_quality_ok=False,
    )
    assert int(result) == INSUFFICIENT_EVIDENCE


def test_unstable_bias_correction_forces_insufficient_evidence():
    result = compute_confidence_layer(
        bss=_da(0.5), roc_auc=_da(0.9), n_hindcast_years=30, bias_correction_stable=False,
    )
    assert int(result) == INSUFFICIENT_EVIDENCE


def test_works_with_only_one_signal_supplied():
    result = compute_confidence_layer(bss=_da(0.5), n_hindcast_years=30)
    assert int(result) == HIGHER_CONFIDENCE
    result_low = compute_confidence_layer(bss=_da(-0.5), n_hindcast_years=30)
    assert int(result_low) == LOWER_CONFIDENCE


def test_raises_when_no_signals_supplied():
    with pytest.raises(ValueError):
        compute_confidence_layer(n_hindcast_years=30)


def test_never_uses_the_word_certain_in_labels():
    result = compute_confidence_layer(bss=_da(0.5), n_hindcast_years=30)
    for label in result.attrs["categories"].values():
        assert "certain" not in label.lower()


def test_works_across_a_grid():
    lat, lon = [8.0, 9.0], [38.0, 39.0]
    bss = xr.DataArray(np.array([[0.3, -0.2], [0.1, 0.4]]), dims=["lat", "lon"], coords={"lat": lat, "lon": lon})
    auc = xr.DataArray(np.array([[0.8, 0.5], [0.6, 0.9]]), dims=["lat", "lon"], coords={"lat": lat, "lon": lon})
    result = compute_confidence_layer(bss=bss, roc_auc=auc, n_hindcast_years=30)
    assert result.dims == ("lat", "lon")
    assert result.dtype == np.int8
