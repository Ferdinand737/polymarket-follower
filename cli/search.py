#!/usr/bin/env python3
"""
Polymarket target search tool.

Searches the Polymarket leaderboard and trader data to find
high-quality copy-trading targets based on PnL, ROI, consistency,
and activity metrics.

Usage:
    python search.py [--min-roi N] [--min-pnl N] [--min-vol N]
                      [--sort FIELD] [--limit N] [--json]
                      [--include-current]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import requests

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
POSITIONS_URL = "https://data-api.polymarket.com/positions"
CLOSED_POSITIONS_URL = "https://data-api.polymarket.com/closed-positions"
ACTIVITY_URL = "https://data-api.polymarket.com/activity"
VALUE_URL = "https://data-api.polymarket.com/value"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def fetch_json(url, params=None):
    """Fetch JSON from URL with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1 * (attempt + 1))
            else:
                raise


def fetch_leaderboard(limit=100, sort="pnl", order="desc"):
    """Fetch leaderboard data."""
    data = fetch_json(LEADERBOARD_URL, params={
        "limit": limit,
        "sort": sort,
        "order": order,
    })
    return data if isinstance(data, list) else []


def fetch_open_positions(address):
    """Fetch open positions for a trader."""
    try:
        data = fetch_json(POSITIONS_URL, params={"user": address})
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_closed_positions(address):
    """Fetch closed positions for a trader."""
    try:
        data = fetch_json(CLOSED_POSITIONS_URL, params={"user": address})
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_recent_activity(address, limit=10):
    """Fetch recent activity to gauge trading recency."""
    try:
        data = fetch_json(ACTIVITY_URL, params={
            "user": address,
            "limit": limit,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        })
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_portfolio_value(address):
    """Fetch total portfolio value."""
    try:
        data = fetch_json(VALUE_URL, params={"user": address})
        if isinstance(data, list) and data:
            return data[0].get("value", 0)
        return 0
    except Exception:
        return 0


def analyze_trader(trader, deep=False):
    """
    Analyze a trader and compute search metrics.

    Returns a dict with:
    - rank, username, address, pnl, vol, roi
    - win_rate, consistency_score (if deep=True)
    - last_active, num_open_positions (if deep=True)
    - composite_score
    """
    address = trader.get("proxyWallet", "").lower()
    pnl = float(trader.get("pnl", 0))
    vol = float(trader.get("vol", 0))
    username = trader.get("userName", "unknown")
    rank = int(trader.get("rank", 0))

    # ROI = PnL / Volume (approximate, since we don't know starting capital)
    # This is a trading ROI, not a portfolio ROI
    roi = (pnl / vol * 100) if vol > 0 else 0

    result = {
        "rank": rank,
        "username": username,
        "address": address,
        "pnl": pnl,
        "vol": vol,
        "roi": roi,
        "profile_url": f"https://polymarket.com/{username}" if username else f"https://polymarket.com/profile/{address}",
    }

    if deep:
        # Fetch additional data for deeper analysis
        open_positions = fetch_open_positions(address)
        closed_positions = fetch_closed_positions(address)
        recent_activity = fetch_recent_activity(address, limit=5)

        # Win rate from closed positions
        total_closed = len(closed_positions)
        winners = sum(1 for p in closed_positions if float(p.get("realizedPnl", 0)) > 0)
        win_rate = (winners / total_closed * 100) if total_closed > 0 else 0

        # Consistency: lower standard deviation of per-position returns = more consistent
        if closed_positions:
            returns = [float(p.get("realizedPnl", 0)) for p in closed_positions]
            avg_return = sum(returns) / len(returns)
            if len(returns) > 1:
                variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
                std_dev = variance ** 0.5
                # Consistency score: 100 if all returns are the same, lower with more variance
                # Normalize by average absolute return
                avg_abs = sum(abs(r) for r in returns) / len(returns)
                consistency_score = max(0, 100 - (std_dev / avg_abs * 50)) if avg_abs > 0 else 50
            else:
                consistency_score = 50
        else:
            consistency_score = 0

        # Last activity timestamp
        if recent_activity:
            last_active_ts = max(a.get("timestamp", 0) for a in recent_activity)
            last_active = datetime.fromtimestamp(last_active_ts, tz=timezone.utc).isoformat()
            days_since_active = (time.time() - last_active_ts) / 86400
        else:
            last_active = None
            days_since_active = 999

        result.update({
            "win_rate": win_rate,
            "consistency_score": consistency_score,
            "total_closed": total_closed,
            "num_open": len(open_positions),
            "last_active": last_active,
            "days_since_active": days_since_active,
        })

    # Composite score: weighted blend of PnL, ROI, consistency
    # Scale PnL logarithmically (diminishing returns for very large PnL)
    import math
    pnl_score = min(100, math.log10(max(1, pnl)) * 20) if pnl > 0 else 0
    roi_score = min(100, roi * 2)  # Cap at 100 for 50%+ ROI

    if deep:
        win_score = result.get("win_rate", 0) * 0.5
        consistency = result.get("consistency_score", 0) * 0.5
        activity_score = max(0, 100 - result.get("days_since_active", 999) * 5)
        composite = (
            pnl_score * 0.25 +
            roi_score * 0.25 +
            win_score * 0.15 +
            consistency * 0.15 +
            activity_score * 0.20
        )
    else:
        # Lightweight score from leaderboard data only
        composite = pnl_score * 0.4 + roi_score * 0.6

    result["composite_score"] = round(composite, 1)
    return result


