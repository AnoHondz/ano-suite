from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any


@dataclass(slots=True)
class DriftEngine:
    """Detects model drift from evaluated tracked-match outcomes."""

    short_window: int = 20
    long_window: int = 100
    min_samples: int = 15
    _log: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def evaluate(self, rows: list[dict[str, Any]], previous_score: float | None = None) -> dict[str, Any]:
        evaluated = self._evaluated_rows(rows)
        short_rows = evaluated[-self.short_window :] if evaluated else []
        long_rows = evaluated[-self.long_window :] if evaluated else []

        short_metrics = self._window_metrics(short_rows)
        long_metrics = self._window_metrics(long_rows)
        sample_size = len(evaluated)

        win_rate_delta = self._diff(long_metrics.get("win_rate"), short_metrics.get("win_rate"))
        calibration_change = self._diff(short_metrics.get("calibration_error"), long_metrics.get("calibration_error"))

        reasons: list[str] = []
        hard_trigger = False
        if sample_size >= self.min_samples:
            if win_rate_delta is not None and win_rate_delta > 0.10:
                hard_trigger = True
                reasons.append("short window win rate dropped > 10% vs long window")
            if calibration_change is not None and calibration_change > 0.08:
                hard_trigger = True
                reasons.append("calibration error increased by > 0.08")
            if short_metrics.get("win_rate") is not None and short_metrics["win_rate"] < 0.45:
                hard_trigger = True
                reasons.append("recent win rate below 0.45")
        else:
            reasons.append("insufficient evaluated sample for drift trigger")

        raw_score = self._severity_score(
            win_rate_delta=win_rate_delta,
            calibration_change=calibration_change,
            short_win_rate=short_metrics.get("win_rate"),
        )
        drift_score = raw_score if previous_score is None else round(previous_score * 0.7 + raw_score * 0.3, 4)

        drift_detected = sample_size >= self.min_samples and (
            hard_trigger or (previous_score is not None and previous_score >= 1.0 and drift_score >= 0.8)
        )
        severity = self._severity_label(drift_score if drift_detected else 0.0)
        reason = "; ".join(reasons) if reasons else "stable performance"

        payload = {
            "drift_detected": drift_detected,
            "severity": severity,
            "reason": reason,
            "short_window_metrics": short_metrics,
            "long_window_metrics": long_metrics,
            "evaluated_sample_size": sample_size,
            "win_rate_delta": win_rate_delta,
            "calibration_change": calibration_change,
            "drift_score": drift_score,
        }
        if drift_detected:
            self._log.info(
                "drift_detected timestamp=%s severity=%s short_win_rate=%s long_win_rate=%s calibration_change=%s",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                severity,
                short_metrics.get("win_rate"),
                long_metrics.get("win_rate"),
                calibration_change,
            )
        return payload

    def _evaluated_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = []
        for row in rows or []:
            if str(row.get("status") or "").lower() != "completed":
                continue
            if row.get("is_correct") is None:
                continue
            filtered.append(row)
        filtered.sort(key=self._sort_key)
        return filtered

    @staticmethod
    def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
        stamp = (
            row.get("updated_at")
            or row.get("date")
            or ((row.get("model_factors") or {}).get("canonical_snapshot") or {}).get("tracked_at")
            or row.get("created_at")
            or ""
        )
        return (1 if stamp else 0, str(stamp))

    def _window_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        wins = losses = pushes = 0
        conf_total = 0.0
        conf_count = 0
        calibration_terms: list[float] = []
        for row in rows:
            state = row.get("is_correct")
            if state is True:
                wins += 1
            elif state is False:
                losses += 1
            else:
                pushes += 1

            confidence = self._confidence_pct(row)
            if confidence is not None:
                conf_total += confidence
                conf_count += 1
            if confidence is not None and state in (True, False):
                outcome = 1.0 if state is True else 0.0
                calibration_terms.append(abs((confidence / 100.0) - outcome))

        wl_count = wins + losses
        win_rate = (wins / wl_count) if wl_count else None
        average_confidence = (conf_total / conf_count) if conf_count else None
        calibration_error = (sum(calibration_terms) / len(calibration_terms)) if calibration_terms else None
        return {
            "sample_size": len(rows),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "average_confidence": round(average_confidence, 2) if average_confidence is not None else None,
            "calibration_error": round(calibration_error, 4) if calibration_error is not None else None,
        }

    @staticmethod
    def _confidence_pct(row: dict[str, Any]) -> float | None:
        snapshot = ((row.get("model_factors") or {}).get("canonical_snapshot") or {})
        raw = snapshot.get("confidence", row.get("confidence"))
        if isinstance(raw, str):
            label = raw.strip().lower()
            if label == "high":
                return 70.0
            if label == "medium":
                return 60.0
            if label == "low":
                return 50.0
        try:
            return max(0.0, min(100.0, float(raw)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _severity_score(
        *,
        win_rate_delta: float | None,
        calibration_change: float | None,
        short_win_rate: float | None,
    ) -> float:
        score = 0.0
        if win_rate_delta is not None and win_rate_delta > 0.10:
            score += 1.0
            if win_rate_delta > 0.18:
                score += 0.6
            if win_rate_delta > 0.25:
                score += 0.6
        if calibration_change is not None and calibration_change > 0.08:
            score += 1.0
            if calibration_change > 0.14:
                score += 0.6
        if short_win_rate is not None and short_win_rate < 0.45:
            score += 1.0
            if short_win_rate < 0.38:
                score += 0.6
        return round(score, 4)

    @staticmethod
    def _severity_label(score: float) -> str:
        if score >= 2.3:
            return "HIGH"
        if score >= 1.3:
            return "MEDIUM"
        if score > 0:
            return "LOW"
        return "LOW"

    @staticmethod
    def _diff(a: Any, b: Any) -> float | None:
        try:
            if a is None or b is None:
                return None
            return round(float(a) - float(b), 4)
        except (TypeError, ValueError):
            return None
