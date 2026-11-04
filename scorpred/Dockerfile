FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-nfl.txt ./
RUN pip install -r requirements-nfl.txt
COPY *.py ./
COPY services/ ./services/
COPY utils/ ./utils/
COPY migrations/ ./migrations/
COPY templates/ ./templates/
COPY static/ ./static/
COPY scripts/ ./scripts/
COPY data/models/ ./data/models/
COPY data/validation/ ./data/validation/
COPY --from=frontend /frontend/dist/ ./frontend/dist/
RUN python scripts/model_artifacts.py
# A Railway volume must be attached at /var/data before enabling delivery.
ENV SCORPRED_DATA_ROOT=/var/data/scorpred \
    SCORPRED_PERSISTENT_ROOT=/var/data/scorpred/persistent \
    SCORPRED_SCHEDULER_ENABLED=1 SCORPRED_NFL_SYNC_ENABLED=1 \
    NFL_ADVANCED_RESEARCH_ENABLED=0 NFL_WINNER_RESEARCH_ENABLED=0 \
    NBA_EARLY_SEASON_RESEARCH_ENABLED=0
CMD ["sh", "-c", "python scripts/cloud_bootstrap.py && exec gunicorn app:app --config gunicorn.conf.py"]
