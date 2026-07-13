#!/usr/bin/env python3
"""
token_keepalive.py — Local Schwab token maintenance (Mac).
Replaces the dead OCI token-sync. Run on a launchd schedule every ~25 min
during market hours.

What it does:
  1. Refreshes the access token so intraday jobs never fail on expiry.
  2. Tracks the ORIGINAL OAuth time to predict the hard 7-day refresh-token
     expiry (Schwab does NOT rotate the refresh token on refresh, so the
     7-day clock is fixed from last full re-auth).
  3. Writes a re-auth warning flag when the refresh token is within 24h of
     expiry, so re-OAuth becomes a scheduled task, not a mid-session wall.

Exit codes: 0 ok, 2 refresh failed (refresh token likely dead -> re-auth).
"""
import json, os, sys, base64
from pathlib import Path
from datetime import datetime, timedelta
import httpx

APP_KEY = os.environ.get("SCHWAB_CLIENT_ID", "")
APP_SECRET = os.environ.get("SCHWAB_CLIENT_SECRET", "")
HOME = Path.home()
TOKEN_PATH = HOME / ".schwab" / "tokens.json"
WARN_PATH  = HOME / ".schwab" / "REAUTH_NEEDED.flag"
LOG_PATH   = HOME / ".schwab" / "logs" / "token_keepalive.log"
REFRESH_TTL_DAYS = 7
WARN_WINDOW_HOURS = 24

def log(msg):
    ts = datetime.now().isoformat(timespec='seconds')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, 'a') as f:
            f.write(line + "\n")
    except Exception:
        pass

def load():
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH) as f:
            return json.load(f)
    return {}

def save(t):
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, 'w') as f:
        json.dump(t, f, indent=2)

def do_refresh(tokens):
    rt = tokens.get('refresh_token')
    if not rt:
        log("ERROR no refresh_token on disk -> full re-auth required")
        return False
    auth = base64.b64encode(f"{APP_KEY}:{APP_SECRET}".encode()).decode()
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post('https://api.schwabapi.com/v1/oauth/token',
                headers={'Authorization': f'Basic {auth}',
                         'Content-Type': 'application/x-www-form-urlencoded'},
                data={'grant_type': 'refresh_token', 'refresh_token': rt})
    except Exception as e:
        log(f"ERROR refresh request failed: {e}")
        return False
    if r.status_code != 200:
        log(f"ERROR refresh HTTP {r.status_code}: {r.text[:160]}")
        return False
    nt = r.json()
    tokens['access_token'] = nt['access_token']
    if 'refresh_token' in nt:
        # capture if Schwab ever DOES rotate it (resets 7d clock)
        if nt['refresh_token'] != rt:
            tokens['refresh_token'] = nt['refresh_token']
            tokens['refresh_obtained_at'] = datetime.now().isoformat()
            log("refresh_token ROTATED -> 7-day clock reset")
    tokens['expires_at'] = (datetime.now() + timedelta(seconds=nt.get('expires_in', 1800))).isoformat()
    tokens['saved_at'] = datetime.now().isoformat()
    save(tokens)
    return True

def refresh_clock(tokens):
    """Return (hours_remaining_on_refresh_token, anchor_iso) or (None, None)."""
    anchor = tokens.get('refresh_obtained_at') or tokens.get('saved_at')
    if not anchor:
        return None, None
    try:
        a = datetime.fromisoformat(anchor)
    except Exception:
        return None, None
    expiry = a + timedelta(days=REFRESH_TTL_DAYS)
    return (expiry - datetime.now()).total_seconds() / 3600.0, anchor

def main():
    tokens = load()
    ok = do_refresh(tokens)
    if not ok:
        # refresh failed -> almost certainly the 7-day refresh token died
        try:
            WARN_PATH.write_text(f"REAUTH NEEDED (refresh failed) {datetime.now().isoformat()}\n")
        except Exception:
            pass
        log("FAIL: refresh failed; wrote REAUTH_NEEDED flag")
        sys.exit(2)

    tokens = load()
    hrs, anchor = refresh_clock(tokens)
    if hrs is not None:
        if hrs <= WARN_WINDOW_HOURS:
            WARN_PATH.write_text(
                f"REAUTH SOON: refresh token expires ~{hrs:.1f}h "
                f"(anchor {anchor}); re-run OAuth.\n")
            log(f"OK refreshed. WARNING refresh token ~{hrs:.1f}h left -> re-auth soon")
        else:
            if WARN_PATH.exists():
                WARN_PATH.unlink()
            log(f"OK refreshed. refresh token ~{hrs/24:.1f}d left")
    else:
        log("OK refreshed. (no anchor timestamp; cannot predict 7d expiry)")
    sys.exit(0)

if __name__ == '__main__':
    main()
