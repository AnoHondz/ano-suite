from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from ano.paper_health import read_status


class PaperHealthTests(unittest.TestCase):
    def test_missing_health_never_claims_healthy_or_verified_broker(self):
        with TemporaryDirectory() as tmp:
            report=read_status(tmp)
        self.assertFalse(report['runner']['fresh']);self.assertFalse(report['broker_verified'])

    def test_old_heartbeat_flagged_stale(self):
        with TemporaryDirectory() as tmp:
            now=datetime.now(timezone.utc)
            (Path(tmp)/'paper-execution.health.json').write_text(json.dumps({'at':(now-timedelta(minutes=5)).isoformat(),'state':'running'}))
            self.assertFalse(read_status(tmp,now)['runner']['fresh'])

    def test_genuinely_future_heartbeat_is_still_rejected(self):
        with TemporaryDirectory() as tmp:
            now=datetime.now(timezone.utc)
            (Path(tmp)/'paper-execution.health.json').write_text(json.dumps({'at':(now+timedelta(minutes=5)).isoformat(),'state':'running'}))
            self.assertFalse(read_status(tmp,lambda:now)['runner']['fresh'])

    def test_worker_blocks_decisions_when_management_cannot_be_verified(self):
        from unittest.mock import Mock
        from ano.paper_execution import ExecutionWorker
        with TemporaryDirectory() as tmp:
            executor=Mock();executor.db_path=str(Path(tmp)/'paper-execution.sqlite3')
            executor.enabled=True;executor.ready=True;executor.symbols=('QQQ',)
            executor.clock.return_value=datetime.now(timezone.utc);executor.manage.side_effect=RuntimeError('test fault')
            worker=ExecutionWorker(executor,Mock());worker._decisions=Mock();worker.step()
            worker._decisions.assert_not_called()
            report=read_status(tmp)
            self.assertEqual(report['runner']['state'],'degraded')
            self.assertEqual(report['runner']['errors'],['management:RuntimeError','entry_gate:management_unverified'])


if __name__=='__main__':unittest.main()
