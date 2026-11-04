"""Thread-safe pipeline health monitor with email alert integration."""
import threading
from datetime import datetime, timedelta, timezone
from typing import Any


def parse_observed_at(value: Any) -> datetime | None:
    """Read source time without substituting the time a cached value was served."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fixture_refresh_outcome(fixtures, error=None, source="", marker=None) -> dict[str, Any]:
    """Classify the loader contract; an empty, successfully fetched schedule is valid."""
    now = datetime.now(timezone.utc)
    if not isinstance(fixtures, list):
        return {"status": "failed", "observed_at": None, "error": "Provider did not return a schedule list"}
    metadata = [marker] if isinstance(marker, dict) else []
    metadata.extend(
        row["data_freshness"] for row in (fixtures or [])
        if isinstance(row, dict) and isinstance(row.get("data_freshness"), dict)
    )
    observed = [parse_observed_at(info.get("observed_at")) for info in metadata]
    observed = [stamp for stamp in observed if stamp is not None]
    warnings = [str(error)] if error else []
    degraded = str(source).lower() in {"degraded", "stale", "fallback", "unavailable", "error"}
    for info in metadata:
        state = str(info.get("status") or info.get("state") or "fresh").lower()
        expired = parse_observed_at(info.get("expires_at"))
        if state not in {"fresh", "success", "ok"} or (expired and expired <= now) or parse_observed_at(info.get("observed_at")) is None:
            degraded = True
            warnings.append(str(info.get("warning") or "Source refresh was not fresh"))
    if observed and any(stamp > now + timedelta(minutes=1) for stamp in observed):
        degraded = True
        warnings.append("Source observation time is in the future")
    failed = bool(error) or degraded
    return {
        "status": ("degraded" if fixtures else "failed") if failed else "success",
        "observed_at": min(observed).isoformat() if observed else (None if failed else now.isoformat()),
        "error": "; ".join(dict.fromkeys(warnings)) or ("Cached provider fallback" if degraded else None),
    }


def combine_refresh_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """No successful component may hide a failed league or result refresh."""
    if not outcomes:
        return {"status": "failed", "observed_at": None, "error": "No refresh outcome was reported"}
    invalid = [row for row in outcomes if not isinstance(row, dict) or row.get("status") != "success"]
    now = datetime.now(timezone.utc)
    invalid.extend({"status": "failed", "error": "Refresh reported an invalid source observation time"}
                   for row in outcomes if isinstance(row, dict) and row.get("status") == "success"
                   and (parse_observed_at(row.get("observed_at")) is None
                        or parse_observed_at(row["observed_at"]) > now + timedelta(minutes=1)))
    observed = [parse_observed_at(row.get("observed_at")) for row in outcomes if isinstance(row, dict)]
    observed = [stamp for stamp in observed if stamp is not None]
    if invalid:
        errors = [str(row.get("error") or row.get("warning") or row.get("status") or "Unknown refresh outcome")
                  if isinstance(row, dict) else "Unknown refresh outcome" for row in invalid]
        return {"status": "degraded" if any(isinstance(row, dict) and row.get("status") in {"success", "degraded"} for row in outcomes) else "failed",
                "observed_at": min(observed).isoformat() if observed else None,
                "error": "; ".join(dict.fromkeys(errors))}
    if not observed:
        return {"status": "failed", "observed_at": None, "error": "Refresh did not report a source observation time"}
    return {"status": "success", "observed_at": min(observed).isoformat(), "error": None}


class PipelineMonitor:
    STALE_THRESHOLDS: dict[str, timedelta] = {
        "soccer_fixtures": timedelta(hours=2),
        "nba_games":       timedelta(hours=2),
        "ml_predictions":  timedelta(hours=3),
        "injury_reports":  timedelta(hours=1),
    }
    FAILURE_ALERT_THRESHOLD = 3

    def __init__(self, alerter=None):
        self._lock = threading.Lock()
        self._last_success: dict[str, datetime] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._total_failures: dict[str, int] = {}
        self._last_attempt: dict[str, datetime] = {}
        self._last_error: dict[str, str] = {}
        self._alerter = alerter

    def record_success(self, pipeline: str, *, observed_at=None) -> None:
        now = datetime.now(timezone.utc)
        observed = parse_observed_at(observed_at) if observed_at is not None else now
        if observed is None or observed > now + timedelta(minutes=1):
            self.record_failure(pipeline, ValueError("Invalid source observation time"))
            return
        with self._lock:
            self._last_attempt[pipeline] = now
            self._last_success[pipeline] = observed
            self._consecutive_failures[pipeline] = 0
            self._last_error.pop(pipeline, None)

    def record_failure(self, pipeline: str, exc: Exception) -> None:
        with self._lock:
            self._last_attempt[pipeline] = datetime.now(timezone.utc)
            self._last_error[pipeline] = str(exc)
            self._consecutive_failures[pipeline] = (
                self._consecutive_failures.get(pipeline, 0) + 1
            )
            self._total_failures[pipeline] = (
                self._total_failures.get(pipeline, 0) + 1
            )
            consecutive = self._consecutive_failures[pipeline]

        if consecutive >= self.FAILURE_ALERT_THRESHOLD and self._alerter:
            self._alerter.send_alert(
                subject=f"[ScorPred] Pipeline FAILURE: {pipeline}",
                body=(
                    f"Pipeline '{pipeline}' has failed {consecutive} times in a row.\n\n"
                    f"Error: {exc}"
                ),
                severity="error",
                rate_key=f"failure:{pipeline}",
            )

    def check_staleness(self) -> None:
        now = datetime.now(timezone.utc)
        for pipeline, threshold in self.STALE_THRESHOLDS.items():
            with self._lock:
                last = self._last_success.get(pipeline)
            if last is None:
                continue
            age = now - last
            if age > threshold and self._alerter:
                self._alerter.send_alert(
                    subject=f"[ScorPred] Pipeline STALE: {pipeline}",
                    body=(
                        f"Pipeline '{pipeline}' last succeeded "
                        f"{int(age.total_seconds() / 60)} min ago "
                        f"(threshold: {int(threshold.total_seconds() / 60)} min)."
                    ),
                    severity="warning",
                    rate_key=f"stale:{pipeline}",
                )

    def get_status(self) -> dict:
        now = datetime.now(timezone.utc)
        pipelines: dict[str, dict] = {}
        for name in self.STALE_THRESHOLDS:
            with self._lock:
                last = self._last_success.get(name)
                failures = self._consecutive_failures.get(name, 0)
                attempted = self._last_attempt.get(name)
                error = self._last_error.get(name)
            if last is None:
                status = "unknown"
                age_mins = None
            else:
                age = now - last
                age_mins = int(age.total_seconds() / 60)
                status = "ok" if age < self.STALE_THRESHOLDS[name] else "stale"
            if failures >= self.FAILURE_ALERT_THRESHOLD:
                status = "down"
            elif failures:
                status = "degraded"
            pipelines[name] = {
                "status": status,
                "last_success_mins_ago": age_mins,
                "consecutive_failures": failures,
                "last_success": last.isoformat() if last else None,
                "last_attempt": attempted.isoformat() if attempted else None,
                "error": error,
            }
        overall = "ok"
        if any(p["status"] == "down" for p in pipelines.values()):
            overall = "down"
        elif any(p["status"] in ("stale", "unknown", "degraded") for p in pipelines.values()):
            overall = "degraded"
        return {"overall": overall, "pipelines": pipelines}
