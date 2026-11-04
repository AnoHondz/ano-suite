"""Independent, bounded output checks. Model prose is never completion evidence."""
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile
from xml.etree import ElementTree

ARTIFACT_TOOLS = {'create_pdf', 'create_spreadsheet', 'create_presentation', 'write_document',
                  'append_document', 'replace_document_text', 'draft_document', 'draft_word_document',
                  'write_word_document', 'revise_word_document', 'create_ano_report'}
FORMATS = {'.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.md'}


class OutputMismatch(ValueError):
    """A missing requested output can be repaired without repeating uncertain actions."""


def inspect_output(path):
    """Read the actual bytes, not a plugin's claimed success or supplied hash."""
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 16*1024*1024:
        raise ValueError('Output is missing, empty or too large for bounded verification.')
    data = path.read_bytes()
    if path.suffix.lower() not in FORMATS:
        raise ValueError('No independent checker for this output format.')
    result = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
              'format': path.suffix.lower(), 'text': ''}
    if path.suffix.lower() in {'.txt', '.md'}:
        result['text'] = data.decode('utf-8')
    elif path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted or not 0 < len(reader.pages) <= 100:
            raise ValueError('PDF must be readable and at most 100 pages.')
        result.update(pages=len(reader.pages), text='\n'.join(p.extract_text() or '' for p in reader.pages))
    else:
        with ZipFile(path) as archive:
            if sum(x.file_size for x in archive.infolist()) > 32*1024*1024:
                raise ValueError('Office output exceeds the expanded verification limit.')
            names = archive.namelist()
            if path.suffix.lower() == '.docx':
                selected = ['word/document.xml']
            elif path.suffix.lower() == '.pptx':
                selected = sorted(n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n))
                result['slides'] = len(selected)
            else:
                selected = [n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)]
                if 'xl/sharedStrings.xml' in names:
                    selected.append('xl/sharedStrings.xml')
                result['sheets'] = len(selected) - int('xl/sharedStrings.xml' in selected)
                sheets = [n for n in selected if n != 'xl/sharedStrings.xml']
                if sheets:
                    rows = ElementTree.fromstring(archive.read(sheets[0])).findall('.//{*}sheetData/{*}row')
                    result['data_rows'] = max(0, len(rows)-1)
            if not selected:
                raise ValueError('Office output contains no readable content.')
            result['text'] = '\n'.join(' '.join(ElementTree.fromstring(archive.read(n)).itertext()) for n in selected)
    if not result['text'].strip():
        raise ValueError('Output has no readable content.')
    return result


def capture_output(store, receipt, tool, arguments, result, roots):
    if receipt is None or tool not in ARTIFACT_TOOLS or not roots:
        return
    from .tools import network_path
    candidates = []
    try:
        value = json.loads(result)
        if isinstance(value, dict) and isinstance(value.get('path'), str):
            candidates.append(value['path'])
    except (ValueError, TypeError):
        pass
    candidates += re.findall(r'(?m)^Saved and (?:read back|verified Word document): (.+)$', str(result))
    for value in candidates:
        if network_path(value):
            continue
        path = Path(value).resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in roots):
            continue
        checked = inspect_output(path)
        store.record_output(receipt[0], {k: checked[k] for k in ('path', 'sha256', 'bytes', 'format')})


def required_formats(goal):
    patterns = {'.pdf': r'\bpdf\b', '.docx': r'\b(?:docx|word document|word report)\b',
                '.xlsx': r'\b(?:xlsx|spreadsheet|workbook)\b', '.pptx': r'\b(?:pptx|powerpoint|slides?|presentation)\b',
                '.txt': r'\btxt\b', '.md': r'\b(?:markdown|\.md)\b'}
    return {ext for ext, pattern in patterns.items() if re.search(pattern, goal, re.I)}


class GoalVerifier:
    def __init__(self, store):
        self.store = store

    def __call__(self, goal, jobs):
        if not jobs:
            return {'passed': False, 'reason': 'No task-linked independent output evidence was recorded.'}
        for job in jobs:
            if any(s['state'] in ('running', 'uncertain') for s in self.store.steps(job)):
                return {'passed': False, 'reason': 'An action is still running or uncertain; inspect it without replaying it.'}
        expected = required_formats(goal)
        if not expected:
            return {'passed': False, 'reason': 'This goal has no registered independent completion contract. The answer is not proof of completion.'}
        # Only outputs produced by this goal's actual jobs can qualify.
        outputs = {o['path']: o for job in jobs for o in self.store.outputs(job)}
        checked = []
        try:
            for output in outputs.values():
                current = inspect_output(output['path'])
                if current['sha256'] != output['sha256']:
                    raise ValueError('A recorded output changed after its tool receipt; fresh read-back is needed.')
                checked.append(current)
            missing = expected - {o['format'] for o in checked}
            if missing:
                raise OutputMismatch('Missing verified requested format: ' + ', '.join(sorted(missing)))
            literals = re.findall(r'[“"]([^”"\n]{1,300})[”"]', goal)
            quoted_names = [Path(x).name for x in literals if re.search(r'\.(?:pdf|docx|xlsx|pptx|txt|md)$',x,re.I)]
            named = quoted_names or re.findall(r'\b([\w-]+\.(?:pdf|docx|xlsx|pptx|txt|md))\b', goal, re.I)
            if any(not any(Path(o['path']).name.casefold() == name.casefold() for o in checked) for name in named):
                raise OutputMismatch('The requested output filename was not verified.')
            targets = [o for o in checked if not named or Path(o['path']).name.casefold() in {n.casefold() for n in named}]
            from .local_profile import requested_slide_total, NUMBERS
            count = requested_slide_total(goal)
            if count is not None and not any(o.get('slides') == count for o in targets):
                raise OutputMismatch('The presentation does not have the requested total slide count.')
            if count is None and re.search(r'\b(?:\d+|'+'|'.join(NUMBERS)+r')[ -]+slides?\b',goal,re.I):
                raise ValueError('The requested slide-count convention needs an explicit total before certification.')
            text = '\n'.join(o['text'] for o in targets)
            for match in re.finditer(r'\b(\d+|'+'|'.join(NUMBERS)+r')[ -]+(?:numbered )?(steps|data rows)\b',goal,re.I):
                value=match[1].lower();number=int(value) if value.isdigit() else NUMBERS[value]
                actual=(len(set(re.findall(r'(?m)^\s*(\d+)[.)]\s+',text))) if match[2].lower()=='steps'
                        else next((o.get('data_rows') for o in targets if o['format']=='.xlsx'),None))
                if actual!=number:
                    raise OutputMismatch('Output does not match the requested '+match[2]+ ' count.')
            for literal in literals:
                if not re.search(r'\.(?:pdf|docx|xlsx|pptx|txt|md)$', literal, re.I) and literal not in text:
                    raise OutputMismatch('Requested exact text is absent from the output.')
            return {'passed': True, 'references': [o['path']+' sha256:'+o['sha256'] for o in checked],
                    'scope': 'Actual file read-back, requested formats, explicit filenames, slide/numbered-step/data-row counts and quoted text. Factual accuracy and visual quality are not certified.'}
        except (OSError, ValueError, KeyError, ElementTree.ParseError) as error:
            return {'passed': False, 'reason': str(error), 'repairable': isinstance(error, OutputMismatch)}
