"""
GoldAlpha v21 Optimizer - Re-optimize entry/exit UNDER D1 regime constraint
v20 fixed WFA (3/8→5/8) but lost trades (1082→808)
v21: re-optimize entry params with D1Slope(5,0.002) active to recover trades
while maintaining WFA improvement
"""

import sys
import os
import time
import numpy as np
import pandas as pd
from itertools import product

sys.path.insert(0, "/tmp/FxTrading_EA")
from backtest_goldalpha import (
    load_csv, GoldAlphaConfig, precompute_indicators,
    backtest_goldalpha, calc_metrics, run_wfa,
    np_ema, np_sma, resample_to_daily
)
from optimize_v20 import make_v19_cfg, backtest_with_regime, run_regime_wfa


REGIME_TYPE = "d1_slope"
REGIME_PARAMS = {"slope_bars": 5, "min_slope": 0.002}


def make_base_cfg(**overrides):
    """v12 base with D1 regime always on (via optimizer filtering)."""
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    # Start from v19 winner but allow overrides
    cfg.SL_ATR_Mult = 3.1; cfg.Trail_ATR = 3.5; cfg.BE_ATR = 0.8
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.24
    cfg.EMA_Zone_ATR = 0.65; cfg.ATR_Filter = 0.7; cfg.D1_Tolerance = 0.002
    cfg.MaxPositions = 3; cfg.MinLot = 0.01; cfg.MaxLot = 0.50
    cfg.INITIAL_BALANCE = 300_000
    cfg.USE_EMA_SLOPE = True; cfg.EMA_SLOPE_BARS = 5
    cfg.USE_STRUCTURE = False; cfg.USE_TIME_DECAY = False
    cfg.USE_VOL_REGIME = False; cfg.USE_SESSION_FILTER = False
    cfg.USE_RSI_CONFIRM = False; cfg.USE_ADX_FILTER = False
    cfg.USE_PARTIAL_CLOSE = False; cfg.USE_W1_SEPARATION = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_bt(h4_df, total_days, **params):
    """Run backtest with D1 regime filter always active."""
    cfg = make_base_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, REGIME_TYPE, REGIME_PARAMS)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def score(m, min_trades=400):
    if m is None or m["n_trades"] < min_trades:
        return -999
    if m["pf"] < 1.2:
        return -999
    return (min(m["pf"], 3.0) * 15
            + min(m["n_trades"], 1500) * 0.005
            - max(0, m["max_dd"] - 25) * 0.3
            + max(0, m["win_rate"] - 55) * 0.2)


