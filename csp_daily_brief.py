#!/usr/bin/env python3
"""
csp_daily_brief.py — Local daily CSP opportunity brief.

Reads ~/.schwab/scanner.db only, writes a Markdown brief under ~/.schwab/reports,
and can raise a local macOS notification. It does not place or stage orders.
"""
import argparse
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DB = HOME / ".schwab" / "scanner.db"
REPORTS = HOME / "Documents" / "Trading" / "reports"
SCHWAB_DIR = HOME / ".schwab"
NOTIFIER_APP = SCHWAB_DIR / "TIPCSPBriefNotifier.app"
LAST_BRIEF = SCHWAB_DIR / "last_csp_brief_path"
NOTIFIER_TITLE = SCHWAB_DIR / "last_csp_brief_title"
NOTIFIER_BODY = SCHWAB_DIR / "last_csp_brief_body"
NOTIFIER_MODE = SCHWAB_DIR / "tip_csp_notifier_mode"
NOTIFIER_VERSION = SCHWAB_DIR / "tip_csp_notifier_version"


def rows(sql, args=()):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def latest_date():
    r = rows("SELECT MAX(scan_date) AS d FROM daily_plan WHERE kind='CSP'")
    return r[0]["d"] if r and r[0]["d"] else None


def get_run(scan_date):
    r = rows(
        "SELECT * FROM scan_runs WHERE scan_date=? ORDER BY finished_at DESC LIMIT 1",
        (scan_date,),
    )
    return r[0] if r else {}


def get_csp(scan_date):
    return rows(
        """SELECT * FROM daily_plan
           WHERE scan_date=? AND kind='CSP'
           ORDER BY CASE earn_state WHEN 'CLEAR' THEN 0 WHEN 'NEAR' THEN 1 ELSE 2 END,
                    ann_yield DESC, premium DESC""",
        (scan_date,),
    )


def get_sentiment():
    try:
        return {
            r["symbol"]: r
            for r in rows(
                """SELECT symbol, mentions, bullish_pct, bearish_pct, net_score, sources
                   FROM v_sentiment_daily"""
            )
        }
    except sqlite3.OperationalError:
        return {}


def get_csp_opportunities(scan_date):
    try:
        return rows(
            """SELECT * FROM tip_opportunities
               WHERE scan_date=? AND strategy='CSP'
               ORDER BY CASE label
                    WHEN 'ACTIONABLE' THEN 0 WHEN 'CONDITIONAL' THEN 1
                    WHEN 'WATCH' THEN 2 ELSE 3 END,
                    score DESC""",
            (scan_date,),
        )
    except sqlite3.OperationalError:
        return []


def money(v):
    if v is None:
        return "-"
    return f"${round(float(v)):,.0f}"


def pct(v):
    if v is None:
        return "-"
    return f"{float(v):.0f}%"


def score(v):
    if v is None:
        return "-"
    n = float(v)
    return f"{n:+.2f}"


def sentiment_note(row, sentiment):
    s = sentiment.get(row["sym"])
    if not s:
        return "sent -"
    note = f"sent {score(s['net_score'])}, {int(s['mentions'])}m"
    if (
        row.get("earn_state") == "CLEAR"
        and float(s.get("bullish_pct") or 0) >= 80
        and int(s.get("mentions") or 0) >= 5
    ):
        note += ", CONTRA"
    return note


def line(row, sentiment):
    return (
        f"- {row['sym']} {row['exp']} {int(row['dte'])}d "
        f"K{float(row['strike']):.0f} {pct(row['otm_pct'])} OTM "
        f"d{abs(float(row['delta'] or 0)):.2f} iv{pct(row['iv'])} "
        f"prem {money(row['premium'])} BP {money(row['bp_reduction'])} "
        f"yld {pct(row['ann_yield'])} [{row['earn_state']}] "
        f"{sentiment_note(row, sentiment)}"
    )


