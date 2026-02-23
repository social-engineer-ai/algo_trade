#!/usr/bin/env python3
"""Export comparison data for HTML: Original vs Tuned (10-candle ORB)."""
import sys, os, json, copy
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.WARNING)

from orb.config import load_config, TrailingStep
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


def run_backtest(config, db, resolver):
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


def extract_data(bt, m, label):
    """Extract data dict for JSON export."""
    # Daily P&L
    daily_pnl = {}
    for dr in bt.day_results:
        dt_str = dr.date.strftime('%Y-%m-%d')
        daily_pnl[dt_str] = dr.net_pnl

    # Equity curve
    equity = {}
    running = 0
    for dr in bt.day_results:
        dt_str = dr.date.strftime('%Y-%m-%d')
        running += dr.net_pnl
        equity[dt_str] = round(running, 2)

    # Exit reasons
    exit_reasons = {}
    for t in bt.all_trades:
        r = t.exit_reason.name
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Trades
    trades = []
    for t in bt.all_trades:
        trades.append({
            'date': t.entry_time.strftime('%Y-%m-%d'),
            'entry_time': t.entry_time.strftime('%H:%M'),
            'exit_time': t.exit_time.strftime('%H:%M'),
            'side': t.side.name,
            'symbol': t.option_symbol,
            'entry_premium': round(t.entry_premium, 2),
            'exit_premium': round(t.exit_premium, 2),
            'gross': round(t.gross_pnl, 0),
            'net': round(t.net_pnl, 0),
            'reason': t.exit_reason.name,
        })

    return {
        'label': label,
        'metrics': {
            'total_trades': m.total_trades,
            'winning_trades': m.winning_trades,
            'win_rate': round(m.win_rate * 100, 1),
            'gross_pnl': round(m.gross_pnl, 0),
            'net_pnl': round(m.net_pnl, 0),
            'charges': round(m.total_charges, 0),
            'avg_win': round(m.avg_win, 0),
            'avg_loss': round(m.avg_loss, 0),
            'rr': round(m.reward_to_risk, 2),
            'pf': round(m.profit_factor, 2),
            'max_dd': round(m.max_drawdown, 0),
            'sharpe': round(m.sharpe_ratio, 2),
        },
        'daily_pnl': daily_pnl,
        'equity': equity,
        'exit_reasons': exit_reasons,
        'trades': trades,
    }


def main():
    db = Database('data/orb_data.db')
    resolver = InstrumentResolver(db)

    # Corrected costs for both
    def set_correct_costs(cfg):
        cfg.backtest.stt_rate = 0.001
        cfg.backtest.exchange_txn_charge = 0.0003503
        cfg.backtest.stamp_duty = 0.00003
        cfg.backtest.sebi_charges = 0.000001

    # --- Original config ---
    original = load_config('config/default_config.yaml')
    original.session.orb_candles = 3
    original.session.orb_end = time(9, 18)
    original.session.no_new_entry_after = time(11, 30)
    original.strategy.rsi_entry_min = 40
    original.strategy.rsi_entry_max = 65
    original.strategy.supertrend_period = 10
    original.strategy.supertrend_multiplier = 3.0
    original.strategy.max_re_entries_per_side = 4
    original.strategy.trailing_ladder = [
        TrailingStep(trigger=30, trail_to=0),
        TrailingStep(trigger=60, trail_to=30),
        TrailingStep(trigger=90, trail_to=60),
        TrailingStep(trigger=120, trail_to=90),
        TrailingStep(trigger=150, trail_to=-1),
    ]
    set_correct_costs(original)

    # --- Tuned config (10-candle ORB) ---
    tuned = load_config('config/default_config.yaml')
    tuned.session.orb_candles = 10
    tuned.session.orb_end = time(9, 25)
    tuned.session.no_new_entry_after = time(12, 0)
    tuned.session.force_exit_time = time(15, 15)
    tuned.strategy.rsi_entry_min = 0
    tuned.strategy.rsi_entry_max = 100
    tuned.strategy.supertrend_period = 14
    tuned.strategy.supertrend_multiplier = 3.0
    tuned.strategy.max_re_entries_per_side = 1
    tuned.strategy.trailing_ladder = [
        TrailingStep(trigger=40, trail_to=0),
        TrailingStep(trigger=80, trail_to=40),
        TrailingStep(trigger=120, trail_to=80),
        TrailingStep(trigger=160, trail_to=120),
        TrailingStep(trigger=200, trail_to=-1),
    ]
    set_correct_costs(tuned)

    print("Running Original config...")
    bt_orig = run_backtest(copy.deepcopy(original), db, resolver)
    m_orig = compute_metrics(bt_orig)
    print(f"  {m_orig.total_trades} trades, Net={m_orig.net_pnl:+.0f}")

    print("Running Tuned config...")
    bt_tuned = run_backtest(copy.deepcopy(tuned), db, resolver)
    m_tuned = compute_metrics(bt_tuned)
    print(f"  {m_tuned.total_trades} trades, Net={m_tuned.net_pnl:+.0f}")

    data = {
        'original': extract_data(bt_orig, m_orig, 'Original (3-min ORB, RSI 40-65, ST 10/3, L30, re-entry 4)'),
        'tuned': extract_data(bt_tuned, m_tuned, 'Tuned (10-min ORB, RSI off, ST 14/3, L40, re-entry 1)'),
    }

    os.makedirs('output', exist_ok=True)
    with open('output/strategy_data.json', 'w') as f:
        json.dump(data, f, indent=2)
    print("\nSaved output/strategy_data.json")

    db.close()


if __name__ == "__main__":
    main()
