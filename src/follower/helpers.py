import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.utils import *
import requests
from requests.exceptions import RequestException
from typing import List, Dict, Any
from utils.logger import Logger, LogType
from datetime import datetime
import time

    
from py_clob_client import ClobClient, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from py_builder_relayer_client.client import RelayClient, BuilderConfig
from py_builder_relayer_client.models import SafeTransaction, OperationType
from web3 import Web3
from decimal import Decimal
from math import gcd


logger = Logger()



def is_transient_error(error: Exception) -> bool:
    """Check if an error is transient and worth retrying."""
    if isinstance(error, requests.exceptions.HTTPError):
        status_code = error.response.status_code if error.response else 0
        return status_code in (502, 503, 504, 429)
    if isinstance(error, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    return False


def with_retry(func, max_retries: int = 3, base_delay: float = 1.0, backoff_factor: float = 2.0):
    """Wrap a function with retry logic for transient errors."""
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if is_transient_error(e) and attempt < max_retries - 1:
                    delay = base_delay * (backoff_factor ** attempt)
                    logger.log(f"Transient error on attempt {attempt + 1}/{max_retries}, retrying in {delay}s: {e}", LogType.WARNING)
                    time.sleep(delay)
                else:
                    raise
        raise last_error
    return wrapper



def calculate_valid_size(usdc_amount: float, price: float, decimals: int = 4) -> float:
    """
    Calculate a valid order size that ensures:
    - Maker amount (size * price) has max 2 decimals
    - Taker amount (size) has max 4 decimals
    
    The Polymarket API requires:
    - maker_amount = size * price * 10^6 (must result in integer with max 2 decimal precision)
    - taker_amount = size * 10^6 (must result in integer with max 4 decimal precision)
    
    Uses Decimal for exact arithmetic to avoid floating-point precision issues.
    """
    if usdc_amount <= 0 or price <= 0:
        return 0.0
    
    # Use Decimal for exact arithmetic
    usdc = Decimal(str(usdc_amount))
    p = Decimal(str(price))
    
    price_cents = int(p * 100)  # Price in cents (integer)
    scale = Decimal(10 ** decimals)
    
    # size_raw * price_cents must be divisible by scale for maker amount to have <= 2 decimals
    step = scale / Decimal(gcd(price_cents, int(scale)))
    
    # Calculate max size in raw units
    max_size_raw = int(usdc * scale / p)
    
    # Round down to nearest valid step
    size_raw = int(Decimal(max_size_raw) / step) * step
    
    # Convert back to float with exact precision
    size = float(Decimal(size_raw) / scale)
    
    # Round to exact decimals to avoid any floating-point representation issues
    return round(size, decimals)


client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137)
user_api_creds = client.create_or_derive_api_creds()


def create_clob_client():
    """Create a fresh ClobClient. Called at startup and periodically to prevent stale state."""
    return ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        creds=user_api_creds,
        signature_type=2,
        funder=POLY_MARKET_FUNDER_ADDRESS
    )

client = create_clob_client()
 
builder_config = BuilderConfig(
    local_builder_creds=BuilderApiKeyCreds(
        key=os.getenv("POLY_MARKET_API_KEY"),
        secret=os.getenv("POLY_MARKET_SECRET"),
        passphrase=os.getenv("POLY_MARKET_PASSPHRASE"),
    )
)

builder_client = RelayClient(
    "https://relayer-v2.polymarket.com",
    137,
    PRIVATE_KEY,
    builder_config,
)

def sell_all_positions():
    logger.log("Selling all positions...")
    positions = fetch_positions(POLY_MARKET_FUNDER_ADDRESS)
    for position in positions:
        sell_position(position)



