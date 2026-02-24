"""Session state persistence for crash recovery."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SessionState:
    """Persist and restore live trading session state to/from a JSON file.

    Saved fields:
    - Position state (side, entry premium, strike, symbol, regime)
    - ORB levels (H3, L3)
    - Breakout levels (H1, L1)
    - Trailing SL state
    - Trades completed today
    - Timestamp of last save
    """

    def __init__(self, state_file: str = "data/live_state.json") -> None:
        self._path = Path(state_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = {}

    @property
    def has_saved_state(self) -> bool:
        """True if a state file exists and was saved today."""
        if not self._path.exists():
            return False
        try:
            data = json.loads(self._path.read_text())
            saved_date = data.get("date", "")
            return saved_date == datetime.now().strftime("%Y-%m-%d")
        except (json.JSONDecodeError, KeyError):
            return False

    def save(
        self,
        *,
        has_position: bool = False,
        side: str = "",
        entry_premium: float = 0.0,
        strike: float = 0.0,
        option_symbol: str = "",
        option_type: str = "",
        regime: str = "A",
        premium_sl: Optional[float] = None,
        highest_premium_gain: float = 0.0,
        last_ladder_idx: int = -1,
        underlying_at_entry: float = 0.0,
        h3: float = 0.0,
        l3: float = 0.0,
        h1: float = 0.0,
        l1: float = 0.0,
        call_entries: int = 0,
        put_entries: int = 0,
        trades_today: list[dict] | None = None,
        net_pnl_today: float = 0.0,
    ) -> None:
        """Persist current session state to disk."""
        self._state = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "saved_at": datetime.now().isoformat(),
            "position": {
                "has_position": has_position,
                "side": side,
                "entry_premium": entry_premium,
                "strike": strike,
                "option_symbol": option_symbol,
                "option_type": option_type,
                "regime": regime,
                "premium_sl": premium_sl,
                "highest_premium_gain": highest_premium_gain,
                "last_ladder_idx": last_ladder_idx,
                "underlying_at_entry": underlying_at_entry,
            },
            "orb": {
                "h3": h3,
                "l3": l3,
            },
            "breakout": {
                "h1": h1,
                "l1": l1,
            },
            "counters": {
                "call_entries": call_entries,
                "put_entries": put_entries,
            },
            "trades_today": trades_today or [],
            "net_pnl_today": net_pnl_today,
        }

        self._path.write_text(json.dumps(self._state, indent=2))
        logger.debug(f"State saved to {self._path}")

    def load(self) -> dict[str, Any]:
        """Load saved state from disk. Returns empty dict if unavailable."""
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
            # Only return if saved today
            if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
                logger.info("State file is from a previous day, ignoring.")
                return {}
            self._state = data
            logger.info(f"State loaded from {self._path}")
            return data
        except (json.JSONDecodeError, KeyError):
            logger.warning(f"Corrupt state file at {self._path}")
            return {}

    def clear(self) -> None:
        """Remove the state file (clean start for a new day)."""
        if self._path.exists():
            self._path.unlink()
            logger.info(f"State file cleared: {self._path}")
        self._state = {}

    def reconcile_with_broker(
        self, broker_positions: dict[str, int]
    ) -> tuple[bool, str]:
        """Compare saved state with actual broker positions.

        Returns (is_consistent, message).
        If inconsistent, caller should alert and wait for manual intervention.
        """
        if not self._state:
            if broker_positions:
                return False, (
                    f"No saved state but broker has positions: {broker_positions}"
                )
            return True, "No state and no positions — clean start."

        pos = self._state.get("position", {})
        expected_symbol = pos.get("option_symbol", "")
        has_position = pos.get("has_position", False)

        if has_position:
            if expected_symbol not in broker_positions:
                return False, (
                    f"State says we hold {expected_symbol} but broker "
                    f"positions are: {broker_positions}"
                )
            return True, f"Position {expected_symbol} confirmed with broker."
        else:
            # We shouldn't have any positions
            if broker_positions:
                return False, (
                    f"State says no position but broker has: {broker_positions}"
                )
            return True, "No position expected, none found — consistent."
