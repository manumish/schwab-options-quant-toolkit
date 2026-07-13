#!/usr/bin/env python3
"""
TIP opportunity model.

Turns raw scanner rows into underwritten opportunity ideas. The goal is to
separate "the scanner found premium" from "this is a trade worth considering."
"""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HOME = Path.home()
DB = HOME / ".schwab" / "scanner.db"


SECTOR_UNIVERSE = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "ORCL", "CRM", "QCOM", "AMZN",
        "GOOGL", "META", "TSLA", "INTC", "MU", "ADBE", "NOW", "PANW", "CRWD",
        "SNOW", "PLTR", "SMCI", "CRDO",
    ],
    "Healthcare": [
        "UNH", "JNJ", "LLY", "ABBV", "MRK", "TMO", "ABT", "PFE", "AMGN",
        "MDT", "ISRG", "GILD", "VRTX", "BSX", "SYK", "CI", "ELV", "HCA",
        "ZTS", "REGN", "CVS", "HUM",
    ],
    "Defense": [
        "RTX", "LMT", "GD", "NOC", "BA", "LHX", "TDG", "HII", "LDOS", "AXON",
        "HEI", "PLTR",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "OXY", "WMB", "KMI",
        "OKE", "TRGP",
    ],
    "Utilities": [
        "NEE", "SO", "DUK", "CEG", "VST", "AEP", "D", "SRE", "XEL", "ED", "EXC",
        "PCG",
    ],
    "Consumer": [
        "PG", "KO", "PEP", "PM", "MO", "CL", "MDLZ", "GIS", "KMB", "STZ",
        "WMT", "TGT", "COST", "KR", "SYY", "HD", "MCD", "NKE",
    ],
    "Financials": [
        "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SCHW", "BLK", "SPGI",
        "ICE", "CME", "CB", "MET", "PGR", "ALL", "AXP", "COF", "MMC", "AON",
    ],
    "Industrials": [
        "CAT", "DE", "UNP", "HON", "GE", "EMR", "ITW", "ROK", "PH", "ETN",
        "WM", "RSG", "UBER", "FDX",
    ],
    "Real Estate": ["PLD", "AMT", "EQIX", "CCI", "PSA", "O", "SPG", "WELL", "DLR", "VICI"],
    "Materials": ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "DOW", "VMC", "MLM"],
    "ETFs": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLU", "TLT", "GLD"],
}

SYMBOL_SECTOR = {}
for _sector, _symbols in SECTOR_UNIVERSE.items():
    for _sym in _symbols:
        SYMBOL_SECTOR[_sym] = _sector

BROAD_OPTION_UNIVERSE = sorted(SYMBOL_SECTOR)

QUALITY_BIAS = {
    "MSFT": 10, "AMZN": 8, "COST": 8, "CAT": 7, "JPM": 7, "UNH": 6, "ABBV": 6,
    "LLY": 5, "RTX": 5, "LMT": 5, "CVX": 5, "XOM": 5, "GOOGL": 5, "META": 4,
    "AVGO": 3, "CRM": 3, "QCOM": 3, "ORCL": -12, "NVDA": -8, "TSLA": -14,
    "SMCI": -12, "PLTR": -6, "BA": -10, "INTC": -8, "OXY": -4,
}

RISK_NOTES = {
    "ORCL": "AI capex, leverage, and existing book concentration explain premium",
    "NVDA": "crowded AI beta and gap risk can make assignment path-dependent",
    "TSLA": "idiosyncratic volatility and narrative risk dominate normal valuation anchors",
    "BA": "execution, balance-sheet, and regulatory risk can overwhelm premium",
    "SMCI": "headline/accounting risk and cyclic AI server exposure can gap the stock",
    "INTC": "turnaround and capital intensity make cheap premium hard to trust",
    "PLTR": "valuation/crowding risk; prefer defined-risk structures when IV is rich",
}


@dataclass
class OpportunityIdea:
    scan_date: str
    sleeve: str
    symbol: str
    strategy: str
    structure: str
    expiry: str
    dte: int
    score: int
    label: str
    raw_rank_reason: str
    assignment_view: str
    failure_point: str
    alternative: str
    portfolio_fit: int
    alpha_score: int
    vol_score: int
    execution_score: int
    risk_score: int
    data_confidence: int
    premium: float = 0.0
    bp_reduction: float = 0.0
    ann_yield: float = 0.0
    delta: float = 0.0
    iv: float = 0.0
    earn_state: str = "UNKNOWN"
    details_json: str = "{}"


