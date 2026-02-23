"""YAML config loader → dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv


@dataclass
class TrailingStep:
    trigger: float   # Premium gain (points) to activate this step
    trail_to: float  # Trail SL to this gain level; -1 means full exit


@dataclass
class MarketConfig:
    symbol: str = "NIFTY 50"
    exchange: str = "NSE"
    options_exchange: str = "NFO"
    lot_size: int = 25
    strike_step: int = 50
    itm_offset: int = 200


@dataclass
class SessionConfig:
    market_open: time = field(default_factory=lambda: time(9, 15))
    market_close: time = field(default_factory=lambda: time(15, 30))
    orb_candles: int = 3
    orb_end: time = field(default_factory=lambda: time(9, 18))
    no_new_entry_after: time = field(default_factory=lambda: time(11, 30))
    force_exit_time: time = field(default_factory=lambda: time(15, 15))


@dataclass
class StrategyConfig:
    max_positions: int = 1
    max_re_entries_per_side: int = 4
    candle_interval: str = "minute"
    rsi_period: int = 14
    rsi_entry_min: float = 40.0
    rsi_entry_max: float = 65.0
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    warmup_candles: int = 30
    trailing_ladder: List[TrailingStep] = field(default_factory=list)


@dataclass
class BacktestConfig:
    slippage_points: float = 2.0
    brokerage_per_order: float = 20.0
    stt_rate: float = 0.000625
    gst_rate: float = 0.18
    sebi_charges: float = 0.000001
    stamp_duty: float = 0.00003
    exchange_txn_charge: float = 0.00053


@dataclass
class ReportingConfig:
    risk_free_rate: float = 0.065
    output_dir: str = "output"


@dataclass
class AppConfig:
    market: MarketConfig = field(default_factory=MarketConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    kite_api_key: str = ""
    kite_api_secret: str = ""


def _parse_time(val: str) -> time:
    parts = val.split(":")
    return time(int(parts[0]), int(parts[1]))


def load_config(config_path: str | Path = "config/default_config.yaml") -> AppConfig:
    """Load YAML config and merge with environment variables."""
    load_dotenv()

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    mkt = raw.get("market", {})
    market = MarketConfig(
        symbol=mkt.get("symbol", "NIFTY 50"),
        exchange=mkt.get("exchange", "NSE"),
        options_exchange=mkt.get("options_exchange", "NFO"),
        lot_size=mkt.get("lot_size", 25),
        strike_step=mkt.get("strike_step", 50),
        itm_offset=mkt.get("itm_offset", 200),
    )

    sess = raw.get("session", {})
    session = SessionConfig(
        market_open=_parse_time(sess.get("market_open", "09:15")),
        market_close=_parse_time(sess.get("market_close", "15:30")),
        orb_candles=sess.get("orb_candles", 3),
        orb_end=_parse_time(sess.get("orb_end", "09:18")),
        no_new_entry_after=_parse_time(sess.get("no_new_entry_after", "11:30")),
        force_exit_time=_parse_time(sess.get("force_exit_time", "15:15")),
    )

    strat = raw.get("strategy", {})
    ladder_raw = strat.get("trailing_ladder", [])
    ladder = [TrailingStep(trigger=s["trigger"], trail_to=s["trail_to"]) for s in ladder_raw]
    strategy = StrategyConfig(
        max_positions=strat.get("max_positions", 1),
        max_re_entries_per_side=strat.get("max_re_entries_per_side", 4),
        candle_interval=strat.get("candle_interval", "minute"),
        rsi_period=strat.get("rsi_period", 14),
        rsi_entry_min=strat.get("rsi_entry_min", 40.0),
        rsi_entry_max=strat.get("rsi_entry_max", 65.0),
        supertrend_period=strat.get("supertrend_period", 10),
        supertrend_multiplier=strat.get("supertrend_multiplier", 3.0),
        warmup_candles=strat.get("warmup_candles", 30),
        trailing_ladder=ladder,
    )

    bt = raw.get("backtest", {})
    backtest = BacktestConfig(
        slippage_points=bt.get("slippage_points", 2.0),
        brokerage_per_order=bt.get("brokerage_per_order", 20.0),
        stt_rate=bt.get("stt_rate", 0.000625),
        gst_rate=bt.get("gst_rate", 0.18),
        sebi_charges=bt.get("sebi_charges", 0.000001),
        stamp_duty=bt.get("stamp_duty", 0.00003),
        exchange_txn_charge=bt.get("exchange_txn_charge", 0.00053),
    )

    rep = raw.get("reporting", {})
    reporting = ReportingConfig(
        risk_free_rate=rep.get("risk_free_rate", 0.065),
        output_dir=rep.get("output_dir", "output"),
    )

    return AppConfig(
        market=market,
        session=session,
        strategy=strategy,
        backtest=backtest,
        reporting=reporting,
        kite_api_key=os.getenv("KITE_API_KEY", ""),
        kite_api_secret=os.getenv("KITE_API_SECRET", ""),
    )
