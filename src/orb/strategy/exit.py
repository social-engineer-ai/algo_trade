"""Dual-regime exit logic: Candle SL (Regime A) + Premium trailing (Regime B)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from orb.config import TrailingStep
from orb.models import Candle, ExitReason, Side


@dataclass
class ExitSignal:
    reason: ExitReason
    exit_premium: float  # Premium at which exit happens


class ExitManager:
    """Manages exit logic for an active position.

    Regime A (pre-T1): Stop loss is on the underlying at L1 (CALL) or H1 (PUT).
    Regime B (post-T1): Stop loss transitions to premium-based trailing ladder.

    Premium trailing ladder (default):
        T1: +30 → trail to cost (breakeven)
        T2: +60 → trail to +30
        T3: +90 → trail to +60
        T4: +120 → trail to +90
        T5: +150 → full exit
    """

    def __init__(self, trailing_ladder: list[TrailingStep]):
        self._ladder = trailing_ladder

    def check_exit(
        self,
        underlying_candle: Candle,
        option_premium: float,
        side: Side,
        h1: float,
        l1: float,
        entry_premium: float,
        current_regime: str,
        premium_sl: float | None,
        highest_gain: float,
        last_ladder_idx: int,
    ) -> tuple[Optional[ExitSignal], str, float | None, float, int]:
        """Check all exit conditions for the current candle.

        Args:
            underlying_candle: Current 1-min underlying candle.
            option_premium: Current option premium (close of option candle).
            side: CALL or PUT.
            h1, l1: Breakout structure levels.
            entry_premium: Premium at which position was entered.
            current_regime: "A" or "B".
            premium_sl: Current premium trailing SL (absolute premium level).
            highest_gain: Highest premium gain seen so far.
            last_ladder_idx: Last triggered ladder step index.

        Returns:
            Tuple of:
            - ExitSignal or None (if no exit)
            - Updated regime ("A" or "B")
            - Updated premium_sl
            - Updated highest_gain
            - Updated last_ladder_idx
        """
        premium_gain = option_premium - entry_premium

        # Update highest gain
        if premium_gain > highest_gain:
            highest_gain = premium_gain

        # --- Regime A: Candle-based SL on underlying ---
        if current_regime == "A":
            sl_hit = self._check_underlying_sl(underlying_candle, side, h1, l1)
            if sl_hit:
                return (
                    ExitSignal(reason=ExitReason.CANDLE_SL, exit_premium=option_premium),
                    "A",
                    premium_sl,
                    highest_gain,
                    last_ladder_idx,
                )

            # Check if we should transition to Regime B (T1 hit)
            new_regime, new_sl, new_idx = self._check_ladder_transition(
                premium_gain, entry_premium, premium_sl, last_ladder_idx
            )
            if new_regime == "B":
                current_regime = "B"
                premium_sl = new_sl
                last_ladder_idx = new_idx

        # --- Regime B: Premium trailing ---
        if current_regime == "B":
            # Check ladder progression
            _, new_sl, new_idx = self._check_ladder_transition(
                premium_gain, entry_premium, premium_sl, last_ladder_idx
            )
            if new_sl is not None:
                premium_sl = new_sl
            if new_idx > last_ladder_idx:
                last_ladder_idx = new_idx

            # Check for T5 full exit
            if last_ladder_idx >= 0 and last_ladder_idx < len(self._ladder):
                step = self._ladder[last_ladder_idx]
                if step.trail_to == -1:  # Full exit signal
                    return (
                        ExitSignal(reason=ExitReason.PREMIUM_TARGET, exit_premium=option_premium),
                        "B",
                        premium_sl,
                        highest_gain,
                        last_ladder_idx,
                    )

            # Check premium trailing SL
            if premium_sl is not None and option_premium <= premium_sl:
                return (
                    ExitSignal(reason=ExitReason.PREMIUM_TRAIL_SL, exit_premium=option_premium),
                    "B",
                    premium_sl,
                    highest_gain,
                    last_ladder_idx,
                )

        return (None, current_regime, premium_sl, highest_gain, last_ladder_idx)

    def check_force_exit(self, option_premium: float) -> ExitSignal:
        """Force exit at end of day (15:15)."""
        return ExitSignal(reason=ExitReason.FORCE_EXIT, exit_premium=option_premium)

    def _check_underlying_sl(
        self,
        candle: Candle,
        side: Side,
        h1: float,
        l1: float,
    ) -> bool:
        """Check if underlying candle hit the SL level.

        CALL SL: underlying goes below L1
        PUT SL: underlying goes above H1
        """
        if side == Side.CALL:
            return candle.low <= l1
        else:  # PUT
            return candle.high >= h1

    def _check_ladder_transition(
        self,
        premium_gain: float,
        entry_premium: float,
        current_sl: float | None,
        last_idx: int,
    ) -> tuple[str, float | None, int]:
        """Check if premium gain has triggered a new ladder step.

        Returns (regime, new_sl_premium, new_last_idx).
        """
        regime = "A" if last_idx < 0 else "B"
        new_sl = current_sl
        new_idx = last_idx

        for i, step in enumerate(self._ladder):
            if i <= last_idx:
                continue  # Already triggered
            if premium_gain >= step.trigger:
                new_idx = i
                if step.trail_to == -1:
                    # Full exit — SL doesn't matter, but mark regime B
                    regime = "B"
                else:
                    regime = "B"
                    # trail_to is the gain level; convert to absolute premium
                    new_sl = entry_premium + step.trail_to
            else:
                break  # Ladder is ordered; no point checking further

        return regime, new_sl, new_idx
