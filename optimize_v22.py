"""
GoldAlpha v22 Optimizer - v12 base, comprehensive multi-regime search
Goal: 500+ trades, PF >= 1.5, 300K JPY, daily >= 5000 JPY
Approach:
  1. Broader grid from v12 base (not locked to v21 params)
  2. Test D1 regime + combined filters (ADX, W1 sep, partial close)
  3. Score prioritizes WFA robustness + trade count + PF balance
  4. Full WFA validation on top candidates
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
    np_ema, np_sma, np_atr, np_adx, resample_to_daily, resample_to_weekly
)
from optimize_v20 import backtest_with_regime, run_regime_wfa


# Best regime from v20/v21 testing
REGIME_TYPE = "d1_slope"
REGIME_PARAMS = {"slope_bars": 5, "min_slope": 0.002}


def make_v12_cfg(**overrides):
    """Pure v12 base config with overrides."""
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    # v12 defaults
    cfg.SL_ATR_Mult = 2.0; cfg.Trail_ATR = 2.5; cfg.BE_ATR = 1.5
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.32
    cfg.EMA_Zone_ATR = 0.4; cfg.ATR_Filter = 0.6; cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 2; cfg.MinLot = 0.01; cfg.MaxLot = 0.50
    cfg.INITIAL_BALANCE = 300_000
    # All features off by default
    cfg.USE_EMA_SLOPE = False; cfg.EMA_SLOPE_BARS = 5
    cfg.USE_STRUCTURE = False; cfg.STRUCTURE_BARS = 2
    cfg.USE_TIME_DECAY = False; cfg.MAX_HOLD_BARS = 30
    cfg.USE_VOL_REGIME = False
    cfg.USE_SESSION_FILTER = False
    cfg.USE_RSI_CONFIRM = False
    cfg.USE_ADX_FILTER = False
    cfg.USE_PARTIAL_CLOSE = False
    cfg.USE_W1_SEPARATION = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_bt(h4_df, total_days, regime_type=REGIME_TYPE, regime_params=REGIME_PARAMS, **params):
    """Run backtest with regime filter."""
    cfg = make_v12_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, regime_type, regime_params)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def score_v22(m, min_trades=500):
    """Scoring that balances PF, trade count, DD, and daily JPY."""
    if m is None or m["n_trades"] < min_trades:
        return -999
    if m["pf"] < 1.3:
        return -999
    # Reward PF (capped at 3.5 to avoid overfitting to few trades)
    s = min(m["pf"], 3.5) * 12
    # Reward trade count (more = more robust)
    s += min(m["n_trades"], 2000) * 0.004
    # Penalize extreme DD
    s -= max(0, m["max_dd"] - 30) * 0.4
    # Reward win rate
    s += max(0, m["win_rate"] - 55) * 0.15
    # Reward daily JPY potential (at low risk, scaled up later)
    s += min(m["daily_jpy"], 3000) * 0.001
    return s


def grid(h4_df, total_days, grid_dict, fixed, label, min_t=500, top_n=10,
         regime_type=REGIME_TYPE, regime_params=REGIME_PARAMS):
    """Grid search with progress reporting."""
    keys = list(grid_dict.keys())
    combos = list(product(*grid_dict.values()))
    n = len(combos)
    print(f"\n  {label}: {n} combos")
    results = []
    best = -999
    for idx, combo in enumerate(combos):
        params = {**fixed, **dict(zip(keys, combo))}
        m = run_bt(h4_df, total_days, regime_type, regime_params, **params)
        s = score_v22(m, min_t)
        if s > -999:
            results.append((params, m, s))
            if s > best:
                best = s
                print(f"    [{idx+1}/{n}] BEST s={s:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        if (idx + 1) % 300 == 0:
            print(f"    [{idx+1}/{n}] {len(results)} valid...")
    results.sort(key=lambda x: x[2], reverse=True)
    if results:
        print(f"  -> {len(results)} valid, best score={results[0][2]:.1f}")
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
    print("GoldAlpha v22 - Comprehensive v12-Base Optimization")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print(f"Regime: {REGIME_TYPE} {REGIME_PARAMS}")
    print(f"Target: 500+ trades, PF >= 1.5, 300K JPY, Daily >= 5000 JPY")
    print("=" * 80)

    # ================================================================
    # Baselines
    # ================================================================
    print("\n--- Baselines ---")
    # v12 raw
    m12 = run_bt(h4_df, total_days, "none", {})
    print(f"v12 (no regime): PF={m12['pf']:.2f} T={m12['n_trades']} DD={m12['max_dd']:.1f}%")
    # v12 + D1 regime
    m12r = run_bt(h4_df, total_days)
    print(f"v12 + D1 regime: PF={m12r['pf']:.2f} T={m12r['n_trades']} DD={m12r['max_dd']:.1f}%")
    # v21 params
    v21p = dict(SL_ATR_Mult=3.8, Trail_ATR=4.4, BE_ATR=0.2, BodyRatio=0.32,
                EMA_Zone_ATR=0.65, ATR_Filter=0.7, D1_Tolerance=0.007, MaxPositions=5)
    m21 = run_bt(h4_df, total_days, **v21p)
    print(f"v21 params: PF={m21['pf']:.2f} T={m21['n_trades']} DD={m21['max_dd']:.1f}%")

    # ================================================================
    # STAGE 1: Wide entry grid from v12 base (under D1 regime)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Wide Entry Grid (v12 base + D1 regime)")
    print("=" * 80)

    entry_grid = {
        "EMA_Zone_ATR": [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90],
        "ATR_Filter":   [0.30, 0.40, 0.50, 0.60, 0.70],
        "BodyRatio":    [0.20, 0.24, 0.28, 0.32, 0.36],
        "MaxPositions": [2, 3, 4, 5, 6],
        "D1_Tolerance": [0.003, 0.005, 0.007, 0.010],
    }
    # 7*5*5*5*4 = 3500 combos
    fixed_exit_v12 = {"SL_ATR_Mult": 2.0, "Trail_ATR": 2.5, "BE_ATR": 1.5}

    top_entry = grid(h4_df, total_days, entry_grid, fixed_exit_v12,
                     "Entry (v12 exit)", min_t=500, top_n=20)

    # Also test with widened v21-style exit
    fixed_exit_wide = {"SL_ATR_Mult": 3.5, "Trail_ATR": 4.0, "BE_ATR": 0.3}
    top_entry_wide = grid(h4_df, total_days, entry_grid, fixed_exit_wide,
                          "Entry (wide exit)", min_t=500, top_n=20)

    # Merge entry results
    all_entry = top_entry + top_entry_wide
    all_entry.sort(key=lambda x: x[2], reverse=True)
    seen = set()
    unique_entry = []
    for p, m, s in all_entry:
        key = tuple(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            unique_entry.append((p, m, s))

    print(f"\n  Top 15 entry configs (merged):")
    for i, (p, m, s) in enumerate(unique_entry[:15]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
              f"| Zone={p['EMA_Zone_ATR']:.2f} ATR_F={p['ATR_Filter']:.2f} "
              f"Body={p['BodyRatio']:.2f} MaxP={p['MaxPositions']} D1T={p['D1_Tolerance']:.3f} "
              f"SL={p['SL_ATR_Mult']:.1f}")

    # ================================================================
    # STAGE 2: Exit optimization on top entries
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: Exit Optimization")
    print("=" * 80)

    exit_grid = {
        "SL_ATR_Mult": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
        "Trail_ATR":   [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
        "BE_ATR":      [0.2, 0.3, 0.5, 0.8, 1.0, 1.5],
    }
    # 6*7*6 = 252 per entry config

    exit_results = []
    for rank, (entry_p, entry_m, entry_s) in enumerate(unique_entry[:8]):
        entry_fixed = {k: entry_p[k] for k in
                       ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_exit = grid(h4_df, total_days, exit_grid, entry_fixed,
                        f"Exit on Entry-R{rank+1}", min_t=500, top_n=5)
        exit_results.extend(top_exit)

    exit_results.sort(key=lambda x: x[2], reverse=True)

    # Fine-tune top 5
    print("\n  Fine-tuning top 5 exit configs...")
    fine_exit = []
    for rank, (params, m, s) in enumerate(exit_results[:5]):
        fg = {
            "SL_ATR_Mult": rng(params["SL_ATR_Mult"], 0.2, 2),
            "Trail_ATR": rng(params["Trail_ATR"], 0.2, 2),
            "BE_ATR": rng(params["BE_ATR"], 0.1, 2),
        }
        ef = {k: params[k] for k in ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio",
                                       "MaxPositions", "D1_Tolerance"]}
        top_f = grid(h4_df, total_days, fg, ef, f"Exit fine R{rank+1}", min_t=500, top_n=5)
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

    print(f"\n  Top 15 entry+exit:")
    for i, (p, m, s) in enumerate(unique_all[:15]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
              f"WR={m['win_rate']:.0f}% | SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} "
              f"BE={p['BE_ATR']:.1f} Zone={p['EMA_Zone_ATR']:.2f} MaxP={p['MaxPositions']}")

    # ================================================================
    # STAGE 3: Feature toggles
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 3: Feature Toggles")
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
        ("W1Sep3", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("W1Sep5", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.005}),
        ("PC1.5", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5}),
        ("PC2.0", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5}),
        ("VolBand", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Slope5+S2", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                       "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("Slope5+TD30", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("S2+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                     "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("S2+W1Sep3", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                       "USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("Slope5+PC1.5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                          "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5}),
    ]

    feat_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique_all[:8]):
        for fname, fparams in feature_sets:
            params = {**base_p, **fparams}
            m = run_bt(h4_df, total_days, **params)
            s = score_v22(m, 500)
            if s > -999:
                feat_results.append((params, m, s, f"R{rank+1}+{fname}"))

    feat_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 20 with features:")
    for i, (p, m, s, label) in enumerate(feat_results[:20]):
        print(f"  {label:>30} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} "
              f"DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")

    # ================================================================
    # STAGE 4: Regime variants (test if different D1 params help)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: Regime Variant Search")
    print("=" * 80)

    regime_variants = [
        ("D1(5,0.001)", "d1_slope", {"slope_bars": 5, "min_slope": 0.001}),
        ("D1(5,0.002)", "d1_slope", {"slope_bars": 5, "min_slope": 0.002}),
        ("D1(5,0.003)", "d1_slope", {"slope_bars": 5, "min_slope": 0.003}),
        ("D1(10,0.002)", "d1_slope", {"slope_bars": 10, "min_slope": 0.002}),
        ("D1(10,0.003)", "d1_slope", {"slope_bars": 10, "min_slope": 0.003}),
        ("D1(3,0.001)", "d1_slope", {"slope_bars": 3, "min_slope": 0.001}),
        ("D1(3,0.002)", "d1_slope", {"slope_bars": 3, "min_slope": 0.002}),
        ("NoRegime", "none", {}),
        ("ADX(14,20)", "adx", {"period": 14, "min_adx": 20}),
        ("ADX(14,25)", "adx", {"period": 14, "min_adx": 25}),
        ("W1Sp(0.005)", "w1_ema_spread", {"min_spread": 0.005}),
        ("W1Sp(0.003)", "w1_ema_spread", {"min_spread": 0.003}),
    ]

    regime_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique_all[:5]):
        for rname, rtype, rparams in regime_variants:
            m = run_bt(h4_df, total_days, rtype, rparams, **base_p)
            s = score_v22(m, 500)
            if s > -999:
                regime_results.append((base_p, m, s, f"R{rank+1}+{rname}", rtype, rparams))

    regime_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 15 regime variants:")
    for i, (p, m, s, label, rt, rp) in enumerate(regime_results[:15]):
        print(f"  {label:>25} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}%")

    # ================================================================
    # STAGE 5: WFA Validation (top 15 overall)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 5: WFA Validation")
    print("=" * 80)

    # Collect all candidates
    candidates = []
    # From base entry+exit
    for p, m, s in unique_all[:5]:
        candidates.append((p, m, s, "base", REGIME_TYPE, REGIME_PARAMS))
    # From feature combos
    for p, m, s, label in feat_results[:10]:
        candidates.append((p, m, s, label, REGIME_TYPE, REGIME_PARAMS))
    # From regime variants
    for p, m, s, label, rt, rp in regime_results[:10]:
        candidates.append((p, m, s, label, rt, rp))

    # Deduplicate
    seen3 = set()
    final_cands = []
    for p, m, s, label, rt, rp in candidates:
        key = (tuple(sorted(p.items())), rt, tuple(sorted(rp.items())))
        if key not in seen3:
            seen3.add(key)
            final_cands.append((p, m, s, label, rt, rp))
    final_cands.sort(key=lambda x: x[2], reverse=True)

    validated = []
    for ci, (params, m, s, label, rt, rp) in enumerate(final_cands[:15]):
        cfg = make_v12_cfg(**params)
        wfa = run_regime_wfa(h4_df, cfg, rt, rp, n_windows=8)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0
        total_wfa_t = sum(r["n_trades"] for r in wfa)

        # OOS 2024-2026
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        tr_oos, _, _ = backtest_with_regime(sub, cfg, rt, rp)
        oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

        # Composite score: WFA weight increased
        wfa_score = (n_pass / 8) * 50  # Up from 40
        wfa_score += min(avg_pf, 2.5) * 6
        base_s = score_v22(m, 500)
        oos_s = 0
        if m_oos and m_oos["n_trades"] >= 20:
            oos_s = min(m_oos["pf"], 4.0) * 4
        final_score = base_s + wfa_score + oos_s

        oos_pf = m_oos["pf"] if m_oos else 0
        oos_daily = m_oos["daily_jpy"] if m_oos else 0
        oos_t = m_oos["n_trades"] if m_oos else 0
        print(f"  [{ci+1}/15] {label:>30} WFA={n_pass}/8 AvgPF={avg_pf:.2f} MinPF={min_pf:.2f} "
              f"PF={m['pf']:.2f} T={m['n_trades']} OOS_PF={oos_pf:.2f} OOS_T={oos_t} FS={final_score:.1f}")

        validated.append({
            "params": params, "label": label, "metrics": m,
            "wfa_pass": n_pass, "wfa_avg_pf": avg_pf, "wfa_min_pf": min_pf,
            "oos": m_oos, "final_score": final_score, "wfa": wfa,
            "regime_type": rt, "regime_params": rp,
        })

    validated.sort(key=lambda x: x["final_score"], reverse=True)

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)
    print(f"{'Rk':>2} {'FS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'OOS_PF':>6} {'OOS_D':>6} {'OOS_T':>5} | Label")
    print("-" * 100)
    for i, v in enumerate(validated):
        m = v["metrics"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        oos_daily = v["oos"]["daily_jpy"] if v["oos"] else 0
        oos_t = v["oos"]["n_trades"] if v["oos"] else 0
        print(f"{i+1:2d} {v['final_score']:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['wfa_avg_pf']:5.2f} {v['wfa_min_pf']:5.2f} "
              f"{oos_pf:6.2f} {oos_daily:6.0f} {oos_t:5d} | {v['label']}")

    # ================================================================
    # WINNER ANALYSIS
    # ================================================================
    W = validated[0]
    wp = W["params"]
    rt = W["regime_type"]
    rp = W["regime_params"]
    print("\n" + "=" * 80)
    print("WINNER")
    print("=" * 80)
    print(f"  Regime: {rt} {rp}")
    for k in sorted(wp.keys()):
        v = wp[k]
        if isinstance(v, bool) and not v:
            continue
        print(f"  {k}: {v}")
    print(f"\n  Full: PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']} "
          f"DD={W['metrics']['max_dd']:.1f}% WR={W['metrics']['win_rate']:.1f}%")
    print(f"  WFA: {W['wfa_pass']}/8, Avg PF={W['wfa_avg_pf']:.2f}, Min PF={W['wfa_min_pf']:.2f}")
    if W["oos"]:
        print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f} DD={W['oos']['max_dd']:.1f}%")

    # WFA details
    print(f"\n  WFA Window Details:")
    for j, w in enumerate(W["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"    W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} DD={w['max_dd']:5.1f}% [{st}]")

    # Risk scaling (full period)
    print(f"\n  Risk Scaling (full period):")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>5} {'Daily':>8} {'Final':>12}")
    print("  " + "-" * 60)
    best_risk = None
    for risk, maxlot in [(0.20, 0.10), (0.50, 0.20), (1.0, 0.30),
                          (1.5, 0.50), (2.0, 0.50), (2.5, 0.75),
                          (3.0, 1.00), (3.5, 1.50)]:
        cfg_r = make_v12_cfg(**{**wp, "RiskPct": risk, "MaxLot": maxlot})
        tr_r, _, _ = backtest_with_regime(h4_df, cfg_r, rt, rp)
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
    oos_best_risk = None
    for risk, maxlot in [(1.0, 0.30), (1.5, 0.50), (2.0, 0.50),
                          (2.5, 0.75), (3.0, 1.00), (3.5, 1.50)]:
        cfg_r = make_v12_cfg(**{**wp, "RiskPct": risk, "MaxLot": maxlot})
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        tr_r, _, _ = backtest_with_regime(sub, cfg_r, rt, rp)
        oos_r = [t for t in tr_r if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_or = calc_metrics(oos_r, cfg_r.INITIAL_BALANCE, oos_days)
        if m_or:
            mark = " ***" if m_or["daily_jpy"] >= 5000 else ""
            print(f"    Risk={risk}%: PF={m_or['pf']:.2f} T={m_or['n_trades']} "
                  f"DD={m_or['max_dd']:.1f}% Daily={m_or['daily_jpy']:.0f}{mark}")
            if m_or["daily_jpy"] >= 5000 and oos_best_risk is None:
                oos_best_risk = risk

    # Year-by-year
    rl = best_risk if best_risk else 2.0
    ml_map = {0.2: 0.10, 0.5: 0.20, 1.0: 0.30, 1.5: 0.50,
              2.0: 0.50, 2.5: 0.75, 3.0: 1.00, 3.5: 1.50}
    ml = ml_map.get(rl, 0.50)
    cfg_f = make_v12_cfg(**{**wp, "RiskPct": rl, "MaxLot": ml})
    tr_f, _, _ = backtest_with_regime(h4_df, cfg_f, rt, rp)
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

    # Also try 2nd and 3rd place winners if they have better WFA
    print("\n" + "=" * 80)
    print("TOP 3 COMPARISON")
    print("=" * 80)
    for i, v in enumerate(validated[:3]):
        m = v["metrics"]
        oos = v["oos"]
        print(f"\n  #{i+1} [{v['label']}] Regime={v['regime_type']} {v['regime_params']}")
        print(f"    Full: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        print(f"    WFA: {v['wfa_pass']}/8 AvgPF={v['wfa_avg_pf']:.2f} MinPF={v['wfa_min_pf']:.2f}")
        if oos:
            print(f"    OOS: PF={oos['pf']:.2f} T={oos['n_trades']} Daily={oos['daily_jpy']:.0f}")
        key_params = ["SL_ATR_Mult", "Trail_ATR", "BE_ATR", "EMA_Zone_ATR",
                      "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]
        vals = {k: v["params"][k] for k in key_params if k in v["params"]}
        print(f"    Params: {vals}")
        features = {k: v["params"][k] for k in v["params"]
                    if k.startswith("USE_") and v["params"][k] is True}
        if features:
            print(f"    Features: {features}")

    # Comparison vs v21
    print("\n" + "=" * 80)
    print("VERSION COMPARISON")
    print("=" * 80)
    print(f"  v12: PF={m12['pf']:.2f} T={m12['n_trades']} (baseline)")
    print(f"  v21: PF={m21['pf']:.2f} T={m21['n_trades']} WFA=5/8 (prev best)")
    m_w = W["metrics"]
    print(f"  v22: PF={m_w['pf']:.2f} T={m_w['n_trades']} WFA={W['wfa_pass']}/8 (new)")
    if W["oos"]:
        print(f"  v22 OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f}")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V22 OPTIMIZATION COMPLETE ===")


if __name__ == "__main__":
    main()
