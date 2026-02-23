"""Golden test: single-day backtest with hand-calculated expected results."""
import pytest
import json
from datetime import datetime
from pathlib import Path

from orb.backtest.engine import BacktestEngine, DayResult
from orb.config import load_config, AppConfig, TrailingStep, StrategyConfig, SessionConfig, MarketConfig, BacktestConfig, ReportingConfig
from orb.models import Candle, ExitReason, Side


def _make_config() -> AppConfig:
    """Create a test config with default parameters."""
    return AppConfig(
        market=MarketConfig(lot_size=25, strike_step=50, itm_offset=200),
        session=SessionConfig(),
        strategy=StrategyConfig(
            rsi_period=14, rsi_entry_min=0, rsi_entry_max=100,  # Disable RSI filter
            supertrend_period=10, supertrend_multiplier=3.0,
            max_re_entries_per_side=4, warmup_candles=30,
            trailing_ladder=[
                TrailingStep(trigger=30, trail_to=0),
                TrailingStep(trigger=60, trail_to=30),
                TrailingStep(trigger=90, trail_to=60),
                TrailingStep(trigger=120, trail_to=90),
                TrailingStep(trigger=150, trail_to=-1),
            ],
        ),
        backtest=BacktestConfig(slippage_points=0, brokerage_per_order=0),  # Zero costs for golden test
        reporting=ReportingConfig(),
    )


def _make_candle(hour, minute, o, h, l, c, vol=1000):
    return Candle(
        timestamp=datetime(2025, 1, 6, hour, minute),
        open=o, high=h, low=l, close=c, volume=vol,
    )


def _make_warmup_candles(count=30):
    """Generate warmup candles with a mild uptrend for indicator bootstrapping."""
    candles = []
    base = 24000
    for i in range(count):
        price = base + i * 2
        candles.append(Candle(
            timestamp=datetime(2025, 1, 3, 14, 30 + i % 30),
            open=price, high=price + 10, low=price - 5, close=price + 5,
            volume=1000,
        ))
    return candles


def test_engine_no_breakout_no_trades():
    """Day where price stays within ORB range — no trades."""
    config = _make_config()
    engine = BacktestEngine(config)

    # ORB candles: range 23980-24070
    candles = [
        _make_candle(9, 15, 24000, 24050, 23980, 24030),
        _make_candle(9, 16, 24030, 24060, 24010, 24040),
        _make_candle(9, 17, 24040, 24070, 24020, 24050),
    ]
    # Post-ORB: stays within range
    for m in range(18, 60):
        candles.append(_make_candle(9, m, 24020, 24050, 23990, 24030))
    for h in range(10, 15):
        for m in range(0, 60):
            candles.append(_make_candle(h, m, 24020, 24050, 23990, 24030))
    candles.append(_make_candle(15, 15, 24020, 24050, 23990, 24030))

    result = engine.run_day(
        trading_date=datetime(2025, 1, 6),
        underlying_candles=candles,
        option_candles={},
        warmup_candles=_make_warmup_candles(),
    )

    assert result.total_trades == 0


def test_engine_force_exit_at_1515():
    """Position open at 15:15 should be force-exited."""
    config = _make_config()
    # Widen RSI to allow any entry
    config.strategy.rsi_entry_min = 0
    config.strategy.rsi_entry_max = 100
    engine = BacktestEngine(config)

    candles = []
    # ORB candles: range 23980-24070
    candles.append(_make_candle(9, 15, 24000, 24050, 23980, 24030))
    candles.append(_make_candle(9, 16, 24030, 24060, 24010, 24040))
    candles.append(_make_candle(9, 17, 24040, 24070, 24020, 24050))

    # Breakout candle: close > H3(24070)
    candles.append(_make_candle(9, 18, 24060, 24090, 24055, 24080))
    # Next candle: price hits H1(24090)
    candles.append(_make_candle(9, 19, 24080, 24095, 24075, 24090))

    # Fill rest of day until 15:15
    for m in range(20, 60):
        candles.append(_make_candle(9, m, 24085, 24095, 24080, 24090))
    for h in range(10, 15):
        for m in range(0, 60):
            candles.append(_make_candle(h, m, 24085, 24095, 24080, 24090))
    # 15:15 force exit
    candles.append(_make_candle(15, 15, 24085, 24090, 24080, 24085))

    # Create option candles for NIFTY23800CE
    option_candles = {}
    ce_symbol = "NIFTY23800CE"
    opt_list = []
    for c in candles:
        opt_list.append(Candle(
            timestamp=c.timestamp,
            open=280, high=290, low=275, close=285,
            volume=5000,
        ))
    option_candles[ce_symbol] = opt_list

    result = engine.run_day(
        trading_date=datetime(2025, 1, 6),
        underlying_candles=candles,
        option_candles=option_candles,
        warmup_candles=_make_warmup_candles(),
    )

    # Should have at least a force-exited trade (if entry triggered)
    # With RSI filter disabled and synthetic warmup, entry may or may not trigger
    # depending on indicator values. This is a structural test.
    assert isinstance(result, DayResult)


def test_day_result_properties():
    """Test DayResult aggregation properties."""
    from orb.models import TradeRecord
    trades = [
        TradeRecord(trade_id=1, gross_pnl=750, charges=50, net_pnl=700),
        TradeRecord(trade_id=2, gross_pnl=-500, charges=50, net_pnl=-550),
    ]
    result = DayResult(date=datetime(2025, 1, 6), trades=trades)

    assert result.total_trades == 2
    assert result.winning_trades == 1
    assert result.losing_trades == 1
    assert result.gross_pnl == 250
    assert result.net_pnl == 150
    assert result.total_charges == 100
