"""
Options Market Structure Analyzer
Professional-grade quantitative metrics for options trading
"""

import httpx
import json
import statistics
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from pathlib import Path

@dataclass
class OptionsStructure:
    """Comprehensive options market structure analysis"""
    symbol: str
    underlying_price: float
    timestamp: datetime
    
    # IV Analysis
    current_iv: float              # ATM IV
    iv_percentile: float           # Where current IV sits vs 52-week range (0-100)
    iv_rank: float                 # (Current - 52w Low) / (52w High - 52w Low)
    iv_52w_high: float
    iv_52w_low: float
    iv_signal: str                 # 'RICH', 'FAIR', 'CHEAP'
    
    # Expected Move
    expected_move_dollars: float   # ATM straddle price
    expected_move_pct: float       # As percentage of stock price
    expected_move_range: Tuple[float, float]  # (lower, upper) bounds
    
    # Put/Call Analysis
    put_call_ratio: float          # Put volume / Call volume
    put_call_oi_ratio: float       # Put OI / Call OI
    sentiment_signal: str          # 'BULLISH', 'NEUTRAL', 'BEARISH'
    
    # Key OI Levels (price magnets/walls)
    max_pain: float                # Strike with max open interest pain
    put_wall: float                # Highest put OI strike
    call_wall: float               # Highest call OI strike
    key_oi_levels: List[Dict]      # Top OI strikes with details
    
    # Volatility Skew
    skew_25delta: float            # IV of 25-delta put - 25-delta call
    skew_signal: str               # 'FEAR' (puts expensive), 'GREED' (calls expensive), 'NEUTRAL'
    put_skew_premium: float        # How much more expensive puts are vs calls
    
    # Liquidity Analysis
    avg_bid_ask_spread_pct: float  # Average spread as % of premium
    liquidity_grade: str           # 'A', 'B', 'C', 'D', 'F'
    liquidity_warning: str         # Warning message if illiquid
    
    # Term Structure
    front_month_iv: float
    back_month_iv: float
    term_structure: str            # 'CONTANGO', 'BACKWARDATION', 'FLAT'
    
    # Trading Recommendations
    optimal_strategy: str
    risk_warnings: List[str]
    entry_quality: str             # 'EXCELLENT', 'GOOD', 'FAIR', 'POOR'


