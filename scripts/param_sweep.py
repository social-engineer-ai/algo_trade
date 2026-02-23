#!/usr/bin/env python3
"""Parameter sweep to find optimal strategy settings."""
import sys, os, logging, copy, itertools
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING)

from orb.config import load_config, TrailingStep, AppConfig
from orb.data.db import Database
from orb.data.instruments import InstrumentResolver
from orb.backtest.engine import BacktestEngine
from orb.backtest.results import BacktestResult
from orb.reports.metrics import compute_metrics
from orb.models import Candle


def load_candles_from_db(db, token, from_dt, to_dt):
    raw = db.get_candles(token, from_dt, to_dt, 'minute')
    return [Candle(
        timestamp=datetime.fromisoformat(r['timestamp']),
        open=r['open'], high=r['high'], low=r['low'], close=r['close'],
        volume=r.get('volume', 0),
    ) for r in raw]


def run_backtest_with_config(config, db, resolver):
    """Run full backtest with given config, return BacktestResult."""
    engine = BacktestEngine(config)
    nifty_token = resolver.get_nifty_spot_token()

    with db._connect() as conn:
        rows = conn.execute("""
            SELECT DISTINCT substr(timestamp, 1, 10) as dt
            FROM candles WHERE instrument_token = 256265
            ORDER BY dt
        """).fetchall()
        trading_days = [date.fromisoformat(r['dt']) for r in rows]

    day_results = []
    prev_warmup = None

    for td in trading_days:
        day_from = f'{td} 09:15:00'
        day_to = f'{td} 15:30:00'

        underlying = load_candles_from_db(db, nifty_token, day_from, day_to)
        if not underlying:
            continue

        spot = underlying[0].open
        rounded = round(spot / 50) * 50
        call_strike = rounded - config.market.itm_offset
        put_strike = rounded + config.market.itm_offset
        expiry = resolver.get_nearest_expiry(td)

        option_candles = {}
        for strike, opt_type in [(call_strike, 'CE'), (put_strike, 'PE')]:
            token = resolver.get_option_token(strike, opt_type, expiry)
            if token:
                opt_list = load_candles_from_db(db, token, day_from, day_to)
                symbol = f'NIFTY{strike:.0f}{opt_type}'
                if opt_list:
                    option_candles[symbol] = opt_list

        result = engine.run_day(
            trading_date=datetime.combine(td, datetime.min.time()),
            underlying_candles=underlying,
            option_candles=option_candles,
            warmup_candles=prev_warmup,
        )
        day_results.append(result)
        prev_warmup = underlying[-config.strategy.warmup_candles:]

    return BacktestResult.from_day_results(day_results)


def make_ladder(t1, step):
    """Build trailing ladder: T1=t1, then every `step` points."""
    ladder = []
    for i in range(5):
        trigger = t1 + i * step
        if i < 4:
            trail_to = t1 + (i - 1) * step if i > 0 else 0
        else:
            trail_to = -1  # Full exit
        ladder.append(TrailingStep(trigger=trigger, trail_to=trail_to))
    return ladder


