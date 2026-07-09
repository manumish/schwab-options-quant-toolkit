"""
TIP Sentiment Scanner — Unified Mac-side scraper
Combines Reddit + Yahoo Finance community scraping and writes local scanner.db.

Runs from Mac via launchd (residential IP required for Yahoo).
Schedule: 6x daily (6:30 AM, 8 AM, 10 AM, 12 PM, 2 PM, 10 PM PT)

Usage:
    python3 tip_sentiment_scanner.py                    # Full scan (Reddit + Yahoo)
    python3 tip_sentiment_scanner.py --yahoo-only       # Yahoo only
    python3 tip_sentiment_scanner.py --reddit-only      # Reddit only
    python3 tip_sentiment_scanner.py --symbols NVDA,TSLA  # Specific tickers
"""

import json
import time
import sys
import os
import logging
import subprocess
from datetime import datetime, timezone
from typing import Dict, List

# Add project dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sentiment_store import persist_sentiment_signals

LOG_PATH = os.path.expanduser('~/.schwab/logs/sentiment.log')
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, mode='a')
    ]
)
log = logging.getLogger('tip_sentiment')

# ============================================================
# Configuration
# ============================================================
VAULT_SCRIPT = os.path.expanduser('~/.codex/skills/shared-brain/brain.py')

# Bullish/Bearish word sets for rule-based sentiment
BULLISH_WORDS = frozenset({
    'buy', 'long', 'calls', 'moon', 'bull', 'green', 'up', 'breakout',
    'undervalued', 'cheap', 'accumulate', 'rally', 'upgrade', 'beat', 'strong',
    'positive', 'growth', 'squeeze', 'run', 'double', 'hold', 'love',
    'rocket', 'rip', 'gains', 'winner', 'bullish', 'oversold', 'dip',
    'support', 'bounce', 'recover', 'upside', 'profit', 'earnings'
})

BEARISH_WORDS = frozenset({
    'sell', 'short', 'puts', 'crash', 'bear', 'red', 'down', 'overvalued',
    'expensive', 'dump', 'drop', 'downgrade', 'miss', 'weak', 'negative',
    'decline', 'tank', 'fade', 'dead', 'bubble', 'avoid', 'loss', 'scam',
    'resistance', 'breakdown', 'bearish', 'overbought', 'plunge', 'risk',
    'danger', 'warning', 'collapse', 'fraud', 'dilution', 'debt'
})

# Default watchlist — EXAMPLE symbols only, replace with your own
DEFAULT_SYMBOLS = [
    # Example holdings
    'NVDA', 'ORCL', 'TSLA', 'LLY', 'UNH', 'AMZN', 'AMD', 'ABBV',
    'ISRG', 'MSFT', 'RTX', 'VST', 'JPM', 'INTC',
    # Active options underlyings
    'CEG', 'MU', 'CVX', 'AVGO', 'CRM', 'LMT', 'XOM', 'COST',
    # Diversification targets
    'AAPL', 'GOOG', 'META', 'GS', 'BAC', 'WFC',
    'CAT', 'DE', 'PFE', 'JNJ',
    'WMT', 'NKE', 'PLTR', 'UBER', 'QCOM',
    # High-IV / Wheel
    'COIN', 'SOFI',
    # ETFs
    'SPY', 'QQQ', 'IWM',
]


# ============================================================
# Sentiment Analysis (rule-based, same as TIP pipeline)
# ============================================================
def analyze_sentiment(text: str) -> Dict:
    """Rule-based sentiment scoring. Returns score -1.0 to +1.0."""
    words = set(text.lower().split())
    bull = len(words & BULLISH_WORDS)
    bear = len(words & BEARISH_WORDS)
    total = bull + bear
    
    if total == 0:
        return {'score': 0.0, 'label': 'neutral', 'bull_signals': 0, 'bear_signals': 0}
    
    score = (bull - bear) / total  # -1.0 to +1.0
    
    if score > 0.3:
        label = 'bullish'
    elif score < -0.3:
        label = 'bearish'
    else:
        label = 'neutral'
    
    return {'score': round(score, 4), 'label': label, 
            'bull_signals': bull, 'bear_signals': bear}


