# Polymarket Follower Bot

A bot that copies trades from target Polymarket traders, executing proportional trades based on portfolio allocation.

## Quick Start (Docker)

```bash
# 1. Search for targets
python cli/polybot.py search --min-pnl 50000 --limit 10

# 2. Add a bot instance (you provide the credentials)
python cli/polybot.py add mybot \
  --target 0x... \
  --api-key KEY \
  --secret SECRET \
  --passphrase PHRASE \
  --private-key KEY \
  --funder 0x...

# 3. Start the bot
python cli/polybot.py start mybot

# 4. Check status
python cli/polybot.py status mybot
python cli/polybot.py logs mybot -f
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `polybot search` | Search for high-quality copy-trading targets |
| `polybot add` | Add a new bot instance with credentials |
| `polybot list` | List all configured bot instances |
| `polybot start <name>` | Start a bot (or `all`) |
| `polybot stop <name>` | Stop a bot (or `all`) |
| `polybot restart <name>` | Restart a bot |
| `polybot logs <name> -f` | View bot logs |
| `polybot status <name>` | Show bot status |
| `polybot remove <name>` | Remove a bot instance |

## Search Options

```bash
# Quick search (leaderboard data only)
python cli/polybot.py search --min-pnl 10000 --sort roi --limit 20

# Deep search (fetches win rate, consistency, activity per trader)
python cli/polybot.py search --deep --min-pnl 50000 --min-win-rate 70 --limit 10

# JSON output for scripting
python cli/polybot.py search --json --limit 50 > targets.json
```

## Adding a New Bot

You need a Polymarket account with:
- CLOB API credentials (api_key, secret, passphrase)
- Private key for the EOA
- Proxy/wallet address (funder)

Set these up manually via the Polymarket UI, then pass them to `polybot add`.

## Architecture

- Each bot runs in its own Docker container
- Config and secrets live in `bots/<name>/.env`
- Docker Compose manages all instances
- `docker-compose.yml` is auto-generated — don't edit manually
