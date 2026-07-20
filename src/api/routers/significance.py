"""GET /significance — serves results already computed by workflows.run_significance.

Like /verification, reads the JSON manifest already written to disk rather
than recomputing (the bootstrap test itself only takes seconds, but a fresh
run still needs a live forecast + observation load and QC pass).
"""

from __future__ import annotations

from api.catalog import find_latest_report, get_output_dir
from api.schemas import SignificanceResponse
from fastapi import APIRouter, HTTPException
import json

router = APIRouter(tags=["significance"])


@router.get("/significance", response_model=SignificanceResponse)
def get_significance(region: str = "ethiopia", period: str | None = None, init_date: str | None = None) -> SignificanceResponse:
    pattern = f"{region}_{period or '*'}_{init_date or '*'}_significance_manifest.json"
    report_path = find_latest_report(pattern)
    if report_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No significance results found matching '{pattern}' under {get_output_dir()}/reports. "
                   "Run workflows.run_significance for this period/init_date first.",
        )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return SignificanceResponse(**{k: data[k] for k in SignificanceResponse.model_fields if k in data})
