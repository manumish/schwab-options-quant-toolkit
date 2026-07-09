#!/usr/bin/env python3
import os, json
from dotenv import load_dotenv
load_dotenv('credentials.env')
from schwab_client import SchwabClient

client = SchwabClient(os.getenv('SCHWAB_CLIENT_ID'), os.getenv('SCHWAB_CLIENT_SECRET'))
accts = client.get_account_numbers()

# Current prices
quotes = client.get_quotes(['ORCL', 'UNH'])
orcl_price = quotes['ORCL']['quote']['lastPrice']
unh_price = quotes['UNH']['quote']['lastPrice']
print(f"\n=== CURRENT PRICES ===")
print(f"ORCL: ${orcl_price}")
print(f"UNH:  ${unh_price}")

# ORCL Jan 2027 LEAPs
print(f"\n=== ORCL Jan 2027 LEAP CALLS (ITM strikes) ===")
orcl_chain = client.get_option_chain('ORCL', contract_type='CALL', strike_count=40)
jan27_key = [k for k in orcl_chain.get('callExpDateMap', {}).keys() if '2027-01' in k]
if jan27_key:
    orcl_calls = orcl_chain['callExpDateMap'][jan27_key[0]]
    print(f"Expiration: {jan27_key[0].split(':')[0]}")
    print(f"{'Strike':>8} {'Bid':>8} {'Ask':>8} {'Mid':>8} {'Delta':>7} {'IV%':>7} {'OI':>8} {'Intrinsic':>10} {'TimeVal':>8} {'TV%':>6}")
    print("-" * 95)
    for strike_str in sorted(orcl_calls.keys(), key=lambda x: float(x)):
        s = float(strike_str)
        if 90 <= s <= 145:
            c = orcl_calls[strike_str][0]
            mid = round((c['bid'] + c['ask']) / 2, 2)
            intrinsic = max(0, round(orcl_price - s, 2))
            tv = round(mid - intrinsic, 2)
            tv_pct = round(tv / mid * 100, 1) if mid > 0 else 0
            delta = c.get('delta', 0)
            iv = c.get('volatility', 0)
            oi = c.get('openInterest', 0)
            print(f"${s:>7.1f} ${c['bid']:>7.2f} ${c['ask']:>7.2f} ${mid:>7.2f} {delta:>7.3f} {iv:>6.1f}% {oi:>7} ${intrinsic:>8.2f} ${tv:>7.2f} {tv_pct:>5.1f}%")

# Also get Jun 2027 and Jan 2028 for comparison
for label, pattern in [('Jun 2027', '2027-06'), ('Jan 2028', '2028-01')]:
    keys = [k for k in orcl_chain.get('callExpDateMap', {}).keys() if pattern in k]
    if keys:
        calls = orcl_chain['callExpDateMap'][keys[0]]
        print(f"\n=== ORCL {label} LEAP CALLS (select ITM) ===")
        print(f"Expiration: {keys[0].split(':')[0]}")
        for target in ['100.0', '110.0', '120.0', '130.0', '140.0']:
            if target in calls:
                c = calls[target][0]
                mid = round((c['bid'] + c['ask']) / 2, 2)
                intrinsic = max(0, round(orcl_price - float(target), 2))
                tv = round(mid - intrinsic, 2)
                print(f"  ${target}C: Mid=${mid}, Delta={round(c.get('delta',0),3)}, IV={round(c.get('volatility',0),1)}%, OI={c.get('openInterest',0)}, TimeVal=${tv}")

# UNH LEAPs
print(f"\n=== UNH Jan 2027 LEAP CALLS (ITM strikes) ===")
unh_chain = client.get_option_chain('UNH', contract_type='CALL', strike_count=40)
unh_jan27_key = [k for k in unh_chain.get('callExpDateMap', {}).keys() if '2027-01' in k]
if unh_jan27_key:
    unh_calls = unh_chain['callExpDateMap'][unh_jan27_key[0]]
    print(f"Expiration: {unh_jan27_key[0].split(':')[0]}")
    print(f"{'Strike':>8} {'Bid':>8} {'Ask':>8} {'Mid':>8} {'Delta':>7} {'IV%':>7} {'OI':>8} {'Intrinsic':>10} {'TimeVal':>8} {'TV%':>6}")
    print("-" * 95)
    for strike_str in sorted(unh_calls.keys(), key=lambda x: float(x)):
        s = float(strike_str)
        if 180 <= s <= 295:
            c = unh_calls[strike_str][0]
            mid = round((c['bid'] + c['ask']) / 2, 2)
            intrinsic = max(0, round(unh_price - s, 2))
            tv = round(mid - intrinsic, 2)
            tv_pct = round(tv / mid * 100, 1) if mid > 0 else 0
            delta = c.get('delta', 0)
            iv = c.get('volatility', 0)
            oi = c.get('openInterest', 0)
            print(f"${s:>7.1f} ${c['bid']:>7.2f} ${c['ask']:>7.2f} ${mid:>7.2f} {delta:>7.3f} {iv:>6.1f}% {oi:>7} ${intrinsic:>8.2f} ${tv:>7.2f} {tv_pct:>5.1f}%")

# UNH longer-dated
for label, pattern in [('Jun 2027', '2027-06'), ('Jan 2028', '2028-01')]:
    keys = [k for k in unh_chain.get('callExpDateMap', {}).keys() if pattern in k]
    if keys:
        calls = unh_chain['callExpDateMap'][keys[0]]
        print(f"\n=== UNH {label} LEAP CALLS (select ITM) ===")
        for target in ['200.0', '220.0', '240.0', '260.0', '280.0']:
            if target in calls:
                c = calls[target][0]
                mid = round((c['bid'] + c['ask']) / 2, 2)
                intrinsic = max(0, round(unh_price - float(target), 2))
                tv = round(mid - intrinsic, 2)
                print(f"  ${target}C: Mid=${mid}, Delta={round(c.get('delta',0),3)}, IV={round(c.get('volatility',0),1)}%, OI={c.get('openInterest',0)}, TimeVal=${tv}")

# Check existing ORCL positions
print(f"\n=== EXISTING ORCL POSITIONS ===")
trading = client.get_account(accts[1]['hashValue'], include_positions=True)
positions = trading.get('securitiesAccount', {}).get('positions', [])
for p in positions:
    sym = p['instrument'].get('symbol', '')
    if 'ORCL' in sym or 'UNH' in sym:
        qty = p.get('longQuantity', 0) - p.get('shortQuantity', 0)
        avg = p.get('averagePrice', 0)
        mv = p.get('marketValue', 0)
        print(f"  {sym}: Qty={qty}, AvgPrice=${avg:.2f}, MktVal=${mv:.2f}")
