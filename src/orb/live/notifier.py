"""Telegram notification alerts for live trading."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import quote_plus
import json

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Send alerts via Telegram Bot API.

    If ``bot_token`` or ``chat_id`` are empty, all send calls are silently
    skipped (useful for paper trading without Telegram configured).
    """

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        if not self._enabled:
            logger.info("Telegram notifier disabled (no token/chat_id).")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # High-level alert methods
    # ------------------------------------------------------------------

    def session_started(self, config_summary: str) -> None:
        self._send(f"🟢 *Session started*\n{config_summary}")

    def orb_complete(self, h3: float, l3: float, num_candles: int) -> None:
        self._send(
            f"📊 *ORB complete* ({num_candles} candles)\n"
            f"H3 = {h3:.2f}  |  L3 = {l3:.2f}"
        )

    def breakout_detected(self, side: str, h1: float, l1: float) -> None:
        emoji = "🟩" if side == "CALL" else "🟥"
        self._send(
            f"{emoji} *Breakout: {side}*\n"
            f"H1 = {h1:.2f}  |  L1 = {l1:.2f}"
        )

    def entry_placed(
        self, side: str, symbol: str, premium: float, qty: int, order_id: str
    ) -> None:
        self._send(
            f"📥 *Entry: {side}*\n"
            f"Symbol: `{symbol}`\n"
            f"Premium: {premium:.2f}  |  Qty: {qty}\n"
            f"Order: `{order_id}`"
        )

    def regime_change(self, from_regime: str, to_regime: str, premium_sl: float) -> None:
        self._send(
            f"🔄 *Regime {from_regime} → {to_regime}*\n"
            f"Premium SL: {premium_sl:.2f}"
        )

    def exit_signal(
        self,
        reason: str,
        symbol: str,
        entry_premium: float,
        exit_premium: float,
        gross_pnl: float,
        order_id: str = "",
    ) -> None:
        pnl_emoji = "✅" if gross_pnl >= 0 else "❌"
        self._send(
            f"📤 *Exit: {reason}* {pnl_emoji}\n"
            f"Symbol: `{symbol}`\n"
            f"Entry: {entry_premium:.2f} → Exit: {exit_premium:.2f}\n"
            f"Gross P&L: ₹{gross_pnl:.0f}\n"
            f"Order: `{order_id}`"
        )

    def force_exit(self, symbol: str, premium: float) -> None:
        self._send(
            f"⏰ *Force exit (15:15)*\n"
            f"Symbol: `{symbol}` @ {premium:.2f}"
        )

    def daily_summary(
        self, num_trades: int, net_pnl: float, gross_pnl: float, charges: float
    ) -> None:
        emoji = "📈" if net_pnl >= 0 else "📉"
        self._send(
            f"{emoji} *Daily Summary*\n"
            f"Trades: {num_trades}\n"
            f"Gross P&L: ₹{gross_pnl:.0f}\n"
            f"Charges: ₹{charges:.0f}\n"
            f"Net P&L: ₹{net_pnl:.0f}"
        )

    def error(self, message: str) -> None:
        self._send(f"🚨 *ERROR*\n{message}")

    def warning(self, message: str) -> None:
        self._send(f"⚠️ *WARNING*\n{message}")

    def info(self, message: str) -> None:
        self._send(f"ℹ️ {message}")

    # ------------------------------------------------------------------
    # Low-level send
    # ------------------------------------------------------------------

    def _send(self, text: str) -> None:
        """Send a Markdown-formatted message via Telegram Bot API."""
        if not self._enabled:
            logger.debug(f"[TELEGRAM-DISABLED] {text}")
            return

        url = _TELEGRAM_API.format(token=self._bot_token)
        payload = json.dumps({
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }).encode("utf-8")

        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Telegram API returned {resp.status}")
        except Exception:
            logger.exception("Failed to send Telegram notification")
