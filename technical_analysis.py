"""
Technical Analysis Module - Support/Resistance Detection
Uses price history to identify key levels
"""

import httpx
import json
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Tuple, Optional
import statistics

@dataclass
class TechnicalLevel:
    """Represents a support or resistance level"""
    price: float
    level_type: str  # 'support' or 'resistance'
    strength: int    # 1-5 (number of touches)
    last_touch: datetime
    description: str

@dataclass 
class TechnicalAnalysis:
    """Complete technical analysis for a symbol"""
    symbol: str
    current_price: float
    change_pct: float
    
    # Moving averages
    sma_20: Optional[float]
    sma_50: Optional[float]
    sma_200: Optional[float]
    
    # RSI
    rsi_14: Optional[float]
    
    # Support/Resistance
    supports: List[TechnicalLevel]
    resistances: List[TechnicalLevel]
    
    # Signals
    near_support: bool
    near_resistance: bool
    oversold: bool
    overbought: bool
    
    # Overall signal
    signal: str  # 'SELL_PUTS', 'SELL_CALLS', 'WAIT'
    signal_strength: int  # 1-5


class TechnicalAnalyzer:
    """Analyzes price action and identifies key levels"""
    
    def __init__(self):
        pass  # Token managed by token_manager
    
    @property
    def headers(self):
        # Import here to avoid circular imports
        from token_manager import get_headers
        return get_headers()
    
    def get_price_history(self, symbol: str, days: int = 200) -> List[dict]:
        """Fetch historical price data from Schwab"""
        with httpx.Client(timeout=30) as client:
            # Schwab price history endpoint
            resp = client.get(
                f'https://api.schwabapi.com/marketdata/v1/pricehistory',
                headers=self.headers,
                params={
                    'symbol': symbol,
                    'periodType': 'year',
                    'period': 1,
                    'frequencyType': 'daily',
                    'frequency': 1,
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                candles = data.get('candles', [])
                return candles
        return []
    
    def calculate_sma(self, prices: List[float], period: int) -> Optional[float]:
        """Calculate Simple Moving Average"""
        if len(prices) >= period:
            return statistics.mean(prices[-period:])
        return None
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """Calculate Relative Strength Index"""
        if len(prices) < period + 1:
            return None
        
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        recent_changes = changes[-period:]
        
        gains = [c for c in recent_changes if c > 0]
        losses = [-c for c in recent_changes if c < 0]
        
        avg_gain = statistics.mean(gains) if gains else 0
        avg_loss = statistics.mean(losses) if losses else 0.001
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def find_support_resistance(self, candles: List[dict], 
                                 num_levels: int = 3) -> Tuple[List[TechnicalLevel], List[TechnicalLevel]]:
        """Identify support and resistance levels from price history"""
        if len(candles) < 20:
            return [], []
        
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        closes = [c['close'] for c in candles]
        
        current_price = closes[-1]
        
        # Find pivot points (local highs and lows)
        pivot_highs = []
        pivot_lows = []
        
        for i in range(2, len(candles) - 2):
            # Local high
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
               highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                pivot_highs.append((highs[i], i))
            
            # Local low
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
               lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                pivot_lows.append((lows[i], i))
        
        # Cluster nearby levels
        def cluster_levels(pivots, threshold_pct=0.02):
            if not pivots:
                return []
            
            pivots = sorted(pivots, key=lambda x: x[0])
            clusters = []
            current_cluster = [pivots[0]]
            
            for pivot in pivots[1:]:
                if abs(pivot[0] - current_cluster[-1][0]) / current_cluster[-1][0] < threshold_pct:
                    current_cluster.append(pivot)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [pivot]
            clusters.append(current_cluster)
            
            # Return cluster centers with strength
            result = []
            for cluster in clusters:
                avg_price = statistics.mean([p[0] for p in cluster])
                strength = len(cluster)
                last_idx = max([p[1] for p in cluster])
                result.append((avg_price, strength, last_idx))
            
            return result
        
        # Get support levels (below current price)
        support_clusters = cluster_levels(pivot_lows)
        supports = []
        for price, strength, idx in support_clusters:
            if price < current_price:
                distance_pct = (current_price - price) / current_price * 100
                supports.append(TechnicalLevel(
                    price=round(price, 2),
                    level_type='support',
                    strength=min(5, strength),
                    last_touch=datetime.now() - timedelta(days=len(candles) - idx),
                    description=f"Support at ${price:.2f} ({distance_pct:.1f}% below) - tested {strength}x"
                ))
        
        # Get resistance levels (above current price)
        resistance_clusters = cluster_levels(pivot_highs)
        resistances = []
        for price, strength, idx in resistance_clusters:
            if price > current_price:
                distance_pct = (price - current_price) / current_price * 100
                resistances.append(TechnicalLevel(
                    price=round(price, 2),
                    level_type='resistance',
                    strength=min(5, strength),
                    last_touch=datetime.now() - timedelta(days=len(candles) - idx),
                    description=f"Resistance at ${price:.2f} ({distance_pct:.1f}% above) - tested {strength}x"
                ))
        
        # Sort by distance from current price
        supports.sort(key=lambda x: current_price - x.price)
        resistances.sort(key=lambda x: x.price - current_price)
        
        return supports[:num_levels], resistances[:num_levels]
    
    def analyze(self, symbol: str) -> Optional[TechnicalAnalysis]:
        """Perform complete technical analysis on a symbol"""
        
        # Get current quote
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f'https://api.schwabapi.com/marketdata/v1/quotes',
                headers=self.headers,
                params={'symbols': symbol}
            )
            if resp.status_code != 200:
                return None
            
            quote_data = resp.json()
            quote = quote_data.get(symbol, {}).get('quote', {})
            current_price = quote.get('lastPrice', 0)
            change_pct = quote.get('netPercentChange', 0)
            high_52 = quote.get('52WeekHigh', 0)
            low_52 = quote.get('52WeekLow', 0)
        
        # Get price history
        candles = self.get_price_history(symbol)
        
        if not candles or current_price == 0:
            return None
        
        closes = [c['close'] for c in candles]
        
        # Calculate indicators
        sma_20 = self.calculate_sma(closes, 20)
        sma_50 = self.calculate_sma(closes, 50)
        sma_200 = self.calculate_sma(closes, 200)
        rsi_14 = self.calculate_rsi(closes, 14)
        
        # Find support/resistance
        supports, resistances = self.find_support_resistance(candles)
        
        # Add 52-week levels
        if low_52 and low_52 < current_price:
            supports.append(TechnicalLevel(
                price=low_52,
                level_type='support',
                strength=5,
                last_touch=datetime.now(),
                description=f"52-week low at ${low_52:.2f}"
            ))
        
        if high_52 and high_52 > current_price:
            resistances.append(TechnicalLevel(
                price=high_52,
                level_type='resistance', 
                strength=5,
                last_touch=datetime.now(),
                description=f"52-week high at ${high_52:.2f}"
            ))
        
        # Add MA levels
        if sma_50 and sma_50 < current_price:
            supports.append(TechnicalLevel(
                price=round(sma_50, 2),
                level_type='support',
                strength=4,
                last_touch=datetime.now(),
                description=f"50-day MA at ${sma_50:.2f}"
            ))
        elif sma_50:
            resistances.append(TechnicalLevel(
                price=round(sma_50, 2),
                level_type='resistance',
                strength=4,
                last_touch=datetime.now(),
                description=f"50-day MA at ${sma_50:.2f}"
            ))
        
        # Sort again after adding levels
        supports.sort(key=lambda x: current_price - x.price)
        resistances.sort(key=lambda x: x.price - current_price)
        
        # Determine signals
        near_support = False
        near_resistance = False
        
        if supports:
            nearest_support = supports[0].price
            if (current_price - nearest_support) / current_price < 0.03:
                near_support = True
        
        if resistances:
            nearest_resistance = resistances[0].price
            if (nearest_resistance - current_price) / current_price < 0.03:
                near_resistance = True
        
        oversold = rsi_14 is not None and rsi_14 < 35
        overbought = rsi_14 is not None and rsi_14 > 70
        
        # Determine overall signal
        signal = 'WAIT'
        signal_strength = 1
        
        if near_support and oversold:
            signal = 'SELL_PUTS'
            signal_strength = 5
        elif near_support:
            signal = 'SELL_PUTS'
            signal_strength = 4
        elif oversold:
            signal = 'SELL_PUTS'
            signal_strength = 3
        elif near_resistance and overbought:
            signal = 'SELL_CALLS'
            signal_strength = 5
        elif near_resistance:
            signal = 'SELL_CALLS'
            signal_strength = 4
        elif overbought:
            signal = 'SELL_CALLS'
            signal_strength = 3
        
        return TechnicalAnalysis(
            symbol=symbol,
            current_price=current_price,
            change_pct=change_pct,
            sma_20=sma_20,
            sma_50=sma_50,
            sma_200=sma_200,
            rsi_14=rsi_14,
            supports=supports[:5],
            resistances=resistances[:5],
            near_support=near_support,
            near_resistance=near_resistance,
            oversold=oversold,
            overbought=overbought,
            signal=signal,
            signal_strength=signal_strength
        )
    
    def to_dict(self, analysis: TechnicalAnalysis) -> dict:
        """Convert analysis to dictionary for JSON serialization"""
        return {
            'symbol': analysis.symbol,
            'current_price': analysis.current_price,
            'change_pct': analysis.change_pct,
            'sma_20': analysis.sma_20,
            'sma_50': analysis.sma_50,
            'sma_200': analysis.sma_200,
            'rsi_14': analysis.rsi_14,
            'supports': [
                {'price': s.price, 'strength': s.strength, 'description': s.description}
                for s in analysis.supports
            ],
            'resistances': [
                {'price': r.price, 'strength': r.strength, 'description': r.description}
                for r in analysis.resistances
            ],
            'near_support': analysis.near_support,
            'near_resistance': analysis.near_resistance,
            'oversold': analysis.oversold,
            'overbought': analysis.overbought,
            'signal': analysis.signal,
            'signal_strength': analysis.signal_strength
        }


# Test
if __name__ == '__main__':
    analyzer = TechnicalAnalyzer()
    
    symbols = ['NVDA', 'VST', 'ORCL', 'UNH']
    
    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"📊 Technical Analysis: {symbol}")
        print('='*60)
        
        analysis = analyzer.analyze(symbol)
        
        if analysis:
            print(f"Price: ${analysis.current_price:.2f} ({analysis.change_pct:+.1f}%)")
            print(f"RSI(14): {analysis.rsi_14:.1f}" if analysis.rsi_14 else "RSI: N/A")
            print(f"50-day MA: ${analysis.sma_50:.2f}" if analysis.sma_50 else "")
            
            print(f"\n📉 Support Levels:")
            for s in analysis.supports[:3]:
                print(f"   ${s.price:.2f} (strength: {'⭐'*s.strength})")
            
            print(f"\n📈 Resistance Levels:")
            for r in analysis.resistances[:3]:
                print(f"   ${r.price:.2f} (strength: {'⭐'*r.strength})")
            
            print(f"\n🎯 Signal: {analysis.signal} (strength: {analysis.signal_strength}/5)")
            
            if analysis.oversold:
                print("   ⚠️ OVERSOLD - Good for selling puts")
            if analysis.overbought:
                print("   ⚠️ OVERBOUGHT - Good for selling calls")
            if analysis.near_support:
                print("   📍 Near support level")
            if analysis.near_resistance:
                print("   📍 Near resistance level")
