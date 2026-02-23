"""Tests for breakout detection."""
import pytest
from datetime import datetime
from orb.models import Candle, Side
from orb.strategy.breakout import BreakoutDetector


def _candle(minute, o, h, l, c):
    return Candle(
        timestamp=datetime(2025, 1, 6, 9, minute),
        open=o, high=h, low=l, close=c,
    )


def test_call_breakout():
    """Close above H3 triggers CALL breakout."""
    det = BreakoutDetector(h3=24070, l3=23980)

    # First candle: no breakout (close within range)
    result = det.update(_candle(18, 24050, 24065, 24030, 24060))
    assert result is None

    # Second candle: close above H3
    result = det.update(_candle(19, 24060, 24090, 24055, 24080))
    assert result is not None
    assert result.side == Side.CALL
    assert result.h1 == 24090  # Breakout candle high
    assert result.l1 == 24030  # Pre-breakout candle low


def test_put_breakout():
    """Close below L3 triggers PUT breakout."""
    det = BreakoutDetector(h3=24070, l3=23980)

    # First candle: no breakout
    result = det.update(_candle(18, 24000, 24010, 23990, 23990))
    assert result is None

    # Second candle: close below L3
    result = det.update(_candle(19, 23985, 23990, 23960, 23970))
    assert result is not None
    assert result.side == Side.PUT
    assert result.l1 == 23960  # Breakout candle low
    assert result.h1 == 24010  # Pre-breakout candle high


def test_breakout_only_fires_once():
    det = BreakoutDetector(h3=24070, l3=23980)
    det.update(_candle(18, 24060, 24090, 24055, 24080))  # CALL breakout

    # Subsequent candles return None
    result = det.update(_candle(19, 24080, 24120, 24070, 24100))
    assert result is None


def test_no_breakout_within_range():
    det = BreakoutDetector(h3=24070, l3=23980)
    for m in range(18, 25):
        result = det.update(_candle(m, 24000, 24050, 23990, 24020))
        assert result is None
