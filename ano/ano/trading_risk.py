"""Offline paper-trading sizing checks. No broker connection or order submission.

This calculator does not verify market data, strategy evidence, or monitoring.
Passing it is necessary but never sufficient to authorize an order.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a financial number.')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid financial number.') from None
    if not result.is_finite():
        raise ValueError('Financial numbers must be finite.')
    return result


# A stop inside normal 5-minute noise gets hit before the idea can work (two 'technicals' trades on
# 2026-11-02 were stopped in under 80 s at 0.6x and 1.1x). The USD/JPY turtle-soup blueprint win
# that day sat 1.3x out and reached its 2.8R target. Risk sizing keeps dollar risk unchanged.
VOLATILITY_STOP_ATR = 1.5


def five_minute_atr(bars, n=14):
    """Wilder ATR of complete 5-minute bars built from completed 1-minute bars; None under three ranges."""
    groups = []
    for b in bars[-300:]:
        if groups and b['t'] // 300 == groups[-1]['t'] // 300:
            g = groups[-1]
            g.update(h=max(g['h'], b['h']), l=min(g['l'], b['l']), c=b['c'], n=g['n'] + 1)
        else:
            groups.append({'t': b['t'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'n': 1})
    groups = [g for g in groups if g['n'] == 5]
    ranges = [max(b['h'] - b['l'], abs(b['h'] - a['c']), abs(b['l'] - a['c'])) for a, b in zip(groups, groups[1:])]
    if len(ranges) < 3:
        return None
    value = sum(ranges[:n]) / len(ranges[:n])
    for r in ranges[n:]:
        value = (value * (n - 1) + r) / n
    return value


def volatility_stop(bars, side, entry, stop):
    """Widen a structural stop that sits inside normal 5-minute noise; never tighten one. None without enough bars."""
    atr = five_minute_atr(bars)
    if atr is None:
        return None
    floor = VOLATILITY_STOP_ATR * atr
    if abs(entry - stop) >= floor:
        return stop
    return entry - floor if side == 'long' else entry + floor


# Set by the user on 2026-11-02 for the paper/practice bots: take as many trades a day as qualify, and stop
# for the day only after losing 10% of the day's starting equity. MAX_ENTRIES_DAY only guards against a
# runaway loop; per-trade risk (0.25%) and total open risk (0.5%) are unchanged.
DAILY_LOSS_LIMIT = Decimal('.10')
MAX_ENTRIES_DAY = 40
# Take-profit in multiples of the risk, net of costs. Was 2R; on 2026-11-02 the user asked for realistic
# targets: with volatility-floored stops, 2R+ targets (18-30 pips) were rarely reached inside a session.
# Liquidity-based targets keep their level but must offer at least this much.
TARGET_R = Decimal('1.5')
# Watched breakeven (user 2026-11-03): once a trade is BREAKEVEN_AT_R in profit, Ano closes it if price comes back
# to entry plus BREAKEVEN_LOCK_R of the risk (covering costs). The broker's original stop stays as the backstop.
BREAKEVEN_AT_R = Decimal('1')
BREAKEVEN_LOCK_R = Decimal('.1')


def breakeven(long, entry, stop, mark, armed_level=None):
    """('arm', level) when price first reaches 1R in profit, ('exit', level) when an armed trade comes back to its
    level, else (None, armed_level). Prices are Decimals; mark is the side the position would close on."""
    entry, stop, mark = number(entry), number(stop), number(mark)
    risk = abs(entry - stop)
    if risk <= 0:
        return None, armed_level
    if armed_level is not None:
        level = number(armed_level)
        return ('exit', level) if (mark <= level if long else mark >= level) else (None, level)
    if (mark - entry if long else entry - mark) >= risk * BREAKEVEN_AT_R:
        return 'arm', entry + risk * BREAKEVEN_LOCK_R if long else entry - risk * BREAKEVEN_LOCK_R
    return None, None


def size_paper_trade(*, equity, session_equity, daily_pnl, existing_risk,
                     entries, consecutive_losses, side, entry, stop, target,
                     point_value, fx_rate, tick_size, minimum_quantity,
                     quantity_increment, round_trip_cost_per_unit,
                     fixed_round_trip_cost=0, minimum_reward_risk=TARGET_R):
    """Return a risk check using verified caller-supplied values, all costs in USD.

    existing_risk includes all open positions and pending entries across symbols.
    daily_pnl includes realized and unrealized P&L net of costs. No data is fetched.
    """
    values = {key: number(value) for key, value in locals().copy().items()
              if key not in ('side', 'entries', 'consecutive_losses', 'minimum_reward_risk')}
    minimum = number(minimum_reward_risk)  # TARGET_R by default; the v1.2 gap fill uses its own, lower minimum
    if side not in ('buy', 'sell'):
        raise ValueError('Side must be buy or sell.')
    for value in (entries, consecutive_losses):
        if type(value) is not int or value < 0:
            raise ValueError('Trade counts must be nonnegative integers.')
    for key, value in values.items():
        if key == 'daily_pnl':
            continue
        if key in ('existing_risk', 'round_trip_cost_per_unit', 'fixed_round_trip_cost'):
            if value < 0:
                raise ValueError(f'{key} cannot be negative.')
        elif value <= 0:
            raise ValueError(f'{key} must be positive.')
    v = values
    reasons = []
    if entries >= MAX_ENTRIES_DAY:
        reasons.append(f'{MAX_ENTRIES_DAY}-entry daily safety limit reached.')
    if consecutive_losses >= 2:
        reasons.append('Two consecutive losses reached.')
    flatten = v['daily_pnl'] <= -v['session_equity'] * DAILY_LOSS_LIMIT
    if flatten:
        reasons.append('Daily loss limit reached: cancel entries and flatten paper exposure.')
    e, s, t = (v[k] for k in ('entry', 'stop', 'target'))
    if not (s < e < t if side == 'buy' else t < e < s):
        raise ValueError('Stop and target must be on the correct sides of entry.')
    for price in (e, s, t):
        if price % v['tick_size']:
            raise ValueError('Prices must align with the verified tick size.')
    unit_risk = abs(e - s) * v['point_value'] * v['fx_rate'] + v['round_trip_cost_per_unit']
    unit_reward = abs(t - e) * v['point_value'] * v['fx_rate'] - v['round_trip_cost_per_unit']
    # Respect daily loss headroom as well as per-trade and aggregate budgets.
    budget = min(v['equity'] * Decimal('.0025'),
                 v['equity'] * Decimal('.005') - v['existing_risk'],
                 v['session_equity'] * DAILY_LOSS_LIMIT + min(v['daily_pnl'], Decimal(0))
                 - v['existing_risk'])
    available = max(Decimal(0), budget - v['fixed_round_trip_cost'])
    quantity = (available / unit_risk / v['quantity_increment']).to_integral_value(
        rounding=ROUND_FLOOR) * v['quantity_increment']
    risk = quantity * unit_risk + v['fixed_round_trip_cost']
    reward = quantity * unit_reward - v['fixed_round_trip_cost']
    if quantity <= 0 or quantity < v['minimum_quantity']:
        reasons.append('Minimum quantity exceeds the remaining risk budget.')
    if risk <= 0 or reward < risk * minimum:
        reasons.append(f'Net planned reward-to-risk is below {minimum}:1.')
    return {'risk_check_passed': not reasons, 'reasons': reasons,
            'quantity': str(quantity if not reasons else Decimal(0)),
            'candidate_quantity': str(quantity), 'planned_loss_usd': str(risk),
            'planned_reward_usd': str(reward), 'flatten_required': flatten,
            'execution_authorized': False}
