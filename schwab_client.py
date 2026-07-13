"""
Schwab API Client for Options and Trading Data
Built for Manu's portfolio diversification research

OAuth2 Flow:
1. Run authenticate() to get initial tokens
2. Access token expires in 30 min (auto-refreshed)
3. Refresh token expires in 7 days (re-auth required)

Features:
- Option chains with full Greeks (delta, gamma, theta, vega, IV)
- Account positions and balances
- Historical price data
- Real-time quotes
"""

import os
import json
import base64
import time
import threading
import httpx
import webbrowser
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path
from urllib.parse import urlencode, parse_qs, urlparse


# ============================================================================
# RATE LIMITER — Respect Schwab's API limits
# Market Data: 120 calls/min | Trading: 60 calls/min | Account: 60 calls/min
# ============================================================================

class RateLimiter:
    """Sliding-window rate limiter for Schwab API endpoints"""

    # Endpoint category → (max_calls_per_minute, window_seconds)
    LIMITS = {
        'marketdata': (110, 60),  # 110/min (10 buffer below 120 hard limit)
        'trader':     (55, 60),   # 55/min  (5 buffer below 60 hard limit)
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._windows = {cat: deque() for cat in self.LIMITS}
        self._total_calls = 0
        self._throttle_events = 0

    def _categorize(self, endpoint: str) -> str:
        if '/marketdata/' in endpoint:
            return 'marketdata'
        elif '/trader/' in endpoint:
            return 'trader'
        return 'marketdata'  # Default to more conservative

    def wait_if_needed(self, endpoint: str):
        """Block until we're safe to make another call"""
        category = self._categorize(endpoint)
        max_calls, window_sec = self.LIMITS.get(category, (110, 60))

        with self._lock:
            now = time.monotonic()
            window = self._windows[category]

            # Prune old entries outside the window
            while window and window[0] < now - window_sec:
                window.popleft()

            if len(window) >= max_calls:
                # Need to wait until oldest call falls out of window
                sleep_time = window[0] + window_sec - now + 0.1
                if sleep_time > 0:
                    self._throttle_events += 1
                    print(f"⏳ Rate limit: waiting {sleep_time:.1f}s "
                          f"({category} {len(window)}/{max_calls}/min)")
                    # Release lock while sleeping
                    self._lock.release()
                    time.sleep(sleep_time)
                    self._lock.acquire()
                    # Re-prune after sleep
                    now = time.monotonic()
                    while window and window[0] < now - window_sec:
                        window.popleft()

            window.append(time.monotonic())
            self._total_calls += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                'total_calls': self._total_calls,
                'throttle_events': self._throttle_events,
                'windows': {cat: len(dq) for cat, dq in self._windows.items()},
            }


# Global rate limiter shared across all client instances
_rate_limiter = RateLimiter()


