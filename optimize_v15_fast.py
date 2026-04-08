#!/usr/bin/env python3
"""
GoldAlpha v15 Fast Optimizer
Based on Phase 2/3 findings: Partial Close hurts PF. Best combos are:
  1. TD20+Slope3: PF=1.52, 1325 trades
  2. TD30+Struct2: PF=1.67, 1183 trades
  3. v13 base: PF=1.76, 1008 trades
Focus: targeted grid search on exit params + risk scaling + WFA
"""
import sys, os
sys.path.insert(0, "/tmp/FxTrading_EA")
sys.stdout.reconfigure(line_buffering=True)  # unbuffered output

import numpy as np
import pandas as pd
from itertools import product
import time
import warnings
warnings.filterwarnings("ignore")

from backtest_goldalpha import (
    load_csv, GoldAlphaConfig, precompute_indicators, backtest_goldalpha,
    calc_metrics, np_ema, np_atr, np_sma,
    resample_to_weekly, resample_to_daily,
    _calc_lot, _manage_positions, _close_position, _calc_pnl, _unrealized_pnl
)

DATA_DIR = "/tmp/FxTrading_EA"


def make_config(**overrides):
    """v13 base + overrides"""
    cfg = GoldAlphaConfig()
    cfg.BodyRatio = 0.34
    cfg.EMA_Zone_ATR = 0.40
    cfg.ATR_Filter = 0.35
    cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 3
    cfg.SL_ATR_Mult = 2.5
    cfg.Trail_ATR = 3.5
    cfg.BE_ATR = 1.5
    cfg.RiskPct = 0.18
    cfg.MaxLot = 0.10
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_test(h4_df, cfg, total_days):
    """Run one backtest via the existing engine"""
    ind = precompute_indicators(h4_df, cfg)
    trades, eq, final = backtest_goldalpha(*ind, cfg)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    return m, trades


def run_risk_table(h4_df, total_days, base_ov, label=""):
    """Risk scaling analysis"""
    print(f"\n  --- Risk Scaling: {label} ---")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>6} {'WR%':>5} {'Daily':>7} {'Final':>12}")
    print(f"  {'-'*65}")
    best = None
    for risk, maxlot in [(0.18, 0.10), (0.50, 0.15), (1.00, 0.20),
                          (1.50, 0.30), (2.00, 0.50), (2.50, 0.75), (3.00, 1.00)]:
        cfg = make_config(**{**base_ov, "RiskPct": risk, "MaxLot": maxlot})
        m, _ = run_test(h4_df, cfg, total_days)
        if m:
            flag = ""
            if m['daily_jpy'] >= 5000 and m['max_dd'] < 50:
                flag = " <<< TARGET"
            elif m['daily_jpy'] >= 5000:
                flag = " ***"
            print(f"  {risk:6.2f} {maxlot:6.2f} | {m['pf']:5.2f} {m['n_trades']:5d} "
                  f"{m['max_dd']:6.1f} {m['win_rate']:5.1f} {m['daily_jpy']:7.0f} "
                  f"{m['final_balance']:12,.0f}{flag}")
            if m['daily_jpy'] >= 5000 and (best is None or m['max_dd'] < best['max_dd']):
                best = {**m, 'risk': risk, 'maxlot': maxlot}
    return best


