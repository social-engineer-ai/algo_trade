"""Comprehensive unit tests for RSI and SuperTrend indicators."""

from __future__ import annotations

import math
import sys
import os

import pytest

# ---------------------------------------------------------------------------
# Ensure the src directory is on the import path so that ``orb`` resolves
# regardless of how pytest is invoked.
# ---------------------------------------------------------------------------
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), os.pardir, "src"),
)

from orb.indicators.rsi import RSI
from orb.indicators.supertrend import SuperTrend


# ===================================================================
# Helpers
# ===================================================================

def _feed_rsi(rsi: RSI, closes: list[float]) -> list[float | None]:
    """Feed a series of closes and collect every return value."""
    return [rsi.update(c) for c in closes]


def _feed_supertrend(
    st: SuperTrend,
    candles: list[tuple[float, float, float]],
) -> list[dict | None]:
    """Feed (high, low, close) candles and collect every return value."""
    return [st.update(h, l, c) for h, l, c in candles]


# ===================================================================
# RSI tests
# ===================================================================

class TestRSIWarmup:
    """RSI must return None until period+1 closes have been fed."""

    def test_returns_none_during_warmup(self):
        rsi = RSI(period=14)
        # Feed exactly 14 closes — not enough (need 15).
        results = _feed_rsi(rsi, list(range(1, 15)))
        assert all(r is None for r in results)

    def test_first_value_at_period_plus_one(self):
        rsi = RSI(period=14)
        results = _feed_rsi(rsi, list(range(1, 16)))  # 15 closes
        assert results[-1] is not None

    def test_short_period_warmup(self):
        rsi = RSI(period=2)
        r1 = rsi.update(10.0)
        r2 = rsi.update(11.0)
        assert r1 is None
        assert r2 is None
        r3 = rsi.update(12.0)
        assert r3 is not None


class TestRSIKnownValues:
    """Verify RSI against hand-computed / well-known sequences."""

    def test_monotonic_increase_gives_high_rsi(self):
        """Steadily increasing prices should yield RSI = 100."""
        rsi = RSI(period=5)
        closes = [float(i) for i in range(1, 20)]
        results = _feed_rsi(rsi, closes)
        valid = [r for r in results if r is not None]
        # Every gain, no losses — RSI must be 100.
        for v in valid:
            assert v == pytest.approx(100.0)

    def test_monotonic_decrease_gives_low_rsi(self):
        """Steadily decreasing prices should yield RSI = 0."""
        rsi = RSI(period=5)
        closes = [float(100 - i) for i in range(20)]
        results = _feed_rsi(rsi, closes)
        valid = [r for r in results if r is not None]
        for v in valid:
            assert v == pytest.approx(0.0)

    def test_flat_prices_give_rsi_50_or_none(self):
        """When price does not change, avg_gain == avg_loss == 0.

        With both at zero, avg_loss is 0 so the code returns 100 (no losses).
        However, if we alternate +1 / -1 symmetrically the RSI should converge
        toward 50.
        """
        rsi = RSI(period=4)
        # Alternate gains and losses of equal magnitude.
        closes = [100.0]
        for i in range(30):
            closes.append(closes[-1] + (1.0 if i % 2 == 0 else -1.0))
        results = _feed_rsi(rsi, closes)
        valid = [r for r in results if r is not None]
        # After enough bars the RSI should hover in the vicinity of 50.
        # With Wilder's smoothing and an alternating +1/-1 pattern starting
        # with a gain, the seed window may not be perfectly symmetric, so we
        # allow a wider tolerance.
        assert valid[-1] == pytest.approx(50.0, abs=10.0)

    def test_known_14_period_sequence(self):
        """Hand-verified RSI-14 using Wilder's method on a specific sequence.

        Prices: 44,44.34,44.09,43.61,44.33,44.83,45.10,45.42,45.84,
                46.08,45.89,46.03,45.61,46.28,46.28,46.00,46.03,46.41,
                46.22,45.64
        (Classic Wilder textbook example — first RSI should be ~70.46)
        """
        closes = [
            44.00, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
        ]
        rsi = RSI(period=14)
        results = _feed_rsi(rsi, closes)
        first_rsi = results[-1]
        assert first_rsi is not None
        # With the gains/losses from these 14 changes:
        # gains:  0, 0, 0.72, 0.50, 0.27, 0.32, 0.42, 0.24, 0, 0.14, 0, 0.67, 0
        # losses: 0.34, 0.25, 0.48, 0, 0, 0, 0, 0, 0.19, 0, 0.42, 0, 0
        # avg_gain = sum(gains)/14, avg_loss = sum(losses)/14
        # The seed RSI computes to ~72.98.
        assert first_rsi == pytest.approx(72.98, abs=0.5)

    def test_period_1(self):
        """With period=1, RSI should be 100 on gain, 0 on loss."""
        rsi = RSI(period=1)
        assert rsi.update(10.0) is None
        assert rsi.update(12.0) == pytest.approx(100.0)
        assert rsi.update(11.0) == pytest.approx(0.0)


