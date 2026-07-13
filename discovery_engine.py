"""
Discovery Engine — Find What You Can't See
============================================
Scans OUTSIDE your portfolio to find growth + income opportunities
in sectors you're underweight in. Fights confirmation bias.

Uses Schwab API:
  - /marketdata/v1/quotes?fields=quote,fundamental  (batch fundamentals)
  - /marketdata/v1/movers/{index}                    (momentum signals)
  - /marketdata/v1/chains                            (options liquidity)
  - /marketdata/v1/instruments?projection=fundamental (deep fundamentals)

Architecture:
  1. Analyze your portfolio → identify sector gaps
  2. Scan 200+ liquid, optionable stocks via Schwab fundamentals
  3. Score by: diversification benefit, growth, dividends, options premium
  4. Surface top discoveries with actionable context
"""

import sqlite3
import json
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path

DB_PATH = Path(__file__).parent / "scanner.db"


# ============================================================================
# DISCOVERY UNIVERSE — Broad, liquid, optionable stocks by sector
# These are NOT your holdings. These are what you SHOULD be looking at.
# ============================================================================

SECTOR_UNIVERSE = {
    'Healthcare': {
        'etf': 'XLV',
        'why': 'Recession-resistant, aging demographics, dividend growth',
        'stocks': [
            'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'TMO', 'ABT', 'PFE',
            'AMGN', 'MDT', 'ISRG', 'GILD', 'VRTX', 'BSX', 'SYK',
            'CI', 'ELV', 'HCA', 'ZTS', 'REGN', 'BDX', 'CVS',
            'HUM', 'IQV', 'BIIB', 'IDXX',
        ],
    },
    'Defense & Aerospace': {
        'etf': 'ITA',
        'why': 'Geopolitical tailwinds, government spending, long-cycle contracts',
        'stocks': [
            'RTX', 'LMT', 'GD', 'NOC', 'BA', 'LHX', 'TDG',
            'HII', 'LDOS', 'AXON', 'HEI', 'PLTR',
        ],
    },
    'Energy & Utilities': {
        'etf': 'XLE',
        'why': 'AI power demand, nuclear renaissance, energy security',
        'stocks': [
            'NEE', 'SO', 'DUK', 'CEG', 'VST', 'AEP', 'D', 'SRE',
            'XEL', 'ED', 'EXC', 'PCG',
            'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'MPC', 'PSX',
            'OXY', 'WMB', 'KMI', 'OKE', 'TRGP',
        ],
    },
    'Consumer Staples': {
        'etf': 'XLP',
        'why': 'Recession-proof, pricing power, dividend aristocrats',
        'stocks': [
            'PG', 'KO', 'PEP', 'PM', 'MO', 'CL', 'MDLZ', 'GIS',
            'KMB', 'STZ', 'WMT', 'TGT', 'COST', 'KR', 'SYY',
        ],
    },
    'Financials': {
        'etf': 'XLF',
        'why': 'Rate cycle beneficiary, buybacks, capital return',
        'stocks': [
            'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'SCHW',
            'BLK', 'SPGI', 'ICE', 'CME', 'CB', 'MET',
            'PGR', 'ALL', 'AXP', 'COF', 'MMC', 'AON',
        ],
    },
    'Industrials': {
        'etf': 'XLI',
        'why': 'Infrastructure spending, reshoring, automation',
        'stocks': [
            'CAT', 'DE', 'UNP', 'HON', 'GE', 'EMR', 'ITW',
            'ROK', 'PH', 'ETN', 'WM', 'RSG', 'UBER', 'FDX',
        ],
    },
    'Real Estate': {
        'etf': 'XLRE',
        'why': 'Income stream, data center demand, rate sensitivity',
        'stocks': [
            'PLD', 'AMT', 'EQIX', 'CCI', 'PSA', 'O', 'SPG',
            'WELL', 'DLR', 'VICI',
        ],
    },
    'Materials': {
        'etf': 'XLB',
        'why': 'Inflation hedge, infrastructure, commodity cycle',
        'stocks': [
            'LIN', 'APD', 'SHW', 'ECL', 'FCX', 'NEM', 'NUE',
            'DOW', 'VMC', 'MLM',
        ],
    },
}

