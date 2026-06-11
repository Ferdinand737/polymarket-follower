#!/usr/bin/env python3
"""
Polymarket target search tool — find consistently profitable ACTIVE traders.

Strategy:
  1. Fetch WEEK + ALL-time leaderboards ordered by BOTH PnL and Volume
     (PnL leaderboard finds big winners; VOL leaderboard finds consistent traders)
  2. Merge and deduplicate candidates
  3. For each candidate, fetch a lightweight win-rate sample (1 page of closed positions)
     plus recent activity — this gives win-rate without the full deep scan
  4. Score heavily on win rate, volume, and consistency
  5. Filter out inactive traders and low performers
  6. Use --deep for full analysis (all pages of closed positions, profile, etc.)

Usage:
    python search.py [--min-roi N] [--min-pnl N] [--min-markets N]
                      [--min-win-rate N] [--min-profit-factor N]
                      [--max-days-inactive N] [--sort FIELD] [--limit N]
                      [--json] [--deep] [--categories CAT1,CAT2,...]
                      [--leaderboard-pages N] [--include-current]
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone

import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── API endpoints ──────────────────────────────────────────────────────────
LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
POSITIONS_URL = "https://data-api.polymarket.com/positions"
CLOSED_POSITIONS_URL = "https://data-api.polymarket.com/closed-positions"
ACTIVITY_URL = "https://data-api.polymarket.com/activity"
VALUE_URL = "https://data-api.polymarket.com/value"
TRADED_URL = "https://data-api.polymarket.com/traded"
PROFILE_URL = "https://gamma-api.polymarket.com/public-profile"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RATE_LIMIT_REQUESTS = 80
RATE_LIMIT_WINDOW = 10.0


class RateLimiter:
    def __init__(self, max_requests=RATE_LIMIT_REQUESTS, per_seconds=RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self._lock = threading.Lock()
        self._timestamps = []

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                self._timestamps = [t for t in self._timestamps if now - t < self.per_seconds]
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return
                wait = self.per_seconds - (now - self._timestamps[0]) + 0.01
            time.sleep(max(0, wait))


_rate_limiter = RateLimiter()


# ── Helpers ────────────────────────────────────────────────────────────────

def fetch_json(url, params=None):
    """Fetch JSON from URL with rate limiting, retry logic, and 429 handling."""
    for attempt in range(MAX_RETRIES):
        _rate_limiter.acquire()
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                print(f"    Rate limited (429), waiting {retry_after}s…", file=sys.stderr)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
            else:
                raise


def paginate_json(url, params=None, max_pages=20, limit_key="limit",
                  offset_key="offset", page_size=50):
    """Paginate through a JSON array endpoint until no more results."""
    all_results = []
    for page in range(max_pages):
        p = dict(params or {})
        p[limit_key] = page_size
        p[offset_key] = page * page_size
        data = fetch_json(url, params=p)
        if not isinstance(data, list) or len(data) == 0:
            break
        all_results.extend(data)
        if len(data) < page_size:
            break
    return all_results


def fmt_dollars(n):
    """Format a number as a human-readable dollar string."""
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n / 1_000:.1f}K"
    return f"${n:.0f}"


# ── Leaderboard ────────────────────────────────────────────────────────────

def fetch_leaderboard_pages(num_pages=5, category="OVERALL", period="ALL",
                            order_by="PNL"):
    """
    Fetch multiple pages of the leaderboard.

    period: DAY, WEEK, MONTH, ALL
    The leaderboard returns up to 50 entries per page.
    """
    all_entries = []
    for page in range(num_pages):
        params = {
            "category": category,
            "timePeriod": period,
            "orderBy": order_by,
            "limit": 50,
            "offset": page * 50,
        }
        data = fetch_json(LEADERBOARD_URL, params=params)
        if not isinstance(data, list) or len(data) == 0:
            break
        all_entries.extend(data)
        if len(data) < 50:
            break
    return all_entries


# ── Deep trader analysis ──────────────────────────────────────────────────

def fetch_profile(address):
    """Fetch public profile (account age, bio, etc.)."""
    try:
        data = fetch_json(PROFILE_URL, params={"address": address})
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def fetch_total_markets_traded(address):
    """Fetch total number of distinct markets a user has traded."""
    try:
        data = fetch_json(TRADED_URL, params={"user": address})
        if isinstance(data, dict):
            return int(data.get("traded", 0))
        return 0
    except Exception:
        return 0


def fetch_all_closed_positions(address, max_pages=40):
    """Fetch ALL closed positions for a trader by paginating."""
    return paginate_json(
        CLOSED_POSITIONS_URL,
        params={"user": address},
        max_pages=max_pages,
        page_size=50,
    )


def fetch_open_positions(address):
    """Fetch current open positions for a trader."""
    try:
        data = paginate_json(
            POSITIONS_URL,
            params={"user": address},
            max_pages=5,
            page_size=100,
        )
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_portfolio_value(address):
    """Fetch total portfolio value."""
    try:
        data = fetch_json(VALUE_URL, params={"user": address})
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0))
        return 0
    except Exception:
        return 0


def fetch_recent_activity(address, limit=20):
    """Fetch recent activity to check if trader is currently active."""
    try:
        data = fetch_json(ACTIVITY_URL, params={
            "user": address,
            "limit": limit,
        })
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_win_rate_sample(address, page_size=50):
    """Fetch a single page of closed positions for a quick win-rate estimate."""
    try:
        data = fetch_json(CLOSED_POSITIONS_URL, params={
            "user": address,
            "limit": page_size,
            "offset": 0,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        })
        if not isinstance(data, list) or len(data) == 0:
            return None
        winners = sum(1 for p in data if float(p.get("realizedPnl", 0)) > 0)
        losers = sum(1 for p in data if float(p.get("realizedPnl", 0)) < 0)
        total = winners + losers
        sum_wins = sum(float(p.get("realizedPnl", 0)) for p in data if float(p.get("realizedPnl", 0)) > 0)
        sum_losses = sum(abs(float(p.get("realizedPnl", 0))) for p in data if float(p.get("realizedPnl", 0)) < 0)
        if total == 0:
            return None
        return {
            "win_rate": winners / total * 100,
            "sample_size": len(data),
            "decisive_positions": total,
            "profit_factor": (sum_wins / sum_losses) if sum_losses > 0 else float("inf") if sum_wins > 0 else 0,
        }
    except Exception:
        return None


def analyze_trader(trader, deep=False, max_days_inactive=3):
    """
    Analyze a trader and compute long-term quality metrics.

    Always checks recency (activity within max_days_inactive).
    If deep=True, also fetches closed positions, profile, etc.
    """
    address = trader.get("proxyWallet", "").lower()
    pnl = float(trader.get("pnl", 0))
    vol = float(trader.get("vol", 0))
    username = trader.get("userName", "unknown")
    rank = int(trader.get("rank", 0))

    # ROI = PnL / Volume — trading profit margin
    roi = (pnl / vol * 100) if vol > 0 else 0

    result = {
        "rank": rank,
        "username": username,
        "address": address,
        "pnl": pnl,
        "vol": vol,
        "roi": round(roi, 2),
        "profile_url": (
            f"https://polymarket.com/@{username}"
            if username and username != "unknown"
            else f"https://polymarket.com/profile/{address}"
        ),
    }

    # ── Always check recency ───────────────────────────────────────────
    recent_activity = fetch_recent_activity(address, limit=20)
    now_ts = time.time()

    # Find the most recent TRADE activity (not splits/merges/redeems)
    latest_trade_ts = 0
    trade_count_recent = 0  # trades in last 7 days
    for act in recent_activity:
        act_type = act.get("type", "")
        ts = act.get("timestamp", 0)
        if act_type == "TRADE" and ts > 0:
            if ts > latest_trade_ts:
                latest_trade_ts = ts
            if (now_ts - ts) < 7 * 86400:
                trade_count_recent += 1

    if latest_trade_ts > 0:
        days_since_last_trade = max(0, (now_ts - latest_trade_ts) / 86400)
    else:
        days_since_last_trade = 999

    result["days_since_last_trade"] = round(days_since_last_trade, 1)
    result["trades_last_7d"] = trade_count_recent

    # Filter: must be actively trading
    is_active = days_since_last_trade <= max_days_inactive
    result["is_active"] = is_active

    if not deep:
        wr_sample = fetch_win_rate_sample(address)
        total_markets = fetch_total_markets_traded(address)
        week_pnl = float(trader.get("week_pnl", pnl))
        week_vol = float(trader.get("week_vol", vol))
        week_roi = (week_pnl / week_vol * 100) if week_vol > 0 else 0

        # 1. Win rate from closed-positions sample — THE key metric
        #    55%=55, 65%=78, 75%+=100
        if wr_sample and wr_sample["decisive_positions"] >= 5:
            wr_score = min(100, wr_sample["win_rate"] * 1.33)
            sample_wr = round(wr_sample["win_rate"], 1)
            sample_pf = wr_sample["profit_factor"]
            sample_size = wr_sample["sample_size"]
        else:
            wr_score = 0
            sample_wr = None
            sample_pf = None
            sample_size = 0

        # 2. Profit factor from sample
        if sample_pf is not None:
            if sample_pf == float("inf"):
                pf_score = 100
            elif sample_pf > 0:
                pf_score = min(100, max(0, (sample_pf - 0.5) * 66.67))
            else:
                pf_score = 0
        else:
            pf_score = 0

        # 3. Volume significance — more volume = more reliable stats, finds gopfan2-types
        #    $100K=50, $1M=60, $10M=70, $100M=80
        vol_score = min(100, math.log10(max(1, vol)) * 10) if vol > 0 else 0

        # 4. Market breadth — 100=50, 500=75, 2000+=100
        breadth_score = min(100, (total_markets / 2000) * 100) if total_markets > 0 else 0

        # 5. Week vs ALL-time consistency — penalize hot streaks
        if week_vol > 0 and vol > 0 and week_roi > 0 and roi > 0:
            ratio = min(week_roi, roi) / max(week_roi, roi)
            consistency_score = ratio * 100
        elif roi > 0:
            consistency_score = 40
        else:
            consistency_score = 0

        # 6. Trade frequency
        if trade_count_recent >= 10:
            freq_score = 100
        elif trade_count_recent >= 5:
            freq_score = 40 + trade_count_recent * 6
        else:
            freq_score = trade_count_recent * 20

        # Recency gate
        if not is_active:
            recency_penalty = 0.3
        elif days_since_last_trade <= 0.5:
            recency_penalty = 1.0
        elif days_since_last_trade <= 1:
            recency_penalty = 0.9
        else:
            recency_penalty = 0.7

        composite = (
            wr_score * 0.30
            + pf_score * 0.10
            + vol_score * 0.15
            + breadth_score * 0.15
            + consistency_score * 0.10
            + freq_score * 0.20
        ) * recency_penalty
        result["composite_score"] = round(composite, 1)
        result["sample_win_rate"] = sample_wr
        result["sample_profit_factor"] = round(sample_pf, 2) if sample_pf is not None and sample_pf != float("inf") else ("inf" if sample_pf == float("inf") else None)
        result["sample_size"] = sample_size
        result["total_markets"] = total_markets
        return result

    # ── Deep analysis ──────────────────────────────────────────────────
    print(f"    Deep-analyzing {username}…", file=sys.stderr)

    # Profile (account age)
    profile = fetch_profile(address)
    created_at = profile.get("createdAt")
    account_age_days = None
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            account_age_days = (datetime.now(timezone.utc) - dt).days
        except Exception:
            account_age_days = None

    # Total markets traded
    total_markets = fetch_total_markets_traded(address)

    # All closed positions
    closed_positions = fetch_all_closed_positions(address, max_pages=40)
    total_closed = len(closed_positions)

    # Compute win/loss metrics from closed positions
    winners = 0
    losers = 0
    sum_wins = 0.0
    sum_losses = 0.0
    returns = []
    worst_loss = 0.0
    best_win = 0.0
    market_set = set()

    # Also compute recent-period metrics (positions closed in last 30 days)
    thirty_days_ago = now_ts - 30 * 86400
    recent_winners = 0
    recent_losers = 0
    recent_sum_wins = 0.0
    recent_sum_losses = 0.0

    for pos in closed_positions:
        rpnl = float(pos.get("realizedPnl", 0))
        returns.append(rpnl)
        market_set.add(pos.get("conditionId", ""))

        # Check timestamp for recent positions
        pos_ts = pos.get("timestamp", 0)

        if rpnl > 0:
            winners += 1
            sum_wins += rpnl
            if rpnl > best_win:
                best_win = rpnl
            if pos_ts and pos_ts > thirty_days_ago:
                recent_winners += 1
                recent_sum_wins += rpnl
        elif rpnl < 0:
            losers += 1
            sum_losses += abs(rpnl)
            if rpnl < worst_loss:
                worst_loss = rpnl
            if pos_ts and pos_ts > thirty_days_ago:
                recent_losers += 1
                recent_sum_losses += abs(rpnl)

    win_rate = (winners / total_closed * 100) if total_closed > 0 else 0
    profit_factor = (
        (sum_wins / sum_losses) if sum_losses > 0
        else float("inf") if sum_wins > 0 else 0
    )
    avg_return = (sum(returns) / len(returns)) if returns else 0

    # Recent win rate & profit factor (last 30 days)
    recent_total = recent_winners + recent_losers
    recent_win_rate = (
        (recent_winners / recent_total * 100) if recent_total > 0 else 0
    )
    recent_profit_factor = (
        (recent_sum_wins / recent_sum_losses) if recent_sum_losses > 0
        else float("inf") if recent_sum_wins > 0 else 0
    )

    distinct_markets_closed = len(market_set)

    # Open positions & portfolio value
    open_positions = fetch_open_positions(address)
    num_open = len(open_positions)
    portfolio_value = fetch_portfolio_value(address)

    # ── Composite scoring (long-term + activity focus) ────────────────
    # Components:
    #   1. Profit factor (ALL-time)            — weight 0.20  ← key metric
    #   2. Win rate (ALL-time)                  — weight 0.15
    #   3. Recency: days since last TRADE        — weight 0.20  ← MUST be active
    #   4. Recent trades (7-day count)           — weight 0.10  ← active volume
    #   5. PnL magnitude (log-scaled)            — weight 0.10
    #   6. ROI on volume                         — weight 0.05
    #   7. Market breadth (total markets)         — weight 0.05
    #   8. Statistical significance (pos count)   — weight 0.05
    #   9. Account longevity                     — weight 0.05
    #  10. Recent profit factor (30-day)         — weight 0.05

    # 1. Profit factor: 1.0=30, 1.5=60, 2.0+=100
    if profit_factor == float("inf"):
        pf_score = 100
    elif profit_factor > 0:
        pf_score = min(100, max(0, (profit_factor - 0.5) * 66.67))
    else:
        pf_score = 0

    # 2. Win rate: 50%=50, 60%=70, 70%+=100
    wr_score = min(100, win_rate * 1.5) if total_closed > 0 else 0

    # 3. Recency: active NOW = 100, 1 day = 80, 3 days = 40, 7+ days = 0
    if days_since_last_trade <= 0.5:
        recency_score = 100
    elif days_since_last_trade <= 1:
        recency_score = 90
    elif days_since_last_trade <= 3:
        recency_score = 60
    elif days_since_last_trade <= 7:
        recency_score = 30
    else:
        recency_score = 0

    # 4. Recent trades count: 1=30, 3=60, 5+=100
    recent_trade_score = min(100, max(0, trade_count_recent * 20))

    # 5. PnL magnitude (log-scaled)
    pnl_score = min(100, math.log10(max(1, pnl)) * 15) if pnl > 0 else 0

    # 6. ROI
    roi_score = min(100, roi * 1.5)

    # 7. Market breadth: 10=30, 50=60, 100+=100
    breadth_score = min(100, (total_markets / 100) * 100) if total_markets > 0 else 0

    # 8. Statistical significance: 20=30, 50=60, 100+=100
    sig_score = min(100, (total_closed / 100) * 100)

    # 9. Account longevity: 30d=20, 180d=60, 365d+=100
    if account_age_days is not None:
        age_score = min(100, (account_age_days / 365) * 100)
    else:
        age_score = 40

    # 10. Recent profit factor (30-day): same scale as all-time
    if recent_profit_factor == float("inf"):
        recent_pf_score = 100
    elif recent_profit_factor > 0:
        recent_pf_score = min(100, max(0, (recent_profit_factor - 0.5) * 66.67))
    else:
        recent_pf_score = 0

    composite = (
        pf_score * 0.20
        + wr_score * 0.15
        + recency_score * 0.20
        + recent_trade_score * 0.10
        + pnl_score * 0.10
        + roi_score * 0.05
        + breadth_score * 0.05
        + sig_score * 0.05
        + age_score * 0.05
        + recent_pf_score * 0.05
    )

    result.update({
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "avg_return": round(avg_return, 2),
        "best_win": round(best_win, 2),
        "worst_loss": round(worst_loss, 2),
        "total_closed": total_closed,
        "winners": winners,
        "losers": losers,
        "recent_win_rate": round(recent_win_rate, 1),
        "recent_profit_factor": (
            round(recent_profit_factor, 2)
            if recent_profit_factor != float("inf") else "inf"
        ),
        "recent_wins": recent_winners,
        "recent_losses": recent_losers,
        "total_markets": total_markets,
        "distinct_markets_closed": distinct_markets_closed,
        "account_age_days": account_age_days,
        "portfolio_value": round(portfolio_value, 2),
        "num_open": num_open,
        "composite_score": round(composite, 1),
        # Sub-scores for transparency
        "_pf_score": round(pf_score, 1),
        "_wr_score": round(wr_score, 1),
        "_recency_score": round(recency_score, 1),
        "_recent_trade_score": round(recent_trade_score, 1),
        "_pnl_score": round(pnl_score, 1),
        "_roi_score": round(roi_score, 1),
        "_breadth_score": round(breadth_score, 1),
        "_sig_score": round(sig_score, 1),
        "_age_score": round(age_score, 1),
        "_recent_pf_score": round(recent_pf_score, 1),
    })

    return result


# ── Main search ────────────────────────────────────────────────────────────

def search_targets(
    min_pnl=0,
    min_roi=0,
    min_markets=0,
    min_win_rate=0,
    min_profit_factor=0,
    max_days_inactive=3,
    limit=20,
    sort="composite_score",
    deep=False,
    leaderboard_pages=2,
    categories=None,
    include_current=False,
    current_target=None,
    workers=4,
    max_candidates=50,
):
    """
    Search for consistently profitable, currently ACTIVE traders.

    Strategy: Fetch WEEK leaderboard (active traders), then optionally
    cross-reference with ALL-time leaderboard for long-term performance.
    Traders inactive for more than max_days_inactive are filtered out.
    """
    if categories is None:
        categories = ["OVERALL"]

    # ── Step 1: Fetch leaderboards (PNL + VOL ordered, WEEK + ALL) ─────
    week_entries = []
    seen_addresses = set()

    for order_by in ["PNL", "VOL"]:
        for cat in categories:
            print(f"Fetching WEEK leaderboard ({order_by}) for: {cat}…", file=sys.stderr)
            entries = fetch_leaderboard_pages(
                num_pages=leaderboard_pages,
                category=cat,
                period="WEEK",
                order_by=order_by,
            )
            for e in entries:
                addr = e.get("proxyWallet", "").lower()
                if addr and addr not in seen_addresses:
                    week_entries.append(e)
                    seen_addresses.add(addr)

    if not week_entries:
        print("No week leaderboard data found.", file=sys.stderr)
        return []

    print(f"Found {len(week_entries)} unique traders on WEEK leaderboards.", file=sys.stderr)

    # ── Step 2: Fetch ALL-time leaderboard for long-term context ──────
    all_time_map = {}
    for order_by in ["PNL", "VOL"]:
        for cat in categories:
            print(f"Fetching ALL-time leaderboard ({order_by}) for: {cat}…", file=sys.stderr)
            entries = fetch_leaderboard_pages(
                num_pages=leaderboard_pages,
                category=cat,
                period="ALL",
                order_by=order_by,
            )
            for e in entries:
                addr = e.get("proxyWallet", "").lower()
                if addr and addr not in all_time_map:
                    all_time_map[addr] = e

    print(f"Found {len(all_time_map)} traders on ALL-time leaderboards.", file=sys.stderr)

    # ── Step 3: Merge — prefer ALL-time data but only for WEEK-active traders ──
    # Use ALL-time data for long-term stats, but require trader to be on WEEK board
    candidates = []
    for trader in week_entries:
        addr = trader.get("proxyWallet", "").lower()
        # If they're also on the ALL-time board, use that data (richer PnL/vol)
        if addr in all_time_map:
            merged = dict(all_time_map[addr])
            # Keep the WEEK rank for reference
            merged["week_rank"] = trader.get("rank")
            merged["week_pnl"] = trader.get("pnl")
            merged["week_vol"] = trader.get("vol")
            candidates.append(merged)
        else:
            trader["week_rank"] = trader.get("rank")
            trader["week_pnl"] = trader.get("pnl")
            trader["week_vol"] = trader.get("vol")
            candidates.append(trader)

    print(f"Merged to {len(candidates)} active candidates.", file=sys.stderr)

    if max_candidates > 0 and len(candidates) > max_candidates:
        print(f"Capping candidates from {len(candidates)} to {max_candidates}.", file=sys.stderr)
        candidates = candidates[:max_candidates]

    # ── Step 4: Analyze candidates (parallel) ──────────────────────────
    candidates_to_analyze = [
        t for t in candidates
        if include_current or not current_target
        or t.get("proxyWallet", "").lower() != current_target.lower()
    ]
    total = len(candidates_to_analyze)
    progress_lock = threading.Lock()
    progress_count = [0]

    def _analyze_one(trader):
        addr = trader.get("proxyWallet", "").lower()
        label = trader.get("userName", addr[:10])

        try:
            analysis = analyze_trader(trader, deep=deep, max_days_inactive=max_days_inactive)
        except Exception as e:
            with progress_lock:
                progress_count[0] += 1
                done = progress_count[0]
            print(f"  [{done}/{total}] {label} ✗ ERROR: {e}", file=sys.stderr)
            return None

        with progress_lock:
            progress_count[0] += 1
            done = progress_count[0]

        if not analysis["is_active"]:
            print(
                f"  [{done}/{total}] {label} ✗ INACTIVE (last trade {analysis['days_since_last_trade']}d ago)",
                file=sys.stderr,
            )
            return None

        if analysis["pnl"] < min_pnl:
            print(f"  [{done}/{total}] {label} ✗ PnL too low ({fmt_dollars(analysis['pnl'])})", file=sys.stderr)
            return None
        if analysis["roi"] < min_roi:
            print(f"  [{done}/{total}] {label} ✗ ROI too low ({analysis['roi']:.1f}%)", file=sys.stderr)
            return None
        if min_win_rate > 0:
            wr = analysis.get("win_rate") or analysis.get("sample_win_rate") or 0
            if wr < min_win_rate:
                print(f"  [{done}/{total}] {label} ✗ Win rate too low ({wr:.1f}%)", file=sys.stderr)
                return None
        if deep and min_profit_factor > 0:
            pf = analysis.get("profit_factor", 0)
            pf_val = float("inf") if pf == "inf" else pf
            if pf_val < min_profit_factor:
                print(f"  [{done}/{total}] {label} ✗ Profit factor too low ({pf})", file=sys.stderr)
                return None
        if min_markets > 0 and analysis.get("total_markets", 0) < min_markets:
            print(f"  [{done}/{total}] {label} ✗ Too few markets ({analysis.get('total_markets', 0)})", file=sys.stderr)
            return None

        print(f"  [{done}/{total}] {label} ✓ score={analysis['composite_score']}", file=sys.stderr)
        return analysis

    results = []
    print(f"Analyzing {total} candidates with {workers} worker(s)…", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_analyze_one, t) for t in candidates_to_analyze]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    # ── Step 5: Sort ──────────────────────────────────────────────────
    valid_sort_fields = [
        "composite_score", "pnl", "roi", "win_rate", "profit_factor",
        "total_markets", "account_age_days", "rank",
    ]
    if sort not in valid_sort_fields:
        sort = "composite_score"

    if sort == "rank":
        results.sort(key=lambda x: x.get("rank", 999))
    elif sort == "profit_factor":
        results.sort(
            key=lambda x: float("inf") if x.get(sort, 0) == "inf" else x.get(sort, 0),
            reverse=True,
        )
    else:
        results.sort(key=lambda x: x.get(sort, 0), reverse=True)

    return results[:limit]


def format_results(results, as_json=False, deep=False):
    """Format search results for output."""
    if as_json:
        return json.dumps(results, indent=2, default=str)

    if not results:
        return "No traders found matching criteria. Try relaxing filters."

    lines = []

    if deep:
        lines.append(
            f"{'#':<5} {'Score':>6} {'PnL':>12} {'ROI':>7} {'Win%':>6} "
            f"{'PF':>6} {'ActD':>5} {'Tr7d':>5} {'Markets':>8} {'Closed':>7} "
            f"{'User':<20}"
        )
        lines.append("─" * 120)

        for r in results:
            rank = r.get("rank", "?")
            score = r.get("composite_score", 0)
            pnl = r.get("pnl", 0)
            roi = r.get("roi", 0)
            wr = r.get("win_rate", 0)
            pf = r.get("profit_factor", 0)
            pf_str = str(pf) if pf != "inf" else "∞"
            days_active = r.get("days_since_last_trade", "—")
            days_str = f"{days_active:.0f}d" if isinstance(days_active, (int, float)) else "—"
            trades_7d = r.get("trades_last_7d", 0)
            markets = r.get("total_markets", 0)
            closed = r.get("total_closed", 0)
            username = r.get("username", "?")[:18]

            lines.append(
                f"{rank:<5} {score:>6.1f} {fmt_dollars(pnl):>12} {roi:>6.1f}% "
                f"{wr:>5.1f}% {pf_str:>6} {days_str:>5} {trades_7d:>5} "
                f"{markets:>8} {closed:>7} {username:<20}"
            )

        lines.append("")
        lines.append("Score: PF 20% | Recency 20% | Win% 15% | RecentTrades 10% | PnL 10% "
                      "| ROI 5% | Markets 5% | Sample 5% | Age 5% | RecentPF 5%")
        lines.append("PF = Profit Factor (gross wins/gross losses). ∞ = no losses. "
                      "ActD = days since last trade. Tr7d = trades in last 7 days.")
    else:
        lines.append(
            f"{'#':<5} {'Score':>6} {'PnL':>12} {'ROI':>7} {'Win%':>6} "
            f"{'PF':>5} {'Vol':>12} {'Mkts':>5} {'ActD':>5} {'Tr7d':>5} {'User':<20}"
        )
        lines.append("─" * 115)

        for r in results:
            rank = r.get("rank", "?")
            score = r.get("composite_score", 0)
            pnl = r.get("pnl", 0)
            roi = r.get("roi", 0)
            wr = r.get("sample_win_rate")
            wr_str = f"{wr:.0f}%" if wr is not None else "—"
            pf = r.get("sample_profit_factor")
            pf_str = str(pf) if pf == "inf" else (f"{pf:.1f}" if pf is not None else "—")
            vol = r.get("vol", 0)
            markets = r.get("total_markets", 0)
            mkts_str = f"{markets}" if markets >= 1000 else str(markets) if markets > 0 else "—"
            if markets >= 1000:
                mkts_str = f"{markets/1000:.1f}K"
            days_active = r.get("days_since_last_trade", "—")
            days_str = f"{days_active:.0f}d" if isinstance(days_active, (int, float)) else "—"
            trades_7d = r.get("trades_last_7d", 0)
            username = r.get("username", "?")[:18]

            lines.append(
                f"{rank:<5} {score:>6.1f} {fmt_dollars(pnl):>12} {roi:>6.1f}% "
                f"{wr_str:>6} {pf_str:>5} {fmt_dollars(vol):>12} {mkts_str:>5} "
                f"{days_str:>5} {trades_7d:>5} {username:<20}"
            )

        lines.append("")
        lines.append("Win%/PF from latest 50 closed positions (sample). Mkts = total markets traded.")
        lines.append("Use --deep for full analysis with all closed positions, account age, etc.")
        lines.append("ActD = days since last trade. Tr7d = trades in last 7 days.")

    lines.append("")
    for r in results:
        lines.append(f"  Profile: {r.get('profile_url', '')}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search Polymarket for consistently profitable, currently ACTIVE traders. "
                    "Finds traders on the WEEK leaderboard (active now) and cross-references "
                    "with ALL-time data for long-term track records."
    )
    parser.add_argument(
        "--min-pnl", type=float, default=1000,
        help="Minimum ALL-time PnL in USD (default: 1000)",
    )
    parser.add_argument(
        "--min-roi", type=float, default=0,
        help="Minimum ROI %% = PnL/Volume (default: 0)",
    )
    parser.add_argument(
        "--min-markets", type=int, default=0,
        help="Minimum total distinct markets traded (default: 0)",
    )
    parser.add_argument(
        "--min-win-rate", type=float, default=0,
        help="Minimum win rate %% from closed-position sample (default: 0)",
    )
    parser.add_argument(
        "--min-profit-factor", type=float, default=0,
        help="Minimum profit factor = gross wins/gross losses (requires --deep, default: 0)",
    )
    parser.add_argument(
        "--max-days-inactive", type=float, default=3,
        help="Max days since last trade to be considered active (default: 3)",
    )
    parser.add_argument(
        "--sort", type=str, default="composite_score",
        choices=["composite_score", "pnl", "roi", "win_rate", "profit_factor",
                 "total_markets", "account_age_days", "rank"],
        help="Sort results by field (default: composite_score)",
    )
    parser.add_argument(
        "--limit", type=int, default=20,
        help="Maximum number of results (default: 20)",
    )
    parser.add_argument(
        "--leaderboard-pages", type=int, default=2,
        help="Number of leaderboard pages to scan, 50 per page (default: 2 = 100 traders)",
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="Deep analysis: fetch all closed positions, profile, total markets. "
             "Much slower but reveals win rate, profit factor, etc.",
    )
    parser.add_argument(
        "--categories", type=str, default="OVERALL",
        help="Comma-separated leaderboard categories: OVERALL,POLITICS,SPORTS,CRYPTO,CULTURE,etc.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--include-current", action="store_true",
        help="Include current target in results",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel workers for analysis (default: 4)",
    )
    parser.add_argument(
        "--max-candidates", type=int, default=100,
        help="Max candidates to analyze after leaderboard merge (0=unlimited, default: 100)",
    )

    args = parser.parse_args()

    # Parse categories
    categories = [c.strip().upper() for c in args.categories.split(",")]

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
        min_markets=args.min_markets,
        min_win_rate=args.min_win_rate,
        min_profit_factor=args.min_profit_factor,
        max_days_inactive=args.max_days_inactive,
        limit=args.limit,
        sort=args.sort,
        deep=args.deep,
        leaderboard_pages=args.leaderboard_pages,
        categories=categories,
        include_current=args.include_current,
        current_target=current_target,
        workers=args.workers,
        max_candidates=args.max_candidates,
    )

    print(format_results(results, as_json=args.as_json, deep=args.deep))


if __name__ == "__main__":
    main()