#!/usr/bin/env python3
"""
Portfolio viewer for Polymarket Follower Bot.
Shows current positions with market name, URL, USDC value, and percentage.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from follower.helpers import get_portfolio_usdc_value, get_on_chain_usdc_balance, POLY_MARKET_FUNDER_ADDRESS


def get_positions(address):
    resp = requests.get("https://data-api.polymarket.com/positions", params={"user": address}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    user = POLY_MARKET_FUNDER_ADDRESS
    
    portfolio_value = get_portfolio_usdc_value(user)
    cash = get_on_chain_usdc_balance(user)
    total = portfolio_value + cash
    
    positions = get_positions(user)
    
    # Calculate value for each position
    pos_data = []
    for p in positions:
        size = float(p.get('size', 0))
        price = float(p.get('curPrice', 0))
        value = size * price
        if value > 0.01:
            slug = p.get('slug', '')
            url = f"https://polymarket.com/event/{slug}" if slug else "N/A"
            pos_data.append({
                'title': p.get('title', 'Unknown'),
                'outcome': p.get('outcome', '?'),
                'url': url,
                'size': size,
                'price': price,
                'value': value,
                'pct': (value / total * 100) if total > 0 else 0,
            })
    
    pos_data.sort(key=lambda x: x['value'], reverse=True)
    
    print(f"{'='*70}")
    print(f"  Polymarket Portfolio — {user[:10]}...{user[-6:]}")
    print(f"{'='*70}")
    print(f"  Positions:  ${portfolio_value:>10,.2f}")
    print(f"  Cash:       ${cash:>10,.2f}")
    print(f"  Total:      ${total:>10,.2f}")
    print(f"{'='*70}")
    
    if pos_data:
        print(f"\n  {'Market':<45} {'Value':>8} {'Pct':>6}")
        print(f"  {'-'*45} {'-'*8} {'-'*6}")
        for p in pos_data:
            title = p['title'][:42] + "..." if len(p['title']) > 45 else p['title']
            print(f"  {title:<45} ${p['value']:>7.2f} {p['pct']:>5.1f}%")
            print(f"    {p['outcome']} | {p['size']:.2f} shares @ ${p['price']:.3f}")
            print(f"    {p['url']}")
            print()
    else:
        print("\n  No open positions.\n")
    
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
