"""Keep tool conversations compact without suggesting that actions be replayed."""

KEEP_RESULTS = 3
RESULT_HEAD = 1200
RESULT_TAIL = 400


def earlier_result(name, text=''):
    notice = (f'[Earlier {name} result shortened. This call already ran; do not replay '
              'actions. Inspect current state with read-only tools if more evidence is needed.]')
    if not isinstance(text, str) or not text:
        return notice
    if len(text) <= RESULT_HEAD + RESULT_TAIL:
        return notice + '\n' + text
    return notice + '\n' + text[:RESULT_HEAD] + '\n[Middle omitted.]\n' + text[-RESULT_TAIL:]


def compact_compatible_messages(messages):
    """Keep recent results and the newest image, preserving call IDs and user text.

    Older results retain bounded excerpts so task IDs and receipts are not simply
    erased. Images are replaced with a stale-view notice; text receipts survive.
    Original user instructions and tool arguments are never truncated here.
    """
    names = {call['id']: call['function']['name']
             for message in messages for call in message.get('tool_calls', [])}
    results = [message for message in messages if message.get('role') == 'tool']
    for message in results[:-KEEP_RESULTS]:
        content = message.get('content')
        if isinstance(content, str) and not content.startswith('[Earlier '):
            message['content'] = earlier_result(names.get(message.get('tool_call_id'), 'tool'), content)

    views = [(message, block) for message in messages
             if isinstance(message.get('content'), list)
             for block in message['content'] if block.get('type') == 'image_url']
    for message, block in views[:-1]:
        message['content'] = [
            {'type': 'text', 'text': '[Earlier screen image omitted; it is not the current view.]'}
            if part is block else part for part in message['content']]


def fast_history(messages):
    """Keep six recent messages and the newest long source through follow-ups."""
    selected = set(range(max(0, len(messages) - 6), len(messages)))
    sources = [i for i, item in enumerate(messages[:-1])
               if item['role'] == 'user' and len(item['content']) > 8000]
    if sources:
        selected.add(sources[-1])
    result = [message for i, message in enumerate(messages) if i in selected]
    while result and result[0]['role'] != 'user':
        result.pop(0)
    return result