class TestRSIReset:
    def test_reset_clears_state(self):
        rsi = RSI(period=5)
        _feed_rsi(rsi, [float(i) for i in range(1, 10)])
        rsi.reset()
        # After reset, first update should return None again.
        assert rsi.update(50.0) is None

    def test_reset_produces_identical_results(self):
        closes = [10, 11, 9, 12, 8, 13, 7, 14, 6, 15]
        rsi = RSI(period=3)
        first_run = _feed_rsi(rsi, closes)
        rsi.reset()
        second_run = _feed_rsi(rsi, closes)
        for a, b in zip(first_run, second_run):
            if a is None:
                assert b is None
            else:
                assert a == pytest.approx(b)


class TestRSIEdgeCases:
    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            RSI(period=0)

    def test_all_same_price(self):
        """No change at all — avg_gain and avg_loss both 0 => RSI = 100."""
        rsi = RSI(period=3)
        closes = [50.0] * 10
        results = _feed_rsi(rsi, closes)
        valid = [r for r in results if r is not None]
        for v in valid:
            assert v == pytest.approx(100.0)


# ===================================================================
# SuperTrend tests
# ===================================================================

def _make_candles_from_closes(
    closes: list[float],
    spread: float = 1.0,
) -> list[tuple[float, float, float]]:
    """Create (high, low, close) tuples by adding a fixed spread."""
    return [(c + spread, c - spread, c) for c in closes]


class TestSuperTrendWarmup:
    """SuperTrend must return None until ATR is ready."""

    def test_returns_none_during_warmup(self):
        st = SuperTrend(period=10, multiplier=3.0)
        # Need 1 bar for prev_close + 10 bars for ATR seed = 11 bars.
        candles = _make_candles_from_closes([100.0] * 10)
        results = _feed_supertrend(st, candles)
        assert all(r is None for r in results)

    def test_first_value_at_correct_bar(self):
        st = SuperTrend(period=3, multiplier=1.0)
        # 1 bar baseline + 3 bars for ATR = 4 bars needed.
        candles = _make_candles_from_closes([100.0, 101.0, 102.0, 103.0])
        results = _feed_supertrend(st, candles)
        assert results[0] is None
        assert results[1] is None
        assert results[2] is None
        assert results[3] is not None

    def test_result_keys(self):
        st = SuperTrend(period=2, multiplier=1.0)
        candles = _make_candles_from_closes([100.0, 101.0, 102.0])
        results = _feed_supertrend(st, candles)
        result = results[-1]
        assert result is not None
        assert "value" in result
        assert "direction" in result
        assert result["direction"] in (1, -1)


