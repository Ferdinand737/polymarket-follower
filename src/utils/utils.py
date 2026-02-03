import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()



DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
POLY_MARKET_API_KEY = os.getenv("POLY_MARKET_API_KEY")
POLY_MARKET_SECRET = os.getenv("POLY_MARKET_SECRET")
POLY_MARKET_PASSPHRASE = os.getenv("POLY_MARKET_PASSPHRASE")
POLY_MARKET_FUNDER_ADDRESS = os.getenv("POLY_MARKET_FUNDER_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")

FOLLOWER_CHECK_INTERVAL_MINUTES = 5

ADDRESS_FILE = Path("config/address_to_follow.txt")
CTF = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


def get_follow_address():
    if not ADDRESS_FILE.exists():
        raise FileNotFoundError("No follow address configured. Use !set_address <address> to set one.")

    address = ADDRESS_FILE.read_text().strip().lower()
    if not address:
        raise ValueError("Follow address file is empty. Use !set_address <address> to set one.")

    if not is_valid_address(address):
        raise ValueError("Follow address is invalid. Use !set_address <address> to set one.")

    return address


def is_valid_address(address):
    return address.startswith("0x") and len(address) == 42 and address.isalnum()


def set_follow_address(address):
    address = address.strip().lower()
    
    if not is_valid_address(address):
        raise ValueError("Address is invalid. Use !set_address <address> to set one.")
 
    ADDRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ADDRESS_FILE.write_text(address)

    