class SchwabClient:
    """Schwab API Client with OAuth2 and Options Support"""
    
    BASE_URL = "https://api.schwabapi.com"
    AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
    TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
    
    def __init__(self, client_id: str, client_secret: str, 
                 callback_url: str = "https://127.0.0.1",
                 token_path: str = None):
        """
        Initialize Schwab client
        
        Args:
            client_id: Your app's client ID from Schwab Developer Portal
            client_secret: Your app's client secret
            callback_url: OAuth callback URL (must match your app config)
            token_path: Path to store/load tokens (default: ~/.schwab/tokens.json)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.token_path = Path(token_path or os.path.expanduser("~/.schwab/tokens.json"))
        
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.account_hash = None
        
        # Create token directory
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Try to load existing tokens
        self._load_tokens()
    
    def _get_auth_header(self) -> str:
        """Get Base64-encoded auth header for client credentials"""
        credentials = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(credentials.encode()).decode()
    
    def _load_tokens(self):
        """Load tokens from disk if available"""
        if self.token_path.exists():
            try:
                with open(self.token_path, 'r') as f:
                    data = json.load(f)
                self.access_token = data.get('access_token')
                self.refresh_token = data.get('refresh_token')
                expires_at = data.get('expires_at')
                if expires_at:
                    self.token_expires_at = datetime.fromisoformat(expires_at)
                print(f"✓ Loaded tokens from {self.token_path}")
                
                # Check if refresh needed
                if self._is_token_expired():
                    print("⚠ Access token expired, attempting refresh...")
                    self.refresh_access_token()
            except Exception as e:
                print(f"⚠ Could not load tokens: {e}")
    
    def _save_tokens(self):
        """Save tokens to disk"""
        data = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None,
            'saved_at': datetime.now().isoformat()
        }
        with open(self.token_path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✓ Tokens saved to {self.token_path}")
    
    def _is_token_expired(self) -> bool:
        """Check if access token is expired (with 5 min buffer)"""
        if not self.token_expires_at:
            return True
        return datetime.now() >= (self.token_expires_at - timedelta(minutes=5))
    
    def get_authorization_url(self) -> str:
        """Get URL for user authorization"""
        return f"{self.AUTH_URL}?client_id={self.client_id}&redirect_uri={self.callback_url}"
    
    def authenticate(self, authorization_code: str = None):
        """
        Complete OAuth2 authentication
        
        If no authorization_code provided, opens browser for user login.
        After login, user must paste the redirect URL containing the code.
        """
        if not authorization_code:
            # Step 1: Open browser for authorization
            auth_url = self.get_authorization_url()
            print("\n" + "="*60)
            print("SCHWAB AUTHENTICATION")
            print("="*60)
            print("\n1. Opening browser for Schwab login...")
            print(f"\n   If browser doesn't open, visit:\n   {auth_url}")
            
            webbrowser.open(auth_url)
            
            print("\n2. Log in with your Schwab brokerage credentials")
            print("   (NOT your developer portal credentials)")
            
            print("\n3. Select account(s) to authorize")
            
            print("\n4. After redirect, paste the FULL URL from your browser:")
            redirect_url = input("\n   URL: ").strip()
            
            # Extract code from URL
            parsed = urlparse(redirect_url)
            params = parse_qs(parsed.query)
            
            if 'code' not in params:
                raise ValueError("No authorization code found in URL. URL should contain 'code=' parameter.")
            
            authorization_code = params['code'][0]
            print(f"\n✓ Authorization code extracted: {authorization_code[:20]}...")
        
        # Step 2: Exchange code for tokens
        print("\n5. Exchanging code for tokens...")
        
        headers = {
            'Authorization': f'Basic {self._get_auth_header()}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'authorization_code',
            'code': authorization_code,
            'redirect_uri': self.callback_url
        }
        
        with httpx.Client() as client:
            response = client.post(self.TOKEN_URL, headers=headers, data=data)
        
        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.status_code} - {response.text}")
        
        token_data = response.json()
        self._process_token_response(token_data)
        
        print("\n" + "="*60)
        print("✓ AUTHENTICATION SUCCESSFUL!")
        print("="*60)
        print(f"\n  Access token expires in: {token_data.get('expires_in', 1800)} seconds")
        print(f"  Refresh token valid for: 7 days")
        print(f"  Tokens saved to: {self.token_path}")
        
        return True
    
    def _process_token_response(self, token_data: dict):
        """Process token response and save"""
        self.access_token = token_data['access_token']
        self.refresh_token = token_data.get('refresh_token', self.refresh_token)
        
        expires_in = token_data.get('expires_in', 1800)  # Default 30 min
        self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        self._save_tokens()
    
    def refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            raise ValueError("No refresh token available. Call authenticate() first.")
        
        headers = {
            'Authorization': f'Basic {self._get_auth_header()}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token
        }
        
        with httpx.Client() as client:
            response = client.post(self.TOKEN_URL, headers=headers, data=data)
        
        if response.status_code != 200:
            print(f"⚠ Token refresh failed: {response.text}")
            print("  Refresh token may have expired (7 day limit)")
            print("  Please run authenticate() again")
            return False
        
        self._process_token_response(response.json())
        print("✓ Access token refreshed successfully")
        return True
    
    def _ensure_token(self):
        """Ensure we have a valid access token"""
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")
        
        if self._is_token_expired():
            if not self.refresh_access_token():
                raise ValueError("Token expired and refresh failed. Re-authenticate required.")
    
    def _request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None) -> dict:
        """Make authenticated API request with rate limiting and retry"""
        self._ensure_token()

        # Rate limit before making the call
        _rate_limiter.wait_if_needed(endpoint)

        url = f"{self.BASE_URL}{endpoint}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

        max_retries = 3
        for attempt in range(max_retries):
            with httpx.Client(timeout=30) as client:
                response = client.request(method, url, headers=headers, params=params, json=json_data)

            if response.status_code == 401:
                # Token expired — refresh and retry
                if self.refresh_access_token():
                    headers['Authorization'] = f'Bearer {self.access_token}'
                    continue

            if response.status_code == 429:
                # Rate limited by Schwab — back off exponentially
                wait = (2 ** attempt) * 5  # 5s, 10s, 20s
                print(f"⚠️  429 Rate limited by Schwab. Backing off {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue

            if response.status_code == 503:
                # Service temporarily unavailable
                wait = (2 ** attempt) * 3
                print(f"⚠️  503 Service unavailable. Retrying in {wait}s")
                time.sleep(wait)
                continue

            break  # Success or non-retryable error

        response.raise_for_status()
        return response.json()

    @staticmethod
    def rate_limit_stats() -> dict:
        """Get current rate limiter statistics"""
        return _rate_limiter.stats()
    
    # ================================================================
    # ACCOUNT METHODS
    # ================================================================
    
    def get_account_numbers(self) -> list:
        """Get all linked account numbers and their hashes"""
        data = self._request('GET', '/trader/v1/accounts/accountNumbers')
        return data
    
    def get_accounts(self, include_positions: bool = True) -> dict:
        """
        Get all accounts with balances and positions
        
        Args:
            include_positions: Include position details (default: True)
        """
        endpoint = '/trader/v1/accounts'
        params = {'fields': 'positions'} if include_positions else None
        return self._request('GET', endpoint, params=params)
    
    def get_account(self, account_hash: str, include_positions: bool = True) -> dict:
        """Get specific account details"""
        params = {'fields': 'positions'} if include_positions else None
        return self._request('GET', f'/trader/v1/accounts/{account_hash}', params=params)
    
    # ================================================================
    # MARKET DATA METHODS  
    # ================================================================
    
    def get_quote(self, symbol: str) -> dict:
        """Get real-time quote for a single symbol"""
        return self._request('GET', f'/marketdata/v1/quotes/{symbol}')
    
    def get_quotes(self, symbols: list) -> dict:
        """Get real-time quotes for multiple symbols"""
        params = {'symbols': ','.join(symbols)}
        return self._request('GET', '/marketdata/v1/quotes', params=params)

    def get_quotes_with_fundamentals(self, symbols: list) -> dict:
        """Get quotes + fundamental data (PE, div yield, market cap, etc.)"""
        params = {
            'symbols': ','.join(symbols),
            'fields': 'quote,fundamental',
        }
        return self._request('GET', '/marketdata/v1/quotes', params=params)

    def get_movers(self, index: str = '$SPX', sort: str = 'percent_change_up',
                   frequency: int = 0) -> dict:
        """
        Get top movers for a market index.
        Args:
            index: '$SPX', '$DJI', '$COMPQ', '$NASDAQ'
            sort: 'volume', 'trades', 'percent_change_up', 'percent_change_down'
            frequency: 0=all, 1=1%+, 5=5%+, 10=10%+
        """
        params = {'sort': sort, 'frequency': frequency}
        return self._request('GET', f'/marketdata/v1/movers/{index}', params=params)

    def search_instruments(self, symbol: str, projection: str = 'fundamental') -> dict:
        """
        Search instruments or get fundamentals.
        Args:
            symbol: Symbol or search term
            projection: 'symbol-search', 'symbol-regex', 'desc-search', 'desc-regex', 'fundamental'
        """
        params = {'symbol': symbol, 'projection': projection}
        return self._request('GET', '/marketdata/v1/instruments', params=params)

    # ================================================================
    # OPTIONS CHAIN METHODS - THE MAIN EVENT!
    # ================================================================
    
    def get_option_chain(self, symbol: str, 
                        contract_type: str = 'ALL',
                        strike_count: int = None,
                        include_underlying_quote: bool = True,
                        from_date: str = None,
                        to_date: str = None,
                        strike_range: str = None,
                        strike: float = None,
                        exp_month: str = None) -> dict:
        """
        Get option chain for a symbol with full Greeks
        
        Args:
            symbol: Underlying symbol (e.g., 'RTX', 'LMT', 'PFE')
            contract_type: 'CALL', 'PUT', or 'ALL' (default)
            strike_count: Number of strikes above/below ATM
            include_underlying_quote: Include underlying stock quote
            from_date: Filter expirations after this date (YYYY-MM-DD)
            to_date: Filter expirations before this date (YYYY-MM-DD)
            strike_range: 'ITM', 'NTM', 'OTM', 'SAK' (strikes above), 
                         'SBK' (strikes below), 'SNK' (strikes near)
            strike: Specific strike price
            exp_month: Expiration month ('JAN', 'FEB', etc.)
        
        Returns:
            Dict containing:
            - underlying: Quote data for the stock
            - callExpDateMap: Calls organized by expiration -> strike
            - putExpDateMap: Puts organized by expiration -> strike
            
            Each option contract includes:
            - bid, ask, last, mark, bidSize, askSize
            - delta, gamma, theta, vega, rho
            - impliedVolatility (IV)
            - openInterest, totalVolume
            - daysToExpiration, expirationDate
            - inTheMoney, intrinsicValue, timeValue
        """
        params = {
            'symbol': symbol,
            'contractType': contract_type,
            'includeUnderlyingQuote': str(include_underlying_quote).lower()
        }
        
        if strike_count:
            params['strikeCount'] = strike_count
        if from_date:
            params['fromDate'] = from_date
        if to_date:
            params['toDate'] = to_date
        if strike_range:
            params['range'] = strike_range
        if strike:
            params['strike'] = strike
        if exp_month:
            params['expMonth'] = exp_month
        
        return self._request('GET', '/marketdata/v1/chains', params=params)
    
    def get_option_chain_dataframe(self, symbol: str, **kwargs) -> 'pd.DataFrame':
        """
        Get option chain as a pandas DataFrame for easy analysis
        
        Returns DataFrame with columns:
        - symbol, putCall, strike, expiration, daysToExpiration
        - bid, ask, last, mark, bidAskSpread
        - delta, gamma, theta, vega, rho, impliedVolatility
        - openInterest, totalVolume
        - inTheMoney, intrinsicValue, timeValue
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required. Install with: pip install pandas")
        
        chain = self.get_option_chain(symbol, **kwargs)
        
        rows = []
        
        # Process calls
        for exp_date, strikes in chain.get('callExpDateMap', {}).items():
            for strike, contracts in strikes.items():
                for contract in contracts:
                    contract['expDate'] = exp_date.split(':')[0]  # Remove time part
                    rows.append(contract)
        
        # Process puts
        for exp_date, strikes in chain.get('putExpDateMap', {}).items():
            for strike, contracts in strikes.items():
                for contract in contracts:
                    contract['expDate'] = exp_date.split(':')[0]
                    rows.append(contract)
        
        df = pd.DataFrame(rows)
        
        # Calculate bid-ask spread
        if 'bid' in df.columns and 'ask' in df.columns:
            df['bidAskSpread'] = df['ask'] - df['bid']
            df['bidAskPct'] = (df['bidAskSpread'] / df['mark'] * 100).round(2)
        
        # Reorder columns for usability
        priority_cols = [
            'symbol', 'putCall', 'strikePrice', 'expDate', 'daysToExpiration',
            'bid', 'ask', 'mark', 'last', 'bidAskSpread', 'bidAskPct',
            'delta', 'gamma', 'theta', 'vega', 'rho', 'volatility',
            'openInterest', 'totalVolume',
            'inTheMoney', 'intrinsicValue', 'timeValue'
        ]
        
        existing_cols = [c for c in priority_cols if c in df.columns]
        other_cols = [c for c in df.columns if c not in priority_cols]
        df = df[existing_cols + other_cols]
        
        return df
    
    def find_cash_secured_puts(self, symbol: str, 
                               min_delta: float = -0.30,
                               max_delta: float = -0.15,
                               min_days: int = 30,
                               max_days: int = 60,
                               min_premium_pct: float = 1.0) -> 'pd.DataFrame':
        """
        Find attractive cash-secured put candidates for income generation
        
        This is tailored for your diversification strategy!
        
        Args:
            symbol: Stock symbol (e.g., 'RTX', 'LMT', 'PFE', 'NEE')
            min_delta: Minimum delta (more negative = more ITM), default -0.30
            max_delta: Maximum delta (closer to 0 = more OTM), default -0.15
            min_days: Minimum days to expiration (default 30)
            max_days: Maximum days to expiration (default 60)
            min_premium_pct: Minimum premium as % of strike (default 1.0%)
        
        Returns:
            DataFrame of put contracts sorted by premium yield
        """
        import pandas as pd
        
        # Get chain for puts only
        df = self.get_option_chain_dataframe(
            symbol,
            contract_type='PUT',
            from_date=(datetime.now() + timedelta(days=min_days)).strftime('%Y-%m-%d'),
            to_date=(datetime.now() + timedelta(days=max_days)).strftime('%Y-%m-%d')
        )
        
        if df.empty:
            print(f"No puts found for {symbol} in date range")
            return df
        
        # Filter by delta range (puts have negative delta)
        mask = (
            (df['delta'] >= min_delta) & 
            (df['delta'] <= max_delta) &
            (df['daysToExpiration'] >= min_days) &
            (df['daysToExpiration'] <= max_days)
        )
        df = df[mask].copy()
        
        if df.empty:
            print(f"No puts match criteria. Try adjusting delta range.")
            return df
        
        # Calculate premium metrics
        df['premiumPct'] = (df['mark'] / df['strikePrice'] * 100).round(2)
        df['annualizedYield'] = (df['premiumPct'] * 365 / df['daysToExpiration']).round(2)
        df['cashRequired'] = df['strikePrice'] * 100  # Per contract
        
        # Filter by minimum premium
        df = df[df['premiumPct'] >= min_premium_pct]
        
        # Sort by annualized yield
        df = df.sort_values('annualizedYield', ascending=False)
        
        # Select relevant columns
        cols = [
            'symbol', 'strikePrice', 'expDate', 'daysToExpiration',
            'bid', 'ask', 'mark', 'bidAskSpread',
            'delta', 'theta', 'volatility',
            'premiumPct', 'annualizedYield', 'cashRequired',
            'openInterest', 'totalVolume'
        ]
        
        return df[[c for c in cols if c in df.columns]]
    
    # ================================================================
    # PRICE HISTORY METHODS
    # ================================================================
    
    def get_price_history(self, symbol: str,
                         period_type: str = 'month',
                         period: int = 1,
                         frequency_type: str = 'daily',
                         frequency: int = 1) -> dict:
        """
        Get historical price data
        
        Args:
            symbol: Stock symbol
            period_type: 'day', 'month', 'year', 'ytd'
            period: Number of periods
            frequency_type: 'minute', 'daily', 'weekly', 'monthly'
            frequency: Frequency value
        """
        params = {
            'symbol': symbol,
            'periodType': period_type,
            'period': period,
            'frequencyType': frequency_type,
            'frequency': frequency
        }
        return self._request('GET', f'/marketdata/v1/pricehistory', params=params)
    
    # ================================================================
    # ORDER METHODS
    # ================================================================
    
    def get_orders(self, account_hash: str, 
                   from_date: str = None,
                   to_date: str = None,
                   status: str = None) -> list:
        """
        Get orders for an account
        
        Args:
            account_hash: Account hash from get_account_numbers()
            from_date: Start date (ISO format)
            to_date: End date (ISO format)
            status: Filter by status ('WORKING', 'FILLED', 'REJECTED', etc.)
        """
        params = {}
        if from_date:
            params['fromEnteredTime'] = from_date
        if to_date:
            params['toEnteredTime'] = to_date
        if status:
            params['status'] = status
        
        return self._request('GET', f'/trader/v1/accounts/{account_hash}/orders', params=params)
    
    def get_transactions(self, account_hash: str,
                        types: str = None,
                        start_date: str = None,
                        end_date: str = None) -> list:
        """
        Get account transactions
        
        Args:
            account_hash: Account hash
            types: Transaction type filter
            start_date: Start date
            end_date: End date
        """
        params = {}
        if types:
            params['types'] = types
        if start_date:
            params['startDate'] = start_date
        if end_date:
            params['endDate'] = end_date
        
        return self._request('GET', f'/trader/v1/accounts/{account_hash}/transactions', params=params)


