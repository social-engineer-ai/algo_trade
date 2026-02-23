"""Event-driven single-day replay engine."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from orb.backtest.broker_sim import BrokerSimulator
from orb.config import AppConfig
from orb.models import Candle, Side, TradeRecord
from orb.strategy.session import TradingSession

logger = logging.getLogger(__name__)

# Default delta for ITM options (~200 pts ITM).
# Used to synthesise premiums when real option data is unavailable.
_SYNTHETIC_DELTA = 0.65
_SYNTHETIC_BASE_PREMIUM = 350.0  # Typical ITM premium at entry


class DayResult:
    """Results for a single trading day."""

    def __init__(self, date: datetime, trades: list[TradeRecord]):
        self.date = date
        self.trades = trades

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.net_pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.net_pnl <= 0)

    @property
    def gross_pnl(self) -> float:
        return sum(t.gross_pnl for t in self.trades)

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def total_charges(self) -> float:
        return sum(t.charges for t in self.trades)


class BacktestEngine:
    """Replays a single day's candles through the strategy."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._broker = BrokerSimulator(config.backtest)

    def run_day(
        self,
        trading_date: datetime,
        underlying_candles: list[Candle],
        option_candles: dict[str, list[Candle]],
        warmup_candles: list[Candle] | None = None,
        synthetic_premiums: bool = False,
    ) -> DayResult:
        """Run the strategy for a single day.

        Args:
            trading_date: The trading date.
            underlying_candles: 1-min candles for the underlying (NIFTY spot/futures)
                               for the full trading day, sorted by timestamp.
            option_candles: Dict mapping option symbols to their 1-min candles.
                           Key format: "NIFTY24500CE" or similar.
                           This provides premiums for all potential strikes.
            warmup_candles: Previous day's last N candles for indicator warmup.

        Returns:
            DayResult with all trades for the day.
        """
        session = TradingSession(self._config, trading_date)

        # Tell the session which option symbols are available
        if option_candles:
            for sym in option_candles:
                session.set_option_symbol(sym)
        elif synthetic_premiums:
            # Register synthetic option symbols based on day's opening spot
            spot = underlying_candles[0].open
            rounded = round(spot / self._config.market.strike_step) * self._config.market.strike_step
            call_strike = rounded - self._config.market.itm_offset
            put_strike = rounded + self._config.market.itm_offset
            session.set_option_symbol(f"NIFTY{call_strike:.0f}CE")
            session.set_option_symbol(f"NIFTY{put_strike:.0f}PE")

        # Warm up indicators
        if warmup_candles:
            session.warm_up(warmup_candles)

        logger.info(f"=== Day: {trading_date.date()} | {len(underlying_candles)} candles ===")

        # Track reference price for synthetic premium calculation
        self._synthetic_ref_price: float | None = None

        for candle in underlying_candles:
            # Find matching option premium
            if synthetic_premiums and not option_candles:
                option_premium = self._get_synthetic_premium(
                    session, candle
                )
            else:
                option_premium = self._get_option_premium(
                    session, candle, option_candles
                )

            trade = session.process_candle(candle, option_premium)

            if trade:
                # Apply slippage to premiums
                trade.entry_premium = self._broker.apply_slippage(
                    trade.entry_premium, is_buy=True
                )
                trade.exit_premium = self._broker.apply_slippage(
                    trade.exit_premium, is_buy=False
                )
                # Recalculate gross P&L with slippage-adjusted premiums
                trade.gross_pnl = (
                    (trade.exit_premium - trade.entry_premium)
                    * trade.lot_size
                    * trade.lots
                )
                # Apply costs
                self._broker.apply_costs(trade)

            if session.is_done:
                break

        return DayResult(date=trading_date, trades=session.trades)

    def _get_option_premium(
        self,
        session: TradingSession,
        candle: Candle,
        option_candles: dict[str, list[Candle]],
    ) -> Optional[float]:
        """Look up option premium for the current candle timestamp.

        If position is active, look up the specific option being traded.
        If no position, find the matching option from available data.
        """
        pos = session._position.position

        if pos.is_active and pos.option_symbol:
            symbol = pos.option_symbol
        elif session._breakout and session._breakout.is_confirmed:
            from orb.models import Side
            breakout = session._breakout.breakout
            side = breakout.side
            option_type = "CE" if side == Side.CALL else "PE"

            # Find the matching symbol from available option data
            # (strike is fixed at data-fetch time based on day's open)
            symbol = None
            for sym in option_candles:
                if sym.endswith(option_type):
                    symbol = sym
                    break
            if symbol is None:
                return None
        else:
            return None

        # Find the candle for this timestamp
        if symbol not in option_candles:
            return None

        for opt_candle in option_candles[symbol]:
            if opt_candle.timestamp == candle.timestamp:
                return opt_candle.close

        return None

    def _get_synthetic_premium(
        self,
        session: TradingSession,
        candle: Candle,
    ) -> Optional[float]:
        """Approximate option premium from underlying price when real data is unavailable.

        Uses a fixed delta model: premium_change ≈ delta × underlying_change.
        For ITM calls, premium rises when underlying rises.
        For ITM puts, premium rises when underlying falls.
        """
        if not session._breakout or not session._breakout.is_confirmed:
            return None

        breakout = session._breakout.breakout
        side = breakout.side

        # Set reference price on first call after breakout
        if self._synthetic_ref_price is None:
            self._synthetic_ref_price = candle.open
            return _SYNTHETIC_BASE_PREMIUM

        underlying_change = candle.close - self._synthetic_ref_price

        if side == Side.CALL:
            premium = _SYNTHETIC_BASE_PREMIUM + _SYNTHETIC_DELTA * underlying_change
        else:  # PUT
            premium = _SYNTHETIC_BASE_PREMIUM - _SYNTHETIC_DELTA * underlying_change

        return max(0.05, premium)