def main():
    base_config = load_config('config/default_config.yaml')
    db = Database('data/orb_data.db')
    resolver = InstrumentResolver(db)

    # Parameter grid
    param_sets = []

    rsi_ranges = [
        (0, 100, "RSI_off"),        # RSI disabled
        (30, 70, "RSI_30_70"),      # Wide
        (35, 65, "RSI_35_65"),      # Default-ish
        (40, 60, "RSI_40_60"),      # Tight
    ]

    max_reentries = [0, 1, 2, 4]

    supertrend_params = [
        (7, 2.0, "ST_7_2"),
        (10, 3.0, "ST_10_3"),       # Default
        (10, 2.0, "ST_10_2"),
        (14, 3.0, "ST_14_3"),
    ]

    trailing_ladders = [
        (20, 20, "Ladder_20_20"),   # Tighter: T1=+20, step=20
        (30, 30, "Ladder_30_30"),   # Default
        (40, 40, "Ladder_40_40"),   # Wider
        (50, 50, "Ladder_50_50"),   # Very wide
    ]

    # Generate combinations — keep it manageable
    # First pass: sweep one param at a time vs baseline
    print("=" * 120)
    print(f"{'Config':<40s} {'Trades':>6s} {'Wins':>5s} {'WR%':>6s} {'NetPnL':>10s} {'AvgWin':>8s} {'AvgLoss':>8s} {'R:R':>6s} {'PF':>6s} {'MaxDD':>10s} {'Sharpe':>7s}")
    print("=" * 120)

    results = []

    # 1. RSI sweep
    for rsi_min, rsi_max, label in rsi_ranges:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.rsi_entry_min = rsi_min
        cfg.strategy.rsi_entry_max = rsi_max
        name = f"rsi={label}"
        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        results.append((name, m))
        print(f"{name:<40s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} {m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    print("-" * 120)

    # 2. Re-entry sweep
    for re in max_reentries:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.max_re_entries_per_side = re
        name = f"reentry={re}"
        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        results.append((name, m))
        print(f"{name:<40s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} {m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    print("-" * 120)

    # 3. SuperTrend sweep
    for period, mult, label in supertrend_params:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.supertrend_period = period
        cfg.strategy.supertrend_multiplier = mult
        name = f"st={label}"
        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        results.append((name, m))
        print(f"{name:<40s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} {m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    print("-" * 120)

    # 4. Trailing ladder sweep
    for t1, step, label in trailing_ladders:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.trailing_ladder = make_ladder(t1, step)
        name = f"ladder={label}"
        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        results.append((name, m))
        print(f"{name:<40s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} {m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    print("=" * 120)

    # Now run top combinations
    print("\n\n=== TOP COMBINATIONS ===\n")
    print("=" * 120)
    print(f"{'Config':<55s} {'Trades':>6s} {'Wins':>5s} {'WR%':>6s} {'NetPnL':>10s} {'AvgWin':>8s} {'AvgLoss':>8s} {'R:R':>6s} {'PF':>6s} {'MaxDD':>10s} {'Sharpe':>7s}")
    print("=" * 120)

    combos = [
        # Based on sweep analysis, try promising combos
        {"rsi": (0, 100), "re": 0, "st": (10, 3.0), "ladder": (30, 30), "label": "RSI_off+re0"},
        {"rsi": (0, 100), "re": 1, "st": (10, 3.0), "ladder": (30, 30), "label": "RSI_off+re1"},
        {"rsi": (30, 70), "re": 0, "st": (10, 3.0), "ladder": (30, 30), "label": "RSI_wide+re0"},
        {"rsi": (30, 70), "re": 1, "st": (10, 3.0), "ladder": (30, 30), "label": "RSI_wide+re1"},
        {"rsi": (0, 100), "re": 0, "st": (7, 2.0), "ladder": (30, 30), "label": "RSI_off+re0+ST_7_2"},
        {"rsi": (0, 100), "re": 0, "st": (10, 2.0), "ladder": (30, 30), "label": "RSI_off+re0+ST_10_2"},
        {"rsi": (0, 100), "re": 0, "st": (14, 3.0), "ladder": (30, 30), "label": "RSI_off+re0+ST_14_3"},
        {"rsi": (0, 100), "re": 1, "st": (7, 2.0), "ladder": (20, 20), "label": "RSI_off+re1+ST_7_2+L20"},
        {"rsi": (0, 100), "re": 1, "st": (10, 2.0), "ladder": (20, 20), "label": "RSI_off+re1+ST_10_2+L20"},
        {"rsi": (30, 70), "re": 1, "st": (7, 2.0), "ladder": (20, 20), "label": "RSI_wide+re1+ST_7_2+L20"},
        {"rsi": (30, 70), "re": 0, "st": (7, 2.0), "ladder": (20, 20), "label": "RSI_wide+re0+ST_7_2+L20"},
        {"rsi": (30, 70), "re": 0, "st": (10, 2.0), "ladder": (20, 20), "label": "RSI_wide+re0+ST_10_2+L20"},
        {"rsi": (0, 100), "re": 0, "st": (10, 3.0), "ladder": (20, 20), "label": "RSI_off+re0+L20"},
        {"rsi": (0, 100), "re": 0, "st": (10, 3.0), "ladder": (50, 50), "label": "RSI_off+re0+L50"},
        {"rsi": (35, 65), "re": 0, "st": (10, 3.0), "ladder": (30, 30), "label": "RSI_tight+re0"},
        {"rsi": (35, 65), "re": 1, "st": (7, 2.0), "ladder": (30, 30), "label": "RSI_tight+re1+ST_7_2"},
    ]

    combo_results = []
    for c in combos:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.rsi_entry_min = c["rsi"][0]
        cfg.strategy.rsi_entry_max = c["rsi"][1]
        cfg.strategy.max_re_entries_per_side = c["re"]
        cfg.strategy.supertrend_period = c["st"][0]
        cfg.strategy.supertrend_multiplier = c["st"][1]
        cfg.strategy.trailing_ladder = make_ladder(c["ladder"][0], c["ladder"][1])
        name = c["label"]

        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        combo_results.append((name, m, c))
        print(f"{name:<55s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} {m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    print("=" * 120)

    # Find best by net P&L
    best = max(combo_results, key=lambda x: x[1].net_pnl)
    print(f"\nBest by Net P&L: {best[0]}")
    print(f"  Config: {best[2]}")

    # Find best by Sharpe
    best_sharpe = max(combo_results, key=lambda x: x[1].sharpe_ratio)
    print(f"\nBest by Sharpe: {best_sharpe[0]}")
    print(f"  Config: {best_sharpe[2]}")

    # Find best by profit factor (where trades > 5)
    valid = [r for r in combo_results if r[1].total_trades >= 5]
    if valid:
        best_pf = max(valid, key=lambda x: x[1].profit_factor)
        print(f"\nBest by Profit Factor (>=5 trades): {best_pf[0]}")
        print(f"  Config: {best_pf[2]}")

    db.close()


if __name__ == "__main__":
    main()
