#!/usr/bin/env python3
"""Run full backtest across all available trading days."""
import sys, os, logging
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger()

from orb.config import load_config
from orb.data.db import Database
from orb.data.instruments import InstrumentResolver
from orb.backtest.engine import BacktestEngine
from orb.backtest.results import BacktestResult
from orb.reports.metrics import compute_metrics, format_metrics
from orb.reports.trade_log import export_trades_csv
from orb.reports.charts import plot_equity_curve, plot_daily_pnl
from orb.models import Candle

config = load_config('config/default_config.yaml')
db = Database('data/orb_data.db')
resolver = InstrumentResolver(db)
engine = BacktestEngine(config)
nifty_token = resolver.get_nifty_spot_token()


def load_candles(token, from_dt, to_dt):
    raw = db.get_candles(token, from_dt, to_dt, 'minute')
    return [Candle(
        timestamp=datetime.fromisoformat(r['timestamp']),
        open=r['open'], high=r['high'], low=r['low'], close=r['close'],
        volume=r.get('volume', 0),
    ) for r in raw]


# Get all trading days
with db._connect() as conn:
    rows = conn.execute("""
        SELECT DISTINCT substr(timestamp, 1, 10) as dt
        FROM candles WHERE instrument_token = 256265
        ORDER BY dt
    """).fetchall()
    trading_days = [date.fromisoformat(r['dt']) for r in rows]

logger.info(f"Running backtest across {len(trading_days)} trading days "
            f"({trading_days[0]} to {trading_days[-1]})")

day_results = []
prev_warmup = None
total_trades_count = 0

for td in trading_days:
    day_from = f'{td} 09:15:00'
    day_to = f'{td} 15:30:00'

    underlying = load_candles(nifty_token, day_from, day_to)
    if not underlying:
        continue

    spot = underlying[0].open
    rounded = round(spot / 50) * 50
    call_strike = rounded - 200
    put_strike = rounded + 200
    expiry = resolver.get_nearest_expiry(td)

    option_candles = {}
    for strike, opt_type in [(call_strike, 'CE'), (put_strike, 'PE')]:
        token = resolver.get_option_token(strike, opt_type, expiry)
        if token:
            opt_list = load_candles(token, day_from, day_to)
            symbol = f'NIFTY{strike:.0f}{opt_type}'
            if opt_list:
                option_candles[symbol] = opt_list

    # Use synthetic premiums when no option data is available
    use_synthetic = not option_candles
    result = engine.run_day(
        trading_date=datetime.combine(td, datetime.min.time()),
        underlying_candles=underlying,
        option_candles=option_candles,
        warmup_candles=prev_warmup,
        synthetic_premiums=use_synthetic,
    )
    day_results.append(result)
    prev_warmup = underlying[-config.strategy.warmup_candles:]

    if result.trades:
        total_trades_count += len(result.trades)
        for t in result.trades:
            tag = "WIN" if t.net_pnl > 0 else "LOSS"
            print(f"  {td} | {tag:4s} | {t.side.name:4s} {t.option_symbol:16s} | "
                  f"entry@{t.entry_time.strftime('%H:%M')}={t.entry_premium:7.2f} "
                  f"exit@{t.exit_time.strftime('%H:%M')}={t.exit_premium:7.2f} | "
                  f"net={t.net_pnl:+8.2f} | {t.exit_reason.name}")

# Aggregate
bt_result = BacktestResult.from_day_results(day_results)
metrics = compute_metrics(bt_result, config.reporting.risk_free_rate)
print()
print(format_metrics(metrics))

# Export
os.makedirs('output', exist_ok=True)
csv_path = export_trades_csv(bt_result.all_trades, 'output/trade_log.csv')
print(f'\nTrade log: {csv_path}')

if bt_result.total_days > 0 and bt_result.total_trades > 0:
    eq = plot_equity_curve(bt_result, 'output/equity_curve.png')
    pnl = plot_daily_pnl(bt_result, 'output/daily_pnl.png')
    print(f'Equity curve: {eq}')
    print(f'Daily P&L chart: {pnl}')

db.close()