def sell_position(position: Dict[str, Any]):
    logger.log(f"Selling position: ${position.get('currentValue')} {position.get('title')}")

    target_price = position.get("curPrice")
    size = round(position.get("size"), 4)
    token_id = position.get("asset")

    current_price = target_price + 0.01
    min_price = max(0.01, target_price - 0.02)
    
    while current_price >= min_price:
        logger.log(f"Attempting to sell {size} shares at price: {current_price}")

        order_args = OrderArgs(
            price=current_price,
            size=size,
            side=SELL,
            token_id=token_id,
        )

        try:
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        try:
            resp = client.post_order(signed_order, OrderType.FAK)
            status = resp.get("status")
            if status == "MATCHED":
                logger.log(f"FAK order filled! Sold {size} shares at {current_price}")
                return
            else:
                logger.log(f"FAK order not filled at {current_price}, adjusting price...")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            # Continue to next price instead of giving up
            current_price = round(current_price - 0.01, 2)
            continue

        current_price = round(current_price - 0.01, 2)

    logger.log(f"Could not fill order within 0.02 of target price {target_price}, giving up.", LogType.WARNING)



def fetch_positions(address: str):
    logger.log(f"Fetching positions for {address}")
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": address
    }
    
    def _fetch():
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    try:
        return with_retry(_fetch, max_retries=3, base_delay=1.0)()
    except Exception as e:
        logger.log(f"Failed to fetch positions after retries: {e}", LogType.ERROR)
        raise


def fetch_activities(address: str, interval_ago_ts: int = None, market: str = None, limit: int = 10):
    address_type = "target" if address != POLY_MARKET_FUNDER_ADDRESS else "user"
    logger.log(f"Fetching activities for {address} ({address_type})")
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "limit": limit,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC"
    }
    if interval_ago_ts:
        params["start"] = interval_ago_ts
    if market:
        params["market"] = market
    
    def _fetch():
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    try:
        return with_retry(_fetch, max_retries=3, base_delay=1.0)()
    except Exception as e:
        logger.log(f"Failed to fetch activities after retries: {e}", LogType.ERROR)
        raise


