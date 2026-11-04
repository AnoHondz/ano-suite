import unittest
from unittest.mock import Mock, patch

from ano.plugins.websearch import WebSearch
from ano.tools import Toolbox


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        self.results = [
            {'title': 'Example One', 'href': 'https://example.com/1', 'body': 'First  snippet  here'},
            {'title': 'Example Two', 'href': 'https://example.com/2', 'body': 'Second snippet'},
        ]
        self.page = ('Example Page Title', 'Hello world.\n\nThis is the page body with useful text.')
        self.plugin = WebSearch(search=lambda q, n: self.results[:n], fetch=lambda url: self.page)
        self.toolbox = Toolbox(lambda: [], plugins=[self.plugin])

    def test_search_returns_titles_links_and_snippets(self):
        text, note, failed = self.toolbox.run('search_internet', {'query': 'example', 'count': 2})
        self.assertFalse(failed)
        self.assertIn('Example One', text)
        self.assertIn('https://example.com/1', text)
        self.assertIn('First snippet here', text)  # whitespace collapsed
        self.assertEqual(note, 'Searched the web for “example”')

    def test_empty_query_and_no_results(self):
        self.assertTrue(self.toolbox.run('search_internet', {'query': '   '})[2])
        empty = WebSearch(search=lambda q, n: [])
        text, _, failed = Toolbox(lambda: [], plugins=[empty]).run('search_internet', {'query': 'zzz'})
        self.assertFalse(failed)
        self.assertIn('No results', text)

    def test_read_web_page_returns_title_and_text(self):
        text, note, failed = self.toolbox.run('read_web_page', {'url': 'https://example.com/1'})
        self.assertFalse(failed)
        self.assertIn('Example Page Title', text)
        self.assertIn('useful text', text)

    def test_read_web_page_rejects_non_https(self):
        for url in ('file:///C:/secret.txt', 'http://example.com/1'):
            self.assertTrue(self.toolbox.run('read_web_page', {'url': url})[2])

    def test_default_reader_refuses_local_and_private_targets_and_redirects(self):
        reader = Toolbox(lambda: [], plugins=[WebSearch()])
        for addresses in (['127.0.0.1'], ['192.168.1.1'], ['169.254.169.254'], ['::1'], ['93.184.216.34', '10.0.0.1']):
            with self.subTest(addresses=addresses), patch('ano.plugins.web.socket.getaddrinfo',
                    return_value=[(2, 1, 6, '', (ip, 443)) for ip in addresses]), \
                    patch('ano.plugins.web.PublicConnection') as connect:
                text, _, failed = reader.run('read_web_page', {'url': 'https://router.example/'})
                self.assertTrue(failed)
                self.assertIn('public HTTPS', text)
                connect.assert_not_called()
        response = Mock(status=302)
        response.getheader.return_value = 'https://localhost/admin'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch('ano.plugins.web.socket.getaddrinfo', side_effect=[
                [(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('127.0.0.1', 443))]]), \
                patch('ano.plugins.web.PublicConnection', return_value=connection) as connect:
            self.assertTrue(reader.run('read_web_page', {'url': 'https://example.org/'})[2])
            connect.assert_called_once_with('example.org', '93.184.216.34')

    def test_search_failure_is_a_clean_message(self):
        def boom(q, n):
            raise RuntimeError('network down')
        plugin = WebSearch(search=boom)
        text, _, failed = Toolbox(lambda: [], plugins=[plugin]).run('search_internet', {'query': 'x'})
        self.assertTrue(failed)
        self.assertIn('unavailable', text)

    def test_html_parsing_strips_scripts(self):
        from ano.plugins.websearch import _fetch_page  # exercise the real parser on inline HTML
        html = '<html><head><title>T</title></head><body><script>bad()</script><p>Kept text</p></body></html>'
        with patch('ano.plugins.websearch.fetch_public', return_value=('https://example.com', 'text/html', html)):
            title, text = _fetch_page('https://example.com')
        self.assertEqual(title, 'T')
        self.assertIn('Kept text', text)
        self.assertNotIn('bad()', text)
