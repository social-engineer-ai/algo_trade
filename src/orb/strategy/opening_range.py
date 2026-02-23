"""Opening Range detection from first 3 × 1-min candles (09:15–09:18)."""
from __future__ import annotations

from orb.models import Candle


class OpeningRangeDetector:
    """Accumulates the first N 1-min candles and computes H3/L3."""

    def __init__(self, num_candles: int = 3):
        self.num_candles = num_candles
        self._candles: list[Candle] = []
        self._h3: float | None = None
        self._l3: float | None = None

    @property
    def is_complete(self) -> bool:
        return len(self._candles) >= self.num_candles

    @property
    def h3(self) -> float | None:
        return self._h3

    @property
    def l3(self) -> float | None:
        return self._l3

    @property
    def candles(self) -> list[Candle]:
        return list(self._candles)

    def update(self, candle: Candle) -> bool:
        """Feed a 1-min candle. Returns True when the opening range is complete.

        Only the first `num_candles` candles are consumed; subsequent calls are no-ops.
        """
        if self.is_complete:
            return True

        self._candles.append(candle)

        if len(self._candles) == self.num_candles:
            self._h3 = max(c.high for c in self._candles)
            self._l3 = min(c.low for c in self._candles)
            return True

        return False

    def reset(self) -> None:
        self._candles.clear()
        self._h3 = None
        self._l3 = None
