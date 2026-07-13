#!/usr/bin/env python3
"""
daily_morning_scan.py — Local TIP morning scan (Mac, TCC-safe under ~/.schwab/bin).
Rebuilt 2026-06 around the CSP/CC engine. Replaces the OCI-VM version lost on retirement.

Pipeline:
  0. Token health check (reads ~/.schwab/REAUTH_NEEDED.flag; aborts clean if re-auth due)
  1. Live Schwab pull: balances + positions (accts[1] active trading)
  2. Book triage by DTE/expiration urgency (short options first)
  3. Earnings hard-stop filter from scanner.db earnings_dates table (7-day window)
  4. CC ladder on covered equity (15-22% OTM, 25-50 DTE), earnings-clear only
  5. Broad CSP scan ranked by raw margin annualized yield
  6. Opportunity model reconciles raw rank against assignment quality, portfolio fit, and risk
  7. Persist daily_plan + tip_opportunities rows; write human-readable report to ~/.schwab/reports/

Run by launchd ~6:00 AM PT weekdays. Exit 0 ok, 2 reauth-needed, 1 error.
"""
import os, sys, json, sqlite3, base64
from pathlib import Path
from datetime import datetime, date, timedelta

HOME = Path.home()
BIN = HOME / ".schwab" / "bin"
sys.path.insert(0, str(BIN))   # import bundled modules from deploy dir

TOKEN_PATH = HOME / ".schwab" / "tokens.json"
WARN_PATH = HOME / ".schwab" / "REAUTH_NEEDED.flag"
DB = str(HOME / ".schwab" / "scanner.db")  # canonical, TCC-safe
REPORTS = HOME / ".schwab" / "reports"
LOG = HOME / ".schwab" / "logs" / "morning_scan.log"

# Credentials must come from environment variables. Do not commit real app keys.
APP_KEY = os.environ.get("SCHWAB_CLIENT_ID", "")
APP_SECRET = os.environ.get("SCHWAB_CLIENT_SECRET", "")
if not APP_KEY or not APP_SECRET:
    raise RuntimeError("Set SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET environment variables")

EARN_BUFFER_DAYS = 7
NEAR_POST_EXP_DAYS = 21   # earnings within N days AFTER expiry => IV contaminated
IV_CEILING = 65.0         # abs IV above this at 25-50 DTE on a large cap => almost always event-driven
TODAY = date.today()

def log(m):
    ts = datetime.now().isoformat(timespec='seconds')
    line = f"[{ts}] {m}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'a') as f: f.write(line+"\n")
    except Exception: pass

# ---------------------------------------------------------------------------
# Earnings filter — authoritative source is scanner.db earnings_dates table
# ---------------------------------------------------------------------------
def load_earnings(db):
    """Return {sym: (date, confirmed, stale_bool)} from earnings_dates."""
    out = {}
    try:
        c = sqlite3.connect(db); cur = c.cursor()
        for sym, nd, conf, upd in cur.execute(
                "SELECT symbol,next_date,confirmed,updated_at FROM earnings_dates"):
            try:
                d = datetime.strptime(nd[:10], '%Y-%m-%d').date()
            except Exception:
                continue
            stale = False
            try:
                age = (datetime.now() - datetime.fromisoformat(upd)).days
                stale = age > 10
            except Exception:
                stale = True
            out[sym] = (d, bool(conf), stale)
        c.close()
    except Exception as e:
        log(f"WARN load_earnings failed: {e}")
    return out

def earnings_state(sym, exp_date, earn):
    """Return (state, note). state in {'CLEAR','BLOCK','NEAR','UNKNOWN'}."""
    rec = earn.get(sym)
    if rec is None:
        return 'UNKNOWN', 'no earnings data'
    d, conf, stale = rec
    tag = ('conf' if conf else 'est') + (',STALE' if stale else '')
    # roll a past date forward one quarter (data not refreshed yet)
    while d < TODAY:
        d = d + timedelta(days=91)
    days = (d - TODAY).days
    if 0 <= days <= EARN_BUFFER_DAYS:
        return 'BLOCK', f'earnings {d} in {days}d [{tag}]'
    if TODAY <= d <= exp_date:
        return 'BLOCK', f'earnings {d} pre-exp [{tag}]'
    post = (d - exp_date).days
    if 0 < post <= NEAR_POST_EXP_DAYS:
        return 'NEAR', f'earnings {d} ({post}d post-exp, IV hot) [{tag}]'
    return 'CLEAR', f'clear, next {d} [{tag}]'

# ---------------------------------------------------------------------------
# Client construction (reuses on-disk token; keepalive keeps it fresh)
# ---------------------------------------------------------------------------
def make_client():
    from schwab_client import SchwabClient
    cl = SchwabClient(client_id=APP_KEY, client_secret=APP_SECRET,
                      callback_url="https://127.0.0.1")
    return cl

