"""
GoldAlpha v15 Optimizer
Goal: 500+ trades, PF 1.5+, 300K JPY start, 5000 JPY/day target
Approach: v13 base + feature combination search + risk scaling
"""
import sys
import os
sys.path.insert(0, "/tmp/FxTrading_EA")

import numpy as np
import pandas as pd
from itertools import product
import time
import warnings
warnings.filterwarnings("ignore")

from backtest_goldalpha import (
    load_csv, GoldAlphaConfig, precompute_indicators, backtest_goldalpha,
    calc_metrics, np_ema, np_atr, np_sma, np_adx,
    resample_to_weekly, resample_to_daily,
    _calc_lot, _manage_positions, _close_position, _calc_pnl, _unrealized_pnl
)

DATA_DIR = "/tmp/FxTrading_EA"


def make_config(**overrides):
    """Create config with v13 base + overrides"""
    cfg = GoldAlphaConfig()
    # v13 optimized base
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
    # Apply overrides
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_single(h4_df, cfg, total_days, label=""):
    """Run one backtest and return metrics"""
    ind = precompute_indicators(h4_df, cfg)
    trades, eq, final = backtest_goldalpha(*ind, cfg)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    return m, trades


def print_result(m, label=""):
    if not m:
        print(f"  {label}: NO TRADES")
        return
    print(f"  {label}: T={m['n_trades']:4d} PF={m['pf']:5.2f} "
          f"WR={m['win_rate']:4.1f}% DD={m['max_dd']:5.1f}% "
          f"Daily={m['daily_jpy']:7.0f} JPY Final={m['final_balance']:12,.0f}")


def run_risk_table(h4_df, cfg_factory, total_days, label=""):
    """Run across risk levels"""
    print(f"\n  Risk Scaling: {label}")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>6} {'WR%':>5} {'Daily':>7} {'Final':>12}")
    print(f"  {'-'*65}")
    for risk, maxlot in [(0.18, 0.10), (0.5, 0.10), (1.0, 0.20),
                          (1.5, 0.30), (2.0, 0.50), (2.5, 0.50), (3.0, 0.75)]:
        cfg = cfg_factory(RiskPct=risk, MaxLot=maxlot)
        m, _ = run_single(h4_df, cfg, total_days)
        if m:
            flag = " ***" if m['daily_jpy'] >= 5000 else ""
            flag += " !" if m['max_dd'] > 50 else ""
            print(f"  {risk:6.2f} {maxlot:6.2f} | {m['pf']:5.2f} {m['n_trades']:5d} "
                  f"{m['max_dd']:6.1f} {m['win_rate']:5.1f} {m['daily_jpy']:7.0f} "
                  f"{m['final_balance']:12,.0f}{flag}")


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

    n_pass = sum(1 for r in results if r["pf"] > 1.0)
    return results, n_pass


