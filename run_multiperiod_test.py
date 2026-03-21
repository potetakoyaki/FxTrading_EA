#!/usr/bin/env python3
"""v8.1 Multi-period backtest (5 periods, matching v8.0 CLAUDE.md format)"""
import os, sys
import pandas as pd

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

def run_period(h4, h1, m15, usdjpy, start, end):
    lead = pd.Timedelta(days=90)  # 70+ bars H4 lead data
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)

    h4_p = h4[(h4.index >= s - lead) & (h4.index < e)]
    h1_p = h1[(h1.index >= s - lead) & (h1.index < e)]
    m15_p = m15[(m15.index >= s - lead) & (m15.index < e)]
    usdjpy_p = usdjpy[(usdjpy.index >= s - lead) & (usdjpy.index < e)] if usdjpy is not None else None

    cfg = GoldConfig()
    bt = GoldBacktester(cfg)
    bt.run(h4_p, h1_p, m15_p, usdjpy_df=usdjpy_p)
    rpt = bt.get_report()

    # Filter trades to only count those within the period
    period_trades = [t for t in bt.trades if t['open_time'] >= s]
    pyramids = sum(1 for t in period_trades if t.get('entry_type') == 'pyramid')

    return rpt, len(period_trades), pyramids, bt

if __name__ == "__main__":
    print("Loading all data...")
    h4, h1, m15, usdjpy = load_all_data()
    print(f"  M15: {len(m15):,} bars ({m15.index[0]} ~ {m15.index[-1]})")
    print(f"  H1:  {len(h1):,} bars")
    print(f"  H4:  {len(h4):,} bars")

    results = []
    print(f"\n{'='*80}")
    print(f" AntigravityMTF EA [GOLD] v8.1 - Multi-Period Backtest")
    print(f"{'='*80}")

    for label, start, end, env in PERIODS:
        print(f"\n--- Period: {label} ({env}) [{start} ~ {end}] ---")
        rpt, trades, pyramids, bt = run_period(h4, h1, m15, usdjpy, start, end)
        if rpt and "error" not in rpt:
            pf = rpt.get("PF", "N/A")
            wr = rpt.get("WinRate", "N/A")
            dd = rpt.get("Max DD", "N/A")
            ret = rpt.get("Return", "N/A")
            buy_pnl = rpt.get("BUY_PnL", "N/A")
            sell_pnl = rpt.get("SELL_PnL", "N/A")
            results.append({
                "period": label, "env": env,
                "pf": pf, "wr": wr, "dd": dd, "ret": ret,
                "trades": trades, "pyramids": pyramids,
                "buy_pnl": buy_pnl, "sell_pnl": sell_pnl,
            })
            print(f"  PF={pf}  WR={wr}  DD={dd}  Return={ret}  Trades={trades}  Pyramids={pyramids}")
        else:
            print(f"  [ERROR] {rpt}")
            results.append({"period": label, "env": env, "error": True})

    # Summary table
    print(f"\n{'='*80}")
    print(f" v8.1 Multi-Period Summary")
    print(f"{'='*80}")
    print(f"{'Period':<10} {'Env':<10} {'PF':>6} {'WR':>8} {'DD':>8} {'Return':>10} {'Trades':>7} {'Pyramids':>9}")
    print("-" * 75)
    for r in results:
        if r.get("error"):
            print(f"{r['period']:<10} {r['env']:<10} ERROR")
            continue
        print(f"{r['period']:<10} {r['env']:<10} {r['pf']:>6} {r['wr']:>8} {r['dd']:>8} {r['ret']:>10} {r['trades']:>7} {r['pyramids']:>9}")
