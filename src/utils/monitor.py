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


def fetch_positions(address: str):
    """Fetch current positions for an address."""
    url = "https://data-api.polymarket.com/positions"
    params = {"user": address}
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
        target_activities = fetch_activities(target_address, limit=30)
        follower_activities = fetch_activities(POLY_MARKET_FUNDER_ADDRESS, limit=30)
        
        # Fetch follower positions to check if we can sell
        follower_positions = fetch_positions(POLY_MARKET_FUNDER_ADDRESS)
        follower_condition_ids_with_position = {p.get('conditionId') for p in follower_positions if float(p.get('totalUsdc', 0)) > 0}
        
        # Filter for trades
        target_trades = [a for a in target_activities if a.get('type') == 'TRADE']
        follower_trades = [a for a in follower_activities if a.get('type') == 'TRADE']
        
        # Categorize target trades
        sells = [t for t in target_trades if t.get('side') == 'SELL']
        buys = [t for t in target_trades if t.get('side') == 'BUY']
        
        print(f"\n--- Target Recent Trades ---")
        for t in target_trades[:15]:
            usdc = t.get('usdcSize', 0)
            side = t.get('side', '?')
            title = t.get('title', '')[:40]
            above = "✓" if usdc >= min_order else "✗"
            print(f"  {above} ${usdc:>8.2f} | {side:4} | {title}")
        
        print(f"\n--- Follower Recent Trades ---")
        for t in follower_trades[:10]:
            usdc = t.get('usdcSize', 0)
            side = t.get('side', '?')
            title = t.get('title', '')[:40]
            print(f"  ${usdc:>8.2f} | {side:4} | {title}")
        
        # Analyze trades
        print(f"\n--- Trade Analysis ---")
        
        # SELLs: Only copy if we have the position
        sell_without_position = []
        sell_with_position = []
        for t in sells:
            cid = t.get('conditionId')
            usdc = t.get('usdcSize', 0)
            if cid in follower_condition_ids_with_position:
                sell_with_position.append(t)
            else:
                sell_without_position.append(t)
        
        if sell_without_position:
            print(f"\n  SELLS skipped (no position):")
            for t in sell_without_position[:5]:
                print(f"    ${t.get('usdcSize',0):>8.2f} | {t.get('title','')[:40]}")
        
        # BUYs: Check if above threshold
        buys_below_threshold = [t for t in buys if t.get('usdcSize', 0) < min_order]
        buys_above_threshold = [t for t in buys if t.get('usdcSize', 0) >= min_order]
        
        if buys_below_threshold:
            print(f"\n  BUYS skipped (below ${min_order:.0f} threshold):")
            for t in buys_below_threshold[:5]:
                print(f"    ${t.get('usdcSize',0):>8.2f} | {t.get('title','')[:40]}")
        
        # Check for missed trades (above threshold, should have been copied)
        follower_condition_ids = {t.get('conditionId') for t in follower_trades}
        missed = []
        for t in buys_above_threshold:
            cid = t.get('conditionId')
            if cid not in follower_condition_ids:
                missed.append(t)
        
        print(f"\n--- Sync Status ---")
        if missed:
            print(f"  ⚠️  MISSED {len(missed)} trades above threshold!")
            for t in missed[:5]:
                print(f"    ${t.get('usdcSize',0):>8.2f} | {t.get('title','')[:40]}")
        else:
            print("  ✓ No missed trades - all above-threshold BUYS have been copied")
        
        print(f"\n{'='*80}\n")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
