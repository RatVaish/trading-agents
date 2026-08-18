import os
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_env_path)

# -- Anthropic (manager uses its own key) ------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPUS_MODEL = "claude-opus-4-6"

# -- Telegram (manager has its own bot) --------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# -- Agent registry ----------------------------------------------------------
AGENTS = {
    "btc": {
        "display": "BTC/XBT",
        "strategy_path": os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "btc-agent", "vault", "strategy", "current.md",
        ),
        "side_options": ["LONG"],
        "symbols": ["XBT/USD"],
    },
    "forex": {
        "display": "EUR/USD Forex",
        "strategy_path": os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "forex-agent", "vault", "strategy", "current.md",
        ),
        "side_options": ["LONG", "SHORT"],
        "symbols": ["EUR/USD"],
    },
    "stocks": {
        "display": "US Stocks",
        "strategy_path": os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "stocks-agent", "vault", "strategy", "current.md",
        ),
        "side_options": ["LONG", "SHORT"],
        "symbols": ["SPY", "AAPL", "MSFT", "NVDA", "TSLA"],
    },
}

# -- Strategy rewrite constraints --------------------------------------------
# Max lines for a rewritten strategy file (keeps it lean for the brain)
MAX_STRATEGY_LINES = 120
# Keep a backup before overwriting
BACKUP_STRATEGIES = True

# -- Paths -------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
