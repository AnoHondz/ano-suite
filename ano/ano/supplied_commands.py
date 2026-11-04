"""Whole-command parser for explicitly supplied document text, never generation.

Quoted values are literal: interior whitespace and backslashes are preserved.
Only the outer quote delimiters are removed; no JSON or code is evaluated.
"""
from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass
class SuppliedDocument:
    tool: str = ''
    arguments: dict = field(default_factory=dict)
    question: str = ''


PREFIX = re.compile(r'^(?:create|make|write|save) (?:an? )?'
    r'(?P<kind>word document|word doc|docx|pdf|text file|text document|markdown file|markdown document|document) '
    r'(?:named|called)\s+', re.I)
BODY = re.compile(r'^(?:containing (?:exactly )?(?:this )?(?:text|content)|'
                  r'with (?:the |this )?(?:exact )?(?:text|body|content))\b\s*:?\s*', re.I)
TITLE = re.compile(r'^with (?:the )?title\b\s*:?\s*', re.I)
AND_BODY = re.compile(r'^and (?:the )?(?:body|text)\s*:?\s*', re.I)
QUOTES = {'"':'"', "'":"'", '\u201c':'\u201d', '\u2018':'\u2019'}


def supplied_document_prefix(text):
    return bool(PREFIX.match(text))


def quoted_value(text):
    text = text.lstrip()
    if not text or text[0] not in QUOTES:
        raise ValueError('Use quoted values')
    closing, slashes = QUOTES[text[0]], 0
    for index in range(1, len(text)):
        char = text[index]
        if char == closing and (text[0] not in ('"', "'") or slashes % 2 == 0):
            return text[1:index], text[index+1:].lstrip()
        slashes = slashes + 1 if char == '\\' else 0
    raise ValueError('Unclosed quoted value')


def parse_supplied_document(text):
    match = PREFIX.match(text)
    if not match:
        return None
    remainder = text[match.end():]
    # Creative writing and incomplete specifications still go to reasoning.
    if not re.search(r'\b(?:containing .*?(?:text|content)|with .*?(?:text|body|content|title))\b', remainder[:1000], re.I | re.S):
        return None
    invalid = SuppliedDocument(question='Use a quoted file name and quoted exact text, with no extra instructions. Nothing was written.')
    try:
        name, remainder = quoted_value(remainder)
        title = None
        body_match = BODY.match(remainder)
        if body_match:
            payload = remainder[body_match.end():]
            if (not payload or payload[0] not in QUOTES) and ':' not in body_match[0] and 'exact' not in body_match[0].lower():
                return None
            body, tail = quoted_value(payload)
        elif match['kind'].lower() == 'pdf' and (title_match := TITLE.match(remainder)):
            title, remainder = quoted_value(remainder[title_match.end():])
            and_body = AND_BODY.match(remainder)
            if not and_body:
                return None
            body, tail = quoted_value(remainder[and_body.end():])
        else:
            return None
    except ValueError:
        return invalid
    if tail.lower().rstrip('.!?') not in ('', 'please', 'for me'):
        return None  # never execute just the first part of a larger request
    if not name.strip() or not body.strip():
        return invalid
    if (len(name) > 120 or name != name.strip() or name.startswith('.') or name.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)):
        return SuppliedDocument(question='Use a simple file name without a path or special characters. Nothing was written.')
    kind, extension = match['kind'].lower(), Path(name).suffix.lower()
    expected = ('.docx' if kind in ('word document','word doc','docx') else '.pdf' if kind == 'pdf'
                else '.txt' if kind.startswith('text') else '.md' if kind.startswith('markdown') else None)
    if expected and extension and extension != expected:
        return SuppliedDocument(question=f'The requested format needs {expected}, but the name has {extension}. Nothing was written.')
    extension = extension or expected or '.md'
    if extension not in ('.docx','.pdf','.txt','.md'):
        return SuppliedDocument(question='Choose Word, PDF, text or Markdown for exact supplied content. Nothing was written.')
    name = name if Path(name).suffix else name + extension
    if extension == '.pdf':
        return SuppliedDocument('create_pdf', dict(name=name,title=title if title is not None else Path(name).stem,text=body))
    if extension == '.docx':
        return SuppliedDocument('write_word_document', dict(name=name,text=body))
    return SuppliedDocument('write_document', dict(name=name,text=body))
