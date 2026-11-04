"""Exact inline revisions preserving every DOCX part except document.xml.

No macros, tracked-change edits, field updates, network, or Word UI automation.
The original remains intact; the caller creates a separate revised document.
"""
from io import BytesIO
import re
import stat
from zipfile import ZipFile

from lxml import etree as ET
from .tools import ToolError

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def paragraphs(container):
    for child in container:
        if child.tag == '{' + W + '}p':
            yield child
        elif child.tag == '{' + W + '}tbl':
            for row in child.findall('{' + W + '}tr'):
                for cell in row.findall('{' + W + '}tc'):
                    yield from paragraphs(cell)


def chunks(paragraph):
    result = []
    for child in paragraph:
        if child.tag == '{' + W + '}r':
            for node in child:
                if node.tag == '{' + W + '}t':
                    result.append((node, node.text or '', True))
                elif node.tag in ('{' + W + '}tab', '{' + W + '}br', '{' + W + '}cr'):
                    result.append((node, '\t' if node.tag.endswith('}tab') else '\n', False))
        elif child.tag == '{' + W + '}hyperlink':
            result.extend((node, node.text or '', False) for node in child.iter('{' + W + '}t'))
    return result


def replace_inline(payload, old, new, expected_count=1):
    if (not isinstance(old, str) or not old or len(old) > 2000 or not isinstance(new, str) or len(new) > 10000
            or any(ord(char) < 32 for char in old + new)):
        raise ToolError('Use a non-empty old phrase and single-line replacement text without control characters.')
    if old == new:
        raise ToolError('Old and new text are identical; no revision was created.')
    if type(expected_count) is not int or not 1 <= expected_count <= 100:
        raise ToolError('expected_count must be between 1 and 100.')
    with ZipFile(BytesIO(payload)) as archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > 10_000_000:
            raise ToolError('Word document expands beyond the revision limit.')
        if any('vbaproject' in info.filename.lower() or info.flag_bits & 1
               or stat.S_ISLNK(info.external_attr >> 16) for info in infos):
            raise ToolError('Macro, encrypted or symbolic-link document entries are not supported for revision.')
        if len({info.filename for info in infos}) != len(infos):
            raise ToolError('Duplicate document parts are not supported for revision.')
        parts = {info.filename: archive.read(info.filename) for info in infos}
    try:
        tree = ET.fromstring(parts['word/document.xml'], ET.XMLParser(resolve_entities=False, no_network=True))
    except (KeyError, ET.XMLSyntaxError):
        raise ToolError('Invalid Word document XML.') from None
    if any(True for _ in tree.iter('{' + W + '}ins', '{' + W + '}del', '{' + W + '}moveFrom', '{' + W + '}moveTo')):
        raise ToolError('Tracked changes are not supported by this revision tool. The source was preserved.')
    body = tree.find('{' + W + '}body')
    if body is None:
        raise ToolError('The document has no supported body.')
    targets = []
    for paragraph in paragraphs(body):
        runs = chunks(paragraph)
        text = ''.join(text for _, text, _ in runs)
        matches = [(match.start(), match.end()) for match in re.finditer(re.escape(old), text)]
        if not matches:
            continue
        if any(True for _ in paragraph.iter('{' + W + '}fldChar', '{' + W + '}instrText')):
            raise ToolError('The matching paragraph contains a field. Nothing was written.')
        for start, end in matches:
            cursor = 0
            for _, text, writable in runs:
                if cursor < end and cursor + len(text) > start and not writable:
                    raise ToolError('The matching text crosses a hyperlink or non-text boundary. Nothing was written.')
                cursor += len(text)
        targets.append((paragraph, matches))
    count = sum(len(matches) for _, matches in targets)
    if count != expected_count:
        raise ToolError(f'Expected {expected_count} exact matches but found {count}. Nothing was written.')
    for paragraph, matches in targets:
        for start, end in reversed(matches):
            cursor = 0
            selected = []
            for node, text, writable in chunks(paragraph):
                if cursor < end and cursor + len(text) > start:
                    selected.append((node, text, cursor))
                cursor += len(text)
            first, last = selected[0], selected[-1]
            if first[0] is last[0]:
                first[0].text = first[1][:start - first[2]] + new + first[1][end - first[2]:]
                first[0].set(XML_SPACE, 'preserve')
            else:
                first[0].text = first[1][:start - first[2]] + new
                first[0].set(XML_SPACE, 'preserve')
                for node, _, _ in selected[1:-1]:
                    node.text = ''
                last[0].text = last[1][end - last[2]:]
                last[0].set(XML_SPACE, 'preserve')
    parts['word/document.xml'] = ET.tostring(tree, encoding='UTF-8', xml_declaration=True, standalone=True)
    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        for info in infos:
            archive.writestr(info, parts[info.filename])
    result = stream.getvalue()
    if len(result) > 4_000_000:
        raise ToolError('The revised document exceeds the supported file size. Nothing was written.')
    return result, count
