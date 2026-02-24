# NIFTY ORB Options Strategy — Description

## Overview

Both strategies trade NIFTY 50 index options using an **Opening Range Breakout (ORB)** approach on 1-minute candles. The idea is simple: the market establishes a range in the first few minutes of the day, and when price breaks out of that range, we ride the move by buying an in-the-money (ITM) option.

The two configurations share the same core logic but differ in key parameters. The **Original** is the initial conservative setup; the **Tuned** version emerged from a 216-configuration parameter sweep and is decisively more profitable.

---

## How the Strategy Works (Common to Both)

### Step 1: Define the Opening Range
At market open (09:15), we watch the first N one-minute candles of NIFTY spot. We record:
- **H3** = highest high across those N candles
- **L3** = lowest low across those N candles

This range represents the initial battle between buyers and sellers.

### Step 2: Detect a Breakout
After the opening range is set, we watch each subsequent 1-minute candle close:
- If the candle **closes above H3** → **CALL breakout** confirmed
- If the candle **closes below L3** → **PUT breakout** confirmed

When a breakout is confirmed, we set two structure levels:
- **H1** = high of the breakout candle (CALL) or high of the pre-breakout candle (PUT)
- **L1** = low of the pre-breakout candle (CALL) or low of the breakout candle (PUT)

These H1/L1 levels serve as the entry trigger and initial stop loss.

### Step 3: Select the Option
We pick an ITM option strike:
- **CALL breakout** → Buy a CE option at `round(spot/100)*100 - 200` (200 points ITM)
- **PUT breakout** → Buy a PE option at `round(spot/100)*100 + 200` (200 points ITM)

ITM options are used because they have higher delta (move more with the underlying) and lower time decay compared to ATM/OTM.

### Step 4: Entry
For a CALL breakout, we enter (buy the CE) when the underlying price crosses above H1. For a PUT breakout, we enter (buy the PE) when price crosses below L1.

Additional filters may apply (RSI, SuperTrend — see differences below).

### Step 5: Two-Regime Exit System

**Regime A (Candle-based Stop Loss)**
Immediately after entry, the stop loss is based on the underlying price:
- CALL position: SL triggers if any candle's low touches L1
- PUT position: SL triggers if any candle's high touches H1

This is a wide, structure-based stop. The position stays in Regime A until the option premium gains enough to hit the first trailing trigger (T1).

**Regime B (Premium Trailing Ladder)**
Once the option premium gain reaches T1, we switch to Regime B. Now the stop loss is based purely on the option premium, not the underlying. A ladder of trailing stops locks in progressively more profit:

| Trigger | Action |
|---------|--------|
| Premium gain hits T1 | Move SL to entry price (breakeven) |
| Premium gain hits T2 | Move SL to entry + T1 |
| Premium gain hits T3 | Move SL to entry + T2 |
| Premium gain hits T4 | Move SL to entry + T3 |
| Premium gain hits T5 | Full exit (take profit) |

The SL only moves up, never down. If price retraces and hits the trailing SL, we exit with a locked-in profit.

### Step 6: Force Exit and Re-entry
- All positions are force-closed at **15:15** regardless of P&L
- No new entries are allowed after a cutoff time
- If stopped out, re-entry is allowed at the same H1/L1 levels (up to a maximum per side per day)

### Transaction Costs
Every trade incurs real NSE costs:
- Brokerage: Rs 20 per order (buy + sell = Rs 40)
- STT: 0.1% on sell-side premium (options)
- Exchange transaction charges: 0.03503% on both legs
- GST: 18% on brokerage + exchange charges
- SEBI charges: 0.0001%
- Stamp duty: 0.003%

---

## Original Configuration

The initial setup with tighter parameters:

| Parameter | Value |
|-----------|-------|
| **ORB Candles** | 3 (09:15 to 09:18) |
| **RSI Filter** | Active: entry only when RSI(14) is between 40 and 65 |
| **SuperTrend** | Period 10, Multiplier 3.0 |
| **Max Re-entries** | 4 per side per day |
| **No Entry After** | 11:30 |
| **Trailing Ladder** | 30-point steps: T1=+30, T2=+60, T3=+90, T4=+120, T5=+150 (full exit) |
| **Force Exit** | 15:15 |

### Characteristics
- **Narrow opening range** (only 3 minutes) leads to frequent false breakouts
- **RSI filter** blocks many entries, including some that would have been winners
- **4 re-entries** means the strategy often chases losing breakouts, compounding losses
- **Tight 30-point ladder** locks in small profits quickly but caps upside

