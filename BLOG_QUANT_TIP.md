# I Built a Trading System That Tells Me When to Skip the Trade

I started building this system for a simple reason: most options scanners are
very good at finding premium and surprisingly bad at answering the question I
actually care about.

If this put is assigned, do I want to own the stock at the effective basis?

That question changed the design.

The result is a local quantitative and trading-intelligence system that I call
Quant + TIP. Quant measures the market. TIP—Trading Intelligence Platform—puts
those measurements through an underwriting process that includes the portfolio,
the calendar, execution quality, and the possibility that the trade is simply a
bad idea.

It is not an auto-trader. It is a decision system.

## The problem with “high yield”

Selling a put can look attractive on a spreadsheet. The premium is visible, the
annualized yield is easy to calculate, and a scanner can rank thousands of
contracts in seconds.

But a high annualized yield can also be a warning sign. The market may be
pricing a binary event, a weak balance sheet, crowded positioning, poor
liquidity, or a stock I would not want to own after a drawdown.

The scanner is doing its job when it finds that premium. It is not doing my job
when it calls the premium an edge.

So I split the workflow into two layers.

Quant asks:

- What is the option paying?
- How far is the strike from spot?
- What are delta, IV, DTE, open interest, and the spread?
- Is implied volatility high relative to its own history and realized volatility?
- Why did this contract rank highly?

TIP asks:

- Would I accept assignment at strike minus premium?
- Does this add to an existing concentration?
- Is the earnings data current?
- What explains the premium?
- What is the failure point?
- Should the answer be a smaller trade, a lower strike, a defined-risk spread,
  or a skip?

That separation is the most important feature in the system.

## What the daily process looks like

The morning pipeline pulls balances, positions, quotes, option chains, Greeks,
price history, and earnings context from Schwab. It then:

1. Reviews existing short options by urgency and expiration.
2. Scans a broad, sector-mapped universe for covered calls and cash-secured
   puts.
3. Measures liquidity and execution quality instead of relying on theoretical
   mid prices.
4. Compares IV with local history and realized volatility.
5. Applies an earnings hard stop.
6. Reconciles raw rank with quality, portfolio fit, risk, and data confidence.
7. Writes a daily plan and a reviewable opportunity record.

The output is deliberately more useful than a list of “top trades.” Each idea
includes the machine’s reason, an assignment view, a failure point, an
alternative structure, and a decision label such as actionable, conditional,
watch, blocked, or skip.

The skip is a real output. It is not an error state.

## The edge is in the vetoes

I do not think the durable advantage comes from discovering one magical signal.
The advantage comes from making several small, repeatable decisions before a
trade gets promoted:

- Reject premium contaminated by a near-term earnings event.
- Penalize a candidate that increases an already-large sector or position
  exposure.
- Treat poor spreads and thin open interest as execution risk.
- Distinguish elevated IV from justified IV.
- Ask whether the underlying is worth owning, not merely whether the option is
  paying enough.
- Prefer a safer structure when the naked risk is not adequately compensated.

None of these rules is exciting on its own. Together they create a system that
is harder to fool with a large premium number.

That is the edge I am trying to build: better selectivity, better memory, and
fewer avoidable mistakes.

## Why local matters

The system runs locally with SQLite-backed state and scheduled macOS jobs. That
choice is practical, not ideological.

The account data, balances, positions, reports, and tokens stay outside the
public repository. The Git export contains the reusable code, examples, and
design—not personal account records.

Local execution also gives me a durable record of what the system saw and what
I decided. That matters because a trade idea without a timestamped rationale is
hard to evaluate honestly later.

## The part I am still measuring

I am not presenting this as proof of outperformance. A ranking system can look
smart in a backtest and still fail in live markets. A human review process can
also drift if it is not recorded.

So the system stores scanner ranks, TIP decisions, and eventual outcomes as
separate layers. That makes it possible to ask better questions over time:

- Did the underwriting improve selection beyond raw premium yield?
- Which failure points were predictable?
- When did high IV represent opportunity, and when did it represent danger?
- Did a trade improve the portfolio, or merely add another correlated bet?

Those are more valuable questions than “what had the highest yield this
morning?”

## The takeaway

I built Quant + TIP because I wanted a system that could do two things at once:

1. Search more broadly and consistently than I can by hand.
2. Remind me that a trade is only attractive if I can explain the downside and
   still want the position afterward.

The system’s job is not to manufacture confidence. Its job is to make the
decision clearer—and sometimes to make the skip obvious.

That is a better foundation for options research than a leaderboard of annualized
premiums.

The public, PII-free code and system overview are available in this repository.

*For research and educational purposes only. This is not investment advice, and
the system does not place orders automatically.*
