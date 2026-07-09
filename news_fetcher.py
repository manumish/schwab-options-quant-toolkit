"""
Live News & Insights Fetcher — Real headlines, not hardcoded text.

Sources (all free, no API keys):
  - Yahoo Finance RSS: Stock-specific news
  - Google News RSS: Broader market/sector news
  - FRED (optional): Macro indicators

Cache: 10-minute TTL per symbol to avoid hammering.

Usage:
    fetcher = NewsFetcher()
    news = fetcher.get_stock_news('NVDA')
    # [{'title': '...', 'source': '...', 'url': '...', 'age': '2h ago'}, ...]
    
    sector_news = fetcher.get_sector_news('defense')
    macro = fetcher.get_macro_headlines()
"""

import re
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger('NewsFetcher')


# ============================================================================
# Sector → search keywords for Google News
# ============================================================================
SECTOR_KEYWORDS = {
    'defense': 'defense military contract Pentagon NATO arms',
    'pharmaceutical': 'FDA drug approval clinical trial pharma',
    'healthcare': 'healthcare insurance hospital Medicare Medicaid',
    'nuclear_energy': 'nuclear energy power plant reactor electricity grid',
    'technology': 'AI semiconductor chip cloud computing',
    'consumer_staples': 'consumer staples retail grocery spending',
    'financials': 'banking interest rates Fed lending credit',
    'energy': 'oil gas OPEC crude pipeline energy prices utilities',
    'industrials': 'manufacturing supply chain infrastructure spending',
    'real_estate': 'real estate REIT housing commercial property',
    'materials': 'mining commodities steel copper lithium',
}

# Macro search terms for market-wide news
MACRO_KEYWORDS = [
    'Federal Reserve interest rate',
    'US CPI inflation',
    'GDP growth economy',
    'unemployment jobs report',
    'Treasury yield bond market',
]


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published: Optional[datetime] = None
    age_str: str = ''
    sentiment: str = 'neutral'  # positive, negative, neutral
    relevance: str = 'stock'    # stock, sector, macro


