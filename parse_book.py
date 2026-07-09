import re, json
from datetime import datetime, date

def parse_occ(sym):
    m = re.search(r'(\d{6})([CP])(\d{8})', sym)
    if not m:
        return None
    yymmdd, cp, strike = m.groups()
    und = sym[:m.start()].strip()
    exp = "20" + yymmdd[:2] + "-" + yymmdd[2:4] + "-" + yymmdd[4:]
    return und, exp, cp, int(strike) / 1000.0

def build(positions):
    eq = []
    opt = []
    for p in positions:
        inst = p['instrument']
        sym = inst['symbol']
        atype = inst.get('assetType')
        lq = p.get('longQuantity', 0)
        sq = p.get('shortQuantity', 0)
        net = lq - sq
        avg = p.get('averagePrice', 0)
        mv = p.get('marketValue', 0)
        pp = parse_occ(sym)
        if atype == 'OPTION' or pp:
            opt.append((sym, net, avg, mv, pp))
        else:
            eq.append((sym, net, avg, mv))
    return eq, opt

def cc_candidates(client, sym, spot, otm_lo=0.15, otm_hi=0.22, dte_lo=25, dte_hi=50):
    """Pull call chain, return strikes in the OTM band within DTE window."""
    try:
        chain = client.get_option_chain(sym, contract_type='CALL', strike_count=60)
    except Exception as e:
        return {"error": str(e)[:120]}
    cmap = chain.get('callExpDateMap', {})
    out = []
    today = date.today()
    for expkey, strikes in cmap.items():
        # expkey like '2026-07-17:46'
        try:
            exp_str, dte = expkey.split(':')
            dte = int(dte)
        except Exception:
            continue
        if not (dte_lo <= dte <= dte_hi):
            continue
        for strike_str, contracts in strikes.items():
            k = float(strike_str)
            otm = (k - spot) / spot
            if otm_lo <= otm <= otm_hi:
                c = contracts[0]
                out.append({
                    'exp': exp_str, 'dte': dte, 'strike': k,
                    'otm_pct': round(otm * 100, 1),
                    'bid': c.get('bid'), 'ask': c.get('ask'), 'mark': c.get('mark'),
                    'delta': c.get('delta'), 'iv': c.get('volatility'),
                    'oi': c.get('openInterest'), 'vol': c.get('totalVolume'),
                    'theta': c.get('theta')
                })
    out.sort(key=lambda x: (x['dte'], x['strike']))
    return out
