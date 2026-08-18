"""
config_writer.py
Safely adjusts agent config.py values based on manager recommendations.

Hard guardrails (stop-loss, drawdown pause) are NEVER modified.
Only whitelisted parameters can be changed. All changes are logged.
"""
import logging
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import AGENTS, LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "config_writer.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Parameters the manager is ALLOWED to adjust per agent
ADJUSTABLE = {
    "btc": [
        "MAX_POSITION_PCT",
        "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT",
        "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
        "BB_PERIOD", "BB_STD",
        "VOL_SPIKE_MULT",
        "CANDLE_INTERVAL", "CANDLE_LIMIT",
    ],
    "forex": [
        "MAX_POSITION_PCT",
        "MIN_LEVERAGE", "MAX_LEVERAGE",
        "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT",
        "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
        "BB_PERIOD", "BB_STD",
        "VOL_SPIKE_MULT",
        "CANDLE_INTERVAL", "CANDLE_LIMIT",
    ],
    "stocks": [
        "MAX_POSITION_PCT", "MAX_OPEN_POSITIONS",
        "MIN_LEVERAGE", "MAX_LEVERAGE",
        "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT",
        "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
        "BB_PERIOD", "BB_STD",
        "VOL_SPIKE_MULT",
        "CANDLE_INTERVAL", "CANDLE_LIMIT",
    ],
}

# Parameters that must NEVER be touched -- hard safety guardrails
FORBIDDEN = {
    "STOP_LOSS_PCT",
    "DRAWDOWN_PAUSE_PCT",
    "LEVERAGE",           # BTC fixed leverage (use MIN/MAX_LEVERAGE for others)
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "OANDA_API_TOKEN",
    "OANDA_ACCOUNT_ID",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
}


def _get_config_path(agent_id: str) -> str:
    """Resolve the config.py path for an agent."""
    agent_dir_map = {
        "btc": "btc-agent",
        "forex": "forex-agent",
        "stocks": "stocks-agent",
    }
    agent_dir = agent_dir_map.get(agent_id)
    if not agent_dir:
        return None
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, agent_dir, "config", "config.py")


def read_config_values(agent_id: str) -> dict:
    """Read current adjustable config values for an agent."""
    config_path = _get_config_path(agent_id)
    if not config_path or not os.path.exists(config_path):
        return {}

    allowed = ADJUSTABLE.get(agent_id, [])
    values = {}
    with open(config_path, "r") as f:
        content = f.read()

    for param in allowed:
        match = re.search(
            rf'^{param}\s*=\s*(.+?)(?:\s*#.*)?$',
            content,
            re.MULTILINE,
        )
        if match:
            raw = match.group(1).strip()
            try:
                values[param] = eval(raw)
            except Exception:
                values[param] = raw

    return values


def apply_config_change(agent_id: str, param: str, new_value) -> dict:
    """
    Change a single config parameter for an agent.
    Returns dict with: success, param, old_value, new_value, agent_id.
    """
    # Safety checks
    if param in FORBIDDEN:
        msg = f"BLOCKED: {param} is a forbidden parameter (hard guardrail)"
        log.error(msg)
        return {"success": False, "error": msg}

    allowed = ADJUSTABLE.get(agent_id, [])
    if param not in allowed:
        msg = f"BLOCKED: {param} is not in the adjustable list for {agent_id}"
        log.error(msg)
        return {"success": False, "error": msg}

    config_path = _get_config_path(agent_id)
    if not config_path or not os.path.exists(config_path):
        return {"success": False, "error": f"Config file not found for {agent_id}"}

    with open(config_path, "r") as f:
        content = f.read()

    # Find the current value
    pattern = rf'^({param}\s*=\s*)(.+?)(\s*#.*)?$'
    match = re.search(pattern, content, re.MULTILINE)
    if not match:
        return {"success": False, "error": f"{param} not found in {agent_id} config"}

    old_raw = match.group(2).strip()
    try:
        old_value = eval(old_raw)
    except Exception:
        old_value = old_raw

    # Format the new value
    if isinstance(new_value, float):
        new_raw = str(new_value)
    elif isinstance(new_value, int):
        new_raw = str(new_value)
    else:
        new_raw = repr(new_value)

    # Preserve the comment if there was one
    comment = match.group(3) or ""

    # Build the replacement line
    old_line = match.group(0)
    new_line = f"{match.group(1)}{new_raw}{comment}"

    new_content = content.replace(old_line, new_line, 1)

    # Write back
    with open(config_path, "w") as f:
        f.write(new_content)

    log.info(f"CONFIG CHANGE: {agent_id}/{param}: {old_value} -> {new_value}")

    return {
        "success": True,
        "agent_id": agent_id,
        "param": param,
        "old_value": old_value,
        "new_value": new_value,
    }


def apply_config_changes(changes: list) -> list:
    """
    Apply a batch of config changes.
    Each item: {"agent_id": str, "param": str, "value": any}
    Returns list of results.
    """
    results = []
    for change in changes:
        result = apply_config_change(
            change["agent_id"],
            change["param"],
            change["value"],
        )
        results.append(result)
    return results


def get_adjustable_summary() -> dict:
    """
    Return current adjustable values for all agents.
    Used as context for the manager's Opus call.
    """
    summary = {}
    for agent_id in ["btc", "forex", "stocks"]:
        summary[agent_id] = read_config_values(agent_id)
    return summary


if __name__ == "__main__":
    import json
    summary = get_adjustable_summary()
    print(json.dumps(summary, indent=2, default=str))