def process_new_activities(new_target_activities: List[Dict[str, Any]]):
    logger.log(f"Processing {len(new_target_activities)} new activities")
    
    consumed = get_consumed_transactions()
    
    # Aggregate BUY trades by conditionId to handle multiple small trades
    # This prevents position-based fallback from triggering multiple times
    aggregated_buys = {}  # conditionId -> {total_usdc, first_activity, all_hashes}
    processed_condition_ids = set()  # Track which conditionIds we've already processed
    
    for target_activity in new_target_activities:
        tx_hash = target_activity.get("transactionHash")
        if tx_hash in consumed:
            continue
        
        if target_activity.get("type") == "TRADE" and target_activity.get("side") == "BUY":
            condition_id = target_activity.get("conditionId")
            usdc_size = float(target_activity.get("usdcSize", 0))
            
            if condition_id not in aggregated_buys:
                aggregated_buys[condition_id] = {
                    "total_usdc": 0,
                    "first_activity": target_activity,
                    "all_hashes": []
                }
            
            aggregated_buys[condition_id]["total_usdc"] += usdc_size
            aggregated_buys[condition_id]["all_hashes"].append(tx_hash)
    
    # Now process all activities
    for target_activity in new_target_activities:
        tx_hash = target_activity.get("transactionHash")
        if tx_hash in consumed:
            continue
            
        match target_activity.get("type"):
            case "TRADE":
                asset = target_activity.get("asset")
                side = target_activity.get("side")
                condition_id = target_activity.get("conditionId")
                
                # For BUYs: check if this is part of an aggregated trade
                if side == "BUY" and condition_id in aggregated_buys:
                    agg = aggregated_buys[condition_id]
                    
                    # Check if we already processed this conditionId
                    if condition_id in processed_condition_ids:
                        # Skip - already processed as aggregated trade
                        add_consumed_transactions([tx_hash])
                        continue
                    
                    # Mark as processed
                    processed_condition_ids.add(condition_id)
                    
                    # Use the aggregated data
                    activity_to_process = dict(agg["first_activity"])
                    activity_to_process["usdcSize"] = agg["total_usdc"]
                    logger.log(f"Aggregated {len(agg['all_hashes'])} trades for conditionId {condition_id}, total: ${agg['total_usdc']:.2f}")
                    
                    # Process the aggregated trade
                    success = buy_activity(activity_to_process)
                    # Mark as consumed regardless of success - failed trades are logged and skipped
                    if not success:
                        logger.log(f"Aggregated buy failed, skipping {len(agg['all_hashes'])} tx(s)", LogType.WARNING)
                    add_consumed_transactions(agg["all_hashes"])
                    continue
                
                # Get user position for SELL activities
                user_token_position = None
                if side == 'SELL':
                    positions = fetch_positions(POLY_MARKET_FUNDER_ADDRESS)
                    user_token_position = next((p for p in positions if p.get("asset") == asset), None)
                    if not user_token_position:
                        logger.log("User does not hold shares, skipping.", log_type=LogType.WARNING)
                        add_consumed_transactions([tx_hash])
                        continue
                
                # Process SELL trades normally
                success = False
                if side == "SELL":
                    success = sell_activity(target_activity, user_token_position)
                    if not success:
                        logger.log("Sell failed, skipping tx", LogType.WARNING)
                    add_consumed_transactions([tx_hash])
                else:
                    # BUY (non-aggregated) - shouldn't reach here but handle it
                    add_consumed_transactions([tx_hash])
                
            case "SPLIT":
                split_activity(target_activity)
                add_consumed_transactions([tx_hash])
            case "MERGE":
                merge_activity(target_activity)
                add_consumed_transactions([tx_hash])
            case "REDEEM":
                # Auto-redeem is now handled by Polymarket natively
                add_consumed_transactions([tx_hash])
            case "REWARD":
                add_consumed_transactions([tx_hash])
            case "CONVERSION":
                convert_activity(target_activity)
                add_consumed_transactions([tx_hash])
            case "MAKER_REBATE":
                add_consumed_transactions([tx_hash])
            case "YIELD":
                # Yield activities are passive rewards, just mark as consumed
                add_consumed_transactions([tx_hash])
            case _:
                logger.log(f"Unknown activity type: {target_activity.get('type')}", LogType.WARNING)
                add_consumed_transactions([tx_hash])


def get_neg_risk_market_id(slug: str) -> str:
    logger.log(f"Fetching negRiskMarketID for slug: {slug}")
    url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
    
    def _fetch():
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    
    try:
        data = with_retry(_fetch, max_retries=3, base_delay=1.0)()
        if data is None:
            logger.log(f"Market slug not found: {slug}", LogType.WARNING)
            return None
        neg_risk_market_id = data["events"][0]["negRiskMarketID"]
        logger.log(f"Found negRiskMarketID: {neg_risk_market_id}")
        return neg_risk_market_id
    except Exception as e:
        logger.log(f"Failed to fetch market ID after retries: {e}", LogType.ERROR)
        return None


def decode_index_set_from_tx(tx_hash: str) -> int:
    logger.log(f"Decoding indexSet from transaction: {tx_hash}")
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": 137,
        "module": "proxy",
        "action": "eth_getTransactionByHash",
        "txhash": tx_hash,
        "apikey": ETHERSCAN_API_KEY
    }
    
    def _fetch():
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    try:
        tx_data = with_retry(_fetch, max_retries=3, base_delay=2.0)()
        input_data = tx_data["result"]["input"]
        index_set_hex = input_data[74:138]
        index_set = int(index_set_hex, 16)
        logger.log(f"Decoded indexSet: {index_set}")
        return index_set
    except Exception as e:
        logger.log(f"Failed to decode tx after retries: {e}", LogType.ERROR)
        raise


