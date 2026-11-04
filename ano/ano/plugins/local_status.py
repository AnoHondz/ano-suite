"""Inspect the local inference server without generating text or calling paid APIs."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing

import psutil
from ..local_runtime import ollama_json


class LocalStatus:
    def __init__(self, data_directory, query=ollama_json):
        self.data_directory = Path(data_directory)
        self.query = query

    def definitions(self):
        return [{'name': 'local_ai_status', 'description':
                 'Check RAM and automatic idle-model cleanup status, local Ollama reachability, loaded models and preferences. No inference or paid API request.',
                 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]

    def run(self, name, arguments):
        if name != 'local_ai_status' or arguments:
            raise ValueError('Invalid local AI status request.')
        ram = psutil.virtual_memory()
        lines = [f'RAM available: {ram.available / 2**30:.1f} GB of {ram.total / 2**30:.1f} GB.']
        try:
            installed = self.query('/api/tags').get('models', [])
            loaded = self.query('/api/ps').get('models', [])
            lines += ['Ollama: reachable on this PC.',
                      'Installed models: ' + (', '.join(str(item.get('name', '?')) for item in installed[:20]) or 'none'),
                      'Loaded models: ' + (', '.join(str(item.get('name', '?')) for item in loaded[:20]) or 'none')]
            for item in loaded[:5]:
                if isinstance(item.get('size'), (int, float)):
                    lines.append(f"  {item.get('name', '?')}: server reports {item['size'] / 2**30:.2f} GB loaded.")
        except (OSError, ValueError) as error:
            lines.append('Ollama: local server could not be verified. Start Ollama and check again.')
        path = self.data_directory / 'memory.sqlite3'
        if path.exists():
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                    for key, value in db.execute('''SELECT key,value FROM settings WHERE key IN
                        ('local_model','local_agent_model','local_chat_model','local_writer_model',
                         'local_vision_model','local_profile') ORDER BY key'''):
                        lines.append(f'Saved {key}: {json.loads(value)}')
                lines.append('Saved preferences are not a trace of which model handled a past message.')
            except (sqlite3.Error, ValueError):
                lines.append('Saved model preferences could not be read.')
        try:
            health=json.loads((self.data_directory/'memory-manager.health.json').read_text(encoding='utf-8'))
            lines.append('Automatic RAM manager: '+str(health.get('state','unknown'))+'.')
            if health.get('last_unloaded'):
                lines.append('Last released idle models: '+', '.join(health['last_unloaded']))
            lines.append('Idle large models release after 2 minutes; chat after 5 minutes; pressure cleanup after 30 idle seconds.')
        except (OSError,ValueError,TypeError):
            lines.append('Automatic RAM manager has not reported its status yet.')
        lines.append('No model generation or paid API request was made.')
        return '\n'.join(lines), 'Checked local AI status'
