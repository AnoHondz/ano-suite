import json
from datetime import date, timedelta

import pytest


@pytest.fixture
def trainer(monkeypatch):
    from scripts import train_player_props
    monkeypatch.setenv('SCORPRED_PROPS_TRAINING', '1')
    return train_player_props


def test_nfl_fit_preserves_other_sports_and_records_run(trainer, monkeypatch, tmp_path):
    monkeypatch.setattr(trainer, 'ROOT', tmp_path)
    model = tmp_path / 'data/models/player_props_calibration_v1.json'
    report = tmp_path / 'data/validation/player_props_training.json'
    for path in (model, report):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': 'player-props-calibration-v1',
                                    'markets': {'nba/receptions': {'sentinel': True}}}))
    dataset = tmp_path / 'examples.jsonl'
    rows = [{'sport': 'nfl', 'market': 'receptions',
             'date': str(date(2010, 1, 1) + timedelta(days=i)),
             'p': .8, 'record': .5, 'y': i % 2} for i in range(1000)]
    dataset.write_text('\n'.join(json.dumps(r) for r in rows))
    trainer.fit(dataset, [{'season': 2010}], 'nfl')
    body = json.loads(model.read_text())
    assert body['markets']['nba/receptions'] == {'sentinel': True}
    assert 'nfl/receptions' in body['markets']
    assert body['sport_runs']['nfl']['examples'] == 1000
    details = json.loads((tmp_path / 'data/validation/nfl_props_training.json').read_text())
    parts = details['markets']['nfl/receptions']['splits']
    assert parts['train']['end'] < parts['validation']['start']
    assert parts['validation']['end'] < parts['test']['start']


def test_nfl_replay_discovers_every_stored_season_and_rejects_sealed_games(trainer, monkeypatch):
    import io
    from services import nfl_reliability
    monkeypatch.setattr(trainer.store, 'metadata', lambda: [
        {'dataset': 'player_stats', 'season': 2023},
        {'dataset': 'player_stats', 'season': 2026}])
    seen = []
    monkeypatch.setattr(trainer.nfl_props, '_rows', lambda season: seen.append(season) or [])
    monkeypatch.setattr(trainer.nfl_props, '_fingerprint', lambda season: str(season))
    monkeypatch.setattr(trainer.store, 'records', lambda *_: [])
    assert [r['season'] for r in trainer.nfl_rows(io.StringIO())] == [2023, 2026]
    assert seen == [2023, 2022, 2026, 2025]
    def denied(_):
        raise ValueError('sealed')
    monkeypatch.setattr(nfl_reliability, 'reject_sealed_games', denied)
    with pytest.raises(ValueError, match='sealed'):
        trainer.nfl_rows(io.StringIO())


def test_anytime_calibration_keeps_yes_probability_and_original_input(monkeypatch):
    from services import prop_calibration as calibration
    monkeypatch.setattr(calibration, 'parameters', lambda *_: {'slope': .7, 'intercept': -.3})
    row = {'market': 'anytime_td', 'probability': .6,
           'pick': {'side': 'yes', 'line': None, 'probability': .6, 'record': .4}}
    result = calibration.apply(row, 'nfl')
    assert result['probability'] == result['pick']['probability']
    assert result['probability'] == pytest.approx(calibration.adjusted(.6, {'slope': .7, 'intercept': -.3}), abs=.0001)
    assert result['pick']['side'] == 'yes'
    assert row['probability'] == row['pick']['probability'] == .6


def test_fast_replay_preserves_exact_market_pick_prices():
    from services import nfl_props
    for market in nfl_props.MARKETS:
        values = [2, 5, 3, 1, 4, 2]
        full = nfl_props.project_market(market, values, values, None, None)
        fast = nfl_props.project_market(market, values, values, None, None, include_distribution=False)
        assert fast['pick'] == full['pick']
        assert fast['projection'] == full['projection']
