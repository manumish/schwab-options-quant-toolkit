"""
Earnings Calendar Module
Fetches upcoming earnings dates to avoid selling puts before earnings
"""

import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List
import json

@dataclass
class EarningsInfo:
    """Earnings information for a symbol"""
    symbol: str
    earnings_date: Optional[datetime]
    days_until: Optional[int]
    time_of_day: str  # 'BMO' (before market open), 'AMC' (after market close), 'Unknown'
    has_upcoming: bool
    warning: str  # Empty if safe, warning message if earnings soon

class EarningsCalendar:
    """Fetches and tracks earnings dates"""
    
    def __init__(self):
        # Cache earnings data
        self._cache = {}
        self._cache_time = {}
        self._cache_duration = timedelta(hours=6)
    
    def get_earnings_date(self, symbol: str) -> EarningsInfo:
        """Get earnings info for a symbol using web search"""
        
        # Check cache
        if symbol in self._cache:
            cache_age = datetime.now() - self._cache_time.get(symbol, datetime.min)
            if cache_age < self._cache_duration:
                return self._cache[symbol]
        
        # For now, use a simple approach - we'll enhance this later
        # In production, this would call an earnings API like Alpha Vantage or scrape
        
        # Default response (unknown)
        info = EarningsInfo(
            symbol=symbol,
            earnings_date=None,
            days_until=None,
            time_of_day='Unknown',
            has_upcoming=False,
            warning=''
        )
        
        # Try to get from Yahoo Finance (simple scrape)
        try:
            with httpx.Client(timeout=10) as client:
                # Yahoo Finance quote page has earnings date
                resp = client.get(
                    f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}',
                    params={
                        'modules': 'calendarEvents'
                    },
                    headers={
                        'User-Agent': 'Mozilla/5.0'
                    }
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get('quoteSummary', {}).get('result', [])
                    
                    if result:
                        calendar = result[0].get('calendarEvents', {})
                        earnings = calendar.get('earnings', {})
                        
                        # Get earnings date
                        earnings_date_raw = earnings.get('earningsDate', [])
                        if earnings_date_raw:
                            # Unix timestamp
                            timestamp = earnings_date_raw[0].get('raw', 0)
                            if timestamp:
                                earnings_date = datetime.fromtimestamp(timestamp)
                                days_until = (earnings_date - datetime.now()).days
                                
                                # Determine warning
                                warning = ''
                                if 0 <= days_until <= 7:
                                    warning = f"⚠️ EARNINGS IN {days_until} DAYS - Avoid selling puts!"
                                elif 7 < days_until <= 14:
                                    warning = f"📅 Earnings in {days_until} days - Be cautious with puts"
                                
                                info = EarningsInfo(
                                    symbol=symbol,
                                    earnings_date=earnings_date,
                                    days_until=days_until,
                                    time_of_day='Unknown',
                                    has_upcoming=days_until >= 0,
                                    warning=warning
                                )
        except Exception as e:
            # Silently fail - we'll just not have earnings data
            pass
        
        # Cache result
        self._cache[symbol] = info
        self._cache_time[symbol] = datetime.now()
        
        return info
    
    def get_earnings_batch(self, symbols: List[str]) -> dict:
        """Get earnings info for multiple symbols"""
        results = {}
        for symbol in symbols:
            results[symbol] = self.get_earnings_date(symbol)
        return results
    
    def filter_safe_for_puts(self, symbols: List[str], 
                             min_days: int = 7) -> List[str]:
        """Return symbols that are safe to sell puts on (no imminent earnings)"""
        safe = []
        for symbol in symbols:
            info = self.get_earnings_date(symbol)
            if info.days_until is None or info.days_until > min_days:
                safe.append(symbol)
        return safe
    
    def get_warnings(self, symbols: List[str]) -> dict:
        """Get earnings warnings for symbols"""
        warnings = {}
        for symbol in symbols:
            info = self.get_earnings_date(symbol)
            if info.warning:
                warnings[symbol] = info.warning
        return warnings


# Known earnings dates (backup data, update periodically)
KNOWN_EARNINGS = {
    # Q1 2026 estimates (update as needed)
    'NVDA': '2026-02-26',
    'ORCL': '2026-03-10',
    'AMD': '2026-02-04',
    'TSLA': '2026-01-29',
    'AMZN': '2026-02-06',
    'MSFT': '2026-01-28',
    'INTC': '2026-01-30',
    'VST': '2026-02-27',
    'CEG': '2026-02-13',
    'UNH': '2026-01-15',
    'RTX': '2026-01-28',
}

class EarningsCalendarWithBackup(EarningsCalendar):
    """Earnings calendar with backup hardcoded dates"""
    
    def get_earnings_date(self, symbol: str) -> EarningsInfo:
        # Try API first
        info = super().get_earnings_date(symbol)
        
        # If no date found, check backup
        if info.earnings_date is None and symbol in KNOWN_EARNINGS:
            try:
                earnings_date = datetime.strptime(KNOWN_EARNINGS[symbol], '%Y-%m-%d')
                days_until = (earnings_date - datetime.now()).days
                
                warning = ''
                if 0 <= days_until <= 7:
                    warning = f"⚠️ EARNINGS IN {days_until} DAYS - Avoid selling puts!"
                elif 7 < days_until <= 14:
                    warning = f"📅 Earnings in {days_until} days - Be cautious with puts"
                
                info = EarningsInfo(
                    symbol=symbol,
                    earnings_date=earnings_date,
                    days_until=days_until,
                    time_of_day='Unknown',
                    has_upcoming=days_until >= 0,
                    warning=warning
                )
            except:
                pass
        
        return info


# Test
if __name__ == '__main__':
    calendar = EarningsCalendarWithBackup()
    
    symbols = ['NVDA', 'ORCL', 'AMD', 'TSLA', 'VST', 'CEG', 'UNH']
    
    print("="*60)
    print("📅 EARNINGS CALENDAR")
    print("="*60)
    
    for symbol in symbols:
        info = calendar.get_earnings_date(symbol)
        
        if info.earnings_date:
            date_str = info.earnings_date.strftime('%Y-%m-%d')
            print(f"\n{symbol}: {date_str} ({info.days_until} days)")
            if info.warning:
                print(f"   {info.warning}")
        else:
            print(f"\n{symbol}: No earnings date found")
    
    print("\n" + "="*60)
    print("Safe symbols for puts (>7 days until earnings):")
    safe = calendar.filter_safe_for_puts(symbols)
    print(f"   {', '.join(safe)}")
