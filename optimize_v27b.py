"""
GoldAlpha v27b - Daily JPY Maximizer
Goal: 5000+ JPY/day from 300K JPY base
Strategy: v12 dip-buy + loose regime, MaxPos 3-4, higher risk

Focus: compound-weighted daily JPY optimization
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
    np_ema, np_sma, np_atr, resample_to_daily, resample_to_weekly,
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


log_file = open("/tmp/v27b_output.log", "w")
sys.stdout = Tee(sys.__stdout__, log_file)


def make_cfg(**overrides):
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    cfg.SL_ATR_Mult = 2.5; cfg.Trail_ATR = 3.0; cfg.BE_ATR = 0.5
    cfg.RiskPct = 2.0; cfg.BodyRatio = 0.28
    cfg.EMA_Zone_ATR = 0.50; cfg.ATR_Filter = 0.30; cfg.D1_Tolerance = 0.005
    cfg.MaxPositions = 3; cfg.MinLot = 0.01; cfg.MaxLot = 1.00
    cfg.INITIAL_BALANCE = 300_000
    cfg.USE_EMA_SLOPE = False; cfg.EMA_SLOPE_BARS = 5
    cfg.USE_STRUCTURE = False; cfg.STRUCTURE_BARS = 3
    cfg.USE_TIME_DECAY = False; cfg.MAX_HOLD_BARS = 30
    cfg.USE_VOL_REGIME = False; cfg.USE_SESSION_FILTER = False
    cfg.USE_RSI_CONFIRM = False; cfg.USE_PARTIAL_CLOSE = False
    cfg.USE_W1_SEPARATION = False; cfg.W1_SEP_MIN = 0.005
    cfg.USE_ADX_FILTER = False; cfg.ADX_Period = 14; cfg.ADX_MIN = 20
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def compute_regime_mask(ind, d1_slope_bars, d1_min_slope, w1_min_sep):
    h4_times = ind[4]
    w1_fast = ind[5]; w1_slow = ind[6]; w1_times = ind[7]
    d1_ema_arr = ind[9]; d1_times = ind[10]
    n = len(h4_times)
    w1_idx = np.searchsorted(w1_times, h4_times, side="right") - 1
    d1_idx = np.searchsorted(d1_times, h4_times, side="right") - 1
    blocked = np.zeros(n, dtype=bool)
    if w1_min_sep > 0:
        for i in range(n):
            wi = w1_idx[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0 and abs(w1_fast[wi] - w1_slow[wi]) / mid < w1_min_sep:
                    blocked[i] = True
    if d1_min_slope > 0:
        for i in range(n):
            if blocked[i]: continue
            di = d1_idx[i]
            if d1_slope_bars <= di < len(d1_ema_arr):
                prev = d1_ema_arr[di - d1_slope_bars]
                cur = d1_ema_arr[di]
                if prev > 0 and abs(cur - prev) / prev < d1_min_slope:
                    blocked[i] = True
    return blocked


def apply_mask(ind, mask):
    mod = list(ind)
    avg_atr = ind[13].copy()
    avg_atr[mask] = 999999
    mod[13] = avg_atr
    return tuple(mod)


def fast_bt(ind, mask, cfg, total_days):
    mod = apply_mask(ind, mask)
    trades, eq, final = backtest_goldalpha(*mod, cfg=cfg)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days), trades


def run_wfa_regime(h4_df, cfg, rp, n_windows=8):
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
        mask = compute_regime_mask(sub_ind, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"])
        mod = apply_mask(sub_ind, mask)
        trades, _, _ = backtest_goldalpha(*mod, cfg=cfg)
        oos_time = h4_df.index[oos_start]
        oos_trades = [t for t in trades if t["open_time"] >= oos_time]
        oos_end = h4_df.index[min(window_end - 1, total_bars - 1)]
        oos_days = max(1, (oos_end - oos_time).days)
        m = calc_metrics(oos_trades, cfg.INITIAL_BALANCE, oos_days)
        if m: results.append(m)
    return results


def daily_score(m, min_t=500):
    """Score focused on daily JPY + PF minimum."""
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.3:
        return -999
    s = min(m["daily_jpy"], 15000) * 0.01  # Primary: daily JPY
    s += min(m["pf"], 2.5) * 5             # Secondary: PF quality
    s -= max(0, m["max_dd"] - 30) * 0.3    # Penalty: excessive DD
    s += min(m["n_trades"], 3000) * 0.001   # Bonus: more trades
    return s


def year_by_year(trades, h4_df):
    if not trades: return {}
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


def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days

    print("=" * 80)
    print("GoldAlpha v27b - Daily JPY Maximizer")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print(f"Target: 5000+ JPY/day, PF >= 1.3, 300K JPY, WFA >= 4/8")
    print("=" * 80)

    cfg_base = make_cfg()
    ind = precompute_indicators(h4_df, cfg_base)
    print(f"Indicators ready in {time.time()-t0:.1f}s")

    # Regime masks
    regimes = [
        ("none",  {"d1_slope_bars": 5, "d1_min_slope": 0.0, "w1_min_sep": 0.0}),
        ("light", {"d1_slope_bars": 5, "d1_min_slope": 0.0005, "w1_min_sep": 0.0}),
        ("med",   {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_sep": 0.003}),
    ]
    masks = {}
    for name, rp in regimes:
        masks[name] = (compute_regime_mask(ind, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"]), rp)
        print(f"  {name}: {masks[name][0].sum()/len(masks[name][0])*100:.1f}% blocked")

    # ================================================================
    # PHASE 1: Risk-focused grid (higher risk, more positions)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 1: High-Frequency + High-Risk Grid")
    print("=" * 80)

    # 4*4*3*3*3*3*3*3 = 11664 per regime, x3 = 34992
    # Too many - reduce: 3*3*2*3*2*3*3*3 = 2916 x3 = 8748
    grid = {
        "SL_ATR_Mult":  [2.0, 2.5, 3.0],          # 3
        "Trail_ATR":    [2.5, 3.0, 3.5],            # 3
        "BE_ATR":       [0.5, 1.0],                  # 2
        "EMA_Zone_ATR": [0.3, 0.4, 0.5],            # 3
        "BodyRatio":    [0.24, 0.28],                # 2
        "MaxPositions": [2, 3, 4],                   # 3
        "ATR_Filter":   [0.2, 0.3, 0.4],            # 3
        "RiskPct":      [1.0, 2.0, 3.0],            # 3
    }

    keys = list(grid.keys())
    combos = list(product(*grid.values()))
    n_combos = len(combos)
    print(f"Grid: {n_combos} combos x {len(regimes)} regimes = {n_combos * len(regimes)}")

    all_results = []
    best_ds = -999

    for rv_name, (mask, rp) in masks.items():
        print(f"\n  Regime: {rv_name}")
        rv_best = -999
        t_rv = time.time()

        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            params["D1_Tolerance"] = 0.005
            params["MaxLot"] = min(1.0, params["RiskPct"] * 0.5)
            cfg = make_cfg(**params)
            mod = apply_mask(ind, mask)
            trades, eq, final = backtest_goldalpha(*mod, cfg=cfg)
            m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
            ds = daily_score(m, 400)

            if ds > -999:
                all_results.append((params, m, ds, rv_name, rp))
                if ds > rv_best:
                    rv_best = ds
                    if ds > best_ds:
                        best_ds = ds
                    print(f"    [{idx+1}/{n_combos}] BEST ds={ds:.1f} PF={m['pf']:.2f} "
                          f"T={m['n_trades']} DD={m['max_dd']:.1f}% D¥={m['daily_jpy']:.0f} | "
                          f"SL={params['SL_ATR_Mult']} Tr={params['Trail_ATR']} "
                          f"BE={params['BE_ATR']} Z={params['EMA_Zone_ATR']} "
                          f"B={params['BodyRatio']} MP={params['MaxPositions']} "
                          f"AF={params['ATR_Filter']} R={params['RiskPct']}")

            if (idx + 1) % 1000 == 0:
                print(f"    [{idx+1}/{n_combos}] {time.time()-t_rv:.0f}s, "
                      f"{sum(1 for x in all_results if x[3]==rv_name)} valid")

        print(f"    {rv_name} done in {time.time()-t_rv:.0f}s")

    all_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Total: {len(all_results)} valid, best DS={best_ds:.1f}")

    # D1_Tolerance expansion
    extra = []
    for d1t in [0.003, 0.007, 0.01]:
        for p, m, ds, rv_n, rp in all_results[:20]:
            params = {**p, "D1_Tolerance": d1t}
            cfg = make_cfg(**params)
            mask = masks[rv_n][0]
            mod = apply_mask(ind, mask)
            trades, eq, final = backtest_goldalpha(*mod, cfg=cfg)
            m2 = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
            ds2 = daily_score(m2, 400)
            if ds2 > -999:
                extra.append((params, m2, ds2, rv_n, rp))
    all_results.extend(extra)
    all_results.sort(key=lambda x: x[2], reverse=True)

    # Top 25
    print(f"\n  Top 25:")
    print(f"  {'Rk':>2} {'DS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'D¥':>7} | {'Reg':>5} SL  Tr  BE  Z    B   MP AF  R%  D1T")
    print("  " + "-" * 100)
    for i, (p, m, ds, rv_n, rp) in enumerate(all_results[:25]):
        print(f"  {i+1:2d} {ds:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} {m['daily_jpy']:7.0f} | "
              f"{rv_n:>5} {p['SL_ATR_Mult']:.1f} {p['Trail_ATR']:.1f} {p['BE_ATR']:.1f} "
              f"{p['EMA_Zone_ATR']:.1f} {p['BodyRatio']:.2f} {p['MaxPositions']}  "
              f"{p['ATR_Filter']:.1f} {p['RiskPct']:.0f} {p.get('D1_Tolerance',0.005):.3f}")

    print(f"\n  Phase 1: {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 2: WFA on top 30
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 2: WFA Validation")
    print("=" * 80)

    candidates = []
    seen = set()
    for p, m, ds, rv_n, rp in all_results:
        key = (tuple(sorted(p.items())), rv_n)
        if key not in seen:
            seen.add(key)
            candidates.append((p, rp, rv_n, m, ds))
        if len(candidates) >= 30:
            break

    validated = []
    for ci, (params, rp, rv_n, m_full, ds) in enumerate(candidates):
        cfg = make_cfg(**params)
        wfa = run_wfa_regime(h4_df, cfg, rp)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0

        mask = masks.get(rv_n, masks["none"])[0]
        trades_full = fast_bt(ind, mask, cfg, total_days)[1]
        n_losing = sum(1 for v in year_by_year(trades_full, h4_df).values() if v["pnl"] < 0)

        # WFA-weighted score
        wfa_bonus = n_pass * 5 + min(avg_pf, 2.5) * 3
        final_score = ds + wfa_bonus - n_losing * 2

        validated.append({
            "params": params, "m": m_full, "wfa": wfa, "n_pass": n_pass,
            "avg_pf": avg_pf, "min_pf": min_pf, "n_losing": n_losing,
            "score": final_score, "rv_n": rv_n, "rp": rp, "ds": ds
        })

        marker = ""
        if n_pass >= 7: marker = " *** 7/8! ***"
        elif n_pass >= 6: marker = " <<<"
        if ci % 5 == 0 or n_pass >= 6:
            print(f"  [{ci+1}/{len(candidates)}] WFA={n_pass}/8 AvgPF={avg_pf:.2f} "
                  f"PF={m_full['pf']:.2f} T={m_full['n_trades']} D¥={m_full['daily_jpy']:.0f} "
                  f"LY={n_losing} FS={final_score:.1f}{marker} ({rv_n})")

    validated.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n  Top 15:")
    print(f"  {'Rk':>2} {'FS':>6} {'WFA':>4} {'PF':>5} {'T':>5} {'DD':>5} {'D¥':>7} {'LY':>2} | Params")
    print("  " + "-" * 100)
    for i, v in enumerate(validated[:15]):
        m = v["m"]; p = v["params"]
        print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['daily_jpy']:7.0f} {v['n_losing']:2d} | "
              f"SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
              f"Z={p['EMA_Zone_ATR']:.1f} B={p['BodyRatio']:.2f} MP={p['MaxPositions']} "
              f"AF={p['ATR_Filter']:.1f} R={p['RiskPct']:.0f}% ({v['rv_n']})")

    print(f"  Phase 2: {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 3: Feature combos on top WFA winners
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 3: Feature Combos")
    print("=" * 80)

    top = [v for v in validated if v["n_pass"] >= 4][:5]
    if len(top) < 3:
        top = validated[:5]

    features = [
        ("TD25", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 25}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("Sl3",  {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3}),
        ("Sl5",  {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
        ("Str2", {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2}),
        ("TD30+Sl5", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30, "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5}),
    ]

    feat_results = []
    for ri, base in enumerate(top):
        bp = base["params"]
        brp = base["rp"]
        rv_n = base["rv_n"]
        print(f"\n  Base R{ri+1}: WFA={base['n_pass']}/8 PF={base['m']['pf']:.2f} "
              f"D¥={base['m']['daily_jpy']:.0f}")
        for fname, fparams in features:
            params = {**bp, **fparams}
            cfg = make_cfg(**params)
            mask = masks.get(rv_n, masks["none"])[0]
            mod = apply_mask(ind, mask)
            trades, _, _ = backtest_goldalpha(*mod, cfg=cfg)
            m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
            if m and m["n_trades"] >= 300 and m["pf"] >= 1.2:
                wfa = run_wfa_regime(h4_df, cfg, brp)
                n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
                ds = daily_score(m, 300)
                wfa_bonus = n_pass * 5 + min(np.mean([r["pf"] for r in wfa]) if wfa else 0, 2.5) * 3
                fs = ds + wfa_bonus
                feat_results.append({
                    "params": params, "m": m, "wfa": wfa, "n_pass": n_pass,
                    "score": fs, "rv_n": rv_n, "rp": brp
                })
                if n_pass >= 4 or fs > base["score"]:
                    print(f"    +{fname:>10} WFA={n_pass}/8 PF={m['pf']:.2f} "
                          f"T={m['n_trades']} D¥={m['daily_jpy']:.0f} FS={fs:.1f}")

    print(f"\n  Phase 3: {time.time()-t0:.0f}s")

    # ================================================================
    # FINAL: Best result + risk scaling
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)

    all_final = validated + feat_results
    all_final.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate
    seen_f = set()
    unique = []
    for v in all_final:
        key = tuple(sorted(v["params"].items()))
        if key not in seen_f:
            seen_f.add(key)
            unique.append(v)

    # Top 5
    print(f"\n  Top 5:")
    for i, v in enumerate(unique[:5]):
        m = v["m"]; p = v["params"]
        print(f"\n  #{i+1} FS={v['score']:.1f} WFA={v['n_pass']}/8")
        print(f"     PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% "
              f"WR={m['win_rate']:.1f}% D¥={m['daily_jpy']:.0f}")
        print(f"     SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
              f"Z={p['EMA_Zone_ATR']:.1f} B={p['BodyRatio']:.2f} MP={p['MaxPositions']} "
              f"AF={p['ATR_Filter']:.1f} R={p['RiskPct']:.0f}% ({v['rv_n']})")
        feats = []
        if p.get("USE_TIME_DECAY"): feats.append(f"TD{p['MAX_HOLD_BARS']}")
        if p.get("USE_EMA_SLOPE"): feats.append(f"Sl{p['EMA_SLOPE_BARS']}")
        if p.get("USE_STRUCTURE"): feats.append(f"Str{p['STRUCTURE_BARS']}")
        if feats: print(f"     Features: {', '.join(feats)}")

    # Winner analysis
    if unique:
        winner = unique[0]
        wp = winner["params"]
        wrp = winner["rp"]
        rv_n = winner["rv_n"]

        print(f"\n  === WINNER ===")
        cfg = make_cfg(**wp)
        mask = masks.get(rv_n, masks["none"])[0]
        trades_win = fast_bt(ind, mask, cfg, total_days)[1]

        yby = year_by_year(trades_win, h4_df)
        print(f"\n  Year-by-Year:")
        print(f"  {'Year':>6} {'N':>5} {'PF':>5} {'WR%':>5} {'PnL':>12} {'Daily':>8}")
        print("  " + "-" * 55)
        for yr in sorted(yby.keys()):
            y = yby[yr]
            print(f"  {yr:6d} {y['n']:5d} {y['pf']:5.2f} {y['wr']:5.1f} "
                  f"{y['pnl']:12.0f} {y['daily']:8.0f}")

        print(f"\n  WFA detail:")
        wfa = winner["wfa"]
        for wi, r in enumerate(wfa):
            status = "PASS" if r["pf"] > 1.0 else "FAIL"
            print(f"    W{wi+1}: PF={r['pf']:.2f} T={r['n_trades']} [{status}]")
        print(f"    Result: {winner['n_pass']}/{len(wfa)}")

        # Risk scaling
        print(f"\n  Risk Scaling:")
        print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>6} {'D¥':>8} {'Final':>12} {'5K':>4}")
        print("  " + "-" * 70)
        for risk, maxlot in [(0.5, 0.25), (1.0, 0.50), (1.5, 0.75), (2.0, 1.0),
                              (3.0, 1.5), (4.0, 2.0), (5.0, 2.5)]:
            params = {**wp, "RiskPct": risk, "MaxLot": maxlot}
            cfg = make_cfg(**params)
            mod = apply_mask(ind, mask)
            trades, _, _ = backtest_goldalpha(*mod, cfg=cfg)
            m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
            if m:
                hit = "YES" if m["daily_jpy"] >= 5000 else ""
                print(f"  {risk:6.1f} {maxlot:6.2f} | {m['pf']:5.2f} {m['n_trades']:5d} "
                      f"{m['max_dd']:6.1f} {m['daily_jpy']:8.0f} {m['final_balance']:12.0f} {hit:>4}")

        # MQ5 parameters
        print(f"\n  === v27b MQ5 PARAMETERS ===")
        for k in ["SL_ATR_Mult", "Trail_ATR", "BE_ATR", "RiskPct", "BodyRatio",
                   "EMA_Zone_ATR", "ATR_Filter", "D1_Tolerance", "MaxPositions", "MaxLot"]:
            print(f"  {k} = {wp.get(k, getattr(cfg_base, k, 'N/A'))}")
        print(f"  D1_Slope_Bars = {wrp['d1_slope_bars']}")
        print(f"  D1_Min_Slope = {wrp['d1_min_slope']}")
        print(f"  W1_Min_Sep = {wrp['w1_min_sep']}")
        if wp.get("USE_TIME_DECAY"):
            print(f"  Max_Hold_Bars = {wp['MAX_HOLD_BARS']}")
        if wp.get("USE_EMA_SLOPE"):
            print(f"  EMA_Slope_Bars = {wp['EMA_SLOPE_BARS']}")

    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("DONE")
    log_file.close()


if __name__ == "__main__":
    main()
