"""Token resolution and ITM strike selection for NIFTY options."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from orb.data.db import Database
from orb.models import Side


# Well-known Kite instrument token for NIFTY 50 spot index.
_NIFTY_SPOT_TOKEN = 256265


class InstrumentResolver:
    """Resolves instrument tokens, ITM strikes, and expiry dates using the DB."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Bulk load
    # ------------------------------------------------------------------

    def load_instruments(self, instruments: list[dict]) -> None:
        """Persist a full instrument dump (e.g. from ``KiteFetcher.fetch_instruments``) into the DB."""
        now_str = datetime.utcnow().isoformat()
        for inst in instruments:
            self._db.insert_instrument(
                {
                    "instrument_token": int(inst["instrument_token"]),
                    "tradingsymbol": inst.get("tradingsymbol", ""),
                    "exchange": inst.get("exchange", ""),
                    "instrument_type": inst.get("instrument_type", ""),
                    "strike": float(inst.get("strike", 0)),
                    "expiry": str(inst.get("expiry", "")),
                    "lot_size": int(inst.get("lot_size", 0)),
                    "name": inst.get("name", ""),
                    "last_updated": now_str,
                }
            )

    # ------------------------------------------------------------------
    # Token lookups
    # ------------------------------------------------------------------

    def get_nifty_spot_token(self) -> int:
        """Return the NIFTY 50 spot instrument token."""
        return _NIFTY_SPOT_TOKEN

    def get_nifty_fut_token(self, expiry_date: date) -> int | None:
        """Find the nearest NIFTY futures token for *expiry_date*.

        Searches the DB for ``instrument_type='FUT'`` with ``name='NIFTY'``
        and picks the contract whose expiry is closest to (and >= ) *expiry_date*.
        """
        sql = """
            SELECT instrument_token, expiry
            FROM instruments
            WHERE name = 'NIFTY'
              AND instrument_type = 'FUT'
              AND exchange = 'NFO'
              AND expiry >= ?
            ORDER BY expiry ASC
            LIMIT 1
        """
        with self._db._connect() as conn:
            row = conn.execute(sql, (expiry_date.isoformat(),)).fetchone()
            return int(row["instrument_token"]) if row else None

    def get_option_token(
        self, strike: float, option_type: str, expiry_date: date
    ) -> int | None:
        """Find the NFO option token for *strike* / *option_type* / *expiry_date*.

        Parameters
        ----------
        strike : float
            The option strike price (e.g. 22000.0).
        option_type : str
            ``"CE"`` or ``"PE"``.
        expiry_date : date
            Expiry date to match.
        """
        sql = """
            SELECT instrument_token
            FROM instruments
            WHERE name = 'NIFTY'
              AND exchange = 'NFO'
              AND instrument_type = ?
              AND strike = ?
              AND expiry = ?
            LIMIT 1
        """
        with self._db._connect() as conn:
            row = conn.execute(
                sql, (option_type, strike, expiry_date.isoformat())
            ).fetchone()
            return int(row["instrument_token"]) if row else None

    # ------------------------------------------------------------------
    # Strike selection
    # ------------------------------------------------------------------

    def get_itm_strike(
        self,
        spot_price: float,
        side: Side,
        itm_offset: int = 200,
        strike_step: int = 50,
    ) -> float:
        """Compute an in-the-money strike for a CALL or PUT entry.

        Logic
        -----
        - CALL: ``round_to_step(spot) - itm_offset``  (use CE)
        - PUT:  ``round_to_step(spot) + itm_offset``  (use PE)

        ``round_to_step(x)`` rounds *x* to the nearest *strike_step*.
        """
        rounded = self._round_to_step(spot_price, strike_step)
        if side is Side.CALL:
            return rounded - itm_offset
        else:
            return rounded + itm_offset

    # ------------------------------------------------------------------
    # Expiry helpers
    # ------------------------------------------------------------------

    def get_nearest_expiry(self, from_date: date) -> date:
        """Return the nearest weekly expiry (Thursday) on or after *from_date*.

        If *from_date* is already a Thursday it is returned as-is.
        """
        # Thursday = weekday 3
        days_ahead = (3 - from_date.weekday()) % 7
        if days_ahead == 0:
            return from_date
        return from_date + timedelta(days=days_ahead)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _round_to_step(value: float, step: int) -> float:
        """Round *value* to the nearest multiple of *step*."""
        return round(value / step) * step
