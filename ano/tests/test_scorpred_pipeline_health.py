from unittest import TestCase
from unittest.mock import Mock,patch
import httpx
from ano.scorpred_health import assess
from ano.supervisor import scor_probe,SCOR_ROOT

class PipelineHealthTests(TestCase):
    def test_online_database_does_not_hide_degraded_pipeline(self):
        service={'status':'ok','db':'connected'}
        pipes={'overall':'degraded','pipelines':{'nba_games':{'status':'ok'},
            'ml_predictions':{'status':'degraded','error':'Source refresh delayed'}}}
        state,detail=assess(service,pipes)
        self.assertEqual(state,'degraded');self.assertIn('ml_predictions',detail);self.assertIn('Source refresh delayed',detail)
        self.assertEqual(assess(service,{})[0],'degraded')

    def test_supervisor_accepts_503_json_as_degraded_not_offline(self):
        def respond(request):
            if request.url.path=='/api/local-runtime':return httpx.Response(200,json={'app':'ScorPred','root':str(SCOR_ROOT)})
            if request.url.path=='/health':return httpx.Response(200,json={'status':'ok','db':'connected'})
            return httpx.Response(503,json={'overall':'degraded','pipelines':{'ml_predictions':{'status':'degraded','error':'stale'}}})
        client=httpx.Client(transport=httpx.MockTransport(respond))
        with patch('httpx.Client',return_value=client):state=scor_probe()
        self.assertTrue(state.startswith('degraded:'));self.assertIn('ml_predictions',state)

    def test_all_reported_pipelines_must_be_ok(self):
        service={'status':'ok','db':'connected','api':'ok'}
        pipes={'overall':'ok','pipelines':{'nba_games':{'status':'ok'}}}
        self.assertEqual(assess(service,pipes)[0],'healthy')
        self.assertEqual(assess(dict(service,degraded_mode=True),pipes)[0],'degraded')

    def test_chat_and_wallpaper_show_degraded_reason(self):
        import tempfile
        from pathlib import Path
        from ano.plugins.scorpred import ScorPred
        from ano import wallpaper_status as ws
        service={'status':'ok','db':'connected'}
        pipes={'overall':'degraded','pipelines':{'ml_predictions':{'status':'degraded','error':'Source delayed'}}}
        def get(path,*a,**kw):
            return (pipes if path=='/api/health' else service),503 if path=='/api/health' else 200
        with tempfile.TemporaryDirectory() as tmp:
            plugin=ScorPred(Path(tmp));plugin._identity=Mock(return_value={'daily_digest':{}});plugin._get=get
            text,_=plugin.run('scorpred_status',{})
            self.assertIn('DEGRADED',text);self.assertIn('ml_predictions',text)
            self.assertEqual(plugin.repair_probe()['state'],'degraded')
        def local(url,*a,**kw):
            if url.endswith('/health') and not url.endswith('/api/health'):return service
            if url.endswith('/api/health'):return pipes
            raise OSError()
        with patch.object(ws,'local_json',side_effect=local),patch.object(ws,'supervisor_repair',return_value=''):
            station=ws.scorpred()
        self.assertEqual(station['state'],'attention')
        self.assertIn('ml_predictions',dict(station['details'])['Data'])
