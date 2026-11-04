"""Immutable fill-based observations; no ranking or execution authority."""
from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import hashlib,json,sqlite3
from .trading_risk import number


class Performance:
    def __init__(self,state):
        self.state=Path(state)
        self.path=Path(state)/'strategy-performance.sqlite3'
        with self.db() as db:db.execute('''CREATE TABLE IF NOT EXISTS closes(
            provider TEXT,trade_id TEXT,strategy TEXT,pair TEXT,session TEXT,closed_at TEXT,
            gross TEXT,cost TEXT,net TEXT,cost_basis TEXT,evidence TEXT,observed_at TEXT,
            PRIMARY KEY(provider,trade_id))''')
        with self.db() as db:db.execute('''CREATE TABLE IF NOT EXISTS original_risks(
            provider TEXT,trade_id TEXT,evidence TEXT NOT NULL,observed_at TEXT NOT NULL,
            PRIMARY KEY(provider,trade_id))''')

    def capture_original_risk(self,provider,trade_id,evidence,lookup=None):
        from .original_risk import original_risk
        if provider=='oanda_practice' and str((evidence.get('receipt') or {}).get('id'))!=str(trade_id):return False
        with self.db() as db:
            if db.execute('SELECT 1 FROM original_risks WHERE provider=? AND trade_id=?',(provider,trade_id)).fetchone():return True
        risk=original_risk(provider,evidence,lookup)
        if risk is None:return False
        with self.db() as db:db.execute('INSERT OR IGNORE INTO original_risks VALUES (?,?,?,?)',
            (provider,trade_id,json.dumps(risk,sort_keys=True),datetime.now(timezone.utc).isoformat()))
        return True

    def recover_original_risk(self,provider,trade_id,evidence):
        """Only historical GETs, and only for this ledger's bound practice account."""
        if self.capture_original_risk(provider,trade_id,evidence):return True
        trade=evidence.get('receipt') or {}
        if provider!='oanda_practice' or not (trade.get('stopLossOrder') or {}).get('replacesOrderID'):return False
        try:
            from .oanda_practice import PracticeAPI
            api=PracticeAPI()
            with sqlite3.connect((self.state/'forex-execution.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                bindings=[r[0] for r in db.execute('SELECT id FROM account_binding')]
            if bindings!=[api.account]:return False
            return self.capture_original_risk(provider,trade_id,evidence,
                lookup=lambda identity:api.read('/orders/'+identity)['order'])
        except Exception:return False  # Unknown risk never breaks trade management or invents R.
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def known(self,provider):
        with self.db() as db:return {r[0] for r in db.execute('SELECT trade_id FROM closes WHERE provider=?',(provider,))}
    def record(self,*,provider,trade_id,strategy,pair,session,gross,cost,cost_basis,evidence,closed_at=None):
        if provider not in ('alpaca_paper','oanda_practice') or cost_basis not in ('broker_reported','configured_allowance'):
            raise ValueError('Unknown evidence/cost scope')
        gross,cost=number(gross),number(cost);net=gross-cost
        if not isinstance(evidence,dict) or evidence.get('fill_verified') is not True:raise ValueError('Verified broker fill evidence required')
        if closed_at:
            at=datetime.fromisoformat(str(closed_at).replace('Z','+00:00'))
            if not at.tzinfo or at>datetime.now(timezone.utc):raise ValueError('Invalid closure timestamp')
            closed_at=at.astimezone(timezone.utc).isoformat()
        body=json.dumps(evidence,sort_keys=True,default=str)
        self.recover_original_risk(provider,trade_id,evidence)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT gross,cost,net,cost_basis FROM closes WHERE provider=? AND trade_id=?',(provider,trade_id)).fetchone()
            if old:
                if (number(old['gross']),number(old['cost']),number(old['net']),old['cost_basis'])!=(gross,cost,net,cost_basis):raise ValueError('Conflicting performance evidence; original retained')
                return
            db.execute('INSERT INTO closes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(provider,trade_id,strategy,pair,session,closed_at,
                str(gross),str(cost),str(net),cost_basis,body,datetime.now(timezone.utc).isoformat()))
    def summary(self,provider=None):
        with self.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM closes ORDER BY closed_at,observed_at,trade_id')]
        if provider:rows=[r for r in rows if r['provider']==provider]
        groups={}
        for row in rows:
            key=tuple(row[k] for k in ('provider','strategy','pair','session','cost_basis'))
            g=groups.setdefault(key,{'provider':key[0],'strategy':key[1],'pair':key[2],'session':key[3],'cost_basis':key[4],
                'trades':0,'wins':0,'losses':0,'gross':Decimal(0),'cost':Decimal(0),'net':Decimal(0),'peak':Decimal(0),'drawdown':Decimal(0),'chronology_verified':True})
            g['trades']+=1;g['wins']+=int(number(row['net'])>0);g['losses']+=int(number(row['net'])<0)
            for k in ('gross','cost','net'):g[k]+=number(row[k])
            g['peak']=max(g['peak'],g['net']);g['drawdown']=max(g['drawdown'],g['peak']-g['net'])
            g['chronology_verified'] &= bool(row['closed_at'])
        result=[]
        for g in groups.values():
            result.append({k:(str(v) if isinstance(v,Decimal) else v) for k,v in g.items() if k not in ('peak','drawdown')}|
                {'max_realized_drawdown':str(g['drawdown']) if g['chronology_verified'] else None,
                 'sample_state':'collecting; no automatic promotion'})
        return {'groups':result,'verified_closes':len(rows),'selection_authority':'none',
            'scope':'Fill-verified closes only; cost basis is explicit. Drawdown is closed-trade realized P/L, not intratrade or account-equity drawdown. Missing closure times leave drawdown unknown. No profitability proof or automatic best-strategy selection.'}


# Alpaca closes before this moment ran under the stop-leg bug that closed filled brackets within seconds
# (fixed and restarted 2026-11-01 17:53 UTC). They say nothing about the strategies, so the scorecard sets them aside.
STOCK_BUG_FIXED_AT='2026-11-01T17:53:00+00:00'
BOTS={'alpaca_paper':'Stocks (Alpaca paper)','oanda_practice':'Forex (OANDA practice)'}


def strategy_label(provider,strategy):
    """One name per strategy on both bots: 'ict_2022_v3' and 'fx_ict_native_v2' are both ICT."""
    from .strategy_signals import STRATEGIES
    reverse={v:k for k,v in STRATEGIES.items()}
    name=reverse.get(strategy) or strategy.removeprefix('fx_').removesuffix('_native_v2')
    return {'ict_2022_v3':'ict','gap_fill_v1_2':'gap fill'}.get(name,name).replace('_',' ')


def r_multiple(row,risk=None):
    """Result in units of the risk taken (entry-to-stop), from the broker evidence; None when unknown.

    R compares the two bots fairly even though their position sizes and account currencies differ."""
    try:
        ev=json.loads(row['evidence'] or '{}')
        if risk is None:
            from .original_risk import original_risk
            risk=original_risk(row['provider'],ev)
        if risk is None:return None
        entry=float(risk['entry']);stop=float(risk['stop']);units=float(risk['units'])
        if row['provider']=='oanda_practice':
            r=ev['receipt'];close=float(r['averageClosePrice']);side=1 if units>0 else -1
            return side*(close-entry)/abs(entry-stop) if entry!=stop else None
        filled=abs(units)
        return float(row['gross'])/(filled*abs(entry-stop)) if filled and entry!=stop else None
    except (KeyError,TypeError,ValueError,StopIteration,ZeroDivisionError):
        return None


def scorecard(state,since=None):
    """Stocks vs forex, strategy by strategy, from fill-verified closes only (optionally only those closed since a UTC time)."""
    with Performance(state).db() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM closes ORDER BY closed_at')]
        risks={(r['provider'],r['trade_id']):json.loads(r['evidence']) for r in db.execute('SELECT * FROM original_risks')}
    if since:rows=[r for r in rows if r['closed_at'] and r['closed_at']>=since.astimezone(timezone.utc).isoformat()]
    excluded=[r for r in rows if r['provider']=='alpaca_paper' and (r['closed_at'] or '')<STOCK_BUG_FIXED_AT]
    rows=[r for r in rows if r not in excluded]
    def stats(group):
        net=[number(r['net']) for r in group];wins=[n for n in net if n>0];losses=[n for n in net if n<0]
        rs=[x for x in (r_multiple(r,risks.get((r['provider'],r['trade_id']))) for r in group) if x is not None]
        return {'trades':len(net),'wins':len(wins),'losses':len(losses),'net':sum(net,Decimal(0)),
                'avg_win':sum(wins,Decimal(0))/len(wins) if wins else None,'avg_loss':sum(losses,Decimal(0))/len(losses) if losses else None,
                'total_r':sum(rs) if rs else None,'avg_r':sum(rs)/len(rs) if rs else None,'r_known':len(rs)}
    bots={p:stats([r for r in rows if r['provider']==p]) for p in BOTS}
    strategies={}
    for r in rows:strategies.setdefault((r['provider'],strategy_label(r['provider'],r['strategy'])),[]).append(r)
    return {'bots':bots,'strategies':{k:stats(v) for k,v in strategies.items()},'excluded':len(excluded)}


def describe_scorecard(state,since=None):
    card=scorecard(state,since)
    money=lambda v:'—' if v is None else ('+' if v>=0 else '−')+f'${abs(v):,.2f}'
    rr=lambda v:'—' if v is None else ('+' if v>=0 else '−')+f'{abs(v):.2f}R'
    lines=['Bot scorecard'+(' · last 7 days' if since else '')+' · paper and practice money · fill-verified closed trades only',
           'R uses verified original entry-to-stop risk (gross, before costs). Net money includes the recorded costs.','']
    for provider,label in BOTS.items():
        s=card['bots'][provider]
        rate=f'{s["wins"]/s["trades"]*100:.0f}% wins' if s['trades'] else 'no closed trades yet'
        lines.append(f'{label}: {s["trades"]} trades · {rate} · total {rr(s["total_r"])} (avg {rr(s["avg_r"])} per trade) · net {money(s["net"])}')
        lines.append(f'Original risk verified for {s["r_known"]}/{s["trades"]} trades; unknown R is excluded.')
    lines += ['','By strategy:']
    for (provider,name),s in sorted(card['strategies'].items(),key=lambda kv:(kv[0][1],kv[0][0])):
        lines.append(f'• {name} · {"stocks" if provider=="alpaca_paper" else "forex"}: {s["trades"]} trades, {s["wins"]}W {s["losses"]}L, '
                     f'total {rr(s["total_r"])}, avg {rr(s["avg_r"])} per trade, net {money(s["net"])}')
    if not card['strategies']:lines.append('• none yet')
    if card['excluded']:
        lines.append(f'\nSet aside: {card["excluded"]} stock trades closed by the stop-leg bug fixed on Nov 1; they do not reflect the strategies.')
    total=sum(s['trades'] for s in card['bots'].values())
    lines.append('\nVerdict: ' + ('too few trades to compare yet. Judge a strategy after roughly 100 trades; '
                 'until then these are observations, not an edge.' if total<200 else
                 'enough trades for a first comparison, but check that results hold after costs and across sessions.'))
    return '\n'.join(lines)


def describe(state,provider=None):
    report=Performance(state).summary(provider)
    lines=[f'Strategy evidence: {report["verified_closes"]} fill-verified closed trades.']
    for g in report['groups']:
        label='net estimate' if g['cost_basis']=='configured_allowance' else 'broker net'
        lines.append(f'{g["provider"]} / {g["strategy"]} / {g["pair"]} / {g["session"]}: n={g["trades"]}, gross ${g["gross"]}, costs ${g["cost"]} ({g["cost_basis"]}), {label} ${g["net"]}, realized drawdown ${g["max_realized_drawdown"] or "unverified"}.')
    return '\n'.join(lines+[report['scope']])
