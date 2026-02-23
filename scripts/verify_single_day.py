#!/usr/bin/env python3
"""Verification script: traces every decision on a single trading day.

Outputs raw candle data + every strategy decision so you can manually verify
that the code implements the strategy correctly.

Usage:
    python scripts/verify_single_day.py [YYYY-MM-DD] [orb_candles]
    python scripts/verify_single_day.py 2026-01-15 10
"""
import sys, os, logging, copy
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from orb.config import load_config, TrailingStep
from orb.data.db import Database
from orb.data.instruments import InstrumentResolver
from orb.backtest.broker_sim import BrokerSimulator
from orb.indicators.rsi import RSI
from orb.indicators.supertrend import SuperTrend
from orb.strategy.opening_range import OpeningRangeDetector
from orb.strategy.breakout import BreakoutDetector
from orb.strategy.entry import EntrySignal
from orb.strategy.exit import ExitManager
from orb.strategy.session import TradingSession
from orb.models import Candle, Side, ExitReason


def load_candles_from_db(db, token, from_dt, to_dt):
    raw = db.get_candles(token, from_dt, to_dt, 'minute')
    return [Candle(
        timestamp=datetime.fromisoformat(r['timestamp']),
        open=r['open'], high=r['high'], low=r['low'], close=r['close'],
        volume=r.get('volume', 0),
    ) for r in raw]


