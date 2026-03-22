"""
Diagnostic: Analyze why 2022-24 range market loses money.
Breakdown by regime, direction, component effectiveness, and loss patterns.
"""
import pandas as pd
import numpy as np
import os, sys

from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester

# Load data
h4_full = load_csv("XAUUSD_H4.csv")
h1_real = load_csv("XAUUSD_H1.csv")
usdjpy = load_csv("USDJPY_H1.csv")
m15_real = load_csv("XAUUSD_M15.csv")

h1_gen = generate_h1_from_h4(h4_full)
h1_full = merge_and_fill(h1_real, h1_gen)
m15_gen = generate_m15_from_h1(h1_full)
m15_full = merge_and_fill(m15_real, m15_gen)

# Filter 2022-2024
h4 = h4_full[(h4_full.index >= '2022-01-01') & (h4_full.index < '2024-01-01')].copy()
h1 = h1_full[(h1_full.index >= '2022-01-01') & (h1_full.index < '2024-01-01')].copy()
m15 = m15_full[(m15_full.index >= '2022-01-01') & (m15_full.index < '2024-01-01')].copy()
usd = usdjpy[(usdjpy.index >= '2022-01-01') & (usdjpy.index < '2024-01-01')].copy()

# Need warm-up data for indicators
h4_warm = h4_full[(h4_full.index >= '2021-06-01') & (h4_full.index < '2024-01-01')].copy()
h1_warm = h1_full[(h1_full.index >= '2021-06-01') & (h1_full.index < '2024-01-01')].copy()
m15_warm = m15_full[(m15_full.index >= '2021-06-01') & (m15_full.index < '2024-01-01')].copy()
usd_warm = usdjpy[(usdjpy.index >= '2021-06-01') & (usdjpy.index < '2024-01-01')].copy()

print(f"H4: {len(h4_warm)} bars, H1: {len(h1_warm)}, M15: {len(m15_warm)}")
print(f"Period: {h4_warm.index[0]} ~ {h4_warm.index[-1]}")

cfg = GoldConfig()
bt = GoldBacktester(cfg)
bt.run(h4_warm, h1_warm, m15_warm, usdjpy_df=usd_warm)

trades = pd.DataFrame(bt.trades)
if trades.empty:
    print("No trades!")
    sys.exit(1)

# Filter trades to 2022-2024 only
trades['open_dt'] = pd.to_datetime(trades['open_time'])
trades = trades[(trades['open_dt'] >= '2022-01-01') & (trades['open_dt'] < '2024-01-01')]

print(f"\n{'='*70}")
print(f" 2022-2024 RANGE MARKET LOSS ANALYSIS")
print(f"{'='*70}")
print(f" Trades: {len(trades)}")
print(f" Total PnL: {trades['pnl_jpy'].sum():+,.0f} JPY")
wins = trades[trades['pnl_jpy'] > 0]
losses = trades[trades['pnl_jpy'] <= 0]
print(f" Win Rate: {len(wins)/len(trades)*100:.1f}%")
print(f" Avg Win: {wins['pnl_jpy'].mean():+,.0f} JPY" if len(wins) > 0 else " No wins")
print(f" Avg Loss: {losses['pnl_jpy'].mean():+,.0f} JPY" if len(losses) > 0 else " No losses")

# 1. By Direction
print(f"\n--- BY DIRECTION ---")
for d in ['BUY', 'SELL']:
    sub = trades[trades['direction'] == d]
    w = sub[sub['pnl_jpy'] > 0]
    wr = len(w)/len(sub)*100 if len(sub) > 0 else 0
    pnl = sub['pnl_jpy'].sum()
    print(f" {d}: {len(sub)} trades, WR={wr:.1f}%, PnL={pnl:+,.0f} JPY")

# 2. By Close Reason
print(f"\n--- BY CLOSE REASON ---")
for r in trades['reason'].unique():
    sub = trades[trades['reason'] == r]
    pnl = sub['pnl_jpy'].sum()
    avg = sub['pnl_jpy'].mean()
    print(f" {r}: {len(sub)} trades, PnL={pnl:+,.0f}, Avg={avg:+,.0f}")

