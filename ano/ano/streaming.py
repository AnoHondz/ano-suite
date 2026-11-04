"""Collect streamed completions before exposing any tool calls for dispatch."""
import time
from types import SimpleNamespace as NS
from .claude import ClaudeError


class PartialText:
    def __init__(self, callback, clock=time.monotonic):
        self.callback, self.clock = callback, clock
        self.last_at = float('-inf')

    def __call__(self, text):
        now = self.clock()
        if now - self.last_at >= .1:
            self.last_at = now
            self.callback(text)


def collect_stream(stream, partial, permitted=None):
    text, calls, usage, finish = '', {}, None, None
    try:
        for chunk in stream:
            if permitted is not None and permitted() is False:
                raise ClaudeError('Streaming stopped because control was paused or cancelled. Inspect prior actions before retrying.')
            if getattr(chunk, 'usage', None) is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                if choice.index != 0:
                    continue
                delta = choice.delta
                content = getattr(delta, 'content', None)
                if isinstance(content, str):
                    text += content
                    if len(text) > 65536:
                        raise ClaudeError('Streamed reply exceeded the local display limit.')
                    partial(text)
                for item in getattr(delta, 'tool_calls', None) or []:
                    if type(item.index) is not int or not 0 <= item.index < 32:
                        raise ClaudeError('Invalid streamed tool-call index. No partial tool was dispatched.')
                    call = calls.setdefault(item.index, {'id': '', 'name': '', 'arguments': ''})
                    call['id'] += getattr(item, 'id', None) or ''
                    function = getattr(item, 'function', None)
                    if function:
                        call['name'] += getattr(function, 'name', None) or ''
                        call['arguments'] += getattr(function, 'arguments', None) or ''
                    if max(map(len, call.values())) > 65536:
                        raise ClaudeError('Streamed tool call exceeded its limit. No partial tool was dispatched.')
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
        if finish is None or (calls and finish != 'tool_calls') or any(not call['id'] or not call['name'] for call in calls.values()):
            raise ClaudeError('The streamed response ended before completion. Inspect prior actions before retrying.')
        assembled = [NS(id=call['id'], type='function', function=NS(name=call['name'], arguments=call['arguments']))
                     for _, call in sorted(calls.items())]
        return NS(usage=usage, choices=[NS(finish_reason=finish,
                  message=NS(content=text, tool_calls=assembled or None))])
    finally:
        close = getattr(stream, 'close', None)
        if callable(close):
            close()
