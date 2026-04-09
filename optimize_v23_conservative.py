"""
GoldAlpha v23 Conservative Optimizer
Base: User-modified v23 (SL=2.5, Trail=3.0, BE=1.0, MaxPos=2, D1_Min_Slope=0.001)
Goal: 500+ trades, PF >= 1.5, WFA 6/8+, Daily >= 5000 JPY on 300K JPY
Strategy: Conservative v12 base + W1 separation + D1 regime
  - Optimize entry/exit around conservative parameters
  - Test combined regime (D1+W1) variants
  - Fine-tune for maximum WFA robustness
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
    np_ema, np_sma, np_atr, resample_to_daily, resample_to_weekly
)
from optimize_v20 import backtest_with_regime, run_regime_wfa


def make_cfg(**overrides):
    """User's conservative v23 base."""
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    # User's conservative v23 params
    cfg.SL_ATR_Mult = 2.5; cfg.Trail_ATR = 3.0; cfg.BE_ATR = 1.0
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.32
    cfg.EMA_Zone_ATR = 0.40; cfg.ATR_Filter = 0.70; cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 2; cfg.MinLot = 0.01; cfg.MaxLot = 0.15
    cfg.INITIAL_BALANCE = 300_000
    # W1 separation (user's v23 feature)
    cfg.USE_W1_SEPARATION = True; cfg.W1_SEP_MIN = 0.005
    # All other features off
    cfg.USE_EMA_SLOPE = False; cfg.USE_STRUCTURE = False
    cfg.USE_TIME_DECAY = False; cfg.USE_VOL_REGIME = False
    cfg.USE_SESSION_FILTER = False; cfg.USE_RSI_CONFIRM = False
    cfg.USE_ADX_FILTER = False; cfg.USE_PARTIAL_CLOSE = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_bt(h4_df, total_days, regime_type="d1_slope", regime_params=None, **params):
    if regime_params is None:
        regime_params = {"slope_bars": 5, "min_slope": 0.001}
    cfg = make_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, regime_type, regime_params)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def score(m, min_trades=400):
    if m is None or m["n_trades"] < min_trades:
        return -999
    if m["pf"] < 1.2:
        return -999
    s = min(m["pf"], 3.5) * 12
    s += min(m["n_trades"], 2000) * 0.004
    s -= max(0, m["max_dd"] - 25) * 0.5
    s += max(0, m["win_rate"] - 55) * 0.15
    return s


