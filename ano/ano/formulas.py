"""Bounded evaluation of supplied single-sheet numeric Excel formulas.

No eval, external workbooks, macros, network, names, or arbitrary functions.
The result cache is suitable for initial XLSX display; Excel can recalculate it.
"""
import ast
from decimal import Decimal, ROUND_HALF_UP
import math
import operator
import re

from .tools import ToolError

CELL = re.compile(r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,5})\Z')
RANGES = re.compile(r'(\$?[A-Z]{1,3}\$?[1-9][0-9]{0,5})\s*:\s*(\$?[A-Z]{1,3}\$?[1-9][0-9]{0,5})')
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow}


def coordinates(address, width, height):
    match = CELL.fullmatch(address.upper()) if isinstance(address, str) else None
    if not match:
        raise ToolError('Use ordinary single-sheet cell addresses such as B2.')
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - 64
    row = int(match[2])
    if not 1 <= column <= width or not 2 <= row <= height + 1:
        raise ToolError('Formula cells and references must lie in the supplied data rows, below the headers.')
    return row, column


def calculate_formulas(headers, rows, formulas, check=lambda: None):
    if not isinstance(formulas, dict) or len(formulas) > 200:
        raise ToolError('Supply up to 200 explicit formula cells.')
    width, height = len(headers), len(rows)
    expressions, originals = {}, {}
    for address, expression in formulas.items():
        position = coordinates(address, width, height)
        if position in expressions:
            raise ToolError('Formula addresses must be unique, ignoring letter case and absolute markers.')
        if not isinstance(expression, str) or not expression.startswith('=') or not 2 <= len(expression) <= 200:
            raise ToolError('Each explicit formula must begin with = and contain at most 200 characters.')
        text = expression[1:].upper()
        if (re.search(r'[^A-Z0-9.$+*/^(),:\s-]', text) or '**' in text
                or re.search(r'\b0[XOB]', text) or re.search(r'\bRANGE\s*\(', text)):
            raise ToolError('This formula uses unsupported syntax or an external reference.')
        text = RANGES.sub(r'RANGE(\1,\2)', text).replace('$', '').replace('^', '**')
        try:
            tree = ast.parse(text.strip(), mode='eval')
        except SyntaxError:
            raise ToolError('An explicit spreadsheet formula is malformed.') from None
        if len(list(ast.walk(tree))) > 100:
            raise ToolError('A formula exceeds the expression limit.')
        expressions[position], originals[position] = tree.body, expression
    cache, visiting = {}, set()
    work = [0]

    def tick():
        work[0] += 1
        if work[0] > 100000:
            raise ToolError('Formula calculation exceeds its work limit. Split the workbook.')
        if work[0] % 500 == 0:
            check()

    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
            raise ToolError('A formula needs finite numeric inputs and results within the supported range.')
        return value

    def cell(position, range_item=False):
        tick()
        if position in expressions:
            if position in cache:
                return cache[position]
            if position in visiting or len(visiting) >= 100:
                raise ToolError('Formula references contain a cycle or an excessively deep dependency chain.')
            visiting.add(position)
            try:
                value = number(evaluate(expressions[position]))
            finally:
                visiting.remove(position)
            cache[position] = value
            return value
        value = rows[position[0] - 2][position[1] - 1]
        if range_item and type(value) not in (int, float):
            return None
        return 0 if value is None else number(value)

    def evaluate(node):
        tick()
        if isinstance(node, ast.Constant):
            return number(node.value)
        if isinstance(node, ast.Name):
            return cell(coordinates(node.id, width, height))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            if isinstance(node.operand, ast.BinOp) and isinstance(node.operand.op, ast.Pow):
                raise ToolError('For powers, parenthesize a negative base or multiply the power by -1 explicitly.')
            return number(evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1))
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            left, right = number(evaluate(node.left)), number(evaluate(node.right))
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Pow):
                raise ToolError('Chained powers need explicit left grouping or intermediate cells.')
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ToolError('Formula powers are limited to exponents between -12 and 12.')
            return number(OPS[type(node.op)](left, right))
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.keywords:
            raise ToolError('Unsupported spreadsheet formula expression.')
        name = node.func.id
        if name == 'RANGE':
            if len(node.args) != 2 or any(not isinstance(x, ast.Name) for x in node.args):
                raise ToolError('Ranges need two cell addresses.')
            start, end = (coordinates(x.id, width, height) for x in node.args)
            if start[0] > end[0] or start[1] > end[1]:
                raise ToolError('Write ranges from the upper-left to the lower-right cell.')
            return [cell((row, col), range_item=True) for row in range(start[0], end[0] + 1)
                    for col in range(start[1], end[1] + 1)]
        if name not in ('SUM', 'AVERAGE', 'MIN', 'MAX', 'COUNT', 'ABS', 'ROUND'):
            raise ToolError('Supported formula functions: SUM, AVERAGE, MIN, MAX, COUNT, ABS, ROUND.')
        if not node.args:
            raise ToolError('Formula functions require at least one argument.')
        values = []
        for arg in node.args:
            value = evaluate(arg)
            values.extend(value if isinstance(value, list) else [value])
        values = [number(v) for v in values if v is not None]
        if name == 'SUM':
            return number(sum(values))
        if name == 'COUNT':
            return len(values)
        if name == 'AVERAGE' and values:
            return number(sum(values) / len(values))
        if name in ('MIN', 'MAX'):
            return (min if name == 'MIN' else max)(values) if values else 0
        if name == 'ABS' and len(values) == 1:
            return abs(values[0])
        if name == 'ROUND' and len(values) == 2 and type(values[1]) is int and -10 <= values[1] <= 10:
            return number(float(Decimal(str(values[0])).quantize(Decimal(1).scaleb(-values[1]), rounding=ROUND_HALF_UP)))
        raise ToolError('Formula function has missing or unsupported arguments.')

    check()
    try:
        for position in expressions:
            cell(position)
    except ToolError:
        raise
    except (ArithmeticError, ValueError, TypeError, RecursionError):
        raise ToolError('Formula calculation failed. Check division by zero, numeric values and function arguments.') from None
    check()
    return {position: {'formula': originals[position], 'value': value} for position, value in cache.items()}


def cache_values(payload, results):
    """Write calculated numeric caches into a newly generated workbook only."""
    from io import BytesIO
    from zipfile import ZipFile, ZIP_DEFLATED
    from lxml import etree as ET
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with ZipFile(BytesIO(payload)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    tree = ET.fromstring(parts['xl/worksheets/sheet1.xml'], ET.XMLParser(resolve_entities=False, no_network=True))
    for cell in tree.findall('.//{' + namespace + '}c'):
        match = CELL.fullmatch(cell.get('r', ''))
        if not match:
            continue
        column = 0
        for letter in match[1]:
            column = column * 26 + ord(letter) - 64
        result = results.get((int(match[2]), column))
        if result is None:
            continue
        if cell.find('{' + namespace + '}f') is None:
            raise ToolError('A calculated formula is missing from the workbook.')
        value = cell.find('{' + namespace + '}v')
        if value is None:
            value = ET.SubElement(cell, '{' + namespace + '}v')
        value.text = str(result['value'])
        cell.set('t', 'n')
    parts['xl/worksheets/sheet1.xml'] = ET.tostring(tree, encoding='utf-8')
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return stream.getvalue()
