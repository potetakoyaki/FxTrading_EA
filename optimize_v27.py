"""
GoldAlpha v27 Optimizer - High-Frequency v12 Dip-Buy with Smart Filters
FAST version: pre-compute indicators once, vectorized regime masking.

Goal: 500+ trades, PF >= 1.5, 300K JPY, daily avg >= 5000 JPY
"""

import sys
import os
import time
import numpy as np
import pandas as pd
from itertools import product
from copy import deepcopy

sys.path.insert(0, "/tmp/FxTrading_EA")
from backtest_goldalpha import (
    load_csv, GoldAlphaConfig, precompute_indicators,
    backtest_goldalpha, calc_metrics, run_wfa,
    np_ema, np_sma, np_atr, np_adx, resample_to_daily, resample_to_weekly,
    _calc_lot, _manage_positions, _close_position, _unrealized_pnl, _calc_pnl
)


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


log_file = open("/tmp/v27_output.log", "w")
sys.stdout = Tee(sys.__stdout__, log_file)


# ============================================================
# v27 Config
# ============================================================
def make_cfg(**overrides):
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    cfg.SL_ATR_Mult = 2.5; cfg.Trail_ATR = 3.0; cfg.BE_ATR = 0.5
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.28
    cfg.EMA_Zone_ATR = 0.50; cfg.ATR_Filter = 0.40; cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 3; cfg.MinLot = 0.01; cfg.MaxLot = 0.50
    cfg.INITIAL_BALANCE = 300_000
    cfg.USE_EMA_SLOPE = False; cfg.EMA_SLOPE_BARS = 5
    cfg.USE_STRUCTURE = False; cfg.STRUCTURE_BARS = 3
    cfg.USE_TIME_DECAY = False; cfg.MAX_HOLD_BARS = 30
    cfg.USE_VOL_REGIME = False; cfg.VOL_LOW_MULT = 0.5; cfg.VOL_HIGH_MULT = 2.5
    cfg.USE_SESSION_FILTER = False; cfg.TRADE_START_HOUR = 2; cfg.TRADE_END_HOUR = 21
    cfg.USE_RSI_CONFIRM = False; cfg.RSI_Period = 14
    cfg.USE_PARTIAL_CLOSE = False; cfg.PARTIAL_ATR = 1.5; cfg.PARTIAL_RATIO = 0.5
    cfg.USE_W1_SEPARATION = False; cfg.W1_SEP_MIN = 0.005
    cfg.USE_ADX_FILTER = False; cfg.ADX_Period = 14; cfg.ADX_MIN = 20
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ============================================================
# Pre-compute regime masks (vectorized, done once)
# ============================================================
def compute_regime_mask(ind, d1_slope_bars, d1_min_slope, w1_min_sep):
    """Returns boolean mask: True = blocked (don't trade)."""
    h4_times = ind[4]
    w1_fast = ind[5]; w1_slow = ind[6]; w1_times = ind[7]
    d1_ema_arr = ind[9]; d1_times = ind[10]
    n = len(h4_times)

    w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1
    d1_idx_map = np.searchsorted(d1_times, h4_times, side="right") - 1

    blocked = np.zeros(n, dtype=bool)

    # W1 separation blocking
    if w1_min_sep > 0:
        for i in range(n):
            wi = w1_idx_map[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0:
                    sep_pct = abs(w1_fast[wi] - w1_slow[wi]) / mid
                    if sep_pct < w1_min_sep:
                        blocked[i] = True

    # D1 slope blocking
    if d1_min_slope > 0:
        for i in range(n):
            if blocked[i]:
                continue
            di = d1_idx_map[i]
            if d1_slope_bars <= di < len(d1_ema_arr):
                prev = d1_ema_arr[di - d1_slope_bars]
                cur = d1_ema_arr[di]
                if prev > 0:
                    slope_pct = abs(cur - prev) / prev
                    if slope_pct < d1_min_slope:
                        blocked[i] = True

    return blocked


def apply_regime_mask(ind, mask):
    """Apply blocking mask to avg_atr (set blocked bars to 999999)."""
    mod_ind = list(ind)
    avg_atr = ind[13].copy()
    avg_atr[mask] = 999999
    mod_ind[13] = avg_atr
    return tuple(mod_ind)


# ============================================================
# Fast backtest: pre-computed indicators + regime mask
# ============================================================
def fast_bt(ind, mask, cfg, total_days):
    """Run backtest with pre-computed indicators and regime mask."""
    mod = apply_regime_mask(ind, mask)
    # Need to recompute h4_ema, h4_atr, h4_avg_atr if cfg changes H4_EMA/ATR params
    # But since we fix those, we can reuse
    trades, eq, final = backtest_goldalpha(*mod, cfg=cfg)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def fast_bt_trades(ind, mask, cfg):
    """Return trades list."""
    mod = apply_regime_mask(ind, mask)
    trades, eq, final = backtest_goldalpha(*mod, cfg=cfg)
    return trades


# ============================================================
# WFA with pre-computed per-window indicators
# ============================================================
def run_fast_wfa(h4_df, cfg, regime_params, n_windows=8):
    """WFA using per-window indicator computation."""
    total_bars = len(h4_df)
    window_size = total_bars // n_windows
    oos_size = int(window_size * 0.25)
    results = []

    for w in range(n_windows):
        window_end = min((w + 1) * window_size, total_bars)
        oos_start = window_end - oos_size
        data_start = max(0, oos_start - 600)
        sub = h4_df.iloc[data_start:window_end].copy()

        sub_ind = precompute_indicators(sub, cfg)
        mask = compute_regime_mask(sub_ind,
                                    regime_params["d1_slope_bars"],
                                    regime_params["d1_min_slope"],
                                    regime_params["w1_min_sep"])
        mod = apply_regime_mask(sub_ind, mask)
        trades, _, _ = backtest_goldalpha(*mod, cfg=cfg)

        oos_time = h4_df.index[oos_start]
        oos_trades = [t for t in trades if t["open_time"] >= oos_time]
        oos_end_time = h4_df.index[min(window_end - 1, total_bars - 1)]
        oos_days = max(1, (oos_end_time - oos_time).days)
        m = calc_metrics(oos_trades, cfg.INITIAL_BALANCE, oos_days)
        if m:
            results.append(m)
    return results


# ============================================================
# Scoring
# ============================================================
def quick_score(m, min_t=400):
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.2:
        return -999
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 2000) * 0.003
    s -= max(0, m["max_dd"] - 25) * 0.5
    s += min(m["daily_jpy"], 8000) * 0.001
    return s


