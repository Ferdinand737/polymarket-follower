
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


logger = Logger(Whomst.POLYMARKET_FOLLOWER)


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

    current_price = position.get("curPrice")
    size = position.get("size")
    token_id = position.get("asset")

    if size < 5:
        logger.log("Position size is less than 5, skipping.")
        return

    order_id = None
    while True:
        if current_price < 0.01:
            logger.log("Price dropped below 0.01, stopping sell attempts.", LogType.WARNING)
            return

        logger.log(f"Attempting to sell at price: {current_price}")

        order_args = OrderArgs(
            price=current_price,
            size=size,
            side=SELL,
            token_id=token_id,
        )

        try:
            logger.log("Creating order...")
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        try:
            if order_id:
                logger.log(f"Cancelling previous order {order_id}...")
                client.cancel(order_id)
            logger.log("Posting order...")
            resp = client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        logger.log("Waiting for order to be filled...")
        for _ in range(6):
            time.sleep(5)
            order_status = client.get_order(order_id)
            if order_status.get("status") == "FILLED":
                logger.log("Order filled successfully!")
                return

        logger.log(f"Order not filled at price {current_price}, reducing price by 0.01...")
        current_price = round(current_price - 0.01, 2)



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


def fetch_activities(address: str, interval_ago_ts: int):
    address_type = "target" if address != POLY_MARKET_FUNDER_ADDRESS else "user"
    logger.log(f"Fetching activities for {address} ({address_type})")
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": address,
        "limit": 10,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC"
    }
    if interval_ago_ts:
        params["start"] = interval_ago_ts
    response = requests.get(url, params=params)
    response.raise_for_status()
    activity_data = response.json()
    return activity_data


def compare_activities(target_activity: List[Dict[str, Any]], user_activity: List[Dict[str, Any]]):
    logger.log(f"Comparing activities for target and user")
    user_transaction_hashes = {activity.get("transactionHash") for activity in user_activity}
    new_activities = [activity for activity in target_activity if activity.get("transactionHash") not in user_transaction_hashes]
    return new_activities


def process_new_activities(new_target_activities: List[Dict[str, Any]]):
    logger.log(f"Processing {len(new_target_activities)} new activities")
    for target_activity in new_target_activities:
        match target_activity.get("type"):
            case "TRADE":
                if target_activity.get("side") == "BUY":
                    buy_activity(target_activity)
                elif target_activity.get("side") == "SELL":
                    sell_activity(target_activity)
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
    """Fetch negRiskMarketID from Gamma API using slug."""
    logger.log(f"Fetching negRiskMarketID for slug: {slug}")
    url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    neg_risk_market_id = data["events"][0]["negRiskMarketID"]
    logger.log(f"Found negRiskMarketID: {neg_risk_market_id}")
    return neg_risk_market_id


def decode_index_set_from_tx(tx_hash: str) -> int:
    """Decode the indexSet from a convertPositions transaction."""
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
    # convertPositions(bytes32 _marketId, uint256 _indexSet, uint256 _amount)
    # Layout: 0x<4 bytes selector><32 bytes marketId><32 bytes indexSet><32 bytes amount>
    # indexSet is at bytes 36-68 (characters 10+64 to 10+128 in hex string)
    index_set_hex = input_data[74:138]  # Skip "0x" + 4 byte selector + 32 byte marketId
    index_set = int(index_set_hex, 16)
    logger.log(f"Decoded indexSet: {index_set}")
    return index_set


def convert_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing convert activity: {target_activity.get('title')}")
    
    target_size = target_activity.get("size")  # Token amount
    target_usdc_size = target_activity.get("usdcSize")
    
    if target_size < 5:
        logger.log("Target convert size is less than 5, skipping.", LogType.WARNING)
        return
    
    # 1. Get marketId from Gamma API using slug
    slug = target_activity.get("slug")
    market_id = get_neg_risk_market_id(slug)
    
    # 2. Get indexSet by decoding target's transaction
    index_set = decode_index_set_from_tx(target_activity.get("transactionHash"))
    
    # 3. Calculate proportional user amount based on token size
    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", LogType.WARNING)
        return
    
    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")
    
    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)
    
    # Calculate user token amount proportionally from target's token size
    user_token_amount = fraction_of_target_portfolio * user_portfolio_usdc_value / (target_usdc_size / target_size) if target_size > 0 else 0
    
    logger.log(f"User token amount to convert: {user_token_amount}")
    
    # Convert to raw amount (6 decimals for conditional tokens)
    user_amount_raw = int(user_token_amount * 10**6)
    
    logger.log(f"Convert details - marketId: {market_id}, indexSet: {index_set}, amount: {user_amount_raw}")
    
    if user_token_amount < 5:
        logger.log("User token amount to convert is less than 5, skipping.", LogType.WARNING)
        return
    
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

