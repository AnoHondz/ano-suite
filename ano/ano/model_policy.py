"""Ano's local-only inference policy. Paid/cloud brains are not supported."""
from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit

LOCAL_URL = 'http://localhost:11434/v1'


def flag(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def is_loopback_url(value):
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or url.username or url.password or url.query or url.fragment:
            return False
        _ = url.port
        host = url.hostname or ''
        return host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ModelPolicy:
    free_only: bool
    local_only: bool
    model: str
    url: str
    output_tokens: int
    profile: str = 'fast'

    @classmethod
    def load(cls, memory, environ):
        def value(env, setting, default):
            return environ[env] if env in environ else memory.setting(setting, default)
        # Legacy settings/environment flags cannot re-enable removed paid brains.
        local_only = free_only = True
        model = str(value('ANO_LOCAL_MODEL', 'local_model', 'ano-local:latest')).strip()
        url = str(value('ANO_LOCAL_URL', 'local_url', LOCAL_URL)).strip()
        try:
            tokens = int(value('ANO_LOCAL_OUTPUT_TOKENS', 'local_output_tokens', 512))
        except (ValueError, TypeError):
            tokens = 512
        tokens = max(256, min(tokens, 16000))
        if local_only:
            if not is_loopback_url(url):
                raise ValueError('Local-only inference requires a localhost or loopback model server.')
            if not model or 'cloud' in model.lower() or '/' in model or '@' in model:
                raise ValueError('Local-only inference requires a downloaded local model, not a cloud model.')
        profile = str(value('ANO_LOCAL_PROFILE', 'local_profile', 'fast')).strip().lower()
        return cls(free_only, local_only, model, url, tokens, profile if profile in ('fast', 'compact', 'full') else 'fast')

    @property
    def label(self):
        location = 'Local' if is_loopback_url(self.url) else 'Remote'
        return f'{location} ({self.model})'
