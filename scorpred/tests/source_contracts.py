"""Explicit received-now source metadata for synthetic successful-provider fixtures."""
from datetime import UTC, datetime

from services.data_freshness import metadata


def fresh_source(now=None):
    now = now or datetime.now(UTC)
    return metadata("synthetic provider fixture", now.isoformat(), 3600, now=now)
