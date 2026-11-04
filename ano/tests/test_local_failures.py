"""Regressions from the October 27 hard-task run on the local model (no GPT)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ano.brain import LocalConversationRouter
from ano.local_profile import task_tools, fast_system
from ano.plugins.artifacts import Artifacts
from ano.plugins.word_documents import WordDocuments
from ano.plugins.workshop import Workshop
from ano.tools import Toolbox


def names(tools):
    return {tool['function']['name'] for tool in tools}


class LocalFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workshop = Workshop(Path(self.tmp.name) / 'AnoWorkspace', None)
        self.toolbox = Toolbox(lambda: [Path(self.tmp.name)], plugins=[self.workshop, WordDocuments(self.workshop),
                                                                     Artifacts(self.workshop)])

    def definitions(self):
        return self.toolbox.definitions() + [{'name': 'read_video_transcript', 'description': 'Read captions.',
                                              'input_schema': {'type': 'object', 'properties': {}}}]

    def test_video_links_reach_the_agent_with_the_transcript_tool(self):
        # The chat model has no tools and invented a summary of a video it never read.
        request = 'Summarise this video in 2 bullet points: https://www.youtube.com/watch?v=jNQXAC9IVRw'
        self.assertIn('read_video_transcript', names(task_tools(self.definitions(), request)))
        agent, chat = Mock(), Mock()
        agent.toolbox = Mock(definitions=Mock(return_value=[]))
        LocalConversationRouter(agent, chat).reply([{'role': 'user', 'body': 'what is on https://example.org ?'}])
        agent.reply.assert_called_once()
        chat.reply.assert_not_called()

    def test_document_formats_offer_their_own_tools(self):
        for request, tool in [('Create a Word document called Week 4 Plan', 'write_word_document'),
                              ('Make a spreadsheet called Grades with a chart', 'create_spreadsheet'),
                              ('Create a PowerPoint called Study Tips with 4 slides', 'create_presentation'),
                              ('Make a PDF called Packing List', 'create_pdf')]:
            with self.subTest(request=request):
                self.assertIn(tool, names(task_tools(self.definitions(), request)))

    def test_writer_tools_are_hidden_without_a_writer_but_typed_commands_still_explain(self):
        offered = {item['name'] for item in self.toolbox.definitions()}
        self.assertFalse({'draft_document', 'draft_word_document', 'research_and_summarize'} & offered)
        self.assertIn('write_word_document', offered)
        text, _, failed = self.toolbox.run('draft_document', {'name': 'X', 'topic': 'Y'})
        self.assertTrue(failed)
        self.assertNotIn('Unknown tool', text)
        self.workshop.writer = lambda prompt: 'draft'
        self.assertIn('draft_word_document', {item['name'] for item in self.toolbox.definitions()})

    def test_chart_numbers_sent_as_text_are_accepted(self):
        result = json.loads(self.toolbox.run('create_spreadsheet', {
            'name': 'Grades', 'headers': ['Assignment', 'Score'],
            'rows': [['Lab 1', '88'], ['Lab 2', '92.5'], ['Quiz 1', 75]], 'chart_column': 2})[0])
        self.assertEqual(result['chart_count'], 1)
        from openpyxl import load_workbook
        values = [row for row in load_workbook(result['path']).active.values]
        self.assertEqual(values[1:], [('Lab 1', 88), ('Lab 2', 92.5), ('Quiz 1', 75)])

    def test_local_instructions_explain_relative_reminder_times(self):
        self.assertIn('2 hours before 6 pm'.replace('2', 'N'), fast_system())


if __name__ == '__main__':
    unittest.main()
