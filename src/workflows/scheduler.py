"""Operational scheduling workflow (spec sections 21-22).

Watches the configured forecast dataset's source file for updates (by mtime)
and re-runs the full batch product generation
(:func:`workflows.generate_products.run_all`) when a new version arrives,
rather than reimplementing the pipeline here.

Uses APScheduler — a lightweight, single-process, in-Python scheduler — per
the spec's explicit MVP allowance for "a robust scheduled Python workflow" as
an alternative to Airflow/Prefect/Dagster. Nothing about this project's scale
(one region, a handful of forecast systems, batch runs measured in minutes)
needs a distributed workflow orchestrator; if that changes, this module's
``check_and_regenerate`` function is the unit to lift into a real DAG.

Example
-------
# Check every 6 hours for an updated 2026 forecast file, regenerating outputs
# for the configured init date whenever the file changes:
python -m workflows.scheduler \\
    --region-config configs/regions/ethiopia.yaml \\
    --forecast-dataset seas5_operational_2026 --init-date 2026-05-01 \\
    --interval-minutes 360 --output-dir outputs

# Or just run the check once (e.g. from an external cron/Task Scheduler entry
# instead of this module's own long-running loop):
python -m workflows.scheduler ... --run-once
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler

from utilities.config import AppConfig, load_config
from workflows.generate_products import run_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def check_and_regenerate(cfg: AppConfig, args: argparse.Namespace, state: dict) -> None:
    """Re-run the full batch if the forecast source file has changed since the last check."""
    spec = cfg.dataset(args.forecast_dataset)
    if not spec.path.exists():
        logger.warning("Configured forecast file %s does not exist; skipping this check.", spec.path)
        return

    mtime = spec.path.stat().st_mtime
    if state.get("last_mtime") == mtime:
        logger.info("No change to %s since the last check; nothing to do.", spec.path)
        return

    logger.info("Detected a new/updated %s (mtime changed) — regenerating products for init_date=%s.",
                spec.path, args.init_date)
    try:
        run_all(
            cfg,
            init_date=pd.Timestamp(args.init_date),
            forecast_dataset=args.forecast_dataset,
            observation_dataset=args.observation_dataset,
            climatology_period_label=args.climatology_period or cfg.climatology.default_observational,
            sub_seasonal_periods=cfg.periods.sub_seasonal and [p.label for p in cfg.periods.sub_seasonal],
            seasonal_periods=[s.label for s in cfg.periods.seasonal if s.verifiable],
            no_rain_threshold=cfg.indices.default_no_rain_threshold_mm,
            dry_spell_lengths=cfg.indices.dry_spell_lengths_days,
            output_dir=args.output_dir,
        )
        state["last_mtime"] = mtime
        logger.info("Scheduled regeneration completed successfully.")
    except Exception:
        # Deliberately don't update state["last_mtime"] on failure, so the next tick retries
        # rather than silently giving up on a file it never successfully processed.
        logger.exception("Scheduled batch run failed; will retry on the next check.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region-config", required=True, type=Path)
    p.add_argument("--forecast-dataset", default="seas5_operational_2026")
    p.add_argument("--observation-dataset", default="chirps_obs")
    p.add_argument("--init-date", required=True)
    p.add_argument("--climatology-period", default=None)
    p.add_argument("--interval-minutes", type=int, default=1440, help="Default: check once per day.")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    p.add_argument("--run-once", action="store_true", help="Check and (if needed) regenerate once, then exit.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(region_path=args.region_config)
    state: dict = {}

    if args.run_once:
        check_and_regenerate(cfg, args, state)
        return 0

    scheduler = BlockingScheduler()
    scheduler.add_job(
        check_and_regenerate, "interval", minutes=args.interval_minutes, args=[cfg, args, state],
        next_run_time=pd.Timestamp.now().to_pydatetime(),  # run an initial check immediately, not after the first interval
    )
    logger.info("Scheduler started: checking %s every %d minute(s).", cfg.dataset(args.forecast_dataset).path, args.interval_minutes)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
