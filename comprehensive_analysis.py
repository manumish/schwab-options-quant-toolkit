"""
Comprehensive Stock Analysis Module
Analyzes stocks with full context: news, macro, geopolitics, sector catalysts
"""

import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum

class RiskLevel(Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXTREME = "extreme"

class Sentiment(Enum):
    VERY_BULLISH = "very_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    VERY_BEARISH = "very_bearish"

@dataclass
class NewsItem:
    headline: str
    source: str
    date: datetime
    sentiment: Sentiment
    relevance: str  # 'high', 'medium', 'low'
    summary: str

@dataclass
class MacroFactor:
    factor: str
    status: str
    impact: str  # 'positive', 'negative', 'neutral'
    description: str

@dataclass
class SectorCatalyst:
    catalyst: str
    date: Optional[datetime]
    impact: str
    description: str

@dataclass 
class GeopoliticalRisk:
    event: str
    region: str
    impact_level: RiskLevel
    affected_sectors: List[str]
    description: str

@dataclass
class ComprehensiveAnalysis:
    """Full contextual analysis of a stock"""
    symbol: str
    company_name: str
    sector: str
    industry: str
    
    # Price data
    current_price: float
    price_change_1d: float
    price_change_1w: float
    price_change_1m: float
    price_change_ytd: float
    high_52w: float
    low_52w: float
    
    # Valuation
    market_cap: float
    pe_ratio: Optional[float]
    forward_pe: Optional[float]
    
    # Technical
    rsi: Optional[float]
    sma_50: Optional[float]
    sma_200: Optional[float]
    support_levels: List[float]
    resistance_levels: List[float]
    
    # News & Sentiment
    recent_news: List[NewsItem]
    overall_sentiment: Sentiment
    
    # Macro factors
    macro_factors: List[MacroFactor]
    
    # Sector-specific catalysts
    sector_catalysts: List[SectorCatalyst]
    
    # Geopolitical risks
    geopolitical_risks: List[GeopoliticalRisk]
    
    # Earnings
    next_earnings: Optional[datetime]
    days_to_earnings: Optional[int]
    
    # Overall assessment
    risk_level: RiskLevel
    recommendation: str
    key_risks: List[str]
    key_opportunities: List[str]
    
    # Options insight
    iv_percentile: Optional[float]
    options_recommendation: str


# Sector-specific knowledge base
SECTOR_FACTORS = {
    'defense': {
        'catalysts': [
            'Defense budget approvals',
            'Military contract awards',
            'Geopolitical tensions',
            'NATO spending commitments',
            'Arms export deals'
        ],
        'risks': [
            'Peace negotiations reducing demand',
            'Budget cuts',
            'Contract cancellations',
            'Export restrictions'
        ],
        'geopolitical_sensitivity': 'HIGH'
    },
    'pharmaceutical': {
        'catalysts': [
            'FDA drug approvals',
            'Clinical trial results (Phase 1/2/3)',
            'Patent expirations',
            'Acquisition targets',
            'Pipeline updates'
        ],
        'risks': [
            'Clinical trial failures',
            'FDA rejection',
            'Patent cliffs',
            'Drug pricing legislation',
            'Generic competition'
        ],
        'geopolitical_sensitivity': 'LOW'
    },
    'nuclear_energy': {
        'catalysts': [
            'AI datacenter power demand',
            'Clean energy policy',
            'Nuclear plant license extensions',
            'SMR (Small Modular Reactor) developments',
            'Uranium supply constraints'
        ],
        'risks': [
            'Nuclear accidents globally',
            'Regulatory changes',
            'Renewable competition',
            'Waste disposal concerns'
        ],
        'geopolitical_sensitivity': 'MODERATE'
    },
    'healthcare': {
        'catalysts': [
            'Medicare/Medicaid policy changes',
            'Aging population demographics',
            'Healthcare reform',
            'M&A activity'
        ],
        'risks': [
            'Government price controls',
            'Reimbursement cuts',
            'Regulatory scrutiny',
            'Litigation'
        ],
        'geopolitical_sensitivity': 'LOW'
    },
    'technology': {
        'catalysts': [
            'AI adoption acceleration',
            'Cloud growth',
            'Chip demand cycles',
            'New product launches'
        ],
        'risks': [
            'Antitrust regulation',
            'China trade restrictions',
            'Semiconductor supply chain',
            'Valuation compression'
        ],
        'geopolitical_sensitivity': 'HIGH'
    }
}

# Current macro environment (update periodically)
CURRENT_MACRO = {
    'fed_policy': {
        'status': 'Rates elevated, potential cuts in 2026',
        'impact': 'neutral',
        'description': 'Fed holding rates, watching inflation'
    },
    'inflation': {
        'status': 'Moderating but sticky',
        'impact': 'neutral',
        'description': 'CPI trending down but services inflation persistent'
    },
    'gdp_growth': {
        'status': 'Moderate growth',
        'impact': 'positive',
        'description': 'US economy showing resilience'
    },
    'unemployment': {
        'status': 'Low unemployment',
        'impact': 'positive',
        'description': 'Labor market remains strong'
    },
    'dollar_strength': {
        'status': 'Strong dollar',
        'impact': 'mixed',
        'description': 'Benefits importers, hurts exporters'
    }
}

# Current geopolitical risks (update periodically)
CURRENT_GEOPOLITICAL = [
    GeopoliticalRisk(
        event="Russia-Ukraine War",
        region="Eastern Europe",
        impact_level=RiskLevel.HIGH,
        affected_sectors=['defense', 'energy', 'agriculture', 'cybersecurity'],
        description="Ongoing conflict driving defense spending, energy volatility"
    ),
    GeopoliticalRisk(
        event="US-China Tech Tensions",
        region="Asia-Pacific",
        impact_level=RiskLevel.HIGH,
        affected_sectors=['technology', 'semiconductors', 'manufacturing'],
        description="Export controls on chips, supply chain reshoring"
    ),
    GeopoliticalRisk(
        event="Middle East Tensions",
        region="Middle East",
        impact_level=RiskLevel.MODERATE,
        affected_sectors=['energy', 'defense', 'shipping'],
        description="Red Sea disruptions, Iran tensions affecting oil"
    ),
    GeopoliticalRisk(
        event="Taiwan Strait",
        region="Asia-Pacific",
        impact_level=RiskLevel.MODERATE,
        affected_sectors=['semiconductors', 'technology', 'defense'],
        description="China-Taiwan tensions, TSMC supply risk"
    )
]

# Auto-build symbol→sector mapping from discovery engine (140+ symbols)
# Falls back to sector name as description if no custom description exists
try:
    from discovery_engine import STOCK_SECTOR_MAP as _DISC_MAP
except ImportError:
    _DISC_MAP = {}

# Normalize discovery sector names → news_fetcher-compatible keys
_SECTOR_NORMALIZE = {
    'Defense & Aerospace': 'defense',
    'Healthcare': 'healthcare',
    'Energy & Utilities': 'energy',
    'Financials': 'financials',
    'Consumer Staples': 'consumer_staples',
    'Industrials': 'industrials',
    'Materials': 'materials',
    'Real Estate': 'real_estate',
    'Technology': 'technology',
}

SYMBOL_SECTORS = {}
for _sym, _raw_sector in _DISC_MAP.items():
    _norm = _SECTOR_NORMALIZE.get(_raw_sector, _raw_sector.lower().replace(' ', '_'))
    SYMBOL_SECTORS[_sym] = (_norm, f'{_sym} - {_raw_sector}')


class ComprehensiveAnalyzer:
    """Performs deep contextual analysis on stocks"""
    
    def __init__(self):
        from token_manager import get_headers
        self.get_headers = get_headers
        from technical_analysis import TechnicalAnalyzer
        self.tech_analyzer = TechnicalAnalyzer()
        from earnings_calendar import EarningsCalendarWithBackup
        self.earnings = EarningsCalendarWithBackup()
        from news_fetcher import NewsFetcher
        self.news = NewsFetcher()
    
    def analyze(self, symbol: str) -> Optional[ComprehensiveAnalysis]:
        """Perform comprehensive analysis on a symbol"""
        symbol = symbol.upper()
        
        # Get sector info
        sector_info = SYMBOL_SECTORS.get(symbol, ('unknown', f'{symbol} - Unknown'))
        sector, company_desc = sector_info
        
        # Get technical analysis
        tech = self.tech_analyzer.analyze(symbol)
        if not tech:
            return None
        
        # Get earnings info
        earnings_info = self.earnings.get_earnings_date(symbol)
        
        # Fetch LIVE news (stock + sector + macro)
        briefing = self.news.get_full_briefing(symbol, sector)
        
        # Convert live stock news to NewsItem dataclass
        live_news = []
        sent_map = {'positive': Sentiment.BULLISH, 'negative': Sentiment.BEARISH, 'neutral': Sentiment.NEUTRAL}
        for n in briefing.get('stock_news', [])[:8]:
            live_news.append(NewsItem(
                headline=n['title'],
                source=n['source'],
                date=datetime.now(),
                sentiment=sent_map.get(n.get('sentiment', 'neutral'), Sentiment.NEUTRAL),
                relevance='high',
                summary=n.get('url', ''),
            ))
        
        # Convert live macro headlines to MacroFactor
        macro_factors = []
        for n in briefing.get('macro_news', [])[:5]:
            macro_factors.append(MacroFactor(
                factor=n['title'][:60],
                status=n.get('age', 'recent'),
                impact=n.get('sentiment', 'neutral'),
                description=f"{n['source']} · {n.get('age', '')}"
            ))
        
        # Convert live sector news to SectorCatalyst
        sector_data = SECTOR_FACTORS.get(sector, {})
        sector_catalysts = []
        for n in briefing.get('sector_news', [])[:5]:
            sector_catalysts.append(SectorCatalyst(
                catalyst=n['title'][:80],
                date=None,
                impact=n.get('sentiment', 'neutral'),
                description=f"{n['source']} · {n.get('age', '')}"
            ))
        
        # Overall sentiment from live data
        sent_data = briefing.get('sentiment', {})
        if sent_data.get('overall') == 'bullish':
            overall_sentiment = Sentiment.BULLISH
        elif sent_data.get('overall') == 'bearish':
            overall_sentiment = Sentiment.BEARISH
        else:
            overall_sentiment = Sentiment.NEUTRAL
        
        # Geopolitical removed per user request
        geo_risks = []
        
        # Determine overall risk level
        risk_level = self._assess_risk(symbol, sector, tech, earnings_info, geo_risks)
        
        # Build recommendation
        recommendation, options_rec = self._build_recommendation(
            symbol, sector, tech, earnings_info, risk_level
        )
        
        # Key risks and opportunities
        key_risks = sector_data.get('risks', [])[:3]
        key_opportunities = sector_data.get('catalysts', [])[:3]
        
        # Add earnings risk if applicable
        if earnings_info.days_until is not None and 0 < earnings_info.days_until <= 14:
            key_risks.insert(0, f"Earnings in {earnings_info.days_until} days - elevated IV/risk")
        
        return ComprehensiveAnalysis(
            symbol=symbol,
            company_name=company_desc,
            sector=sector,
            industry=sector,
            current_price=tech.current_price,
            price_change_1d=tech.change_pct,
            price_change_1w=0,  # TODO: calculate from history
            price_change_1m=0,
            price_change_ytd=0,
            high_52w=0,
            low_52w=0,
            market_cap=0,
            pe_ratio=None,
            forward_pe=None,
            rsi=tech.rsi_14,
            sma_50=tech.sma_50,
            sma_200=tech.sma_200,
            support_levels=[s.price for s in tech.supports[:3]],
            resistance_levels=[r.price for r in tech.resistances[:3]],
            recent_news=live_news,
            overall_sentiment=overall_sentiment,
            macro_factors=macro_factors,
            sector_catalysts=sector_catalysts,
            geopolitical_risks=geo_risks,
            next_earnings=earnings_info.earnings_date,
            days_to_earnings=earnings_info.days_until,
            risk_level=risk_level,
            recommendation=recommendation,
            key_risks=key_risks,
            key_opportunities=key_opportunities,
            iv_percentile=None,
            options_recommendation=options_rec
        )
    
    def _assess_risk(self, symbol, sector, tech, earnings, geo_risks) -> RiskLevel:
        """Assess overall risk level"""
        risk_score = 0
        
        # Earnings risk
        if earnings.days_until is not None and earnings.days_until > 0:
            if earnings.days_until <= 3:
                risk_score += 3
            elif earnings.days_until <= 7:
                risk_score += 2
            elif earnings.days_until <= 14:
                risk_score += 1
        
        # Geopolitical risk
        for risk in geo_risks:
            if risk.impact_level == RiskLevel.HIGH:
                risk_score += 2
            elif risk.impact_level == RiskLevel.MODERATE:
                risk_score += 1
        
        # Technical risk (RSI extremes)
        if tech.rsi_14:
            if tech.rsi_14 > 80 or tech.rsi_14 < 20:
                risk_score += 1
        
        # Sector sensitivity
        sector_data = SECTOR_FACTORS.get(sector, {})
        if sector_data.get('geopolitical_sensitivity') == 'HIGH':
            risk_score += 1
        
        if risk_score >= 5:
            return RiskLevel.EXTREME
        elif risk_score >= 3:
            return RiskLevel.HIGH
        elif risk_score >= 1:
            return RiskLevel.MODERATE
        return RiskLevel.LOW
    
    def _build_recommendation(self, symbol, sector, tech, earnings, risk_level):
        """Build trading recommendation using technicals + discovery data"""
        
        # Load discovery candidate data if available
        disc = self._load_discovery_data(symbol)
        
        # Options recommendation
        options_rec = ""
        
        # Check earnings
        if earnings.days_until is not None and 0 < earnings.days_until <= 7:
            options_rec = f"⚠️ AVOID SELLING PUTS - Earnings in {earnings.days_until} days"
            recommendation = f"Wait until after earnings ({earnings.earnings_date.strftime('%b %d') if earnings.earnings_date else 'soon'})"
            return recommendation, options_rec
        
        # Build signals list from technicals + fundamentals
        signals = []
        
        # Technical signals
        if tech.oversold and tech.near_support:
            signals.append(('strong_buy', 'Oversold at support — prime put-selling zone'))
            options_rec = "✅ SELL PUTS - Oversold at support"
        elif tech.oversold:
            signals.append(('buy', 'Oversold — watch for support confirmation'))
            options_rec = "📊 CONSIDER SELLING PUTS - Oversold"
        elif tech.overbought and tech.near_resistance:
            signals.append(('sell', 'Overbought at resistance — sell calls'))
            options_rec = "✅ SELL CALLS - Overbought at resistance"
        elif tech.overbought:
            signals.append(('caution', 'Overbought — be cautious adding'))
            options_rec = "📊 CONSIDER SELLING CALLS - Extended"
        
        # Fundamental signals from discovery engine
        if disc:
            if disc.get('dividend_yield', 0) >= 3.0:
                signals.append(('income', f"{disc['dividend_yield']:.1f}% dividend yield"))
            if disc.get('analyst_upside', 0) >= 20:
                signals.append(('value', f"+{disc['analyst_upside']:.0f}% analyst upside (target ${disc.get('analyst_target', 0):.0f})"))
            if disc.get('atm_put_iv', 0) >= 35:
                signals.append(('premium', f"IV {disc['atm_put_iv']:.0f}% — rich put premiums available"))
            if disc.get('pe_ratio', 0) > 0 and disc.get('pe_ratio', 999) < 15:
                signals.append(('value', f"P/E {disc['pe_ratio']:.1f} — value territory"))
            if disc.get('total_score', 0) >= 7:
                signals.append(('discovery', f"Discovery score {disc['total_score']:.1f}/10"))
        
        # Build recommendation from signals
        if not signals:
            # No signals at all — shouldn't have been surfaced
            options_rec = options_rec or "📊 No actionable setup right now"
            recommendation = "No strong signal from technicals or fundamentals"
        else:
            # Combine the strongest signals into a recommendation
            rec_parts = [s[1] for s in signals]
            recommendation = ' | '.join(rec_parts)
            if not options_rec:
                # Default options rec based on available signals
                signal_types = {s[0] for s in signals}
                if 'income' in signal_types or 'value' in signal_types:
                    options_rec = "📊 SELL PUTS for discounted entry + income"
                elif 'premium' in signal_types:
                    options_rec = "📊 SELL PUTS — elevated IV for premium capture"
                else:
                    options_rec = "📊 WATCH — fundamentals interesting, wait for technical entry"
        
        # Modify based on risk
        if risk_level == RiskLevel.EXTREME:
            options_rec = "🚨 HIGH RISK - " + options_rec
            recommendation = "⚠️ Elevated risk — " + recommendation
        elif risk_level == RiskLevel.HIGH:
            options_rec = "⚠️ ELEVATED RISK - " + options_rec
        
        return recommendation, options_rec
    
    def _load_discovery_data(self, symbol: str) -> Optional[dict]:
        """Load discovery candidate data from DB if available."""
        try:
            import sqlite3
            from pathlib import Path
            db = Path(__file__).parent / 'scanner.db'
            if not db.exists():
                return None
            conn = sqlite3.connect(str(db))
            row = conn.execute(
                'SELECT dividend_yield, pe_ratio, atm_put_iv, analyst_target, analyst_upside, total_score '
                'FROM discovery_candidates WHERE symbol = ?', (symbol,)
            ).fetchone()
            conn.close()
            if row:
                return {
                    'dividend_yield': row[0] or 0,
                    'pe_ratio': row[1] or 0,
                    'atm_put_iv': row[2] or 0,
                    'analyst_target': row[3] or 0,
                    'analyst_upside': row[4] or 0,
                    'total_score': row[5] or 0,
                }
        except Exception:
            pass
        return None
    
    def to_dict(self, analysis: ComprehensiveAnalysis) -> dict:
        """Convert to dictionary for JSON"""
        return {
            'symbol': analysis.symbol,
            'company_name': analysis.company_name,
            'sector': analysis.sector,
            'industry': analysis.industry,
            'current_price': analysis.current_price,
            'price_change_1d': analysis.price_change_1d,
            'high_52w': analysis.high_52w,
            'low_52w': analysis.low_52w,
            'rsi': analysis.rsi,
            'sma_50': analysis.sma_50,
            'sma_200': analysis.sma_200,
            'support_levels': analysis.support_levels,
            'resistance_levels': analysis.resistance_levels,
            'overall_sentiment': analysis.overall_sentiment.value,
            'recent_news': [
                {'title': n.headline, 'source': n.source, 'url': n.summary,
                 'sentiment': 'positive' if n.sentiment == Sentiment.BULLISH else 'negative' if n.sentiment == Sentiment.BEARISH else 'neutral'}
                for n in analysis.recent_news
            ],
            'macro_factors': [
                {'factor': m.factor, 'status': m.status, 'impact': m.impact, 'description': m.description}
                for m in analysis.macro_factors
            ],
            'sector_catalysts': [
                {'catalyst': c.catalyst, 'impact': c.impact, 'description': c.description}
                for c in analysis.sector_catalysts
            ],

            'next_earnings': analysis.next_earnings.isoformat() if analysis.next_earnings else None,
            'days_to_earnings': analysis.days_to_earnings,
            'risk_level': analysis.risk_level.value,
            'recommendation': analysis.recommendation,
            'key_risks': analysis.key_risks,
            'key_opportunities': analysis.key_opportunities,
            'options_recommendation': analysis.options_recommendation
        }
    
    def print_analysis(self, analysis: ComprehensiveAnalysis):
        """Print formatted analysis"""
        print("\n" + "="*70)
        print(f"📊 COMPREHENSIVE ANALYSIS: {analysis.symbol}")
        print(f"   {analysis.company_name}")
        print("="*70)
        
        # Price
        print(f"\n💰 PRICE: ${analysis.current_price:.2f} ({analysis.price_change_1d:+.2f}%)")
        
        # Technical
        print(f"\n📈 TECHNICAL:")
        print(f"   RSI(14): {analysis.rsi:.1f}" if analysis.rsi else "   RSI: N/A")
        print(f"   50-day MA: ${analysis.sma_50:.2f}" if analysis.sma_50 else "")
        print(f"   200-day MA: ${analysis.sma_200:.2f}" if analysis.sma_200 else "")
        if analysis.support_levels:
            print(f"   Support: {', '.join([f'${s:.2f}' for s in analysis.support_levels[:3]])}")
        if analysis.resistance_levels:
            print(f"   Resistance: {', '.join([f'${r:.2f}' for r in analysis.resistance_levels[:3]])}")
        
        # Earnings
        if analysis.days_to_earnings is not None:
            emoji = "⚠️" if analysis.days_to_earnings <= 7 else "📅"
            print(f"\n{emoji} EARNINGS: {analysis.next_earnings.strftime('%Y-%m-%d') if analysis.next_earnings else 'TBD'} ({analysis.days_to_earnings} days)")
        
        # Macro
        print(f"\n🌍 MACRO ENVIRONMENT:")
        for m in analysis.macro_factors[:3]:
            icon = "🟢" if m.impact == 'positive' else "🔴" if m.impact == 'negative' else "🟡"
            print(f"   {icon} {m.factor}: {m.status}")
        
        # Sector catalysts
        print(f"\n🎯 SECTOR CATALYSTS ({analysis.sector.upper()}):")
        for c in analysis.sector_catalysts[:3]:
            print(f"   • {c.catalyst}")
        
        # Key risks
        print(f"\n🚨 KEY RISKS:")
        for r in analysis.key_risks[:3]:
            print(f"   • {r}")
        
        # Key opportunities  
        print(f"\n✨ KEY OPPORTUNITIES:")
        for o in analysis.key_opportunities[:3]:
            print(f"   • {o}")
        
        # Risk level
        risk_colors = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MODERATE: "🟡", 
            RiskLevel.HIGH: "🟠",
            RiskLevel.EXTREME: "🔴"
        }
        print(f"\n{risk_colors[analysis.risk_level]} RISK LEVEL: {analysis.risk_level.value.upper()}")
        
        # Recommendation
        print(f"\n{'='*70}")
        print(f"💡 RECOMMENDATION:")
        print(f"   {analysis.recommendation}")
        print(f"\n📋 OPTIONS STRATEGY:")
        print(f"   {analysis.options_recommendation}")
        print("="*70)


# Test
if __name__ == '__main__':
    analyzer = ComprehensiveAnalyzer()
    
    for symbol in ['CEG', 'RTX', 'VST', 'NVDA']:
        analysis = analyzer.analyze(symbol)
        if analysis:
            analyzer.print_analysis(analysis)
