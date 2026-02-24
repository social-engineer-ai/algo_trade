"""Live trading session orchestrator — runs one trading day."""
from __future__ import annotations

import logging
import time as time_mod
from datetime import date, datetime, time, timedelta
from typing import Optional

from kiteconnect import KiteConnect

from orb.config import AppConfig, load_config
from orb.data.db import Database
from orb.data.instruments import InstrumentResolver
from orb.data.kite_auth import KiteSession
from orb.data.kite_fetcher import KiteFetcher
from orb.live.notifier import TelegramNotifier
from orb.live.order_manager import OrderManager
from orb.live.state import SessionState
from orb.live.ticker import LiveTicker
from orb.models import Candle, PositionState, Side, TradeRecord
from orb.strategy.session import TradingSession

logger = logging.getLogger(__name__)


class LiveSessionRunner:
    """Orchestrates a single live trading day.

    Reuses the backtest ``TradingSession`` for signal generation, and wraps
    it with real-time data from KiteTicker and order execution via OrderManager.

    Daily flow:
        1. Authenticate Kite, resolve instruments
        2. Warmup indicators with previous day candles
        3. Connect WebSocket, subscribe to NIFTY spot + option tokens
        4. On each completed 1-min candle, call ``TradingSession.process_candle()``
        5. Detect entry/exit state transitions → place orders
        6. Force exit at 15:15, log daily summary
    """

    def __init__(
        self,
        config: AppConfig,
        kite: KiteConnect,
        paper_mode: bool = True,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        state_file: str = "data/live_state.json",
        log_file: str = "output/live_log.csv",
        lots: int = 1,
        max_daily_loss: float = 3000.0,
    ) -> None:
        self._config = config
        self._kite = kite
        self._paper_mode = paper_mode
        self._lots = lots
        self._max_daily_loss = max_daily_loss
        self._today = date.today()

        # Components
        self._fetcher = KiteFetcher(kite)
        self._db = Database()
        self._resolver = InstrumentResolver(self._db)
        self._notifier = TelegramNotifier(telegram_bot_token, telegram_chat_id)
        self._order_mgr = OrderManager(
            kite=kite if not paper_mode else None,
            paper_mode=paper_mode,
            log_file=log_file,
        )
        self._state = SessionState(state_file)
        self._ticker: Optional[LiveTicker] = None

        # Trading session (strategy engine)
        self._session: Optional[TradingSession] = None

        # Instrument tokens
        self._nifty_token = self._resolver.get_nifty_spot_token()
        self._ce_token: Optional[int] = None
        self._pe_token: Optional[int] = None
        self._ce_symbol: str = ""
        self._pe_symbol: str = ""

        # Live state tracking
        self._was_active = False  # Position active before last candle
        self._pending_entry_order: Optional[str] = None
        self._pending_exit_order: Optional[str] = None
        self._daily_trades: list[TradeRecord] = []
        self._net_pnl_today = 0.0
        self._killed = False  # Daily loss limit hit

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the full daily session. Blocks until market close or stop."""
        logger.info(f"{'='*60}")
        logger.info(f"Live session starting — {self._today}")
        logger.info(f"Paper mode: {self._paper_mode}")
        logger.info(f"{'='*60}")

        try:
            self._setup()
            self._warmup_indicators()
            self._connect_websocket()
            self._wait_for_market_close()
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        except Exception:
            logger.exception("Fatal error in live session")
            self._notifier.error("Fatal error — session stopped. Check logs.")
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """Load instruments, resolve option tokens, create TradingSession."""
        # Refresh instruments from Kite
        logger.info("Fetching instruments from Kite...")
        instruments = self._fetcher.fetch_instruments("NFO")
        self._resolver.load_instruments(instruments)

        # Find nearest expiry
        expiry = self._resolver.get_nearest_expiry(self._today)
        logger.info(f"Using expiry: {expiry}")

        # We need a rough spot price to resolve strikes.
        # Use previous close or current LTP from Kite.
        ltp_data = self._kite.ltp(["NSE:NIFTY 50"])
        spot = ltp_data["NSE:NIFTY 50"]["last_price"]
        logger.info(f"Current NIFTY spot: {spot:.2f}")

        # Resolve CE and PE strikes + tokens
        ce_strike = self._resolver.get_itm_strike(
            spot, Side.CALL,
            self._config.market.itm_offset,
            self._config.market.strike_step,
        )
        pe_strike = self._resolver.get_itm_strike(
            spot, Side.PUT,
            self._config.market.itm_offset,
            self._config.market.strike_step,
        )

        self._ce_token = self._resolver.get_option_token(ce_strike, "CE", expiry)
        self._pe_token = self._resolver.get_option_token(pe_strike, "PE", expiry)

        # Build trading symbols
        self._ce_symbol = f"NIFTY{ce_strike:.0f}CE"
        self._pe_symbol = f"NIFTY{pe_strike:.0f}PE"

        logger.info(f"CE: {self._ce_symbol} (token={self._ce_token})")
        logger.info(f"PE: {self._pe_symbol} (token={self._pe_token})")

        if self._ce_token is None or self._pe_token is None:
            raise RuntimeError("Could not resolve option tokens. Check instrument data.")

        # Create the strategy session
        self._session = TradingSession(self._config, datetime.now())
        self._session.set_option_symbol(self._ce_symbol)
        self._session.set_option_symbol(self._pe_symbol)

        mode_label = "PAPER" if self._paper_mode else "LIVE"
        self._notifier.session_started(
            f"Mode: {mode_label}\n"
            f"Lots: {self._lots}\n"
            f"CE: {self._ce_symbol}\n"
            f"PE: {self._pe_symbol}\n"
            f"Expiry: {expiry}\n"
            f"Max loss: ₹{self._max_daily_loss:.0f}"
        )

    def _warmup_indicators(self) -> None:
        """Feed previous-day candles to warm up RSI and SuperTrend."""
        logger.info("Warming up indicators with previous day data...")
        warmup_count = self._config.strategy.warmup_candles

        # Fetch last N trading day candles from Kite
        from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        to_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        raw_candles = self._fetcher.fetch_candles(
            self._nifty_token, from_date, to_date, "minute"
        )

        # Take the last warmup_count candles
        warmup_raw = raw_candles[-warmup_count:] if len(raw_candles) > warmup_count else raw_candles

        candles = [
            Candle(
                timestamp=datetime.fromisoformat(c["timestamp"].replace("+05:30", "")),
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
            )
            for c in warmup_raw
        ]

        self._session.warm_up(candles)
        logger.info(f"Warmed up with {len(candles)} candles.")

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def _connect_websocket(self) -> None:
        """Start the WebSocket ticker and register candle callback."""
        tokens = [self._nifty_token]
        if self._ce_token:
            tokens.append(self._ce_token)
        if self._pe_token:
            tokens.append(self._pe_token)

        self._ticker = LiveTicker(
            self._config.kite_api_key,
            self._kite._access_token,
        )
        self._ticker.on_candle_complete(self._on_candle)

        # Start in background thread
        ws_thread = self._ticker.start_threaded(tokens)
        if not self._ticker.wait_connected(timeout=30):
            raise RuntimeError("WebSocket failed to connect within 30 seconds")

        logger.info("WebSocket connected and streaming.")

    def _wait_for_market_close(self) -> None:
        """Block until past force-exit time, processing candles via callbacks."""
        # The ticker runs in a background thread and calls _on_candle.
        # This main thread just sleeps and checks for stop conditions.
        force_exit = self._config.session.force_exit_time
        shutdown_time = time(15, 20)

        while True:
            now = datetime.now().time()
            if now >= shutdown_time:
                logger.info("Past 15:20 — shutting down.")
                break
            if self._killed:
                logger.info("Daily loss limit hit — stopped.")
                break
            time_mod.sleep(1)

    # ------------------------------------------------------------------
    # Candle processing (called by ticker thread)
    # ------------------------------------------------------------------

    def _on_candle(self, token: int, candle: Candle) -> None:
        """Callback from LiveTicker when a 1-min candle completes."""
        if self._killed or self._session is None:
            return

        # We only process NIFTY spot candles for the strategy engine
        if token != self._nifty_token:
            return

        # Get current option premium from ticker LTP
        option_premium = self._get_current_option_premium()

        # Track state before processing
        self._was_active = self._session._position.is_active
        prev_state = self._session._position.position.state

        # Run strategy logic
        trade = self._session.process_candle(candle, option_premium)

        # Detect state transitions
        curr_state = self._session._position.position.state
        is_active_now = self._session._position.is_active

        # --- ORB complete notification ---
        if (
            self._session._orb.is_complete
            and self._session._breakout is not None
            and not hasattr(self, "_orb_notified")
        ):
            self._orb_notified = True
            self._notifier.orb_complete(
                self._session._orb.h3,
                self._session._orb.l3,
                self._config.session.orb_candles,
            )

        # --- Breakout notification ---
        if (
            self._session._breakout is not None
            and self._session._breakout.is_confirmed
            and not hasattr(self, "_breakout_notified")
        ):
            self._breakout_notified = True
            bo = self._session._breakout.breakout
            self._notifier.breakout_detected(
                bo.side.name, bo.h1, bo.l1
            )

        # --- Entry detected ---
        if not self._was_active and is_active_now:
            self._handle_entry(candle, option_premium)

        # --- Regime change A→B ---
        if (
            prev_state == PositionState.ACTIVE_REGIME_A
            and curr_state == PositionState.ACTIVE_REGIME_B
        ):
            pos = self._session._position.position
            sl = pos.premium_sl or 0.0
            self._notifier.regime_change("A", "B", sl)

        # --- Exit detected ---
        if trade is not None:
            self._handle_exit(trade)

        # --- Save state after every candle ---
        self._save_state()

    def _get_current_option_premium(self) -> Optional[float]:
        """Get the option premium based on which side we're watching."""
        if self._ticker is None:
            return None

        pos = self._session._position.position
        if pos.is_active:
            # Use the premium for the active side
            if pos.option_type == "CE" and self._ce_token:
                return self._ticker.get_ltp(self._ce_token)
            elif pos.option_type == "PE" and self._pe_token:
                return self._ticker.get_ltp(self._pe_token)

        # If not active, check if breakout detected to know which side
        if (
            self._session._breakout is not None
            and self._session._breakout.is_confirmed
        ):
            side = self._session._breakout.breakout.side
            if side == Side.CALL and self._ce_token:
                return self._ticker.get_ltp(self._ce_token)
            elif side == Side.PUT and self._pe_token:
                return self._ticker.get_ltp(self._pe_token)

        # Before breakout, return CE premium as default (won't be used for entry)
        if self._ce_token:
            return self._ticker.get_ltp(self._ce_token)
        return None

    # ------------------------------------------------------------------
    # Entry / Exit handlers
    # ------------------------------------------------------------------

    def _handle_entry(self, candle: Candle, option_premium: Optional[float]) -> None:
        """Place a buy order when TradingSession signals entry."""
        pos = self._session._position.position
        side = pos.side
        symbol = pos.option_symbol
        premium = option_premium or pos.entry_premium
        qty = self._config.market.lot_size * self._lots

        # Price buffer for limit fills (add 1 point for buy)
        limit_price = premium + 1.0

        order_id = self._order_mgr.buy_option(
            symbol=symbol, qty=qty, limit_price=limit_price
        )
        self._pending_entry_order = order_id

        # In paper mode, immediately fill at the premium
        if self._paper_mode:
            self._order_mgr.paper_fill(order_id, premium)

        self._notifier.entry_placed(
            side=side.name if side else "?",
            symbol=symbol,
            premium=premium,
            qty=qty,
            order_id=order_id,
        )
        logger.info(f"Entry order placed: {order_id} for {symbol} @ {premium:.2f}")

    def _handle_exit(self, trade: TradeRecord) -> None:
        """Place a sell order when TradingSession signals exit."""
        symbol = trade.option_symbol
        qty = self._config.market.lot_size * self._lots

        order_id = self._order_mgr.sell_option(
            symbol=symbol, qty=qty, market=True
        )
        self._pending_exit_order = order_id

        # In paper mode, fill at the exit premium
        if self._paper_mode:
            self._order_mgr.paper_fill(order_id, trade.exit_premium)

        # Track trade
        self._daily_trades.append(trade)
        self._net_pnl_today += trade.gross_pnl

        self._notifier.exit_signal(
            reason=trade.exit_reason.name,
            symbol=symbol,
            entry_premium=trade.entry_premium,
            exit_premium=trade.exit_premium,
            gross_pnl=trade.gross_pnl,
            order_id=order_id,
        )
        logger.info(
            f"Exit order placed: {order_id} for {symbol}, "
            f"reason={trade.exit_reason.name}, P&L=₹{trade.gross_pnl:.0f}"
        )

        # Check daily loss kill switch
        if self._net_pnl_today <= -self._max_daily_loss:
            logger.warning(
                f"Daily loss limit hit: ₹{self._net_pnl_today:.0f} "
                f"(limit: -₹{self._max_daily_loss:.0f})"
            )
            self._notifier.warning(
                f"Daily loss limit hit: ₹{self._net_pnl_today:.0f}. "
                f"Stopping trading."
            )
            self._killed = True

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save current session state for crash recovery."""
        if self._session is None:
            return

        pos = self._session._position.position
        breakout = pos.breakout

        self._state.save(
            has_position=pos.is_active,
            side=pos.side.name if pos.side else "",
            entry_premium=pos.entry_premium,
            strike=pos.strike,
            option_symbol=pos.option_symbol,
            option_type=pos.option_type,
            regime="B" if pos.state == PositionState.ACTIVE_REGIME_B else "A",
            premium_sl=pos.premium_sl,
            highest_premium_gain=pos.highest_premium_gain,
            last_ladder_idx=pos.last_triggered_ladder_idx,
            underlying_at_entry=pos.underlying_at_entry,
            h3=breakout.h3 if breakout else 0.0,
            l3=breakout.l3 if breakout else 0.0,
            h1=breakout.h1 if breakout else 0.0,
            l1=breakout.l1 if breakout else 0.0,
            call_entries=pos.call_entries_today,
            put_entries=pos.put_entries_today,
            trades_today=[
                {
                    "trade_id": t.trade_id,
                    "side": t.side.name,
                    "entry_premium": t.entry_premium,
                    "exit_premium": t.exit_premium,
                    "gross_pnl": t.gross_pnl,
                    "exit_reason": t.exit_reason.name,
                }
                for t in self._daily_trades
            ],
            net_pnl_today=self._net_pnl_today,
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        """Clean up: flush candles, disconnect WebSocket, log summary."""
        if self._ticker:
            self._ticker.flush_open_candles()
            self._ticker.stop()

        # Daily summary
        total_gross = sum(t.gross_pnl for t in self._daily_trades)
        total_charges = sum(t.charges for t in self._daily_trades)
        total_net = total_gross - total_charges

        logger.info(f"{'='*60}")
        logger.info(f"Daily summary — {self._today}")
        logger.info(f"Trades: {len(self._daily_trades)}")
        logger.info(f"Gross P&L: ₹{total_gross:.0f}")
        logger.info(f"Net P&L: ₹{total_net:.0f}")
        logger.info(f"{'='*60}")

        self._notifier.daily_summary(
            num_trades=len(self._daily_trades),
            net_pnl=total_net,
            gross_pnl=total_gross,
            charges=total_charges,
        )

        # Clear state file at end of day
        self._state.clear()