def reg_t_bp(strike, spot, prem_ps):
    otm_amt = max(spot - strike, 0)
    return max(0.20*spot - otm_amt + prem_ps, 0.10*strike + prem_ps) * 100

# ---------------------------------------------------------------------------
# Book triage — classify open option positions by urgency
# ---------------------------------------------------------------------------
def triage_book(opt_positions):
    """opt_positions: list of (sym, net, avg, mv, parsed) from parse_book.build."""
    tiers = {'EXPIRING_THIS_WEEK': [], 'NEXT_WEEK': [], 'LATER': [], 'LONG': []}
    for sym, net, avg, mv, pp in opt_positions:
        if not pp:
            continue
        und, exp, cp, strike = pp
        try:
            dte = (datetime.strptime(exp, '%Y-%m-%d').date() - TODAY).days
        except Exception:
            dte = 999
        rec = {'sym': sym, 'und': und, 'cp': cp, 'strike': strike,
               'exp': exp, 'dte': dte, 'net': net, 'mv': mv, 'short': net < 0}
        if net < 0 and dte <= 7:
            tiers['EXPIRING_THIS_WEEK'].append(rec)
        elif net < 0 and dte <= 14:
            tiers['NEXT_WEEK'].append(rec)
        elif net < 0:
            tiers['LATER'].append(rec)
        else:
            tiers['LONG'].append(rec)
    for k in tiers:
        tiers[k].sort(key=lambda r: r['dte'])
    return tiers

# ---------------------------------------------------------------------------
# CC ladder — covered calls on equity holdings >= 100 sh, earnings-clear
# NVDA special rule: never recommend strikes that risk assignment; flag only.
# ---------------------------------------------------------------------------
NVDA_PROTECT = True

def cc_ladder(cl, equities, earn, quotes):
    import parse_book
    out = []
    for sym, net, avg, mv in equities:
        shares = int(net)
        if shares < 100:
            continue
        spot = quotes.get(sym)
        if not spot:
            continue
        cands = parse_book.cc_candidates(cl, sym, spot, otm_lo=0.15, otm_hi=0.22,
                                         dte_lo=25, dte_hi=50)
        if isinstance(cands, dict) and 'error' in cands:
            continue
        for c in cands:
            exp_d = datetime.strptime(c['exp'][:10], '%Y-%m-%d').date()
            st, note = earnings_state(sym, exp_d, earn)
            if st in ('BLOCK',):
                continue
            ivv = c.get('iv') or 0
            if st == 'CLEAR' and ivv >= IV_CEILING:
                st = 'NEAR'
                note = f'IV {ivv:.0f} >= ceiling, likely event-driven; {note}'
            bid = c.get('bid') or 0
            if bid < 0.10:
                continue
            contracts = shares // 100
            prem = bid * 100 * contracts
            c.update({'sym': sym, 'contracts': contracts, 'spot': spot,
                      'cost_basis': avg, 'premium_total': round(prem, 0),
                      'earn_state': st, 'earn_note': note,
                      'nvda_flag': (sym == 'NVDA' and NVDA_PROTECT)})
            out.append(c)
    # prioritize: clear before near, then higher premium
    out.sort(key=lambda r: (0 if r['earn_state'] == 'CLEAR' else 1, -r['premium_total']))
    return out

# ---------------------------------------------------------------------------
# CSP scan — margin-based annualized yield, earnings-clear, ranked
# ---------------------------------------------------------------------------
DEFAULT_UNIVERSE = [
    'NVDA','AMD','MSFT','CRM','ORCL','AMZN','AVGO','QCOM',
    'LLY','UNH','ABBV','ISRG','JNJ','MRK','AMGN','GILD',
    'LMT','RTX','GE','NOC','GD','CAT',
    'CVX','XOM','CEG','VST','COP',
    'JPM','GS','BAC','MS','SCHW','BLK',
    'COST','WMT','HD','MCD',
]

def scan_universe(extra=None):
    """Broad optionable universe for discovery; includes holdings and legacy list."""
    try:
        from tip_opportunity_model import broad_option_universe
        return broad_option_universe(set(DEFAULT_UNIVERSE) | set(extra or []))
    except Exception:
        symbols = set(DEFAULT_UNIVERSE)
        symbols.update(extra or [])
        return sorted(symbols)

