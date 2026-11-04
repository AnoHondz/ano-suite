from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import gzip
import zlib

from ano.brain import AnoBrain
from ano.plugins.web import Web, fetch_public, public_target, decode_transfer
from ano.tools import ToolError, Toolbox


RSS = '''<rss><channel><item><title>Python docs</title>
<link>https://docs.python.org/3/</link><description>Official documentation</description>
</item></channel></rss>'''


def answer(text='', calls=None):
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls), finish_reason='stop')])


def call(identifier, name, arguments):
    return NS(id=identifier, function=NS(name=name, arguments=arguments))


class WebTests(unittest.TestCase):
    def test_compressed_pages_decode_to_text_with_size_limits(self):
        body = b'<p>Readable page text</p>'
        self.assertEqual(decode_transfer(gzip.compress(body), 'gzip'), body)
        self.assertEqual(decode_transfer(zlib.compress(body), 'deflate'), body)
        with patch('ano.plugins.web.MAX_BYTES', 30):
            for encoding, data in [('gzip', gzip.compress(b'x' * 1000)), ('deflate', zlib.compress(b'x' * 1000))]:
                with self.subTest(encoding=encoding), self.assertRaises(ToolError):
                    decode_transfer(data, encoding)
        for encoding, data in [('br', b'unknown'), ('gzip', b'invalid'), ('deflate', b'invalid')]:
            with self.subTest(encoding=encoding), self.assertRaises(ToolError):
                decode_transfer(data, encoding)

    def test_search_encodes_query_and_returns_sources(self):
        fetch = Mock(return_value=('https://www.bing.com/search', 'application/rss+xml', RSS))
        text, note = Web(fetch).run('search_public_web', {'query': 'Python & docs'})
        self.assertIn('q=Python%20%26%20docs', fetch.call_args.args[0])
        self.assertIn('https://docs.python.org/3/', text)
        self.assertIn('Untrusted', text)
        self.assertIn('Searched', note)

    def test_search_prefers_the_full_query_engine_and_falls_back_to_bing(self):
        fetch = Mock(return_value=('https://www.bing.com/search', 'application/rss+xml', RSS))
        engine = Mock(return_value=[{'title': 'Rust vs Go', 'href': 'https://blog.jetbrains.com/rust/rust-vs-go/', 'body': 'Benchmarks'},
                                    {'title': 'Insecure', 'href': 'http://example.org/', 'body': 'skipped'}])
        results = Web(fetch, search=engine).search_results('rust vs go performance')
        engine.assert_called_once_with('rust vs go performance', 8)
        self.assertEqual([r['url'] for r in results], ['https://blog.jetbrains.com/rust/rust-vs-go/'])
        fetch.assert_not_called()
        engine.side_effect = RuntimeError('rate limited')
        self.assertIn('https://docs.python.org/3/', [r['url'] for r in Web(fetch, search=engine).search_results('docs')])
        self.assertIsNone(Web(fetch).engine)  # an injected fetch never reaches the real search engine

    def test_search_block_is_a_tool_error(self):
        with self.assertRaises(ToolError):
            Web(Mock(return_value=('', 'text/html', '<html>blocked</html>'))).search('docs')

    def test_reader_strips_scripts_and_includes_final_source(self):
        web = Web(Mock(return_value=('https://example.org/final', 'text/html',
                                    '<h1>Title</h1><script>steal()</script><style>hidden</style><p>Body</p>')))
        text, _ = web.read('https://example.org/start')
        self.assertIn('Source: https://example.org/final', text)
        self.assertIn('Body', text)
        self.assertNotIn('steal()', text)
        self.assertNotIn('hidden', text)

    def test_reader_paginates(self):
        web = Web(Mock(return_value=('https://example.org', 'text/plain', 'x' * 9000 + 'END')))
        self.assertIn('offset=8000', web.read('https://example.org')[0])
        self.assertIn('END', web.read('https://example.org', 9000)[0])

    def test_private_dns_results_and_mixed_answers_are_rejected(self):
        for addresses in (['127.0.0.1'], ['192.168.1.1'], ['169.254.169.254'], ['::1'],
                          ['93.184.216.34', '10.0.0.1']):
            with self.subTest(addresses=addresses), patch('ano.plugins.web.socket.getaddrinfo',
                    return_value=[(2, 1, 6, '', (ip, 443)) for ip in addresses]):
                with self.assertRaises(ToolError):
                    public_target('https://example.org/')

    def test_invalid_urls_are_rejected_before_network(self):
        for url in ('http://example.org', 'file:///secret', 'https://user:pass@example.org',
                    'https://example.org:8443', 'https://example.org/\r\nX: bad'):
            with self.subTest(url=url), patch('ano.plugins.web.socket.getaddrinfo') as resolve:
                with self.assertRaises(ToolError):
                    public_target(url)
                resolve.assert_not_called()

    def test_public_ip_is_pinned_and_private_redirect_is_rechecked(self):
        response = Mock(status=302)
        response.getheader.return_value = 'https://127.0.0.1/private'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch('ano.plugins.web.socket.getaddrinfo', side_effect=[
                [(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('127.0.0.1', 443))]]), \
                patch('ano.plugins.web.PublicConnection', return_value=connection) as connect:
            with self.assertRaises(ToolError):
                fetch_public('https://example.org/')
            connect.assert_called_once_with('example.org', '93.184.216.34')
            connection.close.assert_called_once()

    def test_pause_blocks_web_before_fetch(self):
        fetch = Mock()
        tools = Toolbox(lambda: [], allowed=lambda: False, plugins=[Web(fetch)])
        _, _, failed = tools.run('search_public_web', {'query': 'docs'})
        self.assertTrue(failed)
        fetch.assert_not_called()

    def test_brain_search_read_control_and_verification_share_one_loop(self):
        fetch = Mock(side_effect=[('https://www.bing.com', 'application/rss+xml', RSS),
                                  ('https://docs.python.org/3/', 'text/html', '<h1>Python</h1>')])
        desktop = Mock()
        desktop.definitions.return_value = [
            {'name': name, 'description': 'Simulated desktop test tool',
             'input_schema': {'type': 'object', 'properties': {}}}
            for name in ('desktop_observe', 'desktop_input')]
        desktop.run.side_effect = [('Observed target', ''), ('Input dispatched', ''), ('Verified result', '')]
        tools = Toolbox(lambda: [], plugins=[Web(fetch), desktop])
        api = Mock()
        api.chat.completions.create.side_effect = [
            answer(calls=[call('1', 'search_public_web', '{"query":"Python docs"}')]),
            answer(calls=[call('2', 'read_public_page', '{"url":"https://docs.python.org/3/"}')]),
            answer(calls=[call('3', 'desktop_observe', '{}')]),
            answer(calls=[call('4', 'desktop_input', '{}')]),
            answer(calls=[call('5', 'desktop_observe', '{}')]),
            answer('Verified. Source: https://docs.python.org/3/')]
        brain = AnoBrain('local', tools, model='test', base_url='http://localhost:11434/v1',
                         connect=lambda: api, rules='User preference')
        text = brain.reply([{'role': 'user', 'body': 'Find the docs and open them'}])
        self.assertIn('Verified', text)
        self.assertEqual(tools.executions, 5)
        messages = api.chat.completions.create.call_args.kwargs['messages']
        self.assertIn('web-connected computer agent', messages[0]['content'])
        self.assertIn('User preference', messages[0]['content'])
        self.assertEqual([m['content'] for m in messages if m['role'] == 'tool'][-1], 'Verified result')


if __name__ == '__main__':
    unittest.main()
