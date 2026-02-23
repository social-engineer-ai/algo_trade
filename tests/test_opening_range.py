"""Tests for opening range detection."""
import pytest
from datetime import datetime
from orb.models import Candle
from orb.strategy.opening_range import OpeningRangeDetector


def _candle(ts_minute, o, h, l, c):
    return Candle(
        timestamp=datetime(2025, 1, 6, 9, ts_minute),
        open=o, high=h, low=l, close=c,
    )


def test_orb_completes_after_3_candles():
    det = OpeningRangeDetector(num_candles=3)
    assert not det.is_complete

    det.update(_candle(15, 24000, 24050, 23980, 24030))
    assert not det.is_complete

    det.update(_candle(16, 24030, 24060, 24010, 24040))
    assert not det.is_complete

    result = det.update(_candle(17, 24040, 24070, 24020, 24050))
    assert result is True
    assert det.is_complete
    assert det.h3 == 24070  # Max high
    assert det.l3 == 23980  # Min low


def test_orb_ignores_extra_candles():
    det = OpeningRangeDetector(num_candles=3)
    for m in range(15, 18):
        det.update(_candle(m, 24000, 24100, 23900, 24050))

    h3 = det.h3
    det.update(_candle(18, 24050, 25000, 23000, 24050))
    assert det.h3 == h3  # Unchanged


def test_orb_reset():
    det = OpeningRangeDetector(num_candles=3)
    for m in range(15, 18):
        det.update(_candle(m, 24000, 24100, 23900, 24050))
    assert det.is_complete

    det.reset()
    assert not det.is_complete
    assert det.h3 is None
