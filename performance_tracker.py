"""
Performance Tracking Module
Tracks trade outcomes and calculates P/L
"""

import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum

class TradeStatus(Enum):
    OPEN = "open"
    CLOSED_PROFIT = "closed_profit"
    CLOSED_LOSS = "closed_loss"
    ASSIGNED = "assigned"
    EXPIRED = "expired"

@dataclass
class Trade:
    """Represents a tracked trade"""
    id: int
    symbol: str
    trade_type: str  # 'SELL_PUT', 'SELL_CALL'
    strike: float
    expiration: str
    premium_received: float
    contracts: int
    open_date: datetime
    close_date: Optional[datetime]
    close_price: Optional[float]
    status: TradeStatus
    pnl: Optional[float]
    notes: str

@dataclass
class PerformanceStats:
    """Overall performance statistics"""
    total_trades: int
    open_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_premium_collected: float
    total_pnl: float
    average_pnl: float
    best_trade: Optional[Trade]
    worst_trade: Optional[Trade]

class PerformanceTracker:
    """Tracks and analyzes trade performance"""
    
    def __init__(self, db_path: str = "scanner.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                strike REAL NOT NULL,
                expiration TEXT NOT NULL,
                premium_received REAL NOT NULL,
                contracts INTEGER NOT NULL DEFAULT 1,
                open_date TEXT NOT NULL,
                close_date TEXT,
                close_price REAL,
                status TEXT NOT NULL DEFAULT 'open',
                pnl REAL,
                notes TEXT,
                alert_id INTEGER,
                FOREIGN KEY (alert_id) REFERENCES alerts(id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                total_premium REAL,
                total_pnl REAL,
                open_positions INTEGER,
                win_rate REAL
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def record_trade(self, symbol: str, trade_type: str, strike: float,
                     expiration: str, premium: float, contracts: int = 1,
                     notes: str = "", alert_id: int = None) -> int:
        """Record a new trade"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            INSERT INTO trades (symbol, trade_type, strike, expiration, 
                               premium_received, contracts, open_date, status, notes, alert_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        ''', (symbol, trade_type, strike, expiration, premium, contracts,
              datetime.now().isoformat(), notes, alert_id))
        
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return trade_id
    
    def close_trade(self, trade_id: int, close_price: float, 
                    status: str = 'closed') -> Optional[float]:
        """Close a trade and calculate P/L"""
        conn = sqlite3.connect(self.db_path)
        
        # Get original trade
        cursor = conn.execute(
            'SELECT premium_received, contracts FROM trades WHERE id = ?',
            (trade_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return None
        
        premium_received, contracts = row
        
        # Calculate P/L
        # For sold options: profit = premium received - buyback cost
        pnl = (premium_received - close_price) * contracts * 100
        
        # Determine status
        if status == 'closed':
            status = 'closed_profit' if pnl >= 0 else 'closed_loss'
        
        conn.execute('''
            UPDATE trades 
            SET close_date = ?, close_price = ?, status = ?, pnl = ?
            WHERE id = ?
        ''', (datetime.now().isoformat(), close_price, status, pnl, trade_id))
        
        conn.commit()
        conn.close()
        
        return pnl
    
    def mark_expired(self, trade_id: int) -> float:
        """Mark a trade as expired worthless (full profit)"""
        conn = sqlite3.connect(self.db_path)
        
        cursor = conn.execute(
            'SELECT premium_received, contracts FROM trades WHERE id = ?',
            (trade_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return 0
        
        premium_received, contracts = row
        pnl = premium_received * contracts * 100  # Full premium kept
        
        conn.execute('''
            UPDATE trades 
            SET close_date = ?, close_price = 0, status = 'expired', pnl = ?
            WHERE id = ?
        ''', (datetime.now().isoformat(), pnl, trade_id))
        
        conn.commit()
        conn.close()
        
        return pnl
    
    def mark_assigned(self, trade_id: int, notes: str = "") -> float:
        """Mark a put as assigned (bought stock)"""
        conn = sqlite3.connect(self.db_path)
        
        cursor = conn.execute(
            'SELECT premium_received, contracts, strike FROM trades WHERE id = ?',
            (trade_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return 0
        
        premium_received, contracts, strike = row
        # When assigned, we keep the premium but now own shares at strike
        # P/L is just the premium for tracking purposes
        pnl = premium_received * contracts * 100
        
        assignment_notes = f"Assigned at ${strike}. {notes}"
        
        conn.execute('''
            UPDATE trades 
            SET close_date = ?, status = 'assigned', pnl = ?, notes = ?
            WHERE id = ?
        ''', (datetime.now().isoformat(), pnl, assignment_notes, trade_id))
        
        conn.commit()
        conn.close()
        
        return pnl
    
    def get_open_trades(self) -> List[Trade]:
        """Get all open trades"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            SELECT id, symbol, trade_type, strike, expiration, premium_received,
                   contracts, open_date, close_date, close_price, status, pnl, notes
            FROM trades WHERE status = 'open'
            ORDER BY expiration
        ''')
        
        trades = []
        for row in cursor.fetchall():
            trades.append(Trade(
                id=row[0],
                symbol=row[1],
                trade_type=row[2],
                strike=row[3],
                expiration=row[4],
                premium_received=row[5],
                contracts=row[6],
                open_date=datetime.fromisoformat(row[7]),
                close_date=datetime.fromisoformat(row[8]) if row[8] else None,
                close_price=row[9],
                status=TradeStatus(row[10]),
                pnl=row[11],
                notes=row[12] or ''
            ))
        
        conn.close()
        return trades
    
    def get_all_trades(self, limit: int = 100) -> List[Trade]:
        """Get all trades"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            SELECT id, symbol, trade_type, strike, expiration, premium_received,
                   contracts, open_date, close_date, close_price, status, pnl, notes
            FROM trades
            ORDER BY open_date DESC
            LIMIT ?
        ''', (limit,))
        
        trades = []
        for row in cursor.fetchall():
            trades.append(Trade(
                id=row[0],
                symbol=row[1],
                trade_type=row[2],
                strike=row[3],
                expiration=row[4],
                premium_received=row[5],
                contracts=row[6],
                open_date=datetime.fromisoformat(row[7]),
                close_date=datetime.fromisoformat(row[8]) if row[8] else None,
                close_price=row[9],
                status=TradeStatus(row[10]),
                pnl=row[11],
                notes=row[12] or ''
            ))
        
        conn.close()
        return trades
    
    def get_stats(self) -> PerformanceStats:
        """Calculate overall performance statistics"""
        conn = sqlite3.connect(self.db_path)
        
        # Total trades
        cursor = conn.execute('SELECT COUNT(*) FROM trades')
        total_trades = cursor.fetchone()[0]
        
        # Open trades
        cursor = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'")
        open_trades = cursor.fetchone()[0]
        
        # Closed trades
        cursor = conn.execute("SELECT COUNT(*) FROM trades WHERE status != 'open'")
        closed_trades = cursor.fetchone()[0]
        
        # Winning/losing
        cursor = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0")
        winning_trades = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT COUNT(*) FROM trades WHERE pnl < 0")
        losing_trades = cursor.fetchone()[0]
        
        # Win rate
        win_rate = winning_trades / closed_trades * 100 if closed_trades > 0 else 0
        
        # Total premium collected
        cursor = conn.execute('SELECT SUM(premium_received * contracts * 100) FROM trades')
        total_premium = cursor.fetchone()[0] or 0
        
        # Total P/L
        cursor = conn.execute('SELECT SUM(pnl) FROM trades WHERE pnl IS NOT NULL')
        total_pnl = cursor.fetchone()[0] or 0
        
        # Average P/L
        cursor = conn.execute('SELECT AVG(pnl) FROM trades WHERE pnl IS NOT NULL')
        avg_pnl = cursor.fetchone()[0] or 0
        
        # Best trade
        cursor = conn.execute('''
            SELECT id, symbol, trade_type, strike, expiration, premium_received,
                   contracts, open_date, close_date, close_price, status, pnl, notes
            FROM trades WHERE pnl IS NOT NULL
            ORDER BY pnl DESC LIMIT 1
        ''')
        row = cursor.fetchone()
        best_trade = None
        if row:
            best_trade = Trade(
                id=row[0], symbol=row[1], trade_type=row[2], strike=row[3],
                expiration=row[4], premium_received=row[5], contracts=row[6],
                open_date=datetime.fromisoformat(row[7]),
                close_date=datetime.fromisoformat(row[8]) if row[8] else None,
                close_price=row[9], status=TradeStatus(row[10]),
                pnl=row[11], notes=row[12] or ''
            )
        
        # Worst trade
        cursor = conn.execute('''
            SELECT id, symbol, trade_type, strike, expiration, premium_received,
                   contracts, open_date, close_date, close_price, status, pnl, notes
            FROM trades WHERE pnl IS NOT NULL
            ORDER BY pnl ASC LIMIT 1
        ''')
        row = cursor.fetchone()
        worst_trade = None
        if row:
            worst_trade = Trade(
                id=row[0], symbol=row[1], trade_type=row[2], strike=row[3],
                expiration=row[4], premium_received=row[5], contracts=row[6],
                open_date=datetime.fromisoformat(row[7]),
                close_date=datetime.fromisoformat(row[8]) if row[8] else None,
                close_price=row[9], status=TradeStatus(row[10]),
                pnl=row[11], notes=row[12] or ''
            )
        
        conn.close()
        
        return PerformanceStats(
            total_trades=total_trades,
            open_trades=open_trades,
            closed_trades=closed_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_premium_collected=total_premium,
            total_pnl=total_pnl,
            average_pnl=avg_pnl,
            best_trade=best_trade,
            worst_trade=worst_trade
        )
    
    def to_dict(self, trade: Trade) -> dict:
        """Convert trade to dictionary"""
        return {
            'id': trade.id,
            'symbol': trade.symbol,
            'trade_type': trade.trade_type,
            'strike': trade.strike,
            'expiration': trade.expiration,
            'premium_received': trade.premium_received,
            'contracts': trade.contracts,
            'open_date': trade.open_date.isoformat(),
            'close_date': trade.close_date.isoformat() if trade.close_date else None,
            'close_price': trade.close_price,
            'status': trade.status.value,
            'pnl': trade.pnl,
            'notes': trade.notes
        }
    
    def stats_to_dict(self, stats: PerformanceStats) -> dict:
        """Convert stats to dictionary"""
        return {
            'total_trades': stats.total_trades,
            'open_trades': stats.open_trades,
            'closed_trades': stats.closed_trades,
            'winning_trades': stats.winning_trades,
            'losing_trades': stats.losing_trades,
            'win_rate': stats.win_rate,
            'total_premium_collected': stats.total_premium_collected,
            'total_pnl': stats.total_pnl,
            'average_pnl': stats.average_pnl,
            'best_trade': self.to_dict(stats.best_trade) if stats.best_trade else None,
            'worst_trade': self.to_dict(stats.worst_trade) if stats.worst_trade else None
        }


# Test
if __name__ == '__main__':
    tracker = PerformanceTracker()
    
    # Example: Record a trade
    # trade_id = tracker.record_trade(
    #     symbol='VST',
    #     trade_type='SELL_PUT',
    #     strike=145.0,
    #     expiration='2026-03-20',
    #     premium=6.40,
    #     contracts=2,
    #     notes='IV spike opportunity'
    # )
    # print(f"Recorded trade ID: {trade_id}")
    
    # Show stats
    stats = tracker.get_stats()
    print("\n" + "="*60)
    print("📊 PERFORMANCE STATS")
    print("="*60)
    print(f"Total Trades:     {stats.total_trades}")
    print(f"Open Trades:      {stats.open_trades}")
    print(f"Closed Trades:    {stats.closed_trades}")
    print(f"Win Rate:         {stats.win_rate:.1f}%")
    print(f"Total Premium:    ${stats.total_premium_collected:,.2f}")
    print(f"Total P/L:        ${stats.total_pnl:,.2f}")
    print(f"Average P/L:      ${stats.average_pnl:,.2f}")
    
    # Show open trades
    open_trades = tracker.get_open_trades()
    if open_trades:
        print("\n📋 OPEN POSITIONS:")
        for t in open_trades:
            print(f"   {t.symbol} {t.trade_type} ${t.strike} exp {t.expiration} - ${t.premium_received:.2f} x {t.contracts}")
