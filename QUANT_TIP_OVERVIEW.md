# Quant + TIP: From Market Data to Underwritten Decisions

This repository is the public, PII-free export of a local trading-intelligence
system built around the Schwab Trader API.

The system combines two complementary layers:

- **Quant** turns live quotes, option chains, Greeks, price history, implied
  volatility, liquidity, and portfolio state into a consistent set of
  measurable candidates.
- **TIP (Trading Intelligence Platform)** turns those candidates into
  portfolio-aware ideas: what the machine saw, why it ranked highly, what could
  go wrong, whether assignment is acceptable, and what safer alternative exists.

The result is not an auto-trader or a promise of returns. It is a decision
system that compresses a noisy market into a small, reviewable queue of ideas
and explicit skips.

## Why it exists

Options scanners are good at finding high premium. They are much less reliable
at answering the question that matters most:

> If this option is assigned, do I actually want to own the underlying at the
> effective basis?

Quant supplies the measurement. TIP supplies the underwriting and portfolio
context. Keeping those jobs separate prevents a high annualized yield, a large
IV print, or a favorable scanner rank from being mistaken for an investment
thesis.

## What the system does

The daily pipeline follows a repeatable sequence:

1. Pull account balances and positions from Schwab using short-lived OAuth
   tokens.
2. Triage existing short options by expiration and assignment urgency.
3. Scan a broad, sector-mapped universe for cash-secured puts and covered
   calls.
4. Add execution context: bid/ask spread, open interest, delta-derived
   assignment probability, IV history, realized volatility, and IV-versus-RV
   context.
5. Apply the earnings calendar as a hard risk gate. Near-term earnings can
   block a trade or mark its IV as event-contaminated.
6. Reconcile raw rank against quality, assignment acceptability, portfolio and
   sector concentration, event risk, data confidence, and execution quality.
7. Persist a daily plan and an opportunity record for review, reporting, and
   later outcome measurement.

The same data supports a local dashboard, human-readable morning reports, and
analysis tools for diversification, covered calls, volatility, and historical
validation.

## Benefits and outcomes

### Better signal-to-noise

Instead of presenting every liquid, high-IV contract, the system produces a
shortlist with a reason for inclusion and a reason it might still be wrong.
That reduces the time spent manually reconciling option metrics across a large
watchlist.

### Faster, repeatable morning workflow

The scan turns a multi-step routine—book review, earnings check, chain review,
portfolio fit, and trade-note writing—into a scheduled, reproducible process.
The output is a decision-ready queue rather than a raw data dump.

### Explicit downside underwriting

Every modeled idea has fields for assignment view, failure point, alternative,
portfolio fit, risk, execution, alpha, volatility, and data confidence. This
makes the downside case first-class instead of burying it beneath premium
math.

### Fewer avoidable event trades

The earnings contract provides a clear vocabulary—`CLEAR`, `NEAR`, `BLOCK`,
and `UNKNOWN`—and prevents a blocked idea from becoming a recommendation just
because its option premium is attractive.

### Portfolio-aware opportunity selection

The system can recognize when a candidate adds to an already-large position or
sector, and can prefer a lower strike, a smaller size, a defined-risk spread,
or a skip. The edge is therefore measured at the book level, not only at the
contract level.

### A learning loop

Raw scanner ranks, TIP decisions, and eventual outcomes are stored separately.
That allows the operator to test whether the underwriting process improves
selection quality without confusing a historical backtest with a live
decision rule.

## Where the edge comes from

The system's edge is process-based rather than a single magic indicator.

### 1. Separate premium discovery from trade approval

The scanner can rank on premium, buying-power reduction, IV, delta, and
liquidity. TIP then asks whether the underlying is desirable at the effective
assignment basis. A high yield that is not desirable to own is labeled a trap
or watch item, not promoted as a trade.

### 2. Treat implied volatility as information, not a free lunch

IV is compared with its local history and realized volatility. Elevated IV can
be an opportunity, but it can also be compensation for a gap, a binary event,
or a deteriorating business. The model records that distinction and lowers
confidence when the context is weak.

### 3. Make execution part of expected value

Bid/ask width, open interest, and quote quality matter because theoretical
premium is not the same as executable premium. The pipeline surfaces poor
liquidity before an idea reaches the review queue.

### 4. Put time and events on the same screen

DTE, earnings timing, IV contamination after expiry, and existing short-option
urgency are evaluated together. This helps avoid selling short-dated premium
into a known catalyst or treating a late-cycle position as passive income.

### 5. Prefer durable, diversified ownership

The quality bias and sector map are deliberately conservative. They favor
names the operator would plausibly hold while penalizing crowded, fragile, or
already-concentrated exposures. For cash-secured puts, premium is treated as a
rebate on an acceptable entry—not as the product by itself.

### 6. Preserve human judgment without hiding the machine's work

TIP does not replace judgment with an opaque score. It records the raw rank
reason, the underwriting reconciliation, the decision, and the notes. A human
can therefore disagree with the model while still seeing exactly what it saw.

## Example of the output contract

An opportunity is more than `symbol + strike + premium`. It carries:

| Layer | Example questions answered |
| --- | --- |
| Market | What are IV, delta, DTE, premium, and spread? |
| Quant | Why did this row rank highly? What is the IV/RV context? |
| Event | Is earnings data fresh? Is the idea `CLEAR`, `NEAR`, or `BLOCK`? |
| Underwriting | Would assignment at strike minus premium be acceptable? |
| Portfolio | Does it diversify the book or add concentration? |
| Risk | What is the failure point and what alternative is safer? |
| Decision | Is it actionable, conditional, watch, blocked, or skip? |

This contract is intentionally reviewable. It is designed for a person to
approve, reject, resize, or replace the structure.

## Operational design

- Runs locally with SQLite-backed state and scheduled launchd jobs.
- Uses Schwab OAuth with credentials and tokens outside the public export.
- Keeps runtime databases, reports, logs, credentials, and sentiment snapshots
  out of Git via `.gitignore`.
- Fails closed on missing or stale earnings data when the trade cannot be
  underwritten safely.
- Exports source code and examples without account numbers, balances,
  positions, tokens, or personal contact data.

## What this is—and is not

**It is:** a portfolio-aware research and trade-preparation system, a durable
record of why an idea was considered, and a foundation for measuring decisions
over time.

**It is not:** an automated order-entry system, a guarantee of positive
returns, or a substitute for verifying live quotes, corporate events, tax
constraints, and personal risk limits before acting.

The practical advantage is disciplined selectivity: more of the operator's
attention goes to the few ideas that survive both quantitative screening and
assignment underwriting, while weak or contaminated opportunities are made
visible as explicit skips.
