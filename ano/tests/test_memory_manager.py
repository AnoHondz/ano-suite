import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from ano.inference_gate import acquire
from ano.memory_manager import MemoryManager


class MemoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=time.time()
        self.loaded={'ano-local:latest','ano-chat:latest','other-user-model:latest'}
        self.calls=[]
        self.manager=MemoryManager(self.root,['ano-local:latest','ano-chat:latest'],
            chat_models=['ano-chat:latest'],query=self.query,sample=lambda:(87,2*1024**3),clock=lambda:self.now)
        self.manager.started=self.now-600
        self.manager.lock_path.write_bytes(b'0')
        os.utime(self.manager.lock_path,(self.now-60,self.now-60))

    def query(self,path,payload=None):
        self.calls.append((path,payload))
        if path=='/api/ps':return {'models':[{'name':name} for name in self.loaded]}
        self.assertEqual(set(payload),{'model','keep_alive'})
        self.assertEqual(payload['keep_alive'],0)
        self.loaded.discard(payload['model'])
        return {'done':True,'done_reason':'unload'}

    def test_pressure_releases_only_owned_idle_models_without_generation(self):
        result=self.manager.sweep()
        self.assertEqual(result['state'],'released_idle_models')
        self.assertEqual(self.loaded,{'other-user-model:latest'})
        self.assertEqual(set(result['unloaded']),{'ano-local:latest','ano-chat:latest'})

    def test_active_cross_process_lease_blocks_even_forced_cleanup(self):
        with acquire(self.manager.lock_path,lambda:None):
            result=self.manager.sweep(force=True)
        self.assertEqual(result['state'],'inference_active')
        self.assertEqual(self.calls,[])

    def test_healthy_memory_keeps_chat_warm_longer_than_large_model(self):
        self.manager.sample=lambda:(70,4*1024**3)
        os.utime(self.manager.lock_path,(self.now-150,self.now-150))
        before=self.manager.lock_path.stat().st_mtime
        result=self.manager.sweep()
        self.assertEqual(result['unloaded'],['ano-local:latest'])
        self.assertIn('ano-chat:latest',self.loaded)
        self.assertEqual(self.manager.lock_path.stat().st_mtime,before)
        self.now+=151
        self.assertEqual(self.manager.sweep()['unloaded'],['ano-chat:latest'])

    def test_recent_request_prevents_immediate_unload_and_failures_are_bounded(self):
        os.utime(self.manager.lock_path,(self.now-5,self.now-5))
        self.assertEqual(self.manager.sweep()['state'],'waiting_for_idle')
        self.assertFalse(any(payload and payload.get('model')=='ano-local:latest' for _,payload in self.calls))
        self.now+=40
        self.manager.query=lambda *args:(_ for _ in ()).throw(OSError('fixture unavailable'))
        self.assertEqual(self.manager.sweep()['state'],'temporarily_unavailable')

    def test_unload_response_must_be_confirmed_by_running_model_list(self):
        def still_loaded(path,payload=None):
            return {'models':[{'name':'ano-local:latest'}]} if path=='/api/ps' else {'done':True,'done_reason':'unload'}
        self.manager.query=still_loaded
        result=self.manager.sweep()
        self.assertEqual(result['state'],'release_pending')
        self.assertEqual(result['unloaded'],[])

    def test_unload_failures_are_not_hammered_every_poll(self):
        def failed(path,payload=None):
            if payload:raise TimeoutError('fixture timeout')
            return {'models':[{'name':'ano-local:latest'}]}
        self.manager.query=failed
        self.assertEqual(self.manager.sweep()['state'],'temporarily_unavailable')
        self.manager.query=self.query;self.calls=[]
        self.manager.sweep()
        self.assertFalse(any(payload and payload.get('model')=='ano-local:latest' for _,payload in self.calls))

    def test_real_requests_update_idle_timestamp_but_monitor_leases_do_not(self):
        old=self.manager.lock_path.stat().st_mtime
        with acquire(self.manager.lock_path,lambda:None):pass
        self.assertGreater(self.manager.lock_path.stat().st_mtime,old)
        current=self.manager.lock_path.stat().st_mtime
        with acquire(self.manager.lock_path,lambda:None,activity=False):pass
        self.assertEqual(self.manager.lock_path.stat().st_mtime,current)

    def test_a_new_request_during_status_read_cancels_the_unload(self):
        original=self.query
        def query(path,payload=None):
            if path=='/api/ps':os.utime(self.manager.lock_path,(self.now,self.now))
            return original(path,payload)
        self.manager.query=query
        result=self.manager.sweep()
        self.assertEqual(result['state'],'waiting_for_idle')
        self.assertFalse(any(payload for _,payload in self.calls))

    def test_pressure_cleanup_stops_once_enough_memory_is_released(self):
        original=self.query
        def query(path,payload=None):
            value=original(path,payload)
            if path=='/api/ps':
                for item in value['models']:item['size']=4*1024**3 if item['name']=='ano-local:latest' else 1024**3
            return value
        self.manager.query=query
        self.manager.sample=lambda:(88,2*1024**3) if 'ano-local:latest' in self.loaded else (75,4*1024**3)
        result=self.manager.sweep()
        self.assertEqual(result['unloaded'],['ano-local:latest'])
        self.assertIn('ano-chat:latest',self.loaded)
