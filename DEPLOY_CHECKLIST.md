# Deployment Checklist -- AntigravityMTF EA Gold Forward Test

**Start Date**: _______________
**Broker / Account**: _______________
**Demo Balance**: _______________

---

## Phase 1: Setup

- [ ] Open demo account at ECN broker (IC Markets / Pepperstone / Exness recommended)
- [ ] Confirm MT5 platform (not MT4)
- [ ] Confirm hedge mode enabled (not netting)
- [ ] Set chart max bars to Unlimited (Tools -> Options -> Charts)
- [ ] Initial balance set to 300,000 JPY (or equivalent)

## Phase 2: Compilation

- [ ] Copy `AntigravityMTF_EA_Gold.mq5` to `MQL5/Experts/`
- [ ] Open in MetaEditor (F4)
- [ ] Compile with F7 -- **0 errors, 0 warnings** (warnings acceptable if cosmetic)
- [ ] EA appears in Navigator -> Expert Advisors

## Phase 3: Configuration

- [ ] Open XAUUSD M15 chart
- [ ] Drag EA onto chart
- [ ] **Apply ALL parameter overrides** (see FORWARD_TEST.md Section 4.3):
  - [ ] RiskPercent = 0.75 (MQ5 default: 0.3)
  - [ ] SL_ATR_Multi = 1.2 (MQ5 default: 1.5)
  - [ ] TP_ATR_Multi = 4.0 (MQ5 default: 3.5)
  - [ ] BE_ATR_Multi = 0.8 (MQ5 default: 1.5)
  - [ ] HighVol_SL_Bonus = 0.0 (MQ5 default: 0.5)
  - [ ] MinEntryScore = 12 (MQ5 default: 9)
  - [ ] CooldownMinutes = 480 (MQ5 default: 240)
  - [ ] MaxPositions = 1 (MQ5 default: 3)
  - [ ] MaxPyramidPositions = 1 (MQ5 default: 3)
  - [ ] UseVolumeClimax = false (MQ5 default: true)
  - [ ] Chandelier_ATR_Multi = 2.0 (MQ5 default: 3.0)
  - [ ] Kelly_MaxRisk = 1.5 (MQ5 default: 1.0)
  - [ ] Trend_SL_Widen = 1.5 (MQ5 default: 1.3)
  - [ ] Trend_SL_Tighten = 0.6 (MQ5 default: 0.7)
  - [ ] UseRSIMomentumConfirm = true
  - [ ] RSIMomentumLookback = 3
- [ ] Common tab: "Allow Algo Trading" checked
- [ ] Click OK
- [ ] Algo Trading button enabled on toolbar (green icon)
- [ ] Smiley face visible on chart (EA running)

## Phase 4: Verification (Week 1)

- [ ] EA is running without errors (check Experts tab daily)
- [ ] No disconnection warnings in Journal tab
- [ ] **Verify first trade within 1 week** of attachment
  - If no trade after 7 days: check spread (must be < 50), check trading hours (8-22 UTC), check Experts tab for "score" log messages
- [ ] First trade parameters look correct (SL/TP distances match ATR-based expectations)
- [ ] Screenshot the first trade for records

## Phase 5: Weekly Monitoring

### Week 2-4
- [ ] Export trade history CSV weekly
- [ ] Run: `python forward_test_tracker.py --csv trades.csv --report weekly`
- [ ] No ALERT messages from tracker
- [ ] Trade count: ~4-5 trades per week expected

### Month 1 Complete
- [ ] Run: `python forward_test_tracker.py --csv trades.csv --report monthly`
- [ ] Check trade count matches backtest rate (~15-20 trades/month)
- [ ] PF not below 1.0 (early data can be noisy; watch trend)
- [ ] Max DD under 10%

### Month 2 Complete
- [ ] Run monthly report
- [ ] **Monthly performance vs backtest comparison**:
  - PF trend (should be converging toward 1.5+)
  - Win rate trend (should be converging toward 60%+)
  - DD profile (should stay under 10%)
- [ ] No stop conditions triggered (see FORWARD_TEST.md Section 7)

### Month 3 Complete
- [ ] Run: `python forward_test_tracker.py --csv trades.csv --report review`
- [ ] **3-month review: go-live decision**:
  - [ ] PF >= 1.14 (70% of backtest 1.63)
  - [ ] Win Rate >= 50%
  - [ ] Max DD <= 15%
  - [ ] Net Profit > 0
  - [ ] Trade frequency: 30-100 trades in 3 months
  - [ ] No single loss > 3% of balance
  - [ ] Max consecutive losses < 8

## Phase 6: Decision

- [ ] **ALL 7 criteria PASS** -> Proceed to Phase 7 (Go-Live)
- [ ] **5-6 criteria PASS** -> Extend test by 1-2 months
- [ ] **4 or fewer PASS** -> STOP. Do not go live. Analyze and redesign.

## Phase 7: Go-Live Transition (if approved)

- [ ] Open live ECN account (same broker as demo)
- [ ] Fund with risk-appropriate capital only
- [ ] Start with **minimum lot size (0.01)** for first 2 weeks
- [ ] Copy exact same EA settings from demo
- [ ] Continue running forward_test_tracker.py on live trades
- [ ] After 2 weeks: gradually increase to 0.75% risk over 1 month
- [ ] Set account-level hard stop: 20% max DD
- [ ] Continue weekly reporting indefinitely

---

## Emergency Procedures

| Condition | Action |
|-----------|--------|
| DD > 15% | Stop EA immediately. Review all trades. |
| 10+ consecutive losses | Stop EA. Check market regime change. |
| EA stops trading for 2+ weeks | Check connection, spread, errors. Restart if needed. |
| Broker changes spread/leverage | Reassess. May need to switch broker. |
| Critical error in Experts tab | Screenshot error. Stop EA. Investigate. |

---

## Notes

_Record any observations, parameter changes, or market events here:_

| Date | Note |
|------|------|
| | |
| | |
| | |
