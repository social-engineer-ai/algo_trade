#!/usr/bin/env python3
"""Fetch option candles for all trading days that have spot data."""
import sys, logging
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger()

from orb.config import load_config
from orb.data.db import Database
from orb.data.kite_auth import KiteSession
from orb.data.kite_fetcher import KiteFetcher
from orb.data.instruments import InstrumentResolver
from orb.data.cache import DataCache

config = load_config('config/default_config.yaml')
ks = KiteSession(config.kite_api_key, config.kite_api_secret)
kite = ks.get_kite()
db = Database('data/orb_data.db')
fetcher = KiteFetcher(kite)
resolver = InstrumentResolver(db)
cache = DataCache(db, fetcher)

nifty_token = resolver.get_nifty_spot_token()

# Get all trading days
with db._connect() as conn:
    rows = conn.execute("""
        SELECT DISTINCT substr(timestamp, 1, 10) as dt
        FROM candles WHERE instrument_token = 256265
        ORDER BY dt
    """).fetchall()
    trading_days = [date.fromisoformat(r['dt']) for r in rows]

logger.info(f"Processing {len(trading_days)} trading days for option data...")

fetched = 0
skipped = 0
errors = 0

for td in trading_days:
    day_from = f'{td} 09:15:00'
    day_to = f'{td} 15:30:00'

    day_candles = db.get_candles(nifty_token, day_from, day_to, 'minute')
    if not day_candles:
        continue

    spot = day_candles[0]['open']
    rounded = round(spot / 50) * 50
    call_strike = rounded - 200
    put_strike = rounded + 200
    expiry = resolver.get_nearest_expiry(td)

    for strike, opt_type in [(call_strike, 'CE'), (put_strike, 'PE')]:
        token = resolver.get_option_token(strike, opt_type, expiry)
        if not token:
            logger.warning(f"  {td} No token for {strike}{opt_type} exp={expiry}")
            errors += 1
            continue

        existing = db.get_candles(token, day_from, day_to, 'minute')
        if existing:
            skipped += 1
            continue

        try:
            opt_candles = cache.get_candles(token, day_from, day_to, 'minute')
            fetched += 1
            if fetched % 10 == 0:
                logger.info(f"  Progress: {fetched} fetched, at {td}")
        except Exception as e:
            logger.error(f"  Error fetching {strike}{opt_type} for {td}: {e}")
            errors += 1

logger.info(f"Done! Fetched={fetched}, Skipped(cached)={skipped}, Errors={errors}")
db.close()
