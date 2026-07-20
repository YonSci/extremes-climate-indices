"""POST /forecast/calculate + GET /forecast/status/{job_id}.

The actual computation reuses the exact same per-index functions the batch
CLI (`workflows.generate_products`) calls — the API triggers the same code
path in a background thread rather than a separate implementation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException

from api.catalog import get_output_dir, load_region_config
from api.jobs import job_manager
from api.schemas import ForecastCalculateRequest, IndexName, JobStatus
from utilities.export import build_metadata
from workflows.common import load_and_prepare, period_tag, resolve_window
from workflows.generate_products import (
    generate_cdd_cwd_products,
    generate_cdd_dryspell_products,
    generate_percentile_products,
    generate_rainfall_total,
    generate_spi_products,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["forecast"])


def _execute_calculate(request: ForecastCalculateRequest, output_dir: Path) -> dict[str, Any]:
    cfg = load_region_config(request.region)
    init_date = pd.Timestamp(request.init_date)
    clim_period = cfg.climatology.get(request.climatology_period or cfg.climatology.default_observational)
    no_rain_threshold = request.no_rain_threshold_mm or cfg.indices.default_no_rain_threshold_mm
    dry_spell_length = request.dry_spell_length_days or cfg.indices.default_dry_spell_length_days

    data = load_and_prepare(cfg, forecast_dataset=request.forecast_dataset, observation_dataset=request.observation_dataset)
    window = resolve_window(
        data, cfg, period_type=request.period_type.value, period_label=request.period,
        init_date=init_date, climatology_period=clim_period,
    )
    tag = period_tag(cfg.region.name, request.period, init_date)
    fc_spec = cfg.dataset(request.forecast_dataset)
    meta_base = build_metadata(
        init_date=str(init_date.date()), valid_start=str(window.valid_start.date()), valid_end=str(window.valid_end.date()),
        forecast_source=request.forecast_dataset, forecast_system_version="SEAS5",
        n_ensemble_members=int(window.lead_window.sizes["realization"]), observation_dataset=request.observation_dataset,
        climatology_period=clim_period.label, index_definition=request.index.value,
        bias_correction_method=fc_spec.bias_correction_method, rain_threshold_mm=no_rain_threshold,
        spatial_resolution_deg=cfg.region.resolution_deg,
    )

    if request.index == IndexName.rainfall_total:
        outputs = generate_rainfall_total(
            cfg, window, clim_period, meta_base, output_dir, tag, ensemble_statistic=request.ensemble_statistic,
        )
    elif request.index == IndexName.percentile:
        outputs = generate_percentile_products(
            cfg, window, clim_period, meta_base, output_dir, tag, ensemble_statistic=request.ensemble_statistic,
        )
    elif request.index in (IndexName.cdd, IndexName.cwd):
        both = generate_cdd_cwd_products(cfg, window, clim_period, meta_base, output_dir, tag, no_rain_threshold=no_rain_threshold)
        outputs = both[request.index.value]
    elif request.index == IndexName.dry_spell_probability:
        outputs = generate_cdd_dryspell_products(
            cfg, window, clim_period, meta_base, output_dir, tag,
            no_rain_threshold=no_rain_threshold, dry_spell_length=dry_spell_length,
        )
    elif request.index == IndexName.spi:
        outputs = generate_spi_products(
            cfg, window, clim_period, meta_base, output_dir, tag, ensemble_statistic=request.ensemble_statistic,
        )
    else:
        raise ValueError(f"Unhandled index '{request.index}'")

    return {"tag": tag, "index": request.index.value, "valid_start": str(window.valid_start.date()),
            "valid_end": str(window.valid_end.date()), "outputs": outputs}


@router.post("/forecast/calculate", response_model=JobStatus)
def calculate_forecast(request: ForecastCalculateRequest) -> JobStatus:
    try:
        load_region_config(request.region)
    except HTTPException:
        raise
    output_dir = get_output_dir()
    job_id = job_manager.submit(_execute_calculate, request, output_dir)
    return job_manager.get(job_id)


@router.get("/forecast/status/{job_id}", response_model=JobStatus)
def get_forecast_status(job_id: str) -> JobStatus:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job
