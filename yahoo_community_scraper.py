"""
Yahoo Finance Community Scraper
Reverse-engineered GraphQL API for Yahoo Finance community conversations.
Endpoint: POST https://yfc-server-query.finance.yahoo.com/
Requires: curl_cffi (for browser TLS fingerprinting)

Usage:
    scraper = YahooCommunityScaper()
    posts = scraper.get_posts('INTC', count=20)
    
Integration: Plugs into TIP sentiment_engine.py alongside Reddit scraper.
Runs from Mac (residential IP) via launchd.
"""

import json
import time
import re
import logging
from datetime import datetime, timezone
from typing import Optional

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    raise ImportError("curl_cffi required: pip3 install curl_cffi")

logger = logging.getLogger(__name__)

# The GraphQL query used by Yahoo Finance community tab
COMMUNITY_GQL_QUERY = """query GetContentByAssociatedContentId($contentId: String!, $sortOrder: SortOrder, $first: Int!, $after: Cursor, $postUuid: UUID!, $includePost: Boolean!) {
  getContentByAssociatedContentId(contentId: $contentId, sortOrder: $sortOrder) {
    contentUuid
    newFeed(first: $first, after: $after) {
      ...Feed
      __typename
    }
    __typename
  }
  ... on Query @include(if: $includePost) {
    getPost(uuid: $postUuid) {
      ...FeedPost
      __typename
    }
    __typename
  }
}
fragment PageInfo on PageInfo {
  endCursor
  hasNextPage
  __typename
}
fragment BasePrice on Prices {
  last {
    close
    timestamp
    __typename
  }
  reference {
    close
    timestamp
    __typename
  }
  __typename
}
fragment AssetOneWeek on Asset {
  name
  symbol
  currency
  prices(period: ONE_WEEK) {
    ...BasePrice
    __typename
  }
  __typename
}
fragment BaseUser on User {
  uuid
  followerAssets
  profile {
    uuid
    username
    name
    picture
    private
    badges
    bio
    website
    creator
    investorIdentity
    acceptedTerms
    __typename
  }
  __typename
}
fragment UserWithRelationships on User {
  ...BaseUser
  relationships {
    backRelationship
    relationship
    __typename
  }
  __typename
}
fragment BaseUpvotes on Votes {
  myVote
  upvoteCount
  upvoteAum
  __typename
}
fragment BaseTrade on Trade {
  uuid
  contentType
  tradeDate
  tradePrice
  tradeType
  asset {
    ...AssetOneWeek
    __typename
  }
  user {
    ...UserWithRelationships
    __typename
  }
  votes {
    ...BaseUpvotes
    __typename
  }
  __typename
}
fragment BaseComment on Comment {
  __typename
  body
  createdAt
  updatedAt
  clientRequestId
  contentType
  uuid
  archivedAt
  appealed
  status
  parentComment {
    uuid
    __typename
  }
  rootContent {
    ... on Content {
      contentType
      uuid
      __typename
    }
    ... on Post {
      associatedContentUuid
      associatedContent {
        __typename
      }
      __typename
    }
    __typename
  }
  user {
    ...UserWithRelationships
    __typename
  }
  votes {
    ...BaseUpvotes
    __typename
  }
}
fragment CommentEdge on CommentEdge {
  uuid
  cursor
  node {
    ...BaseComment
    comments(first: 0, after: null) {
      count
      pageInfo {
        ...PageInfo
        __typename
      }
      edges {
        uuid
        cursor
        node {
          ...BaseComment
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
fragment Comments on Comments {
  count
  pageInfo {
    ...PageInfo
    __typename
  }
  edges {
    ...CommentEdge
    __typename
  }
  __typename
}
fragment FeedTrade on Trade {
  ...BaseTrade
  comments(first: 1, after: null) {
    ...Comments
    __typename
  }
  __typename
}
fragment BasePost on Post {
  archivedAt
  associatedContentUuid
  body
  createdAt
  publishedAt
  updatedAt
  uuid
  clientRequestId
  contentType
  user {
    ...UserWithRelationships
    __typename
  }
  votes {
    ...BaseUpvotes
    __typename
  }
  associatedContent {
    __typename
    ... on QuoteSummaryPage {
      symbol
      url
      __typename
    }
  }
  __typename
}
fragment FeedPost on Post {
  ...BasePost
  comments(first: 1, after: null) {
    ...Comments
    __typename
  }
  __typename
}
fragment FeedComment on Comment {
  ...BaseComment
  comments(first: 1, after: null) {
    ...Comments
    __typename
  }
  __typename
}
fragment Feed on Feed {
  __typename
  pageInfo {
    ...PageInfo
    __typename
  }
  edges {
    cursor
    node {
      uuid
      contentType
      ...FeedTrade
      ...FeedPost
      ...FeedComment
      __typename
    }
    __typename
  }
}"""

