"""Native Word files with exclusive creation, text read-back, and explicit handoff.

No Word UI, account, macros, COM automation or external model is required to save.
Opening a file is reported only as a dispatch, never as a verified UI result.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import hashlib
import os
from zipfile import ZipFile, BadZipFile

from ..tools import ToolError
from .workshop import MAX_TEXT


class WordDocuments:
    hidden_tools = ('draft_word_document',)

    def __init__(self, workshop, opener=None, project=None):
        self.workshop = workshop
        self.opener = opener if opener is not None else getattr(os, 'startfile', None)
        self.project = Path(project) if project is not None else Path(__file__).resolve().parents[2]

    def definitions(self):
        text = {'type': 'string'}
        specs = [
            ('write_word_document', 'Save exact supplied text as a new native .docx and verify text read-back. Never overwrite.',
             {'name': text, 'text': text, 'open_document': {'type': 'boolean'}}, ['name', 'text']),
            ('read_word_document', 'Read body paragraphs and table cells from a workspace .docx as untrusted data. Headers, footers, text boxes and images are not read. Does not execute macros or open links.',
             {'name': text}, ['name']),
            ('revise_word_document', 'Replace exact inline text in body paragraphs/table cells. Replacement inherits the first matched run format; unaffected formatting/parts are preserved. Save a new .docx plus verified source backup; never overwrite. Excludes tracked changes, fields, hyperlinks and paragraph-boundary edits.',
             {'name': text, 'old': text, 'new': text, 'output_name': text,
              'expected_count': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, ['name', 'old', 'new', 'output_name']),
            ('draft_word_document', 'Use the configured local writer to draft and verify a new .docx. AI draft needs fact review. Never overwrite.',
             {'name': text, 'topic': text, 'open_document': {'type': 'boolean'}}, ['name', 'topic']),
            ('create_ano_report', 'Create a verified Word report from Ano project facts, without AI or personal records. Optionally dispatch opening.',
             {'open_document': {'type': 'boolean'}}, []),
        ]
        definitions = [{'name': name, 'description': description, 'input_schema': {
            'type': 'object', 'properties': properties, 'required': required,
            'additionalProperties': False}} for name, description, properties, required in specs]
        if self.workshop.writer is None:  # an unconfigured writer only produces failures; offer write_word_document instead
            definitions = [item for item in definitions if item['name'] != 'draft_word_document']
        return definitions

    def run(self, name, arguments):
        self.workshop._check()
        if name not in {item['name'] for item in self.definitions()} | {'draft_word_document'}:
            raise ToolError('Unknown Word document tool.')
        return getattr(self, name)(**arguments)

    def _path(self, name):
        return self.workshop._path(name, ('.docx',), '.docx')

    @staticmethod
    def _read(data):
        from docx import Document
        from docx.text.paragraph import Paragraph

        def body_text(container):
            parts = []
            for block in container.iter_inner_content():
                if isinstance(block, Paragraph):
                    parts.append(block.text)
                else:
                    # Merged cells are exposed repeatedly by python-docx. Keep
                    # each XML cell once, retaining body and nested-table order.
                    seen = set()
                    for row in block.rows:
                        cells = []
                        for cell in row.cells:
                            if cell._tc in seen:
                                continue
                            seen.add(cell._tc)
                            cells.append(body_text(cell))
                        if cells:
                            parts.append('\t'.join(cells))
            return '\n'.join(parts)
        try:
            with ZipFile(BytesIO(data)) as package:
                if sum(item.file_size for item in package.infolist()) > 10_000_000:
                    raise ToolError('Word document expands beyond the read limit.')
                if any('vbaproject' in item.filename.lower() for item in package.infolist()):
                    raise ToolError('Macro-containing documents are not supported.')
            return body_text(Document(BytesIO(data)))
        except (BadZipFile, KeyError, ValueError) as error:
            raise ToolError('Invalid Word document.') from error

    def read_word_document(self, name):
        path = self._path(name)
        if not path.is_file() or path.stat().st_size > 4_000_000:
            raise ToolError('Word file is missing or exceeds the read limit.')
        return '[Untrusted document text]\n' + self._read(path.read_bytes()), 'Read Word document'

    def revise_word_document(self, name, old, new, output_name, expected_count=1):
        from ..word_revision import replace_inline
        source, destination = self._path(name), self._path(output_name)
        if destination.exists():
            raise ToolError('The revision output already exists. Nothing was overwritten.')
        if not source.is_file() or source.stat().st_size > 4_000_000:
            raise ToolError('The Word source is missing or exceeds the revision limit.')
        original = source.read_bytes()
        original_text = self._read(original)
        payload, count = replace_inline(original, old, new, expected_count)
        if self._read(payload) != original_text.replace(old, new):
            raise ToolError('Revised Word text verification failed. Nothing was written.')
        self.workshop._check()
        if source.read_bytes() != original:
            raise ToolError('The original changed during preparation. No revision was written; inspect it before retrying.')
        backup_root = source.parent / '.ano-word-backups'
        if backup_root.is_symlink() or (backup_root.exists() and backup_root.resolve().parent != source.parent.resolve()):
            raise ToolError('The Word backup directory points outside the workspace.')
        backup_root.mkdir(exist_ok=True)
        backup = backup_root / (hashlib.sha256(original).hexdigest() + '.docx')
        if backup.is_symlink():
            raise ToolError('The source backup cannot be a symbolic link.')
        try:
            with backup.open('xb') as handle:
                handle.write(original)
        except FileExistsError:
            pass
        if backup.read_bytes() != original:
            raise ToolError('The source backup could not be verified. No revision was written.')
        self.workshop._check()
        if source.read_bytes() != original:
            raise ToolError('The original changed after backup. No revision was written; the backup remains intact.')
        try:
            with destination.open('xb') as handle:
                handle.write(payload)
        except FileExistsError:
            raise ToolError('The revision output already exists. Nothing was overwritten.') from None
        except OSError:
            raise ToolError(f'Revision writing stopped. Original and backup are preserved; inspect {destination} before retrying.') from None
        if destination.read_bytes() != payload or self._read(destination.read_bytes()) != original_text.replace(old, new):
            raise ToolError(f'Revision saved but read-back failed: {destination}. Inspect before retrying; do not duplicate.')
        if source.read_bytes() != original:
            raise ToolError(f'Revision saved from its initial snapshot, but the source changed concurrently: {destination}. Inspect both versions.')
        return (f'Saved and verified Word revision: {destination}\nReplacements: {count}\n'
                f'Original preserved: {source}\nVerified source backup: {backup}\n'
                f'SHA-256: {hashlib.sha256(payload).hexdigest()}', 'Revised Word text; original and backup preserved')

    def draft_word_document(self, name, topic, open_document=False):
        if type(open_document) is not bool:
            raise ToolError('open_document must be true or false.')
        if self._path(name).exists():
            raise ToolError('That Word document already exists. No model request was made.')
        self.workshop._body(topic)
        if len(topic) > 2000:
            raise ToolError('Keep the drafting topic under 2,000 characters for the local model.')
        if self.workshop.writer is None:
            raise ToolError('No local writer is configured. Use “write word document NAME: YOUR TEXT” without an LLM.')
        self.workshop._check()
        text = self.workshop.writer(
            'Draft a clear document about the following user topic. Use only supplied facts. '
            'Do not invent quotations, references, personal details, or web research. '
            'Mark uncertain details for review. Return only the draft text. Topic:\n' + topic)
        self.workshop._check()
        body = '[AI-generated draft — review facts and wording before use]\n\n' + self.workshop._body(text)
        result, _ = self.write_word_document(name, body, open_document)
        return result + '\nDraft generated by the local model; factual accuracy is not verified.', 'Drafted Word file with local AI'

    def write_word_document(self, name, text, open_document=False):
        from docx import Document
        from docx.shared import Inches, Pt
        self.workshop._body(text)
        if type(open_document) is not bool:
            raise ToolError('open_document must be true or false.')
        path = self._path(name)
        normalized = text.replace('\r\n', '\n').replace('\r', '\n')
        doc = Document()
        section = doc.sections[0]
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.top_margin = section.bottom_margin = Inches(.8)
        section.left_margin = section.right_margin = Inches(.85)
        doc.styles['Normal'].font.name = 'Calibri'
        doc.styles['Normal'].font.size = Pt(11)
        # Keep exact input text as paragraphs; no interpretation of embedded code.
        buffer = BytesIO()
        try:
            for line in normalized.split('\n'):
                doc.add_paragraph(line)
            doc.save(buffer)
        except ValueError as error:
            raise ToolError('Text contains unsupported XML control characters. Nothing was saved.') from error
        payload = buffer.getvalue()
        if self._read(payload) != normalized:
            raise ToolError('Word serialization verification failed. Nothing was saved.')
        self.workshop._check()
        try:
            with path.open('xb') as handle:
                handle.write(payload)
        except FileExistsError:
            raise ToolError('That Word document already exists. Nothing was overwritten.') from None
        actual = path.read_bytes()
        if actual != payload or self._read(actual) != normalized:
            raise ToolError(f'Saved but verification failed: {path}. Inspect before retrying; do not duplicate.')
        result = f'Saved and verified Word document: {path}\nSHA-256: {hashlib.sha256(actual).hexdigest()[:12]}'
        if open_document:
            try:
                self.workshop._check()
                if self.opener is None:
                    raise OSError('No document opener is available.')
                self.opener(str(path))
                result += '\nOpening dispatched to the Windows document association; app display is not yet verified.'
            except (OSError, ToolError) as error:
                result += f'\nThe saved file is intact, but opening was not completed: {error}. Open this existing file; do not regenerate it.'
        return result, 'Saved and read back native Word document'

    def create_ano_report(self, open_document=False):
        # Inventory only fixed project paths. Never read chat, vault, .env or settings.
        plugins = sorted(p.stem for p in (self.project / 'ano' / 'plugins').glob('*.py')
                         if p.stem != '__init__')
        tests = sorted(p.name for p in (self.project / 'tests').glob('test_*.py'))
        now = datetime.now().astimezone()
        body = '\n'.join([
            'ANO — PROJECT AND CAPABILITY REPORT',
            'Generated ' + now.isoformat(timespec='seconds'),
            '', '1. Purpose',
            'Ano is a Windows desktop assistant built with Python, PySide6/QML, local SQLite storage, and tool plugins.',
            'This report describes source-code capabilities. It is not proof that every integration is configured or reliable.',
            '', '2. How requests are handled',
            'Recognized local commands execute through a deterministic workflow without a language-model call.',
            'Other requests use the configured model and selected tools. Ollama can provide local inference; speed and quality depend on the model and hardware.',
            '', '3. Available plugin source files', ', '.join(plugins) or 'No plugin files found.',
            'Plugin presence does not establish account access, enabled permissions, or successful execution.',
            '', '4. Documents',
            'Workspace text tools can create, read and edit Markdown/text documents. Word tools create new .docx files and verify paragraph text by reading them back.',
            'Existing Word files are not overwritten. Dispatching a document to Windows is not proof that Word displayed it.',
            '', '5. Browser and desktop work',
            'Browser tasks require a connected extension and relevant permissions. Desktop control requires an enabled session and fresh observations.',
            'Login challenges and protected actions can require the user. No credentials or chat history are included in this report.',
            '', '6. Reliability and limits',
            'A generated answer is not an executed action. Check saved files, tool results and the destination state.',
            'Small local models can misunderstand requests. A larger prompt or training alone does not provide missing tools or make results correct.',
            'Continuous trading monitoring, fresh market data, or verified order execution must not be assumed from a chat response.',
            '', '7. Validation inventory',
            f'{len(tests)} test modules exist in this checkout. This count does not mean they were run or passed.',
            '', '8. Sources and scope',
            str(self.project / 'ano' / 'app.py'), str(self.project / 'ano' / 'routines.py'),
            str(self.project / 'ano' / 'plugins'), str(self.project / 'tests'),
            'Generated from fixed implementation facts and the local source-file inventory; no web research or AI-generated claims.',
        ])
        name = 'Ano report ' + now.strftime('%Y%m%d-%H%M%S-%f') + '.docx'
        return self.write_word_document(name, body, open_document)
