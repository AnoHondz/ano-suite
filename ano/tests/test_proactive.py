import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import QCoreApplication
from ano.proactive import HealthState,ProactiveCare,sample

app=QCoreApplication.instance() or QCoreApplication([])


def reading(ram=50,cpu=20,disk=20):
    return dict(at='2026-10-28T19:00:00+00:00',ram_percent=ram,cpu_percent=cpu,
                disk_free_gb=disk,disk_free_percent=15,available_gb=3,
                largest_processes=[{'name':'Example.exe','rss_mb':500}])


class ProactiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'health.json'
        self.now=10000
        self.state=HealthState(self.path,clock=lambda:self.now)

    def test_default_off_and_no_claimed_observation(self):
        self.assertFalse(self.state.data['enabled'])
        self.assertIn('No observations yet',self.state.summary())

    def test_three_sustained_samples_required(self):
        self.assertEqual(self.state.accept(reading(ram=95)),[])
        self.assertEqual(self.state.accept(reading(ram=95)),[])
        text=self.state.accept(reading(ram=95))[0]
        self.assertIn('three checks',text)
        self.assertIn('No apps closed',text)
        self.assertIn('Example.exe',text)

    def test_normal_sample_resets_pressure(self):
        for r in (95,95,50,95,95):self.assertEqual(self.state.accept(reading(ram=r)),[])
        self.assertEqual(len(self.state.accept(reading(ram=95))),1)

    def test_no_repeat_during_cooldown_including_restart(self):
        for _ in range(3):self.state.accept(reading(ram=95))
        other=HealthState(self.path,clock=lambda:self.now)
        for _ in range(4):self.assertEqual(other.accept(reading(ram=95)),[])
        self.now+=1801
        self.assertEqual(len(other.accept(reading(ram=95))),1)

    def test_history_is_bounded(self):
        for _ in range(70):self.state.accept(reading())
        self.assertEqual(len(self.state.data['samples']),60)
        self.assertEqual(self.state.data['events'],[])

    def test_cpu_disk_observations_never_claim_repairs(self):
        for _ in range(2):self.state.accept(reading(cpu=95,disk=2))
        text=' '.join(self.state.accept(reading(cpu=95,disk=2)))
        self.assertIn('No files deleted',text)
        self.assertIn('No process stopped',text)

    def test_corrupt_config_does_not_enable_observation(self):
        self.path.write_text(json.dumps({'enabled':'yes','samples':None,'events':[1],'last_alert':None}))
        state=HealthState(self.path)
        self.assertFalse(state.data['enabled'])
        self.assertIn('No observations',state.summary())

    def care(self,allowed=True):
        notify,reader=Mock(),Mock(return_value=reading())
        obj=ProactiveCare(self.path,lambda:allowed,notify,reader)
        obj.timer.stop();self.addCleanup(obj.close)
        return obj,notify,reader

    def test_pause_lock_private_or_busy_blocks_sampling(self):
        obj,notify,reader=self.care(allowed=False)
        obj.state.data['enabled']=True;obj.state.counts={'ram':2}
        obj.tick()
        reader.assert_not_called();notify.assert_not_called()
        self.assertEqual(obj.state.counts,{})

    def test_disabled_or_paused_inflight_result_is_discarded(self):
        obj,notify,_=self.care()
        obj.state.counts={'ram':2}
        obj._finish(reading(ram=99))
        self.assertEqual(obj.state.data['samples'],[])
        notify.assert_not_called()

    def test_commands_persist_and_unknown_text_never_acts(self):
        obj,notify,reader=self.care()
        self.assertIsNone(obj.command('do anything and everything'))
        self.assertIn('ON',obj.command('enable proactive care'))
        self.assertTrue(HealthState(self.path).data['enabled'])
        self.assertIn('OFF',obj.command('disable proactive care'))
        reader.assert_not_called();notify.assert_not_called()

    def test_real_reader_uses_bounded_cpu_measurement_and_no_command_lines(self):
        with patch('ano.proactive.psutil') as p:
            p.virtual_memory.return_value=Mock(percent=55,available=3*1073741824)
            p.disk_usage.return_value=Mock(free=20*1073741824,percent=75)
            p.cpu_percent.return_value=12
            p.process_iter.return_value=[]
            result=sample()
        p.cpu_percent.assert_called_once_with(interval=.2)
        p.process_iter.assert_called_once_with(['name','memory_info'])
        self.assertEqual(result['cpu_percent'],12)