def full_score(m, wfa=None, n_losing=0, m_oos=None, min_t=400):
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.2:
        return -999
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 2000) * 0.003
    s -= max(0, m["max_dd"] - 25) * 0.5
    s -= n_losing * 3
    s += min(m["daily_jpy"], 8000) * 0.001
    if wfa:
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa])
        min_pf = min(r["pf"] for r in wfa)
        s += (n_pass / 8) * 60
        s += min(avg_pf, 2.5) * 8
        s += min(min_pf, 1.0) * 10
        s -= (8 - n_pass) * 8
    if m_oos and m_oos["n_trades"] >= 15:
        s += min(m_oos["pf"], 4.0) * 4
    return s


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


def full_validate(h4_df, total_days, ind, params, rp, tag=""):
    """Full validation with pre-computed full-period indicators."""
    cfg = make_cfg(**params)
    mask = compute_regime_mask(ind, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"])
    m = fast_bt(ind, mask, cfg, total_days)
    if m is None or m["n_trades"] < 300 or m["pf"] < 1.15:
        return None
    wfa = run_fast_wfa(h4_df, cfg, rp)
    n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
    avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
    min_pf = min(r["pf"] for r in wfa) if wfa else 0
    trades = fast_bt_trades(ind, mask, cfg)
    n_losing = count_losing_years(trades, h4_df)

    # OOS 2025+
    sub = h4_df[h4_df.index >= "2023-01-01"].copy()
    sub_ind = precompute_indicators(sub, cfg)
    sub_mask = compute_regime_mask(sub_ind, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"])
    mod = apply_regime_mask(sub_ind, sub_mask)
    tr_oos, _, _ = backtest_goldalpha(*mod, cfg=cfg)
    oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2025-01-01")]
    oos_days = max(1, (sub.index[-1] - pd.Timestamp("2025-01-01")).days)
    m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

    fs = full_score(m, wfa, n_losing, m_oos)
    return {
        "params": params, "m": m, "wfa": wfa, "n_pass": n_pass,
        "avg_pf": avg_pf, "min_pf": min_pf, "n_losing": n_losing,
        "oos": m_oos, "score": fs, "tag": tag, "rp": rp
    }


def rng(v, step, n=2):
    return sorted(set([round(v + step * i, 4) for i in range(-n, n + 1) if v + step * i > 0]))


# ============================================================
# MAIN
# ============================================================
def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    total_bars = len(h4_df)

    print("=" * 80)
    print("GoldAlpha v27 - High-Frequency v12 Dip-Buy + Smart Filters (FAST)")
    print(f"H4: {total_bars} bars, {total_days} days")
    print(f"Target: 500+ trades, PF >= 1.5, 300K JPY, daily >= 5000 JPY")
    print("=" * 80)

    # Pre-compute indicators ONCE for full dataset
    cfg_base = make_cfg()
    ind_full = precompute_indicators(h4_df, cfg_base)
    print(f"Indicators pre-computed in {time.time()-t0:.1f}s")

    # Pre-compute regime masks for all variants
    regime_variants = [
        ("none",    {"d1_slope_bars": 5, "d1_min_slope": 0.0, "w1_min_sep": 0.0}),
        ("D1low",   {"d1_slope_bars": 5, "d1_min_slope": 0.0005, "w1_min_sep": 0.0}),
        ("D1med",   {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_sep": 0.0}),
        ("D1+W1lo", {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_sep": 0.003}),
        ("D1+W1hi", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_sep": 0.005}),
    ]

    regime_masks = {}
    for rv_name, rp in regime_variants:
        mask = compute_regime_mask(ind_full, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"])
        regime_masks[rv_name] = (mask, rp)
        pct_blocked = mask.sum() / len(mask) * 100
        print(f"  Regime {rv_name}: {pct_blocked:.1f}% bars blocked")

    print(f"Regime masks computed in {time.time()-t0:.1f}s")

    # ================================================================
    # BASELINE
    # ================================================================
    print("\n--- Baselines ---")
    for rv_name, (mask, rp) in regime_masks.items():
        m = fast_bt(ind_full, mask, cfg_base, total_days)
        if m:
            print(f"  {rv_name:>8}: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% "
                  f"Daily={m['daily_jpy']:.0f}")

    # ================================================================
    # PHASE 1: Grid Search (optimized - indicators computed once)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 1: Grid Search (fast, indicators pre-computed)")
    print("=" * 80)

    # Focused grid: 4*4*3*3*3*3*3 = 3888 combos per regime
    grid = {
        "SL_ATR_Mult":    [1.5, 2.0, 2.5, 3.0],         # 4
        "Trail_ATR":      [2.0, 2.5, 3.0, 3.5],          # 4
        "BE_ATR":         [0.3, 0.5, 1.0],               # 3
        "EMA_Zone_ATR":   [0.3, 0.4, 0.5],               # 3
        "BodyRatio":      [0.24, 0.28, 0.32],             # 3
        "MaxPositions":   [2, 3, 4],                      # 3
        "ATR_Filter":     [0.3, 0.4, 0.5],               # 3
    }

    keys = list(grid.keys())
    combos = list(product(*grid.values()))
    n_combos = len(combos)
    n_regimes = len(regime_variants) - 1  # skip "none" for speed, test it on top results later
    print(f"Grid: {n_combos} combos x {n_regimes} regimes = {n_combos * n_regimes} total")

    all_quick = []
    best_qs = -999
    total_tested = 0

    for rv_name, (mask, rp) in list(regime_masks.items())[1:]:  # skip "none"
        print(f"\n  Regime: {rv_name}")
        rv_best = -999
        t_rv = time.time()

        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            params["D1_Tolerance"] = 0.003
            cfg = make_cfg(**params)
            m = fast_bt(ind_full, mask, cfg, total_days)
            qs = quick_score(m, 400)
            total_tested += 1

            if qs > -999:
                all_quick.append((params, m, qs, rv_name, rp))
                if qs > rv_best:
                    rv_best = qs
                    if qs > best_qs:
                        best_qs = qs
                    print(f"    [{idx+1}/{n_combos}] BEST qs={qs:.1f} PF={m['pf']:.2f} "
                          f"T={m['n_trades']} DD={m['max_dd']:.1f}% Daily={m['daily_jpy']:.0f} | "
                          f"SL={params['SL_ATR_Mult']} Tr={params['Trail_ATR']} "
                          f"BE={params['BE_ATR']} Z={params['EMA_Zone_ATR']} "
                          f"B={params['BodyRatio']} MP={params['MaxPositions']} AF={params['ATR_Filter']}")

            if (idx + 1) % 1000 == 0:
                elapsed = time.time() - t_rv
                print(f"    [{idx+1}/{n_combos}] {elapsed:.0f}s, "
                      f"{sum(1 for x in all_quick if x[3]==rv_name)} valid")

        print(f"    {rv_name} done in {time.time()-t_rv:.0f}s")

    all_quick.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Phase 1: {len(all_quick)} valid from {total_tested} tested")
    print(f"  Best QS: {best_qs:.1f}")

    # D1_Tolerance expansion on top 30
    print(f"\n  D1_Tolerance expansion...")
    extra = []
    for d1t in [0.002, 0.005, 0.007]:
        for p, m, qs, rv_n, rp in all_quick[:30]:
            params = {**p, "D1_Tolerance": d1t}
            cfg = make_cfg(**params)
            mask = regime_masks[rv_n][0]
            m2 = fast_bt(ind_full, mask, cfg, total_days)
            qs2 = quick_score(m2, 400)
            if qs2 > -999:
                extra.append((params, m2, qs2, rv_n, rp))
    all_quick.extend(extra)
    all_quick.sort(key=lambda x: x[2], reverse=True)
    print(f"  After expansion: {len(all_quick)} valid")

    # Show top 25
    if all_quick:
        print(f"\n  Top 25:")
        print(f"  {'Rk':>2} {'QS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'Daily':>7} | {'Reg':>7} SL   Tr   BE  Zone Body MP AF   D1T")
        print("  " + "-" * 100)
        for i, (p, m, qs, rv_n, rp) in enumerate(all_quick[:25]):
            print(f"  {i+1:2d} {qs:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} {m['daily_jpy']:7.0f} | "
                  f"{rv_n:>7} {p['SL_ATR_Mult']:.1f} {p['Trail_ATR']:.1f} "
                  f"{p['BE_ATR']:.1f} {p['EMA_Zone_ATR']:.1f} {p['BodyRatio']:.2f} {p['MaxPositions']}  "
                  f"{p['ATR_Filter']:.1f} {p.get('D1_Tolerance',0.003):.3f}")

    print(f"\n  Phase 1 total time: {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 2: WFA Validation (top 40 unique)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 2: WFA Validation")
    print("=" * 80)

    candidates = []
    seen = set()
    for p, m, qs, rv_n, rp in all_quick:
        key = (tuple(sorted(p.items())), rv_n)
        if key not in seen:
            seen.add(key)
            candidates.append((p, rp, rv_n))
        if len(candidates) >= 40:
            break

    print(f"  Validating {len(candidates)} unique candidates...")
    validated = []
    best_wfa = 0
    t_ph2 = time.time()

    for ci, (params, rp, rv_n) in enumerate(candidates):
        v = full_validate(h4_df, total_days, ind_full, params, rp, f"Ph2_{rv_n}")
        if v is None:
            continue
        validated.append(v)
        if v["n_pass"] > best_wfa:
            best_wfa = v["n_pass"]
        marker = ""
        if v["n_pass"] >= 7: marker = " *** 7/8! ***"
        elif v["n_pass"] >= 6: marker = " <<<"
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        if ci % 5 == 0 or v["n_pass"] >= 6:
            print(f"    [{ci+1}/{len(candidates)}] WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                  f"MinPF={v['min_pf']:.2f} PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                  f"DD={v['m']['max_dd']:.1f}% OOS={oos_pf:.2f} LY={v['n_losing']} "
                  f"FS={v['score']:.1f}{marker} ({rv_n})")

    validated.sort(key=lambda x: x["score"], reverse=True)
    if validated:
        print(f"\n  Top 15 after WFA:")
        print(f"  {'Rk':>2} {'FS':>6} {'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'PF':>5} "
              f"{'T':>5} {'DD':>5} {'LY':>2} {'OOS':>5} {'Daily':>7} | Params")
        print("  " + "-" * 115)
        for i, v in enumerate(validated[:15]):
            m = v["m"]; p = v["params"]
            oos_pf = v["oos"]["pf"] if v["oos"] else 0
            print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {v['avg_pf']:5.2f} "
                  f"{v['min_pf']:5.2f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
                  f"{v['n_losing']:2d} {oos_pf:5.2f} {m['daily_jpy']:7.0f} | "
                  f"SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
                  f"Z={p['EMA_Zone_ATR']:.1f} B={p['BodyRatio']:.2f} MP={p['MaxPositions']} "
                  f"AF={p['ATR_Filter']:.1f} D1T={p.get('D1_Tolerance',0.003):.3f}")
        print(f"\n  Best WFA: {best_wfa}/8")

    print(f"  Phase 2 done in {time.time()-t_ph2:.0f}s (total: {time.time()-t0:.0f}s)")

    # ================================================================
    # PHASE 3: Feature combos on WFA winners
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 3: Feature Combinations on WFA Winners")
    print("=" * 80)

    top_wfa = [v for v in validated if v["n_pass"] >= max(4, best_wfa - 2)][:6]
    if len(top_wfa) < 3:
        top_wfa = validated[:6]

    feature_sets = [
        ("TD25",   {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 25}),
        ("TD30",   {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD35",   {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("Slope3", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3}),
        ("Slope5", {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("Str2",   {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("Str3",   {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3}),
        ("Vol",    {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Sess",   {"USE_SESSION_FILTER": True, "TRADE_START_HOUR": 2, "TRADE_END_HOUR": 21}),
        ("TD30+Sl5",     {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30, "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("TD30+Str2",    {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30, "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("Sl5+Str2",     {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5, "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("TD30+Sl5+Str2", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30, "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5, "USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
    ]

    feat_results = []
    feat_best_wfa = best_wfa
    for ri, base in enumerate(top_wfa):
        bp = base["params"]
        brp = base["rp"]
        print(f"\n  Base R{ri+1}: WFA={base['n_pass']}/8 PF={base['m']['pf']:.2f} "
              f"T={base['m']['n_trades']} FS={base['score']:.1f}")
        for fname, fparams in feature_sets:
            params = {**bp, **fparams}
            v = full_validate(h4_df, total_days, ind_full, params, brp, f"R{ri+1}+{fname}")
            if v:
                feat_results.append(v)
                marker = ""
                if v["n_pass"] >= 7: marker = " *** 7/8! ***"
                elif v["n_pass"] > base["n_pass"]: marker = " <IMPROVED>"
                if v["n_pass"] > feat_best_wfa:
                    feat_best_wfa = v["n_pass"]
                oos_pf = v["oos"]["pf"] if v["oos"] else 0
                if v["n_pass"] >= 5 or v["score"] > base["score"]:
                    print(f"    +{fname:>16} WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                          f"MinPF={v['min_pf']:.2f} PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                          f"D={v['m']['daily_jpy']:.0f} FS={v['score']:.1f}{marker}")

    print(f"\n  Phase 3 done in {time.time()-t0:.0f}s, best WFA: {feat_best_wfa}/8")

    # ================================================================
    # PHASE 4: Fine-tune top 5 + regime fine-tune
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 4: Fine-Tune Top Candidates")
    print("=" * 80)

    all_validated = validated + feat_results
    all_validated.sort(key=lambda x: x["score"], reverse=True)

    fine_results = []
    for fi, base in enumerate(all_validated[:5]):
        bp = base["params"]
        brp = base["rp"]
        print(f"\n  F{fi+1}: WFA={base['n_pass']}/8 PF={base['m']['pf']:.2f} "
              f"T={base['m']['n_trades']} FS={base['score']:.1f}")

        sl_vals = rng(bp["SL_ATR_Mult"], 0.25, 1)
        tr_vals = rng(bp["Trail_ATR"], 0.25, 1)
        be_vals = rng(bp["BE_ATR"], 0.2, 1)
        zone_vals = rng(bp["EMA_Zone_ATR"], 0.05, 1)
        body_vals = rng(bp["BodyRatio"], 0.02, 1)

        fine_grid = list(product(sl_vals, tr_vals, be_vals, zone_vals, body_vals))
        print(f"    Fine grid: {len(fine_grid)} combos")

        fine_best = base["score"]
        for combo in fine_grid:
            params = {**bp}
            params["SL_ATR_Mult"] = combo[0]
            params["Trail_ATR"] = combo[1]
            params["BE_ATR"] = combo[2]
            params["EMA_Zone_ATR"] = combo[3]
            params["BodyRatio"] = combo[4]

            cfg = make_cfg(**params)
            mask = compute_regime_mask(ind_full, brp["d1_slope_bars"], brp["d1_min_slope"], brp["w1_min_sep"])
            m = fast_bt(ind_full, mask, cfg, total_days)
            qs = quick_score(m, 400)
            if qs > fine_best - 3:
                # Only full validate if promising
                v = full_validate(h4_df, total_days, ind_full, params, brp, f"Fine_F{fi+1}")
                if v and v["score"] > fine_best - 5:
                    fine_results.append(v)
                    if v["score"] > fine_best:
                        fine_best = v["score"]
                        oos_pf = v["oos"]["pf"] if v["oos"] else 0
                        print(f"    IMPROVED: WFA={v['n_pass']}/8 PF={v['m']['pf']:.2f} "
                              f"T={v['m']['n_trades']} D={v['m']['daily_jpy']:.0f} "
                              f"OOS={oos_pf:.2f} FS={v['score']:.1f}")

    # Regime fine-tune on top 3
    print(f"\n  Regime fine-tune...")
    top3 = (all_validated[:3] + fine_results[:3])
    regime_fine_results = []
    for base in top3:
        bp = base["params"]
        for d1sb in [3, 5, 7]:
            for d1ms in [0.0003, 0.0007, 0.001, 0.0015]:
                for w1ms in [0.0, 0.002, 0.004, 0.006]:
                    rp_test = {"d1_slope_bars": d1sb, "d1_min_slope": d1ms, "w1_min_sep": w1ms}
                    mask = compute_regime_mask(ind_full, d1sb, d1ms, w1ms)
                    cfg = make_cfg(**bp)
                    m = fast_bt(ind_full, mask, cfg, total_days)
                    qs = quick_score(m, 400)
                    if qs > best_qs - 2 and m is not None and m["pf"] >= 1.4 and m["n_trades"] >= 450:
                        regime_fine_results.append((bp, rp_test, m, qs))

    regime_fine_results.sort(key=lambda x: x[3], reverse=True)
    if regime_fine_results:
        print(f"  {len(regime_fine_results)} valid regime combos, WFA validating top 10...")
        for ri, (bp, rp_test, m, qs) in enumerate(regime_fine_results[:10]):
            v = full_validate(h4_df, total_days, ind_full, bp, rp_test, f"RegFine_{ri}")
            if v:
                fine_results.append(v)
                oos_pf = v["oos"]["pf"] if v["oos"] else 0
                print(f"    [{ri+1}] WFA={v['n_pass']}/8 PF={v['m']['pf']:.2f} "
                      f"T={v['m']['n_trades']} D={v['m']['daily_jpy']:.0f} "
                      f"OOS={oos_pf:.2f} FS={v['score']:.1f} | "
                      f"d1sb={rp_test['d1_slope_bars']} d1ms={rp_test['d1_min_slope']} w1ms={rp_test['w1_min_sep']}")

    print(f"  Phase 4 done in {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 5: Risk Scaling & Final Results
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 5: Risk Scaling & Final Results")
    print("=" * 80)

    all_final = all_validated + fine_results
    all_final.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate
    seen_final = set()
    unique_final = []
    for v in all_final:
        key = (tuple(sorted(v["params"].items())), tuple(sorted(v["rp"].items())))
        if key not in seen_final:
            seen_final.add(key)
            unique_final.append(v)

    print(f"\n  TOP 10 FINAL:")
    print(f"  {'Rk':>2} {'FS':>6} {'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'PF':>5} "
          f"{'T':>5} {'DD':>5} {'LY':>2} {'OOS':>5} {'Daily':>7} | Params")
    print("  " + "-" * 120)
    for i, v in enumerate(unique_final[:10]):
        m = v["m"]; p = v["params"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {v['avg_pf']:5.2f} "
              f"{v['min_pf']:5.2f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
              f"{v['n_losing']:2d} {oos_pf:5.2f} {m['daily_jpy']:7.0f} | "
              f"SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
              f"Z={p['EMA_Zone_ATR']:.2f} B={p['BodyRatio']:.2f} MP={p['MaxPositions']} "
              f"AF={p['ATR_Filter']:.1f} D1T={p.get('D1_Tolerance',0.003):.3f}")
        rp = v["rp"]
        print(f"      Regime: d1sb={rp['d1_slope_bars']} d1ms={rp['d1_min_slope']} w1ms={rp['w1_min_sep']}")
        feats = []
        if p.get("USE_TIME_DECAY"): feats.append(f"TD{p['MAX_HOLD_BARS']}")
        if p.get("USE_EMA_SLOPE"): feats.append(f"Slope{p['EMA_SLOPE_BARS']}")
        if p.get("USE_STRUCTURE"): feats.append(f"Struct{p['STRUCTURE_BARS']}")
        if p.get("USE_VOL_REGIME"): feats.append("Vol")
        if p.get("USE_SESSION_FILTER"): feats.append("Session")
        if feats: print(f"      Features: {', '.join(feats)}")

    # Risk scaling for winner
    if unique_final:
        winner = unique_final[0]
        wp = winner["params"]
        wrp = winner["rp"]

        print(f"\n  WINNER: FS={winner['score']:.1f} WFA={winner['n_pass']}/8")
        print(f"\n  Risk Scaling (300K JPY):")
        print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>6} {'WR%':>5} "
              f"{'Daily':>8} {'Final':>12} {'Target':>6}")
        print("  " + "-" * 75)

        best_risk_daily = 0
        best_risk_params = None
        for risk, maxlot in [(0.10, 0.05), (0.15, 0.10), (0.20, 0.15),
                              (0.25, 0.20), (0.30, 0.25), (0.40, 0.30),
                              (0.50, 0.40), (0.75, 0.50), (1.0, 0.50),
                              (1.5, 0.75), (2.0, 1.00)]:
            params = {**wp, "RiskPct": risk, "MaxLot": maxlot}
            cfg = make_cfg(**params)
            mask = compute_regime_mask(ind_full, wrp["d1_slope_bars"], wrp["d1_min_slope"], wrp["w1_min_sep"])
            m = fast_bt(ind_full, mask, cfg, total_days)
            if m:
                target = "YES" if m["daily_jpy"] >= 5000 else ""
                print(f"  {risk:6.2f} {maxlot:6.2f} | {m['pf']:5.2f} {m['n_trades']:5d} "
                      f"{m['max_dd']:6.1f} {m['win_rate']:5.1f} {m['daily_jpy']:8.0f} "
                      f"{m['final_balance']:12.0f} {target:>6}")
                if m["daily_jpy"] >= 5000 and m["pf"] >= 1.3 and m["max_dd"] < 40:
                    if best_risk_params is None or m["daily_jpy"] > best_risk_daily:
                        best_risk_daily = m["daily_jpy"]
                        best_risk_params = params
                # First risk level >= 5000 JPY with reasonable DD
                if best_risk_params is None and m["daily_jpy"] >= 5000 and m["max_dd"] < 50:
                    best_risk_params = params
                    best_risk_daily = m["daily_jpy"]

        # If no risk level hits 5000, pick highest daily with PF >= 1.3
        if best_risk_params is None:
            for risk, maxlot in [(2.0, 1.00), (1.5, 0.75), (1.0, 0.50)]:
                params = {**wp, "RiskPct": risk, "MaxLot": maxlot}
                cfg = make_cfg(**params)
                m = fast_bt(ind_full, mask, cfg, total_days)
                if m and m["pf"] >= 1.3:
                    best_risk_params = params
                    best_risk_daily = m["daily_jpy"]
                    break

        # Year-by-year for final config
        final_p = best_risk_params if best_risk_params else wp
        final_rp = wrp
        cfg = make_cfg(**final_p)
        mask = compute_regime_mask(ind_full, final_rp["d1_slope_bars"], final_rp["d1_min_slope"], final_rp["w1_min_sep"])
        trades = fast_bt_trades(ind_full, mask, cfg)

        yby = year_by_year(trades, h4_df)
        print(f"\n  Year-by-Year (Risk={final_p.get('RiskPct', 0.2)}%):")
        print(f"  {'Year':>6} {'N':>5} {'PF':>5} {'WR%':>5} {'PnL':>10} {'Daily':>8}")
        print("  " + "-" * 50)
        for yr in sorted(yby.keys()):
            y = yby[yr]
            print(f"  {yr:6d} {y['n']:5d} {y['pf']:5.2f} {y['wr']:5.1f} {y['pnl']:10.0f} {y['daily']:8.0f}")

        # Final WFA
        print(f"\n  WFA at final risk:")
        wfa = run_fast_wfa(h4_df, cfg, final_rp)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        for wi, r in enumerate(wfa):
            status = "PASS" if r["pf"] > 1.0 else "FAIL"
            print(f"    W{wi+1}: PF={r['pf']:.2f} T={r['n_trades']} DD={r['max_dd']:.1f}% [{status}]")
        print(f"    Result: {n_pass}/{len(wfa)} PASS")

        # Final metrics
        m_final = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
        print(f"\n  === FINAL v27 METRICS ===")
        print(f"  PF: {m_final['pf']:.2f}")
        print(f"  Trades: {m_final['n_trades']}")
        print(f"  Win Rate: {m_final['win_rate']:.1f}%")
        print(f"  Max DD: {m_final['max_dd']:.1f}%")
        print(f"  Daily JPY: {m_final['daily_jpy']:.0f}")
        print(f"  Final Balance: {m_final['final_balance']:.0f}")
        print(f"  WFA: {n_pass}/{len(wfa)}")

        # Output parameters for MQ5
        print(f"\n  === v27 MQ5 PARAMETERS ===")
        print(f"  W1_FastEMA = {cfg.W1_FastEMA}")
        print(f"  W1_SlowEMA = {cfg.W1_SlowEMA}")
        print(f"  D1_EMA = {cfg.D1_EMA}")
        print(f"  H4_EMA = {cfg.H4_EMA}")
        print(f"  ATR_Period = {cfg.ATR_Period}")
        print(f"  ATR_SMA = {cfg.ATR_SMA}")
        print(f"  SL_ATR_Mult = {final_p['SL_ATR_Mult']}")
        print(f"  Trail_ATR = {final_p['Trail_ATR']}")
        print(f"  BE_ATR = {final_p['BE_ATR']}")
        print(f"  RiskPct = {final_p.get('RiskPct', 0.2)}")
        print(f"  BodyRatio = {final_p['BodyRatio']}")
        print(f"  EMA_Zone_ATR = {final_p['EMA_Zone_ATR']}")
        print(f"  ATR_Filter = {final_p['ATR_Filter']}")
        print(f"  D1_Tolerance = {final_p.get('D1_Tolerance', 0.003)}")
        print(f"  MaxPositions = {final_p['MaxPositions']}")
        print(f"  MinLot = 0.01")
        print(f"  MaxLot = {final_p.get('MaxLot', 0.50)}")
        print(f"  D1_Slope_Bars = {final_rp['d1_slope_bars']}")
        print(f"  D1_Min_Slope = {final_rp['d1_min_slope']}")
        print(f"  W1_Min_Sep = {final_rp['w1_min_sep']}")
        if final_p.get("USE_TIME_DECAY"):
            print(f"  USE_TIME_DECAY = true")
            print(f"  MAX_HOLD_BARS = {final_p['MAX_HOLD_BARS']}")
        if final_p.get("USE_EMA_SLOPE"):
            print(f"  USE_EMA_SLOPE = true")
            print(f"  EMA_SLOPE_BARS = {final_p['EMA_SLOPE_BARS']}")
        if final_p.get("USE_STRUCTURE"):
            print(f"  USE_STRUCTURE = true")
            print(f"  STRUCTURE_BARS = {final_p['STRUCTURE_BARS']}")

        # Also test with "none" regime to see baseline improvement
        print(f"\n  --- Comparison: same params, no regime ---")
        mask_none = compute_regime_mask(ind_full, 5, 0.0, 0.0)
        m_none = fast_bt(ind_full, mask_none, cfg, total_days)
        if m_none:
            print(f"  No regime: PF={m_none['pf']:.2f} T={m_none['n_trades']} DD={m_none['max_dd']:.1f}% "
                  f"Daily={m_none['daily_jpy']:.0f}")
        print(f"  With regime: PF={m_final['pf']:.2f} T={m_final['n_trades']} DD={m_final['max_dd']:.1f}% "
              f"Daily={m_final['daily_jpy']:.0f}")

    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("=" * 80)
    print("DONE")

    log_file.close()


if __name__ == "__main__":
    main()
