import os
import json
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

CONFIG_FILE = Path("config/follower_config.json")
CTF = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

DEFAULT_CONFIG = {
    "address_to_follow": None,
    "current_target_address": None,
    "consumed_transactions": []
}


def load_config():
    """Load config from JSON file. Returns default config if file doesn't exist."""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        for key in DEFAULT_CONFIG:
            if key not in config:
                config[key] = DEFAULT_CONFIG[key]
        return config
    except (json.JSONDecodeError, IOError):
        return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save config to JSON file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def is_valid_address(address):
    """Check if address is a valid Ethereum address."""
    if not address:
        return False
    return address.startswith("0x") and len(address) == 42 and all(c in "0123456789abcdefABCDEF" for c in address[2:])


def get_follow_address():
    """Get the address to follow from config."""
    config = load_config()
    address = config.get("address_to_follow")
    
    if not address:
        raise ValueError("No follow address configured. Use !set_address <address> to set one.")
    
    if not is_valid_address(address):
        raise ValueError("Follow address is invalid. Use !set_address <address> to set one.")
    
    return address.lower()


def set_follow_address(address):
    """Set the address to follow in config."""
    address = address.strip().lower()
    
    if not is_valid_address(address):
        raise ValueError("Address is invalid. Use !set_address <address> to set one.")
    
    config = load_config()
    config["address_to_follow"] = address
    save_config(config)


def get_current_target_address():
    """Get current target address from config. Returns None if not set."""
    config = load_config()
    address = config.get("current_target_address")
    
    if not address or not is_valid_address(address):
        return None
    
    return address.lower()


def save_current_target_address(address):
    """Save current target address to config."""
    config = load_config()
    if address:
        config["current_target_address"] = address.strip().lower()
    else:
        config["current_target_address"] = None
    save_config(config)


def get_consumed_transactions():
    """Get list of consumed transaction hashes."""
    config = load_config()
    return set(config.get("consumed_transactions", []))


def add_consumed_transactions(tx_hashes):
    """Add transaction hashes to consumed list."""
    config = load_config()
    consumed = set(config.get("consumed_transactions", []))
    consumed.update(tx_hashes)
    config["consumed_transactions"] = list(consumed)
    save_config(config)


def clear_consumed_transactions():
    """Clear all consumed transactions (use when target changes)."""
    config = load_config()
    config["consumed_transactions"] = []
    save_config(config)
