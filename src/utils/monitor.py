#!/usr/bin/env python3
"""
Monitor script for Polymarket Follower Bot
Checks if follower is correctly copying target's big trades.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from datetime import datetime
from follower.helpers import get_portfolio_usdc_value, get_on_chain_usdc_balance, POLY_MARKET_FUNDER_ADDRESS
from utils.utils import get_follow_address

# Minimum USDC for a trade to be considered "big" and worth monitoring
MIN_TRADE_SIZE_USDC = 100.0


def fetch_activities(address: str, limit: int = 50):
    """Fetch recent activity for an address."""
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "limit": limit,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC"
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def calculate_min_target_order():
    """Calculate minimum target order size for follower to copy."""
    target_address = get_follow_address()
    user_portfolio = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS) + get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
    target_portfolio = get_portfolio_usdc_value(target_address)
    return 1.0 * target_portfolio / user_portfolio


def main():
    print(f"\n{'='*80}")
    print(f"Polymarket Follower Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    
    try:
        target_address = get_follow_address()
        min_order = calculate_min_target_order()
        
        print(f"\nTarget: {target_address}")
        print(f"Follower: {POLY_MARKET_FUNDER_ADDRESS}")
        print(f"Min target order to copy: ${min_order:,.2f}")
        
        # Fetch recent activities
        print(f"\nFetching recent activities...")
        target_activities = fetch_activities(target_address, limit=20)
        follower_activities = fetch_activities(POLY_MARKET_FUNDER_ADDRESS, limit=20)
        
        # Filter for big trades
        target_big_trades = [a for a in target_activities 
                           if a.get('type') == 'TRADE' 
                           and a.get('usdcSize', 0) >= MIN_TRADE_SIZE_USDC]
        
        follower_trades = [a for a in follower_activities if a.get('type') == 'TRADE']
        
        print(f"\n--- Target Big Trades (>${MIN_TRADE_SIZE_USDC}) ---")
        if target_big_trades:
            for t in target_big_trades[:10]:
                print(f"  ${t.get('usdcSize',0):>8.2f} | {t.get('side', '?'):4} | {t.get('title','')[:50]}")
        else:
            print("  No big trades found")
        
        print(f"\n--- Follower Recent Trades ---")
        if follower_trades:
            for t in follower_trades[:10]:
                print(f"  ${t.get('usdcSize',0):>8.2f} | {t.get('side', '?'):4} | {t.get('title','')[:50]}")
        else:
            print("  No recent trades")
        
        # Check for missed big trades
        print(f"\n--- Trade Sync Check ---")
        if target_big_trades:
            # Get market IDs from follower trades
            follower_condition_ids = {t.get('conditionId') for t in follower_trades}
            
            missed = []
            for t in target_big_trades:
                cid = t.get('conditionId')
                usdc = t.get('usdcSize', 0)
                # If trade is above our minimum copy threshold and we haven't traded it
                if usdc >= min_order and cid not in follower_condition_ids:
                    missed.append(t)
            
            if missed:
                print(f"  ⚠️  MISSED {len(missed)} big trades above copy threshold!")
                for t in missed[:5]:
                    print(f"    ${t.get('usdcSize',0):>8.2f} | {t.get('title','')[:50]}")
            else:
                print("  ✓ All big trades above threshold have been copied (or below minimum)")
        else:
            print("  ✓ No big trades to check")
        
        print(f"\n{'='*80}\n")
        
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
