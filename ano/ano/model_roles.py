"""Explicit local model roles, independent of particular aliases or model sizes."""
from dataclasses import dataclass
import re


def local_model_name(value):
    return (isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', value))
            and not any(word in value.lower() for word in ('cloud', '/', '@')))


@dataclass(frozen=True)
class ModelRoles:
    agent: str
    conversation: str
    writer: str
    vision: str
    warnings: tuple = ()


def select_model_roles(memory, base_model, installed):
    if not local_model_name(base_model):
        raise ValueError('Model roles require a downloaded local base model name.')
    available = set(installed)
    warnings = []

    def choose(key, fallback):
        value = memory.setting(key, '')
        if not value:
            return fallback
        if not local_model_name(value) or value not in available:
            warnings.append(f'{key} unavailable; using {fallback}.')
            return fallback
        return value

    agent = choose('local_agent_model', base_model)
    return ModelRoles(agent, choose('local_chat_model', agent),
                      choose('local_writer_model', agent),
                      choose('local_vision_model', base_model), tuple(warnings))