class TestSuperTrendDirection:
    """Verify that direction changes when price crosses bands."""

    def test_strong_uptrend_is_bullish(self):
        st = SuperTrend(period=3, multiplier=1.0)
        # Steadily rising prices.
        closes = [100 + i * 5 for i in range(20)]
        candles = _make_candles_from_closes(closes, spread=1.0)
        results = _feed_supertrend(st, candles)
        valid = [r for r in results if r is not None]
        # Should settle into bullish direction.
        assert valid[-1]["direction"] == 1

    def test_strong_downtrend_is_bearish(self):
        st = SuperTrend(period=3, multiplier=1.0)
        closes = [200 - i * 5 for i in range(20)]
        candles = _make_candles_from_closes(closes, spread=1.0)
        results = _feed_supertrend(st, candles)
        valid = [r for r in results if r is not None]
        assert valid[-1]["direction"] == -1

    def test_direction_flips_on_reversal(self):
        st = SuperTrend(period=3, multiplier=1.0)
        # Strong up, then strong down.
        up = [100 + i * 10 for i in range(15)]
        down = [up[-1] - i * 10 for i in range(1, 16)]
        closes = up + down
        candles = _make_candles_from_closes(closes, spread=1.0)
        results = _feed_supertrend(st, candles)
        valid = [r for r in results if r is not None]

        # During uptrend, direction should be 1.
        # After reversal, direction should eventually become -1.
        directions = [r["direction"] for r in valid]
        assert 1 in directions
        assert -1 in directions

    def test_flat_prices_maintain_direction(self):
        """Flat prices should not cause erratic direction changes."""
        st = SuperTrend(period=3, multiplier=2.0)
        closes = [100.0] * 20
        candles = [(101.0, 99.0, 100.0) for _ in closes]
        results = _feed_supertrend(st, candles)
        valid = [r for r in results if r is not None]
        # Direction should remain constant once established.
        directions = set(r["direction"] for r in valid)
        assert len(directions) == 1


class TestSuperTrendBands:
    """Verify band tightening logic."""

    def test_lower_band_tightens_in_uptrend(self):
        st = SuperTrend(period=3, multiplier=1.0)
        # Steadily rising — the lower band should ratchet up.
        closes = [100 + i * 2 for i in range(15)]
        candles = _make_candles_from_closes(closes, spread=0.5)
        results = _feed_supertrend(st, candles)
        valid = [r for r in results if r is not None and r["direction"] == 1]
        if len(valid) >= 2:
            values = [r["value"] for r in valid]
            # Lower band (value in uptrend) should be non-decreasing.
            for i in range(1, len(values)):
                assert values[i] >= values[i - 1] - 1e-9

    def test_upper_band_tightens_in_downtrend(self):
        st = SuperTrend(period=3, multiplier=1.0)
        closes = [200 - i * 2 for i in range(15)]
        candles = _make_candles_from_closes(closes, spread=0.5)
        results = _feed_supertrend(st, candles)
        valid = [r for r in results if r is not None and r["direction"] == -1]
        if len(valid) >= 2:
            values = [r["value"] for r in valid]
            # Upper band (value in downtrend) should be non-increasing.
            for i in range(1, len(values)):
                assert values[i] <= values[i - 1] + 1e-9


class TestSuperTrendReset:
    def test_reset_clears_state(self):
        st = SuperTrend(period=3, multiplier=1.0)
        candles = _make_candles_from_closes([100 + i for i in range(10)])
        _feed_supertrend(st, candles)
        st.reset()
        assert st.update(50.0, 49.0, 49.5) is None

    def test_reset_produces_identical_results(self):
        candles = _make_candles_from_closes(
            [100, 102, 98, 105, 95, 110, 90, 108, 92, 106],
            spread=1.0,
        )
        st = SuperTrend(period=3, multiplier=1.5)
        first_run = _feed_supertrend(st, candles)
        st.reset()
        second_run = _feed_supertrend(st, candles)
        for a, b in zip(first_run, second_run):
            if a is None:
                assert b is None
            else:
                assert a["value"] == pytest.approx(b["value"])
                assert a["direction"] == b["direction"]


class TestSuperTrendEdgeCases:
    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            SuperTrend(period=0)

    def test_single_candle_returns_none(self):
        st = SuperTrend(period=5)
        assert st.update(100.0, 99.0, 99.5) is None