# Known contentId mappings (finmb_XXXXX format)
# These are Yahoo Finance message board IDs, discovered by visiting each ticker's community page
CONTENT_ID_MAP = {
    # Will be populated by discover_content_ids()
    'INTC': 'finmb_21127',
}


class YahooCommunityScaper:
    """Scrapes Yahoo Finance community conversations via GraphQL API."""
    
    API_URL = 'https://yfc-server-query.finance.yahoo.com/'
    
    def __init__(self, rate_limit_delay: float = 1.5, content_ids_file: str = None):
        """
        Args:
            rate_limit_delay: Seconds between requests to avoid throttling
            content_ids_file: Path to yahoo_content_ids.json (auto-detected if None)
        """
        self.session = cffi_requests.Session(impersonate="chrome120")
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time = 0
        self._session_initialized = False
        self._max_retries = 3
        
        # Auto-load content IDs from JSON
        if content_ids_file is None:
            import os
            content_ids_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 
                "yahoo_content_ids.json"
            )
        
        if os.path.exists(content_ids_file):
            try:
                with open(content_ids_file) as f:
                    data = json.load(f)
                CONTENT_ID_MAP.update(data.get("content_ids", {}))
                logger.info(f"Loaded {len(data.get('content_ids', {}))} content IDs from {content_ids_file}")
            except Exception as e:
                logger.warning(f"Could not load content IDs: {e}")
    
    def _init_session(self):
        """Visit Yahoo Finance to establish session cookies."""
        if self._session_initialized:
            return
        
        logger.info("Initializing Yahoo Finance session...")
        try:
            r = self.session.get('https://finance.yahoo.com/', timeout=15)
            if r.status_code == 200:
                self._session_initialized = True
                logger.info("Session initialized (cookies acquired)")
            else:
                logger.warning(f"Session init returned {r.status_code}")
        except Exception as e:
            logger.error(f"Session init failed: {e}")
            raise
    
    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()
    
    def discover_content_id(self, symbol: str) -> Optional[str]:
        """
        Discover a ticker's contentId by loading its community page
        and extracting from the __data.json endpoint.
        
        Returns: contentId string (e.g., 'finmb_21127') or None
        """
        self._init_session()
        self._rate_limit()
        
        try:
            # Method 1: __data.json endpoint
            url = f'https://finance.yahoo.com/__data.json?q=/quote/{symbol}/community'
            r = self.session.get(url, timeout=15)
            
            if r.status_code == 200:
                data = r.json()
                # The contentId is embedded in the page data
                # Search for finmb_ pattern in the response
                text = json.dumps(data)
                match = re.search(r'finmb_(\d+)', text)
                if match:
                    content_id = f'finmb_{match.group(1)}'
                    logger.info(f"Discovered {symbol} contentId: {content_id}")
                    return content_id
            
            # Method 2: Load community page HTML and search
            self._rate_limit()
            r2 = self.session.get(
                f'https://finance.yahoo.com/quote/{symbol}/community/',
                timeout=15
            )
            if r2.status_code == 200:
                match = re.search(r'finmb_(\d+)', r2.text)
                if match:
                    content_id = f'finmb_{match.group(1)}'
                    logger.info(f"Discovered {symbol} contentId from HTML: {content_id}")
                    return content_id
            
            logger.warning(f"Could not discover contentId for {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"Error discovering contentId for {symbol}: {e}")
            return None
    
    def discover_content_ids(self, symbols: list) -> dict:
        """
        Bulk discover contentIds for a list of symbols.
        Returns dict of {symbol: contentId}.
        """
        results = {}
        for sym in symbols:
            if sym in CONTENT_ID_MAP:
                results[sym] = CONTENT_ID_MAP[sym]
                continue
            
            content_id = self.discover_content_id(sym)
            if content_id:
                results[sym] = content_id
                CONTENT_ID_MAP[sym] = content_id
            
            time.sleep(self.rate_limit_delay)
        
        return results
    
    def get_posts(self, symbol: str, count: int = 20, 
                  cursor: str = None) -> dict:
        """
        Fetch community posts for a ticker.
        
        Args:
            symbol: Stock ticker (e.g., 'INTC')
            count: Number of posts to fetch (max ~20 per page)
            cursor: Pagination cursor for next page (base64 string)
        
        Returns:
            dict with keys: posts (list), has_more (bool), 
            next_cursor (str), symbol (str)
        """
        self._init_session()
        
        # Get contentId
        content_id = CONTENT_ID_MAP.get(symbol)
        if not content_id:
            content_id = self.discover_content_id(symbol)
            if not content_id:
                return {'posts': [], 'has_more': False, 
                        'next_cursor': None, 'symbol': symbol,
                        'error': f'Could not find contentId for {symbol}'}
        
        self._rate_limit()
        
        # Build GraphQL request
        payload = {
            'operationName': 'GetContentByAssociatedContentId',
            'query': COMMUNITY_GQL_QUERY,
            'variables': {
                'contentId': content_id,
                'first': min(count, 20),
                'after': cursor,
                'sortOrder': 'TIME_DESC',
                'includePost': False,
                'postUuid': '00000000-0000-0000-0000-000000000000'
            }
        }
        
        try:
            r = self.session.post(
                self.API_URL,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=15
            )
            
            if r.status_code != 200:
                logger.error(f"API returned {r.status_code} for {symbol}")
                return {'posts': [], 'has_more': False,
                        'next_cursor': None, 'symbol': symbol,
                        'error': f'HTTP {r.status_code}'}
            
            data = r.json()
            feed = (data.get('data', {})
                   .get('getContentByAssociatedContentId', {})
                   .get('newFeed', {}))
            
            edges = feed.get('edges', [])
            page_info = feed.get('pageInfo', {})
            
            posts = []
            for edge in edges:
                node = edge.get('node', {})
                if not node:
                    continue
                
                content_type = node.get('contentType', '')
                body = node.get('body', '')
                created_at = node.get('createdAt', '')
                uuid = node.get('uuid', '')
                
                # Extract author info
                user = node.get('user', {})
                profile = user.get('profile', {})
                author = profile.get('name') or profile.get('username') or 'Anonymous'
                
                # Extract votes
                votes = node.get('votes', {})
                upvotes = votes.get('upvoteCount', 0)
                
                # Extract comment count
                comments = node.get('comments', {})
                comment_count = comments.get('count', 0)
                
                # Extract associated symbol
                assoc = node.get('associatedContent', {})
                assoc_symbol = assoc.get('symbol', symbol)
                
                posts.append({
                    'uuid': uuid,
                    'body': body,
                    'created_at': created_at,
                    'author': author,
                    'upvotes': upvotes,
                    'comment_count': comment_count,
                    'content_type': content_type,
                    'symbol': assoc_symbol,
                    'source': 'yahoo_finance_community'
                })
            
            return {
                'posts': posts,
                'has_more': page_info.get('hasNextPage', False),
                'next_cursor': page_info.get('endCursor'),
                'symbol': symbol,
                'count': len(posts),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error fetching posts for {symbol}: {e}")
            return {'posts': [], 'has_more': False,
                    'next_cursor': None, 'symbol': symbol,
                    'error': str(e)}
    
    def get_all_posts(self, symbol: str, max_posts: int = 60) -> list:
        """
        Fetch multiple pages of posts up to max_posts.
        Handles pagination automatically.
        """
        all_posts = []
        cursor = None
        
        while len(all_posts) < max_posts:
            result = self.get_posts(symbol, count=20, cursor=cursor)
            
            if result.get('error') or not result['posts']:
                break
            
            all_posts.extend(result['posts'])
            
            if not result['has_more']:
                break
            
            cursor = result['next_cursor']
            time.sleep(self.rate_limit_delay)
        
        return all_posts[:max_posts]
    
    def scan_watchlist(self, symbols: list, posts_per_symbol: int = 20) -> dict:
        """
        Scan multiple tickers' community conversations.
        Returns dict keyed by symbol.
        
        Args:
            symbols: List of ticker symbols
            posts_per_symbol: Posts to fetch per ticker
        
        Returns:
            dict of {symbol: {posts: [...], sentiment_summary: {...}}}
        """
        results = {}
        
        # First discover any missing contentIds
        missing = [s for s in symbols if s not in CONTENT_ID_MAP]
        if missing:
            logger.info(f"Discovering contentIds for {len(missing)} symbols...")
            self.discover_content_ids(missing)
        
        for sym in symbols:
            if sym not in CONTENT_ID_MAP:
                logger.warning(f"Skipping {sym} - no contentId found")
                results[sym] = {'posts': [], 'error': 'no_content_id'}
                continue
            
            result = self.get_posts(sym, count=posts_per_symbol)
            
            # Basic sentiment summary
            if result['posts']:
                bodies = [p['body'] for p in result['posts'] if p['body']]
                total_upvotes = sum(p['upvotes'] for p in result['posts'])
                avg_upvotes = total_upvotes / len(result['posts']) if result['posts'] else 0
                
                result['sentiment_summary'] = {
                    'post_count': len(result['posts']),
                    'total_upvotes': total_upvotes,
                    'avg_upvotes': round(avg_upvotes, 1),
                    'total_comments': sum(p['comment_count'] for p in result['posts']),
                    'newest_post': result['posts'][0]['created_at'] if result['posts'] else None,
                    'oldest_post': result['posts'][-1]['created_at'] if result['posts'] else None,
                }
            
            results[sym] = result
            logger.info(f"{sym}: {result.get('count', 0)} posts fetched")
        
        return results


# ============================================================
# Standalone test
# ============================================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    scraper = YahooCommunityScaper()
    
    # Test with INTC (known contentId)
    print("=" * 60)
    print("Testing INTC community scrape...")
    result = scraper.get_posts('INTC', count=10)
    print(f"Status: {result.get('count', 0)} posts, has_more={result.get('has_more')}")
    
    for i, post in enumerate(result.get('posts', [])[:5]):
        body = post['body'][:120].replace('\n', ' ')
        print(f"  [{i+1}] {post['created_at'][:16]} | Up:{post['upvotes']} | {body}")
    
    # Test contentId discovery for a few tickers
    print("\n" + "=" * 60)
    print("Discovering contentIds for sample tickers...")
    test_symbols = ['NVDA', 'TSLA', 'AAPL', 'AMD', 'ORCL', 'MSFT', 'AMZN', 'LLY', 'UNH', 'JPM']
    
    for sym in test_symbols:
        content_id = scraper.discover_content_id(sym)
        status = f"OK: {content_id}" if content_id else "FAILED"
        print(f"  {sym:6s} -> {status}")
    
    # Test fetching posts for a discovered ticker
    print("\n" + "=" * 60)
    if 'NVDA' in CONTENT_ID_MAP:
        print(f"Testing NVDA community scrape (contentId: {CONTENT_ID_MAP['NVDA']})...")
        result2 = scraper.get_posts('NVDA', count=5)
        for i, post in enumerate(result2.get('posts', [])[:3]):
            body = post['body'][:120].replace('\n', ' ')
            print(f"  [{i+1}] {post['created_at'][:16]} | Up:{post['upvotes']} | {body}")
    
    print("\n" + "=" * 60)
    print(f"Content ID Map ({len(CONTENT_ID_MAP)} tickers):")
    for sym, cid in sorted(CONTENT_ID_MAP.items()):
        print(f"  {sym:6s} -> {cid}")

