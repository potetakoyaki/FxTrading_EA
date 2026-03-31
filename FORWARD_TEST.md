# AntigravityMTF EA Gold -- Forward Test Plan

## 1. Overview

**EA Version**: AntigravityMTF_EA_Gold v12.3 (MQ5) / backtest v7.0 (Python)
**Symbol**: XAUUSD (Gold) M15
**Backtest Reference**:
- Full period: PF=1.70, WR=67%, DD=7.3%, Sharpe=3.68
- Unknown data (2025-26): PF=1.51
- Spread-adjusted: PF=1.63
- WFA: 11/14 (79%), PF>=1.0 base: 12/14 (86%)
- Monte Carlo: 100% profit probability, PF 5th percentile=1.75

**IMPORTANT**: Python backtest uses pseudo-M15 data (interpolated from H1) prior to 2021-12.
Forward testing on live data is the only way to validate real-world performance.

---

## 2. Demo Account Setup

### 2.1 Recommended Brokers (Low Spread Gold ECN)

| Broker | Typical XAUUSD Spread | Account Type | Notes |
|--------|----------------------|--------------|-------|
| IC Markets | 5-10 points | Raw Spread | Recommended. Lowest Gold spreads. |
| Pepperstone | 6-12 points | Razor | Good execution, low slippage. |
| Exness | 7-16 points | Raw Spread | Flexible leverage, fast execution. |
| Tickmill | 8-15 points | Pro | Competitive commissions. |
| FPMarkets | 8-14 points | Raw | Reliable MT5 platform. |

**Requirements**:
- MT5 platform (NOT MT4 -- this EA is MQ5 only)
- ECN/Raw Spread account (avoid Standard accounts with markup)
- XAUUSD spread consistently under MaxSpread=50 points
- Hedge mode enabled (not netting mode)
- Leverage: 1:100 or higher for Gold
- Demo balance: 300,000 JPY (or USD equivalent for testing)

### 2.2 Demo Account Creation

1. Open a demo account at your chosen broker
2. Select MT5 platform, ECN/Raw account type
3. Set initial balance to match backtest assumptions (300,000 JPY)
4. Ensure XAUUSD is available in the symbol list
5. Set chart max bars to **Unlimited** in Tools -> Options -> Charts

---

## 3. EA Compilation in MetaEditor

### 3.1 File Placement

```
<MT5 Data Folder>/
  MQL5/
    Experts/
      AntigravityMTF_EA_Gold.mq5    <-- Copy here
    Files/
      (trade history CSV exports will appear here)
    Scripts/
      ExportHistory.mq5              <-- Optional: for data export
```

To find the data folder: In MT5, go to **File -> Open Data Folder**.

### 3.2 Compilation Steps

1. Open MetaEditor (press F4 in MT5, or from Start Menu)
2. File -> Open -> navigate to `MQL5/Experts/AntigravityMTF_EA_Gold.mq5`
3. Press **F7** (or Compile button) to compile
4. Check the **Errors** tab at the bottom:
   - **0 errors**: Compilation successful
   - If errors appear: check that `#include <Trade/Trade.mqh>` is available (standard library)
5. The compiled `.ex5` file will appear in the same folder
6. Switch back to MT5 -- the EA should now appear in the Navigator panel under **Expert Advisors**

### 3.3 Compilation Troubleshooting

- **"Trade.mqh not found"**: Ensure your MT5 installation has the standard library. Reinstall MT5 if missing.
- **Encoding errors**: Save the file as UTF-8 with BOM if Japanese comments cause issues.

---

## 4. Recommended Input Settings

### 4.1 CRITICAL: Settings That Differ Between Python Backtest and MQ5 Defaults

The MQ5 file (v12.0) has older default values in some input parameters.
The Python backtest (v7.0/v8.0) evolved these values through WFA optimization.
v12.4ではMQ5のデフォルト値がWFA検証済み値に統一されました。
**追加の設定変更は不要です** — デフォルトのまま使用してください。

変更可能なinputパラメータ（47個）のうち、主要なものは以下の通りです：

| Parameter | Default (=WFA値) | 説明 |
|-----------|-------------------|------|
| **RiskPercent** | 0.75 | 基本リスク% |
| **SL_ATR_Multi** | 1.2 | SL = ATR × 1.2 |
| **TP_ATR_Multi** | 4.0 | TP = ATR × 4.0 |
| **BE_ATR_Multi** | 0.8 | 建値移動 = ATR × 0.8 |
| **MinEntryScore** | 12 | 最低エントリースコア |
| **CooldownMinutes** | 480 | SL後クールダウン(8時間) |
| **MaxPyramidPositions** | 1 | ピラミッド無効(DD削減) |
| **Trend_SL_Widen** | 1.5 | 順トレンドSL倍率 |
| **Trend_SL_Tighten** | 0.6 | 逆トレンドSL倍率 |
| **GMTOffset** | 2 | ブローカーGMTオフセット |

