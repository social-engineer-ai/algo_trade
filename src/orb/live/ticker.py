"""WebSocket tick streaming and 1-minute candle aggregation."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime, time
from typing import Callable, Optional

from kiteconnect import KiteTicker

from orb.models import Candle

logger = logging.getLogger(__name__)

# Market hours
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


class LiveTicker:
    """Connects to Kite WebSocket, subscribes to tokens, and aggregates
    ticks into 1-minute candles.

    Usage::

        ticker = LiveTicker(api_key, access_token)
        ticker.on_candle_complete(my_callback)  # callback(token, candle)
        ticker.start([256265, ...])             # blocking until stop()
    """

    def __init__(self, api_key: str, access_token: str) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._kws: Optional[KiteTicker] = None

        # Candle aggregation state per token
        # Key: token, Value: dict with o, h, l, c, v, minute_key
        self._candle_buf: dict[int, dict] = {}
        self._ltp: dict[int, float] = {}

        # Callbacks
        self._candle_callbacks: list[Callable[[int, Candle], None]] = []

        # Control
        self._tokens: list[int] = []
        self._connected = threading.Event()
        self._stop_requested = False

    def on_candle_complete(self, callback: Callable[[int, Candle], None]) -> None:
        """Register a callback invoked when a 1-min candle completes.

        Callback signature: ``callback(instrument_token: int, candle: Candle)``.
        """
        self._candle_callbacks.append(callback)

    def get_ltp(self, token: int) -> Optional[float]:
        """Return the last traded price for *token*, or None if unseen."""
        return self._ltp.get(token)

    def wait_connected(self, timeout: float = 30.0) -> bool:
        """Block until WebSocket is connected. Returns True if connected."""
        return self._connected.wait(timeout=timeout)

    def start(self, tokens: list[int]) -> None:
        """Connect to Kite WebSocket and begin streaming. Blocking call."""
        self._tokens = tokens
        self._stop_requested = False

        self._kws = KiteTicker(self._api_key, self._access_token)
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = self._on_close
        self._kws.on_error = self._on_error

        logger.info("Starting WebSocket connection...")
        self._kws.connect(threaded=False)

    def start_threaded(self, tokens: list[int]) -> threading.Thread:
        """Start WebSocket in a background thread. Returns the thread."""
        self._tokens = tokens
        self._stop_requested = False

        self._kws = KiteTicker(self._api_key, self._access_token)
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = self._on_close
        self._kws.on_error = self._on_error

        t = threading.Thread(target=self._kws.connect, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        """Disconnect WebSocket gracefully."""
        self._stop_requested = True
        if self._kws:
            self._kws.close()
            logger.info("WebSocket disconnected.")

    def flush_open_candles(self) -> None:
        """Emit any partially-filled candle buffers (e.g. at force-exit time)."""
        for token, buf in list(self._candle_buf.items()):
            if buf.get("minute_key") is not None:
                candle = self._buf_to_candle(buf)
                if candle:
                    self._emit_candle(token, candle)
        self._candle_buf.clear()

    # ------------------------------------------------------------------
    # KiteTicker callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, ws, response) -> None:
        logger.info(f"WebSocket connected. Subscribing to {len(self._tokens)} tokens.")
        ws.subscribe(self._tokens)
        ws.set_mode(ws.MODE_FULL, self._tokens)
        self._connected.set()

    def _on_close(self, ws, code, reason) -> None:
        logger.info(f"WebSocket closed: code={code}, reason={reason}")
        self._connected.clear()

    def _on_error(self, ws, code, reason) -> None:
        logger.error(f"WebSocket error: code={code}, reason={reason}")

    def _on_ticks(self, ws, ticks: list[dict]) -> None:
        """Process incoming ticks and aggregate into 1-min candles."""
        for tick in ticks:
            token = tick["instrument_token"]
            ltp = tick.get("last_price")
            if ltp is None:
                continue

            self._ltp[token] = ltp

            # Determine the minute key: floor to the start of the minute
            tick_ts = tick.get("exchange_timestamp") or tick.get("timestamp")
            if tick_ts is None:
                tick_ts = datetime.now()
            elif isinstance(tick_ts, str):
                tick_ts = datetime.fromisoformat(tick_ts)

            minute_key = tick_ts.replace(second=0, microsecond=0)

            volume = tick.get("volume_traded", 0)

            buf = self._candle_buf.get(token)

            if buf is None or buf["minute_key"] != minute_key:
                # New minute — emit previous candle if exists
                if buf is not None and buf["minute_key"] is not None:
                    candle = self._buf_to_candle(buf)
                    if candle:
                        self._emit_candle(token, candle)

                # Start new candle buffer
                self._candle_buf[token] = {
                    "minute_key": minute_key,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": volume,
                    "prev_volume": buf["volume"] if buf else 0,
                }
            else:
                # Same minute — update OHLCV
                buf["high"] = max(buf["high"], ltp)
                buf["low"] = min(buf["low"], ltp)
                buf["close"] = ltp
                buf["volume"] = volume

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _buf_to_candle(buf: dict) -> Optional[Candle]:
        """Convert a candle buffer dict to a Candle model."""
        mk = buf.get("minute_key")
        if mk is None:
            return None
        # Volume for this candle = current cumulative - previous cumulative
        vol = max(0, buf.get("volume", 0) - buf.get("prev_volume", 0))
        return Candle(
            timestamp=mk,
            open=buf["open"],
            high=buf["high"],
            low=buf["low"],
            close=buf["close"],
            volume=vol,
        )

    def _emit_candle(self, token: int, candle: Candle) -> None:
        """Invoke all registered candle callbacks."""
        for cb in self._candle_callbacks:
            try:
                cb(token, candle)
            except Exception:
                logger.exception(f"Error in candle callback for token {token}")
