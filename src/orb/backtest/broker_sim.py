"""Broker simulation — slippage, brokerage, STT, GST, and other charges."""
from __future__ import annotations

from dataclasses import dataclass

from orb.config import BacktestConfig
from orb.models import TradeRecord


@dataclass
class TradeCosts:
    brokerage: float = 0.0
    stt: float = 0.0
    gst: float = 0.0
    sebi_charges: float = 0.0
    stamp_duty: float = 0.0
    exchange_txn: float = 0.0
    slippage_cost: float = 0.0
    total: float = 0.0


class BrokerSimulator:
    """Simulates realistic trading costs for Indian options markets."""

    def __init__(self, config: BacktestConfig):
        self._config = config

    def apply_slippage(self, premium: float, is_buy: bool) -> float:
        """Apply slippage to premium. Buyer pays more, seller gets less."""
        if is_buy:
            return premium + self._config.slippage_points
        else:
            return max(0.05, premium - self._config.slippage_points)

    def calculate_costs(self, trade: TradeRecord) -> TradeCosts:
        """Calculate all trading charges for a completed trade.

        Charges:
        - Brokerage: flat per order × 2 (buy + sell)
        - STT: on sell side premium × lot_size × lots
        - GST: 18% on brokerage
        - SEBI charges: on total turnover
        - Stamp duty: on buy side turnover
        - Exchange transaction charges: on total turnover
        """
        qty = trade.lot_size * trade.lots
        buy_turnover = trade.entry_premium * qty
        sell_turnover = trade.exit_premium * qty
        total_turnover = buy_turnover + sell_turnover

        brokerage = self._config.brokerage_per_order * 2  # Buy + sell
        stt = sell_turnover * self._config.stt_rate
        gst = brokerage * self._config.gst_rate
        sebi = total_turnover * self._config.sebi_charges
        stamp = buy_turnover * self._config.stamp_duty
        exchange_txn = total_turnover * self._config.exchange_txn_charge

        # Slippage cost (already applied to premiums, but track separately)
        slippage_cost = self._config.slippage_points * 2 * qty  # Both legs

        total = brokerage + stt + gst + sebi + stamp + exchange_txn

        return TradeCosts(
            brokerage=brokerage,
            stt=stt,
            gst=gst,
            sebi_charges=sebi,
            stamp_duty=stamp,
            exchange_txn=exchange_txn,
            slippage_cost=slippage_cost,
            total=total,
        )

    def apply_costs(self, trade: TradeRecord) -> TradeRecord:
        """Apply all costs to a trade record, updating charges and net_pnl."""
        costs = self.calculate_costs(trade)
        trade.charges = costs.total
        trade.net_pnl = trade.gross_pnl - costs.total
        return trade
