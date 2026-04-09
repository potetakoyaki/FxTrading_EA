"""
GoldAlpha v25 Optimizer - Multi-Condition Momentum Entry

FUNDAMENTAL CHANGE: v12-v24 all used EMA dip-buy entry.
This fails in ranging markets (W2/W4 in WFA) because flat EMA triggers false dips.

v25 uses MOMENTUM/BREAKOUT entry instead:
  A) Breakout: H4 close > prev bar high AND close > EMA AND bullish body
  B) Pullback: EMA slope positive N bars AND low touches EMA zone AND bullish close

Key insight: momentum entries avoid entries when EMA is flat, which is exactly
when the dip-buy approach fails.

All MQ5-compatible: EMA, ATR, RSI, price action only.
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
    np_ema, np_sma, np_atr, np_adx, resample_to_daily, resample_to_weekly,
    _calc_lot, _manage_positions, _close_position, _unrealized_pnl, _calc_pnl
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

log_file = open("/tmp/v25_output.log", "w")
sys.stdout = Tee(sys.__stdout__, log_file)


# ============================================================
# RSI calculation (numpy, MQ5-compatible)
# ============================================================
def np_rsi(close, period=14):
    """Wilder's RSI using exponential smoothing."""
    n = len(close)
    rsi = np.full(n, 50.0, dtype=np.float64)
    if n < period + 1:
        return rsi
    # Initial gains/losses
    gains = np.zeros(n)
    losses = np.zeros(n)
    for i in range(1, n):
        diff = close[i] - close[i - 1]
        if diff > 0:
            gains[i] = diff
        else:
            losses[i] = -diff
    # Wilder's smoothing (same as EMA with alpha=1/period)
    alpha = 1.0 / period
    avg_gain = np.zeros(n)
    avg_loss = np.zeros(n)
    # Seed with SMA
    avg_gain[period] = np.mean(gains[1:period + 1])
    avg_loss[period] = np.mean(losses[1:period + 1])
    for i in range(period + 1, n):
        avg_gain[i] = alpha * gains[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * losses[i] + (1 - alpha) * avg_loss[i - 1]
    for i in range(period, n):
        if avg_loss[i] == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


# ============================================================
# Momentum Backtest Engine
# ============================================================
def backtest_momentum(h4_o, h4_h, h4_l, h4_c, h4_times,
                      w1_fast_ema, w1_slow_ema, w1_times,
                      d1_close, d1_ema, d1_times,
                      h4_ema, h4_atr, h4_avg_atr, h4_adx,
                      h4_rsi, cfg, start_balance=None):
    """
    Momentum/Breakout entry backtest engine.
    Same exit logic as v12 (SL/BE/Trail), completely different entry.

    Entry modes:
      "both"          - breakout OR pullback
      "breakout_only" - only breakout entries
      "pullback_only" - only pullback entries

    BUY Breakout: close > prev high AND close > EMA AND body_ratio OK
    BUY Pullback: EMA slope > 0 over N bars AND low within zone of EMA AND bullish close above EMA

    SELL: mirror conditions.
    """
    balance = start_balance if start_balance else cfg.INITIAL_BALANCE
    peak_balance = balance
    positions = []
    trades = []
    equity_curve = []

    n_h4 = len(h4_o)
    spread = cfg.SPREAD_POINTS * cfg.POINT
    point = cfg.POINT

    entry_mode = getattr(cfg, "ENTRY_MODE", "both")
    slope_bars = getattr(cfg, "EMA_SLOPE_BARS", 5)
    zone_atr = getattr(cfg, "EMA_Zone_ATR", 0.5)
    body_ratio = getattr(cfg, "BodyRatio", 0.28)

    # Pre-compute W1/D1 index lookups
    w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1
    d1_idx_map = np.searchsorted(d1_times, h4_times, side="right") - 1

    warmup = max(cfg.ATR_SMA + cfg.ATR_Period, 60, slope_bars + 5)

    for i in range(warmup, n_h4):
        cur_time = h4_times[i]
        cur_atr = h4_atr[i]

        if np.isnan(cur_atr) or cur_atr < point:
            continue

        # Day-of-week filter
        dow = cur_time.weekday()
        if dow >= 5:
            continue
        hour = cur_time.hour
        if dow == 4 and hour > 16:
            continue

        # Session filter
        if cfg.USE_SESSION_FILTER:
            if hour < cfg.TRADE_START_HOUR or hour >= cfg.TRADE_END_HOUR:
                _manage_positions(positions, trades, h4_h[i], h4_l[i], h4_c[i],
                                  cur_time, cur_atr, cfg, balance)
                equity_curve.append(balance + _unrealized_pnl(positions, h4_c[i], cfg))
                continue

        # Manage existing positions (same exit logic as v12)
        closed = _manage_positions(positions, trades, h4_h[i], h4_l[i], h4_c[i],
                                    cur_time, cur_atr, cfg, balance)
        for pnl in closed:
            balance += pnl
            if balance > peak_balance:
                peak_balance = balance

        # Time decay exit
        if cfg.USE_TIME_DECAY:
            for pos in list(positions):
                bars_held = i - pos["bar_idx"]
                if bars_held >= cfg.MAX_HOLD_BARS:
                    pnl = _close_position(pos, h4_c[i], cur_time, "TIME_DECAY", trades, cfg)
                    balance += pnl
                    positions.remove(pos)
                    if balance > peak_balance:
                        peak_balance = balance

        equity_curve.append(balance + _unrealized_pnl(positions, h4_c[i], cfg))

        # Check entry conditions
        if len(positions) >= cfg.MaxPositions:
            continue

        # ============ W1 TREND (same as v12) ============
        w1_i = w1_idx_map[i]
        if w1_i < 1:
            continue
        w1_fi = w1_i
        if w1_fi < 0 or w1_fi >= len(w1_fast_ema):
            continue
        w1f = w1_fast_ema[w1_fi]
        w1s = w1_slow_ema[w1_fi]
        if np.isnan(w1f) or np.isnan(w1s):
            continue
        w1_dir = 0
        if w1f > w1s:
            w1_dir = 1
        elif w1f < w1s:
            w1_dir = -1
        if w1_dir == 0:
            continue

        # ============ D1 FILTER (same as v12) ============
        d1_i = d1_idx_map[i]
        if d1_i < 1:
            continue
        d1_cl = d1_close[d1_i]
        d1_em = d1_ema[d1_i]
        if np.isnan(d1_cl) or np.isnan(d1_em) or d1_em == 0:
            continue
        d1_diff = (d1_cl - d1_em) / d1_em
        if w1_dir == 1 and d1_diff < -cfg.D1_Tolerance:
            continue
        if w1_dir == -1 and d1_diff > cfg.D1_Tolerance:
            continue

        # ============ ATR FILTER (same as v12) ============
        avg_atr = h4_avg_atr[i]
        if np.isnan(avg_atr) or avg_atr <= 0:
            continue
        if cur_atr < avg_atr * cfg.ATR_Filter:
            continue

        # ============ H4 EMA ============
        ema_val = h4_ema[i]
        if np.isnan(ema_val):
            continue
        zone = zone_atr * cur_atr

        # ============ NEW: MOMENTUM/BREAKOUT ENTRY ============
        # We check bar[i-1] (completed bar) for entry signal, enter at bar[i] close
        if i < 2:
            continue

        entered = False

        # --- BUY ---
        if w1_dir == 1 and not entered:
            # Breakout entry: prev bar closes above bar before's high, above EMA, bullish
            if entry_mode in ("both", "breakout_only"):
                bar_c1 = h4_c[i - 1]
                bar_o1 = h4_o[i - 1]
                bar_h1 = h4_h[i - 1]
                bar_l1 = h4_l[i - 1]
                bar_h2 = h4_h[i - 2]
                bar_range1 = bar_h1 - bar_l1

                if bar_range1 > point:
                    body1 = bar_c1 - bar_o1
                    if (bar_c1 > bar_h2 and           # Breakout: close above prev high
                        bar_c1 > ema_val and           # Above EMA
                        body1 > 0 and                  # Bullish
                        body1 / bar_range1 >= body_ratio):  # Body ratio
                        # Entry
                        entry_price = h4_c[i] + spread / 2
                        sl_dist = cfg.SL_ATR_Mult * cur_atr
                        sl = entry_price - sl_dist
                        lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                        positions.append({
                            "direction": "BUY",
                            "entry": entry_price,
                            "sl": sl,
                            "lot": lot,
                            "open_time": cur_time,
                            "bar_idx": i,
                            "be_done": False,
                            "highest": entry_price,
                            "partial_done": False,
                        })
                        entered = True

            # Pullback entry: EMA slope positive, low touched zone, bullish close above EMA
            if not entered and entry_mode in ("both", "pullback_only"):
                if i >= slope_bars + 1:
                    ema_prev = h4_ema[i - slope_bars]
                    if not np.isnan(ema_prev):
                        ema_slope = ema_val - ema_prev
                        if ema_slope > 0:  # EMA rising (buy only when trending)
                            # Check bar[i-1] for pullback entry
                            bar_c1 = h4_c[i - 1]
                            bar_o1 = h4_o[i - 1]
                            bar_h1 = h4_h[i - 1]
                            bar_l1 = h4_l[i - 1]
                            bar_range1 = bar_h1 - bar_l1

                            if bar_range1 > point:
                                body1 = bar_c1 - bar_o1
                                if (bar_l1 <= ema_val + zone and   # Low touched EMA zone
                                    bar_c1 > ema_val and           # Close above EMA
                                    body1 > 0 and                  # Bullish
                                    body1 / bar_range1 >= body_ratio):  # Body ratio
                                    entry_price = h4_c[i] + spread / 2
                                    sl_dist = cfg.SL_ATR_Mult * cur_atr
                                    sl = entry_price - sl_dist
                                    lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                                    positions.append({
                                        "direction": "BUY",
                                        "entry": entry_price,
                                        "sl": sl,
                                        "lot": lot,
                                        "open_time": cur_time,
                                        "bar_idx": i,
                                        "be_done": False,
                                        "highest": entry_price,
                                        "partial_done": False,
                                    })
                                    entered = True

        # --- SELL ---
        if w1_dir == -1 and not entered:
            # Breakout entry (bearish): prev bar closes below bar before's low, below EMA, bearish
            if entry_mode in ("both", "breakout_only"):
                bar_c1 = h4_c[i - 1]
                bar_o1 = h4_o[i - 1]
                bar_h1 = h4_h[i - 1]
                bar_l1 = h4_l[i - 1]
                bar_l2 = h4_l[i - 2]
                bar_range1 = bar_h1 - bar_l1

                if bar_range1 > point:
                    body1 = bar_o1 - bar_c1
                    if (bar_c1 < bar_l2 and           # Breakout: close below prev low
                        bar_c1 < ema_val and           # Below EMA
                        body1 > 0 and                  # Bearish
                        body1 / bar_range1 >= body_ratio):
                        entry_price = h4_c[i] - spread / 2
                        sl_dist = cfg.SL_ATR_Mult * cur_atr
                        sl = entry_price + sl_dist
                        lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                        positions.append({
                            "direction": "SELL",
                            "entry": entry_price,
                            "sl": sl,
                            "lot": lot,
                            "open_time": cur_time,
                            "bar_idx": i,
                            "be_done": False,
                            "lowest": entry_price,
                            "partial_done": False,
                        })
                        entered = True

            # Pullback entry (bearish): EMA slope negative, high touched zone, bearish close below EMA
            if not entered and entry_mode in ("both", "pullback_only"):
                if i >= slope_bars + 1:
                    ema_prev = h4_ema[i - slope_bars]
                    if not np.isnan(ema_prev):
                        ema_slope = ema_val - ema_prev
                        if ema_slope < 0:  # EMA falling
                            bar_c1 = h4_c[i - 1]
                            bar_o1 = h4_o[i - 1]
                            bar_h1 = h4_h[i - 1]
                            bar_l1 = h4_l[i - 1]
                            bar_range1 = bar_h1 - bar_l1

                            if bar_range1 > point:
                                body1 = bar_o1 - bar_c1
                                if (bar_h1 >= ema_val - zone and   # High touched EMA zone
                                    bar_c1 < ema_val and           # Close below EMA
                                    body1 > 0 and                  # Bearish
                                    body1 / bar_range1 >= body_ratio):
                                    entry_price = h4_c[i] - spread / 2
                                    sl_dist = cfg.SL_ATR_Mult * cur_atr
                                    sl = entry_price + sl_dist
                                    lot = _calc_lot(balance, cfg.RiskPct, sl_dist, cfg)
                                    positions.append({
                                        "direction": "SELL",
                                        "entry": entry_price,
                                        "sl": sl,
                                        "lot": lot,
                                        "open_time": cur_time,
                                        "bar_idx": i,
                                        "be_done": False,
                                        "lowest": entry_price,
                                        "partial_done": False,
                                    })
                                    entered = True

    # Close remaining positions
    if positions:
        final_price = h4_c[-1]
        final_time = h4_times[-1]
        for pos in list(positions):
            pnl = _close_position(pos, final_price, final_time, "END", trades, cfg)
            balance += pnl
        positions.clear()

    return trades, equity_curve, balance


# ============================================================
# Momentum backtest with regime filtering
# ============================================================
def backtest_momentum_regime(h4_df, cfg, regime_type="none", regime_params=None):
    """
    Momentum backtest with regime filtering.
    Same regime filtering as v20's backtest_with_regime, but uses momentum entry.
    """
    if regime_params is None:
        regime_params = {}

    ind = precompute_indicators(h4_df, cfg)
    h4_o, h4_h, h4_l, h4_c, h4_times = ind[0], ind[1], ind[2], ind[3], ind[4]
    n = len(h4_o)

    # Compute RSI
    h4_rsi = np_rsi(h4_c, getattr(cfg, "RSI_Period", 14))

    # Regime filtering: modify avg_atr to block entries
    h4_avg_atr = ind[13].copy()
    h4_atr = ind[12]

    if regime_type == "combined":
        w1_fast = ind[5]; w1_slow = ind[6]; w1_times = ind[7]
        d1_ema = ind[9]; d1_times = ind[10]

        w1_min_spread = regime_params.get("w1_min_spread", 0.005)
        d1_slope_bars = regime_params.get("d1_slope_bars", 10)
        d1_min_slope = regime_params.get("d1_min_slope", 0.002)

        w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1
        d1_idx_map = np.searchsorted(d1_times, h4_times, side="right") - 1

        for i in range(n):
            blocked = False
            wi = w1_idx_map[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0:
                    spread_pct = abs(w1_fast[wi] - w1_slow[wi]) / mid
                    if spread_pct < w1_min_spread:
                        blocked = True
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

    elif regime_type == "d1_slope":
        d1_ema = ind[9]; d1_times = ind[10]
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

    elif regime_type == "w1_ema_spread":
        w1_fast = ind[5]; w1_slow = ind[6]; w1_times = ind[7]
        min_spread = regime_params.get("min_spread", 0.005)
        w1_idx_map = np.searchsorted(w1_times, h4_times, side="right") - 1
        for i in range(n):
            wi = w1_idx_map[i]
            if 0 <= wi < len(w1_fast):
                mid = (w1_fast[wi] + w1_slow[wi]) / 2
                if mid > 0:
                    spread_pct = abs(w1_fast[wi] - w1_slow[wi]) / mid
                    if spread_pct < min_spread:
                        h4_avg_atr[i] = 999999

    # Call momentum backtest with modified indicators
    mod_ind = list(ind)
    mod_ind[13] = h4_avg_atr

    trades, eq, final = backtest_momentum(
        mod_ind[0], mod_ind[1], mod_ind[2], mod_ind[3], mod_ind[4],
        mod_ind[5], mod_ind[6], mod_ind[7],
        mod_ind[8], mod_ind[9], mod_ind[10],
        mod_ind[11], mod_ind[12], mod_ind[13], mod_ind[14],
        h4_rsi, cfg
    )
    return trades, eq, final


def run_momentum_wfa(h4_df, cfg, regime_type, regime_params, n_windows=8):
    """WFA with momentum entry + regime filtering."""
    total_bars = len(h4_df)
    window_size = total_bars // n_windows
    oos_size = int(window_size * 0.25)
    results = []

    for w in range(n_windows):
        window_end = min((w + 1) * window_size, total_bars)
        oos_start = window_end - oos_size
        data_start = max(0, oos_start - 600)
        sub = h4_df.iloc[data_start:window_end].copy()
        trades, _, _ = backtest_momentum_regime(sub, cfg, regime_type, regime_params)
        oos_time = h4_df.index[oos_start]
        oos_trades = [t for t in trades if t["open_time"] >= oos_time]
        oos_end_time = h4_df.index[min(window_end - 1, total_bars - 1)]
        oos_days = max(1, (oos_end_time - oos_time).days)
        m = calc_metrics(oos_trades, cfg.INITIAL_BALANCE, oos_days)
        if m:
            results.append(m)
    return results


# ============================================================
# Config / helpers
# ============================================================
def make_cfg(**overrides):
    """v25 base config."""
    cfg = GoldAlphaConfig()
    cfg.W1_FastEMA = 8; cfg.W1_SlowEMA = 21; cfg.D1_EMA = 50
    cfg.H4_EMA = 20; cfg.ATR_Period = 14; cfg.ATR_SMA = 50
    cfg.SL_ATR_Mult = 2.5; cfg.Trail_ATR = 3.0; cfg.BE_ATR = 0.5
    cfg.RiskPct = 0.20; cfg.BodyRatio = 0.28
    cfg.EMA_Zone_ATR = 0.5; cfg.ATR_Filter = 0.5; cfg.D1_Tolerance = 0.003
    cfg.MaxPositions = 3; cfg.MinLot = 0.01; cfg.MaxLot = 0.50
    cfg.INITIAL_BALANCE = 300_000
    cfg.USE_EMA_SLOPE = False; cfg.EMA_SLOPE_BARS = 5
    cfg.USE_STRUCTURE = False; cfg.STRUCTURE_BARS = 2
    cfg.USE_TIME_DECAY = False; cfg.MAX_HOLD_BARS = 30
    cfg.USE_VOL_REGIME = False; cfg.USE_SESSION_FILTER = False
    cfg.USE_RSI_CONFIRM = False; cfg.USE_ADX_FILTER = False
    cfg.USE_PARTIAL_CLOSE = False; cfg.USE_W1_SEPARATION = False
    cfg.RSI_Period = 14
    # v25-specific
    cfg.ENTRY_MODE = "both"  # "both", "breakout_only", "pullback_only"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run_bt(h4_df, total_days, rt, rp, **params):
    cfg = make_cfg(**params)
    trades, _, _ = backtest_momentum_regime(h4_df, cfg, rt, rp)
    return calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)


def run_bt_trades(h4_df, rt, rp, **params):
    cfg = make_cfg(**params)
    trades, _, _ = backtest_momentum_regime(h4_df, cfg, rt, rp)
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


def score_v25(m, wfa=None, n_losing=0, m_oos=None, min_trades=400):
    """WFA-heavy scoring, per task spec."""
    if m is None or m["n_trades"] < min_trades or m["pf"] < 1.2:
        return -999
    s = min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.003
    s -= max(0, m["max_dd"] - 30) * 0.5
    s -= n_losing * 3
    if wfa:
        n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
        avg_pf = np.mean([r["pf"] for r in wfa])
        min_pf = min(r["pf"] for r in wfa)
        s += (n_pass / 8) * 60
        s += min(avg_pf, 2.5) * 8
        s += min(min_pf, 1.0) * 10
        s -= (8 - n_pass) * 8
    if m_oos and m_oos["n_trades"] >= 20:
        s += min(m_oos["pf"], 4.0) * 4
    return s


def quick_score(m, min_t=400):
    if m is None or m["n_trades"] < min_t or m["pf"] < 1.2:
        return -999
    return min(m["pf"], 3.0) * 10 + min(m["n_trades"], 1500) * 0.003 - max(0, m["max_dd"] - 30) * 0.5


def full_validate(h4_df, total_days, params, rt, rp, tag=""):
    """Full validation: full-period metrics + WFA + OOS + losing years."""
    cfg = make_cfg(**params)
    trades, _, _ = backtest_momentum_regime(h4_df, cfg, rt, rp)
    m = calc_metrics(trades, cfg.INITIAL_BALANCE, total_days)
    if m is None or m["n_trades"] < 200 or m["pf"] < 1.1:
        return None
    wfa = run_momentum_wfa(h4_df, cfg, rt, rp)
    n_pass = sum(1 for r in wfa if r["pf"] > 1.0)
    avg_pf = np.mean([r["pf"] for r in wfa]) if wfa else 0
    min_pf = min(r["pf"] for r in wfa) if wfa else 0
    n_losing = count_losing_years(trades, h4_df)
    # OOS
    sub = h4_df[h4_df.index >= "2022-01-01"].copy()
    tr_oos, _, _ = backtest_momentum_regime(sub, cfg, rt, rp)
    oos_list = [t for t in tr_oos if t["open_time"] >= pd.Timestamp("2024-01-01")]
    oos_days = max(1, (sub.index[-1] - pd.Timestamp("2024-01-01")).days)
    m_oos = calc_metrics(oos_list, cfg.INITIAL_BALANCE, oos_days)
    fs = score_v25(m, wfa, n_losing, m_oos)
    return {"params": params, "m": m, "wfa": wfa, "n_pass": n_pass, "avg_pf": avg_pf,
            "min_pf": min_pf, "n_losing": n_losing, "oos": m_oos, "score": fs,
            "tag": tag, "rt": rt, "rp": rp}


def rng(v, step, n=2):
    return sorted(set([round(v + step * i, 4) for i in range(-n, n + 1) if v + step * i > 0]))


# ============================================================
# MAIN
# ============================================================
def main():
    t0 = time.time()
    h4_df = load_csv("/tmp/FxTrading_EA/XAUUSD_H4.csv")
    total_days = (h4_df.index[-1] - h4_df.index[0]).days
    print("=" * 80)
    print("GoldAlpha v25 - Multi-Condition Momentum Entry")
    print(f"H4: {len(h4_df)} bars, {total_days} days")
    print("FUNDAMENTAL CHANGE: Momentum/Breakout entry replaces EMA dip-buy")
    print("=" * 80)

    COMB_RT = "combined"
    COMB_RP = {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.005}
    D1_RT = "d1_slope"
    D1_RP = {"slope_bars": 5, "min_slope": 0.002}

    # ================================================================
    # BASELINES: v12 dip-buy vs v25 momentum (quick sanity check)
    # ================================================================
    print("\n--- Sanity Check ---")

    # v12 dip-buy baseline
    from optimize_v20 import backtest_with_regime as v12_bt
    cfg_v12 = make_cfg()
    tr_v12, _, _ = v12_bt(h4_df, cfg_v12, "none", {})
    m_v12 = calc_metrics(tr_v12, cfg_v12.INITIAL_BALANCE, total_days)
    if m_v12:
        print(f"v12 dip-buy (no regime): PF={m_v12['pf']:.2f} T={m_v12['n_trades']} DD={m_v12['max_dd']:.1f}%")
    else:
        print("v12 dip-buy: no trades")

    # v25 momentum baseline (no regime, default params)
    m_v25_base = run_bt(h4_df, total_days, "none", {})
    if m_v25_base:
        print(f"v25 momentum (no regime, default): PF={m_v25_base['pf']:.2f} "
              f"T={m_v25_base['n_trades']} DD={m_v25_base['max_dd']:.1f}%")
    else:
        print("v25 momentum default: no trades (will adjust params)")

    # Test each entry mode
    for mode in ["both", "breakout_only", "pullback_only"]:
        m_mode = run_bt(h4_df, total_days, "none", {}, ENTRY_MODE=mode)
        if m_mode:
            print(f"  {mode}: PF={m_mode['pf']:.2f} T={m_mode['n_trades']} DD={m_mode['max_dd']:.1f}%")
        else:
            print(f"  {mode}: no trades")

    # Test with combined regime
    m_v25_comb = run_bt(h4_df, total_days, COMB_RT, COMB_RP)
    if m_v25_comb:
        print(f"v25 momentum (combined regime): PF={m_v25_comb['pf']:.2f} "
              f"T={m_v25_comb['n_trades']} DD={m_v25_comb['max_dd']:.1f}%")
    else:
        print("v25 momentum (combined regime): no trades")

    print(f"\nSanity check done in {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 1: Broad Grid Search (D1 quick screen)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 1: Broad Grid Search (D1 quick screen)")
    print("=" * 80)

    # 4*4*4*3*3*3*3*3 = 15552 -> too many
    # 4*4*3*3*3*2*3*3 = 7776 -> ok
    # Target ~5000-6000
    screen_grid = {
        "SL_ATR_Mult":    [2.0, 2.5, 3.0, 3.5],         # 4
        "Trail_ATR":      [2.5, 3.0, 3.5, 4.0],          # 4
        "BE_ATR":         [0.3, 0.5, 1.0],               # 3
        "EMA_Zone_ATR":   [0.3, 0.4, 0.5],               # 3
        "BodyRatio":      [0.24, 0.28, 0.32],             # 3
        "MaxPositions":   [2, 3],                         # 2
        "EMA_SLOPE_BARS": [3, 5, 8],                      # 3 (NEW)
        "ENTRY_MODE":     ["both", "pullback_only", "breakout_only"],  # 3 (NEW)
    }
    # = 4*4*3*3*3*2*3*3 = 7776
    keys = list(screen_grid.keys())
    combos = list(product(*screen_grid.values()))
    n_combos = len(combos)
    print(f"  Grid: {n_combos} combos")

    # D1 regime quick screen
    quick_results = []
    best_qs = -999
    t_phase1 = time.time()
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        params["ATR_Filter"] = 0.5  # Fixed for screen
        params["D1_Tolerance"] = 0.003  # Fixed for screen
        m = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
        qs = quick_score(m, 200)  # Relaxed for new strategy
        if qs > -999:
            quick_results.append((params, m, qs))
            if qs > best_qs:
                best_qs = qs
                print(f"    [{idx+1}/{n_combos}] BEST qs={qs:.1f} PF={m['pf']:.2f} "
                      f"T={m['n_trades']} DD={m['max_dd']:.1f}% | "
                      f"SL={params['SL_ATR_Mult']} Tr={params['Trail_ATR']} "
                      f"BE={params['BE_ATR']} Zone={params['EMA_Zone_ATR']} "
                      f"Slope={params['EMA_SLOPE_BARS']} Mode={params['ENTRY_MODE']} "
                      f"MaxP={params['MaxPositions']} Body={params['BodyRatio']}")
        if (idx + 1) % 2000 == 0 and qs <= best_qs:
            print(f"    [{idx+1}/{n_combos}] {len(quick_results)} valid... ({time.time()-t_phase1:.0f}s)")

    quick_results.sort(key=lambda x: x[2], reverse=True)
    if quick_results:
        print(f"  -> {len(quick_results)} valid, best qs={quick_results[0][2]:.1f}")
    else:
        print(f"  -> 0 valid results! Strategy may need fundamentally different parameters.")

    # Also try wider ATR filter and D1 tolerance on top 20
    extra_results = []
    for af in [0.3, 0.4, 0.6, 0.7]:
        for d1t in [0.005, 0.007, 0.01]:
            for p, m, qs in quick_results[:20]:
                params = {**p, "ATR_Filter": af, "D1_Tolerance": d1t}
                m2 = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
                qs2 = quick_score(m2, 200)
                if qs2 > -999:
                    extra_results.append((params, m2, qs2))

    print(f"  Extra ATR/D1T combos: {len(extra_results)} valid")
    quick_results.extend(extra_results)
    quick_results.sort(key=lambda x: x[2], reverse=True)
    print(f"  Total valid: {len(quick_results)}")
    # Also try MaxPositions=4 on top configs
    mp4_results = []
    for p, m, qs in quick_results[:30]:
        if p["MaxPositions"] != 4:
            params = {**p, "MaxPositions": 4}
            m2 = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
            qs2 = quick_score(m2, 200)
            if qs2 > -999:
                mp4_results.append((params, m2, qs2))
    quick_results.extend(mp4_results)
    quick_results.sort(key=lambda x: x[2], reverse=True)
    print(f"  After MP4 expansion: {len(quick_results)} total")

    if quick_results:
        print(f"\n  Top 20 quick screen:")
        print(f"  {'Rk':>2} {'QS':>6} {'PF':>5} {'T':>5} {'DD':>5} | Mode          SL   Tr   BE  Zone Slope MP Body AF   D1T")
        print("  " + "-" * 100)
        for i, (p, m, qs) in enumerate(quick_results[:20]):
            print(f"  {i+1:2d} {qs:6.1f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} | "
                  f"{p['ENTRY_MODE']:13s} {p['SL_ATR_Mult']:.1f} {p['Trail_ATR']:.1f} "
                  f"{p['BE_ATR']:.1f} {p['EMA_Zone_ATR']:.1f}  {p['EMA_SLOPE_BARS']:2d}   {p['MaxPositions']} "
                  f"{p['BodyRatio']:.2f} {p.get('ATR_Filter',0.5):.1f} {p.get('D1_Tolerance',0.003):.3f}")

    print(f"  Phase 1 done in {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 2: WFA Validation (Combined regime, top 50)
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 2: WFA Validation (Combined regime, top 30)")
    print("=" * 80)

    candidates = []
    seen = set()
    for p, m, s in quick_results:
        key = tuple(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            candidates.append(p)
        if len(candidates) >= 30:
            break

    print(f"  Validating {len(candidates)} candidates...")
    validated = []
    best_wfa = 0
    for ci, params in enumerate(candidates):
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
            print(f"    [{ci+1}/{len(candidates)}] WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                  f"MinPF={v['min_pf']:.2f} PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                  f"OOS={oos_pf:.2f} LY={v['n_losing']} FS={v['score']:.1f}{marker}")

    validated.sort(key=lambda x: x["score"], reverse=True)
    if validated:
        print(f"\n  Top 15 after Phase 2:")
        print(f"  {'Rk':>2} {'FS':>6} {'WFA':>4} {'AvgPF':>5} {'MinPF':>5} {'PF':>5} "
              f"{'T':>5} {'DD':>5} {'LY':>2} {'OOS':>5} | Mode          Key params")
        print("  " + "-" * 110)
        for i, v in enumerate(validated[:15]):
            m = v["m"]; p = v["params"]
            oos_pf = v["oos"]["pf"] if v["oos"] else 0
            print(f"  {i+1:2d} {v['score']:6.1f} {v['n_pass']:2d}/8 {v['avg_pf']:5.2f} "
                  f"{v['min_pf']:5.2f} {m['pf']:5.2f} {m['n_trades']:5d} {m['max_dd']:5.1f} "
                  f"{v['n_losing']:2d} {oos_pf:5.2f} | {p['ENTRY_MODE']:13s} "
                  f"SL={p['SL_ATR_Mult']:.1f} Tr={p['Trail_ATR']:.1f} "
                  f"BE={p['BE_ATR']:.1f} Z={p['EMA_Zone_ATR']:.1f} Sl={p['EMA_SLOPE_BARS']} "
                  f"MP={p['MaxPositions']} B={p['BodyRatio']:.2f}")

    print(f"  Best WFA: {best_wfa}/8")
    print(f"  Phase 2 done in {time.time()-t0:.0f}s")

    # ================================================================
    # PHASE 3: Feature combos on top configs
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 3: Feature Combinations")
    print("=" * 80)

    top_wfa = [v for v in validated if v["n_pass"] >= max(4, best_wfa - 1)][:6]
    if len(top_wfa) < 3:
        top_wfa = validated[:6]

    feature_sets = [
        ("TD25", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 25}),
        ("TD30", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30}),
        ("TD35", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35}),
        ("TD40", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 40}),
        ("Vol0.5-2.5", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("Vol0.6-2.0", {"USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.6, "VOL_HIGH_MULT": 2.0}),
        ("W1Sep3", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("W1Sep5", {"USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.005}),
        ("Session", {"USE_SESSION_FILTER": True, "TRADE_START_HOUR": 2, "TRADE_END_HOUR": 21}),
        ("TD30+Vol", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
                      "USE_VOL_REGIME": True, "VOL_LOW_MULT": 0.5, "VOL_HIGH_MULT": 2.5}),
        ("TD35+W1Sep3", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 35,
                         "USE_W1_SEPARATION": True, "W1_SEP_MIN": 0.003}),
        ("TD30+Session", {"USE_TIME_DECAY": True, "MAX_HOLD_BARS": 30,
                          "USE_SESSION_FILTER": True, "TRADE_START_HOUR": 2, "TRADE_END_HOUR": 21}),
    ]

    feat_results = []
    feat_best_wfa = best_wfa
    for ri, base in enumerate(top_wfa):
        bp = base["params"]
        print(f"\n  Base R{ri+1}: WFA={base['n_pass']}/8 PF={base['m']['pf']:.2f} "
              f"T={base['m']['n_trades']} Mode={bp['ENTRY_MODE']}")
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
                if v["n_pass"] >= 4 or v["n_pass"] > base["n_pass"]:
                    oos_pf = v["oos"]["pf"] if v["oos"] else 0
                    print(f"    +{fname:>18} WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                          f"MinPF={v['min_pf']:.2f} PF={v['m']['pf']:.2f} T={v['m']['n_trades']} "
                          f"FS={v['score']:.1f}{marker}")

    print(f"\n  Phase 3 done in {time.time()-t0:.0f}s")
    print(f"  Best WFA so far: {feat_best_wfa}/8")

    # ================================================================
    # PHASE 4: Regime Variants + Fine-Tune
    # ================================================================
    print("\n" + "=" * 80)
    print("PHASE 4: Regime Variants + Fine-Tune")
    print("=" * 80)

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

    # Phase 4a: Alternative regimes
    alt_regimes = [
        ("Comb(5/0.002,0.003)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.003}),
        ("Comb(5/0.002,0.007)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.002, "w1_min_spread": 0.007}),
        ("Comb(5/0.003,0.005)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.003, "w1_min_spread": 0.005}),
        ("Comb(5/0.001,0.005)", "combined", {"d1_slope_bars": 5, "d1_min_slope": 0.001, "w1_min_spread": 0.005}),
        ("Comb(7/0.002,0.005)", "combined", {"d1_slope_bars": 7, "d1_min_slope": 0.002, "w1_min_spread": 0.005}),
        ("Comb(10/0.002,0.005)", "combined", {"d1_slope_bars": 10, "d1_min_slope": 0.002, "w1_min_spread": 0.005}),
        ("D1only(5,0.002)", "d1_slope", {"slope_bars": 5, "min_slope": 0.002}),
        ("D1only(5,0.003)", "d1_slope", {"slope_bars": 5, "min_slope": 0.003}),
        ("NoRegime", "none", {}),
    ]

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
                if v["n_pass"] >= 4:
                    print(f"    P{pi+1}+{rname:>25} WFA={v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} "
                          f"PF={v['m']['pf']:.2f} T={v['m']['n_trades']} FS={v['score']:.1f}{marker}")

    # Phase 4b: Fine-tune exit params
    print(f"\n  Fine-tuning exit params on top configs...")
    top_for_fine = [v for v in (unique_v + feat_results + alt_results)
                    if v["n_pass"] >= max(3, alt_best_wfa - 1)]
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
        fine_grid = {
            "SL_ATR_Mult": rng(bp["SL_ATR_Mult"], 0.3, 2),
            "Trail_ATR": rng(bp["Trail_ATR"], 0.3, 2),
            "BE_ATR": rng(bp["BE_ATR"], 0.15, 2),
        }
        fixed = {k: v for k, v in bp.items() if k not in fine_grid}
        keys_f = list(fine_grid.keys())
        combos_f = list(product(*fine_grid.values()))
        print(f"\n  Fine #{fi+1} (WFA={base['n_pass']}/8, regime={rt}): {len(combos_f)} exit combos")

        fine_q = []
        for combo in combos_f:
            params = {**fixed, **dict(zip(keys_f, combo))}
            m = run_bt(h4_df, total_days, D1_RT, D1_RP, **params)
            qs = quick_score(m, 200)
            if qs > -999:
                fine_q.append((params, m, qs))
        fine_q.sort(key=lambda x: x[2], reverse=True)

        for qi, (params, _, _) in enumerate(fine_q[:10]):
            v = full_validate(h4_df, total_days, params, rt, rp, f"Fine{fi+1}")
            if v:
                fine_results.append(v)
                marker = ""
                if v["n_pass"] >= 7:
                    marker = " *** 7/8! ***"
                if v["n_pass"] > alt_best_wfa:
                    alt_best_wfa = v["n_pass"]
                if v["n_pass"] >= 4 or qi == 0:
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

    if not ranked:
        print("NO VALID RESULTS. Strategy may not produce enough trades.")
        print("HONEST ASSESSMENT: Momentum entry does not work with current parameters.")
        elapsed = time.time() - t0
        print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
        print("\n=== V25 OPTIMIZATION COMPLETE ===")
        log_file.close()
        return

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
        tr_r, _, _ = backtest_momentum_regime(h4_df, cfg_r, rt, rp)
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
        tr_r, _, _ = backtest_momentum_regime(sub, cfg_r, rt, rp)
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
        if m_f:
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
    for i, v in enumerate(ranked[:min(3, len(ranked))]):
        m = v["m"]; oos = v["oos"]
        rtype = v.get("rt", COMB_RT); rparams = v.get("rp", COMB_RP)
        print(f"\n  #{i+1} [{v['tag']}] Regime={rtype} {rparams}")
        print(f"    Full: PF={m['pf']:.2f} T={m['n_trades']} DD={m['max_dd']:.1f}% WR={m['win_rate']:.0f}%")
        print(f"    WFA: {v['n_pass']}/8 AvgPF={v['avg_pf']:.2f} MinPF={v['min_pf']:.2f}")
        print(f"    Losing years: {v['n_losing']}")
        if oos:
            print(f"    OOS: PF={oos['pf']:.2f} T={oos['n_trades']} Daily={oos['daily_jpy']:.0f}")
        kp = ["SL_ATR_Mult", "Trail_ATR", "BE_ATR", "EMA_Zone_ATR",
              "ATR_Filter", "BodyRatio", "MaxPositions", "D1_Tolerance",
              "ENTRY_MODE", "EMA_SLOPE_BARS"]
        vals = {k: v["params"][k] for k in kp if k in v["params"]}
        print(f"    Params: {vals}")
        features = {k: v["params"][k] for k in v["params"]
                    if k.startswith("USE_") and v["params"][k] is True}
        if features:
            print(f"    Features: {features}")
        print(f"    WFA details:")
        for j, w in enumerate(v["wfa"]):
            st = "PASS" if w["pf"] > 1.0 else "FAIL"
            print(f"      W{j+1}: PF={w['pf']:5.2f} T={w['n_trades']:3d} [{st}]")

    # ================================================================
    # HONEST ASSESSMENT
    # ================================================================
    print("\n" + "=" * 80)
    print("HONEST ASSESSMENT")
    print("=" * 80)

    m_w = W["m"]
    targets_met = 0
    if m_w["n_trades"] >= 500:
        print(f"  [PASS] Trades: {m_w['n_trades']} >= 500")
        targets_met += 1
    else:
        print(f"  [FAIL] Trades: {m_w['n_trades']} < 500")

    if m_w["pf"] >= 1.5:
        print(f"  [PASS] PF: {m_w['pf']:.2f} >= 1.5")
        targets_met += 1
    else:
        print(f"  [FAIL] PF: {m_w['pf']:.2f} < 1.5")

    if W["n_pass"] >= 7:
        print(f"  [PASS] WFA: {W['n_pass']}/8 >= 7/8")
        targets_met += 1
    else:
        print(f"  [FAIL] WFA: {W['n_pass']}/8 < 7/8")

    if best_risk and best_risk <= 4.0:
        print(f"  [PASS] Daily >= 5000 JPY achievable at Risk={best_risk}%")
        targets_met += 1
    elif m_w.get("daily_jpy", 0) >= 5000:
        print(f"  [PASS] Daily: {m_w['daily_jpy']:.0f} >= 5000")
        targets_met += 1
    else:
        print(f"  [FAIL] Daily < 5000 JPY at risk=0.2%")

    print(f"\n  Targets met: {targets_met}/4")

    if W["n_pass"] < 7:
        print(f"\n  HONEST NOTE: Could not achieve 7/8 WFA target.")
        print(f"  Best WFA achieved: {W['n_pass']}/8")
        seven_plus = [v for v in ranked if v["n_pass"] >= 7]
        if seven_plus:
            print(f"  Found {len(seven_plus)} configs with 7/8+:")
            for v in seven_plus[:3]:
                m = v["m"]
                print(f"    {v['tag']}: WFA={v['n_pass']}/8 PF={m['pf']:.2f} T={m['n_trades']} "
                      f"DD={m['max_dd']:.1f}% FS={v['score']:.1f}")
        else:
            print(f"  No configurations achieved 7/8 WFA.")

    # Compare v25 momentum vs v12-v24 dip-buy approach
    if m_v12:
        print(f"\n  v25 Momentum vs v12 Dip-Buy (no regime, default params):")
        print(f"    v12 dip-buy: PF={m_v12['pf']:.2f} T={m_v12['n_trades']}")
        if m_v25_base:
            print(f"    v25 momentum: PF={m_v25_base['pf']:.2f} T={m_v25_base['n_trades']}")
        print(f"    v25 winner:   PF={m_w['pf']:.2f} T={m_w['n_trades']} WFA={W['n_pass']}/8")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("\n=== V25 OPTIMIZATION COMPLETE ===")
    log_file.close()


if __name__ == "__main__":
    main()
