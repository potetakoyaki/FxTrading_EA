"""Quick comparison: v8.2 (USE_REGIME_ADAPTIVE=False) vs v9.0 (USE_REGIME_ADAPTIVE=True)"""
import os, sys, time
from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester
import pandas as pd

# Load data once
print("[CSV] Loading data...")
m15_real = load_csv("XAUUSD_M15.csv")
h1_real = load_csv("XAUUSD_H1.csv")
h4 = load_csv("XAUUSD_H4.csv")
usdjpy = load_csv("USDJPY_H1.csv")
h1_gen = generate_h1_from_h4(h4)
h1 = merge_and_fill(h1_real, h1_gen)
m15_gen = generate_m15_from_h1(h1)
m15 = merge_and_fill(m15_real, m15_gen)

# ---- Test periods ----
periods = [
    ("2024-2026 (Recent)", "2024-01-01", "2026-03-20"),
    ("2020-2022 (COVID)", "2020-01-01", "2022-12-31"),
    ("2022-2024 (Range)", "2022-01-01", "2024-12-31"),
]

for period_name, start, end in periods:
    print(f"\n{'='*70}")
    print(f" Period: {period_name}")
    print(f"{'='*70}")

    # Filter data to period
    m15_p = m15[(m15.index >= start) & (m15.index <= end)]
    h1_p = h1[(h1.index >= pd.Timestamp(start) - pd.Timedelta(days=60)) & (h1.index <= end)]
    h4_p = h4[(h4.index >= pd.Timestamp(start) - pd.Timedelta(days=120)) & (h4.index <= end)]
    usdjpy_p = usdjpy[(usdjpy.index >= pd.Timestamp(start) - pd.Timedelta(days=60)) & (usdjpy.index <= end)]

    if len(m15_p) < 1000:
        print(f"  [SKIP] Not enough data ({len(m15_p)} bars)")
        continue

    results = {}

    # v8.2 baseline (regime adaptive OFF)
    cfg_v82 = GoldConfig()
    cfg_v82.USE_REGIME_ADAPTIVE = False  # Disable v9.0
    bt_v82 = GoldBacktester(cfg_v82)
    t0 = time.time()
    bt_v82.run(h4_p.copy(), h1_p.copy(), m15_p.copy(), usdjpy_df=usdjpy_p.copy())
    t_v82 = time.time() - t0
    rpt_v82 = bt_v82.get_report()

    # v9.0 regime adaptive (ON)
    cfg_v90 = GoldConfig()
    cfg_v90.USE_REGIME_ADAPTIVE = True  # Enable v9.0
    bt_v90 = GoldBacktester(cfg_v90)
    t0 = time.time()
    bt_v90.run(h4_p.copy(), h1_p.copy(), m15_p.copy(), usdjpy_df=usdjpy_p.copy())
    t_v90 = time.time() - t0
    rpt_v90 = bt_v90.get_report()

    # Compare
    def extract(rpt):
        if not rpt or "error" in rpt:
            return {"PF": 0, "WR": 0, "DD": 0, "Return": 0, "Trades": 0}
        return {
            "PF": float(rpt.get("PF", "0").replace("INF", "99")),
            "WR": float(rpt.get("Win Rate", "0%").split("%")[0]),
            "DD": float(rpt.get("Max DD", "0%").split("%")[0]),
            "Return": float(rpt.get("Return", "0%").replace("%", "").replace("+", "")),
            "Trades": rpt.get("Trades", 0),
            "BUY": rpt.get("BUY", ""),
            "SELL": rpt.get("SELL", ""),
        }

    v82 = extract(rpt_v82)
    v90 = extract(rpt_v90)

    print(f"\n  {'Metric':<12} {'v8.2':>12} {'v9.0':>12} {'Delta':>12}")
    print(f"  {'-'*50}")
    print(f"  {'PF':<12} {v82['PF']:>12.2f} {v90['PF']:>12.2f} {v90['PF']-v82['PF']:>+12.2f}")
    print(f"  {'WinRate':<12} {v82['WR']:>11.1f}% {v90['WR']:>11.1f}% {v90['WR']-v82['WR']:>+11.1f}%")
    print(f"  {'MaxDD':<12} {v82['DD']:>11.1f}% {v90['DD']:>11.1f}% {v90['DD']-v82['DD']:>+11.1f}%")
    print(f"  {'Return':<12} {v82['Return']:>+11.1f}% {v90['Return']:>+11.1f}% {v90['Return']-v82['Return']:>+11.1f}%")
    print(f"  {'Trades':<12} {v82['Trades']:>12} {v90['Trades']:>12} {v90['Trades']-v82['Trades']:>+12}")
    print(f"  {'Time(s)':<12} {t_v82:>11.1f}s {t_v90:>11.1f}s")

    # v9.0 regime stats
    if cfg_v90.USE_REGIME_ADAPTIVE:
        total_bars_counted = sum(bt_v90.regime_stats.values())
        print(f"\n  v9.0 Regime Distribution:")
        for regime_name, count in bt_v90.regime_stats.items():
            pct = count / total_bars_counted * 100 if total_bars_counted > 0 else 0
            trades_in_regime = len(bt_v90.regime_trades.get(regime_name, []))
            print(f"    {regime_name:>10}: {count:>6} bars ({pct:>5.1f}%) | {trades_in_regime} entries")

        # Entry type breakdown
        trade_df = pd.DataFrame(bt_v90.trades)
        if not trade_df.empty:
            print(f"\n  v9.0 Entry Type Performance:")
            for et in ['normal', 'pyramid', 'mean_reversion', 'reversal']:
                subset = trade_df[trade_df['entry_type'] == et]
                if len(subset) > 0:
                    et_wins = subset[subset['pnl_jpy'] > 0]
                    et_wr = len(et_wins) / len(subset) * 100
                    et_pnl = subset['pnl_jpy'].sum()
                    loss_sum = abs(subset[subset['pnl_jpy'] <= 0]['pnl_jpy'].sum())
                    et_pf = et_wins['pnl_jpy'].sum() / loss_sum if loss_sum > 0 else float('inf')
                    print(f"    {et:>16}: {len(subset):>4} trades | WR={et_wr:>5.1f}% | PF={et_pf:>5.2f} | PnL={et_pnl:>+10,.0f} JPY")

    # Monthly comparison for recent period
    if "2024-2026" in period_name and rpt_v90 and "Monthly" in rpt_v90:
        print(f"\n  v9.0 Monthly PnL:")
        for m, p in rpt_v90["Monthly"].items():
            bar = "#" * max(1, int(abs(p) / 2000))
            icon = "[+]" if p > 0 else "[-]"
            print(f"    {m}: {icon} {p:+,.0f} JPY {bar}")

print(f"\n{'='*70}")
print(f" Comparison Complete")
print(f"{'='*70}")