def aggregate_sentiment(posts: List[Dict], symbol: str) -> Dict:
    """Aggregate multiple posts into a single sentiment digest for a symbol."""
    if not posts:
        return {
            'symbol': symbol,
            'sentiment_score': 0.0,
            'mention_count': 0,
            'bull_count': 0,
            'bear_count': 0,
            'neutral_count': 0,
            'euphoria_pct': 0,
            'panic_pct': 0,
            'contrarian_signal': 'NEUTRAL',
            'top_themes': [],
            'top_post': '',
        }
    
    scores = []
    bull = bear = neutral = 0
    total_upvotes = 0
    top_post = ''
    top_weight = -1
    
    for p in posts:
        sent = analyze_sentiment(p.get('body', ''))
        scores.append(sent['score'])
        
        if sent['label'] == 'bullish':
            bull += 1
        elif sent['label'] == 'bearish':
            bear += 1
        else:
            neutral += 1
        
        total_upvotes += p.get('upvotes', 0)
        weight = (p.get('upvotes') or 0) + (p.get('comment_count') or 0)
        if weight > top_weight:
            top_weight = weight
            body = (p.get('body') or '').strip().replace('\n', ' ')
            src = p.get('source') or 'unknown'
            top_post = f"{src}: {body}"[:500]
    
    total = len(posts)
    avg_score = sum(scores) / total if total else 0
    bull_pct = bull / total * 100 if total else 0
    bear_pct = bear / total * 100 if total else 0
    
    # Contrarian signal (key edge for premium selling)
    if bull_pct > 80:
        contrarian = 'SELL_PREMIUM'  # Euphoria = sell calls/strangles
    elif bear_pct > 80:
        contrarian = 'BUY_DIP'  # Panic = sell CSPs
    elif bull_pct > 65:
        contrarian = 'CAUTION_BULL'
    elif bear_pct > 65:
        contrarian = 'CAUTION_BEAR'
    else:
        contrarian = 'NEUTRAL'
    
    return {
        'symbol': symbol,
        'sentiment_score': round(avg_score, 4),
        'mention_count': total,
        'bull_count': bull,
        'bear_count': bear,
        'neutral_count': neutral,
        'euphoria_pct': round(bull_pct, 1),
        'panic_pct': round(bear_pct, 1),
        'contrarian_signal': contrarian,
        'avg_upvotes': round(total_upvotes / total, 1) if total else 0,
        'top_post': top_post,
    }


# ============================================================
# Yahoo Finance Scraper
# ============================================================
def scrape_yahoo(symbols: List[str]) -> Dict[str, List[Dict]]:
    """Scrape Yahoo Finance community for the given symbols."""
    from yahoo_community_scraper import YahooCommunityScaper
    
    log.info(f"Yahoo: Starting scrape for {len(symbols)} symbols...")
    scraper = YahooCommunityScaper(rate_limit_delay=1.5)
    
    results = {}
    for sym in symbols:
        try:
            result = scraper.get_posts(sym, count=20)
            posts = result.get('posts', [])
            
            if posts:
                # Normalize to common format
                normalized = []
                for p in posts:
                    normalized.append({
                        'post_id': p.get('uuid', ''),
                        'body': p.get('body', ''),
                        'created_at': p.get('created_at', ''),
                        'author': p.get('author', 'Anonymous'),
                        'upvotes': p.get('upvotes', 0),
                        'comment_count': p.get('comment_count', 0),
                        'source': 'yahoo_finance',
                        'subreddit': 'yahoo_community',
                        'symbol': sym
                    })
                results[sym] = normalized
                log.info(f"  Yahoo {sym}: {len(normalized)} posts")
            else:
                log.info(f"  Yahoo {sym}: 0 posts")
                
        except Exception as e:
            log.error(f"  Yahoo {sym} failed: {e}")
    
    log.info(f"Yahoo: Done. {len(results)} symbols with data.")
    return results