def convert_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing convert activity: {target_activity.get('title')}")
    
    target_size = target_activity.get("size")
    target_usdc_size = target_activity.get("usdcSize")
    slug = target_activity.get("slug")
    market_id = get_neg_risk_market_id(slug)
    if not market_id:
        logger.log("Could not get market ID, skipping conversion.", LogType.WARNING)
        return
    index_set = decode_index_set_from_tx(target_activity.get("transactionHash"))
    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    target_cash = get_on_chain_usdc_balance(target_activity.get("proxyWallet"))
    logger.log(f"Target cash: {target_cash}")
    target_portfolio_value = target_portfolio_value + target_cash
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", LogType.WARNING)
        return
    
    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")
    
    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)
    user_token_amount = fraction_of_target_portfolio * user_portfolio_usdc_value / (target_usdc_size / target_size) if target_size > 0 else 0
    logger.log(f"User token amount to convert: {user_token_amount}")
    user_amount_raw = int(user_token_amount * 10**6)
    
    logger.log(f"Convert details - marketId: {market_id}, indexSet: {index_set}, amount: {user_amount_raw}")
    
    try:
        data = Web3().eth.contract(
            address=NEG_RISK_ADAPTER,
            abi=[{"name": "convertPositions", "type": "function", "inputs": [{"name": "_marketId", "type": "bytes32"}, {"name": "_indexSet", "type": "uint256"}, {"name": "_amount", "type": "uint256"}], "outputs": []}]
        ).encode_abi(abi_element_identifier="convertPositions", args=[market_id, index_set, user_amount_raw])
        
        convert_tx = SafeTransaction(
            to=NEG_RISK_ADAPTER,
            data=data,
            value="0",
            operation=OperationType.Call
        )
        logger.log("Executing convert transaction...")
        response = builder_client.execute([convert_tx], "Convert positions")
        if hasattr(response, 'wait'):
            response.wait()
        logger.log("Convert transaction completed successfully")
    except Exception as e:
        logger.log(str(e), LogType.ERROR)
        return

def get_position_value(conditionId: str, address: str) -> float:

    logger.log(f"Fetching positions for {address}")
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": address,
        "market": [conditionId]
    }
    
    def _fetch():
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        if not response.json():
            return 0
        
        return response.json()[0].get("currentValue", 0)
    
    try:
        return with_retry(_fetch, max_retries=3, base_delay=1.0)()
    except Exception as e:
        logger.log(f"Failed to fetch positions after retries: {e}", LogType.ERROR)
        raise


