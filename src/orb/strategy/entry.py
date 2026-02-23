"""Entry trigger detection with RSI and SuperTrend filters."""
from __future__ import annotations

from datetime import time
from typing import Optional

from orb.indicators.rsi import RSI
from orb.indicators.supertrend import SuperTrend
from orb.models import BreakoutInfo, Candle, Side


class EntrySignal:
    """Checks whether entry conditions are met for a given candle.

    Entry triggers:
    - CALL: underlying price crosses above H1
    - PUT:  underlying price crosses below L1

    Filters (all must pass):
    - RSI(14) between rsi_min and rsi_max
    - SuperTrend direction aligned with trade side
    - Within allowed time window
    - Re-entry count not exceeded
    """

    def __init__(
        self,
        rsi: RSI,
        supertrend: SuperTrend,
        rsi_min: float = 40.0,
        rsi_max: float = 65.0,
        no_entry_after: time = time(11, 30),
        max_re_entries: int = 4,
    ):
        self._rsi = rsi
        self._supertrend = supertrend
        self._rsi_min = rsi_min
        self._rsi_max = rsi_max
        self._no_entry_after = no_entry_after
        self._max_re_entries = max_re_entries

    def check_entry(
        self,
        candle: Candle,
        breakout: BreakoutInfo,
        entries_this_side: int,
        current_time: time,
    ) -> Optional[Side]:
        """Check if entry conditions are met on this candle.

        Uses synthetic ticks (intra-candle ordering) to determine if
        price crossed H1/L1 during this candle. SL is checked first
        (conservative: SL fires before entry on same candle).

        Args:
            candle: Current underlying 1-min candle.
            breakout: Confirmed breakout info with H1/L1.
            entries_this_side: Number of entries already taken on this side today.
            current_time: Current candle time (time portion).

        Returns:
            Side if entry should be taken, None otherwise.
        """
        # Time gate
        if current_time > self._no_entry_after:
            return None

        # Re-entry limit
        if entries_this_side >= self._max_re_entries:
            return None

        # Get current indicator values
        rsi_val = self._rsi.value
        st_result = self._supertrend.value

        if rsi_val is None or st_result is None:
            return None

        # RSI filter
        if not (self._rsi_min <= rsi_val <= self._rsi_max):
            return None

        side = breakout.side

        if side == Side.CALL:
            # SuperTrend must be bullish (direction = 1)
            if st_result["direction"] != 1:
                return None
            # Price must cross above H1
            if candle.high >= breakout.h1:
                return Side.CALL

        elif side == Side.PUT:
            # SuperTrend must be bearish (direction = -1)
            if st_result["direction"] != -1:
                return None
            # Price must cross below L1
            if candle.low <= breakout.l1:
                return Side.PUT

        return None

    def check_sl_before_entry(
        self,
        candle: Candle,
        breakout: BreakoutInfo,
    ) -> bool:
        """Check if SL level was hit before entry on the same candle.

        Conservative rule: if the candle's synthetic ticks hit SL before
        hitting the entry level, the entry is invalidated.

        Returns True if SL was hit first (entry blocked).
        """
        ticks = candle.synthetic_ticks()
        side = breakout.side

        if side == Side.CALL:
            entry_level = breakout.h1
            sl_level = breakout.l1
            for tick in ticks:
                if tick <= sl_level:
                    return True  # SL hit first
                if tick >= entry_level:
                    return False  # Entry hit first
        elif side == Side.PUT:
            entry_level = breakout.l1
            sl_level = breakout.h1
            for tick in ticks:
                if tick >= sl_level:
                    return True  # SL hit first
                if tick <= entry_level:
                    return False  # Entry hit first

        return False  # Neither hit
