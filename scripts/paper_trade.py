#!/usr/bin/env python3
"""Paper trading convenience script — equivalent to: live_trade.py --paper

Usage:
    python scripts/paper_trade.py [--config CONFIG_PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# Re-use the live_trade main with --paper forced
if __name__ == "__main__":
    # Inject --paper if not already present
    if "--live" not in sys.argv:
        sys.argv.append("--paper")
    from live_trade import main
    main()
