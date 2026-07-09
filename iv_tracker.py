"""
IV History Tracker
Tracks implied volatility over time for accurate IV percentile/rank calculations
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import statistics

class IVHistoryTracker:
    """Tracks and analyzes historical IV data"""
    
    def __init__(self, db_path: str = "scanner.db"):
        self.db_path = db_path
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
