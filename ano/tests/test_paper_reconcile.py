from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import json
import sqlite3
import unittest
from ano.paper_reconcile import reconcile,PaperReader


class PaperReconcileTests(unittest.TestCase):
    def reader(self):
        reader=Mock();reader.get.side_effect=[{'status':'ACTIVE','trading_blocked':False},[],[]]
        return reader

    def test_empty_verified_snapshot_never_places_orders(self):
        with TemporaryDirectory() as tmp:
            reader=self.reader();result=reconcile(tmp,reader)
        self.assertTrue(result['broker_verified']);self.assertEqual(result['positions'],[])
        self.assertEqual([c.args[0] for c in reader.get.call_args_list],['/v2/account','/v2/positions','/v2/orders'])

    def test_disconnect_is_not_flat_account(self):
        reader=Mock();reader.get.side_effect=OSError('offline')
        with TemporaryDirectory() as tmp:result=reconcile(tmp,reader)
        self.assertFalse(result['broker_verified']);self.assertNotIn('positions',result)

    def test_truncated_order_list_is_explicit(self):
        reader=Mock();reader.get.side_effect=[{'status':'ACTIVE'},[],[{}]*100]
        with TemporaryDirectory() as tmp:result=reconcile(tmp,reader)
        self.assertFalse(result['open_orders_complete']);self.assertTrue(result['issues'])

    def tracked(self,order):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        db=sqlite3.connect(Path(tmp.name)/'paper-execution.sqlite3')
        db.execute('CREATE TABLE gap_trades (client_id,symbol,state,filled_qty)')
        db.execute("INSERT INTO gap_trades VALUES ('ano-paper-test','QQQ','partially_filled',5)");db.commit();db.close()
        reader=Mock();reader.get.side_effect=[{'status':'ACTIVE'},[{'symbol':'QQQ','qty':'5','side':'long'}],[],order]
        return reconcile(tmp.name,reader)

    def test_partial_fill_without_active_protection_is_flagged(self):
        result=self.tracked({'client_order_id':'ano-paper-test','symbol':'QQQ','side':'buy','status':'partially_filled','filled_qty':'5',
            'legs':[{'symbol':'QQQ','side':'sell','type':'stop','qty':'10','status':'held'}]})
        self.assertFalse(result['tracked_orders'][0]['protective_legs_seen'])
        self.assertTrue(any('protective' in x for x in result['issues']))

    def test_held_bracket_stop_with_a_live_target_counts_as_protected(self):
        legs=[{'symbol':'QQQ','side':'sell','type':'limit','qty':'5','status':'new'},
              {'symbol':'QQQ','side':'sell','type':'stop','qty':'5','status':'held'}]
        result=self.tracked({'client_order_id':'ano-paper-test','symbol':'QQQ','side':'buy','status':'filled','filled_qty':'5','legs':legs})
        self.assertTrue(result['tracked_orders'][0]['protective_legs_seen'])
        self.assertFalse(any('protective' in x for x in result['issues']))

    def test_active_opposite_side_legs_are_seen_but_not_price_risk_verified(self):
        legs=[{'symbol':'QQQ','side':'sell','type':kind,'qty':'5','status':'new'} for kind in ('stop','limit')]
        result=self.tracked({'client_order_id':'ano-paper-test','symbol':'QQQ','side':'buy','status':'filled','filled_qty':'5','legs':legs})
        self.assertTrue(result['tracked_orders'][0]['protective_legs_seen'])
        self.assertIn('intended price/risk',result['notice'])

    def test_filled_bracket_exit_is_distinguished_from_missing_protection(self):
        result=self.tracked({'client_order_id':'ano-paper-test','symbol':'QQQ','side':'buy','status':'filled','filled_qty':'5',
            'legs':[{'symbol':'QQQ','side':'sell','type':'limit','status':'filled','filled_qty':'5'}]})
        self.assertTrue(result['tracked_orders'][0]['closing_bracket_fill_seen'])
        self.assertFalse(any('protective' in x for x in result['issues']))

    def test_paper_reader_only_uses_fixed_get_host(self):
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
        client.get.return_value=Mock(status_code=200,content=b'{}',json=lambda:{})
        reader=PaperReader(('fixture','fixture'),lambda **kwargs:client)
        reader.get('/v2/account')
        self.assertEqual(client.get.call_args.args[0],'https://paper-api.alpaca.markets/v2/account')
        client.post.assert_not_called();client.delete.assert_not_called()
        with self.assertRaises(ValueError):reader.get('https://api.alpaca.markets/v2/orders')
