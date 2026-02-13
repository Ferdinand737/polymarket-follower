
from utils.utils import *
import requests
from typing import List, Dict, Any
from utils.logger import Logger, Whomst, LogType
from py_clob_client import ClobClient, OrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY, SELL
import time
from py_builder_signing_sdk.signing.hmac import build_hmac_signature
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from py_builder_relayer_client.client import RelayClient, TransactionType, BuilderConfig
from web3 import Web3
from decimal import Decimal, ROUND_DOWN



logger = Logger(Whomst.POLYMARKET_FOLLOWER)


def calculate_valid_size(usdc_amount: float, price: float, decimals: int = 4) -> float:
    size = Decimal(str(usdc_amount)) / Decimal(str(price))
    quantize_str = '0.' + '0' * decimals
    return float(size.quantize(Decimal(quantize_str), rounding=ROUND_DOWN))


client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137)
user_api_creds = client.create_or_derive_api_creds()


client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137,
    creds=user_api_creds,
    signature_type=2,
    funder=POLY_MARKET_FUNDER_ADDRESS
)

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
    logger.log(f"Selling position: ${position.get("currentValue")} {position.get("title")}")

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
            resp = client.post_order(signed_order, OrderType.FOK)
            status = resp.get("status")
            if status == "MATCHED":
                logger.log(f"FOK order filled! Sold {size} shares at {current_price}")
                return
            else:
                logger.log(f"FOK order not filled at {current_price}, adjusting price...")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        current_price = round(current_price - 0.01, 2)

    logger.log(f"Could not fill order within 0.02 of target price {target_price}, giving up.", LogType.WARNING)



def fetch_positions(address: str):
    logger.log(f"Fetching positions for {address}")
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": address
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    positions_data = response.json()
    return positions_data


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
    response = requests.get(url, params=params)
    response.raise_for_status()
    activity_data = response.json()
    return activity_data


def compare_activities(target_activity: List[Dict[str, Any]], user_activity: List[Dict[str, Any]]):
    logger.log(f"Comparing activities for target and user")
    user_transaction_hashes = {activity.get("transactionHash") for activity in user_activity}
    new_activities = [activity for activity in target_activity if activity.get("transactionHash") not in user_transaction_hashes]
    return new_activities


def fetch_market_trades_for_aggregation(target_address: str, market: str, asset: str, side: str, price: float) -> Dict[str, Any]:
    consumed = get_consumed_transactions()
    
    logger.log(f"Fetching full market history for aggregation: {market}")
    all_market_activities = fetch_activities(target_address, market=market, limit=100)
    
    matching_trades = []
    for activity in all_market_activities:
        tx_hash = activity.get("transactionHash")
        if tx_hash in consumed:
            continue
        if activity.get("type") != "TRADE":
            continue
        if activity.get("asset") == asset and activity.get("side") == side and activity.get("price") == price:
            matching_trades.append(activity)
    
    if not matching_trades:
        return None
    
    total_size = sum(t.get("size", 0) for t in matching_trades)
    total_usdc = sum(t.get("usdcSize", 0) for t in matching_trades)
    rounded_usdc = round(total_usdc, 2)
    
    if rounded_usdc < 0.01:
        logger.log(f"Aggregate too small: {len(matching_trades)} trades, ${total_usdc:.4f} total")
        return None
    
    tx_hashes = [t.get("transactionHash") for t in matching_trades]
    
    combined = matching_trades[0].copy()
    combined["size"] = total_size
    combined["usdcSize"] = total_usdc
    combined["_aggregated_tx_hashes"] = tx_hashes
    combined["_aggregated_count"] = len(matching_trades)
    
    logger.log(f"Aggregated {len(matching_trades)} trades: {side} {total_size:.4f} shares @ {price} = ${total_usdc:.2f}")
    return combined


