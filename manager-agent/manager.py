"""
manager.py
Orchestrator for the management agent. Runs weekly via cron.

Workflow:
1. Analyze all agents (pure DB queries, no AI)
2. Generate recommendations + config changes using Opus
3. Apply config changes to agent config.py files
4. Rewrite bloated strategy files
5. Send management report via Telegram

Usage:
  python manager.py           # full run (analyze + rewrite + config + report)
  python manager.py --analyze # analyze only, print to stdout
  python manager.py --dry-run # analyze + recommendations, no file writes
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import ANTHROPIC_API_KEY, OPUS_MODEL, AGENTS, LOG_DIR
from analyzer import analyze_all
from strategy_writer import rewrite_all
from config_writer import get_adjustable_summary, apply_config_changes, ADJUSTABLE, FORBIDDEN
from telegram_bot import send_management_report, send_message

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "manager.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate_recommendations(analyses: dict, current_configs: dict) -> tuple:
    """
    Use Opus to generate actionable recommendations AND config changes
    based on performance data and current config values.

    Returns (recommendations_text, config_changes_list).
    """
    system = """You are a trading portfolio manager reviewing three autonomous trading agents.
You have two outputs:

1. RECOMMENDATIONS: 3-5 specific, actionable text recommendations (plain text, numbered)
2. CONFIG_CHANGES: a JSON array of config parameter changes to apply

Focus on:
- Which agent is performing well and why (what should be preserved)
- Which agent is underperforming and what the data suggests about the cause
- Whether leverage, position sizing, or indicator thresholds should change
- Fee impact analysis (are small trades being eaten by fees?)
- Cross-agent patterns

For CONFIG_CHANGES, you can adjust these parameters per agent:
- btc: MAX_POSITION_PCT, RSI_OVERSOLD, RSI_OVERBOUGHT, VOL_SPIKE_MULT, CANDLE_INTERVAL
- forex: MAX_POSITION_PCT, MIN_LEVERAGE, MAX_LEVERAGE, RSI_OVERSOLD, RSI_OVERBOUGHT, VOL_SPIKE_MULT
- stocks: MAX_POSITION_PCT, MAX_OPEN_POSITIONS, MIN_LEVERAGE, MAX_LEVERAGE, RSI_OVERSOLD, RSI_OVERBOUGHT, VOL_SPIKE_MULT

You CANNOT change: STOP_LOSS_PCT, DRAWDOWN_PAUSE_PCT, API keys, or any security parameters.

Only propose config changes when the data clearly supports them. Do not change things for the sake of changing them. If current values are working, leave them alone.

Respond with EXACTLY this format (no markdown fences):

RECOMMENDATIONS:
1. First recommendation...
2. Second recommendation...

CONFIG_CHANGES:
[{"agent_id": "forex", "param": "MAX_LEVERAGE", "value": 50, "reason": "short explanation"}]

If no config changes are needed, use an empty array: CONFIG_CHANGES: []"""

    slim = {}
    for agent_id, a in analyses.items():
        slim[agent_id] = {
            k: v for k, v in a.items()
            if k in (
                "balance", "total_return_pct", "max_drawdown_pct",
                "total_closed", "win_rate", "avg_win_pct", "avg_loss_pct",
                "profit_factor", "sharpe", "by_side", "by_symbol",
                "by_confidence", "by_sizing", "recent_wr", "prior_wr",
            )
        }

    user = f"""Performance data for all three agents:
{json.dumps(slim, indent=2, default=str)}

Current config values:
{json.dumps(current_configs, indent=2, default=str)}

