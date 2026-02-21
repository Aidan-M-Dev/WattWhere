###############################################################
# FILE: api/Dockerfile
# Role: FastAPI service container.
# Agent boundary: Deployment layer
# Dependencies: requirements.txt
# Output: FastAPI server on :8000
# How to test: docker compose up api; curl http://localhost:8000/api/sorts
###############################################################

FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Run with hot-reload in dev (docker-compose mounts ./api:/app)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
