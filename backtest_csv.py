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
    import os
    if not os.path.exists(filepath):
        print(f"  [WARN] File not found: {filepath}")
        return None
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


def generate_h1_from_h4(h4_df):
    """H4データからH1を補間生成"""
    h1_list = []
    for idx, row in h4_df.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        vol = row.get("Volume", 0)
        for j in range(4):
            frac = j / 4
            frac_next = (j + 1) / 4
            seg_o = o + (c - o) * frac
            seg_c = o + (c - o) * frac_next
            seg_h = max(seg_o, seg_c) + (h - max(o, c)) * (1 - abs(frac - 0.5) * 2) * 0.5
            seg_l = min(seg_o, seg_c) - (min(o, c) - l) * (1 - abs(frac - 0.5) * 2) * 0.5
            ts = idx + timedelta(hours=j)
            h1_list.append({"Open": seg_o, "High": seg_h, "Low": seg_l,
                            "Close": seg_c, "Volume": vol / 4, "time": ts})
    return pd.DataFrame(h1_list).set_index("time")


def generate_m15_from_h1(h1_df):
    """H1データからM15を補間生成"""
    m15_list = []
    for idx, row in h1_df.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        vol = row.get("Volume", 0)
        for j in range(4):
            frac = j / 4
            frac_next = (j + 1) / 4
            seg_o = o + (c - o) * frac
            seg_c = o + (c - o) * frac_next
            seg_h = max(seg_o, seg_c) + (h - max(o, c)) * (1 - abs(frac - 0.5) * 2) * 0.5
            seg_l = min(seg_o, seg_c) - (min(o, c) - l) * (1 - abs(frac - 0.5) * 2) * 0.5
            ts = idx + timedelta(minutes=j * 15)
            m15_list.append({"Open": seg_o, "High": seg_h, "Low": seg_l,
                             "Close": seg_c, "Volume": vol / 4, "time": ts})
    return pd.DataFrame(m15_list).set_index("time")


def generate_h4_from_d1(d1_df):
    """D1データからH4を補間生成"""
    h4_list = []
    for idx, row in d1_df.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        vol = row.get("Volume", 0)
        # 1日を6本のH4に分割
        segments = [
            (o, max(o, o + (h - o) * 0.3), min(o, o - (o - l) * 0.1), o + (c - o) * 0.17),
            (o + (c - o) * 0.17, max(o + (c - o) * 0.17, (o + h) / 2), min(o + (c - o) * 0.17, (o + l) / 2), o + (c - o) * 0.33),
            (o + (c - o) * 0.33, h, (h + l) / 2, o + (c - o) * 0.5),
            (o + (c - o) * 0.5, max(o + (c - o) * 0.5, h * 0.7 + c * 0.3), l, o + (c - o) * 0.67),
            (o + (c - o) * 0.67, max(o + (c - o) * 0.67, (c + h) / 2), min(o + (c - o) * 0.67, (c + l) / 2), o + (c - o) * 0.83),
            (o + (c - o) * 0.83, max(c, o + (c - o) * 0.83), min(c, o + (c - o) * 0.83), c),
        ]
        for j, (so, sh, sl, sc) in enumerate(segments):
            ts = idx + timedelta(hours=j * 4)
            h4_list.append({"Open": so, "High": sh, "Low": sl, "Close": sc,
                            "Volume": vol / 6, "time": ts})
    return pd.DataFrame(h4_list).set_index("time")


def merge_and_fill(real_df, generated_df):
    """実データと補間データを結合。実データ優先、不足分を補間で埋める"""
    if real_df is None or len(real_df) == 0:
        return generated_df
    if generated_df is None or len(generated_df) == 0:
        return real_df

    # 実データにない期間を補間データで埋める
    gen_only = generated_df[~generated_df.index.isin(real_df.index)]
    merged = pd.concat([gen_only, real_df]).sort_index()
    merged = merged[~merged.index.duplicated(keep='last')]
    return merged


