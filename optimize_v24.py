"""
GoldAlpha v24 Optimizer - Adaptive Parameters for WFA 7/8+
Problem: v23 achieves WFA 6/8 but W2 (2017-2018) and W4 (2019-2020H1) fail
         because gold was ranging/mild downtrend. Strategy too aggressive.

Key approaches:
  Phase 1: Broad conservative grid (5000 combos) with D1 quick screen
  Phase 2: WFA validation with Combined regime on top 50
  Phase 3: Feature combos (TD, Structure, ADX, VolBand, PartialClose, Session)
  Phase 4: Regime variants + fine-tune

Scoring: WFA-heavy with min PF bonus and losing year penalty
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

log_file = open("/tmp/v24_output.log", "w")
sys.stdout = Tee(sys.__stdout__, log_file)


def make_cfg(**overrides):
    """v12-base config with all features off by default."""
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
    cfg.USE_VOL_REGIME = False; cfg.VOL_LOW_MULT = 0.5; cfg.VOL_HIGH_MULT = 2.5
    cfg.USE_SESSION_FILTER = False
    cfg.USE_RSI_CONFIRM = False
    cfg.USE_ADX_FILTER = False; cfg.ADX_Period = 14; cfg.ADX_MIN = 20
    cfg.USE_PARTIAL_CLOSE = False; cfg.PARTIAL_ATR = 1.5; cfg.PARTIAL_RATIO = 0.5
    cfg.USE_W1_SEPARATION = False; cfg.W1_SEP_MIN = 0.005
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_bt(h4_df, total_days, rt, rp, **params):
    cfg = make_cfg(**params)
    trades, _, _ = backtest_with_regime(h4_df, cfg, rt, rp)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def run_bt_trades(h4_df, rt, rp, **params):
    cfg = make_cfg(**params)
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


def score_v24(m, wfa=None, n_losing=0, m_oos=None, min_trades=500):
    """WFA-heavy scoring function for v24."""
    if m is None or m["n_trades"] < min_trades or m["pf"] < 1.3:
        return -999
    s = min(m["pf"], 3.0) * 10
    s += min(m["n_trades"], 1500) * 0.003
    s -= max(0, m["max_dd"] - 30) * 0.5
    s -= n_losing * 5
    if wfa:
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa])
        min_pf = min(r["pf"] for r in wfa)
        s += (n_pass / 8) * 60  # Heavy WFA weight
        s += min(avg_pf, 2.5) * 8
        s += min(min_pf, 1.0) * 10  # Reward high minimum PF
        s -= (8 - n_pass) * 8  # Penalize each failure
    if m_oos and m_oos["n_trades"] >= 20:
        s += min(m_oos["pf"], 4.0) * 4
    return s


def quick_score(m, min_t=500):
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.3:
        return -999
    return min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.003 - max(0, m["max_dd"] - 30) * 0.5


def full_validate(h4_df, total_days, params, rt, rp, tag=""):
    """Full validation: full-period metrics + WFA + OOS + losing years."""
    cfg = make_cfg(**params)
    m = run_bt(h4_df, total_days, rt, rp, **params)
    if m is None or m["n_trades"] < 400 or m["pf"] < 1.2:
        return None
    wfa = run_regime_wfa(h4_df, cfg, rt, rp)
    n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
    avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
    min_pf = min(r["pf"] for r in wfa) if wfa else 0
    tr, _ = run_bt_trades(h4_df, rt, rp, **params)
    n_losing = count_losing_years(tr, h4_df)
    # OOS
    sub = h4_df[h4_df.index >= "2022-01-01"].copy()
    tr_oos, _, _ = backtest_with_regime(sub, cfg, rt, rp)
    oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
    oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
    m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)
    fs = score_v24(m, wfa, n_losing, m_oos)
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
    print("GoldAlpha v24 - Adaptive Parameters for WFA 7/8+")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print("=" * 80)

    # v23 winner params
    V23 = dict(SL_ATR_Mult=4.0, Trail_ATR=4.9, BE_ATR=0.3,
               EMA_Zone_ATR=0.30, ATR_Filter=0.70, BodyRatio=0.32,
               MaxPositions=6, D1_Tolerance=0.01)
    COMB_RT = "combined"
    COMB_RP = {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.005}
    D1_RT = "d1_slope"
    D1_RP = {"slope_bars": 5, "min_slope": 0.002}

    # ================================================================
    # BASELINES
    # ================================================================
    print("\n--- Baselines ---")
    m12 = run_bt(h4_df, total_days, "none", {})
    print(f"v12 (no regime): PF={m12['pf']:.2f} T={m12['n_trades']} DD={m12['max_dd']:.1f}%")

    # v23 baseline
    v23_val = full_validate(h4_df, total_days, V23, COMB_RT, COMB_RP, "v23")
    print(f"v23 baseline:    PF={v23_val['m']['pf']:.2f} T={v23_val['m']['n_trades']} "
          f"WFA={v23_val['n_pass']}/8 AvgPF={v23_val['avg_pf']:.2f} "
          f"MinPF={v23_val['min_pf']:.2f} LY={v23_val['n_losing']}")
    for j, w in enumerate(v23_val["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"  W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} [{st}]")

    tr23, _ = run_bt_trades(h4_df, COMB_RT, COMB_RP, **V23)
    yby23 = year_by_year(tr23, h4_df)
    losing23 = sum(1 for v in yby23.values() if v["pnl"] < 0)
    print(f"v23 year-by-year (losing: {losing23}):")
    for yr in sorted(yby23.keys()):
        v = yby23[yr]
        tag = " LOSS" if v["pnl"] < 0 else ""
        print(f"  {yr}: T={v['n']:3d} PF={v['pf']:5.2f} PnL={v['pnl']:+10,.0f}{tag}")

    print(f"\nBaselines done in {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 1: Broad conservative grid (D1 quick screen)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 1: Broad Grid Search (D1 quick screen)")
    print("=" * 80)

    # Key insight: more conservative params may help ranging periods
    # 5 * 6 * 5 * 4 * 4 * 3 * 4 * 4 = 57,600 -> too many
    # Reduce: 5 * 5 * 4 * 4 * 3 * 3 * 4 * 3 = 43,200 -> still too many
    # Need ~5000: 5 * 4 * 4 * 3 * 3 * 3 * 3 * 3 = 9720 -> cut further
    # 4 * 4 * 4 * 3 * 3 * 3 * 3 * 3 = 7776 with D1 quick screen ~30min
    # Actually aim for ~5000: 4*4*4*3*3*3*3*2 = 5184
    screen_grid = {
        "SL_ATR_Mult":  [2.0, 2.5, 3.0, 3.5],           # 4 - more conservative options
        "Trail_ATR":    [2.0, 3.0, 4.0, 4.9],            # 4
        "BE_ATR":       [0.2, 0.5, 0.8, 1.0],            # 4
        "EMA_Zone_ATR": [0.3, 0.4, 0.5],                 # 3
        "ATR_Filter":   [0.4, 0.5, 0.7],                 # 3
        "BodyRatio":    [0.24, 0.28, 0.32],               # 3
        "MaxPositions": [2, 3, 4],                        # 3 - much more conservative
        "D1_Tolerance": [0.003, 0.01],                    # 2
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
        qs = quick_score(m, 400)  # Relaxed for screen
        if qs > -999:
            quick_results.append((params, m, qs))
            if qs > best_qs:
                best_qs = qs
                print(f"    [{idx+1}/{n_combos}] BEST qs={qs:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% | "
                      f"SL={params['SL_ATR_Mult']} Tr={params['Trail_ATR']} "
                      f"BE={params['BE_ATR']} Zone={params['EMA_Zone_ATR']} "
                      f"MaxP={params['MaxPositions']} Body={params['BodyRatio']}")
        if (idx + 1) % 1000 == 0 and qs <= best_qs:
            print(f"    [{idx+1}/{n_combos}] {len(quick_results)} valid...")

    quick_results.sort(key=lambda x: x[2], reverse=True)
    print(f"  -> {len(quick_results)} valid, best qs={quick_results[0][2]:.1f}")
    print(f"  Phase 1 done in {time.time()-t0:.0f}s")

    # Also include aggressive v23-like params in the mix
    extra_combos = []
    for sl in [3.5, 4.0, 4.5, 5.0]:
        for tr in [3.5, 4.0, 4.9, 5.5]:
            for be in [0.2, 0.3, 0.5]:
                for zone in [0.20, 0.30]:
                    for mp in [4, 5, 6]:
                        for body in [0.24, 0.32]:
                            for d1t in [0.007, 0.01]:
                                for af in [0.60, 0.70]:
                                    params = dict(SL_ATR_Mult=sl, Trail_ATR=tr, BE_ATR=be,
                                                  EMA_Zone_ATR=zone, ATR_Filter=af,
                                                  BodyRatio=body, MaxPositions=mp,
                                                  D1_Tolerance=d1t)
                                    m = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
                                    qs = quick_score(m, 400)
                                    if qs > -999:
                                        extra_combos.append((params, m, qs))

    print(f"  Extra aggressive combos: {len(extra_combos)} valid")
    quick_results.extend(extra_combos)
    quick_results.sort(key=lambda x: x[2], reverse=True)
    print(f"  Total valid: {len(quick_results)}")

    # ================================================================
    # PHASE 2: WFA Validation (Combined regime, top 60)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 2: WFA Validation (Combined regime, top 60)")
    print("=" * 80)

    candidates = []
    seen = set()
    # Always include v23
    seen.add(tuple(sorted(V23.items())))
    candidates.append(V23)
    for p, m, s in quick_results:
        key = tuple(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            candidates.append(p)
        if len(candidates) >= 60:
            break

    print(f"  Validating {len(candidates)} candidates...")
    validated = [v23_val]  # Already have v23
    best_wfa = v23_val["n_pass"]
    for ci, params in enumerate(candidates[1:], 1):
        v = full_validate(h4_df, total_days, params, COMB_RT, COMB_RP, "Ph2")
        if v is None:
            continue
        validated.append(v)
        marker = ""
        if v["n_pass"] >= 7:
            marker = " *** 7/8! ***"
        elif v["n_pass"] >= 6:
            marker = " <<<"
        if v["n_pass"] > best_wfa:
            best_wfa = v["n_pass"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        if ci % 10 == 0 or v["n_pass"] >= 6:
            print(f"    [{ci}/{len(candidates)-1}] WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                  f"MinPF={v['min_pf']:.2f} PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                  f"OOS={oos_pf:.2f} LY={v['n_losing']} FS={v['score']:.1f}{marker}")

    validated.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  Top 15 after Phase 2:")
    print(f"  {'Rk':>2} {'FS':>6} {'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'PF':>5} "
          f"{'T':>5} {'DD':>5} {'LY':>2} {'OOS':>5} | Key params")
    print("  " + "-" * 95)
    for i, v in enumerate(validated[:15]):
        m = v["m"]; p = v["params"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {v['avg_pf']:5.2f} "
              f"{v['min_pf']:5.2f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
              f"{v['n_losing']:2d} {oos_pf:5.2f} | SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} "
              f"BE={p['BE_ATR']:.1f} Z={p['EMA_Zone_ATR']:.2f} MP={p['MaxPositions']} "
              f"B={p['BodyRatio']:.2f} AF={p['ATR_Filter']:.2f} D1T={p['D1_Tolerance']:.3f}")
    print(f"  Phase 2 done in {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 3: Feature combos on top WFA configs
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 3: Feature Combinations")
    print("=" * 80)

    # Select top configs with best WFA
    top_wfa = [v for v in validated if v["n_pass"] >= max(5, best_wfa - 1)][:6]
    if len(top_wfa) < 4:
        top_wfa = validated[:6]

    feature_sets = [
        # Time decay variants
        ("TD25", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 25}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD35", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        # Structure
        ("S2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("S3", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3}),
        # ADX filter
        ("ADX20", {"USE_ADX_FILTER": True, "ADX_MIN": 20}),
        ("ADX25", {"USE_ADX_FILTER": True, "ADX_MIN": 25}),
        # Volatility band
        ("Vol0.5-2.5", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Vol0.6-2.0", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.6, "VOL_HIGH_MULT": 2.0}),
        # EMA slope
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("Slope3", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3}),
        # Partial close
        ("PC1.5", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5}),
        ("PC2.0", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5}),
        # W1 separation
        ("W1Sep3", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("W1Sep5", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.005}),
        # Session filter
        ("Session", {"USE_SESSION_FILTER": True, "TRADE_START_HOUR": 2, "TRADE_END_HOUR": 21}),
        # Combos
        ("S2+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                     "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("S2+TD35", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                     "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("Slope5+TD30", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                         "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("Slope5+S2", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                       "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("ADX20+S2", {"USE_ADX_FILTER": True, "ADX_MIN": 20,
                      "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("ADX20+TD30", {"USE_ADX_FILTER": True, "ADX_MIN": 20,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("PC1.5+TD35", {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("Vol+S2", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5,
                    "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("S2+Slope5+TD30", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                            "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5,
                            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("ADX20+S2+TD30", {"USE_ADX_FILTER": True, "ADX_MIN": 20,
                           "USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
                           "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("W1Sep3+S2", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003,
                       "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
    ]

    feat_results = []
    feat_best_wfa = best_wfa
    for ri, base in enumerate(top_wfa):
        bp = base["params"]
        print(f"\n  Base R{ri+1}: WFA={base['n_pass']}/8 PF={base['m']['pf']:.2f} "
              f"T={base['m']['n_trades']} | SL={bp['SL_ATR_Mult']:.1f} Tr={bp['Trail_ATR']:.1f} "
              f"BE={bp['BE_ATR']:.1f} MP={bp['MaxPositions']}")
        for fname, fparams in feature_sets:
            params = {**bp, **fparams}
            v = full_validate(h4_df, total_days, params, COMB_RT, COMB_RP, f"R{ri+1}+{fname}")
            if v:
                feat_results.append(v)
                marker = ""
                if v["n_pass"] >= 7:
                    marker = " *** 7/8! ***"
                elif v["n_pass"] > base["n_pass"]:
                    marker = " <IMPROVED>"
                if v["n_pass"] > feat_best_wfa:
                    feat_best_wfa = v["n_pass"]
                if v["n_pass"] >= 5 or v["n_pass"] > base["n_pass"]:
                    oos_pf = v["oos"]["pf"] if v["oos"] else 0
                    print(f"    +{fname:>18} WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                          f"MinPF={v['min_pf']:.2f} PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                          f"FS={v['score']:.1f}{marker}")

    print(f"\n  Phase 3 done in {time.time()-t0:.0f}s")
    print(f"  Best WFA so far: {feat_best_wfa}/8")

    # ================================================================
    # PHASE 4: Regime variants + fine-tune on promising configs
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 4: Regime Variants + Fine-Tune")
    print("=" * 80)

    # Gather all results so far
    all_so_far = validated + feat_results
    all_so_far.sort(key=lambda x: x["score"], reverse=True)
    seen_v = set()
    unique_v = []
    for v in all_so_far:
        key = (tuple(sorted(v["params"].items())), v.get("rt", COMB_RT),
               tuple(sorted(v.get("rp", COMB_RP).items())))
        if key not in seen_v:
            seen_v.add(key)
            unique_v.append(v)

    # Phase 4a: Alternative regimes on top configs
    alt_regimes = [
        ("Comb(5/0.002,0.003)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.003}),
        ("Comb(5/0.002,0.007)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.007}),
        ("Comb(5/0.003,0.005)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.003, "w1_min_spread": 0.005}),
        ("Comb(5/0.001,0.005)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_spread": 0.005}),
        ("Comb(7/0.002,0.005)", "combined", {"d1_slope_bars": 7, "d1_min_slope": 0.002, "w1_min_spread": 0.005}),
        ("Comb(7/0.003,0.005)", "combined", {"d1_slope_bars": 7, "d1_min_slope": 0.003, "w1_min_spread": 0.005}),
        ("Comb(10/0.002,0.005)", "combined", {"d1_slope_bars": 10, "d1_min_slope": 0.002, "w1_min_spread": 0.005}),
        ("Comb(5/0.002,0.010)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.010}),
        ("D1only(5,0.002)", "d1_slope", {"slope_bars": 5, "min_slope": 0.002}),
        ("D1only(5,0.003)", "d1_slope", {"slope_bars": 5, "min_slope": 0.003}),
        ("ADX(14,20)", "adx", {"period": 14, "min_adx": 20}),
        ("ADX(14,22)", "adx", {"period": 14, "min_adx": 22}),
    ]

    # Take top 5 unique param sets (regardless of regime)
    top5_params = []
    seen_p = set()
    for v in unique_v:
        key = tuple(sorted(v["params"].items()))
        if key not in seen_p:
            seen_p.add(key)
            top5_params.append(v["params"])
        if len(top5_params) >= 5:
            break

    alt_results = []
    alt_best_wfa = feat_best_wfa
    for pi, bp in enumerate(top5_params):
        for rname, rt, rp in alt_regimes:
            v = full_validate(h4_df, total_days, bp, rt, rp, f"P{pi+1}+{rname}")
            if v:
                alt_results.append(v)
                marker = ""
                if v["n_pass"] >= 7:
                    marker = " *** 7/8! ***"
                if v["n_pass"] > alt_best_wfa:
                    alt_best_wfa = v["n_pass"]
                if v["n_pass"] >= 5:
                    print(f"    P{pi+1}+{rname:>25} WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                          f"PF={v['m']['pf']:.2f} T={v['m']['n_trades']} FS={v['score']:.1f}{marker}")

    # Phase 4b: Fine-tune exit params on best WFA configs
    print(f"\n  Fine-tuning exit params on top configs...")
    top_for_fine = [v for v in (unique_v + feat_results + alt_results)
                    if v["n_pass"] >= max(5, alt_best_wfa - 1)]
    # Deduplicate by params + regime
    seen_ft = set()
    unique_fine_bases = []
    for v in sorted(top_for_fine, key=lambda x: x["score"], reverse=True):
        key = (tuple(sorted(v["params"].items())), v.get("rt", COMB_RT),
               tuple(sorted(v.get("rp", COMB_RP).items())))
        if key not in seen_ft:
            seen_ft.add(key)
            unique_fine_bases.append(v)
        if len(unique_fine_bases) >= 5:
            break

    fine_results = []
    for fi, base in enumerate(unique_fine_bases):
        bp = base["params"]
        rt = base.get("rt", COMB_RT)
        rp = base.get("rp", COMB_RP)
        # Generate fine-tune grid for exit params
        fine_grid = {
            "SL_ATR_Mult": rng(bp["SL_ATR_Mult"], 0.3, 2),
            "Trail_ATR": rng(bp["Trail_ATR"], 0.3, 2),
            "BE_ATR": rng(bp["BE_ATR"], 0.15, 2),
        }
        fixed = {k: v for k, v in bp.items() if k not in fine_grid}
        keys_f = list(fine_grid.keys())
        combos_f = list(product(*fine_grid.values()))
        print(f"\n  Fine #{fi+1} (WFA={base['n_pass']}/8, regime={rt}): {len(combos_f)} exit combos")

        # Quick D1 screen
        fine_q = []
        for combo in combos_f:
            params = {**fixed, **dict(zip(keys_f, combo))}
            # Use D1 quick screen for speed
            m = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
            qs = quick_score(m, 400)
            if qs > -999:
                fine_q.append((params, m, qs))
        fine_q.sort(key=lambda x: x[2], reverse=True)

        # WFA validate top 10
        for qi, (params, _, _) in enumerate(fine_q[:10]):
            v = full_validate(h4_df, total_days, params, rt, rp, f"Fine{fi+1}")
            if v:
                fine_results.append(v)
                marker = ""
                if v["n_pass"] >= 7:
                    marker = " *** 7/8! ***"
                if v["n_pass"] > alt_best_wfa:
                    alt_best_wfa = v["n_pass"]
                if v["n_pass"] >= 5 or qi == 0:
                    oos_pf = v["oos"]["pf"] if v["oos"] else 0
                    print(f"      WFA={v['n_pass']}/8 PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                          f"FS={v['score']:.1f} | SL={params['SL_ATR_Mult']:.1f} "
                          f"Tr={params['Trail_ATR']:.1f} BE={params['BE_ATR']:.2f}{marker}")

    print(f"\n  Phase 4 done in {time.time()-t0:.0f}s")

    # ================================================================
    # FINAL RANKING
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)

    all_final = validated + feat_results + alt_results + fine_results
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
    print("-" * 110)
    for i, v in enumerate(ranked[:25]):
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
    for risk, maxlot in [(0.50, 0.20), (1.0, 0.30), (1.5, 0.50),
                          (2.0, 0.50), (2.5, 0.75), (3.0, 1.00), (3.5, 1.50)]:
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

        # WFA details for top 3
        print(f"    WFA details:")
        for j, w in enumerate(v["wfa"]):
            st = "PASS" if w["pf"] > 1.0 else "FAIL"
            print(f"      W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} [{st}]")

    # ================================================================
    # VERSION COMPARISON vs v23
    # ================================================================
    print("\n" + "=" * 80)
    print("VERSION COMPARISON")
    print("=" * 80)
    print(f"  v12: PF={m12['pf']:.2f} T={m12['n_trades']} (baseline, no regime)")
    print(f"  v23: PF={v23_val['m']['pf']:.2f} T={v23_val['m']['n_trades']} "
          f"WFA={v23_val['n_pass']}/8 AvgPF={v23_val['avg_pf']:.2f} "
          f"MinPF={v23_val['min_pf']:.2f} LoseYrs={v23_val['n_losing']} (prev winner)")
    m_w = W["m"]
    print(f"  v24: PF={m_w['pf']:.2f} T={m_w['n_trades']} WFA={W['n_pass']}/8 "
          f"AvgPF={W['avg_pf']:.2f} MinPF={W['min_pf']:.2f} "
          f"LoseYrs={W['n_losing']} (new)")
    if W["oos"]:
        print(f"  v24 OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} "
              f"Daily={W['oos']['daily_jpy']:.0f}")

    wfa_diff = W['n_pass'] - v23_val['n_pass']
    print(f"\n  WFA: {v23_val['n_pass']}/8 -> {W['n_pass']}/8 ({'+' if wfa_diff >= 0 else ''}{wfa_diff})")
    pf_diff = m_w['pf'] - v23_val['m']['pf']
    print(f"  PF: {v23_val['m']['pf']:.2f} -> {m_w['pf']:.2f} ({'+' if pf_diff >= 0 else ''}{pf_diff:.2f})")
    t_diff = m_w['n_trades'] - v23_val['m']['n_trades']
    print(f"  Trades: {v23_val['m']['n_trades']} -> {m_w['n_trades']} ({'+' if t_diff >= 0 else ''}{t_diff})")
    ly_diff = W['n_losing'] - v23_val['n_losing']
    print(f"  Losing years: {v23_val['n_losing']} -> {W['n_losing']} ({'+' if ly_diff >= 0 else ''}{ly_diff})")

    print(f"\n  Assessment:")
    if W['n_pass'] > v23_val['n_pass']:
        print(f"    + WFA improved by {wfa_diff} windows ({v23_val['n_pass']}/8 -> {W['n_pass']}/8)")
    elif W['n_pass'] == v23_val['n_pass']:
        print(f"    = WFA unchanged at {W['n_pass']}/8")
    else:
        print(f"    - WFA degraded by {abs(wfa_diff)} windows")
    if W['n_losing'] < v23_val['n_losing']:
        print(f"    + Fewer losing years ({W['n_losing']} vs {v23_val['n_losing']})")
    elif W['n_losing'] == v23_val['n_losing']:
        print(f"    = Same losing years ({W['n_losing']})")
    else:
        print(f"    - More losing years ({W['n_losing']} vs {v23_val['n_losing']})")
    if m_w['pf'] >= 1.5 and m_w['n_trades'] >= 500:
        print(f"    + Meets PF >= 1.5 and 500+ trades target")
    elif m_w['pf'] >= 1.5:
        print(f"    ~ PF >= 1.5 but trades below 500 ({m_w['n_trades']})")
    else:
        print(f"    - PF below 1.5 ({m_w['pf']:.2f})")

    if W['n_pass'] < 7:
        print(f"\n  HONEST NOTE: Could not achieve 7/8 WFA target.")
        print(f"  Best WFA achieved: {W['n_pass']}/8")
        # Show what the 7/8+ configs look like if any exist
        seven_plus = [v for v in ranked if v["n_pass"] >= 7]
        if seven_plus:
            print(f"  Found {len(seven_plus)} configs with 7/8+, but scored lower overall:")
            for v in seven_plus[:3]:
                m = v["m"]
                print(f"    {v['tag']}: WFA={v['n_pass']}/8 PF={m['pf']:.2f} T={m['n_trades']} "
                      f"DD={m['max_dd']:.1f}% FS={v['score']:.1f}")
        else:
            print(f"  No configurations achieved 7/8 WFA in this search space.")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V24 OPTIMIZATION COMPLETE ===")
    log_file.close()


if __name__ == "__main__":
    main()
