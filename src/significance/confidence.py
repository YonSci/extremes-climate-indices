"""Skill-based confidence layer (spec section 14): combine skill, significance, agreement, and sample
size into a single categorical "how much should you trust this map" layer.

Deliberately avoids the word "certain" anywhere (per spec) — the categories
are Higher / Moderate / Lower confidence, or Insufficient evidence when a
hard precondition (sample size, data quality, correction stability) fails
regardless of what the skill/significance signals say.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

INSUFFICIENT_EVIDENCE = 0
LOWER_CONFIDENCE = 1
MODERATE_CONFIDENCE = 2
HIGHER_CONFIDENCE = 3

CONFIDENCE_LABELS = {
    INSUFFICIENT_EVIDENCE: "Insufficient evidence",
    LOWER_CONFIDENCE: "Lower confidence",
    MODERATE_CONFIDENCE: "Moderate confidence",
    HIGHER_CONFIDENCE: "Higher confidence",
}


def compute_confidence_layer(
    *,
    bss: xr.DataArray | None = None,
    roc_auc: xr.DataArray | None = None,
    ensemble_agreement: xr.DataArray | None = None,
    statistically_significant: xr.DataArray | None = None,
    n_hindcast_years: int | xr.DataArray = 0,
    observation_quality_ok: bool | xr.DataArray = True,
    bias_correction_stable: bool | xr.DataArray = True,
    low_skill_bss_threshold: float = 0.0,
    low_skill_auc_threshold: float = 0.55,
    min_sample_years: int = 15,
    min_ensemble_agreement: float = 0.6,
    higher_confidence_fraction: float = 0.75,
    moderate_confidence_fraction: float = 0.4,
) -> xr.DataArray:
    """Combine whichever signals are available into a categorical confidence map.

    Any subset of ``bss``/``roc_auc``/``ensemble_agreement``/
    ``statistically_significant`` may be omitted (e.g. a product with no
    directly matching hindcast-verification skill map yet) — the category is
    then based on the fraction of *supplied* signals that pass their
    threshold, not a fixed all-four checklist. At least one signal must be
    supplied.

    Hard preconditions (insufficient hindcast sample, known-bad observation
    quality, an unstable bias correction) short-circuit straight to
    ``INSUFFICIENT_EVIDENCE`` regardless of what the skill signals say —
    skill numbers computed from too few years, or from data known to be
    compromised, are not trustworthy inputs to begin with.
    """
    signal_checks = []
    if bss is not None and roc_auc is not None:
        signal_checks.append((bss > low_skill_bss_threshold) & (roc_auc > low_skill_auc_threshold))
    elif bss is not None:
        signal_checks.append(bss > low_skill_bss_threshold)
    elif roc_auc is not None:
        signal_checks.append(roc_auc > low_skill_auc_threshold)

    if ensemble_agreement is not None:
        signal_checks.append(ensemble_agreement >= min_ensemble_agreement)
    if statistically_significant is not None:
        signal_checks.append(statistically_significant.astype(bool))

    if not signal_checks:
        raise ValueError(
            "At least one of bss/roc_auc, ensemble_agreement, or statistically_significant must be supplied."
        )

    n_signals = len(signal_checks)
    passed_fraction = sum(check.astype(float) for check in signal_checks) / n_signals

    category = xr.where(
        passed_fraction >= higher_confidence_fraction, HIGHER_CONFIDENCE,
        xr.where(passed_fraction >= moderate_confidence_fraction, MODERATE_CONFIDENCE, LOWER_CONFIDENCE),
    )

    insufficient = (
        (np.asarray(n_hindcast_years) < min_sample_years)
        | (~np.asarray(observation_quality_ok, dtype=bool))
        | (~np.asarray(bias_correction_stable, dtype=bool))
    )
    category = xr.where(insufficient, INSUFFICIENT_EVIDENCE, category)

    category = category.astype("int8")
    category.attrs["categories"] = {int(k): v for k, v in CONFIDENCE_LABELS.items()}
    category.attrs["n_signals_used"] = n_signals
    category.attrs["min_sample_years"] = min_sample_years
    return category
