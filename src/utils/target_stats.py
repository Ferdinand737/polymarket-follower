#!/usr/bin/env python3
"""
Target Trader Stats - Comprehensive analytics for a Polymarket trader
Usage: python -m src.utils.target_stats [address]
"""

import requests
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from tabulate import tabulate
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from utils.utils import get_follow_address

# ============================================================================
# Data Fetching
# ============================================================================

def fetch_all_activities(address: str, days: int = 30) -> list:
    """Fetch all activities for the past N days."""
    all_activities = []
    start_ts = int((datetime.now() - timedelta(days=days)).timestamp())
    
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "limit": 500,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC",
        "start": start_ts
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    all_activities = response.json()
    
    print(f"📊 Fetched {len(all_activities)} activities from last {days} days")
    return all_activities


def fetch_positions(address: str) -> list:
    """Fetch current positions."""
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": address,
        "sizeThreshold": 1,
        "limit": 100,
        "sortBy": "TOKENS",
        "sortDirection": "DESC"
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    positions = response.json()
    
    print(f"📈 Fetched {len(positions)} positions")
    return positions


def fetch_portfolio_value(address: str) -> float:
    """Fetch total portfolio value."""
    url = "https://data-api.polymarket.com/value"
    params = {"user": address}
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    return data[0].get("value", 0) if data else 0


# ============================================================================
# Stats Calculations
# ============================================================================

def calculate_stats(activities: list, positions: list, portfolio_value: float) -> dict:
    """Calculate comprehensive statistics."""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    
    # Filter activities by type
    trades = [a for a in activities if a.get("type") == "TRADE"]
    buys = [a for a in trades if a.get("side") == "BUY"]
    sells = [a for a in trades if a.get("side") == "SELL"]
    splits = [a for a in activities if a.get("type") == "SPLIT"]
    merges = [a for a in activities if a.get("type") == "MERGE"]
    conversions = [a for a in activities if a.get("type") == "CONVERSION"]
    redeems = [a for a in activities if a.get("type") == "REDEEM"]
    
    # Time-based filtering
    def filter_by_time(items, start_time):
        return [a for a in items if datetime.fromtimestamp(a.get("timestamp", 0)) >= start_time]
    
    today_activities = filter_by_time(activities, today_start)
    today_trades = filter_by_time(trades, today_start)
    today_buys = filter_by_time(buys, today_start)
    today_sells = filter_by_time(sells, today_start)
    
    week_activities = filter_by_time(activities, week_start)
    week_trades = filter_by_time(trades, week_start)
    week_buys = filter_by_time(buys, week_start)
    week_sells = filter_by_time(sells, week_start)
    
    # Calculate bet sizes
    def get_usdc_sizes(items):
        return [a.get("usdcSize", 0) for a in items if a.get("usdcSize")]
    
    all_sizes = get_usdc_sizes(trades)
    today_sizes = get_usdc_sizes(today_trades)
    week_sizes = get_usdc_sizes(week_trades)
    
    # Daily breakdown for the past 7 days
    daily_stats = defaultdict(lambda: {"trades": 0, "volume": 0, "buys": 0, "sells": 0, "markets": set()})
    for activity in week_activities:
        ts = activity.get("timestamp", 0)
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        daily_stats[day]["trades"] += 1 if activity.get("type") == "TRADE" else 0
        daily_stats[day]["volume"] += activity.get("usdcSize", 0)
        if activity.get("side") == "BUY":
            daily_stats[day]["buys"] += 1
        elif activity.get("side") == "SELL":
            daily_stats[day]["sells"] += 1
        daily_stats[day]["markets"].add(activity.get("slug", ""))
    
    # Large bets (>= 5% of portfolio)
    large_bet_pct = 0.05
    large_bet_threshold = portfolio_value * large_bet_pct if portfolio_value else 1000
    large_bets_today = [a for a in today_trades if a.get("usdcSize", 0) >= large_bet_threshold]
    large_bets_week = [a for a in week_trades if a.get("usdcSize", 0) >= large_bet_threshold]
    
    # Unique markets
    today_markets = set(a.get("slug") for a in today_activities if a.get("slug"))
    week_markets = set(a.get("slug") for a in week_activities if a.get("slug"))
    all_markets = set(a.get("slug") for a in activities if a.get("slug"))
    
    # Positions analysis
    total_position_value = sum(p.get("currentValue", 0) for p in positions)
    avg_position_size = total_position_value / len(positions) if positions else 0
    largest_position = max(positions, key=lambda p: p.get("currentValue", 0)) if positions else None
    
    # Hourly activity pattern
    hourly_activity = defaultdict(int)
    for activity in activities:
        hour = datetime.fromtimestamp(activity.get("timestamp", 0)).hour
        hourly_activity[hour] += 1
    
    # Win rate estimation (based on redeems vs positions)
    
    return {
        "portfolio_value": portfolio_value,
        "total_position_value": total_position_value,
        
        # All-time stats
        "total_activities": len(activities),
        "total_trades": len(trades),
        "total_buys": len(buys),
        "total_sells": len(sells),
        "total_splits": len(splits),
        "total_merges": len(merges),
        "total_conversions": len(conversions),
        "total_redeems": len(redeems),
        "total_volume": sum(all_sizes),
        "avg_bet_size": sum(all_sizes) / len(all_sizes) if all_sizes else 0,
        "max_bet_size": max(all_sizes) if all_sizes else 0,
        "min_bet_size": min(all_sizes) if all_sizes else 0,
        "unique_markets": len(all_markets),
        
        # Today stats
        "today_activities": len(today_activities),
        "today_trades": len(today_trades),
        "today_buys": len(today_buys),
        "today_sells": len(today_sells),
        "today_volume": sum(today_sizes),
        "today_avg_bet": sum(today_sizes) / len(today_sizes) if today_sizes else 0,
        "today_max_bet": max(today_sizes) if today_sizes else 0,
        "today_large_bets": len(large_bets_today),
        "today_markets": len(today_markets),
        
        # Week stats
        "week_activities": len(week_activities),
        "week_trades": len(week_trades),
        "week_buys": len(week_buys),
        "week_sells": len(week_sells),
        "week_volume": sum(week_sizes),
        "week_avg_bet": sum(week_sizes) / len(week_sizes) if week_sizes else 0,
        "week_max_bet": max(week_sizes) if week_sizes else 0,
        "week_large_bets": len(large_bets_week),
        "week_markets": len(week_markets),
        
        # Averages
        "avg_daily_trades": len(week_trades) / 7,
        "avg_daily_volume": sum(week_sizes) / 7,
        "avg_daily_markets": len(week_markets) / 7,
        
        # Positions
        "num_positions": len(positions),
        "avg_position_size": avg_position_size,
        "largest_position": largest_position,
        
        # Patterns
        "daily_stats": dict(daily_stats),
        "hourly_activity": dict(hourly_activity),
        "large_bets_today": large_bets_today,
        "large_bets_week": large_bets_week,
        "large_bet_threshold": large_bet_threshold,
        "large_bet_pct": large_bet_pct,
        
        # Raw data for graphs
        "trades": trades,
        "today_trades_raw": today_trades,
        "week_trades_raw": week_trades,
    }


