"""
Smart Options Scanner - Core Engine
Continuously monitors for high-probability trading opportunities
"""

import httpx
import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
from enum import Enum

# ============================================================================
# DATA MODELS
# ============================================================================

class SignalType(Enum):
    IV_SPIKE = "iv_spike"
    SUPPORT_BOUNCE = "support_bounce"
    RALLY_EXHAUSTION = "rally_exhaustion"
    PREMIUM_RICH = "premium_rich"
    ASSIGNMENT_RISK = "assignment_risk"
    CONCENTRATION_WARNING = "concentration_warning"

class ActionType(Enum):
    SELL_PUT = "SELL PUT"
    SELL_CALL = "SELL CALL"
    ROLL = "ROLL"
    CLOSE = "CLOSE"
    HEDGE = "HEDGE"
    WAIT = "WAIT"

@dataclass
class Opportunity:
    """Represents a trading opportunity"""
    symbol: str
    signal_type: SignalType
    action: ActionType
    strike: float
    expiration: str
    premium: float
    annualized_yield: float
    delta: float
    iv: float
    iv_rank: Optional[float]
    score: int  # 1-5 stars
    headline: str
    details: str
    timestamp: datetime
    
    def to_dict(self):
        return {
            'symbol': self.symbol,
            'signal_type': self.signal_type.value,
            'action': self.action.value,
            'strike': self.strike,
            'expiration': self.expiration,
            'premium': self.premium,
            'annualized_yield': self.annualized_yield,
            'delta': self.delta,
            'iv': self.iv,
            'iv_rank': self.iv_rank,
            'score': self.score,
            'headline': self.headline,
            'details': self.details,
            'timestamp': self.timestamp.isoformat()
        }

# ============================================================================
# SCHWAB API CLIENT (simplified from existing)
# ============================================================================

class SchwabClient:
    """Handles Schwab API authentication and requests"""
    
    def __init__(self):
        self.base_url = "https://api.schwabapi.com"
    
    @property
    def headers(self):
        # Use token manager for auto-refresh
        from token_manager import get_headers
        return get_headers()
    
    def get_quote(self, symbol: str) -> dict:
        """Get real-time quote for a symbol"""
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'{self.base_url}/marketdata/v1/quotes',
                headers=self.headers,
                params={'symbols': symbol}
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get(symbol, {}).get('quote', {})
        return {}
    
    def get_quotes(self, symbols: list) -> dict:
        """Get quotes for multiple symbols"""
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'{self.base_url}/marketdata/v1/quotes',
                headers=self.headers,
                params={'symbols': ','.join(symbols)}
            )
            if resp.status_code == 200:
                data = resp.json()
                return {s: data.get(s, {}).get('quote', {}) for s in symbols}
        return {}
    
    def get_quotes_with_fundamentals(self, symbols: list) -> dict:
        """Get quotes + fundamental data (PE, div yield, market cap, etc.)"""
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'{self.base_url}/marketdata/v1/quotes',
                headers=self.headers,
                params={'symbols': ','.join(symbols), 'fields': 'quote,fundamental'}
            )
            if resp.status_code == 200:
                return resp.json()
        return {}

    def get_movers(self, index: str = '$SPX', sort: str = 'percent_change_up',
                   frequency: int = 0) -> dict:
        """Get top movers for a market index"""
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'{self.base_url}/marketdata/v1/movers/{index}',
                headers=self.headers,
                params={'sort': sort, 'frequency': frequency}
            )
            if resp.status_code == 200:
                return resp.json()
        return {}

    def get_option_chain(self, symbol: str, contract_type: str = 'PUT',
                         strike_count: int = 10, days_out: int = 45) -> dict:
        """Get option chain for a symbol"""
        from_date = datetime.now().strftime('%Y-%m-%d')
        to_date = (datetime.now() + timedelta(days=days_out)).strftime('%Y-%m-%d')
        
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'{self.base_url}/marketdata/v1/chains',
                headers=self.headers,
                params={
                    'symbol': symbol,
                    'contractType': contract_type,
                    'strikeCount': strike_count,
                    'fromDate': from_date,
                    'toDate': to_date,
                    'includeUnderlyingQuote': 'true'
                }
            )
            if resp.status_code == 200:
                return resp.json()
        return {}
    
    def get_positions(self) -> list:
        """Get all positions across accounts"""
        positions = []
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'{self.base_url}/trader/v1/accounts?fields=positions',
                headers=self.headers
            )
            if resp.status_code == 200:
                accounts = resp.json()
                for acc in accounts:
                    sec = acc.get('securitiesAccount', {})
                    for pos in sec.get('positions', []):
                        pos['accountNumber'] = sec.get('accountNumber')
                        positions.append(pos)
        return positions

