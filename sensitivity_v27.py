"""
GoldAlpha v27 Sensitivity Analysis
Tests parameter stability: ±variation on each param, measures PF/trade impact.
Also validates on 2024+ subperiod with risk scaling.
"""

import sys, os, time
import numpy as np
import pandas as pd

sys.path.insert(0, "/tmp/FxTrading_EA")
from backtest_goldalpha import (
    load_csv, GoldAlphaConfig, precompute_indicators,
    backtest_goldalpha, calc_metrics, np_ema, np_sma, np_atr,
    resample_to_daily, resample_to_weekly
)


class Tee:
    def __init__(self, *f):
        self.files = f
    def write(self, d):
        for f in self.files: f.write(d); f.flush()
    def flush(self):
        for f in self.files: f.flush()

log = open("/tmp/v27_sensitivity.log", "w")
sys.stdout = Tee(sys.__stdout__, log)


def make_cfg(**ov):
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA=8; cfg.W1_SlowEMA=21; cfg.D1_EMA=50
    cfg.H4_EMA=20; cfg.ATR_Period=14; cfg.ATR_SMA=50
    cfg.SL_ATR_Mult=3.0; cfg.Trail_ATR=3.5; cfg.BE_ATR=1.0
    cfg.RiskPct=0.20; cfg.BodyRatio=0.32
    cfg.EMA_Zone_ATR=0.30; cfg.ATR_Filter=0.30; cfg.D1_Tolerance=0.007
    cfg.MaxPositions=2; cfg.MinLot=0.01; cfg.MaxLot=0.50
    cfg.INITIAL_BALANCE=300_000
    cfg.USE_EMA_SLOPE=True; cfg.EMA_SLOPE_BARS=5
    cfg.USE_TIME_DECAY=True; cfg.MAX_HOLD_BARS=30
    cfg.USE_W1_SEPARATION=True; cfg.W1_SEP_MIN=0.005
    cfg.USE_STRUCTURE=False; cfg.USE_VOL_REGIME=False
    cfg.USE_SESSION_FILTER=False; cfg.USE_RSI_CONFIRM=False
    cfg.USE_PARTIAL_CLOSE=False; cfg.USE_ADX_FILTER=False
    for k,v in ov.items(): setattr(cfg, k, v)
    return cfg


def compute_mask(ind, d1sb=5, d1ms=0.002, w1ms=0.005):
    h4_times=ind[4]; w1f=ind[5]; w1s=ind[6]; w1t=ind[7]
    d1e=ind[9]; d1t=ind[10]; n=len(h4_times)
    w1i=np.searchsorted(w1t, h4_times, side="right")-1
    d1i=np.searchsorted(d1t, h4_times, side="right")-1
    blk=np.zeros(n, dtype=bool)
    for i in range(n):
        wi=w1i[i]
        if w1ms>0 and 0<=wi<len(w1f):
            mid=(w1f[wi]+w1s[wi])/2
            if mid>0 and abs(w1f[wi]-w1s[wi])/mid<w1ms: blk[i]=True
        if not blk[i] and d1ms>0:
            di=d1i[i]
            if d1sb<=di<len(d1e):
                p=d1e[di-d1sb]; c=d1e[di]
                if p>0 and abs(c-p)/p<d1ms: blk[i]=True
    return blk


def run(ind, mask, cfg, total_days):
    mod=list(ind); aa=ind[13].copy(); aa[mask]=999999; mod[13]=aa
    t,_,_=backtest_goldalpha(*tuple(mod), cfg=cfg)
    return calc_metrics(t, cfg.INITIAL_BALANCE, total_days), t


