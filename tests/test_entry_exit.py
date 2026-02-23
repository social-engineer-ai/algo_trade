"""Tests for entry and exit logic."""
import pytest
from datetime import datetime, time
from orb.config import TrailingStep
from orb.indicators.rsi import RSI
from orb.indicators.supertrend import SuperTrend
from orb.models import BreakoutInfo, Candle, ExitReason, Side
from orb.strategy.entry import EntrySignal
from orb.strategy.exit import ExitManager


# --- Entry tests ---

class FakeRSI:
    """Stub RSI that returns a fixed value."""
    def __init__(self, val):
        self._val = val
    @property
    def value(self):
        return self._val
    def update(self, close):
        return self._val


class FakeSuperTrend:
    """Stub SuperTrend that returns a fixed direction."""
    def __init__(self, direction):
        self._dir = direction
    @property
    def value(self):
        return {"value": 24000.0, "direction": self._dir}
    def update(self, h, l, c):
        return self.value


def _breakout_info(side, h1, l1):
    return BreakoutInfo(
        side=side, breakout_candle_idx=1,
        h3=24070, l3=23980, h1=h1, l1=l1,
        confirmed_at=datetime(2025, 1, 6, 9, 19),
    )


def _candle(minute, o, h, l, c):
    return Candle(timestamp=datetime(2025, 1, 6, 9, minute), open=o, high=h, low=l, close=c)


def test_call_entry_triggers():
    entry = EntrySignal(
        rsi=FakeRSI(50), supertrend=FakeSuperTrend(1),
        rsi_min=40, rsi_max=65, no_entry_after=time(11, 30), max_re_entries=4,
    )
    breakout = _breakout_info(Side.CALL, h1=24090, l1=24030)
    candle = _candle(20, 24080, 24095, 24075, 24090)  # High >= H1

    result = entry.check_entry(candle, breakout, entries_this_side=0, current_time=time(9, 20))
    assert result == Side.CALL


def test_call_entry_blocked_by_rsi():
    entry = EntrySignal(
        rsi=FakeRSI(70), supertrend=FakeSuperTrend(1),  # RSI too high
        rsi_min=40, rsi_max=65, no_entry_after=time(11, 30), max_re_entries=4,
    )
    breakout = _breakout_info(Side.CALL, h1=24090, l1=24030)
    candle = _candle(20, 24080, 24095, 24075, 24090)

    result = entry.check_entry(candle, breakout, entries_this_side=0, current_time=time(9, 20))
    assert result is None


def test_call_entry_blocked_by_supertrend():
    entry = EntrySignal(
        rsi=FakeRSI(50), supertrend=FakeSuperTrend(-1),  # Bearish SuperTrend
        rsi_min=40, rsi_max=65, no_entry_after=time(11, 30), max_re_entries=4,
    )
    breakout = _breakout_info(Side.CALL, h1=24090, l1=24030)
    candle = _candle(20, 24080, 24095, 24075, 24090)

    result = entry.check_entry(candle, breakout, entries_this_side=0, current_time=time(9, 20))
    assert result is None


def test_entry_blocked_after_cutoff():
    entry = EntrySignal(
        rsi=FakeRSI(50), supertrend=FakeSuperTrend(1),
        rsi_min=40, rsi_max=65, no_entry_after=time(11, 30), max_re_entries=4,
    )
    breakout = _breakout_info(Side.CALL, h1=24090, l1=24030)
    candle = _candle(20, 24080, 24095, 24075, 24090)

    result = entry.check_entry(candle, breakout, entries_this_side=0, current_time=time(11, 31))
    assert result is None


def test_entry_blocked_by_re_entry_limit():
    entry = EntrySignal(
        rsi=FakeRSI(50), supertrend=FakeSuperTrend(1),
        rsi_min=40, rsi_max=65, no_entry_after=time(11, 30), max_re_entries=4,
    )
    breakout = _breakout_info(Side.CALL, h1=24090, l1=24030)
    candle = _candle(20, 24080, 24095, 24075, 24090)

    # max_re_entries=4 means first entry + 4 re-entries = 5 total allowed
    # entries_this_side=5 means 5 already taken, so next is blocked
    result = entry.check_entry(candle, breakout, entries_this_side=5, current_time=time(9, 20))
    assert result is None


# --- Exit tests ---

def _default_ladder():
    return [
        TrailingStep(trigger=30, trail_to=0),
        TrailingStep(trigger=60, trail_to=30),
        TrailingStep(trigger=90, trail_to=60),
        TrailingStep(trigger=120, trail_to=90),
        TrailingStep(trigger=150, trail_to=-1),
    ]


def test_regime_a_sl_hit():
    mgr = ExitManager(_default_ladder())
    candle = _candle(25, 24050, 24060, 24025, 24030)  # Low <= L1

    signal, regime, sl, gain, idx = mgr.check_exit(
        underlying_candle=candle, option_premium=250.0,
        side=Side.CALL, h1=24090, l1=24030,
        entry_premium=260.0, current_regime="A",
        premium_sl=None, highest_gain=0.0, last_ladder_idx=-1,
    )
    assert signal is not None
    assert signal.reason == ExitReason.CANDLE_SL


def test_regime_b_transition_on_t1():
    mgr = ExitManager(_default_ladder())
    candle = _candle(25, 24080, 24100, 24075, 24090)  # No SL hit

    signal, regime, sl, gain, idx = mgr.check_exit(
        underlying_candle=candle, option_premium=290.0,  # +30 gain
        side=Side.CALL, h1=24090, l1=24030,
        entry_premium=260.0, current_regime="A",
        premium_sl=None, highest_gain=0.0, last_ladder_idx=-1,
    )
    assert signal is None
    assert regime == "B"
    assert sl == 260.0  # Trail to cost (entry + 0)
    assert idx == 0


def test_premium_trailing_sl_hit():
    mgr = ExitManager(_default_ladder())
    candle = _candle(30, 24060, 24065, 24050, 24055)

    signal, regime, sl, gain, idx = mgr.check_exit(
        underlying_candle=candle, option_premium=255.0,  # Below SL of 260
        side=Side.CALL, h1=24090, l1=24030,
        entry_premium=260.0, current_regime="B",
        premium_sl=260.0, highest_gain=30.0, last_ladder_idx=0,
    )
    assert signal is not None
    assert signal.reason == ExitReason.PREMIUM_TRAIL_SL


def test_t5_full_exit():
    mgr = ExitManager(_default_ladder())
    candle = _candle(35, 24200, 24210, 24190, 24200)

    signal, regime, sl, gain, idx = mgr.check_exit(
        underlying_candle=candle, option_premium=420.0,  # +160 gain, past T5
        side=Side.CALL, h1=24090, l1=24030,
        entry_premium=260.0, current_regime="B",
        premium_sl=350.0, highest_gain=140.0, last_ladder_idx=3,
    )
    assert signal is not None
    assert signal.reason == ExitReason.PREMIUM_TARGET


def test_force_exit():
    mgr = ExitManager(_default_ladder())
    signal = mgr.check_force_exit(option_premium=270.0)
    assert signal.reason == ExitReason.FORCE_EXIT
    assert signal.exit_premium == 270.0
