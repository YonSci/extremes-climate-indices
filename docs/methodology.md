# Scientific Methodology — Index Definitions and Computation Methods

This document describes exactly how each climate index is computed, with the
formula/algorithm, the module and function that implement it, and the
scientific choices behind it. See [architecture.md](architecture.md) for system
design and [operations.md](operations.md) for how to run the pipeline, the CLI
reference, and the log of bugs found and fixed while building this.

All indices operate on the full ensemble (dimension `realization`) and only
reduce to a single number when explicitly computing an ensemble statistic or
probability — no index is computed on a pre-averaged ensemble mean.

---

## 1. Rainfall total, anomaly, percentage anomaly, percentile

Module: [`src/indices/rainfall.py`](../src/indices/rainfall.py)

**Rainfall total** — `member_totals = lead_window.sum(dim="time", skipna=True, min_count=1)`,
i.e. the accumulated precipitation over the requested valid window, computed
separately for every ensemble member. `ensemble_statistics()` then derives, across
members (NaN members excluded via `skipna=True`, per the missing-ensemble-member
QC finding — see operations.md §3):

- mean, median, standard deviation, IQR (Q75−Q25)
- configurable quantiles (default 10th/25th/50th/75th/90th, `indices.ensemble_quantiles`)
- min, max
- `n_members_used` (so a map/export can show how many members actually
  contributed at each cell, since this varies by year for the hindcast)

**Absolute anomaly** (`absolute_anomaly()`): `A_m = T_m − Clim`, computed
**per member before any aggregation** (spec requirement — anomalies are never
derived from an already-averaged forecast).

**Percentage anomaly** (`percent_anomaly()`): `P_m = 100 · (T_m − Clim) / Clim`,
with a safeguard: any cell where `Clim < min_climatological_denominator_mm`
(default 1.0 mm, config `indices.min_climatological_denominator_mm`) is masked
to `NaN` rather than producing an artificially extreme percentage from a
near-zero denominator. The count of masked cells is recorded in
`.attrs["masked_cell_count"]`.

**Rainfall percentile** (`rainfall_percentile()`): the percentile rank (0–100)
of each ensemble member's total against the historical distribution for the
*exact same calendar-valid window* (never the whole containing month), via
`scipy.stats.percentileofscore(historical_sample, member_value, kind="mean")`,
vectorized per grid cell with `xr.apply_ufunc`.

**Percentile category** (`percentile_category()`): classifies a percentile array
into the 7-category WMO/IRI scheme, thresholds configurable
(`indices.percentile_category_edges`, default `[10, 20, 33, 67, 80, 90]`):

| Percentile range | Category |
|---|---|
| < 10 | exceptionally dry |
| 10–20 | very dry |
| 20–33 | dry |
| 33–67 | near normal |
| 67–80 | wet |
| 80–90 | very wet |
| > 90 | exceptionally wet |

**Threshold probabilities** (`probability_exceed()`, `probability_below()`,
`probability_below_percentile()`, `probability_above_percentile()`): fraction of
non-NaN ensemble members satisfying a condition, e.g. `P(total >= threshold)`.
NaN members are excluded from both numerator and denominator, never counted as
"not exceeding."

---

## 2. Consecutive Dry Days (CDD) / Consecutive Wet Days (CWD)

Module: [`src/indices/spells.py`](../src/indices/spells.py)

**No-rain threshold** (τ, spec §9.6): user-configurable, default 1.0 mm/day
(`indices.default_no_rain_threshold_mm`, selectable from
`indices.no_rain_thresholds_mm = [0.1, 0.5, 1.0, 2.0]` or a custom value). Dry
day: `precip < τ`. Wet day: `precip >= τ`. The threshold actually used is
written into every map footer and export's metadata (`no_rain_threshold_mm`).

**CDD/CWD algorithm**: for a boolean "is dry" (or "is wet") series along the
time axis, the longest run of `True` values is found via a run-length-encoding
trick — pad with `0`, take `diff()`, and the run length is
`(end_index − start_index)` for each `1 → -1` transition:

```python
bounded = np.concatenate(([0], a.astype(int), [0]))
diffs = np.diff(bounded)
starts = np.flatnonzero(diffs == 1)
ends = np.flatnonzero(diffs == -1)
max_run = (ends - starts).max()
```

This is vectorized per grid-cell/member via `xr.apply_ufunc(..., vectorize=True)`.
**Missing days (NaN) break a run rather than extending or ignoring it** — a
conservative choice: it can only shorten a detected spell, never fabricate one
across a data gap.

**Boundary handling** (spec §9.7, config `indices.dry_spell_boundary_mode`,
default `"seamless"`): three modes are supported for how a spell that would
cross the edge of the forecast window is treated:

- `strict_window` — CDD/CWD computed only within the requested valid window.
- `seamless` (default) — `extend_window_with_preceding_observations()` prepends
  N days of *observed* rainfall (identical across all ensemble members, since
  observations aren't an ensemble) before the forecast window, so a dry spell
  that started before the forecast's first day is correctly counted from its
  real start. Verified against real data: a hindcast year's forecast window
  extended with 5 preceding CHIRPS days correctly extended a 7-day forecast-only
  CDD to 10 days once the preceding dry days were included.
- `obs_plus_forecast` — reserved for the same seamless-series approach applied
  explicitly across the obs→forecast transition (currently implemented via the
  same `extend_window_with_preceding_observations()` function).

## 3. Dry-spell event detection and probability

Module: `src/indices/spells.py`, function `dry_spell_events()`.

For a user-selected minimum spell duration (5/7/9 days or custom,
`indices.dry_spell_lengths_days`), each ensemble member is scanned for
**qualifying** runs (duration ≥ threshold), producing per member:

- `has_qualifying_spell` (bool)
- `n_qualifying_spells` (count)
- `max_spell_duration_days`
- `first_spell_start_dayindex` (0-based index into the window; convert to a
  calendar date via the window's own day coordinate)

**Dry-spell probability** (`dry_spell_probability()`): `P(max CDD >= N)` across
ensemble members — reuses the same NaN-safe fraction-of-members logic as the
rainfall threshold probabilities. The same function is applied to a
*historical* CDD distribution (one value per climatology year, `clim_year`
playing the role `realization` normally does) to get the **climatological**
dry-spell probability for the departure-map comparison — see operations.md's
description of `generate_cdd_dryspell_products()`.

---

## 4. Standardized Precipitation Index / standardized short-duration anomaly

Module: [`src/indices/spi.py`](../src/indices/spi.py)

**Distribution fit**: a **zero-inflated gamma** distribution is fit to the
historical accumulated-rainfall distribution for the exact calendar window
(the same distribution used for the rainfall-percentile index):

1. `q_zero = P(X = 0)` estimated empirically as the fraction of climatology
   years with zero accumulated rainfall in the window.
2. The gamma shape/scale parameters for the non-zero values are estimated using
   **Thom's (1958) approximation** — the closed-form estimator used by
   operational SPI software (McKee et al. 1993 / NOAA / WMO implementations),
   not `scipy.stats.gamma.fit`'s iterative MLE:

   ```
   A = ln(mean(x)) − mean(ln(x))
   shape = (1 + sqrt(1 + 4A/3)) / (4A)
   scale = mean(x) / shape
   ```

   This was chosen over iterative MLE for two reasons: it is the field-standard
   method, and it is dramatically faster at this grid's cell count — see
   operations.md §3 for the ~20x performance regression this fixed.
3. **Goodness-of-fit**: a Kolmogorov–Smirnov test compares the non-zero sample
   against the fitted gamma CDF. If the p-value is below
   `indices.spi_gof_pvalue_threshold` (default 0.01), or the climatology sample
   has fewer than `indices.spi_min_sample_size` years (default 20) or fewer than
   4 non-zero values, the fit is rejected.
4. **Empirical fallback**: when the gamma fit is rejected, the transform instead
   uses the empirical CDF of the historical sample directly (Weibull-style rank
   position, `rank / (n + 1)`), guaranteeing a finite, sensible statistic is
   always produced.

**Transform**: `H(x) = q_zero + (1 − q_zero) · G(x)` for the fitted gamma CDF
`G` (this correctly reduces to `H(0) = q_zero` since `G(0) = 0`, so zero and
non-zero precipitation are handled by one formula). `H(x)` is clipped to
`[1e-6, 1 − 1e-6]` and transformed to a standard-normal quantile via
`scipy.stats.norm.ppf`.

**Performance note**: the fit is done **once per grid cell**, not once per
ensemble member — `realization` is passed as an `apply_ufunc` *core* dimension
alongside the fit inputs so all members of a cell are transformed against the
same already-fitted parameters in one call. Fitting is idempotent across
members (same historical sample), so refitting per member was pure waste; see
the bug log in operations.md.

**Labeling** (`index_label()`, spec §9.5 requirement — never present a
sub-monthly window as conventional SPI): the label depends on whether the
requested period is a calendar-month-aligned "seasonal" period or a day-count
"sub-seasonal" period:

- Sub-seasonal periods (week1, week1_2, week3_4, …) → **"Standardized N-day
  Precipitation Anomaly"**, regardless of N.
- Seasonal periods (June, JJAS, MJJASO, …), which are always whole
  calendar-month multiples → **"SPI-N"** where N = number of months
  (June → SPI-1, JJAS → SPI-4, MJJASO → SPI-6).

**Drought-threshold probability** (`probability_spi_below()`):
`P(ensemble member's SPI <= threshold)`, e.g. threshold = −1.0 or −1.5.

---

## 5. Climatology (observational)

Module: [`src/climatology/observational.py`](../src/climatology/observational.py),
built on [`src/preprocessing/temporal.py`](../src/preprocessing/temporal.py).

Climatology is always computed for the **exact calendar-valid window** of the
forecast — e.g. a forecast valid 16–29 May is compared against 16–29 May in
every climatology year, not the whole May climatology. Two window-extraction
paths:

- `select_calendar_window()` — arbitrary month/day start-end (used for
  sub-seasonal periods), with explicit year-wraparound handling
  (`calendar_window_bounds()`) for windows like DJF that cross a year boundary,
  and a hard error (not a silent shift) on invalid dates like 29 Feb in a
  non-leap year.
- `select_season_window()` — named calendar-month seasons (JJAS, June, …),
  including DJF-style wraparound where trailing months belong to the following
  year's label.

Both drop (with a logged warning) any climatology year that has zero matching
timestamps, rather than silently including an empty/all-NaN year that would
bias the mean downward.

`calendar_window_totals()` / `season_totals()` sum each year's window to a
single accumulated total, producing the `(clim_year, lat, lon)` **historical
distribution** array that every other index (percentile, SPI, anomaly) is
calibrated against. `climatology_statistics()` derives mean/median/sd/quantiles
across `clim_year` — this is what the three-map layout's **middle panel**
("Historical Climatology" / "Normal Conditions") is rendered from, per spec's
explicit instruction not to label this panel "climatology forecast."

---

## 6. Data preparation (feeds every index above)

- **Units** ([`preprocessing/units.py`](../src/preprocessing/units.py)): read
  from the file's own `units` attribute, never assumed; converts
  mm/day↔m↔kg m⁻²↔kg m⁻² s⁻¹ to the canonical `mm/day`, and raises rather than
  guessing if the attribute is missing/unrecognized. A `deaccumulate()` utility
  is provided for cumulative-since-init products (not needed by the current
  on-disk files, whose `GRIB_stepType` is already `instant`).
- **Quality control** ([`preprocessing/quality_control.py`](../src/preprocessing/quality_control.py)):
  negative precipitation, unrealistic highs, duplicate timestamps, temporal
  gaps, all-NaN grid cells, non-monotonic/duplicate coordinates, and — the
  check that matters most for this dataset — **missing ensemble members per
  year**, which correctly detects the real 25-member (1993–2016) →
  51-member (2017–2025) SEAS5 transition in the hindcast file.
- **Spatial** ([`preprocessing/spatial.py`](../src/preprocessing/spatial.py)):
  longitude/latitude normalization (done at load time), bbox clipping, and
  polygon (admin-boundary) clipping/masking via `clip_to_boundary()` — see
  operations.md for what this changed once the real shapefile was wired in.
  Regridding is conservative (area-weighted, via xESMF) when installed, with a
  bilinear fallback that is explicitly logged as non-conservative.

---

## 7. Probabilistic verification (spec section 12)

Modules: [`src/verification/hindcast.py`](../src/verification/hindcast.py) (leakage-free
data prep), [`src/verification/metrics.py`](../src/verification/metrics.py) (the
metric formulas), [`src/verification/skill_maps.py`](../src/verification/skill_maps.py)
(vectorizes the metrics across the grid).

### 7.1 Leakage-free hindcast data preparation

Every binary event that is *defined relative to a sample statistic of the
observational record* (a percentile, a fitted SPI distribution, a tercile) is
evaluated with a **leave-one-hindcast-year-out** threshold: the year being
scored never contributes to its own event definition.

- `leave_one_out_percentile()`: for each held-out year, the threshold is the
  requested percentile of every *other* year's total (`_leave_one_out_percentile_1d`,
  O(n²) per grid cell — cheap at n≈33 years).
- `leave_one_out_spi()`: for each held-out year, fits the zero-inflated-gamma/
  empirical SPI distribution (reusing `indices.spi.fit_and_transform_1d`) on
  every *other* year's observations, then transforms that year's ensemble
  forecast members **and** its own observed value against that fit.
- Events defined on an absolute constant (CDD ≥ 7 days) need no leave-one-out
  handling — the threshold doesn't depend on the sample at all.

**Ensemble size is fixed at 25 members for every hindcast year** (config
`verification.ensemble_size_for_verification`). The real SEAS5 hindcast file
only has a *consistently populated* 25 members across its full 1993-2025
span (the 51-member operational configuration only starts in 2017 — see
architecture.md §2); truncating to the first 25 members for every year avoids
mixing two different ensemble-size regimes within one skill estimate.

For each (period, event), `forecast_probability[year]` is the fraction of
(truncated) ensemble members satisfying the event's leave-one-out threshold;
`observed_binary[year]` is whether that year's actual observation did.

### 7.2 Metrics computed

All formulas are implemented as plain NumPy functions in `metrics.py`
(vectorized across the grid via `xr.apply_ufunc` in `skill_maps.py`), so each
is independently unit-testable against a hand-computable known answer (see
§20-style tests in `tests/unit/test_verification_metrics.py`):

- **Brier Score / Brier Skill Score**: `BS = mean((p-o)²)`; `BSS = 1 - BS/BS_ref`
  where the reference is the **leave-one-out climatological event frequency**
  (`skill_maps.leave_one_out_mean`) — not the raw percentile, and not a single
  domain-wide constant, so BSS is itself leakage-free per grid cell.
- **Reliability diagram / sharpness / discrimination diagram**: binned
  forecast-probability vs. observed-frequency, computed on samples **pooled
  across the whole grid and all hindcast years** (`skill_maps.pool_domain_samples`)
  — a single grid cell's ~33 years is too few for a meaningful 10-bin
  reliability curve; pooling within the domain is standard practice in
  seasonal-forecast verification (e.g. IRI-style regional verification).
- **Reliability-resolution-uncertainty decomposition** (Murphy 1973):
  `BS = reliability − resolution + uncertainty`. Only exact when forecast
  probabilities are constant within each bin — see the docstring/test in
  `metrics.py` for the derivation and the deliberately bin-aligned test case.
- **ROC curve / AUC**: via `sklearn.metrics.roc_curve`/`roc_auc_score` (not
  hand-rolled — a standard, independently-tested implementation), computed
  both per-cell and domain-pooled.
- **RPS / RPSS**: 3-category (tercile) scheme — below/near/above-normal, with
  **leave-one-out tercile edges** (`hindcast.evaluate_tercile_categories`).
  Classic (non-normalized) RPS definition, range `[0, K-1]`.
- **CRPS / CRPSS**: energy-form (NRG) estimator, no external dependency
  (`CRPS = mean|x_m - o| - 0.5·mean|x_m - x_m'|`), reducing exactly to MAE for
  a single-member "ensemble" (tested). CRPSS's reference is a genuine
  **leave-one-out climatological pseudo-ensemble** — for held-out year *y*,
  the reference "ensemble" is every other year's observed value
  (`skill_maps.build_climatological_reference_ensemble`).
- **Rank histogram (Talagrand diagram)**: domain-pooled rank of the
  observation among sorted ensemble members.
- **Deterministic metrics**: bias, MAE, RMSE, Pearson correlation, Spearman
  correlation, Anomaly Correlation Coefficient (correlation of
  forecast/observed *anomalies* relative to the all-years climatological
  mean) — computed on the raw ensemble-mean rainfall total, not any binary
  event.
- **Contingency-table metrics** (ETS, POD, FAR, frequency bias): the binary
  "forecast says yes" indicator is `forecast_probability >= 0.5` — a
  simplification (spec doesn't mandate a specific deterministic-conversion
  rule); documented here rather than left implicit.
- **Spread-error relationship**: per-year ensemble spread (std across
  members) vs. absolute error of the ensemble mean, plus their correlation
  across years — a well-calibrated ensemble has a positive spread-error
  correlation.

### 7.3 Real verification results (Ethiopia, 1993-2025 hindcast vs. CHIRPS)

Running the full pipeline against the real data produced a scientifically
credible pattern — skill degrading with lead time, which is exactly what a
genuine (not buggy) verification should show:

| Period | Mean ACC (rainfall total) | Mean BSS (below-20th-pct) | Mean ROC AUC (below-20th-pct) |
|---|---|---|---|
| week1_2 (days 1-14) | 0.645 | 0.165 | 0.810 |
| week3_4 (days 15-28) | 0.392 | 0.046 | 0.738 |
| JJAS (seasonal, ~2 month lead) | 0.208 | -0.104 | 0.642 |

JJAS's near-chance skill for *binary threshold* events (BSS ≈ 0 or negative,
AUC ≈ 0.5-0.6 for CDD events specifically) alongside still-positive *raw
rainfall-total* correlation skill (ACC > 0 for 82% of land cells) is a
realistic and well-documented pattern in seasonal forecasting: general
above/below-normal pattern skill is generally more attainable than sharp
extreme/threshold skill at long lead times. See
[operations.md §5](operations.md#5-real-verification-results-and-what-they-mean)
for the full results and what they imply for how these products should (and
should not) be used operationally.

---

## 8. Statistical significance testing (spec section 13)

Modules: [`src/significance/bootstrap.py`](../src/significance/bootstrap.py),
[`src/significance/permutation.py`](../src/significance/permutation.py),
[`src/significance/fdr.py`](../src/significance/fdr.py),
[`src/significance/anomaly_significance.py`](../src/significance/anomaly_significance.py),
[`src/significance/confidence.py`](../src/significance/confidence.py).

### 8.1 Skill-score significance (bootstrap and permutation)

Both modules resample **whole hindcast years**, never individual ensemble
members — members are correlated realizations of the same forecast period,
not independent samples of inter-annual variability (spec's explicit
warning, carried through consistently from §13.1 to §13.2 here).

- `bootstrap.bootstrap_statistic_ci()`: percentile-bootstrap CI for any metric
  function of paired (forecast, observed) year-arrays; `block_bootstrap_indices()`
  supports resampling in contiguous blocks (`block_size > 1`) to preserve
  short-range serial dependence, though independent hindcast years default to
  `block_size=1` (plain case resampling).
- `bootstrap.bootstrap_difference_ci()`: CI for the *difference* between two
  forecasts' skill (e.g. raw vs. bias-corrected), using **paired** resampling
  — the same year-indices are drawn for both forecasts each resample, which
  isolates genuine skill difference from shared sampling variability that
  independent bootstraps of each forecast would not.
- `permutation.permutation_test_statistic()` / `permutation_test_difference()`:
  null distributions built by shuffling which year's observation pairs with
  which year's forecast (or which forecast produced which year's prediction),
  giving an exact-ish p-value without a normality assumption.

