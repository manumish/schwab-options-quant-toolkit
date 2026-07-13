"""
Earnings calendar module.

Primary source: Finnhub earnings calendar.
Fallback / cross-check: Alpha Vantage EARNINGS_CALENDAR CSV.

The live TIP scanner treats scanner.db earnings_dates as authoritative. This
module refreshes that table when vendor API keys are available, and otherwise
fails closed by preserving existing rows.
"""

import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
USER_AGENT = "TIP-earnings-refresh/1.0"
VAULT_SCRIPT = os.path.expanduser("~/.codex/skills/shared-brain/brain.py")
FINNHUB_VAULT_KEY = "TIP_FINNHUB_API_KEY"
ALPHAVANTAGE_VAULT_KEY = "TIP_ALPHAVANTAGE_API_KEY"
FINNHUB_MAX_ROWS = 1500
FINNHUB_CHUNK_DAYS = 7
ALPHAVANTAGE_REFRESH_DAYS = 7


@dataclass
class EarningsInfo:
    """Earnings information for a symbol."""

    symbol: str
    earnings_date: Optional[datetime]
    days_until: Optional[int]
    time_of_day: str
    has_upcoming: bool
    warning: str
    source: str = "unknown"
    confidence: str = "UNKNOWN"


def _today() -> date:
    return date.today()


