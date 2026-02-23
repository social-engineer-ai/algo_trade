#!/usr/bin/env python3
"""CLI: Download historical data from Kite Connect to SQLite.

Usage:
    python scripts/fetch_data.py --from 2025-01-06 --to 2025-01-10
    python scripts/fetch_data.py --from 2025-01-06 --to 2025-01-10 --token <request_token>
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orb.config import load_config
from orb.data.db import Database
from orb.data.kite_auth import KiteSession
from orb.data.kite_fetcher import KiteFetcher
from orb.data.instruments import InstrumentResolver
from orb.data.cache import DataCache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(description="Fetch historical data from Kite Connect")
    parser.add_argument("--from", dest="from_date", required=True, type=parse_date,
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", required=True, type=parse_date,
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--token", dest="request_token", default=None,
                        help="Kite request_token (from login redirect)")
    parser.add_argument("--config", default="config/default_config.yaml",
                        help="Path to config file")
    parser.add_argument("--db", default="data/orb_data.db",
                        help="Path to SQLite database")
    args = parser.parse_args()

    config = load_config(args.config)

    # Authenticate with Kite
    kite_session = KiteSession(config.kite_api_key, config.kite_api_secret)

    if args.request_token:
        kite_session.generate_session(args.request_token)
    elif not kite_session.is_authenticated:
        print(f"\nPlease login at:\n  {kite_session.get_login_url()}\n")
        print("Then re-run with --token <request_token> from the redirect URL.\n")
        sys.exit(1)

    kite = kite_session.get_kite()
    db = Database(args.db)
    fetcher = KiteFetcher(kite)
    resolver = InstrumentResolver(db)
    cache = DataCache(db, fetcher)

    # Step 1: Fetch and store instrument dump
    logger.info("Fetching NFO instruments...")
    instruments = fetcher.fetch_instruments("NFO")
    resolver.load_instruments(instruments)
    logger.info(f"Loaded {len(instruments)} NFO instruments")

    # Also fetch NSE instruments for NIFTY spot
    nse_instruments = fetcher.fetch_instruments("NSE")
    resolver.load_instruments(nse_instruments)
    logger.info(f"Loaded {len(nse_instruments)} NSE instruments")

    # Step 2: Fetch NIFTY spot candles
    nifty_token = resolver.get_nifty_spot_token()
    logger.info(f"NIFTY 50 token: {nifty_token}")

    logger.info(f"Fetching NIFTY spot data from {args.from_date} to {args.to_date}...")
    from_dt = f"{args.from_date} 09:15:00"
    to_dt = f"{args.to_date} 15:30:00"
    candles = cache.get_candles(nifty_token, from_dt, to_dt, "minute")
    logger.info(f"Fetched/cached {len(candles)} spot candles")

    # Step 3: Fetch option candles for likely strikes
    current = args.from_date
    while current <= args.to_date:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        logger.info(f"Fetching option data for {current}...")

        # Get day's spot data to determine strikes
        day_from = f"{current} 09:15:00"
        day_to = f"{current} 15:30:00"
        day_candles = cache.get_candles(nifty_token, day_from, day_to, "minute")

        if day_candles:
            spot = day_candles[0]["open"]
            strike_step = config.market.strike_step
            itm_offset = config.market.itm_offset
            rounded = round(spot / strike_step) * strike_step

            call_strike = rounded - itm_offset
            put_strike = rounded + itm_offset
            expiry = resolver.get_nearest_expiry(current)

            for strike, opt_type in [(call_strike, "CE"), (put_strike, "PE")]:
                token = resolver.get_option_token(strike, opt_type, expiry)
                if token:
                    opt_candles = cache.get_candles(token, day_from, day_to, "minute")
                    logger.info(
                        f"  {strike}{opt_type} exp={expiry}: {len(opt_candles)} candles"
                    )
                else:
                    logger.warning(f"  No token for {strike}{opt_type} exp={expiry}")

        current += timedelta(days=1)

    db.close()
    logger.info("Done!")


if __name__ == "__main__":
    main()
