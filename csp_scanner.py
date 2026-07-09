"""CSP Scanner - Cash-Secured Put opportunity finder"""
from datetime import date

def csp_candidates(client, sym, spot=None, otm_lo=0.03, otm_hi=0.15, dte_lo=25, dte_hi=50):
    """Pull put chain, return OTM puts within DTE window with annualized yield."""
    if spot is None:
        try:
            q = client.get_quotes([sym])
            spot = q[sym]['quote']['mark']
        except:
            return {"error": f"Could not get quote for {sym}"}
    try:
        chain = client.get_option_chain(sym, contract_type='PUT', strike_count=60)
    except Exception as e:
        return {"error": str(e)[:120]}
    pmap = chain.get('putExpDateMap', {})
    out = []
    for expkey, strikes in pmap.items():
        try:
            exp_str, dte = expkey.split(':')
            dte = int(dte)
        except:
            continue
        if not (dte_lo <= dte <= dte_hi):
            continue
        for strike_str, contracts in strikes.items():
            k = float(strike_str)
            otm = (spot - k) / spot
            if otm_lo <= otm <= otm_hi:
                c = contracts[0]
                bid = c.get('bid', 0) or 0
                # Cash required = strike * 100
                cash_req = k * 100
                premium_100 = bid * 100
                ann_yield = (premium_100 / cash_req) * (365 / max(dte, 1)) * 100 if cash_req > 0 else 0
                out.append({
                    'sym': sym, 'exp': exp_str, 'dte': dte, 'strike': k,
                    'otm_pct': round(otm * 100, 1),
                    'bid': bid, 'ask': c.get('ask'), 'mark': c.get('mark'),
                    'delta': c.get('delta'), 'iv': c.get('volatility'),
                    'oi': c.get('openInterest'), 'vol': c.get('totalVolume'),
                    'theta': c.get('theta'),
                    'cash_req': cash_req,
                    'premium_100': premium_100,
                    'ann_yield': round(ann_yield, 1),
                    'spot': spot
                })
    out.sort(key=lambda x: (-x['ann_yield'], x['dte'], x['strike']))
    return out