def csp_scan(cl, earn, universe=None):
    import csp_scanner
    universe = universe or scan_universe()
    rows = []
    for s in universe:
        try:
            res = csp_scanner.csp_candidates(cl, s, otm_lo=0.03, otm_hi=0.15,
                                             dte_lo=25, dte_hi=50)
        except Exception:
            continue
        if isinstance(res, dict):
            continue
        for r in res:
            if (r.get('oi') or 0) < 200 or (r.get('bid') or 0) < 0.10:
                continue
            exp_d = datetime.strptime(r['exp'][:10], '%Y-%m-%d').date()
            st, note = earnings_state(s, exp_d, earn)
            if st == 'BLOCK':
                continue
            # IV-ceiling cross-check: high abs IV at this DTE on a large cap is
            # almost always event-driven even if earnings fall outside the window.
            iv = r.get('iv') or 0
            if st == 'CLEAR' and iv >= IV_CEILING:
                st = 'NEAR'
                note = f'IV {iv:.0f} >= ceiling {IV_CEILING:.0f}, likely event-driven; {note}'
            bp = reg_t_bp(r['strike'], r['spot'], r['bid'])
            r['bp_reduction'] = round(bp, 0)
            r['notional'] = round(r['strike']*100, 0)
            r['margin_ann_yield'] = round((r['premium_100']/bp)*(365/max(r['dte'],1))*100, 1) if bp > 0 else 0
            r['earn_state'] = st; r['earn_note'] = note
            rows.append(r)
    rows.sort(key=lambda r: (0 if r['earn_state']=='CLEAR' else 1, -r['margin_ann_yield']))
    return rows