# ============================================================================
# Display Functions
# ============================================================================

def print_stats(stats: dict, address: str):
    """Print formatted statistics."""
    print("\n" + "=" * 80)
    print(f"📊 TARGET TRADER STATS: {address[:10]}...{address[-6:]}")
    print("=" * 80)
    
    # Portfolio Overview
    print("\n💰 PORTFOLIO OVERVIEW")
    print("-" * 40)
    portfolio_data = [
        ["Portfolio Value", f"${stats['portfolio_value']:,.2f}"],
        ["Total Position Value", f"${stats['total_position_value']:,.2f}"],
        ["Number of Positions", stats['num_positions']],
        ["Avg Position Size", f"${stats['avg_position_size']:,.2f}"],
    ]
    print(tabulate(portfolio_data, tablefmt="simple"))
    
    if stats['largest_position']:
        lp = stats['largest_position']
        print(f"\n  🏆 Largest Position: ${lp.get('currentValue', 0):,.2f} - {lp.get('title', 'N/A')[:50]}...")
    
    # Today Stats
    print("\n📅 TODAY'S ACTIVITY")
    print("-" * 40)
    today_data = [
        ["Total Trades", stats['today_trades']],
        ["Buys / Sells", f"{stats['today_buys']} / {stats['today_sells']}"],
        ["Volume", f"${stats['today_volume']:,.2f}"],
        ["Average Bet", f"${stats['today_avg_bet']:,.2f}"],
        ["Largest Bet", f"${stats['today_max_bet']:,.2f}"],
        [f"Bets ≥ {stats['large_bet_pct']*100:.0f}% (${stats['large_bet_threshold']:,.0f})", stats['today_large_bets']],
        ["Unique Markets", stats['today_markets']],
    ]
    print(tabulate(today_data, tablefmt="simple"))
    
    # Week Stats
    print("\n📆 THIS WEEK (7 days)")
    print("-" * 40)
    week_data = [
        ["Total Trades", stats['week_trades']],
        ["Buys / Sells", f"{stats['week_buys']} / {stats['week_sells']}"],
        ["Volume", f"${stats['week_volume']:,.2f}"],
        ["Average Bet", f"${stats['week_avg_bet']:,.2f}"],
        ["Largest Bet", f"${stats['week_max_bet']:,.2f}"],
        [f"Bets ≥ {stats['large_bet_pct']*100:.0f}% (${stats['large_bet_threshold']:,.0f})", stats['week_large_bets']],
        ["Unique Markets", stats['week_markets']],
    ]
    print(tabulate(week_data, tablefmt="simple"))
    
    # Daily Averages
    print("\n📈 DAILY AVERAGES (based on last 7 days)")
    print("-" * 40)
    avg_data = [
        ["Avg Daily Trades", f"{stats['avg_daily_trades']:.1f}"],
        ["Avg Daily Volume", f"${stats['avg_daily_volume']:,.2f}"],
        ["Avg Daily Markets", f"{stats['avg_daily_markets']:.1f}"],
        ["Overall Avg Bet Size", f"${stats['avg_bet_size']:,.2f}"],
    ]
    print(tabulate(avg_data, tablefmt="simple"))
    
    # Activity Breakdown
    print("\n🔄 ACTIVITY BREAKDOWN (all time)")
    print("-" * 40)
    activity_data = [
        ["Total Activities", stats['total_activities']],
        ["Trades", stats['total_trades']],
        ["Splits", stats['total_splits']],
        ["Merges", stats['total_merges']],
        ["Conversions", stats['total_conversions']],
        ["Redeems", stats['total_redeems']],
        ["Total Volume", f"${stats['total_volume']:,.2f}"],
        ["Unique Markets", stats['unique_markets']],
    ]
    print(tabulate(activity_data, tablefmt="simple"))
    
    # Daily Breakdown Table
    print("\n📊 DAILY BREAKDOWN (last 7 days)")
    print("-" * 40)
    daily_rows = []
    for day in sorted(stats['daily_stats'].keys(), reverse=True):
        d = stats['daily_stats'][day]
        daily_rows.append([
            day,
            d['trades'],
            f"{d['buys']}/{d['sells']}",
            f"${d['volume']:,.0f}",
            len(d['markets'])
        ])
    print(tabulate(daily_rows, headers=["Date", "Trades", "B/S", "Volume", "Markets"], tablefmt="simple"))
    
    # Peak Hours
    print("\n⏰ PEAK TRADING HOURS (UTC)")
    print("-" * 40)
    sorted_hours = sorted(stats['hourly_activity'].items(), key=lambda x: x[1], reverse=True)[:5]
    hour_rows = [[f"{h:02d}:00", count] for h, count in sorted_hours]
    print(tabulate(hour_rows, headers=["Hour", "Activities"], tablefmt="simple"))
    
    # Large Bets Today
    if stats['large_bets_today']:
        print(f"\n🐋 LARGE BETS TODAY (≥ {stats['large_bet_pct']*100:.0f}% / ${stats['large_bet_threshold']:,.0f}): {len(stats['large_bets_today'])}")
        print("-" * 40)
        for bet in stats['large_bets_today'][:5]:
            side = "🟢 BUY" if bet.get("side") == "BUY" else "🔴 SELL"
            print(f"  {side} ${bet.get('usdcSize', 0):,.2f} @ {bet.get('price', 0):.2f} - {bet.get('title', 'N/A')[:40]}...")
    
    print("\n" + "=" * 80)


