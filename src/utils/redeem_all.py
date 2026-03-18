

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.utils import *
import requests
import traceback
from typing import List, Dict, Any
from utils.logger import Logger, LogType
from py_clob_client import ClobClient, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from py_builder_relayer_client.client import RelayClient, BuilderConfig
from py_builder_relayer_client.models import SafeTransaction, OperationType
from web3 import Web3
from decimal import Decimal
from math import gcd
from utils.utils import CTF, USDC_ADDRESS, POLY_MARKET_API_KEY, POLY_MARKET_SECRET, POLY_MARKET_PASSPHRASE, PRIVATE_KEY, POLY_MARKET_FUNDER_ADDRESS

builder_config = BuilderConfig(
    local_builder_creds=BuilderApiKeyCreds(
        key=POLY_MARKET_API_KEY,
        secret=POLY_MARKET_SECRET,
        passphrase=POLY_MARKET_PASSPHRASE,
    )
)

builder_client = RelayClient(
    "https://relayer-v2.polymarket.com",
    137,
    PRIVATE_KEY,
    builder_config,
)


logger = Logger()


def fetch_positions() -> list:
    """Fetch positions from Polymarket data API."""
    url = "https://data-api.polymarket.com/positions"
    params = {
        "user": POLY_MARKET_FUNDER_ADDRESS,
        "limit": 100,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        return []




def redeem_position(position: dict) -> tuple[bool, str]:
    """Redeem a position using the CTF contract. Returns (success, message)."""
    if not builder_client:
        return False, "[ERROR] Builder client not initialized - need POLY_MARKET_API_KEY/SECRET/PASSPHRASE"
    
    condition_id = position.get("conditionId")
    title = position.get("title", "Unknown")
    size = position.get("size", 0)
    
    try:
        data = Web3().eth.contract(
            address=CTF,
            abi=[{
                "name": "redeemPositions",
                "type": "function",
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "indexSets", "type": "uint256[]"}
                ],
                "outputs": []
            }]
        ).encode_abi(
            abi_element_identifier="redeemPositions",
            args=[USDC_ADDRESS, "0x" + "00" * 32, condition_id, [1, 2]]
        )
        
        redeem_tx = SafeTransaction(
            to=CTF,
            data=data,
            value="0",
            operation=OperationType.Call
        )
        
        response = builder_client.execute([redeem_tx], "Redeem positions")
        if hasattr(response, 'wait'):
            response.wait()
        return True, f"[SUCCESS] Redeemed {size} shares from {title[:35]}"
    except Exception as e:
        return False, f"[ERROR] Failed to redeem: {e}\n{traceback.format_exc()}"




def check_and_redeem_positions() -> tuple[int, list[str]]:
    """Check positions and redeem any that are redeemable. Returns (count_redeemed, info_lines)."""
    positions = fetch_positions()
    redeemed_count = 0
    info_lines = []
    
    info_lines.append(f"Found {len(positions)} positions")
    
    for position in positions:
        title = position.get('title', 'Unknown')[:35]
        redeemable = position.get('redeemable', False)
        size = position.get('size', 0)
        info_lines.append(f"  {title} | redeemable={redeemable} | size={size}")
        
        if redeemable:
            success, msg = redeem_position(position)
            info_lines.append(f"    {msg}")
            if success:
                redeemed_count += 1
    
    return redeemed_count, info_lines


if __name__ == "__main__":
    count, lines = check_and_redeem_positions()
    for line in lines:
        print(line)
    print(f"\nRedeemed {count} positions.")
