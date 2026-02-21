###############################################################
# FILE: pipeline/Dockerfile
# Role: Data pipeline container — geopandas + rasterio environment.
# Agent boundary: Pipeline layer
# Dependencies: requirements.txt; /data volume with raw source files
# Output: Populated PostgreSQL tables
# How to test: docker compose --profile pipeline run pipeline python grid/generate_grid.py
###############################################################

FROM python:3.12-slim

# Geospatial libraries (GDAL/GEOS/PROJ required by geopandas + rasterio)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: do nothing (run specific scripts manually or via pipeline orchestration)
CMD ["echo", "Pipeline ready. Run specific scripts: python grid/generate_grid.py"]
