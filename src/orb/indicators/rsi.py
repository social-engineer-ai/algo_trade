"""Incremental RSI(14) using Wilder's smoothing method."""

from __future__ import annotations


class RSI:
    """Relative Strength Index calculated incrementally using Wilder's smoothing.

    Feed one candle close at a time via `update()`.  The first valid RSI value
    is returned after `period + 1` closes have been supplied (the first close
    establishes the baseline, then `period` changes are needed for the initial
    SMA of gains and losses).

    Parameters
    ----------
    period : int
        Look-back period (default 14).
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError(f"period must be >= 1, got {period}")
        self.period = period
        self._alpha: float = 1.0 / period
        self.reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def value(self) -> float | None:
        """Return the last computed RSI value, or None if not yet warmed up."""
        if self._avg_gain is None or self._avg_loss is None:
            return None
        return self._compute_rsi()

    def update(self, close: float) -> float | None:
        """Ingest a new closing price and return the current RSI value.

        Returns ``None`` until *period + 1* closes have been received (we need
        *period* price changes to compute the seed SMA).
        """
        self._count += 1

        # First close — nothing to compute yet.
        if self._prev_close is None:
            self._prev_close = close
            return None

        # Compute the gain / loss for this bar.
        change = close - self._prev_close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self._prev_close = close

        # Accumulation phase — collecting initial gains / losses for the SMA.
        if self._avg_gain is None:
            self._gain_buf.append(gain)
            self._loss_buf.append(loss)

            if len(self._gain_buf) < self.period:
                return None

            # Seed the smoothed averages with a simple mean.
            self._avg_gain = sum(self._gain_buf) / self.period
            self._avg_loss = sum(self._loss_buf) / self.period

            # Free the buffers — no longer needed.
            self._gain_buf = []
            self._loss_buf = []
        else:
            # Wilder's exponential smoothing:
            #   avg = prev_avg * (1 - alpha) + current_value * alpha
            # where alpha = 1 / period.
            self._avg_gain = self._avg_gain * (1.0 - self._alpha) + gain * self._alpha
            self._avg_loss = self._avg_loss * (1.0 - self._alpha) + loss * self._alpha

        return self._compute_rsi()

    def reset(self) -> None:
        """Clear all internal state so the indicator can be reused."""
        self._prev_close: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._count: int = 0
        # Temporary buffers used only during the seed (SMA) phase.
        self._gain_buf: list[float] = []
        self._loss_buf: list[float] = []

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_rsi(self) -> float:
        assert self._avg_gain is not None and self._avg_loss is not None
        if self._avg_loss == 0.0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