def process_new_activities(new_target_activities: List[Dict[str, Any]]):
    logger.log(f"Processing {len(new_target_activities)} new activities")
    
    consumed = get_consumed_transactions()
    processed_markets = set()
    
    for target_activity in new_target_activities:
        tx_hash = target_activity.get("transactionHash")
        if tx_hash in consumed:
            continue
            
        match target_activity.get("type"):
            case "TRADE":

                asset = target_activity.get("asset")
                side = target_activity.get("side")
                price = target_activity.get("price")
                market = target_activity.get("conditionId")
                target_address = target_activity.get("proxyWallet")

                if side == 'SELL':
                    positions = fetch_positions(POLY_MARKET_FUNDER_ADDRESS)
                    user_share_position = next((p for p in positions if p.get("asset") == asset), None)

                    if not user_share_position:
                        logger.log("User does not hold shares, skipping.", log_type=LogType.WARNING)
                        continue
                
                
                aggregation_key = (asset, side, price)
                if aggregation_key in processed_markets:
                    continue
                processed_markets.add(aggregation_key)
                
                aggregated = fetch_market_trades_for_aggregation(
                    target_address, market, asset, side, price
                )
                
                if aggregated:
                    tx_hashes = aggregated.get("_aggregated_tx_hashes", [tx_hash])
                    success = False
                    if side == "BUY":
                        success = buy_activity(aggregated)
                    elif side == "SELL":
                        success = sell_activity(aggregated, user_share_position)
                    
                    if success:
                        add_consumed_transactions(tx_hashes)
            case "SPLIT":
                split_activity(target_activity)
            case "MERGE":
                merge_activity(target_activity)
            case "REDEEM":
                redeem_activity(target_activity)
            case "REWARD":
                continue
            case "CONVERSION":
                convert_activity(target_activity)
            case "MAKER_REBATE":
                continue
            case _:
                logger.log(f"Unknown activity type: {target_activity.get('type')}", LogType.WARNING)
                continue


def get_neg_risk_market_id(slug: str) -> str:
    logger.log(f"Fetching negRiskMarketID for slug: {slug}")
    url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    neg_risk_market_id = data["events"][0]["negRiskMarketID"]
    logger.log(f"Found negRiskMarketID: {neg_risk_market_id}")
    return neg_risk_market_id


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
    response = requests.get(url, params=params)
    response.raise_for_status()
    tx_data = response.json()
    
    input_data = tx_data["result"]["input"]
    index_set_hex = input_data[74:138]
    index_set = int(index_set_hex, 16)
    logger.log(f"Decoded indexSet: {index_set}")
    return index_set


def convert_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing convert activity: {target_activity.get('title')}")
    
    target_size = target_activity.get("size")
    target_usdc_size = target_activity.get("usdcSize")
    slug = target_activity.get("slug")
    market_id = get_neg_risk_market_id(slug)
    index_set = decode_index_set_from_tx(target_activity.get("transactionHash"))
    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
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
        convert_tx = {
            "to": NEG_RISK_ADAPTER,
            "data": Web3().eth.contract(
                address=NEG_RISK_ADAPTER,
                abi=[{"name": "convertPositions", "type": "function", "inputs": [{"name": "_marketId", "type": "bytes32"}, {"name": "_indexSet", "type": "uint256"}, {"name": "_amount", "type": "uint256"}], "outputs": []}]
            ).encode_abi(abi_element_identifier="convertPositions", args=[market_id, index_set, user_amount_raw]),
            "value": "0"
        }
        logger.log("Executing convert transaction...")
        response = builder_client.execute([convert_tx], "Convert positions")
        response.wait()
        logger.log("Convert transaction completed successfully")
    except Exception as e:
        logger.log(str(e), LogType.ERROR)
        return

def buy_activity(target_activity: Dict[str, Any]) -> bool:
    logger.log(f"Buying activity: {target_activity.get('title')}")

    target_usdc_size = target_activity.get("usdcSize")

    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
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
    if user_size_to_buy_usdc > user_cash:
        logger.log("User size to buy is more than cash available, skipping.", log_type=LogType.WARNING)
        return False

    target_price = target_activity.get("price")
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return False

    current_price = round(target_price - 0.01, 2)
    max_price = target_price + 0.02
    
    while current_price <= max_price:
        user_size_to_buy = calculate_valid_size(user_size_to_buy_usdc, current_price, decimals=4)
        
        logger.log(f"Attempting to buy {user_size_to_buy} shares at price: {current_price}")

        order_args = OrderArgs(
            price=current_price,
            size=user_size_to_buy,
            side=BUY,
            token_id=target_activity.get("asset"),
        )

        try:
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return False

        try:
            resp = client.post_order(signed_order, OrderType.FOK)
            status = resp.get("status")
            if status == "MATCHED":
                logger.log(f"FOK order filled! Bought {user_size_to_buy} shares at {current_price}")
                return True
            else:
                logger.log(f"FOK order not filled at {current_price}, adjusting price...")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return False

        current_price = round(current_price + 0.01, 2)

    logger.log(f"Could not fill order within 0.02 of target price {target_price}, giving up.", LogType.WARNING)
    return False


