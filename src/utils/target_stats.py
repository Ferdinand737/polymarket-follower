#!/usr/bin/env python3
"""
Target Trader Stats - Position analysis with buy activity breakdown
Usage: python target_stats.py [address]
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from tabulate import tabulate
import matplotlib.pyplot as plt
from utils.utils import get_follow_address


# ============================================================================
# Data Fetching
# ============================================================================

def fetch_positions(address: str) -> list:
    """Fetch current positions sorted by current value."""
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": address,
        "sizeThreshold": 1,
        "limit": 10,
        "sortBy": "CURRENT",
        "sortDirection": "DESC"
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    positions = response.json()
    
    print(f"� Fetched {len(positions)} positions")
    return positions


def fetch_market_activities(address: str, condition_id: str) -> list:
    """Fetch all activities for a specific market (conditionId)."""
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "market": condition_id,
        "limit": 10000,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC"
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    activities = response.json()
    
    return activities


def fetch_portfolio_value(address: str) -> float:
    """Fetch total portfolio value."""
    url = "https://data-api.polymarket.com/value"
    params = {"user": address}
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    return data[0].get("value", 0) if data else 0


# ============================================================================
# Analysis
# ============================================================================

def analyze_position_buys(position: dict, address: str) -> dict:
    """Analyze buy activities for a specific position/market."""
    condition_id = position.get("conditionId")
    asset = position.get("asset")
    
    # Fetch activities for this market using the market parameter
    activities = fetch_market_activities(address, condition_id)
    
    # Filter for BUY trades on this specific asset
    buys = [
        a for a in activities
        if a.get("type") == "TRADE"
        and a.get("side") == "BUY"
        and a.get("asset") == asset
    ]
    
    if not buys:
        return {
            "conditionId": condition_id,
            "asset": asset,
            "num_buys": 0,
            "avg_buy_size": 0,
            "total_buy_volume": 0,
            "buy_sizes": [],
        }
    
    buy_sizes = [a.get("usdcSize", 0) for a in buys]
    
    return {
        "conditionId": condition_id,
        "asset": asset,
        "num_buys": len(buys),
        "avg_buy_size": sum(buy_sizes) / len(buy_sizes) if buy_sizes else 0,
        "total_buy_volume": sum(buy_sizes),
        "buy_sizes": buy_sizes,
    }


# ============================================================================
# Display
# ============================================================================

def print_position_stats(positions: list, portfolio_value: float, address: str):
    """Print position analysis with buy activity stats."""
    print("\n" + "=" * 100)
    print(f"📊 TARGET TRADER STATS: {address[:10]}...{address[-6:]}")
    print("=" * 100)
    
    print(f"\n💰 Portfolio Value: ${portfolio_value:,.2f}")
    print(f"📈 Total Positions: {len(positions)}")
    
    print(f"\n{'='*100}")
    print("🏆 TOP POSITIONS WITH BUY ACTIVITY ANALYSIS")
    print("=" * 100)
    
    rows = []
    for i, pos in enumerate(positions, 1):
        print(f"  Fetching activities for position {i}: {pos.get('title', 'N/A')[:30]}...")
        buy_stats = analyze_position_buys(pos, address)
        
        rows.append([
            i,
            pos.get("title", "N/A")[:40],
            f"${pos.get('currentValue', 0):,.2f}",
            f"{pos.get('size', 0):,.2f}",
            f"{pos.get('curPrice', 0):.2f}",
            buy_stats["num_buys"],
            f"${buy_stats['avg_buy_size']:,.2f}" if buy_stats['avg_buy_size'] > 0 else "-",
            f"${buy_stats['total_buy_volume']:,.2f}" if buy_stats['total_buy_volume'] > 0 else "-",
        ])
    
    print()
    print(tabulate(
        rows,
        headers=["#", "Market", "Value", "Size", "Price", "Buys", "Avg Buy", "Total Vol"],
        tablefmt="grid",
        maxcolwidths=[None, 40, None, None, None, None, None, None]
    ))
    
    return positions


def create_histograms(positions: list, address: str):
    """Create histograms of buy sizes for each position."""
    # Filter to positions with buy data
    positions_with_buys = []
    for pos in positions:
        buy_stats = analyze_position_buys(pos, address)
        if buy_stats["buy_sizes"]:
            positions_with_buys.append((pos, buy_stats))
    
    if not positions_with_buys:
        print("\n⚠️  No buy activity data to create histograms")
        return
    
    # Create figure with subplots
    n = len(positions_with_buys)
    cols = 2
    rows = (n + 1) // 2
    
    fig, axes = plt.subplots(rows, cols, figsize=(14, 4 * rows))
    fig.suptitle(f"Buy Size Distribution per Position: {address[:10]}...{address[-6:]}", 
                 fontsize=14, fontweight='bold')
    
    if rows == 1:
        axes = [axes] if cols == 1 else axes
    else:
        axes = axes.flatten()
    
    for idx, (pos, buy_stats) in enumerate(positions_with_buys):
        ax = axes[idx]
        sizes = buy_stats["buy_sizes"]
        
        # Create histogram
        ax.hist(sizes, bins=10, color='#3498db', alpha=0.7, edgecolor='black')
        ax.set_title(f"{pos.get('title', 'N/A')[:30]}...", fontsize=10)
        ax.set_xlabel('Buy Size ($)')
        ax.set_ylabel('Frequency')
        ax.axvline(buy_stats["avg_buy_size"], color='red', linestyle='--', 
                   label=f'Avg: ${buy_stats["avg_buy_size"]:,.2f}')
        ax.legend(fontsize=8)
        
        # Add stats text
        stats_text = f"Buys: {buy_stats['num_buys']} | Total: ${buy_stats['total_buy_volume']:,.0f}"
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=8,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # Hide empty subplots
    for idx in range(len(positions_with_buys), len(axes)):
        axes[idx].set_visible(False)
    
    plt.tight_layout()
    
    filename = f"target_stats_{address[:8]}.png"
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    print(f"\n📊 Histogram saved to: {filename}")
    plt.show()


# ============================================================================
# Main
# ============================================================================

def main():
    if len(sys.argv) > 1:
        address = sys.argv[1].lower()
    else:
        try:
            address = get_follow_address()
            print(f"Using configured follow address: {address}")
        except Exception as e:
            print(f"Error: {e}")
            print("Usage: python target_stats.py [address]")
            sys.exit(1)
    
    print(f"\n🔍 Analyzing trader: {address}")
    print("-" * 50)
    
    # Fetch positions
    positions = fetch_positions(address)
    portfolio_value = fetch_portfolio_value(address)
    
    if not positions:
        print("No positions found for this address.")
        sys.exit(1)
    
    # Display stats
    print_position_stats(positions, portfolio_value, address)
    
    # Generate histograms
    try:
        create_histograms(positions, address)
    except Exception as e:
        print(f"⚠️  Could not generate histograms: {e}")


if __name__ == "__main__":
    main()