def diversified(rows, limit, max_per_symbol=1):
    if limit <= 0:
        return []

    counts = {}
    picked = []
    max_per_symbol = max(1, int(max_per_symbol or 1))

    for row in rows:
        sym = row["sym"]
        if counts.get(sym, 0) >= max_per_symbol:
            continue
        picked.append(row)
        counts[sym] = counts.get(sym, 0) + 1
        if len(picked) >= limit:
            break

    return picked


def diversified_opportunities(rows, limit, max_per_symbol=1):
    counts = {}
    picked = []
    for row in rows:
        sym = row["symbol"]
        if counts.get(sym, 0) >= max_per_symbol:
            continue
        picked.append(row)
        counts[sym] = counts.get(sym, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def unique_symbol_count(rows):
    return len({r["sym"] for r in rows})


def build_brief(scan_date=None, limit=8, max_per_symbol=1):
    scan_date = scan_date or latest_date()
    if not scan_date:
        raise SystemExit("No CSP rows found in daily_plan.")

    run = get_run(scan_date)
    csp = get_csp(scan_date)
    sentiment = get_sentiment()
    opportunities = get_csp_opportunities(scan_date)

    clear = [r for r in csp if r["earn_state"] == "CLEAR"]
    near = [r for r in csp if r["earn_state"] == "NEAR"]
    block = [r for r in csp if r["earn_state"] == "BLOCK"]

    top = diversified(clear, limit, max_per_symbol=max_per_symbol)
    watch = diversified(near, 5, max_per_symbol=max_per_symbol)
    total_premium = sum(float(r["premium"] or 0) for r in top)
    total_bp = sum(float(r["bp_reduction"] or 0) for r in top)

    generated = datetime.now().isoformat(timespec="seconds")
    title = f"CSP Daily Brief - {scan_date}"
    lines = [
        f"# {title}",
        "",
        f"Generated {generated}",
        "",
        "Scope: read-only ranked ideas. The operator sizes and executes manually.",
        "",
        "## Account",
        "",
        (
            f"NLV {money(run.get('nlv'))} | Avail {money(run.get('avail_funds'))} | "
            f"MaintReq {money(run.get('maint_req'))} | MarginBal {money(run.get('margin_bal'))}"
        ),
        "",
        "## Alert",
        "",
        (
            f"{len(clear)} CLEAR CSP rows across {unique_symbol_count(clear)} symbols. "
            f"Diversified top {len(top)} total premium {money(total_premium)} "
            f"on {money(total_bp)} buying-power reduction."
        ),
        "",
    ]

    if opportunities:
        lines.extend(["## Underwritten CSP Ideas", ""])
        for r in diversified_opportunities(opportunities, limit, max_per_symbol=max_per_symbol):
            lines.extend(
                [
                    (
                        f"- {r['label']} {int(r['score']):02d} {r['symbol']} {r['structure']} "
                        f"{r['expiry']} {int(r['dte'] or 0)}d prem {money(r['premium'])} "
                        f"yld {pct(r['ann_yield'])}"
                    ),
                    f"  - Why scanner liked it: {r['raw_rank_reason']}",
                    f"  - Assignment: {r['assignment_view']}",
                    f"  - Failure point: {r['failure_point']}",
                    f"  - Better expression: {r['alternative']}",
                ]
            )
        lines.extend(["", f"## Raw Top CLEAR CSP Rows (max {max_per_symbol}/symbol)", ""])
    else:
        lines.extend([f"## Top CLEAR CSP Opportunities (max {max_per_symbol}/symbol)", ""])

    lines.extend(line(r, sentiment) for r in top)

    if watch:
        lines.extend(["", f"## NEAR Watchlist (max {max_per_symbol}/symbol)", ""])
        lines.extend(line(r, sentiment) for r in watch)

    if block:
        lines.extend(["", "## Blocked By Earnings", ""])
        lines.append(f"{len(block)} BLOCK candidates excluded by the earnings hard stop.")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- CLEAR names remain earnings-safe under the local earnings filter.",
            "- NEAR names may be IV-contaminated or close enough to earnings to treat carefully.",
            "- Scanner rank is a first-pass yield signal, not a recommendation; the underwritten section reconciles assignment quality, portfolio fit, and why the market is paying the premium.",
            "- The brief caps repeated strikes/expiries per symbol so the alert surfaces distinct underlyings first.",
            "- CONTRA means very bullish social chatter on a CLEAR name; useful as a sell-premium warning/edge, not an execution signal.",
            "",
        ]
    )

    return "\n".join(lines), top, scan_date


