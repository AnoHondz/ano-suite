from datetime import datetime,timedelta,timezone
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo
from ano import ict
from ano.forex_sessions import active,context,entry_bounds,pricing
from ano.forex_signals import scan


class SessionTests(TestCase):
    def test_added_pairs_trade_their_sessions_with_correct_pip_size(self):
        new_york=datetime(2026,11,2,10,tzinfo=ZoneInfo('America/New_York'))
        tokyo=datetime(2026,11,4,10,tzinfo=ZoneInfo('Asia/Tokyo'))
        for pair in ('USD_JPY','AUD_USD','USD_CHF'):self.assertIn('new_york',active(pair,new_york))
        for pair in ('AUD_USD','NZD_USD','EUR_JPY'):self.assertIn('tokyo',active(pair,tokyo))
        self.assertEqual(active('NZD_USD',new_york),[])
        self.assertEqual(pricing('EUR_JPY'),(Decimal('.001'),Decimal('.01'),10))
        self.assertEqual(pricing('AUD_USD'),(Decimal('.00001'),Decimal('.0001'),1000))

    def test_london_follows_uk_dst_and_preserves_new_york_overlap(self):
        for month,day,hour in ((1,15,8),(7,15,7),(10,29,8)):
            now=datetime(2026,month,day,hour,30,tzinfo=timezone.utc)
            self.assertIn('london',active('GBP_USD',now))
            self.assertEqual(context('london',now)['start'].astimezone(timezone.utc).hour,hour)
        overlap=datetime(2026,10,29,14,tzinfo=timezone.utc)
        self.assertEqual(set(active('EUR_USD',overlap)),{'london','new_york'})
        self.assertEqual(active('USD_CAD',overlap),['new_york'])

    def test_tokyo_uses_japan_date_and_only_usdjpy(self):
        now=datetime(2026,10,29,20,30,tzinfo=ict.NY)
        self.assertEqual(active('USD_JPY',now),['tokyo'])
        self.assertEqual(context('tokyo',now)['id'],'tokyo:2026-10-30')
        self.assertNotIn('tokyo',active('GBP_USD',now))
        weekend=datetime(2026,10,31,10,tzinfo=ZoneInfo('Asia/Tokyo'))
        self.assertEqual(active('USD_JPY',weekend),[])

    def test_late_london_entries_and_flatten_are_session_local(self):
        now=datetime(2026,10,29,16,40,tzinfo=ZoneInfo('Europe/London'))
        p={'pair':'GBP_USD','name':'turtle soup','session':'london','session_id':'london:2026-10-29'}
        window=entry_bounds(p,now)
        self.assertEqual((window[0].hour,window[0].minute),(16,50))
        self.assertEqual((window[1].hour,window[1].minute),(16,55))
        self.assertIsNone(entry_bounds(p,now.replace(hour=17)))
        self.assertIsNone(entry_bounds({**p,'pair':'USD_JPY'},now))

    def test_jpy_tick_and_pip_match_normalized_analytics_cent(self):
        for pair in ('EUR_USD','USD_JPY'):
            tick,pip,scale=pricing(pair)
            self.assertEqual(Decimal('.01')/scale,tick)
            self.assertEqual(tick*10,pip)

    def test_full_london_day_does_not_exceed_indicator_history_limit(self):
        now=datetime(2026,10,29,16,45,tzinfo=ZoneInfo('Europe/London'))
        start=int(now.replace(hour=8,minute=0).timestamp())
        rows=[dict(t=start+i*60,o=1.1,h=1.1001,l=1.0999,c=1.1) for i in range(525)]
        _,notes=scan('GBP_USD',rows,{},now,session='london')
        self.assertEqual(len(notes),7)

    def test_signal_timestamps_return_to_actual_clock_and_prices_to_fx_units(self):
        for pair,session,zone,hour,price in (('GBP_USD','london','Europe/London',8,1.1),('USD_JPY','tokyo','Asia/Tokyo',9,150)):
            now=datetime(2026,10,29,hour,45,tzinfo=ZoneInfo(zone))
            rows=[dict(t=int(now.timestamp())-60*i,o=price,h=price,l=price,c=price) for i in range(60,0,-1)]
            scale=pricing(pair)[2]
            def candidate(name,symbol,bars,virtual,report,**kwargs):
                if name!='technicals':return []
                return [dict(name=name,strategy='ema_cross_v1',symbol=symbol,side='long',entry=price*scale,
                    stop=(price-0.01)*scale,target=(price+0.03)*scale,confirmed_at=virtual.timestamp()-20,
                    last_closed_at=virtual.timestamp()-20,signal_id='signal')]
            with patch('ano.forex_signals.candidates',side_effect=candidate):
                plans,_=scan(pair,rows,{},now,session=session)
            self.assertEqual(len(plans),1)
            self.assertEqual(plans[0]['confirmed_at'],now.timestamp()-20)
            self.assertEqual(Decimal(plans[0]['entry']),Decimal(str(price)))
            self.assertEqual(plans[0]['session'],session)