# ============================================================
# Reddit Scraper (PRAW-based, existing pipeline)
# ============================================================
def vault_get_value(key_name: str) -> str:
    """Fetch a secret value from the local Keychain-backed shared-brain vault."""
    try:
        r = subprocess.run(
            [sys.executable, VAULT_SCRIPT, 'vault-get', key_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            log.warning(f"Vault lookup failed for {key_name}: exit {r.returncode}")
            return ''
        data = json.loads(r.stdout)
        if not data.get('found'):
            log.warning(f"Vault key not found: {key_name}")
            return ''
        return data.get('value', '')
    except Exception as e:
        log.warning(f"Vault lookup failed for {key_name}: {e}")
        return ''


def load_reddit_credentials() -> Dict:
    """Load Reddit API credentials from explicitly named vault keys."""
    key_map = {
        'client_id': os.environ.get('TIP_REDDIT_CLIENT_ID_KEY'),
        'client_secret': os.environ.get('TIP_REDDIT_CLIENT_SECRET_KEY'),
        'user_agent': os.environ.get('TIP_REDDIT_USER_AGENT_KEY'),
    }
    if not key_map['client_id'] or not key_map['client_secret']:
        log.warning(
            "Reddit vault key env vars not set "
            "(TIP_REDDIT_CLIENT_ID_KEY, TIP_REDDIT_CLIENT_SECRET_KEY); skipping Reddit"
        )
        return {}

    creds = {
        'client_id': vault_get_value(key_map['client_id']),
        'client_secret': vault_get_value(key_map['client_secret']),
        'user_agent': 'TIP_Sentiment/1.0',
    }
    if key_map['user_agent']:
        creds['user_agent'] = vault_get_value(key_map['user_agent']) or creds['user_agent']

    if not creds['client_id'] or not creds['client_secret']:
        log.warning("Reddit credentials incomplete after vault lookup; skipping Reddit")
        return {}
    return creds


def scrape_reddit(symbols: List[str]) -> Dict[str, List[Dict]]:
    """Scrape Reddit for sentiment on given symbols.
    Uses PRAW with vault-backed credentials if available, else skips.
    """
    try:
        import praw
    except ImportError:
        log.warning("PRAW not installed, skipping Reddit scrape")
        return {}

    try:
        creds = load_reddit_credentials()
        if not creds:
            return {}
        
        reddit = praw.Reddit(
            client_id=creds['client_id'],
            client_secret=creds['client_secret'],
            user_agent=creds.get('user_agent', 'TIP_Sentiment/1.0')
        )
        
        log.info(f"Reddit: Scanning r/wallstreetbets + r/options...")
        
        results = {}
        symbol_set = set(s.upper() for s in symbols)
        
        for sub_name in ['wallstreetbets', 'options', 'stocks']:
            try:
                subreddit = reddit.subreddit(sub_name)
                for post in subreddit.hot(limit=50):
                    title_body = f"{post.title} {post.selftext}".upper()
                    
                    # Check if any watchlist symbol is mentioned
                    for sym in symbol_set:
                        if f'${sym}' in title_body or f' {sym} ' in title_body:
                            if sym not in results:
                                results[sym] = []
                            
                            results[sym].append({
                                'post_id': post.id,
                                'body': f"{post.title}\n{post.selftext[:500]}",
                                'created_at': datetime.fromtimestamp(
                                    post.created_utc, tz=timezone.utc
                                ).isoformat(),
                                'author': str(post.author) if post.author else 'deleted',
                                'upvotes': post.score,
                                'comment_count': post.num_comments,
                                'source': 'reddit',
                                'subreddit': sub_name,
                                'symbol': sym
                            })
                
                time.sleep(1)  # Rate limit between subreddits
                
            except Exception as e:
                log.error(f"Reddit r/{sub_name} failed: {e}")
        
        log.info(f"Reddit: Done. {len(results)} symbols with mentions.")
        return results
        
    except Exception as e:
        log.error(f"Reddit scrape failed: {e}")
        return {}


# ============================================================
# Local scanner.db sink
# ============================================================
def write_to_db(digests: List[Dict], scan_id: str) -> int:
    """Write aggregated sentiment digests to local scanner.db."""
    if not digests:
        log.warning("No digests to write")
        return 0

    scan_ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for d in digests:
        if d['mention_count'] == 0:
            continue
        sources = []
        if d.get('yahoo_posts', 0):
            sources.append('yahoo_finance')
        if d.get('reddit_posts', 0):
            sources.append('reddit')
        rows.append({
            'scan_ts': scan_ts,
            'symbol': d['symbol'],
            'source': '+'.join(sources) if sources else 'combined',
            'mentions': d['mention_count'],
            'bullish': d['bull_count'],
            'bearish': d['bear_count'],
            'net_score': d['sentiment_score'],
            'top_post': d.get('top_post', ''),
        })

    try:
        inserted = persist_sentiment_signals(rows)
        log.info(f"DB write OK: {inserted} sentiment rows written (scan: {scan_id})")
        return inserted
    except Exception as e:
        log.error(f"DB write failed: {e}")
        return -1


# ============================================================
# Main Scanner
# ============================================================
def run_scan(symbols: List[str] = None,
             yahoo: bool = True, reddit: bool = True,
             write_db: bool = True) -> Dict:
    """Run unified sentiment scan."""
    
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    
    scan_id = f"tip-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log.info(f"=== TIP Sentiment Scan {scan_id} ===")
    log.info(f"Symbols: {len(symbols)}, Yahoo: {yahoo}, Reddit: {reddit}")
    
    # Collect posts from all sources
    all_posts = {}  # symbol -> list of posts
    
    if yahoo:
        yahoo_data = scrape_yahoo(symbols)
        for sym, posts in yahoo_data.items():
            all_posts.setdefault(sym, []).extend(posts)
    
    if reddit:
        reddit_data = scrape_reddit(symbols)
        for sym, posts in reddit_data.items():
            all_posts.setdefault(sym, []).extend(posts)
    
    # Aggregate sentiment per symbol
    digests = []
    for sym in symbols:
        posts = all_posts.get(sym, [])
        digest = aggregate_sentiment(posts, sym)
        
        # Add source breakdown
        yahoo_count = sum(1 for p in posts if p.get('source') == 'yahoo_finance')
        reddit_count = sum(1 for p in posts if p.get('source') == 'reddit')
        digest['yahoo_posts'] = yahoo_count
        digest['reddit_posts'] = reddit_count
        
        digests.append(digest)
    
    if write_db:
        rows_written = write_to_db(digests, scan_id)
    else:
        rows_written = 0
        log.info("Dry run: skipped scanner.db write")
    persist_success = rows_written >= 0
    
    # Print summary
    log.info(f"\n{'='*60}")
    log.info(f"Scan Summary: {scan_id}")
    log.info(f"{'='*60}")
    
    active_digests = [d for d in digests if d['mention_count'] > 0]
    active_digests.sort(key=lambda x: abs(x['sentiment_score']), reverse=True)
    
    for d in active_digests[:15]:
        signal = d['contrarian_signal']
        marker = '***' if signal in ('SELL_PREMIUM', 'BUY_DIP') else ''
        log.info(
            f"  {d['symbol']:6s} | Score: {d['sentiment_score']:+.3f} | "
            f"Bull: {d['bull_count']}/{d['mention_count']} | "
            f"Y:{d['yahoo_posts']} R:{d['reddit_posts']} | "
            f"{signal} {marker}"
        )
    
    quiet = [d for d in digests if d['mention_count'] == 0]
    if quiet:
        log.info(f"  No mentions: {', '.join(d['symbol'] for d in quiet)}")
    
    if write_db:
        log.info(f"\nDB write: {'OK' if persist_success else 'FAILED'} ({rows_written} rows)")
    else:
        log.info("\nDB write: SKIPPED")
    
    return {
        'scan_id': scan_id,
        'total_symbols': len(symbols),
        'symbols_with_data': len(active_digests),
        'rows_written': rows_written,
        'persist_success': persist_success,
        'digests': digests
    }


# ============================================================
# CLI
# ============================================================
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='TIP Sentiment Scanner')
    parser.add_argument('--yahoo-only', action='store_true', help='Yahoo only')
    parser.add_argument('--reddit-only', action='store_true', help='Reddit only')
    parser.add_argument('--symbols', type=str, help='Comma-separated symbols')
    parser.add_argument('--dry-run', action='store_true', help='Skip scanner.db write')
    args = parser.parse_args()
    
    symbols = args.symbols.split(',') if args.symbols else None
    yahoo = not args.reddit_only
    reddit = not args.yahoo_only
    
    result = run_scan(symbols=symbols, yahoo=yahoo, reddit=reddit, write_db=not args.dry_run)
    
    if not result['persist_success']:
        sys.exit(1)