# ---------------------------------------------------------------------------
# Persistence — daily_plan table in scanner.db
# ---------------------------------------------------------------------------
def ensure_tables(db):
    c = sqlite3.connect(db); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS daily_plan (
        scan_date TEXT, kind TEXT, sym TEXT, exp TEXT, dte INTEGER,
        strike REAL, otm_pct REAL, bid REAL, delta REAL, iv REAL,
        premium REAL, bp_reduction REAL, notional REAL, ann_yield REAL,
        earn_state TEXT, earn_note TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS scan_runs (
        scan_date TEXT, started_at TEXT, finished_at TEXT,
        nlv REAL, avail_funds REAL, maint_req REAL, margin_bal REAL,
        n_cc INTEGER, n_csp INTEGER, status TEXT)""")
    c.commit(); c.close()

def persist(db, cc, csp):
    c = sqlite3.connect(db); cur = c.cursor()
    sd = TODAY.isoformat(); now = datetime.now().isoformat()
    cur.execute("DELETE FROM daily_plan WHERE scan_date=?", (sd,))
    for r in cc[:40]:
        cur.execute("""INSERT INTO daily_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sd,'CC',r['sym'],r['exp'],r['dte'],r['strike'],r.get('otm_pct'),
             r.get('bid'),r.get('delta'),r.get('iv'),r.get('premium_total'),
             None,None,None,r['earn_state'],r['earn_note'],now))
    for r in csp[:60]:
        cur.execute("""INSERT INTO daily_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sd,'CSP',r['sym'],r['exp'],r['dte'],r['strike'],r.get('otm_pct'),
             r.get('bid'),r.get('delta'),r.get('iv'),r.get('premium_100'),
             r.get('bp_reduction'),r.get('notional'),r.get('margin_ann_yield'),
             r['earn_state'],r['earn_note'],now))
    c.commit(); c.close()

# ---------------------------------------------------------------------------
# Opportunity model + report writer
# ---------------------------------------------------------------------------
def build_opportunity_layer(cc, csp, equities, bal):
    try:
        from tip_opportunity_model import build_opportunities
        nlv = float(bal.get('liquidationValue') or 0)
        return build_opportunities(cc, csp, equities, nlv, Path(DB), TODAY.isoformat())
    except Exception as e:
        log(f"WARN opportunity model failed: {e}")
        return []

def diversified_opportunities(rows, limit=30, max_per_symbol=1):
    picked = []
    counts = {}
    for r in rows:
        sym = getattr(r, 'symbol', None)
        if counts.get(sym, 0) >= max_per_symbol:
            continue
        picked.append(r)
        counts[sym] = counts.get(sym, 0) + 1
        if len(picked) >= limit:
            break
    return picked

def write_report(bal, tiers, cc, csp, opportunities=None):
    opportunities = opportunities or []
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"morning_{TODAY.isoformat()}.md"
    L = []
    L.append(f"# Morning Scan — {TODAY.isoformat()}\n")
    L.append(f"NLV ${bal.get('liquidationValue',0):,.0f} | "
             f"AvailFunds ${bal.get('availableFunds',0):,.0f} | "
             f"MaintReq ${bal.get('maintenanceRequirement',0):,.0f} | "
             f"MarginBal ${bal.get('marginBalance',0):,.0f}\n")
    L.append("\n## Book triage\n")
    for tier in ['EXPIRING_THIS_WEEK','NEXT_WEEK','LATER','LONG']:
        recs = tiers.get(tier, [])
        if not recs: continue
        L.append(f"\n**{tier}** ({len(recs)})")
        for r in recs:
            L.append(f"- {r['sym']} {r['cp']} K{r['strike']:.0f} {r['dte']}DTE "
                     f"{'SHORT' if r['short'] else 'long'} mv${r['mv']:,.0f}")
    L.append("\n## CC ladder (earnings-clear)\n")
    for r in cc[:15]:
        flag = " ⚠NVDA-PROTECT" if r.get('nvda_flag') else ""
        L.append(f"- {r['sym']} {r['exp']} {r['dte']}d K{r['strike']:.0f} "
                 f"{r['otm_pct']:.0f}%OTM x{r['contracts']} prem${r['premium_total']:,.0f} "
                 f"[{r['earn_state']}]{flag}")
    if opportunities:
        L.append("\n## Underwritten opportunity board\n")
        L.append("Machine rank is reconciled against assignment quality, portfolio fit, risk, and data confidence.")
        for r in diversified_opportunities(opportunities, 30, 1):
            prem = f" prem${r.premium:,.0f}" if getattr(r, 'premium', 0) else ""
            yld = f" yld{r.ann_yield:.0f}%" if getattr(r, 'ann_yield', 0) else ""
            exp = f" {r.expiry} {r.dte}d" if getattr(r, 'expiry', '') else ""
            L.append(
                f"- {r.label} {r.score:02d} {r.sleeve}: {r.symbol} {r.strategy} "
                f"{r.structure}{exp}{prem}{yld} | {r.assignment_view} "
                f"Alt: {r.alternative}."
            )

    L.append("\n## Raw CSP candidates (ranked by margin ann. yield)\n")
    for r in csp[:25]:
        L.append(f"- {r['sym']} {r['exp']} {r['dte']}d K{r['strike']:.0f} "
                 f"{r['otm_pct']:.0f}%OTM d{abs(r.get('delta') or 0):.2f} iv{r.get('iv') or 0:.0f} "
                 f"prem${r['premium_100']:,.0f} BP${r['bp_reduction']:,.0f} "
                 f"yld{r['margin_ann_yield']:.0f}% notl${r['notional']:,.0f} [{r['earn_state']}]")
    p.write_text("\n".join(L))
    return p

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    started = datetime.now().isoformat()
    log("=== morning scan start ===")

    # 0. token health
    if WARN_PATH.exists():
        log(f"ABORT: REAUTH flag present -> {WARN_PATH.read_text().strip()}")
        sys.exit(2)

    import parse_book
    ensure_tables(DB)
    earn = load_earnings(DB)
    log(f"loaded {len(earn)} earnings dates")

    cl = make_client()
    accts = cl.get_account_numbers()
    thash = accts[1]['hashValue']
    acct = cl.get_account(thash, include_positions=True)
    sa = acct.get('securitiesAccount', acct)
    bal = sa.get('currentBalances', {})
    positions = sa.get('positions', [])
    log(f"balances + {len(positions)} positions pulled")

    eq, opt = parse_book.build(positions)
    tiers = triage_book(opt)

    # batch quotes for equities
    eqsyms = [s for s,_,_,_ in eq]
    quotes = {}
    try:
        q = cl.get_quotes(eqsyms)
        for s in eqsyms:
            quotes[s] = q.get(s, {}).get('quote', {}).get('mark')
    except Exception as e:
        log(f"WARN quotes failed: {e}")

    cc = cc_ladder(cl, eq, earn, quotes)
    log(f"CC ladder: {len(cc)} earnings-clear candidates")
    universe = scan_universe(eqsyms)
    log(f"CSP universe: {len(universe)} symbols")
    csp = csp_scan(cl, earn, universe=universe)
    log(f"CSP scan: {len(csp)} earnings-screened candidates")

    persist(DB, cc, csp)
    opportunities = build_opportunity_layer(cc, csp, eq, bal)
    log(f"Opportunity model: {len(opportunities)} underwritten ideas")
    rpt = write_report(bal, tiers, cc, csp, opportunities)
    log(f"report -> {rpt}")

    # scan_runs row
    c = sqlite3.connect(DB); cur = c.cursor()
    cur.execute("INSERT INTO scan_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TODAY.isoformat(), started, datetime.now().isoformat(),
         bal.get('liquidationValue'), bal.get('availableFunds'),
         bal.get('maintenanceRequirement'), bal.get('marginBalance'),
         len(cc), len(csp), 'OK'))
    c.commit(); c.close()
    log("=== morning scan done ===")
    sys.exit(0)

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        log(f"FATAL: {e}\n{traceback.format_exc()}")
        sys.exit(1)
