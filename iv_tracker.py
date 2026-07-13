"""
IV History Tracker
Tracks implied volatility over time for accurate IV percentile/rank calculations
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
import statistics
import math

HIGH_CONFIDENCE_MIN_POINTS = 180
LOW_CONFIDENCE_MIN_POINTS = 60
STALE_HISTORY_DAYS = 14

class IVHistoryTracker:
    """Tracks and analyzes historical IV data"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(Path.home() / ".schwab" / "scanner.db")
        self._init_db()
    
    def _init_db(self):
        """Initialize IV history table"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS iv_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                atm_iv REAL NOT NULL,
                front_month_iv REAL,
                back_month_iv REAL,
                put_skew REAL,
                UNIQUE(symbol, date)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_iv_symbol_date ON iv_history(symbol, date)')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS realized_vol_history (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                window_days INTEGER NOT NULL,
                rv_yang_zhang REAL NOT NULL,
                source TEXT,
                created_at TEXT,
                PRIMARY KEY (symbol, date, window_days)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_rv_symbol_date ON realized_vol_history(symbol, date)')
        conn.commit()
        conn.close()
    
    def record_iv(self, symbol: str, atm_iv: float, 
                  front_iv: float = None, back_iv: float = None,
                  put_skew: float = None):
        """Record today's IV for a symbol"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                INSERT OR REPLACE INTO iv_history 
                (symbol, date, atm_iv, front_month_iv, back_month_iv, put_skew)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (symbol.upper(), today, atm_iv, front_iv, back_iv, put_skew))
            conn.commit()
        except Exception as e:
            print(f"Error recording IV: {e}")
        finally:
            conn.close()
    
    def get_iv_history(self, symbol: str, days: int = 365) -> List[Tuple[str, float]]:
        """Get IV history for a symbol"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            SELECT date, atm_iv FROM iv_history
            WHERE symbol = ? AND date >= ?
            ORDER BY date
        ''', (symbol.upper(), cutoff))
        
        history = [(row[0], row[1]) for row in cursor.fetchall()]
        conn.close()
        
        return history

    def history_confidence(self, history: List[Tuple[str, float]]) -> str:
        """Return HIGH/LOW/NONE based on point count and freshness."""
        if len(history) < LOW_CONFIDENCE_MIN_POINTS:
            return "NONE"
        try:
            last_date = datetime.strptime(history[-1][0], "%Y-%m-%d").date()
            age = (datetime.now().date() - last_date).days
        except Exception:
            age = STALE_HISTORY_DAYS + 1
        if len(history) >= HIGH_CONFIDENCE_MIN_POINTS and age <= STALE_HISTORY_DAYS:
            return "HIGH"
        return "LOW"

    def calculate_iv_metrics(self, symbol: str, current_iv: float, days: int = 365) -> dict:
        """
        Calculate IV rank, IV percentile, divergence, and sparse-history confidence.

        For fewer than 60 observations we intentionally do not pretend to know
        annual distribution rank; callers can fall back to raw IV or sector medians.
        """
        history = self.get_iv_history(symbol, days=days)
        confidence = self.history_confidence(history)
        out = {
            "symbol": symbol.upper(),
            "current_iv": current_iv,
            "history_points": len(history),
            "history_confidence": confidence,
            "iv_rank": None,
            "iv_percentile": None,
            "iv_rank_percentile_divergence": None,
            "iv_outlier_distorted": False,
            "iv_min": None,
            "iv_max": None,
            "iv_median": None,
            "last_iv_date": history[-1][0] if history else None,
        }
        if confidence == "NONE":
            out["estimated_percentile"] = self._estimate_percentile(current_iv)
            return out

        ivs = [float(iv) for _, iv in history if iv is not None]
        if not ivs:
            return out
        iv_low = min(ivs)
        iv_high = max(ivs)
        iv_rank = 50.0 if iv_high == iv_low else ((current_iv - iv_low) / (iv_high - iv_low)) * 100
        iv_percentile = (sum(1 for iv in ivs if iv < current_iv) / len(ivs)) * 100
        median_iv = statistics.median(ivs)
        divergence = abs(iv_rank - iv_percentile)

        out.update({
            "iv_rank": max(0.0, min(100.0, iv_rank)),
            "iv_percentile": max(0.0, min(100.0, iv_percentile)),
            "iv_rank_percentile_divergence": divergence,
            "iv_outlier_distorted": divergence > 25 or any(iv > median_iv * 2 for iv in ivs if median_iv > 0),
            "iv_min": iv_low,
            "iv_max": iv_high,
            "iv_median": median_iv,
        })
        return out
    
    def calculate_iv_percentile(self, symbol: str, current_iv: float) -> float:
        """
        Calculate IV percentile (what % of days had lower IV)
        
        IV Percentile = (# of days with IV < current) / (total days) * 100
        """
        history = self.get_iv_history(symbol, days=252)  # ~1 trading year
        
        if len(history) < 20:
            # Not enough history, estimate based on typical ranges
            return self._estimate_percentile(current_iv)
        
        ivs = [iv for _, iv in history]
        below_current = sum(1 for iv in ivs if iv < current_iv)
        
        return (below_current / len(ivs)) * 100
    
    def calculate_iv_rank(self, symbol: str, current_iv: float) -> float:
        """
        Calculate IV Rank
        
        IV Rank = (Current IV - 52w Low) / (52w High - 52w Low) * 100
        """
        history = self.get_iv_history(symbol, days=365)
        
        if len(history) < 20:
            return self._estimate_percentile(current_iv)
        
        ivs = [iv for _, iv in history]
        iv_high = max(ivs)
        iv_low = min(ivs)
        
        if iv_high == iv_low:
            return 50.0
        
        return ((current_iv - iv_low) / (iv_high - iv_low)) * 100
    
    def get_52w_range(self, symbol: str) -> Tuple[float, float]:
        """Get 52-week IV high and low"""
        history = self.get_iv_history(symbol, days=365)
        
        if not history:
            return (20.0, 80.0)  # Default range
        
        ivs = [iv for _, iv in history]
        return (min(ivs), max(ivs))
    
    def _estimate_percentile(self, current_iv: float) -> float:
        """Estimate percentile when we don't have history"""
        # Based on typical IV distributions:
        # < 20% IV = very low (10th percentile)
        # 20-30% = low (25th)
        # 30-40% = below average (40th)
        # 40-50% = average (50th)
        # 50-60% = above average (65th)
        # 60-70% = high (80th)
        # > 70% = very high (90th+)
        
        if current_iv < 20:
            return 10
        elif current_iv < 30:
            return 25
        elif current_iv < 40:
            return 40
        elif current_iv < 50:
            return 50
        elif current_iv < 60:
            return 65
        elif current_iv < 70:
            return 80
        else:
            return 90
    
    def get_iv_stats(self, symbol: str) -> dict:
        """Get comprehensive IV statistics"""
        history = self.get_iv_history(symbol, days=365)
        
        if len(history) < 5:
            return {
                'has_history': False,
                'data_points': len(history),
                'min': None,
                'max': None,
                'mean': None,
                'median': None,
                'std_dev': None
            }
        
        ivs = [iv for _, iv in history]
        
        return {
            'has_history': True,
            'data_points': len(history),
            'min': min(ivs),
            'max': max(ivs),
            'mean': statistics.mean(ivs),
            'median': statistics.median(ivs),
            'std_dev': statistics.stdev(ivs) if len(ivs) > 1 else 0
        }

    def record_realized_vol(self, symbol: str, rv_yang_zhang: float, window_days: int = 30, source: str = "schwab"):
        """Record today's realized volatility estimate."""
        today = datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                INSERT OR REPLACE INTO realized_vol_history
                (symbol, date, window_days, rv_yang_zhang, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (symbol.upper(), today, int(window_days), float(rv_yang_zhang), source, datetime.now().isoformat()))
            conn.commit()
        finally:
            conn.close()

    def latest_realized_vol(self, symbol: str, window_days: int = 30, max_age_days: int = 5) -> Optional[float]:
        """Return recent Yang-Zhang realized vol if available."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute('''
                SELECT date, rv_yang_zhang FROM realized_vol_history
                WHERE symbol=? AND window_days=?
                ORDER BY date DESC LIMIT 1
            ''', (symbol.upper(), int(window_days))).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        try:
            age = (datetime.now().date() - datetime.strptime(row[0], "%Y-%m-%d").date()).days
        except Exception:
            age = max_age_days + 1
        return float(row[1]) if age <= max_age_days else None


def yang_zhang_realized_vol(candles: List[dict], window_days: int = 30) -> Optional[float]:
    """
    Yang-Zhang realized volatility, annualized and returned as percentage.

    Candles should contain open/high/low/close fields. At least window_days + 1
    candles are needed because overnight variance uses the prior close.
    """
    clean = []
    for c in candles or []:
        try:
            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            close = float(c["close"])
            if min(o, h, l, close) <= 0:
                continue
            clean.append({"open": o, "high": h, "low": l, "close": close})
        except Exception:
            continue
    if len(clean) < window_days + 1:
        return None

    sample = clean[-(window_days + 1):]
    overnight = []
    open_close = []
    rs_terms = []
    for i in range(1, len(sample)):
        prev_close = sample[i - 1]["close"]
        o = sample[i]["open"]
        h = sample[i]["high"]
        l = sample[i]["low"]
        c = sample[i]["close"]
        overnight.append(math.log(o / prev_close))
        open_close.append(math.log(c / o))
        rs_terms.append(math.log(h / o) * math.log(h / c) + math.log(l / o) * math.log(l / c))

    n = len(open_close)
    if n < 2:
        return None
    var_o = statistics.variance(overnight)
    var_oc = statistics.variance(open_close)
    mean_rs = statistics.mean(rs_terms)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    variance = max(0.0, var_o + k * var_oc + (1 - k) * mean_rs)
    return math.sqrt(variance * 252) * 100


# Seed with some baseline data for common symbols
def seed_baseline_iv_data():
    """Seed database with baseline IV estimates for common symbols"""
    
    # Approximate historical IV ranges (you'd want real data ideally)
    baseline_data = {
        'NVDA': {'low': 35, 'high': 90, 'typical': 55},
        'AMD': {'low': 35, 'high': 85, 'typical': 50},
        'ORCL': {'low': 20, 'high': 50, 'typical': 30},
        'MSFT': {'low': 15, 'high': 45, 'typical': 25},
        'AMZN': {'low': 25, 'high': 60, 'typical': 35},
        'TSLA': {'low': 40, 'high': 120, 'typical': 60},
        'INTC': {'low': 25, 'high': 60, 'typical': 35},
        'VST': {'low': 35, 'high': 90, 'typical': 55},
        'CEG': {'low': 30, 'high': 80, 'typical': 50},
        'UNH': {'low': 15, 'high': 45, 'typical': 25},
        'RTX': {'low': 15, 'high': 40, 'typical': 25},
        'LMT': {'low': 15, 'high': 40, 'typical': 22},
        'PFE': {'low': 15, 'high': 50, 'typical': 25},
        'JNJ': {'low': 12, 'high': 35, 'typical': 18},
    }
    
    tracker = IVHistoryTracker()
    
    # Generate synthetic history (last 30 days) for baseline
    import random
    
    for symbol, data in baseline_data.items():
        for days_ago in range(30):
            date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
            
            # Generate realistic IV around typical with some variance
            iv = data['typical'] + random.gauss(0, 5)
            iv = max(data['low'], min(data['high'], iv))  # Clamp to range
            
            conn = sqlite3.connect(tracker.db_path)
            try:
                conn.execute('''
                    INSERT OR IGNORE INTO iv_history 
                    (symbol, date, atm_iv)
                    VALUES (?, ?, ?)
                ''', (symbol, date, iv))
                conn.commit()
            except:
                pass
            finally:
                conn.close()
    
    print("Seeded baseline IV data for common symbols")


if __name__ == '__main__':
    # Seed baseline data
    seed_baseline_iv_data()
    
    # Test
    tracker = IVHistoryTracker()
    
    for symbol in ['NVDA', 'CEG', 'VST']:
        print(f"\n{symbol}:")
        stats = tracker.get_iv_stats(symbol)
        print(f"  Data points: {stats['data_points']}")
        if stats['has_history']:
            print(f"  Range: {stats['min']:.1f}% - {stats['max']:.1f}%")
            print(f"  Mean: {stats['mean']:.1f}%")
        
        # Test percentile calculation
        test_iv = 55
        percentile = tracker.calculate_iv_percentile(symbol, test_iv)
        rank = tracker.calculate_iv_rank(symbol, test_iv)
        print(f"  At {test_iv}% IV: Percentile={percentile:.0f}%, Rank={rank:.0f}%")
