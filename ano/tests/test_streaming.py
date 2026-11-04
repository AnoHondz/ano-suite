from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from ano.claude import ClaudeError
from ano.streaming import collect_stream, PartialText
from ano.paid import GuardedAPI, SpendLedger, MODEL


def chunk(text=None, calls=None, finish=None, usage=None):
    return NS(choices=[NS(index=0, delta=NS(content=text, tool_calls=calls), finish_reason=finish)], usage=usage)


class StreamingTests(unittest.TestCase):
    def test_real_sdk_chunks_through_paid_client_dispatch_only_completed_tool(self):
        from openai.types.chat import ChatCompletionChunk
        from ano.gpt import GPTClient
        def sdk(delta, finish=None, usage=None):
            return ChatCompletionChunk(id='fixture', created=0, model=MODEL, object='chat.completion.chunk',
                choices=[dict(index=0,delta=delta,finish_reason=finish)] if delta is not None else [], usage=usage)
        toolbox, client = Mock(), Mock()
        toolbox.allowed.return_value = True
        toolbox.folders.return_value = []
        toolbox.definitions.return_value = [{'name':'read_file','description':'Read a file',
                                            'input_schema':{'type':'object','properties':{'path':{'type':'string'}}}}]
        toolbox.run.return_value = ('Exact fixture content', '', False)
        def tool_stream():
            yield sdk({'tool_calls':[{'index':0,'id':'call1','type':'function','function':{'name':'read_file','arguments':'{"path":'}}]})
            toolbox.run.assert_not_called()
            yield sdk({'tool_calls':[{'index':0,'function':{'arguments':'"fixture.txt"}'}}]}, 'tool_calls')
            toolbox.run.assert_not_called()
        client.chat.completions.create.side_effect = [tool_stream(), iter([sdk({'content':'Final exact answer'},'stop')])]
        with TemporaryDirectory() as folder:
            guard = GuardedAPI(client, SpendLedger(Path(folder)/'spend.db'), partial=lambda _: None)
            brain = GPTClient('unused', toolbox, model=MODEL, profile='paid', connect=lambda:guard)
            self.assertEqual(brain.reply([{'role':'user','body':'Read the file fixture.txt'}]), 'Final exact answer')
        toolbox.run.assert_called_once_with('read_file', {'path':'fixture.txt'})
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertFalse(client.chat.completions.create.call_args.kwargs['parallel_tool_calls'])

    def test_content_and_tool_fragments_assemble_before_any_dispatch(self):
        fragments = [chunk('Checking ', [NS(index=0,id='call1',function=NS(name='read_file',arguments='{"pa'))]),
                     chunk('file', [NS(index=0,id=None,function=NS(name=None,arguments='th":"fixture.txt"}'))], 'tool_calls')]
        partials = []
        result = collect_stream(iter(fragments), partials.append)
        self.assertEqual(partials, ['Checking ', 'Checking file'])
        message = result.choices[0].message
        self.assertEqual(message.content, 'Checking file')
        self.assertEqual(message.tool_calls[0].function.arguments, '{"path":"fixture.txt"}')
        self.assertEqual(message.tool_calls[0].id, 'call1')

    def test_no_completion_marker_is_failure_not_partial_success(self):
        stream = Mock()
        stream.__iter__ = Mock(return_value=iter([chunk('Incomplete')]))
        with self.assertRaises(ClaudeError):
            collect_stream(stream, lambda _: None)
        stream.close.assert_called_once()
        with self.assertRaises(ClaudeError):
            collect_stream(iter([chunk(calls=[NS(index=0,id='x',function=NS(name='open_app',arguments='{}'))],
                                      finish='length')]), lambda _: None)

    def test_callback_is_throttled_at_ten_updates_per_second(self):
        now, seen = [0.0], []
        callback = PartialText(seen.append, clock=lambda: now[0])
        for i in range(100):
            now[0] = i * .01
            callback(str(i))
        self.assertLessEqual(len(seen), 10)
        self.assertGreaterEqual(len(seen), 9)

    def test_stream_usage_settles_real_cost_and_missing_usage_keeps_reservation(self):
        with TemporaryDirectory() as folder:
            ledger, client = SpendLedger(Path(folder)/'spend.db'), Mock()
            usage = NS(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=NS(cached_tokens=0))
            client.chat.completions.create.return_value = iter([chunk('Done', finish='stop'), NS(choices=[], usage=usage)])
            guard = GuardedAPI(client, ledger, partial=lambda _: None)
            result = guard.create(model=MODEL, messages=[])
            self.assertEqual(result.choices[0].message.content, 'Done')
            self.assertTrue(client.chat.completions.create.call_args.kwargs['stream'])
            self.assertAlmostEqual(guard.spent, .000013)  # Nano: 100 input + 20 output tokens.
            client.chat.completions.create.return_value = iter([chunk('Done', finish='stop')])
            unmetered = GuardedAPI(client, ledger, partial=lambda _: None)
            unmetered.create(model=MODEL, messages=[])
            self.assertGreater(unmetered.spent, .00036)

    def test_cancelled_stream_closes_without_final_answer(self):
        stream = Mock()
        stream.__iter__ = Mock(return_value=iter([chunk('Should not display', finish='stop')]))
        seen = []
        with self.assertRaisesRegex(ClaudeError, 'cancelled'):
            collect_stream(stream, seen.append, permitted=lambda: False)
        self.assertEqual(seen, [])
        stream.close.assert_called_once()
