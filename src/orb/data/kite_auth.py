"""Kite Connect session management — login URL, token exchange, persistence."""
from __future__ import annotations

import json
import os
from pathlib import Path

from kiteconnect import KiteConnect


_TOKEN_FILE = "data/.kite_access_token"


class KiteSession:
    """Wraps KiteConnect authentication lifecycle."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._kite = KiteConnect(api_key=api_key)
        self._access_token: str | None = None
        # Try to restore a previously saved token
        self._load_token()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_login_url(self) -> str:
        """Return the Kite login URL the user must open in a browser."""
        return self._kite.login_url()

    def generate_session(self, request_token: str) -> dict:
        """Exchange *request_token* for an access token and persist it.

        Returns the full session dict from Kite.
        """
        session_data: dict = self._kite.generate_session(
            request_token, api_secret=self._api_secret
        )
        self._access_token = session_data["access_token"]
        self._kite.set_access_token(self._access_token)
        self._save_token()
        return session_data

    def get_kite(self) -> KiteConnect:
        """Return an authenticated KiteConnect instance.

        Raises ``RuntimeError`` if no access token is available.
        """
        if not self.is_authenticated:
            raise RuntimeError(
                "Not authenticated. Call generate_session() with a valid request_token first."
            )
        return self._kite

    @property
    def is_authenticated(self) -> bool:
        return self._access_token is not None

    # ------------------------------------------------------------------
    # Token persistence helpers
    # ------------------------------------------------------------------

    def _save_token(self) -> None:
        """Persist access token to a local file for reuse across restarts."""
        if self._access_token is None:
            return
        path = Path(_TOKEN_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"access_token": self._access_token}))

    def _load_token(self) -> None:
        """Attempt to load a previously saved access token."""
        path = Path(_TOKEN_FILE)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            token = data.get("access_token")
            if token:
                self._access_token = token
                self._kite.set_access_token(token)
        except (json.JSONDecodeError, KeyError):
            # Corrupt file — ignore silently
            pass
