#!/usr/bin/env python3
"""
Quick Start - Schwab API Authentication
Run this after your app is approved (status: "Ready for Use")
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schwab_client import SchwabClient

# Your credentials
CLIENT_ID = os.environ.get("SCHWAB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SCHWAB_CLIENT_SECRET", "")
CALLBACK_URL = "https://127.0.0.1"

def main():
    print("="*60)
    print("SCHWAB API - Quick Start")
    print("="*60)
    
    # Check if app is likely approved
    print("\n⚠️  IMPORTANT: Your app must show 'Ready for Use' status")
    print("   in the Schwab Developer Portal before this will work.")
    print("   New apps take 1-3 business days for approval.\n")
    
    response = input("Is your app approved and 'Ready for Use'? (y/n): ").strip().lower()
    
    if response != 'y':
        print("\n📋 Next steps:")
        print("   1. Check developer.schwab.com for app status")
        print("   2. Wait for 'Ready for Use' status")
        print("   3. Run this script again")
        return
    
    # Create client and authenticate
    print("\n🔐 Initializing Schwab client...")
    client = SchwabClient(CLIENT_ID, CLIENT_SECRET, CALLBACK_URL)
    
    # Check if we already have valid tokens
    if client.access_token and not client._is_token_expired():
        print("✓ Already authenticated! Tokens loaded from disk.")
        print("\n📊 Testing API connection...")
        
        try:
            # Test with a simple quote
            quote = client.get_quote('SPY')
            price = quote.get('quote', {}).get('lastPrice', 'N/A')
            print(f"   SPY Last Price: ${price}")
            print("\n✅ API is working! You're ready to go.")
        except Exception as e:
            print(f"   API test failed: {e}")
            print("   You may need to re-authenticate.")
    else:
        # Need to authenticate
        print("\n🌐 Starting OAuth authentication flow...")
        client.authenticate()
        
        print("\n📊 Testing API connection...")
        try:
            quote = client.get_quote('SPY')
            price = quote.get('quote', {}).get('lastPrice', 'N/A')
            print(f"   SPY Last Price: ${price}")
            print("\n✅ Authentication successful! You're ready to go.")
        except Exception as e:
            print(f"   API test failed: {e}")
    
    print("\n" + "="*60)
    print("NEXT STEPS")
    print("="*60)
    print("""
    1. Run the diversification scanner:
       python scan_diversification.py
    
    2. Or use interactively in Python:
       from quick_start import get_client
       client = get_client()
       puts = client.find_cash_secured_puts('RTX')
       print(puts)
    """)


def get_client():
    """Get an authenticated client for interactive use"""
    client = SchwabClient(CLIENT_ID, CLIENT_SECRET, CALLBACK_URL)
    if not client.access_token:
        print("Not authenticated. Run: python quick_start.py")
        return None
    return client


if __name__ == '__main__':
    main()
