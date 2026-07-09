"""
Analyst Target Manager
======================
Fetches consensus analyst price targets and ratings from multiple sources.
Persists to SQLite for reliability. Supports manual + daily auto-refresh.
"""

import httpx
import json
import re
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List
from pathlib import Path


@dataclass
class AnalystTarget:
    symbol: str
    target: float           # Average analyst price target
    rating: str             # Strong Buy / Buy / Hold / Sell
    analyst_count: int      # Number of analysts
    target_high: float      # Highest target
    target_low: float       # Lowest target
    upside_pct: float       # Upside from current price
    current_price: float    # Price at time of fetch
    source: str             # Data source
    updated: str            # ISO timestamp
    error: Optional[str] = None


DB_PATH = Path(__file__).parent / "scanner.db"


def _init_db():
    """Create analyst_targets table if not exists"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS analyst_targets (
            symbol TEXT PRIMARY KEY,
            target REAL,
            rating TEXT,
            analyst_count INTEGER,
            target_high REAL,
            target_low REAL,
            upside_pct REAL,
            current_price REAL,
            source TEXT,
            updated TEXT,
            error TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS analyst_refresh_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbols_refreshed INTEGER,
            symbols_failed INTEGER,
            trigger TEXT,
            details TEXT
        )
    ''')
    conn.commit()
    conn.close()


_init_db()


# ============================================================================
# FETCHER: Schwab Quote (for current price, always reliable)
# ============================================================================

def _fetch_schwab_price(symbol: str) -> Optional[float]:
    """Get current price from Schwab API"""
    try:
        from token_manager import get_headers
        headers = get_headers()
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                'https://api.schwabapi.com/marketdata/v1/quotes',
                headers=headers,
                params={'symbols': symbol}
            )
            if resp.status_code == 200:
                data = resp.json()
                quote = data.get(symbol, {}).get('quote', {})
                return quote.get('lastPrice', 0)
    except Exception:
        pass
    return None


# ============================================================================
# FETCHER: HTML scraping from StockAnalysis.com
# ============================================================================

def _fetch_from_html(symbol: str) -> Optional[AnalystTarget]:
    """Scrape analyst data from StockAnalysis.com forecast page"""
    try:
        url = f"https://stockanalysis.com/stocks/{symbol.lower()}/forecast/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0',
            'Accept': 'text/html',
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return None

            html = resp.text

            # Extract price target
            target_match = re.search(
                r'average price target[^$]*\$([0-9,]+\.?\d*)', html, re.IGNORECASE
            )
            if not target_match:
                target_match = re.search(
                    r'price target of \$([0-9,]+\.?\d*)', html, re.IGNORECASE
                )

            # Extract rating
            rating_match = re.search(
                r'consensus (?:rating|recommendation)[^"]*["\s]+(Strong Buy|Buy|Hold|Sell|Strong Sell)',
                html, re.IGNORECASE
            )

            # Extract analyst count
            count_match = re.search(r'(\d+)\s+analyst', html, re.IGNORECASE)

            # Extract high/low targets
            high_match = re.search(r'highest[^$]*\$([0-9,]+\.?\d*)', html, re.IGNORECASE)
            low_match = re.search(r'lowest[^$]*\$([0-9,]+\.?\d*)', html, re.IGNORECASE)

            if target_match:
                target = float(target_match.group(1).replace(',', ''))
                rating = rating_match.group(1).title() if rating_match else 'Unknown'
                count = int(count_match.group(1)) if count_match else 0
                high = float(high_match.group(1).replace(',', '')) if high_match else target
                low = float(low_match.group(1).replace(',', '')) if low_match else target

                price = _fetch_schwab_price(symbol) or 0
                upside = ((target - price) / price * 100) if price > 0 else 0

                return AnalystTarget(
                    symbol=symbol.upper(),
                    target=target,
                    rating=rating,
                    analyst_count=count,
                    target_high=high,
                    target_low=low,
                    upside_pct=round(upside, 1),
                    current_price=price,
                    source='stockanalysis_html',
                    updated=datetime.now().isoformat()
                )
    except Exception as e:
        print(f"   HTML scrape failed for {symbol}: {e}")
    return None


# ============================================================================
# MAIN FETCH (tries HTML scraper, returns error entry if all fail)
# ============================================================================

def fetch_analyst_target(symbol: str) -> AnalystTarget:
    """Fetch analyst target from best available source"""
    symbol = symbol.upper()
    result = _fetch_from_html(symbol)
    if result and result.target > 0:
        return result

    return AnalystTarget(
        symbol=symbol, target=0, rating='Unknown', analyst_count=0,
        target_high=0, target_low=0, upside_pct=0,
        current_price=_fetch_schwab_price(symbol) or 0,
        source='none', updated=datetime.now().isoformat(),
        error=f"Could not fetch analyst data for {symbol}"
    )


# ============================================================================
# PERSISTENCE (SQLite)
# ============================================================================

def save_target(target: AnalystTarget):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''
        INSERT OR REPLACE INTO analyst_targets
        (symbol, target, rating, analyst_count, target_high, target_low,
         upside_pct, current_price, source, updated, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (target.symbol, target.target, target.rating, target.analyst_count,
          target.target_high, target.target_low, target.upside_pct,
          target.current_price, target.source, target.updated, target.error))
    conn.commit()
    conn.close()


def _row_to_target(row) -> AnalystTarget:
    return AnalystTarget(
        symbol=row[0], target=row[1], rating=row[2],
        analyst_count=row[3], target_high=row[4], target_low=row[5],
        upside_pct=row[6], current_price=row[7], source=row[8],
        updated=row[9], error=row[10]
    )

def load_all_targets() -> Dict[str, AnalystTarget]:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute('SELECT * FROM analyst_targets ORDER BY symbol')
    targets = {row[0]: _row_to_target(row) for row in cursor.fetchall()}
    conn.close()
    return targets

def load_target(symbol: str) -> Optional[AnalystTarget]:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute('SELECT * FROM analyst_targets WHERE symbol = ?', (symbol.upper(),))
    row = cursor.fetchone()
    conn.close()
    return _row_to_target(row) if row else None

def get_refresh_log(limit: int = 10) -> list:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        'SELECT * FROM analyst_refresh_log ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{'id': r[0], 'timestamp': r[1], 'refreshed': r[2],
             'failed': r[3], 'trigger': r[4], 'details': r[5]} for r in rows]


def _log_refresh(refreshed: int, failed: int, trigger: str, details: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''
        INSERT INTO analyst_refresh_log (timestamp, symbols_refreshed, symbols_failed, trigger, details)
        VALUES (?, ?, ?, ?, ?)
    ''', (datetime.now().isoformat(), refreshed, failed, trigger, details))
    conn.commit()
    conn.close()


# Default symbols to track (positions + watchlist)
DEFAULT_SYMBOLS = [
    'MSFT', 'NVDA', 'ORCL', 'AMD', 'AMZN', 'TSLA', 'COST', 'CRDO', 'INTC',
    'VST', 'CEG', 'UNH', 'RTX', 'LMT', 'PFE', 'JNJ', 'NEE',
    'GD', 'NOC', 'MRK', 'LLY', 'ABBV',
]


def refresh_all_targets(symbols: List[str] = None, trigger: str = 'manual') -> dict:
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    print(f"\n🔄 Refreshing analyst targets for {len(symbols)} symbols...")
    refreshed, failed, results, errors = 0, 0, {}, []
    for symbol in symbols:
        print(f"   Fetching {symbol}...", end=" ")
        target = fetch_analyst_target(symbol)
        if target.error:
            print(f"❌ {target.error}")
            failed += 1; errors.append(symbol)
        else:
            print(f"✅ ${target.target:.0f} ({target.rating}, {target.analyst_count} analysts)")
            refreshed += 1
        save_target(target)
        results[symbol] = target

    details = f"{refreshed} OK, {failed} failed"
    if errors:
        details += f" ({', '.join(errors)})"
    _log_refresh(refreshed, failed, trigger, details)
    print(f"\n✅ Refresh complete: {details}")
    return {'refreshed': refreshed, 'failed': failed, 'errors': errors, 'results': results}


def refresh_single_target(symbol: str, trigger: str = 'manual') -> AnalystTarget:
    """Refresh a single symbol's analyst target"""
    symbol = symbol.upper()
    print(f"🔄 Refreshing {symbol}...")
    target = fetch_analyst_target(symbol)
    save_target(target)
    _log_refresh(
        1 if not target.error else 0,
        0 if not target.error else 1,
        trigger,
        f"{symbol}: ${int(target.target)}" if not target.error else f"{symbol}: {target.error}"
    )
    return target