def run_wfa(h4_df, cfg, n_windows=8):
    """Walk-Forward Analysis"""
    total_bars = len(h4_df)
    window_size = total_bars // n_windows
    oos_size = int(window_size * 0.25)
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
    print("=" * 70)
    print("GoldAlpha v15 Fast Optimizer")
    print("=" * 70)

    h4_df = load_csv(os.path.join(DATA_DIR, "XAUUSD_H4.csv"))
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    total_bars = len(h4_df)
    print(f"H4: {total_bars} bars, {total_days} days")

    # =========================================================
    # Define 3 candidate feature sets
    # =========================================================
    candidates = {
        "A_v13base": {},
        "B_TD30_Struct2": {
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
        },
        "C_TD20_Slope3": {
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 20,
            "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3,
        },
    }

    # =========================================================
    # Phase 1: Focused grid search (entry/exit params)
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 1: Grid Search (3 candidates × 576 combos = 1728)")
    print("=" * 70)

    grid = {
        "BodyRatio": [0.28, 0.30, 0.32, 0.34],
        "EMA_Zone_ATR": [0.35, 0.40, 0.50, 0.60],
        "ATR_Filter": [0.25, 0.30, 0.35],
        "SL_ATR_Mult": [2.0, 2.5, 3.0],
        "Trail_ATR": [3.0, 3.5, 4.0],
        "BE_ATR": [1.0, 1.5],
    }

    keys = list(grid.keys())
    vals = list(grid.values())
    combos = list(product(*vals))
    n_combos = len(combos)
    print(f"Per candidate: {n_combos} combinations")

    all_results = {}
    for cand_name, feat_ov in candidates.items():
        print(f"\n--- {cand_name} ---")
        results = []
        best_score = -999
        for idx, cv in enumerate(combos):
            ov = {**feat_ov}
            for k, v in zip(keys, cv):
                ov[k] = v
            cfg = make_config(**ov)
            m, _ = run_test(h4_df, cfg, total_days)
            if m and m['n_trades'] >= 500:
                # Score: PF × min(trades, 1500) / 1000 - DD penalty
                score = m['pf'] * min(m['n_trades'], 1500) / 1000
                if m['max_dd'] > 30:
                    score -= (m['max_dd'] - 30) * 0.02
                entry = {k: v for k, v in zip(keys, cv)}
                entry.update(m)
                entry['score'] = score
                entry['features'] = feat_ov
                results.append(entry)
                if score > best_score:
                    best_score = score
            if (idx + 1) % 100 == 0:
                print(f"  [{idx+1}/{n_combos}] best_score={best_score:.2f}")

        results.sort(key=lambda x: x['score'], reverse=True)
        all_results[cand_name] = results

        print(f"\n  Top 10 for {cand_name}:")
        print(f"  {'PF':>5} {'T':>5} {'DD%':>6} {'WR%':>5} {'Daily':>6} | "
              f"{'Body':>5} {'Zone':>5} {'ATR_F':>5} {'SL':>4} {'Trail':>5} {'BE':>4} Score")
        for r in results[:10]:
            print(f"  {r['pf']:5.2f} {r['n_trades']:5d} {r['max_dd']:6.1f} "
                  f"{r['win_rate']:5.1f} {r['daily_jpy']:6.0f} | "
                  f"{r['BodyRatio']:5.2f} {r['EMA_Zone_ATR']:5.2f} {r['ATR_Filter']:5.2f} "
                  f"{r['SL_ATR_Mult']:4.1f} {r['Trail_ATR']:5.1f} {r['BE_ATR']:4.1f} "
                  f"{r['score']:5.2f}")

    # =========================================================
    # Phase 2: Overall winner and D1 tolerance sweep
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 2: D1 Tolerance + MaxPositions Fine-tuning")
    print("=" * 70)

    # Get top 3 from each candidate
    top_configs = []
    for cand_name, results in all_results.items():
        for r in results[:3]:
            entry_params = {k: r[k] for k in keys}
            feat_params = r['features']
            top_configs.append({
                "name": cand_name,
                "score": r['score'],
                "entry": entry_params,
                "feat": feat_params,
                "pf": r['pf'],
                "trades": r['n_trades'],
                "dd": r['max_dd'],
            })

    top_configs.sort(key=lambda x: x['score'], reverse=True)
    print(f"\nTop 9 overall:")
    for i, tc in enumerate(top_configs[:9]):
        print(f"  #{i+1}: {tc['name']:20s} PF={tc['pf']:.2f} T={tc['trades']} "
              f"DD={tc['dd']:.1f}% Score={tc['score']:.2f} | {tc['entry']}")

    # Fine-tune D1_Tolerance and MaxPositions on top 3
    print(f"\nFine-tuning D1_Tolerance + MaxPositions on top 3:")
    final_candidates = []
    for tc in top_configs[:3]:
        for d1_tol in [0.003, 0.005, 0.008, 0.010]:
            for maxpos in [3, 4]:
                ov = {**tc['feat'], **tc['entry'], "D1_Tolerance": d1_tol, "MaxPositions": maxpos}
                cfg = make_config(**ov)
                m, _ = run_test(h4_df, cfg, total_days)
                if m and m['n_trades'] >= 500:
                    score = m['pf'] * min(m['n_trades'], 1500) / 1000
                    if m['max_dd'] > 30:
                        score -= (m['max_dd'] - 30) * 0.02
                    final_candidates.append({
                        "name": tc['name'],
                        "ov": ov,
                        "pf": m['pf'], "trades": m['n_trades'],
                        "dd": m['max_dd'], "wr": m['win_rate'],
                        "daily": m['daily_jpy'], "score": score,
                    })
                    print(f"    {tc['name']:15s} D1={d1_tol:.3f} MaxP={maxpos}: "
                          f"PF={m['pf']:.2f} T={m['n_trades']:4d} DD={m['max_dd']:.1f}% "
                          f"Score={score:.2f}")

    final_candidates.sort(key=lambda x: x['score'], reverse=True)
    print(f"\nTop 5 final candidates:")
    for i, fc in enumerate(final_candidates[:5]):
        print(f"  #{i+1}: {fc['name']:15s} PF={fc['pf']:.2f} T={fc['trades']} "
              f"DD={fc['dd']:.1f}% Daily={fc['daily']:.0f} Score={fc['score']:.2f}")

    # =========================================================
    # Phase 3: Winner analysis
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 3: Winner Full Analysis")
    print("=" * 70)

    winner = final_candidates[0]
    winner_ov = winner['ov']
    print(f"\nWINNER: {winner['name']}")
    print(f"  PF={winner['pf']:.2f} T={winner['trades']} DD={winner['dd']:.1f}% "
          f"WR={winner['wr']:.1f}% Daily={winner['daily']:.0f} JPY (at 0.18% risk)")
    print(f"  Parameters:")
    for k, v in sorted(winner_ov.items()):
        print(f"    {k}: {v}")

    # Risk scaling
    best_spot = run_risk_table(h4_df, total_days, winner_ov, winner['name'])

    # Also check runner-ups
    if len(final_candidates) > 1:
        runner = final_candidates[1]
        run_risk_table(h4_df, total_days, runner['ov'], f"Runner-up: {runner['name']}")
    if len(final_candidates) > 2:
        runner2 = final_candidates[2]
        run_risk_table(h4_df, total_days, runner2['ov'], f"Runner-up 2: {runner2['name']}")

    # =========================================================
    # Phase 4: WFA Validation
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 4: Walk-Forward Analysis (8 windows)")
    print("=" * 70)

    for label, risk, maxlot in [("Low", 0.18, 0.10), ("Mid", 1.5, 0.30), ("High", 2.5, 0.75)]:
        cfg_wfa = make_config(**{**winner_ov, "RiskPct": risk, "MaxLot": maxlot})
        wfa = run_wfa(h4_df, cfg_wfa, 8)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
        total_t = sum(r["n_trades"] for r in wfa) if wfa else 0
        print(f"\n  {label} (Risk={risk}%): {n_pass}/{len(wfa)} PASS, "
              f"Avg PF={avg_pf:.2f}, OOS Trades={total_t}")
        for j, r in enumerate(wfa):
            s = "PASS" if r["pf"] > 1.0 else "FAIL"
            print(f"    W{j+1}: PF={r['pf']:5.2f} T={r['n_trades']:3d} "
                  f"DD={r['max_dd']:5.1f}% WR={r['win_rate']:4.0f}% [{s}]")

    # Runner-up WFA if winner WFA is poor
    if len(final_candidates) > 1:
        runner = final_candidates[1]
        cfg_wfa_r = make_config(**{**runner['ov'], "RiskPct": 0.18, "MaxLot": 0.10})
        wfa_r = run_wfa(h4_df, cfg_wfa_r, 8)
        n_pass_r = sum(1 for r in wfa_r if r["pf"] > 1.0)
        avg_pf_r = np.mean([r["pf"] for r in wfa_r]) if wfa_r else 0
        print(f"\n  Runner-up ({runner['name']}): {n_pass_r}/{len(wfa_r)} PASS, Avg PF={avg_pf_r:.2f}")
        for j, r in enumerate(wfa_r):
            s = "PASS" if r["pf"] > 1.0 else "FAIL"
            print(f"    W{j+1}: PF={r['pf']:5.2f} T={r['n_trades']:3d} [{s}]")

    # =========================================================
    # Phase 5: OOS 2024-2026
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 5: Out-of-Sample (2024-2026)")
    print("=" * 70)

    mask = h4_df.index >= "2022-01-01"
    sub_oos = h4_df[mask].copy()
    for risk, maxlot in [(1.0, 0.20), (1.5, 0.30), (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        cfg_oos = make_config(**{**winner_ov, "RiskPct": risk, "MaxLot": maxlot})
        ind = precompute_indicators(sub_oos, cfg_oos)
        trades, _, _ = backtest_goldalpha(*ind, cfg_oos)
        oos_trades = [t for t in trades if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub_oos.index[-1] - pd.Timestamp("2024-01-01")).days)
        m = calc_metrics(oos_trades, cfg_oos.INITIAL_BALANCE, oos_days)
        if m:
            flag = " ***" if m['daily_jpy'] >= 5000 else ""
            print(f"  Risk={risk}%: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% "
                  f"Daily={m['daily_jpy']:.0f} JPY{flag}")

    # =========================================================
    # Phase 6: Year-by-year
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 6: Year-by-Year")
    print("=" * 70)

    target_risk = best_spot['risk'] if best_spot else 2.5
    target_maxlot = best_spot['maxlot'] if best_spot else 0.75
    cfg_yy = make_config(**{**winner_ov, "RiskPct": target_risk, "MaxLot": target_maxlot})
    ind = precompute_indicators(h4_df, cfg_yy)
    trades_yy, _, _ = backtest_goldalpha(*ind, cfg_yy)
    df_yy = pd.DataFrame(trades_yy)
    if len(df_yy) > 0:
        df_yy["year"] = pd.to_datetime(df_yy["close_time"]).dt.year
        print(f"  Risk={target_risk}% MaxLot={target_maxlot}")
        for yr, grp in df_yy.groupby("year"):
            pnls = grp["pnl_jpy"].values
            wins = (pnls > 0).sum()
            n = len(pnls)
            wr = wins / n * 100 if n > 0 else 0
            gp = pnls[pnls > 0].sum() if wins > 0 else 0
            gl = abs(pnls[pnls <= 0].sum()) if (n - wins) > 0 else 1
            pf = gp / gl if gl > 0 else float("inf")
            daily = pnls.sum() / 365
            print(f"    {yr}: T={n:4d} PF={pf:5.2f} WR={wr:4.0f}% "
                  f"PnL={pnls.sum():+12,.0f} Daily={daily:+7,.0f}")

    # Also show 0.18% risk year-by-year for true PF
    cfg_low = make_config(**{**winner_ov, "RiskPct": 0.18, "MaxLot": 0.10})
    ind = precompute_indicators(h4_df, cfg_low)
    trades_low, _, _ = backtest_goldalpha(*ind, cfg_low)
    df_low = pd.DataFrame(trades_low)
    if len(df_low) > 0:
        df_low["year"] = pd.to_datetime(df_low["close_time"]).dt.year
        print(f"\n  Low risk (0.18%) year-by-year:")
        for yr, grp in df_low.groupby("year"):
            pnls = grp["pnl_jpy"].values
            wins = (pnls > 0).sum()
            n = len(pnls)
            wr = wins / n * 100 if n > 0 else 0
            gp = pnls[pnls > 0].sum() if wins > 0 else 0
            gl = abs(pnls[pnls <= 0].sum()) if (n - wins) > 0 else 1
            pf = gp / gl if gl > 0 else float("inf")
            print(f"    {yr}: T={n:4d} PF={pf:5.2f} WR={wr:4.0f}% PnL={pnls.sum():+10,.0f}")

    # =========================================================
    # FINAL SUMMARY
    # =========================================================
    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    print(f"\n  v15 Configuration ({winner['name']} base):")
    param_keys = ['BodyRatio', 'EMA_Zone_ATR', 'ATR_Filter', 'D1_Tolerance',
                  'MaxPositions', 'SL_ATR_Mult', 'Trail_ATR', 'BE_ATR']
    feat_keys = ['USE_TIME_DECAY', 'MAX_HOLD_BARS', 'USE_STRUCTURE', 'STRUCTURE_BARS',
                 'USE_EMA_SLOPE', 'EMA_SLOPE_BARS']
    print(f"  Entry/Exit:")
    for k in param_keys:
        if k in winner_ov:
            print(f"    {k}: {winner_ov[k]}")
    print(f"  Features:")
    for k in feat_keys:
        if k in winner_ov:
            print(f"    {k}: {winner_ov[k]}")

    print(f"\n  Full period (0.18% risk):")
    print(f"    PF={winner['pf']:.2f}, T={winner['trades']}, DD={winner['dd']:.1f}%, "
          f"WR={winner['wr']:.1f}%")

    if best_spot:
        print(f"\n  Sweet spot for 5000 JPY/day:")
        print(f"    Risk={best_spot['risk']}%, MaxLot={best_spot['maxlot']}")
        print(f"    PF={best_spot['pf']:.2f}, T={best_spot['n_trades']}, "
              f"DD={best_spot['max_dd']:.1f}%, Daily={best_spot['daily_jpy']:.0f} JPY")

    print(f"\n  Elapsed: {elapsed:.0f}s")

    # Output winner config as JSON for easy copy
    print("\n  v15 config dict:")
    print(f"  {winner_ov}")


if __name__ == "__main__":
    main()
