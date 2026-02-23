"""Performance metrics: win rate, Sharpe, drawdown, profit factor, R:R."""
from __future__ import annotations

import math
from dataclasses import dataclass

from orb.backtest.results import BacktestResult


@dataclass
class PerformanceMetrics:
    total_days: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    total_charges: float
    avg_win: float
    avg_loss: float
    reward_to_risk: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    avg_daily_pnl: float
    daily_pnl_std: float


def compute_metrics(
    result: BacktestResult, risk_free_rate: float = 0.065
) -> PerformanceMetrics:
    """Compute all performance metrics from backtest results.

    Args:
        result: Aggregated backtest results.
        risk_free_rate: Annual risk-free rate for Sharpe calculation.

    Returns:
        PerformanceMetrics dataclass.
    """
    daily_pnls = result.daily_net_pnls
    n_days = len(daily_pnls)

    avg_daily = sum(daily_pnls) / n_days if n_days > 0 else 0.0

    if n_days > 1:
        variance = sum((p - avg_daily) ** 2 for p in daily_pnls) / (n_days - 1)
        std_daily = math.sqrt(variance)
    else:
        std_daily = 0.0

    # Annualized Sharpe ratio
    # ~252 trading days per year
    daily_rf = risk_free_rate / 252
    if std_daily > 0:
        sharpe = (avg_daily - daily_rf) / std_daily * math.sqrt(252)
    else:
        sharpe = 0.0

    return PerformanceMetrics(
        total_days=result.total_days,
        total_trades=result.total_trades,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate=result.win_rate,
        gross_pnl=result.gross_pnl,
        net_pnl=result.net_pnl,
        total_charges=result.total_charges,
        avg_win=result.avg_win,
        avg_loss=result.avg_loss,
        reward_to_risk=result.reward_to_risk,
        profit_factor=result.profit_factor,
        max_drawdown=result.max_drawdown,
        sharpe_ratio=sharpe,
        avg_daily_pnl=avg_daily,
        daily_pnl_std=std_daily,
    )


def format_metrics(m: PerformanceMetrics) -> str:
    """Pretty-print performance metrics."""
    lines = [
        "=" * 50,
        "       BACKTEST PERFORMANCE SUMMARY",
        "=" * 50,
        f"  Trading Days:      {m.total_days}",
        f"  Total Trades:      {m.total_trades}",
        f"  Winning Trades:    {m.winning_trades}",
        f"  Losing Trades:     {m.losing_trades}",
        f"  Win Rate:          {m.win_rate:.1%}",
        "-" * 50,
        f"  Gross P&L:         Rs {m.gross_pnl:,.2f}",
        f"  Total Charges:     Rs {m.total_charges:,.2f}",
        f"  Net P&L:           Rs {m.net_pnl:,.2f}",
        "-" * 50,
        f"  Avg Win:           Rs {m.avg_win:,.2f}",
        f"  Avg Loss:          Rs {m.avg_loss:,.2f}",
        f"  Reward:Risk:       {m.reward_to_risk:.2f}",
        f"  Profit Factor:     {m.profit_factor:.2f}",
        "-" * 50,
        f"  Max Drawdown:      Rs {m.max_drawdown:,.2f}",
        f"  Sharpe Ratio:      {m.sharpe_ratio:.2f}",
        f"  Avg Daily P&L:     Rs {m.avg_daily_pnl:,.2f}",
        f"  Daily P&L Std:     Rs {m.daily_pnl_std:,.2f}",
        "=" * 50,
    ]
    return "\n".join(lines)