def sell_activity(target_activity: Dict[str, Any], user_token_position: Dict[str, Any]) -> bool:
    logger.log(f"Selling activity: {target_activity.get('title')}")

    target_usdc_size = target_activity.get("usdcSize")

    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return False

    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)

    user_size_to_sell_usdc = round(fraction_of_target_portfolio * user_portfolio_usdc_value, 2)

    logger.log(f"User size to sell usdc: {user_size_to_sell_usdc}")

    target_price = target_activity.get("price")
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return False

    token_id = target_activity.get("asset")

    user_share_size = user_token_position.get("size")
    needed_size = user_size_to_sell_usdc / target_price
    if user_share_size < needed_size:
        logger.log(f"User shares ({user_share_size}) is less than needed ({needed_size}), selling all available.", log_type=LogType.WARNING)
        user_size_to_sell_usdc = user_share_size * target_price

    current_price = round(target_price + 0.01, 2)
    min_price = max(0.01, target_price - 0.02)
    
    while current_price >= min_price:
        user_size_to_sell = calculate_valid_size(user_size_to_sell_usdc, current_price, decimals=4)
        
        logger.log(f"Attempting to sell {user_size_to_sell} shares at price: {current_price}")

        order_args = OrderArgs(
            price=current_price,
            size=user_size_to_sell,
            side=SELL,
            token_id=token_id,
        )

        try:
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return False

        try:
            resp = client.post_order(signed_order, OrderType.FOK)
            status = resp.get("status")
            if status == "MATCHED":
                logger.log(f"FOK order filled! Sold {user_size_to_sell} shares at {current_price}")
                return True
            else:
                logger.log(f"FOK order not filled at {current_price}, adjusting price...")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return False

        current_price = round(current_price - 0.01, 2)

    logger.log(f"Could not fill order within 0.02 of target price {target_price}, giving up.", LogType.WARNING)
    return False


def split_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing split activity: {target_activity.get('title')}")
    
    condition_id = target_activity.get("conditionId")
    target_usdc_size = target_activity.get("usdcSize")
    partition = [1, 2]
    
    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
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
        split_tx = {
            "to": CTF,
            "data": Web3().eth.contract(
                address=CTF,
                abi=[{"name": "splitPosition", "type": "function", "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "partition", "type": "uint256[]"}, {"name": "amount", "type": "uint256"}], "outputs": []}]
            ).encode_abi(abi_element_identifier="splitPosition", args=[USDC_ADDRESS, "0x" + "00" * 32, condition_id, partition, user_amount_raw]),
            "value": "0"
        }
        logger.log("Executing split transaction...")
        response = builder_client.execute([split_tx], "Split positions")
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
        merge_tx = {
            "to": CTF,
            "data": Web3().eth.contract(
                address=CTF,
                abi=[{"name": "mergePositions", "type": "function", "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "partition", "type": "uint256[]"}, {"name": "amount", "type": "uint256"}], "outputs": []}]
            ).encode_abi(abi_element_identifier="mergePositions", args=[USDC_ADDRESS, "0x" + "00" * 32, condition_id, partition, user_amount_raw]),
            "value": "0"
        }

        logger.log("Executing merge transaction...")
        response = builder_client.execute([merge_tx], "Merge positions")
        response.wait()
        logger.log("Merge transaction completed successfully")
    except Exception as e:
        logger.log(str(e), LogType.ERROR)
        return  


def redeem_activity(activity: Dict[str, Any]):
    logger.log(f"Processing redeem activity: {activity.get('title')}")

    try:
        redeem_tx = {
        "to": CTF,
        "data": Web3().eth.contract(
            address=CTF,
            abi=[{"name": "redeemPositions", "type": "function", "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "indexSets", "type": "uint256[]"}], "outputs": []}]
        ).encode_abi(abi_element_identifier="redeemPositions", args=[USDC_ADDRESS,  "0x" + "00" * 32, activity.get("conditionId"), [1,2]]),
        "value": "0"
    }

        logger.log("Executing redeem transaction...")
        response = builder_client.execute([redeem_tx], "Redeem positions")
        response.wait()
        logger.log("Redeem transaction completed successfully")
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
    response = requests.get(url, params=params)
    response.raise_for_status()
    balance_data = response.json()
    balance = int(balance_data.get("result", 0)) / 10**6
    return balance
    
    

def get_portfolio_usdc_value(address: str):
    logger.log(f"Fetching portfolio usdc value for {address}")
    url = "https://data-api.polymarket.com/value"
    params = {
        "user": address
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    value_data = response.json()[0]
    return value_data.get("value")





