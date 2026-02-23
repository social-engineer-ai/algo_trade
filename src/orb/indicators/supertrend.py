"""Incremental SuperTrend(10,3) with ATR."""

from __future__ import annotations


class SuperTrend:
    """Incremental SuperTrend indicator.

    Feed one candle (high, low, close) at a time via `update()`.  The first
    valid result is returned once enough bars have been received to compute the
    Wilder-smoothed ATR (i.e. after ``period`` complete candles, since the very
    first candle has no True Range).

    Parameters
    ----------
    period : int
        ATR look-back period (default 10).
    multiplier : float
        Band distance in ATR multiples (default 3.0).
    """

    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        if period < 1:
            raise ValueError(f"period must be >= 1, got {period}")
        self.period = period
        self.multiplier = multiplier
        self._alpha: float = 1.0 / period
        self.reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def value(self) -> dict | None:
        """Return the last computed SuperTrend result, or None if not yet warmed up."""
        if self._direction is None or self._final_lower is None or self._final_upper is None:
            return None
        v = self._final_lower if self._direction == 1 else self._final_upper
        return {"value": v, "direction": self._direction}

    def update(self, high: float, low: float, close: float) -> dict | None:
        """Ingest a new candle and return the SuperTrend value and direction.

        Returns ``None`` during the warm-up phase.  Once ready, returns::

            {"value": float, "direction": int}

        where *direction* is **1** for bullish (price above the lower band,
        uptrend) or **-1** for bearish (price below the upper band, downtrend).
        """
        self._count += 1

        # ----------------------------------------------------------
        # True Range calculation (needs a previous close)
        # ----------------------------------------------------------
        if self._prev_close is None:
            self._prev_close = close
            return None

        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

        # ----------------------------------------------------------
        # ATR: accumulate for SMA seed, then Wilder-smooth
        # ----------------------------------------------------------
        if self._atr is None:
            self._tr_buf.append(tr)
            if len(self._tr_buf) < self.period:
                self._prev_close = close
                return None

            # Seed ATR with simple mean of first `period` true ranges.
            self._atr = sum(self._tr_buf) / self.period
            self._tr_buf = []
        else:
            self._atr = self._atr * (1.0 - self._alpha) + tr * self._alpha

        # ----------------------------------------------------------
        # SuperTrend band logic
        # ----------------------------------------------------------
        hl2 = (high + low) / 2.0
        basic_upper = hl2 + self.multiplier * self._atr
        basic_lower = hl2 - self.multiplier * self._atr

        # Final upper band: tighten if possible.
        if self._final_upper is not None and self._prev_close <= self._final_upper:
            final_upper = min(basic_upper, self._final_upper)
        else:
            final_upper = basic_upper

        # Final lower band: tighten if possible.
        if self._final_lower is not None and self._prev_close >= self._final_lower:
            final_lower = max(basic_lower, self._final_lower)
        else:
            final_lower = basic_lower

        # ----------------------------------------------------------
        # Direction logic
        # ----------------------------------------------------------
        if self._direction is None:
            # Bootstrap: decide initial direction from close vs bands.
            direction = 1 if close > final_upper else -1
        else:
            prev_dir = self._direction
            if prev_dir == 1:
                # Was bullish — stays bullish unless close drops below lower band.
                direction = -1 if close < final_lower else 1
            else:
                # Was bearish — stays bearish unless close rises above upper band.
                direction = 1 if close > final_upper else -1

        # SuperTrend value is the active band.
        value = final_lower if direction == 1 else final_upper

        # ----------------------------------------------------------
        # Persist state for next bar
        # ----------------------------------------------------------
        self._final_upper = final_upper
        self._final_lower = final_lower
        self._direction = direction
        self._prev_close = close

        return {"value": value, "direction": direction}

    def reset(self) -> None:
        """Clear all internal state so the indicator can be reused."""
        self._prev_close: float | None = None
        self._atr: float | None = None
        self._count: int = 0
        self._tr_buf: list[float] = []
        self._final_upper: float | None = None
        self._final_lower: float | None = None
        self._direction: int | None = None