def buy_activity(target_activity: Dict[str, Any]) -> bool:
    logger.log(f"Buying activity: {target_activity.get('title')}")

    target_usdc_size = target_activity.get("usdcSize")

    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    target_cash = get_on_chain_usdc_balance(target_activity.get("proxyWallet"))
    logger.log(f"Target cash: {target_cash}")
    target_portfolio_value = target_portfolio_value + target_cash
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return False

    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)

    user_cash = get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
    logger.log(f"User cash: {user_cash}")

    user_total_usdc_value = user_portfolio_usdc_value + user_cash

    user_size_to_buy_usdc = round(fraction_of_target_portfolio * user_total_usdc_value, 2)

    logger.log(f"User size to buy usdc: {user_size_to_buy_usdc}")

    if user_size_to_buy_usdc < 1.0:
        logger.log("User size to buy USDC is less than $1, calculating position-based buy size.")

        current_target_position_value = get_position_value(target_activity.get("conditionId"), target_activity.get("proxyWallet"))

        target_position_portfolio_fraction = current_target_position_value / target_portfolio_value
        logger.log(f"Target position portfolio fraction: {target_position_portfolio_fraction}")

        current_user_position_value = get_position_value(target_activity.get("conditionId"), POLY_MARKET_FUNDER_ADDRESS)

        user_position_portfolio_fraction = current_user_position_value / user_total_usdc_value
        logger.log(f"User position portfolio fraction: {user_position_portfolio_fraction}")

        fraction_diff = target_position_portfolio_fraction - user_position_portfolio_fraction
        if user_position_portfolio_fraction >= target_position_portfolio_fraction:
            logger.log("User position portfolio fraction is greater than or equal to target position portfolio fraction, skipping.", log_type=LogType.WARNING)
            return False

        user_usdc_needed = target_position_portfolio_fraction * user_total_usdc_value - current_user_position_value
        logger.log(f"User USDC needed to match target fraction: {user_usdc_needed}")

        if user_usdc_needed < 1:
            logger.log("Buy size would be less than $1, skipping.", log_type=LogType.WARNING)
            return False

        user_size_to_buy_usdc = round(user_usdc_needed, 2)
        logger.log(f"Updated user size to buy USDC: {user_size_to_buy_usdc}")


    if user_size_to_buy_usdc > user_cash:
        logger.log("User size to buy is more than cash available, skipping.", log_type=LogType.WARNING)
        return False

    target_price = target_activity.get("price")
    if target_price is not None:
        target_price = round(float(target_price), 2)
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return False

    remaining_usdc = user_size_to_buy_usdc
    total_filled_usdc = 0.0

    for offset in [0, 0.01, 0.02]:
        if remaining_usdc < 1.0:
            logger.log(f"Remaining USDC ${remaining_usdc} below $1 minimum, stopping.")
            break

        buy_price = min(round(target_price + offset, 2), 0.99)
        user_size_to_buy = calculate_valid_size(remaining_usdc, buy_price, decimals=2)

        if user_size_to_buy <= 0:
            continue

        # Use Decimal for exact validation
        size = Decimal(str(user_size_to_buy))
        price = Decimal(str(buy_price))
        
        # Calculate maker amount (size * price) with exact precision
        maker_amount = size * price
        # Verify it has exactly 2 decimal places
        maker_amount_str = f"{maker_amount:.2f}"
        maker_amount = Decimal(maker_amount_str)
        
        # Verify taker amount has exactly 2 decimals (since we used decimals=2)
        taker_amount_str = f"{size:.2f}"
        size = Decimal(taker_amount_str)
        
        order_value = float(maker_amount)
        
        if order_value < 1.0:
            continue

        logger.log(f"Placing FAK buy order: {size} shares at price {buy_price} (maker: ${maker_amount_str})")

        # Convert via string to ensure exact precision - this prevents float representation issues
        # The API requires: maker_amount (size*price) with max 2 decimals, taker_amount (size) with max 4 decimals
        order_args = OrderArgs(
            price=float(f"{price:.2f}"),
            size=float(f"{size:.2f}"),  # Using 2 decimals since that's what we validated
            side=BUY,
            token_id=target_activity.get("asset"),
        )

        try:
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return False

        try:
            resp = client.post_order(signed_order, OrderType.FAK)
            status = resp.get("status")
            making_amount = resp.get("makingAmount", "")
            
            if status == "MATCHED":
                filled_usdc = float(making_amount) if making_amount else order_value
                total_filled_usdc += filled_usdc
                remaining_usdc -= filled_usdc
                logger.log(f"FAK order filled! Bought {user_size_to_buy} shares at {buy_price} (${filled_usdc} USDC)")
                if remaining_usdc < 1.0:
                    logger.log(f"Order complete. Total filled: ${total_filled_usdc}")
                    return True
                logger.log(f"Partial fill, ${remaining_usdc} remaining, trying next price level...")
                continue
            else:
                logger.log(f"FAK order status: {status} at {buy_price}", LogType.WARNING)
                if making_amount:
                    filled_usdc = float(making_amount)
                    if filled_usdc > 0:
                        total_filled_usdc += filled_usdc
                        remaining_usdc -= filled_usdc
                        logger.log(f"Partial fill of ${filled_usdc}, ${remaining_usdc} remaining")
                        if remaining_usdc < 1.0:
                            return True
                continue
        except Exception as e:
            error_msg = str(e)
            if "no orders found" in error_msg.lower():
                logger.log(f"No matching orders at {buy_price}, trying next price level...")
                continue
            else:
                logger.log(error_msg, LogType.ERROR)
                return False

    if total_filled_usdc > 0:
        logger.log(f"Order complete. Total filled: ${total_filled_usdc}")
        return True

    logger.log(f"Could not fill buy order within 0.02 of target price {target_price}", LogType.WARNING)
    return False