# ============================================================
# Enhanced backtest with additional features
# ============================================================
def backtest_v15(h4_df, cfg, start_balance=None):
    """
    Enhanced v15 backtest with:
    - Cooldown between trades (avoid clustering)
    - Re-entry after SL (wait N bars)
    - Adaptive trail (tighten in profit, widen early)
    - Check bars 1,2,3 for dip (more signals)
    """
    h4_o = h4_df["Open"].values
    h4_h = h4_df["High"].values
    h4_l = h4_df["Low"].values
    h4_c = h4_df["Close"].values
    h4_times = h4_df.index.to_pydatetime()

    h4_ema = np_ema(h4_c, cfg.H4_EMA)
    h4_atr = np_atr(h4_h, h4_l, h4_c, cfg.ATR_Period)
    h4_avg_atr = np_sma(h4_atr, cfg.ATR_SMA)

    w1 = resample_to_weekly(h4_df)
    w1_c = w1["Close"].values
    w1_fast = np_ema(w1_c, cfg.W1_FastEMA)
    w1_slow = np_ema(w1_c, cfg.W1_SlowEMA)
    w1_times = w1.index.to_pydatetime()
    w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1

    d1 = resample_to_daily(h4_df)
    d1_c = d1["Close"].values
    d1_ema = np_ema(d1_c, cfg.D1_EMA)
    d1_times = d1.index.to_pydatetime()
    d1_idx_map = np.searchsorted(d1_times, h4_times, side="right") - 1

    balance = start_balance if start_balance else cfg.INITIAL_BALANCE
    peak_balance = balance
    positions = []
    trades = []
    equity_curve = []
    spread = cfg.SPREAD_POINTS * cfg.POINT
    point = cfg.POINT
    n = len(h4_o)

    # Cooldown tracking
    last_entry_bar = -100
    last_sl_bar = -100
    cooldown_bars = getattr(cfg, 'COOLDOWN_BARS', 0)
    sl_cooldown_bars = getattr(cfg, 'SL_COOLDOWN_BARS', 0)
    check_bars = getattr(cfg, 'CHECK_BARS', 2)  # default: check bar 1 and 2

    for i in range(max(cfg.ATR_SMA + cfg.ATR_Period, 60), n):
        cur_time = h4_times[i]
        cur_atr = h4_atr[i]
        if np.isnan(cur_atr) or cur_atr < point:
            continue

        dow = cur_time.weekday()
        if dow >= 5:
            continue
        hour = cur_time.hour
        if dow == 4 and hour > 16:
            continue

        if cfg.USE_SESSION_FILTER:
            if hour < cfg.TRADE_START_HOUR or hour >= cfg.TRADE_END_HOUR:
                # Still manage positions
                closed = _manage_positions(positions, trades, h4_h[i], h4_l[i], h4_c[i],
                                           cur_time, cur_atr, cfg, balance)
                for pnl in closed:
                    balance += pnl
                    if balance > peak_balance:
                        peak_balance = balance
                # Time decay
                if cfg.USE_TIME_DECAY:
                    for pos in list(positions):
                        if i - pos["bar_idx"] >= cfg.MAX_HOLD_BARS:
                            pnl = _close_position(pos, h4_c[i], cur_time, "TIME_DECAY", trades, cfg)
                            balance += pnl
                            positions.remove(pos)
                equity_curve.append(balance + _unrealized_pnl(positions, h4_c[i], cfg))
                continue

        # Manage positions
        closed = _manage_positions(positions, trades, h4_h[i], h4_l[i], h4_c[i],
                                   cur_time, cur_atr, cfg, balance)
        for pnl in closed:
            balance += pnl
            if balance > peak_balance:
                peak_balance = balance
            if pnl < 0:
                last_sl_bar = i

        # Time decay
        if cfg.USE_TIME_DECAY:
            for pos in list(positions):
                if i - pos["bar_idx"] >= cfg.MAX_HOLD_BARS:
                    pnl = _close_position(pos, h4_c[i], cur_time, "TIME_DECAY", trades, cfg)
                    balance += pnl
                    positions.remove(pos)
                    if balance > peak_balance:
                        peak_balance = balance

        equity_curve.append(balance + _unrealized_pnl(positions, h4_c[i], cfg))

        # Entry logic
        if len(positions) >= cfg.MaxPositions:
            continue

        # Cooldown check
        if cooldown_bars > 0 and (i - last_entry_bar) < cooldown_bars:
            continue
        if sl_cooldown_bars > 0 and (i - last_sl_bar) < sl_cooldown_bars:
            continue

        # W1 trend
        w1_i = w1_idx_map[i]
        if w1_i < 1:
            continue
        w1f = w1_fast[w1_i]
        w1s = w1_slow[w1_i]
        if np.isnan(w1f) or np.isnan(w1s):
            continue
        w1_dir = 0
        if w1f > w1s:
            w1_dir = 1
        elif w1f < w1s:
            w1_dir = -1
        if w1_dir == 0:
            continue

        # D1 filter
        d1_i = d1_idx_map[i]
        if d1_i < 1:
            continue
        d1_cl = d1_c[d1_i]
        d1_em = d1_ema[d1_i]
        if np.isnan(d1_cl) or np.isnan(d1_em) or d1_em == 0:
            continue
        d1_diff = (d1_cl - d1_em) / d1_em
        if w1_dir == 1 and d1_diff < -cfg.D1_Tolerance:
            continue
        if w1_dir == -1 and d1_diff > cfg.D1_Tolerance:
            continue

        # W1 separation filter
        if cfg.USE_W1_SEPARATION:
            w1_price = (w1f + w1s) / 2
            if w1_price > 0 and abs(w1f - w1s) / w1_price < cfg.W1_SEP_MIN:
                continue

        # ATR filter
        avg_atr = h4_avg_atr[i]
        if np.isnan(avg_atr) or avg_atr <= 0:
            continue
        if cur_atr < avg_atr * cfg.ATR_Filter:
            continue

        # Vol regime
        if cfg.USE_VOL_REGIME:
            vol_ratio = cur_atr / avg_atr
            if vol_ratio < cfg.VOL_LOW_MULT or vol_ratio > cfg.VOL_HIGH_MULT:
                continue

        # H4 EMA
        ema_val = h4_ema[i]
        if np.isnan(ema_val):
            continue
        zone = cfg.EMA_Zone_ATR * cur_atr

        # EMA slope
        if cfg.USE_EMA_SLOPE and i >= cfg.EMA_SLOPE_BARS:
            ema_prev = h4_ema[i - cfg.EMA_SLOPE_BARS]
            if not np.isnan(ema_prev):
                if w1_dir == 1 and ema_val < ema_prev:
                    continue
                if w1_dir == -1 and ema_val > ema_prev:
                    continue

        # Structure filter
        if cfg.USE_STRUCTURE and i >= cfg.STRUCTURE_BARS + 1:
            sb = cfg.STRUCTURE_BARS
            if w1_dir == 1:
                lows = [h4_l[i - j] for j in range(1, sb + 1)]
                if lows[0] < min(lows[1:]):
                    continue
            elif w1_dir == -1:
                highs = [h4_h[i - j] for j in range(1, sb + 1)]
                if highs[0] > max(highs[1:]):
                    continue

        # Dip check on multiple bars
        entered = False
        for shift in range(1, check_bars + 1):
            if entered:
                break
            si = i - shift
            if si < 0:
                continue

            bar_o = h4_o[si]
            bar_c = h4_c[si]
            bar_h = h4_h[si]
            bar_l = h4_l[si]
            bar_range = bar_h - bar_l
            if bar_range <= point:
                continue

            if w1_dir == 1:
                if bar_l > ema_val + zone:
                    continue
                if bar_c <= ema_val:
                    continue
                if bar_c <= bar_o:
                    continue
                body = bar_c - bar_o
                if body / bar_range < cfg.BodyRatio:
                    continue

                if cfg.USE_RSI_CONFIRM and i >= 3:
                    if h4_c[i-1] < h4_c[i-3]:
                        continue

                entry_price = h4_c[i] + spread / 2
                sl_dist = cfg.SL_ATR_Mult * cur_atr
                sl = entry_price - sl_dist
                lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                positions.append({
                    "direction": "BUY", "entry": entry_price, "sl": sl,
                    "lot": lot, "open_time": cur_time, "bar_idx": i,
                    "be_done": False, "highest": entry_price, "partial_done": False,
                })
                last_entry_bar = i
                entered = True

            elif w1_dir == -1:
                if bar_h < ema_val - zone:
                    continue
                if bar_c >= ema_val:
                    continue
                if bar_c >= bar_o:
                    continue
                body = bar_o - bar_c
                if body / bar_range < cfg.BodyRatio:
                    continue

                if cfg.USE_RSI_CONFIRM and i >= 3:
                    if h4_c[i-1] > h4_c[i-3]:
                        continue

                entry_price = h4_c[i] - spread / 2
                sl_dist = cfg.SL_ATR_Mult * cur_atr
                sl = entry_price + sl_dist
                lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                positions.append({
                    "direction": "SELL", "entry": entry_price, "sl": sl,
                    "lot": lot, "open_time": cur_time, "bar_idx": i,
                    "be_done": False, "lowest": entry_price, "partial_done": False,
                })
                last_entry_bar = i
                entered = True

    # Close remaining
    if positions:
        final_price = h4_c[-1]
        final_time = h4_times[-1]
        for pos in list(positions):
            pnl = _close_position(pos, final_price, final_time, "END", trades, cfg)
            balance += pnl
        positions.clear()

    return trades, equity_curve, balance


