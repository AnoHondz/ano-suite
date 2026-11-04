"""Causality, research isolation, calibration and preview expiry contracts."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest

import app
from scripts import evaluate_nba_early_season as study
from services import nba_early_season as research
from services import nba_matchup_context as context_service

NOW = datetime(2026, 10, 22, 16, tzinfo=UTC)


def games():
    result = []
    for season in (2022, 2023, 2024, 2025):
        for index in range(20):
            for pair in range(15):
                result.append({"game_id": f"{season}-{index}-{pair}", "season": season,
                    "kickoff": (datetime(season - 1, 10, 1, tzinfo=UTC) + timedelta(days=index*2)).isoformat(),
                    "home_team": str(pair*2+1), "away_team": str(pair*2+2), "neutral": False,
                    "actual": {"home": 100 + (index*7+pair+season) % 25, "away": 101 + (index*3+pair*2) % 23}})
    return result


def preview_inputs():
    summary = {"games": 82, "points_for": 110., "points_against": 108., "recent_results": []}
    team = {"previous": summary, "current": {"games": 0, "points_for": None, "points_against": None, "recent_results": []},
            "current_source": {"state": "fresh", "observed_at": (NOW-timedelta(minutes=1)).isoformat(), "expires_at": (NOW+timedelta(minutes=5)).isoformat()}}
    context = {"game": {"id": "123", "kickoff": (NOW+timedelta(hours=8)).isoformat(), "season_type": 2, "neutral_site": False, "status": {"state": "pre"}},
               "statistics_cutoff": (NOW-timedelta(hours=24)).isoformat(), "teams": {"home": deepcopy(team), "away": deepcopy(team)}}
    artifact = {"generated_at": NOW.isoformat(), "calibration": {"count": 96, "end": "2025-11-01T00:00:00+00:00", "margin_bias": 0., "margin_sigma": 15., "radius": [15., 16.]}}
    return context, artifact


def test_target_and_future_outcomes_cannot_change_this_games_features():
    original = games()
    before, _ = study.candidates(original)
    target = next(row for row in before if row["season"] == 2024)
    changed = deepcopy(original)
    next(row for row in changed if row["game_id"] == target["game_id"])["actual"] = {"home": 190, "away": 85}
    after, _ = study.candidates(changed)
    same = next(row for row in after if row["game_id"] == target["game_id"])
    assert same["blend"] == target["blend"]
    assert same["input_end"] < same["statistics_cutoff"]


def test_validation_uses_prior_seasons_and_does_not_evaluate_reserved_seasons():
    rows = games()
    report, _, forecasts = study.evaluate(rows)
    extended = deepcopy(rows)
    for row in extended:
        if row["season"] == 2025:
            row["actual"]["home"] += 20
    extended.append({**rows[0], "game_id": "reserved", "season": 2026, "actual": {"home": 300, "away": 1}})
    later_report, _, later = study.evaluate(extended)
    selected = lambda values: [(r["game_id"], r["probabilities"], r["blend"]) for r in values if r["season"] == 2024]
    assert selected(forecasts) == selected(later)
    assert set(report["seasons"]) == {"2024", "2025"}
    assert later_report["evaluated_games"] == report["evaluated_games"]
    assert report["production_eligible"] is False
    assert all(row["calibration"]["blend"]["end"] < row["statistics_cutoff"] for row in forecasts)


def test_archive_loader_never_opens_reserved_months_or_writes_sources(tmp_path):
    (tmp_path / "202610.json").write_text("This reserved file must never be parsed")
    path = tmp_path / "202310.json"
    from tests.test_nba_matchup_context import event
    record = event("123", "2023-10-25T00:00Z", season=2024)
    record["competitions"][0]["neutralSite"] = False
    path.write_text(json.dumps({"events": [record]}))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    rows, sources = study.load_games(tmp_path)
    assert len(rows) == len(sources) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_disabled_preview_never_loads_a_game(monkeypatch):
    monkeypatch.delenv("NBA_EARLY_SEASON_RESEARCH_ENABLED", raising=False)
    monkeypatch.setattr(context_service, "game_for_id", lambda *a: pytest.fail("Disabled previews must not read games"))
    assert app.app.test_client().get('/api/nba/research/early-season/matches/123').status_code == 404


def test_valid_preview_is_separate_and_uses_real_sources():
    context, artifact = preview_inputs()
    prediction = research.forecast(context, artifact, now=NOW)
    assert prediction["role"] == "research_only"
    assert prediction["production_eligible"] is False
    assert prediction["home_probability"] + prediction["away_probability"] == 1
    assert prediction["projected_score"]["inputs"][0]["current_weight"] == 0


@pytest.mark.parametrize('problem', ['past_game', 'missing_venue', 'stale_source', 'future_receipt', 'future_calibration', 'stale_artifact', 'insufficient_history', 'short_lag'])
def test_preview_abstains_on_invalid_availability_or_provenance(problem):
    context, artifact = preview_inputs()
    if problem == 'past_game': context['game']['kickoff'] = NOW.isoformat()
    if problem == 'missing_venue': context['game']['neutral_site'] = None
    if problem == 'stale_source': context['teams']['home']['current_source']['expires_at'] = NOW.isoformat()
    if problem == 'future_receipt': context['teams']['home']['current_source']['observed_at'] = (NOW+timedelta(minutes=1)).isoformat()
    if problem == 'future_calibration': artifact['calibration']['end'] = NOW.isoformat()
    if problem == 'stale_artifact': artifact['generated_at'] = (NOW-timedelta(days=31)).isoformat()
    if problem == 'insufficient_history': context['teams']['away']['previous']['games'] = 1
    if problem == 'short_lag': context['statistics_cutoff'] = NOW.isoformat()
    with pytest.raises(ValueError): research.forecast(context, artifact, now=NOW)


def test_corrupt_or_changed_research_artifact_abstains(tmp_path, monkeypatch):
    monkeypatch.setattr(research, 'DIRECTORY', tmp_path)
    raw = json.dumps({'version': research.VERSION, 'method_sha256': 'obsolete'}).encode()
    (tmp_path/'artifact.json').write_bytes(raw)
    (tmp_path/'manifest.json').write_text(json.dumps({'artifact_sha256': hashlib.sha256(raw).hexdigest()}))
    with pytest.raises(ValueError, match='version changed'): research.load_artifact()
    (tmp_path/'artifact.json').write_text('{}')
    with pytest.raises(ValueError, match='integrity'): research.load_artifact()


def test_missing_report_is_explicit_and_default_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(research, 'DIRECTORY', tmp_path)
    monkeypatch.delenv('NBA_EARLY_SEASON_RESEARCH_ENABLED', raising=False)
    response = app.app.test_client().get('/api/nba/research/early-season')
    assert response.json['available'] is False
    assert response.json['production_enabled'] is False
    assert response.json['research_enabled'] is False


def test_prospective_snapshot_is_immutable_and_never_captures_a_post_tipoff_outcome(tmp_path):
    from scripts.snapshot_nba_early_season import capture
    context, artifact = preview_inputs()
    artifact['protocol_sha256'] = 'frozen-protocol'
    path = tmp_path/'prospective.json'
    assert capture(context, artifact, path, now=NOW)['status'] == 'research_snapshot_saved'
    original = path.read_bytes()
    context['game']['kickoff'] = NOW.isoformat()
    context['game']['result'] = {'home': 120, 'away': 100}
    assert capture(context, artifact, path, now=NOW)['status'] == 'existing_snapshot_preserved'
    assert path.read_bytes() == original
    with pytest.raises(ValueError): capture(context, artifact, tmp_path/'late.json', now=NOW)
    assert not (tmp_path/'late.json').exists()