def sell_activity(target_activity: Dict[str, Any], user_token_position: Dict[str, Any]) -> bool:
    logger.log(f"Selling activity: {target_activity.get('title')}")

    target_usdc_size = target_activity.get("usdcSize")
    condition_id = target_activity.get("conditionId")
    target_address = target_activity.get("proxyWallet")

    # Check if target fully exited this position
    # If so, we should sell ALL our shares, not just the proportional fraction
    target_positions = fetch_positions(target_address)
    target_still_holds = any(
        p.get("conditionId") == condition_id and float(p.get("size", 0)) > 0
        for p in target_positions
    )

    if not target_still_holds:
        logger.log(f"Target fully exited {target_activity.get('title', '')[:50]}, selling all follower shares")
        user_share_size = float(user_token_position.get("size", 0))
        if user_share_size <= 0:
            logger.log("Follower has no shares to sell.", log_type=LogType.WARNING)
            return False
        user_size_to_sell_usdc = user_share_size * float(user_token_position.get("curPrice", 0))
        logger.log(f"Full exit sell: {user_share_size} shares, ~${user_size_to_sell_usdc:.2f} USDC")
    else:
        target_portfolio_value = get_portfolio_usdc_value(target_address)
        target_cash = get_on_chain_usdc_balance(target_address)
        logger.log(f"Target cash: {target_cash}")
        target_portfolio_value = target_portfolio_value + target_cash
        if not target_portfolio_value or target_portfolio_value == 0:
            logger.log("Target portfolio value is zero or unavailable, skipping.", log_type=LogType.WARNING)
            return False

        fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
        logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

        user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)

        user_size_to_sell_usdc = round(fraction_of_target_portfolio * user_portfolio_usdc_value, 2)

        logger.log(f"User size to sell usdc: {user_size_to_sell_usdc}")
        if user_size_to_sell_usdc < 1.0:
            logger.log(f"Order size ${user_size_to_sell_usdc} is below $1 minimum, skipping.", log_type=LogType.WARNING)
            return False

    target_price = target_activity.get("price")
    if target_price is not None:
        target_price = round(float(target_price), 2)
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return False

    token_id = target_activity.get("asset")

    user_share_size = float(user_token_position.get("size", 0))
    needed_size = user_size_to_sell_usdc / target_price
    if user_share_size < needed_size:
        logger.log(f"User shares ({user_share_size}) is less than needed ({needed_size}), selling all available.", log_type=LogType.WARNING)
        user_size_to_sell_usdc = user_share_size * target_price

    remaining_usdc = user_size_to_sell_usdc
    total_filled_usdc = 0.0

    # Try target_price, then -0.01, then -0.02 (never more than 0.02 below target)
    for offset in [0, 0.01, 0.02]:
        if remaining_usdc < 1.0:
            logger.log(f"Remaining USDC ${remaining_usdc} below $1 minimum, stopping.")
            break

        sell_price = min(max(round(target_price - offset, 2), 0.01), 0.999)
        user_size_to_sell = calculate_valid_size(remaining_usdc, sell_price, decimals=2)

        if user_size_to_sell <= 0:
            continue

        # Use Decimal for exact validation
        size = Decimal(str(user_size_to_sell))
        price = Decimal(str(sell_price))
        
        # Calculate maker amount (size * price) with exact precision
        maker_amount = size * price
        # Verify it has exactly 2 decimal places
        maker_amount_str = f"{maker_amount:.2f}"
        maker_amount = Decimal(maker_amount_str)
        
        # Verify taker amount has exactly 2 decimals (since we used decimals=2)
        taker_amount_str = f"{size:.2f}"
        size = Decimal(taker_amount_str)
        
        order_value = float(maker_amount)
        
        if order_value < 1.0:
            continue

        logger.log(f"Placing FAK sell order: {size} shares at price {sell_price} (maker: ${maker_amount_str})")

        # Convert via string to ensure exact precision - this prevents float representation issues
        # The API requires: maker_amount (size*price) with max 2 decimals, taker_amount (size) with max 4 decimals
        order_args = OrderArgs(
            price=float(f"{price:.2f}"),
            size=float(f"{size:.2f}"),  # Using 2 decimals since that's what we validated
            side=SELL,
            token_id=token_id,
        )

        try:
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return False

        try:
            resp = client.post_order(signed_order, OrderType.FAK)
            status = resp.get("status")
            # For SELL: takingAmount = USDC received, makingAmount = shares given
            taking_amount = resp.get("takingAmount", "")
            
            if status == "MATCHED":
                filled_usdc = float(taking_amount) if taking_amount else order_value
                total_filled_usdc += filled_usdc
                remaining_usdc -= filled_usdc
                logger.log(f"FAK order filled! Sold {user_size_to_sell} shares at {sell_price} (${filled_usdc} USDC)")
                if remaining_usdc < 1.0:
                    logger.log(f"Order complete. Total filled: ${total_filled_usdc}")
                    return True
                # Partial fill - continue to next price level
                logger.log(f"Partial fill, ${remaining_usdc} remaining, trying next price level...")
                continue
            else:
                logger.log(f"FAK order status: {status} at {sell_price}", LogType.WARNING)
                # Check if there was a partial fill even with non-MATCHED status
                if taking_amount:
                    filled_usdc = float(taking_amount)
                    if filled_usdc > 0:
                        total_filled_usdc += filled_usdc
                        remaining_usdc -= filled_usdc
                        logger.log(f"Partial fill of ${filled_usdc}, ${remaining_usdc} remaining")
                        if remaining_usdc < 1.0:
                            return True
                continue
        except Exception as e:
            error_msg = str(e)
            if "no orders found" in error_msg.lower():
                logger.log(f"No matching orders at {sell_price}, trying next price level...")
                continue
            else:
                logger.log(error_msg, LogType.ERROR)
                return False

    if total_filled_usdc > 0:
        logger.log(f"Order complete. Total filled: ${total_filled_usdc}")
        return True

    logger.log(f"Could not fill sell order within 0.02 of target price {target_price}", LogType.WARNING)
    return False


