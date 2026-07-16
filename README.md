# Schwab API Client for Options Trading

A Python client for the Charles Schwab Trader API with focus on options chain data and cash-secured put analysis for portfolio diversification.

For the system-level story—benefits, operational outcomes, and the edge created
by combining quantitative screening with portfolio-aware underwriting—see
[`QUANT_TIP_OVERVIEW.md`](QUANT_TIP_OVERVIEW.md).

## Features

- **OAuth2 Authentication**: Full OAuth2 flow with automatic token refresh
- **Option Chains**: Real-time option chains with Greeks (delta, gamma, theta, vega, IV)
- **Cash-Secured Put Scanner**: Find attractive put-selling opportunities
- **Account Data**: Positions, balances, orders, transactions
- **Price History**: Historical price data for equities

## Quick Start

### 1. Install Dependencies

```bash
cd ~/Documents/schwab-api
pip install -r requirements.txt
```

### 2. Configure Credentials

```bash
# Set environment variables
export SCHWAB_CLIENT_ID="your_client_id"
export SCHWAB_CLIENT_SECRET="your_client_secret"

# Or create credentials file
cp credentials.env.example ~/.schwab/credentials.env
# Edit the file with your credentials
```

### 3. Authenticate

First time setup - this opens browser for Schwab login:

```bash
python schwab_client.py auth --client-id YOUR_ID --client-secret YOUR_SECRET
```

Or in Python:

```python
from schwab_client import SchwabClient

client = SchwabClient('your_client_id', 'your_client_secret')
client.authenticate()  # Opens browser, prompts for redirect URL
```

### 4. Use the API

```python
from schwab_client import SchwabClient

client = SchwabClient('your_client_id', 'your_client_secret')

# Get real-time quote
quote = client.get_quote('RTX')
print(f"RTX: ${quote['quote']['lastPrice']}")

# Get option chain with Greeks
chain = client.get_option_chain('RTX', contract_type='PUT')

# As DataFrame for analysis
df = client.get_option_chain_dataframe('RTX', contract_type='PUT')
print(df[['symbol', 'strikePrice', 'bid', 'ask', 'delta', 'theta']].head(10))

# Find cash-secured put opportunities
puts = client.find_cash_secured_puts(
    'RTX',
    min_delta=-0.30,  # More ITM
    max_delta=-0.15,  # More OTM  
    min_days=30,
    max_days=60,
    min_premium_pct=1.0  # At least 1% premium
)
print(puts)

# Get account positions
accounts = client.get_accounts()
```

### 5. Run Diversification Scanner

Scan all your target stocks for put opportunities:

```bash
python scan_diversification.py
```

## API Reference

### Authentication

| Method | Description |
|--------|-------------|
| `authenticate()` | Complete OAuth2 flow (opens browser) |
| `refresh_access_token()` | Refresh access token using refresh token |
| `get_authorization_url()` | Get URL for manual authorization |

### Market Data

| Method | Description |
|--------|-------------|
| `get_quote(symbol)` | Get real-time quote |
| `get_quotes(symbols)` | Get quotes for multiple symbols |
| `get_option_chain(symbol, ...)` | Get option chain with Greeks |
| `get_option_chain_dataframe(symbol)` | Get chain as pandas DataFrame |
| `get_price_history(symbol, ...)` | Get historical prices |

### Account Data

| Method | Description |
|--------|-------------|
| `get_accounts()` | Get all accounts with positions |
| `get_account(account_hash)` | Get specific account |
| `get_account_numbers()` | Get account numbers and hashes |
| `get_orders(account_hash)` | Get orders |
| `get_transactions(account_hash)` | Get transactions |

### Analysis Tools

| Method | Description |
|--------|-------------|
| `find_cash_secured_puts(symbol, ...)` | Find put-selling opportunities |
| `analyze_put_opportunities(client, symbols)` | Scan multiple symbols |

## Token Management

- **Access Token**: Valid for 30 minutes, auto-refreshed
- **Refresh Token**: Valid for 7 days, then re-auth required
- **Token Storage**: `~/.schwab/tokens.json`

## Rate Limits

- Maximum 120 requests per minute
- Be mindful when scanning multiple symbols

## Troubleshooting

**"401 Unauthorized"**
- Token expired - will auto-refresh
- Refresh token expired (7 days) - run `authenticate()` again

**"Callback URL mismatch"**
- Callback URL in code must EXACTLY match Schwab Developer Portal config

**"App not approved"**
- New apps take 1-3 business days for Schwab approval

## License

For personal use only. Not affiliated with Charles Schwab.
