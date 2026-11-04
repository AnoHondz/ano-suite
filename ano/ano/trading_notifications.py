"""User-selected notifications: verified entry fills and the session summary, said in plain words."""
import re

def should_notify(message):
    if message.startswith('Paper session summary '):return True
    # Entry receipts only; a submitted/acknowledged order is not a taken trade.
    return bool(re.search(r'\bFILLED at\b',message) and '(verified;' in message)


# Strategy ids in the journal -> the names the user knows them by
SETUPS={'gap fill':'gap fill','gap_fill_v1_2':'gap fill','ict_2022_v3':'ICT','smc_retest_v1':'SMC retest',
        'silver_bullet_v1':'Silver Bullet','turtle_soup_intraday_v1':'Turtle Soup','amd_ict_v1':'AMD',
        'opening_range_v1':'opening range','ema_cross_v1':'moving-average crossover'}
STOCK_FILL=re.compile(r'(?P<symbol>[A-Z.]+) (?P<setup>[\w ]+?): (?P<qty>[\d.]+) shares(?: (?P<side>long|short))? FILLED at (?P<price>[\d.]+) \(verified; order \w+\)\.')
STOCK_CLOSE=re.compile(r'(?P<symbol>[A-Z.]+) (?P<setup>[\w ]+?): CLOSED by its (?P<how>target|stop) at (?P<price>[\d.]+) \(verified\)\.')
FX_FILL=re.compile(r'(?P<pair>[A-Z]{3})_(?P<quote>[A-Z]{3}) / (?P<setup>[\w ]+?) \[(?P<session>\w+)\]: (?P<units>[\d.]+) units (?P<side>LONG|SHORT) '
                   r'FILLED at (?P<price>[\d.]+) \(verified; OANDA demo trade \w+\)\.')


def setup_name(name):
    return SETUPS.get(name.strip(),name.strip().replace('_',' '))


def friendly(message):
    """A fill or close receipt as a sentence a person would say; any other message unchanged."""
    if m:=STOCK_FILL.fullmatch(message):
        qty,price=float(m['qty']),float(m['price'])
        did='sold short' if m['side']=='short' else 'bought'
        return (f'Paper trade opened: {did} {qty:g} {m["symbol"]} shares at ${price:,.2f} '
                f'(about ${qty*price:,.0f} of practice money), using the {setup_name(m["setup"])} setup. Alpaca confirmed it.')
    if m:=STOCK_CLOSE.fullmatch(message):
        how='hit its profit target' if m['how']=='target' else 'hit its stop-loss'
        return f'Paper trade closed: the {m["symbol"]} {setup_name(m["setup"])} trade {how} at ${float(m["price"]):,.2f}.'
    if m:=FX_FILL.fullmatch(message):
        did='bought' if m['side']=='LONG' else 'sold'
        return (f'Forex practice trade opened: {did} {float(m["units"]):,.0f} units of {m["pair"]}/{m["quote"]} at '
                f'{float(m["price"]):g}, using the {setup_name(m["setup"])} setup in the {m["session"].replace("_"," ").title()} session. OANDA confirmed it.')
    return message
