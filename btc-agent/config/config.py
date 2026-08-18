import os
from dotenv import load_dotenv

# Load .env from config directory
_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_env_path)

# ── Kraken ─────────────────────────────────────────────────────────────────────
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
KRAKEN_DEMO       = os.getenv("KRAKEN_DEMO", "true").lower() == "true"

# Kraken uses XBT (not BTC) and USD (not USDT)
# Spot pair: XXBTZUSD  |  Futures demo pair: PI_XBTUSD
SPOT_PAIR         = "XXBTZUSD"
DISPLAY_PAIR      = "XBT/USD"

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HAIKU_MODEL       = "claude-haiku-4-5-20251001"   # cheap filter calls
SONNET_MODEL      = "claude-sonnet-4-6"            # trade decisions

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Guardrails (NEVER modified by Claude — enforced in executor) ───────────────
MAX_POSITION_PCT   = 0.15   # max 50% of available balance per trade
STOP_LOSS_PCT      = 0.04   # hard 4% stop-loss per trade
DRAWDOWN_PAUSE_PCT = 0.20   # pause if balance drops 20% from peak
LEVERAGE           = 1      # 1:1 always, no margin

# ── Indicator settings ────────────────────────────────────────────────────────
RSI_PERIOD         = 14
RSI_OVERSOLD       = 25
RSI_OVERBOUGHT     = 75
MACD_FAST          = 12
MACD_SLOW          = 26
MACD_SIGNAL        = 9
BB_PERIOD          = 20
BB_STD             = 2.0
VOL_SPIKE_MULT     = 1.8    # volume must be 1.8x 24h average to count as spike
CANDLE_INTERVAL    = 60     # minutes per candle
CANDLE_LIMIT       = 200    # candles to fetch per cycle

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT_DIR   = os.path.join(BASE_DIR, "vault")
STATE_FILE  = os.path.join(BASE_DIR, "state.json")
LOG_DIR     = os.path.join(BASE_DIR, "logs")