def grid(h4_df, total_days, grid_dict, fixed, label, min_t=400, top_n=10):
    keys = list(grid_dict.keys())
    combos = list(product(*grid_dict.values()))
    n = len(combos)
    print(f"\n  {label}: {n} combos")
    results = []
    best = -999
    for idx, combo in enumerate(combos):
        params = {**fixed, **dict(zip(keys, combo))}
        m = run_bt(h4_df, total_days, **params)
        s = score(m, min_t)
        if s > -999:
            results.append((params, m, s))
            if s > best:
                best = s
                print(f"    [{idx+1}/{n}] BEST s={s:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        if (idx + 1) % 200 == 0:
            print(f"    [{idx+1}/{n}] {len(results)} valid...")
    results.sort(key=lambda x: x[2], reverse=True)
    if results:
        print(f"  -> {len(results)} valid, best={results[0][2]:.1f}")
    else:
        print(f"  -> 0 valid!")
    return results[:top_n]


def rng(v, step, n=3):
    return sorted(set([round(v + step * i, 4) for i in range(-n, n + 1) if v + step * i > 0]))


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v21 - Re-Optimize Under D1 Regime Constraint")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print(f"Regime: {REGIME_TYPE} {REGIME_PARAMS}")
    print("=" * 80)

    # v20 baseline (v19 params + D1 regime)
    m0 = run_bt(h4_df, total_days)
    print(f"\nv20 baseline (under regime): PF={m0['pf']:.2f} T={m0['n_trades']} "
          f"DD={m0['max_dd']:.1f}% WR={m0['win_rate']:.1f}%")

    # ================================================================
    # STAGE 1: Entry re-optimization under regime constraint
    # Wider search to recover lost trades
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Entry Re-Optimization (under regime)")
    print("=" * 80)

    # Try relaxing filters to get more trades WITH the regime filter active
    entry_grid = {
        "EMA_Zone_ATR": [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80],
        "ATR_Filter":   [0.30, 0.40, 0.50, 0.60, 0.70],
        "BodyRatio":    [0.20, 0.24, 0.28, 0.32],
        "MaxPositions": [2, 3, 4, 5],
        "D1_Tolerance": [0.002, 0.003, 0.005, 0.007],
    }
    # 8*5*4*4*4 = 2560 combos
    fixed_exit = {"SL_ATR_Mult": 3.1, "Trail_ATR": 3.5, "BE_ATR": 0.8}

    top_entry = grid(h4_df, total_days, entry_grid, fixed_exit,
                     "Entry (coarse)", min_t=400, top_n=15)

    # Fine-tune top 3
    print("\n  Refining top 3...")
    fine_results = []
    for rank, (params, m, s) in enumerate(top_entry[:3]):
        fine_grid = {
            "EMA_Zone_ATR": rng(params["EMA_Zone_ATR"], 0.025, 2),
            "ATR_Filter": rng(params["ATR_Filter"], 0.05, 2),
            "BodyRatio": rng(params["BodyRatio"], 0.02, 2),
            "MaxPositions": sorted(set([max(2, params["MaxPositions"]-1),
                                        params["MaxPositions"],
                                        min(6, params["MaxPositions"]+1)])),
            "D1_Tolerance": rng(params["D1_Tolerance"], 0.001, 2),
        }
        fine = grid(h4_df, total_days, fine_grid, fixed_exit,
                    f"Entry fine R{rank+1}", min_t=400, top_n=5)
        fine_results.extend(fine)

    all_entry = top_entry + fine_results
    all_entry.sort(key=lambda x: x[2], reverse=True)
    seen = set()
    unique_entry = []
    for p, m, s in all_entry:
        key = tuple(sorted((k, v) for k, v in p.items() if k in entry_grid or k in fixed_exit))
        if key not in seen:
            seen.add(key)
            unique_entry.append((p, m, s))

    print(f"\n  Top 10 entry configs:")
    for i, (p, m, s) in enumerate(unique_entry[:10]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
              f"| Zone={p['EMA_Zone_ATR']:.2f} ATR_F={p['ATR_Filter']:.2f} "
              f"Body={p['BodyRatio']:.2f} MaxP={p['MaxPositions']} D1T={p['D1_Tolerance']:.3f}")

    # ================================================================
    # STAGE 2: Exit re-optimization
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: Exit Re-Optimization")
    print("=" * 80)

    exit_grid = {
        "SL_ATR_Mult": [1.5, 1.8, 2.0, 2.3, 2.5, 2.8, 3.1, 3.5],
        "Trail_ATR":   [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
        "BE_ATR":      [0.3, 0.5, 0.8, 1.0, 1.5],
    }
    # 8*6*5 = 240 per config * 5 = 1200

    exit_results = []
    for rank, (entry_p, entry_m, entry_s) in enumerate(unique_entry[:5]):
        entry_fixed = {k: entry_p[k] for k in
                       ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_exit = grid(h4_df, total_days, exit_grid, entry_fixed,
                        f"Exit on Entry-R{rank+1}", min_t=400, top_n=5)
        exit_results.extend(top_exit)

    exit_results.sort(key=lambda x: x[2], reverse=True)

    # Fine-tune top 3
    print("\n  Refining top 3 exit configs...")
    fine_exit = []
    for rank, (params, m, s) in enumerate(exit_results[:3]):
        fg = {
            "SL_ATR_Mult": rng(params["SL_ATR_Mult"], 0.15, 2),
            "Trail_ATR": rng(params["Trail_ATR"], 0.2, 2),
            "BE_ATR": rng(params["BE_ATR"], 0.1, 2),
        }
        ef = {k: params[k] for k in ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_f = grid(h4_df, total_days, fg, ef, f"Exit fine R{rank+1}", min_t=400, top_n=5)
        fine_exit.extend(top_f)

    all_exit = exit_results + fine_exit
    all_exit.sort(key=lambda x: x[2], reverse=True)
    seen2 = set()
    unique_all = []
    for p, m, s in all_exit:
        key = tuple(sorted(p.items()))
        if key not in seen2:
            seen2.add(key)
            unique_all.append((p, m, s))

    print(f"\n  Top 10 entry+exit:")
    for i, (p, m, s) in enumerate(unique_all[:10]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}% "
              f"| SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f}")

    # ================================================================
    # STAGE 3: Feature toggles
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 3: Feature Toggles (under regime)")
    print("=" * 80)

    feature_sets = [
        ("None", {}),
        ("Slope3", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3}),
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("Slope8", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 8}),
        ("NoSlope", {"USE_EMA_SLOPE": False}),
        ("S2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("S3", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("W1Sep3", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("Slope5+S2", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                       "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("Slope5+TD30", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
    ]

    feat_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique_all[:5]):
        for fname, fparams in feature_sets:
            params = {**base_p, **fparams}
            m = run_bt(h4_df, total_days, **params)
            s = score(m, 400)
            if s > -999:
                feat_results.append((params, m, s, f"R{rank+1}+{fname}"))

    feat_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 15 with features:")
    for p, m, s, label in feat_results[:15]:
        print(f"  {label:>25} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}%")

    # ================================================================
    # STAGE 4: WFA Validation (Top 10)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: WFA Validation")
    print("=" * 80)

    candidates = [(p, m, s, "base") for p, m, s in unique_all[:5]]
    candidates += [(p, m, s, label) for p, m, s, label in feat_results[:8]]
    seen3 = set()
    final_cands = []
    for p, m, s, label in candidates:
        key = tuple(sorted(p.items()))
        if key not in seen3:
            seen3.add(key)
            final_cands.append((p, m, s, label))
    final_cands.sort(key=lambda x: x[2], reverse=True)

    validated = []
    for ci, (params, m, s, label) in enumerate(final_cands[:10]):
        cfg = make_base_cfg(**params)
        wfa = run_regime_wfa(h4_df, cfg, REGIME_TYPE, REGIME_PARAMS)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0
        total_wfa_t = sum(r["n_trades"] for r in wfa)

        # OOS
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        tr_oos, _, _ = backtest_with_regime(sub, cfg, REGIME_TYPE, REGIME_PARAMS)
        oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

        # Composite score
        wfa_score = (n_pass / 8) * 40
        wfa_score += min(avg_pf, 2.5) * 5
        base_s = score(m, 400)
        oos_s = 0
        if m_oos and m_oos["n_trades"] >= 20:
            oos_s = min(m_oos["pf"], 4.0) * 3
        final_score = base_s + wfa_score + oos_s

        oos_pf = m_oos["pf"] if m_oos else 0
        oos_daily = m_oos["daily_jpy"] if m_oos else 0
        print(f"  [{ci+1}/10] {label:>25} WFA={n_pass}/8 AvgPF={avg_pf:.2f} "
              f"PF={m['pf']:.2f} T={m['n_trades']} OOS_PF={oos_pf:.2f} FS={final_score:.1f}")

        validated.append({
            "params": params, "label": label, "metrics": m,
            "wfa_pass": n_pass, "wfa_avg_pf": avg_pf, "wfa_min_pf": min_pf,
            "oos": m_oos, "final_score": final_score, "wfa": wfa,
        })

    validated.sort(key=lambda x: x["final_score"], reverse=True)

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)
    print(f"{'Rk':>2} {'FS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'OOS_PF':>6} {'OOS_D':>6} | Label")
    print("-" * 85)
    for i, v in enumerate(validated):
        m = v["metrics"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        oos_daily = v["oos"]["daily_jpy"] if v["oos"] else 0
        print(f"{i+1:2d} {v['final_score']:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['wfa_avg_pf']:5.2f} "
              f"{oos_pf:6.2f} {oos_daily:6.0f} | {v['label']}")

    # ================================================================
    # WINNER
    # ================================================================
    W = validated[0]
    wp = W["params"]
    print("\n" + "=" * 80)
    print("WINNER")
    print("=" * 80)
    for k in sorted(wp.keys()):
        v = wp[k]
        if isinstance(v, bool) and not v:
            continue
        print(f"  {k}: {v}")
    print(f"\n  Full: PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']} DD={W['metrics']['max_dd']:.1f}%")
    print(f"  WFA: {W['wfa_pass']}/8, Avg PF={W['wfa_avg_pf']:.2f}")
    if W["oos"]:
        print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} Daily={W['oos']['daily_jpy']:.0f}")

    # WFA details
    print(f"\n  WFA Window Details:")
    for j, w in enumerate(W["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"    W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} DD={w['max_dd']:5.1f}% [{st}]")

    # Risk scaling
    print(f"\n  Risk Scaling:")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>5} {'Daily':>8} {'Final':>12}")
    print("  " + "-" * 60)
    best_risk = None
    for risk, maxlot in [(0.20, 0.10), (0.50, 0.20), (1.0, 0.30),
                          (1.5, 0.50), (2.0, 0.50), (2.5, 0.75),
                          (3.0, 1.00), (3.5, 1.50)]:
        cfg_r = make_base_cfg(**{**wp, "RiskPct": risk, "MaxLot": maxlot})
        tr_r, _, _ = backtest_with_regime(h4_df, cfg_r, REGIME_TYPE, REGIME_PARAMS)
        m_r = calc_metrics(tr_r, cfg_r.INITIAL_BALANCE, total_days)
        if m_r:
            mark = " ***" if m_r["daily_jpy"] >= 5000 and m_r["max_dd"] < 50 else ""
            print(f"  {risk:6.2f} {maxlot:6.2f} | {m_r['pf']:5.2f} {m_r['n_trades']:5d} "
                  f"{m_r['max_dd']:5.1f} {m_r['daily_jpy']:8.0f} "
                  f"{m_r['final_balance']:12,.0f}{mark}")
            if m_r["daily_jpy"] >= 5000 and m_r["max_dd"] < 50 and best_risk is None:
                best_risk = risk

    # OOS risk scaling
    print(f"\n  OOS 2024-2026 Risk Scaling:")
    for risk, maxlot in [(1.0, 0.30), (1.5, 0.50), (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        cfg_r = make_base_cfg(**{**wp, "RiskPct": risk, "MaxLot": maxlot})
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        tr_r, _, _ = backtest_with_regime(sub, cfg_r, REGIME_TYPE, REGIME_PARAMS)
        oos_r = [t for t in tr_r if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_or = calc_metrics(oos_r, cfg_r.INITIAL_BALANCE, oos_days)
        if m_or:
            mark = " ***" if m_or["daily_jpy"] >= 5000 else ""
            print(f"    Risk={risk}%: PF={m_or['pf']:.2f} T={m_or['n_trades']} "
                  f"DD={m_or['max_dd']:.1f}% Daily={m_or['daily_jpy']:.0f}{mark}")

    # Year-by-year
    rl = best_risk if best_risk else 2.0
    ml_map = {0.2: 0.10, 0.5: 0.20, 1.0: 0.30, 1.5: 0.50,
              2.0: 0.50, 2.5: 0.75, 3.0: 1.00, 3.5: 1.50}
    ml = ml_map.get(rl, 0.50)
    cfg_f = make_base_cfg(**{**wp, "RiskPct": rl, "MaxLot": ml})
    tr_f, _, _ = backtest_with_regime(h4_df, cfg_f, REGIME_TYPE, REGIME_PARAMS)
    df = pd.DataFrame(tr_f)
    if len(df) > 0:
        df["year"] = pd.to_datetime(df["close_time"]).dt.year
        m_f = calc_metrics(tr_f, cfg_f.INITIAL_BALANCE, total_days)
        print(f"\n  Year-by-Year (Risk={rl}%):")
        print(f"  {'Year':>6} {'T':>4} {'PF':>6} {'WR%':>5} {'PnL':>12} {'Daily':>8}")
        print("  " + "-" * 50)
        for yr, grp in df.groupby("year"):
            pnls = grp["pnl_jpy"].values
            wins = (pnls > 0).sum()
            n = len(pnls)
            wr = wins / n * 100 if n > 0 else 0
            gp = pnls[pnls > 0].sum() if wins > 0 else 0
            gl = abs(pnls[pnls <= 0].sum()) if (n - wins) > 0 else 1
            pf = gp / gl if gl > 0 else float("inf")
            yr_days = 365 if yr < df["year"].max() else max(1, (h4_df.index[-1] - pd.Timestamp(f"{yr}-01-01")).days)
            daily = pnls.sum() / max(1, yr_days)
            print(f"  {yr:6d} {n:4d} {pf:6.2f} {wr:5.0f} {pnls.sum():+12,.0f} {daily:8.0f}")
        print(f"\n  TOTAL: PF={m_f['pf']:.2f} T={m_f['n_trades']} DD={m_f['max_dd']:.1f}% "
              f"Daily={m_f['daily_jpy']:.0f} Final={m_f['final_balance']:,.0f}")

    # Comparison summary
    print("\n" + "=" * 80)
    print("VERSION COMPARISON")
    print("=" * 80)
    print(f"  v19: PF=2.01 T=1082 WFA=3/8 (no regime)")
    print(f"  v20: PF=2.23 T=808  WFA=5/8 (D1Slope(5,0.002), v19 params)")
    print(f"  v21: PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']}  WFA={W['wfa_pass']}/8 "
          f"(D1Slope(5,0.002), re-optimized)")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V21 OPTIMIZATION COMPLETE ===")


if __name__ == "__main__":
    main()