Provide recommendations and any config changes supported by the data."""

    try:
        resp = ai.messages.create(
            model=OPUS_MODEL,
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = resp.content[0].text.strip()

        # Parse recommendations text
        recommendations = raw
        config_changes = []

        if "CONFIG_CHANGES:" in raw:
            parts = raw.split("CONFIG_CHANGES:", 1)
            recommendations = parts[0].replace("RECOMMENDATIONS:", "").strip()
            try:
                changes_raw = parts[1].strip()
                config_changes = json.loads(changes_raw)
            except (json.JSONDecodeError, IndexError) as e:
                log.warning(f"Could not parse config changes JSON: {e}")
                config_changes = []

        log.info(f"Recommendations generated. Config changes proposed: {len(config_changes)}")
        return recommendations, config_changes

    except Exception as e:
        log.error(f"Recommendation generation failed: {e}")
        return f"Failed to generate recommendations: {e}", []


def run(dry_run: bool = False):
    """Full management cycle."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info(f"=== Manager run started at {ts} ===")

    # Step 1: Analyze
    log.info("Step 1: Analyzing all agents...")
    analyses = analyze_all()

    for agent_id, a in analyses.items():
        log.info(
            f"  {agent_id}: {a.get('total_closed', 0)} trades, "
            f"WR {a.get('win_rate', 0)}%, "
            f"P&L ${a.get('total_pnl_usd', 0):+.4f}, "
            f"balance ${a.get('balance', 0):.2f}"
        )

    # Step 2: Read current configs and generate recommendations
    log.info("Step 2: Reading configs and generating recommendations...")
    current_configs = get_adjustable_summary()
    recommendations, config_changes = generate_recommendations(analyses, current_configs)
    log.info(f"Recommendations:\n{recommendations}")
    if config_changes:
        log.info(f"Proposed config changes: {json.dumps(config_changes, default=str)}")

    # Step 3: Apply config changes (unless dry run)
    config_results = []
    if config_changes and not dry_run:
        log.info("Step 3: Applying config changes...")
        changes_to_apply = [
            {"agent_id": c["agent_id"], "param": c["param"], "value": c["value"]}
            for c in config_changes
        ]
        config_results = apply_config_changes(changes_to_apply)
        for cr in config_results:
            if cr.get("success"):
                log.info(f"  {cr['agent_id']}/{cr['param']}: {cr['old_value']} -> {cr['new_value']}")
            else:
                log.error(f"  Config change failed: {cr.get('error')}")
    elif config_changes and dry_run:
        log.info("Step 3: Config changes SKIPPED (dry run)")
        for c in config_changes:
            log.info(f"  Would change {c['agent_id']}/{c['param']} to {c['value']} ({c.get('reason', '')})")
    else:
        log.info("Step 3: No config changes proposed")

    # Step 4: Rewrite strategies (unless dry run)
    strategy_results = {}
    if dry_run:
        log.info("Step 4: Strategy rewrites SKIPPED (dry run)")
        for agent_id in analyses:
            strategy_results[agent_id] = {"success": False, "error": "Dry run"}
    else:
        log.info("Step 4: Rewriting strategy files...")
        strategy_results = rewrite_all(analyses)
        for agent_id, sr in strategy_results.items():
            if sr.get("success"):
                log.info(f"  {agent_id}: {sr['old_lines']} -> {sr['new_lines']} lines")
            else:
                log.error(f"  {agent_id}: FAILED - {sr.get('error')}")

    # Step 5: Send report
    log.info("Step 5: Sending Telegram report...")
    send_management_report(analyses, strategy_results, recommendations,
                           config_changes=config_changes, config_results=config_results)

    log.info(f"=== Manager run complete ===")
    return analyses, strategy_results, recommendations, config_changes, config_results


def main():
    args = sys.argv[1:]

    if "--analyze" in args:
        analyses = analyze_all()
        configs = get_adjustable_summary()
        print(json.dumps({"performance": analyses, "configs": configs}, indent=2, default=str))
        return

    dry_run = "--dry-run" in args
    if dry_run:
        log.info("Running in dry-run mode (no file writes)")

    analyses, strategy_results, recommendations, config_changes, config_results = run(dry_run=dry_run)

    # Print summary to stdout
    print("\n=== MANAGER REPORT ===\n")
    for agent_id in ["btc", "forex", "stocks"]:
        a = analyses.get(agent_id, {})
        sr = strategy_results.get(agent_id, {})
        print(f"--- {agent_id.upper()} ---")
        print(f"  Balance: ${a.get('balance', 0):.2f} ({a.get('total_return_pct', 0):+.1f}%)")
        print(f"  Trades: {a.get('total_closed', 0)} | WR: {a.get('win_rate', 0):.1f}%")
        print(f"  P&L: ${a.get('total_pnl_usd', 0):+.4f}")
        if sr.get("success"):
            print(f"  Strategy: {sr['old_lines']} -> {sr['new_lines']} lines")
        print()

    if config_changes:
        print("CONFIG CHANGES:")
        for c in config_changes:
            print(f"  {c['agent_id']}/{c['param']} -> {c['value']} ({c.get('reason', '')})")
        print()

    if config_results:
        print("CONFIG RESULTS:")
        for cr in config_results:
            if cr.get("success"):
                print(f"  {cr['agent_id']}/{cr['param']}: {cr['old_value']} -> {cr['new_value']}")
            else:
                print(f"  FAILED: {cr.get('error')}")
        print()

    print("RECOMMENDATIONS:")
    print(recommendations)


if __name__ == "__main__":
    main()
