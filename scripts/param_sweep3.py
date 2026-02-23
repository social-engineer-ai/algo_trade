#!/usr/bin/env python3
"""Comprehensive parameter sweep with corrected costs and new dimensions.

New dimensions vs param_sweep2:
- ITM offset: 100, 200, 300 (affects delta/premium)
- Entry cutoff: 11:00, 11:30, 12:00, 13:00
- ORB candles: 3, 5, 10
- Force exit time: 15:00, 15:15

Also tests two cost models:
- Zerodha (Rs 20/order)
- Zero brokerage (Wisdom Capital / ProStocks)

Corrected STT rate: 0.1% (sell-side) per NSE current rules.
Exchange txn: 0.03503% per NSE current rules.
"""
import sys, os, logging, copy, itertools
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING)

from orb.config import load_config, TrailingStep, SessionConfig
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


def make_ladder(t1, step):
    ladder = []
    for i in range(5):
        trigger = t1 + i * step
        trail_to = t1 + (i - 1) * step if i > 0 else 0
        if i == 4:
            trail_to = -1
        ladder.append(TrailingStep(trigger=trigger, trail_to=trail_to))
    return ladder


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
        rounded = round(spot / config.market.strike_step) * config.market.strike_step
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


def main():
    db = Database('data/orb_data.db')
    resolver = InstrumentResolver(db)

    # Corrected cost model (NSE current rates)
    CORRECT_STT = 0.001       # 0.1% on sell side (options)
    CORRECT_EXCHANGE = 0.0003503  # 0.03503% on both sides
    CORRECT_STAMP = 0.00003   # 0.003% on buy side
    CORRECT_SEBI = 0.000001   # Rs 10/crore

    # =========================================================
    # PHASE 1: Sweep new dimensions with corrected Zerodha costs
    # =========================================================
    combos = []

    # Fixed from sweep2: RSI off, re-entry 1, ST 14/3.0
    # New dimensions to sweep:
    for itm_offset in [100, 200, 300]:
        for cutoff_h, cutoff_m in [(11, 0), (11, 30), (12, 0), (13, 0)]:
            for orb_n in [3, 5, 10]:
                for force_h, force_m in [(15, 0), (15, 15)]:
                    for t1, step in [(30, 30), (40, 40), (50, 50)]:
                        label = (f"ITM{itm_offset}_cut{cutoff_h}:{cutoff_m:02d}"
                                 f"_orb{orb_n}_fx{force_h}:{force_m:02d}"
                                 f"_L{t1}s{step}")
                        combos.append({
                            "itm": itm_offset,
                            "cutoff": time(cutoff_h, cutoff_m),
                            "orb": orb_n,
                            "force": time(force_h, force_m),
                            "t1": t1, "step": step,
                            "label": label,
                        })

    print(f"Running {len(combos)} configurations with corrected Zerodha costs...\n")
    print("=" * 155)
    hdr = (f"{'Config':<50s} {'Trades':>6s} {'Wins':>5s} {'WR%':>6s} "
           f"{'GrossPnL':>10s} {'Charges':>8s} {'NetPnL':>10s} "
           f"{'AvgWin':>8s} {'AvgLoss':>8s} {'R:R':>6s} {'PF':>6s} "
           f"{'MaxDD':>8s} {'Sharpe':>7s}")
    print(hdr)
    print("=" * 155)

    all_results = []

    for i, c in enumerate(combos):
        cfg = load_config('config/default_config.yaml')

        # Corrected costs
        cfg.backtest.stt_rate = CORRECT_STT
        cfg.backtest.exchange_txn_charge = CORRECT_EXCHANGE
        cfg.backtest.stamp_duty = CORRECT_STAMP
        cfg.backtest.sebi_charges = CORRECT_SEBI

        # Fixed best params from sweep2
        cfg.strategy.rsi_entry_min = 0
        cfg.strategy.rsi_entry_max = 100
        cfg.strategy.max_re_entries_per_side = 1
        cfg.strategy.supertrend_period = 14
        cfg.strategy.supertrend_multiplier = 3.0

        # New sweep dimensions
        cfg.market.itm_offset = c["itm"]
        cfg.session.no_new_entry_after = c["cutoff"]
        cfg.session.orb_candles = c["orb"]
        # Adjust orb_end to match orb_candles
        orb_end_min = 15 + c["orb"]
        cfg.session.orb_end = time(9, orb_end_min)
        cfg.session.force_exit_time = c["force"]
        cfg.strategy.trailing_ladder = make_ladder(c["t1"], c["step"])

        bt = run_backtest_with_config(cfg, db, resolver)
        m = compute_metrics(bt)
        all_results.append((c["label"], m, c))

        if m.total_trades > 0:
            print(f"{c['label']:<50s} {m.total_trades:>6d} {m.winning_trades:>5d} "
                  f"{m.win_rate:>5.1%} {m.gross_pnl:>+10.0f} {m.total_charges:>8.0f} "
                  f"{m.net_pnl:>+10.0f} {m.avg_win:>+8.0f} {m.avg_loss:>+8.0f} "
                  f"{m.reward_to_risk:>6.2f} {m.profit_factor:>6.2f} "
                  f"{m.max_drawdown:>8.0f} {m.sharpe_ratio:>+7.2f}")
        else:
            print(f"{c['label']:<50s}      0 trades")

        if (i + 1) % 50 == 0:
            print(f"  ... {i+1}/{len(combos)} done")

    print("=" * 155)

    # Sort by net P&L
    valid = [(n, m, c) for n, m, c in all_results if m.total_trades >= 5]
    valid.sort(key=lambda x: x[1].net_pnl, reverse=True)

    print(f"\n=== TOP 15 by Net P&L (Zerodha costs, >= 5 trades) ===\n")
    print(f"{'Rank':<5s} {'Config':<50s} {'Trades':>6s} {'WR%':>6s} "
          f"{'Gross':>10s} {'Net':>10s} {'PF':>6s} {'R:R':>6s} {'Sharpe':>7s}")
    print("-" * 115)
    for i, (n, m, c) in enumerate(valid[:15], 1):
        print(f"{i:<5d} {n:<50s} {m.total_trades:>6d} {m.win_rate:>5.1%} "
              f"{m.gross_pnl:>+10.0f} {m.net_pnl:>+10.0f} {m.profit_factor:>6.2f} "
              f"{m.reward_to_risk:>6.2f} {m.sharpe_ratio:>+7.2f}")

    # =========================================================
    # PHASE 2: Re-run top 15 with zero brokerage (Wisdom Capital)
    # =========================================================
    print(f"\n\n{'='*80}")
    print("PHASE 2: Top 15 re-tested with ZERO BROKERAGE (Wisdom Capital / ProStocks)")
    print(f"{'='*80}\n")
    print(f"{'Rank':<5s} {'Config':<50s} {'Trades':>6s} {'WR%':>6s} "
          f"{'Gross':>10s} {'ZeroBrok':>10s} {'Zerodha':>10s} {'Diff':>8s}")
    print("-" * 115)

    for i, (n, m_zerodha, c) in enumerate(valid[:15], 1):
        cfg = load_config('config/default_config.yaml')
        cfg.backtest.stt_rate = CORRECT_STT
        cfg.backtest.exchange_txn_charge = CORRECT_EXCHANGE
        cfg.backtest.stamp_duty = CORRECT_STAMP
        cfg.backtest.sebi_charges = CORRECT_SEBI
        cfg.backtest.brokerage_per_order = 0  # Zero brokerage!

        cfg.strategy.rsi_entry_min = 0
        cfg.strategy.rsi_entry_max = 100
        cfg.strategy.max_re_entries_per_side = 1
        cfg.strategy.supertrend_period = 14
        cfg.strategy.supertrend_multiplier = 3.0

        cfg.market.itm_offset = c["itm"]
        cfg.session.no_new_entry_after = c["cutoff"]
        cfg.session.orb_candles = c["orb"]
        orb_end_min = 15 + c["orb"]
        cfg.session.orb_end = time(9, orb_end_min)
        cfg.session.force_exit_time = c["force"]
        cfg.strategy.trailing_ladder = make_ladder(c["t1"], c["step"])

        bt = run_backtest_with_config(cfg, db, resolver)
        m_zero = compute_metrics(bt)

        diff = m_zero.net_pnl - m_zerodha.net_pnl
        print(f"{i:<5d} {n:<50s} {m_zero.total_trades:>6d} {m_zero.win_rate:>5.1%} "
              f"{m_zero.gross_pnl:>+10.0f} {m_zero.net_pnl:>+10.0f} "
              f"{m_zerodha.net_pnl:>+10.0f} {diff:>+8.0f}")

    # =========================================================
    # PHASE 3: Summary insights
    # =========================================================
    print(f"\n\n{'='*80}")
    print("ANALYSIS: Impact of each dimension")
    print(f"{'='*80}")

    # Group by dimension and average net PnL
    from collections import defaultdict
    dims = {
        'itm': lambda c: c['itm'],
        'cutoff': lambda c: str(c['cutoff']),
        'orb': lambda c: c['orb'],
        'force': lambda c: str(c['force']),
        'ladder': lambda c: f"L{c['t1']}s{c['step']}",
    }

    for dim_name, key_fn in dims.items():
        groups = defaultdict(list)
        for n, m, c in all_results:
            if m.total_trades >= 3:
                groups[key_fn(c)].append(m.net_pnl)

        print(f"\n  {dim_name.upper()}:")
        for val in sorted(groups.keys()):
            vals = groups[val]
            avg = sum(vals) / len(vals)
            print(f"    {str(val):>12s}: avg Net = {avg:>+8.0f} ({len(vals)} configs)")

    db.close()


if __name__ == "__main__":
    main()
