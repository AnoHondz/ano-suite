import unittest
from services.prop_video_content import candidate_mentions


class VideoContentTests(unittest.TestCase):
    def test_explicit_named_line_is_candidate_not_verified_pick(self):
        segments=[{'start':379,'text':"I'm taking Cedric Coward under 13.5 points."},
                  {'start':381,'text':'Cedric Coward under 13.5 points.'}]
        result=candidate_mentions(segments,['Cedric Coward'])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['line'],13.5)
        self.assertEqual(result[0]['status'],'unverified_caption_candidate')
        self.assertEqual(candidate_mentions(segments,['Other Player']),[])

    def test_title_style_and_non_numeric_picks_are_not_invented(self):
        self.assertEqual(candidate_mentions([{'start':0,'text':'Two best picks today, Cedric Coward looks good.'}],['Cedric Coward']),[])
