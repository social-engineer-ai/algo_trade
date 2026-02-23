"""Tests for position state machine."""
import pytest
from datetime import datetime
from orb.models import BreakoutInfo, ExitReason, PositionState, Side
from orb.strategy.position import PositionManager


def _breakout():
    return BreakoutInfo(
        side=Side.CALL, breakout_candle_idx=1,
        h3=24070, l3=23980, h1=24090, l1=24030,
        confirmed_at=datetime(2025, 1, 6, 9, 19),
    )


def test_lifecycle():
    pm = PositionManager(lot_size=25, lots=1)
    assert pm.is_idle

    # Breakout
    pm.on_breakout(_breakout())
    assert pm.position.state == PositionState.WAITING_ENTRY

    # Entry
    pm.on_entry(
        side=Side.CALL, entry_premium=260.0,
        entry_time=datetime(2025, 1, 6, 9, 21),
        underlying_price=24095.0, strike=23800.0,
        option_type="CE", option_symbol="NIFTY23800CE",
    )
    assert pm.is_active
    assert pm.position.state == PositionState.ACTIVE_REGIME_A
    assert pm.position.call_entries_today == 1

    # Regime change
    pm.on_regime_change("B")
    assert pm.position.state == PositionState.ACTIVE_REGIME_B

    # Exit
    trade = pm.on_exit(
        exit_premium=290.0, exit_time=datetime(2025, 1, 6, 9, 35),
        underlying_price=24150.0, exit_reason=ExitReason.PREMIUM_TRAIL_SL,
    )
    assert pm.is_idle
    assert trade.gross_pnl == (290.0 - 260.0) * 25
    assert trade.side == Side.CALL
    assert trade.re_entry_number == 0  # First entry


def test_re_entry_count():
    pm = PositionManager(lot_size=25, lots=1)
    pm.on_breakout(_breakout())

    # First entry + exit
    pm.on_entry(Side.CALL, 260.0, datetime(2025, 1, 6, 9, 21), 24095.0, 23800.0, "CE", "NIFTY23800CE")
    pm.on_exit(250.0, datetime(2025, 1, 6, 9, 25), 24020.0, ExitReason.CANDLE_SL)

    # Second entry
    pm.on_entry(Side.CALL, 255.0, datetime(2025, 1, 6, 9, 30), 24095.0, 23800.0, "CE", "NIFTY23800CE")
    trade = pm.on_exit(265.0, datetime(2025, 1, 6, 9, 35), 24100.0, ExitReason.PREMIUM_TRAIL_SL)

    assert trade.re_entry_number == 1
    assert pm.entries_for_side(Side.CALL) == 2


def test_reset_for_new_day():
    pm = PositionManager(lot_size=25, lots=1)
    pm.on_breakout(_breakout())
    pm.on_entry(Side.CALL, 260.0, datetime(2025, 1, 6, 9, 21), 24095.0, 23800.0, "CE", "NIFTY23800CE")
    pm.on_exit(270.0, datetime(2025, 1, 6, 9, 30), 24100.0, ExitReason.CANDLE_SL)

    pm.reset_for_new_day()
    assert pm.is_idle
    assert pm.entries_for_side(Side.CALL) == 0
    assert pm.entries_for_side(Side.PUT) == 0
