import json,tempfile
from pathlib import Path
from unittest import TestCase
from ano.original_risk import original_risk
from ano.performance import Performance,r_multiple,scorecard

class OriginalRiskTests(TestCase):
    def fixture(self):
        opened='2026-11-02T15:01:23Z'
        trade={'id':'49','price':'1.34202','averageClosePrice':'1.34117','initialUnits':'-180702','openTime':opened,
               'stopLossOrder':{'id':'59','price':'1.34200','tradeID':'49','type':'STOP_LOSS',
                                'createTime':'2026-11-02T20:01:10Z','replacesOrderID':'51'}}
        initial={'id':'51','price':'1.34285','tradeID':'49','type':'STOP_LOSS','createTime':opened}
        return {'fill_verified':True,'receipt':trade},initial

    def test_replaced_stop_cannot_create_forty_two_r_win(self):
        ev,initial=self.fixture();row={'provider':'oanda_practice','evidence':json.dumps(ev)}
        self.assertIsNone(r_multiple(row))
        risk=original_risk('oanda_practice',ev,lambda identity:initial)
        self.assertAlmostEqual(r_multiple(row,risk),.00085/.00083)

    def test_wrong_trade_delayed_stop_and_cycles_are_unknown(self):
        ev,initial=self.fixture()
        self.assertIsNone(original_risk('oanda_practice',ev,lambda _:dict(initial,tradeID='wrong')))
        self.assertIsNone(original_risk('oanda_practice',ev,lambda _:dict(initial,createTime='2026-11-02T16:01:23Z')))
        self.assertIsNone(original_risk('oanda_practice',ev,lambda _:dict(initial,replacesOrderID='51')))

    def test_original_snapshot_survives_later_stop_changes(self):
        ev,initial=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            store=Performance(Path(tmp))
            self.assertTrue(store.capture_original_risk('oanda_practice','49',ev,lambda _:initial))
            store.record(provider='oanda_practice',trade_id='49',strategy='turtle soup',pair='GBP_USD',session='new_york',
                         gross='153.5967',cost=0,cost_basis='broker_reported',evidence=ev,closed_at='2026-11-02T20:01:20Z')
            ev['receipt']['stopLossOrder']['price']='1.342019'
            self.assertTrue(store.capture_original_risk('oanda_practice','49',ev))
            result=scorecard(Path(tmp))['bots']['oanda_practice']
            self.assertEqual(result['r_known'],1)
            self.assertAlmostEqual(result['total_r'],.00085/.00083)
            with store.db() as db:self.assertEqual(json.loads(db.execute('select evidence from original_risks').fetchone()[0])['stop'],'1.34285')

    def test_missing_original_risk_reports_unknown_and_keeps_money(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Performance(Path(tmp));store.record(provider='oanda_practice',trade_id='missing',strategy='ict',pair='EUR_USD',
                session='london',gross=10,cost=1,cost_basis='broker_reported',evidence={'fill_verified':True})
            result=scorecard(Path(tmp))['bots']['oanda_practice']
            self.assertEqual(result['net'],9);self.assertEqual(result['r_known'],0);self.assertIsNone(result['total_r'])
