import numpy as np
import pandas as pd
import xarray as xr

from indices.spells import (
    classify_dry_wet,
    consecutive_dry_days,
    consecutive_wet_days,
    dry_spell_events,
    dry_spell_probability,
    max_consecutive_run,
)


def _time_series(values):
    times = pd.date_range("2026-06-01", periods=len(values))
    return xr.DataArray(values, dims=["time"], coords={"time": times})


def test_classify_dry_wet_threshold_boundaries():
    da = _time_series([0.0, 0.9, 1.0, 1.1])
    is_dry = classify_dry_wet(da, threshold_mm=1.0)
    # dry: < threshold; wet: >= threshold
    np.testing.assert_array_equal(is_dry.values, [True, True, False, False])


def test_ten_completely_dry_days_produce_cdd_10():
    da = _time_series([0.0] * 10)
    cdd = consecutive_dry_days(da, threshold_mm=1.0)
    assert float(cdd) == 10.0


def test_cdd_with_single_wet_day_breaks_the_run():
    da = _time_series([0.0, 0.0, 0.0, 5.0, 0.0, 0.0])
    cdd = consecutive_dry_days(da, threshold_mm=1.0)
    assert float(cdd) == 3.0  # longest run is the first 3 dry days


def test_ten_completely_wet_days_produce_cwd_10():
    da = _time_series([5.0] * 10)
    cwd = consecutive_wet_days(da, threshold_mm=1.0)
    assert float(cwd) == 10.0


def test_max_consecutive_run_handles_nan_as_run_breaking():
    is_true = xr.DataArray([1.0, 1.0, np.nan, 1.0, 1.0, 1.0], dims=["time"])
    run = max_consecutive_run(is_true)
    assert float(run) == 3.0  # NaN breaks the run; longest remaining run is 3


def test_seven_day_dry_sequence_qualifies_for_5_and_7_but_not_9_day_threshold():
    da = _time_series([0.0] * 7 + [5.0])
    events_5 = dry_spell_events(da, threshold_mm=1.0, min_length_days=5)
    events_7 = dry_spell_events(da, threshold_mm=1.0, min_length_days=7)
    events_9 = dry_spell_events(da, threshold_mm=1.0, min_length_days=9)

    assert bool(events_5["has_qualifying_spell"]) is True
    assert bool(events_7["has_qualifying_spell"]) is True
    assert bool(events_9["has_qualifying_spell"]) is False
    assert float(events_5["max_spell_duration_days"]) == 7.0


def test_dry_spell_events_reports_correct_start_index_and_count():
    # dry days at indices 0-2 (run of 3) and 5-9 (run of 5); threshold = 5
    values = [0, 0, 0, 5, 5, 0, 0, 0, 0, 0]
    da = _time_series([float(v) for v in values])
    events = dry_spell_events(da, threshold_mm=1.0, min_length_days=5)
    assert float(events["n_qualifying_spells"]) == 1.0
    assert float(events["first_spell_start_dayindex"]) == 5.0
    assert float(events["max_spell_duration_days"]) == 5.0


def test_dry_spell_probability_across_ensemble_members():
    # 3 members: CDD = 4, 8, 10 -> P(CDD>=7) should be 2/3
    cdd = xr.DataArray([4.0, 8.0, 10.0], dims=["realization"])
    prob = dry_spell_probability(cdd, min_length_days=7)
    assert abs(float(prob) - 2.0 / 3.0) < 1e-9
