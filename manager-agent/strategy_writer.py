"""
strategy_writer.py
Reads a bloated strategy file and the agent's performance data,
then uses Sonnet to produce a clean, distilled replacement.
Backs up the old file before overwriting.

This is the highest-value function in the manager agent.
"""
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone

import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import (
    ANTHROPIC_API_KEY,
    OPUS_MODEL,
    AGENTS,
    MAX_STRATEGY_LINES,
    BACKUP_STRATEGIES,
    LOG_DIR,
)

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "strategy_writer.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def rewrite_strategy(agent_id: str, analysis: dict) -> dict:
    """
    Read the current strategy file, send it plus performance data to Sonnet,
    and replace the file with a clean distilled version.

    Returns a dict with keys: success, old_lines, new_lines, backup_path, summary.
    """
    agent_cfg = AGENTS.get(agent_id)
    if not agent_cfg:
        return {"success": False, "error": f"Unknown agent: {agent_id}"}

    strategy_path = agent_cfg["strategy_path"]
    if not os.path.exists(strategy_path):
        return {"success": False, "error": f"Strategy file not found: {strategy_path}"}

    # Read the current bloated strategy
    with open(strategy_path, "r") as f:
        old_content = f.read()
    old_lines = old_content.count("\n")

    # Build the Sonnet prompt
    system = f"""You are a trading strategy analyst and editor. Your job is to take a bloated,
repetitive strategy file and distill it into a clean, actionable document.

The strategy file has grown organically over months of trading. Every WAIT decision
appended a line, every daily review added notes, and the result is thousands of lines
where 95% are duplicates or near-duplicates. The trading brain reads the LAST 300-600
characters of this file on every decision cycle, so the file needs to be concise and
the most important rules need to be near the end.

RULES FOR THE REWRITE:
1. Keep the initial configuration section (pair, indicators, risk params) intact
2. Distill all the repeated observations into a clean ruleset (max {MAX_STRATEGY_LINES} lines total)
3. Group rules by category: Entry conditions, Exit conditions, Risk management, Market regime filters
4. Preserve any genuinely useful learned rules (not repetitions of the same rule)
5. Remove ALL timestamp-prefixed entries that just repeat the same observation
6. Keep the most recent daily review insights if they add new information
7. Add a "Performance summary" section at the end with key stats from the data
8. Write in the same first-person agent voice the file already uses
9. The final section should contain the current active rules (these go at the end
   because the brain reads the last 300-600 chars)

Do NOT add rules that aren't supported by the data. Do NOT invent new strategies.
Only distill what is already there and add the performance context.

Respond with ONLY the new strategy file content. No preamble, no explanation, no markdown fences."""

    # Trim old content if it's extremely large (keep first 2000 + last 8000 chars)
    content_for_prompt = old_content
    if len(old_content) > 12000:
        content_for_prompt = (
            old_content[:2000]
            + "\n\n[... middle section omitted for brevity -- contains "
            + f"{old_lines - 200} lines of mostly repeated observations ...]\n\n"
            + old_content[-8000:]
        )

    # Slim down analysis for the prompt
    slim_analysis = {
        k: v for k, v in analysis.items()
        if k in (
            "agent_id", "balance", "total_return_pct", "max_drawdown_pct",
            "total_closed", "wins", "losses", "win_rate", "avg_win_pct",
            "avg_loss_pct", "profit_factor", "sharpe", "best_trade_pct",
            "worst_trade_pct", "by_side", "by_symbol", "by_confidence",
            "by_sizing", "recent_wr", "prior_wr", "last_5",
        )
    }

    user = f"""Agent: {agent_cfg['display']} ({agent_id})
Side options: {agent_cfg['side_options']}
Symbols: {agent_cfg['symbols']}

PERFORMANCE DATA:
{json.dumps(slim_analysis, indent=2, default=str)}

CURRENT STRATEGY FILE ({old_lines} lines, needs distilling):
---
{content_for_prompt}
---

Rewrite this into a clean, distilled strategy file of no more than {MAX_STRATEGY_LINES} lines.
Preserve the header, distill all rules, remove repetition, add performance context at the end."""

    try:
        resp = ai.messages.create(
            model=OPUS_MODEL,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        new_content = resp.content[0].text.strip()
        new_lines = new_content.count("\n")

        # Add a management footer
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        footer = (
            f"\n\n## Management review {ts}\n"
            f"Strategy distilled from {old_lines} lines to {new_lines} lines by manager agent.\n"
            f"Performance at time of review: {analysis.get('total_closed', 0)} trades, "
            f"{analysis.get('win_rate', 0)}% WR, "
            f"${analysis.get('total_pnl_usd', 0):+.2f} total P&L, "
            f"balance ${analysis.get('balance', 0):.2f}\n"
        )
        new_content += footer
        new_lines = new_content.count("\n")

        # Backup the old file
        backup_path = None
        if BACKUP_STRATEGIES:
            backup_dir = os.path.join(
                os.path.dirname(strategy_path), "backups"
            )
            os.makedirs(backup_dir, exist_ok=True)
            ts_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"current_{ts_file}.md")
            shutil.copy2(strategy_path, backup_path)
            log.info(f"Backed up {strategy_path} to {backup_path}")

        # Write the new strategy
        with open(strategy_path, "w") as f:
            f.write(new_content)

        log.info(
            f"Rewrote {agent_id} strategy: {old_lines} -> {new_lines} lines"
        )

        # Generate a short summary of what changed
        summary = (
            f"Distilled {old_lines} lines to {new_lines} lines. "
            f"Removed repetitive observations, consolidated entry/exit rules, "
            f"added performance context."
        )

        return {
            "success": True,
            "old_lines": old_lines,
            "new_lines": new_lines,
            "backup_path": backup_path,
            "summary": summary,
        }

    except Exception as e:
        log.error(f"Strategy rewrite failed for {agent_id}: {e}")
        return {"success": False, "error": str(e)}


def rewrite_all(analyses: dict) -> dict:
    """Rewrite strategy files for all agents."""
    results = {}
    for agent_id, analysis in analyses.items():
        log.info(f"Rewriting strategy for {agent_id}...")
        results[agent_id] = rewrite_strategy(agent_id, analysis)
    return results


if __name__ == "__main__":
    from analyzer import analyze_all
    analyses = analyze_all()
    results = rewrite_all(analyses)
    for agent_id, result in results.items():
        print(f"\n--- {agent_id} ---")
        print(json.dumps(result, indent=2, default=str))
