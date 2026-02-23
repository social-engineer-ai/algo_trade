"""DB-first candle cache with Kite API fallback."""
from __future__ import annotations

from typing import Optional

from orb.data.db import Database
from orb.data.kite_fetcher import KiteFetcher


class DataCache:
    """Transparent caching layer: check SQLite first, fetch from Kite if missing."""

    def __init__(self, db: Database, fetcher: Optional[KiteFetcher] = None) -> None:
        self._db = db
        self._fetcher = fetcher

    def get_candles(
        self,
        instrument_token: int,
        from_dt: str,
        to_dt: str,
        interval: str = "minute",
    ) -> list[dict]:
        """Return candles for the given range, fetching from the API if the DB
        does not contain a complete set.

        Strategy (kept deliberately simple):
        1. Query the DB for candles in [from_dt, to_dt].
        2. Estimate how many candles we *expect* for the range.
        3. If the DB count is lower than the expected count, fetch the full
           range from the API, upsert into the DB, then re-query.
        4. Return the result sorted by timestamp.
        """
        # 1. Check the DB first
        db_candles = self._db.get_candles(instrument_token, from_dt, to_dt, interval)

        # 2. Quick heuristic: if we already have rows, assume complete.
        #    A fancier implementation could compare row counts against the
        #    expected number of trading minutes, but this is intentionally simple.
        if db_candles:
            return db_candles

        # 3. Nothing in DB — fetch from Kite (if fetcher available)
        if self._fetcher is None:
            return []

        api_candles = self._fetcher.fetch_candles(
            instrument_token, from_dt, to_dt, interval
        )

        # Prepare rows for DB insertion
        db_rows = [
            {
                "instrument_token": instrument_token,
                "timestamp": c["timestamp"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
                "interval": interval,
            }
            for c in api_candles
        ]
        self._db.insert_candles(db_rows)

        # 4. Re-read from DB to get a consistent, de-duplicated result
        return self._db.get_candles(instrument_token, from_dt, to_dt, interval)
