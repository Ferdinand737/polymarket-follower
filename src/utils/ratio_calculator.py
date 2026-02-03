#!/usr/bin/env python3
"""
Ratio Calculator for Polymarket Bot

This script calculates how big a target order needs to be to result in a user
order size of at least 5, at different price points. This helps determine the
minimum target order size that will execute on the user's portfolio.

Usage:
    python ratio_calculator.py <target_address>
"""

import sys
from tabulate import tabulate
from polymarket.helpers import get_portfolio_usdc_value, get_on_chain_usdc_balance, POLY_MARKET_FUNDER_ADDRESS
from utils.utils import get_follow_address


def calculate_min_target_order_size(
    user_portfolio_usdc: float,
    target_portfolio_usdc: float,
    user_order_size: float = 5.0
) -> list:
    """
    Calculate the minimum target order size needed at different price points.

    Args:
        user_portfolio_usdc: User's total portfolio value in USDC
        target_portfolio_usdc: Target's total portfolio value in USDC
        user_order_size: Minimum user order size (default 5.0)

    Returns:
        List of tuples containing (price, min_target_order_size)
    """
    results = []

    # Price points from 0.01 to 0.99
    price_points = [0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40,
                    0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85,
                    0.90, 0.95, 0.99]

    for price in price_points:
        # Formula derived from:
        # user_size = (target_usdc_size / target_portfolio) * user_portfolio / price
        # We want user_size >= 5, so:
        # target_usdc_size >= (5 * target_portfolio * price) / user_portfolio

        min_target_usdc_size = (user_order_size * target_portfolio_usdc * price) / user_portfolio_usdc
        min_target_token_size = min_target_usdc_size / price

        results.append({
            "Price": f"${price:.2f}",
            "Min Target USDC": f"${min_target_usdc_size:.2f}",
            "Target Size": f"{min_target_token_size:.2f}",
            "User Size": f"{user_order_size:.2f}",
            "User USDC": f"${user_order_size * price:.2f}"

        })

    return results


def main():
    print(f"\n{'='*80}")
    print(f"Ratio Calculator - Minimum Target Order Size Analysis")
    print(f"{'='*80}")
    print(f"\nFetching target address and portfolio values...")

    try:
        target_address = get_follow_address()
        user_portfolio = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS) + get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
        target_portfolio = get_portfolio_usdc_value(target_address)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"\nTarget Address: {target_address}")
    print(f"User Portfolio: ${user_portfolio:,.2f} USDC")
    print(f"Target Portfolio: ${target_portfolio:,.2f} USDC")
    print(f"Portfolio Ratio: {user_portfolio/target_portfolio:.4f} (User/Target)")
    print(f"\nMinimum user order size: 5.0 tokens")
    print(f"\nThe table below shows the minimum target order size needed")
    print(f"to result in a user order of at least 5 tokens at each price point.\n")

    results = calculate_min_target_order_size(user_portfolio, target_portfolio)

    # Create formatted table
    table_data = [
        [
            r["Price"],
            r["Min Target USDC"],
            r["Target Size"],
            r["User Size"],
            r["User USDC"]
        ]
        for r in results
    ]

    print(tabulate(
        table_data,
        headers=["Price", "Min Target USDC", "Target Size", "User Size", "User USDC"],
        tablefmt="grid",
        colalign=("right", "right", "right", "right", "right")
    ))

    print(f"\n{'='*80}")
    print("Interpretation:")
    print("  - If target places an order smaller than 'Target Size' at a given price,")
    print("    the scaled user order will be < 5 and won't be placed.")
    print("  - Only target orders >= the minimum will execute on your portfolio.")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()