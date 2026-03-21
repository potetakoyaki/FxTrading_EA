#!/usr/bin/env python3
"""v8.2 A/B test: Graduated Volatility Defense
Tests each change independently and combined against v8.1 baseline.
"""
import sys, io, contextlib
import pandas as pd
import numpy as np
from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester

# Load data
print("[CSV] Loading data...", flush=True)
m15_real = load_csv("XAUUSD_M15.csv")
h1_real = load_csv("XAUUSD_H1.csv")
h4 = load_csv("XAUUSD_H4.csv")
usdjpy = load_csv("USDJPY_H1.csv")
h1_gen = generate_h1_from_h4(h4)
h1 = merge_and_fill(h1_real, h1_gen)
m15_gen = generate_m15_from_h1(h1)
m15 = merge_and_fill(m15_real, m15_gen)

print(f"Data: {m15.index[0]} ~ {m15.index[-1]}, M15 bars: {len(m15):,}", flush=True)


def run_test(label, **overrides):
    """Run backtest with config overrides, return metrics dict."""
    cfg = GoldConfig()
    # v8.1 baseline defaults
    cfg.HIGH_VOL_PYRAMID_BLOCK = 1.5
    cfg.GRADUATED_SL = False
    cfg.CONSEC_LOSS_ESCALATION = False
    # Apply overrides
    for k, v in overrides.items():
        setattr(cfg, k, v)

    bt = GoldBacktester(cfg)
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        bt.run(h4, h1, m15, usdjpy_df=usdjpy)

    trades_df = pd.DataFrame(bt.trades)
    if len(trades_df) == 0:
        return None

    wins = trades_df[trades_df["pnl_pts"] > 0]
    losses = trades_df[trades_df["pnl_pts"] <= 0]
    gross_win = wins["pnl_jpy"].sum()
    gross_loss = abs(losses["pnl_jpy"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / len(trades_df) * 100
    ret = (bt.balance / cfg.INITIAL_BALANCE - 1) * 100
    pyramids = sum(1 for t in bt.trades if t.get('entry_type') == 'pyramid')

    # Max DD
    eq = pd.DataFrame(bt.equity_curve)
    eq["peak"] = eq["equity"].cummax()
    eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
    max_dd = eq["dd"].max()

    # Sharpe
    returns = trades_df["pnl_jpy"]
    sharpe = returns.mean() / returns.std() * np.sqrt(252 * 4) if returns.std() > 0 else 0

    # Monthly WR
    trades_df["month"] = pd.to_datetime(trades_df["close_time"]).dt.to_period("M")
    monthly = trades_df.groupby("month")["pnl_jpy"].sum()
    pm = (monthly > 0).sum()
    tm = len(monthly)

    # Jan 2026 sub-period
    trades_df["close_dt"] = pd.to_datetime(trades_df["close_time"])
    jan26 = trades_df[(trades_df["close_dt"].dt.year == 2026) & (trades_df["close_dt"].dt.month == 1)]
    jan26_pnl = jan26["pnl_jpy"].sum() if len(jan26) > 0 else 0
    jan26_trades = len(jan26)
    jan26_sl = len(jan26[jan26["exit_reason"] == "SL"]) if len(jan26) > 0 and "exit_reason" in jan26.columns else 0
    jan26_tp = len(jan26[jan26["exit_reason"] == "TP"]) if len(jan26) > 0 and "exit_reason" in jan26.columns else 0

    result = {
        "trades": len(trades_df), "pyramids": pyramids, "pf": pf, "wr": wr,
        "dd": max_dd, "ret": ret, "bal": bt.balance, "sharpe": sharpe,
        "monthly_wr": pm / tm * 100 if tm > 0 else 0,
        "jan26_pnl": jan26_pnl, "jan26_trades": jan26_trades,
        "jan26_sl": jan26_sl, "jan26_tp": jan26_tp,
    }

    print(f"\n{'='*60}")
    print(f" {label}")
    print(f"{'='*60}")
    print(f"  Trades:       {result['trades']}")
    print(f"  Pyramids:     {pyramids}")
    print(f"  PF:           {pf:.2f}")
    print(f"  Win Rate:     {wr:.1f}%")
    print(f"  Max DD:       {max_dd:.1f}%")
    print(f"  Return:       {ret:+.1f}%")
    print(f"  Final Bal:    {bt.balance:,.0f} JPY")
    print(f"  Sharpe:       {sharpe:.2f}")
    print(f"  Monthly WR:   {pm}/{tm} ({result['monthly_wr']:.0f}%)")
    print(f"  --- Jan 2026 ---")
    print(f"  Jan26 Trades: {jan26_trades} (SL:{jan26_sl} TP:{jan26_tp})")
    print(f"  Jan26 PnL:    {jan26_pnl:+,.0f} JPY")
    sys.stdout.flush()
    return result


# ── Run all variants ──
print(f"\n{'#'*65}")
print(f" v8.2 A/B TEST: Graduated Volatility Defense")
print(f"{'#'*65}")

results = {}

# Baseline: v8.1 (all v8.2 features OFF)
results["v8.1 Baseline"] = run_test("v8.1 Baseline",
    HIGH_VOL_PYRAMID_BLOCK=1.5, GRADUATED_SL=False, CONSEC_LOSS_ESCALATION=False)

# A: Pyramid block only
results["A: PyramidBlock=1.2"] = run_test("A: HIGH_VOL_PYRAMID_BLOCK=1.2 only",
    HIGH_VOL_PYRAMID_BLOCK=1.2, GRADUATED_SL=False, CONSEC_LOSS_ESCALATION=False)

# B: Graduated SL only
results["B: GraduatedSL"] = run_test("B: Graduated SL only",
    HIGH_VOL_PYRAMID_BLOCK=1.5, GRADUATED_SL=True, CONSEC_LOSS_ESCALATION=False)

# C: Consec Loss CD only
results["C: ConsecLossCD"] = run_test("C: Consecutive Loss Cooldown only",
    HIGH_VOL_PYRAMID_BLOCK=1.5, GRADUATED_SL=False, CONSEC_LOSS_ESCALATION=True)

# D: A+B combined
results["D: A+B"] = run_test("D: PyramidBlock=1.2 + Graduated SL",
    HIGH_VOL_PYRAMID_BLOCK=1.2, GRADUATED_SL=True, CONSEC_LOSS_ESCALATION=False)

# E: Full v8.2 (A+B+C)
results["E: Full v8.2"] = run_test("E: Full v8.2 (A+B+C)",
    HIGH_VOL_PYRAMID_BLOCK=1.2, GRADUATED_SL=True, CONSEC_LOSS_ESCALATION=True)

# ── Comparison table ──
print(f"\n\n{'='*100}")
print(f" COMPARISON TABLE")
print(f"{'='*100}")
header = f"  {'Variant':<22} {'Trades':>7} {'Pyr':>5} {'PF':>6} {'WR%':>6} {'DD%':>6} {'Ret%':>8} {'Sharpe':>7} {'J26 PnL':>10} {'J26 #':>5}"
print(header)
print(f"  {'-'*95}")

base = results["v8.1 Baseline"]
for name, r in results.items():
    if r is None:
        continue
    marker = " " if name == "v8.1 Baseline" else ("*" if r["pf"] >= base["pf"] and r["dd"] <= base["dd"] * 1.02 else " ")
    print(f"{marker} {name:<22} {r['trades']:>7,} {r['pyramids']:>5} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['dd']:>5.1f}% {r['ret']:>+7.1f}% {r['sharpe']:>7.2f} {r['jan26_pnl']:>+10,.0f} {r['jan26_trades']:>5}")

# Best variant
print(f"\n  * = PF >= baseline AND DD <= baseline + 2%")

# Delta table
print(f"\n  {'Delta vs v8.1':<22} {'dPF':>6} {'dWR':>6} {'dDD':>6} {'dRet':>8} {'dJ26':>10}")
print(f"  {'-'*60}")
for name, r in results.items():
    if name == "v8.1 Baseline" or r is None:
        continue
    print(f"  {name:<22} {r['pf']-base['pf']:>+6.2f} {r['wr']-base['wr']:>+5.1f}% {r['dd']-base['dd']:>+5.1f}% {r['ret']-base['ret']:>+7.1f}% {r['jan26_pnl']-base['jan26_pnl']:>+10,.0f}")

print(f"\n{'='*100}")
print("DONE", flush=True)
