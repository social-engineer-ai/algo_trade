"""Order placement and tracking — real and paper trading modes."""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


@dataclass
class OrderRecord:
    """Lightweight record of an order placed (real or paper)."""
    order_id: str
    symbol: str
    transaction_type: str  # "BUY" or "SELL"
    qty: int
    price: float
    order_type: str  # "LIMIT" or "MARKET"
    status: str = "PENDING"
    fill_price: float = 0.0
    filled_at: Optional[datetime] = None
    is_paper: bool = False


class OrderManager:
    """Place and track orders via Kite Connect, or simulate in paper mode.

    In paper mode, orders are logged to a CSV file and tracked in-memory
    using real LTP data from the ticker.
    """

    def __init__(
        self,
        kite: Optional[KiteConnect] = None,
        paper_mode: bool = True,
        log_file: str = "output/live_log.csv",
    ) -> None:
        self._kite = kite
        self._paper_mode = paper_mode
        self._log_file = Path(log_file)
        self._orders: dict[str, OrderRecord] = {}
        self._paper_order_counter = 0
        self._paper_positions: dict[str, int] = {}  # symbol → net qty

        # Ensure log directory exists
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def buy_option(
        self,
        symbol: str,
        qty: int,
        limit_price: float,
        exchange: str = "NFO",
    ) -> str:
        """Place a BUY order. Returns order_id."""
        if self._paper_mode:
            return self._paper_order(symbol, "BUY", qty, limit_price)

        return self._place_real_order(
            exchange=exchange,
            symbol=symbol,
            transaction_type="BUY",
            qty=qty,
            price=limit_price,
            order_type="LIMIT",
        )

    def sell_option(
        self,
        symbol: str,
        qty: int,
        market: bool = True,
        limit_price: float = 0.0,
        exchange: str = "NFO",
    ) -> str:
        """Place a SELL order. Default is MARKET for exits."""
        if self._paper_mode:
            return self._paper_order(symbol, "SELL", qty, limit_price)

        order_type = "MARKET" if market else "LIMIT"
        return self._place_real_order(
            exchange=exchange,
            symbol=symbol,
            transaction_type="SELL",
            qty=qty,
            price=limit_price,
            order_type=order_type,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if successful."""
        rec = self._orders.get(order_id)
        if rec is None:
            logger.warning(f"cancel_order: unknown order_id={order_id}")
            return False

        if self._paper_mode:
            rec.status = "CANCELLED"
            logger.info(f"[PAPER] Cancelled order {order_id}")
            return True

        try:
            self._kite.cancel_order(
                variety=self._kite.VARIETY_REGULAR, order_id=order_id
            )
            rec.status = "CANCELLED"
            logger.info(f"Cancelled order {order_id}")
            return True
        except Exception:
            logger.exception(f"Failed to cancel order {order_id}")
            return False

    def emergency_exit_all(self, positions: list[dict] | None = None) -> list[str]:
        """Market-sell all open positions. Kill switch.

        Parameters
        ----------
        positions : list[dict], optional
            Override positions list. If None, fetches from broker (real mode)
            or uses paper positions.

        Returns
        -------
        list[str]
            Order IDs of the exit orders placed.
        """
        order_ids = []

        if self._paper_mode:
            for symbol, qty in list(self._paper_positions.items()):
                if qty > 0:
                    oid = self._paper_order(symbol, "SELL", qty, 0.0)
                    order_ids.append(oid)
            self._paper_positions.clear()
            logger.warning("[PAPER] Emergency exit: all positions closed")
            return order_ids

        # Real mode — get positions from broker
        if positions is None:
            try:
                positions = self._kite.positions().get("net", [])
            except Exception:
                logger.exception("Failed to fetch positions for emergency exit")
                return order_ids

        for pos in positions:
            qty = pos.get("quantity", 0)
            if qty > 0:
                symbol = pos["tradingsymbol"]
                exchange = pos.get("exchange", "NFO")
                try:
                    oid = self._place_real_order(
                        exchange=exchange,
                        symbol=symbol,
                        transaction_type="SELL",
                        qty=qty,
                        price=0.0,
                        order_type="MARKET",
                    )
                    order_ids.append(oid)
                except Exception:
                    logger.exception(f"Emergency exit failed for {symbol}")

        logger.warning(f"Emergency exit: placed {len(order_ids)} exit orders")
        return order_ids

    # ------------------------------------------------------------------
    # Order status
    # ------------------------------------------------------------------

    def get_order_status(self, order_id: str) -> Optional[OrderRecord]:
        """Return the tracked OrderRecord, refreshing status from broker if real."""
        rec = self._orders.get(order_id)
        if rec is None:
            return None

        if not self._paper_mode and rec.status == "PENDING":
            try:
                history = self._kite.order_history(order_id)
                if history:
                    latest = history[-1]
                    rec.status = latest.get("status", rec.status)
                    if rec.status == "COMPLETE":
                        rec.fill_price = float(latest.get("average_price", 0))
                        rec.filled_at = datetime.now()
            except Exception:
                logger.exception(f"Failed to fetch order status for {order_id}")

        return rec

    def get_positions(self) -> dict[str, int]:
        """Return current net positions (symbol → qty).

        In paper mode, returns the in-memory paper positions.
        In real mode, fetches from broker.
        """
        if self._paper_mode:
            return dict(self._paper_positions)

        try:
            positions = self._kite.positions().get("net", [])
            return {
                p["tradingsymbol"]: p.get("quantity", 0)
                for p in positions
                if p.get("quantity", 0) != 0
            }
        except Exception:
            logger.exception("Failed to fetch positions")
            return {}

    # ------------------------------------------------------------------
    # Paper trading
    # ------------------------------------------------------------------

    def paper_fill(self, order_id: str, fill_price: float) -> None:
        """Manually fill a paper order at a given price (called by session runner)."""
        rec = self._orders.get(order_id)
        if rec is None or not rec.is_paper:
            return
        rec.status = "COMPLETE"
        rec.fill_price = fill_price
        rec.filled_at = datetime.now()

        # Update paper positions
        if rec.transaction_type == "BUY":
            self._paper_positions[rec.symbol] = (
                self._paper_positions.get(rec.symbol, 0) + rec.qty
            )
        else:
            self._paper_positions[rec.symbol] = (
                self._paper_positions.get(rec.symbol, 0) - rec.qty
            )
            if self._paper_positions[rec.symbol] <= 0:
                self._paper_positions.pop(rec.symbol, None)

        self._log_order(rec)

    def _paper_order(
        self, symbol: str, txn_type: str, qty: int, price: float
    ) -> str:
        """Create a paper order record."""
        self._paper_order_counter += 1
        order_id = f"PAPER-{self._paper_order_counter:06d}"

        rec = OrderRecord(
            order_id=order_id,
            symbol=symbol,
            transaction_type=txn_type,
            qty=qty,
            price=price,
            order_type="LIMIT" if txn_type == "BUY" else "MARKET",
            status="PENDING",
            is_paper=True,
        )
        self._orders[order_id] = rec
        logger.info(
            f"[PAPER] {txn_type} {qty} × {symbol} @ {price:.2f} → {order_id}"
        )
        return order_id

    # ------------------------------------------------------------------
    # Real order placement
    # ------------------------------------------------------------------

    def _place_real_order(
        self,
        exchange: str,
        symbol: str,
        transaction_type: str,
        qty: int,
        price: float,
        order_type: str,
    ) -> str:
        """Place a real order via Kite Connect."""
        params = {
            "variety": self._kite.VARIETY_REGULAR,
            "exchange": exchange,
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": qty,
            "product": self._kite.PRODUCT_MIS,  # Intraday
            "order_type": order_type,
        }
        if order_type == "LIMIT":
            params["price"] = price

        order_id = self._kite.place_order(**params)
        logger.info(
            f"[LIVE] {transaction_type} {qty} × {symbol} @ {price:.2f} "
            f"({order_type}) → order_id={order_id}"
        )

        rec = OrderRecord(
            order_id=str(order_id),
            symbol=symbol,
            transaction_type=transaction_type,
            qty=qty,
            price=price,
            order_type=order_type,
            status="PENDING",
            is_paper=False,
        )
        self._orders[str(order_id)] = rec
        return str(order_id)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_order(self, rec: OrderRecord) -> None:
        """Append a filled order to the CSV log."""
        file_exists = self._log_file.exists()
        with open(self._log_file, "a", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "timestamp", "order_id", "symbol", "txn_type",
                    "qty", "price", "fill_price", "order_type",
                    "status", "is_paper",
                ],
            )
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "timestamp": rec.filled_at.isoformat() if rec.filled_at else "",
                "order_id": rec.order_id,
                "symbol": rec.symbol,
                "txn_type": rec.transaction_type,
                "qty": rec.qty,
                "price": rec.price,
                "fill_price": rec.fill_price,
                "order_type": rec.order_type,
                "status": rec.status,
                "is_paper": rec.is_paper,
            })