class OptionsStructureAnalyzer:
    """Analyzes options market structure for professional trading insights"""
    
    def __init__(self):
        from token_manager import get_headers
        self.get_headers = get_headers
        self.base_url = "https://api.schwabapi.com"
        
        # IV history tracker for accurate percentiles
        from iv_tracker import IVHistoryTracker
        self.iv_tracker = IVHistoryTracker()
    
    def analyze(self, symbol: str) -> Optional[OptionsStructure]:
        """Perform complete options structure analysis"""
        symbol = symbol.upper()
        
        headers = self.get_headers()
        
        with httpx.Client(timeout=60) as client:
            # Get underlying quote
            quote_resp = client.get(
                f'{self.base_url}/marketdata/v1/quotes',
                headers=headers,
                params={'symbols': symbol}
            )
            
            if quote_resp.status_code != 200:
                print(f"Failed to get quote: {quote_resp.status_code}")
                return None
            
            quote_data = quote_resp.json()
            quote = quote_data.get(symbol, {}).get('quote', {})
            underlying_price = quote.get('lastPrice', 0)
            
            if underlying_price == 0:
                return None
            
            # Get full option chain (multiple expirations)
            chain_resp = client.get(
                f'{self.base_url}/marketdata/v1/chains',
                headers=headers,
                params={
                    'symbol': symbol,
                    'contractType': 'ALL',
                    'strikeCount': 30,
                    'includeUnderlyingQuote': 'true',
                    'range': 'ALL'
                }
            )
            
            if chain_resp.status_code != 200:
                print(f"Failed to get chain: {chain_resp.status_code}")
                return None
            
            chain = chain_resp.json()
            
            # Parse the chain
            call_map = chain.get('callExpDateMap', {})
            put_map = chain.get('putExpDateMap', {})
            
            if not call_map or not put_map:
                print("Empty option chain")
                return None
            
            # === CALCULATE ALL METRICS ===
            
            # 1. IV Analysis
            iv_data = self._analyze_iv(symbol, underlying_price, call_map, put_map)
            
            # 2. Expected Move (from nearest expiration ATM straddle)
            expected_move = self._calculate_expected_move(underlying_price, call_map, put_map)
            
            # 3. Put/Call Ratio and OI Analysis
            pc_data = self._analyze_put_call(call_map, put_map)
            
            # 4. Key OI Levels
            oi_levels = self._find_oi_levels(underlying_price, call_map, put_map)
            
            # 5. Volatility Skew
            skew_data = self._analyze_skew(underlying_price, call_map, put_map)
            
            # 6. Liquidity Analysis
            liquidity = self._analyze_liquidity(underlying_price, call_map, put_map)
            
            # 7. Term Structure
            term_structure = self._analyze_term_structure(call_map, put_map)
            
            # 8. Generate Recommendations
            recommendations = self._generate_recommendations(
                symbol, underlying_price, iv_data, expected_move, 
                pc_data, skew_data, liquidity, term_structure
            )
            
            return OptionsStructure(
                symbol=symbol,
                underlying_price=underlying_price,
                timestamp=datetime.now(),
                
                # IV
                current_iv=iv_data['current_iv'],
                iv_percentile=iv_data['iv_percentile'],
                iv_rank=iv_data['iv_rank'],
                iv_52w_high=iv_data['iv_high'],
                iv_52w_low=iv_data['iv_low'],
                iv_signal=iv_data['signal'],
                
                # Expected Move
                expected_move_dollars=expected_move['dollars'],
                expected_move_pct=expected_move['pct'],
                expected_move_range=expected_move['range'],
                
                # Put/Call
                put_call_ratio=pc_data['volume_ratio'],
                put_call_oi_ratio=pc_data['oi_ratio'],
                sentiment_signal=pc_data['sentiment'],
                
                # OI Levels
                max_pain=oi_levels['max_pain'],
                put_wall=oi_levels['put_wall'],
                call_wall=oi_levels['call_wall'],
                key_oi_levels=oi_levels['key_levels'],
                
                # Skew
                skew_25delta=skew_data['skew_25d'],
                skew_signal=skew_data['signal'],
                put_skew_premium=skew_data['put_premium'],
                
                # Liquidity
                avg_bid_ask_spread_pct=liquidity['avg_spread_pct'],
                liquidity_grade=liquidity['grade'],
                liquidity_warning=liquidity['warning'],
                
                # Term Structure
                front_month_iv=term_structure['front_iv'],
                back_month_iv=term_structure['back_iv'],
                term_structure=term_structure['structure'],
                
                # Recommendations
                optimal_strategy=recommendations['strategy'],
                risk_warnings=recommendations['warnings'],
                entry_quality=recommendations['quality']
            )
    
    def _analyze_iv(self, symbol: str, price: float, call_map: dict, put_map: dict) -> dict:
        """Analyze implied volatility metrics"""
        
        # Find ATM options in nearest expiration
        ivs = []
        atm_iv = 0
        
        # Get first expiration
        first_exp = list(call_map.keys())[0] if call_map else None
        
        if first_exp and first_exp in call_map:
            for strike_str, contracts in call_map[first_exp].items():
                for c in contracts:
                    strike = c.get('strikePrice', 0)
                    iv = c.get('volatility', 0)
                    if iv > 0:
                        ivs.append(iv)
                        # Check if ATM
                        if abs(strike - price) / price < 0.02:
                            atm_iv = iv
        
        if first_exp and first_exp in put_map:
            for strike_str, contracts in put_map[first_exp].items():
                for c in contracts:
                    strike = c.get('strikePrice', 0)
                    iv = c.get('volatility', 0)
                    if iv > 0:
                        ivs.append(iv)
                        if abs(strike - price) / price < 0.02:
                            if atm_iv == 0:
                                atm_iv = iv
                            else:
                                atm_iv = (atm_iv + iv) / 2  # Average call/put ATM IV
        
        current_iv = atm_iv if atm_iv > 0 else (statistics.mean(ivs) if ivs else 30)
        
        # Record today's IV for future percentile calculations
        self.iv_tracker.record_iv(symbol, current_iv)
        
        # Get IV percentile and rank from historical data
        iv_percentile = self.iv_tracker.calculate_iv_percentile(symbol, current_iv)
        iv_rank = self.iv_tracker.calculate_iv_rank(symbol, current_iv)
        iv_low, iv_high = self.iv_tracker.get_52w_range(symbol)
        
        # Determine signal based on percentile
        if iv_percentile >= 70:
            iv_signal = 'RICH'
        elif iv_percentile <= 30:
            iv_signal = 'CHEAP'
        else:
            iv_signal = 'FAIR'
        
        return {
            'current_iv': current_iv,
            'iv_percentile': iv_percentile,
            'iv_rank': iv_rank,
            'iv_high': iv_high,
            'iv_low': iv_low,
            'signal': iv_signal
        }
    
    def _calculate_expected_move(self, price: float, call_map: dict, put_map: dict) -> dict:
        """Calculate expected move from ATM straddle"""
        
        # Find nearest expiration
        expirations = sorted(call_map.keys())
        if not expirations:
            return {'dollars': 0, 'pct': 0, 'range': (price, price)}
        
        # Get first expiration (usually weekly or next monthly)
        first_exp = expirations[0]
        
        # Find ATM strike
        atm_strike = round(price / 5) * 5  # Round to nearest $5
        
        atm_call_price = 0
        atm_put_price = 0
        
        # Get ATM call
        if first_exp in call_map:
            for strike_str, contracts in call_map[first_exp].items():
                for c in contracts:
                    if abs(c.get('strikePrice', 0) - atm_strike) < 3:
                        atm_call_price = c.get('mark', 0) or c.get('last', 0)
                        break
        
        # Get ATM put
        if first_exp in put_map:
            for strike_str, contracts in put_map[first_exp].items():
                for c in contracts:
                    if abs(c.get('strikePrice', 0) - atm_strike) < 3:
                        atm_put_price = c.get('mark', 0) or c.get('last', 0)
                        break
        
        # Straddle price = expected move
        straddle_price = atm_call_price + atm_put_price
        expected_move_pct = (straddle_price / price) * 100 if price > 0 else 0
        
        lower_bound = price - straddle_price
        upper_bound = price + straddle_price
        
        return {
            'dollars': straddle_price,
            'pct': expected_move_pct,
            'range': (round(lower_bound, 2), round(upper_bound, 2))
        }
    
    def _analyze_put_call(self, call_map: dict, put_map: dict) -> dict:
        """Analyze put/call ratios"""
        
        total_put_volume = 0
        total_call_volume = 0
        total_put_oi = 0
        total_call_oi = 0
        
        for exp, strikes in call_map.items():
            for strike_str, contracts in strikes.items():
                for c in contracts:
                    total_call_volume += c.get('totalVolume', 0)
                    total_call_oi += c.get('openInterest', 0)
        
        for exp, strikes in put_map.items():
            for strike_str, contracts in strikes.items():
                for c in contracts:
                    total_put_volume += c.get('totalVolume', 0)
                    total_put_oi += c.get('openInterest', 0)
        
        volume_ratio = total_put_volume / total_call_volume if total_call_volume > 0 else 1
        oi_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 1
        
        # Interpret sentiment
        if oi_ratio < 0.7:
            sentiment = 'BULLISH'
        elif oi_ratio > 1.3:
            sentiment = 'BEARISH'
        else:
            sentiment = 'NEUTRAL'
        
        return {
            'volume_ratio': round(volume_ratio, 2),
            'oi_ratio': round(oi_ratio, 2),
            'sentiment': sentiment,
            'total_put_oi': total_put_oi,
            'total_call_oi': total_call_oi
        }
    
    def _find_oi_levels(self, price: float, call_map: dict, put_map: dict) -> dict:
        """Find key open interest levels that act as price magnets/walls"""
        
        # Aggregate OI by strike
        put_oi_by_strike = {}
        call_oi_by_strike = {}
        
        for exp, strikes in put_map.items():
            for strike_str, contracts in strikes.items():
                for c in contracts:
                    strike = c.get('strikePrice', 0)
                    oi = c.get('openInterest', 0)
                    put_oi_by_strike[strike] = put_oi_by_strike.get(strike, 0) + oi
        
        for exp, strikes in call_map.items():
            for strike_str, contracts in strikes.items():
                for c in contracts:
                    strike = c.get('strikePrice', 0)
                    oi = c.get('openInterest', 0)
                    call_oi_by_strike[strike] = call_oi_by_strike.get(strike, 0) + oi
        
        # Find put wall (highest put OI below current price)
        put_wall = 0
        max_put_oi = 0
        for strike, oi in put_oi_by_strike.items():
            if strike < price and oi > max_put_oi:
                max_put_oi = oi
                put_wall = strike
        
        # Find call wall (highest call OI above current price)
        call_wall = 0
        max_call_oi = 0
        for strike, oi in call_oi_by_strike.items():
            if strike > price and oi > max_call_oi:
                max_call_oi = oi
                call_wall = strike
        
        # Calculate max pain (strike where option holders lose most)
        max_pain = self._calculate_max_pain(price, put_oi_by_strike, call_oi_by_strike)
        
        # Top OI levels
        all_oi = []
        for strike, oi in put_oi_by_strike.items():
            all_oi.append({'strike': strike, 'type': 'PUT', 'oi': oi})
        for strike, oi in call_oi_by_strike.items():
            all_oi.append({'strike': strike, 'type': 'CALL', 'oi': oi})
        
        # Sort by OI and get top 5
        all_oi.sort(key=lambda x: x['oi'], reverse=True)
        key_levels = all_oi[:5]
        
        return {
            'max_pain': max_pain,
            'put_wall': put_wall,
            'call_wall': call_wall,
            'key_levels': key_levels
        }
    
    def _calculate_max_pain(self, current_price: float, put_oi: dict, call_oi: dict) -> float:
        """Calculate max pain strike"""
        
        all_strikes = set(put_oi.keys()) | set(call_oi.keys())
        if not all_strikes:
            return current_price
        
        min_pain = float('inf')
        max_pain_strike = current_price
        
        for test_strike in all_strikes:
            pain = 0
            
            # Put holders lose if price > strike
            for strike, oi in put_oi.items():
                if test_strike > strike:
                    pain += oi * (test_strike - strike)
            
            # Call holders lose if price < strike
            for strike, oi in call_oi.items():
                if test_strike < strike:
                    pain += oi * (strike - test_strike)
            
            if pain < min_pain:
                min_pain = pain
                max_pain_strike = test_strike
        
        return max_pain_strike
    
    def _analyze_skew(self, price: float, call_map: dict, put_map: dict) -> dict:
        """Analyze volatility skew"""
        
        # Find 25-delta options in nearest expiration
        first_exp = list(call_map.keys())[0] if call_map else None
        
        if not first_exp:
            return {'skew_25d': 0, 'signal': 'NEUTRAL', 'put_premium': 0}
        
        put_25d_iv = 0
        call_25d_iv = 0
        
        # Find ~25 delta put (OTM put around -0.25 delta)
        if first_exp in put_map:
            for strike_str, contracts in put_map[first_exp].items():
                for c in contracts:
                    delta = abs(c.get('delta', 0))
                    if 0.20 <= delta <= 0.30:
                        put_25d_iv = c.get('volatility', 0)
                        break
        
        # Find ~25 delta call (OTM call around 0.25 delta)
        if first_exp in call_map:
            for strike_str, contracts in call_map[first_exp].items():
                for c in contracts:
                    delta = c.get('delta', 0)
                    if 0.20 <= delta <= 0.30:
                        call_25d_iv = c.get('volatility', 0)
                        break
        
        # Skew = Put IV - Call IV
        skew = put_25d_iv - call_25d_iv
        put_premium = ((put_25d_iv / call_25d_iv) - 1) * 100 if call_25d_iv > 0 else 0
        
        if skew > 5:
            signal = 'FEAR'  # Puts expensive, market worried about downside
        elif skew < -5:
            signal = 'GREED'  # Calls expensive, market bullish
        else:
            signal = 'NEUTRAL'
        
        return {
            'skew_25d': round(skew, 2),
            'signal': signal,
            'put_premium': round(put_premium, 1)
        }
    
    def _analyze_liquidity(self, price: float, call_map: dict, put_map: dict) -> dict:
        """Analyze bid-ask spreads and liquidity"""
        
        spreads = []
        
        # Check ATM and near-ATM options
        first_exp = list(call_map.keys())[0] if call_map else None
        
        if first_exp:
            # Check calls
            if first_exp in call_map:
                for strike_str, contracts in call_map[first_exp].items():
                    for c in contracts:
                        strike = c.get('strikePrice', 0)
                        if abs(strike - price) / price < 0.10:  # Within 10% of ATM
                            bid = c.get('bid', 0)
                            ask = c.get('ask', 0)
                            mark = c.get('mark', 0)
                            if mark > 0:
                                spread_pct = (ask - bid) / mark * 100
                                spreads.append(spread_pct)
            
            # Check puts
            if first_exp in put_map:
                for strike_str, contracts in put_map[first_exp].items():
                    for c in contracts:
                        strike = c.get('strikePrice', 0)
                        if abs(strike - price) / price < 0.10:
                            bid = c.get('bid', 0)
                            ask = c.get('ask', 0)
                            mark = c.get('mark', 0)
                            if mark > 0:
                                spread_pct = (ask - bid) / mark * 100
                                spreads.append(spread_pct)
        
        avg_spread = statistics.mean(spreads) if spreads else 50
        
        # Grade liquidity
        if avg_spread < 3:
            grade = 'A'
            warning = ''
        elif avg_spread < 5:
            grade = 'B'
            warning = ''
        elif avg_spread < 10:
            grade = 'C'
            warning = 'Moderate spreads - use limit orders'
        elif avg_spread < 20:
            grade = 'D'
            warning = '⚠️ WIDE SPREADS - Significant slippage risk'
        else:
            grade = 'F'
            warning = '🚨 VERY WIDE SPREADS - May lose 5%+ on entry/exit'
        
        return {
            'avg_spread_pct': round(avg_spread, 1),
            'grade': grade,
            'warning': warning
        }
    
    def _analyze_term_structure(self, call_map: dict, put_map: dict) -> dict:
        """Analyze IV term structure across expirations"""
        
        expirations = sorted(call_map.keys())
        
        if len(expirations) < 2:
            return {'front_iv': 0, 'back_iv': 0, 'structure': 'UNKNOWN'}
        
        front_exp = expirations[0]
        back_exp = expirations[-1] if len(expirations) > 2 else expirations[1]
        
        def get_atm_iv(exp_map, exp_key):
            ivs = []
            if exp_key in exp_map:
                for strike_str, contracts in exp_map[exp_key].items():
                    for c in contracts:
                        iv = c.get('volatility', 0)
                        delta = abs(c.get('delta', 0))
                        if iv > 0 and 0.4 <= delta <= 0.6:  # Near ATM
                            ivs.append(iv)
            return statistics.mean(ivs) if ivs else 0
        
        front_iv = get_atm_iv(call_map, front_exp)
        back_iv = get_atm_iv(call_map, back_exp)
        
        if front_iv > 0 and back_iv > 0:
            if front_iv > back_iv * 1.1:
                structure = 'BACKWARDATION'  # Front IV higher (usually pre-event)
            elif back_iv > front_iv * 1.1:
                structure = 'CONTANGO'  # Back IV higher (normal)
            else:
                structure = 'FLAT'
        else:
            structure = 'UNKNOWN'
        
        return {
            'front_iv': round(front_iv, 1),
            'back_iv': round(back_iv, 1),
            'structure': structure
        }
    
    def _generate_recommendations(self, symbol: str, price: float, iv_data: dict,
                                   expected_move: dict, pc_data: dict, skew_data: dict,
                                   liquidity: dict, term_structure: dict) -> dict:
        """Generate trading recommendations based on all metrics"""
        
        warnings = []
        
        # Liquidity warning
        if liquidity['warning']:
            warnings.append(liquidity['warning'])
        
        # IV-based strategy
        if iv_data['signal'] == 'RICH':
            if term_structure['structure'] == 'BACKWARDATION':
                strategy = "SELL PREMIUM (but wait for event) - IV elevated pre-event"
                warnings.append("⚠️ Backwardation suggests upcoming event (earnings?) - wait")
            else:
                strategy = "SELL PREMIUM - IV is rich, good for credit strategies"
        elif iv_data['signal'] == 'CHEAP':
            strategy = "BUY PREMIUM or WAIT - IV is cheap, selling premium less attractive"
        else:
            strategy = "NEUTRAL - IV is fair, look for directional setups"
        
        # Skew-based adjustments
        if skew_data['signal'] == 'FEAR':
            warnings.append(f"Put skew +{skew_data['put_premium']:.0f}% - Market pricing downside risk")
        
        # Sentiment
        if pc_data['sentiment'] == 'BEARISH':
            warnings.append("High put/call OI ratio - Bearish positioning")
        
        # Expected move context
        if expected_move['pct'] > 8:
            warnings.append(f"High expected move ({expected_move['pct']:.1f}%) - Elevated event risk")
        
        # Determine entry quality
        quality_score = 0
        
        # Good IV for selling
        if iv_data['iv_percentile'] > 70:
            quality_score += 2
        elif iv_data['iv_percentile'] > 50:
            quality_score += 1
        
        # Good liquidity
        if liquidity['grade'] in ['A', 'B']:
            quality_score += 2
        elif liquidity['grade'] == 'C':
            quality_score += 1
        else:
            quality_score -= 1
        
        # No backwardation (no event)
        if term_structure['structure'] != 'BACKWARDATION':
            quality_score += 1
        else:
            quality_score -= 2
        
        if quality_score >= 4:
            quality = 'EXCELLENT'
        elif quality_score >= 2:
            quality = 'GOOD'
        elif quality_score >= 0:
            quality = 'FAIR'
        else:
            quality = 'POOR'
        
        return {
            'strategy': strategy,
            'warnings': warnings,
            'quality': quality
        }
    
    def print_analysis(self, structure: OptionsStructure):
        """Print formatted analysis"""
        
        print("\n" + "="*70)
        print(f"📊 OPTIONS MARKET STRUCTURE: {structure.symbol}")
        print(f"   Price: ${structure.underlying_price:.2f}")
        print("="*70)
        
        # IV Analysis
        print(f"\n📈 IMPLIED VOLATILITY ANALYSIS")
        print(f"   Current IV:      {structure.current_iv:.1f}%")
        print(f"   IV Percentile:   {structure.iv_percentile:.0f}th percentile")
        print(f"   IV Rank:         {structure.iv_rank:.0f}%")
        print(f"   52-Week Range:   {structure.iv_52w_low:.0f}% - {structure.iv_52w_high:.0f}%")
        iv_color = "🟢" if structure.iv_signal == 'RICH' else "🔴" if structure.iv_signal == 'CHEAP' else "🟡"
        print(f"   Signal:          {iv_color} {structure.iv_signal}")
        
        # Expected Move
        print(f"\n🎯 EXPECTED MOVE (from ATM straddle)")
        print(f"   Dollar Move:     ±${structure.expected_move_dollars:.2f}")
        print(f"   Percent Move:    ±{structure.expected_move_pct:.1f}%")
        print(f"   Range:           ${structure.expected_move_range[0]:.2f} - ${structure.expected_move_range[1]:.2f}")
        
        # Put/Call Analysis
        print(f"\n📊 PUT/CALL ANALYSIS")
        print(f"   Volume Ratio:    {structure.put_call_ratio:.2f}")
        print(f"   OI Ratio:        {structure.put_call_oi_ratio:.2f}")
        sentiment_color = "🟢" if structure.sentiment_signal == 'BULLISH' else "🔴" if structure.sentiment_signal == 'BEARISH' else "🟡"
        print(f"   Sentiment:       {sentiment_color} {structure.sentiment_signal}")
        
        # Key OI Levels
        print(f"\n🧲 KEY OPEN INTEREST LEVELS")
        print(f"   Max Pain:        ${structure.max_pain:.0f}")
        print(f"   Put Wall:        ${structure.put_wall:.0f} (support)")
        print(f"   Call Wall:       ${structure.call_wall:.0f} (resistance)")
        print(f"   Top OI Strikes:")
        for level in structure.key_oi_levels[:3]:
            print(f"      ${level['strike']:.0f} ({level['type']}): {level['oi']:,} contracts")
        
        # Volatility Skew
        print(f"\n📐 VOLATILITY SKEW")
        print(f"   25-Delta Skew:   {structure.skew_25delta:+.1f}%")
        print(f"   Put Premium:     {structure.put_skew_premium:+.1f}%")
        skew_color = "🔴" if structure.skew_signal == 'FEAR' else "🟢" if structure.skew_signal == 'GREED' else "🟡"
        print(f"   Signal:          {skew_color} {structure.skew_signal}")
        
        # Liquidity
        print(f"\n💧 LIQUIDITY ANALYSIS")
        print(f"   Avg Spread:      {structure.avg_bid_ask_spread_pct:.1f}%")
        print(f"   Grade:           {structure.liquidity_grade}")
        if structure.liquidity_warning:
            print(f"   ⚠️ {structure.liquidity_warning}")
        
        # Term Structure
        print(f"\n📅 TERM STRUCTURE")
        print(f"   Front Month IV:  {structure.front_month_iv:.1f}%")
        print(f"   Back Month IV:   {structure.back_month_iv:.1f}%")
        print(f"   Structure:       {structure.term_structure}")
        
        # Recommendations
        print(f"\n{'='*70}")
        print(f"🎯 TRADING RECOMMENDATION")
        print(f"   Strategy:        {structure.optimal_strategy}")
        print(f"   Entry Quality:   {structure.entry_quality}")
        if structure.risk_warnings:
            print(f"\n   ⚠️ WARNINGS:")
            for w in structure.risk_warnings:
                print(f"      • {w}")
        print("="*70)
    
    def to_dict(self, structure: OptionsStructure) -> dict:
        """Convert to dictionary for JSON/API"""
        return {
            'symbol': structure.symbol,
            'underlying_price': structure.underlying_price,
            'timestamp': structure.timestamp.isoformat(),
            
            'iv': {
                'current': structure.current_iv,
                'percentile': structure.iv_percentile,
                'rank': structure.iv_rank,
                'high_52w': structure.iv_52w_high,
                'low_52w': structure.iv_52w_low,
                'signal': structure.iv_signal
            },
            
            'expected_move': {
                'dollars': structure.expected_move_dollars,
                'percent': structure.expected_move_pct,
                'range_low': structure.expected_move_range[0],
                'range_high': structure.expected_move_range[1]
            },
            
            'put_call': {
                'volume_ratio': structure.put_call_ratio,
                'oi_ratio': structure.put_call_oi_ratio,
                'sentiment': structure.sentiment_signal
            },
            
            'oi_levels': {
                'max_pain': structure.max_pain,
                'put_wall': structure.put_wall,
                'call_wall': structure.call_wall,
                'key_levels': structure.key_oi_levels
            },
            
            'skew': {
                'skew_25d': structure.skew_25delta,
                'put_premium': structure.put_skew_premium,
                'signal': structure.skew_signal
            },
            
            'liquidity': {
                'avg_spread_pct': structure.avg_bid_ask_spread_pct,
                'grade': structure.liquidity_grade,
                'warning': structure.liquidity_warning
            },
            
            'term_structure': {
                'front_iv': structure.front_month_iv,
                'back_iv': structure.back_month_iv,
                'structure': structure.term_structure
            },
            
            'recommendation': {
                'strategy': structure.optimal_strategy,
                'quality': structure.entry_quality,
                'warnings': structure.risk_warnings
            }
        }


# Test
if __name__ == '__main__':
    analyzer = OptionsStructureAnalyzer()
    
    for symbol in ['CEG', 'NVDA', 'VST']:
        print(f"\n\n{'#'*70}")
        print(f"# ANALYZING: {symbol}")
        print(f"{'#'*70}")
        
        structure = analyzer.analyze(symbol)
        if structure:
            analyzer.print_analysis(structure)
