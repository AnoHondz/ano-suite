from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from services.calibration_engine import CalibrationEngine
from services.data_freshness import metadata as source_metadata
from services.data_freshness import worst
from services.decision_engine import DecisionEngine
from services.drift_engine import DriftEngine
from services.feature_attribution_engine import FeatureAttributionEngine
from services.pipeline_monitor import (
    combine_refresh_outcomes,
    fixture_refresh_outcome,
    parse_observed_at,
)
from services.prediction_lifecycle import (
    calendar_date,
    freshness_state,
    sanitize_decision,
    unavailable_reason,
)


@dataclass(slots=True)
class MatchBrain:
    """Orchestrates fixture ingestion, canonical decisions, tracking, and insights."""

    load_fixtures: Callable[[int], tuple[list[dict[str, Any]], Any, str, str]]
    get_fixture_by_id: Callable[[str], dict[str, Any] | None]
    decision_engine: DecisionEngine
    tracker_save: Callable[..., str] | None = None
    tracker_recent: Callable[[int], list[dict[str, Any]]] | None = None
    refresh_results: Callable[[], Any] | None = None
    _fixture_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    _analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _analysis_checksum: dict[str, str] = field(default_factory=dict)
    _status_memory: dict[str, str] = field(default_factory=dict)
    _last_successful_fixtures: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    _fixture_observed_at: dict[int, datetime] = field(default_factory=dict)
    _fixture_refresh_outcomes: dict[int, dict[str, Any]] = field(default_factory=dict)
    _last_refresh_attempts: dict[int, datetime] = field(default_factory=dict)
    _refresh_outcomes: dict[int, dict[str, Any]] = field(default_factory=dict)
    _results_read_outcome: dict[str, Any] = field(default_factory=dict)
    _result_refresh_outcome: dict[str, Any] = field(default_factory=dict)
    _last_successful_results: list[dict[str, Any]] = field(default_factory=list)
    _active_computations: set[str] = field(default_factory=set)
    _tracked_match_ids: set[str] = field(default_factory=set)
    _refresh_lock: threading.Lock = field(default_factory=threading.Lock)
    _last_refresh_at: datetime | None = None
    _last_fetch_at: datetime | None = None
    _error_count: int = 0
    _api_status: str = "ok"
    _log: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _calibration_engine: CalibrationEngine = field(default_factory=CalibrationEngine)
    _feature_attribution_engine: FeatureAttributionEngine = field(default_factory=FeatureAttributionEngine)
    _drift_engine: DriftEngine = field(default_factory=DriftEngine)
    _last_thresholds: dict[str, Any] | None = None
    _last_drift_score: float | None = None
    _last_drift_signature: str | None = None
    _last_drift_payload: dict[str, Any] | None = None

    def get_match_status(self, fixture: dict[str, Any]) -> str:
        status = ((fixture.get("fixture") or {}).get("status") or {}).get("short")
        text = str(status or "").upper()
        if text in {"FT", "AET", "PEN"}:
            return "completed"
        if text in {"PST", "CANC", "ABD", "INT"}:
            return "postponed"
        if text in {"1H", "2H", "HT", "LIVE", "ET", "BT"}:
            return "live"
        return "scheduled"

    def get_date_bucket(self, fixture: dict[str, Any], now_utc: datetime | None = None) -> str:
        now = now_utc or datetime.now(timezone.utc)
        kickoff = self._parse_kickoff((fixture.get("fixture") or {}).get("date"))
        if kickoff is None:
            return "upcoming"
        delta = (date.fromisoformat(calendar_date(kickoff)) - date.fromisoformat(calendar_date(now))).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "tomorrow"
        if delta == -1:
            return "yesterday"
        if delta > 1:
            return "upcoming"
        return "past"

    @staticmethod
    def _dq_to_completeness_static(data_quality: str | None) -> dict:
        """Translate scorpred_engine's data_quality string into the data_completeness dict format."""
        dq = str(data_quality or "").lower()
        if "strong" in dq or "high" in dq:
            tier = "strong"
        elif "moderate" in dq or "partial" in dq:
            tier = "moderate"
        elif "limited" in dq or "low" in dq:
            tier = "limited"
        else:
            tier = "partial"
        return {"tier": tier}

    def canonical_from_fixture(self, fixture: dict[str, Any]) -> dict[str, Any] | None:
        teams = fixture.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        home_name = home.get("name")
        away_name = away.get("name")
        fixture_id = (fixture.get("fixture") or {}).get("id")
        if fixture_id is None or not home_name or not away_name:
            return None

        self._fixture_index[str(fixture_id)] = fixture
        reason = unavailable_reason(
            (fixture.get("fixture") or {}).get("date"),
            (fixture.get("fixture") or {}).get("status"), fixture.get("data_freshness"),
        )
        from services.input_contracts import three_way_probabilities
        if not three_way_probabilities((fixture.get("prediction") or {}).get("win_probabilities")):
            reason = reason or "Prediction probabilities are missing or malformed; no estimate is available."
        if reason:
            canonical = self._safe_unavailable(fixture_id)
            canonical.update(matchup=f"{home_name} vs {away_name}", teams={"home": home, "away": away},
                             kickoff=(fixture.get("fixture") or {}).get("date") or "",
                             status=self.get_match_status(fixture), league=(fixture.get("league") or {}).get("name") or "Soccer",
                             event_status=(fixture.get("fixture") or {}).get("status"), data_freshness=fixture.get("data_freshness"),
                             date_bucket=self.get_date_bucket(fixture), reason=reason)
            return self._sanitize_canonical(canonical)
        _pred = fixture.get("prediction") or {}
        raw_probs = _pred.get("win_probabilities") or {}
        best_pick = _pred.get("best_pick") or {}
        # scorpred_predict returns "confidence" (str label) not "confidence_pct" (int)
        model_conf = _pred.get("confidence_pct") or _pred.get("confidence")
        model_metrics = self.get_model_metrics()
        trust_score = model_metrics.get("trust_score")
        if isinstance(model_metrics.get("calibration_score"), str):
            trust_score = None
        drift_status = self.get_drift_status()
        adaptive = self.get_adaptive_thresholds(model_metrics=model_metrics, drift_status=drift_status)
        # Feature feedback is learned from the rules engine's components; it does not apply to the ratings model.
        feature_attribution = {} if _pred.get("ratings_model") else self.get_feature_attribution()

        decision = self.decision_engine.build_decision(
            {
                "home_name": home_name,
                "away_name": away_name,
                "probabilities": {
                    "home": raw_probs.get("a"),
                    "draw": raw_probs.get("draw"),
                    "away": raw_probs.get("b"),
                },
                "confidence": model_conf,
                "recommended_side": best_pick.get("prediction") or best_pick.get("team") or home_name,
                "data_completeness": _pred.get("data_completeness") or self._dq_to_completeness_static(_pred.get("data_quality")),
                "strengths": [best_pick.get("reasoning")] if best_pick.get("reasoning") else [],
                "risks": [],
                "odds": (fixture.get("prediction") or {}).get("odds") or {},
                "trust_score": trust_score,
                "calibration_score": model_metrics.get("calibration_score") if not isinstance(model_metrics.get("calibration_score"), str) else None,
                "calibration_error": model_metrics.get("calibration_error"),
                "adaptive_thresholds": adaptive,
                "feature_attribution": feature_attribution.get("feature_impacts"),
                "drift_status": drift_status,
            }
        )

        # No soccer model has beaten bookmaker prices in walk-forward testing
        # (docs/SOCCER_MODEL_RESEARCH.md), so the strongest soccer call is CONSIDER.
        if decision.get("action") == "BET":
            decision["action"] = "CONSIDER"
        kickoff = (fixture.get("fixture") or {}).get("date") or ""
        status = self.get_match_status(fixture)
        league = (fixture.get("league") or {}).get("name") or "Soccer"
        reason = " | ".join((decision.get("reasoning") or {}).get("strengths", []))
        breakdown = {
            "model_probability": decision.get("model_probability"),
            "implied_probability": decision.get("implied_probability"),
            "edge_score": decision.get("edge_score"),
            "expected_value": decision.get("expected_value"),
            "risk_score": decision.get("risk_score"),
            "risk_level": decision.get("risk_level"),
            "decision_grade": decision.get("decision_grade"),
        }
        canonical = {
            "match_id": str(fixture_id),
            "matchup": f"{home_name} vs {away_name}",
            "league": league,
            "kickoff": kickoff,
            "status": status,
            "recommended_side": decision.get("side"),
            "action": decision.get("action"),
            "confidence": decision.get("confidence"),
            "probabilities": decision.get("probabilities"),
            "data_quality": decision.get("data_quality"),
            "reason": reason,
            "metric_breakdown": breakdown,
            "model_probability": breakdown.get("model_probability"),
            "implied_probability": breakdown.get("implied_probability"),
            "edge_score": breakdown.get("edge_score"),
            "expected_value": breakdown.get("expected_value"),
            "risk_score": breakdown.get("risk_score"),
            "risk_level": breakdown.get("risk_level"),
            "decision_grade": breakdown.get("decision_grade"),
            "prediction": decision,
            "model_source": _pred.get("model_source"),
            "teams": {"home": home, "away": away},
            "date_bucket": self.get_date_bucket(fixture),
            "event_status": (fixture.get("fixture") or {}).get("status"),
            "data_freshness": fixture.get("data_freshness"),
        }
        return self._sanitize_canonical(self._validate_canonical_decision(canonical))

    def get_match_analysis(self, match_id: str | int) -> dict[str, Any] | None:
        match_key = str(match_id)
        if not match_key:
            return self._safe_unavailable(match_id)
        cached = self._analysis_cache.get(match_key)
        if cached is not None:
            fixture = self._fixture_index.get(match_key) or self.get_fixture_by_id(match_key)
            if fixture:
                previous_source = cached.get("data_freshness")
                current_source = fixture.get("data_freshness")
                if (previous_source and freshness_state(previous_source) != "fresh"
                        and current_source and freshness_state(current_source) == "fresh"):
                    regenerated = self.canonical_from_fixture(fixture)
                    if regenerated:
                        self._analysis_cache[match_key] = regenerated
                        self._analysis_checksum[match_key] = self._checksum(regenerated)
                        return self._sanitize_canonical(regenerated)
                cached = dict(cached, kickoff=(fixture.get("fixture") or {}).get("date") or "",
                              status=self.get_match_status(fixture), event_status=(fixture.get("fixture") or {}).get("status"),
                              data_freshness=worst([previous_source, current_source]) or (previous_source if previous_source is not None else current_source))
            return self._sanitize_canonical(cached)
        if match_key in self._active_computations:
            return cached or self._safe_unavailable(match_id)

        self._active_computations.add(match_key)
        try:
            fixture = self._fixture_index.get(match_key) or self.get_fixture_by_id(match_key)
            if not fixture:
                return self._safe_unavailable(match_id)
            canonical = self.canonical_from_fixture(fixture)
            if not canonical:
                return self._safe_unavailable(match_id)
            checksum = self._checksum(canonical)
            existing_checksum = self._analysis_checksum.get(match_key)
            if existing_checksum and existing_checksum != checksum:
                self._log.error("canonical_mismatch match_id=%s", match_key)
                return self._sanitize_canonical(self._analysis_cache.get(match_key, canonical))
            self._analysis_cache[match_key] = canonical
            self._analysis_checksum[match_key] = checksum
            return self._sanitize_canonical(canonical)
        finally:
            self._active_computations.discard(match_key)

    def get_insights(self, league_id: int) -> dict[str, Any]:
        fixtures = self.safe_fetch_fixtures(league_id)
        canonical = []
        for f in fixtures or []:
            try:
                row = self.canonical_from_fixture(f)
                if row:
                    canonical.append(row)
            except Exception as exc:
                self._log.warning("get_insights canonical_from_fixture failed: %s", exc)
        try:
            opportunities = sorted(canonical, key=lambda row: (-self._priority_score(row), -(row.get("confidence") or 0)))
        except Exception:
            opportunities = canonical
        high_conf = []
        for row in opportunities:
            try:
                conf = (row.get("prediction") or {}).get("confidence") or 0
                if int(float(conf)) >= 64:
                    high_conf.append(row)
            except (TypeError, ValueError):
                pass
        return {"top_opportunities": opportunities[:6], "high_confidence": high_conf[:6]}

    def track_match(self, canonical_match: dict[str, Any]) -> str:
        if not self.tracker_save:
            return ""
        match_id = str(canonical_match.get("match_id") or "").strip()
        if not match_id:
            return ""
        if match_id in self._tracked_match_ids:
            return ""
        if self.tracker_recent:
            existing = self.tracker_recent(500) or []
            duplicate = next((row for row in existing if str(row.get("fixture_id") or "") == match_id), None)
            if duplicate:
                self._tracked_match_ids.add(match_id)
                return str(duplicate.get("id") or "")

        prediction = canonical_match.get("prediction") or {}
        teams = canonical_match.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        probs = prediction.get("probabilities") or {}
        snapshot = {
            "canonical_snapshot": {
                "match_id": canonical_match.get("match_id"),
                "matchup": canonical_match.get("matchup"),
                "sport": "soccer",
                "league": canonical_match.get("league"),
                "kickoff": canonical_match.get("kickoff"),
                "status": canonical_match.get("status"),
                "recommended_side": canonical_match.get("recommended_side"),
                "action": canonical_match.get("action"),
                "confidence": canonical_match.get("confidence"),
                "probabilities": canonical_match.get("probabilities"),
                "data_quality": canonical_match.get("data_quality"),
                "reason": canonical_match.get("reason"),
                "model_probability": (canonical_match.get("metric_breakdown") or {}).get("model_probability"),
                "implied_probability": (canonical_match.get("metric_breakdown") or {}).get("implied_probability"),
                "edge_score": (canonical_match.get("metric_breakdown") or {}).get("edge_score"),
                "expected_value": (canonical_match.get("metric_breakdown") or {}).get("expected_value"),
                "risk_score": (canonical_match.get("metric_breakdown") or {}).get("risk_score"),
                "risk_level": (canonical_match.get("metric_breakdown") or {}).get("risk_level"),
                "decision_grade": (canonical_match.get("metric_breakdown") or {}).get("decision_grade"),
                "tracked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": None,
                "evaluation_status": "OPEN",
            }
        }
        snapshot["evaluation"] = self._calibration_engine.build_snapshot(
            {
                "match_id": canonical_match.get("match_id"),
                "recommended_side": canonical_match.get("recommended_side"),
                "confidence": canonical_match.get("confidence"),
                "probabilities": canonical_match.get("probabilities"),
                "edge_score": canonical_match.get("edge_score"),
                "expected_value": canonical_match.get("expected_value"),
                "data_quality": canonical_match.get("data_quality"),
                "tracked_at": snapshot["canonical_snapshot"]["tracked_at"],
                "features": self._feature_payload(canonical_match),
            }
        )
        saved_id = self.tracker_save(
            sport="soccer",
            team_a=home.get("name") or "Home",
            team_b=away.get("name") or "Away",
            predicted_winner=prediction.get("side") or home.get("name") or "Home",
            win_probs={"a": probs.get("home") or 0, "draw": probs.get("draw") or 0, "b": probs.get("away") or 0},
            confidence="High" if (prediction.get("confidence") or 0) >= 66 else "Medium" if (prediction.get("confidence") or 0) >= 55 else "Low",
            game_date=(canonical_match.get("kickoff") or "")[:10],
            team_a_id=home.get("id"),
            team_b_id=away.get("id"),
            league_name=canonical_match.get("league"),
            fixture_id=canonical_match.get("match_id"),
            model_factors=snapshot,
        )
        if saved_id:
            self._tracked_match_ids.add(match_id)
        return saved_id

    def refresh_tracked_matches(self) -> list[dict[str, Any]]:
        if self.refresh_results:
            try:
                result = self.refresh_results()
                if isinstance(result, dict) and (result.get("failed") or result.get("status") in {"failed", "degraded"}):
                    raise RuntimeError(f"Result refresh reported failures: {result.get('errors') or result.get('error') or result.get('failed')}")
                self._result_refresh_outcome = {"status": "success", "observed_at": datetime.now(timezone.utc).isoformat(), "error": None}
            except Exception as exc:
                self._error_count += 1
                self._result_refresh_outcome = {"status": "failed", "observed_at": None, "error": str(exc)}
                self._log.warning("Result refresh failed: %s", exc)
        else:
            self._result_refresh_outcome = {"status": "success", "observed_at": datetime.now(timezone.utc).isoformat(), "error": None}
        if not self.tracker_recent:
            return []
        tracked = self.safe_fetch_results()
        rows: list[dict[str, Any]] = []
        for row in tracked:
            fixture_id = str(row.get("fixture_id") or "")
            if fixture_id:
                self._tracked_match_ids.add(fixture_id)
            repaired_status = self._repair_tracking_state(row, fixture_id)
            rows.append({**row, "match_id": fixture_id, "tracking_status": repaired_status})
        return rows

    def safe_fetch_fixtures(self, league_id: int) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        try:
            fixtures, error, source, marker = self.load_fixtures(league_id)
            if not isinstance(fixtures, list):
                raise ValueError("Fixture loader did not return a schedule list")
            outcome = fixture_refresh_outcome(fixtures, error, source, marker)
            observed = parse_observed_at(outcome.get("observed_at"))
            if observed and (now - observed).total_seconds() > 7200:
                outcome = {"status": "failed", "observed_at": observed.isoformat(), "error": "Fixture source is older than two hours"}
                fixtures = []
            if outcome["status"] == "success":
                # A fresh empty schedule clears old games instead of resurrecting them.
                self._last_successful_fixtures[league_id] = fixtures
                self._fixture_observed_at[league_id] = observed or now
            else:
                self._error_count += 1
                if observed is None or observed > now:
                    fixtures = []
            self._fixture_refresh_outcomes[league_id] = outcome
        except Exception as exc:
            self._error_count += 1
            fixtures = []
            self._fixture_refresh_outcomes[league_id] = {"status": "failed", "observed_at": None, "error": str(exc)}
            self._log.warning("Fixture refresh failed league=%s: %s", league_id, exc)
        outcome = self._fixture_refresh_outcomes[league_id]
        if outcome["status"] != "success" and not fixtures:
            previous_observed = self._fixture_observed_at.get(league_id)
            if previous_observed and 0 <= (now - previous_observed).total_seconds() <= 7200:
                fixtures = self._last_successful_fixtures.get(league_id, [])
                if fixtures:
                    outcome.update(status="degraded", observed_at=previous_observed.isoformat())
        if outcome["status"] != "success" and fixtures:
            # Fallback rows retain their source time and cannot generate fresh recommendations.
            observed = parse_observed_at(outcome.get("observed_at"))
            refreshed = []
            for fixture in fixtures:
                freshness = dict(fixture.get("data_freshness") or source_metadata("soccer", observed, 900, 7200, degraded=True))
                freshness.update(state="degraded", warning=outcome.get("error") or "Provider refresh failed; showing a cached schedule")
                refreshed.append(dict(fixture, data_freshness=freshness))
            fixtures = refreshed
        self._update_refresh_health()
        return fixtures

    def safe_fetch_results(self) -> list[dict[str, Any]]:
        if not self.tracker_recent:
            self._results_read_outcome = {"status": "success", "observed_at": datetime.now(timezone.utc).isoformat(), "error": None}
            return self._last_successful_results
        try:
            results = self.tracker_recent(300) or []
            self._last_successful_results = results
            self._results_read_outcome = {"status": "success", "observed_at": datetime.now(timezone.utc).isoformat(), "error": None}
            return results
        except Exception as exc:
            self._error_count += 1
            self._results_read_outcome = {"status": "degraded" if self._last_successful_results else "failed", "observed_at": None, "error": str(exc)}
            self._api_status = "degraded"
            return self._last_successful_results

    def refresh_cycle(self, league_id: int, min_interval_seconds: int = 60) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        last_attempt = self._last_refresh_attempts.get(league_id)
        if last_attempt and (now - last_attempt).total_seconds() < min_interval_seconds:
            return {"status": "skipped", "observed_at": None, "error": "Refresh interval has not elapsed"}
        with self._refresh_lock:
            now = datetime.now(timezone.utc)
            last_attempt = self._last_refresh_attempts.get(league_id)
            if last_attempt and (now - last_attempt).total_seconds() < min_interval_seconds:
                return {"status": "skipped", "observed_at": None, "error": "Refresh interval has not elapsed"}
            self._last_refresh_attempts[league_id] = now
            self.safe_fetch_fixtures(league_id)
            self.refresh_tracked_matches()
            self.safe_fetch_results()
            outcome = combine_refresh_outcomes([
                self._fixture_refresh_outcomes[league_id], self._result_refresh_outcome, self._results_read_outcome,
            ])
            outcome["league_id"] = league_id
            self._refresh_outcomes[league_id] = outcome
            if all(row["status"] == "success" for row in self._refresh_outcomes.values()):
                self._last_refresh_at = min(parse_observed_at(row["observed_at"]) for row in self._refresh_outcomes.values())
            self._update_refresh_health()
            return dict(outcome)

    def _update_refresh_health(self) -> None:
        outcomes = list(self._fixture_refresh_outcomes.values())
        outcomes.extend(row for row in (self._results_read_outcome, self._result_refresh_outcome) if row)
        self._api_status = "ok" if outcomes and all(row.get("status") == "success" for row in outcomes) else "degraded"
        # A single freshly fetched league must not make another league's old data look new.
        stamps = [parse_observed_at(row.get("observed_at")) for row in self._fixture_refresh_outcomes.values()]
        self._last_fetch_at = min(stamps) if stamps and all(stamps) else None

    @staticmethod
    def _sanitize_canonical(canonical: dict[str, Any]) -> dict[str, Any]:
        result = sanitize_decision(canonical)
        if result.get("pregame_available"):
            return result
        reason = result.get("reason") or "Pregame prediction is unavailable"
        result.update(data_quality=0, model_probability=None, implied_probability=None,
                      edge_score=None, expected_value=None)
        prediction = dict(result.get("prediction") or {})
        prediction.update(action="SKIP", side="Unavailable", recommended_side="Unavailable", confidence=0,
                          probabilities={}, model_probability=None, implied_probability=None, edge_score=None,
                          expected_value=None, data_quality=0, reason=reason,
                          reasoning={"strengths": [], "risks": [reason]})
        result["prediction"] = prediction
        result["metric_breakdown"] = dict(result.get("metric_breakdown") or {}, model_probability=None,
                                          implied_probability=None, edge_score=None, expected_value=None)
        return result

    def get_performance_snapshot(self, completed: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(1 for row in completed if row.get("is_correct") is True)
        losses = sum(1 for row in completed if row.get("is_correct") is False)
        pushes = max(0, len(completed) - wins - losses)
        if not completed:
            return {"win_rate": "N/A", "roi": "N/A", "record": "0W-0L-0P"}
        win_rate = round((wins / len(completed)) * 100, 1)
        return {"win_rate": f"{win_rate:.1f}%", "roi": "N/A", "record": f"{wins}W-{losses}L-{pushes}P"}

    def get_alerts(self, league_id: int) -> list[dict[str, Any]]:
        fixtures = self.safe_fetch_fixtures(league_id)
        alerts: list[dict[str, Any]] = []
        for canonical in [self.canonical_from_fixture(item) for item in fixtures or []]:
            if not canonical:
                continue
            prediction = canonical.get("prediction") or {}
            confidence = int(prediction.get("confidence") or 0)
            if confidence >= 70 and prediction.get("action") == "BET":
                alerts.append(
                    {
                        "type": "high_confidence_opportunity",
                        "title": canonical.get("matchup"),
                        "description": f"{prediction.get('side')} at {confidence}% confidence",
                        "match_id": canonical.get("match_id"),
                    }
                )
            last = self._status_memory.get(canonical["match_id"])
            current = canonical.get("status")
            if last and current != last:
                alerts.append(
                    {
                        "type": "status_change",
                        "title": canonical.get("matchup"),
                        "description": f"Status changed from {last} to {current}",
                        "match_id": canonical.get("match_id"),
                    }
                )
            self._status_memory[canonical["match_id"]] = current
        return alerts[:20]

    def get_system_health(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        freshness = int((now - self._last_fetch_at).total_seconds()) if self._last_fetch_at else None
        evaluated = [r for r in self.safe_fetch_results() if str(r.get("status") or "").lower() == "completed"]
        return {
            "api_status": self._api_status,
            "last_refresh_time": self._last_refresh_at.isoformat().replace("+00:00", "Z") if self._last_refresh_at else None,
            "data_freshness": freshness,
            "tracked_count": len(self._tracked_match_ids),
            "evaluated_count": len(evaluated),
            "calibration_status": "ready" if len(evaluated) >= 2 else "insufficient_data",
            "error_count": self._error_count,
            "degraded_mode": self._api_status != "ok",
            "fixture_refreshes": {str(league): dict(outcome) for league, outcome in self._fixture_refresh_outcomes.items()},
            "result_refresh": dict(self._result_refresh_outcome),
        }

    def get_model_metrics(self) -> dict[str, Any]:
        rows = self.safe_fetch_results()
        metrics = self._calibration_engine.get_model_metrics(rows)
        calib = metrics.get("bucket_breakdown") or {}
        errors = []
        for v in calib.values():
            raw = v.get("error")
            if raw is not None:
                try:
                    errors.append(float(raw))
                except (TypeError, ValueError):
                    pass
        metrics["calibration_error"] = round(sum(errors) / len(errors), 2) if errors else None
        return metrics

    def get_adaptive_thresholds(
        self,
        model_metrics: dict[str, Any] | None = None,
        drift_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metrics = model_metrics or self.get_model_metrics()
        drift_status = drift_status or self.get_drift_status()
        trust_score = metrics.get("trust_score")
        calibration_score = metrics.get("calibration_score")
        calibration_error = metrics.get("calibration_error")
        rows = [r for r in self.safe_fetch_results() if str(r.get("status") or "").lower() == "completed" and r.get("is_correct") is not None]
        recent = rows[-30:]
        recent_accuracy = (sum(1 for r in recent if r.get("is_correct") is True) / len(recent) * 100.0) if recent else None

        bet_min_confidence = 64
        consider_min_confidence = 54
        risk_tolerance = "MEDIUM"

        if trust_score is not None and float(trust_score) < 55:
            bet_min_confidence += 5
            consider_min_confidence += 2
            risk_tolerance = "LOW"
        elif trust_score is not None and float(trust_score) > 75:
            bet_min_confidence -= 2

        if calibration_error is not None and float(calibration_error) > 12:
            bet_min_confidence += 3
            consider_min_confidence += 1
            risk_tolerance = "LOW"
        elif calibration_score not in (None, "insufficient data") and float(calibration_score) > 80:
            bet_min_confidence -= 1

        if recent_accuracy is not None and recent_accuracy > 60:
            bet_min_confidence -= 1
        elif recent_accuracy is not None and recent_accuracy < 45:
            bet_min_confidence += 2

        drift_shift = 0
        if drift_status.get("drift_detected"):
            severity = str(drift_status.get("severity") or "LOW").upper()
            drift_shift = {"LOW": 2, "MEDIUM": 4, "HIGH": 6}.get(severity, 2)
            bet_min_confidence += drift_shift
            consider_min_confidence += max(1, drift_shift // 2)
            if severity in {"MEDIUM", "HIGH"}:
                risk_tolerance = "LOW"
            elif risk_tolerance == "HIGH":
                risk_tolerance = "MEDIUM"

        bet_min_confidence = max(60, min(85, int(round(bet_min_confidence))))
        consider_min_confidence = max(50, min(75, int(round(consider_min_confidence))))
        payload = {
            "bet_min_confidence": bet_min_confidence,
            "consider_min_confidence": consider_min_confidence,
            "max_risk_allowed": risk_tolerance,
            "risk_tolerance": risk_tolerance,
            "drift_shift": drift_shift,
            "drift_detected": bool(drift_status.get("drift_detected")),
            "drift_severity": drift_status.get("severity"),
        }
        old = self._last_thresholds
        if old != payload:
            self._log.info(
                "adaptive_threshold_change timestamp=%s trust_score=%s calibration_score=%s old=%s new=%s reason=%s",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                trust_score,
                calibration_score,
                old,
                payload,
                "trust/calibration/recent-performance",
            )
        self._last_thresholds = payload
        return payload

    def get_drift_status(self) -> dict[str, Any]:
        rows = self.safe_fetch_results()
        signature = self._checksum(
            [
                {
                    "id": row.get("id"),
                    "status": row.get("status"),
                    "is_correct": row.get("is_correct"),
                    "confidence": row.get("confidence"),
                    "date": row.get("date"),
                    "updated_at": row.get("updated_at"),
                    "tracked_at": ((row.get("model_factors") or {}).get("canonical_snapshot") or {}).get("tracked_at"),
                }
                for row in rows
            ]
        )
        if self._last_drift_signature == signature and self._last_drift_payload is not None:
            return self._last_drift_payload
        payload = self._drift_engine.evaluate(rows, previous_score=self._last_drift_score)
        self._last_drift_score = payload.get("drift_score")
        self._last_drift_signature = signature
        self._last_drift_payload = payload
        return payload

    def get_system_intelligence(self) -> dict[str, Any]:
        model_metrics = self.get_model_metrics()
        health = self.get_system_health()
        drift = self.get_drift_status()
        rows = self.safe_fetch_results()

        bet_count = 0
        consider_count = 0
        skip_count = 0
        high_conf_completed = []
        for row in rows:
            snapshot = ((row.get("model_factors") or {}).get("canonical_snapshot") or {})
            action = str(snapshot.get("action") or "").upper()
            if action == "BET":
                bet_count += 1
            elif action == "CONSIDER":
                consider_count += 1
            elif action == "SKIP":
                skip_count += 1
            if str(row.get("status") or "").lower() == "completed" and row.get("is_correct") is not None:
                conf = float(snapshot.get("confidence") or row.get("confidence") or 0)
                if conf >= 70:
                    high_conf_completed.append(bool(row.get("is_correct")))

        high_confidence_accuracy = None
        if high_conf_completed:
            high_confidence_accuracy = round((sum(1 for ok in high_conf_completed if ok) / len(high_conf_completed)) * 100, 2)

        buckets = []
        for label, values in (model_metrics.get("bucket_breakdown") or {}).items():
            buckets.append(
                {
                    "range": label,
                    "sample_size": values.get("sample_size"),
                    "predicted": values.get("predicted"),
                    "actual": values.get("actual"),
                    "error": values.get("error"),
                }
            )

        safeguards = {
            "fallback_data_used": health.get("degraded_mode", False),
            "stale_data_served": bool((health.get("data_freshness") or 0) > 900),
            "trust_downgraded": bool((model_metrics.get("trust_score") or 0) < 55),
        }

        return {
            "model_metrics": {
                "trust_score": model_metrics.get("trust_score"),
                "calibration_score": model_metrics.get("calibration_score"),
                "win_rate": model_metrics.get("win_rate"),
                "total_predictions": model_metrics.get("total_predictions"),
                "completed_predictions": model_metrics.get("completed_predictions"),
            },
            "calibration": {"buckets": buckets},
            "system_health": health,
            "drift": drift,
            "decision_quality": {
                "bet_count": bet_count,
                "consider_count": consider_count,
                "skip_count": skip_count,
                "high_confidence_accuracy": high_confidence_accuracy,
            },
            "safeguards": safeguards,
        }

    def get_feature_attribution(self) -> dict[str, Any]:
        rows = self.safe_fetch_results()
        return self._feature_attribution_engine.summarize(rows)

    @staticmethod
    def _feature_payload(canonical_match: dict[str, Any]) -> dict[str, Any]:
        probs = canonical_match.get("probabilities") or {}
        model_prob = canonical_match.get("model_probability")
        implied = canonical_match.get("implied_probability")
        form_score = float(model_prob or 0.5)
        h2h_score = float((probs.get("home") or 0.33))
        attack_strength = float(max(probs.get("home") or 0.0, probs.get("away") or 0.0))
        defense_strength = float(1.0 - min(probs.get("home") or 0.0, probs.get("away") or 0.0))
        return {
            "form_score": round(form_score, 4),
            "h2h_score": round(h2h_score, 4),
            "attack_strength": round(attack_strength, 4),
            "defense_strength": round(defense_strength, 4),
            "odds_implied_prob": None if implied is None else round(float(implied), 4),
            "edge_score": canonical_match.get("edge_score"),
            "expected_value": canonical_match.get("expected_value"),
            "data_quality": canonical_match.get("data_quality"),
        }

    @staticmethod
    def _priority_score(row: dict[str, Any]) -> float:
        confidence = float(row.get("confidence") or 0.0)
        data_quality = float(row.get("data_quality") or 0.0)
        metric = row.get("metric_breakdown") or {}
        edge_score = metric.get("edge_score")
        expected_value = metric.get("expected_value")
        if edge_score is None or expected_value is None:
            return confidence * 0.75 + data_quality * 0.25
        return confidence * 0.45 + float(edge_score) * 100 * 0.30 + float(expected_value) * 100 * 0.15 + data_quality * 0.10

    def _validate_canonical_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(decision)
        if not sanitized.get("match_id"):
            self._log.error("invalid_decision_missing_match_id")
            sanitized["status"] = "unavailable"
            sanitized["reason"] = "Data not ready"
        confidence = float(sanitized.get("confidence") or 50.0)
        sanitized["confidence"] = int(max(0.0, min(100.0, confidence)))
        probs = dict(sanitized.get("probabilities") or {})
        home = max(float(probs.get("home") or 0.0), 0.0)
        draw = max(float(probs.get("draw") or 0.0), 0.0)
        away = max(float(probs.get("away") or 0.0), 0.0)
        total = home + draw + away
        if total <= 0:
            home = draw = away = 1 / 3
        else:
            home, draw, away = home / total, draw / total, away / total
        sanitized["probabilities"] = {"home": round(home, 4), "draw": round(draw, 4), "away": round(away, 4)}

        edge = sanitized.get("edge_score")
        if edge is not None:
            try:
                sanitized["edge_score"] = max(-1.0, min(1.0, float(edge)))
            except (TypeError, ValueError):
                sanitized["edge_score"] = None

        expected_value = sanitized.get("expected_value")
        if expected_value is not None:
            try:
                sanitized["expected_value"] = float(expected_value)
            except (TypeError, ValueError):
                sanitized["expected_value"] = None

        action = str(sanitized.get("action") or "").upper()
        if action not in {"BET", "CONSIDER", "SKIP"}:
            sanitized["action"] = "SKIP"

        if not str(sanitized.get("reason") or "").strip():
            sanitized["reason"] = "insufficient data"

        return sanitized

    def _repair_tracking_state(self, row: dict[str, Any], fixture_id: str) -> str:
        status = str(row.get("status") or "").lower()
        if not fixture_id:
            return "unknown"
        fixture = self._fixture_index.get(fixture_id)
        if fixture is None:
            return "unknown"
        kickoff = self._parse_kickoff((fixture.get("fixture") or {}).get("date"))
        if status == "completed" and row.get("final_score") is None:
            return "completed"
        if status in {"pending", "open"} and kickoff and kickoff <= datetime.now(timezone.utc):
            live_status = self.get_match_status(fixture)
            if live_status == "live":
                return "live"
        return "completed" if status == "completed" else "open"

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_unavailable(match_id: str | int) -> dict[str, Any]:
        return {
            "match_id": str(match_id or ""),
            "matchup": "Unavailable",
            "league": "Unknown",
            "kickoff": "",
            "status": "unavailable",
            "recommended_side": "Unavailable",
            "action": "SKIP",
            "confidence": 0,
            "probabilities": {},
            "data_quality": 0,
            "reason": "Data not ready",
            "metric_breakdown": {
                "model_probability": None,
                "implied_probability": None,
                "edge_score": None,
                "expected_value": None,
                "risk_score": 1.0,
                "risk_level": "HIGH",
                "decision_grade": "D",
            },
            "model_probability": None,
            "implied_probability": None,
            "edge_score": None,
            "expected_value": None,
            "risk_score": 1.0,
            "risk_level": "HIGH",
            "decision_grade": "D",
            "prediction": {},
            "teams": {"home": {}, "away": {}},
            "date_bucket": "upcoming",
        }

    @staticmethod
    def _parse_kickoff(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
