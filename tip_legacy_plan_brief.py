#!/usr/bin/env python3
"""
Legacy-style TIP daily plan brief.

Reconstructs the old "ranked trades" report shape from the local TIP tables:
daily_plan, scan_runs, v_sentiment_daily, and optional live Schwab positions.
It does not place or stage orders.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from collections import Counter

HOME = Path.home()
DB = HOME / ".schwab" / "scanner.db"
REPORTS = HOME / "Documents" / "Trading" / "reports"

SECTOR = {
    "MSFT": "Technology", "NVDA": "Technology", "ORCL": "Technology",
    "AMD": "Technology", "AMZN": "Technology", "TSLA": "Technology",
    "AAPL": "Technology", "INTC": "Technology", "CRM": "Technology",
    "AVGO": "Technology", "QCOM": "Technology",
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "HD": "Consumer",
    "MCD": "Consumer", "NKE": "Consumer",
    "LLY": "Healthcare", "UNH": "Healthcare", "ABBV": "Healthcare",
    "ISRG": "Healthcare", "JNJ": "Healthcare", "MRK": "Healthcare",
    "AMGN": "Healthcare", "GILD": "Healthcare", "PFE": "Healthcare",
    "LMT": "Defense", "RTX": "Defense", "NOC": "Defense", "GD": "Defense",
    "BA": "Defense", "AXON": "Defense", "PLTR": "Defense",
    "CVX": "Energy", "XOM": "Energy", "COP": "Energy", "OXY": "Energy",
    "CEG": "Utilities", "VST": "Utilities",
    "JPM": "Financials", "GS": "Financials", "BAC": "Financials",
    "MS": "Financials", "SCHW": "Financials", "BLK": "Financials",
    "WFC": "Financials", "C": "Financials",
    "CAT": "Industrials", "GE": "Industrials",
}


def db_rows(sql, args=()):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def latest_date():
    rows = db_rows("SELECT MAX(scan_date) d FROM daily_plan")
    return rows[0]["d"] if rows and rows[0]["d"] else None


def money(v):
    if v is None:
        return "-"
    return f"${float(v):,.0f}"


def pct(v, places=0):
    if v is None:
        return "-"
    return f"{float(v):.{places}f}%"


def get_positions():
    """Return sanitized position map; fail soft when Schwab is unavailable."""
    try:
        from quick_start import get_client
        client = get_client()
        if not client:
            return {}
        accts = client.get_accounts(True)
    except Exception:
        return {}

    out = {}
    for acct in accts:
        for pos in acct.get("securitiesAccount", {}).get("positions", []):
            inst = pos.get("instrument", {})
            sym = inst.get("symbol")
            if not sym or inst.get("assetType") == "OPTION":
                continue
            qty = float(pos.get("longQuantity", 0) or 0) - float(pos.get("shortQuantity", 0) or 0)
            out[sym] = {
                "qty": qty,
                "avg": float(pos.get("averagePrice", 0) or 0),
                "mv": float(pos.get("marketValue", 0) or 0),
            }
    return out


def get_sentiment():
    try:
        rows = db_rows(
            """SELECT symbol, mentions, bullish_pct, bearish_pct, net_score, top_post
               FROM v_sentiment_daily"""
        )
    except sqlite3.OperationalError:
        return {}
    return {r["symbol"]: r for r in rows}


def get_opportunities(scan_date):
    try:
        return db_rows(
            """SELECT * FROM tip_opportunities
               WHERE scan_date=?
               ORDER BY CASE label
                    WHEN 'ACTIONABLE' THEN 0 WHEN 'MONETIZE' THEN 1 WHEN 'CONDITIONAL' THEN 2
                    WHEN 'WATCH' THEN 3 ELSE 4 END,
                    score DESC""",
            (scan_date,),
        )
    except sqlite3.OperationalError:
        return []


def cc_contracts(row):
    bid = float(row.get("bid") or 0)
    prem = float(row.get("premium") or 0)
    if bid <= 0:
        return 1
    return max(1, int(round(prem / (bid * 100))))


def row_yield(row):
    if row["kind"] == "CSP":
        return float(row.get("ann_yield") or 0)
    contracts = cc_contracts(row)
    notional = float(row.get("strike") or 0) * 100 * contracts
    premium = float(row.get("premium") or 0)
    dte = max(1, int(row.get("dte") or 1))
    return (premium / notional) * (365 / dte) * 100 if notional > 0 else 0


def probability(row):
    delta = abs(float(row.get("delta") or 0))
    return max(0, min(100, (1 - delta) * 100))


def breakeven(row):
    strike = float(row.get("strike") or 0)
    premium = float(row.get("premium") or 0)
    contracts = cc_contracts(row) if row["kind"] == "CC" else 1
    per_contract = premium / max(1, contracts) / 100
    if row["kind"] == "CSP":
        return strike - per_contract
    return strike + per_contract


def underwriting(row, positions, nlv):
    sym = row["sym"]
    state = row.get("earn_state") or "UNKNOWN"
    kind = row["kind"]
    yld = row_yield(row)
    delta = abs(float(row.get("delta") or 0))
    pos_mv = positions.get(sym, {}).get("mv", 0)
    weight = (pos_mv / nlv * 100) if nlv else 0

    score = 45 + min(28, yld / 8) + max(0, 12 - abs(delta - 0.25) * 50)
    label = "REVIEW"
    why = []

    if state == "CLEAR":
        score += 8
    elif state == "NEAR":
        score -= 12
        why.append("earnings/IV near")
    else:
        score -= 18
        why.append("earnings unknown")

    if kind == "CC":
        label = "MONETIZE"
        if weight >= 10:
            score += 10
            why.append("reduces concentrated upside risk")
        else:
            why.append("covered-call income")
    else:
        if weight >= 10:
            score -= 25
            why.append(f"already {weight:.0f}% of NLV")
        if sym == "ORCL":
            score -= 18
            why.append("AI capex/debt risk")
        if sym == "NVDA":
            score -= 15
            why.append("adds crowded AI concentration")
        if sym in ("AVGO", "COST", "AMZN", "CRM", "CAT"):
            score += 6
            why.append("assignment potentially acceptable")

    if kind == "CSP" and state == "CLEAR" and weight < 10 and score >= 68:
        label = "ACTIONABLE"
    elif kind == "CSP" and score >= 55:
        label = "CONDITIONAL"
    elif kind == "CSP":
        label = "WATCH/SKIP"

    if float(row.get("notional") or 0) >= 80000 and kind == "CSP":
        score -= 6
        why.append("large notional")

    score = int(max(1, min(99, round(score))))
    return label, score, "; ".join(why[:3]) or "yield-ranked scanner idea"


def strategy(row):
    if row["kind"] == "CSP":
        return "CSP"
    return "CC"


def strikes(row):
    if row["kind"] == "CSP":
        return f"${float(row['strike']):.0f}"
    return f"${float(row['strike']):.0f}C"


def diversify_opportunities(rows, limit=8, max_per_symbol=1):
    picked = []
    counts = {}
    for r in rows:
        sym = r.get("symbol") or r.get("sym")
        if counts.get(sym, 0) >= max_per_symbol:
            continue
        picked.append(r)
        counts[sym] = counts.get(sym, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def generate(scan_date=None):
    scan_date = scan_date or latest_date()
    if not scan_date:
        raise SystemExit("No daily_plan rows found.")

    run_rows = db_rows(
        "SELECT * FROM scan_runs WHERE scan_date=? ORDER BY finished_at DESC LIMIT 1",
        (scan_date,),
    )
    run = run_rows[0] if run_rows else {}
    nlv = float(run.get("nlv") or 0)
    positions = get_positions()
    sentiment = get_sentiment()
    opportunities = get_opportunities(scan_date)

    rows = db_rows(
        """SELECT * FROM daily_plan WHERE scan_date=?
           ORDER BY CASE earn_state WHEN 'CLEAR' THEN 0 WHEN 'NEAR' THEN 1 ELSE 2 END,
                    CASE kind WHEN 'CC' THEN premium ELSE ann_yield END DESC""",
        (scan_date,),
    )

    enriched = []
    seen = {}
    for r in rows:
        label, score, reason = underwriting(r, positions, nlv)
        r["sector"] = SECTOR.get(r["sym"], "Other")
        r["strategy"] = strategy(r)
        r["score"] = score
        r["label"] = label
        r["reason"] = reason
        r["calc_yield"] = row_yield(r)
        r["pop"] = probability(r)
        r["breakeven"] = breakeven(r)
        key = (r["kind"], r["sym"])
        if seen.get(key, 0) >= 2:
            continue
        seen[key] = seen.get(key, 0) + 1
        enriched.append(r)

    enriched.sort(key=lambda r: (r["label"] != "ACTIONABLE", -r["score"], -r["calc_yield"]))
    top_cards = diversify_opportunities(opportunities, 8, 1) if opportunities else enriched[:8]
    ranked = enriched[:50]

    lines = [
        f"# TIP Legacy Daily Plan - {scan_date}",
        "",
        f"Generated {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Scope: ranked ideas only. Manual sizing and execution.",
        "",
        "## Account",
        "",
        (
            f"NLV {money(run.get('nlv'))} | Avail {money(run.get('avail_funds'))} | "
            f"MaintReq {money(run.get('maint_req'))} | MarginBal {money(run.get('margin_bal'))}"
        ),
        "",
        "## Top Cards",
        "",
    ]

    for i, r in enumerate(top_cards, 1):
        if "sleeve" in r:
            lines.extend(
                [
                    f"### #{i} {r['symbol']} - {r['label']} {r['strategy']} ({r['sleeve']})",
                    (
                        f"Score {r['score']} | {r['structure']} {r.get('expiry') or '-'} | "
                        f"Yield {pct(r.get('ann_yield'), 1)} | Delta {float(r.get('delta') or 0):.2f} | "
                        f"DTE {int(r.get('dte') or 0)} | Prem {money(r.get('premium'))}"
                    ),
                    f"Why ranked: {r['raw_rank_reason']}",
                    f"Underwriting: {r['assignment_view']} Failure point: {r['failure_point']}.",
                    f"Alternative: {r['alternative']}.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"### #{i} {r['sym']} - {r['label']} {r['strategy']}",
                    (
                        f"Score {r['score']} | {strikes(r)} exp {r['exp']} | "
                        f"PoP {pct(r['pop'])} | Yield {pct(r['calc_yield'], 1)} | "
                        f"Delta {abs(float(r.get('delta') or 0)):.2f} | DTE {int(r.get('dte') or 0)} | "
                        f"Prem {money(r.get('premium'))}"
                    ),
                    f"Underwriting: {r['reason']}. Breakeven {r['breakeven']:.2f}.",
                    "",
                ]
            )

    if opportunities:
        lines.extend(["", "## Sleeve Breakdown", ""])
        sleeve_counts = Counter(r["sleeve"] for r in opportunities)
        for sleeve, count in sleeve_counts.most_common():
            best = diversify_opportunities([r for r in opportunities if r["sleeve"] == sleeve], 5, 1)
            tickers = ", ".join(f"{r['symbol']}({r['label']} {r['score']})" for r in best)
            lines.append(f"- {sleeve}: {count} ideas - {tickers}")
        lines.append("")

        lines.extend(["## Evidence Against Top Ideas", ""])
        for r in diversify_opportunities(opportunities, 10, 1):
            lines.append(
                f"- {r['symbol']} {r['strategy']}: {r['failure_point']}. "
                f"Better expression: {r['alternative']}."
            )
        lines.append("")

    if opportunities:
        lines.extend(
            [
                "## All Underwritten Ideas",
                "",
                "| # | Symbol | Sleeve | Strategy | Structure | Expiry | Score | Label | Yield | Delta | DTE | Prem | Underwriting |",
                "|---:|---|---|---|---|---|---:|---|---:|---:|---:|---:|---|",
            ]
        )
        for i, r in enumerate(opportunities[:50], 1):
            note = f"{r['assignment_view']} Alt: {r['alternative']}".replace("|", "/")
            lines.append(
                "| {i} | {symbol} | {sleeve} | {strategy} | {structure} | {expiry} | {score} | {label} | "
                "{yld} | {delta:.2f} | {dte} | {prem} | {note} |".format(
                    i=i,
                    symbol=r["symbol"],
                    sleeve=r["sleeve"],
                    strategy=r["strategy"],
                    structure=r["structure"] or "-",
                    expiry=r["expiry"] or "-",
                    score=r["score"],
                    label=r["label"],
                    yld=pct(r.get("ann_yield"), 1),
                    delta=abs(float(r.get("delta") or 0)),
                    dte=int(r.get("dte") or 0),
                    prem=money(r.get("premium")),
                    note=note,
                )
            )
    else:
        lines.extend(
            [
                "## All Ranked Trades",
                "",
                "| # | Symbol | Sector | Strategy | Strike | Expiry | Score | Label | PoP | Yield | Delta | DTE | Prem | Note |",
                "|---:|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for i, r in enumerate(ranked, 1):
            lines.append(
                "| {i} | {sym} | {sector} | {strategy} | {strike} | {exp} | {score} | {label} | "
                "{pop} | {yld} | {delta:.2f} | {dte} | {prem} | {note} |".format(
                    i=i,
                    sym=r["sym"],
                    sector=r["sector"],
                    strategy=r["strategy"],
                    strike=strikes(r),
                    exp=r["exp"] or "-",
                    score=r["score"],
                    label=r["label"],
                    pop=pct(r["pop"]),
                    yld=pct(r["calc_yield"], 1),
                    delta=abs(float(r.get("delta") or 0)),
                    dte=int(r.get("dte") or 0),
                    prem=money(r.get("premium")),
                    note=r["reason"].replace("|", "/"),
                )
            )

    lines.extend(["", "## Contrarian Signals", ""])
    signals = sorted(sentiment.values(), key=lambda s: abs(float(s.get("net_score") or 0)), reverse=True)[:12]
    for s in signals:
        net = float(s.get("net_score") or 0)
        bull = float(s.get("bullish_pct") or 0)
        bear = float(s.get("bearish_pct") or 0)
        tag = "SELL_PREMIUM" if bull >= 45 and net > 0 else ("BUY_DIP" if bear >= 30 and net < 0 else "WATCH")
        lines.append(f"- {tag}: {s['symbol']} net {net:+.2f}, bull {bull:.0f}%, bear {bear:.0f}%, mentions {int(s.get('mentions') or 0)}")

    if opportunities:
        radar = [r for r in opportunities if r["strategy"] == "RADAR"][:15]
        if radar:
            lines.extend(["", "## Discovery / Radar Queue", ""])
            for r in radar:
                lines.append(
                    f"- {r['sleeve']}: {r['symbol']} score {r['score']} - "
                    f"{r['raw_rank_reason']} Next: {r['alternative']}."
                )

    lines.extend(
        [
            "",
            "## Gaps Vs Old Dashboard",
            "",
            "- Bull-put spread generation is still pending as a dedicated structure table.",
            "- The new tip_opportunities layer separates raw scanner rows from recommendation quality.",
            "- Earnings cache has UNKNOWN/STALE rows; those are demoted until verified.",
            "- Raw yield rank is preserved, but recommendation labels now apply assignment underwriting.",
            "",
        ]
    )

    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"tip_legacy_plan_{scan_date}.md"
    path.write_text("\n".join(lines))
    return path


def main():
    path = generate()
    print(path)


if __name__ == "__main__":
    main()
