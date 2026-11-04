"""Alpaca paper-account verification and public IEX bars. Read-only, fixed hosts."""
from datetime import datetime, timezone, timedelta
import httpx

from .tools import ToolError

SYMBOLS = ('QQQ', 'DIA')
SESSION_BREAK = 30 * 60  # seconds without a bar that count as a new session rather than a quiet minute


class AlpacaPaperFeed:
    def __init__(self, key, secret, client_factory=httpx.Client):
        if any(not isinstance(v,str) or not v or any(c.isspace() for c in v) for v in (key,secret)):
            raise ToolError('Paper credentials are missing or invalid. Configure them locally, never in chat.')
        self._headers = {'APCA-API-KEY-ID':key, 'APCA-API-SECRET-KEY':secret}
        self._client_factory = client_factory

    def _get(self, url, params=None):
        # All callers below use literal hosts and paths. No redirects, arbitrary URL,
        # order method, or response-body errors can leak credentials to another host.
        try:
            with self._client_factory(timeout=15, follow_redirects=False, trust_env=False) as client:
                response = client.get(url, headers=self._headers, params=params)
                if response.status_code != 200:
                    raise ToolError(f'Paper connection returned HTTP {response.status_code}; check paper credentials and data permissions.')
                if len(response.content) > 2_000_000:
                    raise ToolError('Paper response exceeded the size limit.')
                result = response.json()
                if not isinstance(result,dict):
                    raise ToolError('Paper response was not a JSON object.')
                return result
        except (httpx.HTTPError, ValueError):
            raise ToolError('Paper connection failed or returned invalid JSON. No order was submitted.') from None

    def verify(self):
        account = self._get('https://paper-api.alpaca.markets/v2/account')
        return dict(paper_endpoint_verified=True, account_active=account.get('status')=='ACTIVE',
                    trading_blocked=bool(account.get('trading_blocked',True)),
                    execution_enabled=None,execution_scope='Connection-only probe; use trading bot readiness for actual strategy permissions.',
                    feed='iex', symbols=list(SYMBOLS))

    def latest_quote(self,symbol):
        if symbol not in SYMBOLS:raise ToolError('Paper quote scope is QQQ/DIA only.')
        result=self._get(f'https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest',{'feed':'iex'})
        try:
            quote=result['quote']
            return {'bid':quote['bp'],'ask':quote['ap'],'at':quote['t']}
        except (KeyError,TypeError):raise ToolError('Latest IEX quote unavailable; no entry assumed.') from None

    def between(self, symbol, start, end, pages=40):
        """One-minute IEX bars from start to end (UTC datetimes), oldest first. For research: up to 400,000 bars."""
        if symbol not in SYMBOLS:
            raise ToolError('This paper watchlist permits QQQ and DIA only.')
        if not (isinstance(start, datetime) and isinstance(end, datetime) and start < end):
            raise ToolError('Give a start before the end.')
        params = dict(symbols=symbol, timeframe='1Min', limit=10000, feed='iex', sort='asc', adjustment='raw',
                      start=start.isoformat(), end=end.isoformat())
        rows = []
        for _page in range(pages):
            result = self._get('https://data.alpaca.markets/v2/stocks/bars', params)
            try:
                for b in (result.get('bars') or {}).get(symbol) or []:
                    rows.append(dict(t=int(datetime.fromisoformat(b['t'].replace('Z', '+00:00')).timestamp()),
                                     **{key: b[key] for key in ('o', 'h', 'l', 'c')},
                                     **({'v':b['v']} if 'v' in b else {})))
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                raise ToolError('The paper data returned an unusable bar.') from None
            token = result.get('next_page_token')
            if not token:
                break
            params = dict(params, page_token=token)
        else:
            raise ToolError('The date range is too long for one request; ask for fewer months.')
        rows.sort(key=lambda b: b['t'])
        return [row for i, row in enumerate(rows) if i == 0 or row['t'] != rows[i - 1]['t']]

    def history(self, symbol, days, now=None):
        """One-minute IEX bars for about the last `days` trading days (1–20), oldest first, pre-market included."""
        if symbol not in SYMBOLS:
            raise ToolError('This paper watchlist permits QQQ and DIA only.')
        if type(days) is not int or not 1 <= days <= 20:
            raise ToolError('Choose 1–20 trading days.')
        now = now or datetime.now(timezone.utc)
        params = dict(symbols=symbol, timeframe='1Min', limit=10000, feed='iex', sort='asc', adjustment='raw',
                      start=(now - timedelta(days=days * 7 // 5 + 4)).isoformat(), end=now.isoformat())
        rows = []
        for _page in range(8):  # at most 80,000 bars; 20 days is about 20,000
            result = self._get('https://data.alpaca.markets/v2/stocks/bars', params)
            try:
                for b in (result.get('bars') or {}).get(symbol) or []:
                    rows.append(dict(t=int(datetime.fromisoformat(b['t'].replace('Z', '+00:00')).timestamp()),
                                     **{key: b[key] for key in ('o', 'h', 'l', 'c')}))
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                raise ToolError('The paper data returned an unusable bar.') from None
            token = result.get('next_page_token')
            if not token:
                break
            params = dict(params, page_token=token)
        rows.sort(key=lambda b: b['t'])
        unique = [row for i, row in enumerate(rows) if i == 0 or row['t'] != rows[i - 1]['t']]
        if len(unique) < 20:
            raise ToolError('No usable history returned for the paper watchlist.')
        return dict(candles=unique, feed='iex')

    def higher_timeframes(self, symbol, now=None):
        """Bounded daily/4h/1h source bars. SMC excludes forming bars itself."""
        if symbol not in SYMBOLS:
            raise ToolError('This paper watchlist permits QQQ and DIA only.')
        from .smc import closed_bars
        now = now or datetime.now(timezone.utc)
        series = {}
        for name, timeframe, seconds in (('daily','1Day',86400),('4h','4Hour',14400),('1h','1Hour',3600)):
            result = self._get('https://data.alpaca.markets/v2/stocks/bars', dict(
                symbols=symbol, timeframe=timeframe, limit=200, feed='iex', sort='desc', adjustment='raw',
                start=(now-timedelta(days=45)).isoformat(), end=now.isoformat()))
            try:
                raw = (result.get('bars') or {}).get(symbol) or []
                rows = sorted([dict(t=int(datetime.fromisoformat(b['t'].replace('Z','+00:00')).timestamp()),
                                    **{k:b[k] for k in 'ohlc'}) for b in raw], key=lambda b:b['t'])
                series[name] = closed_bars(rows, int(now.timestamp()), seconds)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                raise ToolError('Higher-timeframe data was invalid; no bias was inferred.') from None
        return series

    def candles(self, symbol, now=None):
        if symbol not in SYMBOLS:
            raise ToolError('This paper watchlist permits QQQ and DIA only.')
        now = now or datetime.now(timezone.utc)
        result = self._get('https://data.alpaca.markets/v2/stocks/bars', dict(
            symbols=symbol, timeframe='1Min', limit=500, feed='iex', sort='desc', adjustment='raw',
            start=(now-timedelta(days=4)).isoformat(), end=now.isoformat()))
        try:
            bars = result['bars'][symbol]
            if not isinstance(bars,list) or not 3 <= len(bars) <= 500:
                raise ValueError()
            rows = [dict(t=int(datetime.fromisoformat(b['t'].replace('Z','+00:00')).timestamp()),
                         **{key:b[key] for key in ('o','h','l','c')},
                         **({'v':b['v']} if 'v' in b else {})) for b in bars]
            rows = sorted(rows,key=lambda b:b['t'])
            # IEX has no bar for a minute without an IEX trade, so short holes are normal and kept (the analysis
            # reports them). Only a break longer than SESSION_BREAK (overnight, a halt) starts a new segment.
            cut, missing = 0, 0
            for i in range(1,len(rows)):
                step = rows[i]['t']-rows[i-1]['t']
                if step <= 0 or step % 60:
                    raise ValueError()
                if step > SESSION_BREAK:
                    cut, missing = i, 0
                else:
                    missing += step // 60 - 1
            if len(rows)-cut < 3:
                raise ValueError()
            return dict(candles=rows[cut:], omitted_before_last_gap=cut, missing_minutes=missing, feed='iex')
        except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
            raise ToolError('No usable candle series returned for the paper watchlist.') from None
