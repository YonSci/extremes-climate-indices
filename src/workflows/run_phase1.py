"""Phase 1 operational workflow: forecast vs. climatology vs. departure for one period.

Runs the full path from raw NetCDF files to exported products for one forecast
initialization and one valid period: load -> QC -> clip -> climatology ->
indices (rainfall total/anomaly/%anomaly/percentile, CDD, CWD, dry-spell
probabilities) -> three-panel maps -> NetCDF/GeoTIFF/CSV export -> processing
manifest. For generating products across *every* configured period in one run,
see :mod:`workflows.generate_products`; both share the load/QC/window logic in
:mod:`workflows.common` and the per-index-group product logic in
:mod:`workflows.generate_products`.

Example
-------
python -m workflows.run_phase1 \\
    --region-config configs/regions/ethiopia.yaml \\
    --init-date 2026-05-01 \\
    --period-type sub_seasonal --period week1_2 \\
    --climatology-period obs_1993_2025 \\
    --no-rain-threshold 1.0 --dry-spell-lengths 5 7 9 \\
    --output-dir outputs

Inputs: the three NetCDF files declared in the region config (CHIRPS observation,
SEAS5 hindcast, SEAS5 operational forecast). Outputs: PNG maps, NetCDF/GeoTIFF/CSV
exports, and a JSON processing manifest, all under ``--output-dir``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from utilities.config import AppConfig, load_config
from utilities.export import build_metadata
from workflows.common import load_and_prepare, period_tag, resolve_window
from workflows.generate_products import (
    generate_cdd_dryspell_products,
    generate_percentile_products,
    generate_rainfall_total,
    generate_spi_products,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region-config", required=True, type=Path)
    p.add_argument("--forecast-dataset", default="seas5_operational_2026")
    p.add_argument("--observation-dataset", default="chirps_obs")
    p.add_argument("--init-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-type", choices=["sub_seasonal", "seasonal"], default="sub_seasonal")
    p.add_argument("--period", required=True, help="e.g. week1_2, or a season label like JJAS")
    p.add_argument("--climatology-period", default=None, help="Defaults to the config's default_observational")
    p.add_argument("--no-rain-threshold", type=float, default=None, help="Defaults to config's default")
    p.add_argument("--dry-spell-lengths", type=int, nargs="+", default=None, help="Defaults to config's list")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> dict:
    cfg: AppConfig = load_config(region_path=args.region_config)
    init_date = pd.Timestamp(args.init_date)
    no_rain_threshold = args.no_rain_threshold or cfg.indices.default_no_rain_threshold_mm
    dry_spell_lengths = args.dry_spell_lengths or cfg.indices.dry_spell_lengths_days
    clim_period = cfg.climatology.get(args.climatology_period or cfg.climatology.default_observational)

    logger.info("=== Phase 1 run: init=%s period=%s(%s) climatology=%s ===",
                init_date.date(), args.period_type, args.period, clim_period.label)

    data = load_and_prepare(cfg, forecast_dataset=args.forecast_dataset, observation_dataset=args.observation_dataset)
    window = resolve_window(
        data, cfg, period_type=args.period_type, period_label=args.period,
        init_date=init_date, climatology_period=clim_period,
    )

    output_dir = args.output_dir
    tag = period_tag(cfg.region.name, args.period, init_date)
    fc_spec = cfg.dataset(args.forecast_dataset)
    meta_base = build_metadata(
        init_date=str(init_date.date()), valid_start=str(window.valid_start.date()), valid_end=str(window.valid_end.date()),
        forecast_source=args.forecast_dataset, forecast_system_version="SEAS5",
        n_ensemble_members=int(window.lead_window.sizes["realization"]), observation_dataset=args.observation_dataset,
        climatology_period=clim_period.label, index_definition="", bias_correction_method=fc_spec.bias_correction_method,
        rain_threshold_mm=no_rain_threshold, spatial_resolution_deg=cfg.region.resolution_deg,
    )

    outputs = {}
    outputs["rainfall_total"] = generate_rainfall_total(cfg, window, clim_period, meta_base, output_dir, tag)
    outputs["percentile"] = generate_percentile_products(cfg, window, clim_period, meta_base, output_dir, tag)
    outputs["spi"] = generate_spi_products(cfg, window, clim_period, meta_base, output_dir, tag)
    for n in dry_spell_lengths:
        outputs[f"dry_spell_{n}d"] = generate_cdd_dryspell_products(
            cfg, window, clim_period, meta_base, output_dir, tag, no_rain_threshold=no_rain_threshold, dry_spell_length=n,
        )

    manifest = {
        "tag": tag,
        "config_path": str(args.region_config),
        "qc_forecast": data.qc_summary["forecast"],
        "qc_observation": data.qc_summary["observation"],
        "dry_spell_lengths_days": dry_spell_lengths,
        "outputs": outputs,
    }
    manifest_path = output_dir / "reports" / f"{tag}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.info("Wrote processing manifest to %s", manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except Exception:
        logger.exception("Phase 1 workflow failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
