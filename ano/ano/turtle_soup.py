"""Ano intraday Turtle Soup-inspired research variant; never sends orders.

Uses a 20-minute failed breakout with four-bar separation, NOT the original
20-day Connors/Raschke strategy. Entry is the closed reclaim candle; stop is
one cent outside its extreme; target is 2R. No performance claim or fill simulation.
"""
from datetime import time
from decimal import Decimal,InvalidOperation
from . import ict

VERSION='ano_turtle_soup_intraday_v1'


def analyze(bars,now,*,regular_end=time(16)):
    result={'version':VERSION,'setups':[],'summary':'WAIT — need 21 consecutive closed RTH minute bars'}
    try:
        rows=[b for b in bars if b['t']+60<=now and ict.ny(b['t']).date()==ict.ny(now).date()
              and time(9,30)<=ict.ny(b['t']).time()<regular_end]
        rows=sorted(rows,key=lambda b:b['t'])[-21:]
        if len(rows)!=21 or any(b['t']-a['t']!=60 for a,b in zip(rows,rows[1:])):return result
        if now-rows[-1]['t']-60>90:
            result['summary']='WAIT — stale closed minute data';return result
        for row in rows:
            values=[Decimal(str(row[k])) for k in 'ohlc']
            o,h,l,c=values
            if not all(v.is_finite() and v>0 for v in values) or not l<=min(o,c)<=max(o,c)<=h:raise ValueError()
        past,signal=rows[:-1],rows[-1]
        high=max(float(b['h']) for b in past);low=min(float(b['l']) for b in past)
        high_age=20-max(i for i,b in enumerate(past) if float(b['h'])==high)
        low_age=20-max(i for i,b in enumerate(past) if float(b['l'])==low)
        short=float(signal['h'])>high and float(signal['c'])<high and high_age>=4
        long=float(signal['l'])<low and float(signal['c'])>low and low_age>=4
        result['summary']='WAIT — no separated sweep and closed-candle reclaim'
        if short and long:
            result['summary']='WAIT — both sides swept; direction ambiguous';return result
        if not (short or long):return result
        entry=Decimal(str(signal['c']));direction='short' if short else 'long'
        stop=Decimal(str(signal['h']))+Decimal('.01') if short else Decimal(str(signal['l']))-Decimal('.01')
        from .trading_risk import TARGET_R
        risk=abs(stop-entry);target=entry-TARGET_R*risk if short else entry+TARGET_R*risk
        if risk<=0 or stop<=0 or target<=0:raise ValueError()
        result['setups']=[{'direction':direction,'confirmed_at':signal['t'],'entry':float(entry),'stop':float(stop),
                           'target':float(target),'reference':high if short else low,'status':'research signal; no fill verified'}]
        result['summary']=f'Confirmed {direction} failed breakout; 20-minute / four-bar intraday variant; research only'
        return result
    except (ValueError,TypeError,KeyError,InvalidOperation):
        result['summary']='WAIT — invalid source bars';return result
