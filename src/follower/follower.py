import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from datetime import datetime, timedelta
from utils.utils import *
from follower.helpers import *
from utils.logger import Logger, LogType


def main():

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
                current_target_address = target_address
                save_current_target_address(current_target_address)
                continue

            
            now = datetime.now()
            interval_ago_ts = int((now - timedelta(minutes=FOLLOWER_CHECK_INTERVAL_MINUTES)).timestamp())
            

            target_activities = fetch_activities(target_address, interval_ago_ts)

            process_new_activities(target_activities)

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
            break
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            save_current_target_address(current_target_address)
            logger.log(f"Saved current target address: {current_target_address}")
            break
            


if __name__ == "__main__":
    main()