# Map every stock in universe to its sector
STOCK_SECTOR_MAP = {
    # Tech (user's existing heavy allocation)
    'MSFT': 'Technology', 'NVDA': 'Technology', 'ORCL': 'Technology',
    'AMD': 'Technology', 'AMZN': 'Technology', 'TSLA': 'Technology',
    'AAPL': 'Technology', 'CRDO': 'Technology', 'INTC': 'Technology',
    'COST': 'Consumer Staples',
}
for _sector, _info in SECTOR_UNIVERSE.items():
    for _s in _info['stocks']:
        STOCK_SECTOR_MAP[_s] = _sector


# Balanced target allocation — still tech-heavy (your edge) but diversified
TARGET_ALLOCATION = {
    'Technology': 55.0,
    'Healthcare': 10.0,
    'Defense & Aerospace': 7.0,
    'Energy & Utilities': 7.0,
    'Financials': 6.0,
    'Consumer Staples': 5.0,
    'Industrials': 4.0,
    'Real Estate': 3.0,
    'Materials': 2.0,
    'Other': 1.0,
}


@dataclass
class DiscoveryCandidate:
    symbol: str
    sector: str
    price: float = 0
    market_cap_b: float = 0          # billions
    dividend_yield: float = 0         # %
    pe_ratio: float = 0
    change_52w: float = 0             # 52-week % change
    avg_volume_m: float = 0           # avg daily volume in millions
    # Schwab fundamental fields
    pb_ratio: float = 0               # price/book
    net_margin: float = 0             # net profit margin %
    roe: float = 0                    # return on equity %
    revenue_growth: float = 0         # revenue growth %
    # Options data
    atm_put_iv: float = 0             # ATM put implied vol %
    atm_put_premium_pct: float = 0    # premium as % of strike
    atm_put_annualized: float = 0     # annualized yield %
    options_liquid: bool = False       # has liquid options
    # Analyst
    analyst_target: float = 0
    analyst_rating: str = ''
    analyst_upside: float = 0         # %
    # Scoring
    diversification_score: float = 0
    growth_score: float = 0
    income_score: float = 0
    total_score: float = 0
    headline: str = ''
    why: str = ''


@dataclass
class PortfolioGap:
    sector: str
    current_pct: float
    target_pct: float
    gap_pct: float
    top_picks: List[str] = field(default_factory=list)


# ============================================================================
# DATABASE
# ============================================================================

