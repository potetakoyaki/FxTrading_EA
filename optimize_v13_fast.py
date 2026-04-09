"""
optimize_v13_fast.py -- Two-phase optimization for GoldAlpha v13
Phase 1: Search entry params (trade count drivers) with fixed exits
Phase 2: Refine exit params on best entry combos
Phase 3: WFA validation on top candidates
"""

import sys
import json
import itertools
import time
from backtest_alpha import (
    AlphaConfig, load_data, build_weekly, prepare_indicators,
    run_backtest, calc_metrics, run_wfa
)

DATA_DIR = "/tmp/FxTrading_EA_clone"


def search_phase(h4, d1, w1, param_grid, base_cfg, label=""):
    """Generic grid search returning sorted results."""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))
    total = len(combos)
    print(f"  {label}: {total} combinations")

    results = []
    t0 = time.time()

    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        cfg = AlphaConfig(**{k: getattr(base_cfg, k) for k in base_cfg.__dataclass_fields__})
        for k, v in params.items():
            setattr(cfg, k, v)

        trades_list, dd_val = run_backtest(h4, d1, w1, cfg)
        m = calc_metrics(trades_list, cfg, dd_val)
        for k, v in params.items():
            m[k] = v
        m["config"] = cfg.label()
        results.append(m)

        if (idx + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed
            eta = (total - idx - 1) / rate
            print(f"    [{idx+1}/{total}] {elapsed:.0f}s, ETA {eta:.0f}s | "
                  f"T={m['trades']} PF={m['pf']} DD={m['dd']}%")

    elapsed = time.time() - t0
    print(f"    Done in {elapsed:.0f}s ({total/elapsed:.0f} combos/sec)")
    return results


def main():
    h4_raw, d1_raw = load_data(f"{DATA_DIR}/XAUUSD_H4.csv", f"{DATA_DIR}/XAUUSD_D1.csv")
    w1_raw = build_weekly(d1_raw)

    # ---- Baseline ----
    print("=" * 80)
    print("BASELINE: v12")
    print("=" * 80)
    cfg_v12 = AlphaConfig()
    h4, d1, w1 = prepare_indicators(h4_raw.copy(), d1_raw.copy(), w1_raw.copy(), cfg_v12)
    trades, dd = run_backtest(h4, d1, w1, cfg_v12)
    m = calc_metrics(trades, cfg_v12, dd)
    print(f"  Trades={m['trades']} PF={m['pf']} WR={m['wr']}% DD={m['dd']}% "
          f"Net={m['net_jpy']:,}JPY Daily={m['daily_jpy']:,}JPY/day\n")

    # ================================================================
    # PHASE 1: Entry params search (trade count drivers)
    # Fix exit params at v12 defaults
    # ================================================================
    print("=" * 80)
    print("PHASE 1: Entry parameters search (trade count)")
    print("=" * 80)

    entry_grid = {
        "BodyRatio":     [0.26, 0.27, 0.28, 0.29, 0.30, 0.31, 0.32, 0.33],
        "EMA_Zone_ATR":  [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70],
        "ATR_Filter":    [0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
        "D1_Tolerance":  [0.002, 0.003, 0.005, 0.007, 0.010],
        "MaxPositions":  [2, 3],
    }
    # 8*9*6*5*2 = 4320 combos - manageable

    entry_results = search_phase(h4, d1, w1, entry_grid, cfg_v12, "Entry search")

    # Filter: 500+ trades, PF >= 1.4 (relaxed for entry-only)
    good_entry = [r for r in entry_results
                  if r["trades"] >= 500 and r["pf"] >= 1.40 and r["dd"] < 20.0]
    good_entry.sort(key=lambda x: (-x["pf"], -x["trades"]))

    print(f"\n  Candidates with 500+ trades & PF>=1.40: {len(good_entry)}")
    for i, r in enumerate(good_entry[:15]):
        print(f"    #{i+1}: T={r['trades']:4d} PF={r['pf']:.3f} DD={r['dd']:.1f}% "
              f"Daily={r['daily_jpy']:,}JPY "
              f"Body={r['BodyRatio']:.2f} Zone={r['EMA_Zone_ATR']:.2f} "
              f"ATR={r['ATR_Filter']:.2f} D1T={r['D1_Tolerance']:.3f} MaxP={r['MaxPositions']}")

    if not good_entry:
        # Relax further
        good_entry = [r for r in entry_results
                      if r["trades"] >= 450 and r["pf"] >= 1.30]
        good_entry.sort(key=lambda x: (-x["pf"], -x["trades"]))
        print(f"  Relaxed (450+ trades, PF>=1.30): {len(good_entry)}")
        for i, r in enumerate(good_entry[:15]):
            print(f"    #{i+1}: T={r['trades']:4d} PF={r['pf']:.3f} DD={r['dd']:.1f}% "
                  f"Body={r['BodyRatio']:.2f} Zone={r['EMA_Zone_ATR']:.2f} "
                  f"ATR={r['ATR_Filter']:.2f} D1T={r['D1_Tolerance']:.3f}")

    # ================================================================
    # PHASE 2: Exit params refinement on top 5 entry configs
    # ================================================================
    print(f"\n{'=' * 80}")
    print("PHASE 2: Exit parameters refinement")
    print("=" * 80)

    # Pick top 5 unique entry configs by PF
    seen_entries = set()
    top_entries = []
    for r in good_entry:
        key = (r["BodyRatio"], r["EMA_Zone_ATR"], r["ATR_Filter"],
               r["D1_Tolerance"], r["MaxPositions"])
        if key not in seen_entries:
            seen_entries.add(key)
            top_entries.append(r)
            if len(top_entries) >= 5:
                break

    exit_grid = {
        "BE_ATR":       [0.8, 1.0, 1.2, 1.5, 1.8, 2.0],
        "Trail_ATR":    [1.5, 2.0, 2.5, 3.0, 3.5],
        "SL_ATR_Mult":  [1.5, 1.8, 2.0, 2.2, 2.5],
        "RiskPct":      [0.15, 0.18, 0.21, 0.24, 0.30],
    }
    # 6*5*5*5 = 750 per entry config, 5 configs = 3750

    all_phase2 = []
    for entry_idx, entry in enumerate(top_entries):
        print(f"\n  Entry config #{entry_idx+1}: Body={entry['BodyRatio']:.2f} "
              f"Zone={entry['EMA_Zone_ATR']:.2f} ATR={entry['ATR_Filter']:.2f} "
              f"D1T={entry['D1_Tolerance']:.3f} MaxP={entry['MaxPositions']}")

        base = AlphaConfig(
            BodyRatio=entry["BodyRatio"],
            EMA_Zone_ATR=entry["EMA_Zone_ATR"],
            ATR_Filter=entry["ATR_Filter"],
            D1_Tolerance=entry["D1_Tolerance"],
            MaxPositions=entry["MaxPositions"],
        )

        exit_results = search_phase(h4, d1, w1, exit_grid, base,
                                     f"Exit search (entry #{entry_idx+1})")

        # Filter
        good_exit = [r for r in exit_results
                     if r["trades"] >= 500 and r["pf"] >= 1.50 and r["dd"] < 20.0]

        if not good_exit:
            good_exit = [r for r in exit_results
                         if r["trades"] >= 450 and r["pf"] >= 1.40 and r["dd"] < 20.0]

        good_exit.sort(key=lambda x: (-x["pf"], -x["trades"]))

        for r in good_exit:
            r["entry_body"] = entry["BodyRatio"]
            r["entry_zone"] = entry["EMA_Zone_ATR"]
            r["entry_atr"] = entry["ATR_Filter"]
            r["entry_d1t"] = entry["D1_Tolerance"]
            r["entry_maxp"] = entry["MaxPositions"]
        all_phase2.extend(good_exit)

        print(f"    Good candidates: {len(good_exit)}")
        for i, r in enumerate(good_exit[:5]):
            daily_ok = "OK" if r["daily_jpy"] >= 5000 else "NG"
            print(f"      #{i+1}: T={r['trades']:4d} PF={r['pf']:.3f} DD={r['dd']:.1f}% "
                  f"Daily={r['daily_jpy']:,}[{daily_ok}] "
                  f"BE={r['BE_ATR']:.1f} Trail={r['Trail_ATR']:.1f} "
                  f"SL={r['SL_ATR_Mult']:.1f} Risk={r['RiskPct']:.2f}")

    # ================================================================
    # PHASE 3: WFA on top 10 candidates
    # ================================================================
    print(f"\n{'=' * 80}")
    print("PHASE 3: WFA Validation")
    print("=" * 80)

    # Score and rank all phase2 results
    for r in all_phase2:
        trade_f = min(r["trades"] / 500, 1.2)
        daily_f = min(r["daily_jpy"] / 5000, 1.5)
        dd_pen = max(0, (r["dd"] - 15) * 0.05)
        r["composite"] = r["pf"] * trade_f * daily_f - dd_pen

    all_phase2.sort(key=lambda x: -x["composite"])

    # Deduplicate and take top 10
    seen = set()
    top10 = []
    for r in all_phase2:
        key = (r.get("entry_body"), r.get("entry_zone"), r.get("entry_atr"),
               r.get("entry_d1t"), r.get("entry_maxp"),
               r.get("BE_ATR"), r.get("Trail_ATR"), r.get("SL_ATR_Mult"),
               r.get("RiskPct"))
        if key not in seen:
            seen.add(key)
            top10.append(r)
            if len(top10) >= 10:
                break

    if not top10:
        print("  No candidates found! Using best from phase 1 with default exits.")
        if good_entry:
            e = good_entry[0]
            top10 = [{
                "entry_body": e["BodyRatio"],
                "entry_zone": e["EMA_Zone_ATR"],
                "entry_atr": e["ATR_Filter"],
                "entry_d1t": e["D1_Tolerance"],
                "entry_maxp": e["MaxPositions"],
                "BE_ATR": 1.5,
                "Trail_ATR": 2.5,
                "SL_ATR_Mult": 2.0,
                "RiskPct": 0.18,
                "pf": e["pf"],
                "trades": e["trades"],
                "dd": e["dd"],
                "daily_jpy": e.get("daily_jpy", 0),
            }]

    wfa_winners = []
    for i, cand in enumerate(top10):
        cfg = AlphaConfig(
            BodyRatio=cand.get("entry_body", cand.get("BodyRatio", 0.32)),
            EMA_Zone_ATR=cand.get("entry_zone", cand.get("EMA_Zone_ATR", 0.4)),
            ATR_Filter=cand.get("entry_atr", cand.get("ATR_Filter", 0.6)),
            D1_Tolerance=cand.get("entry_d1t", cand.get("D1_Tolerance", 0.003)),
            MaxPositions=cand.get("entry_maxp", cand.get("MaxPositions", 2)),
            BE_ATR=cand["BE_ATR"],
            Trail_ATR=cand["Trail_ATR"],
            SL_ATR_Mult=cand["SL_ATR_Mult"],
            RiskPct=cand["RiskPct"],
        )

        h4c, d1c, w1c = prepare_indicators(h4_raw.copy(), d1_raw.copy(), w1_raw.copy(), cfg)

        # Full
        trades_f, dd_f = run_backtest(h4c, d1c, w1c, cfg)
        m_f = calc_metrics(trades_f, cfg, dd_f)

        # OOS
        trades_o, dd_o = run_backtest(h4c, d1c, w1c, cfg,
                                       start_date="2022-01-01", end_date="2026-12-31")
        m_o = calc_metrics(trades_o, cfg, dd_o)

        # WFA
        wfa = run_wfa(h4c, d1c, w1c, cfg)
        profitable_q = sum(1 for r in wfa if r["pf"] > 1.0 and r["trades"] >= 3)
        total_q = len([r for r in wfa if r["trades"] >= 3])

        result = {
            "rank": i + 1,
            "full_trades": m_f["trades"],
            "full_pf": m_f["pf"],
            "full_dd": m_f["dd"],
            "full_daily": m_f["daily_jpy"],
            "full_net": m_f["net_jpy"],
            "oos_trades": m_o["trades"],
            "oos_pf": m_o["pf"],
            "oos_dd": m_o["dd"],
            "oos_daily": m_o["daily_jpy"],
            "wfa_pass": profitable_q,
            "wfa_total": total_q,
            "wfa_details": wfa,
            "params": {
                "BodyRatio": cfg.BodyRatio,
                "EMA_Zone_ATR": cfg.EMA_Zone_ATR,
                "ATR_Filter": cfg.ATR_Filter,
                "D1_Tolerance": cfg.D1_Tolerance,
                "MaxPositions": cfg.MaxPositions,
                "BE_ATR": cfg.BE_ATR,
                "Trail_ATR": cfg.Trail_ATR,
                "SL_ATR_Mult": cfg.SL_ATR_Mult,
                "RiskPct": cfg.RiskPct,
            }
        }
        wfa_winners.append(result)

        wfa_pct = profitable_q / max(total_q, 1) * 100
        t_ok = "OK" if m_f["trades"] >= 500 else "NG"
        pf_ok = "OK" if m_f["pf"] >= 1.5 else "NG"
        dd_ok = "OK" if m_f["dd"] < 15 else "NG"
        d_ok = "OK" if m_f["daily_jpy"] >= 5000 else "NG"
        w_ok = "OK" if wfa_pct >= 75 else "NG"
        print(f"  #{i+1}: T={m_f['trades']}[{t_ok}] PF={m_f['pf']:.3f}[{pf_ok}] "
              f"DD={m_f['dd']:.1f}%[{dd_ok}] Daily={m_f['daily_jpy']:,}[{d_ok}] "
              f"WFA={profitable_q}/{total_q}[{w_ok}] OOS_PF={m_o['pf']:.2f} "
              f"Body={cfg.BodyRatio:.2f} Zone={cfg.EMA_Zone_ATR:.2f}")

    # ================================================================
    # FINAL: Select winner
    # ================================================================
    # Score: WFA rate * PF * trade_factor * daily_factor
    for w in wfa_winners:
        wfa_rate = w["wfa_pass"] / max(w["wfa_total"], 1)
        tf = min(w["full_trades"] / 500, 1.0)
        df = min(w["full_daily"] / 5000, 1.5)
        dd_pen = max(0, (w["full_dd"] - 15) * 0.1)
        w["final_score"] = wfa_rate * w["full_pf"] * tf * df * w["oos_pf"] - dd_pen

    wfa_winners.sort(key=lambda x: -x["final_score"])
    winner = wfa_winners[0]

    print(f"\n{'=' * 80}")
    print("WINNER:")
    print(f"  Full: Trades={winner['full_trades']} PF={winner['full_pf']:.3f} "
          f"DD={winner['full_dd']:.1f}% Daily={winner['full_daily']:,}JPY")
    print(f"  OOS:  Trades={winner['oos_trades']} PF={winner['oos_pf']:.3f} "
          f"DD={winner['oos_dd']:.1f}% Daily={winner['oos_daily']:,}JPY")
    print(f"  WFA:  {winner['wfa_pass']}/{winner['wfa_total']} quarters profitable")
    print(f"  Params: {json.dumps(winner['params'], indent=4)}")
    print("=" * 80)

    # Detailed WFA breakdown
    print(f"\nWFA Detail:")
    print(f"{'Quarter':<12} {'Trades':>6} {'PF':>6} {'WR':>6} {'DD':>6} {'Net JPY':>12}")
    print("-" * 55)
    for r in winner["wfa_details"]:
        if r["trades"] < 1:
            continue
        status = "PASS" if r["pf"] > 1.0 and r["trades"] >= 3 else "FAIL"
        print(f"{r['quarter']:<12} {r['trades']:>6} {r['pf']:>6.2f} {r['wr']:>5.1f}% "
              f"{r['dd']:>5.1f}% {r['net_jpy']:>11,} [{status}]")

    # Validation summary
    checks = {
        "Trades >= 500": winner["full_trades"] >= 500,
        "PF >= 1.5": winner["full_pf"] >= 1.5,
        "DD < 15%": winner["full_dd"] < 15,
        "Daily >= 5000 JPY": winner["full_daily"] >= 5000,
        "WFA >= 75%": winner["wfa_pass"] / max(winner["wfa_total"], 1) >= 0.75,
        "OOS PF >= 1.3": winner["oos_pf"] >= 1.3,
    }
    print(f"\nValidation:")
    all_pass = True
    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {check}")

    # Save
    with open(f"{DATA_DIR}/v13_final_results.json", "w") as f:
        json.dump({
            "winner": {k: v for k, v in winner.items() if k != "wfa_details"},
            "wfa_details": winner["wfa_details"],
            "all_candidates": [{k: v for k, v in w.items() if k != "wfa_details"}
                               for w in wfa_winners],
            "all_pass": all_pass,
        }, f, indent=2, default=str)

    print(f"\nResults saved to v13_final_results.json")

    # If not all pass, try aggressive parameter expansion
    if not all_pass:
        print(f"\n{'=' * 80}")
        print("WARNING: Not all targets met. Consider:")
        if not checks["Trades >= 500"]:
            print("  - Lower BodyRatio or widen EMA_Zone_ATR")
        if not checks["PF >= 1.5"]:
            print("  - Tighten entry filters or improve exit (BE/Trail)")
        if not checks["DD < 15%"]:
            print("  - Reduce RiskPct or MaxPositions")
        if not checks["Daily >= 5000 JPY"]:
            print("  - Increase RiskPct (carefully)")
        print("=" * 80)

    return winner


if __name__ == "__main__":
    winner = main()
