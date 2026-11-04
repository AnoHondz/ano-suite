from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from ano import wallpaper_status as ws


class WallpaperStatusTests(TestCase):
    def test_corrupt_station_and_non_object_json_do_not_stop_other_stations(self):
        def corrupt():return {'state':'healthy','details':[]}
        def good():return ws.station('good','GOOD','Verified observation','healthy')
        with TemporaryDirectory() as tmp,patch.object(ws,'state_dir',return_value=Path(tmp)),patch.object(ws,'STATIONS',[corrupt,good]),patch.object(ws,'repairs',return_value=[]):
            (Path(tmp)/'repairs.json').write_text('[]')
            result=ws.sample()
        self.assertEqual([s['state'] for s in result['stations']],['stale','healthy'])
        self.assertFalse(result['defense']['automatic_repairs'])

    def test_every_station_has_the_shape_the_wallpaper_draws(self):
        result = ws.sample()
        self.assertEqual([s['id'] for s in result['stations']], [b.__name__ for b in ws.STATIONS])
        for s in result['stations']:
            self.assertIn(s['state'], ('healthy', 'attention', 'down', 'stale'))
            self.assertTrue(s['name'] and s['role'] is not None)
            self.assertTrue(all(len(row) == 2 for row in s['details']))

    def test_adding_a_feature_is_one_builder_and_a_broken_one_cannot_hide_the_rest(self):
        def journal():
            return ws.station('journal', 'JOURNAL', 'Trading journal', 'healthy', say=['journal today'])
        def broken():
            raise RuntimeError('feed down')
        with patch.object(ws, 'STATIONS', [ws.system, journal, broken]):
            stations = ws.sample()['stations']
        self.assertEqual([s['id'] for s in stations], ['system', 'journal', 'broken'])
        self.assertEqual(stations[1]['say'], ['journal today'])
        self.assertEqual(stations[2]['state'], 'stale');self.assertEqual(stations[2]['headline'], 'no signal')

    def test_repairs_follow_the_supervisor_and_point_at_real_stations(self):
        files = {'supervisor.health.json': {'checks': {'ScorPred': 'starting', 'Forex manager': 'fresh'}}, 'supervisor.policy.json': {}}
        with TemporaryDirectory() as empty, patch.object(ws, 'state_dir', return_value=Path(empty)), \
             patch.object(ws, 'beating', return_value=True), patch.object(ws, 'read', side_effect=lambda n: files.get(n, {})), \
             patch.object(ws.psutil, 'virtual_memory', return_value=type('M', (), {'percent': 95.0})()), \
             patch.object(ws, 'biggest_apps', return_value=[('code', 3 * 1024 ** 3)]):
            found = ws.repairs()
        self.assertEqual([(r['station'], r['status']) for r in found], [('scorpred', 'working'), ('system', 'needs-you')])
        self.assertEqual(found[1]['steps'], ['using ram: code 3.0 gb'])  # the wallpaper shows "USING RAM" over "code 3.0 gb"
        ids = {b.__name__ for b in ws.STATIONS}
        self.assertTrue(set(ws.COMPONENT_STATION.values()) <= ids and set(ws.POLICY_STATION.values()) <= ids)