def create_graphs(stats: dict, address: str):
    """Create visualization graphs."""
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(f"Target Trader Analytics: {address[:10]}...{address[-6:]}", fontsize=14, fontweight='bold')
    
    # 1. Daily Volume Bar Chart
    ax1 = axes[0, 0]
    daily_data = stats['daily_stats']
    dates = sorted(daily_data.keys())
    volumes = [daily_data[d]['volume'] for d in dates]
    colors = ['#2ecc71' if daily_data[d]['buys'] > daily_data[d]['sells'] else '#e74c3c' for d in dates]
    ax1.bar(dates, volumes, color=colors, alpha=0.8)
    ax1.set_title('Daily Trading Volume', fontweight='bold')
    ax1.set_ylabel('Volume ($)')
    ax1.tick_params(axis='x', rotation=45)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    # 2. Trade Count by Day
    ax2 = axes[0, 1]
    trades = [daily_data[d]['trades'] for d in dates]
    ax2.plot(dates, trades, marker='o', linewidth=2, color='#3498db', markersize=8)
    ax2.fill_between(dates, trades, alpha=0.3, color='#3498db')
    ax2.set_title('Daily Trade Count', fontweight='bold')
    ax2.set_ylabel('Number of Trades')
    ax2.tick_params(axis='x', rotation=45)
    
    # 3. Buy vs Sell Ratio (Pie Chart)
    ax3 = axes[0, 2]
    buy_sell_data = [stats['week_buys'], stats['week_sells']]
    if sum(buy_sell_data) > 0:
        ax3.pie(buy_sell_data, labels=['Buys', 'Sells'], autopct='%1.1f%%', 
                colors=['#2ecc71', '#e74c3c'], explode=(0.05, 0.05))
    ax3.set_title('Buy/Sell Ratio (This Week)', fontweight='bold')
    
    # 4. Hourly Activity Heatmap
    ax4 = axes[1, 0]
    hours = list(range(24))
    activity = [stats['hourly_activity'].get(h, 0) for h in hours]
    bars = ax4.bar(hours, activity, color='#9b59b6', alpha=0.8)
    ax4.set_title('Hourly Activity Pattern', fontweight='bold')
    ax4.set_xlabel('Hour (UTC)')
    ax4.set_ylabel('Activity Count')
    ax4.set_xticks(range(0, 24, 2))
    
    # 5. Bet Size Distribution
    ax5 = axes[1, 1]
    week_trades = stats['week_trades_raw']
    if week_trades:
        sizes = [t.get('usdcSize', 0) for t in week_trades if t.get('usdcSize')]
        if sizes:
            bins = [0, 100, 500, 1000, 2000, 4000, 10000, max(sizes) + 1]
            ax5.hist(sizes, bins=bins, color='#f39c12', alpha=0.8, edgecolor='black')
            ax5.set_title('Bet Size Distribution (This Week)', fontweight='bold')
            ax5.set_xlabel('Bet Size ($)')
            ax5.set_ylabel('Frequency')
            ax5.set_xscale('log')
    
    # 6. Cumulative Volume
    ax6 = axes[1, 2]
    if week_trades:
        sorted_trades = sorted(week_trades, key=lambda x: x.get('timestamp', 0))
        timestamps = [datetime.fromtimestamp(t.get('timestamp', 0)) for t in sorted_trades]
        cumulative = []
        total = 0
        for t in sorted_trades:
            total += t.get('usdcSize', 0)
            cumulative.append(total)
        if timestamps and cumulative:
            ax6.plot(timestamps, cumulative, linewidth=2, color='#1abc9c')
            ax6.fill_between(timestamps, cumulative, alpha=0.3, color='#1abc9c')
            ax6.set_title('Cumulative Volume (This Week)', fontweight='bold')
            ax6.set_ylabel('Cumulative Volume ($)')
            ax6.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
            ax6.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
            ax6.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    
    # Save and show
    filename = f"target_stats_{address[:8]}.png"
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    print(f"\n📈 Graph saved to: {filename}")
    plt.show()


# ============================================================================
# Main
# ============================================================================

def main():
    # Get address from command line or config
    if len(sys.argv) > 1:
        address = sys.argv[1].lower()
    else:
        try:
            address = get_follow_address()
            print(f"Using configured follow address: {address}")
        except Exception as e:
            print(f"Error: {e}")
            print("Usage: python -m src.utils.target_stats [address]")
            sys.exit(1)
    
    print(f"\n🔍 Analyzing trader: {address}")
    print("-" * 50)
    
    # Fetch data
    activities = fetch_all_activities(address, days=30)
    positions = fetch_positions(address)
    portfolio_value = fetch_portfolio_value(address)
    
    if not activities:
        print("No activities found for this address.")
        sys.exit(1)
    
    # Calculate stats
    stats = calculate_stats(activities, positions, portfolio_value)
    
    # Display
    print_stats(stats, address)
    
    # Generate graphs
    try:
        create_graphs(stats, address)
    except Exception as e:
        print(f"⚠️  Could not generate graphs: {e}")
        print("   (Make sure matplotlib is installed and you have a display available)")


if __name__ == "__main__":
    main()
