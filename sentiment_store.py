#!/usr/bin/env python3
"""Local SQLite persistence for TIP sentiment scans."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("SCHWAB_SCANNER_DB", Path.home() / ".schwab" / "scanner.db"))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ensure_sentiment_schema(db_path=DB_PATH):
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as conn:
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS sentiment_signals (
                scan_ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                mentions INTEGER NOT NULL,
                bullish INTEGER NOT NULL,
                bearish INTEGER NOT NULL,
                net_score REAL NOT NULL,
                top_post TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        cur.execute(
            """CREATE INDEX IF NOT EXISTS idx_sentiment_signals_symbol_scan
               ON sentiment_signals(symbol, scan_ts)"""
        )
        cur.execute("DROP VIEW IF EXISTS v_sentiment_daily")
        cur.execute(
            """CREATE VIEW v_sentiment_daily AS
               WITH daily AS (
                   SELECT
                       date(scan_ts) AS scan_date,
                       symbol,
                       SUM(mentions) AS mentions,
                       SUM(bullish) AS bullish,
                       SUM(bearish) AS bearish,
                       CASE
                           WHEN SUM(mentions) > 0
                           THEN ROUND(SUM(net_score * mentions) / SUM(mentions), 4)
                           ELSE 0
                       END AS net_score,
                       MAX(scan_ts) AS latest_scan_ts,
                       GROUP_CONCAT(DISTINCT source) AS sources
                   FROM sentiment_signals
                   WHERE mentions > 0
                   GROUP BY date(scan_ts), symbol
               ),
               latest AS (
                   SELECT symbol, MAX(scan_date) AS scan_date
                   FROM daily
                   GROUP BY symbol
               )
               SELECT
                   d.scan_date,
                   d.symbol,
                   d.mentions,
                   d.bullish,
                   d.bearish,
                   d.net_score,
                   ROUND(CASE WHEN d.mentions > 0 THEN d.bullish * 100.0 / d.mentions ELSE 0 END, 1) AS bullish_pct,
                   ROUND(CASE WHEN d.mentions > 0 THEN d.bearish * 100.0 / d.mentions ELSE 0 END, 1) AS bearish_pct,
                   d.latest_scan_ts,
                   d.sources,
                   (
                       SELECT s.top_post
                       FROM sentiment_signals s
                       WHERE s.symbol = d.symbol
                         AND date(s.scan_ts) = d.scan_date
                         AND COALESCE(s.top_post, '') <> ''
                       ORDER BY s.mentions DESC, ABS(s.net_score) DESC, s.created_at DESC
                       LIMIT 1
                   ) AS top_post
               FROM daily d
               JOIN latest l ON l.symbol = d.symbol AND l.scan_date = d.scan_date
               ORDER BY ABS(d.net_score) DESC, d.mentions DESC, d.symbol"""
        )
        conn.commit()


def _as_int(value):
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _as_float(value):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def persist_sentiment_signals(rows, db_path=DB_PATH):
    ensure_sentiment_schema(db_path)
    now = utc_now()
    clean = []

    for row in rows:
        symbol = str(row.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        mentions = _as_int(row.get("mentions"))
        if mentions <= 0:
            continue
        source = str(row.get("source") or "unknown").strip() or "unknown"
        clean.append(
            (
                row.get("scan_ts") or now,
                symbol,
                source,
                mentions,
                _as_int(row.get("bullish")),
                _as_int(row.get("bearish")),
                round(_as_float(row.get("net_score")), 4),
                (row.get("top_post") or "")[:500],
                row.get("created_at") or now,
            )
        )

    if not clean:
        return 0

    with sqlite3.connect(Path(db_path).expanduser()) as conn:
        conn.executemany(
            """INSERT INTO sentiment_signals
               (scan_ts, symbol, source, mentions, bullish, bearish, net_score, top_post, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            clean,
        )
        conn.commit()
    return len(clean)
