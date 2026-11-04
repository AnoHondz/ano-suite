"""Exercise exact Word revision commands, then retain files for native visual QA."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.plugins.word_documents import WordDocuments
from ano.plugins.workshop import Workshop
from ano.routines import LocalRoutines, RoutineStore, RoutineBrain
from ano.tools import Toolbox


def main(source):
    root = source.resolve().parent
    before = source.read_bytes()
    plugin = WordDocuments(Workshop(root, None))
    toolbox = Toolbox(lambda: [root], plugins=[plugin])
    brain = RoutineBrain(LocalRoutines(toolbox, RoutineStore(root / 'routines.db')))
    results = []
    for name, old, new, output in [(source.name, 'Ravi', 'Mira', 'Name revision.docx'),
                                    ('Name revision.docx', 'Pending', 'Approved', 'Table revision.docx')]:
        command = 'revise word document ' + name + ': ' + json.dumps({'old': old, 'new': new, 'output_name': output})
        result = brain.reply([{'role': 'user', 'body': command}])
        results.append(result)
    revised = root / 'Table revision.docx'
    content = plugin._read(revised.read_bytes())
    passed = source.read_bytes() == before and 'Hello Mira, exact body remains.' in content and 'Approved' in content and 'Pending' not in content
    report = {'scope': 'Actual command parser/tool paths, no model. Public fixture only; native Word visual check pending.',
              'original': str(source.resolve()), 'original_sha256': hashlib.sha256(before).hexdigest(),
              'revised': str(revised), 'revised_sha256': hashlib.sha256(revised.read_bytes()).hexdigest(),
              'backups': len(list((root / '.ano-word-backups').glob('*.docx'))), 'passed': passed, 'results': results}
    (root / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    main(parser.parse_args().source)