def _env_key(*names: str) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def vault_get_value(key_name: str, logger=None) -> str:
    """Fetch a secret using the same local Keychain-backed vault pattern as sentiment."""
    try:
        result = subprocess.run(
            [sys.executable, VAULT_SCRIPT, "vault-get", key_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            if logger:
                logger(f"WARN vault lookup failed for {key_name}: exit {result.returncode}")
            return ""
        payload = json.loads(result.stdout)
        if not payload.get("found"):
            return ""
        return str(payload.get("value") or "").strip()
    except Exception as exc:
        if logger:
            logger(f"WARN vault lookup failed for {key_name}: {type(exc).__name__}")
        return ""


def _get_json(url: str, params: Dict[str, str], timeout: int = 20) -> dict:
    qs = urlencode(params)
    req = Request(f"{url}?{qs}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        # Do not let urllib exception strings leak query-string API tokens.
        raise RuntimeError(f"vendor JSON request failed ({type(exc).__name__})")


def _get_text(url: str, params: Dict[str, str], timeout: int = 20) -> str:
    qs = urlencode(params)
    req = Request(f"{url}?{qs}", headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"vendor CSV request failed ({type(exc).__name__})")


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    value = value[:10]
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _timing_from_hour(hour: Optional[str]) -> str:
    if not hour:
        return "Unknown"
    h = str(hour).strip().lower()
    if h in ("bmo", "before market open", "amc", "after market close"):
        return "BMO" if h.startswith("b") else "AMC"
    if "before" in h:
        return "BMO"
    if "after" in h:
        return "AMC"
    return "Unknown"


def _fetch_finnhub_rows(start: date, end: date, token: str) -> List[dict]:
    data = _get_json(
        FINNHUB_URL,
        {"from": start.isoformat(), "to": end.isoformat(), "token": token},
    )
    rows = data.get("earningsCalendar") or []
    if not isinstance(rows, list):
        raise RuntimeError("Finnhub response missing earningsCalendar list")
    return rows


def _fetch_finnhub_safe_range(start: date, end: date, token: str, logger=None) -> List[dict]:
    """Pull one range and recursively bisect any response at Finnhub's hard cap."""
    rows = _fetch_finnhub_rows(start, end, token)
    if len(rows) < FINNHUB_MAX_ROWS:
        return rows
    if logger:
        logger(
            f"WARN Finnhub returned {len(rows)} rows for {start}..{end}; "
            "bisecting to avoid silent truncation"
        )
    if start >= end:
        raise RuntimeError(
            f"Finnhub single-day range {start} hit {FINNHUB_MAX_ROWS}-row cap"
        )
    midpoint = start + timedelta(days=(end - start).days // 2)
    left = _fetch_finnhub_safe_range(start, midpoint, token, logger)
    right = _fetch_finnhub_safe_range(midpoint + timedelta(days=1), end, token, logger)
    return left + right


def fetch_finnhub_earnings(
    start: date,
    end: date,
    token: Optional[str] = None,
    logger=None,
) -> Dict[str, dict]:
    """Fetch Finnhub in inclusive seven-day chunks, bisecting capped responses."""
    token = token or _env_key("FINNHUB_API_KEY", "FINNHUB_TOKEN")
    if not token:
        return {}
    if end < start:
        raise ValueError("end date precedes start date")

    rows: List[dict] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(days=FINNHUB_CHUNK_DAYS - 1))
        chunk_rows = _fetch_finnhub_safe_range(chunk_start, chunk_end, token, logger)
        # The recursive helper guarantees this for every leaf request.
        if len(chunk_rows) >= FINNHUB_MAX_ROWS and chunk_start == chunk_end:
            raise AssertionError("unresolved Finnhub truncation")
        rows.extend(chunk_rows)
        chunk_start = chunk_end + timedelta(days=1)

    out: Dict[str, dict] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper().strip()
        d = _parse_date(row.get("date") or "")
        if not sym or not d:
            continue
        prev = out.get(sym)
        if prev and prev["next_date"] <= d:
            continue
        out[sym] = {
            "symbol": sym,
            "next_date": d,
            "confirmed": 1,
            "timing": _timing_from_hour(row.get("hour")),
            "source": "finnhub",
            "confidence": "MEDIUM",
        }
    return out


def fetch_alpha_vantage_earnings(token: Optional[str] = None, horizon: str = "3month") -> Dict[str, dict]:
    """Fetch Alpha Vantage earnings calendar CSV."""
    token = token or _env_key("ALPHAVANTAGE_API_KEY", "ALPHA_VANTAGE_API_KEY")
    if not token:
        return {}

    text = _get_text(
        ALPHAVANTAGE_URL,
        {"function": "EARNINGS_CALENDAR", "horizon": horizon, "apikey": token},
    )
    out: Dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        sym = str(row.get("symbol") or "").upper().strip()
        d = _parse_date(row.get("reportDate") or "")
        if not sym or not d:
            continue
        prev = out.get(sym)
        if prev and prev["next_date"] <= d:
            continue
        out[sym] = {
            "symbol": sym,
            "next_date": d,
            "confirmed": 0,
            "timing": "Unknown",
            "source": "alphavantage",
            "confidence": "MEDIUM",
        }
    return out


def _ensure_earnings_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_dates (
            symbol TEXT PRIMARY KEY,
            next_date TEXT,
            confirmed INTEGER,
            timing TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(earnings_dates)").fetchall()}
    added_source = False
    if "source" not in cols:
        conn.execute("ALTER TABLE earnings_dates ADD COLUMN source TEXT")
        added_source = True
    if "confidence" not in cols:
        conn.execute("ALTER TABLE earnings_dates ADD COLUMN confidence TEXT")
    # Existing rows predate the vendor confidence contract.
    if added_source:
        conn.execute("UPDATE earnings_dates SET source='legacy' WHERE source IS NULL OR source=''")
    conn.execute(
        "UPDATE earnings_dates SET source='legacy' WHERE source IS NULL OR source=''"
    )
    conn.execute(
        "UPDATE earnings_dates SET confidence='LOW' WHERE confidence IS NULL OR confidence=''"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_refresh_meta (
            source TEXT PRIMARY KEY,
            last_attempt_at TEXT,
            last_success_at TEXT,
            status TEXT,
            message TEXT
        )
        """
    )


def _record_refresh_meta(conn, source: str, success: bool, message: str) -> None:
    now = datetime.now().isoformat()
    previous = conn.execute(
        "SELECT last_success_at FROM earnings_refresh_meta WHERE source=?", (source,)
    ).fetchone()
    last_success = now if success else (previous[0] if previous else None)
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_refresh_meta
            (source, last_attempt_at, last_success_at, status, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source, now, last_success, "OK" if success else "FAILED", message[:500]),
    )


def _alpha_refresh_due(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT last_success_at FROM earnings_refresh_meta WHERE source='alphavantage'"
    ).fetchone()
    if not row or not row[0]:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(row[0]) >= timedelta(
            days=ALPHAVANTAGE_REFRESH_DAYS
        )
    except Exception:
        return True


def _downgrade_existing(conn: sqlite3.Connection, symbols: Iterable[str]) -> int:
    symbols = sorted({s.upper() for s in symbols if s})
    if not symbols:
        return 0
    placeholders = ",".join("?" for _ in symbols)
    cur = conn.execute(
        f"UPDATE earnings_dates SET confidence='LOW' WHERE symbol IN ({placeholders})",
        symbols,
    )
    return int(cur.rowcount or 0)


def merge_earnings_sources(
    finnhub: Dict[str, dict],
    alpha_vantage: Dict[str, dict],
    symbols: Optional[Iterable[str]] = None,
) -> Dict[str, dict]:
    """Prefer Finnhub, use Alpha Vantage fallback, and mark agreement confidence."""
    wanted = {s.upper() for s in symbols or [] if s}
    all_symbols = set(finnhub) | set(alpha_vantage)
    if wanted:
        all_symbols &= wanted

    merged: Dict[str, dict] = {}
    for sym in sorted(all_symbols):
        f = finnhub.get(sym)
        a = alpha_vantage.get(sym)
        row = dict(f or a)
        if f and a:
            if f["next_date"] == a["next_date"]:
                row["source"] = "finnhub+alphavantage"
                row["confirmed"] = max(int(f["confirmed"]), int(a["confirmed"]))
                row["confidence"] = "HIGH"
            else:
                row["source"] = "finnhub;alphavantage_disagrees"
                row["confidence"] = "MEDIUM"
        elif row:
            row["confidence"] = "MEDIUM"
        if row:
            merged[sym] = row
    return merged


def refresh_earnings_dates(
    symbols: Iterable[str],
    db_path: str,
    days_forward: int = 90,
    logger=None,
) -> dict:
    """
    Refresh scanner.db earnings_dates for the supplied symbols.

    Returns a small status dict. Missing vendor credentials are not an error;
    the caller should continue using the existing local table.
    """
    symbols = sorted({s.upper() for s in symbols if s})
    start = _today()
    end = start + timedelta(days=days_forward)
    status = {
        "updated": 0,
        "sources": [],
        "skipped": False,
        "message": "",
        "finnhub_failed": False,
        "downgraded": 0,
    }

    conn = sqlite3.connect(db_path)
    try:
        _ensure_earnings_schema(conn)
        conn.commit()
        alpha_due = _alpha_refresh_due(conn)
    finally:
        conn.close()

    finnhub = {}
    alpha = {}
    finnhub_token = vault_get_value(FINNHUB_VAULT_KEY, logger=logger)
    alpha_token = vault_get_value(ALPHAVANTAGE_VAULT_KEY, logger=logger)

    finnhub_error = ""
    try:
        if not finnhub_token:
            raise RuntimeError(f"vault key {FINNHUB_VAULT_KEY} is absent")
        finnhub = fetch_finnhub_earnings(start, end, token=finnhub_token, logger=logger)
        if finnhub:
            status["sources"].append("finnhub")
        else:
            raise RuntimeError("Finnhub returned no usable earnings rows")
    except Exception as e:
        finnhub_error = str(e)
        status["finnhub_failed"] = True
        if logger:
            logger(f"ERROR EARNINGS DATA FAILURE — preserving last-known dates: {e}")

    if not alpha_token:
        if logger:
            logger(f"INFO Alpha Vantage cross-check skipped: vault key {ALPHAVANTAGE_VAULT_KEY} absent")
    elif not alpha_due:
        if logger:
            logger("INFO Alpha Vantage cross-check skipped: weekly refresh not due")
    else:
        try:
            alpha = fetch_alpha_vantage_earnings(token=alpha_token)
            if alpha:
                status["sources"].append("alphavantage")
            else:
                raise RuntimeError("Alpha Vantage returned no usable earnings rows")
        except Exception as e:
            if logger:
                logger(f"ERROR Alpha Vantage earnings cross-check failed: {e}")

    # Finnhub is authoritative. Never silently replace last-known data with a
    # fallback when the primary pull fails.
    if not finnhub:
        conn = sqlite3.connect(db_path)
        try:
            _ensure_earnings_schema(conn)
            status["downgraded"] = _downgrade_existing(conn, symbols)
            _record_refresh_meta(conn, "finnhub", False, finnhub_error or "no data")
            if alpha_token and alpha_due:
                _record_refresh_meta(conn, "alphavantage", bool(alpha), "cross-check only")
            conn.commit()
        finally:
            conn.close()
        status["skipped"] = True
        status["message"] = (
            f"Finnhub failed; preserved dates and downgraded {status['downgraded']} rows"
        )
        return status

    merged = merge_earnings_sources(finnhub, alpha, symbols)
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_earnings_schema(conn)
        for row in merged.values():
            conn.execute(
                """
                INSERT OR REPLACE INTO earnings_dates
                    (symbol, next_date, confirmed, timing, source, updated_at, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["symbol"],
                    row["next_date"].isoformat(),
                    int(row.get("confirmed") or 0),
                    row.get("timing") or "Unknown",
                    row.get("source") or "unknown",
                    now,
                    row.get("confidence") or "MEDIUM",
                ),
            )
        _record_refresh_meta(conn, "finnhub", True, f"updated {len(merged)} symbols")
        if alpha_token and alpha_due:
            _record_refresh_meta(
                conn,
                "alphavantage",
                bool(alpha),
                f"cross-checked {len(alpha)} symbols" if alpha else "no usable data",
            )
        conn.commit()
    finally:
        conn.close()

    status["updated"] = len(merged)
    status["message"] = f"updated {len(merged)} earnings rows"
    return status


class EarningsCalendar:
    """SQLite-backed earnings lookup with optional vendor refresh."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.expanduser("~/.schwab/scanner.db")
        self._cache: Dict[str, EarningsInfo] = {}
        self._cache_time: Dict[str, datetime] = {}
        self._cache_duration = timedelta(hours=6)

    def get_earnings_date(self, symbol: str) -> EarningsInfo:
        symbol = symbol.upper()
        if symbol in self._cache:
            cache_age = datetime.now() - self._cache_time.get(symbol, datetime.min)
            if cache_age < self._cache_duration:
                return self._cache[symbol]

        info = self._from_db(symbol)
        self._cache[symbol] = info
        self._cache_time[symbol] = datetime.now()
        return info

    def _from_db(self, symbol: str) -> EarningsInfo:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM earnings_dates WHERE symbol=?", (symbol,)).fetchone()
            conn.close()
        except sqlite3.Error:
            row = None

        if not row:
            return EarningsInfo(symbol, None, None, "Unknown", False, "", "none", "UNKNOWN")

        d = _parse_date(row["next_date"] or "")
        if d is None:
            return EarningsInfo(symbol, None, None, "Unknown", False, "", row["source"], "UNKNOWN")

        while d < _today():
            d = d + timedelta(days=91)
        days_until = (d - _today()).days
        warning = ""
        if 0 <= days_until <= 7:
            warning = f"EARNINGS IN {days_until} DAYS - avoid short premium"
        elif 7 < days_until <= 14:
            warning = f"Earnings in {days_until} days - size carefully"
        confidence = row["confidence"] if "confidence" in row.keys() else "MEDIUM"
        return EarningsInfo(
            symbol=symbol,
            earnings_date=datetime.combine(d, datetime.min.time()),
            days_until=days_until,
            time_of_day=row["timing"] or "Unknown",
            has_upcoming=days_until >= 0,
            warning=warning,
            source=row["source"] or "unknown",
            confidence=confidence or "MEDIUM",
        )

    def get_earnings_batch(self, symbols: List[str]) -> dict:
        return {symbol: self.get_earnings_date(symbol) for symbol in symbols}

    def filter_safe_for_puts(self, symbols: List[str], min_days: int = 7) -> List[str]:
        safe = []
        for symbol in symbols:
            info = self.get_earnings_date(symbol)
            if info.days_until is None or info.days_until > min_days:
                safe.append(symbol)
        return safe

    def get_warnings(self, symbols: List[str]) -> dict:
        warnings = {}
        for symbol in symbols:
            info = self.get_earnings_date(symbol)
            if info.warning:
                warnings[symbol] = info.warning
        return warnings


class EarningsCalendarWithBackup(EarningsCalendar):
    """Backwards-compatible name; data now comes from scanner.db."""


if __name__ == "__main__":
    symbols = ["NVDA", "ORCL", "AMD", "TSLA", "VST", "CEG", "UNH"]
    db = os.path.expanduser("~/.schwab/scanner.db")
    status = refresh_earnings_dates(symbols, db)
    print(status)
    calendar = EarningsCalendarWithBackup(db)
    for symbol in symbols:
        info = calendar.get_earnings_date(symbol)
        if info.earnings_date:
            print(f"{symbol}: {info.earnings_date:%Y-%m-%d} ({info.days_until}d) {info.source} {info.confidence}")
        else:
            print(f"{symbol}: no earnings date found")
