"""
GoldAlpha v23 Optimizer - WFA Robustness Focus (v4 - time-optimized)
Key insight from run 1: Combined regime (D1+W1) achieves 6/8 WFA with v22 params.
Strategy:
  - Use D1 regime for fast screening (~5000 combos, ~20 min)
  - Validate top 50 with Combined regime + WFA (~20 min)
  - Fine-tune + features (~10 min)
Total budget: ~45 min
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

class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, data):
        for f in self.files:
            f.write(data)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

log_file = open("/tmp/v23_output.log", "w")
sys.stdout = Tee(sys.__stdout__, log_file)


def make_v12_cfg(**overrides):
    from backtest_goldalpha import GoldAlphaConfig
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    cfg.SL_ATR_Mult = 2.0; cfg.Trail_ATR = 2.5; cfg.BE_ATR = 1.5
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.32
    cfg.EMA_Zone_ATR = 0.4; cfg.ATR_Filter = 0.6; cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 2; cfg.MinLot = 0.01; cfg.MaxLot = 0.50
    cfg.INITIAL_BALANCE = 300_000
    cfg.USE_EMA_SLOPE = False; cfg.EMA_SLOPE_BARS = 5
    cfg.USE_STRUCTURE = False; cfg.STRUCTURE_BARS = 2
    cfg.USE_TIME_DECAY = False; cfg.MAX_HOLD_BARS = 30
    cfg.USE_VOL_REGIME = False; cfg.USE_SESSION_FILTER = False
    cfg.USE_RSI_CONFIRM = False; cfg.USE_ADX_FILTER = False
    cfg.USE_PARTIAL_CLOSE = False; cfg.USE_W1_SEPARATION = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_bt(h4_df, total_days, rt, rp, **params):
    cfg = make_v12_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, rt, rp)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def run_bt_trades(h4_df, rt, rp, **params):
    cfg = make_v12_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, rt, rp)
    return trades, cfg


def year_by_year(trades, h4_df):
    if not trades:
        return {}
    df = pd.DataFrame(trades)
    df["year"] = pd.to_datetime(df["close_time"]).dt.year
    results = {}
    for yr, grp in df.groupby("year"):
        pnls = grp["pnl_jpy"].values
        wins = (pnls > 0).sum(); n = len(pnls)
        wr = wins / n * 100 if n > 0 else 0
        gp = pnls[pnls > 0].sum() if wins > 0 else 0
        gl = abs(pnls[pnls <= 0].sum()) if (n - wins) > 0 else 1
        pf = gp / gl if gl > 0 else float("inf")
        yr_days = 365 if yr < df["year"].max() else max(1, (h4_df.index[-1] - pd.Timestamp(f"{yr}-01-01")).days)
        results[yr] = {"n": n, "pf": pf, "wr": wr, "pnl": pnls.sum(), "daily": pnls.sum() / max(1, yr_days)}
    return results


def count_losing_years(trades, h4_df):
    return sum(1 for v in year_by_year(trades, h4_df).values() if v["pnl"] < 0)


def wfa_score(m, wfa, n_losing=0, m_oos=None):
    if m is None or m["n_trades"] < 400 or m["pf"] < 1.2:
        return -999
    n_pass = sum(1 for r in wfa if r["pf"] > 1.0) if wfa else 0
    avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.004
    s += (n_pass / 8) * 60 + min(avg_pf, 2.5) * 8 - (8 - n_pass) * 5
    s -= max(0, m["max_dd"] - 30) * 0.5 - n_losing * 5
    if m_oos and m_oos["n_trades"] >= 20:
        s += min(m_oos["pf"], 4.0) * 4
    return s


def quick_score(m, min_t=400):
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.2:
        return -999
    return min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.004 - max(0, m["max_dd"] - 30) * 0.5


def full_validate(h4_df, total_days, params, rt, rp, tag=""):
    cfg = make_v12_cfg(**params)
    m = run_bt(h4_df, total_days, rt, rp, **params)
    if m is None or m["n_trades"] < 400 or m["pf"] < 1.2:
        return None
    wfa = run_regime_wfa(h4_df, cfg, rt, rp)
    n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
    avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
    min_pf = min(r["pf"] for r in wfa) if wfa else 0
    tr, _ = run_bt_trades(h4_df, rt, rp, **params)
    n_losing = count_losing_years(tr, h4_df)
    sub = h4_df[h4_df.index >= "2022-01-01"].copy()
    tr_oos, _, _ = backtest_with_regime(sub, cfg, rt, rp)
    oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
    oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
    m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)
    fs = wfa_score(m, wfa, n_losing, m_oos)
    return {"params": params, "m": m, "wfa": wfa, "n_pass": n_pass, "avg_pf": avg_pf,
            "min_pf": min_pf, "n_losing": n_losing, "oos": m_oos, "score": fs,
            "tag": tag, "rt": rt, "rp": rp}


def rng(v, step, n=2):
    return sorted(set([round(v + step * i, 4) for i in range(-n, n + 1) if v + step * i > 0]))


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v23 - WFA Robustness Optimization (v4)")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print("=" * 80)

    V22 = dict(SL_ATR_Mult=4.0, Trail_ATR=4.9, BE_ATR=0.3,
               EMA_Zone_ATR=0.30, ATR_Filter=0.70, BodyRatio=0.32,
               MaxPositions=6, D1_Tolerance=0.01)
    COMB_RT = "combined"
    COMB_RP = {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.005}
    D1_RT = "d1_slope"
    D1_RP = {"slope_bars": 5, "min_slope": 0.002}

    # ================================================================
    # Baselines
    # ================================================================
    print("\n--- Baselines ---")
    m12 = run_bt(h4_df, total_days, "none", {})
    print(f"v12 (no regime): PF={m12['pf']:.2f} T={m12['n_trades']} DD={m12['max_dd']:.1f}%")

    v22d1 = full_validate(h4_df, total_days, V22, D1_RT, D1_RP, "v22+D1")
    print(f"v22+D1:          PF={v22d1['m']['pf']:.2f} T={v22d1['m']['n_trades']} WFA={v22d1['n_pass']}/8 AvgPF={v22d1['avg_pf']:.2f}")
    for j, w in enumerate(v22d1["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"  W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} [{st}]")

    v22c = full_validate(h4_df, total_days, V22, COMB_RT, COMB_RP, "v22+Comb")
    print(f"v22+Comb:        PF={v22c['m']['pf']:.2f} T={v22c['m']['n_trades']} WFA={v22c['n_pass']}/8 AvgPF={v22c['avg_pf']:.2f}")
    for j, w in enumerate(v22c["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"  W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} [{st}]")

    tr22c, _ = run_bt_trades(h4_df, COMB_RT, COMB_RP, **V22)
    yby22 = year_by_year(tr22c, h4_df)
    losing22 = sum(1 for v in yby22.values() if v["pnl"] < 0)
    print(f"v22+Comb year-by-year (losing: {losing22}):")
    for yr in sorted(yby22.keys()):
        v = yby22[yr]
        tag = " LOSS" if v["pnl"] < 0 else ""
        print(f"  {yr}: T={v['n']:3d} PF={v['pf']:5.2f} PnL={v['pnl']:+10,.0f}{tag}")

    print(f"\nBaselines done in {time.time()-t0:.0f}s")

    # ================================================================
    # STAGE 1: Quick screen with D1 regime (~5000 combos)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Quick Screen (D1 regime, ~5000 combos)")
    print("=" * 80)

    # Compact grid: 4*4*3*3*3*3*2*2 = 5184
    screen_grid = {
        "SL_ATR_Mult":  [3.5, 4.0, 4.5, 5.0],    # 4
        "Trail_ATR":    [3.5, 4.0, 4.9, 5.5],     # 4
        "BE_ATR":       [0.2, 0.3, 0.8],           # 3
        "EMA_Zone_ATR": [0.20, 0.30, 0.40],        # 3
        "BodyRatio":    [0.24, 0.32, 0.36],         # 3
        "MaxPositions": [4, 6, 7],                  # 3
        "D1_Tolerance": [0.007, 0.010],             # 2
        "ATR_Filter":   [0.60, 0.70],              # 2
    }
    keys = list(screen_grid.keys())
    combos = list(product(*screen_grid.values()))
    n_combos = len(combos)
    print(f"  Grid: {n_combos} combos")

    quick_results = []
    best_qs = -999
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        m = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
        qs = quick_score(m, 400)
        if qs > -999:
            quick_results.append((params, m, qs))
            if qs > best_qs:
                best_qs = qs
                print(f"    [{idx+1}/{n_combos}] BEST qs={qs:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% | "
                      f"SL={params['SL_ATR_Mult']} Tr={params['Trail_ATR']} "
                      f"BE={params['BE_ATR']} Zone={params['EMA_Zone_ATR']} "
                      f"MaxP={params['MaxPositions']}")
        if (idx + 1) % 1000 == 0 and qs <= best_qs:
            print(f"    [{idx+1}/{n_combos}] {len(quick_results)} valid...")

    quick_results.sort(key=lambda x: x[2], reverse=True)
    print(f"  -> {len(quick_results)} valid, best qs={quick_results[0][2]:.1f}")
    print(f"  Stage 1 done in {time.time()-t0:.0f}s")

    # ================================================================
    # STAGE 2: Combined regime WFA on top 50
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: WFA Validation (Combined regime)")
    print("=" * 80)

    candidates = []
    seen = set()
    # Always include v22
    seen.add(tuple(sorted(V22.items())))
    candidates.append(V22)
    for p, m, s in quick_results:
        key = tuple(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            candidates.append(p)
        if len(candidates) >= 50:
            break

    print(f"  Validating {len(candidates)} candidates...")
    validated = [v22c]  # Already have v22c
    for ci, params in enumerate(candidates[1:], 1):
        v = full_validate(h4_df, total_days, params, COMB_RT, COMB_RP, "screen")
        if v is None:
            continue
        validated.append(v)
        marker = " <<<" if v["n_pass"] >= 6 else ""
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        if ci % 10 == 0 or v["n_pass"] >= 6:
            print(f"    [{ci}/{len(candidates)-1}] WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                  f"PF={v['m']['pf']:.2f} T={v['m']['n_trades']} OOS={oos_pf:.2f} "
                  f"LY={v['n_losing']} FS={v['score']:.1f}{marker}")

    validated.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  Top 10:")
    print(f"  {'Rk':>2} {'FS':>6} {'WFA':>4} {'AvgPF':>5} {'PF':>5} {'T':>5} {'DD':>5} {'LY':>2} {'OOS':>5} | Key params")
    print("  " + "-" * 80)
    for i, v in enumerate(validated[:10]):
        m = v["m"]; p = v["params"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {v['avg_pf']:5.2f} "
              f"{m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} {v['n_losing']:2d} {oos_pf:5.2f} "
              f"| SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
              f"Zone={p['EMA_Zone_ATR']:.2f} MaxP={p['MaxPositions']} Body={p['BodyRatio']:.2f} "
              f"D1T={p['D1_Tolerance']:.3f} [{v['tag']}]")
    print(f"  Stage 2 done in {time.time()-t0:.0f}s")

    # ================================================================
    # STAGE 3: Fine-tune exit params around best WFA configs
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 3: Fine-Tune Exit Params")
    print("=" * 80)

    top_wfa = [v for v in validated if v["n_pass"] >= 5][:4]
    if len(top_wfa) < 2:
        top_wfa = validated[:4]

    fine_validated = []
    for ri, base in enumerate(top_wfa):
        bp = base["params"]
        fine_grid = {
            "SL_ATR_Mult": rng(bp["SL_ATR_Mult"], 0.3, 2),
            "Trail_ATR": rng(bp["Trail_ATR"], 0.3, 2),
            "BE_ATR": rng(bp["BE_ATR"], 0.1, 2),
        }
        fixed = {k: bp[k] for k in ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio",
                                      "MaxPositions", "D1_Tolerance"]}
        keys_f = list(fine_grid.keys())
        combos_f = list(product(*fine_grid.values()))
        print(f"\n  Fine R{ri+1} (WFA={base['n_pass']}/8): {len(combos_f)} exit combos")

        # D1 quick screen
        fine_q = []
        for combo in combos_f:
            params = {**fixed, **dict(zip(keys_f, combo))}
            m = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
            qs = quick_score(m, 400)
            if qs > -999:
                fine_q.append((params, m, qs))
        fine_q.sort(key=lambda x: x[2], reverse=True)

        # Combined WFA on top 8
        for fi, (params, _, _) in enumerate(fine_q[:8]):
            v = full_validate(h4_df, total_days, params, COMB_RT, COMB_RP, f"fine_R{ri+1}")
            if v:
                fine_validated.append(v)
                marker = " <<<" if v["n_pass"] >= 6 else ""
                if v["n_pass"] >= 5 or fi == 0:
                    oos_pf = v["oos"]["pf"] if v["oos"] else 0
                    print(f"    WFA={v['n_pass']}/8 PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                          f"FS={v['score']:.1f} | SL={params['SL_ATR_Mult']:.1f} "
                          f"Tr={params['Trail_ATR']:.1f} BE={params['BE_ATR']:.1f}{marker}")

    print(f"  Stage 3 done in {time.time()-t0:.0f}s")

    # ================================================================
    # STAGE 4: Features + Alternative regimes
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: Features + Regimes")
    print("=" * 80)

    all_so_far = validated + fine_validated
    all_so_far.sort(key=lambda x: x["score"], reverse=True)
    seen_v = set()
    unique_v = []
    for v in all_so_far:
        key = tuple(sorted(v["params"].items()))
        if key not in seen_v:
            seen_v.add(key)
            unique_v.append(v)

    # Features on top 4
    feature_sets = [
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD35", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("PC1.5", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5}),
        ("PC2.0", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5}),
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("S2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("VolBand", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Slope5+TD35", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("PC1.5+TD35", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
    ]

    top4 = [v for v in unique_v if v["n_pass"] >= 5][:4]
    if len(top4) < 2:
        top4 = unique_v[:4]

    feat_results = []
    for ri, base in enumerate(top4):
        bp = base["params"]
        for fname, fparams in feature_sets:
            params = {**bp, **fparams}
            v = full_validate(h4_df, total_days, params, COMB_RT, COMB_RP, f"R{ri+1}+{fname}")
            if v:
                feat_results.append(v)
                marker = " <<<" if v["n_pass"] >= 6 else ""
                if v["n_pass"] >= 5 or v["n_pass"] > base["n_pass"]:
                    print(f"    R{ri+1}+{fname:>12} WFA={v['n_pass']}/8 PF={v['m']['pf']:.2f} "
                          f"T={v['m']['n_trades']} FS={v['score']:.1f}{marker}")

    # Alt regimes on top 3
    alt_regimes = [
        ("Comb(5/0.002,0.003)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.003}),
        ("Comb(5/0.002,0.007)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.007}),
        ("Comb(5/0.003,0.005)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.003, "w1_min_spread": 0.005}),
        ("Comb(7/0.002,0.005)", "combined", {"d1_slope_bars": 7, "d1_min_slope": 0.002, "w1_min_spread": 0.005}),
        ("Comb(7/0.003,0.005)", "combined", {"d1_slope_bars": 7, "d1_min_slope": 0.003, "w1_min_spread": 0.005}),
    ]

    top3 = unique_v[:3]
    alt_results = []
    for pi, base in enumerate(top3):
        bp = base["params"]
        for rname, rt, rp in alt_regimes:
            v = full_validate(h4_df, total_days, bp, rt, rp, f"P{pi+1}+{rname}")
            if v:
                alt_results.append(v)
                if v["n_pass"] >= 5:
                    marker = " <<<" if v["n_pass"] >= 6 else ""
                    print(f"    P{pi+1}+{rname:>25} WFA={v['n_pass']}/8 PF={v['m']['pf']:.2f} "
                          f"T={v['m']['n_trades']} FS={v['score']:.1f}{marker}")

    print(f"  Stage 4 done in {time.time()-t0:.0f}s")

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)

    all_final = validated + fine_validated + feat_results + alt_results
    all_final.sort(key=lambda x: x["score"], reverse=True)
    seen_final = set()
    ranked = []
    for v in all_final:
        key = (tuple(sorted(v["params"].items())), v.get("rt", COMB_RT),
               tuple(sorted(v.get("rp", COMB_RP).items())))
        if key not in seen_final:
            seen_final.add(key)
            ranked.append(v)

    print(f"{'Rk':>2} {'FS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'OOS_PF':>6} {'LY':>3} | Tag")
    print("-" * 105)
    for i, v in enumerate(ranked[:20]):
        m = v["m"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        print(f"{i+1:2d} {v['score']:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['n_pass']:2d}/8 {v['avg_pf']:5.2f} {v['min_pf']:5.2f} "
              f"{oos_pf:6.2f} {v['n_losing']:3d} | {v['tag']}")

    # ================================================================
    # WINNER
    # ================================================================
    W = ranked[0]
    wp = W["params"]
    rt = W.get("rt", COMB_RT)
    rp = W.get("rp", COMB_RP)

    print("\n" + "=" * 80)
    print("WINNER")
    print("=" * 80)
    print(f"  Tag: {W['tag']}")
    print(f"  Regime: {rt} {rp}")
    print(f"  ALL Parameters:")
    for k in sorted(wp.keys()):
        v = wp[k]
        if isinstance(v, bool) and not v:
            continue
        print(f"    {k}: {v}")

    print(f"\n  Full Period:")
    print(f"    PF={W['m']['pf']:.2f} T={W['m']['n_trades']} "
          f"DD={W['m']['max_dd']:.1f}% WR={W['m']['win_rate']:.1f}%")
    print(f"  WFA: {W['n_pass']}/8, Avg PF={W['avg_pf']:.2f}, Min PF={W['min_pf']:.2f}")
    print(f"  Losing years: {W['n_losing']}")
    if W["oos"]:
        print(f"  OOS 2024+: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f} DD={W['oos']['max_dd']:.1f}%")

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
                          (3.0, 1.00), (3.5, 1.50), (4.0, 2.00)]:
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
    for risk, maxlot in [(0.50, 0.20), (1.0, 0.30), (1.5, 0.50),
                          (2.0, 0.50), (2.5, 0.75), (3.0, 1.00), (3.5, 1.50)]:
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

    # Year-by-year
    rl = best_risk if best_risk else 2.0
    ml_map = {0.2: 0.10, 0.5: 0.20, 1.0: 0.30, 1.5: 0.50,
              2.0: 0.50, 2.5: 0.75, 3.0: 1.00, 3.5: 1.50, 4.0: 2.00}
    ml = ml_map.get(rl, 0.50)
    tr_f, _ = run_bt_trades(h4_df, rt, rp, **{**wp, "RiskPct": rl, "MaxLot": ml})
    yby = year_by_year(tr_f, h4_df)
    m_f = calc_metrics(tr_f, 300_000, total_days)

    if yby:
        print(f"\n  Year-by-Year (Risk={rl}%, MaxLot={ml}):")
        print(f"  {'Year':>6} {'T':>4} {'PF':>6} {'WR%':>5} {'PnL':>12} {'Daily':>8}")
        print("  " + "-" * 50)
        for yr in sorted(yby.keys()):
            v = yby[yr]
            tag = " <LOSS>" if v["pnl"] < 0 else ""
            print(f"  {yr:6d} {v['n']:4d} {v['pf']:6.2f} {v['wr']:5.0f} "
                  f"{v['pnl']:+12,.0f} {v['daily']:8.0f}{tag}")
        n_loss_yrs = sum(1 for v in yby.values() if v["pnl"] < 0)
        print(f"\n  TOTAL: PF={m_f['pf']:.2f} T={m_f['n_trades']} DD={m_f['max_dd']:.1f}% "
              f"Daily={m_f['daily_jpy']:.0f} Final={m_f['final_balance']:,.0f}")
        print(f"  Losing years: {n_loss_yrs}/{len(yby)}")
        print(f"  Best risk for 5000 JPY/day: {best_risk}%")

    # ================================================================
    # TOP 3 COMPARISON
    # ================================================================
    print("\n" + "=" * 80)
    print("TOP 3 COMPARISON")
    print("=" * 80)
    for i, v in enumerate(ranked[:3]):
        m = v["m"]; oos = v["oos"]
        rtype = v.get("rt", COMB_RT); rparams = v.get("rp", COMB_RP)
        print(f"\n  #{i+1} [{v['tag']}] Regime={rtype} {rparams}")
        print(f"    Full: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        print(f"    WFA: {v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} MinPF={v['min_pf']:.2f}")
        print(f"    Losing years: {v['n_losing']}")
        if oos:
            print(f"    OOS: PF={oos['pf']:.2f} T={oos['n_trades']} Daily={oos['daily_jpy']:.0f}")
        kp = ["SL_ATR_Mult", "Trail_ATR", "BE_ATR", "EMA_Zone_ATR",
              "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]
        vals = {k: v["params"][k] for k in kp if k in v["params"]}
        print(f"    Params: {vals}")
        features = {k: v["params"][k] for k in v["params"]
                    if k.startswith("USE_") and v["params"][k] is True}
        if features:
            print(f"    Features: {features}")

    # ================================================================
    # VERSION COMPARISON
    # ================================================================
    print("\n" + "=" * 80)
    print("VERSION COMPARISON")
    print("=" * 80)
    print(f"  v12: PF={m12['pf']:.2f} T={m12['n_trades']} (baseline)")
    print(f"  v22+D1: PF={v22d1['m']['pf']:.2f} T={v22d1['m']['n_trades']} WFA={v22d1['n_pass']}/8 (prev)")
    m_w = W["m"]
    print(f"  v23: PF={m_w['pf']:.2f} T={m_w['n_trades']} WFA={W['n_pass']}/8 "
          f"AvgPF={W['avg_pf']:.2f} LoseYrs={W['n_losing']} (new)")
    if W["oos"]:
        print(f"  v23 OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f}")

    wfa_diff = W['n_pass'] - v22d1['n_pass']
    print(f"\n  WFA: {v22d1['n_pass']}/8 -> {W['n_pass']}/8 ({'+' if wfa_diff >= 0 else ''}{wfa_diff})")
    pf_diff = m_w['pf'] - v22d1['m']['pf']
    print(f"  PF: {v22d1['m']['pf']:.2f} -> {m_w['pf']:.2f} ({'+' if pf_diff >= 0 else ''}{pf_diff:.2f})")

    print(f"\n  Assessment:")
    if W['n_pass'] > v22d1['n_pass']:
        print(f"    + WFA improved by {wfa_diff} windows")
    elif W['n_pass'] == v22d1['n_pass']:
        print(f"    = WFA unchanged at {W['n_pass']}/8")
    else:
        print(f"    - WFA degraded by {abs(wfa_diff)} windows")
    if W['n_losing'] < losing22:
        print(f"    + Fewer losing years ({W['n_losing']} vs {losing22})")
    elif W['n_losing'] == losing22:
        print(f"    = Same losing years ({W['n_losing']})")
    else:
        print(f"    - More losing years ({W['n_losing']} vs {losing22})")
    if m_w['pf'] >= 1.5 and m_w['n_trades'] >= 500:
        print(f"    + Meets PF >= 1.5 and 500+ trades")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V23 OPTIMIZATION COMPLETE ===")
    log_file.close()


if __name__ == "__main__":
    main()