### 8.2 Operational forecast-departure significance

This answers a different question than hindcast-skill significance: *given
the one real ensemble forecast that was just issued, is its departure from
climatology larger than the climatological reference's own sampling
uncertainty could plausibly explain?*

- `anomaly_significance.bootstrap_anomaly_ci()`: the forecast ensemble is
  fixed (it's the one real forecast); what's resampled is **which years
  happened to define "normal"** — climatology years are resampled with
  replacement, the climatological mean recomputed each draw, giving a
  bootstrap distribution for `(forecast ensemble mean) − (resampled
  climatological mean)`. Also returns a two-sided bootstrap p-value for FDR
  correction.
- `anomaly_significance.mann_whitney_significance()`: a nonparametric,
  distribution-comparison alternative — is the forecast ensemble's
  *distribution* (as a collective sample) shifted relative to climatology's
  distribution? This is a legitimate use of the ensemble (comparing two
  distributions), distinct from the invalid use spec 13.1 warns against
  (treating individual members as independent estimates of the forecast's own
  sampling uncertainty).
- `anomaly_significance.practical_significance_mask()` (spec 13.4): configurable
  practical thresholds (±20% rainfall anomaly, SPI < −1, CDD anomaly > 3 days,
  dry-spell probability > 60%) combined with OR — a departure can be
  practically significant without being statistically distinguishable from
  noise (small sample), or statistically significant yet practically trivial;
  both are reported, neither alone.

### 8.3 Spatial multiple-testing correction

`fdr.benjamini_hochberg()`: standard BH procedure — sort p-values ascending,
find the largest `k` with `p₍ₖ₎ ≤ (k/m)·α`, reject all hypotheses with
`p ≤ p₍ₖ₎`. NaN p-values (cells with too little data to test) are excluded
from both the correction and the count of tests `m`. Chosen over Bonferroni
because it controls the *expected proportion of false positives among
flagged cells* rather than the probability of *any* false positive across
the whole domain — appropriate for a spatial map where a handful of isolated
false positives is a far smaller problem than an overly conservative test
that hides every genuine signal. Verified (`test_significance_fdr.py`) to
reject more than Bonferroni and no more than uncorrected thresholding, on a
synthetic true-signal/true-null mixture.

Applied in `run_significance.py` to `bootstrap_anomaly_ci()`'s per-cell
p-values before hatching the departure panel of the three-panel map — the
hatching in `outputs/maps/*_departure_significance.png` is **FDR-corrected**,
not per-cell-uncorrected significance.

### 8.4 Confidence layer (spec section 14)

`confidence.compute_confidence_layer()` combines whichever signals are
available (skill from verification, ensemble agreement, FDR-corrected
significance) into four categories — **Insufficient evidence / Lower /
Moderate / Higher confidence** (never "certain," per spec). Hard
preconditions (hindcast sample below `min_sample_years`, known-bad
observation quality, an unstable bias correction) short-circuit straight to
*Insufficient evidence* regardless of what the skill signals say.

**Calibration note, found by inspecting the real JJAS output**: the default
`low_skill_bss_threshold=0.0` is permissive — any positive skill value counts
as "passing." For the real 2026 JJAS run, feeding the *rainfall-total* ACC
skill map (mean 0.208, positive in 82% of cells) into the confidence layer
produced 39% of cells labeled "Higher confidence," even though the *same
period's* binary-extreme-event BSS (§7.3) is weak-to-negative. This is not
inconsistent — general-pattern correlation skill and extreme-event skill are
different things, and both are real, verified numbers — but it means the
confidence label answers "is the general rainfall-total pattern trustworthy,"
not "are extreme-event probabilities for this exact index trustworthy,"
unless the caller feeds in an event-specific BSS/AUC map instead. Documented
here rather than silently shipped; `low_skill_bss_threshold` and which skill
map gets passed in are both caller-controlled per spec's configurability
requirement.
