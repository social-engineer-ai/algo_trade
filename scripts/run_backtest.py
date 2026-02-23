#!/usr/bin/env python3
"""CLI: Run backtest and generate reports.

Usage:
    python scripts/run_backtest.py --from 2025-01-06 --to 2025-01-10
    python scripts/run_backtest.py --from 2025-01-06 --to 2025-03-31 --config config/default_config.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orb.config import load_config
from orb.data.db import Database
from orb.data.kite_auth import KiteSession
from orb.data.kite_fetcher import KiteFetcher
from orb.data.instruments import InstrumentResolver
from orb.data.cache import DataCache
from orb.backtest.runner import BacktestRunner
from orb.backtest.results import BacktestResult
from orb.reports.metrics import compute_metrics, format_metrics
from orb.reports.trade_log import export_trades_csv
from orb.reports.charts import plot_equity_curve, plot_daily_pnl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(description="Run ORB strategy backtest")
    parser.add_argument("--from", dest="from_date", required=True, type=parse_date,
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", required=True, type=parse_date,
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--config", default="config/default_config.yaml",
                        help="Path to config file")
    parser.add_argument("--db", default="data/orb_data.db",
                        help="Path to SQLite database")
    parser.add_argument("--output", default=None,
                        help="Output directory for reports")
    args = parser.parse_args()

    config = load_config(args.config)
    output_dir = Path(args.output or config.reporting.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db = Database(args.db)
    resolver = InstrumentResolver(db)

    # For backtest, we need a fetcher — create a dummy one if no auth
    # (assumes data already in DB from fetch_data.py)
    try:
        kite_session = KiteSession(config.kite_api_key, config.kite_api_secret)
        kite = kite_session.get_kite()
        fetcher = KiteFetcher(kite)
    except Exception:
        logger.warning("Kite not authenticated — using DB-only mode (no API fallback)")
        fetcher = None

    cache = DataCache(db, fetcher)

    # Run backtest
    logger.info(f"Running backtest from {args.from_date} to {args.to_date}")
    runner = BacktestRunner(config, cache, resolver)
    day_results = runner.run(args.from_date, args.to_date)

    result = BacktestResult.from_day_results(day_results)

    # Compute and print metrics
    metrics = compute_metrics(result, config.reporting.risk_free_rate)
    print(format_metrics(metrics))

    # Export trade log
    csv_path = export_trades_csv(result.all_trades, output_dir / "trade_log.csv")
    logger.info(f"Trade log exported to {csv_path}")

    # Generate charts
    if result.total_days > 0:
        eq_path = plot_equity_curve(result, output_dir / "equity_curve.png")
        logger.info(f"Equity curve saved to {eq_path}")

        pnl_path = plot_daily_pnl(result, output_dir / "daily_pnl.png")
        logger.info(f"Daily P&L chart saved to {pnl_path}")

    db.close()
    logger.info("Backtest complete!")


if __name__ == "__main__":
    main()
