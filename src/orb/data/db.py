"""SQLite schema and CRUD operations for ORB trading data."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Generator


class Database:
    """Lightweight SQLite wrapper with schema auto-creation and CRUD helpers."""

    def __init__(self, db_path: str = "data/orb_data.db") -> None:
        self.db_path = db_path
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context-managed connection that commits on success, rolls back on error."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # dict-like access
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_token INTEGER NOT NULL,
                    timestamp        TEXT    NOT NULL,
                    open             REAL    NOT NULL,
                    high             REAL    NOT NULL,
                    low              REAL    NOT NULL,
                    close            REAL    NOT NULL,
                    volume           INTEGER NOT NULL DEFAULT 0,
                    interval         TEXT    NOT NULL DEFAULT 'minute',
                    UNIQUE(instrument_token, timestamp, interval)
                );

                CREATE TABLE IF NOT EXISTS instruments (
                    instrument_token INTEGER PRIMARY KEY,
                    tradingsymbol    TEXT,
                    exchange         TEXT,
                    instrument_type  TEXT,
                    strike           REAL,
                    expiry           TEXT,
                    lot_size         INTEGER,
                    name             TEXT,
                    last_updated     TEXT
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    date              TEXT,
                    side              TEXT,
                    entry_time        TEXT,
                    exit_time         TEXT,
                    underlying_entry  REAL,
                    underlying_exit   REAL,
                    h3                REAL,
                    l3                REAL,
                    h1                REAL,
                    l1                REAL,
                    strike            REAL,
                    option_type       TEXT,
                    option_symbol     TEXT,
                    entry_premium     REAL,
                    exit_premium      REAL,
                    lots              INTEGER,
                    lot_size          INTEGER,
                    gross_pnl         REAL,
                    charges           REAL,
                    net_pnl           REAL,
                    exit_reason       TEXT,
                    re_entry_number   INTEGER,
                    regime_at_exit    TEXT
                );

                CREATE TABLE IF NOT EXISTS daily_summary (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    date           TEXT UNIQUE,
                    total_trades   INTEGER,
                    winning_trades INTEGER,
                    gross_pnl      REAL,
                    net_pnl        REAL,
                    max_drawdown   REAL
                );
                """
            )

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------

    def insert_candles(self, candles: list[dict]) -> None:
        """Bulk upsert candles (INSERT OR REPLACE)."""
        if not candles:
            return
        sql = """
            INSERT OR REPLACE INTO candles
                (instrument_token, timestamp, open, high, low, close, volume, interval)
            VALUES
                (:instrument_token, :timestamp, :open, :high, :low, :close, :volume, :interval)
        """
        with self._connect() as conn:
            conn.executemany(sql, candles)

    def get_candles(
        self,
        instrument_token: int,
        from_dt: str,
        to_dt: str,
        interval: str = "minute",
    ) -> list[dict]:
        """Return candles in the given datetime range (inclusive)."""
        sql = """
            SELECT instrument_token, timestamp, open, high, low, close, volume, interval
            FROM candles
            WHERE instrument_token = ?
              AND timestamp >= ?
              AND timestamp <= ?
              AND interval = ?
            ORDER BY timestamp
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (instrument_token, from_dt, to_dt, interval)).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Instruments
    # ------------------------------------------------------------------

    def insert_instrument(self, instrument: dict) -> None:
        """Insert or replace a single instrument record."""
        sql = """
            INSERT OR REPLACE INTO instruments
                (instrument_token, tradingsymbol, exchange, instrument_type,
                 strike, expiry, lot_size, name, last_updated)
            VALUES
                (:instrument_token, :tradingsymbol, :exchange, :instrument_type,
                 :strike, :expiry, :lot_size, :name, :last_updated)
        """
        with self._connect() as conn:
            conn.execute(sql, instrument)

    def get_instrument(self, tradingsymbol: str) -> dict | None:
        """Look up an instrument by trading symbol."""
        sql = "SELECT * FROM instruments WHERE tradingsymbol = ?"
        with self._connect() as conn:
            row = conn.execute(sql, (tradingsymbol,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def insert_trade(self, trade: dict) -> None:
        """Insert a single trade record."""
        columns = ", ".join(trade.keys())
        placeholders = ", ".join(f":{k}" for k in trade.keys())
        sql = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.execute(sql, trade)

    def get_trades(self, date: str) -> list[dict]:
        """Return all trades for a given date string (e.g. '2025-01-15')."""
        sql = "SELECT * FROM trades WHERE date = ? ORDER BY entry_time"
        with self._connect() as conn:
            rows = conn.execute(sql, (date,)).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def insert_daily_summary(self, summary: dict) -> None:
        """Insert or replace a daily summary row."""
        sql = """
            INSERT OR REPLACE INTO daily_summary
                (date, total_trades, winning_trades, gross_pnl, net_pnl, max_drawdown)
            VALUES
                (:date, :total_trades, :winning_trades, :gross_pnl, :net_pnl, :max_drawdown)
        """
        with self._connect() as conn:
            conn.execute(sql, summary)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """No-op for the context-manager-per-call pattern.

        Kept for API compatibility so callers can signal they are done.
        """
        pass
