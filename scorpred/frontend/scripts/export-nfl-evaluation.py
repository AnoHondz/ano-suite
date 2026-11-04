"""Export public historical metrics only; never export models or game predictions.

Run from the repository root. The source report remains immutable and disabled.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('cache/nflverse/phase2_1/class_b/run_d51f0ec86b0dde870b81a606/evaluation.json')
MODELS = {
    'home_prior:selected': 'Home prior',
    'elo:selected': 'Elo',
    'logistic_baseline:selected': 'Small logistic',
    'full_logistic:selected': 'Expanded logistic',
    'nonlinear_forest:selected': 'Random forest',
}


def main():
    raw = (ROOT / SOURCE).read_bytes()
    report = json.loads(raw)
    assert report['research_only'] is True
    assert report['enabled'] is False and report['production_active'] is False
    assert report['evidence_class'] == 'B' and report['evaluated_games'] == 139
    models = []
    for key, label in MODELS.items():
        metrics = report['models'][key]
        assert metrics['count'] == report['evaluated_games']
        models.append({'id': key, 'label': label, **{
            field: metrics[field] for field in ['count', 'log_loss', 'brier', 'accuracy', 'reliability']
        }})
    windows = [fold['windows']['test'] for fold in report['folds']]
    snapshot = {
        'source': SOURCE.as_posix(), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'run_id': SOURCE.parent.name,
        **{key: report[key] for key in ['version', 'feature_version', 'generated_at',
           'dataset_fingerprint', 'evidence_class', 'research_only', 'enabled',
           'production_active', 'evaluated_games', 'eligible_games', 'improvement_established', 'tie_policy']},
        'test_start': min(window['start'] for window in windows),
        'test_end': max(window['end'] for window in windows),
        'models': models,
        'forest_vs_prior': report['paired_comparisons_vs_home_prior']['nonlinear_forest:selected'],
    }
    destination = ROOT / 'frontend/src/data/nfl-phase21-evaluation.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(snapshot, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(models)} models / {snapshot["evaluated_games"]} games to {destination.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
