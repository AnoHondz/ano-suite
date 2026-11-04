"""Capture bounded OANDA practice GET-only replay inputs. Never enables orders."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ano.oanda_practice import PracticeAPI
from ano.forex_shadow import fingerprint
from ano.forex_runner import forex_news


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair',choices=['EUR_USD','GBP_USD','USD_CAD','USD_JPY'],required=True)
    parser.add_argument('--session',choices=['london','new_york','tokyo'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();api=PracticeAPI();now=datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        price=pool.submit(api.bid_ask_candles,args.pair,5000)
        higher={k:pool.submit(api.candles,args.pair,frame,100) for k,frame in [('daily','D'),('4h','H4'),('1h','H1')]}
        candles=price.result();series={k:v.result() for k,v in higher.items()}
    try:
        news=[{'currency':currency,'t':at.timestamp()} for currency,at in forex_news(now)]
        news_status='current-week calendar received now; historical revision timing unverified'
    except Exception:
        news=None;news_status='unavailable; replay blocks new entries'
    result={'pair':args.pair,'session':args.session,'candles':candles,'higher':series,'news':news,
            'source':'OANDA practice GET candles, M1 bid/ask and completed HTF mid candles',
            'received_at':now.isoformat(),'engine_before_capture':fingerprint(),'news_status':news_status,
            'scope':'Retrospective capture; not prospective proof or permission to trade'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():raise ValueError('Choose a new input filename; original evidence retained')
    args.output.write_text(json.dumps(result),encoding='utf-8')
    print(json.dumps({'saved':str(args.output),'minute_bars':len(candles),'higher_bars':{k:len(v) for k,v in series.items()},
                      'news_status':news_status,'orders_sent':0}))


if __name__=='__main__':main()
