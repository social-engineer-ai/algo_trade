"""Core domain models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional


class Side(Enum):
    CALL = auto()
    PUT = auto()


class ExitReason(Enum):
    CANDLE_SL = auto()          # Regime A: underlying hit SL level
    PREMIUM_TRAIL_SL = auto()   # Regime B: premium trailing SL hit
    PREMIUM_TARGET = auto()     # T5 full exit (+150)
    FORCE_EXIT = auto()         # 15:15 forced closure
    MANUAL = auto()


class PositionState(Enum):
    IDLE = auto()
    WAITING_ENTRY = auto()      # Breakout confirmed, waiting for H1/L1 cross
    ACTIVE_REGIME_A = auto()    # In position, pre-T1 (candle SL)
    ACTIVE_REGIME_B = auto()    # In position, post-T1 (premium trailing)
    CLOSED = auto()


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    def synthetic_ticks(self) -> list[float]:
        """Intra-candle ordering heuristic: bullish → O,L,H,C; bearish → O,H,L,C."""
        if self.is_bullish:
            return [self.open, self.low, self.high, self.close]
        else:
            return [self.open, self.high, self.low, self.close]


@dataclass
class BreakoutInfo:
    side: Side
    breakout_candle_idx: int     # Index of the candle that confirmed breakout
    h3: float                    # Opening range high
    l3: float                    # Opening range low
    h1: float                    # Entry/SL reference high
    l1: float                    # Entry/SL reference low
    confirmed_at: datetime = field(default_factory=datetime.now)


@dataclass
class TradeRecord:
    trade_id: int = 0
    date: datetime = field(default_factory=datetime.now)
    side: Side = Side.CALL
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    # Underlying levels
    underlying_entry: float = 0.0
    underlying_exit: float = 0.0
    h3: float = 0.0
    l3: float = 0.0
    h1: float = 0.0
    l1: float = 0.0
    # Option details
    strike: float = 0.0
    option_type: str = ""        # "CE" or "PE"
    option_symbol: str = ""
    entry_premium: float = 0.0
    exit_premium: float = 0.0
    # P&L
    lots: int = 1
    lot_size: int = 25
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    # Meta
    exit_reason: ExitReason = ExitReason.MANUAL
    re_entry_number: int = 0     # 0 = first entry, 1 = first re-entry, etc.
    regime_at_exit: str = "A"    # "A" or "B"


@dataclass
class Position:
    """Mutable position state tracked during a session."""
    state: PositionState = PositionState.IDLE
    side: Optional[Side] = None
    breakout: Optional[BreakoutInfo] = None
    entry_premium: float = 0.0
    current_premium: float = 0.0
    entry_time: Optional[datetime] = None
    underlying_at_entry: float = 0.0
    strike: float = 0.0
    option_type: str = ""
    option_symbol: str = ""
    lots: int = 1
    # Trailing state
    premium_sl: Optional[float] = None   # Current trailing SL in premium terms
    highest_premium_gain: float = 0.0    # Max (current - entry) seen
    last_triggered_ladder_idx: int = -1  # Index into trailing_ladder
    # Counters (per side per day)
    call_entries_today: int = 0
    put_entries_today: int = 0

    @property
    def is_active(self) -> bool:
        return self.state in (PositionState.ACTIVE_REGIME_A, PositionState.ACTIVE_REGIME_B)
