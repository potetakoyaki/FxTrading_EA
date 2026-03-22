#!/usr/bin/env python3
"""v8.2 Multi-period regression test: v8.1 vs v8.2 across all 5 periods"""
import os, sys, io, contextlib
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, generate_h4_from_d1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester

PERIODS = [
    ("2016-18", "2016-03-21", "2018-03-21", "低ボラ"),
    ("2018-20", "2018-03-21", "2020-03-21", "トレンド"),
    ("2020-22", "2020-03-21", "2022-03-21", "コロナ"),
    ("2022-24", "2022-03-21", "2024-03-21", "レンジ"),
    ("2024-26", "2024-03-21", "2026-03-21", "高ボラ"),
]

def load_all_data():
    m15_real = load_csv("XAUUSD_M15.csv")
    h1_real = load_csv("XAUUSD_H1.csv")
    h4_real = load_csv("XAUUSD_H4.csv")
    d1_real = load_csv("XAUUSD_D1.csv")
    usdjpy_h1 = load_csv("USDJPY_H1.csv")
    usdjpy_h4 = load_csv("USDJPY_H4.csv")
    usdjpy_d1 = load_csv("USDJPY_D1.csv")

    h4 = h4_real
    if d1_real is not None:
        h4_gen = generate_h4_from_d1(d1_real)
        h4 = merge_and_fill(h4_real, h4_gen)

    h1 = h1_real
    if h4 is not None:
        h1_gen = generate_h1_from_h4(h4)
        h1 = merge_and_fill(h1_real, h1_gen)

    m15 = m15_real
    if h1 is not None:
        m15_gen = generate_m15_from_h1(h1)
        m15 = merge_and_fill(m15_real, m15_gen)

    usdjpy = usdjpy_h1
    if usdjpy_h4 is not None:
        usdjpy_h1_gen = generate_h1_from_h4(usdjpy_h4)
        usdjpy = merge_and_fill(usdjpy_h1, usdjpy_h1_gen)

    return h4, h1, m15, usdjpy


def run_period(h4, h1, m15, usdjpy, start, end, version="v8.1"):
    lead = pd.Timedelta(days=90)
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)

    h4_p = h4[(h4.index >= s - lead) & (h4.index < e)]
    h1_p = h1[(h1.index >= s - lead) & (h1.index < e)]
    m15_p = m15[(m15.index >= s - lead) & (m15.index < e)]
    usdjpy_p = usdjpy[(usdjpy.index >= s - lead) & (usdjpy.index < e)] if usdjpy is not None else None

    cfg = GoldConfig()
    if version == "v8.1":
        cfg.HIGH_VOL_PYRAMID_BLOCK = 1.5
        cfg.GRADUATED_SL = False
        cfg.CONSEC_LOSS_ESCALATION = False
    # v8.2 uses current defaults (already set in GoldConfig)

    bt = GoldBacktester(cfg)
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        bt.run(h4_p, h1_p, m15_p, usdjpy_df=usdjpy_p)

    period_trades = [t for t in bt.trades if t['open_time'] >= s]
    trades_df = pd.DataFrame(period_trades) if period_trades else pd.DataFrame()

    if len(trades_df) == 0:
        return {"trades": 0, "pf": 0, "wr": 0, "dd": 0, "ret": 0, "pyramids": 0}

    wins = trades_df[trades_df["pnl_pts"] > 0]
    losses = trades_df[trades_df["pnl_pts"] <= 0]
    gross_win = wins["pnl_jpy"].sum()
    gross_loss = abs(losses["pnl_jpy"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / len(trades_df) * 100
    pyramids = sum(1 for t in period_trades if t.get('entry_type') == 'pyramid')

    # Return & DD from equity curve within period
    eq = pd.DataFrame(bt.equity_curve)
    eq["peak"] = eq["equity"].cummax()
    eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
    max_dd = eq["dd"].max()
    ret = (bt.balance / cfg.INITIAL_BALANCE - 1) * 100

    return {
        "trades": len(trades_df), "pyramids": pyramids,
        "pf": pf, "wr": wr, "dd": max_dd, "ret": ret,
    }


if __name__ == "__main__":
    print("Loading all data...", flush=True)
    h4, h1, m15, usdjpy = load_all_data()
    print(f"  M15: {len(m15):,} bars ({m15.index[0]} ~ {m15.index[-1]})", flush=True)

    v81_results = []
    v82_results = []

    for label, start, end, env in PERIODS:
        print(f"\n--- {label} ({env}) ---", flush=True)

        r81 = run_period(h4, h1, m15, usdjpy, start, end, "v8.1")
        print(f"  v8.1: PF={r81['pf']:.2f} WR={r81['wr']:.1f}% DD={r81['dd']:.1f}% Ret={r81['ret']:+.1f}% Trades={r81['trades']}", flush=True)
        v81_results.append({**r81, "period": label, "env": env})

        r82 = run_period(h4, h1, m15, usdjpy, start, end, "v8.2")
        print(f"  v8.2: PF={r82['pf']:.2f} WR={r82['wr']:.1f}% DD={r82['dd']:.1f}% Ret={r82['ret']:+.1f}% Trades={r82['trades']}", flush=True)
        v82_results.append({**r82, "period": label, "env": env})

    # Summary comparison
    print(f"\n{'='*90}")
    print(f" v8.1 vs v8.2 Multi-Period Comparison")
    print(f"{'='*90}")
    print(f"{'Period':<10} {'Env':<8} | {'v8.1 PF':>7} {'v8.2 PF':>7} | {'v8.1 WR':>7} {'v8.2 WR':>7} | {'v8.1 DD':>7} {'v8.2 DD':>7} | {'v8.1 Ret':>8} {'v8.2 Ret':>8} | {'v8.1 T':>6} {'v8.2 T':>6}")
    print("-" * 90)

    pf_wins = 0
    dd_wins = 0
    for r81, r82 in zip(v81_results, v82_results):
        pf_mark = "+" if r82["pf"] >= r81["pf"] else "-"
        dd_mark = "+" if r82["dd"] <= r81["dd"] else "-"
        if r82["pf"] >= r81["pf"]:
            pf_wins += 1
        if r82["dd"] <= r81["dd"]:
            dd_wins += 1
        print(f"{r81['period']:<10} {r81['env']:<8} | {r81['pf']:>7.2f} {r82['pf']:>6.2f}{pf_mark} | {r81['wr']:>6.1f}% {r82['wr']:>6.1f}% | {r81['dd']:>6.1f}% {r82['dd']:>5.1f}%{dd_mark} | {r81['ret']:>+7.1f}% {r82['ret']:>+7.1f}% | {r81['trades']:>6} {r82['trades']:>6}")

    print(f"\nPF improved/maintained: {pf_wins}/5 periods")
    print(f"DD improved/maintained: {dd_wins}/5 periods")

    verdict = "APPROVED" if pf_wins >= 4 and dd_wins >= 4 else "NEEDS REVIEW"
    print(f"\nVERDICT: {verdict}")
    print(f"{'='*90}", flush=True)
