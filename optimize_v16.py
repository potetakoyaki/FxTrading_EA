"""
GoldAlpha v16 Optimizer - Staged approach for speed
v12 base → entry optimization → exit optimization → features → WFA validation
Target: 500+ trades, PF >= 1.5, 300K JPY, daily avg 5000+ JPY
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
    backtest_goldalpha, calc_metrics
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


def score(m, min_trades=300):
    """Score a metrics dict. Higher = better."""
    if m is None or m["n_trades"] < min_trades:
        return -999
    pf = m["pf"]
    if pf < 1.2:
        return -999
    return (min(pf, 3.5) * 15
            + min(m["n_trades"], 2000) * 0.005
            - max(0, m["max_dd"] - 25) * 0.3
            + max(0, m["win_rate"] - 55) * 0.2)


def run_backtest(h4_df, total_days, **params):
    cfg = make_cfg(**params)
    ind = precompute_indicators(h4_df, cfg)
    trades, eq, final = backtest_goldalpha(*ind, cfg)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    return m


def grid_stage(h4_df, total_days, grid, fixed_params, label, min_trades=300, top_n=10):
    """Run a grid search stage. Returns sorted list of (params, metrics, score)."""
    keys = list(grid.keys())
    combos = list(product(*grid.values()))
    n = len(combos)
    print(f"\n  {label}: {n} combos")

    results = []
    best = -999
    for idx, combo in enumerate(combos):
        params = {**fixed_params, **dict(zip(keys, combo))}
        m = run_backtest(h4_df, total_days, **params)
        s = score(m, min_trades)
        if s > -999:
            results.append((params, m, s))
            if s > best:
                best = s
                if len(results) % 50 == 1 or s > best - 0.5:
                    print(f"    [{idx+1}/{n}] BEST s={s:.1f} PF={m['pf']:.2f} "
                          f"T={m['n_trades']} DD={m['max_dd']:.1f}%")
        if (idx + 1) % 500 == 0:
            print(f"    [{idx+1}/{n}] {len(results)} valid...")

    results.sort(key=lambda x: x[2], reverse=True)
    print(f"  → {len(results)} valid, best score={results[0][2]:.1f}" if results else "  → 0 valid!")
    return results[:top_n]


def run_wfa(h4_df, cfg, n_windows=8, oos_ratio=0.25):
    """Walk-Forward Analysis"""
    total_bars = len(h4_df)
    window_size = total_bars // n_windows
    oos_size = int(window_size * oos_ratio)
    results = []

    for w in range(n_windows):
        window_end = min((w + 1) * window_size, total_bars)
        oos_start = window_end - oos_size
        data_start = max(0, oos_start - 600)
        sub = h4_df.iloc[data_start:window_end].copy()
        ind = precompute_indicators(sub, cfg)
        trades, _, _ = backtest_goldalpha(*ind, cfg)
        oos_time = h4_df.index[oos_start]
        oos_end_time = h4_df.index[min(window_end - 1, total_bars - 1)]
        oos_trades = [t for t in trades if t["open_time"] >= oos_time]
        oos_days = max(1, (oos_end_time - oos_time).days)
        m = calc_metrics(oos_trades, cfg.INITIAL_BALANCE, oos_days)
        if m:
            results.append(m)
    return results


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v16 Staged Optimizer")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print("=" * 80)

    # ================================================================
    # v12 Baseline
    # ================================================================
    m0 = run_backtest(h4_df, total_days)
    print(f"\nv12 baseline: PF={m0['pf']:.2f} T={m0['n_trades']} DD={m0['max_dd']:.1f}% "
          f"WR={m0['win_rate']:.1f}% Daily={m0['daily_jpy']:.0f}")

    # ================================================================
    # STAGE 1: Entry parameters (most impactful for trade count & quality)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Entry Parameter Optimization")
    print("=" * 80)

    entry_grid = {
        "EMA_Zone_ATR": [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70],
        "ATR_Filter": [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70, 0.80],
        "BodyRatio": [0.24, 0.28, 0.30, 0.32, 0.34, 0.36, 0.38],
        "MaxPositions": [2, 3, 4, 5],
        "D1_Tolerance": [0.002, 0.003, 0.004, 0.005, 0.007],
    }
    # 10*10*7*4*5 = 14,000 combos (~47 min)
    # Reduce: fix D1_Tolerance, reduce others
    entry_grid_fast = {
        "EMA_Zone_ATR": [0.30, 0.40, 0.50, 0.60, 0.70],
        "ATR_Filter": [0.20, 0.35, 0.50, 0.65, 0.80],
        "BodyRatio": [0.26, 0.30, 0.34, 0.38],
        "MaxPositions": [2, 3, 4],
        "D1_Tolerance": [0.003, 0.005, 0.007],
    }
    # 5*5*4*3*3 = 900 combos (~3 min)
    fixed_exit = {"SL_ATR_Mult": 2.0, "Trail_ATR": 2.5, "BE_ATR": 1.5}

    top_entry = grid_stage(h4_df, total_days, entry_grid_fast, fixed_exit,
                           "Entry (coarse)", min_trades=300, top_n=15)

    # Fine-tune top 3 entry results
    print("\n  Refining top 3 entry configs...")
    fine_entry_results = []
    for rank, (params, m, s) in enumerate(top_entry[:3]):
        def rng(v, step, n=3):
            return sorted(set([round(v + step * i, 3) for i in range(-n, n+1) if v + step * i > 0]))

        fine_grid = {
            "EMA_Zone_ATR": rng(params["EMA_Zone_ATR"], 0.03, 3),
            "ATR_Filter": rng(params["ATR_Filter"], 0.03, 3),
            "BodyRatio": rng(params["BodyRatio"], 0.01, 2),
            "MaxPositions": [max(2, params["MaxPositions"]-1), params["MaxPositions"], min(5, params["MaxPositions"]+1)],
            "D1_Tolerance": rng(params["D1_Tolerance"], 0.001, 2),
        }
        top_fine = grid_stage(h4_df, total_days, fine_grid, fixed_exit,
                              f"Entry fine R{rank+1}", min_trades=300, top_n=5)
        fine_entry_results.extend(top_fine)

    # Merge
    all_entry = top_entry + fine_entry_results
    all_entry.sort(key=lambda x: x[2], reverse=True)
    # Deduplicate
    seen = set()
    unique_entry = []
    for p, m, s in all_entry:
        key = (p["EMA_Zone_ATR"], p["ATR_Filter"], p["BodyRatio"], p["MaxPositions"], p["D1_Tolerance"])
        if key not in seen:
            seen.add(key)
            unique_entry.append((p, m, s))

    print(f"\n  Top 10 entry configs:")
    print(f"  {'Rank':>4} {'Score':>6} {'PF':>5} {'Trades':>6} {'DD%':>6} {'WR%':>5} "
          f"| {'Zone':>5} {'ATR_F':>5} {'Body':>5} {'MaxP':>4} {'D1T':>5}")
    print("  " + "-" * 75)
    for i, (p, m, s) in enumerate(unique_entry[:10]):
        print(f"  {i+1:4d} {s:6.1f} {m['pf']:5.2f} {m['n_trades']:6d} "
              f"{m['max_dd']:6.1f} {m['win_rate']:5.1f} "
              f"| {p['EMA_Zone_ATR']:5.2f} {p['ATR_Filter']:5.2f} "
              f"{p['BodyRatio']:5.2f} {p['MaxPositions']:4d} {p['D1_Tolerance']:5.3f}")

    # ================================================================
    # STAGE 2: Exit parameters on top 5 entry configs
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: Exit Parameter Optimization")
    print("=" * 80)

    exit_grid = {
        "SL_ATR_Mult": [1.2, 1.5, 1.8, 2.0, 2.3, 2.5, 3.0],
        "Trail_ATR": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
        "BE_ATR": [0.3, 0.5, 0.8, 1.0, 1.5, 2.0],
    }
    # 7*6*6 = 252 per entry config * 5 = 1260 (~4 min)

    exit_results = []
    for rank, (entry_p, entry_m, entry_s) in enumerate(unique_entry[:5]):
        entry_fixed = {
            "EMA_Zone_ATR": entry_p["EMA_Zone_ATR"],
            "ATR_Filter": entry_p["ATR_Filter"],
            "BodyRatio": entry_p["BodyRatio"],
            "MaxPositions": entry_p["MaxPositions"],
            "D1_Tolerance": entry_p["D1_Tolerance"],
        }
        top_exit = grid_stage(h4_df, total_days, exit_grid, entry_fixed,
                              f"Exit on Entry-R{rank+1}", min_trades=300, top_n=5)
        exit_results.extend(top_exit)

    exit_results.sort(key=lambda x: x[2], reverse=True)

    # Fine-tune top 3 exit
    print("\n  Refining top 3 exit configs...")
    fine_exit_results = []
    for rank, (params, m, s) in enumerate(exit_results[:3]):
        def rng(v, step, n=2):
            return sorted(set([round(v + step * i, 3) for i in range(-n, n+1) if v + step * i > 0]))

        fine_grid = {
            "SL_ATR_Mult": rng(params["SL_ATR_Mult"], 0.15, 2),
            "Trail_ATR": rng(params["Trail_ATR"], 0.2, 2),
            "BE_ATR": rng(params["BE_ATR"], 0.15, 2),
        }
        entry_fixed = {k: params[k] for k in ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_fine = grid_stage(h4_df, total_days, fine_grid, entry_fixed,
                              f"Exit fine R{rank+1}", min_trades=300, top_n=5)
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
    print(f"  {'Rank':>4} {'Score':>6} {'PF':>5} {'Trades':>6} {'DD%':>6} {'WR%':>5} "
          f"| {'SL':>4} {'Trail':>5} {'BE':>4} {'Zone':>5} {'ATR_F':>5} {'Body':>5} {'MaxP':>4}")
    print("  " + "-" * 80)
    for i, (p, m, s) in enumerate(unique_all[:10]):
        print(f"  {i+1:4d} {s:6.1f} {m['pf']:5.2f} {m['n_trades']:6d} "
              f"{m['max_dd']:6.1f} {m['win_rate']:5.1f} "
              f"| {p['SL_ATR_Mult']:4.1f} {p['Trail_ATR']:5.1f} {p['BE_ATR']:4.1f} "
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
        ("Struct2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("Struct3", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3}),
        ("TD20", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 20}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("Slope3", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3}),
        ("VolReg", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("S2+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2, "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("S2+TD20", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2, "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 20}),
        ("S3+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3, "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("S2+Slope5", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2, "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("S2+TD30+Slope5", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2, "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30, "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("S2+VolReg", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2, "USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("TD30+VolReg", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30, "USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
    ]

    feat_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique_all[:5]):
        for feat_name, feat_params in feature_sets:
            params = {**base_p, **feat_params}
            m = run_backtest(h4_df, total_days, **params)
            s = score(m, 300)
            if s > -999:
                feat_results.append((params, m, s, f"R{rank+1}+{feat_name}"))

    feat_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 15 with features:")
    print(f"  {'Label':>20} {'Score':>6} {'PF':>5} {'Trades':>6} {'DD%':>6} {'WR%':>5}")
    print("  " + "-" * 60)
    for p, m, s, label in feat_results[:15]:
        print(f"  {label:>20} {s:6.1f} {m['pf']:5.2f} {m['n_trades']:6d} "
              f"{m['max_dd']:6.1f} {m['win_rate']:5.1f}")

    # ================================================================
    # STAGE 4: WFA + OOS + Risk Scaling on Top 8
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: Full Validation (WFA + OOS + Risk)")
    print("=" * 80)

    # Merge unique_all (no features) + feat_results
    # Take unique_all top5 + feat_results top10
    candidates = [(p, m, s, "base") for p, m, s in unique_all[:5]]
    candidates += [(p, m, s, label) for p, m, s, label in feat_results[:10]]
    # Deduplicate
    seen3 = set()
    final_cands = []
    for p, m, s, label in candidates:
        key = tuple(sorted((k, v) for k, v in p.items()))
        if key not in seen3:
            seen3.add(key)
            final_cands.append((p, m, s, label))
    final_cands.sort(key=lambda x: x[2], reverse=True)

    validated = []
    for ci, (params, m, s, label) in enumerate(final_cands[:8]):
        print(f"\n--- Candidate {ci+1} [{label}]: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% ---")

        cfg = make_cfg(**params)

        # WFA
        wfa = run_wfa(h4_df, cfg, n_windows=8)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        total_wfa_t = sum(r["n_trades"] for r in wfa)
        print(f"  WFA: {n_pass}/8 PASS, Avg PF={avg_pf:.2f}, OOS Trades={total_wfa_t}")
        for j, w in enumerate(wfa):
            st = "PASS" if w["pf"] > 1.0 else "FAIL"
            print(f"    W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} DD={w['max_dd']:5.1f}% [{st}]")

        # OOS 2024-2026
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        ind = precompute_indicators(sub, cfg)
        trades_oos, _, _ = backtest_goldalpha(*ind, cfg)
        oos_list = [t for t in trades_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)
        if m_oos:
            print(f"  OOS 2024-2026: PF={m_oos['pf']:.2f} T={m_oos['n_trades']} "
                  f"DD={m_oos['max_dd']:.1f}% Daily={m_oos['daily_jpy']:.0f}")
        else:
            m_oos = {"pf": 0, "n_trades": 0, "max_dd": 100, "daily_jpy": 0}

        # Risk scaling
        print(f"\n  Risk Scaling:")
        print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'Trades':>6} {'DD%':>6} {'Daily':>8} {'Final':>12}")
        print("  " + "-" * 65)

        best_risk = None
        risk_data = []
        for risk, maxlot in [(0.20, 0.10), (0.50, 0.20), (1.0, 0.30),
                              (1.5, 0.50), (2.0, 0.50), (2.5, 0.75),
                              (3.0, 1.00), (3.5, 1.50), (4.0, 2.00)]:
            rp = {**params, "RiskPct": risk, "MaxLot": maxlot}
            m_r = run_backtest(h4_df, total_days, **rp)
            if m_r:
                mark = " ***" if m_r["daily_jpy"] >= 5000 and m_r["max_dd"] < 50 else ""
                print(f"  {risk:6.2f} {maxlot:6.2f} | {m_r['pf']:5.2f} {m_r['n_trades']:6d} "
                      f"{m_r['max_dd']:6.1f} {m_r['daily_jpy']:8.0f} "
                      f"{m_r['final_balance']:12,.0f}{mark}")
                if m_r["daily_jpy"] >= 5000 and m_r["max_dd"] < 50 and best_risk is None:
                    best_risk = risk
                risk_data.append((risk, maxlot, m_r))

        # OOS risk scaling
        if m_oos and m_oos["n_trades"] > 0:
            print(f"\n  OOS 2024-2026 Risk Scaling:")
            for risk, maxlot in [(1.0, 0.30), (1.5, 0.50), (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
                rp = {**params, "RiskPct": risk, "MaxLot": maxlot}
                cfg_r = make_cfg(**rp)
                sub_r = h4_df[h4_df.index >= "2022-01-01"].copy()
                ind_r = precompute_indicators(sub_r, cfg_r)
                tr_r, _, _ = backtest_goldalpha(*ind_r, cfg_r)
                oos_r = [t for t in tr_r if t["open_time"] >= pd.Timestamp("2024-01-01")]
                m_or = calc_metrics(oos_r, cfg_r.INITIAL_BALANCE, oos_days)
                if m_or:
                    mark = " ***" if m_or["daily_jpy"] >= 5000 else ""
                    print(f"    Risk={risk}%: PF={m_or['pf']:.2f} T={m_or['n_trades']} "
                          f"DD={m_or['max_dd']:.1f}% Daily={m_or['daily_jpy']:.0f}{mark}")

        # Final composite score
        wfa_score = n_pass / 8
        oos_pf_capped = min(m_oos["pf"], 5.0) if m_oos["pf"] > 0 else 0
        final_score = (s * 0.3
                       + wfa_score * 35
                       + avg_pf * 5
                       + oos_pf_capped * 5
                       - max(0, m_oos["max_dd"] - 30) * 0.2)

        validated.append({
            "params": params,
            "label": label,
            "metrics": m,
            "score": s,
            "wfa_pass": n_pass,
            "wfa_avg_pf": avg_pf,
            "oos": m_oos,
            "final_score": final_score,
            "best_risk": best_risk,
        })

    # ================================================================
    # FINAL RANKING
    # ================================================================
    validated.sort(key=lambda x: x["final_score"], reverse=True)

    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)
    print(f"{'Rk':>2} {'FScore':>7} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'OOS_PF':>6} {'BstR':>4} | Label")
    print("-" * 75)
    for i, v in enumerate(validated):
        m = v["metrics"]
        print(f"{i+1:2d} {v['final_score']:7.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['oos']['pf']:6.2f} "
              f"{v['best_risk'] or 'N/A':>4} | {v['label']}")

    # ================================================================
    # WINNER DETAILS
    # ================================================================
    if validated:
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

        print(f"\n  Full Period: PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']} DD={W['metrics']['max_dd']:.1f}%")
        print(f"  WFA: {W['wfa_pass']}/8, Avg PF={W['wfa_avg_pf']:.2f}")
        print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} DD={W['oos']['max_dd']:.1f}%")
        print(f"  Recommended Risk: {W['best_risk']}%")

        # Year-by-year at recommended risk
        risk_level = W['best_risk'] if W['best_risk'] else 2.0
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
                yr_days = 365 if yr < df["year"].max() else (h4_df.index[-1] - pd.Timestamp(f"{yr}-01-01")).days
                daily = pnls.sum() / max(1, yr_days)
                print(f"  {yr:6d} {n:4d} {pf:6.2f} {wr:5.0f} {pnls.sum():+12,.0f} {daily:8.0f}")

            print(f"\n  TOTAL: PF={m_f['pf']:.2f} T={m_f['n_trades']} DD={m_f['max_dd']:.1f}% "
                  f"Daily={m_f['daily_jpy']:.0f} JPY Final={m_f['final_balance']:,.0f}")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")


if __name__ == "__main__":
    main()