def _init_discovery_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS discovery_candidates (
            symbol TEXT PRIMARY KEY,
            sector TEXT, price REAL, market_cap_b REAL, dividend_yield REAL,
            pe_ratio REAL, change_52w REAL, avg_volume_m REAL,
            pb_ratio REAL, net_margin REAL, roe REAL, revenue_growth REAL,
            atm_put_iv REAL, atm_put_premium_pct REAL, atm_put_annualized REAL,
            options_liquid INTEGER,
            analyst_target REAL, analyst_rating TEXT, analyst_upside REAL,
            diversification_score REAL, growth_score REAL, income_score REAL,
            total_score REAL, headline TEXT, why TEXT,
            updated TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS discovery_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, sectors_scanned INTEGER, candidates_found INTEGER,
            top_picks TEXT, portfolio_json TEXT, gaps_json TEXT, trigger TEXT
        )
    ''')
    conn.commit()
    conn.close()

_init_discovery_db()


def _save_candidate(c: DiscoveryCandidate):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''
        INSERT OR REPLACE INTO discovery_candidates
        (symbol, sector, price, market_cap_b, dividend_yield, pe_ratio,
         change_52w, avg_volume_m, pb_ratio, net_margin, roe, revenue_growth,
         atm_put_iv, atm_put_premium_pct, atm_put_annualized, options_liquid,
         analyst_target, analyst_rating, analyst_upside,
         diversification_score, growth_score, income_score, total_score,
         headline, why, updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (c.symbol, c.sector, c.price, c.market_cap_b, c.dividend_yield, c.pe_ratio,
          c.change_52w, c.avg_volume_m, c.pb_ratio, c.net_margin, c.roe, c.revenue_growth,
          c.atm_put_iv, c.atm_put_premium_pct, c.atm_put_annualized, int(c.options_liquid),
          c.analyst_target, c.analyst_rating, c.analyst_upside,
          c.diversification_score, c.growth_score, c.income_score, c.total_score,
          c.headline, c.why, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def load_discovery_results() -> dict:
    """Load latest discovery scan results for dashboard API"""
    conn = sqlite3.connect(str(DB_PATH))

    # Get candidates
    cursor = conn.execute('''
        SELECT symbol, sector, price, market_cap_b, dividend_yield, pe_ratio,
               change_52w, avg_volume_m, pb_ratio, net_margin, roe, revenue_growth,
               atm_put_iv, atm_put_premium_pct, atm_put_annualized, options_liquid,
               analyst_target, analyst_rating, analyst_upside,
               diversification_score, growth_score, income_score, total_score,
               headline, why, updated
        FROM discovery_candidates
        ORDER BY total_score DESC
        LIMIT 50
    ''')
    candidates = []
    for row in cursor.fetchall():
        candidates.append({
            'symbol': row[0], 'sector': row[1], 'price': row[2],
            'market_cap_b': row[3], 'dividend_yield': row[4], 'pe_ratio': row[5],
            'change_52w': row[6], 'avg_volume_m': row[7],
            'pb_ratio': row[8], 'net_margin': row[9], 'roe': row[10],
            'revenue_growth': row[11],
            'atm_put_iv': row[12], 'atm_put_premium_pct': row[13],
            'atm_put_annualized': row[14], 'options_liquid': bool(row[15]),
            'analyst_target': row[16], 'analyst_rating': row[17],
            'analyst_upside': row[18],
            'diversification_score': row[19], 'growth_score': row[20],
            'income_score': row[21], 'total_score': row[22],
            'headline': row[23], 'why': row[24], 'updated': row[25],
        })

    # Get latest scan metadata
    scan_cursor = conn.execute(
        'SELECT * FROM discovery_scans ORDER BY timestamp DESC LIMIT 1')
    scan_row = scan_cursor.fetchone()
    scan_info = None
    if scan_row:
        scan_info = {
            'timestamp': scan_row[1], 'sectors_scanned': scan_row[2],
            'candidates_found': scan_row[3],
            'top_picks': json.loads(scan_row[4]) if scan_row[4] else [],
            'portfolio': json.loads(scan_row[5]) if scan_row[5] else {},
            'gaps': json.loads(scan_row[6]) if scan_row[6] else [],
        }

    conn.close()
    return {'candidates': candidates, 'scan_info': scan_info}


# ============================================================================
# PORTFOLIO GAP ANALYSIS
# ============================================================================

def analyze_portfolio(client) -> Tuple[Dict[str, float], List[str], float]:
    """Returns (sector_allocation_pct, held_symbols, total_value)"""
    positions = client.get_positions()
    held = []
    sector_values = {}
    total_value = 0

    for pos in positions:
        instrument = pos.get('instrument', {})
        if instrument.get('assetType') != 'EQUITY':
            continue
        symbol = instrument.get('symbol', '')
        value = pos.get('marketValue', 0)
        if value <= 0:
            continue
        held.append(symbol)
        sector = STOCK_SECTOR_MAP.get(symbol, 'Other')
        sector_values[sector] = sector_values.get(sector, 0) + value
        total_value += value

    allocation = {}
    if total_value > 0:
        for sector, val in sector_values.items():
            allocation[sector] = round(val / total_value * 100, 1)
    return allocation, held, total_value


def identify_gaps(allocation: Dict[str, float]) -> List[PortfolioGap]:
    """Find sectors where you're underweight vs target"""
    gaps = []
    for sector, target_pct in TARGET_ALLOCATION.items():
        current = allocation.get(sector, 0)
        gap = target_pct - current
        if gap > 0.5:
            gaps.append(PortfolioGap(
                sector=sector, current_pct=current,
                target_pct=target_pct, gap_pct=round(gap, 1)
            ))
    gaps.sort(key=lambda g: g.gap_pct, reverse=True)
    return gaps


# ============================================================================
# DISCOVERY ENGINE
# ============================================================================

class DiscoveryEngine:
    """Scans outside your portfolio for growth + income opportunities"""

    def __init__(self, client):
        self.client = client

    def scan(self, trigger: str = 'manual') -> dict:
        """Full discovery scan across all non-held sectors"""
        print("\n" + "=" * 60)
        print("🔭 DISCOVERY ENGINE — Finding What You Can't See")
        print("=" * 60)

        # 1. Analyze portfolio
        print("\n📊 Analyzing current portfolio...")
        allocation, held_symbols, total_value = analyze_portfolio(self.client)
        print(f"   Portfolio: ${total_value:,.0f}")
        for sector, pct in sorted(allocation.items(), key=lambda x: -x[1]):
            bar = '█' * int(pct / 2)
            print(f"   {sector:25s} {pct:5.1f}% {bar}")

        # 2. Identify gaps
        gaps = identify_gaps(allocation)
        gap_map = {g.sector: g.gap_pct for g in gaps}
        print(f"\n🎯 Sector gaps (underweight vs target):")
        for g in gaps:
            print(f"   {g.sector:25s} {g.current_pct:5.1f}% → {g.target_pct}% (gap: +{g.gap_pct}%)")

        # 3. Also pull today's movers for momentum signals
        movers_set = set()
        try:
            for sort_type in ['percent_change_up', 'percent_change_down']:
                movers = self.client.get_movers('$SPX', sort=sort_type, frequency=0)
                screeners = movers.get('screeners', [])
                for m in screeners:
                    sym = m.get('symbol', '')
                    if sym and sym not in held_symbols:
                        movers_set.add(sym)
            print(f"\n🚀 Today's S&P movers (not held): {', '.join(sorted(movers_set)[:15])}")
        except Exception as e:
            print(f"   Movers fetch skipped: {e}")

        # 4. Scan universe sector by sector
        all_candidates = []
        sectors_scanned = 0

        # Scan biggest gaps first
        sorted_sectors = sorted(
            SECTOR_UNIVERSE.keys(),
            key=lambda s: gap_map.get(s, 0),
            reverse=True
        )

        for sector in sorted_sectors:
            info = SECTOR_UNIVERSE[sector]
            symbols = [s for s in info['stocks'] if s not in held_symbols]
            if not symbols:
                continue

            gap_bonus = gap_map.get(sector, 0)
            print(f"\n🔍 Scanning {sector} ({len(symbols)} stocks, gap bonus +{gap_bonus:.1f})...")
            sectors_scanned += 1

            # Batch fetch quotes + fundamentals from Schwab
            for i in range(0, len(symbols), 25):
                batch = symbols[i:i+25]
                try:
                    quotes = self.client.get_quotes_with_fundamentals(batch)
                except Exception as e:
                    print(f"   ⚠️ Quote fetch failed for {batch[:3]}: {e}")
                    time.sleep(1)
                    continue

                for symbol in batch:
                    quote_data = quotes.get(symbol, {})
                    if not isinstance(quote_data, dict):
                        continue
                    quote = quote_data.get('quote', {})
                    fund = quote_data.get('fundamental', {})

                    price = quote.get('lastPrice', 0)
                    if price <= 0:
                        continue

                    c = DiscoveryCandidate(symbol=symbol, sector=sector)
                    c.price = round(price, 2)
                    c.dividend_yield = fund.get('divYield', 0) or 0
                    c.pe_ratio = fund.get('peRatio', 0) or 0
                    c.pb_ratio = 0  # Not available in Schwab fundamentals
                    c.net_margin = 0  # Not available
                    c.roe = 0  # Not available
                    c.revenue_growth = 0  # Not available
                    # Compute market cap: price × shares outstanding
                    shares = fund.get('sharesOutstanding', 0) or 0
                    c.market_cap_b = round(price * shares / 1e9, 1) if shares else 0
                    # Compute 52-week change from high/low/current
                    high_52 = quote.get('52WeekHigh', 0) or 0
                    low_52 = quote.get('52WeekLow', 0) or 0
                    mid_52 = (high_52 + low_52) / 2 if (high_52 and low_52) else 0
                    c.change_52w = round((price - mid_52) / mid_52 * 100, 1) if mid_52 > 0 else 0
                    # Volume: avg10DaysVolume is in fundamental, not quote
                    c.avg_volume_m = round((fund.get('avg10DaysVolume', 0) or 0) / 1e6, 2)

                    # Skip illiquid
                    if c.avg_volume_m < 0.3:
                        continue

                    # Is it a today's mover? Bonus context
                    is_mover = symbol in movers_set

                    # Scan options (quick ATM put check)
                    c = self._scan_options(c)

                    # Load analyst target from DB
                    c = self._load_analyst(c)

                    # Score
                    c = self._score(c, gap_bonus, is_mover)

                    # Generate headline
                    c = self._headline(c, info['why'])

                    all_candidates.append(c)
                    _save_candidate(c)
                    print(f"   {c.symbol:6s} ${c.price:>8.2f}  score={c.total_score:.1f}  "
                          f"div={c.dividend_yield:.1f}%  PE={c.pe_ratio:.0f}  "
                          f"52w={c.change_52w:+.0f}%  IV={c.atm_put_iv:.0f}%")

                # Rate limiting handled by SchwabClient._request()

        # 5. Rank results
        all_candidates.sort(key=lambda c: c.total_score, reverse=True)
        top_symbols = [c.symbol for c in all_candidates[:10]]

        # Save scan metadata
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute('''
            INSERT INTO discovery_scans
            (timestamp, sectors_scanned, candidates_found, top_picks, portfolio_json, gaps_json, trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(), sectors_scanned, len(all_candidates),
              json.dumps(top_symbols), json.dumps(allocation),
              json.dumps([asdict(g) for g in gaps]), trigger))
        conn.commit()
        conn.close()

        print(f"\n" + "=" * 60)
        print(f"✅ Discovery complete: {len(all_candidates)} candidates, {sectors_scanned} sectors")
        print(f"🏆 Top 10: {', '.join(top_symbols)}")
        for c in all_candidates[:10]:
            print(f"   {c.symbol:6s} {c.total_score:.1f}pts  {c.headline}")
        print("=" * 60)

        return {
            'allocation': allocation,
            'gaps': [asdict(g) for g in gaps],
            'total_value': total_value,
            'candidates_count': len(all_candidates),
            'sectors_scanned': sectors_scanned,
            'top_picks': [asdict(c) for c in all_candidates[:30]],
        }

    # ------------------------------------------------------------------
    def _scan_options(self, c: DiscoveryCandidate) -> DiscoveryCandidate:
        """Quick ATM put scan for options income assessment"""
        try:
            chain = self.client.get_option_chain(
                c.symbol, 'PUT', strike_count=3,
                from_date=(datetime.now() + timedelta(days=25)).strftime('%Y-%m-%d'),
                to_date=(datetime.now() + timedelta(days=50)).strftime('%Y-%m-%d'),
            )
            put_map = chain.get('putExpDateMap', {})
            best_prem = 0
            best_iv = 0

            for exp_key, strikes in put_map.items():
                for strike_str, contracts in strikes.items():
                    for con in contracts:
                        strike = con.get('strikePrice', 0)
                        bid = con.get('bid', 0)
                        iv = con.get('volatility', 0)
                        dte = con.get('daysToExpiration', 0)
                        delta = abs(con.get('delta', 0))
                        oi = con.get('openInterest', 0)

                        if bid <= 0 or strike <= 0 or dte <= 0:
                            continue
                        if not (0.15 <= delta <= 0.40):
                            continue

                        prem_pct = bid / strike * 100
                        annual = prem_pct * (365 / dte)

                        if annual > best_prem:
                            best_prem = annual
                            best_iv = iv
                            c.atm_put_premium_pct = round(prem_pct, 2)
                            c.atm_put_annualized = round(annual, 1)
                            c.atm_put_iv = round(iv, 1)
                            c.options_liquid = oi >= 10

        except Exception:
            pass  # Options data is a bonus, not required
        return c

    # ------------------------------------------------------------------
    def _load_analyst(self, c: DiscoveryCandidate) -> DiscoveryCandidate:
        """Load analyst target from DB if available"""
        try:
            from analyst_targets import load_target
            at = load_target(c.symbol)
            if at and at.target > 0 and not at.error:
                c.analyst_target = at.target
                c.analyst_rating = at.rating or ''
                c.analyst_upside = at.upside_pct
        except Exception:
            pass
        return c

    # ------------------------------------------------------------------
    def _score(self, c: DiscoveryCandidate, gap_bonus: float,
               is_mover: bool) -> DiscoveryCandidate:
        """Score candidate on diversification, growth, and income potential"""

        # --- DIVERSIFICATION SCORE (0-4) ---
        div_score = 0
        # Bigger gap = more diversification value
        if gap_bonus >= 7:
            div_score += 2.0
        elif gap_bonus >= 4:
            div_score += 1.5
        elif gap_bonus >= 2:
            div_score += 1.0
        # Dividend yield adds defensive diversification
        if c.dividend_yield >= 3.0:
            div_score += 1.0
        elif c.dividend_yield >= 1.5:
            div_score += 0.5
        # Low correlation sectors (Healthcare, Staples, Utilities) get a bonus
        if c.sector in ('Healthcare', 'Consumer Staples', 'Energy & Utilities'):
            div_score += 0.5
        c.diversification_score = round(min(div_score, 4.0), 1)

        # --- GROWTH SCORE (0-4) ---
        growth = 0
        # 52-week momentum
        if c.change_52w > 30:
            growth += 1.5
        elif c.change_52w > 15:
            growth += 1.0
        elif c.change_52w > 0:
            growth += 0.5
        elif c.change_52w < -20:
            growth -= 0.5  # Falling knife warning
        # Analyst upside
        if c.analyst_upside > 25:
            growth += 1.5
        elif c.analyst_upside > 15:
            growth += 1.0
        elif c.analyst_upside > 5:
            growth += 0.5
        # Revenue growth
        if c.revenue_growth > 15:
            growth += 0.5
        # Today's mover bonus
        if is_mover:
            growth += 0.5
        c.growth_score = round(max(min(growth, 4.0), 0), 1)

        # --- INCOME SCORE (0-4) ---
        income = 0
        # Dividend yield
        if c.dividend_yield >= 4.0:
            income += 1.5
        elif c.dividend_yield >= 2.5:
            income += 1.0
        elif c.dividend_yield >= 1.0:
            income += 0.5
        # Options premium potential
        if c.atm_put_annualized >= 20:
            income += 1.5
        elif c.atm_put_annualized >= 12:
            income += 1.0
        elif c.atm_put_annualized >= 6:
            income += 0.5
        # Options liquidity bonus
        if c.options_liquid:
            income += 0.5
        # High IV = better premium
        if c.atm_put_iv >= 40:
            income += 0.5
        c.income_score = round(min(income, 4.0), 1)

        # --- TOTAL SCORE ---
        # Weight: diversification matters most for this portfolio
        c.total_score = round(
            c.diversification_score * 1.5 +
            c.growth_score * 1.2 +
            c.income_score * 1.0,
            1
        )
        return c

    # ------------------------------------------------------------------
    def _headline(self, c: DiscoveryCandidate, sector_why: str) -> DiscoveryCandidate:
        """Generate a human-readable headline explaining WHY this stock"""
        parts = []

        # Growth signal
        if c.change_52w > 20:
            parts.append(f"+{c.change_52w:.0f}% in 52w")
        if c.analyst_upside > 15:
            parts.append(f"analysts see +{c.analyst_upside:.0f}% upside")
        if c.analyst_rating and 'Buy' in c.analyst_rating:
            parts.append(c.analyst_rating)

        # Income signal
        if c.dividend_yield >= 2.0:
            parts.append(f"{c.dividend_yield:.1f}% dividend")
        if c.atm_put_annualized >= 10:
            parts.append(f"{c.atm_put_annualized:.0f}% put premium")

        c.headline = f"{c.sector}: {', '.join(parts[:3])}" if parts else f"{c.sector} candidate"

        # Why this stock
        reasons = [sector_why]
        if c.pe_ratio > 0 and c.pe_ratio < 20:
            reasons.append(f"reasonable valuation (PE {c.pe_ratio:.0f})")
        if c.roe > 15:
            reasons.append(f"strong ROE ({c.roe:.0f}%)")
        if c.net_margin > 15:
            reasons.append(f"high margins ({c.net_margin:.0f}%)")
        if c.options_liquid:
            reasons.append("liquid options for income")
        c.why = '. '.join(reasons[:3])

        return c
