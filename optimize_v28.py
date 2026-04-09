"""
GoldAlpha v28 - H1 Entry for 6x Trade Frequency

Same quality filters as v27 (D1 regime, W1 sep, EMA slope) but entry on H1
instead of H4. This multiplies trade opportunities ~6x.

Entry: H1 dip below H4 EMA, bullish close above EMA
Exit: Same ATR-based SL/BE/Trail as v27
Filters: Same W1/D1/regime as v27

Goal: 2000+ trades, PF >= 1.5, WFA >= 5/8, daily >= 5000 JPY at 1% risk
"""

import sys
import os
import time
import numpy as np
import pandas as pd
from itertools import product

sys.path.insert(0, "/tmp/FxTrading_EA")
from backtest_goldalpha import (
    load_csv, GoldAlphaConfig, np_ema, np_sma, np_atr, np_adx,
    resample_to_daily, resample_to_weekly, calc_metrics,
    _calc_lot, _calc_pnl
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


log_file = open("/tmp/v28_output.log", "w")
sys.stdout = Tee(sys.__stdout__, log_file)


# ============================================================
# V28 Config
# ============================================================
class V28Config:
    W1_FastEMA = 8; W1_SlowEMA = 21
    D1_EMA = 50; D1_Tolerance = 0.005
    H4_EMA = 20; ATR_Period = 14; ATR_SMA = 50
    SL_ATR_Mult = 2.5; Trail_ATR = 3.0; BE_ATR = 0.5
    RiskPct = 1.0; BodyRatio = 0.28
    EMA_Zone_ATR = 0.40; ATR_Filter = 0.30
    MaxPositions = 3; MinLot = 0.01; MaxLot = 0.50
    INITIAL_BALANCE = 300_000
    SPREAD_POINTS = 30; POINT = 0.01
    CONTRACT_SIZE = 100; COMMISSION_PER_LOT = 7.0; USDJPY_RATE = 150.0
    # Regime
    D1_Slope_Bars = 5; D1_Min_Slope = 0.001; W1_Min_Sep = 0.003
    # H4 EMA Slope
    EMA_Slope_Bars = 5
    # Time decay
    USE_TIME_DECAY = False; MAX_HOLD_BARS = 50  # H1 bars (~50 = ~2 days)
    # Min bars between entries (prevent clustering)
    MIN_ENTRY_GAP = 4  # H1 bars


def make_cfg(**overrides):
    cfg = V28Config()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ============================================================
# H1 Entry Backtest Engine
# ============================================================
def backtest_v28(h1_o, h1_h, h1_l, h1_c, h1_times,
                 h4_ema, h4_atr, h4_avg_atr, h4_times,
                 w1_fast_ema, w1_slow_ema, w1_times,
                 d1_close, d1_ema, d1_times,
                 regime_mask_h1, cfg):
    """
    H1-entry backtest with H4 EMA for dip reference, H4 ATR for SL.
    regime_mask_h1: boolean array, True = blocked
    """
    balance = cfg.INITIAL_BALANCE
    peak_balance = balance
    positions = []
    trades = []
    eq_curve = []

    n = len(h1_o)
    spread = cfg.SPREAD_POINTS * cfg.POINT
    point = cfg.POINT

    # Map H1 bars to H4/W1/D1
    h4_idx = np.searchsorted(h4_times, h1_times, side="right") - 1
    w1_idx = np.searchsorted(w1_times, h1_times, side="right") - 1
    d1_idx = np.searchsorted(d1_times, h1_times, side="right") - 1

    warmup = max(cfg.ATR_SMA + cfg.ATR_Period, 100)
    last_entry_bar = -999

    for i in range(warmup, n):
        cur_time = h1_times[i]
        h4i = h4_idx[i]
        if h4i < 0 or h4i >= len(h4_atr):
            continue

        cur_atr = h4_atr[h4i]  # Use H4 ATR for sizing/exits
        if np.isnan(cur_atr) or cur_atr < point:
            continue

        # Day/hour filter
        dow = cur_time.weekday()
        if dow >= 5:
            continue
        hour = cur_time.hour
        if dow == 4 and hour > 16:
            continue

        # Manage positions
        closed_pnls = _manage_h1(positions, trades, h1_h[i], h1_l[i], h1_c[i],
                                  cur_time, cur_atr, cfg, balance, i)
        for pnl in closed_pnls:
            balance += pnl
            if balance > peak_balance:
                peak_balance = balance

        eq_curve.append(balance + _unreal(positions, h1_c[i], cfg))

        # Entry
        if len(positions) >= cfg.MaxPositions:
            continue

        # Min gap between entries
        if i - last_entry_bar < cfg.MIN_ENTRY_GAP:
            continue

        # Regime block
        if regime_mask_h1[i]:
            continue

        # === W1 trend ===
        wi = w1_idx[i]
        if wi < 1 or wi >= len(w1_fast_ema):
            continue
        w1f = w1_fast_ema[wi]; w1s = w1_slow_ema[wi]
        if np.isnan(w1f) or np.isnan(w1s):
            continue
        w1_dir = 0
        if w1f > w1s: w1_dir = 1
        elif w1f < w1s: w1_dir = -1
        if w1_dir == 0:
            continue

        # W1 separation
        w1_mid = (w1f + w1s) / 2
        if w1_mid > 0 and abs(w1f - w1s) / w1_mid < cfg.W1_Min_Sep:
            continue

        # === D1 filter ===
        di = d1_idx[i]
        if di < 1 or di >= len(d1_close):
            continue
        d1_cl = d1_close[di]; d1_em = d1_ema[di]
        if np.isnan(d1_cl) or np.isnan(d1_em) or d1_em == 0:
            continue
        d1_diff = (d1_cl - d1_em) / d1_em
        if w1_dir == 1 and d1_diff < -cfg.D1_Tolerance:
            continue
        if w1_dir == -1 and d1_diff > cfg.D1_Tolerance:
            continue

        # === ATR filter ===
        avg_atr = h4_avg_atr[h4i]
        if np.isnan(avg_atr) or avg_atr <= 0:
            continue
        if cur_atr < avg_atr * cfg.ATR_Filter:
            continue

        # === H4 EMA + slope ===
        ema_val = h4_ema[h4i]
        if np.isnan(ema_val):
            continue

        # H4 EMA slope check
        slope_h4i = h4i - cfg.EMA_Slope_Bars
        if slope_h4i >= 0 and slope_h4i < len(h4_ema):
            ema_prev = h4_ema[slope_h4i]
            if not np.isnan(ema_prev):
                slope = ema_val - ema_prev
                if w1_dir == 1 and slope < 0:
                    continue
                if w1_dir == -1 and slope > 0:
                    continue

        zone = cfg.EMA_Zone_ATR * cur_atr

        # === H1 dip-buy/sell check (bar[i-1]) ===
        if i < 1:
            continue
        bar_o = h1_o[i-1]; bar_c = h1_c[i-1]
        bar_h = h1_h[i-1]; bar_l = h1_l[i-1]
        bar_range = bar_h - bar_l
        if bar_range <= point:
            continue

        entered = False

        if w1_dir == 1:
            # BUY: H1 bar dips below H4 EMA zone, closes above EMA, bullish body
            if bar_l <= ema_val + zone and bar_c > ema_val and bar_c > bar_o:
                body = bar_c - bar_o
                if body / bar_range >= cfg.BodyRatio:
                    entry = h1_c[i] + spread / 2
                    sl_dist = cfg.SL_ATR_Mult * cur_atr
                    sl = entry - sl_dist
                    lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                    positions.append({
                        "direction": "BUY", "entry": entry, "sl": sl,
                        "lot": lot, "open_time": cur_time, "bar_idx": i,
                        "be_done": False, "highest": entry
                    })
                    last_entry_bar = i
                    entered = True

        elif w1_dir == -1:
            # SELL: H1 bar rises above H4 EMA zone, closes below EMA, bearish body
            if bar_h >= ema_val - zone and bar_c < ema_val and bar_c < bar_o:
                body = bar_o - bar_c
                if body / bar_range >= cfg.BodyRatio:
                    entry = h1_c[i] - spread / 2
                    sl_dist = cfg.SL_ATR_Mult * cur_atr
                    sl = entry + sl_dist
                    lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                    positions.append({
                        "direction": "SELL", "entry": entry, "sl": sl,
                        "lot": lot, "open_time": cur_time, "bar_idx": i,
                        "be_done": False, "lowest": entry
                    })
                    last_entry_bar = i
                    entered = True

    # Close remaining
    if positions:
        for pos in list(positions):
            pnl = _close_h1(pos, h1_c[-1], h1_times[-1], "END", trades, cfg)
            balance += pnl
        positions.clear()

    return trades, eq_curve, balance


def _manage_h1(positions, trades, bar_h, bar_l, bar_c, cur_time, cur_atr, cfg, balance, bar_i):
    closed = []
    for pos in list(positions):
        # Time decay
        if cfg.USE_TIME_DECAY:
            bars_held = bar_i - pos["bar_idx"]
            if bars_held >= cfg.MAX_HOLD_BARS:
                pnl = _close_h1(pos, bar_c, cur_time, "TD", trades, cfg)
                closed.append(pnl)
                positions.remove(pos)
                continue

        if pos["direction"] == "BUY":
            if bar_l <= pos["sl"]:
                pnl = _close_h1(pos, pos["sl"], cur_time, "SL", trades, cfg)
                closed.append(pnl)
                positions.remove(pos)
                continue
            profit = bar_c - pos["entry"]
            if not pos["be_done"] and profit > cfg.BE_ATR * cur_atr:
                pos["sl"] = pos["entry"] + 0.1 * cur_atr
                pos["be_done"] = True
            if pos["be_done"]:
                if bar_h > pos.get("highest", pos["entry"]):
                    pos["highest"] = bar_h
                new_sl = pos["highest"] - cfg.Trail_ATR * cur_atr
                if new_sl > pos["sl"] + 10 * cfg.POINT:
                    pos["sl"] = new_sl

        elif pos["direction"] == "SELL":
            if bar_h >= pos["sl"]:
                pnl = _close_h1(pos, pos["sl"], cur_time, "SL", trades, cfg)
                closed.append(pnl)
                positions.remove(pos)
                continue
            profit = pos["entry"] - bar_c
            if not pos["be_done"] and profit > cfg.BE_ATR * cur_atr:
                pos["sl"] = pos["entry"] - 0.1 * cur_atr
                pos["be_done"] = True
            if pos["be_done"]:
                if bar_l < pos.get("lowest", pos["entry"]):
                    pos["lowest"] = bar_l
                new_sl = pos["lowest"] + cfg.Trail_ATR * cur_atr
                if new_sl < pos["sl"] - 10 * cfg.POINT:
                    pos["sl"] = new_sl
    return closed


def _close_h1(pos, exit_price, cur_time, reason, trades, cfg):
    pnl = _calc_pnl(pos["entry"], exit_price, pos["lot"], cfg)
    commission = cfg.COMMISSION_PER_LOT * pos["lot"] * cfg.USDJPY_RATE
    pnl -= commission
    trades.append({
        "open_time": pos["open_time"], "close_time": cur_time,
        "direction": pos["direction"], "entry": pos["entry"],
        "exit": exit_price, "lot": pos["lot"], "pnl_jpy": pnl, "reason": reason
    })
    return pnl


def _unreal(positions, price, cfg):
    total = 0
    for pos in positions:
        if pos["direction"] == "BUY":
            total += (price - pos["entry"]) * pos["lot"] * cfg.CONTRACT_SIZE * cfg.USDJPY_RATE
        else:
            total += (pos["entry"] - price) * pos["lot"] * cfg.CONTRACT_SIZE * cfg.USDJPY_RATE
    return total


# ============================================================
# Precompute indicators
# ============================================================
def precompute_v28(h1_df, h4_df, cfg):
    # H1
    h1_o = h1_df["Open"].values; h1_h = h1_df["High"].values
    h1_l = h1_df["Low"].values; h1_c = h1_df["Close"].values
    h1_times = h1_df.index.to_pydatetime()

    # H4
    h4_c = h4_df["Close"].values; h4_h = h4_df["High"].values; h4_l = h4_df["Low"].values
    h4_times = h4_df.index.to_pydatetime()
    h4_ema = np_ema(h4_c, cfg.H4_EMA)
    h4_atr = np_atr(h4_h, h4_l, h4_c, cfg.ATR_Period)
    h4_avg_atr = np_sma(h4_atr, cfg.ATR_SMA)

    # W1
    w1 = resample_to_weekly(h4_df)
    w1_c = w1["Close"].values
    w1_fast = np_ema(w1_c, cfg.W1_FastEMA)
    w1_slow = np_ema(w1_c, cfg.W1_SlowEMA)
    w1_times = w1.index.to_pydatetime()

    # D1
    d1 = resample_to_daily(h4_df)
    d1_c = d1["Close"].values
    d1_ema = np_ema(d1_c, cfg.D1_EMA)
    d1_times = d1.index.to_pydatetime()

    return (h1_o, h1_h, h1_l, h1_c, h1_times,
            h4_ema, h4_atr, h4_avg_atr, h4_times,
            w1_fast, w1_slow, w1_times,
            d1_c, d1_ema, d1_times)


def compute_h1_regime_mask(h1_times, d1_ema, d1_times, w1_fast, w1_slow, w1_times, cfg):
    n = len(h1_times)
    d1_idx = np.searchsorted(d1_times, h1_times, side="right") - 1
    w1_idx = np.searchsorted(w1_times, h1_times, side="right") - 1
    blocked = np.zeros(n, dtype=bool)

    # D1 slope
    if cfg.D1_Min_Slope > 0:
        for i in range(n):
            di = d1_idx[i]
            if cfg.D1_Slope_Bars <= di < len(d1_ema):
                prev = d1_ema[di - cfg.D1_Slope_Bars]
                cur = d1_ema[di]
                if prev > 0 and abs(cur - prev) / prev < cfg.D1_Min_Slope:
                    blocked[i] = True

    # W1 separation
    if cfg.W1_Min_Sep > 0:
        for i in range(n):
            if blocked[i]: continue
            wi = w1_idx[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0 and abs(w1_fast[wi] - w1_slow[wi]) / mid < cfg.W1_Min_Sep:
                    blocked[i] = True

    return blocked


def quick_score(m, min_t=800):
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.3:
        return -999
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 5000) * 0.002
    s -= max(0, m["max_dd"] - 25) * 0.3
    s += min(m["daily_jpy"], 10000) * 0.001
    return s


def year_by_year(trades, h1_df):
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
        yr_days = 365 if yr < df["year"].max() else max(1, (h1_df.index[-1] - pd.Timestamp(f"{yr}-01-01")).days)
        results[yr] = {"n": n, "pf": pf, "wr": wr, "pnl": pnls.sum(), "daily": pnls.sum() / max(1, yr_days)}
    return results


# ============================================================
# WFA
# ============================================================
def run_v28_wfa(h1_df, h4_df, cfg, n_windows=8):
    total_bars = len(h1_df)
    window_size = total_bars // n_windows
    oos_size = int(window_size * 0.25)
    results = []

    for w in range(n_windows):
        window_end = min((w + 1) * window_size, total_bars)
        oos_start = window_end - oos_size
        data_start = max(0, oos_start - 3000)  # Need more warmup for H1

        sub_h1 = h1_df.iloc[data_start:window_end].copy()
        # Get matching H4 data
        h1_start = sub_h1.index[0]; h1_end = sub_h1.index[-1]
        sub_h4 = h4_df[(h4_df.index >= h1_start - pd.Timedelta(days=365)) &
                        (h4_df.index <= h1_end)].copy()

        ind = precompute_v28(sub_h1, sub_h4, cfg)
        mask = compute_h1_regime_mask(ind[4], ind[13], ind[14], ind[9], ind[10], ind[11], cfg)
        trades, _, _ = backtest_v28(*ind, mask, cfg)

        oos_time = h1_df.index[oos_start]
        oos_trades = [t for t in trades if t["open_time"] >= oos_time]
        oos_end = h1_df.index[min(window_end - 1, total_bars - 1)]
        oos_days = max(1, (oos_end - oos_time).days)
        m = calc_metrics(oos_trades, cfg.INITIAL_BALANCE, oos_days)
        if m:
            results.append(m)
    return results


# ============================================================
# MAIN
# ============================================================
def main():
    t0 = time.time()
    h1_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H1.csv")
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h1_df.index[-1] - h1_df.index[0]).days

    print("=" * 80)
    print("GoldAlpha v28 - H1 Entry for 6x Trade Frequency")
    print(f"H1: {len(h1_df)} bars, H4: {len(h4_df)} bars, {total_days} days")
    print(f"Target: 2000+ trades, PF >= 1.5, WFA >= 5/8, daily >= 5000 JPY")
    print("=" * 80)

    # Baseline
    cfg = make_cfg()
    ind = precompute_v28(h1_df, h4_df, cfg)
    mask = compute_h1_regime_mask(ind[4], ind[13], ind[14], ind[9], ind[10], ind[11], cfg)
    print(f"Indicators ready, {mask.sum()/len(mask)*100:.1f}% blocked, {time.time()-t0:.1f}s")

    trades, _, _ = backtest_v28(*ind, mask, cfg)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    if m:
        print(f"Baseline: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% "
              f"D¥={m['daily_jpy']:.0f} WR={m['win_rate']:.1f}%")

    # ================================================================
    # PHASE 1: Grid Search
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 1: Grid Search")
    print("=" * 80)

    # 4*4*3*3*3*3*3*3 = 11664 -> 3*3*2*3*2*3*3*3 = 2916
    grid = {
        "SL_ATR_Mult":   [2.0, 2.5, 3.0],
        "Trail_ATR":     [2.5, 3.0, 3.5],
        "BE_ATR":        [0.5, 1.0],
        "EMA_Zone_ATR":  [0.3, 0.4, 0.5],
        "BodyRatio":     [0.24, 0.30],
        "MaxPositions":  [2, 3, 4],
        "ATR_Filter":    [0.2, 0.3, 0.4],
        "MIN_ENTRY_GAP": [2, 4, 6],
    }

    # Regime variants
    regime_cfgs = [
        ("light", {"D1_Min_Slope": 0.0005, "W1_Min_Sep": 0.0}),
        ("med",   {"D1_Min_Slope": 0.001, "W1_Min_Sep": 0.003}),
    ]

    keys = list(grid.keys())
    combos = list(product(*grid.values()))
    n_combos = len(combos)
    print(f"Grid: {n_combos} combos x {len(regime_cfgs)} regimes = {n_combos * len(regime_cfgs)}")

    all_results = []
    best_qs = -999

    for rv_name, rv_overrides in regime_cfgs:
        print(f"\n  Regime: {rv_name}")
        t_rv = time.time()

        # Pre-compute mask once per regime
        cfg_rv = make_cfg(**rv_overrides)
        mask_rv = compute_h1_regime_mask(ind[4], ind[13], ind[14], ind[9], ind[10], ind[11], cfg_rv)
        pct = mask_rv.sum() / len(mask_rv) * 100
        print(f"    {pct:.1f}% blocked")

        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            params.update(rv_overrides)
            params["D1_Tolerance"] = 0.005
            params["RiskPct"] = 1.0
            params["MaxLot"] = 0.50

            cfg = make_cfg(**params)
            trades, _, _ = backtest_v28(*ind, mask_rv, cfg)
            m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
            qs = quick_score(m, 600)

            if qs > -999:
                all_results.append((params, m, qs, rv_name))
                if qs > best_qs:
                    best_qs = qs
                    print(f"    [{idx+1}/{n_combos}] BEST qs={qs:.1f} PF={m['pf']:.2f} "
                          f"T={m['n_trades']} DD={m['max_dd']:.1f}% D¥={m['daily_jpy']:.0f} | "
                          f"SL={params['SL_ATR_Mult']} Tr={params['Trail_ATR']} BE={params['BE_ATR']} "
                          f"Z={params['EMA_Zone_ATR']} B={params['BodyRatio']} "
                          f"MP={params['MaxPositions']} AF={params['ATR_Filter']} G={params['MIN_ENTRY_GAP']}")

            if (idx + 1) % 500 == 0:
                print(f"    [{idx+1}/{n_combos}] {time.time()-t_rv:.0f}s, "
                      f"{sum(1 for x in all_results if x[3]==rv_name)} valid")

        print(f"    {rv_name} done in {time.time()-t_rv:.0f}s")

    all_results.sort(key=lambda x: x[2], reverse=True)
    print(f"\n  Total: {len(all_results)} valid, best QS={best_qs:.1f}")

    # Top 25
    if all_results:
        print(f"\n  Top 20:")
        print(f"  {'Rk':>2} {'QS':>6} {'PF':>5} {'T':>5} {'DD':>5} {'D¥':>7} | {'Reg':>5} SL  Tr  BE  Z    B   MP AF  G")
        print("  " + "-" * 90)
        for i, (p, m, qs, rv_n) in enumerate(all_results[:20]):
            print(f"  {i+1:2d} {qs:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} {m['daily_jpy']:7.0f} | "
                  f"{rv_n:>5} {p['SL_ATR_Mult']:.1f} {p['Trail_ATR']:.1f} {p['BE_ATR']:.1f} "
                  f"{p['EMA_Zone_ATR']:.1f} {p['BodyRatio']:.2f} {p['MaxPositions']}  "
                  f"{p['ATR_Filter']:.1f} {p['MIN_ENTRY_GAP']}")

    print(f"\n  Phase 1: {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 2: WFA on top 20
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 2: WFA Validation")
    print("=" * 80)

    candidates = []
    seen = set()
    for p, m, qs, rv_n in all_results:
        key = (tuple(sorted(p.items())), rv_n)
        if key not in seen:
            seen.add(key)
            candidates.append((p, rv_n, m, qs))
        if len(candidates) >= 20:
            break

    validated = []
    best_wfa = 0
    for ci, (params, rv_n, m_full, qs) in enumerate(candidates):
        cfg = make_cfg(**params)
        wfa = run_v28_wfa(h1_df, h4_df, cfg)
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0

        if n_pass > best_wfa:
            best_wfa = n_pass

        mask_test = compute_h1_regime_mask(ind[4], ind[13], ind[14], ind[9], ind[10], ind[11], cfg)
        trades_test, _, _ = backtest_v28(*ind, mask_test, cfg)
        n_losing = sum(1 for v in year_by_year(trades_test, h1_df).values() if v["pnl"] < 0)

        validated.append({
            "params": params, "m": m_full, "wfa": wfa, "n_pass": n_pass,
            "avg_pf": avg_pf, "n_losing": n_losing, "qs": qs, "rv_n": rv_n
        })

        marker = ""
        if n_pass >= 7: marker = " *** 7/8! ***"
        elif n_pass >= 6: marker = " <<<"
        print(f"  [{ci+1}/{len(candidates)}] WFA={n_pass}/8 AvgPF={avg_pf:.2f} "
              f"PF={m_full['pf']:.2f} T={m_full['n_trades']} D¥={m_full['daily_jpy']:.0f} "
              f"LY={n_losing}{marker} ({rv_n})")

    validated.sort(key=lambda x: (x["n_pass"], x["qs"]), reverse=True)

    print(f"\n  Top 10:")
    for i, v in enumerate(validated[:10]):
        m = v["m"]; p = v["params"]
        print(f"  {i+1:2d} WFA={v['n_pass']}/8 PF={m['pf']:.2f} T={m['n_trades']} "
              f"DD={m['max_dd']:.1f}% D¥={m['daily_jpy']:.0f} LY={v['n_losing']} | "
              f"SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} BE={p['BE_ATR']:.1f} "
              f"Z={p['EMA_Zone_ATR']:.1f} B={p['BodyRatio']:.2f} MP={p['MaxPositions']} "
              f"AF={p['ATR_Filter']:.1f} G={p['MIN_ENTRY_GAP']}")
    print(f"\n  Best WFA: {best_wfa}/8")

    # ================================================================
    # FINAL: Risk scaling on winner
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL")
    print("=" * 80)

    if validated:
        winner = validated[0]
        wp = winner["params"]
        rv_n = winner["rv_n"]

        cfg = make_cfg(**wp)
        mask_win = compute_h1_regime_mask(ind[4], ind[13], ind[14], ind[9], ind[10], ind[11], cfg)
        trades_win, _, _ = backtest_v28(*ind, mask_win, cfg)

        yby = year_by_year(trades_win, h1_df)
        print(f"\n  Year-by-Year:")
        print(f"  {'Year':>6} {'N':>5} {'PF':>5} {'WR%':>5} {'PnL':>10} {'Daily':>8}")
        print("  " + "-" * 50)
        for yr in sorted(yby.keys()):
            y = yby[yr]
            print(f"  {yr:6d} {y['n']:5d} {y['pf']:5.2f} {y['wr']:5.1f} {y['pnl']:10.0f} {y['daily']:8.0f}")

        # WFA detail
        print(f"\n  WFA:")
        for wi, r in enumerate(winner["wfa"]):
            status = "PASS" if r["pf"] > 1.0 else "FAIL"
            print(f"    W{wi+1}: PF={r['pf']:.2f} T={r['n_trades']} [{status}]")

        # Risk scaling
        print(f"\n  Risk Scaling:")
        print(f"  {'Risk%':>6} {'MaxLot':>6} | {'PF':>5} {'T':>5} {'DD%':>6} {'D¥':>8} {'Final':>12} {'5K':>4}")
        print("  " + "-" * 70)
        for risk, maxlot in [(0.5, 0.25), (1.0, 0.50), (1.5, 0.75),
                              (2.0, 1.0), (3.0, 1.5)]:
            params = {**wp, "RiskPct": risk, "MaxLot": maxlot}
            cfg = make_cfg(**params)
            trades, _, _ = backtest_v28(*ind, mask_win, cfg)
            m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
            if m:
                hit = "YES" if m["daily_jpy"] >= 5000 else ""
                print(f"  {risk:6.1f} {maxlot:6.2f} | {m['pf']:5.2f} {m['n_trades']:5d} "
                      f"{m['max_dd']:6.1f} {m['daily_jpy']:8.0f} {m['final_balance']:12.0f} {hit:>4}")

        # MQ5 params
        print(f"\n  === v28 MQ5 PARAMETERS ===")
        for k in ["SL_ATR_Mult", "Trail_ATR", "BE_ATR", "BodyRatio",
                   "EMA_Zone_ATR", "ATR_Filter", "D1_Tolerance",
                   "MaxPositions", "MIN_ENTRY_GAP",
                   "D1_Slope_Bars", "D1_Min_Slope", "W1_Min_Sep", "EMA_Slope_Bars"]:
            val = wp.get(k, getattr(V28Config, k, "N/A"))
            print(f"  {k} = {val}")

    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("DONE")
    log_file.close()


if __name__ == "__main__":
    main()
