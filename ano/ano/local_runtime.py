"""Check Ollama and enable local-only inference without making a generation request."""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

from .memory import Memory
from .model_policy import ModelPolicy, is_loopback_url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ollama_json(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request('http://127.0.0.1:11434' + path, data=data,
                                     headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read(2_000_000))


def installed_models():
    return [item['name'] for item in ollama_json('/api/tags').get('models', [])]


def local_writer_config(model, url):
    """Keep writing local even when the main chat uses a remote provider."""
    if model and is_loopback_url(url) and 'cloud' not in model.lower() and '/' not in model and '@' not in model:
        return model, url
    try:
        if 'ano-local:latest' in installed_models():
            return 'ano-local:latest', 'http://127.0.0.1:11434/v1'
    except (OSError, ValueError):
        pass
    return None


def configure_local(model, memory):
    # Validate before changing settings, and require an actually installed model.
    policy = ModelPolicy.load(memory, {'ANO_LOCAL_ONLY': '1', 'ANO_LOCAL_MODEL': model,
                                      'ANO_LOCAL_URL': 'http://127.0.0.1:11434/v1'})
    models = installed_models()
    if model not in models:
        raise ValueError(f'{model} is not installed. Download it with: ollama pull {model}')
    info = ollama_json('/api/show', {'model': model})
    capabilities = info.get('capabilities', [])
    if 'tools' not in capabilities:
        raise ValueError('This model does not report tool support. Choose a tool-capable local model.')
    with memory.db:
        for key, value in {'local_model': model, 'local_url': policy.url,
                           'local_only': True, 'free_only': True, 'brain': 'local',
                           'local_output_tokens': 512, 'local_profile': 'fast',
                             'local_agent_model': '', 'local_chat_model': '',
                             'local_writer_model': '', 'local_vision_model': ''}.items():
            memory.set_setting(key, value)
    return capabilities


def data_path():
    from .paths import data_directory
    return data_directory()


def main():
    try:
        models = installed_models()
    except (OSError, ValueError, urllib.error.URLError) as error:
        print('Ollama is not reachable at 127.0.0.1:11434. No paid AI request was made.')
        print('Install/start Ollama from https://ollama.com/download/windows, then download a model.')
        return 1
    print('Local Ollama is reachable. No generation or paid AI request was made.')
    if not models:
        print('No models installed. Suggested first text/tool model: ollama pull qwen3:4b')
    for model in models:
        info = ollama_json('/api/show', {'model': model})
        print(f"  {model}: {', '.join(info.get('capabilities', [])) or 'capabilities unknown'}")
    print('A text-only model cannot inspect screenshots. Desktop vision requires a vision-capable model.')
    print('Enable a downloaded model with: python -m ano.configure local')
    return 0


if __name__ == '__main__':
    sys.exit(main())
