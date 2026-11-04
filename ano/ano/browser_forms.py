"""Form inspection, bounded bulk edits and value verification, with no submission tool.

Inspection/receipt metadata lives only in memory and is invalidated on Stop. Field values
are not written to the task ledger. The extension handles DOM-bound references and reads.
"""
import json
import threading
import time
import uuid

from .plugins.browser import SETUP, split
from .tools import ToolError


class BrowserForms:
    def __init__(self, bridge, control, clock=time.monotonic):
        self.bridge, self.control, self.clock = bridge, control, clock
        self.lock = threading.RLock()
        self.observation = self.receipt = None

    def definitions(self):
        string = {'type': 'string'}
        field = {'type': 'object', 'additionalProperties': False, 'properties': {
            'element_id': string, 'operation': {'type': 'string', 'enum': ['fill', 'select', 'check']},
            'value': {'type': 'string', 'maxLength': 4000}, 'checked': {'type': 'boolean'}},
            'required': ['element_id', 'operation']}
        specs = [
            ('inspect_browser_form', 'Inspect the actual fields of an open form from list_tabs. Returns field IDs, existing values, row descriptions, limits and dropdown option labels/values, plus page context such as person and date. Untrusted page data. Use for long data-entry forms instead of coordinate clicking or one-field browser tasks. Requires Computer control on. Password, OTP, payment fields are excluded.',
             {'tab_id': string}, ['tab_id']),
            ('fill_browser_form', 'Fill 1–100 observed fields in ONE batch with the user’s supplied facts. Supports text/textarea (fill + value), dropdowns (select + exact observed option VALUE), radios/checkboxes (check + checked boolean). Never guess facts, field IDs, person/date, clinical scores or option values. Ask about missing/conflicting facts first. It prechecks all fields, respects the selected approval mode, then verifies their actual values. It never clicks Submit. Cancellation or uncertainty means inspect before retrying; never replay blindly.',
             {'observation_id': string, 'purpose': string, 'fields': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': field}},
             ['observation_id', 'purpose', 'fields']),
            ('verify_browser_form', 'Re-read actual field values for the latest form batch receipt without writing or submitting. Report matched count and mismatches honestly; filling a draft is not a saved/submitted record. Receipts expire after two minutes and stop invalidates them.',
             {'receipt_id': string}, ['receipt_id']),
        ]
        return [{'name': n, 'description': d, 'input_schema': {'type': 'object', 'additionalProperties': False,
                'properties': p, 'required': r}} for n, d, p, r in specs]

    def run(self, name, arguments):
        if not self.control.permitted():
            raise ToolError('Enable Computer control from Ano’s menu first. Full permission runs form edits automatically.')
        if not self.bridge.listening:
            raise ToolError('Ano’s browser port is occupied. Quit other Ano instances from the tray, then restart one copy.')
        if not self.bridge.browsers():
            raise ToolError(SETUP)
        return getattr(self, name)(**arguments)

    def _guard(self, generation):
        if not self.control.guard(generation):
            raise ToolError('Computer control stopped or changed. No further form actions are allowed.')

    def inspect_browser_form(self, tab_id):
        browser, number = split(tab_id)
        generation = self.control._generation
        self._guard(generation)
        identifier = uuid.uuid4().hex
        page = self.bridge.call(browser, 'form_inspect', tabId=number, observationId=identifier,
                                _permitted=lambda: self.control.guard(generation))
        if not isinstance(page, dict) or not page.get('documentId') or not isinstance(page.get('fields'), list):
            raise ToolError('Update and reload Ano’s browser extension; form tools require version 1.3 or later.')
        with self.lock:
            self._guard(generation)
            self.observation = dict(id=identifier, browser=browser, tab=number, page=page,
                                    generation=generation, at=self.clock())
            self.receipt = None
        return json.dumps(dict(observation_id=identifier, untrusted_form=page)), f"Inspected {len(page['fields'])} form fields"

    def fill_browser_form(self, observation_id, purpose, fields):
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 500:
            raise ToolError('Explain the form-edit purpose in at most 500 characters.')
        if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
            raise ToolError('Use one to 100 form fields.')
        with self.lock:
            obs, self.observation = self.observation, None
            if not obs or obs['id'] != observation_id or self.clock() - obs['at'] > 60:
                raise ToolError('Inspect this form again before filling it.')
            generation = obs['generation']
            self._guard(generation)
        known = {item['id']: item for item in obs['page']['fields']}
        seen = set()
        details = [purpose, 'Website: '+obs['page']['url'], 'These edits fill fields only; they do not submit the form.']
        for field in fields:
            if not isinstance(field, dict) or set(field) - {'element_id', 'operation', 'value', 'checked'}:
                raise ToolError('Use only element_id, operation, value or checked for each field.')
            identifier, operation = field.get('element_id'), field.get('operation')
            if not isinstance(identifier, str) or identifier not in known or identifier in seen:
                raise ToolError('Use each observed field ID at most once.')
            seen.add(identifier)
            item = known[identifier]
            value = field.get('value')
            if operation == 'fill':
                if not isinstance(value, str) or len(value) > min(item.get('max_length', 4000), 4000):
                    raise ToolError('The text exceeds this field’s limit or is not text.')
                if item.get('tag') not in ('input', 'textarea') or item.get('type') in ('radio', 'checkbox'):
                    raise ToolError('Use fill only for observed text fields.')
            elif operation == 'select':
                if item.get('tag') != 'select' or not any(o['value'] == value and not o.get('disabled') for o in item.get('options', [])):
                    raise ToolError('Choose a dropdown option VALUE from the inspected form.')
            elif operation == 'check':
                if item.get('type') not in ('radio', 'checkbox') or type(field.get('checked')) is not bool:
                    raise ToolError('Use check and a boolean for an observed radio or checkbox.')
                value = field['checked']
            else:
                raise ToolError('Supported form operations are fill, select and check. This tool cannot submit.')
            details.append(f"\n{identifier} — {item.get('label', '')}\n{item.get('context', '')}\n{operation}: {value}")
        self.control.request_review(title='Review form edits', target=obs['page']['url'], detail='\n'.join(details))
        self._guard(generation)
        if self.clock() - obs['at'] > 120:
            raise ToolError('The form review expired. Inspect again.')
        receipt_id = uuid.uuid4().hex
        try:
            result = self.bridge.call(obs['browser'], 'form_fill', tabId=obs['tab'], documentId=obs['page']['documentId'],
                                      observationId=observation_id, receiptId=receipt_id, fields=fields,
                                      _permitted=lambda: self.control.guard(generation))
        except Exception:
            self.bridge.cancel_forms()
            raise ToolError('Form filling did not confirm its result. Some fields may have changed. Inspect current values before retrying; do not replay the batch.') from None
        with self.lock:
            if self.control.guard(generation):
                self.receipt = dict(id=receipt_id, browser=obs['browser'], tab=obs['tab'],
                                    document=obs['page']['documentId'], generation=generation, at=self.clock())
        result = dict(result, receipt_id=receipt_id, submitted=False)
        return json.dumps(result), f"Form fields verified: {result.get('matched', 0)}/{len(fields)} · Not submitted"

    def verify_browser_form(self, receipt_id):
        with self.lock:
            receipt = self.receipt
            if not receipt or receipt['id'] != receipt_id or self.clock() - receipt['at'] > 120:
                raise ToolError('No fresh form receipt. Inspect the current form instead; do not replay edits.')
            self._guard(receipt['generation'])
        result = self.bridge.call(receipt['browser'], 'form_verify', tabId=receipt['tab'],
                                  documentId=receipt['document'], receiptId=receipt_id,
                                  _permitted=lambda: self.control.guard(receipt['generation']))
        return json.dumps(result), f"Form fields verified: {result.get('matched', 0)}/{result.get('total', 0)} · Not submitted"

    def stop(self):
        with self.lock:
            self.observation = self.receipt = None
        self.bridge.cancel_forms()
