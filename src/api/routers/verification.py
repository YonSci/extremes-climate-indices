"""GET /verification — serves the results already computed by workflows.run_verification.

Does not recompute anything (a full verification run takes ~9 minutes against
the real hindcast); this endpoint reads the JSON manifest that run already
wrote to outputs/reports/.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.catalog import read_json_report
from api.schemas import VerificationEventSummary, VerificationPeriodSummary

router = APIRouter(tags=["verification"])

_MANIFEST_NAME_TEMPLATE = "{region}_verification_manifest.json"


@router.get("/verification", response_model=list[VerificationPeriodSummary])
def get_verification(region: str = "ethiopia", period: str | None = None) -> list[VerificationPeriodSummary]:
    data = read_json_report(_MANIFEST_NAME_TEMPLATE.format(region=region))
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="Verification manifest has an unexpected shape.")

    summaries = []
    for period_summary in data:
        if period is not None and period_summary["period_label"] != period:
            continue
        rt = period_summary["rainfall_total"]
        events = [
            VerificationEventSummary(
                label=label, description=ev["description"], mean_brier_score=ev["mean_brier_score"],
                mean_bss=ev["mean_bss"], mean_roc_auc=ev["mean_roc_auc"], domain_pooled_roc_auc=ev["domain_pooled_roc_auc"],
            )
            for label, ev in period_summary["events"].items()
        ]
        summaries.append(VerificationPeriodSummary(
            period_label=period_summary["period_label"], period_type=period_summary["period_type"],
            n_hindcast_years=period_summary["n_hindcast_years"], mean_bias_mm=rt["mean_bias"],
            mean_mae_mm=rt["mean_mae"], mean_acc=rt["mean_acc"], mean_crpss=rt["mean_crpss"], events=events,
        ))

    if period is not None and not summaries:
        raise HTTPException(status_code=404, detail=f"No verification results found for period '{period}'.")
    return summaries