def sector_for(symbol: str) -> str:
    return SYMBOL_SECTOR.get(symbol, "Other")


def broad_option_universe(extra: Optional[Iterable[str]] = None) -> List[str]:
    symbols = set(BROAD_OPTION_UNIVERSE)
    if extra:
        symbols.update(s for s in extra if s)
    return sorted(symbols)


def ensure_opportunity_tables(db: Path = DB) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tip_opportunities (
            scan_date TEXT,
            sleeve TEXT,
            symbol TEXT,
            strategy TEXT,
            structure TEXT,
            expiry TEXT,
            dte INTEGER,
            score INTEGER,
            label TEXT,
            raw_rank_reason TEXT,
            assignment_view TEXT,
            failure_point TEXT,
            alternative TEXT,
            portfolio_fit INTEGER,
            alpha_score INTEGER,
            vol_score INTEGER,
            execution_score INTEGER,
            risk_score INTEGER,
            data_confidence INTEGER,
            premium REAL,
            bp_reduction REAL,
            ann_yield REAL,
            delta REAL,
            iv REAL,
            earn_state TEXT,
            details_json TEXT,
            created_at TEXT,
            PRIMARY KEY (scan_date, sleeve, symbol, strategy, structure, expiry)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            scan_date TEXT,
            symbol TEXT,
            strategy TEXT,
            scanner_rank_reason TEXT,
            reconciliation TEXT,
            decision TEXT,
            notes TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def portfolio_context(equities: Iterable[Tuple[str, float, float, float]], nlv: float) -> Dict[str, object]:
    weights = {}
    sector_weights = {}
    for sym, qty, _avg, mv in equities:
        if not sym:
            continue
        w = (float(mv or 0) / nlv * 100.0) if nlv else 0.0
        weights[sym] = w
        sec = sector_for(sym)
        sector_weights[sec] = sector_weights.get(sec, 0.0) + w
    return {"weights": weights, "sector_weights": sector_weights}


def _bounded(v: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(v))))


