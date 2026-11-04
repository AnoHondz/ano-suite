from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from services.prop_evidence_store import EvidenceStore
from services.prop_analyst_research import ingest,for_game


class AnalystTests(unittest.TestCase):
    def test_approving_one_video_does_not_approve_every_youtube_video(self):
        sources={'a':{'permitted':True,'hosts':['www.youtube.com','youtu.be'],'video_ids':['GR1YM8qUHjE']}}
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'evidence.sqlite3')
            with self.assertRaisesRegex(ValueError,'video is not approved'):
                ingest(store,game_id='g',source_id='a',url='https://www.youtube.com/watch?v=AAAAAAAAAAA',
                       published_at='2026-10-29T15:00:00Z',excerpt='Evidence',sources=sources)
            ingest(store,game_id='g',source_id='a',url='https://youtu.be/GR1YM8qUHjE',
                   published_at='2026-10-29T15:00:00Z',excerpt='User-reviewed excerpt',sources=sources)
    def test_copied_arguments_are_grouped_and_late_received_sources_excluded(self):
        sources={s:{'name':s,'hosts':['example.com'],'permitted':True} for s in ('a','b')}
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'evidence.sqlite3')
            for source,received in [('a','2026-10-29T15:01:00Z'),('b','2026-10-29T15:02:00Z')]:
                ingest(store,game_id='g',source_id=source,url='https://example.com/'+source,
                       published_at='2026-10-29T15:00:00Z',received_at=received,
                       excerpt='Minutes increased after the rotation changed.',sources=sources)
            early=for_game(store,'g','2026-10-29T15:01:30Z',sources)
            self.assertEqual(len(early['argument_groups'][0]['sources']),1)
            later=for_game(store,'g','2026-10-29T16:00:00Z',sources)
            self.assertEqual(len(later['argument_groups']),1)
            self.assertEqual(len(later['argument_groups'][0]['sources']),2)
            self.assertEqual(later['probability_adjustment'],0)
            sources['a']['permitted']=False;sources['b']['permitted']=False
            self.assertEqual(for_game(store,'g','2026-10-29T16:00:00Z',sources)['status'],'unavailable')

    def test_unapproved_collection_and_wrong_hosts_are_rejected(self):
        with TemporaryDirectory() as folder:
            store=EvidenceStore(Path(folder)/'evidence.sqlite3')
            for sources,url in (({},'https://example.com/a'),({'a':{'permitted':True,'hosts':['example.com']}},'https://other.example/a')):
                with self.assertRaises(ValueError):
                    ingest(store,game_id='g',source_id='a',url=url,published_at='2026-10-29T15:00:00Z',
                           excerpt='Evidence',sources=sources)
