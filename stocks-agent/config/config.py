import os
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_env_path)

# ── Alpaca ─────────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_BASE_URL   = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL   = "https://data.alpaca.markets"

# ── Watchlist ──────────────────────────────────────────────────────────────────
WATCHLIST = ["SPY", "AAPL", "MSFT", "NVDA", "TSLA"]

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HAIKU_MODEL       = "claude-haiku-4-5-20251001"
SONNET_MODEL      = "claude-sonnet-4-6"

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Market hours (NYSE) ────────────────────────────────────────────────────────
MARKET_OPEN_UTC  = (14, 30)
MARKET_CLOSE_UTC = (21, 0)

# ── Guardrails ────────────────────────────────────────────────────────────────
MAX_POSITION_PCT   = 0.30   # max 30% of balance as margin per trade
STOP_LOSS_PCT      = 0.03   # 3% on notional — executor applies per actual leverage
DRAWDOWN_PAUSE_PCT = 0.20
MAX_OPEN_POSITIONS = 3
SHORT_ENABLED      = True

# ── Dynamic leverage bounds ────────────────────────────────────────────────────
# Sonnet chooses leverage per trade. Executor clamps to [MIN, MAX].
# Alpaca paper supports 4:1 intraday margin on most equities.
MIN_LEVERAGE = 1
MAX_LEVERAGE = 10

# ── Indicator settings ────────────────────────────────────────────────────────
RSI_PERIOD         = 14
RSI_OVERSOLD       = 33
RSI_OVERBOUGHT     = 67
MACD_FAST          = 12
MACD_SLOW          = 26
MACD_SIGNAL        = 9
BB_PERIOD          = 20
BB_STD             = 2.0
VOL_SPIKE_MULT     = 1.8
CANDLE_INTERVAL    = "5Min"
CANDLE_LIMIT       = 100

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT_DIR   = os.path.join(BASE_DIR, "vault")
STATE_FILE  = os.path.join(BASE_DIR, "state.json")
LOG_DIR     = os.path.join(BASE_DIR, "logs")

# ── Paper trading ─────────────────────────────────────────────────────────────
PAPER_STARTING_BALANCE = 150.0