class NewsFetcher:
    """Fetches live news from Yahoo Finance RSS and Google News RSS."""

    CACHE_TTL = 600  # 10 minutes

    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # key → (timestamp, data)
        self._lock = threading.Lock()

    # ================================================================
    # PUBLIC API
    # ================================================================

    def get_stock_news(self, symbol: str, max_items: int = 10) -> List[NewsItem]:
        """Get recent news for a specific stock."""
        key = f'stock:{symbol.upper()}'
        cached = self._get_cache(key)
        if cached is not None:
            return cached[:max_items]

        news = []
        # Source 1: Yahoo Finance RSS (most relevant for individual stocks)
        news.extend(self._fetch_yahoo_rss(symbol))
        # Source 2: Google News (broader coverage)
        news.extend(self._fetch_google_news(f'{symbol} stock'))

        # Deduplicate by title similarity
        news = self._deduplicate(news)
        # Score sentiment
        for item in news:
            item.sentiment = self._score_sentiment(item.title)
            item.relevance = 'stock'

        self._set_cache(key, news)
        return news[:max_items]

    def get_sector_news(self, sector: str, max_items: int = 8) -> List[NewsItem]:
        """Get sector-level news and catalysts."""
        key = f'sector:{sector}'
        cached = self._get_cache(key)
        if cached is not None:
            return cached[:max_items]

        keywords = SECTOR_KEYWORDS.get(sector, sector)
        news = self._fetch_google_news(keywords)
        for item in news:
            item.sentiment = self._score_sentiment(item.title)
            item.relevance = 'sector'

        self._set_cache(key, news)
        return news[:max_items]

    def get_macro_headlines(self, max_items: int = 8) -> List[NewsItem]:
        """Get macroeconomic headlines."""
        key = 'macro'
        cached = self._get_cache(key)
        if cached is not None:
            return cached[:max_items]

        news = []
        for query in MACRO_KEYWORDS[:3]:  # Limit to 3 queries
            news.extend(self._fetch_google_news(query, max_items=3))

        news = self._deduplicate(news)
        for item in news:
            item.sentiment = self._score_sentiment(item.title)
            item.relevance = 'macro'

        self._set_cache(key, news)
        return news[:max_items]

    def get_full_briefing(self, symbol: str, sector: str = None) -> Dict:
        """Get complete news briefing for a stock: stock + sector + macro."""
        stock_news = self.get_stock_news(symbol)
        sector_news = self.get_sector_news(sector) if sector else []
        macro_news = self.get_macro_headlines()

        # Compute sentiment summary
        all_news = stock_news + sector_news
        pos = sum(1 for n in all_news if n.sentiment == 'positive')
        neg = sum(1 for n in all_news if n.sentiment == 'negative')
        total = len(all_news) or 1

        if pos / total > 0.5:
            overall = 'bullish'
        elif neg / total > 0.5:
            overall = 'bearish'
        else:
            overall = 'mixed'

        return {
            'symbol': symbol,
            'sector': sector,
            'stock_news': [self._to_dict(n) for n in stock_news],
            'sector_news': [self._to_dict(n) for n in sector_news],
            'macro_news': [self._to_dict(n) for n in macro_news],
            'sentiment': {
                'overall': overall,
                'positive': pos,
                'negative': neg,
                'neutral': total - pos - neg,
                'total': total,
            },
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        }


    # ================================================================
    # RSS FETCHERS
    # ================================================================

    def _fetch_yahoo_rss(self, symbol: str, max_items: int = 15) -> List[NewsItem]:
        """Fetch stock news from Yahoo Finance RSS."""
        url = f'https://finance.yahoo.com/rss/headline?s={symbol.upper()}'
        try:
            resp = httpx.get(url, headers={'User-Agent': 'Mozilla/5.0'},
                             timeout=10, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f'Yahoo RSS {symbol}: HTTP {resp.status_code}')
                return []

            items = []
            # Parse RSS XML with regex (avoid xml dependency)
            xml = resp.text
            for match in re.finditer(
                r'<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>'
                r'.*?<link>(.*?)</link>'
                r'(?:.*?<source[^>]*>(.*?)</source>)?'
                r'(?:.*?<pubDate>(.*?)</pubDate>)?'
                r'.*?</item>',
                xml, re.DOTALL
            ):
                title = match.group(1).strip()
                link = match.group(2).strip()
                source = match.group(3) or 'Yahoo Finance'
                pub_str = match.group(4)

                pub_dt = self._parse_rss_date(pub_str) if pub_str else None
                age = self._age_string(pub_dt) if pub_dt else ''

                items.append(NewsItem(
                    title=self._clean_html(title),
                    source=source.strip(),
                    url=link,
                    published=pub_dt,
                    age_str=age,
                ))
                if len(items) >= max_items:
                    break

            logger.info(f'Yahoo RSS {symbol}: {len(items)} articles')
            return items

        except Exception as e:
            logger.error(f'Yahoo RSS {symbol} error: {e}')
            return []

    def _fetch_google_news(self, query: str, max_items: int = 10) -> List[NewsItem]:
        """Fetch news from Google News RSS."""
        encoded = query.replace(' ', '+')
        url = f'https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en'
        try:
            resp = httpx.get(url, headers={'User-Agent': 'Mozilla/5.0'},
                             timeout=10, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f'Google News "{query}": HTTP {resp.status_code}')
                return []

            items = []
            xml = resp.text
            for match in re.finditer(
                r'<item>.*?<title>(.*?)</title>'
                r'.*?<link>(.*?)</link>'
                r'(?:.*?<source[^>]*>(.*?)</source>)?'
                r'(?:.*?<pubDate>(.*?)</pubDate>)?'
                r'.*?</item>',
                xml, re.DOTALL
            ):
                title = match.group(1).strip()
                link = match.group(2).strip()
                source = match.group(3) or 'Google News'
                pub_str = match.group(4)

                pub_dt = self._parse_rss_date(pub_str) if pub_str else None
                age = self._age_string(pub_dt) if pub_dt else ''

                items.append(NewsItem(
                    title=self._clean_html(title),
                    source=source.strip(),
                    url=link,
                    published=pub_dt,
                    age_str=age,
                ))
                if len(items) >= max_items:
                    break

            logger.info(f'Google News "{query}": {len(items)} articles')
            return items

        except Exception as e:
            logger.error(f'Google News "{query}" error: {e}')
            return []


    # ================================================================
    # SENTIMENT SCORING (keyword-based, fast)
    # ================================================================

    _POSITIVE = {
        'upgrade', 'upgrades', 'upgraded', 'buy', 'outperform', 'overweight',
        'beat', 'beats', 'beating', 'exceeded', 'exceeds', 'surpass', 'surpasses',
        'record', 'high', 'surge', 'surges', 'surging', 'soar', 'soars', 'soaring',
        'rally', 'rallies', 'rallying', 'gain', 'gains', 'jump', 'jumps',
        'strong', 'bullish', 'optimistic', 'positive', 'growth', 'profit',
        'approve', 'approved', 'approval', 'launch', 'launches',
        'raises', 'raised', 'hikes', 'dividend', 'buyback', 'repurchase',
        'breakthrough', 'innovation', 'partnership', 'deal', 'acquisition',
        'boost', 'boosts', 'boosting', 'accelerate', 'momentum',
    }

    _NEGATIVE = {
        'downgrade', 'downgrades', 'downgraded', 'sell', 'underperform', 'underweight',
        'miss', 'misses', 'missed', 'below', 'disappoint', 'disappoints',
        'fall', 'falls', 'falling', 'drop', 'drops', 'decline', 'declines',
        'crash', 'crashes', 'plunge', 'plunges', 'plummets', 'tumble', 'tumbles',
        'sink', 'sinks', 'slump', 'slumps', 'weak', 'bearish', 'pessimistic',
        'loss', 'losses', 'deficit', 'debt', 'risk', 'warning', 'warns',
        'layoff', 'layoffs', 'cut', 'cuts', 'cutting', 'closure',
        'recall', 'lawsuit', 'sued', 'investigation', 'probe', 'scandal',
        'tariff', 'tariffs', 'sanction', 'sanctions', 'ban', 'restrict',
        'recession', 'slowdown', 'contraction', 'bankruptcy', 'default',
        'crater', 'craters', 'cratering', 'worst', 'fear', 'fears',
    }

    def _score_sentiment(self, title: str) -> str:
        words = set(re.findall(r'[a-z]+', title.lower()))
        pos = len(words & self._POSITIVE)
        neg = len(words & self._NEGATIVE)
        if pos > neg:
            return 'positive'
        elif neg > pos:
            return 'negative'
        return 'neutral'

    # ================================================================
    # UTILITIES
    # ================================================================

    def _deduplicate(self, news: List[NewsItem]) -> List[NewsItem]:
        """Remove near-duplicate headlines."""
        seen = set()
        unique = []
        for item in news:
            # Normalize: lowercase, strip punctuation, first 50 chars
            key = re.sub(r'[^a-z0-9 ]', '', item.title.lower())[:50]
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def _parse_rss_date(self, date_str: str) -> Optional[datetime]:
        """Parse RSS pubDate format."""
        if not date_str:
            return None
        formats = [
            '%a, %d %b %Y %H:%M:%S %z',
            '%a, %d %b %Y %H:%M:%S %Z',
            '%Y-%m-%dT%H:%M:%S%z',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        # Try replacing timezone abbreviations
        cleaned = date_str.strip().replace('GMT', '+0000').replace('EST', '-0500').replace('EDT', '-0400')
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        return None

    def _age_string(self, dt: Optional[datetime]) -> str:
        """Convert datetime to '2h ago', '3d ago' etc."""
        if not dt:
            return ''
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return 'just now'
        if seconds < 3600:
            return f'{seconds // 60}m ago'
        if seconds < 86400:
            return f'{seconds // 3600}h ago'
        days = seconds // 86400
        if days == 1:
            return '1d ago'
        if days < 30:
            return f'{days}d ago'
        return f'{days // 30}mo ago'

    def _clean_html(self, text: str) -> str:
        """Strip HTML tags and decode entities."""
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&#39;', "'").replace('&quot;', '"')
        return text.strip()

    def _get_cache(self, key: str):
        with self._lock:
            if key in self._cache:
                ts, data = self._cache[key]
                if time.time() - ts < self.CACHE_TTL:
                    return data
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data):
        with self._lock:
            self._cache[key] = (time.time(), data)

    @staticmethod
    def _to_dict(item: NewsItem) -> Dict:
        return {
            'title': item.title,
            'source': item.source,
            'url': item.url,
            'age': item.age_str,
            'sentiment': item.sentiment,
            'relevance': item.relevance,
        }


# ============================================================================
# Module-level convenience
# ============================================================================
_fetcher = None

def get_fetcher() -> NewsFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = NewsFetcher()
    return _fetcher


if __name__ == '__main__':
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'NVDA'
    f = NewsFetcher()
    
    print(f'\n{"="*60}')
    print(f'📰 NEWS BRIEFING: {symbol}')
    print(f'{"="*60}')
    
    news = f.get_stock_news(symbol)
    for n in news[:8]:
        icon = '🟢' if n.sentiment == 'positive' else '🔴' if n.sentiment == 'negative' else '⚪'
        print(f'  {icon} {n.title[:80]}')
        print(f'     {n.source} · {n.age_str}')
    
    print(f'\n📊 Sentiment: {sum(1 for n in news if n.sentiment=="positive")} positive, '
          f'{sum(1 for n in news if n.sentiment=="negative")} negative, '
          f'{sum(1 for n in news if n.sentiment=="neutral")} neutral')