def _num(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _optional_num(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _label(score: int, risk_score: int, earn_state: str, strategy: str) -> str:
    if earn_state == "BLOCK":
        return "BLOCKED"
    if strategy == "CC":
        return "MONETIZE" if score >= 58 else "LOW_PRIORITY"
    if score >= 76 and risk_score <= 55:
        return "ACTIONABLE"
    if score >= 62:
        return "CONDITIONAL"
    if score >= 48:
        return "WATCH"
    return "SKIP"


def _csp_idea(row: dict, ctx: Dict[str, object], raw_rank: int) -> OpportunityIdea:
    sym = row.get("sym")
    yld = _num(row.get("margin_ann_yield") or row.get("ann_yield"))
    delta = abs(_num(row.get("delta")))
    iv = _num(row.get("iv"))
    oi = _num(row.get("oi"))
    bid = _num(row.get("bid"))
    ask = _num(row.get("ask"))
    spread_pct = _optional_num(row, "spread_pct")
    iv_rank = _optional_num(row, "iv_rank")
    iv_percentile = _optional_num(row, "iv_percentile")
    iv_rv_spread = _optional_num(row, "iv_rv_spread")
    divergence = _optional_num(row, "iv_rank_percentile_divergence")
    vol_confidence = row.get("vol_confidence") or "NONE"
    earn_state = row.get("earn_state") or "UNKNOWN"
    weight = ctx.get("weights", {}).get(sym, 0.0)
    sector_weight = ctx.get("sector_weights", {}).get(sector_for(sym), 0.0)

    alpha = 48 + QUALITY_BIAS.get(sym, 0)
    if 0.18 <= delta <= 0.35:
        alpha += 10
    if row.get("otm_pct") is not None and float(row.get("otm_pct") or 0) <= 8:
        alpha += 5
    if yld >= 50:
        alpha -= 6

    vol = 40 + min(22, yld / 3.0)
    if 25 <= iv <= 60:
        vol += 10
    elif iv > 75:
        vol -= 12
    if iv_rv_spread is not None:
        if iv_rv_spread >= 15:
            vol += 16
        elif iv_rv_spread >= 8:
            vol += 10
        elif iv_rv_spread >= 3:
            vol += 4
        elif iv_rv_spread < 0:
            vol -= 18
    if iv_percentile is not None:
        if iv_percentile >= 80:
            vol += 8
        elif iv_percentile >= 65:
            vol += 4
        elif iv_percentile < 35:
            vol -= 8

    execution = 45
    if oi >= 500:
        execution += 20
    elif oi >= 100:
        execution += 10
    if bid > 0 and ask > 0:
        spread = (ask - bid) / max((ask + bid) / 2, 0.01)
        if spread <= 0.12:
            execution += 12
        elif spread >= 0.30:
            execution -= 12
    if spread_pct is not None:
        if spread_pct <= 8:
            execution += 6
        elif spread_pct >= 25:
            execution -= 18
        elif spread_pct >= 15:
            execution -= 8

    fit = 62
    if weight >= 10:
        fit -= 35
    elif weight >= 5:
        fit -= 18
    if sector_weight >= 35 and sector_for(sym) in ("Technology", "ETFs"):
        fit -= 12
    if sector_for(sym) in ("Healthcare", "Financials", "Energy", "Defense", "Consumer"):
        fit += 6

    data_confidence = 78 if earn_state == "CLEAR" else (58 if earn_state == "NEAR" else 42)
    if oi <= 0:
        data_confidence -= 10
    if vol_confidence == "HIGH":
        data_confidence += 5
    elif vol_confidence == "LOW":
        data_confidence -= 5
    else:
        data_confidence -= 12
    if divergence is not None and divergence > 25:
        data_confidence -= 8

    risk = 36
    if earn_state == "NEAR":
        risk += 18
    elif earn_state == "UNKNOWN":
        risk += 24
    if iv >= 65:
        risk += 14
    if iv_rv_spread is not None and iv_rv_spread < 0:
        risk += 10
    if spread_pct is not None and spread_pct >= 25:
        risk += 10
    if row.get("iv_outlier_distorted"):
        risk += 6
    if weight >= 10:
        risk += 25
    if sym in RISK_NOTES:
        risk += 12

    alpha = _bounded(alpha)
    vol = _bounded(vol)
    execution = _bounded(execution)
    fit = _bounded(fit)
    data_confidence = _bounded(data_confidence)
    risk = _bounded(risk)
    score = _bounded(alpha * 0.23 + vol * 0.23 + fit * 0.24 + execution * 0.15 + data_confidence * 0.15 - max(0, risk - 50) * 0.35)
    label = _label(score, risk, earn_state, "CSP")

    breakeven = float(row.get("strike") or 0) - float(row.get("premium_100") or row.get("premium") or 0) / 100.0
    if label in ("ACTIONABLE", "CONDITIONAL") and weight < 10:
        assignment = f"Potentially acceptable at breakeven {breakeven:.2f}; still needs current thesis check."
    elif weight >= 10:
        assignment = f"Poor fit: already {weight:.1f}% of NLV before assignment."
    else:
        assignment = f"Not yet proven as desirable ownership at breakeven {breakeven:.2f}."

    risk_note = RISK_NOTES.get(sym, "stock-specific drawdown or event risk is what funds the premium")
    alt = "Consider lower strike or put spread" if risk >= 58 else "Size small; compare with sector alternatives"
    if weight >= 10:
        alt = "Prefer covered-call monetization or skip adding exposure"

    structure = f"{float(row.get('strike') or 0):.0f}P"
    vol_bits = []
    if iv_percentile is not None:
        vol_bits.append(f"IVP {iv_percentile:.0f}")
    if iv_rank is not None:
        vol_bits.append(f"IVR {iv_rank:.0f}")
    if iv_rv_spread is not None:
        vol_bits.append(f"VRP {iv_rv_spread:+.0f}")
    if spread_pct is not None:
        vol_bits.append(f"spread {spread_pct:.0f}%")
    vol_context = ", ".join(vol_bits)
    raw_reason = f"Raw CSP rank #{raw_rank}: {yld:.1f}% margin annualized yield, IV {iv:.0f}, delta {delta:.2f}, earnings {earn_state}."
    if vol_context:
        raw_reason = raw_reason[:-1] + f", {vol_context}."

    return OpportunityIdea(
        scan_date=date.today().isoformat(),
        sleeve="QUALITY_DIP_CSP" if label in ("ACTIONABLE", "CONDITIONAL") else "YIELD_TRAP_REVIEW",
        symbol=sym,
        strategy="CSP",
        structure=structure,
        expiry=row.get("exp") or "",
        dte=int(row.get("dte") or 0),
        score=score,
        label=label,
        raw_rank_reason=raw_reason,
        assignment_view=assignment,
        failure_point=risk_note,
        alternative=alt,
        portfolio_fit=fit,
        alpha_score=alpha,
        vol_score=vol,
        execution_score=execution,
        risk_score=risk,
        data_confidence=data_confidence,
        premium=float(row.get("premium_100") or row.get("premium") or 0),
        bp_reduction=float(row.get("bp_reduction") or 0),
        ann_yield=yld,
        delta=delta,
        iv=iv,
        earn_state=earn_state,
        details_json=json.dumps(row, sort_keys=True, default=str),
    )


def _cc_idea(row: dict, ctx: Dict[str, object], raw_rank: int) -> OpportunityIdea:
    sym = row.get("sym")
    weight = ctx.get("weights", {}).get(sym, 0.0)
    iv = _num(row.get("iv"))
    delta = abs(_num(row.get("delta")))
    premium = _num(row.get("premium_total") or row.get("premium"))
    spread_pct = _optional_num(row, "spread_pct")
    iv_percentile = _optional_num(row, "iv_percentile")
    iv_rv_spread = _optional_num(row, "iv_rv_spread")
    earn_state = row.get("earn_state") or "UNKNOWN"
    fit = _bounded(52 + min(28, weight * 2.0))
    vol = _bounded(42 + min(25, iv / 2.5))
    if iv_rv_spread is not None and iv_rv_spread >= 8:
        vol = _bounded(vol + 8)
    if iv_percentile is not None and iv_percentile >= 70:
        vol = _bounded(vol + 5)
    alpha = _bounded(48 + (8 if delta <= 0.25 else -5))
    execution = 62 if premium > 0 else 40
    if spread_pct is not None:
        if spread_pct <= 10:
            execution += 6
        elif spread_pct >= 25:
            execution -= 12
    execution = _bounded(execution)
    data_confidence = 76 if earn_state == "CLEAR" else 58
    risk = 34 if weight >= 8 else 42
    score = _bounded(alpha * 0.20 + vol * 0.20 + fit * 0.30 + execution * 0.15 + data_confidence * 0.15)
    label = _label(score, risk, earn_state, "CC")
    strike = float(row.get("strike") or 0)
    structure = f"{strike:.0f}C"
    vol_bits = []
    if iv_percentile is not None:
        vol_bits.append(f"IVP {iv_percentile:.0f}")
    if iv_rv_spread is not None:
        vol_bits.append(f"VRP {iv_rv_spread:+.0f}")
    if spread_pct is not None:
        vol_bits.append(f"spread {spread_pct:.0f}%")
    raw_reason = f"Raw CC rank #{raw_rank}: premium ${premium:,.0f}, IV {iv:.0f}, delta {delta:.2f}, earnings {earn_state}."
    if vol_bits:
        raw_reason = raw_reason[:-1] + f", {', '.join(vol_bits)}."
    return OpportunityIdea(
        scan_date=date.today().isoformat(),
        sleeve="PORTFOLIO_MONETIZATION",
        symbol=sym,
        strategy="CC",
        structure=structure,
        expiry=row.get("exp") or "",
        dte=int(row.get("dte") or 0),
        score=score,
        label=label,
        raw_rank_reason=raw_reason,
        assignment_view=f"Covered-call sale monetizes existing {weight:.1f}% NLV position; check willingness to cap upside above {strike:.2f}.",
        failure_point="upside opportunity cost if the stock re-rates through the strike",
        alternative="Use higher strike or wait if catalyst/upside is still the main thesis",
        portfolio_fit=fit,
        alpha_score=alpha,
        vol_score=vol,
        execution_score=execution,
        risk_score=risk,
        data_confidence=data_confidence,
        premium=premium,
        ann_yield=0.0,
        delta=delta,
        iv=iv,
        earn_state=earn_state,
        details_json=json.dumps(row, sort_keys=True, default=str),
    )


def _sentiment_ideas(scan_date: str, db: Path) -> List[OpportunityIdea]:
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT symbol, mentions, bullish_pct, bearish_pct, net_score, top_post
            FROM v_sentiment_daily
            ORDER BY ABS(COALESCE(net_score, 0)) DESC
            LIMIT 20
            """
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    out = []
    for r in rows:
        sym = r["symbol"]
        net = float(r["net_score"] or 0)
        bull = float(r["bullish_pct"] or 0)
        bear = float(r["bearish_pct"] or 0)
        mentions = int(r["mentions"] or 0)
        if mentions < 3:
            continue
        if bear >= 35 and net < 0:
            sleeve = "CONTRARIAN_BUY_DIP"
            raw = f"Panic signal: bearish {bear:.0f}%, net {net:+.2f}, mentions {mentions}."
            assignment = "Radar only until price action, fundamentals, and options chain confirm a tradable dip."
        elif bull >= 45 and net > 0:
            sleeve = "CONTRARIAN_SELL_PREMIUM"
            raw = f"Euphoria signal: bullish {bull:.0f}%, net {net:+.2f}, mentions {mentions}."
            assignment = "Radar only; euphoric chatter can support premium selling but can also mark squeeze risk."
        else:
            continue
        score = _bounded(50 + min(25, abs(net) * 30) + min(15, mentions / 3))
        out.append(
            OpportunityIdea(
                scan_date=scan_date,
                sleeve=sleeve,
                symbol=sym,
                strategy="RADAR",
                structure="sentiment",
                expiry="",
                dte=0,
                score=score,
                label="WATCH",
                raw_rank_reason=raw,
                assignment_view=assignment,
                failure_point="social signal can be noisy; require live chain and catalyst check",
                alternative="Promote only after chain liquidity, earnings state, and thesis quality pass",
                portfolio_fit=55,
                alpha_score=score,
                vol_score=50,
                execution_score=0,
                risk_score=55,
                data_confidence=50,
                details_json=json.dumps(dict(r), sort_keys=True, default=str),
            )
        )
    return out


def persist_opportunities(ideas: List[OpportunityIdea], db: Path = DB, scan_date: Optional[str] = None) -> None:
    ensure_opportunity_tables(db)
    scan_date = scan_date or date.today().isoformat()
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM tip_opportunities WHERE scan_date=?", (scan_date,))
    now = datetime.now().isoformat()
    for idea in ideas:
        data = asdict(idea)
        conn.execute(
            """
            INSERT OR REPLACE INTO tip_opportunities VALUES
            (:scan_date, :sleeve, :symbol, :strategy, :structure, :expiry, :dte,
             :score, :label, :raw_rank_reason, :assignment_view, :failure_point,
             :alternative, :portfolio_fit, :alpha_score, :vol_score,
             :execution_score, :risk_score, :data_confidence, :premium,
             :bp_reduction, :ann_yield, :delta, :iv, :earn_state,
             :details_json, :created_at)
            """,
            {**data, "created_at": now},
        )
    conn.commit()
    conn.close()


def build_opportunities(
    cc_rows: List[dict],
    csp_rows: List[dict],
    equities: Iterable[Tuple[str, float, float, float]],
    nlv: float,
    db: Path = DB,
    scan_date: Optional[str] = None,
) -> List[OpportunityIdea]:
    scan_date = scan_date or date.today().isoformat()
    ctx = portfolio_context(equities, nlv)
    ideas = []
    for i, row in enumerate(csp_rows, 1):
        idea = _csp_idea(row, ctx, i)
        idea.scan_date = scan_date
        ideas.append(idea)
    for i, row in enumerate(cc_rows, 1):
        idea = _cc_idea(row, ctx, i)
        idea.scan_date = scan_date
        ideas.append(idea)
    ideas.extend(_sentiment_ideas(scan_date, db))
    ideas.sort(key=lambda x: (x.label not in ("ACTIONABLE", "MONETIZE", "CONDITIONAL"), -x.score))
    persist_opportunities(ideas, db, scan_date)
    return ideas


def latest_opportunities(db: Path = DB, scan_date: Optional[str] = None, limit: int = 80) -> List[dict]:
    ensure_opportunity_tables(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if not scan_date:
        row = conn.execute("SELECT MAX(scan_date) d FROM tip_opportunities").fetchone()
        scan_date = row["d"] if row else None
    if not scan_date:
        conn.close()
        return []
    rows = conn.execute(
        """
        SELECT * FROM tip_opportunities
        WHERE scan_date=?
        ORDER BY CASE label
            WHEN 'ACTIONABLE' THEN 0 WHEN 'MONETIZE' THEN 1 WHEN 'CONDITIONAL' THEN 2
            WHEN 'WATCH' THEN 3 ELSE 4 END,
            score DESC
        LIMIT ?
        """,
        (scan_date, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