# ============================================================================
# SCANNING STRATEGIES
# ============================================================================

class IVSpikeStrategy:
    """Detects IV spikes for put selling opportunities"""
    
    def __init__(self, client: SchwabClient):
        self.client = client
        self.iv_history = {}  # symbol -> list of recent IVs
        # Import earnings calendar
        from earnings_calendar import EarningsCalendarWithBackup
        self.earnings = EarningsCalendarWithBackup()
        # Import options structure analyzer for professional metrics
        from options_structure import OptionsStructureAnalyzer
        self.structure_analyzer = OptionsStructureAnalyzer()
    
    def scan(self, symbols: list, min_iv: float = 45, 
             min_annualized: float = 25) -> list:
        """Scan for IV spike opportunities"""
        opportunities = []
        
        for symbol in symbols:
            # CHECK EARNINGS FIRST - skip if too close
            earnings_info = self.earnings.get_earnings_date(symbol)
            if earnings_info.days_until is not None and 0 <= earnings_info.days_until <= 7:
                print(f"   ⚠️ Skipping {symbol} - earnings in {earnings_info.days_until} days")
                continue
            
            # GET OPTIONS STRUCTURE for professional metrics
            structure = None
            try:
                structure = self.structure_analyzer.analyze(symbol)
            except Exception as e:
                print(f"   ⚠️ Could not get options structure for {symbol}: {e}")
            
            # Skip if liquidity is too poor (Grade D or F)
            if structure and structure.liquidity_grade in ['D', 'F']:
                print(f"   ⚠️ Skipping {symbol} - poor liquidity (Grade {structure.liquidity_grade}, {structure.avg_bid_ask_spread_pct:.1f}% spreads)")
                continue
            
            quote = self.client.get_quote(symbol)
            if not quote:
                continue
                
            price = quote.get('lastPrice', 0)
            change_pct = quote.get('netPercentChange', 0)
            
            # Get put options
            chain = self.client.get_option_chain(symbol, 'PUT', strike_count=8)
            put_map = chain.get('putExpDateMap', {})
            
            for exp_date, strikes in put_map.items():
                exp_str = exp_date.split(':')[0]
                
                for strike_str, contracts in strikes.items():
                    for c in contracts:
                        iv = c.get('volatility', 0)
                        delta = c.get('delta', 0)
                        bid = c.get('bid', 0)
                        ask = c.get('ask', 0)
                        mark = c.get('mark', 0)
                        dte = c.get('daysToExpiration', 1)
                        oi = c.get('openInterest', 0)
                        strike = c.get('strikePrice', 0)
                        
                        # Filter criteria
                        if iv < min_iv:
                            continue
                        if not (-0.35 <= delta <= -0.15):
                            continue
                        if bid <= 0 or dte <= 0:
                            continue
                        
                        # EARNINGS FILTER: Skip if option expires on or before earnings
                        if earnings_info.days_until is not None:
                            if dte <= earnings_info.days_until + 1:  # +1 for buffer
                                continue  # Option expires too close to earnings
                        
                        # Skip very short DTE (< 7 days) - too risky, gamma risk
                        if dte < 7:
                            continue
                        
                        # Calculate metrics
                        spread_pct = (ask - bid) / mark * 100 if mark > 0 else 100
                        annualized = (bid / strike * 100) * (365 / dte)
                        otm_pct = (price - strike) / price * 100
                        
                        if annualized < min_annualized:
                            continue
                        if spread_pct > 25:  # Skip illiquid
                            continue
                        
                        # Score the opportunity (1-5 stars) with structure data
                        score = self._calculate_score(
                            iv, annualized, spread_pct, oi, otm_pct, abs(delta), structure
                        )
                        
                        if score >= 3:  # Only report 3+ star opportunities
                            opp = Opportunity(
                                symbol=symbol,
                                signal_type=SignalType.IV_SPIKE,
                                action=ActionType.SELL_PUT,
                                strike=strike,
                                expiration=exp_str,
                                premium=bid,
                                annualized_yield=annualized,
                                delta=delta,
                                iv=iv,
                                iv_rank=None,  # TODO: calculate IV rank
                                score=score,
                                headline=f"🔥 {symbol} IV at {iv:.0f}% - Sell ${strike:.0f} put for {annualized:.0f}% annualized",
                                details=f"Stock: ${price:.2f} ({change_pct:+.1f}%), Strike: ${strike:.0f} ({otm_pct:.1f}% OTM), "
                                       f"Bid: ${bid:.2f}, Delta: {delta:.2f}, DTE: {dte}, OI: {oi}",
                                timestamp=datetime.now()
                            )
                            opportunities.append(opp)
        
        # Sort by score then annualized yield
        opportunities.sort(key=lambda x: (-x.score, -x.annualized_yield))
        return opportunities[:10]  # Top 10
    
    def _calculate_score(self, iv, annualized, spread_pct, oi, otm_pct, delta, structure=None) -> int:
        """Calculate opportunity score (1-5 stars) with professional metrics"""
        score = 0
        
        # IV component (0-2 points) - use percentile if available
        if structure and hasattr(structure, 'iv_percentile'):
            if structure.iv_percentile >= 80:
                score += 2
            elif structure.iv_percentile >= 60:
                score += 1.5
            elif structure.iv_percentile >= 40:
                score += 1
        else:
            if iv >= 60:
                score += 2
            elif iv >= 50:
                score += 1
        
        # Yield component (0-1.5 points)
        if annualized >= 40:
            score += 1.5
        elif annualized >= 30:
            score += 1
        elif annualized >= 20:
            score += 0.5
        
        # Liquidity component (0-1 point) - use grade if available
        if structure and hasattr(structure, 'liquidity_grade'):
            if structure.liquidity_grade == 'A':
                score += 1
            elif structure.liquidity_grade == 'B':
                score += 0.75
            elif structure.liquidity_grade == 'C':
                score += 0.5
        else:
            if spread_pct < 10 and oi > 200:
                score += 1
            elif spread_pct < 15 and oi > 50:
                score += 0.5
        
        # Safety component (0-0.5 points)
        if otm_pct >= 8:
            score += 0.5
        
        # Sentiment bonus/penalty
        if structure and hasattr(structure, 'sentiment_signal'):
            if structure.sentiment_signal == 'BULLISH':
                score += 0.25
            elif structure.sentiment_signal == 'BEARISH':
                score -= 0.25
        
        return min(5, max(1, int(round(score))))