def ensure_notifier_app():
    """Build a tiny applet so the notification Show action opens the brief."""
    version = "2"
    if NOTIFIER_APP.exists() and NOTIFIER_VERSION.exists() and NOTIFIER_VERSION.read_text().strip() == version:
        return True

    SCHWAB_DIR.mkdir(parents=True, exist_ok=True)
    last_brief = str(LAST_BRIEF)
    note_title = str(NOTIFIER_TITLE)
    note_body = str(NOTIFIER_BODY)
    note_mode = str(NOTIFIER_MODE)
    source = f'''
on run
    set modeValue to my readFile("{note_mode}")
    if modeValue is "notify" then
        my writeFile("{note_mode}", "open")
        set noteTitle to my readFile("{note_title}")
        set noteBody to my readFile("{note_body}")
        display notification noteBody with title noteTitle
        delay 0.1
        quit
    else
        my openLatestBrief()
    end if
end run

on reopen
    my openLatestBrief()
end reopen

to readFile(posixPath)
    try
        return do shell script "cat " & quoted form of posixPath
    on error
        return ""
    end try
end readFile

to writeFile(posixPath, fileText)
    do shell script "printf %s " & quoted form of fileText & " > " & quoted form of posixPath
end writeFile

to openLatestBrief()
    try
        set briefPath to do shell script "cat " & quoted form of "{last_brief}"
        if briefPath is not "" then
            do shell script "open " & quoted form of briefPath
        end if
    end try
    delay 0.1
    quit
end openLatestBrief
'''.strip()

    result = subprocess.run(
        ["/usr/bin/osacompile", "-o", str(NOTIFIER_APP), "-e", source],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    ok = result.returncode == 0 and NOTIFIER_APP.exists()
    if ok:
        NOTIFIER_VERSION.write_text(version)
    return ok


def notify(top, path):
    if not top:
        title = "TIP CSP Brief"
        body = "No CLEAR CSP opportunities found."
    else:
        names = ", ".join(dict.fromkeys(r["sym"] for r in top[:3]))
        title = "TIP CSP Opportunities"
        body = f"Top CLEAR CSPs: {names}. Brief: {path.name}"

    if ensure_notifier_app():
        LAST_BRIEF.write_text(str(path))
        NOTIFIER_TITLE.write_text(title)
        NOTIFIER_BODY.write_text(body)
        NOTIFIER_MODE.write_text("notify")
        subprocess.run(
            [
                "/usr/bin/open",
                "-gj",
                str(NOTIFIER_APP),
            ],
            check=False,
        )
        return

    script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Scan date to brief, defaults to latest CSP scan date")
    ap.add_argument("--limit", type=int, default=12, help="Top CLEAR CSP rows to include")
    ap.add_argument(
        "--max-per-symbol",
        type=int,
        default=1,
        help="Maximum rows per underlying in the top CLEAR and NEAR sections",
    )
    ap.add_argument("--no-notify", action="store_true", help="Write brief without macOS notification")
    args = ap.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)
    text, top, scan_date = build_brief(args.date, args.limit, args.max_per_symbol)
    path = REPORTS / f"csp_brief_{scan_date}.md"
    path.write_text(text)

    if not args.no_notify:
        notify(top, path)

    print(path)
    print(f"{len(top)} CLEAR opportunities in alert brief")


if __name__ == "__main__":
    main()
