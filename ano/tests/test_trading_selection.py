import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from ano.trading_selection import evidence_status


class SelectionTests(TestCase):
    def test_failed_or_absent_evidence_never_invents_a_winner(self):
        with TemporaryDirectory() as tmp:
            Path(tmp,'Session-research-2026-10-27.json').write_text(json.dumps({'passed':False,'best':{
                'strategy':'gap fill','oos':{'both':{'trades':31,'average_r':.038,'profit_factor':1.21}}}}))
            result=evidence_status(tmp)
            self.assertIn('NOT PASSED',result);self.assertIn('unavailable',result)
            self.assertIn('not implemented',result);self.assertIn('no automatic strategy switch',result)

    def test_saved_pass_is_not_misrepresented_as_broker_execution_permission(self):
        with TemporaryDirectory() as tmp:
            Path(tmp,'ICT-research-2026-10-27.json').write_text(json.dumps({'passed':True,'best_in_sample':{'oos':{'both':{'trades':50}}}}))
            result=evidence_status(tmp)
            self.assertIn('PASSED in the saved simulation report',result)
            self.assertIn('not a broker-fill record',result);self.assertIn('best-model selection is not implemented',result)
