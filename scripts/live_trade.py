#!/usr/bin/env python3
"""Live trading CLI — runs one trading session per day.

Usage:
    python scripts/live_trade.py [--paper] [--config CONFIG_PATH]

Options:
    --paper     Run in paper trading mode (no real orders). Default.
    --live      Run with real orders (requires confirmation).
    --config    Path to config file (default: config/default_config.yaml)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from dotenv import load_dotenv

from orb.config import load_config
from orb.data.kite_auth import KiteSession
from orb.live.live_session import LiveSessionRunner


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="NIFTY ORB Live Trading")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--paper", action="store_true", default=True,
        help="Paper trading mode (default)",
    )
    mode_group.add_argument(
        "--live", action="store_true",
        help="Real trading mode",
    )
    parser.add_argument(
        "--config", default="config/default_config.yaml",
        help="Config file path",
    )
    parser.add_argument(
        "--lots", type=int, default=None,
        help="Number of lots to trade (overrides config)",
    )
    args = parser.parse_args()

    paper_mode = not args.live

    # Setup logging
    log_level = logging.DEBUG if paper_mode else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("output/live_session.log", mode="a"),
        ],
    )
    logger = logging.getLogger(__name__)

    # Load config
    config = load_config(args.config)

    # Read live config from YAML (with defaults)
    live_cfg = _load_live_config(args.config)

    lots = args.lots or live_cfg.get("lots", 1)
    max_daily_loss = live_cfg.get("max_daily_loss", 3000.0)
    state_file = live_cfg.get("state_file", "data/live_state.json")
    log_file = live_cfg.get("log_file", "output/live_log.csv")
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", live_cfg.get("telegram_bot_token", ""))
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", live_cfg.get("telegram_chat_id", ""))

    # Safety confirmation for live mode
    if not paper_mode:
        print("\n" + "=" * 60)
        print("  WARNING: LIVE TRADING MODE")
        print("  Real orders will be placed with real money!")
        print(f"  Lots: {lots} | Max daily loss: ₹{max_daily_loss}")
        print("=" * 60)
        confirm = input("\nType 'YES' to confirm: ").strip()
        if confirm != "YES":
            print("Aborted.")
            sys.exit(0)

    # Authenticate with Kite
    logger.info("Authenticating with Kite Connect...")
    kite_session = KiteSession(config.kite_api_key, config.kite_api_secret)

    if not kite_session.is_authenticated:
        print(f"\nPlease login at: {kite_session.get_login_url()}")
        request_token = input("Enter request token from redirect URL: ").strip()
        kite_session.generate_session(request_token)

    kite = kite_session.get_kite()
    logger.info("Kite authenticated successfully.")

    # Run the session
    runner = LiveSessionRunner(
        config=config,
        kite=kite,
        paper_mode=paper_mode,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        state_file=state_file,
        log_file=log_file,
        lots=lots,
        max_daily_loss=max_daily_loss,
    )
    runner.run()


def _load_live_config(config_path: str) -> dict:
    """Load the 'live' section from the YAML config, with defaults."""
    import yaml
    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        return raw.get("live", {})
    except Exception:
        return {}


if __name__ == "__main__":
    main()