def buy_activity(target_activity: Dict[str, Any]):
    logger.log(f"Buying activity: {target_activity.get("title")}")

    target_usdc_size = target_activity.get("usdcSize")
    target_size = target_activity.get("size")
    if target_size < 5:
        logger.log("Position size is less than 5, skipping.", log_type=LogType.WARNING)
        return

    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return

    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)

    user_cash = get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
    logger.log(f"User cash: {user_cash}")

    user_total_usdc_value = user_portfolio_usdc_value + user_cash

    user_size_to_buy_usdc = fraction_of_target_portfolio * user_total_usdc_value

    logger.log(f"User size to buy usdc: {user_size_to_buy_usdc}")
    if user_size_to_buy_usdc > user_cash:
        logger.log("User size to buy is more than cash available, skipping.", log_type=LogType.WARNING)
        return

    target_price = target_activity.get("price")
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return

    user_size_to_buy = user_size_to_buy_usdc / target_price

    if user_size_to_buy < 5:
        logger.log("User size to buy is less than 5, skipping.", log_type=LogType.WARNING)
        return

    current_price = target_price
    order_id = None
    while True:
        if current_price > target_price + 2:
            logger.log("Price exceeded target price + 2, stopping buy attempts.", LogType.WARNING)
            return

        logger.log(f"Attempting to buy at price: {current_price}")

        order_args = OrderArgs(
            price=current_price,
            size=user_size_to_buy,
            side=BUY,
            token_id=target_activity.get("asset"),
        )

        try:
            logger.log("Creating order...")
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        try:
            if order_id:
                logger.log(f"Cancelling previous order {order_id}...")
                client.cancel(order_id)
            logger.log("Posting order...")
            resp = client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        logger.log("Waiting for order to be filled...")
        for _ in range(6):
            time.sleep(5)
            order_status = client.get_order(order_id)
            if order_status.get("status") == "FILLED":
                logger.log("Order filled successfully!")
                return

        logger.log(f"Order not filled at price {current_price}, increasing price by 0.01...")
        current_price = round(current_price + 0.01, 2)



def sell_activity(target_activity: Dict[str, Any]):
    logger.log(f"Selling activity: {target_activity.get("title")}")

    target_usdc_size = target_activity.get("usdcSize")
    target_size = target_activity.get("size")
    if target_size < 5:
        logger.log("Position size is less than 5, skipping.", log_type=LogType.WARNING)
        return

    target_portfolio_value = get_portfolio_usdc_value(target_activity.get("proxyWallet"))
    if not target_portfolio_value or target_portfolio_value == 0:
        logger.log("Target portfolio value is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return

    fraction_of_target_portfolio = target_usdc_size / target_portfolio_value
    logger.log(f"Fraction of target portfolio: {fraction_of_target_portfolio}")

    user_portfolio_usdc_value = get_portfolio_usdc_value(POLY_MARKET_FUNDER_ADDRESS)

    user_size_to_sell_usdc = fraction_of_target_portfolio * user_portfolio_usdc_value

    logger.log(f"User size to sell usdc: {user_size_to_sell_usdc}")

    target_price = target_activity.get("price")
    if not target_price or target_price == 0:
        logger.log("Target price is zero or unavailable, skipping.", log_type=LogType.WARNING)
        return

    user_size_to_sell = user_size_to_sell_usdc / target_price

    if user_size_to_sell < 5:
        logger.log("User size to sell is less than 5, skipping.", log_type=LogType.WARNING)
        return

    positions = fetch_positions(POLY_MARKET_FUNDER_ADDRESS)
    token_id = target_activity.get("asset")
    user_token_position = next((p for p in positions if p.get("asset") == token_id), None)

    if not user_token_position:
        logger.log("User does not hold this token, skipping.", log_type=LogType.WARNING)
        return

    user_token_size = user_token_position.get("size")
    if user_token_size < user_size_to_sell:
        logger.log(f"User token size ({user_token_size}) is less than size to sell ({user_size_to_sell}), skipping.", log_type=LogType.WARNING)
        return

    current_price = target_price
    order_id = None
    while True:
        if current_price < target_price - 2:
            logger.log("Price dropped below target price - 2, stopping sell attempts.", LogType.WARNING)
            return

        logger.log(f"Attempting to sell at price: {current_price}")

        order_args = OrderArgs(
            price=current_price,
            size=user_size_to_sell,
            side=SELL,
            token_id=token_id,
        )

        try:
            logger.log("Creating order...")
            signed_order = client.create_order(order_args)
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        try:
            if order_id:
                logger.log(f"Cancelling previous order {order_id}...")
                client.cancel(order_id)
            logger.log("Posting order...")
            resp = client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID")
        except Exception as e:
            logger.log(str(e), LogType.ERROR)
            return

        logger.log("Waiting for order to be filled...")
        for _ in range(6):
            time.sleep(5)
            order_status = client.get_order(order_id)
            if order_status.get("status") == "FILLED":
                logger.log("Order filled successfully!")
                return

        logger.log(f"Order not filled at price {current_price}, reducing price by 0.01...")
        current_price = round(current_price - 0.01, 2)


def split_activity(target_activity: Dict[str, Any]):
    logger.log(f"Processing split activity: {target_activity.get('title')}")
    
    condition_id = target_activity.get("conditionId")
    target_size = target_activity.get("size")
    target_usdc_size = target_activity.get("usdcSize")
    partition = [1, 2]
    
    if target_size < 5:
        logger.log("Target split size is less than 5, skipping.", LogType.WARNING)
        return

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
    
    if user_size_to_split < 5:
        logger.log("User size to split is less than 5, skipping.", LogType.WARNING)
        return

    user_cash = get_on_chain_usdc_balance(POLY_MARKET_FUNDER_ADDRESS)
    logger.log(f"User USDC balance: {user_cash}")

    if user_size_to_split_usdc > user_cash:
        logger.log(f"Insufficient USDC balance. Need {user_size_to_split_usdc}, have {user_cash}", LogType.WARNING)
        return

    # Convert to raw amount (6 decimals)
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
    target_size = target_activity.get("size")
    target_usdc_size = target_activity.get("usdcSize")
    partition = [1, 2]
    
    if target_size < 5:
        logger.log("Target merge size is less than 5, skipping.", LogType.WARNING)
        return

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
    
    if user_size_to_merge < 5:
        logger.log("User size to merge is less than 5, skipping.", LogType.WARNING)
        return

    # Convert to raw amount (6 decimals)
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





