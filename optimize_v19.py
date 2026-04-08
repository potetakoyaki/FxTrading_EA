"""
GoldAlpha v19 Optimizer - WFA-Robust Optimization
v12 base → entry grid → exit grid → features → WFA-integrated scoring
Target: 500+ trades, PF >= 1.5, WFA >= 6/8, 300K JPY daily 5000+ JPY

Key difference from v16: WFA robustness is part of the scoring, not just validation.
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
    backtest_goldalpha, calc_metrics, run_wfa
)


def make_cfg(**overrides):
    """v12 base config with overrides"""
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    cfg.SL_ATR_Mult = 2.0; cfg.Trail_ATR = 2.5; cfg.BE_ATR = 1.5
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.32
    cfg.EMA_Zone_ATR = 0.4; cfg.ATR_Filter = 0.6; cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 2; cfg.MinLot = 0.01; cfg.MaxLot = 0.50
    cfg.INITIAL_BALANCE = 300_000
    cfg.USE_STRUCTURE = False; cfg.USE_TIME_DECAY = False
    cfg.USE_EMA_SLOPE = False; cfg.USE_VOL_REGIME = False
    cfg.USE_SESSION_FILTER = False; cfg.USE_RSI_CONFIRM = False
    cfg.USE_ADX_FILTER = False; cfg.USE_PARTIAL_CLOSE = False
    cfg.USE_W1_SEPARATION = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def score_basic(m, min_trades=400):
    """Basic score for grid stages (no WFA)."""
    if m is None or m["n_trades"] < min_trades:
        return -999
    pf = m["pf"]
    if pf < 1.2:
        return -999
    # Reward PF, trade count, win rate; penalize DD
    return (min(pf, 3.0) * 15
            + min(m["n_trades"], 1500) * 0.005
            - max(0, m["max_dd"] - 25) * 0.3
            + max(0, m["win_rate"] - 55) * 0.2)


def score_with_wfa(m, wfa_results, oos_m=None, min_trades=400):
    """Composite score incorporating WFA robustness."""
    if m is None or m["n_trades"] < min_trades:
        return -999
    if m["pf"] < 1.2:
        return -999

    base = score_basic(m, min_trades)

    # WFA scoring (heavily weighted)
    if wfa_results:
        n_pass = sum(1 for r in wfa_results if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa_results])
        min_pf = min(r["pf"] for r in wfa_results)
        wfa_trades = sum(r["n_trades"] for r in wfa_results)

        wfa_score = (n_pass / 8) * 40  # 0-40 points for WFA pass rate
        wfa_score += min(avg_pf, 2.5) * 5  # 0-12.5 for avg PF
        wfa_score += max(0, min_pf - 0.5) * 5  # Reward worst-window resilience
        # Penalize if WFA trades are too few
        wfa_score += min(wfa_trades, 500) * 0.005
    else:
        wfa_score = 0

    # OOS scoring
    oos_score = 0
    if oos_m and oos_m["n_trades"] >= 30:
        oos_score = min(oos_m["pf"], 4.0) * 3
        oos_score += min(oos_m["daily_jpy"], 8000) * 0.001

    return base + wfa_score + oos_score


def run_backtest(h4_df, total_days, **params):
    cfg = make_cfg(**params)
    ind = precompute_indicators(h4_df, cfg)
    trades, eq, final = backtest_goldalpha(*ind, cfg)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    return m


def quick_wfa(h4_df, params, n_windows=8):
    """Quick WFA check."""
    cfg = make_cfg(**params)
    results = run_wfa(h4_df, cfg, n_windows=n_windows)
    return results


def oos_test(h4_df, params, oos_start="2024-01-01"):
    """OOS test on 2024-2026 data."""
    cfg = make_cfg(**params)
    sub = h4_df[h4_df.index >= "2022-01-01"].copy()
    ind = precompute_indicators(sub, cfg)
    trades, _, _ = backtest_goldalpha(*ind, cfg)
    oos_trades = [t for t in trades if t["open_time"] >= pd.Timestamp(oos_start)]
    oos_days = max(1, (sub.index[-1] - pd.Timestamp(oos_start)).days)
    return calc_metrics(oos_trades, cfg.INITIAL_BALANCE, oos_days)


def grid_stage(h4_df, total_days, grid, fixed_params, label, min_trades=400, top_n=10):
    """Run a grid search stage."""
    keys = list(grid.keys())
    combos = list(product(*grid.values()))
    n = len(combos)
    print(f"\n  {label}: {n} combos")

    results = []
    best = -999
    for idx, combo in enumerate(combos):
        params = {**fixed_params, **dict(zip(keys, combo))}
        m = run_backtest(h4_df, total_days, **params)
        s = score_basic(m, min_trades)
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
        print(f"  -> {len(results)} valid, best score={results[0][2]:.1f}")
    else:
        print(f"  -> 0 valid!")
    return results[:top_n]


def rng(v, step, n=3):
    """Generate range around value."""
    return sorted(set([round(v + step * i, 4) for i in range(-n, n+1) if v + step * i > 0]))


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v19 WFA-Robust Optimizer")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print("=" * 80)

    # ================================================================
    # v12 Baseline
    # ================================================================
    m0 = run_backtest(h4_df, total_days)
    print(f"\nv12 baseline: PF={m0['pf']:.2f} T={m0['n_trades']} DD={m0['max_dd']:.1f}% "
          f"WR={m0['win_rate']:.1f}% Daily={m0['daily_jpy']:.0f}")

    wfa0 = quick_wfa(h4_df, {})
    n_pass0 = sum(1 for r in wfa0 if r["pf"] > 1.0)
    print(f"v12 WFA: {n_pass0}/8 PASS")

    # ================================================================
    # STAGE 1: Entry optimization (coarse)
    # Focus on zones that worked in v13/v16/v18 while adding new ranges
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Entry Parameter Optimization (Coarse)")
    print("=" * 80)

    entry_grid = {
        "EMA_Zone_ATR": [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
        "ATR_Filter":   [0.20, 0.30, 0.40, 0.50, 0.60, 0.70],
        "BodyRatio":    [0.26, 0.30, 0.32, 0.34, 0.38],
        "MaxPositions": [2, 3, 4],
        "D1_Tolerance": [0.003, 0.005, 0.007],
    }
    # 7*6*5*3*3 = 1890 combos
    fixed_exit = {"SL_ATR_Mult": 2.0, "Trail_ATR": 2.5, "BE_ATR": 1.5}

    top_entry = grid_stage(h4_df, total_days, entry_grid, fixed_exit,
                           "Entry (coarse)", min_trades=400, top_n=15)

    # Fine-tune top 5
    print("\n  Refining top 5 entry configs...")
    fine_entry_results = []
    for rank, (params, m, s) in enumerate(top_entry[:5]):
        fine_grid = {
            "EMA_Zone_ATR": rng(params["EMA_Zone_ATR"], 0.025, 2),
            "ATR_Filter": rng(params["ATR_Filter"], 0.05, 2),
            "BodyRatio": rng(params["BodyRatio"], 0.02, 2),
            "MaxPositions": sorted(set([max(2, params["MaxPositions"]-1),
                                        params["MaxPositions"],
                                        min(5, params["MaxPositions"]+1)])),
            "D1_Tolerance": rng(params["D1_Tolerance"], 0.001, 2),
        }
        top_fine = grid_stage(h4_df, total_days, fine_grid, fixed_exit,
                              f"Entry fine R{rank+1}", min_trades=400, top_n=5)
        fine_entry_results.extend(top_fine)

    all_entry = top_entry + fine_entry_results
    all_entry.sort(key=lambda x: x[2], reverse=True)
    seen = set()
    unique_entry = []
    for p, m, s in all_entry:
        key = (p["EMA_Zone_ATR"], p["ATR_Filter"], p["BodyRatio"],
               p["MaxPositions"], p["D1_Tolerance"])
        if key not in seen:
            seen.add(key)
            unique_entry.append((p, m, s))

    print(f"\n  Top 10 entry configs:")
    print(f"  {'Rk':>3} {'Score':>6} {'PF':>5} {'T':>5} {'DD%':>5} {'WR%':>4} "
          f"| {'Zone':>5} {'ATR_F':>5} {'Body':>5} {'MaxP':>4} {'D1T':>5}")
    print("  " + "-" * 70)
    for i, (p, m, s) in enumerate(unique_entry[:10]):
        print(f"  {i+1:3d} {s:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"| {p['EMA_Zone_ATR']:5.2f} {p['ATR_Filter']:5.2f} "
              f"{p['BodyRatio']:5.2f} {p['MaxPositions']:4d} {p['D1_Tolerance']:5.3f}")

    # ================================================================
    # STAGE 2: Exit parameters on top 5 entry configs
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: Exit Parameter Optimization")
    print("=" * 80)

    exit_grid = {
        "SL_ATR_Mult": [1.2, 1.5, 1.8, 2.0, 2.3, 2.5, 2.8, 3.0],
        "Trail_ATR":   [1.5, 2.0, 2.5, 3.0, 3.5],
        "BE_ATR":      [0.3, 0.5, 0.8, 1.0, 1.5, 2.0],
    }
    # 8*5*6 = 240 per config * 5 = 1200

    exit_results = []
    for rank, (entry_p, entry_m, entry_s) in enumerate(unique_entry[:5]):
        entry_fixed = {k: entry_p[k] for k in
                       ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_exit = grid_stage(h4_df, total_days, exit_grid, entry_fixed,
                              f"Exit on Entry-R{rank+1}", min_trades=400, top_n=5)
        exit_results.extend(top_exit)

    exit_results.sort(key=lambda x: x[2], reverse=True)

    # Fine-tune top 3 exit
    print("\n  Refining top 3 exit configs...")
    fine_exit_results = []
    for rank, (params, m, s) in enumerate(exit_results[:3]):
        fine_grid = {
            "SL_ATR_Mult": rng(params["SL_ATR_Mult"], 0.1, 2),
            "Trail_ATR": rng(params["Trail_ATR"], 0.15, 2),
            "BE_ATR": rng(params["BE_ATR"], 0.1, 2),
        }
        entry_fixed = {k: params[k] for k in
                       ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_fine = grid_stage(h4_df, total_days, fine_grid, entry_fixed,
                              f"Exit fine R{rank+1}", min_trades=400, top_n=5)
        fine_exit_results.extend(top_fine)

    all_exit = exit_results + fine_exit_results
    all_exit.sort(key=lambda x: x[2], reverse=True)
    seen2 = set()
    unique_all = []
    for p, m, s in all_exit:
        key = tuple(sorted(p.items()))
        if key not in seen2:
            seen2.add(key)
            unique_all.append((p, m, s))

    print(f"\n  Top 10 entry+exit configs:")
    print(f"  {'Rk':>3} {'Score':>6} {'PF':>5} {'T':>5} {'DD%':>5} {'WR%':>4} "
          f"| {'SL':>4} {'Tr':>4} {'BE':>4} {'Zone':>5} {'ATR_F':>5} {'Body':>5} {'MaxP':>4}")
    print("  " + "-" * 80)
    for i, (p, m, s) in enumerate(unique_all[:10]):
        print(f"  {i+1:3d} {s:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"| {p['SL_ATR_Mult']:4.1f} {p['Trail_ATR']:4.1f} {p['BE_ATR']:4.1f} "
              f"{p['EMA_Zone_ATR']:5.2f} {p['ATR_Filter']:5.2f} "
              f"{p['BodyRatio']:5.2f} {p['MaxPositions']:4d}")

    # ================================================================
    # STAGE 3: Feature toggles on top 5
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 3: Feature Toggle Optimization")
    print("=" * 80)

    feature_sets = [
        ("None", {}),
        ("Slope3", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3}),
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("Slope8", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 8}),
        ("S2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("S3", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3}),
        ("TD25", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 25}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("VolReg", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("W1Sep", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.005}),
        ("W1Sep3", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("ADX20", {"USE_ADX_FILTER": True, "ADX_Period": 14, "ADX_MIN": 20}),
        ("ADX15", {"USE_ADX_FILTER": True, "ADX_Period": 14, "ADX_MIN": 15}),
        ("S2+Slope5", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                       "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("S2+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                     "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("Slope5+TD30", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("Slope5+W1Sep", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                          "USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("S2+Slope5+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                            "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("S2+ADX15", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                      "USE_ADX_FILTER": True, "ADX_Period": 14, "ADX_MIN": 15}),
    ]

    feat_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique_all[:5]):
        for feat_name, feat_params in feature_sets:
            params = {**base_p, **feat_params}
            m = run_backtest(h4_df, total_days, **params)
            s = score_basic(m, 400)
            if s > -999:
                feat_results.append((params, m, s, f"R{rank+1}+{feat_name}"))

    feat_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 20 with features:")
    print(f"  {'Label':>25} {'Score':>6} {'PF':>5} {'T':>5} {'DD%':>5} {'WR%':>4}")
    print("  " + "-" * 60)
    for p, m, s, label in feat_results[:20]:
        print(f"  {label:>25} {s:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f}")

    # ================================================================
    # STAGE 4: WFA-Integrated Validation on Top 12
    # This is the KEY difference from v16 - WFA is part of scoring
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: WFA-Integrated Validation (Top 12)")
    print("=" * 80)

    # Merge candidates: top 5 base + top 10 features
    candidates = [(p, m, s, "base") for p, m, s in unique_all[:5]]
    candidates += [(p, m, s, label) for p, m, s, label in feat_results[:10]]
    seen3 = set()
    final_cands = []
    for p, m, s, label in candidates:
        key = tuple(sorted((k, v) for k, v in p.items()))
        if key not in seen3:
            seen3.add(key)
            final_cands.append((p, m, s, label))
    final_cands.sort(key=lambda x: x[2], reverse=True)

    validated = []
    for ci, (params, m, s, label) in enumerate(final_cands[:12]):
        print(f"\n--- Candidate {ci+1}/{min(12,len(final_cands))} [{label}]: "
              f"PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% ---")

        # WFA
        wfa = quick_wfa(h4_df, params, n_windows=8)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0
        total_wfa_t = sum(r["n_trades"] for r in wfa)
        print(f"  WFA: {n_pass}/8 PASS, Avg PF={avg_pf:.2f}, Min PF={min_pf:.2f}, OOS T={total_wfa_t}")
        for j, w in enumerate(wfa):
            st = "PASS" if w["pf"] > 1.0 else "FAIL"
            print(f"    W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} DD={w['max_dd']:5.1f}% [{st}]")

        # OOS
        m_oos = oos_test(h4_df, params)
        if m_oos and m_oos["n_trades"] > 0:
            print(f"  OOS 2024-2026: PF={m_oos['pf']:.2f} T={m_oos['n_trades']} "
                  f"DD={m_oos['max_dd']:.1f}% Daily={m_oos['daily_jpy']:.0f}")
        else:
            m_oos = {"pf": 0, "n_trades": 0, "max_dd": 100, "daily_jpy": 0}

        # Composite score with WFA
        final_score = score_with_wfa(m, wfa, m_oos, min_trades=400)

        validated.append({
            "params": params,
            "label": label,
            "metrics": m,
            "basic_score": s,
            "wfa_results": wfa,
            "wfa_pass": n_pass,
            "wfa_avg_pf": avg_pf,
            "wfa_min_pf": min_pf,
            "oos": m_oos,
            "final_score": final_score,
        })

    validated.sort(key=lambda x: x["final_score"], reverse=True)

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING (WFA-Integrated)")
    print("=" * 80)
    print(f"{'Rk':>2} {'FScore':>7} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'OOS_PF':>6} {'OOS_D':>6} | Label")
    print("-" * 85)
    for i, v in enumerate(validated):
        m = v["metrics"]
        print(f"{i+1:2d} {v['final_score']:7.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['wfa_avg_pf']:5.2f} {v['wfa_min_pf']:5.2f} "
              f"{v['oos']['pf']:6.2f} {v['oos']['daily_jpy']:6.0f} | {v['label']}")

    # ================================================================
    # WINNER: Risk Scaling + Year-by-Year
    # ================================================================
    if not validated:
        print("\nNo valid candidates found!")
        return

    W = validated[0]
    wp = W["params"]
    print("\n" + "=" * 80)
    print("WINNER PARAMETERS")
    print("=" * 80)
    for k in sorted(wp.keys()):
        v = wp[k]
        if isinstance(v, bool) and not v:
            continue
        print(f"  {k}: {v}")

    print(f"\n  Full Period: PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']} "
          f"DD={W['metrics']['max_dd']:.1f}% WR={W['metrics']['win_rate']:.1f}%")
    print(f"  WFA: {W['wfa_pass']}/8, Avg PF={W['wfa_avg_pf']:.2f}, Min PF={W['wfa_min_pf']:.2f}")
    print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
          f"DD={W['oos']['max_dd']:.1f}% Daily={W['oos']['daily_jpy']:.0f}")

    # Risk scaling
    print(f"\n  Risk Scaling (Full Period):")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>5} {'Daily':>8} {'Final':>12}")
    print("  " + "-" * 60)
    best_risk = None
    for risk, maxlot in [(0.20, 0.10), (0.50, 0.20), (1.0, 0.30),
                          (1.5, 0.50), (2.0, 0.50), (2.5, 0.75),
                          (3.0, 1.00), (3.5, 1.50), (4.0, 2.00)]:
        rp = {**wp, "RiskPct": risk, "MaxLot": maxlot}
        m_r = run_backtest(h4_df, total_days, **rp)
        if m_r:
            mark = " ***" if m_r["daily_jpy"] >= 5000 and m_r["max_dd"] < 50 else ""
            print(f"  {risk:6.2f} {maxlot:6.2f} | {m_r['pf']:5.2f} {m_r['n_trades']:5d} "
                  f"{m_r['max_dd']:5.1f} {m_r['daily_jpy']:8.0f} "
                  f"{m_r['final_balance']:12,.0f}{mark}")
            if m_r["daily_jpy"] >= 5000 and m_r["max_dd"] < 50 and best_risk is None:
                best_risk = risk

    # OOS risk scaling
    print(f"\n  OOS 2024-2026 Risk Scaling:")
    best_oos_risk = None
    for risk, maxlot in [(0.50, 0.20), (1.0, 0.30), (1.5, 0.50),
                          (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        rp = {**wp, "RiskPct": risk, "MaxLot": maxlot}
        m_or = oos_test(h4_df, rp)
        if m_or:
            mark = " ***" if m_or["daily_jpy"] >= 5000 else ""
            print(f"    Risk={risk}%: PF={m_or['pf']:.2f} T={m_or['n_trades']} "
                  f"DD={m_or['max_dd']:.1f}% Daily={m_or['daily_jpy']:.0f}{mark}")
            if m_or["daily_jpy"] >= 5000 and best_oos_risk is None:
                best_oos_risk = risk

    print(f"\n  Best Risk (Full): {best_risk}%, Best Risk (OOS): {best_oos_risk}%")

    # Year-by-year at recommended risk
    risk_level = best_risk if best_risk else 2.0
    maxlot_map = {0.2: 0.10, 0.5: 0.20, 1.0: 0.30, 1.5: 0.50,
                  2.0: 0.50, 2.5: 0.75, 3.0: 1.00, 3.5: 1.50, 4.0: 2.00}
    maxlot = maxlot_map.get(risk_level, 0.50)

    final_params = {**wp, "RiskPct": risk_level, "MaxLot": maxlot}
    cfg_f = make_cfg(**final_params)
    ind_f = precompute_indicators(h4_df, cfg_f)
    trades_f, _, _ = backtest_goldalpha(*ind_f, cfg_f)

    df = pd.DataFrame(trades_f)
    if len(df) > 0:
        df["year"] = pd.to_datetime(df["close_time"]).dt.year
        m_f = calc_metrics(trades_f, cfg_f.INITIAL_BALANCE, total_days)
        print(f"\n  Year-by-Year (Risk={risk_level}%, MaxLot={maxlot}):")
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
              f"Daily={m_f['daily_jpy']:.0f} JPY Final={m_f['final_balance']:,.0f}")

    # ================================================================
    # TOP 3 comparison
    # ================================================================
    print("\n" + "=" * 80)
    print("TOP 3 CANDIDATES COMPARISON")
    print("=" * 80)
    for i, v in enumerate(validated[:3]):
        p = v["params"]
        m = v["metrics"]
        print(f"\n  #{i+1} [{v['label']}] FinalScore={v['final_score']:.1f}")
        print(f"    PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.1f}%")
        print(f"    WFA: {v['wfa_pass']}/8 Avg={v['wfa_avg_pf']:.2f} Min={v['wfa_min_pf']:.2f}")
        print(f"    OOS: PF={v['oos']['pf']:.2f} T={v['oos']['n_trades']} Daily={v['oos']['daily_jpy']:.0f}")
        print(f"    Params: SL={p['SL_ATR_Mult']} Tr={p['Trail_ATR']} BE={p['BE_ATR']} "
              f"Zone={p['EMA_Zone_ATR']} ATR_F={p['ATR_Filter']} Body={p['BodyRatio']} "
              f"MaxP={p['MaxPositions']} D1T={p['D1_Tolerance']}")
        features = []
        if p.get("USE_EMA_SLOPE"): features.append(f"Slope({p.get('EMA_SLOPE_BARS', '?')})")
        if p.get("USE_STRUCTURE"): features.append(f"Struct({p.get('STRUCTURE_BARS', '?')})")
        if p.get("USE_TIME_DECAY"): features.append(f"TD({p.get('MAX_HOLD_BARS', '?')})")
        if p.get("USE_VOL_REGIME"): features.append("VolReg")
        if p.get("USE_W1_SEPARATION"): features.append(f"W1Sep({p.get('W1_SEP_MIN', '?')})")
        if p.get("USE_ADX_FILTER"): features.append(f"ADX({p.get('ADX_MIN', '?')})")
        print(f"    Features: {', '.join(features) if features else 'None'}")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== OPTIMIZATION COMPLETE ===")


if __name__ == "__main__":
    main()