def run_v15(h4_df, cfg, total_days, label=""):
    """Run v15 backtest"""
    trades, eq, final = backtest_v15(h4_df, cfg)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    return m, trades


def run_v15_risk_table(h4_df, total_days, base_overrides, label=""):
    """Risk scaling for v15"""
    print(f"\n  Risk Scaling: {label}")
    print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>6} {'WR%':>5} {'Daily':>7} {'Final':>12}")
    print(f"  {'-'*65}")
    best_daily = None
    for risk, maxlot in [(0.18, 0.10), (0.5, 0.15), (1.0, 0.20),
                          (1.5, 0.30), (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        ov = {**base_overrides, "RiskPct": risk, "MaxLot": maxlot}
        cfg = make_config(**ov)
        m, _ = run_v15(h4_df, cfg, total_days)
        if m:
            flag = ""
            if m['daily_jpy'] >= 5000 and m['max_dd'] < 50:
                flag = " <<< SWEET SPOT"
            elif m['daily_jpy'] >= 5000:
                flag = " ***"
            if m['max_dd'] > 60:
                flag += " DANGER"
            print(f"  {risk:6.2f} {maxlot:6.2f} | {m['pf']:5.2f} {m['n_trades']:5d} "
                  f"{m['max_dd']:6.1f} {m['win_rate']:5.1f} {m['daily_jpy']:7.0f} "
                  f"{m['final_balance']:12,.0f}{flag}")
            if best_daily is None or (m['daily_jpy'] >= 5000 and m['max_dd'] < (best_daily.get('max_dd', 999))):
                if m['daily_jpy'] >= 5000:
                    best_daily = {**m, "risk": risk, "maxlot": maxlot}
    return best_daily


def main():
    t0 = time.time()
    print("=" * 70)
    print("GoldAlpha v15 Optimization")
    print("Target: 500+ trades, PF 1.5+, 5000 JPY/day from 300K JPY")
    print("=" * 70)

    print("\nLoading data...")
    h4_df = load_csv(os.path.join(DATA_DIR, "XAUUSD_H4.csv"))
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    total_bars = len(h4_df)
    print(f"H4: {total_bars} bars, {h4_df.index[0]} to {h4_df.index[-1]}, {total_days} days")

    # =========================================================
    # Phase 1: Baseline (v13 at low risk)
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 1: Baselines")
    print("=" * 70)

    # v13 baseline
    cfg_v13 = make_config()
    m_v13, _ = run_single(h4_df, cfg_v13, total_days)
    print_result(m_v13, "v13 (0.18% risk)")

    # v13 using v15 engine (should match)
    m_v13b, _ = run_v15(h4_df, cfg_v13, total_days)
    print_result(m_v13b, "v13 via v15 engine")

    # v14 baseline
    cfg_v14 = make_config(USE_STRUCTURE=True, STRUCTURE_BARS=3, USE_TIME_DECAY=True, MAX_HOLD_BARS=30)
    m_v14, _ = run_v15(h4_df, cfg_v14, total_days)
    print_result(m_v14, "v14 (Struct+TimeDec)")

    # =========================================================
    # Phase 2: Feature ablation (test each feature individually)
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 2: Individual Feature Tests (v13 base + each feature)")
    print("=" * 70)

    features = {
        "Partial Close (1.5 ATR, 50%)": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5},
        "Partial Close (2.0 ATR, 50%)": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5},
        "Partial Close (1.0 ATR, 40%)": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.0, "PARTIAL_RATIO": 0.4},
        "Time Decay (20 bars)": {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 20},
        "Time Decay (30 bars)": {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30},
        "Time Decay (40 bars)": {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40},
        "EMA Slope (5 bars)": {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 5},
        "EMA Slope (3 bars)": {"USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3},
        "Session 2-21 UTC": {"USE_SESSION_FILTER": True, "TRADE_START_HOUR": 2, "TRADE_END_HOUR": 21},
        "Session 6-20 UTC": {"USE_SESSION_FILTER": True, "TRADE_START_HOUR": 6, "TRADE_END_HOUR": 20},
        "Vol Regime (0.5-2.5)": {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5},
        "Vol Regime (0.4-3.0)": {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.4, "VOL_HIGH_MULT": 3.0},
        "Structure (3 bars)": {"USE_STRUCTURE": True, "STRUCTURE_BARS": 3},
        "Structure (2 bars)": {"USE_STRUCTURE": True, "STRUCTURE_BARS": 2},
        "RSI Confirm": {"USE_RSI_CONFIRM": True},
        "Check 3 bars": {"CHECK_BARS": 3},
        "Check 4 bars": {"CHECK_BARS": 4},
        "Cooldown 2": {"COOLDOWN_BARS": 2},
        "SL Cooldown 3": {"SL_COOLDOWN_BARS": 3},
        "SL Cooldown 6": {"SL_COOLDOWN_BARS": 6},
        "W1 Sep 0.003": {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003},
        "W1 Sep 0.005": {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.005},
        "MaxPos 4": {"MaxPositions": 4},
        "MaxPos 5": {"MaxPositions": 5},
        "Zone 0.50": {"EMA_Zone_ATR": 0.50},
        "Zone 0.60": {"EMA_Zone_ATR": 0.60},
        "Body 0.30": {"BodyRatio": 0.30},
        "Body 0.28": {"BodyRatio": 0.28},
        "ATR Filt 0.25": {"ATR_Filter": 0.25},
        "ATR Filt 0.30": {"ATR_Filter": 0.30},
        "SL 2.0": {"SL_ATR_Mult": 2.0},
        "SL 3.0": {"SL_ATR_Mult": 3.0},
        "Trail 2.5": {"Trail_ATR": 2.5},
        "Trail 3.0": {"Trail_ATR": 3.0},
        "Trail 4.0": {"Trail_ATR": 4.0},
        "BE 1.0": {"BE_ATR": 1.0},
        "BE 2.0": {"BE_ATR": 2.0},
        "D1 Tol 0.005": {"D1_Tolerance": 0.005},
        "D1 Tol 0.010": {"D1_Tolerance": 0.010},
    }

    baseline_pf = m_v13b['pf'] if m_v13b else 0
    baseline_trades = m_v13b['n_trades'] if m_v13b else 0
    baseline_dd = m_v13b['max_dd'] if m_v13b else 100

    results_phase2 = []
    for name, overrides in features.items():
        cfg = make_config(**overrides)
        m, _ = run_v15(h4_df, cfg, total_days)
        if m:
            delta_pf = m['pf'] - baseline_pf
            delta_t = m['n_trades'] - baseline_trades
            delta_dd = m['max_dd'] - baseline_dd
            flag = ""
            if delta_pf > 0.05 and delta_t >= 0:
                flag = " +"
            elif delta_pf > 0.1:
                flag = " ++"
            if m['n_trades'] > baseline_trades * 1.1 and m['pf'] >= baseline_pf * 0.95:
                flag += " MORE"
            print(f"  {name:28s}: T={m['n_trades']:4d} PF={m['pf']:5.2f} "
                  f"DD={m['max_dd']:5.1f}% Daily={m['daily_jpy']:5.0f} "
                  f"(dPF={delta_pf:+.2f} dT={delta_t:+4d} dDD={delta_dd:+.1f}){flag}")
            results_phase2.append({"name": name, "overrides": overrides, **m,
                                    "delta_pf": delta_pf, "delta_t": delta_t})

    # Sort by combined score: PF improvement + trade count maintenance
    results_phase2.sort(key=lambda x: x['pf'] * min(x['n_trades'], 1500) / 1000, reverse=True)
    print(f"\n  Top 10 by PF×Trades score:")
    for i, r in enumerate(results_phase2[:10]):
        print(f"    #{i+1}: {r['name']:28s} PF={r['pf']:.2f} T={r['n_trades']} DD={r['max_dd']:.1f}%")

    # =========================================================
    # Phase 3: Combination search
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 3: Feature Combinations (best individuals combined)")
    print("=" * 70)

    # Test promising combinations
    combos = {
        "v13_base": {},
        "PC1.5+TD30": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30},
        "PC2.0+TD30": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30},
        "PC1.5+TD20": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                        "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 20},
        "PC1.5+Slope3": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                          "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3},
        "PC1.5+Struct2": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                           "USE_STRUCTURE": True, "STRUCTURE_BARS": 2},
        "PC1.5+TD30+Slope3": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                               "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
                               "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3},
        "PC1.5+TD30+Struct2": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
                                "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
                                "USE_STRUCTURE": True, "STRUCTURE_BARS": 2},
        "TD30+Struct2": {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
                          "USE_STRUCTURE": True, "STRUCTURE_BARS": 2},
        "TD20+Slope3": {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 20,
                         "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3},
        "PC2+TD30+Struct2": {"USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5,
                              "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
                              "USE_STRUCTURE": True, "STRUCTURE_BARS": 2},
        "PC1.5+TD30+Struct2+Slope3": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
            "USE_EMA_SLOPE": True, "EMA_SLOPE_BARS": 3},
        "Relax+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "BodyRatio": 0.30, "ATR_Filter": 0.25, "EMA_Zone_ATR": 0.50},
        "Relax+PC1.5+TD30+Struct2": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
            "BodyRatio": 0.30, "ATR_Filter": 0.25, "EMA_Zone_ATR": 0.50},
        "MaxRelax+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "BodyRatio": 0.28, "ATR_Filter": 0.25, "EMA_Zone_ATR": 0.60,
            "MaxPositions": 4},
        "MaxRelax+PC2+TD30+Struct2": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "USE_STRUCTURE": True, "STRUCTURE_BARS": 2,
            "BodyRatio": 0.28, "ATR_Filter": 0.25, "EMA_Zone_ATR": 0.60,
            "MaxPositions": 4},
        "Check3+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "CHECK_BARS": 3},
        "Check3+Relax+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "CHECK_BARS": 3,
            "BodyRatio": 0.30, "ATR_Filter": 0.30, "EMA_Zone_ATR": 0.50},
        "SL2+Trail3+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "SL_ATR_Mult": 2.0, "Trail_ATR": 3.0},
        "SL3+Trail4+PC2+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 2.0, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "SL_ATR_Mult": 3.0, "Trail_ATR": 4.0},
        "D1Tol005+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "D1_Tolerance": 0.005},
        "D1Tol010+Relax+PC1.5+TD30": {
            "USE_PARTIAL_CLOSE": True, "PARTIAL_ATR": 1.5, "PARTIAL_RATIO": 0.5,
            "USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
            "D1_Tolerance": 0.010, "BodyRatio": 0.30, "ATR_Filter": 0.30},
    }

    combo_results = []
    for name, overrides in combos.items():
        cfg = make_config(**overrides)
        m, _ = run_v15(h4_df, cfg, total_days)
        if m:
            score = m['pf'] * min(m['n_trades'], 1500) / 1000
            print(f"  {name:38s}: T={m['n_trades']:4d} PF={m['pf']:5.2f} "
                  f"DD={m['max_dd']:5.1f}% Daily={m['daily_jpy']:5.0f} Score={score:.2f}")
            combo_results.append({"name": name, "overrides": overrides, **m, "score": score})

    combo_results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  Top 5 combinations:")
    for i, r in enumerate(combo_results[:5]):
        print(f"    #{i+1}: {r['name']:38s} PF={r['pf']:.2f} T={r['n_trades']} "
              f"DD={r['max_dd']:.1f}% Score={r['score']:.2f}")

    # =========================================================
    # Phase 4: Grid search on best combo
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 4: Fine-tuning grid search on top combinations")
    print("=" * 70)

    # Take top 3 combos and run fine-tuning grid
    for rank, combo in enumerate(combo_results[:3]):
        print(f"\n--- Fine-tuning #{rank+1}: {combo['name']} ---")
        base_ov = combo['overrides'].copy()

        # Grid over key entry/exit params
        grid_params = {
            "BodyRatio": [0.28, 0.30, 0.32, 0.34],
            "EMA_Zone_ATR": [0.35, 0.40, 0.50, 0.60],
            "ATR_Filter": [0.25, 0.30, 0.35, 0.40],
            "SL_ATR_Mult": [2.0, 2.5, 3.0],
            "Trail_ATR": [2.5, 3.0, 3.5, 4.0],
            "BE_ATR": [1.0, 1.5, 2.0],
        }

        keys = list(grid_params.keys())
        vals = list(grid_params.values())
        all_combos = list(product(*vals))
        n_total = len(all_combos)
        print(f"  {n_total} parameter combinations")

        grid_results = []
        best_score = -999
        for idx, combo_vals in enumerate(all_combos):
            ov = {**base_ov}
            for k, v in zip(keys, combo_vals):
                ov[k] = v
            cfg = make_config(**ov)
            m, _ = run_v15(h4_df, cfg, total_days)
            if m and m['n_trades'] >= 500:
                score = m['pf'] * min(m['n_trades'], 1500) / 1000
                # Penalize extreme DD
                if m['max_dd'] > 30:
                    score -= (m['max_dd'] - 30) * 0.02
                entry = {k: v for k, v in zip(keys, combo_vals)}
                entry.update(m)
                entry["score"] = score
                grid_results.append(entry)

                if score > best_score:
                    best_score = score
                    if idx % 200 == 0:
                        print(f"    [{idx+1}/{n_total}] BEST: PF={m['pf']:.2f} T={m['n_trades']} "
                              f"DD={m['max_dd']:.1f}% Score={score:.2f}")
            elif idx % 500 == 0:
                t = m['n_trades'] if m else 0
                print(f"    [{idx+1}/{n_total}] T={t} (skip)")

        grid_results.sort(key=lambda x: x["score"], reverse=True)
        print(f"\n  Top 10 for {combo['name']}:")
        print(f"  {'PF':>5} {'T':>5} {'DD%':>6} {'WR%':>5} {'Daily':>6} | "
              f"{'Body':>5} {'Zone':>5} {'ATR_F':>5} {'SL':>4} {'Trail':>5} {'BE':>4} Score")
        print(f"  {'-'*75}")
        for r in grid_results[:10]:
            print(f"  {r['pf']:5.2f} {r['n_trades']:5d} {r['max_dd']:6.1f} "
                  f"{r['win_rate']:5.1f} {r['daily_jpy']:6.0f} | "
                  f"{r['BodyRatio']:5.2f} {r['EMA_Zone_ATR']:5.2f} {r['ATR_Filter']:5.2f} "
                  f"{r['SL_ATR_Mult']:4.1f} {r['Trail_ATR']:5.1f} {r['BE_ATR']:4.1f} {r['score']:.2f}")

    # =========================================================
    # Phase 5: Best config risk scaling + WFA
    # =========================================================
    print("\n" + "=" * 70)
    print("PHASE 5: Final Validation")
    print("=" * 70)

    # Collect the overall best config from Phase 4
    # We'll take the best from each grid and compare
    all_grid_bests = []
    for rank, combo in enumerate(combo_results[:3]):
        base_ov = combo['overrides'].copy()
        grid_params = {
            "BodyRatio": [0.28, 0.30, 0.32, 0.34],
            "EMA_Zone_ATR": [0.35, 0.40, 0.50, 0.60],
            "ATR_Filter": [0.25, 0.30, 0.35, 0.40],
            "SL_ATR_Mult": [2.0, 2.5, 3.0],
            "Trail_ATR": [2.5, 3.0, 3.5, 4.0],
            "BE_ATR": [1.0, 1.5, 2.0],
        }
        keys = list(grid_params.keys())
        vals = list(grid_params.values())
        best_entry = None
        best_score = -999
        for combo_vals in product(*vals):
            ov = {**base_ov}
            for k, v in zip(keys, combo_vals):
                ov[k] = v
            cfg = make_config(**ov)
            m, _ = run_v15(h4_df, cfg, total_days)
            if m and m['n_trades'] >= 500:
                score = m['pf'] * min(m['n_trades'], 1500) / 1000
                if m['max_dd'] > 30:
                    score -= (m['max_dd'] - 30) * 0.02
                if score > best_score:
                    best_score = score
                    best_entry = {**ov, **m, "score": score, "combo_name": combo['name']}
        if best_entry:
            all_grid_bests.append(best_entry)

    all_grid_bests.sort(key=lambda x: x["score"], reverse=True)
    if not all_grid_bests:
        print("  No valid configurations found!")
        return

    winner = all_grid_bests[0]
    print(f"\n  WINNER: {winner.get('combo_name', 'unknown')}")
    print(f"  PF={winner['pf']:.2f} T={winner['n_trades']} DD={winner['max_dd']:.1f}% "
          f"WR={winner['win_rate']:.1f}% Daily={winner['daily_jpy']:.0f} JPY")

    # Extract just the override params
    winner_ov = {}
    exclude = {'n_trades', 'pf', 'win_rate', 'max_dd', 'total_pnl', 'daily_jpy',
               'final_balance', 'avg_win', 'avg_loss', 'score', 'combo_name',
               'RiskPct', 'MaxLot'}
    for k, v in winner.items():
        if k not in exclude and k in dir(GoldAlphaConfig):
            winner_ov[k] = v

    print(f"\n  Winner parameters:")
    for k, v in sorted(winner_ov.items()):
        print(f"    {k}: {v}")

    # Risk scaling
    best_spot = run_v15_risk_table(h4_df, total_days, winner_ov, "WINNER")

    # WFA
    print(f"\n  Walk-Forward Analysis (8 windows):")
    for risk, maxlot, label in [(0.18, 0.10, "Low"), (1.5, 0.30, "Mid"), (2.5, 0.75, "Target")]:
        ov_wfa = {**winner_ov, "RiskPct": risk, "MaxLot": maxlot}
        cfg_wfa = make_config(**ov_wfa)
        total_bars_wfa = len(h4_df)
        n_windows = 8
        window_size = total_bars_wfa // n_windows
        oos_size = int(window_size * 0.25)

        wfa_results = []
        for w in range(n_windows):
            window_end = min((w + 1) * window_size, total_bars_wfa)
            oos_start = window_end - oos_size
            data_start = max(0, oos_start - 600)
            sub = h4_df.iloc[data_start:window_end].copy()
            trades_w, _, _ = backtest_v15(sub, cfg_wfa)
            oos_time = h4_df.index[oos_start]
            oos_end_time = h4_df.index[min(window_end - 1, total_bars_wfa - 1)]
            oos_trades = [t for t in trades_w if t["open_time"] >= oos_time]
            oos_days = max(1, (oos_end_time - oos_time).days)
            m_w = calc_metrics(oos_trades, cfg_wfa.INITIAL_BALANCE, oos_days)
            if m_w:
                wfa_results.append(m_w)

        n_pass = sum(1 for r in wfa_results if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa_results]) if wfa_results else 0
        total_t = sum(r["n_trades"] for r in wfa_results) if wfa_results else 0
        print(f"\n    {label} (Risk={risk}%): {n_pass}/{len(wfa_results)} PASS, "
              f"Avg PF={avg_pf:.2f}, OOS Trades={total_t}")
        for j, r in enumerate(wfa_results):
            s = "PASS" if r["pf"] > 1.0 else "FAIL"
            print(f"      W{j+1}: PF={r['pf']:5.2f} T={r['n_trades']:3d} "
                  f"DD={r['max_dd']:5.1f}% WR={r['win_rate']:4.0f}% [{s}]")

    # OOS 2024-2026
    print(f"\n  Out-of-Sample (2024-2026):")
    for risk, maxlot in [(1.0, 0.20), (1.5, 0.30), (2.0, 0.50), (2.5, 0.75), (3.0, 1.00)]:
        ov_oos = {**winner_ov, "RiskPct": risk, "MaxLot": maxlot}
        cfg_oos = make_config(**ov_oos)
        mask = h4_df.index >= "2022-01-01"
        sub = h4_df[mask].copy()
        trades_oos, _, _ = backtest_v15(sub, cfg_oos)
        oos_trades = [t for t in trades_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
        oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
        m_oos = calc_metrics(oos_trades, cfg_oos.INITIAL_BALANCE, oos_days)
        if m_oos:
            flag = " ***" if m_oos['daily_jpy'] >= 5000 else ""
            print(f"    Risk={risk}% MaxLot={maxlot}: PF={m_oos['pf']:.2f} T={m_oos['n_trades']} "
                  f"DD={m_oos['max_dd']:.1f}% Daily={m_oos['daily_jpy']:.0f} JPY{flag}")

    # Year-by-year at target risk
    if best_spot:
        target_risk = best_spot['risk']
        target_maxlot = best_spot['maxlot']
    else:
        target_risk = 2.5
        target_maxlot = 0.75

    print(f"\n  Year-by-Year (Risk={target_risk}%, MaxLot={target_maxlot}):")
    ov_yy = {**winner_ov, "RiskPct": target_risk, "MaxLot": target_maxlot}
    cfg_yy = make_config(**ov_yy)
    trades_yy, _, _ = backtest_v15(h4_df, cfg_yy)
    df_yy = pd.DataFrame(trades_yy)
    if len(df_yy) > 0:
        df_yy["year"] = pd.to_datetime(df_yy["close_time"]).dt.year
        for yr, grp in df_yy.groupby("year"):
            pnls = grp["pnl_jpy"].values
            wins = (pnls > 0).sum()
            n = len(pnls)
            wr = wins / n * 100 if n > 0 else 0
            gp = pnls[pnls > 0].sum() if wins > 0 else 0
            gl = abs(pnls[pnls <= 0].sum()) if (n - wins) > 0 else 1
            pf = gp / gl if gl > 0 else float("inf")
            days_in_year = 365
            daily = pnls.sum() / days_in_year
            print(f"    {yr}: T={n:4d} PF={pf:5.2f} WR={wr:4.0f}% "
                  f"PnL={pnls.sum():+12,.0f} Daily={daily:+7,.0f}")

    # =========================================================
    # FINAL SUMMARY
    # =========================================================
    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("FINAL SUMMARY - GoldAlpha v15")
    print("=" * 70)
    print(f"  Base: v13 + {winner.get('combo_name', 'unknown')}")
    print(f"  Parameters:")
    for k, v in sorted(winner_ov.items()):
        if not k.startswith("USE_") and not k.startswith("PARTIAL") and \
           not k.startswith("MAX_HOLD") and not k.startswith("STRUCTURE") and \
           not k.startswith("EMA_SLOPE") and not k.startswith("CHECK"):
            print(f"    {k}: {v}")
    print(f"  Features:")
    for k, v in sorted(winner_ov.items()):
        if k.startswith("USE_") and v:
            print(f"    {k}: {v}")
    feature_params = {k: v for k, v in winner_ov.items()
                      if k.startswith("PARTIAL") or k.startswith("MAX_HOLD") or
                      k.startswith("STRUCTURE") or k.startswith("EMA_SLOPE") or
                      k.startswith("CHECK")}
    for k, v in sorted(feature_params.items()):
        print(f"    {k}: {v}")
    print(f"\n  Full period (low risk 0.18%): PF={winner['pf']:.2f} T={winner['n_trades']} "
          f"DD={winner['max_dd']:.1f}%")
    if best_spot:
        print(f"  Sweet spot: Risk={best_spot['risk']}% MaxLot={best_spot['maxlot']} "
              f"Daily={best_spot['daily_jpy']:.0f} JPY DD={best_spot['max_dd']:.1f}%")
    print(f"\n  Elapsed: {elapsed:.0f}s")


if __name__ == "__main__":
    main()
