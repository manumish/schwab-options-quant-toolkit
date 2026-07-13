# 🎯 Smart Options Trading Scanner - Design Document

## Overview

A continuously running application that monitors your portfolio and watchlist, scanning for high-probability trading opportunities based on IV, price action, and technical signals. Delivers real-time alerts via multiple channels.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SMART OPTIONS SCANNER                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │   SCHWAB     │    │   SCANNER    │    │    ALERT     │              │
│  │     API      │───▶│    ENGINE    │───▶│   DELIVERY   │              │
│  │   (Data)     │    │   (Logic)    │    │  (Notify)    │              │
│  └──────────────┘    └──────────────┘    └──────────────┘              │
│         │                   │                   │                       │
│         ▼                   ▼                   ▼                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │ - Quotes     │    │ - IV Spikes  │    │ - SMS/Text   │              │
│  │ - Chains     │    │ - Price Dips │    │ - Email      │              │
│  │ - Account    │    │ - Support    │    │ - Desktop    │              │
│  │ - History    │    │ - Greeks     │    │ - Dashboard  │              │
│  └──────────────┘    └──────────────┘    └──────────────┘              │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      WEB DASHBOARD                               │   │
│  │  - Live portfolio view         - Alert history                  │   │
│  │  - Current opportunities       - Performance tracking           │   │
│  │  - One-click trade prep        - Risk metrics                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📊 Scanning Strategies

### 1. **IV SPIKE DETECTOR** (Sell Puts)
```
Trigger: IV jumps >20% from 20-day average
         AND price drops >3% from recent high
         AND IV > 45%

Alert: "🔥 VST IV spiked to 65% (avg: 48%), down 7% from high. 
        Sell $145 Mar puts @ $8.50 for 52% annualized."
```

### 2. **SUPPORT BOUNCE** (Sell Puts)
```
Trigger: Price within 2% of 50-day MA or prior support
         AND RSI < 35 (oversold)
         AND IV elevated (>40%)

Alert: "📉 NVDA hit 50-day MA support at $185. RSI: 32.
        Sell $180 puts @ $6.20 for 38% annualized."
```

### 3. **RALLY EXHAUSTION** (Sell Calls on Holdings)
```
Trigger: Price up >8% in 5 days
         AND RSI > 70 (overbought)
         AND approaching resistance

Alert: "📈 ORCL rallied 12% to resistance at $165. RSI: 74.
        Sell $170 Mar calls on your 1820 shares @ $8.70."
```

### 4. **PREMIUM RICH** (Best Risk/Reward)
```
Trigger: Annualized premium > 30%
         AND delta between -0.25 and -0.35
         AND bid-ask spread < 15%
         AND open interest > 100

Alert: "💰 High-quality put found: CEG $250 Mar put
        32% annualized, delta -0.28, tight spread, liquid."
```

### 5. **EARNINGS PLAY** (Pre-earnings IV crush)
```
Trigger: Earnings in 7-14 days
         AND IV rank > 80%
         AND not a current holding (avoid assignment risk)

Alert: "📅 AMZN earnings Feb 15. IV rank 85%.
        Consider iron condor or strangle to capture IV crush."
```

### 6. **ASSIGNMENT RISK** (Protect current positions)
```
Trigger: Short put approaching strike (within 3%)
         AND DTE < 7
         
Alert: "⚠️ Your VST $145 put is ITM with stock at $143.
        Consider rolling to Apr $140 for $2.50 credit."
```

### 7. **PORTFOLIO RISK** (Concentration warning)
```
Trigger: Single position > 30% of portfolio
         OR Tech sector > 85%
         OR Margin utilization > 60%

Alert: "🚨 Portfolio check: Tech now 92% of holdings.
        NVDA alone is 38%. Consider trimming or hedging."
```

---

## 🔔 Alert Channels

### SMS/Text (Twilio)
- Instant alerts for high-priority signals
- Short, actionable messages
- One alert per opportunity (no spam)

### Email (Daily Digest)
- Morning summary: overnight movers, day's opportunities
- Evening recap: what triggered, what you missed
- Weekly performance review

### Desktop Notifications (macOS)
- Real-time popups during market hours
- Click to open dashboard with details

### Web Dashboard
- Live updating opportunity board
- Click to see full analysis
- "Prepare Trade" button (copies order details)

---

## 📱 Dashboard Features

