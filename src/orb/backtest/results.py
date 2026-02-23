"""Backtest result aggregation."""
from __future__ import annotations

from dataclasses import dataclass, field

from orb.backtest.engine import DayResult
from orb.models import TradeRecord


@dataclass
class BacktestResult:
    """Aggregated results across all trading days."""

    day_results: list[DayResult] = field(default_factory=list)

    @property
    def all_trades(self) -> list[TradeRecord]:
        trades = []
        for dr in self.day_results:
            trades.extend(dr.trades)
        return trades

    @property
    def total_days(self) -> int:
        return len(self.day_results)

    @property
    def total_trades(self) -> int:
        return sum(dr.total_trades for dr in self.day_results)

    @property
    def winning_trades(self) -> int:
        return sum(dr.winning_trades for dr in self.day_results)

    @property
    def losing_trades(self) -> int:
        return sum(dr.losing_trades for dr in self.day_results)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def gross_pnl(self) -> float:
        return sum(dr.gross_pnl for dr in self.day_results)

    @property
    def net_pnl(self) -> float:
        return sum(dr.net_pnl for dr in self.day_results)

    @property
    def total_charges(self) -> float:
        return sum(dr.total_charges for dr in self.day_results)

    @property
    def daily_net_pnls(self) -> list[float]:
        return [dr.net_pnl for dr in self.day_results]

    @property
    def max_drawdown(self) -> float:
        """Maximum peak-to-trough drawdown in absolute terms."""
        pnls = self.daily_net_pnls
        if not pnls:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return max_dd

    @property
    def avg_win(self) -> float:
        wins = [t.net_pnl for t in self.all_trades if t.net_pnl > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.net_pnl for t in self.all_trades if t.net_pnl <= 0]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def reward_to_risk(self) -> float:
        if self.avg_loss == 0:
            return 0.0
        return abs(self.avg_win / self.avg_loss)

    @property
    def profit_factor(self) -> float:
        gross_wins = sum(t.net_pnl for t in self.all_trades if t.net_pnl > 0)
        gross_losses = abs(sum(t.net_pnl for t in self.all_trades if t.net_pnl <= 0))
        if gross_losses == 0:
            return float("inf") if gross_wins > 0 else 0.0
        return gross_wins / gross_losses

    @classmethod
    def from_day_results(cls, results: list[DayResult]) -> BacktestResult:
        return cls(day_results=results)