def search_targets(
    min_pnl=0,
    min_roi=0,
    min_vol=0,
    min_win_rate=0,
    limit=20,
    sort="composite_score",
    deep=False,
    leaderboard_size=100,
    include_current=False,
    current_target=None,
):
    """
    Search for high-quality copy-trading targets.

    Args:
        min_pnl: Minimum PnL in USD
        min_roi: Minimum ROI percentage
        min_vol: Minimum trading volume in USD
        min_win_rate: Minimum win rate percentage (requires deep=True)
        limit: Maximum number of results
        sort: Sort field (composite_score, pnl, roi, win_rate, consistency_score)
        deep: Fetch detailed stats for each trader (slower)
        leaderboard_size: How many leaderboard entries to fetch
        include_current: Include current target in results
        current_target: Current target address to skip or include
    """
    print(f"Fetching top {leaderboard_size} traders from leaderboard...", file=sys.stderr)
    leaderboard = fetch_leaderboard(limit=leaderboard_size)

    if not leaderboard:
        print("No leaderboard data found.", file=sys.stderr)
        return []

    results = []
    for i, trader in enumerate(leaderboard):
        addr = trader.get("proxyWallet", "").lower()

        # Skip current target unless explicitly included
        if not include_current and current_target and addr == current_target.lower():
            continue

        print(f"  Analyzing {i+1}/{len(leaderboard)}: {trader.get('userName', addr[:10])}...",
              file=sys.stderr, end="")

        analysis = analyze_trader(trader, deep=deep)

        # Apply filters
        if analysis["pnl"] < min_pnl:
            print(" filtered (pnl)", file=sys.stderr)
            continue
        if analysis["roi"] < min_roi:
            print(" filtered (roi)", file=sys.stderr)
            continue
        if analysis["vol"] < min_vol:
            print(" filtered (vol)", file=sys.stderr)
            continue
        if deep and min_win_rate > 0 and analysis.get("win_rate", 0) < min_win_rate:
            print(" filtered (win_rate)", file=sys.stderr)
            continue

        print(f" score={analysis['composite_score']}", file=sys.stderr)
        results.append(analysis)

    # Sort
    valid_sort_fields = ["composite_score", "pnl", "roi", "win_rate", "consistency_score", "rank"]
    if sort not in valid_sort_fields:
        sort = "composite_score"
    results.sort(key=lambda x: x.get(sort, 0), reverse=(sort != "rank"))
    if sort == "rank":
        results.sort(key=lambda x: x.get("rank", 999))

    return results[:limit]


