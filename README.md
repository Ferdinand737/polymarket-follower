# Polymarket Bot

A suite of tools for following and analyzing Polymarket traders. Copy trades from successful traders, analyze their strategies, and manage your portfolio.

## Features

- **Trade Follower** — Automatically copy trades from a target trader
- **Target Analytics** — Comprehensive stats and graphs for any trader
- **Discord Bot** — Control and monitor via Discord
- **Ratio Calculator** — Calculate optimal position sizes

---

## Setup

### 1. Clone and Create Virtual Environment

```bash
git clone <repo-url>
cd polymarket-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows
```

### 2. Install Dependencies

```bash
cd src
pip install -e .
```

### 3. Configure Environment Variables

Create a `.env` file in the project root:

```env
# Polymarket API Credentials
POLY_MARKET_API_KEY=your_api_key
POLY_MARKET_SECRET=your_secret
POLY_MARKET_PASSPHRASE=your_passphrase
POLY_MARKET_FUNDER_ADDRESS=your_wallet_address
PRIVATE_KEY=your_private_key

# Etherscan API (for transaction lookups)
ETHERSCAN_API_KEY=your_etherscan_key

# Discord Bot (optional)
DISCORD_BOT_TOKEN=your_discord_token
```

### 4. Set Target Address

Create the config directory and set the address to follow:

```bash
mkdir -p config
echo "0xTARGET_WALLET_ADDRESS" > config/address_to_follow.txt
```

---

## Scripts

### `polymarket-follower`

**Main trade copying bot.** Monitors a target trader and automatically copies their trades proportionally to your portfolio size.

```bash
polymarket-follower
```

**What it copies:**

- BUY / SELL trades
- SPLIT / MERGE operations
- CONVERSION (NegRisk)
- REDEEM positions

**Configuration:**

- Check interval: 5 minutes (configurable in `utils.py`)
- Minimum trade size: 5 tokens
- Proportional sizing based on portfolio value

---

### `target-stats`

**Comprehensive analytics for any Polymarket trader.** Generates detailed stats and visualizations.

```bash
target-stats [wallet_address]

# Or use configured follow address:
target-stats
```

**Stats included:**

- Portfolio value and positions
- Today's activity (trades, volume, avg bet, largest bet)
- Weekly activity and daily averages
- Large bets (≥$4,000)
- Activity breakdown (trades, splits, merges, conversions, redeems)
- Peak trading hours
- Daily breakdown table

**Graphs generated:**

- Daily volume bar chart
- Trade count trend
- Buy/Sell ratio pie chart
- Hourly activity pattern
- Bet size distribution
- Cumulative volume

Saves graph as `target_stats_<address>.png`

---

### `ratio-calculator`

**Calculate minimum trade sizes** based on your portfolio ratio vs target.

```bash
ratio-calculator
```

Shows a table of minimum target trade sizes at various prices that will result in a valid trade for your portfolio.

---

### `discord-bot`

**Discord interface** for controlling and monitoring the bot.

```bash
discord-bot
```

**Commands:**

- `!set_address <address>` — Set target address to follow
- `!status` — Check bot status
- _(Add more commands as implemented)_

---

## Project Structure

```
polymarket-bot/
├── config/
│   └── address_to_follow.txt    # Target wallet address
├── src/
│   ├── polymarket/
│   │   ├── follower.py          # Main follower bot
│   │   └── helpers.py           # Trade execution functions
│   ├── discord/
│   │   └── discord_bot.py       # Discord bot
│   ├── utils/
│   │   ├── utils.py             # Constants and utilities
│   │   ├── logger.py            # Logging
│   │   ├── target_stats.py      # Trader analytics
│   │   └── ratio_calculator.py  # Position sizing
│   └── pyproject.toml           # Package config
├── .env                         # Environment variables (not tracked)
└── README.md
```

---

## API Endpoints Used

| Endpoint                                       | Purpose                 |
| ---------------------------------------------- | ----------------------- |
| `data-api.polymarket.com/activity`             | Fetch trader activities |
| `data-api.polymarket.com/positions`            | Fetch current positions |
| `data-api.polymarket.com/value`                | Portfolio value         |
| `gamma-api.polymarket.com/markets/slug/{slug}` | Market details          |
| `clob.polymarket.com`                          | Order placement         |
| `relayer-v2.polymarket.com`                    | Transaction execution   |

---

## Safety Features

- **Price bounds** — Buys stop at +$0.02 above target, sells stop at -$0.02 below
- **Minimum size** — Trades under 5 tokens are skipped
- **Order cancellation** — Stale orders are cancelled before retrying
- **Division-by-zero guards** — Handles edge cases gracefully
- **Polling with timeout** — Orders are monitored for 30 seconds before price adjustment

---

## License

MIT