# ================================================================
# CONVENIENCE FUNCTIONS 
# ================================================================

def create_client_from_env() -> SchwabClient:
    """Create client using environment variables"""
    client_id = os.environ.get('SCHWAB_CLIENT_ID')
    client_secret = os.environ.get('SCHWAB_CLIENT_SECRET')
    callback_url = os.environ.get('SCHWAB_CALLBACK_URL', 'https://developer.schwab.com/oauth2-redirect.html')
    
    if not client_id or not client_secret:
        raise ValueError("Set SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET environment variables")
    
    return SchwabClient(client_id, client_secret, callback_url)


def analyze_put_opportunities(client: SchwabClient, symbols: list) -> 'pd.DataFrame':
    """
    Analyze cash-secured put opportunities across multiple symbols
    
    Perfect for scanning diversification candidates!
    
    Args:
        client: Authenticated SchwabClient
        symbols: List of symbols to analyze
    
    Returns:
        Combined DataFrame of all put opportunities
    """
    import pandas as pd
    
    all_puts = []
    
    for symbol in symbols:
        print(f"Scanning {symbol}...")
        try:
            puts = client.find_cash_secured_puts(symbol)
            if not puts.empty:
                puts['underlying'] = symbol
                all_puts.append(puts)
        except Exception as e:
            print(f"  Error: {e}")
    
    if all_puts:
        combined = pd.concat(all_puts, ignore_index=True)
        return combined.sort_values('annualizedYield', ascending=False)
    
    return pd.DataFrame()


