"""
Token Manager - Handles Schwab OAuth token refresh
"""

import httpx
import json
import base64
import os
from pathlib import Path
from datetime import datetime, timedelta

APP_KEY = os.environ.get("SCHWAB_CLIENT_ID", "")
APP_SECRET = os.environ.get("SCHWAB_CLIENT_SECRET", "")
TOKEN_PATH = Path.home() / ".schwab" / "tokens.json"

def load_tokens() -> dict:
    """Load tokens from file"""
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH) as f:
            return json.load(f)
    return {}

def save_tokens(tokens: dict):
    """Save tokens to file"""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, 'w') as f:
        json.dump(tokens, f, indent=2)

def is_token_expired(tokens: dict) -> bool:
    """Check if access token is expired or about to expire"""
    expires_at = tokens.get('expires_at')
    if not expires_at:
        return True
    
    try:
        expiry = datetime.fromisoformat(expires_at)
        # Consider expired if less than 2 minutes remaining
        return datetime.now() > expiry - timedelta(minutes=2)
    except:
        return True

def refresh_token() -> bool:
    """Refresh the access token using refresh token"""
    tokens = load_tokens()
    refresh_tok = tokens.get('refresh_token')
    
    if not refresh_tok:
        print("❌ No refresh token available")
        return False
    
    auth_string = base64.b64encode(f"{APP_KEY}:{APP_SECRET}".encode()).decode()
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                'https://api.schwabapi.com/v1/oauth/token',
                headers={
                    'Authorization': f'Basic {auth_string}',
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_tok
                }
            )
            
            if resp.status_code == 200:
                new_tokens = resp.json()
                
                tokens['access_token'] = new_tokens['access_token']
                if 'refresh_token' in new_tokens:
                    tokens['refresh_token'] = new_tokens['refresh_token']
                tokens['expires_at'] = (
                    datetime.now() + timedelta(seconds=new_tokens.get('expires_in', 1800))
                ).isoformat()
                
                save_tokens(tokens)
                print(f"✅ Token refreshed, expires at {tokens['expires_at']}")
                return True
            else:
                print(f"❌ Token refresh failed: {resp.status_code}")
                return False
    except Exception as e:
        print(f"❌ Token refresh error: {e}")
        return False

def get_valid_token() -> str:
    """Get a valid access token, refreshing if necessary"""
    tokens = load_tokens()
    
    if is_token_expired(tokens):
        print("🔄 Token expired, refreshing...")
        if not refresh_token():
            raise Exception("Failed to refresh token. Please re-authenticate.")
        tokens = load_tokens()
    
    return tokens.get('access_token', '')

def get_headers() -> dict:
    """Get headers with valid access token"""
    token = get_valid_token()
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }


if __name__ == '__main__':
    print("Testing token manager...")
    tokens = load_tokens()
    print(f"Token expired: {is_token_expired(tokens)}")
    
    token = get_valid_token()
    print(f"Got valid token: {token[:20]}...")
