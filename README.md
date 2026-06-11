# Polymarket Follower Bot

A bot that copies trades from target Polymarket traders, executing proportional trades based on portfolio allocation. Each target runs in its own Docker container, managed through the `polybot` CLI.

> **Polymarket API Docs:** https://docs.polymarket.com/llms.txt

## Prerequisites

- Docker & Docker Compose
- Python 3.10+
- A Polymarket account with CLOB API credentials
- An [Etherscan API key](https://etherscan.io/apis) (shared across all bots)

## Quick Start

### 1. Search for targets

```bash
python cli/polybot.py search --min-pnl 50000 --limit 10
```

### 2. Create an env file for the bot

Each bot is configured via a `.env` file. Create one with the following format:

```env
BOT_NAME="my-bot"
BOT_USERNAME="my-username"
BOT_PROFILE_URL="https://polymarket.com/@my-username"

PRIVATE_KEY="your-private-key"
TARGET_ADDRESS="0x-target-address"
TARGET_USERNAME="target-username"
TARGET_PROFILE_URL="https://polymarket.com/@target-username"

POLY_MARKET_API_KEY="your-api-key"
POLY_MARKET_SECRET="your-secret"
POLY_MARKET_PASSPHRASE="your-passphrase"

POLY_MARKET_FUNDER_ADDRESS="0x-your-funder-address"
```

| Variable | Required | Description |
|---|---|---|
| `BOT_NAME` | Yes | Docker container name (lowercase, hyphens ok) |
| `BOT_USERNAME` | Yes | Polymarket username for the bot account |
| `BOT_PROFILE_URL` | Yes | Link to the bot's Polymarket profile |
| `PRIVATE_KEY` | Yes | EOA private key for the bot wallet |
| `TARGET_ADDRESS` | Yes | Ethereum address of the trader to copy |
| `TARGET_USERNAME` | Yes | Polymarket username of the target trader |
| `TARGET_PROFILE_URL` | Yes | Link to the target's Polymarket profile |
| `POLY_MARKET_API_KEY` | Yes | CLOB API key |
| `POLY_MARKET_SECRET` | Yes | CLOB API secret |
| `POLY_MARKET_PASSPHRASE` | Yes | CLOB API passphrase |
| `POLY_MARKET_FUNDER_ADDRESS` | Yes | Proxy/wallet address |

The `ETHERSCAN_API_KEY` is shared across all bots and configured in `docker/.env` — do not add it to individual bot env files.

### 3. Add the bot

```bash
python cli/polybot.py add /path/to/bot.env
```

The `BOT_NAME` field in the env file determines the container name.

### 4. Start the bot

```bash
python cli/polybot.py start my-bot
```

### 5. Monitor

```bash
python cli/polybot.py list
python cli/polybot.py logs my-bot -f
python cli/polybot.py status my-bot
```

## CLI Commands

| Command | Description |
|---|---|
| `polybot search` | Search for high-quality copy-trading targets |
| `polybot add <env-file>` | Add a new bot instance from an env file |
| `polybot list` | List all bots with profile links and status |
| `polybot start <name>` | Start a single bot |
| `polybot start --all` | Start all bots that aren't running |
| `polybot stop <name>` | Stop a single bot |
| `polybot stop --all` | Stop all bots that are running |
| `polybot restart <name>` | Restart a bot |
| `polybot logs <name> -f` | Follow bot logs |
| `polybot status <name>` | Show detailed bot status |
| `polybot remove <name>` | Remove a bot and its volumes |

## Search Options

```bash
# Quick search (leaderboard data only)
python cli/polybot.py search --min-pnl 10000 --sort roi --limit 20

# Deep search (fetches win rate, consistency, activity per trader)
python cli/polybot.py search --deep --min-pnl 50000 --min-win-rate 70 --limit 10

# JSON output for scripting
python cli/polybot.py search --json --limit 50 > targets.json
```

| Flag | Default | Description |
|---|---|---|
| `--min-pnl` | 0 | Minimum profit/loss in USD |
| `--min-roi` | 0 | Minimum ROI percentage |
| `--min-win-rate` | 0 | Minimum win rate percentage |
| `--min-markets` | 0 | Minimum markets traded |
| `--max-days-inactive` | 3 | Max days since last trade |
| `--sort` | composite_score | Sort field (composite_score, pnl, roi, win_rate, profit_factor, rank) |
| `--limit` | 20 | Number of results |
| `--deep` | off | Fetch detailed stats per trader |
| `--json` | off | Output as JSON |

## Architecture

- Each bot runs in its own Docker container under the `polymarket-swarm` compose project
- Per-bot config and secrets live in `docker/bots/<name>/.env`
- Shared config (Etherscan key) lives in `docker/.env`
- `docker-compose.yml` is auto-generated — do not edit manually
- Bot state (last processed timestamp, consumed transactions) persists in Docker volumes
- Health check monitors log freshness

## Getting API Credentials

1. Create a Polymarket account and deposit funds
2. Generate CLOB API credentials via the Polymarket UI or SDK — see [Authentication](https://docs.polymarket.com/api-reference/authentication.md)
3. Your funder/proxy address is displayed in your Polymarket wallet settings
4. Get an Etherscan API key from [etherscan.io/apis](https://etherscan.io/apis) and add it to `docker/.env`

## References

- [Polymarket API Documentation](https://docs.polymarket.com/llms.txt)
- [Polymarket Python SDK](https://docs.polymarket.com/dev-tooling/python.md)
- [Polymarket Quickstart](https://docs.polymarket.com/quickstart.md)
