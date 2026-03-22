"""
A/B Test: v10.1 (baseline) vs v11.0 (range improvements)
Tests across 5 periods to ensure no regression.
"""
import pandas as pd
import numpy as np
import copy
import os, sys

from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester

# Load full data
print("[CSV] Loading data...")
h4_full = load_csv("XAUUSD_H4.csv")
h1_real = load_csv("XAUUSD_H1.csv")
usdjpy = load_csv("USDJPY_H1.csv")
m15_real = load_csv("XAUUSD_M15.csv")

h1_gen = generate_h1_from_h4(h4_full)
h1_full = merge_and_fill(h1_real, h1_gen)
m15_gen = generate_m15_from_h1(h1_full)
m15_full = merge_and_fill(m15_real, m15_gen)

# Test periods (with warm-up data starting 6 months before)
PERIODS = [
    ("2016-18", "2015-06-01", "2016-01-01", "2018-01-01"),
    ("2018-20", "2017-06-01", "2018-01-01", "2020-01-01"),
    ("2020-22", "2019-06-01", "2020-01-01", "2022-01-01"),
    ("2022-24", "2021-06-01", "2022-01-01", "2024-01-01"),
    ("2024-26", "2023-06-01", "2024-01-01", "2026-06-01"),
]


def run_backtest(cfg, h4, h1, m15, usd):
    bt = GoldBacktester(cfg)
    bt.run(h4, h1, m15, usdjpy_df=usd)
    if not bt.trades:
        return None
    trades = pd.DataFrame(bt.trades)
    wins = trades[trades['pnl_jpy'] > 0]
    losses = trades[trades['pnl_jpy'] <= 0]
    pnl = trades['pnl_jpy'].sum()
    wr = len(wins) / len(trades) * 100
    pf = wins['pnl_jpy'].sum() / abs(losses['pnl_jpy'].sum()) if len(losses) > 0 and losses['pnl_jpy'].sum() != 0 else float('inf')
    ret = (bt.balance / cfg.INITIAL_BALANCE - 1) * 100

    eq = pd.DataFrame(bt.equity_curve)
    dd = 0
    if len(eq) > 0:
        eq["peak"] = eq["equity"].cummax()
        eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
        dd = eq["dd"].max()

    returns = trades['pnl_jpy']
    sharpe = returns.mean() / returns.std() * np.sqrt(252 * 4) if returns.std() > 0 else 0

    return {
        'trades': len(trades),
        'wr': wr,
        'pf': pf,
        'ret': ret,
        'dd': dd,
        'sharpe': sharpe,
        'pnl': pnl,
    }


print("\n" + "="*90)
print(" v10.1 vs v11.0 A/B TEST (Range Market Improvements)")
print("="*90)
print(f" {'Period':<10} | {'Variant':<8} | {'Trades':>6} | {'WR':>6} | {'PF':>6} | {'Return':>9} | {'MaxDD':>6} | {'Sharpe':>7}")
print("-"*90)

for name, warm_start, start, end in PERIODS:
    # Slice data
    h4 = h4_full[(h4_full.index >= warm_start) & (h4_full.index < end)].copy()
    h1 = h1_full[(h1_full.index >= warm_start) & (h1_full.index < end)].copy()
    m15 = m15_full[(m15_full.index >= warm_start) & (m15_full.index < end)].copy()
    usd = usdjpy[(usdjpy.index >= warm_start) & (usdjpy.index < end)].copy()

    if len(h4) < 100 or len(m15) < 200:
        print(f" {name:<10} | SKIP - insufficient data (H4={len(h4)}, M15={len(m15)})")
        continue

    # Baseline: v10.1 (USE_V11_RANGE=False)
    cfg_base = GoldConfig()
    cfg_base.USE_V11_RANGE = False
    r_base = run_backtest(cfg_base, h4.copy(), h1.copy(), m15.copy(), usd.copy())

    # v11.0: USE_V11_RANGE=True
    cfg_v11 = GoldConfig()
    cfg_v11.USE_V11_RANGE = True
    r_v11 = run_backtest(cfg_v11, h4.copy(), h1.copy(), m15.copy(), usd.copy())

    if r_base and r_v11:
        print(f" {name:<10} | v10.1   | {r_base['trades']:>6} | {r_base['wr']:>5.1f}% | {r_base['pf']:>5.2f} | {r_base['ret']:>+8.1f}% | {r_base['dd']:>5.1f}% | {r_base['sharpe']:>6.2f}")
        print(f" {'':10} | v11.0   | {r_v11['trades']:>6} | {r_v11['wr']:>5.1f}% | {r_v11['pf']:>5.2f} | {r_v11['ret']:>+8.1f}% | {r_v11['dd']:>5.1f}% | {r_v11['sharpe']:>6.2f}")
        # Delta
        d_pf = r_v11['pf'] - r_base['pf']
        d_dd = r_v11['dd'] - r_base['dd']
        d_ret = r_v11['ret'] - r_base['ret']
        d_wr = r_v11['wr'] - r_base['wr']
        d_trades = r_v11['trades'] - r_base['trades']
        verdict = "BETTER" if d_pf > 0 and d_dd <= 1.0 else "WORSE" if d_pf < -0.05 else "NEUTRAL"
        print(f" {'':10} | delta   | {d_trades:>+6} | {d_wr:>+5.1f}% | {d_pf:>+5.2f} | {d_ret:>+8.1f}% | {d_dd:>+5.1f}% | {verdict}")
        print("-"*90)

print("\nDone.")
