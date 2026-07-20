"""FastAPI application entry point (spec section 17).

Run with:
    uvicorn api.main:app --reload --port 8000
(with PYTHONPATH=src, or after `pip install -e .`)

Every endpoint here is a thin wrapper around the existing src/ modules —
config loading, index computation, verification, and significance testing are
all implemented once, in the CLI-facing modules, and reused here rather than
duplicated.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import analysis, forecast, products, reference, significance, verification

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Extremes Forecasting Tool API",
    description="Downscaled ensemble precipitation forecasts vs. observational climatology, with "
                "probabilistic verification and statistical significance testing.",
    version="0.1.0",
)

# Permissive CORS for local development (the Vite dev server runs on a different port).
# Tighten to specific origins before any non-local deployment.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
)

app.include_router(reference.router)
app.include_router(forecast.router)
app.include_router(products.router)
app.include_router(analysis.router)
app.include_router(verification.router)
app.include_router(significance.router)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {"name": "Extremes Forecasting Tool API", "docs": "/docs"}