### Main View
```
┌─────────────────────────────────────────────────────────────────┐
│  SMART SCANNER DASHBOARD                    Market: OPEN 🟢    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  🔥 LIVE OPPORTUNITIES (3)                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ SELL PUT  VST $145 Mar20  │ 48% Ann │ IV: 62% │ ⭐⭐⭐⭐⭐  │   │
│  │ SELL CALL NVDA $220 Mar20 │ 18% Ann │ IV: 52% │ ⭐⭐⭐⭐   │   │
│  │ SELL PUT  UNH $270 Mar13  │ 26% Ann │ IV: 36% │ ⭐⭐⭐    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  📊 YOUR POSITIONS                                              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ NVDA  2106 sh  +523%  │ No calls written │ SELL CALLS? │   │
│  │ ORCL  1820 sh  +11%   │ No calls written │ WAIT        │   │
│  │ Short VST $145P       │ 18 DTE │ Safe    │ MONITOR     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  📈 WATCHLIST IV MONITOR                                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Symbol │ Price  │ Chg   │ IV   │ IV Rank │ Signal      │   │
│  │ VST    │ $154   │ +3.0% │ 58%  │ 72%     │ 🔥 SELL PUT │   │
│  │ CEG    │ $271   │ +3.6% │ 58%  │ 68%     │ 🔥 SELL PUT │   │
│  │ RTX    │ $198   │ -0.2% │ 29%  │ 25%     │ 😴 WAIT     │   │
│  │ UNH    │ $280   │ +1.1% │ 36%  │ 45%     │ 📊 NEUTRAL  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ⚠️ RISK ALERTS                                                 │
│  • Tech concentration: 91% (target: 85%)                       │
│  • Margin used: 12% of available                               │
│  • Largest position: NVDA 38%                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

### Backend
- **Python 3.11+** - Main scanner engine
- **FastAPI** - REST API for dashboard
- **SQLite/PostgreSQL** - Store alerts, history, performance
- **APScheduler** - Run scans every 5 minutes during market hours
- **Redis** (optional) - Cache quotes, rate limiting

### Frontend
- **React + Tailwind** - Web dashboard
- **Recharts** - IV charts, performance graphs
- **WebSocket** - Real-time updates

### Alerts
- **Twilio** - SMS alerts ($0.01/message)
- **SendGrid** - Email digests
- **macOS notifications** - osascript for desktop

### Deployment
- **Local Mac** - Run as background service (launchd)
- **OR Cloud** - Railway/Render for 24/7 (with webhook alerts)

---

## 📁 Project Structure

```
schwab-scanner/
├── scanner/
│   ├── __init__.py
│   ├── schwab_client.py      # API client (already built!)
│   ├── strategies/
│   │   ├── iv_spike.py       # IV spike detector
│   │   ├── support_bounce.py # Technical support scanner
│   │   ├── rally_exhaustion.py
│   │   ├── premium_rich.py
│   │   └── assignment_risk.py
│   ├── alerts/
│   │   ├── sms.py            # Twilio integration
│   │   ├── email.py          # SendGrid integration
│   │   └── desktop.py        # macOS notifications
│   ├── models.py             # Data models
│   ├── database.py           # SQLite storage
│   └── scheduler.py          # APScheduler setup
├── api/
│   ├── main.py               # FastAPI app
│   ├── routes/
│   │   ├── opportunities.py
│   │   ├── positions.py
│   │   └── alerts.py
│   └── websocket.py          # Real-time updates
├── dashboard/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── OpportunityCard.jsx
│   │   │   ├── PositionTable.jsx
│   │   │   ├── IVChart.jsx
│   │   │   └── AlertFeed.jsx
│   │   └── hooks/
│   │       └── useWebSocket.js
│   └── package.json
├── config.yaml               # Watchlist, thresholds, alert settings
├── requirements.txt
└── README.md
```

---

## ⚙️ Configuration

```yaml
# config.yaml

watchlist:
  # Your positions
  positions:
    - NVDA
    - ORCL
    - AMD
    - TSLA
    - AMZN
    - MSFT
    - INTC
    - CRDO
  
  # Diversification targets
  targets:
    - VST   # Nuclear
    - CEG   # Nuclear
    - UNH   # Healthcare
    - RTX   # Defense
    - LMT   # Defense
    - PFE   # Pharma
    - JNJ   # Pharma

scanning:
  interval_minutes: 5
  market_hours_only: true
  
thresholds:
  iv_spike:
    min_iv: 45
    iv_increase_pct: 20
    price_drop_pct: 3
  
  support_bounce:
    ma_proximity_pct: 2
    rsi_oversold: 35
    min_iv: 35
  
  premium_rich:
    min_annualized: 25
    max_delta: -0.35
    min_delta: -0.20
    max_spread_pct: 15
    min_open_interest: 100

alerts:
  sms:
    enabled: true
    phone: "+1XXXXXXXXXX"
    priority_only: true  # Only 5-star opportunities
  
  email:
    enabled: true
    address: "user@example.com"
    daily_digest: "07:00"
    weekly_recap: "sunday 18:00"
  
  desktop:
    enabled: true
    sound: true

risk_limits:
  max_tech_pct: 85
  max_single_position_pct: 30
  max_margin_utilization_pct: 50
```

---

## 🚀 MVP Phases

### Phase 1: Core Scanner (Week 1)
- [x] Schwab API client ✅ (already built)
- [ ] IV spike detector
- [ ] Premium quality scanner
- [ ] Desktop notifications (macOS)
- [ ] SQLite storage for alerts

### Phase 2: More Strategies (Week 2)
- [ ] Support/resistance detection
- [ ] RSI overbought/oversold
- [ ] Assignment risk monitor
- [ ] Position concentration alerts

### Phase 3: Dashboard (Week 3)
- [ ] FastAPI backend
- [ ] React dashboard
- [ ] Real-time WebSocket updates
- [ ] Alert history view

### Phase 4: Alerts & Polish (Week 4)
- [ ] SMS via Twilio
- [ ] Email digests
- [ ] Performance tracking
- [ ] Mobile-friendly dashboard

---

## 💡 Smart Features (Future)

1. **Machine Learning IV Prediction**
   - Train on historical IV patterns
   - Predict IV spikes before they happen

2. **Earnings Calendar Integration**
   - Auto-detect upcoming earnings
   - Suggest pre-earnings strategies

3. **Backtesting Engine**
   - Test strategies on historical data
   - Optimize thresholds

4. **Voice Alerts**
   - "Hey Siri, any trading opportunities?"
   - Shortcut integration

5. **Auto-Trade Preparation**
   - Pre-fill Schwab order form
   - One-click to trade screen

---

## 💰 Cost Estimate

| Service | Cost |
|---------|------|
| Twilio SMS | ~$5/month (500 alerts) |
| SendGrid Email | Free tier (100/day) |
| Hosting (optional) | $0 (run locally) or $7/mo |
| Domain (optional) | $12/year |
| **Total** | **~$5-12/month** |

---

## Ready to Build?

Let's start with Phase 1 - the core scanner with desktop notifications!
