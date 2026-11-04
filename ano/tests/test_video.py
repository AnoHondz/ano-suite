import unittest

from ano.plugins.video import Video, video_id, transcript_error
from ano.tools import Toolbox


class VideoIdTests(unittest.TestCase):
    def test_accepts_many_youtube_forms(self):
        for text in ('dQw4w9WgXcQ',
                     'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                     'https://youtu.be/dQw4w9WgXcQ',
                     'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s',
                     'https://www.youtube.com/shorts/dQw4w9WgXcQ',
                     'youtube.com/embed/dQw4w9WgXcQ'):
            self.assertEqual(video_id(text), 'dQw4w9WgXcQ', text)

    def test_rejects_non_youtube_and_junk(self):
        toolbox = Toolbox(lambda: [], plugins=[Video()])
        for text in ('https://vimeo.com/12345', 'not a link', 'https://youtube.com/watch?v=short'):
            self.assertTrue(toolbox.run('read_video_transcript', {'video': text})[2], text)


class VideoTranscriptTests(unittest.TestCase):
    def test_transcript_is_joined_and_cleaned(self):
        captions = [{'text': 'Hello  there'}, {'text': '\n'}, {'text': 'welcome to the video'}]
        video = Video(fetch=lambda identifier: captions)
        text, note = video.read_video_transcript('https://youtu.be/dQw4w9WgXcQ')
        self.assertEqual(text, 'Hello there welcome to the video')
        self.assertEqual(note, 'Read the transcript of youtu.be/dQw4w9WgXcQ')

    def test_long_transcript_is_capped_with_offset(self):
        captions = [{'text': 'word ' * 4000}]  # ~20k chars
        video = Video(fetch=lambda identifier: captions)
        first, _ = video.read_video_transcript('dQw4w9WgXcQ')
        self.assertLessEqual(len(first), 8200)
        self.assertIn('offset=8000', first)
        second, _ = video.read_video_transcript('dQw4w9WgXcQ', offset=8000)
        self.assertNotIn('offset=8000', second)

    def test_missing_captions_is_a_clean_message(self):
        class TranscriptsDisabled(Exception):
            pass
        def fail(identifier):
            raise TranscriptsDisabled()
        toolbox = Toolbox(lambda: [], plugins=[Video(fetch=fail)])
        result, _, failed = toolbox.run('read_video_transcript', {'video': 'dQw4w9WgXcQ'})
        self.assertTrue(failed)
        self.assertIn('no captions', result)

    def test_error_mapping(self):
        self.assertIn('no captions', transcript_error(type('NoTranscriptFound', (Exception,), {})()))
        self.assertIn('unavailable', transcript_error(type('VideoUnavailable', (Exception,), {})()))
