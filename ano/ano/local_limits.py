"""Conservative local inference waits, independent of model action permissions.

These are bounded allowances, not response-time predictions. Cold CPU prompts
can take longer than two minutes before producing a first token. Actual replies
return immediately when ready; all existing cancellation checks remain active.
"""
import json
import math


def wait_allowance(arguments):
    chars = 0
    for message in arguments.get('messages', []):
        content = message.get('content', '')
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for item in content:
                if item.get('type') == 'text':
                    chars += len(item.get('text', ''))
                elif item.get('type') == 'image_url':
                    chars += 4000  # Approximate vision allowance; never count base64 bytes as text.
        if message.get('tool_calls'):
            chars += len(json.dumps(message['tool_calls'], ensure_ascii=False))
    chars += len(json.dumps(arguments.get('tools', []), ensure_ascii=False))
    try:
        output = min(16000, max(1, int(arguments.get('max_tokens', 512))))
    except (ValueError, TypeError):
        output = 512
    # Observed laptop cold prefill can be about 8 tokens/s. Output length is a
    # ceiling, not a prediction; reserve space without delaying short answers.
    seconds = 30 + chars / (3 * 8) + output / 2
    return min(900, max(180, math.ceil(seconds / 30) * 30))


def turn_allowance(arguments, conversation=False):
    single = wait_allowance(arguments)
    return single if conversation else min(1800, max(600, single * 3))
