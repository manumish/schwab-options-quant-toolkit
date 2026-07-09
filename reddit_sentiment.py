#!/usr/bin/env python3
"""
Reddit Sentiment Scraper — runs on Mac (residential IP), writes local scanner.db.
Uses Reddit's public JSON feed when available; exits nonzero if Reddit blocks all fetches.
"""

import json, logging, re, sys, time, uuid
from collections import defaultdict
from datetime import datetime, timezone
from urllib import request

from sentiment_store import persist_sentiment_signals

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reddit_sentiment")

# ── Config ─────────────────────────────────────────────────────────────────
SUBREDDITS = ["wallstreetbets", "options", "stocks"]
POST_LIMIT = 50
RATE_LIMIT = 1.5  # seconds between requests
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SESSION = cffi_requests.Session(impersonate="chrome120") if cffi_requests else None

# ── Watchlist ──────────────────────────────────────────────────────────────
WATCHLIST = {
    "NVDA","AMD","AAPL","MSFT","GOOGL","META","TSLA","AVGO","CRM","ORCL",
    "MU","INTC","QCOM","NFLX","AMZN","CRDO","SMCI","MRVL","ARM","PLTR",
    "JPM","BAC","GS","MS","WFC","SCHW","C","BLK","AXP","COF","V","MA",
    "UNH","JNJ","LLY","ABBV","MRK","PFE","AMGN","GILD","BMY","CVS",
    "XOM","CVX","COP","SLB","OXY","EOG","MPC","PSX","VLO","HAL",
    "LMT","RTX","NOC","GD","BA","LHX","HII","GE",
    "COST","WMT","HD","NKE","MCD","SBUX","TGT","PG","KO","PEP","DIS","UBER",
    "CAT","DE","HON","UNP","MMM","EMR","ITW","GE",
    "O","AMT","PLD","SPG","EQIX",
    "SPY","QQQ","IWM","DIA","XLF","XLE","XLV","GLD",
    "MSTR","GME","AMC","RIVN","LCID","CEG","VST","NNE","OKLO","SMR",
}

COMMON_WORDS = {
    "I","A","AM","AN","AS","AT","BE","BY","DO","GO","HE","IF","IN","IS",
    "IT","ME","MY","NO","OF","ON","OR","SO","TO","UP","US","WE","CEO",
    "DD","EPS","ATH","IPO","SEC","FDA","GDP","CPI","FOMC","FED","YOLO",
    "FOMO","HODL","TLDR","TA","IV","OI","DTE","ITM","OTM","ATM","PT",
    "ER","PM","AH","EOD","ALL","FOR","THE","AND","NOT","BUT","ARE",
    "CAN","HAS","HAD","DID","BUY","PUT","CALL","SELL","LONG","SHORT",
    "BULL","BEAR","GAIN","LOSS","MOON","PUMP","DUMP","DIP","RIP","PLAY",
    "LEAP","CASH","DEBT","RISK","HOLD","MOVE","DROP","HIGH","LOW","FLAT",
    "OPEN","NEXT","JUST","LIKE","VERY","GOOD","FAST","SAFE","HUGE","NEED",
    "FREE","MAX","MIN","ANY","BIG","OLD","NEW","TOP","NOW","DAY","OUT",
    "WAY","HOW","WHO","WHY","RUN","LOT","OWN","SET","FAR","TRY","END",
    "TWO","ONE","HIT","GDP","IMO","RH","LOL","WTF","FYI","PSA","TIL",
}

TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b|(?<!\w)([A-Z]{2,5})(?!\w)")


# ── Scraper ────────────────────────────────────────────────────────────────
def scrape_subreddit(sub: str, sort: str = "hot") -> list[dict]:
    url = f"https://old.reddit.com/r/{sub}/{sort}.json?limit={POST_LIMIT}&raw_json=1"
    try:
        time.sleep(RATE_LIMIT)
        headers = {"User-Agent": USER_AGENT}
        if SESSION:
            r = SESSION.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
        else:
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning(f"Failed r/{sub}/{sort}: {e}")
        return []
    posts = []
    for child in data.get("data", {}).get("children", []):
        p = child.get("data", {})
        posts.append({
            "id": p.get("id", ""),
            "subreddit": sub,
            "title": p.get("title", ""),
            "selftext": (p.get("selftext", "") or "")[:2000],
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "created_utc": p.get("created_utc", 0),
            "upvote_ratio": p.get("upvote_ratio", 0.5),
            "flair": p.get("link_flair_text", ""),
        })
    log.info(f"r/{sub}/{sort}: {len(posts)} posts")
    return posts

def extract_tickers(text: str) -> list[str]:
    found = set()
    for m in TICKER_RE.finditer(text):
        sym = m.group(1) or m.group(2)
        if sym and sym in WATCHLIST and sym not in COMMON_WORDS:
            found.add(sym)
    return sorted(found)