def main():
    target_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    orb_n = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    db = Database('data/orb_data.db')
    resolver = InstrumentResolver(db)
    config = load_config('config/default_config.yaml')

    # Apply the best config from sweep3
    config.session.orb_candles = orb_n
    config.session.orb_end = time(9, 15 + orb_n)
    config.session.no_new_entry_after = time(12, 0)
    config.session.force_exit_time = time(15, 15)
    config.strategy.rsi_entry_min = 0
    config.strategy.rsi_entry_max = 100
    config.strategy.max_re_entries_per_side = 1
    config.strategy.supertrend_period = 14
    config.strategy.supertrend_multiplier = 3.0
    config.strategy.trailing_ladder = [
        TrailingStep(trigger=40, trail_to=0),
        TrailingStep(trigger=80, trail_to=40),
        TrailingStep(trigger=120, trail_to=80),
        TrailingStep(trigger=160, trail_to=120),
        TrailingStep(trigger=200, trail_to=-1),
    ]

    # Corrected costs
    config.backtest.stt_rate = 0.001
    config.backtest.exchange_txn_charge = 0.0003503
    config.backtest.stamp_duty = 0.00003
    config.backtest.sebi_charges = 0.000001

    nifty_token = resolver.get_nifty_spot_token()

    # Find a day with trades if none specified
    with db._connect() as conn:
        rows = conn.execute("""
            SELECT DISTINCT substr(timestamp, 1, 10) as dt
            FROM candles WHERE instrument_token = 256265 ORDER BY dt
        """).fetchall()
        all_days = [date.fromisoformat(r['dt']) for r in rows]

    if target_date and target_date in all_days:
        days_to_check = [target_date]
    elif target_date:
        print(f"Date {target_date} not in data. Available: {all_days[0]} to {all_days[-1]}")
        return
    else:
        days_to_check = all_days

    broker = BrokerSimulator(config.backtest)

    for td in days_to_check:
        day_from = f'{td} 09:15:00'
        day_to = f'{td} 15:30:00'

        underlying = load_candles_from_db(db, nifty_token, day_from, day_to)
        if not underlying:
            continue

        spot = underlying[0].open
        rounded = round(spot / config.market.strike_step) * config.market.strike_step
        call_strike = rounded - config.market.itm_offset
        put_strike = rounded + config.market.itm_offset
        expiry = resolver.get_nearest_expiry(td)

        option_candles = {}
        for strike, opt_type in [(call_strike, 'CE'), (put_strike, 'PE')]:
            token = resolver.get_option_token(strike, opt_type, expiry)
            if token:
                opt_list = load_candles_from_db(db, token, day_from, day_to)
                symbol = f'NIFTY{strike:.0f}{opt_type}'
                if opt_list:
                    option_candles[symbol] = opt_list

        # Get warmup from previous day
        prev_idx = all_days.index(td)
        warmup = None
        if prev_idx > 0:
            prev_day = all_days[prev_idx - 1]
            warmup = load_candles_from_db(db, nifty_token,
                                          f'{prev_day} 09:15:00', f'{prev_day} 15:30:00')
            if warmup:
                warmup = warmup[-config.strategy.warmup_candles:]

        # === NOW TRACE THE DAY ===
        session = TradingSession(config, datetime.combine(td, datetime.min.time()))
        for sym in option_candles:
            session.set_option_symbol(sym)
        if warmup:
            session.warm_up(warmup)

        print(f"\n{'='*100}")
        print(f"DATE: {td}")
        print(f"Spot open: {spot:.2f}, Rounded: {rounded:.0f}")
        print(f"CE strike: {call_strike:.0f}, PE strike: {put_strike:.0f}")
        print(f"CE candles: {len(option_candles.get(f'NIFTY{call_strike:.0f}CE', []))}")
        print(f"PE candles: {len(option_candles.get(f'NIFTY{put_strike:.0f}PE', []))}")
        print(f"ORB candles: {orb_n} (09:15 to 09:{15+orb_n:02d})")
        print(f"{'='*100}")

        # Pre-ORB indicator state
        rsi_val = session._rsi.value
        st_val = session._supertrend.value
        print(f"\nPre-day indicators (after warmup):")
        print(f"  RSI: {rsi_val:.2f}" if rsi_val else "  RSI: None")
        if st_val:
            st_key = 'supertrend' if 'supertrend' in st_val else 'value'
            print(f"  SuperTrend: dir={st_val['direction']}, value={st_val[st_key]:.2f}")
        else:
            print(f"  SuperTrend: None")

        day_had_trade = False

        print(f"\n--- Candle-by-Candle Trace ---")
        print(f"{'Time':<8s} {'Open':>10s} {'High':>10s} {'Low':>10s} {'Close':>10s} "
              f"{'RSI':>6s} {'ST_Dir':>6s} {'OptPrem':>8s} {'Event'}")
        print("-" * 100)

        for i, candle in enumerate(underlying):
            ct = candle.timestamp.time()

            # Get option premium (same logic as engine)
            option_premium = None
            pos = session._position.position
            if pos.is_active and pos.option_symbol:
                sym = pos.option_symbol
            elif session._breakout and session._breakout.is_confirmed:
                breakout = session._breakout.breakout
                ot = "CE" if breakout.side == Side.CALL else "PE"
                sym = None
                for s in option_candles:
                    if s.endswith(ot):
                        sym = s
                        break
            else:
                sym = None

            if sym and sym in option_candles:
                for oc in option_candles[sym]:
                    if oc.timestamp == candle.timestamp:
                        option_premium = oc.close
                        break

            # Process the candle
            trade = session.process_candle(candle, option_premium)

            # Get current indicator values
            rsi_v = session._rsi.value
            st_v = session._supertrend.value

            # Determine event
            events = []
            if not session._orb.is_complete and len(session._orb._candles) <= orb_n:
                events.append(f"ORB candle {len(session._orb._candles)}/{orb_n}")
            if session._orb.is_complete and i == orb_n - 1:
                events.append(f"ORB COMPLETE: H3={session._orb.h3:.2f}, L3={session._orb.l3:.2f}")
            if session._breakout and session._breakout.is_confirmed:
                bo = session._breakout.breakout
                if bo.confirmed_at == candle.timestamp:
                    events.append(f"BREAKOUT {bo.side.name}: H1={bo.h1:.2f}, L1={bo.l1:.2f}")
            if session._position.is_active:
                p = session._position.position
                regime = "B" if p.state.name == "ACTIVE_REGIME_B" else "A"
                gain = (option_premium - p.entry_premium) if option_premium else 0
                events.append(f"IN_POS({p.side.name} regime={regime} gain={gain:+.1f} sl={p.premium_sl})")
            if trade:
                day_had_trade = True
                events.append(
                    f"*** TRADE: {trade.side.name} entry={trade.entry_premium:.2f} "
                    f"exit={trade.exit_premium:.2f} gross={trade.gross_pnl:+.0f} "
                    f"reason={trade.exit_reason.name}"
                )

            rsi_str = f"{rsi_v:.1f}" if rsi_v else "  N/A"
            st_str = f"  {st_v['direction']:+d}" if st_v else "  N/A"
            opt_str = f"{option_premium:.1f}" if option_premium else "    N/A"

            # Only print interesting candles to keep output manageable
            show = (
                i < orb_n + 3  # ORB period + a few after
                or events  # Any event
                or trade  # Any trade
                or (session._position.is_active)  # While in position
                or ct >= time(15, 10)  # Near force exit
            )
            if show:
                event_str = " | ".join(events) if events else ""
                print(f"{str(ct):<8s} {candle.open:>10.2f} {candle.high:>10.2f} "
                      f"{candle.low:>10.2f} {candle.close:>10.2f} "
                      f"{rsi_str:>6s} {st_str:>6s} {opt_str:>8s} {event_str}")

            if trade:
                # Apply slippage and costs for display
                t = copy.copy(trade)
                t.entry_premium = broker.apply_slippage(t.entry_premium, is_buy=True)
                t.exit_premium = broker.apply_slippage(t.exit_premium, is_buy=False)
                t.gross_pnl = (t.exit_premium - t.entry_premium) * t.lot_size * t.lots
                broker.apply_costs(t)
                print(f"         After slippage: entry={t.entry_premium:.2f} exit={t.exit_premium:.2f}")
                print(f"         Gross={t.gross_pnl:+.2f}, Charges={t.charges:.2f}, Net={t.net_pnl:+.2f}")
                print(f"         Charges breakdown: brokerage={40:.0f}, "
                      f"STT={t.exit_premium * t.lot_size * 0.001:.2f}, "
                      f"exchange_txn={((t.entry_premium + t.exit_premium) * t.lot_size * 0.0003503):.2f}")
                print()

            if session.is_done:
                break

        if not day_had_trade:
            if not target_date:
                continue  # Skip days without trades when browsing
            print("\n  NO TRADES on this day.")

        # Summary
        trades = session.trades
        if trades:
            print(f"\n--- Day Summary ---")
            print(f"  Trades: {len(trades)}")
            for j, t in enumerate(trades):
                print(f"  #{j+1}: {t.side.name} {t.option_symbol} "
                      f"entry={t.entry_premium:.2f}@{t.entry_time.time()} "
                      f"exit={t.exit_premium:.2f}@{t.exit_time.time()} "
                      f"gross={t.gross_pnl:+.0f} reason={t.exit_reason.name}")

        if target_date:
            break  # Only trace requested day

        # If no target date, show first 3 days with trades then stop
        if day_had_trade:
            days_with_trades = sum(1 for _ in range(1))  # placeholder
            # Just show this one day as example
            print("\n(Showing first day with trades. Specify a date to see others.)")
            break

    db.close()
    print("\n\nVERIFICATION CHECKLIST:")
    print("=" * 60)
    print("1. ORB: Are H3/L3 the max high / min low of first N candles?")
    print("2. Breakout: Does price close above H3 (CALL) or below L3 (PUT)?")
    print("3. H1/L1: H1=breakout candle high, L1=previous candle low (CALL)?")
    print("4. Entry: Price crosses H1 (CALL) while SuperTrend bullish?")
    print("5. Regime A SL: Does underlying hit L1 (CALL) or H1 (PUT)?")
    print("6. Regime B: Does premium gain >= T1(40) trigger regime change?")
    print("7. Trailing SL: After T1, is SL at entry_premium + 0 (breakeven)?")
    print("8. Force exit: Is position closed at 15:15?")
    print("9. Charges: Brokerage 2x20=40, STT=0.1% sell, exchange=0.035% both?")


if __name__ == "__main__":
    main()
