#!/usr/bin/env python3
"""
Polymarket Follower Bot Report
Generates standardized reports for cron-based monitoring.
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from follower.helpers import (
    get_portfolio_usdc_value, 
    get_on_chain_usdc_balance, 
    POLY_MARKET_FUNDER_ADDRESS,
    fetch_positions,
    fetch_activities
)
from utils.utils import get_follow_address

# Paths
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"
LOG_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "polymarket_follower.log"
STATE_FILE = REPORTS_DIR / "last_report_state.json"


def ensure_reports_dir():
    """Create reports directory if it doesn't exist."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def get_bot_uptime():
    """Get bot uptime from log file. Returns human-readable string."""
    if not LOG_FILE.exists():
        return "Unknown"
    
    with open(LOG_FILE, 'r') as f:
        first_line = f.readline().strip()
    
    if not first_line:
        return "Unknown"
    
    try:
        start = first_line.find('[')
        end = first_line.find(']')
        if start == -1 or end == -1:
            return "Unknown"
        
        timestamp_str = first_line[start+1:end]
        start_dt = datetime.strptime(timestamp_str, "%d-%B-%Y-%H:%M:%S")
        now = datetime.now()
        delta = now - start_dt
        
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except (ValueError, IndexError):
        return "Unknown"


def is_bot_running():
    """Check if follower process is running."""
    try:
        result = subprocess.run(['pgrep', '-f', 'follower'], capture_output=True)
        return result.returncode == 0
    except:
        return False


def load_last_state():
    """Load state from last report."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"last_ts": 0, "successful_trades": [], "failed_trades": []}


def save_state(state):
    """Save state for next report."""
    ensure_reports_dir()
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def parse_log_for_trades(since_ts):
    """Parse log file for trades since timestamp.
    
    Returns (successful, failed) lists of trade descriptions.
    """
    if not LOG_FILE.exists():
        return [], []
    
    successful = []
    failed = []
    
    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        # Look for successful fills
        if "FAK order filled" in line:
            # Extract trade details from previous lines
            for j in range(max(0, i-5), i):
                if "Buying activity:" in lines[j] or "Selling activity:" in lines[j]:
                    title = lines[j].split(": ", 1)[-1].strip() if ": " in lines[j] else "Unknown"
                    successful.append(title[:50])
                    break
        
        # Look for failures
        elif "Could not fill" in line or "ERROR" in line:
            # Extract context
            for j in range(max(0, i-5), i):
                if "Buying activity:" in lines[j] or "Selling activity:" in lines[j]:
                    title = lines[j].split(": ", 1)[-1].strip() if ": " in lines[j] else "Unknown"
                    failed.append(title[:50])
                    break
    
    return successful, failed


def generate_report():
    """Generate standardized report."""
    ensure_reports_dir()
    
    # Get current state
    target_address = get_follow_address()
    user_portfolio = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)
    user_cash = get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
    target_portfolio = get_portfolio_usdc_value(target_address)
    
    # Min order threshold (same as monitor.py)
    min_order = target_portfolio / (user_portfolio + user_cash) if (user_portfolio + user_cash) > 0 else 0
    
    # Bot status
    bot_running = is_bot_running()
    bot_uptime = get_bot_uptime()
    
    # Get trades from log
    successful_trades, failed_trades = parse_log_for_trades(0)
    
    # Limit to recent trades (last 10 of each)
    recent_successful = successful_trades[-10:] if successful_trades else []
    recent_failed = failed_trades[-10:] if failed_trades else []
    
    # Get target positions (top 5 by value)
    try:
        target_positions = fetch_positions(target_address)
        positions_by_value = sorted(
            [p for p in target_positions if float(p.get('currentValue', 0)) > 0],
            key=lambda x: float(x.get('currentValue', 0)),
            reverse=True
        )[:5]
    except:
        positions_by_value = []
    
    # Build report
    now = datetime.now().strftime("%B %d %Y")
    
    report_lines = [
        f"📊 Polymarket Follower Report",
        f"📅 {now}",
        f"",
        f"🤖 Bot Status: {'✅ Running' if bot_running else '❌ Not Running'}",
        f"⏱ Uptime: {bot_uptime}",
        f"",
        f"💰 Balances:",
        f"  Cash: ${user_cash:,.2f}",
        f"  Positions: ${user_portfolio:,.2f}",
        f"  Total: ${user_portfolio + user_cash:,.2f}",
        f"",
        f"📈 Target Portfolio: ${target_portfolio:,.2f}",
        f"📏 Min Order Threshold: ${min_order:,.2f}",
        f"",
    ]
    
    # Target positions
    if positions_by_value:
        report_lines.append("🎯 Target Top Positions:")
        for pos in positions_by_value:
            title = pos.get('title', 'Unknown')[:35]
            value = float(pos.get('currentValue', 0))
            report_lines.append(f"  • ${value:,.0f} - {title}")
        report_lines.append("")
    
    if recent_successful:
        report_lines.append(f"✅ Recent Successful Trades ({len(recent_successful)}):")
        for trade in recent_successful[-5:]:
            report_lines.append(f"  • {trade}")
        report_lines.append(f"")
    
    if recent_failed:
        report_lines.append(f"❌ Recent Failed Trades ({len(recent_failed)}):")
        for trade in recent_failed[-5:]:
            report_lines.append(f"  • {trade}")
        report_lines.append(f"")
    
    if not recent_successful and not recent_failed:
        report_lines.append(f"📭 No trades in recent logs")
        report_lines.append(f"")
    
    report = "\n".join(report_lines)
    
    # Save to file
    report_file = REPORTS_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w') as f:
        f.write(report)
    
    return report, report_file


def send_telegram_report(report):
    """Send report to Ferdinand via Telegram."""
    import subprocess
    try:
        result = subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'telegram',
            '--target', '7513671476',
            '-m', report
        ], capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        print(f"Failed to send Telegram: {e}")
        return False


def main():
    """Main entry point."""
    report, report_file = generate_report()
    print(report)
    print(f"\nReport saved to: {report_file}")
    
    # Send to Telegram
    if send_telegram_report(report):
        print("✅ Report sent to Telegram")
    else:
        print("❌ Failed to send to Telegram")
    
    return report


if __name__ == "__main__":
    main()