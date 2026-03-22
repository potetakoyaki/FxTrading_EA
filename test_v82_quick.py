#!/usr/bin/env python3
"""v8.2 Quick A/B test: 2024-2026 period only (fast execution)"""
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

# Filter to 2024-2026 only (with 90-day lead for indicators)
lead = pd.Timedelta(days=90)
start = pd.Timestamp("2024-03-21")
end = pd.Timestamp("2026-03-21")
h4 = h4[(h4.index >= start - lead) & (h4.index < end)]
h1 = h1[(h1.index >= start - lead) & (h1.index < end)]
m15 = m15[(m15.index >= start - lead) & (m15.index < end)]
usdjpy = usdjpy[(usdjpy.index >= start - lead) & (usdjpy.index < end)]

print(f"Data: {m15.index[0]} ~ {m15.index[-1]}, M15 bars: {len(m15):,}", flush=True)


def run_test(label, **overrides):
    cfg = GoldConfig()
    # v8.1 baseline defaults
    cfg.HIGH_VOL_PYRAMID_BLOCK = 1.5
    cfg.GRADUATED_SL = False
    cfg.CONSEC_LOSS_ESCALATION = False
    for k, v in overrides.items():
        setattr(cfg, k, v)

    bt = GoldBacktester(cfg)
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        bt.run(h4, h1, m15, usdjpy_df=usdjpy)

    trades_df = pd.DataFrame(bt.trades)
    # Filter to period trades only
    trades_df = trades_df[pd.to_datetime(trades_df["open_time"]) >= start]
    if len(trades_df) == 0:
        return None

    wins = trades_df[trades_df["pnl_pts"] > 0]
    losses = trades_df[trades_df["pnl_pts"] <= 0]
    gross_win = wins["pnl_jpy"].sum()
    gross_loss = abs(losses["pnl_jpy"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / len(trades_df) * 100
    ret = (bt.balance / cfg.INITIAL_BALANCE - 1) * 100
    pyramids = sum(1 for _, t in trades_df.iterrows() if t.get('entry_type') == 'pyramid')

    eq = pd.DataFrame(bt.equity_curve)
    eq["peak"] = eq["equity"].cummax()
    eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
    max_dd = eq["dd"].max()

    returns = trades_df["pnl_jpy"]
    sharpe = returns.mean() / returns.std() * np.sqrt(252 * 4) if returns.std() > 0 else 0

    # Monthly breakdown
    trades_df["close_dt"] = pd.to_datetime(trades_df["close_time"])
    trades_df["month"] = trades_df["close_dt"].dt.to_period("M")
    monthly = trades_df.groupby("month")["pnl_jpy"].sum()
    pm = (monthly > 0).sum()
    tm = len(monthly)

    # Jan 2026
    jan26 = trades_df[(trades_df["close_dt"].dt.year == 2026) & (trades_df["close_dt"].dt.month == 1)]
    jan26_pnl = jan26["pnl_jpy"].sum() if len(jan26) > 0 else 0
    jan26_trades = len(jan26)
    jan26_sl = len(jan26[jan26["exit_reason"] == "SL"]) if "exit_reason" in jan26.columns and len(jan26) > 0 else 0
    jan26_tp = len(jan26[jan26["exit_reason"] == "TP"]) if "exit_reason" in jan26.columns and len(jan26) > 0 else 0
    jan26_pyr = sum(1 for _, t in jan26.iterrows() if t.get('entry_type') == 'pyramid')

    result = {
        "trades": len(trades_df), "pyramids": pyramids, "pf": pf, "wr": wr,
        "dd": max_dd, "ret": ret, "bal": bt.balance, "sharpe": sharpe,
        "monthly_wr": pm / tm * 100 if tm > 0 else 0,
        "jan26_pnl": jan26_pnl, "jan26_trades": jan26_trades,
        "jan26_sl": jan26_sl, "jan26_tp": jan26_tp, "jan26_pyr": jan26_pyr,
    }

    print(f"\n{'='*60}", flush=True)
    print(f" {label}", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  Trades:       {result['trades']} (Pyramids: {pyramids})", flush=True)
    print(f"  PF:           {pf:.2f}", flush=True)
    print(f"  Win Rate:     {wr:.1f}%", flush=True)
    print(f"  Max DD:       {max_dd:.1f}%", flush=True)
    print(f"  Return:       {ret:+.1f}%", flush=True)
    print(f"  Sharpe:       {sharpe:.2f}", flush=True)
    print(f"  Monthly WR:   {pm}/{tm} ({result['monthly_wr']:.0f}%)", flush=True)
    print(f"  --- Jan 2026 ---", flush=True)
    print(f"  Trades: {jan26_trades} (Pyr:{jan26_pyr} SL:{jan26_sl} TP:{jan26_tp})", flush=True)
    print(f"  PnL:    {jan26_pnl:+,.0f} JPY", flush=True)
    sys.stdout.flush()
    return result


print(f"\n{'#'*65}", flush=True)
print(f" v8.2 QUICK A/B TEST (2024-2026)", flush=True)
print(f"{'#'*65}", flush=True)

results = {}

results["v8.1 Baseline"] = run_test("v8.1 Baseline",
    HIGH_VOL_PYRAMID_BLOCK=1.5, GRADUATED_SL=False, CONSEC_LOSS_ESCALATION=False)

results["A: PyramidBlock=1.2"] = run_test("A: HIGH_VOL_PYRAMID_BLOCK=1.2 only",
    HIGH_VOL_PYRAMID_BLOCK=1.2, GRADUATED_SL=False, CONSEC_LOSS_ESCALATION=False)

results["B: GraduatedSL"] = run_test("B: Graduated SL only",
    HIGH_VOL_PYRAMID_BLOCK=1.5, GRADUATED_SL=True, CONSEC_LOSS_ESCALATION=False)

results["C: ConsecLossCD"] = run_test("C: Consecutive Loss Cooldown only",
    HIGH_VOL_PYRAMID_BLOCK=1.5, GRADUATED_SL=False, CONSEC_LOSS_ESCALATION=True)

results["D: A+B"] = run_test("D: PyramidBlock=1.2 + Graduated SL",
    HIGH_VOL_PYRAMID_BLOCK=1.2, GRADUATED_SL=True, CONSEC_LOSS_ESCALATION=False)

results["E: Full v8.2"] = run_test("E: Full v8.2 (A+B+C)",
    HIGH_VOL_PYRAMID_BLOCK=1.2, GRADUATED_SL=True, CONSEC_LOSS_ESCALATION=True)

# Comparison
print(f"\n\n{'='*110}", flush=True)
print(f" COMPARISON TABLE (2024-2026)", flush=True)
print(f"{'='*110}", flush=True)
header = f"  {'Variant':<22} {'Trades':>7} {'Pyr':>5} {'PF':>6} {'WR%':>6} {'DD%':>6} {'Ret%':>8} {'Shrp':>5} {'J26 PnL':>10} {'J26#':>4} {'J26P':>4} {'J26SL':>5}"
print(header, flush=True)
print(f"  {'-'*105}", flush=True)

base = results["v8.1 Baseline"]
for name, r in results.items():
    if r is None:
        continue
    print(f"  {name:<22} {r['trades']:>7,} {r['pyramids']:>5} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['dd']:>5.1f}% {r['ret']:>+7.1f}% {r['sharpe']:>5.2f} {r['jan26_pnl']:>+10,.0f} {r['jan26_trades']:>4} {r['jan26_pyr']:>4} {r['jan26_sl']:>5}", flush=True)

print(f"\n  {'Delta vs v8.1':<22} {'dPF':>6} {'dWR':>6} {'dDD':>6} {'dRet':>8} {'dJ26 PnL':>10}", flush=True)
print(f"  {'-'*60}", flush=True)
for name, r in results.items():
    if name == "v8.1 Baseline" or r is None:
        continue
    print(f"  {name:<22} {r['pf']-base['pf']:>+6.2f} {r['wr']-base['wr']:>+5.1f}% {r['dd']-base['dd']:>+5.1f}% {r['ret']-base['ret']:>+7.1f}% {r['jan26_pnl']-base['jan26_pnl']:>+10,.0f}", flush=True)

# Best variant selection
best_name = max(
    [(n, r) for n, r in results.items() if n != "v8.1 Baseline" and r is not None],
    key=lambda x: (x[1]["pf"], -x[1]["dd"], x[1]["ret"])
)[0]
print(f"\n  BEST VARIANT: {best_name}", flush=True)
print(f"{'='*110}", flush=True)