以下のパラメータはconst化済み（WFA検証値で固定）：
- UseRSIMomentumConfirm=true, RSIMomentumLookback=3
- HighVol_SL_Bonus=0.0, UseVolumeClimax=false
- Chandelier_ATR_Multi=2.0, Kelly_MaxRisk=1.5

### 4.2 Settings to Keep at MQ5 Defaults

These parameters match between Python and MQ5, or the MQ5-specific features
(v9-v12) have their own tested defaults:

- ATR_Period_SL = 14
- Trail_ATR_Multi = 1.0
- MinSL_Points = 200, MaxSL_Points = 1500
- VolRegime_Period = 50, VolRegime_Low = 0.7, VolRegime_High = 1.5
- All H4/H1/M15 MA/RSI/BB periods (unchanged)
- TradeStartHour = 8, TradeEndHour = 22
- UsePartialClose = true, PartialCloseRatio = 0.5, PartialTP_Ratio = 0.5
- UseCorrelation = true (with USDJPY)
- UseDivergence = true
- UseSRLevels = true
- UseCandlePatterns = true
- UseH4RSI = true
- UseChandelierExit = true
- UseEquityCurveFilter = true
- UseAdaptiveSizing = true
- UseNewsFilter = true
- UseWeekendClose = true
- UseMomentumBurst = true
- UseReversalMode = true
- UseRegimeAdaptive = true
- UseSessionRegime = true
- UseAdaptiveExit = true
- UseV11Range = true
- MagicNumber = 20260224

### 4.3 Settings Input Summary (Copy-Paste Ready)

```
=== Risk Management ===
RiskPercent        = 0.75
MaxLots            = 0.50
MinLots            = 0.01
MaxSpread          = 50
MaxPositions       = 1
MagicNumber        = 20260224
MaxDrawdownPct     = 6.0
DDHalfRiskPct      = 2.5

=== Dynamic SL/TP (ATR) ===
ATR_Period_SL      = 14
SL_ATR_Multi       = 1.2
TP_ATR_Multi       = 4.0
Trail_ATR_Multi    = 1.0
BE_ATR_Multi       = 0.8
MinSL_Points       = 200
MaxSL_Points       = 1500

=== Volatility Regime ===
VolRegime_Period   = 50
VolRegime_Low      = 0.7
VolRegime_High     = 1.5
HighVol_SL_Bonus   = 0.0

=== Scoring ===
MinEntryScore      = 12
UseSessionBonus    = true
UseMomentum        = true

=== Time Filter ===
TradeStartHour     = 8
TradeEndHour       = 22
AvoidFriday        = true
CooldownMinutes    = 480

=== v4.0 Attack ===
UseMomentumBurst   = true
UseVolumeClimax    = false
MaxPyramidPositions = 1
PyramidLotDecay    = 0.5
UseReversalMode    = true

=== Trend SL/TP ===
Trend_SL_Widen     = 1.5
Trend_SL_Tighten   = 0.6

=== Chandelier Exit ===
Chandelier_ATR_Multi = 2.0

=== Adaptive Sizing ===
Kelly_MaxRisk      = 1.5

=== RSI Momentum ===
UseRSIMomentumConfirm = true
RSIMomentumLookback   = 3
```

### 4.4 Note on MQ5 v9-v12 Features

The MQ5 file includes features (v9.0 Regime Adaptive, v10.0 Session x Regime,
v11.0 Range Market Guard) that were developed in MQ5 and have their own
default parameter sets. These features were not part of the Python WFA
validation. Keep them at MQ5 defaults and monitor their contribution.
If performance diverges significantly from backtest expectations, consider
disabling these newer features (UseRegimeAdaptive=false, UseSessionRegime=false,
UseV11Range=false) to fall back to the WFA-validated core.

---

## 5. Attaching the EA

1. Open an **XAUUSD M15** chart in MT5
2. Drag the EA from Navigator -> Expert Advisors onto the chart
3. In the settings dialog:
   - **Common** tab: Check "Allow Algo Trading"
   - **Inputs** tab: Apply all settings from Section 4.3 above
4. Click **OK**
5. Verify: The EA name should appear in the top-right corner of the chart with a smiley face
6. Ensure **Algo Trading** button on the toolbar is enabled (green icon)

---

## 6. What to Monitor

### 6.1 Daily Checks (1 minute)

- EA is still running (smiley face visible on chart)
- No disconnection warnings in Journal tab
- Check Experts tab for any error messages

### 6.2 Weekly Review (15 minutes)

Export trade history and run the tracker:

```bash
# Export trades from MT5: Account History tab -> right-click -> Export
# Save as CSV to a known location, then:

python forward_test_tracker.py --csv trades.csv --report weekly
```