# ============================================================
# Gold Backtest (CSV)
# ============================================================
def run_gold_backtest():
    print("=" * 60)
    print(" XAUUSD Backtest (MT5 Real Data)")
    print("=" * 60)

    # 各時間足を読み込み (存在しないファイルはNone)
    m15_real = load_csv("XAUUSD_M15.csv")
    h1_real = load_csv("XAUUSD_H1.csv")
    h4_real = load_csv("XAUUSD_H4.csv")
    d1_real = load_csv("XAUUSD_D1.csv")
    usdjpy_h1 = load_csv("USDJPY_H1.csv")
    usdjpy_h4 = load_csv("USDJPY_H4.csv")
    usdjpy_d1 = load_csv("USDJPY_D1.csv")

    # H4: 実データ + D1から補間
    h4 = h4_real
    if d1_real is not None:
        h4_gen = generate_h4_from_d1(d1_real)
        h4 = merge_and_fill(h4_real, h4_gen)

    # H1: 実データ + H4から補間
    h1 = h1_real
    if h4 is not None:
        h1_gen = generate_h1_from_h4(h4)
        h1 = merge_and_fill(h1_real, h1_gen)

    # M15: 実データ + H1から補間
    m15 = m15_real
    if h1 is not None:
        m15_gen = generate_m15_from_h1(h1)
        m15 = merge_and_fill(m15_real, m15_gen)

    # USDJPY: H1実データ + H4/D1から補間
    usdjpy = usdjpy_h1
    if usdjpy_h4 is not None:
        usdjpy_h1_gen = generate_h1_from_h4(usdjpy_h4)
        usdjpy = merge_and_fill(usdjpy_h1, usdjpy_h1_gen)
    if usdjpy_d1 is not None and usdjpy is None:
        usdjpy_h4_gen = generate_h4_from_d1(usdjpy_d1)
        usdjpy_h1_gen = generate_h1_from_h4(usdjpy_h4_gen)
        usdjpy = merge_and_fill(usdjpy, usdjpy_h1_gen)

    if m15 is None or h1 is None or h4 is None:
        print("[ERR] XAUUSD data insufficient")
        return None, {"error": "data"}

    real_m15 = len(m15_real) if m15_real is not None else 0
    real_h1 = len(h1_real) if h1_real is not None else 0
    real_h4 = len(h4_real) if h4_real is not None else 0
    print(f"  XAUUSD M15: {len(m15):,} bars (real: {real_m15:,}) ({m15.index[0]} ~ {m15.index[-1]})")
    print(f"  XAUUSD H1:  {len(h1):,} bars (real: {real_h1:,})")
    print(f"  XAUUSD H4:  {len(h4):,} bars (real: {len(h4_real) if h4_real is not None else 0:,})")
    if usdjpy is not None:
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

        # v12.0: Statistical Significance Analysis
        from backtest_gold import StatisticalSignificanceAnalyzer
        ssa = StatisticalSignificanceAnalyzer(bt.trades, cfg.INITIAL_BALANCE)
        ssa_results = ssa.run()
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

    usdjpy_h1_real = load_csv("USDJPY_H1.csv")
    usdjpy_h4_real = load_csv("USDJPY_H4.csv")
    usdjpy_d1_real = load_csv("USDJPY_D1.csv")
    usdjpy_m15_real = load_csv("USDJPY_M15.csv")

    # H4: 実データ + D1から補間
    h4_df = usdjpy_h4_real
    if usdjpy_d1_real is not None:
        h4_gen = generate_h4_from_d1(usdjpy_d1_real)
        h4_df = merge_and_fill(usdjpy_h4_real, h4_gen)
    if h4_df is None and usdjpy_h1_real is not None:
        h4_df = usdjpy_h1_real.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
        }).dropna()

    # H1: 実データ + H4から補間
    usdjpy_h1 = usdjpy_h1_real
    if h4_df is not None:
        h1_gen = generate_h1_from_h4(h4_df)
        usdjpy_h1 = merge_and_fill(usdjpy_h1_real, h1_gen)

    if usdjpy_h1 is None:
        print("[ERR] USDJPY data insufficient")
        return None, {"error": "data"}

    # M15: 実データ + H1から補間
    m15_gen = generate_m15_from_h1(usdjpy_h1)
    m15_df = merge_and_fill(usdjpy_m15_real, m15_gen)

    real_h1 = len(usdjpy_h1_real) if usdjpy_h1_real is not None else 0
    print(f"  USDJPY H1:  {len(usdjpy_h1):,} bars (real: {real_h1:,}) ({usdjpy_h1.index[0]} ~ {usdjpy_h1.index[-1]})")
    print(f"  USDJPY H4:  {len(h4_df):,} bars")
    print(f"  USDJPY M15: {len(m15_df):,} bars")

    cfg = USDJPYConfig()
    bt = USDJPYBacktester(cfg)
    bt.run(h4_df, usdjpy_h1, m15_df)
    del usdjpy_h1, m15_df, h4_df  # free memory
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
