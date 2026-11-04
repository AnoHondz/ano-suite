"""One-time, reversible migration to the user's free-first spending preference."""


def apply(memory, installed=()):
    if memory.setting('reliability_defaults_v1', False):
        return False
    from .paid import MODEL
    previous = {key: memory.setting(key, '') for key in
                ('brain', 'openai_model', 'local_chat_model', 'local_output_tokens')}
    # Preserve old preferences for review; never touch credentials or trade permissions.
    values = {'before_reliability_defaults_v1': previous, 'brain': 'local', 'openai_model': MODEL,
              'reliability_defaults_v1': True}
    if 'ano-chat:latest' in installed and not memory.setting('local_chat_model', ''):
        values['local_chat_model'] = 'ano-chat:latest'
    setter = getattr(memory, 'set_settings', None)
    if callable(setter):
        setter(values)
    else:
        for key, value in values.items():
            memory.set_setting(key, value)
    return True
