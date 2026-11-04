"""Exercise the no-model command route and retain a public Excel QA fixture."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop
from ano.routines import LocalRoutines, RoutineBrain, RoutineStore
from ano.tools import Toolbox


def main():
    root = Path(__file__).resolve().parents[1] / '.runtime' / ('formula-fixture-' + time.strftime('%Y%m%d-%H%M%S'))
    plugin = Artifacts(Workshop(root, None))
    toolbox = Toolbox(lambda: [root], plugins=[plugin])
    brain = RoutineBrain(LocalRoutines(toolbox, RoutineStore(root / 'routines.db')))
    spec = {'headers': ['Category', 'Amount'], 'rows': [['Item A', 10], ['Item B', 20], ['Item C', 30],
             ['Total', None], ['Average', None]], 'formulas': {'B5': '=SUM(B2:B4)', 'B6': '=AVERAGE(B2:B4)'}, 'chart_column': 2}
    command = 'create spreadsheet Cached-formula-check: ' + json.dumps(spec)
    reply = brain.reply([{'role': 'user', 'body': command}])
    report = {'scope': 'Actual local command parser/tool path, synthetic data, no model; not a native Ano UI command.',
              'reply': reply, 'native_excel_check': 'Pending', 'path': str(root / 'Cached-formula-check.xlsx')}
    (root / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
