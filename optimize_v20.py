"""
GoldAlpha v20 Optimizer - WFA Improvement via Regime-Adaptive Approach
Goal: Fix v19's WFA 3/8 problem by adding ranging market detection
Approach: Test multiple regime detection methods on v19's winning params
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
    np_ema, np_atr, np_sma, np_adx, resample_to_weekly, resample_to_daily
)


def make_v19_cfg(**overrides):
    """v19 winner config"""
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
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


def backtest_with_regime(h4_df, cfg, regime_type="none", regime_params=None):
    """
    Extended backtest that adds regime filtering not in the standard backtester.
    We wrap the standard backtester and pre-filter the H4 data based on regime.

    For efficiency, we implement regime filtering by modifying the ATR filter
    or adding a custom filter mask.
    """
    if regime_params is None:
        regime_params = {}

    ind = precompute_indicators(h4_df, cfg)
    h4_o, h4_h, h4_l, h4_c, h4_times = ind[0], ind[1], ind[2], ind[3], ind[4]
    n = len(h4_o)

    if regime_type == "none":
        trades, eq, final = backtest_goldalpha(*ind, cfg)
        return trades, eq, final

    # For regime-based filtering, we modify the avg_atr to effectively block
    # entries during ranging periods by setting avg_atr very high
    h4_avg_atr = ind[13].copy()
    h4_atr = ind[12]

    if regime_type == "w1_ema_spread":
        # Block trading when W1 EMAs are too close (flat/ranging market)
        w1_fast = ind[5]
        w1_slow = ind[6]
        w1_times = ind[7]
        min_spread = regime_params.get("min_spread", 0.005)

        w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1
        for i in range(n):
            wi = w1_idx_map[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0:
                    spread_pct = abs(w1_fast[wi] - w1_slow[wi]) / mid
                    if spread_pct < min_spread:
                        h4_avg_atr[i] = 999999  # Block entry

    elif regime_type == "atr_trend":
        # Block when ATR is declining (volatility contraction = ranging)
        atr_lookback = regime_params.get("lookback", 20)
        atr_threshold = regime_params.get("threshold", 0.0)
        for i in range(atr_lookback, n):
            atr_slope = (h4_atr[i] - h4_atr[i - atr_lookback]) / max(h4_atr[i - atr_lookback], 0.01)
            if atr_slope < atr_threshold:
                h4_avg_atr[i] = 999999

    elif regime_type == "d1_slope":
        # Block when D1 EMA is flat (no trend direction)
        d1_ema = ind[9]
        d1_times = ind[10]
        slope_bars = regime_params.get("slope_bars", 10)
        min_slope = regime_params.get("min_slope", 0.002)

        d1_idx_map = np.searchsorted(d1_times, h4_times, side="right") - 1
        for i in range(n):
            di = d1_idx_map[i]
            if slope_bars <= di < len(d1_ema):
                prev = d1_ema[di - slope_bars]
                cur = d1_ema[di]
                if prev > 0:
                    slope_pct = abs(cur - prev) / prev
                    if slope_pct < min_slope:
                        h4_avg_atr[i] = 999999

    elif regime_type == "adx":
        # Block when ADX is low (no trend)
        adx = np_adx(h4_h, h4_l, h4_c, regime_params.get("period", 14))
        min_adx = regime_params.get("min_adx", 20)
        for i in range(n):
            if adx[i] < min_adx:
                h4_avg_atr[i] = 999999

    elif regime_type == "combined":
        # Multiple regime filters combined
        w1_fast = ind[5]; w1_slow = ind[6]; w1_times = ind[7]
        d1_ema = ind[9]; d1_times = ind[10]

        w1_min_spread = regime_params.get("w1_min_spread", 0.005)
        d1_slope_bars = regime_params.get("d1_slope_bars", 10)
        d1_min_slope = regime_params.get("d1_min_slope", 0.002)

        w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1
        d1_idx_map = np.searchsorted(d1_times, h4_times, side="right") - 1

        for i in range(n):
            blocked = False
            # W1 spread check
            wi = w1_idx_map[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0:
                    spread_pct = abs(w1_fast[wi] - w1_slow[wi]) / mid
                    if spread_pct < w1_min_spread:
                        blocked = True

            # D1 slope check
            if not blocked:
                di = d1_idx_map[i]
                if d1_slope_bars <= di < len(d1_ema):
                    prev = d1_ema[di - d1_slope_bars]
                    cur = d1_ema[di]
                    if prev > 0:
                        slope_pct = abs(cur - prev) / prev
                        if slope_pct < d1_min_slope:
                            blocked = True

            if blocked:
                h4_avg_atr[i] = 999999

    # Rebuild indicator tuple with modified avg_atr
    mod_ind = list(ind)
    mod_ind[13] = h4_avg_atr
    trades, eq, final = backtest_goldalpha(*mod_ind, cfg)
    return trades, eq, final


def run_regime_wfa(h4_df, cfg, regime_type, regime_params, n_windows=8):
    """WFA with regime filtering."""
    total_bars = len(h4_df)
    window_size = total_bars // n_windows
    oos_size = int(window_size * 0.25)
    results = []

    for w in range(n_windows):
        window_end = min((w + 1) * window_size, total_bars)
        oos_start = window_end - oos_size
        data_start = max(0, oos_start - 600)
        sub = h4_df.iloc[data_start:window_end].copy()
        trades, _, _ = backtest_with_regime(sub, cfg, regime_type, regime_params)
        oos_time = h4_df.index[oos_start]
        oos_trades = [t for t in trades if t["open_time"] >= oos_time]
        oos_end_time = h4_df.index[min(window_end - 1, total_bars - 1)]
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
    print("GoldAlpha v20 Regime-Adaptive Optimizer")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print("=" * 80)

    cfg = make_v19_cfg()

    # v19 baseline
    trades0, _, _ = backtest_with_regime(h4_df, cfg, "none")
    m0 = calc_metrics(trades0, cfg.INITIAL_BALANCE, total_days)
    wfa0 = run_regime_wfa(h4_df, cfg, "none", {})
    n_pass0 = sum(1 for r in wfa0 if r["pf"] > 1.0)
    print(f"\nv19 baseline: PF={m0['pf']:.2f} T={m0['n_trades']} DD={m0['max_dd']:.1f}% "
          f"WR={m0['win_rate']:.1f}% WFA={n_pass0}/8")

    # ================================================================
    # Test regime detection methods
    # ================================================================
    print("\n" + "=" * 80)
    print("REGIME DETECTION GRID SEARCH")
    print("=" * 80)

    tests = []

    # 1. W1 EMA Spread
    for min_spread in [0.002, 0.003, 0.004, 0.005, 0.007, 0.010, 0.015, 0.020]:
        tests.append(("w1_spread", "w1_ema_spread",
                      {"min_spread": min_spread}, f"W1Spread={min_spread}"))

    # 2. D1 EMA Slope
    for slope_bars in [5, 10, 15, 20]:
        for min_slope in [0.001, 0.002, 0.003, 0.005, 0.008, 0.010]:
            tests.append(("d1_slope", "d1_slope",
                          {"slope_bars": slope_bars, "min_slope": min_slope},
                          f"D1Slope({slope_bars},{min_slope})"))

    # 3. ADX
    for period in [14, 20]:
        for min_adx in [15, 18, 20, 22, 25, 28, 30]:
            tests.append(("adx", "adx",
                          {"period": period, "min_adx": min_adx},
                          f"ADX({period},{min_adx})"))

    # 4. ATR Trend
    for lookback in [10, 20, 30]:
        for threshold in [-0.1, -0.05, 0.0, 0.05, 0.1]:
            tests.append(("atr_trend", "atr_trend",
                          {"lookback": lookback, "threshold": threshold},
                          f"ATRTrend({lookback},{threshold})"))

    # Phase 1: Quick screen - just full-period metrics + trade count
    print(f"\n  Phase 1: Quick screen ({len(tests)} configs)")
    screened = []
    for i, (cat, rtype, rparams, label) in enumerate(tests):
        trades, _, _ = backtest_with_regime(h4_df, cfg, rtype, rparams)
        m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
        if m and m["n_trades"] >= 400 and m["pf"] >= 1.3:
            screened.append((cat, rtype, rparams, label, m))
        if (i + 1) % 50 == 0:
            print(f"    [{i+1}/{len(tests)}] {len(screened)} valid...")

    screened.sort(key=lambda x: x[4]["pf"], reverse=True)
    print(f"  -> {len(screened)} valid after screen")

    # Show top 20
    print(f"\n  Top 20 (pre-WFA):")
    print(f"  {'Label':>30} {'PF':>5} {'T':>5} {'DD%':>5} {'WR%':>4}")
    print("  " + "-" * 55)
    for _, _, _, label, m in screened[:20]:
        print(f"  {label:>30} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f}")

    # Phase 2: WFA on top 30
    print(f"\n  Phase 2: WFA on top 30")
    wfa_results = []
    for ci, (cat, rtype, rparams, label, m) in enumerate(screened[:30]):
        wfa = run_regime_wfa(h4_df, cfg, rtype, rparams)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        min_pf = min(r["pf"] for r in wfa) if wfa else 0
        wfa_trades = sum(r["n_trades"] for r in wfa)

        # OOS
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        trades_oos, _, _ = backtest_with_regime(sub, cfg, rtype, rparams)
        oos_list = [t for t in trades_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

        score = (n_pass / 8) * 40 + min(avg_pf, 2.5) * 5 + min(m["pf"], 3.0) * 10
        if m_oos and m_oos["n_trades"] >= 20:
            score += min(m_oos["pf"], 4.0) * 3

        wfa_results.append({
            "cat": cat, "rtype": rtype, "rparams": rparams,
            "label": label, "metrics": m,
            "wfa_pass": n_pass, "wfa_avg_pf": avg_pf, "wfa_min_pf": min_pf,
            "wfa_trades": wfa_trades, "wfa": wfa,
            "oos": m_oos, "score": score,
        })

        oos_pf = m_oos["pf"] if m_oos else 0
        oos_daily = m_oos["daily_jpy"] if m_oos else 0
        print(f"    [{ci+1}/30] {label:>30} WFA={n_pass}/8 AvgPF={avg_pf:.2f} "
              f"PF={m['pf']:.2f} T={m['n_trades']} OOS_PF={oos_pf:.2f} Score={score:.1f}")

    wfa_results.sort(key=lambda x: x["score"], reverse=True)

    # ================================================================
    # RESULTS
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RANKING")
    print("=" * 80)
    print(f"{'Rk':>2} {'Score':>6} {'PF':>5} {'T':>5} {'DD':>5} {'WR':>4} "
          f"{'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'OOS_PF':>6} | Label")
    print("-" * 85)
    for i, v in enumerate(wfa_results[:15]):
        m = v["metrics"]
        oos_pf = v["oos"]["pf"] if v["oos"] else 0
        print(f"{i+1:2d} {v['score']:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} "
              f"{m['max_dd']:5.1f} {m['win_rate']:4.0f} "
              f"{v['wfa_pass']:2d}/8 {v['wfa_avg_pf']:5.2f} {v['wfa_min_pf']:5.2f} "
              f"{oos_pf:6.2f} | {v['label']}")

    # ================================================================
    # WINNER DETAILS
    # ================================================================
    if not wfa_results:
        print("\nNo valid regime configs found!")
        return

    W = wfa_results[0]
    print("\n" + "=" * 80)
    print("WINNER REGIME")
    print("=" * 80)
    print(f"  Type: {W['rtype']}")
    print(f"  Params: {W['rparams']}")
    print(f"  Full: PF={W['metrics']['pf']:.2f} T={W['metrics']['n_trades']} DD={W['metrics']['max_dd']:.1f}%")
    print(f"  WFA: {W['wfa_pass']}/8, Avg PF={W['wfa_avg_pf']:.2f}, Min PF={W['wfa_min_pf']:.2f}")
    if W["oos"]:
        print(f"  OOS: PF={W['oos']['pf']:.2f} T={W['oos']['n_trades']} Daily={W['oos']['daily_jpy']:.0f}")

    # WFA window details
    print(f"\n  WFA Window Details:")
    for j, w in enumerate(W["wfa"]):
        st = "PASS" if w["pf"] > 1.0 else "FAIL"
        print(f"    W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} DD={w['max_dd']:5.1f}% [{st}]")

    # Risk scaling
    print(f"\n  Risk Scaling (Full Period):")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>5} {'Daily':>8} {'Final':>12}")
    print("  " + "-" * 60)
    best_risk = None
    for risk, maxlot in [(0.20, 0.10), (0.50, 0.20), (1.0, 0.30),
                          (1.5, 0.50), (2.0, 0.50), (2.5, 0.75),
                          (3.0, 1.00), (3.5, 1.50), (4.0, 2.00)]:
        cfg_r = make_v19_cfg(RiskPct=risk, MaxLot=maxlot)
        trades_r, _, _ = backtest_with_regime(h4_df, cfg_r, W["rtype"], W["rparams"])
        m_r = calc_metrics(trades_r, cfg_r.INITIAL_BALANCE, total_days)
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
                          (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        cfg_r = make_v19_cfg(RiskPct=risk, MaxLot=maxlot)
        sub = h4_df[h4_df.index >= "2022-01-01"].copy()
        trades_r, _, _ = backtest_with_regime(sub, cfg_r, W["rtype"], W["rparams"])
        oos_r = [t for t in trades_r if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_or = calc_metrics(oos_r, cfg_r.INITIAL_BALANCE, oos_days)
        if m_or:
            mark = " ***" if m_or["daily_jpy"] >= 5000 else ""
            print(f"    Risk={risk}%: PF={m_or['pf']:.2f} T={m_or['n_trades']} "
                  f"DD={m_or['max_dd']:.1f}% Daily={m_or['daily_jpy']:.0f}{mark}")

    # Year-by-year at 2.0% risk
    risk_level = best_risk if best_risk else 2.0
    maxlot_map = {0.2: 0.10, 0.5: 0.20, 1.0: 0.30, 1.5: 0.50,
                  2.0: 0.50, 2.5: 0.75, 3.0: 1.00, 3.5: 1.50, 4.0: 2.00}
    maxlot = maxlot_map.get(risk_level, 0.50)

    cfg_f = make_v19_cfg(RiskPct=risk_level, MaxLot=maxlot)
    trades_f, _, _ = backtest_with_regime(h4_df, cfg_f, W["rtype"], W["rparams"])
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
        print(f"  Best Risk: {best_risk}%")

    # ================================================================
    # Combined regime tests on top 3
    # ================================================================
    print("\n" + "=" * 80)
    print("COMBINED REGIME TESTS")
    print("=" * 80)

    top3 = wfa_results[:3]
    combined_tests = []

    # Try combining the best single regime filters
    for v in top3:
        rp = v["rparams"]
        rt = v["rtype"]
        if rt == "w1_ema_spread":
            for d1_sb in [5, 10, 15]:
                for d1_ms in [0.002, 0.003, 0.005]:
                    combined_tests.append(("combined", {
                        "w1_min_spread": rp["min_spread"],
                        "d1_slope_bars": d1_sb,
                        "d1_min_slope": d1_ms,
                    }, f"W1({rp['min_spread']})+D1({d1_sb},{d1_ms})"))
        elif rt == "d1_slope":
            for w1_ms in [0.003, 0.005, 0.007]:
                combined_tests.append(("combined", {
                    "w1_min_spread": w1_ms,
                    "d1_slope_bars": rp["slope_bars"],
                    "d1_min_slope": rp["min_slope"],
                }, f"W1({w1_ms})+D1({rp['slope_bars']},{rp['min_slope']})"))
        elif rt == "adx":
            for w1_ms in [0.003, 0.005]:
                combined_tests.append(("combined", {
                    "w1_min_spread": w1_ms,
                    "d1_slope_bars": 10,
                    "d1_min_slope": 0.003,
                }, f"W1({w1_ms})+D1(10,0.003)"))

    # Deduplicate
    seen = set()
    unique_combined = []
    for rt, rp, label in combined_tests:
        key = tuple(sorted(rp.items()))
        if key not in seen:
            seen.add(key)
            unique_combined.append((rt, rp, label))

    combined_results = []
    for ci, (rtype, rparams, label) in enumerate(unique_combined):
        trades_c, _, _ = backtest_with_regime(h4_df, cfg, rtype, rparams)
        m_c = calc_metrics(trades_c, cfg.INITIAL_BALANCE, total_days)
        if m_c and m_c["n_trades"] >= 400 and m_c["pf"] >= 1.3:
            wfa_c = run_regime_wfa(h4_df, cfg, rtype, rparams)
            n_pass_c = sum(1 for r in wfa_c if r["pf"] > 1.0)
            avg_pf_c = np.mean([r["pf"] for r in wfa_c]) if wfa_c else 0

            sub = h4_df[h4_df.index >= "2022-01-01"].copy()
            tr_oos, _, _ = backtest_with_regime(sub, cfg, rtype, rparams)
            oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
            oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
            m_oos_c = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)

            score_c = (n_pass_c / 8) * 40 + min(avg_pf_c, 2.5) * 5 + min(m_c["pf"], 3.0) * 10
            if m_oos_c and m_oos_c["n_trades"] >= 20:
                score_c += min(m_oos_c["pf"], 4.0) * 3

            combined_results.append({
                "rtype": rtype, "rparams": rparams, "label": label,
                "metrics": m_c, "wfa_pass": n_pass_c, "wfa_avg_pf": avg_pf_c,
                "oos": m_oos_c, "score": score_c,
            })
            oos_pf = m_oos_c["pf"] if m_oos_c else 0
            print(f"  [{ci+1}/{len(unique_combined)}] {label:>35} WFA={n_pass_c}/8 "
                  f"PF={m_c['pf']:.2f} T={m_c['n_trades']} OOS_PF={oos_pf:.2f}")

    combined_results.sort(key=lambda x: x["score"], reverse=True)
    if combined_results:
        print(f"\n  Top 5 Combined:")
        for i, v in enumerate(combined_results[:5]):
            m = v["metrics"]
            oos_pf = v["oos"]["pf"] if v["oos"] else 0
            print(f"  {i+1}. {v['label']:>35} Score={v['score']:.1f} WFA={v['wfa_pass']}/8 "
                  f"PF={m['pf']:.2f} T={m['n_trades']} OOS_PF={oos_pf:.2f}")

    # ================================================================
    # OVERALL WINNER
    # ================================================================
    all_results = wfa_results + combined_results
    all_results.sort(key=lambda x: x["score"], reverse=True)

    print("\n" + "=" * 80)
    print("OVERALL WINNER")
    print("=" * 80)
    OW = all_results[0]
    print(f"  Type: {OW['rtype']}")
    print(f"  Params: {OW.get('rparams', 'N/A')}")
    print(f"  Score: {OW['score']:.1f}")
    print(f"  Full: PF={OW['metrics']['pf']:.2f} T={OW['metrics']['n_trades']} DD={OW['metrics']['max_dd']:.1f}%")
    print(f"  WFA: {OW['wfa_pass']}/8, Avg PF={OW['wfa_avg_pf']:.2f}")
    if OW.get("oos"):
        print(f"  OOS: PF={OW['oos']['pf']:.2f} T={OW['oos']['n_trades']} Daily={OW['oos']['daily_jpy']:.0f}")
    print(f"  Label: {OW['label']}")

    # vs v19 comparison
    print(f"\n  v19 Baseline: PF={m0['pf']:.2f} T={m0['n_trades']} WFA={n_pass0}/8")
    print(f"  v20 Winner:   PF={OW['metrics']['pf']:.2f} T={OW['metrics']['n_trades']} WFA={OW['wfa_pass']}/8")
    improvement = OW['wfa_pass'] - n_pass0
    print(f"  WFA Improvement: {'+' if improvement >= 0 else ''}{improvement} windows")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V20 OPTIMIZATION COMPLETE ===")


if __name__ == "__main__":
    main()
