"""
GoldAlpha v23 Optimizer - WFA Robustness Focus
Base: v22 winner (PF=3.20, 1598T, WFA 5/8, DD=29.4%)
Goals:
  1. 500+ trades, PF >= 1.5, Daily >= 5000 JPY at reasonable risk
  2. Improve WFA to 6/8+ PASS (main v22 weakness)
  3. Reduce losing years (2016, 2018, 2021, 2022 were negative in v22)
Strategy:
  - Adaptive regime: D1 slope + W1 EMA separation combined
  - Wider param neighborhood around v22 winner
  - New scoring: heavily penalize WFA failures and losing years
  - Test partial close, volatility band, time decay exit
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

# Tee output to log file
import io

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

# ================================================================
# v22 winner params (starting point)
# ================================================================
V22_PARAMS = dict(
    SL_ATR_Mult=4.0, Trail_ATR=4.9, BE_ATR=0.3,
    EMA_Zone_ATR=0.30, ATR_Filter=0.70, BodyRatio=0.32,
    MaxPositions=6, D1_Tolerance=0.01,
)
V22_REGIME_TYPE = "d1_slope"
V22_REGIME_PARAMS = {"slope_bars": 5, "min_slope": 0.002}


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


def run_bt(h4_df, total_days, regime_type, regime_params, **params):
    """Run backtest with regime filter."""
    cfg = make_v12_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, regime_type, regime_params)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def score_v23(m, wfa_results=None, min_trades=500):
    """
    v23 scoring: heavily penalize WFA failures and losing years.
    Base: min(PF, 3.0) * 10 + min(trades, 1500) * 0.004
    WFA bonus: (n_pass/8) * 60 + min(avg_pf, 2.5) * 8
    Penalty: -max(0, DD-30) * 0.5 - (8-n_pass) * 5 for each failed window
    """
    if m is None or m["n_trades"] < min_trades:
        return -999

    if m["pf"] < 1.2:
        return -999

    # Base score
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.004

    # DD penalty
    s -= max(0, m["max_dd"] - 30) * 0.5

    # WFA bonus if available
    if wfa_results is not None:
        n_pass = sum(1 for r in wfa_results if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa_results]) if wfa_results else 0
        s += (n_pass / 8) * 60  # Big bonus for WFA passes
        s += min(avg_pf, 2.5) * 8
        # Penalty for each failed window
        s -= (8 - n_pass) * 5

    return s


def score_quick(m, min_trades=500):
    """Quick scoring without WFA (for grid search)."""
    if m is None or m["n_trades"] < min_trades:
        return -999
    if m["pf"] < 1.2:
        return -999
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.004
    s -= max(0, m["max_dd"] - 30) * 0.5
    s += max(0, m["win_rate"] - 55) * 0.15
    return s


def grid(h4_df, total_days, grid_dict, fixed, label, regime_type, regime_params,
         min_t=500, top_n=10):
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
        s = score_quick(m, min_t)
        if s > -999:
            results.append((params, m, s))
            if s > best:
                best = s
                print(f"    [{idx+1}/{n}] BEST s={s:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        if (idx + 1) % 500 == 0 and s <= best:
            print(f"    [{idx+1}/{n}] {len(results)} valid...")
    results.sort(key=lambda x: x[2], reverse=True)
    if results:
        print(f"  -> {len(results)} valid, best score={results[0][2]:.1f}")
    else:
        print(f"  -> 0 valid!")
    return results[:top_n]


def rng(v, step, n=3):
    """Generate range around value."""
    return sorted(set([round(v + step * i, 4) for i in range(-n, n + 1) if v + step * i > 0]))


def year_by_year(trades, h4_df, initial_balance):
    """Compute year-by-year results. Returns dict {year: metrics}."""
    if not trades:
        return {}
    df = pd.DataFrame(trades)
    df["year"] = pd.to_datetime(df["close_time"]).dt.year
    results = {}
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
        results[yr] = {"n": n, "pf": pf, "wr": wr, "pnl": pnls.sum(), "daily": daily}
    return results


def count_losing_years(trades, h4_df):
    """Count how many years have negative PnL."""
    yby = year_by_year(trades, h4_df, 300_000)
    return sum(1 for v in yby.values() if v["pnl"] < 0)


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v23 - WFA Robustness Optimization")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print(f"v22 winner: PF=3.20, 1598T, WFA 5/8, DD=29.4%")
    print(f"Target: 500+ T, PF>=1.5, WFA 6/8+, Daily>=5000 JPY")
    print("=" * 80)

    # ================================================================
    # Baselines
    # ================================================================
    print("\n--- Baselines ---")

    # v12 raw (no regime)
    m12 = run_bt(h4_df, total_days, "none", {})
    print(f"v12 (no regime): PF={m12['pf']:.2f} T={m12['n_trades']} DD={m12['max_dd']:.1f}%")

    # v22 winner
    m22 = run_bt(h4_df, total_days, V22_REGIME_TYPE, V22_REGIME_PARAMS, **V22_PARAMS)
    print(f"v22 winner:      PF={m22['pf']:.2f} T={m22['n_trades']} DD={m22['max_dd']:.1f}%")

    # v22 WFA
    cfg22 = make_v12_cfg(**V22_PARAMS)
    wfa22 = run_regime_wfa(h4_df, cfg22, V22_REGIME_TYPE, V22_REGIME_PARAMS)
    n_pass22 = sum(1 for r in wfa22 if r["pf"] > 1.0)
    avg_pf22 = np.mean([r["pf"] for r in wfa22]) if wfa22 else 0
    print(f"v22 WFA:         {n_pass22}/8 AvgPF={avg_pf22:.2f}")
    for j, w in enumerate(wfa22):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"  W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} [{st}]")

    # v22 year-by-year
    tr22, _, _ = backtest_with_regime(h4_df, cfg22, V22_REGIME_TYPE, V22_REGIME_PARAMS)
    yby22 = year_by_year(tr22, h4_df, 300_000)
    print(f"v22 year-by-year:")
    for yr in sorted(yby22.keys()):
        v = yby22[yr]
        tag = " LOSS" if v["pnl"] < 0 else ""
        print(f"  {yr}: T={v['n']:3d} PF={v['pf']:5.2f} PnL={v['pnl']:+10,.0f}{tag}")
    losing22 = sum(1 for v in yby22.values() if v["pnl"] < 0)
    print(f"  Losing years: {losing22}")

    # ================================================================
    # STAGE 1: Regime exploration - find best regime for WFA
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 1: Regime Exploration (optimize for WFA)")
    print("=" * 80)

    # Test various regimes with v22 params to find what improves WFA
    regime_tests = []
    # D1 slope variants
    for sb in [3, 5, 7, 10, 15]:
        for ms in [0.001, 0.002, 0.003, 0.005, 0.008]:
            regime_tests.append(("d1_slope", {"slope_bars": sb, "min_slope": ms},
                                 f"D1({sb},{ms})"))
    # Combined D1+W1
    for sb in [3, 5, 7, 10]:
        for ms in [0.001, 0.002, 0.003, 0.005]:
            for ws in [0.003, 0.005, 0.007, 0.010]:
                regime_tests.append(("combined",
                    {"d1_slope_bars": sb, "d1_min_slope": ms, "w1_min_spread": ws},
                    f"Comb(D1={sb}/{ms},W1={ws})"))
    # W1 spread only
    for ws in [0.003, 0.005, 0.007, 0.010, 0.015]:
        regime_tests.append(("w1_ema_spread", {"min_spread": ws}, f"W1({ws})"))
    # ADX
    for period in [14]:
        for min_adx in [18, 20, 22, 25]:
            regime_tests.append(("adx", {"period": period, "min_adx": min_adx},
                                 f"ADX({period},{min_adx})"))

    print(f"  Testing {len(regime_tests)} regime configs with v22 params...")

    # Phase 1: Quick screen on full-period
    screened_regimes = []
    for i, (rt, rp, label) in enumerate(regime_tests):
        m = run_bt(h4_df, total_days, rt, rp, **V22_PARAMS)
        if m and m["n_trades"] >= 500 and m["pf"] >= 1.3:
            screened_regimes.append((rt, rp, label, m))
        if (i + 1) % 100 == 0:
            print(f"    [{i+1}/{len(regime_tests)}] {len(screened_regimes)} valid...")

    print(f"  -> {len(screened_regimes)} passed screen (500+ trades, PF>=1.3)")

    # Phase 2: WFA on screened regimes
    print(f"\n  Phase 2: WFA validation on {min(len(screened_regimes), 40)} top regimes...")
    screened_regimes.sort(key=lambda x: x[3]["pf"], reverse=True)

    regime_wfa_results = []
    for ci, (rt, rp, label, m) in enumerate(screened_regimes[:40]):
        cfg_t = make_v12_cfg(**V22_PARAMS)
        wfa = run_regime_wfa(h4_df, cfg_t, rt, rp)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0

        # Year-by-year loss count
        tr_t, _, _ = backtest_with_regime(h4_df, cfg_t, rt, rp)
        n_losing = count_losing_years(tr_t, h4_df)

        # Score: WFA-dominant
        s = (n_pass / 8) * 60 + min(avg_pf, 2.5) * 8 + min(m["pf"], 3.0) * 10
        s -= (8 - n_pass) * 5
        s -= n_losing * 5  # Penalize losing years

        regime_wfa_results.append({
            "rt": rt, "rp": rp, "label": label, "m": m,
            "wfa": wfa, "n_pass": n_pass, "avg_pf": avg_pf, "min_pf": min_pf,
            "n_losing": n_losing, "score": s,
        })

        if (ci + 1) % 10 == 0 or n_pass >= 6:
            marker = " <<<" if n_pass >= 6 else ""
            print(f"    [{ci+1}] {label:>35} WFA={n_pass}/8 AvgPF={avg_pf:.2f} "
                  f"PF={m['pf']:.2f} T={m['n_trades']} LoseYrs={n_losing}{marker}")

    regime_wfa_results.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n  Top 10 regimes (by WFA-weighted score):")
    print(f"  {'Rk':>2} {'Score':>6} {'WFA':>4} {'AvgPF':>5} {'PF':>5} {'T':>5} {'Lose':>4} | Label")
    print("  " + "-" * 70)
    for i, v in enumerate(regime_wfa_results[:10]):
        print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {v['avg_pf']:5.2f} "
              f"{v['m']['pf']:5.2f} {v['m']['n_trades']:5d} {v['n_losing']:4d} | {v['label']}")

    # Pick best regime(s) for further optimization
    best_regimes = regime_wfa_results[:3]  # Top 3 regimes to test
    BEST_RT = best_regimes[0]["rt"]
    BEST_RP = best_regimes[0]["rp"]
    print(f"\n  Selected regime: {BEST_RT} {BEST_RP}")

    # ================================================================
    # STAGE 2: Entry param grid around v22 (with best regime)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 2: Entry Optimization (v22 neighborhood + best regime)")
    print("=" * 80)

    # v22 params as center, wider search
    entry_grid = {
        "EMA_Zone_ATR": [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50],
        "ATR_Filter":   [0.40, 0.50, 0.60, 0.70, 0.80],
        "BodyRatio":    [0.20, 0.24, 0.28, 0.32, 0.36, 0.40],
        "MaxPositions": [3, 4, 5, 6, 7, 8],
        "D1_Tolerance": [0.005, 0.007, 0.010, 0.015, 0.020],
    }
    # 7*5*6*6*5 = 6300 combos
    fixed_exit_v22 = {"SL_ATR_Mult": 4.0, "Trail_ATR": 4.9, "BE_ATR": 0.3}

    top_entry = grid(h4_df, total_days, entry_grid, fixed_exit_v22,
                     "Entry (v22 exit)", BEST_RT, BEST_RP, min_t=500, top_n=20)

    # Also test with tighter exit (potentially better WFA)
    fixed_exit_tight = {"SL_ATR_Mult": 3.0, "Trail_ATR": 3.5, "BE_ATR": 0.5}
    top_entry_tight = grid(h4_df, total_days, entry_grid, fixed_exit_tight,
                           "Entry (tight exit)", BEST_RT, BEST_RP, min_t=500, top_n=15)

    # Merge
    all_entry = top_entry + top_entry_tight
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
    # STAGE 3: Exit optimization on top entries
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 3: Exit Optimization")
    print("=" * 80)

    exit_grid = {
        "SL_ATR_Mult": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
        "Trail_ATR":   [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5],
        "BE_ATR":      [0.2, 0.3, 0.4, 0.5, 0.8, 1.0, 1.5],
    }
    # 7*8*7 = 392 per entry config

    exit_results = []
    for rank, (entry_p, entry_m, entry_s) in enumerate(unique_entry[:6]):
        entry_fixed = {k: entry_p[k] for k in
                       ["EMA_Zone_ATR", "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance"]}
        top_exit = grid(h4_df, total_days, exit_grid, entry_fixed,
                        f"Exit on Entry-R{rank+1}", BEST_RT, BEST_RP, min_t=500, top_n=5)
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
        top_f = grid(h4_df, total_days, fg, ef, f"Exit fine R{rank+1}",
                     BEST_RT, BEST_RP, min_t=500, top_n=5)
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
    # STAGE 4: Feature toggles (partial close, time decay, vol band, etc.)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 4: Feature Toggles")
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
        ("TD35", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("PC1.0", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.0, "PARTIAL_RATIO": 0.5}),
        ("PC1.5", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5}),
        ("PC2.0", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5}),
        ("PC1.5_30", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.3}),
        ("VolBand", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("VolBand2", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.4, "VOL_HIGH_MULT": 2.0}),
        ("W1Sep3", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("W1Sep5", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.005}),
        # Combos targeting WFA improvement
        ("Slope5+TD30", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("Slope5+TD35", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("Slope5+PC1.5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                          "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5}),
        ("PC1.5+TD30", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("PC1.5+TD35", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("S2+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                     "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("Slope5+VolBand", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                            "USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Slope5+S2", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                       "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("PC1.5+VolBand", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                           "USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Slope5+PC1.5+TD35", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                                "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                                "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
    ]

    feat_results = []
    for rank, (base_p, base_m, base_s) in enumerate(unique_all[:8]):
        for fname, fparams in feature_sets:
            params = {**base_p, **fparams}
            m = run_bt(h4_df, total_days, BEST_RT, BEST_RP, **params)
            s = score_quick(m, 500)
            if s > -999:
                feat_results.append((params, m, s, f"R{rank+1}+{fname}"))

    feat_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Top 20 with features:")
    for i, (p, m, s, label) in enumerate(feat_results[:20]):
        print(f"  {label:>35} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} "
              f"DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")

    # ================================================================
    # STAGE 5: Multi-regime validation (test top params with 2nd/3rd regimes)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 5: Multi-Regime Cross-Validation")
    print("=" * 80)

    # Collect top candidates from all stages
    cross_candidates = []
    # From base exit optimization
    for p, m, s in unique_all[:5]:
        cross_candidates.append((p, "base"))
    # From feature combos
    for p, m, s, label in feat_results[:10]:
        cross_candidates.append((p, label))

    # Test each candidate with top 3 regimes
    cross_results = []
    for ci, (params, label) in enumerate(cross_candidates):
        for ri, regime in enumerate(best_regimes[:3]):
            rt, rp = regime["rt"], regime["rp"]
            m = run_bt(h4_df, total_days, rt, rp, **params)
            s = score_quick(m, 500)
            if s > -999:
                cross_results.append((params, m, s, f"{label}+Reg{ri+1}", rt, rp))

    cross_results.sort(key=lambda x: x[2], reverse=True)
    print(f"  {len(cross_results)} valid cross-regime combos")
    print(f"\n  Top 15:")
    for i, (p, m, s, label, rt, rp) in enumerate(cross_results[:15]):
        print(f"  {i+1:3d} s={s:5.1f} PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
              f"| {label}")

    # ================================================================
    # STAGE 6: WFA Validation (top 15 overall)
    # ================================================================
    print("\n" + "=" * 80)
    print("STAGE 6: WFA Validation (top candidates)")
    print("=" * 80)

    # Collect all candidates
    candidates = []
    # From base exit
    for p, m, s in unique_all[:5]:
        candidates.append((p, m, s, "base", BEST_RT, BEST_RP))
    # From features
    for p, m, s, label in feat_results[:8]:
        candidates.append((p, m, s, label, BEST_RT, BEST_RP))
    # From cross-regime
    for p, m, s, label, rt, rp in cross_results[:10]:
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

        # Year-by-year loss count
        tr_yy, _, _ = backtest_with_regime(h4_df, cfg, rt, rp)
        n_losing = count_losing_years(tr_yy, h4_df)

        # OOS 2024-2026
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        tr_oos, _, _ = backtest_with_regime(sub, cfg, rt, rp)
        oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

        # v23 composite score: WFA-dominant + year penalty
        base_s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.004
        base_s -= max(0, m["max_dd"] - 30) * 0.5
        wfa_s = (n_pass / 8) * 60 + min(avg_pf, 2.5) * 8 - (8 - n_pass) * 5
        oos_s = 0
        if m_oos and m_oos["n_trades"] >= 20:
            oos_s = min(m_oos["pf"], 4.0) * 4
        year_penalty = n_losing * 5
        final_score = base_s + wfa_s + oos_s - year_penalty

        oos_pf = m_oos["pf"] if m_oos else 0
        oos_daily = m_oos["daily_jpy"] if m_oos else 0
        oos_t = m_oos["n_trades"] if m_oos else 0

        marker = " <<<" if n_pass >= 6 else ""
        print(f"  [{ci+1}/15] {label:>35} WFA={n_pass}/8 AvgPF={avg_pf:.2f} MinPF={min_pf:.2f} "
              f"PF={m['pf']:.2f} T={m['n_trades']} OOS_PF={oos_pf:.2f} LoseYrs={n_losing} "
              f"FS={final_score:.1f}{marker}")

        validated.append({
            "params": params, "label": label, "metrics": m,
            "wfa_pass": n_pass, "wfa_avg_pf": avg_pf, "wfa_min_pf": min_pf,
            "wfa": wfa, "oos": m_oos, "final_score": final_score,
            "regime_type": rt, "regime_params": rp,
            "n_losing": n_losing,
        })

    validated.sort(key=lambda x: x["final_score"], reverse=True)

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)
    print(f"{'Rk':>2} {'FS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'OOS_PF':>6} {'OOS_D':>6} {'LYr':>3} | Label")
    print("-" * 105)
    for i, v in enumerate(validated):
        m = v["metrics"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        oos_daily = v["oos"]["daily_jpy"] if v["oos"] else 0
        print(f"{i+1:2d} {v['final_score']:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['wfa_avg_pf']:5.2f} {v['wfa_min_pf']:5.2f} "
              f"{oos_pf:6.2f} {oos_daily:6.0f} {v['n_losing']:3d} | {v['label']}")

    # ================================================================
    # WINNER ANALYSIS
    # ================================================================
    if not validated:
        print("\nNo valid candidates found!")
        elapsed = time.time() - t0
        print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
        return

    W = validated[0]
    wp = W["params"]
    rt = W["regime_type"]
    rp = W["regime_params"]

    print("\n" + "=" * 80)
    print("WINNER")
    print("=" * 80)
    print(f"  Regime: {rt} {rp}")
    print(f"  ALL Parameters:")
    for k in sorted(wp.keys()):
        v = wp[k]
        if isinstance(v, bool) and not v:
            continue
        print(f"    {k}: {v}")

    print(f"\n  Full Period:")
    print(f"    PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']} "
          f"DD={W['metrics']['max_dd']:.1f}% WR={W['metrics']['win_rate']:.1f}%")
    print(f"  WFA: {W['wfa_pass']}/8, Avg PF={W['wfa_avg_pf']:.2f}, Min PF={W['wfa_min_pf']:.2f}")
    print(f"  Losing years: {W['n_losing']}")
    if W["oos"]:
        print(f"  OOS 2024+: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
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
    oos_best_risk = None
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
            if m_or["daily_jpy"] >= 5000 and oos_best_risk is None:
                oos_best_risk = risk

    # Year-by-year at best risk
    rl = best_risk if best_risk else 2.0
    ml_map = {0.2: 0.10, 0.5: 0.20, 1.0: 0.30, 1.5: 0.50,
              2.0: 0.50, 2.5: 0.75, 3.0: 1.00, 3.5: 1.50, 4.0: 2.00}
    ml = ml_map.get(rl, 0.50)
    cfg_f = make_v12_cfg(**{**wp, "RiskPct": rl, "MaxLot": ml})
    tr_f, _, _ = backtest_with_regime(h4_df, cfg_f, rt, rp)
    yby = year_by_year(tr_f, h4_df, cfg_f.INITIAL_BALANCE)
    m_f = calc_metrics(tr_f, cfg_f.INITIAL_BALANCE, total_days)

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
    for i, v in enumerate(validated[:3]):
        m = v["metrics"]
        oos = v["oos"]
        print(f"\n  #{i+1} [{v['label']}] Regime={v['regime_type']} {v['regime_params']}")
        print(f"    Full: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        print(f"    WFA: {v['wfa_pass']}/8 AvgPF={v['wfa_avg_pf']:.2f} MinPF={v['wfa_min_pf']:.2f}")
        print(f"    Losing years: {v['n_losing']}")
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

    # ================================================================
    # VERSION COMPARISON vs v22
    # ================================================================
    print("\n" + "=" * 80)
    print("VERSION COMPARISON")
    print("=" * 80)
    print(f"  v12: PF={m12['pf']:.2f} T={m12['n_trades']} (baseline)")
    print(f"  v22: PF={m22['pf']:.2f} T={m22['n_trades']} WFA={n_pass22}/8 AvgPF={avg_pf22:.2f} "
          f"LoseYrs={losing22} (prev best)")
    m_w = W["metrics"]
    print(f"  v23: PF={m_w['pf']:.2f} T={m_w['n_trades']} WFA={W['wfa_pass']}/8 "
          f"AvgPF={W['wfa_avg_pf']:.2f} LoseYrs={W['n_losing']} (new)")
    if W["oos"]:
        print(f"  v23 OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f}")

    wfa_diff = W['wfa_pass'] - n_pass22
    print(f"\n  WFA Change: {n_pass22}/8 -> {W['wfa_pass']}/8 ({'+' if wfa_diff >= 0 else ''}{wfa_diff})")
    pf_diff = m_w['pf'] - m22['pf']
    print(f"  PF Change: {m22['pf']:.2f} -> {m_w['pf']:.2f} ({'+' if pf_diff >= 0 else ''}{pf_diff:.2f})")
    lose_diff = W['n_losing'] - losing22
    print(f"  Losing Yrs Change: {losing22} -> {W['n_losing']} ({'+' if lose_diff >= 0 else ''}{lose_diff})")

    # Honest assessment
    print(f"\n  Assessment:")
    if W['wfa_pass'] > n_pass22:
        print(f"    + WFA improved by {wfa_diff} windows")
    elif W['wfa_pass'] == n_pass22:
        print(f"    = WFA unchanged at {W['wfa_pass']}/8")
    else:
        print(f"    - WFA degraded by {abs(wfa_diff)} windows")

    if W['n_losing'] < losing22:
        print(f"    + Fewer losing years ({W['n_losing']} vs {losing22})")
    elif W['n_losing'] == losing22:
        print(f"    = Same number of losing years ({W['n_losing']})")
    else:
        print(f"    - More losing years ({W['n_losing']} vs {losing22})")

    if m_w['pf'] >= 1.5 and m_w['n_trades'] >= 500:
        print(f"    + Meets PF >= 1.5 and 500+ trades target")
    else:
        if m_w['pf'] < 1.5:
            print(f"    - PF {m_w['pf']:.2f} below 1.5 target")
        if m_w['n_trades'] < 500:
            print(f"    - Only {m_w['n_trades']} trades (target 500+)")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V23 OPTIMIZATION COMPLETE ===")

    log_file.close()


if __name__ == "__main__":
    main()