class CoveredCallStrategy:
    """Identifies covered call opportunities on existing positions
    
    IMPORTANT: This strategy now incorporates analyst consensus targets
    to prevent recommending calls that sacrifice massive upside for tiny premiums.
    A covered call is ONLY recommended when the opportunity cost is reasonable
    relative to the premium collected.
    """
    
    # Analyst consensus targets - UPDATE PERIODICALLY via web search
    # Format: symbol -> {target: avg_analyst_target, rating: consensus_rating, updated: date}
    # Updated from StockAnalysis, TipRanks, MarketBeat — Feb 9, 2026
    ANALYST_TARGETS = {
        'MSFT':  {'target': 598, 'rating': 'Strong Buy', 'updated': '2026-02-09'},  # 33 analysts, range $392-$678
        'NVDA':  {'target': 260, 'rating': 'Strong Buy', 'updated': '2026-02-09'},  # 39 analysts, range $165-$352
        'ORCL':  {'target': 310, 'rating': 'Buy', 'updated': '2026-02-09'},          # 32 analysts, range $175-$400
        'AMZN':  {'target': 296, 'rating': 'Strong Buy', 'updated': '2026-02-09'},  # 45 analysts
        'TSLA':  {'target': 394, 'rating': 'Hold', 'updated': '2026-02-09'},         # 30 analysts, range $25-$600
        'AMD':   {'target': 257, 'rating': 'Strong Buy', 'updated': '2026-02-09'},  # 33 analysts, range $120-$345
        'COST':  {'target': 1056, 'rating': 'Buy', 'updated': '2026-02-09'},         # 21 analysts, range $769-$1225
        'CRDO':  {'target': 90, 'rating': 'Buy', 'updated': '2026-02-09'},           # Estimate
        'INTC':  {'target': 25, 'rating': 'Hold', 'updated': '2026-02-09'},          # Consensus
    }
    
    # Minimum strike-to-target ratio: only sell calls at strikes >= X% of analyst target
    MIN_STRIKE_TO_TARGET_RATIO = 0.90  # Strike must be >= 90% of analyst target
    
    # Maximum opportunity cost ratio: premium must be >= X% of foregone upside
    # If you'd give up $19K upside for $270 premium, that's 1.4% - way below threshold
    MIN_PREMIUM_TO_OPPORTUNITY_COST = 0.15  # Premium should be >= 15% of opportunity cost
    
    def __init__(self, client: SchwabClient):
        self.client = client
        # Try to load fresh targets from DB (populated by analyst_targets module)
        self._load_targets_from_db()
    
    def _load_targets_from_db(self):
        """Load analyst targets from SQLite, overriding hardcoded defaults"""
        try:
            from analyst_targets import load_all_targets
            db_targets = load_all_targets()
            for symbol, t in db_targets.items():
                if t.target > 0 and not t.error:
                    self.ANALYST_TARGETS[symbol] = {
                        'target': t.target,
                        'rating': t.rating,
                        'updated': t.updated,
                    }
            if db_targets:
                print(f"   📊 Loaded {len(db_targets)} analyst targets from database")
        except Exception as e:
            print(f"   ⚠️ Could not load analyst targets from DB, using hardcoded: {e}")
    
    def scan(self, positions: list, min_annualized: float = 15) -> list:
        """Scan positions for covered call opportunities with strategic intelligence.
        
        KEY IMPROVEMENT: Now checks analyst consensus targets before recommending.
        Won't suggest selling calls that sacrifice massive upside for tiny premiums.
        """
        opportunities = []
        strategic_holds = []  # Stocks where we recommend NOT selling calls
        
        for pos in positions:
            instrument = pos.get('instrument', {})
            if instrument.get('assetType') != 'EQUITY':
                continue
            
            symbol = instrument.get('symbol', '')
            qty = pos.get('longQuantity', 0)
            avg_cost = pos.get('averagePrice', 0)
            
            if qty < 100:  # Need 100 shares for covered call
                continue
            
            contracts_available = int(qty // 100)
            
            # Get current price
            quote = self.client.get_quote(symbol)
            if not quote:
                continue
            
            price = quote.get('lastPrice', 0)
            change_pct = quote.get('netPercentChange', 0)
            
            # Skip if underwater (don't sell calls below cost)
            if price < avg_cost:
                continue
            
            gain_pct = (price / avg_cost - 1) * 100
            
            # ====== STRATEGIC INTELLIGENCE: Check analyst targets ======
            target_info = self.ANALYST_TARGETS.get(symbol)
            analyst_target = target_info['target'] if target_info else None
            analyst_rating = target_info['rating'] if target_info else 'Unknown'
            analyst_upside_pct = ((analyst_target - price) / price * 100) if analyst_target else None
            
            # If analyst target implies >25% upside AND rating is Buy/Strong Buy,
            # flag as strategic hold - don't sell standard covered calls
            if (analyst_target and analyst_upside_pct and analyst_upside_pct > 25 
                    and analyst_rating in ['Strong Buy', 'Buy']):
                strategic_holds.append(Opportunity(
                    symbol=symbol,
                    signal_type=SignalType.CONCENTRATION_WARNING,
                    action=ActionType.WAIT,
                    strike=analyst_target,
                    expiration='N/A',
                    premium=0,
                    annualized_yield=0,
                    delta=0,
                    iv=0,
                    iv_rank=None,
                    score=5,
                    headline=f"🛡️ {symbol} STRATEGIC HOLD — Don't sell covered calls below ${analyst_target:.0f}",
                    details=f"Analyst consensus: {analyst_rating} with ${analyst_target:.0f} target "
                           f"({analyst_upside_pct:.0f}% upside). Current: ${price:.2f}. "
                           f"You have {contracts_available} contracts of shares. "
                           f"Selling calls here caps upside for negligible premium.",
                    timestamp=datetime.now()
                ))
                
                # For strong buy with huge upside, only allow DEEP OTM calls
                # (strikes at or above 90% of analyst target)
                min_acceptable_strike = analyst_target * self.MIN_STRIKE_TO_TARGET_RATIO
            else:
                min_acceptable_strike = price * 1.02  # Default: at least 2% OTM
            
            # Get call options - wider range for stocks with high targets
            days_out = 90 if (analyst_upside_pct and analyst_upside_pct > 25) else 45
            chain = self.client.get_option_chain(symbol, 'CALL', strike_count=12, days_out=days_out)
            call_map = chain.get('callExpDateMap', {})
            
            for exp_date, strikes in call_map.items():
                exp_str = exp_date.split(':')[0]
                
                for strike_str, contracts in strikes.items():
                    for c in contracts:
                        strike = c.get('strikePrice', 0)
                        delta = c.get('delta', 0)
                        bid = c.get('bid', 0)
                        iv = c.get('volatility', 0)
                        dte = c.get('daysToExpiration', 1)
                        
                        # Filter: OTM calls with reasonable delta
                        if strike <= price:
                            continue
                        if not (0.05 <= delta <= 0.45):  # Wider range to find deep OTM
                            continue
                        if bid <= 0 or dte <= 0:
                            continue
                        
                        # ====== STRATEGIC FILTER: Check against analyst target ======
                        if strike < min_acceptable_strike:
                            continue  # Don't recommend strikes far below analyst target
                        
                        # Calculate metrics
                        annualized = (bid / price * 100) * (365 / dte)
                        upside_pct = (strike - price) / price * 100
                        total_return = (bid / price * 100) + upside_pct
                        
                        # ====== OPPORTUNITY COST CHECK ======
                        if analyst_target and strike < analyst_target:
                            foregone_per_share = analyst_target - strike
                            opportunity_cost = foregone_per_share * 100  # per contract
                            premium_collected = bid * 100
                            opp_cost_ratio = premium_collected / opportunity_cost if opportunity_cost > 0 else 999
                            
                            if opp_cost_ratio < self.MIN_PREMIUM_TO_OPPORTUNITY_COST:
                                continue  # Premium too small vs opportunity cost sacrificed
                        
                        if annualized < min_annualized:
                            continue
                        
                        # Score with analyst context
                        score = self._calculate_score(
                            gain_pct, annualized, delta, upside_pct,
                            analyst_target=analyst_target, analyst_rating=analyst_rating,
                            price=price, strike=strike
                        )
                        
                        if score >= 3:
                            # Determine strategy label with analyst context
                            if analyst_target and strike >= analyst_target:
                                strategy = "Above target — safe to sell"
                            elif analyst_target and strike >= analyst_target * 0.95:
                                strategy = "Near target — moderate risk"
                            elif delta > 0.35:
                                strategy = "Aggressive — may get called below target"
                            elif delta < 0.20:
                                strategy = "Conservative — low call probability"
                            else:
                                strategy = "Balanced"
                            
                            # Build details with analyst context
                            detail_parts = [
                                f"You have {contracts_available} contracts available.",
                                f"Bid: ${bid:.2f}, {annualized:.0f}% annualized,",
                                f"Total return if called: {total_return:.1f}%"
                            ]
                            if analyst_target:
                                detail_parts.append(
                                    f"| Analyst target: ${analyst_target:.0f} ({analyst_rating}), "
                                    f"strike captures {strike/analyst_target*100:.0f}% of target upside"
                                )
                            
                            opp = Opportunity(
                                symbol=symbol,
                                signal_type=SignalType.RALLY_EXHAUSTION,
                                action=ActionType.SELL_CALL,
                                strike=strike,
                                expiration=exp_str,
                                premium=bid,
                                annualized_yield=annualized,
                                delta=delta,
                                iv=iv,
                                iv_rank=None,
                                score=score,
                                headline=f"📈 {symbol} — Sell ${strike:.0f} calls ({strategy})",
                                details=' '.join(detail_parts),
                                timestamp=datetime.now()
                            )
                            opportunities.append(opp)
        
        # Sort by score
        opportunities.sort(key=lambda x: (-x.score, -x.annualized_yield))
        
        # Dedupe - keep best per symbol
        seen = set()
        unique = []
        for opp in opportunities:
            if opp.symbol not in seen:
                seen.add(opp.symbol)
                unique.append(opp)
        
        # Prepend strategic holds at the top so user sees them first
        return strategic_holds + unique[:10]
    
    def _calculate_score(self, gain_pct, annualized, delta, upside_pct,
                          analyst_target=None, analyst_rating=None,
                          price=None, strike=None) -> int:
        """Calculate opportunity score with analyst-aware intelligence.
        
        Key insight: A huge unrealized gain does NOT mean you should sell calls.
        If analyst consensus says 50% more upside, selling calls is destructive.
        Score should reflect whether capping upside makes strategic sense.
        """
        score = 0
        
        # ====== ANALYST CONTEXT (0 to -2 penalty or +1 bonus) ======
        if analyst_target and price and strike:
            target_upside_pct = (analyst_target - price) / price * 100
            strike_captures_pct = (strike - price) / (analyst_target - price) * 100 if analyst_target > price else 100
            
            if strike >= analyst_target:
                # Strike is ABOVE analyst target - excellent, safe to sell
                score += 1.5
            elif strike_captures_pct >= 80:
                # Strike captures 80%+ of expected move - acceptable
                score += 0.5
            elif strike_captures_pct >= 50:
                # Gives up significant upside - penalty
                score -= 0.5
            else:
                # Gives up majority of expected upside - heavy penalty
                score -= 1.5
            
            # Additional penalty for Strong Buy stocks with low strikes
            if analyst_rating == 'Strong Buy' and strike_captures_pct < 60:
                score -= 1
        
        # Yield quality (0-1.5 points)
        if annualized >= 30:
            score += 1.5
        elif annualized >= 20:
            score += 1
        elif annualized >= 15:
            score += 0.5
        
        # Upside room to strike (0-1 point)
        if upside_pct >= 15:
            score += 1
        elif upside_pct >= 8:
            score += 0.5
        
        # Low delta bonus (less likely to be called)
        if delta < 0.15:
            score += 0.5
        
        # Rating-appropriate bonus for Hold/Sell rated stocks
        # (These stocks are GOOD candidates for covered calls)
        if analyst_rating in ['Hold', 'Sell']:
            score += 1
        
        return min(5, max(1, int(round(score))))


# ============================================================================
# ALERT SYSTEM
# ============================================================================

class AlertManager:
    """Manages alert delivery across channels"""
    
    def __init__(self, db_path: str = "scanner.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                signal_type TEXT,
                action TEXT,
                strike REAL,
                expiration TEXT,
                premium REAL,
                annualized_yield REAL,
                score INTEGER,
                headline TEXT,
                details TEXT,
                timestamp TEXT,
                notified INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER,
                channel TEXT,
                sent_at TEXT,
                FOREIGN KEY (alert_id) REFERENCES alerts(id)
            )
        ''')
        conn.commit()
        conn.close()
    
    def save_opportunity(self, opp: Opportunity) -> int:
        """Save opportunity to database, return ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            INSERT INTO alerts (symbol, signal_type, action, strike, expiration, 
                               premium, annualized_yield, score, headline, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (opp.symbol, opp.signal_type.value, opp.action.value, opp.strike,
              opp.expiration, opp.premium, opp.annualized_yield, opp.score,
              opp.headline, opp.details, opp.timestamp.isoformat()))
        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return alert_id
    
    def is_duplicate(self, opp: Opportunity, hours: int = 4) -> bool:
        """Check if similar alert was sent recently"""
        conn = sqlite3.connect(self.db_path)
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor = conn.execute('''
            SELECT COUNT(*) FROM alerts 
            WHERE symbol = ? AND strike = ? AND expiration = ? 
            AND timestamp > ?
        ''', (opp.symbol, opp.strike, opp.expiration, cutoff))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    
    def send_desktop_notification(self, opp: Opportunity):
        """Send macOS desktop notification"""
        title = f"🎯 {opp.action.value}: {opp.symbol}"
        message = opp.headline
        
        script = f'''
        display notification "{message}" with title "{title}" sound name "Glass"
        '''
        
        try:
            subprocess.run(['osascript', '-e', script], check=True)
            print(f"✅ Desktop notification sent: {opp.symbol}")
        except Exception as e:
            print(f"❌ Notification failed: {e}")
    
    def send_alert(self, opp: Opportunity, channels: list = ['desktop']):
        """Send alert through specified channels"""
        if self.is_duplicate(opp):
            print(f"⏭️ Skipping duplicate alert: {opp.symbol} ${opp.strike}")
            return
        
        alert_id = self.save_opportunity(opp)
        
        for channel in channels:
            if channel == 'desktop':
                self.send_desktop_notification(opp)
            # TODO: Add SMS, email channels
        
        # Record that we sent notifications
        conn = sqlite3.connect(self.db_path)
        conn.execute('UPDATE alerts SET notified = 1 WHERE id = ?', (alert_id,))
        conn.commit()
        conn.close()


# ============================================================================
# MAIN SCANNER
# ============================================================================

class SmartScanner:
    """Main scanner orchestrator"""
    
    def __init__(self):
        self.client = SchwabClient()
        self.alert_manager = AlertManager()
        self.iv_strategy = IVSpikeStrategy(self.client)
        self.call_strategy = CoveredCallStrategy(self.client)
        
        # Default watchlist -- EXAMPLE symbols only, replace with your own
        self.watchlist = [
            # Diversification targets (example)
            'VST', 'CEG', 'UNH', 'RTX', 'LMT', 'PFE', 'JNJ', 'NEE',
            # Example holdings -- replace with your actual positions
            'NVDA', 'ORCL', 'AMD', 'TSLA', 'AMZN', 'MSFT'
        ]
    
    def run_scan(self, alert_channels: list = ['desktop']):
        """Run full scan across all strategies"""
        print("\n" + "="*60)
        print(f"🔍 SMART SCANNER - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60)
        
        all_opportunities = []
        
        # 1. Scan for IV spike put opportunities
        print("\n📊 Scanning for IV spikes (put selling)...")
        iv_opps = self.iv_strategy.scan(self.watchlist)
        all_opportunities.extend(iv_opps)
        print(f"   Found {len(iv_opps)} opportunities")
        
        # 2. Scan positions for covered calls
        print("\n📊 Scanning positions for covered calls...")
        positions = self.client.get_positions()
        call_opps = self.call_strategy.scan(positions)
        all_opportunities.extend(call_opps)
        print(f"   Found {len(call_opps)} opportunities")
        
        # 3. Sort all by score
        all_opportunities.sort(key=lambda x: (-x.score, -x.annualized_yield))
        
        # 4. Display and alert
        print("\n" + "="*60)
        print("🎯 TOP OPPORTUNITIES")
        print("="*60)
        
        for i, opp in enumerate(all_opportunities[:10], 1):
            stars = "⭐" * opp.score
            print(f"\n{i}. {stars}")
            print(f"   {opp.headline}")
            print(f"   {opp.details}")
            
            # Send alerts for 4+ star opportunities
            if opp.score >= 4:
                self.alert_manager.send_alert(opp, alert_channels)
        
        return all_opportunities
    
    def get_market_status(self) -> bool:
        """Check if market is open"""
        now = datetime.now()
        # Simple check - weekday, 6:30 AM - 1:00 PM PT (market hours)
        if now.weekday() >= 5:  # Weekend
            return False
        hour = now.hour
        if 6 <= hour <= 13:  # Rough market hours PT
            return True
        return False


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """Run scanner from command line"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Smart Options Scanner')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', type=int, default=5, help='Scan interval in minutes')
    parser.add_argument('--no-alerts', action='store_true', help='Disable notifications')
    args = parser.parse_args()
    
    scanner = SmartScanner()
    channels = [] if args.no_alerts else ['desktop']
    
    if args.once:
        scanner.run_scan(channels)
    else:
        import time
        print(f"🚀 Starting continuous scan (every {args.interval} minutes)")
        print("   Press Ctrl+C to stop\n")
        
        while True:
            try:
                if scanner.get_market_status():
                    scanner.run_scan(channels)
                else:
                    print(f"💤 Market closed - waiting... ({datetime.now().strftime('%H:%M')})")
                
                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print("\n\n👋 Scanner stopped.")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                time.sleep(60)  # Wait a minute on error


if __name__ == '__main__':
    main()
