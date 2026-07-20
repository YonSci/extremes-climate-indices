# Backend API image. NOT built/tested in this development environment (no Docker
# CLI available there) — written to the standard multi-stage pattern for a
# scientific-Python service, but treat the first real build as a verification
# step, not a formality. See docs/deployment.md.
FROM python:3.11-slim AS base

# GDAL/PROJ/GEOS system libraries are required by rasterio/rioxarray/geopandas/pyproj
# (not installable via pip alone) — this is the main reason this can't be a
# from-scratch "pip install -r requirements.txt" image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*

ENV GDAL_CONFIG=/usr/bin/gdal-config

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

COPY configs ./configs

# data/ and outputs/ are expected to be mounted volumes (see docker-compose.yml) —
# the real NetCDF/shapefile inputs and generated products are not baked into the image.
ENV PYTHONPATH=/app/src
ENV EXTREMES_OUTPUT_DIR=/app/outputs

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