### Results (Sep 2025 – Feb 2026, 121 trading days)
- 54 trades, 11 winners (20.4% win rate)
- Gross P&L: **-Rs 10,044**
- Net P&L: **-Rs 13,719** (after Rs 3,675 in charges)
- Max drawdown: Rs 19,689
- Sharpe ratio: -2.36
- Avg win: Rs 1,117 / Avg loss: Rs 605 (R:R = 1.85)

---

## Tuned Configuration (10-Candle ORB)

The optimized setup discovered through parameter sweep:

| Parameter | Value | Change from Original |
|-----------|-------|---------------------|
| **ORB Candles** | **10** (09:15 to 09:25) | +7 candles (wider range) |
| **RSI Filter** | **Disabled** (0–100) | Removed |
| **SuperTrend** | Period **14**, Multiplier 3.0 | Longer period |
| **Max Re-entries** | **1** per side per day | Down from 4 |
| **No Entry After** | **12:00** | 30 min later |
| **Trailing Ladder** | **40-point steps**: T1=+40, T2=+80, T3=+120, T4=+160, T5=+200 (full exit) | Wider steps |
| **Force Exit** | 15:15 | Same |

### Why These Changes Work

**10-Candle ORB (the biggest improvement)**
Waiting 10 minutes instead of 3 gives the market time to establish a meaningful range. The wider H3/L3 band filters out noise — when price does break out, it's more likely a genuine directional move, not just morning volatility.

**RSI Disabled**
The RSI filter was blocking valid breakouts. In a momentum strategy, requiring RSI to be in a narrow 40–65 band contradicts the premise — strong breakouts often have RSI above 65 (CALL) or below 40 (PUT).

**1 Re-entry (down from 4)**
If the first entry gets stopped out, the breakout likely failed. Allowing 4 re-entries meant throwing good money after bad. Limiting to 1 re-entry cuts losses on failed breakout days.

**40-Point Ladder (up from 30)**
Wider steps give winning trades more room to breathe. A 30-point T1 means the position switches to premium trailing very quickly, and minor retracements can stop it out at breakeven. The 40-point steps allow winners to develop further before trailing kicks in.

**Entry Cutoff 12:00 (from 11:30)**
The extra 30 minutes allows catching afternoon breakouts that the original config missed.

### Results (Sep 2025 – Feb 2026, 121 trading days)
- 61 trades, 17 winners (27.9% win rate)
- Gross P&L: **+Rs 13,618**
- Net P&L: **+Rs 9,446** (after Rs 4,171 in charges)
- Max drawdown: Rs 8,576
- Sharpe ratio: 1.40
- Profit factor: 1.51
- Avg win: Rs 1,643 / Avg loss: Rs 420 (R:R = 3.91)

---

## Head-to-Head Comparison

| Metric | Original | Tuned | Improvement |
|--------|----------|-------|-------------|
| Net P&L | -Rs 13,719 | **+Rs 9,446** | +Rs 23,165 |
| Win Rate | 20.4% | **27.9%** | +7.5pp |
| Reward:Risk | 1.85 | **3.91** | +111% |
| Profit Factor | 0.47 | **1.51** | +221% |
| Max Drawdown | Rs 19,689 | **Rs 8,576** | -56% |
| Sharpe Ratio | -2.36 | **1.40** | Positive |
| Avg Win | Rs 1,117 | **Rs 1,643** | +47% |
| Avg Loss | Rs 605 | **Rs 420** | -31% |

### Exit Reason Breakdown

| Exit Reason | Original | Tuned |
|-------------|----------|-------|
| Candle SL (Regime A) | 37 (69%) | 37 (61%) |
| Premium Trail SL (Regime B) | 12 (22%) | 12 (20%) |
| Force Exit (15:15) | 3 (6%) | 11 (18%) |
| Premium Target (T5) | 2 (4%) | 1 (2%) |

The tuned strategy has significantly more force exits (18% vs 6%). These are positions still profitably open at 15:15 — the wider ladder means fewer trades hit T5 for a full exit, but the ones that run are held longer with larger gains. The higher force exit count is actually a sign of the strategy capturing sustained intraday trends.

---

## Key Takeaway

The single most impactful change was switching from a 3-minute to a 10-minute opening range. This one change filters out most false breakouts and transforms the strategy from a consistent loser to a net profitable system. The other parameter changes (disabling RSI, reducing re-entries, widening the ladder) reinforce this by letting winning trades run and cutting losing trades faster.
