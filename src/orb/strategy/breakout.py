"""Breakout detection → H1/L1 structure variables."""
from __future__ import annotations

from orb.models import BreakoutInfo, Candle, Side


class BreakoutDetector:
    """Detects when price closes above H3 or below L3, establishing H1/L1 levels.

    After breakout is confirmed:
    - CALL breakout: H1 = breakout candle high, L1 = pre-breakout candle low
    - PUT breakout:  L1 = breakout candle low,  H1 = pre-breakout candle high
    """

    def __init__(self, h3: float, l3: float):
        self._h3 = h3
        self._l3 = l3
        self._prev_candle: Candle | None = None
        self._breakout: BreakoutInfo | None = None
        self._candle_idx: int = 0

    @property
    def breakout(self) -> BreakoutInfo | None:
        return self._breakout

    @property
    def is_confirmed(self) -> bool:
        return self._breakout is not None

    def update(self, candle: Candle) -> BreakoutInfo | None:
        """Feed a 1-min candle (post-ORB). Returns BreakoutInfo on first confirmation.

        Once a breakout is confirmed, subsequent candles are ignored (breakout
        is a one-time event per session — the same H1/L1 persist for re-entries).
        """
        if self._breakout is not None:
            return None  # Already confirmed

        self._candle_idx += 1

        if candle.close > self._h3:
            # CALL breakout
            pre_candle_low = self._prev_candle.low if self._prev_candle else self._l3
            self._breakout = BreakoutInfo(
                side=Side.CALL,
                breakout_candle_idx=self._candle_idx,
                h3=self._h3,
                l3=self._l3,
                h1=candle.high,
                l1=pre_candle_low,
                confirmed_at=candle.timestamp,
            )
            return self._breakout

        if candle.close < self._l3:
            # PUT breakout
            pre_candle_high = self._prev_candle.high if self._prev_candle else self._h3
            self._breakout = BreakoutInfo(
                side=Side.PUT,
                breakout_candle_idx=self._candle_idx,
                h3=self._h3,
                l3=self._l3,
                h1=pre_candle_high,
                l1=candle.low,
                confirmed_at=candle.timestamp,
            )
            return self._breakout

        self._prev_candle = candle
        return None