# ============================================================================
# SYNC + API RESPONSE (used by dashboard.py)
# ============================================================================

def sync_from_db():
    """Load all targets from DB into memory. Called on dashboard startup."""
    targets = load_all_targets()
    print(f"📊 Loaded {len(targets)} analyst targets from DB")
    for sym, t in targets.items():
        if not t.error and t.target > 0:
            print(f"   {sym}: ${t.target:.0f} ({t.rating}, {t.analyst_count} analysts)")
    return targets


def targets_to_api_response() -> dict:
    """Format all targets + refresh log for the dashboard API"""
    targets = load_all_targets()
    log = get_refresh_log(limit=5)

    # Find last refresh time and count stale entries (>48h old)
    last_refresh = None
    stale_count = 0
    now = datetime.now()

    serialized = {}
    for sym, t in targets.items():
        data = asdict(t)
        serialized[sym] = data
        if t.updated:
            updated_dt = datetime.fromisoformat(t.updated)
            if last_refresh is None or updated_dt > last_refresh:
                last_refresh = updated_dt
            if (now - updated_dt).total_seconds() > 48 * 3600:
                stale_count += 1

    return {
        'targets': serialized,
        'last_refresh': last_refresh.isoformat() if last_refresh else None,
        'stale_count': stale_count,
        'total_count': len(targets),
        'refresh_log': log
    }
