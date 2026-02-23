"""Position state machine."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from orb.models import (
    BreakoutInfo,
    ExitReason,
    Position,
    PositionState,
    Side,
    TradeRecord,
)


class PositionManager:
    """Manages position lifecycle: IDLE → WAITING → ACTIVE_A → ACTIVE_B → CLOSED."""

    def __init__(self, lot_size: int = 25, lots: int = 1):
        self._lot_size = lot_size
        self._lots = lots
        self._position = Position()
        self._trade_counter = 0

    @property
    def position(self) -> Position:
        return self._position

    @property
    def is_active(self) -> bool:
        return self._position.is_active

    @property
    def is_idle(self) -> bool:
        return self._position.state == PositionState.IDLE

    def on_breakout(self, breakout: BreakoutInfo) -> None:
        """Transition to WAITING_ENTRY after breakout confirmation."""
        if self._position.state != PositionState.IDLE:
            return
        self._position.state = PositionState.WAITING_ENTRY
        self._position.breakout = breakout
        self._position.side = breakout.side

    def on_entry(
        self,
        side: Side,
        entry_premium: float,
        entry_time: datetime,
        underlying_price: float,
        strike: float,
        option_type: str,
        option_symbol: str,
    ) -> None:
        """Transition to ACTIVE_REGIME_A on entry."""
        self._position.state = PositionState.ACTIVE_REGIME_A
        self._position.side = side
        self._position.entry_premium = entry_premium
        self._position.current_premium = entry_premium
        self._position.entry_time = entry_time
        self._position.underlying_at_entry = underlying_price
        self._position.strike = strike
        self._position.option_type = option_type
        self._position.option_symbol = option_symbol
        self._position.lots = self._lots
        self._position.premium_sl = None
        self._position.highest_premium_gain = 0.0
        self._position.last_triggered_ladder_idx = -1

        # Increment entry counter
        if side == Side.CALL:
            self._position.call_entries_today += 1
        else:
            self._position.put_entries_today += 1

    def on_regime_change(self, regime: str) -> None:
        """Transition between Regime A and B."""
        if regime == "B":
            self._position.state = PositionState.ACTIVE_REGIME_B
        elif regime == "A":
            self._position.state = PositionState.ACTIVE_REGIME_A

    def on_exit(
        self,
        exit_premium: float,
        exit_time: datetime,
        underlying_price: float,
        exit_reason: ExitReason,
    ) -> TradeRecord:
        """Close position and generate a TradeRecord."""
        self._trade_counter += 1
        pos = self._position
        breakout = pos.breakout

        gross_pnl = (exit_premium - pos.entry_premium) * self._lot_size * self._lots
        regime = "B" if pos.state == PositionState.ACTIVE_REGIME_B else "A"

        # Determine re-entry number
        if pos.side == Side.CALL:
            re_entry = pos.call_entries_today - 1  # 0-based
        else:
            re_entry = pos.put_entries_today - 1

        record = TradeRecord(
            trade_id=self._trade_counter,
            date=exit_time,
            side=pos.side or Side.CALL,
            entry_time=pos.entry_time or exit_time,
            exit_time=exit_time,
            underlying_entry=pos.underlying_at_entry,
            underlying_exit=underlying_price,
            h3=breakout.h3 if breakout else 0.0,
            l3=breakout.l3 if breakout else 0.0,
            h1=breakout.h1 if breakout else 0.0,
            l1=breakout.l1 if breakout else 0.0,
            strike=pos.strike,
            option_type=pos.option_type,
            option_symbol=pos.option_symbol,
            entry_premium=pos.entry_premium,
            exit_premium=exit_premium,
            lots=self._lots,
            lot_size=self._lot_size,
            gross_pnl=gross_pnl,
            charges=0.0,  # Filled in by broker_sim
            net_pnl=gross_pnl,  # Updated after charges
            exit_reason=exit_reason,
            re_entry_number=re_entry,
            regime_at_exit=regime,
        )

        # Reset position to IDLE for potential re-entry
        self._position.state = PositionState.IDLE
        self._position.entry_premium = 0.0
        self._position.current_premium = 0.0
        self._position.entry_time = None
        self._position.premium_sl = None
        self._position.highest_premium_gain = 0.0
        self._position.last_triggered_ladder_idx = -1
        # Note: breakout, side, entry counters persist for re-entries

        return record

    def reset_for_new_day(self) -> None:
        """Full reset for a new trading day."""
        self._position = Position()

    def entries_for_side(self, side: Side) -> int:
        """Return number of entries taken for a given side today."""
        if side == Side.CALL:
            return self._position.call_entries_today
        return self._position.put_entries_today
