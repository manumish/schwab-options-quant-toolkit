#!/usr/bin/env python3
"""
Schwab API - Portfolio Diversification Scanner
==============================================

Scans for cash-secured put opportunities in recession-resistant sectors
to help reduce tech concentration while generating income.

Target Sectors:
- Defense: RTX, LMT, GD, NOC, BA
- Pharma: PFE, JNJ, MRK, LLY, ABBV
- Nuclear/Energy: NEE, D, DUK, SO, CEG
- Healthcare: UNH, CVS, HCA
"""

import os
from schwab_client import SchwabClient, analyze_put_opportunities

# =========================================
# CONFIGURATION - Update with your credentials
# =========================================

# Your Schwab API credentials
# export SCHWAB_CLIENT_ID="your_client_id"
# export SCHWAB_CLIENT_SECRET="your_client_secret"
CLIENT_ID = os.environ.get("SCHWAB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SCHWAB_CLIENT_SECRET", "")

# Diversification target symbols
DEFENSE_STOCKS = ['RTX', 'LMT', 'GD', 'NOC']
PHARMA_STOCKS = ['PFE', 'JNJ', 'MRK', 'LLY', 'ABBV']
NUCLEAR_ENERGY = ['NEE', 'CEG', 'VST']  # CEG = Constellation, VST = Vistra
HEALTHCARE = ['UNH', 'CVS', 'HCA']

ALL_TARGETS = DEFENSE_STOCKS + PHARMA_STOCKS + NUCLEAR_ENERGY + HEALTHCARE


def main():
    print("="*60)
    print("SCHWAB OPTIONS SCANNER - Portfolio Diversification")
    print("="*60)
    
    # Initialize client
    client = SchwabClient(CLIENT_ID, CLIENT_SECRET)
    
    # Check if authenticated
    if not client.access_token:
        print("\n⚠ Not authenticated. Running authentication flow...")
        client.authenticate()
    
    print("\n" + "-"*60)
    print("STEP 1: Account Overview")
    print("-"*60)
    
    try:
        accounts = client.get_accounts()
        for acc in accounts:
            sec_acc = acc.get('securitiesAccount', {})
            balance = sec_acc.get('currentBalances', {})
            print(f"\nAccount: {sec_acc.get('accountNumber', 'N/A')}")
            print(f"  Cash Available: ${balance.get('cashAvailableForTrading', 0):,.2f}")
            print(f"  Buying Power: ${balance.get('buyingPower', 0):,.2f}")
    except Exception as e:
        print(f"Could not fetch accounts: {e}")
    
    print("\n" + "-"*60)
    print("STEP 2: Scanning Cash-Secured Put Opportunities")
    print("-"*60)
    print(f"\nTargets: {', '.join(ALL_TARGETS)}")
    print("Criteria: Delta -0.30 to -0.15, 30-60 DTE, min 1% premium")
    
    # Scan all targets
    opportunities = analyze_put_opportunities(client, ALL_TARGETS)
    
    if not opportunities.empty:
        print("\n" + "="*60)
        print("TOP 15 PUT OPPORTUNITIES BY ANNUALIZED YIELD")
        print("="*60)
        
        top_15 = opportunities.head(15)
        display_cols = ['underlying', 'strikePrice', 'expDate', 'daysToExpiration',
                       'bid', 'ask', 'delta', 'premiumPct', 'annualizedYield', 'cashRequired']
        available_cols = [c for c in display_cols if c in top_15.columns]
        
        print(top_15[available_cols].to_string(index=False))
        
        # Summary by sector
        print("\n" + "-"*60)
        print("SUMMARY BY SECTOR")
        print("-"*60)
        
        opportunities['sector'] = opportunities['underlying'].apply(
            lambda x: 'Defense' if x in DEFENSE_STOCKS 
                     else 'Pharma' if x in PHARMA_STOCKS
                     else 'Nuclear/Energy' if x in NUCLEAR_ENERGY
                     else 'Healthcare'
        )
        
        for sector, group in opportunities.groupby('sector'):
            avg_yield = group['annualizedYield'].mean()
            count = len(group)
            print(f"  {sector}: {count} options, avg yield {avg_yield:.1f}%")
    else:
        print("\nNo opportunities found matching criteria")
    
    print("\n" + "="*60)
    print("DONE!")
    print("="*60)


if __name__ == '__main__':
    main()