def split_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing split activity: {target_activity.get('title')}")
    
    condition_id = target_activity.get("conditionId")
    target_usdc_size = target_activity.get("usdcSize")
    partition = [1, 2]
    
    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    target_cash = get_on_chain_usdc_balance(target_activity.get("proxyWallet"))
    logger.log(f"Target cash: {target_cash}")
    target_portfolio_value = target_portfolio_value + target_cash
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", LogType.WARNING)
        return

    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)
    user_size_to_split_usdc = fraction_of_target_portfolio * user_portfolio_usdc_value
    
    logger.log(f"User size to split usdc: {user_size_to_split_usdc}")
    
    target_price = target_activity.get("price")
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", LogType.WARNING)
        return

    user_size_to_split = user_size_to_split_usdc / target_price
    
    logger.log(f"Split details - conditionId: {condition_id}, amount: {user_size_to_split}, partition: {partition}")
    
    user_cash = get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
    logger.log(f"User USDC balance: {user_cash}")

    if user_size_to_split_usdc > user_cash:
        logger.log(f"Insufficient USDC balance. Need {user_size_to_split_usdc}, have {user_cash}", LogType.WARNING)
        return

    user_amount_raw = int(user_size_to_split_usdc * 10**6)

    try:
        data = Web3().eth.contract(
            address=CTF,
            abi=[{"name": "splitPosition", "type": "function", "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "partition", "type": "uint256[]"}, {"name": "amount", "type": "uint256"}], "outputs": []}]
        ).encode_abi(abi_element_identifier="splitPosition", args=[USDC_ADDRESS, "0x" + "00" * 32, condition_id, partition, user_amount_raw])
        
        split_tx = SafeTransaction(
            to=CTF,
            data=data,
            value="0",
            operation=OperationType.Call
        )
        logger.log("Executing split transaction...")
        response = builder_client.execute([split_tx], "Split positions")
        if hasattr(response, 'wait'):
            response.wait()
        logger.log("Split transaction completed successfully")
    except Exception as e:
        logger.log(str(e), LogType.ERROR)
        return

def merge_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing merge activity: {target_activity.get('title')}")
    
    condition_id = target_activity.get("conditionId")
    target_usdc_size = target_activity.get("usdcSize")
    partition = [1, 2]
    
   
    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    target_cash = get_on_chain_usdc_balance(target_activity.get("proxyWallet"))
    logger.log(f"Target cash: {target_cash}")
    target_portfolio_value = target_portfolio_value + target_cash
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", LogType.WARNING)
        return

    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)
    user_size_to_merge_usdc = fraction_of_target_portfolio * user_portfolio_usdc_value
    
    logger.log(f"User size to merge usdc: {user_size_to_merge_usdc}")
    
    target_price = target_activity.get("price")
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", LogType.WARNING)
        return

    user_size_to_merge = user_size_to_merge_usdc / target_price
    logger.log(f"Merge details - conditionId: {condition_id}, amount: {user_size_to_merge}, partition: {partition}")
    user_amount_raw = int(user_size_to_merge_usdc * 10**6)

    try:
        data = Web3().eth.contract(
            address=CTF,
            abi=[{"name": "mergePositions", "type": "function", "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "partition", "type": "uint256[]"}, {"name": "amount", "type": "uint256"}], "outputs": []}]
        ).encode_abi(abi_element_identifier="mergePositions", args=[USDC_ADDRESS, "0x" + "00" * 32, condition_id, partition, user_amount_raw])
        
        merge_tx = SafeTransaction(
            to=CTF,
            data=data,
            value="0",
            operation=OperationType.Call
        )

        logger.log("Executing merge transaction...")
        response = builder_client.execute([merge_tx], "Merge positions")
        if hasattr(response, 'wait'):
            response.wait()
        logger.log("Merge transaction completed successfully")
    except Exception as e:
        logger.log(str(e), LogType.ERROR)
        return  





def get_on_chain_usdc_balance(address: str):
    logger.log(f"Fetching on chain usdc balance for {address}")
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": 137,
        "module": "account",
        "action": "tokenbalance",
        "contractaddress": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "address": address,
        "tag": "latest",
        "apikey": ETHERSCAN_API_KEY
    }
    
    def _fetch():
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    try:
        balance_data = with_retry(_fetch, max_retries=3, base_delay=2.0)()
        balance = int(balance_data.get("result", 0)) / 10**6
        return balance
    except Exception as e:
        logger.log(f"Failed to fetch USDC balance after retries: {e}", LogType.ERROR)
        raise
    
    

def get_portfolio_usdc_value(address: str):
    logger.log(f"Fetching portfolio usdc value for {address}")
    url = "https://data-api.polymarket.com/value"
    params = {
        "user": address
    }
    
    def _fetch():
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    try:
        value_data = with_retry(_fetch, max_retries=3, base_delay=1.0)()[0]
        return value_data.get("value")
    except Exception as e:
        logger.log(f"Failed to fetch portfolio value after retries: {e}", LogType.ERROR)
        raise





