#!/usr/bin/env python3
"""
Monitor script for Polymarket Follower Bot
Checks if follower is correctly copying target's big trades.
Generates a Markdown report for LLM agent review.
"""

import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from datetime import datetime, timezone

from utils.utils import get_follow_address, POLY_MARKET_FUNDER_ADDRESS, ETHERSCAN_API_KEY

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE_PATH = PROJECT_ROOT / "logs" / "polymarket_follower.log"
REPORTS_DIR = PROJECT_ROOT / "reports"
PID_FILE = Path(__file__).resolve().parent.parent / "follower.pid"


# ========================================================================== #
#  Polymarket API helpers (read-only, no trading SDK needed)                  #
# ========================================================================== #

def fetch_activities(address: str, limit: int = 100, start_ts: int = None):
    """Fetch activity for an address.  Uses `start` param (unix seconds)."""
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "limit": limit,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC",
    }
    if start_ts:
        params["start"] = start_ts
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_positions(address: str, limit: int = 100):
    """Fetch current open positions (sorted by value DESC)."""
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": address,
        "limit": limit,
        "sortBy": "CURRENT",
        "sortDirection": "DESC",
        "sizeThreshold": 0,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_portfolio_value(address: str) -> float:
    """Return total position value in USDC for *address*."""
    url = "https://data-api.polymarket.com/value"
    resp = requests.get(url, params={"user": address}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data:
        return float(data[0].get("value", 0))
    return 0.0


def fetch_on_chain_usdc(address: str) -> float:
    """Return on-chain USDC balance (Polygon) via Etherscan v2."""
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": 137,
        "module": "account",
        "action": "tokenbalance",
        "contractaddress": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "address": address,
        "tag": "latest",
        "apikey": ETHERSCAN_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return int(resp.json().get("result", 0)) / 1e6


# ========================================================================== #
#  Bot status helpers                                                         #
# ========================================================================== #

def is_bot_running() -> bool:
    """Check whether the follower process is alive via PID file."""
    if not PID_FILE.exists():
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def get_bot_start_timestamp():
    """Parse timestamp from first log line.  Returns datetime | None."""
    if not LOG_FILE_PATH.exists():
        return None
    with open(LOG_FILE_PATH, "r") as f:
        first_line = f.readline().strip()
    if not first_line:
        return None
    try:
        s = first_line.find("[")
        e = first_line.find("]")
        if s == -1 or e == -1:
            return None
        dt = datetime.strptime(first_line[s + 1 : e], "%d-%B-%Y-%H:%M:%S")
        return dt
    except (ValueError, IndexError):
        return None


def format_uptime(start_dt: datetime) -> str:
    delta = datetime.now() - start_dt
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


# ========================================================================== #
#  Log searcher                                                               #
# ========================================================================== #

def fetch_log_entries(search_terms: list, context_lines: int = 3):
    """Return list of (line_number, [context_lines]) matching any term."""
    if not LOG_FILE_PATH.exists():
        return []
    results = []
    with open(LOG_FILE_PATH, "r") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        for term in search_terms:
            if term.lower() in line.lower():
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                results.append((i + 1, lines[start:end]))
                break
    return results


# ========================================================================== #
#  Core analysis                                                              #
# ========================================================================== #

def calculate_min_target_order(target_portfolio: float, follower_total: float) -> float:
    """Min USDC a target trade must be for the follower to copy it.
    The follower copies proportionally: target_trade / target_portfolio * follower_total.
    The minimum resulting follower order is $1, so:
        min_target = 1.0 * target_portfolio / follower_total
    """
    if follower_total <= 0:
        return float("inf")
    return 1.0 * target_portfolio / follower_total


def analyse_trades(
    target_activities,
    follower_activities,
    min_order: float,
    follower_positions,
    target_portfolio_value: float,
    follower_total_value: float,
):
    """Return (issues: list[str], matched_pairs: list[dict], missed: list, allocation_issues: list)."""
    issues: list[str] = []
    matched_pairs: list[dict] = []
    allocation_issues: list[dict] = []

    target_trades = [a for a in target_activities if a.get("type") == "TRADE"]
    follower_trades = [a for a in follower_activities if a.get("type") == "TRADE"]

    # Index follower trades by conditionId
    follower_by_cid: dict[str, list] = {}
    for t in follower_trades:
        cid = t.get("conditionId")
        follower_by_cid.setdefault(cid, []).append(t)

    follower_cids_with_position = {
        p.get("conditionId")
        for p in follower_positions
        if float(p.get("currentValue", 0)) > 0
    }

    buys_above = [t for t in target_trades if t.get("side") == "BUY" and float(t.get("usdcSize", 0)) >= min_order]
    buys_below = [t for t in target_trades if t.get("side") == "BUY" and float(t.get("usdcSize", 0)) < min_order]
    sells = [t for t in target_trades if t.get("side") == "SELL"]

    # --- Check missed buys ---
    missed = []
    for t in buys_above:
        cid = t.get("conditionId")
        if cid not in follower_by_cid:
            missed.append(t)
        else:
            # Found matching follower trade(s) for this conditionId
            f_trades = follower_by_cid[cid]
            # Sum follower USDC for this cid
            f_usdc = sum(float(ft.get("usdcSize", 0)) for ft in f_trades if ft.get("side") == "BUY")
            t_usdc = float(t.get("usdcSize", 0))

            # The bot copies proportionally:
            #   expected_follower = (target_usdc / target_portfolio) * follower_total
            # Check if the actual follower trade is within 20% of expected.
            expected_follower = (t_usdc / target_portfolio_value * follower_total_value) if target_portfolio_value > 0 else 0
            if expected_follower > 0:
                deviation_pct = abs(f_usdc - expected_follower) / expected_follower * 100
            else:
                deviation_pct = 0.0

            matched_pairs.append({
                "title": t.get("title", "Unknown"),
                "slug": t.get("slug", ""),
                "eventSlug": t.get("eventSlug", ""),
                "target_usdc": t_usdc,
                "follower_usdc": f_usdc,
                "expected_follower": expected_follower,
                "deviation_pct": deviation_pct,
            })

            if deviation_pct > 20.0:
                allocation_issues.append(matched_pairs[-1])

    if missed:
        issues.append(f"MISSED {len(missed)} BUY trade(s) above threshold")

    if allocation_issues:
        issues.append(f"{len(allocation_issues)} matched trade(s) with >20% size deviation from expected")

    return issues, matched_pairs, missed, allocation_issues


# ========================================================================== #
#  Markdown report generation                                                 #
# ========================================================================== #

def polymarket_link(event_slug: str, slug: str = "") -> str:
    if not event_slug:
        return ""
    base = f"https://polymarket.com/event/{event_slug}"
    if slug:
        base += f"/{slug}"
    return base


def generate_report(
    *,
    now: datetime,
    bot_running: bool,
    bot_start_dt,
    target_address: str,
    follower_address: str,
    target_cash: float,
    target_positions_value: float,
    follower_cash: float,
    follower_positions_value: float,
    target_positions: list,
    follower_positions: list,
    min_order: float,
    issues: list[str],
    matched_pairs: list[dict],
    missed: list,
    allocation_issues: list[dict],
) -> str:
    lines: list[str] = []

    date_str = now.strftime("%B %d %Y")
    lines.append(f"# Polymarket Follower Bot Report")
    lines.append(f"**Date:** {date_str}  ")
    lines.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M:%S')}  ")
    lines.append("")

    # --- Status ---
    lines.append("## Bot Status")
    status = "Running" if bot_running else "Off"
    lines.append(f"- **Status:** {status}")
    if bot_start_dt:
        lines.append(f"- **Started:** {bot_start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"- **Uptime:** {format_uptime(bot_start_dt)}")
    else:
        lines.append("- **Uptime:** Unknown (no log file)")
    lines.append(f"- **Min target order to copy:** ${min_order:,.2f}")
    lines.append("")

    # --- Balances ---
    target_total = target_cash + target_positions_value
    follower_total = follower_cash + follower_positions_value

    lines.append("## Balances")
    lines.append("")
    lines.append("**Target**")
    lines.append(f"- Cash: ${target_cash:,.2f}")
    lines.append(f"- Positions: ${target_positions_value:,.2f}")
    lines.append(f"- Total: ${target_total:,.2f}")
    lines.append(f"- Address: `{target_address}`")
    lines.append("")
    lines.append("**Follower**")
    lines.append(f"- Cash: ${follower_cash:,.2f}")
    lines.append(f"- Positions: ${follower_positions_value:,.2f}")
    lines.append(f"- Total: ${follower_total:,.2f}")
    lines.append(f"- Address: `{follower_address}`")
    lines.append("")

    # --- Top 5 Positions ---
    def _positions_list(positions: list, total_value: float) -> list[str]:
        out = []
        for i, p in enumerate(positions[:5], 1):
            val = float(p.get("currentValue", 0))
            title = p.get("title", "Unknown")
            slug = p.get("slug", "")
            event_slug = p.get("eventSlug", "")
            pct = (val / total_value * 100) if total_value > 0 else 0
            link = polymarket_link(event_slug, slug)
            if link:
                out.append(f"{i}. **[{title}]({link})**")
            else:
                out.append(f"{i}. **{title}**")
            out.append(f"   ${val:,.2f} ({pct:.1f}%)")
        return out

    lines.append("## Top 5 Positions — Target")
    lines.extend(_positions_list(target_positions, target_total))
    lines.append("")

    lines.append("## Top 5 Positions — Follower")
    lines.extend(_positions_list(follower_positions, follower_total))
    lines.append("")

    # --- Issues ---
    issue_num = 0
    has_issues = missed or allocation_issues

    if not has_issues:
        lines.append("## Trade Sync")
        lines.append("No issues detected. All above-threshold target BUYs have been copied.")
        lines.append("")
    else:
        lines.append("## ISSUES FOUND")
        lines.append("")

        # Missed trades
        for t in missed[:10]:
            issue_num += 1
            usdc = float(t.get("usdcSize", 0))
            title = t.get("title", "Unknown")
            event_slug = t.get("eventSlug", "")
            slug = t.get("slug", "")
            link = polymarket_link(event_slug, slug)

            lines.append(f"### Issue {issue_num} — Missed Trade")
            title_md = f"[{title}]({link})" if link else title
            lines.append(f"**Market:** {title_md}")
            lines.append(f"**Type:** Target BUY above threshold was NOT copied by follower")
            lines.append(f"**Target trade size:** ${usdc:,.2f}")
            lines.append("")

            # Per-issue log lines
            log_entries = fetch_log_entries([title[:40]], context_lines=2)
            if log_entries:
                lines.append("**Relevant logs:**")
                lines.append("```")
                seen = set()
                for line_num, ctx_lines in log_entries[:5]:
                    if line_num in seen:
                        continue
                    seen.add(line_num)
                    for l in ctx_lines:
                        lines.append(l.rstrip())
                    lines.append("")
                lines.append("```")
            lines.append("")

        # Size deviations
        for a in allocation_issues[:10]:
            issue_num += 1
            title = a["title"]
            slug = a.get("slug", "")
            event_slug = a.get("eventSlug", "")
            link = polymarket_link(event_slug, slug)

            lines.append(f"### Issue {issue_num} — Size Deviation")
            title_md = f"[{title}]({link})" if link else title
            lines.append(f"**Market:** {title_md}")
            lines.append(f"**Type:** Follower trade size deviates >20% from expected")
            lines.append(f"**Target traded:** ${a['target_usdc']:,.2f}")
            lines.append(f"**Expected follower trade:** ${a['expected_follower']:,.2f}")
            lines.append(f"**Actual follower trade:** ${a['follower_usdc']:,.2f}")
            lines.append(f"**Deviation:** {a['deviation_pct']:.0f}%")
            lines.append("")

            # Per-issue log lines
            log_entries = fetch_log_entries([title[:40]], context_lines=2)
            if log_entries:
                lines.append("**Relevant logs:**")
                lines.append("```")
                seen = set()
                for line_num, ctx_lines in log_entries[:5]:
                    if line_num in seen:
                        continue
                    seen.add(line_num)
                    for l in ctx_lines:
                        lines.append(l.rstrip())
                    lines.append("")
                lines.append("```")
            lines.append("")

    # --- Matched trades summary (no issues, just info) ---
    if matched_pairs:
        lines.append("## Matched Trades")
        for m in matched_pairs[:15]:
            status = "✅" if m["deviation_pct"] <= 20 else "⚠️"
            lines.append(
                f"- {status} **{m['title'][:50]}** — target ${m['target_usdc']:,.2f} "
                f"/ follower ${m['follower_usdc']:,.2f} (expected ${m['expected_follower']:,.2f}, {m['deviation_pct']:.0f}% off)"
            )
        lines.append("")

    # --- LLM section placeholder ---
    lines.append("## Agent Notes")
    lines.append("_This section is reserved for the LLM agent to append findings, actions taken, or general comments._")
    lines.append("")

    return "\n".join(lines)


# ========================================================================== #
#  Main                                                                       #
# ========================================================================== #

def main():
    print(f"\n{'='*80}")
    print(f"Polymarket Follower Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")

    try:
        target_address = get_follow_address()
        follower_address = POLY_MARKET_FUNDER_ADDRESS

        # --- Bot status ---
        bot_running = is_bot_running()
        bot_start_dt = get_bot_start_timestamp()
        print(f"\nBot running: {'Yes' if bot_running else 'No'}")
        if bot_start_dt:
            print(f"Bot started: {bot_start_dt.strftime('%Y-%m-%d %H:%M:%S')}  (uptime: {format_uptime(bot_start_dt)})")
        bot_start_ts = int(bot_start_dt.timestamp()) if bot_start_dt else None

        # --- Balances ---
        print("\nFetching balances...")
        target_positions_value = fetch_portfolio_value(target_address)
        follower_positions_value = fetch_portfolio_value(follower_address)
        target_cash = fetch_on_chain_usdc(target_address)
        follower_cash = fetch_on_chain_usdc(follower_address)
        target_total = target_cash + target_positions_value
        follower_total = follower_cash + follower_positions_value

        print(f"  Target  — cash: ${target_cash:,.2f}  positions: ${target_positions_value:,.2f}  total: ${target_total:,.2f}")
        print(f"  Follower — cash: ${follower_cash:,.2f}  positions: ${follower_positions_value:,.2f}  total: ${follower_total:,.2f}")

        min_order = calculate_min_target_order(target_positions_value, follower_total)
        print(f"  Min target order to copy: ${min_order:,.2f}")

        # --- Positions ---
        print("\nFetching positions...")
        target_positions = fetch_positions(target_address)
        follower_positions = fetch_positions(follower_address)
        print(f"  Target positions: {len(target_positions)}")
        print(f"  Follower positions: {len(follower_positions)}")

        # --- Activities ---
        print("\nFetching activities...")
        target_activities = fetch_activities(target_address, limit=100, start_ts=bot_start_ts)
        follower_activities = fetch_activities(follower_address, limit=100, start_ts=bot_start_ts)
        # Safety filter
        if bot_start_ts:
            target_activities = [a for a in target_activities if a.get("timestamp", 0) >= bot_start_ts]
            follower_activities = [a for a in follower_activities if a.get("timestamp", 0) >= bot_start_ts]
        print(f"  Target activities (since bot start): {len(target_activities)}")
        print(f"  Follower activities (since bot start): {len(follower_activities)}")

        # --- Analysis ---
        print("\nAnalysing trades...")
        issues, matched_pairs, missed, allocation_issues = analyse_trades(
            target_activities,
            follower_activities,
            min_order,
            follower_positions,
            target_positions_value,
            follower_total,
        )

        # --- Print issues to console ---
        if issues:
            print(f"\n--- Issues Found ---")
            for issue in issues:
                print(f"  ⚠️  {issue}")
        else:
            print("\n  ✓ No issues detected")

        # --- Generate report ---
        now = datetime.now()
        report_md = generate_report(
            now=now,
            bot_running=bot_running,
            bot_start_dt=bot_start_dt,
            target_address=target_address,
            follower_address=follower_address,
            target_cash=target_cash,
            target_positions_value=target_positions_value,
            follower_cash=follower_cash,
            follower_positions_value=follower_positions_value,
            target_positions=target_positions,
            follower_positions=follower_positions,
            min_order=min_order,
            issues=issues,
            matched_pairs=matched_pairs,
            missed=missed,
            allocation_issues=allocation_issues,
        )

        # --- Save report ---
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"report_{now.strftime('%Y-%m-%d_%H%M%S')}.md"
        report_path = REPORTS_DIR / filename
        with open(report_path, "w") as f:
            f.write(report_md)

        print(f"\n{'='*80}")
        print(f"Report saved: {report_path}")
        print(f"{'='*80}\n")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