def format_results(results, as_json=False):
    """Format search results for output."""
    if as_json:
        return json.dumps(results, indent=2)

    if not results:
        return "No traders found matching criteria."

    lines = []
    lines.append(f"{'#':<4} {'Score':>6} {'PnL':>12} {'ROI':>7} {'Win%':>6} {'Con%':>5} {'User':<20} {'Address'}")
    lines.append("-" * 100)

    for i, r in enumerate(results):
        rank = r.get("rank", "?")
        score = r.get("composite_score", 0)
        pnl = r.get("pnl", 0)
        roi = r.get("roi", 0)
        win_rate = r.get("win_rate", None)
        consistency = r.get("consistency_score", None)
        username = r.get("username", "?")[:18]
        addr = r.get("address", "?")[:14] + "..."

        win_str = f"{win_rate:.0f}" if win_rate is not None else "-"
        con_str = f"{consistency:.0f}" if consistency is not None else "-"

        if pnl >= 1_000_000:
            pnl_str = f"${pnl/1_000_000:.1f}M"
        elif pnl >= 1_000:
            pnl_str = f"${pnl/1_000:.1f}K"
        else:
            pnl_str = f"${pnl:.0f}"

        lines.append(
            f"{rank:<4} {score:>6.1f} {pnl_str:>12} {roi:>6.1f}% {win_str:>6} {con_str:>5} {username:<20} {addr}"
        )

    # Add footer with details
    lines.append("")
    lines.append("Score = composite of PnL, ROI, win rate, consistency, activity")
    lines.append("Use --deep for win rate & consistency data (slower)")
    lines.append("Use --json for full JSON output")
    lines.append("")
    for r in results[:5]:
        lines.append(f"  Profile: {r.get('profile_url', '')}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search Polymarket for high-quality copy-trading targets"
    )
    parser.add_argument("--min-pnl", type=float, default=0,
                        help="Minimum PnL in USD (default: 0)")
    parser.add_argument("--min-roi", type=float, default=0,
                        help="Minimum ROI percentage (default: 0)")
    parser.add_argument("--min-vol", type=float, default=0,
                        help="Minimum trading volume in USD (default: 0)")
    parser.add_argument("--min-win-rate", type=float, default=0,
                        help="Minimum win rate %% (requires --deep, default: 0)")
    parser.add_argument("--sort", type=str, default="composite_score",
                        choices=["composite_score", "pnl", "roi", "win_rate", "consistency_score", "rank"],
                        help="Sort results by field (default: composite_score)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Maximum number of results (default: 20)")
    parser.add_argument("--leaderboard-size", type=int, default=100,
                        help="Number of leaderboard entries to fetch (default: 100)")
    parser.add_argument("--deep", action="store_true",
                        help="Fetch detailed stats (win rate, consistency, activity). Slower.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output results as JSON")
    parser.add_argument("--include-current", action="store_true",
                        help="Include current target in results")

    args = parser.parse_args()

    # Read current target from config if it exists
    current_target = None
    try:
        config_path = __file__.replace("cli/search.py", "src/config/follower_config.json")
        import os
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
                current_target = cfg.get("address_to_follow")
    except Exception:
        pass

    results = search_targets(
        min_pnl=args.min_pnl,
        min_roi=args.min_roi,
        min_vol=args.min_vol,
        min_win_rate=args.min_win_rate,
        limit=args.limit,
        sort=args.sort,
        deep=args.deep,
        leaderboard_size=args.leaderboard_size,
        include_current=args.include_current,
        current_target=current_target,
    )

    print(format_results(results, as_json=args.as_json))


if __name__ == "__main__":
    main()