BULLISH = {"buy","call","long","moon","rocket","bull","calls","leap","yolo",
           "squeeze","breakout","undervalued","upside","rip","pump","gain",
           "tendies","lambo","print","brrrr","diamond","hands","dip"}
BEARISH = {"sell","put","short","bear","crash","dump","overvalued","puts",
           "downside","tank","drop","red","bag","loss","drill","fade",
           "dead","rekt","rug","scam","fraud","bubble"}

def analyze_sentiment(text: str) -> tuple[float, str]:
    words = set(text.lower().split())
    b = len(words & BULLISH)
    s = len(words & BEARISH)
    total = b + s
    if total == 0:
        return 0.0, "neutral"
    score = (b - s) / total
    label = "bullish" if score > 0.2 else ("bearish" if score < -0.2 else "neutral")
    return round(score, 3), label

def aggregate(posts: list[dict]) -> dict:
    agg = defaultdict(lambda: {
        "mentions": 0,
        "scores": [],
        "labels": [],
        "upvotes": 0,
        "top_post": "",
        "top_weight": -1,
    })
    for p in posts:
        text = p["title"] + " " + p["selftext"]
        tickers = extract_tickers(text)
        if not tickers:
            continue
        score, label = analyze_sentiment(text)
        for sym in tickers:
            a = agg[sym]
            a["mentions"] += 1
            a["scores"].append(score)
            a["labels"].append(label)
            a["upvotes"] += p["score"]
            weight = (p.get("score") or 0) + (p.get("num_comments") or 0)
            if weight > a["top_weight"]:
                a["top_weight"] = weight
                title = (p.get("title") or "").strip().replace("\n", " ")
                a["top_post"] = f"r/{p.get('subreddit', '?')}: {title}"[:500]

    result = {}
    for sym, d in agg.items():
        n = d["mentions"]
        avg = sum(d["scores"]) / n if n else 0
        bull_count = d["labels"].count("bullish")
        bear_count = d["labels"].count("bearish")
        bull_pct = bull_count / n * 100 if n else 0
        bear_pct = bear_count / n * 100 if n else 0
        euphoria = bull_pct
        panic = bear_pct
        contrarian = ""
        if euphoria > 80:
            contrarian = "SELL_PREMIUM"
        elif panic > 80:
            contrarian = "BUY_DIP"
        result[sym] = {
            "sentiment_score": round(avg, 4),
            "mention_count": n,
            "bullish": bull_count,
            "bearish": bear_count,
            "bullish_pct": round(bull_pct, 1),
            "bearish_pct": round(bear_pct, 1),
            "euphoria_score": round(euphoria, 1),
            "panic_score": round(panic, 1),
            "contrarian_signal": contrarian,
            "total_upvotes": d["upvotes"],
            "top_post": d["top_post"],
        }
    return result


# ── Local scanner.db sink ─────────────────────────────────────────────────
def write_to_db(agg: dict, scan_id: str) -> int:
    scan_ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for sym, d in agg.items():
        rows.append({
            "scan_ts": scan_ts,
            "symbol": sym,
            "source": "reddit",
            "mentions": d.get("mention_count", 0),
            "bullish": d.get("bullish", 0),
            "bearish": d.get("bearish", 0),
            "net_score": d.get("sentiment_score", 0),
            "top_post": d.get("top_post", ""),
        })
    inserted = persist_sentiment_signals(rows)
    log.info(f"Wrote {inserted} sentiment rows to scanner.db (scan: {scan_id})")
    return inserted


# ── Main ───────────────────────────────────────────────────────────────────
def run():
    scan_id = f"sent_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"=== Reddit Sentiment Scan {scan_id} ===")

    # Scrape all subreddits
    all_posts = []
    for sub in SUBREDDITS:
        all_posts.extend(scrape_subreddit(sub, "hot"))
        all_posts.extend(scrape_subreddit(sub, "new"))

    # Deduplicate
    seen = set()
    unique = [p for p in all_posts if p["id"] not in seen and not seen.add(p["id"])]
    log.info(f"Total unique posts: {len(unique)}")

    if not unique:
        log.warning("No posts fetched — exiting")
        return 1

    # Aggregate
    agg = aggregate(unique)
    log.info(f"Tickers found: {len(agg)}")

    # Top movers
    for sym, d in sorted(agg.items(), key=lambda x: -x[1]["mention_count"])[:15]:
        sig = f" *** {d['contrarian_signal']}" if d["contrarian_signal"] else ""
        log.info(f"  {sym:>6}: mentions={d['mention_count']:>3} sent={d['sentiment_score']:+.3f} "
                 f"bull={d['bullish_pct']:.0f}% bear={d['bearish_pct']:.0f}%{sig}")

    write_to_db(agg, scan_id)
    return 0

if __name__ == "__main__":
    sys.exit(run())