# ================================================================
# CLI INTERFACE
# ================================================================

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Schwab API Client')
    parser.add_argument('command', choices=['auth', 'accounts', 'chain', 'puts', 'quote'],
                       help='Command to run')
    parser.add_argument('--symbol', '-s', help='Stock symbol')
    parser.add_argument('--client-id', help='Schwab client ID')
    parser.add_argument('--client-secret', help='Schwab client secret')
    
    args = parser.parse_args()
    
    # Get credentials
    client_id = args.client_id or os.environ.get('SCHWAB_CLIENT_ID')
    client_secret = args.client_secret or os.environ.get('SCHWAB_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        print("ERROR: Provide --client-id and --client-secret or set environment variables")
        print("       SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET")
        exit(1)
    
    client = SchwabClient(client_id, client_secret)
    
    if args.command == 'auth':
        client.authenticate()
        
    elif args.command == 'accounts':
        accounts = client.get_accounts()
        print(json.dumps(accounts, indent=2))
        
    elif args.command == 'chain':
        if not args.symbol:
            print("ERROR: --symbol required for chain command")
            exit(1)
        chain = client.get_option_chain(args.symbol)
        print(json.dumps(chain, indent=2)[:5000])  # Truncate for readability
        
    elif args.command == 'puts':
        if not args.symbol:
            print("ERROR: --symbol required for puts command")
            exit(1)
        puts = client.find_cash_secured_puts(args.symbol)
        print(puts.to_string())
        
    elif args.command == 'quote':
        if not args.symbol:
            print("ERROR: --symbol required for quote command")
            exit(1)
        quote = client.get_quote(args.symbol)
        print(json.dumps(quote, indent=2))
