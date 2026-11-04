"""Read local job states and model names without reading command/chat payloads."""
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ano.paths import data_directory
from ano.local_runtime import ollama_json


def observe():
    root = data_directory()
    result = {'scope': 'Read-only snapshot. Persisted state alone is not proof a worker is currently live.'}
    path = root / 'jobs.sqlite3'
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            result['jobs_by_state'] = dict(db.execute('SELECT state,count(*) FROM work_jobs GROUP BY state'))
            result['recent_jobs'] = [dict(zip(('id', 'state', 'updated_at'), row)) for row in db.execute(
                'SELECT id,state,updated_at FROM work_jobs ORDER BY updated_at DESC LIMIT 5')]
            result['steps_by_state'] = dict(db.execute('SELECT state,count(*) FROM work_steps GROUP BY state'))
    memory = root / 'memory.sqlite3'
    if memory.exists():
        with sqlite3.connect(memory.as_uri() + '?mode=ro', uri=True) as db:
            keys = ('local_model', 'local_agent_model', 'local_chat_model', 'local_writer_model', 'local_vision_model', 'brain')
            result['saved_model_preferences'] = {key: json.loads(value) for key, value in db.execute(
                'SELECT key,value FROM settings WHERE key IN (?,?,?,?,?,?)', keys)}
    try:
        result['loaded_model_names'] = [m.get('name') for m in ollama_json('/api/ps').get('models', [])]
        result['loaded_capabilities'] = {}
        for name in result['loaded_model_names'][:3]:
            data = ollama_json('/api/show', {'model': name})
            result['loaded_capabilities'][name] = {'details': data.get('details'), 'thinking': data.get('thinking'),
                                                 'capabilities': data.get('capabilities')}
    except (OSError, ValueError):
        result['ollama_status'] = 'Could not read current status; not evidence that work stopped.'
    return result


if __name__ == '__main__':
    print(json.dumps(observe(), indent=2))
