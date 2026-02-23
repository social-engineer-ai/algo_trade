"""Per-day session orchestrator — wires ORB, breakout, entry, exit, and position."""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Optional

from orb.config import AppConfig
from orb.indicators.rsi import RSI
from orb.indicators.supertrend import SuperTrend
from orb.models import Candle, ExitReason, Side, TradeRecord
from orb.strategy.breakout import BreakoutDetector
from orb.strategy.entry import EntrySignal
from orb.strategy.exit import ExitManager
from orb.strategy.opening_range import OpeningRangeDetector
from orb.strategy.position import PositionManager

logger = logging.getLogger(__name__)


class TradingSession:
    """Orchestrates a single trading day.

    Call `process_candle()` for each 1-min candle (underlying + option premium)
    in chronological order. Collects TradeRecords for all completed trades.
    """

    def __init__(self, config: AppConfig, trading_date: datetime):
        self._config = config
        self._trading_date = trading_date
        self._trades: list[TradeRecord] = []

        # Strategy components
        self._orb = OpeningRangeDetector(num_candles=config.session.orb_candles)
        self._breakout: Optional[BreakoutDetector] = None
        self._entry: Optional[EntrySignal] = None
        self._exit = ExitManager(config.strategy.trailing_ladder)
        self._position = PositionManager(
            lot_size=config.market.lot_size,
            lots=1,
        )

        # Indicators
        self._rsi = RSI(period=config.strategy.rsi_period)
        self._supertrend = SuperTrend(
            period=config.strategy.supertrend_period,
            multiplier=config.strategy.supertrend_multiplier,
        )

        # State
        self._candle_count = 0
        self._last_candle: Optional[Candle] = None
        self._day_done = False
        self._available_option_symbols: dict[str, str] = {}  # "CE"→symbol, "PE"→symbol

    def set_option_symbol(self, symbol: str) -> None:
        """Register an available option symbol for the day."""
        if symbol.endswith("CE"):
            self._available_option_symbols["CE"] = symbol
        elif symbol.endswith("PE"):
            self._available_option_symbols["PE"] = symbol

    @property
    def trades(self) -> list[TradeRecord]:
        return list(self._trades)

    @property
    def is_done(self) -> bool:
        return self._day_done

    def warm_up(self, candles: list[Candle]) -> None:
        """Feed prior-day candles to warm up RSI and SuperTrend indicators."""
        for c in candles:
            self._rsi.update(c.close)
            self._supertrend.update(c.high, c.low, c.close)

    def process_candle(
        self,
        underlying_candle: Candle,
        option_premium: float | None = None,
    ) -> Optional[TradeRecord]:
        """Process one 1-min candle. Returns a TradeRecord if a trade was closed.

        Args:
            underlying_candle: NIFTY spot/futures 1-min candle.
            option_premium: Current option premium (close of the option 1-min candle).
                           None if no option data available for this minute.

        Returns:
            TradeRecord if a position was exited this candle, None otherwise.
        """
        if self._day_done:
            return None

        self._candle_count += 1
        candle = underlying_candle
        candle_time = candle.timestamp.time()

        # Update indicators on every candle
        self._rsi.update(candle.close)
        self._supertrend.update(candle.high, candle.low, candle.close)

        # --- Phase 1: ORB accumulation (09:15–09:18) ---
        if not self._orb.is_complete:
            completed = self._orb.update(candle)
            if completed:
                logger.info(
                    f"ORB complete: H3={self._orb.h3:.2f}, L3={self._orb.l3:.2f}"
                )
                self._breakout = BreakoutDetector(self._orb.h3, self._orb.l3)
                self._entry = EntrySignal(
                    rsi=self._rsi,
                    supertrend=self._supertrend,
                    rsi_min=self._config.strategy.rsi_entry_min,
                    rsi_max=self._config.strategy.rsi_entry_max,
                    no_entry_after=self._config.session.no_new_entry_after,
                    max_re_entries=self._config.strategy.max_re_entries_per_side,
                )
            self._last_candle = candle
            return None

        # --- Force exit check (15:15) ---
        if candle_time >= self._config.session.force_exit_time:
            trade = self._handle_force_exit(candle, option_premium)
            self._day_done = True
            return trade

        # --- Phase 2: Breakout detection ---
        if self._breakout and not self._breakout.is_confirmed:
            breakout_info = self._breakout.update(candle)
            if breakout_info:
                logger.info(
                    f"Breakout confirmed: {breakout_info.side.name}, "
                    f"H1={breakout_info.h1:.2f}, L1={breakout_info.l1:.2f}"
                )
                self._position.on_breakout(breakout_info)

        # --- Phase 3: Exit check (if in position) ---
        if self._position.is_active and option_premium is not None:
            trade = self._check_exit(candle, option_premium)
            if trade:
                self._last_candle = candle
                return trade

        # --- Phase 4: Entry check (if not in position) ---
        # Allow entry from IDLE (re-entry after exit) or WAITING_ENTRY (first entry after breakout)
        if (
            not self._position.is_active
            and self._breakout
            and self._breakout.is_confirmed
            and self._entry
            and option_premium is not None
        ):
            breakout = self._breakout.breakout
            side = breakout.side

            # Check SL-before-entry (conservative)
            if not self._entry.check_sl_before_entry(candle, breakout):
                entries = self._position.entries_for_side(side)
                entry_side = self._entry.check_entry(
                    candle, breakout, entries, candle_time
                )
                if entry_side:
                    self._execute_entry(candle, option_premium, entry_side)

        self._last_candle = candle
        return None

    def _execute_entry(
        self,
        candle: Candle,
        option_premium: float,
        side: Side,
    ) -> None:
        """Execute entry into a position."""
        breakout = self._breakout.breakout
        option_type = "CE" if side == Side.CALL else "PE"

        # Use registered symbol if available, else compute from current price
        if option_type in self._available_option_symbols:
            option_symbol = self._available_option_symbols[option_type]
            # Extract strike from symbol (e.g. "NIFTY25850PE" → 25850)
            strike_str = option_symbol.replace("NIFTY", "").replace("CE", "").replace("PE", "")
            strike = float(strike_str)
        else:
            spot = candle.close
            strike_step = self._config.market.strike_step
            itm_offset = self._config.market.itm_offset
            rounded_spot = round(spot / strike_step) * strike_step
            if side == Side.CALL:
                strike = rounded_spot - itm_offset
            else:
                strike = rounded_spot + itm_offset
            option_symbol = f"NIFTY{strike:.0f}{option_type}"

        logger.info(
            f"ENTRY: {side.name} @ {candle.timestamp}, "
            f"premium={option_premium:.2f}, strike={strike}, "
            f"underlying={candle.close:.2f}"
        )

        self._position.on_entry(
            side=side,
            entry_premium=option_premium,
            entry_time=candle.timestamp,
            underlying_price=candle.close,
            strike=strike,
            option_type=option_type,
            option_symbol=option_symbol,
        )

    def _check_exit(
        self, candle: Candle, option_premium: float
    ) -> Optional[TradeRecord]:
        """Check exit conditions and close position if triggered."""
        pos = self._position.position
        breakout = pos.breakout

        current_regime = "B" if pos.state.name == "ACTIVE_REGIME_B" else "A"

        exit_signal, new_regime, new_sl, new_gain, new_idx = self._exit.check_exit(
            underlying_candle=candle,
            option_premium=option_premium,
            side=pos.side,
            h1=breakout.h1,
            l1=breakout.l1,
            entry_premium=pos.entry_premium,
            current_regime=current_regime,
            premium_sl=pos.premium_sl,
            highest_gain=pos.highest_premium_gain,
            last_ladder_idx=pos.last_triggered_ladder_idx,
        )

        # Update position state
        pos.premium_sl = new_sl
        pos.highest_premium_gain = new_gain
        pos.last_triggered_ladder_idx = new_idx
        if new_regime != current_regime:
            self._position.on_regime_change(new_regime)

        if exit_signal:
            logger.info(
                f"EXIT: {exit_signal.reason.name} @ {candle.timestamp}, "
                f"premium={exit_signal.exit_premium:.2f}, "
                f"underlying={candle.close:.2f}"
            )
            trade = self._position.on_exit(
                exit_premium=exit_signal.exit_premium,
                exit_time=candle.timestamp,
                underlying_price=candle.close,
                exit_reason=exit_signal.reason,
            )
            self._trades.append(trade)
            return trade

        return None

    def _handle_force_exit(
        self, candle: Candle, option_premium: float | None
    ) -> Optional[TradeRecord]:
        """Force-exit any open position at 15:15."""
        if not self._position.is_active:
            return None

        premium = option_premium if option_premium is not None else 0.0
        exit_signal = self._exit.check_force_exit(premium)

        logger.info(f"FORCE EXIT @ {candle.timestamp}")
        trade = self._position.on_exit(
            exit_premium=exit_signal.exit_premium,
            exit_time=candle.timestamp,
            underlying_price=candle.close,
            exit_reason=ExitReason.FORCE_EXIT,
        )
        self._trades.append(trade)
        return trade
