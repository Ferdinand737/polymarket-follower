import time
from datetime import datetime, timedelta
from utils.utils import *
from polymarket.helpers import *
from utils.logger import Logger, Whomst, LogType


def main():

    current_target_address = None

    logger = Logger(Whomst.POLYMARKET_FOLLOWER)
    
    logger.log("Starting follower...")
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
                logger.log(f"Target address changed from {current_target_address} to {target_address}")

                sell_all_positions()

                current_target_address = target_address
                continue

            
            now = datetime.now()
            interval_ago_ts = int((now - timedelta(minutes=FOLLOWER_CHECK_INTERVAL_MINUTES)).timestamp())
            

            target_activities = fetch_activities(target_address, interval_ago_ts)

            user_activities = fetch_activities(POLY_MARKET_FUNDER_ADDRESS, interval_ago_ts)

            new_activities = compare_activities(target_activities, user_activities)

            process_new_activities(new_activities)

            for remaining in range(FOLLOWER_CHECK_INTERVAL_MINUTES * 60, 0, -1):
                minutes = remaining // 60
                seconds = remaining % 60
                print(f"\rNext check in: {minutes:02d}:{seconds:02d}", end="", flush=True)
                time.sleep(1)
            print()

            
        except KeyboardInterrupt:
            logger.log("Follower stopped by user.", LogType.INFO)
            break
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            break
            


if __name__ == "__main__":
    main()