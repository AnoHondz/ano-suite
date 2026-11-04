"""Fill the bundled editable PowerPoint template without a Node runtime.

Only supplied strings enter text nodes. No macros, links, code or external media
are added. Every slide's texts are checked before returning the serialized deck.
"""
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as ET
from .tools import ToolError

NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


def _xml(payload):
    return ET.fromstring(payload, ET.XMLParser(resolve_entities=False, no_network=True))


def _bytes(node):
    return ET.tostring(node, encoding='UTF-8', xml_declaration=True, standalone=True)


def _text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(
            ord(c) < 32 for c in value):
        raise ToolError(f'{label} must be non-empty text, under {limit} characters, without control characters.')
    return value


def slide_texts(payload):
    with ZipFile(BytesIO(payload)) as archive:
        presentation = _xml(archive.read('ppt/presentation.xml'))
        rels = _xml(archive.read('ppt/_rels/presentation.xml.rels'))
        targets = {r.get('Id'): r.get('Target') for r in rels}
        result = []
        for item in presentation.find('p:sldIdLst', NS):
            target = targets[item.get('{' + NS['r'] + '}id')]
            node = _xml(archive.read('ppt/' + target))
            result.append([t.text or '' for t in node.findall('.//a:t', NS)])
        return result


def build_deck(title, slides, subtitle='Supplied content'):
    title = _text(title, 70, 'Deck title')
    subtitle = _text(subtitle, 180, 'Subtitle')
    if not isinstance(slides, list) or not 1 <= len(slides) <= 20:
        raise ToolError('Supply between 1 and 20 content slides.')
    for slide in slides:
        if not isinstance(slide, dict) or set(slide) != {'title', 'bullets'}:
            raise ToolError('Each slide needs exactly title and bullets.')
        _text(slide['title'], 65, 'Slide title')
        if not isinstance(slide['bullets'], list) or not 1 <= len(slide['bullets']) <= 4:
            raise ToolError('Each slide needs 1–4 short content points.')
        for point in slide['bullets']:
            _text(point, 120, 'Content point')
    template = Path(__file__).parent / 'assets' / 'supplied-deck.pptx'
    with ZipFile(template) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    presentation = _xml(parts['ppt/presentation.xml'])
    rels = _xml(parts['ppt/_rels/presentation.xml.rels'])
    types = _xml(parts['[Content_Types].xml'])
    ids = presentation.find('p:sldIdLst', NS)
    for child in list(ids):
        ids.remove(child)
    for child in list(rels):
        if child.get('Type', '').endswith('/slide'):
            rels.remove(child)
    for child in list(types):
        if re.fullmatch(r'/ppt/(slides/slide\d+\.xml|notesSlides/notesSlide\d+\.xml)', child.get('PartName', '')):
            types.remove(child)
    originals = dict(parts)
    for name in list(parts):
        if re.match(r'ppt/(slides/|notesSlides/)', name):
            del parts[name]
    for index, spec in enumerate([None] + slides):
        number = index + 1
        variant = 1 if index == 0 else 2 + (index - 1) % 2
        tree = _xml(originals[f'ppt/slides/slide{variant}.xml'])
        mapping = {'{{DECK_TITLE}}': title, '{{DECK_SUBTITLE}}': subtitle}
        if spec is not None:
            mapping['{{TITLE}}'] = spec['title']
            mapping.update({'{{ITEM%d}}' % (i + 1): spec['bullets'][i] if i < len(spec['bullets']) else '' for i in range(4)})
            for shape in tree.findall('.//p:sp', NS):
                prop = shape.find('p:nvSpPr/p:cNvPr', NS)
                match = re.fullmatch(r'(?:StepCircle|StepNumber|ContentCard|Item)(\d)', prop.get('name', '') if prop is not None else '')
                if match and int(match[1]) >= len(spec['bullets']):
                    shape.getparent().remove(shape)
        for item in tree.findall('.//a:t', NS):
            if item.text in mapping:
                item.text = mapping[item.text]
        parts[f'ppt/slides/slide{number}.xml'] = _bytes(tree)
        slide_rels = _xml(originals[f'ppt/slides/_rels/slide{variant}.xml.rels'])
        for relationship in list(slide_rels):
            if relationship.get('Type', '').endswith('/notesSlide'):
                slide_rels.remove(relationship)
        parts[f'ppt/slides/_rels/slide{number}.xml.rels'] = _bytes(slide_rels)
        rid = 'anoSlide' + str(number)
        ET.SubElement(rels, '{' + REL + '}Relationship', Id=rid,
            Type=NS['r'] + '/slide', Target=f'slides/slide{number}.xml')
        item = ET.SubElement(ids, '{' + NS['p'] + '}sldId', id=str(255 + number))
        item.set('{' + NS['r'] + '}id', rid)
        ET.SubElement(types, '{' + CT + '}Override', PartName=f'/ppt/slides/slide{number}.xml',
                      ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
    # Keep template sections, assigning cloned slides to their proper section.
    sections = presentation.xpath('//*[local-name()="section"]')
    for index, section in enumerate(sections):
        listing = next((x for x in section if ET.QName(x).localname == 'sldIdLst'), None)
        if listing is not None:
            tag = listing[0].tag if len(listing) else '{http://schemas.microsoft.com/office/powerpoint/2010/main}sldId'
            for child in list(listing):
                listing.remove(child)
            for number in ([256] if index == 0 else range(257, 257 + len(slides))):
                ET.SubElement(listing, tag, id=str(number))
    parts['ppt/presentation.xml'] = _bytes(presentation)
    parts['ppt/_rels/presentation.xml.rels'] = _bytes(rels)
    parts['[Content_Types].xml'] = _bytes(types)
    if 'docProps/app.xml' in parts:
        app = _xml(parts['docProps/app.xml'])
        for count in app.xpath('//*[local-name()="Slides"]'):
            count.text = str(len(slides) + 1)
        parts['docProps/app.xml'] = _bytes(app)
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    payload = stream.getvalue()
    actual = slide_texts(payload)
    expected = [[title, subtitle]] + [[s['title']] + s['bullets'] for s in slides]
    for actual_slide, expected_slide in zip(actual, expected, strict=True):
        if any(text not in actual_slide for text in expected_slide) or any('{{' in text for text in actual_slide):
            raise ToolError('PowerPoint text verification failed. Nothing was saved.')
    return payload
