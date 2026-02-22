#!/usr/bin/env python3
"""
Ratio Calculator for Polymarket Bot

This script calculates the minimum USDC the target must spend in a single order
for the follower to be able to copy it. The follower has a $1 minimum order size.

Usage:
    python ratio_calculator.py <target_address>
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tabulate import tabulate
from follower.helpers import get_portfolio_usdc_value, get_on_chain_usdc_balance, POLY_MARKET_FUNDER_ADDRESS
from utils.utils import get_follow_address


def calculate_min_target_usdc(
    user_portfolio_usdc: float,
    target_portfolio_usdc: float,
    min_user_order_usdc: float = 1.0
) -> float:
    """
    Calculate the minimum target order USDC needed for follower to copy.
    
    Formula derived from:
        user_usdc = (target_usdc / target_portfolio) * user_portfolio
    For follower to execute:
        user_usdc >= 1.0
    Therefore:
        target_usdc >= target_portfolio / user_portfolio
    
    Args:
        user_portfolio_usdc: User's total portfolio value in USDC
        target_portfolio_usdc: Target's total portfolio value in USDC
        min_user_order_usdc: Minimum user order size in USDC (default $1.00)
    
    Returns:
        Minimum target USDC order size
    """
    return min_user_order_usdc * target_portfolio_usdc / user_portfolio_usdc


def main():
    print(f"\n{'='*80}")
    print(f"Ratio Calculator - Minimum Target Order Size")
    print(f"{'='*80}")
    print(f"\nFetching target address and portfolio values...")

    try:
        target_address = get_follow_address()
        user_portfolio = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS) + get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
        target_portfolio = get_portfolio_usdc_value(target_address)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    min_target_usdc = calculate_min_target_usdc(user_portfolio, target_portfolio)
    ratio = user_portfolio / target_portfolio

    print(f"\nTarget Address: {target_address}")
    print(f"User Portfolio: ${user_portfolio:,.2f} USDC")
    print(f"Target Portfolio: ${target_portfolio:,.2f} USDC")
    print(f"Portfolio Ratio: {ratio:.4f} (User/Target)")
    
    print(f"\n{'='*80}")
    print(f"MINIMUM TARGET ORDER SIZE: ${min_target_usdc:,.2f} USDC")
    print(f"{'='*80}")
    
    print(f"\nInterpretation:")
    print(f"  - Follower minimum order size: $1.00 USDC")
    print(f"  - Target must spend at least ${min_target_usdc:,.2f} USDC in a single order")
    print(f"    for the follower's proportional order to be >= $1.00")
    print(f"  - Any target order smaller than ${min_target_usdc:,.2f} USDC will be skipped")
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    main()