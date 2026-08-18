# BTC Trading Agent

Autonomous BTC/USD trading agent built on Claude AI + Kraken.
Runs on your home server. Paper trades first, live when ready.

## Stack
- Exchange: Kraken (demo → live, one env var switch)
- AI brain: Claude Haiku (signal filter) + Sonnet (trade decisions)
- Memory: Markdown vault
- Alerts: Telegram bot
- Runtime: Ubuntu 24.04, cron, Python 3.11+

## Directory structure
```
btc-agent/
├── config/
│   ├── config.py          # All settings
│   └── .env               # Secrets (never commit)
├── vault/
│   ├── trades/            # Per-decision JSON logs
│   ├── strategy/          # Agent's evolving strategy notes
│   └── reports/           # Daily/weekly performance reports
├── logs/                  # Monitor + brain logs
├── market_monitor.py      # Runs every 60s via cron, no Claude
├── claude_brain.py        # Called on trigger, makes decisions
├── trade_executor.py      # Executes orders, enforces guardrails
├── telegram_bot.py        # Sends alerts and daily reports
├── daily_review.py        # Called once/day via cron
├── performance_tracker.py # Updates performance.json after each trade
└── requirements.txt
```

## Setup

### 1. Install dependencies
```bash
cd ~/btc-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create .env
```bash
cp config/.env.example config/.env
# Fill in your keys
```

### 3. Kraken demo account
- Sign up at https://demo-futures.kraken.com
- Generate API key with: Orders + Positions (read/write), Account (read)
- Add keys to .env

### 4. Telegram bot
- Message @BotFather on Telegram → /newbot
- Get your chat ID: message @userinfobot
- Add both to .env

### 5. Cron jobs
```bash
crontab -e
```
Add:
```
# Market monitor — every 60 seconds
* * * * * cd /home/ratul/btc-agent && venv/bin/python market_monitor.py >> logs/cron.log 2>&1
* * * * * sleep 30 && cd /home/ratul/btc-agent && venv/bin/python market_monitor.py >> logs/cron.log 2>&1

# Daily review — 7am every day
0 7 * * * cd /home/ratul/btc-agent && venv/bin/python daily_review.py >> logs/cron.log 2>&1
```

### 6. Paper trade
- Set KRAKEN_DEMO=true in .env
- Run manually first: `python market_monitor.py`
- Watch logs/monitor.log

### 7. Go live (when ready)
- Set KRAKEN_DEMO=false in .env
- Switch to live Kraken API keys
- Done — no other changes needed
```

## Guardrails (hardcoded, Claude cannot change these)
- Max position: 30% of balance per trade
- Stop-loss: 4% per trade (auto-executed, no Claude call needed)
- Drawdown pause: 20% from peak balance → all trading halts + Telegram alert
- Leverage: 1:1 always
