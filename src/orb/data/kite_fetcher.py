"""Historical data fetcher with Kite API rate limiting and date chunking."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from kiteconnect import KiteConnect


# Kite allows max 60 days of minute data per request.
_MAX_MINUTE_DAYS = 60
# Rate limit: max 3 requests/sec → sleep at least 0.34 s between calls.
_MIN_REQUEST_INTERVAL = 0.34


class KiteFetcher:
    """Fetches historical candle data and instrument dumps from Kite Connect."""

    def __init__(self, kite: KiteConnect) -> None:
        self._kite = kite
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_candles(
        self,
        instrument_token: int,
        from_date: str,
        to_date: str,
        interval: str = "minute",
    ) -> list[dict]:
        """Fetch OHLCV candles, automatically chunking long date ranges.

        Parameters
        ----------
        instrument_token : int
            Kite instrument token.
        from_date, to_date : str
            ISO-style date strings, e.g. ``"2025-01-01"`` or ``"2025-01-01 09:15:00"``.
        interval : str
            Candle interval — ``"minute"``, ``"3minute"``, ``"5minute"``, ``"day"``, etc.

        Returns
        -------
        list[dict]
            Each dict has keys: timestamp, open, high, low, close, volume.
        """
        dt_from = self._parse_dt(from_date)
        dt_to = self._parse_dt(to_date)

        # Determine chunk size (in days) based on interval
        max_days = self._max_days_for_interval(interval)

        all_candles: list[dict] = []
        chunk_start = dt_from

        while chunk_start <= dt_to:
            chunk_end = min(chunk_start + timedelta(days=max_days - 1), dt_to)
            self._throttle()
            raw = self._kite.historical_data(
                instrument_token,
                from_date=chunk_start,
                to_date=chunk_end,
                interval=interval,
            )
            for row in raw:
                all_candles.append(
                    {
                        "timestamp": str(row["date"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row["volume"]),
                    }
                )
            chunk_start = chunk_end + timedelta(days=1)

        return all_candles

    def fetch_instruments(self, exchange: str = "NFO") -> list[dict]:
        """Return the full instrument dump for *exchange*."""
        self._throttle()
        return self._kite.instruments(exchange)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """Enforce rate limit of ~3 requests/sec."""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_ts = time.monotonic()

    @staticmethod
    def _parse_dt(dt_str: str) -> datetime:
        """Parse a date or datetime string to a ``datetime`` object."""
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse date string: {dt_str!r}")

    @staticmethod
    def _max_days_for_interval(interval: str) -> int:
        """Return the maximum number of days Kite allows per request for *interval*."""
        if interval in ("minute", "2minute", "3minute"):
            return _MAX_MINUTE_DAYS
        if interval in ("5minute", "10minute", "15minute", "30minute", "60minute"):
            return 100
        # day, week, month — essentially unlimited; use 2000 as a safe cap
        return 2000
