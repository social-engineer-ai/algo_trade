"""Equity curve and daily P&L charts using matplotlib."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from orb.backtest.results import BacktestResult


def plot_equity_curve(result: BacktestResult, output_path: str | Path) -> Path:
    """Plot cumulative equity curve.

    Args:
        result: Backtest results.
        output_path: Path to save the chart image.

    Returns:
        Path to the saved image.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = [dr.date for dr in result.day_results]
    daily_pnls = result.daily_net_pnls

    # Cumulative P&L
    cumulative = []
    running = 0.0
    for pnl in daily_pnls:
        running += pnl
        cumulative.append(running)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, cumulative, "b-", linewidth=1.5, label="Cumulative Net P&L")
    ax.fill_between(dates, cumulative, alpha=0.1, color="blue")
    ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.5)

    ax.set_title("Equity Curve — NIFTY 3-Min ORB Strategy", fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative Net P&L (Rs)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()

    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    return output_path


def plot_daily_pnl(result: BacktestResult, output_path: str | Path) -> Path:
    """Plot daily P&L bar chart.

    Args:
        result: Backtest results.
        output_path: Path to save the chart image.

    Returns:
        Path to the saved image.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = [dr.date for dr in result.day_results]
    daily_pnls = result.daily_net_pnls

    colors = ["green" if p >= 0 else "red" for p in daily_pnls]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(dates, daily_pnls, color=colors, alpha=0.7, width=0.8)
    ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.5)

    ax.set_title("Daily P&L — NIFTY 3-Min ORB Strategy", fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Net P&L (Rs)")
    ax.grid(True, alpha=0.3, axis="y")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()

    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    return output_path
