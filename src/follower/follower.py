import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from datetime import datetime, timedelta
from utils.utils import *
from follower.helpers import *
from utils.logger import Logger, LogType

PID_FILE = Path(__file__).parent.parent / "follower.pid"
LAST_PROCESSED_TS_FILE = Path(__file__).parent.parent / "last_processed_ts.txt"


def get_last_processed_ts():
    """Get the timestamp of the last processed activity."""
    if LAST_PROCESSED_TS_FILE.exists():
        try:
            with open(LAST_PROCESSED_TS_FILE, 'r') as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            return None
    return None


def save_last_processed_ts(ts: int):
    """Save the timestamp of the last processed activity."""
    with open(LAST_PROCESSED_TS_FILE, 'w') as f:
        f.write(str(ts))


def check_single_instance():
    """Ensure only one follower process is running."""
    if PID_FILE.exists():
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Check if process is still running
            os.kill(old_pid, 0)  # Raises OSError if process doesn't exist
            print(f"Follower already running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except (ValueError, OSError):
            # PID file exists but process is dead, remove it
            PID_FILE.unlink()
    
    # Write our PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def cleanup_pid():
    """Remove PID file on exit."""
    if PID_FILE.exists():
        try:
            with open(PID_FILE, 'r') as f:
                stored_pid = int(f.read().strip())
            if stored_pid == os.getpid():
                PID_FILE.unlink()
        except (ValueError, OSError):
            pass


def main():
    check_single_instance()
    
    logger = Logger()
    
    logger.log("Starting follower...")
    
    # Load current target from file (persists across restarts)
    current_target_address = get_current_target_address()
    logger.log(f"Loaded current target from file: {current_target_address}")
    
    while True:
        try:
            logger.log(f"Current target address: {current_target_address}")

            logger.log(f"Fetching target address...")
            try:
                target_address = get_follow_address()
            except Exception as e:
                logger.log(str(e), LogType.ERROR)
                logger.log(f"Sleeping for {FOLLOWER_CHECK_INTERVAL_MINUTES} minutes...")
                time.sleep(FOLLOWER_CHECK_INTERVAL_MINUTES * 60)
                continue

            logger.log(f"Target address: {target_address}")
            
            if target_address != current_target_address:
                if current_target_address is None:
                    logger.log(f"No previous target address, starting fresh with {target_address}")
                else:
                    logger.log(f"Target address changed from {current_target_address} to {target_address}")
                    logger.log("Selling all positions due to target change...")
                    sell_all_positions()
                
                clear_consumed_transactions()
                # Reset last processed timestamp on target change
                if LAST_PROCESSED_TS_FILE.exists():
                    LAST_PROCESSED_TS_FILE.unlink()
                current_target_address = target_address
                save_current_target_address(current_target_address)
                continue

            
            now = datetime.now()
            
            # Use last processed timestamp if available, otherwise use interval-based
            last_processed_ts = get_last_processed_ts()
            if last_processed_ts:
                interval_ago_ts = last_processed_ts
                logger.log(f"Using last processed timestamp: {datetime.fromtimestamp(interval_ago_ts)}")
            else:
                interval_ago_ts = int((now - timedelta(minutes=FOLLOWER_CHECK_INTERVAL_MINUTES)).timestamp())
                logger.log(f"Using interval-based timestamp: {datetime.fromtimestamp(interval_ago_ts)}")
            

            target_activities = fetch_activities(target_address, interval_ago_ts, limit=50)

            process_new_activities(target_activities)

            # Save timestamp AFTER processing so we don't skip activities on crash
            if target_activities:
                latest_ts = max(a.get('timestamp', 0) for a in target_activities)
                if latest_ts > 0:
                    save_last_processed_ts(latest_ts + 1)

            # Prune consumed_transactions: since `start` param now correctly filters,
            # we'll never re-see activities older than last_processed_ts.
            # Safe to clear the consumed set after advancing the timestamp.
            clear_consumed_transactions()

            for remaining in range(FOLLOWER_CHECK_INTERVAL_MINUTES * 60, 0, -1):
                minutes = remaining // 60
                seconds = remaining % 60
                print(f"\rNext check in: {minutes:02d}:{seconds:02d}", end="", flush=True)
                time.sleep(1)
            print()

            
        except KeyboardInterrupt:
            logger.log("Follower stopped by user.", LogType.INFO)
            save_current_target_address(current_target_address)
            logger.log(f"Saved current target address: {current_target_address}")
            cleanup_pid()
            break
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            save_current_target_address(current_target_address)
            logger.log(f"Saved current target address: {current_target_address}")
            logger.log(f"Sleeping for {FOLLOWER_CHECK_INTERVAL_MINUTES} minutes before retry...")
            time.sleep(FOLLOWER_CHECK_INTERVAL_MINUTES * 60)
            continue
            


if __name__ == "__main__":
    main()