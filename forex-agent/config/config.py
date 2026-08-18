import os
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_env_path)

# ── OANDA ──────────────────────────────────────────────────────────────────────
OANDA_API_KEY     = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID  = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_DEMO        = os.getenv("OANDA_DEMO", "true").lower() == "true"

INSTRUMENT        = "EUR_USD"
DISPLAY_PAIR      = "EUR/USD"

# ── Anthropic ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HAIKU_MODEL       = "claude-haiku-4-5-20251001"
SONNET_MODEL      = "claude-sonnet-4-6"

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Guardrails ─────────────────────────────────────────────────────────────────
MAX_POSITION_PCT   = 0.50   # max 50% of balance as margin per trade
STOP_LOSS_PCT      = 0.015  # 1.5% stop on notional — executor applies per actual leverage
DRAWDOWN_PAUSE_PCT = 0.20
SHORT_ENABLED      = True

# ── Dynamic leverage bounds ────────────────────────────────────────────────────
# Sonnet chooses leverage per trade. Executor clamps to [MIN, MAX].
# OANDA practice supports up to 30:1 — keeping ceiling at 10 for paper learning.
MIN_LEVERAGE = 1
MAX_LEVERAGE = 20

# ── Indicator settings ─────────────────────────────────────────────────────────
RSI_PERIOD         = 14
RSI_OVERSOLD       = 25
RSI_OVERBOUGHT     = 75
MACD_FAST          = 12
MACD_SLOW          = 26
MACD_SIGNAL        = 9
BB_PERIOD          = 20
BB_STD             = 2.0
VOL_SPIKE_MULT     = 1.5
CANDLE_INTERVAL    = "M15"
CANDLE_LIMIT       = 200

# ── Market hours ───────────────────────────────────────────────────────────────
MARKET_OPEN_WEEKDAY   = 0
MARKET_CLOSE_WEEKDAY  = 4
MARKET_CLOSE_HOUR_UTC = 21

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT_DIR  = os.path.join(BASE_DIR, "vault")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOG_DIR    = os.path.join(BASE_DIR, "logs")
