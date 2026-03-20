"""
AntigravityMTF EA -- CSV Backtester
MT5 ExportHistory CSVを使ったバックテスト
XAUUSD (M15/H1/H4) + USDJPY (H1) 実データ
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

# Re-use indicator functions from backtest_gold.py
from backtest_gold import (
    GoldConfig, GoldBacktester,
    calc_sma, calc_ema, calc_rsi, calc_atr, calc_adx, calc_bb, calc_channel_signal,
)
from backtest_usdjpy import USDJPYConfig, USDJPYBacktester


# ============================================================
# CSV Reader
# ============================================================
def load_csv(filepath):
    """Load MT5 ExportHistory CSV."""
    df = pd.read_csv(filepath, parse_dates=["DateTime"])
    df = df.rename(columns={
        "DateTime": "time",
        "Open": "Open",
        "High": "High",
        "Low": "Low",
        "Close": "Close",
        "TickVolume": "Volume",
        "Spread": "Spread",
    })
    df = df.set_index("time")
    df = df.sort_index()
    # Ensure numeric
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


# ============================================================
# Gold Backtest (CSV)
# ============================================================
def run_gold_backtest():
    print("=" * 60)
    print(" XAUUSD Backtest (MT5 Real Data)")
    print("=" * 60)

    m15 = load_csv("XAUUSD_M15.csv")
    h1 = load_csv("XAUUSD_H1.csv")
    h4 = load_csv("XAUUSD_H4.csv")
    usdjpy = load_csv("USDJPY_H1.csv")

    print(f"  XAUUSD M15: {len(m15):,} bars ({m15.index[0]} ~ {m15.index[-1]})")
    print(f"  XAUUSD H1:  {len(h1):,} bars")
    print(f"  XAUUSD H4:  {len(h4):,} bars")
    print(f"  USDJPY H1:  {len(usdjpy):,} bars")

    cfg = GoldConfig()
    bt = GoldBacktester(cfg)
    bt.run(h4, h1, m15, usdjpy_df=usdjpy)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print(" AntigravityMTF EA [GOLD] v4.0 -- MT5 Real Data Results")
        print("=" * 60)
        for k, v in rpt.items():
            if k == "Monthly":
                print(f"\n  Monthly PnL:")
                for m, p in v.items():
                    bar = "#" * max(1, int(abs(p) / 2000))
                    icon = "[+]" if p > 0 else "[-]"
                    print(f"    {m}: {icon} {p:+,.0f} JPY {bar}")
            elif k == "ByReason":
                print(f"\n  Close Reasons:")
                counts = v.get("count", {})
                pnls = v.get("pnl", {})
                for reason in counts:
                    print(f"    {reason}: {int(counts[reason])}x / {pnls[reason]:+,.0f} JPY")
            else:
                print(f"  {k}: {v}")

        bt.analyze_components()

        print(f"\n  --- v4.0 Defense Stats ---")
        print(f"  News filter blocks:   {bt.news_blocks}")
        print(f"  Crash regime skips:   {bt.crash_skips}")
        print(f"  Weekend closes:       {bt.weekend_closes}")
        print(f"  Spread blocks:        {bt.spread_blocks}")

        reversals = sum(1 for t in bt.trades if t.get('entry_type') == 'reversal')
        pyramids = sum(1 for t in bt.trades if t.get('entry_type') == 'pyramid')
        bursts = sum(1 for t in bt.trades if t.get('momentum_burst', False))
        print(f"\n  --- v4.0 Attack Stats ---")
        print(f"  Reversal trades:      {reversals}")
        print(f"  Pyramid entries:      {pyramids}")
        print(f"  Momentum burst trades:{bursts}")

        print(f"\n  Trade Details (last 10):")
        print(f"  {'DateTime':<20} {'Dir':<5} {'Entry':>10} {'Exit':>10} {'Lot':>5} {'PnL(pt)':>8} {'PnL(JPY)':>10} {'Balance':>12} {'Reason':<10} {'Type':<8}")
        print("  " + "-" * 110)
        for t in bt.trades[-10:]:
            print(f"  {str(t['open_time'])[:19]:<20} {t['direction']:<5} {t['entry']:>10.2f} {t['exit']:>10.2f} {t['lot']:>5.2f} {t['pnl_pts']:>8.0f} {t['pnl_jpy']:>+10,.0f} {t['balance']:>12,.0f} {t['reason']:<10} {t.get('entry_type','normal'):<8}")
    else:
        print("[WARN] No trades occurred")

    return bt, rpt


# ============================================================
# USDJPY Backtest (CSV)
# ============================================================
def run_usdjpy_backtest():
    print("\n\n" + "=" * 60)
    print(" USDJPY Backtest (MT5 Real Data)")
    print("=" * 60)

    usdjpy_h1 = load_csv("USDJPY_H1.csv")
    print(f"  USDJPY H1: {len(usdjpy_h1):,} bars ({usdjpy_h1.index[0]} ~ {usdjpy_h1.index[-1]})")

    # Generate H4 from H1
    h4_df = usdjpy_h1.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna()

    # Generate M15 from H1
    m15_list = []
    for idx, row in usdjpy_h1.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        for j in range(4):
            frac = j / 4
            frac_next = (j + 1) / 4
            seg_o = o + (c - o) * frac
            seg_c = o + (c - o) * frac_next
            seg_h = max(seg_o, seg_c) + (h - max(o, c)) * (1 - abs(frac - 0.5) * 2) * 0.5
            seg_l = min(seg_o, seg_c) - (min(o, c) - l) * (1 - abs(frac - 0.5) * 2) * 0.5
            ts = idx + timedelta(minutes=j * 15)
            m15_list.append({"Open": seg_o, "High": seg_h, "Low": seg_l, "Close": seg_c, "time": ts})

    m15_df = pd.DataFrame(m15_list).set_index("time")

    print(f"  USDJPY H4:  {len(h4_df):,} bars (H1 -> resample)")
    print(f"  USDJPY M15: {len(m15_df):,} bars (H1 -> interpolated)")

    cfg = USDJPYConfig()
    bt = USDJPYBacktester(cfg)
    bt.run(h4_df, usdjpy_h1, m15_df)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print(" AntigravityMTF EA [USDJPY] v2.0 -- MT5 Real Data Results")
        print("=" * 60)
        for k, v in rpt.items():
            if k == "monthly_detail":
                print(f"\n  Monthly PnL:")
                for m, p in v.items():
                    bar = "#" * max(1, int(abs(p) / 2000))
                    icon = "[+]" if p > 0 else "[-]"
                    print(f"    {m}: {icon} {p:+,.0f} JPY {bar}")
            elif k == "reason_stats":
                print(f"\n  Exit Reasons:")
                counts = v.get("count", {})
                pnls = v.get("pnl", {})
                for reason in counts:
                    print(f"    {reason}: {int(counts[reason])}x / {pnls[reason]:+,.0f} JPY")
            else:
                print(f"  {k}: {v}")

        print(f"\n  Last 10 trades:")
        print(f"  {'Time':<20} {'Dir':<5} {'Entry':>9} {'Exit':>9} {'Lot':>5} {'PnL(pip)':>8} {'PnL(JPY)':>10} {'Balance':>12} {'Reason':<6}")
        print("  " + "-" * 95)
        for t in bt.trades[-10:]:
            print(f"  {str(t['open_time'])[:19]:<20} {t['direction']:<5} {t['entry']:>9.3f} {t['exit']:>9.3f} {t['lot']:>5.2f} {t['pnl_pips']:>8.1f} {t['pnl_jpy']:>+10,.0f} {t['balance']:>12,.0f} {t['reason']:<6}")
    else:
        print("[WARN] No trades occurred")

    return bt, rpt


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    gold_bt, gold_rpt = run_gold_backtest()
    usdjpy_bt, usdjpy_rpt = run_usdjpy_backtest()

    # Summary
    print("\n\n" + "=" * 60)
    print(" COMBINED SUMMARY (MT5 Real Data)")
    print("=" * 60)

    if gold_rpt and "error" not in gold_rpt:
        print(f"  XAUUSD: {gold_rpt.get('Return', 'N/A')} | {gold_rpt.get('Win Rate', 'N/A')} | PF {gold_rpt.get('PF', 'N/A')} | DD {gold_rpt.get('Max DD', 'N/A')}")
    if usdjpy_rpt and "error" not in usdjpy_rpt:
        print(f"  USDJPY: {usdjpy_rpt.get('Return', 'N/A')} | {usdjpy_rpt.get('WinRate', 'N/A')} | PF {usdjpy_rpt.get('PF', 'N/A')} | DD {usdjpy_rpt.get('MaxDD', 'N/A')}")