def grid(h4_df, total_days, grid_dict, fixed, label, min_t=400, top_n=10,
         regime_type="d1_slope", regime_params=None):
    if regime_params is None:
        regime_params = {"slope_bars": 5, "min_slope": 0.001}
    keys = list(grid_dict.keys())
    combos = list(product(*grid_dict.values()))
    n = len(combos)
    print(f"\n  {label}: {n} combos")
    results = []
    best = -999
    for idx, combo in enumerate(combos):
        params = {**fixed, **dict(zip(keys, combo))}
        m = run_bt(h4_df, total_days, regime_type, regime_params, **params)
        s = score(m, min_t)
        if s > -999:
            results.append((params, m, s))
            if s > best:
                best = s
                print(f"    [{idx+1}/{n}] BEST s={s:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        if (idx + 1) % 500 == 0:
            print(f"    [{idx+1}/{n}] {len(results)} valid...")
    results.sort(key=lambda x: x[2], reverse=True)
    if results:
        print(f"  -> {len(results)} valid, best={results[0][2]:.1f}")
    else:
        print(f"  -> 0 valid!")
    return results[:top_n]


def rng(v, step, n=3):
    return sorted(set([round(v + step * i, 4) for i in range(-n, n+1) if v + step * i > 0]))


def count_losing_years(trades, h4_df):
    if not trades:
        return 99
    df = pd.DataFrame(trades)
    df["year"] = pd.to_datetime(df["close_time"]).dt.year
    n_loss = 0
    for yr, grp in df.groupby("year"):
        if grp["pnl_jpy"].sum() < 0:
            n_loss += 1
    return n_loss


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v23 Conservative Optimization")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print(f"Base: User's conservative v23 (SL=2.5, Trail=3.0, BE=1.0, MaxPos=2)")
    print(f"Target: 500+ trades, PF >= 1.5, WFA 6/8+, Daily >= 5000 JPY")
    print("=" * 80)

    # ================================================================
    # Baselines
    # ================================================================
    print("\n--- Baselines ---")

    # User's exact v23 params (D1 slope only, min_slope=0.001)
    m_user = run_bt(h4_df, total_days, "d1_slope", {"slope_bars": 5, "min_slope": 0.001})
    if m_user:
        print(f"User v23 (D1 0.001): PF={m_user['pf']:.2f} T={m_user['n_trades']} DD={m_user['max_dd']:.1f}%")

    # User's v23 with combined regime
    m_comb = run_bt(h4_df, total_days, "combined",
                    {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_spread": 0.005})
    if m_comb:
        print(f"User v23 (Combined): PF={m_comb['pf']:.2f} T={m_comb['n_trades']} DD={m_comb['max_dd']:.1f}%")

    # v12 raw
    m12 = run_bt(h4_df, total_days, "none", {},
                 SL_ATR_Mult=2.0, Trail_ATR=2.5, BE_ATR=1.5,
                 EMA_Zone_ATR=0.4, ATR_Filter=0.6, D1_Tolerance=0.003,
                 MaxPositions=2, USE_W1_SEPARATION=False)
    if m12:
        print(f"v12 raw (no filter): PF={m12['pf']:.2f} T={m12['n_trades']} DD={m12['max_dd']:.1f}%")

    # ================================================================
    # STAGE 1: Entry/Exit grid around conservative base
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Entry/Exit Grid (conservative base)")
    print("=" * 80)

    # Test with D1 slope (user's choice)
    regime_d1 = {"slope_bars": 5, "min_slope": 0.001}

    grid1 = {
        "SL_ATR_Mult":  [1.5, 2.0, 2.5, 3.0, 3.5],
        "Trail_ATR":    [2.0, 2.5, 3.0, 3.5, 4.0],
        "BE_ATR":       [0.3, 0.5, 0.8, 1.0, 1.5],
        "EMA_Zone_ATR": [0.30, 0.40, 0.50, 0.60],
        "MaxPositions": [2, 3, 4],
        "D1_Tolerance": [0.003, 0.005, 0.007],
    }
    # 5*5*5*4*3*3 = 4500 combos
    fixed1 = {"ATR_Filter": 0.70, "BodyRatio": 0.32}

    top1 = grid(h4_df, total_days, grid1, fixed1, "Conservative grid",
                min_t=400, top_n=20, regime_type="d1_slope", regime_params=regime_d1)

    # Also test with combined regime
    regime_comb = {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_spread": 0.005}
    top1c = grid(h4_df, total_days, grid1, fixed1, "Conservative+Combined",
                 min_t=400, top_n=20, regime_type="combined", regime_params=regime_comb)

    all1 = top1 + top1c
    all1.sort(key=lambda x: x[2], reverse=True)
    seen = set()
    unique1 = []
    for p, m, s in all1:
        key = tuple(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            unique1.append((p, m, s))

    print(f"\n  Top 15 (merged):")
    for i, (p, m, s) in enumerate(unique1[:15]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
              f"WR={m['win_rate']:.0f}% | SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} "
              f"BE={p['BE_ATR']:.1f} Zone={p['EMA_Zone_ATR']:.2f} MaxP={p['MaxPositions']}")

    # ================================================================
    # STAGE 2: Fine-tune top 5 + ATR/Body variations
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: Fine-tune + ATR/Body")
    print("=" * 80)

    fine_results = []
    for rank, (params, m, s) in enumerate(unique1[:5]):
        fg = {
            "SL_ATR_Mult": rng(params["SL_ATR_Mult"], 0.15, 2),
            "Trail_ATR": rng(params["Trail_ATR"], 0.2, 2),
            "BE_ATR": rng(params["BE_ATR"], 0.1, 2),
            "ATR_Filter": [0.50, 0.60, 0.70],
            "BodyRatio": [0.24, 0.28, 0.32],
        }
        ef = {k: params[k] for k in ["EMA_Zone_ATR", "MaxPositions", "D1_Tolerance"]}
        # Use combined regime
        fr = grid(h4_df, total_days, fg, ef, f"Fine R{rank+1}",
                  min_t=400, top_n=5, regime_type="combined", regime_params=regime_comb)
        fine_results.extend(fr)

    all2 = [(p, m, s) for p, m, s in unique1[:10]] + fine_results
    all2.sort(key=lambda x: x[2], reverse=True)
    seen2 = set()
    unique2 = []
    for p, m, s in all2:
        key = tuple(sorted(p.items()))
        if key not in seen2:
            seen2.add(key)
            unique2.append((p, m, s))

    print(f"\n  Top 10 (all):")
    for i, (p, m, s) in enumerate(unique2[:10]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
              f"| SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
              f"Zone={p['EMA_Zone_ATR']:.2f} ATR_F={p.get('ATR_Filter', 0.7):.2f}")

    # ================================================================
    # STAGE 3: Feature toggles
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 3: Feature Toggles")
    print("=" * 80)

    feature_sets = [
        ("None", {}),
        ("S2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("S3", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3}),
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("VolBand", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("S2+Slope5", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                       "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("S2+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                     "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
    ]

    feat_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique2[:6]):
        for fname, fparams in feature_sets:
            params = {**base_p, **fparams}
            m = run_bt(h4_df, total_days, "combined", regime_comb, **params)
            s = score(m, 400)
            if s > -999:
                feat_results.append((params, m, s, f"R{rank+1}+{fname}"))

    feat_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 15 with features:")
    for i, (p, m, s, label) in enumerate(feat_results[:15]):
        print(f"  {label:>25} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}%")

    # ================================================================
    # STAGE 4: Regime variants on top candidates
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: Regime Variants")
    print("=" * 80)

    regime_variants = [
        ("D1(5,0.001)", "d1_slope", {"slope_bars": 5, "min_slope": 0.001}),
        ("D1(5,0.002)", "d1_slope", {"slope_bars": 5, "min_slope": 0.002}),
        ("D1(3,0.001)", "d1_slope", {"slope_bars": 3, "min_slope": 0.001}),
        ("Comb(5,0.001,0.005)", "combined",
         {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_spread": 0.005}),
        ("Comb(5,0.001,0.003)", "combined",
         {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_spread": 0.003}),
        ("Comb(5,0.002,0.005)", "combined",
         {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.005}),
        ("NoRegime", "none", {}),
        ("W1Sp(0.005)", "w1_ema_spread", {"min_spread": 0.005}),
    ]

    regime_results = []
    candidates = [(p, m, s, "base") for p, m, s in unique2[:5]]
    candidates += [(p, m, s, label) for p, m, s, label in feat_results[:8]]
    for rank, (base_p, base_m, base_s, blabel) in enumerate(candidates[:8]):
        for rname, rtype, rparams in regime_variants:
            m = run_bt(h4_df, total_days, rtype, rparams, **base_p)
            s = score(m, 400)
            if s > -999:
                regime_results.append((base_p, m, s, f"{blabel}+{rname}", rtype, rparams))

    regime_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 15 regime variants:")
    for i, (p, m, s, label, rt, rp) in enumerate(regime_results[:15]):
        print(f"  {label:>40} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}%")

    # ================================================================
    # STAGE 5: WFA Validation (top 15)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 5: WFA Validation")
    print("=" * 80)

    # Collect all candidates
    all_cands = []
    for p, m, s in unique2[:5]:
        all_cands.append((p, m, s, "base", "combined", regime_comb))
    for p, m, s, label in feat_results[:8]:
        all_cands.append((p, m, s, label, "combined", regime_comb))
    for p, m, s, label, rt, rp in regime_results[:8]:
        all_cands.append((p, m, s, label, rt, rp))

    # Deduplicate
    seen3 = set()
    final_cands = []
    for p, m, s, label, rt, rp in all_cands:
        key = (tuple(sorted(p.items())), rt, tuple(sorted(rp.items())))
        if key not in seen3:
            seen3.add(key)
            final_cands.append((p, m, s, label, rt, rp))
    final_cands.sort(key=lambda x: x[2], reverse=True)

    validated = []
    for ci, (params, m, s, label, rt, rp) in enumerate(final_cands[:15]):
        cfg = make_cfg(**params)
        wfa = run_regime_wfa(h4_df, cfg, rt, rp, n_windows=8)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0

        # OOS 2024-2026
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        tr_oos, _, _ = backtest_with_regime(sub, cfg, rt, rp)
        oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

        # Losing years
        all_tr, _, _ = backtest_with_regime(h4_df, cfg, rt, rp)
        n_loss_yr = count_losing_years(all_tr, h4_df)

        # Composite score
        wfa_score = (n_pass / 8) * 60
        wfa_score += min(avg_pf, 2.5) * 8
        wfa_score += min(min_pf, 1.0) * 10
        wfa_score -= (8 - n_pass) * 8
        base_s = score(m, 400)
        oos_s = min(m_oos["pf"], 4.0) * 4 if m_oos and m_oos["n_trades"] >= 20 else 0
        loss_penalty = n_loss_yr * 3
        final_score = base_s + wfa_score + oos_s - loss_penalty

        oos_pf = m_oos["pf"] if m_oos else 0
        oos_daily = m_oos["daily_jpy"] if m_oos else 0
        oos_t = m_oos["n_trades"] if m_oos else 0

        print(f"  [{ci+1}/15] {label:>30} WFA={n_pass}/8 AvgPF={avg_pf:.2f} MinPF={min_pf:.2f} "
              f"PF={m['pf']:.2f} T={m['n_trades']} OOS={oos_pf:.2f} LY={n_loss_yr} FS={final_score:.1f}")

        validated.append({
            "params": params, "label": label, "metrics": m,
            "wfa_pass": n_pass, "wfa_avg_pf": avg_pf, "wfa_min_pf": min_pf,
            "oos": m_oos, "final_score": final_score, "wfa": wfa,
            "regime_type": rt, "regime_params": rp,
            "n_loss_yr": n_loss_yr,
        })

    validated.sort(key=lambda x: x["final_score"], reverse=True)

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)
    print(f"{'Rk':>2} {'FS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'OOS':>5} {'LY':>2} | Label")
    print("-" * 100)
    for i, v in enumerate(validated):
        m = v["metrics"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        print(f"{i+1:2d} {v['final_score']:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['wfa_avg_pf']:5.2f} {v['wfa_min_pf']:5.2f} "
              f"{oos_pf:5.2f} {v['n_loss_yr']:2d} | {v['label']}")

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
    print(f"  Losing years: {W['n_loss_yr']}")
    if W["oos"]:
        print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f} DD={W['oos']['max_dd']:.1f}%")

    # WFA details
    print(f"\n  WFA Window Details:")
    for j, w in enumerate(W["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"    W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} DD={w['max_dd']:5.1f}% [{st}]")

    # Risk scaling
    print(f"\n  Risk Scaling (full period):")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>5} {'Daily':>8} {'Final':>12}")
    print("  " + "-" * 60)
    best_risk = None
    for risk, maxlot in [(0.20, 0.10), (0.50, 0.20), (1.0, 0.30),
                          (1.5, 0.50), (2.0, 0.50), (2.5, 0.75),
                          (3.0, 1.00), (3.5, 1.50)]:
        cfg_r = make_cfg(**{**wp, "RiskPct": risk, "MaxLot": maxlot})
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
    for risk, maxlot in [(0.5, 0.15), (1.0, 0.30), (1.5, 0.50),
                          (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        cfg_r = make_cfg(**{**wp, "RiskPct": risk, "MaxLot": maxlot})
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
    cfg_f = make_cfg(**{**wp, "RiskPct": rl, "MaxLot": ml})
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
            loss = " <LOSS>" if pnls.sum() < 0 else ""
            print(f"  {yr:6d} {n:4d} {pf:6.2f} {wr:5.0f} {pnls.sum():+12,.0f} {daily:8.0f}{loss}")
        print(f"\n  TOTAL: PF={m_f['pf']:.2f} T={m_f['n_trades']} DD={m_f['max_dd']:.1f}% "
              f"Daily={m_f['daily_jpy']:.0f} Final={m_f['final_balance']:,.0f}")

    # Comparison
    print("\n" + "=" * 80)
    print("VERSION COMPARISON")
    print("=" * 80)
    print(f"  v12: PF={m12['pf']:.2f} T={m12['n_trades']} (baseline)")
    if m_user:
        print(f"  User v23: PF={m_user['pf']:.2f} T={m_user['n_trades']} (user's conservative)")
    m_w = W["metrics"]
    print(f"  Optimized: PF={m_w['pf']:.2f} T={m_w['n_trades']} WFA={W['wfa_pass']}/8 LY={W['n_loss_yr']}")
    if W["oos"]:
        print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} Daily={W['oos']['daily_jpy']:.0f}")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== CONSERVATIVE OPTIMIZATION COMPLETE ===")


if __name__ == "__main__":
    main()