def main():
    t0=time.time()
    h4=load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    td=(h4.index[-1]-h4.index[0]).days
    cfg=make_cfg()
    ind=precompute_indicators(h4, cfg)
    mask=compute_mask(ind)

    print("="*80)
    print("GoldAlpha v27 - Sensitivity & Stability Analysis")
    print(f"H4: {len(h4)} bars, {td} days")
    print("="*80)

    # Baseline
    m_base,_=run(ind, mask, cfg, td)
    print(f"\nBASELINE: PF={m_base['pf']:.2f} T={m_base['n_trades']} DD={m_base['max_dd']:.1f}% "
          f"D¥={m_base['daily_jpy']:.0f} WR={m_base['win_rate']:.1f}%")

    # ================================================================
    # 1. Parameter Sensitivity (one-at-a-time)
    # ================================================================
    print("\n" + "="*80)
    print("1. PARAMETER SENSITIVITY (±variation)")
    print("="*80)

    params_to_test = [
        ("SL_ATR_Mult",   [2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0]),
        ("Trail_ATR",     [2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0, 4.25, 4.5]),
        ("BE_ATR",        [0.3, 0.5, 0.7, 1.0, 1.2, 1.5]),
        ("EMA_Zone_ATR",  [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]),
        ("BodyRatio",     [0.20, 0.24, 0.28, 0.32, 0.36, 0.40]),
        ("ATR_Filter",    [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]),
        ("D1_Tolerance",  [0.002, 0.003, 0.005, 0.007, 0.010, 0.015]),
        ("EMA_SLOPE_BARS",[3, 4, 5, 6, 7, 8, 10]),
        ("MAX_HOLD_BARS", [15, 20, 25, 30, 35, 40, 50]),
    ]

    for param_name, values in params_to_test:
        print(f"\n  {param_name}:")
        print(f"    {'Value':>8} {'PF':>5} {'T':>5} {'DD':>5} {'D¥':>7} {'WR':>5} | {'Δ':>5}")
        print("    " + "-"*55)
        for v in values:
            c = make_cfg(**{param_name: v})
            # Need to recompute indicators if H4_EMA or ATR params change
            m, _ = run(ind, mask, c, td)
            if m:
                delta = m['pf'] - m_base['pf']
                marker = " <--BASE" if abs(v - getattr(cfg, param_name)) < 0.001 else ""
                print(f"    {v:8.3f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
                      f"{m['daily_jpy']:7.0f} {m['win_rate']:5.1f} | {delta:+5.2f}{marker}")

    # ================================================================
    # 2. Regime Parameter Sensitivity
    # ================================================================
    print("\n" + "="*80)
    print("2. REGIME SENSITIVITY")
    print("="*80)

    regime_tests = [
        ("D1_Min_Slope", [0.0, 0.0003, 0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005]),
        ("W1_Min_Sep",   [0.0, 0.002, 0.003, 0.005, 0.007, 0.01]),
        ("D1_Slope_Bars",[3, 5, 7, 10]),
    ]

    for rname, rvals in regime_tests:
        print(f"\n  {rname}:")
        print(f"    {'Value':>8} {'PF':>5} {'T':>5} {'DD':>5} {'D¥':>7} | Block%")
        print("    " + "-"*50)
        for v in rvals:
            rp = {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_sep": 0.005}
            if rname == "D1_Min_Slope": rp["d1_min_slope"] = v
            elif rname == "W1_Min_Sep": rp["w1_min_sep"] = v
            elif rname == "D1_Slope_Bars": rp["d1_slope_bars"] = v
            mk = compute_mask(ind, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"])
            m, _ = run(ind, mk, cfg, td)
            blk = mk.sum()/len(mk)*100
            if m:
                marker = " <--BASE" if abs(v - (0.002 if rname=="D1_Min_Slope" else 0.005 if rname=="W1_Min_Sep" else 5)) < 0.0001 else ""
                print(f"    {v:8.4f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
                      f"{m['daily_jpy']:7.0f} | {blk:5.1f}%{marker}")

    # ================================================================
    # 3. Recent Period Analysis (2024+)
    # ================================================================
    print("\n" + "="*80)
    print("3. RECENT PERIOD (2024+) ANALYSIS")
    print("="*80)

    h4_2024 = h4[h4.index >= "2023-01-01"].copy()  # Need warmup before 2024
    ind_2024 = precompute_indicators(h4_2024, cfg)
    mask_2024 = compute_mask(ind_2024)
    cutoff = pd.Timestamp("2024-01-01")

    for risk, maxlot in [(0.5, 0.25), (1.0, 0.50), (1.5, 0.75), (2.0, 1.0), (3.0, 1.5), (5.0, 2.5)]:
        c = make_cfg(RiskPct=risk, MaxLot=maxlot)
        mod = list(ind_2024); aa=ind_2024[13].copy(); aa[mask_2024]=999999; mod[13]=aa
        trades, _, _ = backtest_goldalpha(*tuple(mod), cfg=c)
        recent = [t for t in trades if t["open_time"] >= cutoff]
        recent_days = max(1, (h4_2024.index[-1] - cutoff).days)
        m = calc_metrics(recent, c.INITIAL_BALANCE, recent_days)
        if m:
            hit = "YES" if m["daily_jpy"] >= 5000 else ""
            print(f"  Risk={risk:.1f}% ML={maxlot:.2f}: PF={m['pf']:.2f} T={m['n_trades']} "
                  f"DD={m['max_dd']:.1f}% D¥={m['daily_jpy']:.0f} WR={m['win_rate']:.1f}% {hit}")

    # ================================================================
    # 4. Robustness: Feature ablation
    # ================================================================
    print("\n" + "="*80)
    print("4. FEATURE ABLATION (remove one feature at a time)")
    print("="*80)

    ablations = [
        ("Full v27",          {}),
        ("-D1 Regime",        {"D1_Min_Slope": 0.0}),
        ("-W1 Sep",           {"W1_Min_Sep": 0.0}),
        ("-EMA Slope",        {"USE_EMA_SLOPE": False}),
        ("-Time Decay",       {"USE_TIME_DECAY": False}),
        ("-D1Reg -W1Sep",     {"D1_Min_Slope": 0.0, "W1_Min_Sep": 0.0}),
        ("-All Filters (v12)",{"D1_Min_Slope": 0.0, "W1_Min_Sep": 0.0, "USE_EMA_SLOPE": False, "USE_TIME_DECAY": False}),
    ]

    print(f"  {'Config':>22} {'PF':>5} {'T':>5} {'DD':>5} {'D¥':>7} {'WR':>5}")
    print("  " + "-"*60)
    for name, overrides in ablations:
        rp = {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_sep": 0.005}
        cfg_params = {}
        for k, v in overrides.items():
            if k in ("D1_Min_Slope",):
                rp["d1_min_slope"] = v
            elif k in ("W1_Min_Sep",):
                rp["w1_min_sep"] = v
            else:
                cfg_params[k] = v
        c = make_cfg(**cfg_params)
        mk = compute_mask(ind, rp["d1_slope_bars"], rp["d1_min_slope"], rp["w1_min_sep"])
        m, _ = run(ind, mk, c, td)
        if m:
            print(f"  {name:>22} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
                  f"{m['daily_jpy']:7.0f} {m['win_rate']:5.1f}")

    # ================================================================
    # 5. Combined perturbation (worst-case)
    # ================================================================
    print("\n" + "="*80)
    print("5. COMBINED PERTURBATION (all params ±10%)")
    print("="*80)

    import random
    random.seed(42)
    perturbations = []
    base_params = {
        "SL_ATR_Mult": 3.0, "Trail_ATR": 3.5, "BE_ATR": 1.0,
        "EMA_Zone_ATR": 0.30, "BodyRatio": 0.32, "ATR_Filter": 0.30
    }
    for trial in range(50):
        params = {}
        for k, v in base_params.items():
            factor = 1 + random.uniform(-0.15, 0.15)
            params[k] = round(v * factor, 3)
        c = make_cfg(**params)
        m, _ = run(ind, mask, c, td)
        if m:
            perturbations.append((params, m))

    if perturbations:
        pfs = [m["pf"] for _, m in perturbations]
        trades = [m["n_trades"] for _, m in perturbations]
        dds = [m["max_dd"] for _, m in perturbations]
        print(f"  50 random ±15% perturbations:")
        print(f"  PF:     min={min(pfs):.2f} max={max(pfs):.2f} mean={np.mean(pfs):.2f} std={np.std(pfs):.2f}")
        print(f"  Trades: min={min(trades)} max={max(trades)} mean={np.mean(trades):.0f}")
        print(f"  DD:     min={min(dds):.1f}% max={max(dds):.1f}% mean={np.mean(dds):.1f}%")
        print(f"  PF>1.5: {sum(1 for p in pfs if p>1.5)}/50 ({sum(1 for p in pfs if p>1.5)/50*100:.0f}%)")
        print(f"  PF>1.0: {sum(1 for p in pfs if p>1.0)}/50 ({sum(1 for p in pfs if p>1.0)/50*100:.0f}%)")

        # Worst and best
        worst = min(perturbations, key=lambda x: x[1]["pf"])
        best = max(perturbations, key=lambda x: x[1]["pf"])
        print(f"\n  Worst: PF={worst[1]['pf']:.2f} T={worst[1]['n_trades']} | {worst[0]}")
        print(f"  Best:  PF={best[1]['pf']:.2f} T={best[1]['n_trades']} | {best[0]}")

    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("="*80)
    print("DONE")
    log.close()


if __name__ == "__main__":
    main()
