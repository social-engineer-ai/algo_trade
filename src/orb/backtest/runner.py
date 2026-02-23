"""Multi-day backtest orchestrator."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from orb.backtest.engine import BacktestEngine, DayResult
from orb.config import AppConfig
from orb.data.cache import DataCache
from orb.data.instruments import InstrumentResolver
from orb.models import Candle, Side

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Runs the backtest across multiple trading days."""

    def __init__(
        self,
        config: AppConfig,
        cache: DataCache,
        instrument_resolver: InstrumentResolver,
    ):
        self._config = config
        self._cache = cache
        self._resolver = instrument_resolver
        self._engine = BacktestEngine(config)

    def run(
        self,
        from_date: date,
        to_date: date,
        nifty_token: int | None = None,
    ) -> list[DayResult]:
        """Run backtest from from_date to to_date (inclusive).

        Args:
            from_date: Start date.
            to_date: End date.
            nifty_token: NIFTY spot instrument token. Auto-resolved if None.

        Returns:
            List of DayResult, one per trading day.
        """
        if nifty_token is None:
            nifty_token = self._resolver.get_nifty_spot_token()

        results: list[DayResult] = []
        current = from_date
        prev_day_candles: list[Candle] | None = None

        while current <= to_date:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            logger.info(f"Processing {current}")

            try:
                day_result = self._run_single_day(
                    current, nifty_token, prev_day_candles
                )
                if day_result and day_result.total_trades > 0:
                    results.append(day_result)
                    logger.info(
                        f"  {current}: {day_result.total_trades} trades, "
                        f"net P&L: {day_result.net_pnl:.2f}"
                    )
                else:
                    logger.info(f"  {current}: No trades")

                # Store last candles for warmup
                underlying = self._fetch_underlying_candles(current, nifty_token)
                if underlying:
                    warmup_count = self._config.strategy.warmup_candles
                    prev_day_candles = underlying[-warmup_count:]

            except Exception as e:
                logger.error(f"  Error on {current}: {e}")

            current += timedelta(days=1)

        return results

    def _run_single_day(
        self,
        trading_date: date,
        nifty_token: int,
        warmup_candles: list[Candle] | None,
    ) -> Optional[DayResult]:
        """Run strategy for a single day."""
        # Fetch underlying candles
        underlying = self._fetch_underlying_candles(trading_date, nifty_token)
        if not underlying:
            logger.warning(f"No underlying data for {trading_date}")
            return None

        # Determine likely option strikes and fetch their data
        option_candles = self._fetch_option_candles(trading_date, underlying)

        return self._engine.run_day(
            trading_date=datetime.combine(trading_date, datetime.min.time()),
            underlying_candles=underlying,
            option_candles=option_candles,
            warmup_candles=warmup_candles,
        )

    def _fetch_underlying_candles(
        self, trading_date: date, nifty_token: int
    ) -> list[Candle]:
        """Fetch 1-min candles for the underlying."""
        from_dt = f"{trading_date} 09:15:00"
        to_dt = f"{trading_date} 15:30:00"

        raw = self._cache.get_candles(nifty_token, from_dt, to_dt, "minute")
        return [
            Candle(
                timestamp=datetime.fromisoformat(r["timestamp"])
                if isinstance(r["timestamp"], str)
                else r["timestamp"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r.get("volume", 0),
            )
            for r in raw
        ]

    def _fetch_option_candles(
        self, trading_date: date, underlying: list[Candle]
    ) -> dict[str, list[Candle]]:
        """Fetch option candles for likely ITM strikes.

        Determines strikes based on the opening price and fetches CE and PE
        candles for a range of strikes around ITM.
        """
        if not underlying:
            return {}

        spot = underlying[0].open  # Opening price
        strike_step = self._config.market.strike_step
        itm_offset = self._config.market.itm_offset
        rounded_spot = round(spot / strike_step) * strike_step

        # Calculate ITM strikes for both sides
        call_strike = rounded_spot - itm_offset
        put_strike = rounded_spot + itm_offset

        # Find nearest expiry
        expiry = self._resolver.get_nearest_expiry(trading_date)

        option_candles: dict[str, list[Candle]] = {}

        for strike, opt_type in [(call_strike, "CE"), (put_strike, "PE")]:
            token = self._resolver.get_option_token(strike, opt_type, expiry)
            if token is None:
                logger.warning(
                    f"No token for NIFTY {strike}{opt_type} exp {expiry}"
                )
                continue

            symbol = f"NIFTY{strike:.0f}{opt_type}"
            from_dt = f"{trading_date} 09:15:00"
            to_dt = f"{trading_date} 15:30:00"

            raw = self._cache.get_candles(token, from_dt, to_dt, "minute")
            candles = [
                Candle(
                    timestamp=datetime.fromisoformat(r["timestamp"])
                    if isinstance(r["timestamp"], str)
                    else r["timestamp"],
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r.get("volume", 0),
                )
                for r in raw
            ]
            if candles:
                option_candles[symbol] = candles
                logger.debug(f"  Loaded {len(candles)} candles for {symbol}")

        return option_candles
