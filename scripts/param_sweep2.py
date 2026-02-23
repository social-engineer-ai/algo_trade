#!/usr/bin/env python3
"""Focused parameter sweep around the best settings found in sweep 1."""
import sys, os, logging, copy
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING)

from orb.config import load_config, TrailingStep
from orb.data.db import Database
from orb.data.instruments import InstrumentResolver
from orb.backtest.engine import BacktestEngine
from orb.backtest.results import BacktestResult
from orb.reports.metrics import compute_metrics, format_metrics
from orb.reports.trade_log import export_trades_csv
from orb.reports.charts import plot_equity_curve, plot_daily_pnl
from orb.models import Candle


def load_candles_from_db(db, token, from_dt, to_dt):
    raw = db.get_candles(token, from_dt, to_dt, 'minute')
    return [Candle(
        timestamp=datetime.fromisoformat(r['timestamp']),
        open=r['open'], high=r['high'], low=r['low'], close=r['close'],
        volume=r.get('volume', 0),
    ) for r in raw]


def run_backtest_with_config(config, db, resolver):
    engine = BacktestEngine(config)
    nifty_token = resolver.get_nifty_spot_token()

    with db._connect() as conn:
        rows = conn.execute("""
            SELECT DISTINCT substr(timestamp, 1, 10) as dt
            FROM candles WHERE instrument_token = 256265 ORDER BY dt
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
    ladder = []
    for i in range(5):
        trigger = t1 + i * step
        trail_to = t1 + (i - 1) * step if i > 0 else 0
        if i == 4:
            trail_to = -1
        ladder.append(TrailingStep(trigger=trigger, trail_to=trail_to))
    return ladder


def main():
    base_config = load_config('config/default_config.yaml')
    db = Database('data/orb_data.db')
    resolver = InstrumentResolver(db)

    # Focused grid: RSI off, re-entry 0 or 1, various ST and ladder combos
    combos = []

    for re in [0, 1]:
        for st_period, st_mult in [(10, 3.0), (14, 3.0), (14, 2.5), (20, 3.0), (20, 2.5)]:
            for t1, step in [(30, 30), (40, 40), (50, 50), (40, 30), (50, 30)]:
                label = f"re{re}_ST{st_period}_{st_mult}_L{t1}s{step}"
                combos.append({
                    "re": re, "st_p": st_period, "st_m": st_mult,
                    "t1": t1, "step": step, "label": label,
                })

    print(f"Running {len(combos)} configurations...\n")
    print("=" * 130)
    print(f"{'Config':<35s} {'Trades':>6s} {'Wins':>5s} {'WR%':>6s} {'NetPnL':>10s} {'AvgWin':>8s} {'AvgLoss':>8s} {'R:R':>6s} {'PF':>6s} {'MaxDD':>10s} {'Sharpe':>7s}")
    print("=" * 130)

    all_results = []

    for c in combos:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.rsi_entry_min = 0
        cfg.strategy.rsi_entry_max = 100
        cfg.strategy.max_re_entries_per_side = c["re"]
        cfg.strategy.supertrend_period = c["st_p"]
        cfg.strategy.supertrend_multiplier = c["st_m"]
        cfg.strategy.trailing_ladder = make_ladder(c["t1"], c["step"])

        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        all_results.append((c["label"], m, c))

        if m.total_trades > 0:
            print(f"{c['label']:<35s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} {m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    print("=" * 130)

    # Sort by net P&L and show top 10
    valid = [(n, m, c) for n, m, c in all_results if m.total_trades >= 5]
    valid.sort(key=lambda x: x[1].net_pnl, reverse=True)

    print(f"\n=== TOP 10 by Net P&L (>= 5 trades) ===\n")
    print(f"{'Rank':<5s} {'Config':<35s} {'Trades':>6s} {'Wins':>5s} {'WR%':>6s} {'NetPnL':>10s} {'PF':>6s} {'MaxDD':>10s} {'Sharpe':>7s}")
    print("-" * 100)
    for i, (n, m, c) in enumerate(valid[:10], 1):
        print(f"{i:<5d} {n:<35s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    # Sort by Sharpe and show top 10
    valid.sort(key=lambda x: x[1].sharpe_ratio, reverse=True)
    print(f"\n=== TOP 10 by Sharpe Ratio (>= 5 trades) ===\n")
    print(f"{'Rank':<5s} {'Config':<35s} {'Trades':>6s} {'Wins':>5s} {'WR%':>6s} {'NetPnL':>10s} {'PF':>6s} {'MaxDD':>10s} {'Sharpe':>7s}")
    print("-" * 100)
    for i, (n, m, c) in enumerate(valid[:10], 1):
        print(f"{i:<5d} {n:<35s} {m.total_trades:>6d} {m.winning_trades:>5d} {m.win_rate:>5.1%} {m.net_pnl:>+10.0f} {m.profit_factor:>6.2f} {m.max_drawdown:>10.0f} {m.sharpe_ratio:>+7.2f}")

    # BEST overall: generate detailed report
    best = valid[0]  # By Sharpe
    print(f"\n{'='*60}")
    print(f"BEST CONFIG: {best[0]}")
    print(f"  Parameters: {best[2]}")
    print(f"{'='*60}")

    # Re-run best config with full output
    cfg = copy.deepcopy(base_config)
    cfg.strategy.rsi_entry_min = 0
    cfg.strategy.rsi_entry_max = 100
    cfg.strategy.max_re_entries_per_side = best[2]["re"]
    cfg.strategy.supertrend_period = best[2]["st_p"]
    cfg.strategy.supertrend_multiplier = best[2]["st_m"]
    cfg.strategy.trailing_ladder = make_ladder(best[2]["t1"], best[2]["step"])

    bt = run_backtest_with_config(cfg, db, resolver)
    m = compute_metrics(bt)
    print(format_metrics(m))

    os.makedirs('output', exist_ok=True)
    export_trades_csv(bt.all_trades, 'output/trade_log_tuned.csv')
    if bt.total_trades > 0:
        plot_equity_curve(bt, 'output/equity_curve_tuned.png')
        plot_daily_pnl(bt, 'output/daily_pnl_tuned.png')
        print("Saved: output/trade_log_tuned.csv, equity_curve_tuned.png, daily_pnl_tuned.png")

    db.close()


if __name__ == "__main__":
    main()
