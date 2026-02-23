"""Event-driven single-day replay engine."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from orb.backtest.broker_sim import BrokerSimulator
from orb.config import AppConfig
from orb.models import Candle, TradeRecord
from orb.strategy.session import TradingSession

logger = logging.getLogger(__name__)


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

        # Warm up indicators
        if warmup_candles:
            session.warm_up(warmup_candles)

        logger.info(f"=== Day: {trading_date.date()} | {len(underlying_candles)} candles ===")

        for candle in underlying_candles:
            # Find matching option premium
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
        If no position, try to find the likely ITM option premium.
        """
        pos = session._position.position

        if pos.is_active and pos.option_symbol:
            symbol = pos.option_symbol
        elif session._breakout and session._breakout.is_confirmed:
            # Estimate the option symbol based on current spot
            from orb.models import Side
            breakout = session._breakout.breakout
            side = breakout.side
            option_type = "CE" if side == Side.CALL else "PE"
            strike_step = self._config.market.strike_step
            itm_offset = self._config.market.itm_offset
            rounded_spot = round(candle.close / strike_step) * strike_step
            if side == Side.CALL:
                strike = rounded_spot - itm_offset
            else:
                strike = rounded_spot + itm_offset
            symbol = f"NIFTY{strike:.0f}{option_type}"
        else:
            return None

        # Find the candle for this timestamp
        if symbol not in option_candles:
            return None

        for opt_candle in option_candles[symbol]:
            if opt_candle.timestamp == candle.timestamp:
                return opt_candle.close

        return None
