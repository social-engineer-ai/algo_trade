"""CSV export for trade log."""
from __future__ import annotations

import csv
from pathlib import Path

from orb.models import TradeRecord


def export_trades_csv(trades: list[TradeRecord], output_path: str | Path) -> Path:
    """Export trades to a CSV file.

    Args:
        trades: List of completed trade records.
        output_path: File path for the CSV output.

    Returns:
        Path to the created CSV file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "trade_id",
        "date",
        "side",
        "entry_time",
        "exit_time",
        "underlying_entry",
        "underlying_exit",
        "h3",
        "l3",
        "h1",
        "l1",
        "strike",
        "option_type",
        "option_symbol",
        "entry_premium",
        "exit_premium",
        "lots",
        "lot_size",
        "gross_pnl",
        "charges",
        "net_pnl",
        "exit_reason",
        "re_entry_number",
        "regime_at_exit",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for t in trades:
            writer.writerow([
                t.trade_id,
                t.date.strftime("%Y-%m-%d") if t.date else "",
                t.side.name,
                t.entry_time.strftime("%Y-%m-%d %H:%M:%S") if t.entry_time else "",
                t.exit_time.strftime("%Y-%m-%d %H:%M:%S") if t.exit_time else "",
                f"{t.underlying_entry:.2f}",
                f"{t.underlying_exit:.2f}",
                f"{t.h3:.2f}",
                f"{t.l3:.2f}",
                f"{t.h1:.2f}",
                f"{t.l1:.2f}",
                f"{t.strike:.0f}",
                t.option_type,
                t.option_symbol,
                f"{t.entry_premium:.2f}",
                f"{t.exit_premium:.2f}",
                t.lots,
                t.lot_size,
                f"{t.gross_pnl:.2f}",
                f"{t.charges:.2f}",
                f"{t.net_pnl:.2f}",
                t.exit_reason.name,
                t.re_entry_number,
                t.regime_at_exit,
            ])

    return output_path