# 3. By Entry Type
print(f"\n--- BY ENTRY TYPE ---")
for et in trades['entry_type'].unique():
    sub = trades[trades['entry_type'] == et]
    w = sub[sub['pnl_jpy'] > 0]
    wr = len(w)/len(sub)*100 if len(sub) > 0 else 0
    pnl = sub['pnl_jpy'].sum()
    print(f" {et}: {len(sub)} trades, WR={wr:.1f}%, PnL={pnl:+,.0f}")

# 4. Monthly breakdown
print(f"\n--- MONTHLY BREAKDOWN ---")
trades['month'] = trades['open_dt'].dt.to_period('M')
for m, grp in trades.groupby('month'):
    w = grp[grp['pnl_jpy'] > 0]
    wr = len(w)/len(grp)*100 if len(grp) > 0 else 0
    pnl = grp['pnl_jpy'].sum()
    sl_count = len(grp[grp['reason'] == 'SL'])
    tp_count = len(grp[grp['reason'] == 'TP'])
    trail_count = len(grp[grp['reason'] == 'Trail'])
    print(f" {m}: {len(grp):>3} trades WR={wr:>5.1f}% PnL={pnl:>+10,.0f} (SL:{sl_count} TP:{tp_count} Trail:{trail_count})")

# 5. Score distribution of losing trades
print(f"\n--- SCORE DISTRIBUTION (LOSERS) ---")
loser_scores = losses['score'].value_counts().sort_index()
for s, c in loser_scores.items():
    avg_loss = losses[losses['score'] == s]['pnl_jpy'].mean()
    print(f" Score={s}: {c} losers, Avg loss={avg_loss:+,.0f}")

# 6. Win rate by score threshold
print(f"\n--- WIN RATE BY SCORE ---")
for s in sorted(trades['score'].unique()):
    sub = trades[trades['score'] == s]
    w = sub[sub['pnl_jpy'] > 0]
    wr = len(w)/len(sub)*100 if len(sub) > 0 else 0
    pnl = sub['pnl_jpy'].sum()
    print(f" Score={s}: {len(sub):>4} trades WR={wr:>5.1f}% PnL={pnl:>+10,.0f}")

# 7. Loss clustering (consecutive losses)
print(f"\n--- CONSECUTIVE LOSS STREAKS ---")
streak = 0
max_streak = 0
streak_loss = 0
max_streak_loss = 0
for _, t in trades.iterrows():
    if t['pnl_jpy'] <= 0:
        streak += 1
        streak_loss += t['pnl_jpy']
        if streak > max_streak:
            max_streak = streak
            max_streak_loss = streak_loss
    else:
        streak = 0
        streak_loss = 0
print(f" Max consecutive losses: {max_streak} (total {max_streak_loss:+,.0f} JPY)")

# 8. Average holding time (bars)
print(f"\n--- HOLDING TIME ---")
trades['close_dt'] = pd.to_datetime(trades['close_time'])
trades['hold_hours'] = (trades['close_dt'] - trades['open_dt']).dt.total_seconds() / 3600
win_hold = wins['hold_hours'].mean() if 'hold_hours' in wins.columns else trades.loc[trades['pnl_jpy'] > 0, 'hold_hours'].mean()
loss_hold = trades.loc[trades['pnl_jpy'] <= 0, 'hold_hours'].mean()
print(f" Avg win holding: {win_hold:.1f} hours")
print(f" Avg loss holding: {loss_hold:.1f} hours")

# 9. Component analysis
print(f"\n--- COMPONENT ANALYSIS ---")
bt.analyze_components()

# 10. Regime stats
if cfg.USE_REGIME_ADAPTIVE:
    total_bars = sum(bt.regime_stats.values())
    print(f"\n--- REGIME DISTRIBUTION ---")
    for rn, count in bt.regime_stats.items():
        pct = count / total_bars * 100 if total_bars > 0 else 0
        trades_in = len(bt.regime_trades.get(rn, []))
        print(f" {rn:>10}: {count:>6} bars ({pct:>5.1f}%) | {trades_in} entries")