Review:
- Weekly PnL positive or within normal variance
- No ALERT messages from deviation check
- Trade count roughly 4-5 per week (annualized ~50-60/quarter)
- Spread in executed trades under 50 points

### 6.3 Monthly Review (30 minutes)

```bash
python forward_test_tracker.py --csv trades.csv --report monthly
python forward_test_tracker.py --csv trades.csv --report summary
```

Compare monthly metrics against backtest expectations:
- PF should be trending toward 1.5+
- Win rate should be 55%+
- Max DD should stay under 10%
- Trade frequency should be 15-20/month

### 6.4 3-Month Review (Go/No-Go Decision)

```bash
python forward_test_tracker.py --csv trades.csv --report review
```

---

## 7. When to Stop the Test

### 7.1 Immediate Stop Conditions

Stop the EA immediately if any of these occur:

- **Drawdown exceeds 15%** of initial balance
- **10+ consecutive losing trades** (backtest max was ~5-6)
- **EA stops trading for 2+ weeks** (no signals at all)
- **Broker changes conditions** (spread permanently above 50, leverage reduced)
- **Critical error** in Experts tab (handle errors, connection failures)

### 7.2 Pause and Investigate

Pause and review (but don't necessarily stop) if:

- PF drops below 1.0 after 30+ trades
- Win rate below 45% after 30+ trades
- Monthly loss exceeds 5% of balance
- Trade frequency is 2x higher or lower than expected

---

## 8. Success/Failure Criteria (3-Month Test)

### 8.1 Success (Proceed to Live)

ALL of the following must be true:

- [ ] PF >= 1.14 (at least 70% of backtest spread-adjusted PF 1.63)
- [ ] Win Rate >= 50%
- [ ] Max Drawdown <= 15%
- [ ] Net profit > 0 over the 3-month period
- [ ] Trade frequency: 30-100 trades per quarter
- [ ] No single loss exceeds 3% of balance
- [ ] Max consecutive losses < 8

### 8.2 Conditional Pass (Extend Test)

If 5 out of 7 criteria pass:
- Extend test by 1-2 months
- Review the failing criteria
- Consider parameter adjustment only if clearly justified

### 8.3 Failure (Do Not Go Live)

If 3 or more criteria fail:
- Do not proceed to live trading
- Analyze which market conditions caused failures
- Consider whether EA design changes are needed
- Re-run backtest with latest data to check for regime shift

---

## 9. Risk Management Rules

### 9.1 Position Sizing

- **Risk per trade**: 0.75% of account balance (adaptive via Half-Kelly)
- **Max lot size**: 0.50 lots
- **Min lot size**: 0.01 lots
- **Kelly range**: 0.1% to 1.5% (capped by adaptive sizing)

### 9.2 Drawdown Controls (Built into EA)

- DD > 2.5%: Risk halved automatically
- DD > 6%: MIN_SCORE escalated to 11 (fewer trades)
- DD > 10%: MIN_SCORE escalated to 13
- DD > 15%: MIN_SCORE escalated to 16
- DD > 20%: MIN_SCORE escalated to 18 (near-stop)

### 9.3 Additional Forward Test Rules

- **Do NOT change parameters** during the test unless stopping criteria are met
- **Do NOT manually close trades** -- let the EA manage all exits
- **Do NOT add other EAs** to the same account during the test
- **Log every parameter change** with date and reason if adjustments are made
- **Take screenshots** of the Experts tab weekly for audit trail

### 9.4 Go-Live Transition

When the 3-month test passes:
1. Start with **minimum lot size (0.01)** on a live account for 2 weeks
2. Gradually increase to target risk (0.75%) over 1 month
3. Continue running the tracker on live trades
4. Set hard stop-loss at account level: 20% max DD on live
5. Never risk more than you can afford to lose

---

## 10. Data Export for Tracker

### 10.1 From MT5 Account History

1. In MT5, go to the **Account History** tab (bottom panel)
2. Right-click -> **Custom Period** -> set start date to test start
3. Right-click -> **Report** -> **CSV** (or **Export**)
4. Save to a known location
5. Run the tracker as described in Section 6

### 10.2 Automated Export (Optional)

The `ExportHistory.mq5` script in this repository can export price data.
For trade history, use MT5's built-in export or a custom trade logger EA.

---

## Appendix: Backtest Data Limitations

The Python backtester used M15 data that is **pseudo-data** (linearly
interpolated from H1) for dates prior to December 2021. This means:

- Backtest results before 2022 are likely **overstated**
- The "unknown data" test (2025-26, PF=1.51) is the most reliable reference
- Forward testing on live tick data is the definitive validation
- MT5 Strategy Tester with real tick data is the next-best alternative

The 20% deviation threshold in the tracker accounts for this uncertainty.
