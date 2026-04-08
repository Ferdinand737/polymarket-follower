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

# Path to log file
LOG_FILE_PATH = Path(__file__).resolve().parent.parent.parent / "logs" / "polymarket_follower.log"

# Minimum USDC for a trade to be considered "big" and worth monitoring
MIN_TRADE_SIZE_USDC = 100.0


def get_bot_start_timestamp():
    """Get timestamp from the first log entry. Returns None if no log exists."""
    if not LOG_FILE_PATH.exists():
        return None
    
    with open(LOG_FILE_PATH, 'r') as f:
        first_line = f.readline().strip()
    
    if not first_line:
        return None
    
    # Parse timestamp from log format: [dd-monthname-yyyy-hh:mm:ss]
    # Example: [29-march-2026-12:07:14]
    try:
        # Extract the timestamp part between first [ and ]
        start = first_line.find('[')
        end = first_line.find(']')
        if start == -1 or end == -1:
            return None
        
        timestamp_str = first_line[start+1:end]
        # Parse format: dd-monthname-yyyy-hh:mm:ss
        dt = datetime.strptime(timestamp_str, "%d-%B-%Y-%H:%M:%S")
        return int(dt.timestamp())
    except (ValueError, IndexError):
        return None


def fetch_activities(address: str, limit: int = 50, after_ts: int = None):
    """Fetch recent activity for an address.
    
    Args:
        address: Wallet address
        limit: Max number of activities to fetch
        after_ts: Unix timestamp to filter activities after this time
    """
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "limit": limit,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC"
    }
    if after_ts:
        params["after"] = after_ts
    
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


def fetch_log_entries(search_terms: list, context_lines: int = 3):
    """Fetch log entries matching any of the search terms with context."""
    if not LOG_FILE_PATH.exists():
        return []
    
    results = []
    with open(LOG_FILE_PATH, 'r') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        for term in search_terms:
            if term.lower() in line.lower():
                # Get context lines around the match
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                results.append((i + 1, lines[start:end]))  # 1-indexed line number
                break
    
    return results


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
        
        # Get bot start time from log
        bot_start_ts = get_bot_start_timestamp()
        if bot_start_ts:
            bot_start_dt = datetime.fromtimestamp(bot_start_ts)
            print(f"Bot started: {bot_start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print("Bot start time: Unknown (no log file)")
        
        # Fetch recent activities
        print(f"\nFetching recent activities...")
        all_target_activities = fetch_activities(target_address, limit=30, after_ts=bot_start_ts)
        all_follower_activities = fetch_activities(POLY_MARKET_FUNDER_ADDRESS, limit=30, after_ts=bot_start_ts)
        
        # Filter activities to only include those after bot start (in case API filter didn't work)
        if bot_start_ts:
            target_activities = [a for a in all_target_activities if a.get('timestamp', 0) > bot_start_ts]
            follower_activities = [a for a in all_follower_activities if a.get('timestamp', 0) > bot_start_ts]
            # Show how many were filtered
            if len(target_activities) < len(all_target_activities):
                print(f"  Filtered out {len(all_target_activities) - len(target_activities)} target activities from before bot start")
            if len(follower_activities) < len(all_follower_activities):
                print(f"  Filtered out {len(all_follower_activities) - len(follower_activities)} follower activities from before bot start")
        else:
            target_activities = all_target_activities
            follower_activities = all_follower_activities
        
        # Fetch positions for both users
        follower_positions = fetch_positions(POLY_MARKET_FUNDER_ADDRESS)
        target_positions = fetch_positions(target_address)
        follower_condition_ids_with_position = {p.get('conditionId') for p in follower_positions if float(p.get('totalUsdc', 0)) > 0}

        # Calculate portfolio values for allocation comparison
        user_portfolio_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS) + get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
        target_portfolio_value = get_portfolio_usdc_value(target_address)
        
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
            
            # Fetch relevant log entries for missed trades
            missed_titles = [t.get('title', '')[:40] for t in missed[:5]]
            log_entries = fetch_log_entries(missed_titles)
            if log_entries:
                print(f"\n  Relevant log entries:")
                for line_num, lines in log_entries[:5]:
                    for line in lines:
                        print(f"    {line.rstrip()}")
                    print()
        else:
            print("  ✓ No missed trades - all above-threshold BUYS have been copied")

        # TODO: Allocation comparison disabled - need better solution for idle USDC
        # The follower account has idle USDC that skews allocation percentages
        # Need to either: track invested portfolio separately, or auto-allocate USDC
        
        # # Compare allocation percentages for shared positions
        # print(f"\n--- Allocation Comparison ---")
        # 
        # # Build dicts of positions by conditionId
        # target_pos_by_cid = {p.get('conditionId'): p for p in target_positions if float(p.get('currentValue', 0)) > 0}
        # follower_pos_by_cid = {p.get('conditionId'): p for p in follower_positions if float(p.get('currentValue', 0)) > 0}
        # 
        # # Find shared positions
        # shared_cids = set(target_pos_by_cid.keys()) & set(follower_pos_by_cid.keys())
        # 
        # allocation_issues = []
        # for cid in shared_cids:
        #     target_pos = target_pos_by_cid[cid]
        #     follower_pos = follower_pos_by_cid[cid]
        # 
        #     target_usdc = float(target_pos.get('currentValue', 0))
        #     follower_usdc = float(follower_pos.get('currentValue', 0))
        # 
        #     # Calculate allocation percentages
        #     target_alloc_pct = (target_usdc / target_portfolio_value) * 100 if target_portfolio_value > 0 else 0
        #     follower_alloc_pct = (follower_usdc / user_portfolio_value) * 100 if user_portfolio_value > 0 else 0
        # 
        #     alloc_diff = abs(target_alloc_pct - follower_alloc_pct)
        # 
        #     if alloc_diff > 2.0:
        #         allocation_issues.append({
        #             'conditionId': cid,
        #             'title': target_pos.get('title', 'Unknown'),
        #             'target_usdc': target_usdc,
        #             'follower_usdc': follower_usdc,
        #             'target_alloc': target_alloc_pct,
        #             'follower_alloc': follower_alloc_pct,
        #             'diff': alloc_diff
        #         })
        # 
        # if allocation_issues:
        #     print(f"  ⚠️  {len(allocation_issues)} positions with >2% allocation difference:")
        #     for issue in allocation_issues[:10]:
        #         print(f"    {issue['title'][:35]}")
        #         print(f"      Target: ${issue['target_usdc']:,.0f} ({issue['target_alloc']:.1f}%) | "
        #               f"Follower: ${issue['follower_usdc']:,.0f} ({issue['follower_alloc']:.1f}%) | "
        #               f"Diff: {issue['diff']:.1f}%")
        #     
        #     # Fetch relevant log entries for allocation issues
        #     issue_titles = [issue['title'] for issue in allocation_issues[:5]]
        #     log_entries = fetch_log_entries(issue_titles)
        #     if log_entries:
        #         print(f"\n  Relevant log entries:")
        #         for line_num, lines in log_entries[:5]:
        #             for line in lines:
        #                 print(f"    {line.rstrip()}")
        #             print()
        # else:
        #     print(f"  ✓ All {len(shared_cids)} shared positions within 2% allocation tolerance")

        print(f"\n{'='*80}\n")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
