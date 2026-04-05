"""
backtest_gold_fast.py -- Performance-optimized backtester
Overrides GoldBacktester.run() with O(n log m) lookups instead of O(n*m).

All trading logic, parameters, and signal generation are IDENTICAL to
backtest_gold.py. Only data access patterns are optimized.

Key optimizations:
1. np.searchsorted for H4/H1/USDJPY index lookups (replaces boolean masks)
2. Pre-computed USDJPY correlation signals (eliminates per-bar EMA/ATR recalc)
3. Numpy arrays for H1 close/RSI series (avoids pandas iloc overhead)
4. Pre-computed H1 OHLC numpy arrays for S/R, candle pattern, divergence
5. Cached index positions to avoid repeated pandas operations
"""

import pandas as pd
import numpy as np
from backtest_gold import (
    GoldBacktester, GoldConfig,
    calc_sma, calc_ema, calc_rsi, calc_atr, calc_adx, calc_bb,
    calc_stochastic, calc_keltner,
    get_h4_rsi_alignment,
    fetch_gold_data,
)
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# Optimized standalone functions (replace per-bar mask versions)
# ============================================================

def precompute_correlation_signals(usdjpy_df, cfg):
    """Pre-compute correlation signal for every bar in usdjpy_df.

    Returns a numpy int8 array of length len(usdjpy_df) where each element
    is the correlation signal (-1, 0, +1) at that bar index.
    """
    n = len(usdjpy_df)
    signals = np.zeros(n, dtype=np.int8)
    min_bars = max(cfg.CORR_MA_SLOW, 14) + 6

    if n < min_bars:
        return signals

    # Use pre-computed indicators if available, otherwise compute once
    if "ema_fast" in usdjpy_df.columns:
        fast_ema = usdjpy_df["ema_fast"].values
        slow_ema = usdjpy_df["ema_slow"].values
        atr = usdjpy_df["atr"].values
    else:
        close = usdjpy_df["Close"]
        fast_ema_s = calc_ema(close, cfg.CORR_MA_FAST)
        slow_ema_s = calc_ema(close, cfg.CORR_MA_SLOW)
        atr_s = calc_atr(usdjpy_df["High"], usdjpy_df["Low"], usdjpy_df["Close"], 14)
        fast_ema = fast_ema_s.values
        slow_ema = slow_ema_s.values
        atr = atr_s.values

    threshold = cfg.CORR_THRESHOLD

    for i in range(min_bars, n):
        f_cur = fast_ema[i]
        s_cur = slow_ema[i]
        a_cur = atr[i]

        if np.isnan(f_cur) or np.isnan(s_cur) or np.isnan(a_cur) or a_cur <= 0:
            continue

        if i < 5:
            continue
        f_5ago = fast_ema[i - 5]
        if np.isnan(f_5ago):
            continue

        move_speed = (f_cur - f_5ago) / a_cur

        if f_cur < s_cur and move_speed < -threshold:
            signals[i] = 1
        elif f_cur > s_cur and move_speed > threshold:
            signals[i] = -1

    return signals


def get_sr_signal_fast(h1_highs, h1_lows, h1_end_idx, current_price, current_atr, cfg):
    """Support/Resistance signal using pre-extracted numpy arrays.

    h1_highs, h1_lows: full numpy arrays of H1 High/Low
    h1_end_idx: index of the last valid H1 bar (exclusive upper bound for slicing)
    """
    if h1_end_idx < cfg.SR_LOOKBACK:
        return 0

    start = h1_end_idx - cfg.SR_LOOKBACK
    highs = h1_highs[start:h1_end_idx]
    lows = h1_lows[start:h1_end_idx]
    strength = cfg.SR_SWING_STRENGTH
    length = len(highs)

    levels = []

    # Find swing highs (resistance candidates)
    for i in range(strength, length - strength):
        left = highs[i - strength:i]
        right = highs[i + 1:i + 1 + strength]
        if len(left) > 0 and len(right) > 0 and highs[i] > left.max() and highs[i] > right.max():
            levels.append(highs[i])

    # Find swing lows (support candidates)
    for i in range(strength, length - strength):
        left = lows[i - strength:i]
        right = lows[i + 1:i + 1 + strength]
        if len(left) > 0 and len(right) > 0 and lows[i] < left.min() and lows[i] < right.min():
            levels.append(lows[i])

    if not levels:
        return 0

    # Cluster levels within SR_CLUSTER_ATR * ATR
    cluster_dist = cfg.SR_CLUSTER_ATR * current_atr
    levels.sort()
    clustered = []
    cluster = [levels[0]]
    for i in range(1, len(levels)):
        if levels[i] - levels[i - 1] <= cluster_dist:
            cluster.append(levels[i])
        else:
            clustered.append(np.mean(cluster))
            cluster = [levels[i]]
    clustered.append(np.mean(cluster))

    # Find nearest support (below price) and resistance (above price)
    supports = [lv for lv in clustered if lv < current_price]
    resistances = [lv for lv in clustered if lv > current_price]

    proximity = cfg.SR_PROXIMITY_ATR * current_atr

    if supports:
        nearest_support = max(supports)
        if current_price - nearest_support <= proximity:
            return 1

    if resistances:
        nearest_resistance = min(resistances)
        if nearest_resistance - current_price <= proximity:
            return -1

    return 0


def get_candle_pattern_fast(h1_open, h1_high, h1_low, h1_close, h1_end_idx):
    """Detect candlestick patterns from last 3 H1 bars using numpy arrays.

    h1_end_idx: the index such that bars up to (but not including) h1_end_idx are valid.
    We use h1_end_idx-3, h1_end_idx-2, h1_end_idx-1 as c0, c1, c2.
    """
    if h1_end_idx < 3:
        return 0

    # c0 = oldest, c1 = middle, c2 = most recent (matches original logic)
    idx0 = h1_end_idx - 3
    idx1 = h1_end_idx - 2
    idx2 = h1_end_idx - 1

    o0, h0, l0, cl0 = h1_open[idx0], h1_high[idx0], h1_low[idx0], h1_close[idx0]
    o1, h1_v, l1, cl1 = h1_open[idx1], h1_high[idx1], h1_low[idx1], h1_close[idx1]
    o2, h2, l2, cl2 = h1_open[idx2], h1_high[idx2], h1_low[idx2], h1_close[idx2]

    body2 = cl2 - o2
    body1 = cl1 - o1
    body0 = cl0 - o0

    abs_body2 = abs(body2)
    abs_body1 = abs(body1)

    range2 = h2 - l2
    range1 = h1_v - l1

    if range2 == 0:
        range2 = 1e-10
    if range1 == 0:
        range1 = 1e-10

    # Bullish Engulfing
    if body1 < 0 and body2 > 0 and o2 <= cl1 and cl2 >= o1:
        return 1

    # Bearish Engulfing
    if body1 > 0 and body2 < 0 and o2 >= cl1 and cl2 <= o1:
        return -1

    # Hammer
    lower_shadow2 = min(o2, cl2) - l2
    upper_shadow2 = h2 - max(o2, cl2)
    if abs_body2 > 0 and lower_shadow2 >= abs_body2 * 2 and upper_shadow2 <= abs_body2 * 0.5:
        return 1

    # Shooting Star
    if abs_body2 > 0 and upper_shadow2 >= abs_body2 * 2 and lower_shadow2 <= abs_body2 * 0.5:
        return -1

    # Morning Star
    if body0 < 0 and abs_body1 < abs(body0) * 0.3 and body2 > 0 and cl2 > (o0 + cl0) / 2:
        return 1

    # Evening Star
    if body0 > 0 and abs_body1 < abs(body0) * 0.3 and body2 < 0 and cl2 < (o0 + cl0) / 2:
        return -1

    return 0


def get_divergence_fast(h1_close_arr, h1_rsi_arr, h1_end_idx, lookback=30, swing_strength=3):
    """Divergence detection using pre-extracted numpy arrays.

    h1_close_arr, h1_rsi_arr: full numpy arrays
    h1_end_idx: exclusive upper bound of valid data
    """
    if h1_end_idx < lookback:
        return 0

    closes = h1_close_arr[h1_end_idx - lookback:h1_end_idx]
    rsi = h1_rsi_arr[h1_end_idx - lookback:h1_end_idx]

    if np.any(np.isnan(closes)) or np.any(np.isnan(rsi)):
        return 0

    # Find swing lows
    swing_lows = []
    for i in range(swing_strength, lookback - swing_strength):
        left = closes[i - swing_strength:i]
        right = closes[i + 1:i + 1 + swing_strength]
        if len(left) > 0 and len(right) > 0 and closes[i] < left.min() and closes[i] < right.min():
            swing_lows.append(i)

    # Find swing highs
    swing_highs = []
    for i in range(swing_strength, lookback - swing_strength):
        left = closes[i - swing_strength:i]
        right = closes[i + 1:i + 1 + swing_strength]
        if len(left) > 0 and len(right) > 0 and closes[i] > left.max() and closes[i] > right.max():
            swing_highs.append(i)

    # Bullish divergence (swing lows)
    if len(swing_lows) >= 2:
        i1, i2 = swing_lows[-2], swing_lows[-1]
        if closes[i2] < closes[i1] and rsi[i2] > rsi[i1]:
            return 1
        if closes[i2] > closes[i1] and rsi[i2] < rsi[i1]:
            return 1

    # Bearish divergence (swing highs)
    if len(swing_highs) >= 2:
        i1, i2 = swing_highs[-2], swing_highs[-1]
        if closes[i2] > closes[i1] and rsi[i2] < rsi[i1]:
            return -1
        if closes[i2] < closes[i1] and rsi[i2] > rsi[i1]:
            return -1

    return 0


def calc_channel_signal_fast(h1_close_arr, h1_end_idx, lookback=40):
    """Channel regression signal from numpy array."""
    if h1_end_idx < lookback + 1:
        return 0
    # Original uses close_series[-(lookback+1):-1], which is the lookback bars ending
    # one bar before the last. With our indexing, that's h1_end_idx-lookback-1 to h1_end_idx-1
    # But actually original receives h1_df[h1_mask]["Close"] and calls calc_channel_signal
    # which does series[-(lookback+1):-1] = last lookback bars before the final bar.
    y = h1_close_arr[h1_end_idx - lookback - 1:h1_end_idx - 1]
    if len(y) < lookback:
        return 0
    x = np.arange(lookback)
    slope, intercept = np.polyfit(x, y, 1)
    predicted = intercept + slope * x
    std = np.std(y - predicted)
    upper = predicted[-1] + 2 * std
    lower = predicted[-1] - 2 * std
    if upper == lower:
        return 0
    pos = (y[-1] - lower) / (upper - lower)
    if pos < 0.2 and slope > 0:
        return 1
    if pos > 0.8 and slope < 0:
        return -1
    return 0


# ============================================================
# Optimized run method
# ============================================================
class GoldBacktesterFast(GoldBacktester):
    """Drop-in replacement with optimized run() method."""

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None):
        cfg = self.cfg

        # Store USDJPY data
        self.usdjpy_df = usdjpy_df

        # Indicator calculation (identical to original)
        h4_df = h4_df.copy()
        h1_df = h1_df.copy()
        m15_df = m15_df.copy()

        h4_df["ma_fast"] = calc_sma(h4_df["Close"], cfg.H4_MA_FAST)
        h4_df["ma_slow"] = calc_sma(h4_df["Close"], cfg.H4_MA_SLOW)
        h4_df["adx"], h4_df["plus_di"], h4_df["minus_di"] = calc_adx(
            h4_df["High"], h4_df["Low"], h4_df["Close"], cfg.H4_ADX_PERIOD)

        # v5.2: H4 SMA(50) slope for macro trend
        h4_df["ma_slow_slope"] = h4_df["ma_slow"] - h4_df["ma_slow"].shift(cfg.H4_SLOPE_PERIOD)

        # v3.0: H4 RSI
        h4_df["rsi"] = calc_rsi(h4_df["Close"], cfg.H4_RSI_PERIOD)

        h1_df["ma_fast"] = calc_ema(h1_df["Close"], cfg.H1_MA_FAST)
        h1_df["ma_slow"] = calc_ema(h1_df["Close"], cfg.H1_MA_SLOW)
        h1_df["rsi"] = calc_rsi(h1_df["Close"], cfg.H1_RSI_PERIOD)
        h1_df["bb_upper"], h1_df["bb_mid"], h1_df["bb_lower"] = calc_bb(
            h1_df["Close"], cfg.H1_BB_PERIOD, cfg.H1_BB_DEV)

        # v9.0: Range Strategy v2 indicators (Stochastic + Keltner on H1)
        _use_rv2 = getattr(cfg, 'USE_RANGE_STRATEGY_V2', False)
        if _use_rv2:
            _stoch_p = getattr(cfg, 'RANGE_V2_STOCH_PERIOD', 14)
            _stoch_s = getattr(cfg, 'RANGE_V2_STOCH_SMOOTH', 3)
            h1_df["stoch_k"], h1_df["stoch_d"] = calc_stochastic(
                h1_df["High"], h1_df["Low"], h1_df["Close"],
                k_period=_stoch_p, k_smooth=_stoch_s, d_smooth=_stoch_s)
            # Range v2 BB (may differ from trend BB params)
            _rv2_bb_p = getattr(cfg, 'RANGE_V2_BB_PERIOD', 20)
            _rv2_bb_d = getattr(cfg, 'RANGE_V2_BB_DEV', 2.0)
            h1_df["rv2_bb_upper"], h1_df["rv2_bb_mid"], h1_df["rv2_bb_lower"] = calc_bb(
                h1_df["Close"], _rv2_bb_p, _rv2_bb_d)
            # Keltner Channel (optional alternative)
            if getattr(cfg, 'RANGE_V2_USE_KELTNER', False):
                _kc_p = getattr(cfg, 'RANGE_V2_KELTNER_PERIOD', 20)
                _kc_m = getattr(cfg, 'RANGE_V2_KELTNER_ATR_MULTI', 1.5)
                h1_df["kc_upper"], h1_df["kc_mid"], h1_df["kc_lower"] = calc_keltner(
                    h1_df["Close"], h1_df["High"], h1_df["Low"],
                    ema_period=_kc_p, atr_multi=_kc_m)

        m15_df["ma_fast"] = calc_ema(m15_df["Close"], cfg.M15_MA_FAST)
        m15_df["ma_slow"] = calc_ema(m15_df["Close"], cfg.M15_MA_SLOW)

        # v2.0: M15 ATR calculation
        m15_df["atr"] = calc_atr(m15_df["High"], m15_df["Low"], m15_df["Close"], cfg.ATR_PERIOD)
        m15_df["atr_avg"] = m15_df["atr"].rolling(window=cfg.VOL_REGIME_PERIOD).mean()

        # v3.0: Pre-compute USDJPY indicators
        if self.usdjpy_df is not None:
            self.usdjpy_df = self.usdjpy_df.copy()
            self.usdjpy_df["ema_fast"] = calc_ema(self.usdjpy_df["Close"], cfg.CORR_MA_FAST)
            self.usdjpy_df["ema_slow"] = calc_ema(self.usdjpy_df["Close"], cfg.CORR_MA_SLOW)
            self.usdjpy_df["atr"] = calc_atr(
                self.usdjpy_df["High"], self.usdjpy_df["Low"], self.usdjpy_df["Close"], 14)

        # ============================================================
        # OPTIMIZATION: Pre-compute numpy arrays and searchsorted indices
        # ============================================================

        # v8.0: Pre-compute H1 range for range compression detection
        h1_df["range"] = h1_df["High"] - h1_df["Low"]

        # Convert datetime indices to int64 for searchsorted
        h4_index_i64 = h4_df.index.values.astype(np.int64)
        h1_index_i64 = h1_df.index.values.astype(np.int64)
        m15_index_i64 = m15_df.index.values.astype(np.int64)

        # Pre-extract H4 row data as dict-of-arrays for fast access
        h4_ma_fast = h4_df["ma_fast"].values
        h4_ma_slow = h4_df["ma_slow"].values
        h4_adx = h4_df["adx"].values
        h4_plus_di = h4_df["plus_di"].values
        h4_minus_di = h4_df["minus_di"].values
        h4_ma_slow_slope = h4_df["ma_slow_slope"].values
        h4_rsi = h4_df["rsi"].values
        h4_close_arr = h4_df["Close"].values
        h4_open_arr = h4_df["Open"].values

        # v9.1: Pre-compute H4 chop signal (direction change frequency)
        _use_chop = getattr(cfg, 'USE_CHOP_FILTER', False)
        _chop_lookback = getattr(cfg, 'CHOP_LOOKBACK', 8)
        _chop_threshold = getattr(cfg, 'CHOP_THRESHOLD', 5)
        _chop_boost = getattr(cfg, 'CHOP_SCORE_BOOST', 3)
        h4_chop = np.zeros(len(h4_df), dtype=np.int8)  # 1 = choppy
        if _use_chop:
            h4_dir = np.sign(h4_close_arr - h4_open_arr)  # +1=up, -1=down, 0=doji
            for k in range(_chop_lookback, len(h4_dir)):
                window = h4_dir[k - _chop_lookback:k]
                # Count direction changes (sign flips, ignoring doji)
                nonzero = window[window != 0]
                if len(nonzero) >= 2:
                    changes = np.sum(np.diff(nonzero) != 0)
                    if changes >= _chop_threshold:
                        h4_chop[k] = 1

        # v9.2: Adaptive chandelier config
        _use_adaptive_chand = getattr(cfg, 'USE_ADAPTIVE_CHANDELIER', False)
        _adaptive_chand_adx = getattr(cfg, 'ADAPTIVE_CHAND_ADX_THRESHOLD', 20)
        _adaptive_chand_multi = getattr(cfg, 'ADAPTIVE_CHAND_ATR_MULTI', 1.2)

        # v8.0: H1 range for compression detection
        h1_range_arr = h1_df["range"].values.copy()

        # Pre-extract H1 data as numpy arrays
        h1_close_arr = h1_df["Close"].values.copy()
        h1_open_arr = h1_df["Open"].values.copy()
        h1_high_arr = h1_df["High"].values.copy()
        h1_low_arr = h1_df["Low"].values.copy()
        h1_ma_fast = h1_df["ma_fast"].values
        h1_ma_slow = h1_df["ma_slow"].values
        h1_rsi_arr = h1_df["rsi"].values.copy()
        h1_bb_upper = h1_df["bb_upper"].values
        h1_bb_lower = h1_df["bb_lower"].values
        h1_bb_mid = h1_df["bb_mid"].values

        # v9.0: Range Strategy v2 numpy arrays
        h1_stoch_k = h1_df["stoch_k"].values if "stoch_k" in h1_df.columns else None
        h1_stoch_d = h1_df["stoch_d"].values if "stoch_d" in h1_df.columns else None
        h1_rv2_bb_upper = h1_df["rv2_bb_upper"].values if "rv2_bb_upper" in h1_df.columns else None
        h1_rv2_bb_mid = h1_df["rv2_bb_mid"].values if "rv2_bb_mid" in h1_df.columns else None
        h1_rv2_bb_lower = h1_df["rv2_bb_lower"].values if "rv2_bb_lower" in h1_df.columns else None
        h1_kc_upper = h1_df["kc_upper"].values if "kc_upper" in h1_df.columns else None
        h1_kc_mid = h1_df["kc_mid"].values if "kc_mid" in h1_df.columns else None
        h1_kc_lower = h1_df["kc_lower"].values if "kc_lower" in h1_df.columns else None

        # Pre-extract M15 data as numpy arrays
        m15_close = m15_df["Close"].values
        m15_high = m15_df["High"].values
        m15_low = m15_df["Low"].values
        m15_open = m15_df["Open"].values
        m15_ma_fast = m15_df["ma_fast"].values
        m15_ma_slow = m15_df["ma_slow"].values
        m15_atr = m15_df["atr"].values
        m15_atr_avg = m15_df["atr_avg"].values
        m15_volume = m15_df["Volume"].values if "Volume" in m15_df.columns else None

        # v10.0: Pre-extract Spread array for realistic execution simulation
        m15_spread = m15_df["Spread"].values if "Spread" in m15_df.columns else None

        # OPTIMIZATION: Pre-compute USDJPY correlation signals
        usdjpy_corr_signals = None
        usdjpy_index_i64 = None
        if self.usdjpy_df is not None and cfg.USE_CORRELATION:
            usdjpy_corr_signals = precompute_correlation_signals(self.usdjpy_df, cfg)
            usdjpy_index_i64 = self.usdjpy_df.index.values.astype(np.int64)

        total_bars = len(m15_df)
        print(f"\n[BT] Backtest start: {m15_df.index[0].date()} -> {m15_df.index[-1].date()}")
        print(f"   M15 bars: {total_bars:,}")
        print(f"   Config: Risk={cfg.RISK_PERCENT}% ATR_SL={cfg.SL_ATR_MULTI}x ATR_TP={cfg.TP_ATR_MULTI}x MinScore={cfg.MIN_SCORE}")
        print(f"   v2.0: VolRegime={cfg.VOL_REGIME_LOW}/{cfg.VOL_REGIME_HIGH} Session={cfg.USE_SESSION_BONUS} Momentum={cfg.USE_MOMENTUM} PartialClose={cfg.USE_PARTIAL_CLOSE}")
        print(f"   v3.0: Corr={cfg.USE_CORRELATION} Div={cfg.USE_DIVERGENCE} SR={cfg.USE_SR_LEVELS} Candle={cfg.USE_CANDLE_PATTERNS} H4RSI={cfg.USE_H4_RSI}")
        print(f"   v3.0: Chandelier={cfg.USE_CHANDELIER_EXIT} EquityCurve={cfg.USE_EQUITY_CURVE} AdaptSize={cfg.USE_ADAPTIVE_SIZING}")
        print(f"   v4.0 Defense: News={cfg.USE_NEWS_FILTER} Weekend={cfg.USE_WEEKEND_CLOSE} CircuitBreaker={cfg.DAILY_MAX_LOSS_PCT}% CrashATR={cfg.CRASH_ATR_MULTI}x")
        print(f"   v4.0 Attack: MomentumBurst={cfg.USE_MOMENTUM_BURST} VolClimax={cfg.USE_VOLUME_CLIMAX} Pyramid={cfg.MAX_PYRAMID_POSITIONS} Reversal={cfg.USE_REVERSAL_MODE}")
        print(f"   v5.2: TrendSL Widen={cfg.TREND_SL_WIDEN}x Tighten={cfg.TREND_SL_TIGHTEN}x SlopePeriod={cfg.H4_SLOPE_PERIOD}")
        # Safe access for optional features (may not exist in GoldConfig)
        _hsf = getattr(cfg, 'USE_HARD_SESSION_FILTER', False)
        _srat = getattr(cfg, 'USE_SRAT', False)
        _dd_esc = sorted(getattr(cfg, 'DD_ESCALATION', [(10, 12), (15, 15), (20, 18)]), reverse=True)
        _dz = getattr(cfg, 'USE_DEAD_ZONE_FILTER', False)
        _skip11 = getattr(cfg, 'SKIP_SCORE_11', False)
        if _hsf: print(f"   v5.3: HardSessionFilter=True")
        if _dz: print(f"   v5.4: DeadZoneFilter=True SkipScore11={_skip11}")
        if _srat: print(f"   v6.0: SRAT=True")
        _real_spread = getattr(cfg, 'USE_REALISTIC_SPREAD', False)
        _slippage = getattr(cfg, 'SLIPPAGE_POINTS', 0)
        _commission = getattr(cfg, 'COMMISSION_PER_LOT', 0)
        _intrabar_order = getattr(cfg, 'USE_INTRABAR_SLTP_ORDER', False)
        if _real_spread or _commission > 0:
            print(f"   v10.0: RealisticSpread={_real_spread} Slippage={_slippage}pt Commission=${_commission}/lot IntrabarOrder={_intrabar_order}")
        print(f"   [FAST] Using optimized run() with searchsorted + pre-computed signals")

        for i in range(100, total_bars):
            ct = m15_df.index[i]
            cc = m15_close[i]
            ch = m15_high[i]
            cl = m15_low[i]
            co = m15_open[i]
            # v11.0: Use next bar's Open for entry to avoid look-ahead bias
            # Signal generated at bar[i] Close -> entry at bar[i+1] Open (matches MT5 live)
            next_bar_open = m15_open[i + 1] if i + 1 < total_bars else cc

            # v10.0: Get bar spread from CSV data
            _bar_spread = float(m15_spread[i]) if m15_spread is not None else None

            # v4.0: Daily circuit breaker reset
            bar_day = ct.date() if hasattr(ct, 'date') else ct
            if bar_day != self.current_day:
                self.current_day = bar_day
                self.daily_pnl = 0.0
                self.circuit_breaker = False

            if self.circuit_breaker:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v9.2: Pass H4 ADX state to _manage_positions for adaptive chandelier
            # (self._current_h4_adx is updated after H4 lookup each bar, see below)

            self._manage_positions(ch, cl, cc, ct, i, m15_df,
                                   bar_open=co, bar_spread_points=_bar_spread)

            # v9.0: Range v2 position management (time exit, partial close)
            # BB mid TP update needs H1 data, so do bar-only checks here
            if _use_rv2:
                for pos in list(self.open_positions):
                    if pos.get('entry_type') != 'range_v2':
                        continue
                    # Time exit
                    _time_bar = pos.get('rv2_time_exit_bar', 0)
                    if _time_bar > 0 and i >= _time_bar:
                        self._close_position(pos, cc, ct, "TimeExit", i,
                                             bar_spread_points=_bar_spread)
                        continue
                    # Partial close at 0.5*ATR profit
                    if not pos.get('rv2_partial_done', True):
                        _partial_dist = pos.get('rv2_partial_atr', 0)
                        if _partial_dist > 0:
                            if pos['direction'] == 'BUY':
                                profit_price = cc - pos['entry']
                            else:
                                profit_price = pos['entry'] - cc
                            if profit_price >= _partial_dist:
                                _ratio = pos.get('rv2_partial_ratio', 0.5)
                                closed_lot = pos['original_lot'] * _ratio
                                remaining = pos['lot'] - closed_lot
                                if remaining < cfg.MIN_LOT:
                                    remaining = cfg.MIN_LOT
                                    closed_lot = pos['lot'] - remaining
                                if closed_lot > 0:
                                    pt = cfg.POINT
                                    # v10.1: Apply exit spread to RV2 partial close
                                    _rv2_hs = 0
                                    if _real_spread and _bar_spread is not None:
                                        _rv2_hs = _bar_spread * pt * 0.5
                                    if pos['direction'] == 'BUY':
                                        _rv2_exit = cc - _rv2_hs
                                        _rv2_profit = _rv2_exit - pos['entry']
                                    else:
                                        _rv2_exit = cc + _rv2_hs
                                        _rv2_profit = pos['entry'] - _rv2_exit
                                    pnl_pts = _rv2_profit / pt
                                    pnl_usd = pnl_pts * pt * cfg.CONTRACT_SIZE * closed_lot
                                    # v10.0: Commission on RV2 partial close
                                    _comm = getattr(cfg, 'COMMISSION_PER_LOT', 0)
                                    if _comm > 0:
                                        pnl_usd -= _comm * closed_lot * 0.5
                                    pnl_jpy = pnl_usd * 150.0
                                    self.balance += pnl_jpy
                                    self.peak_balance = max(self.peak_balance, self.balance)
                                    self.daily_pnl += pnl_jpy
                                    self.trades.append({
                                        "open_time": pos["open_time"],
                                        "close_time": ct,
                                        "direction": pos["direction"],
                                        "entry": round(pos["entry"], 2),
                                        "exit": round(_rv2_exit, 2),
                                        "lot": closed_lot,
                                        "pnl_pts": round(pnl_pts, 1),
                                        "pnl_usd": round(pnl_usd, 2),
                                        "pnl_jpy": round(pnl_jpy, 0),
                                        "balance": round(self.balance, 0),
                                        "reason": "RV2_Partial",
                                        "score": pos["score"],
                                        "entry_type": "range_v2",
                                        "momentum_burst": False,
                                    })
                                    self.recent_trade_pnls.append(pnl_jpy)
                                    pos['lot'] = remaining
                                # Move SL to breakeven
                                pos['sl'] = pos['entry'] + (10 * cfg.POINT if pos['direction'] == 'BUY' else -10 * cfg.POINT)
                                pos['rv2_partial_done'] = True
                                pos['breakeven_done'] = True

            if self.balance > self.peak_balance:
                self.peak_balance = self.balance
            current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0

            hour = ct.hour if hasattr(ct, "hour") else 12

            # v4.0: Weekend close
            if self.check_weekend(ct):
                if self.open_positions:
                    for pos in list(self.open_positions):
                        self._close_position(pos, cc, ct, "Weekend", i,
                                             bar_spread_points=_bar_spread)
                    self.weekend_closes += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            if hour < cfg.TRADE_START_HOUR or hour >= cfg.TRADE_END_HOUR:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            if hasattr(ct, "dayofweek") and ct.dayofweek == 4 and hour >= 18:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # Cooldown after SL
            if i < self.cooldown_until:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v4.0: News filter
            if self.simulate_news_filter(ct):
                self.news_blocks += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v5.3: Hard session filter (optional)
            if _hsf and hasattr(self, 'check_hard_session_filter') and self.check_hard_session_filter(ct):
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v2.0: ATR and volatility regime check
            current_atr = m15_atr[i]
            current_atr_avg = m15_atr_avg[i]
            if np.isnan(current_atr) or np.isnan(current_atr_avg) or current_atr_avg <= 0:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v4.0: Dynamic spread check
            if not self.check_dynamic_spread(current_atr, current_atr_avg):
                self.spread_blocks += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v4.0: Advanced 4-state regime
            regime = self.get_advanced_regime(current_atr, current_atr_avg)
            if regime == 0:  # Crash
                self.crash_skips += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            vol_ratio = current_atr / current_atr_avg

            # High volatility regime: SL bonus
            sl_multi = cfg.SL_ATR_MULTI
            if vol_ratio > cfg.VOL_REGIME_HIGH:
                sl_multi += cfg.HIGH_VOL_SL_BONUS

            # v2.0: Dynamic SL/TP in points
            atr_points = current_atr / cfg.POINT
            dynamic_sl_points = atr_points * sl_multi
            dynamic_sl_points = max(cfg.MIN_SL_POINTS, min(cfg.MAX_SL_POINTS, dynamic_sl_points))
            dynamic_tp_points = atr_points * cfg.TP_ATR_MULTI
            if dynamic_tp_points < dynamic_sl_points * 1.5:
                dynamic_tp_points = dynamic_sl_points * 1.5

            # ============================================================
            # OPTIMIZATION: H4 lookup via searchsorted
            # ============================================================
            ct_i64 = m15_index_i64[i]
            h4_pos = np.searchsorted(h4_index_i64, ct_i64, side='right')
            # h4_pos is the number of H4 bars with index <= ct
            if h4_pos < 2:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h4_idx = h4_pos - 1  # last H4 bar <= ct

            # Build h4_row as a lightweight dict (avoids pandas Series overhead)
            h4_row_ma_fast = h4_ma_fast[h4_idx]
            h4_row_ma_slow = h4_ma_slow[h4_idx]
            h4_row_adx = h4_adx[h4_idx]
            h4_row_plus_di = h4_plus_di[h4_idx]
            h4_row_minus_di = h4_minus_di[h4_idx]
            h4_row_ma_slow_slope = h4_ma_slow_slope[h4_idx]
            h4_row_rsi = h4_rsi[h4_idx]

            # v9.2: Update H4 ADX for adaptive chandelier in _manage_positions
            if _use_adaptive_chand:
                self._current_h4_adx = h4_row_adx

            # ============================================================
            # OPTIMIZATION: H1 lookup via searchsorted
            # ============================================================
            h1_pos = np.searchsorted(h1_index_i64, ct_i64, side='right')
            if h1_pos < 4:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h1_idx = h1_pos - 1  # last H1 bar index <= ct
            h1_idx_prev = h1_pos - 2

            # H1 current and previous bar values
            h1_curr_ma_fast = h1_ma_fast[h1_idx]
            h1_curr_ma_slow = h1_ma_slow[h1_idx]
            h1_curr_rsi = h1_rsi_arr[h1_idx]
            h1_curr_bb_upper = h1_bb_upper[h1_idx]
            h1_curr_bb_lower = h1_bb_lower[h1_idx]
            h1_curr_close = h1_close_arr[h1_idx]
            h1_prev_close = h1_close_arr[h1_idx_prev]

            m15_curr_ma_fast = m15_ma_fast[i]
            m15_curr_ma_slow = m15_ma_slow[i]
            m15_prev_ma_fast = m15_ma_fast[i - 1]
            m15_prev_ma_slow = m15_ma_slow[i - 1]

            # v9.0: Range v2 BB mid TP check (needs H1 index)
            if _use_rv2:
                for pos in list(self.open_positions):
                    if pos.get('entry_type') != 'range_v2':
                        continue
                    _bb_mid_target = pos.get('rv2_bb_mid_target')
                    if _bb_mid_target is not None:
                        _curr_bb_m = h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]
                        if not np.isnan(_curr_bb_m):
                            pos['rv2_bb_mid_target'] = _curr_bb_m
                            if pos['direction'] == 'BUY' and ch >= _curr_bb_m:
                                self._close_position(pos, _curr_bb_m, ct, "BB_Mid_TP", i,
                                                     bar_spread_points=_bar_spread)
                                continue
                            elif pos['direction'] == 'SELL' and cl <= _curr_bb_m:
                                self._close_position(pos, _curr_bb_m, ct, "BB_Mid_TP", i,
                                                     bar_spread_points=_bar_spread)
                                continue

            # ---- Optional Dead Zone Hour Filter (moved early for range v2) ----
            dead_zone_all = False
            dead_zone_normal = False
            if _dz:
                _dz_all = getattr(cfg, 'DEAD_ZONE_ALL_HOURS', set())
                _dz_norm = getattr(cfg, 'DEAD_ZONE_NORMAL_HOURS', set())
                if hour in _dz_all:
                    dead_zone_all = True
                elif hour in _dz_norm:
                    dead_zone_normal = True

            # ============================================================
            # v9.0: Range Strategy v2 -- BB Mean Reversion + Stochastic
            # When H4 ADX < threshold, SWITCH to range-bound logic
            # ============================================================
            _rv2_entered = False
            if _use_rv2 and len(self.open_positions) == 0:
                _rv2_adx_thresh = getattr(cfg, 'RANGE_V2_ADX_THRESHOLD', 20)
                _rv2_is_ranging = (not np.isnan(h4_row_adx) and h4_row_adx < _rv2_adx_thresh)

                if _rv2_is_ranging and not dead_zone_all:
                    # --- Range v2 Entry Logic ---
                    _rv2_rsi_os = getattr(cfg, 'RANGE_V2_RSI_OS', 35)
                    _rv2_rsi_ob = getattr(cfg, 'RANGE_V2_RSI_OB', 65)
                    _rv2_use_stoch = getattr(cfg, 'RANGE_V2_USE_STOCH', True)
                    _rv2_use_keltner = getattr(cfg, 'RANGE_V2_USE_KELTNER', False)
                    _rv2_use_m15_confirm = getattr(cfg, 'RANGE_V2_USE_M15_CONFIRM', True)

                    rv2_buy = False
                    rv2_sell = False

                    # --- Primary: BB Mean Reversion + RSI + Stochastic ---
                    if not _rv2_use_keltner:
                        # Use range v2 BB arrays (may have different period/dev)
                        _bb_up = h1_rv2_bb_upper[h1_idx] if h1_rv2_bb_upper is not None else h1_bb_upper[h1_idx]
                        _bb_low = h1_rv2_bb_lower[h1_idx] if h1_rv2_bb_lower is not None else h1_bb_lower[h1_idx]
                        _bb_m = h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]

                        if not (np.isnan(_bb_up) or np.isnan(_bb_low) or np.isnan(h1_curr_rsi)):
                            _bw = _bb_up - _bb_low
                            _bb_proximity = _bw * 0.10 if _bw > 0 else 0  # within 10% of band width

                            # BUY: close near/below lower BB, RSI < OS, Stoch cross up, M15 bullish
                            bb_buy = (h1_curr_close <= _bb_low + _bb_proximity)
                            rsi_buy = (h1_curr_rsi < _rv2_rsi_os)

                            # SELL: close near/above upper BB, RSI > OB, Stoch cross down, M15 bearish
                            bb_sell = (h1_curr_close >= _bb_up - _bb_proximity)
                            rsi_sell = (h1_curr_rsi > _rv2_rsi_ob)

                            stoch_buy = True
                            stoch_sell = True
                            if _rv2_use_stoch and h1_stoch_k is not None and h1_stoch_d is not None:
                                _sk = h1_stoch_k[h1_idx]
                                _sd = h1_stoch_d[h1_idx]
                                _sk_prev = h1_stoch_k[h1_idx_prev] if h1_idx_prev >= 0 else np.nan
                                _sd_prev = h1_stoch_d[h1_idx_prev] if h1_idx_prev >= 0 else np.nan
                                if not (np.isnan(_sk) or np.isnan(_sd) or np.isnan(_sk_prev) or np.isnan(_sd_prev)):
                                    # BUY: %K in oversold zone (<30) AND either:
                                    #   - %K crosses above %D (classic cross)
                                    #   - %K is rising from below 20 (momentum turning)
                                    stoch_buy = (_sk < 30 and
                                                 ((_sk > _sd and _sk_prev <= _sd_prev) or  # cross
                                                  (_sk > _sk_prev and _sk_prev < 20)))      # rising from extreme
                                    # SELL: %K in overbought zone (>70) AND either:
                                    #   - %K crosses below %D (classic cross)
                                    #   - %K is falling from above 80 (momentum turning)
                                    stoch_sell = (_sk > 70 and
                                                  ((_sk < _sd and _sk_prev >= _sd_prev) or  # cross
                                                   (_sk < _sk_prev and _sk_prev > 80)))      # falling from extreme
                                else:
                                    stoch_buy = False
                                    stoch_sell = False

                            # M15 candle confirmation
                            m15_bull = True
                            m15_bear = True
                            if _rv2_use_m15_confirm:
                                m15_bull = (m15_close[i] > m15_open[i])
                                m15_bear = (m15_close[i] < m15_open[i])

                            rv2_buy = (bb_buy and rsi_buy and stoch_buy and m15_bull)
                            rv2_sell = (bb_sell and rsi_sell and stoch_sell and m15_bear)

                    # --- Macro trend direction filter (configurable) ---
                    # Even in ranging mode, don't counter-trade a strong directional bias
                    # Use H4 MA fast vs slow to detect macro direction
                    _rv2_use_trend_filter = getattr(cfg, 'RANGE_V2_TREND_FILTER', True)
                    if _rv2_use_trend_filter and not np.isnan(h4_row_ma_fast) and not np.isnan(h4_row_ma_slow):
                        if h4_row_ma_fast < h4_row_ma_slow:
                            # H4 bearish -- block BUY mean reversion
                            rv2_buy = False
                        elif h4_row_ma_fast > h4_row_ma_slow:
                            # H4 bullish -- block SELL mean reversion
                            rv2_sell = False

                    # --- Alternative: Keltner + RSI ---
                    if _rv2_use_keltner and h1_kc_upper is not None:
                        _kc_up = h1_kc_upper[h1_idx]
                        _kc_low = h1_kc_lower[h1_idx]
                        _kc_rsi_os = getattr(cfg, 'RANGE_V2_KELTNER_RSI_OS', 30)
                        _kc_rsi_ob = getattr(cfg, 'RANGE_V2_KELTNER_RSI_OB', 70)

                        if not (np.isnan(_kc_up) or np.isnan(_kc_low) or np.isnan(h1_curr_rsi)):
                            # BUY: price below lower Keltner, RSI < OS, reversal candle
                            prev_bearish = (h1_close_arr[h1_idx_prev] < h1_open_arr[h1_idx_prev])
                            curr_bullish = (h1_close_arr[h1_idx] > h1_open_arr[h1_idx])
                            rv2_buy = (h1_curr_close < _kc_low and h1_curr_rsi < _kc_rsi_os
                                       and prev_bearish and curr_bullish)

                            # SELL: price above upper Keltner, RSI > OB, reversal candle
                            prev_bullish = (h1_close_arr[h1_idx_prev] > h1_open_arr[h1_idx_prev])
                            curr_bearish = (h1_close_arr[h1_idx] < h1_open_arr[h1_idx])
                            rv2_sell = (h1_curr_close > _kc_up and h1_curr_rsi > _kc_rsi_ob
                                        and prev_bullish and curr_bearish)

                    # --- Execute Range v2 Entry ---
                    if rv2_buy or rv2_sell:
                        _rv2_sl_atr = getattr(cfg, 'RANGE_V2_SL_ATR', 1.5)
                        _rv2_risk = getattr(cfg, 'RANGE_V2_RISK_MULTI', 0.6)

                        rv2_sl_pts = max(cfg.MIN_SL_POINTS, min(cfg.MAX_SL_POINTS,
                                         atr_points * _rv2_sl_atr))

                        # TP: either BB middle band distance or ATR-based
                        _rv2_tp_mode = getattr(cfg, 'RANGE_V2_TP_MODE', 'bb_mid')
                        if _rv2_tp_mode == 'bb_mid':
                            # Use range v2 BB mid if available
                            _tp_bb_mid = h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]
                            if not np.isnan(_tp_bb_mid):
                                if rv2_buy:
                                    rv2_tp_pts = abs(_tp_bb_mid - cc) / cfg.POINT
                                else:
                                    rv2_tp_pts = abs(cc - _tp_bb_mid) / cfg.POINT
                                # Ensure minimum TP
                                rv2_tp_pts = max(rv2_tp_pts, rv2_sl_pts * 1.0)
                            else:
                                rv2_tp_pts = atr_points * 2.0
                        else:
                            rv2_tp_pts = atr_points * 2.0

                        rv2_dir = "BUY" if rv2_buy else "SELL"
                        # Store range v2 metadata in position
                        component_mask_rv2 = [0] * 15
                        current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0
                        lot_multiplier_rv2 = 1.0
                        if cfg.USE_EQUITY_CURVE and len(self.recent_trade_pnls) >= cfg.EQUITY_MA_PERIOD:
                            recent = self.recent_trade_pnls[-cfg.EQUITY_MA_PERIOD:]
                            if np.mean(recent) < 0:
                                lot_multiplier_rv2 = cfg.EQUITY_REDUCE_FACTOR

                        self._open_trade(rv2_dir, next_bar_open, ct, 0, current_dd,
                                         rv2_sl_pts, rv2_tp_pts, current_atr,
                                         lot_multiplier_rv2 * _rv2_risk, component_mask_rv2,
                                         entry_type="range_v2", momentum_burst=False,
                                         entry_bar=i, bar_spread_points=_bar_spread)
                        # Store BB mid target and time exit in the position
                        pos = self.open_positions[-1]
                        pos["rv2_bb_mid_target"] = _tp_bb_mid if _rv2_tp_mode == 'bb_mid' and not np.isnan(h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]) else None
                        pos["rv2_time_exit_bar"] = i + int(getattr(cfg, 'RANGE_V2_TIME_EXIT_HOURS', 24) * 4)  # M15 bars
                        pos["rv2_partial_done"] = False
                        pos["rv2_partial_atr"] = getattr(cfg, 'RANGE_V2_PARTIAL_ATR', 0.5) * current_atr
                        pos["rv2_partial_ratio"] = getattr(cfg, 'RANGE_V2_PARTIAL_RATIO', 0.5)
                        self.range_trades += 1
                        _rv2_entered = True

            if _rv2_entered:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # ---- Scoring (Gold EA v4.0: max 27 points) ----
            buy_score = 0
            sell_score = 0
            component_mask = [0] * 15

            # 1. H4 Trend (3 pts)
            if not np.isnan(h4_row_adx) and h4_row_adx >= cfg.H4_ADX_THRESHOLD:
                if h4_row_ma_fast > h4_row_ma_slow and h4_row_plus_di > h4_row_minus_di:
                    buy_score += 3
                    component_mask[0] = 1
                elif h4_row_ma_fast < h4_row_ma_slow and h4_row_minus_di > h4_row_plus_di:
                    sell_score += 3
                    component_mask[0] = -1

            # 1b. v5.2: Macro trend direction
            macro_trend_dir = 0
            if not np.isnan(h4_row_ma_slow_slope):
                if h4_row_ma_slow_slope > 0:
                    macro_trend_dir = 1
                elif h4_row_ma_slow_slope < 0:
                    macro_trend_dir = -1

            # 2. H1 MA direction (2 pts)
            if not np.isnan(h1_curr_ma_fast) and not np.isnan(h1_curr_ma_slow):
                if h1_curr_ma_fast > h1_curr_ma_slow:
                    buy_score += 2
                    component_mask[1] = 1
                elif h1_curr_ma_fast < h1_curr_ma_slow:
                    sell_score += 2
                    component_mask[1] = -1

            # 3. H1 RSI (1 pt)
            if not np.isnan(h1_curr_rsi):
                rsi_val = h1_curr_rsi
                if 40 < rsi_val < 60:
                    buy_score += 1
                    sell_score += 1
                    component_mask[2] = 1
                elif 60 <= rsi_val < 65:
                    buy_score += 1
                    component_mask[2] = 1
                elif 35 < rsi_val <= 40:
                    sell_score += 1
                    component_mask[2] = -1

            # 4. H1 BB bounce (1 pt)
            if not np.isnan(h1_curr_bb_upper) and not np.isnan(h1_curr_bb_lower):
                bw = h1_curr_bb_upper - h1_curr_bb_lower
                if bw > 0:
                    bp = (h1_curr_close - h1_curr_bb_lower) / bw
                    if bp < 0.2 and h1_curr_close > h1_prev_close:
                        buy_score += 1
                        component_mask[3] = 1
                    if bp > 0.8 and h1_curr_close < h1_prev_close:
                        sell_score += 1
                        component_mask[3] = -1

            # 5. M15 MA cross (2 pts) -- cross just occurred
            if not np.isnan(m15_curr_ma_fast) and not np.isnan(m15_curr_ma_slow):
                fast_above = bool(m15_curr_ma_fast > m15_curr_ma_slow)
                if not np.isnan(m15_prev_ma_fast):
                    prev_fast_above = bool(m15_prev_ma_fast > m15_prev_ma_slow)
                else:
                    prev_fast_above = None
                if fast_above and prev_fast_above is False:
                    buy_score += 2
                    component_mask[4] = 1
                elif not fast_above and prev_fast_above is True:
                    sell_score += 2
                    component_mask[4] = -1

            # 6. Channel regression (1 pt) -- use optimized numpy version
            # h1_pos is the number of valid bars (exclusive upper bound)
            cs = calc_channel_signal_fast(h1_close_arr, h1_pos, 40)
            if cs == 1:
                buy_score += 1
                component_mask[5] = 1
            elif cs == -1:
                sell_score += 1
                component_mask[5] = -1

            # 7. v2.0: Momentum scoring (+1 pt)
            if cfg.USE_MOMENTUM and i >= 2:
                close_now = m15_close[i]
                close_2ago = m15_close[i - 2]
                momentum_diff = close_now - close_2ago
                momentum_threshold = current_atr * 0.1
                if momentum_diff > momentum_threshold:
                    buy_score += 1
                    component_mask[6] = 1
                elif momentum_diff < -momentum_threshold:
                    sell_score += 1
                    component_mask[6] = -1

            # 8. v2.0: Session bonus (+1 pt)
            if cfg.USE_SESSION_BONUS:
                if (13 <= hour <= 16) or (8 <= hour <= 10):
                    buy_score += 1
                    sell_score += 1
                    component_mask[7] = 1

            # 9. v3.0: USD Correlation (+2) -- use pre-computed signals
            if cfg.USE_CORRELATION and usdjpy_corr_signals is not None:
                # Find the USDJPY bar index for current time via searchsorted
                uj_pos = np.searchsorted(usdjpy_index_i64, ct_i64, side='right')
                if uj_pos > 0:
                    corr = int(usdjpy_corr_signals[uj_pos - 1])
                    if corr == 1:
                        buy_score += 2
                        component_mask[8] = 1
                    elif corr == -1:
                        sell_score += 2
                        component_mask[8] = -1

            # 10. v3.0: RSI Divergence (+2) -- use optimized numpy version
            if cfg.USE_DIVERGENCE:
                div = get_divergence_fast(h1_close_arr, h1_rsi_arr, h1_pos,
                                          cfg.DIV_LOOKBACK, cfg.DIV_SWING_STRENGTH)
                if div == 1:
                    buy_score += 2
                    component_mask[9] = 1
                elif div == -1:
                    sell_score += 2
                    component_mask[9] = -1

            # 11. v3.0: S/R Level (+1/-1) -- use optimized numpy version
            if cfg.USE_SR_LEVELS:
                sr = get_sr_signal_fast(h1_high_arr, h1_low_arr, h1_pos,
                                        cc, current_atr, cfg)
                if sr == 1:
                    buy_score += 1
                    sell_score -= 1
                    component_mask[10] = 1
                elif sr == -1:
                    sell_score += 1
                    buy_score -= 1
                    component_mask[10] = -1

            # 12. v3.0: Candle Pattern (+1) -- use optimized numpy version
            if cfg.USE_CANDLE_PATTERNS:
                cdl = get_candle_pattern_fast(h1_open_arr, h1_high_arr,
                                              h1_low_arr, h1_close_arr, h1_pos)
                if cdl == 1:
                    buy_score += 1
                    component_mask[11] = 1
                elif cdl == -1:
                    sell_score += 1
                    component_mask[11] = -1

            # 13. v3.0: H4 RSI Alignment (+1)
            if cfg.USE_H4_RSI and not np.isnan(h4_row_rsi):
                h4r = get_h4_rsi_alignment(
                    h4_row_rsi,
                    h1_curr_rsi if not np.isnan(h1_curr_rsi) else 50
                )
                if h4r == 1:
                    buy_score += 1
                    component_mask[12] = 1
                elif h4r == -1:
                    sell_score += 1
                    component_mask[12] = -1

            # 14. v4.0: Momentum Burst (+3)
            # Build lightweight dicts matching original's pd.Series .get() interface
            _h4_row = {
                "ma_fast": h4_row_ma_fast,
                "ma_slow": h4_row_ma_slow,
            }
            _h1_curr = {
                "ma_fast": h1_curr_ma_fast,
                "ma_slow": h1_curr_ma_slow,
            }
            _m15_curr = {
                "ma_fast": m15_curr_ma_fast,
                "ma_slow": m15_curr_ma_slow,
            }
            burst = self.get_momentum_burst(_h4_row, _h1_curr, _m15_curr, None)
            if burst > 0:
                buy_score += burst
                component_mask[13] = 1
            elif burst < 0:
                sell_score += abs(burst)
                component_mask[13] = -1

            # 15. v4.0: Volume Climax (+2) -- use numpy arrays directly
            climax = 0
            if cfg.USE_VOLUME_CLIMAX and m15_volume is not None and i >= 21:
                current_vol = m15_volume[i]
                avg_vol = m15_volume[i - 20:i].mean()
                if avg_vol > 0 and current_vol > avg_vol * 2.0:
                    if m15_close[i] > m15_open[i]:
                        climax = 2
                    elif m15_close[i] < m15_open[i]:
                        climax = -2
            if climax > 0:
                buy_score += climax
                component_mask[14] = 1
            elif climax < 0:
                sell_score += abs(climax)
                component_mask[14] = -1

            # Clamp to 0
            buy_score = max(0, buy_score)
            sell_score = max(0, sell_score)

            # ---- v6.0: Session-Regime Adaptive Threshold (SRAT) ----
            if _srat and hasattr(cfg, 'SRAT_THRESHOLDS') and hour in cfg.SRAT_THRESHOLDS:
                dynamic_min_score = cfg.SRAT_THRESHOLDS[hour]
            else:
                dynamic_min_score = cfg.MIN_SCORE  # 9
            # DD escalation stacks on top of session base (configurable)
            for dd_thresh, dd_score in _dd_esc:
                if current_dd >= dd_thresh:
                    dynamic_min_score = max(dynamic_min_score, dd_score)
                    break
            if regime == 1:  # Ranging
                dynamic_min_score += 3
            # v6.2: Detect H4 ranging regime (used for TP cap and optional score boost)
            is_ranging = False
            if getattr(cfg, 'USE_RANGING_ADAPTATION', False):
                _ranging_adx_thresh = getattr(cfg, 'RANGING_ADX_THRESHOLD', 20)
                if not np.isnan(h4_row_adx) and h4_row_adx < _ranging_adx_thresh:
                    is_ranging = True
                _ranging_boost = getattr(cfg, 'RANGING_SCORE_BOOST', 0)
                if is_ranging and _ranging_boost > 0:
                    dynamic_min_score += _ranging_boost

            # v9.1: Chop Filter -- raise min_score when H4 shows frequent direction changes
            if _use_chop and h4_idx > 0 and h4_chop[h4_idx] == 1:
                dynamic_min_score += _chop_boost

            # ---- v8.0a: Volatility Trend Filter ----
            # Block entries when ATR is expanding but no clear H4 trend (choppy danger)
            _use_vol_trend = getattr(cfg, 'USE_VOL_TREND_FILTER', False)
            vol_trend_block = False
            if _use_vol_trend:
                _vt_lookback = getattr(cfg, 'VOL_TREND_LOOKBACK', 10)
                if i >= _vt_lookback:
                    atr_now = m15_atr[i]
                    atr_past = m15_atr[i - _vt_lookback]
                    if not np.isnan(atr_now) and not np.isnan(atr_past) and atr_past > 0:
                        atr_expansion = atr_now / atr_past
                        # ATR expanding >20% but H4 ADX below trend threshold = danger
                        if atr_expansion > 1.20 and (np.isnan(h4_row_adx) or h4_row_adx < 20):
                            if getattr(cfg, 'VOL_TREND_EXPANSION_BLOCK', True):
                                vol_trend_block = True

            # ---- v8.0b: H4 ADX Slope Filter ----
            # When ADX is falling, trend is weakening -> require higher confirmation
            _use_adx_slope = getattr(cfg, 'USE_ADX_SLOPE', False)
            if _use_adx_slope and not np.isnan(h4_row_adx):
                _adx_slope_lb = getattr(cfg, 'ADX_SLOPE_LOOKBACK', 5)
                if h4_idx >= _adx_slope_lb:
                    adx_prev = h4_adx[h4_idx - _adx_slope_lb]
                    if not np.isnan(adx_prev):
                        adx_slope = h4_row_adx - adx_prev
                        if adx_slope < 0:  # ADX falling = trend weakening
                            _penalty = getattr(cfg, 'ADX_FALLING_PENALTY', 2)
                            dynamic_min_score += _penalty

            # ---- v8.0c: Confirmation Escalation ----
            # When recent trades are losing, require more confirmation
            _use_conf_esc = getattr(cfg, 'USE_CONFIRMATION_ESCALATION', False)
            if _use_conf_esc:
                _esc_lb = getattr(cfg, 'CONF_ESC_LOOKBACK', 20)
                _esc_wr_thresh = getattr(cfg, 'CONF_ESC_WR_THRESHOLD', 0.40)
                _esc_boost = getattr(cfg, 'CONF_ESC_SCORE_BOOST', 2)
                if len(self.recent_trade_pnls) >= _esc_lb:
                    recent_wins = sum(1 for p in self.recent_trade_pnls[-_esc_lb:] if p > 0)
                    recent_wr = recent_wins / _esc_lb
                    if recent_wr < _esc_wr_thresh:
                        dynamic_min_score += _esc_boost

            # ---- v8.0e: Range Compression Detection ----
            # Detect narrow-range consolidation -> higher false breakout risk
            _use_range_comp = getattr(cfg, 'USE_RANGE_COMPRESSION', False)
            if _use_range_comp:
                _rc_lb = getattr(cfg, 'RANGE_COMP_LOOKBACK', 20)
                _rc_hist_lb = getattr(cfg, 'RANGE_COMP_HIST_LOOKBACK', 100)
                _rc_ratio = getattr(cfg, 'RANGE_COMP_RATIO', 0.5)
                _rc_boost = getattr(cfg, 'RANGE_COMP_SCORE_BOOST', 2)
                if h1_pos >= _rc_hist_lb:
                    recent_range = np.nanmean(h1_range_arr[h1_pos - _rc_lb:h1_pos])
                    hist_range = np.nanmean(h1_range_arr[h1_pos - _rc_hist_lb:h1_pos - _rc_lb])
                    if hist_range > 0 and recent_range / hist_range < _rc_ratio:
                        dynamic_min_score += _rc_boost

            # ---- v8.0f: Seasonal/Quarterly Adaptation ----
            # Tighten criteria during historically weak months (Q1/Q2)
            _use_seasonal = getattr(cfg, 'USE_SEASONAL_ADAPT', False)
            is_weak_season = False
            if _use_seasonal:
                _weak_months = getattr(cfg, 'SEASONAL_WEAK_MONTHS', {1,2,3,4,5,6})
                bar_month = ct.month if hasattr(ct, 'month') else 1
                if bar_month in _weak_months:
                    is_weak_season = True
                    _seasonal_boost = getattr(cfg, 'SEASONAL_SCORE_BOOST', 1)
                    dynamic_min_score += _seasonal_boost

            # ---- v3.0: Equity Curve Filter ----
            lot_multiplier = 1.0
            if cfg.USE_EQUITY_CURVE and len(self.recent_trade_pnls) >= cfg.EQUITY_MA_PERIOD:
                recent = self.recent_trade_pnls[-cfg.EQUITY_MA_PERIOD:]
                if np.mean(recent) < 0:
                    lot_multiplier = cfg.EQUITY_REDUCE_FACTOR

            # v4.0: Momentum burst TP multiplier
            tp_multi = 1.5 if abs(burst) == 3 else 1.0
            adjusted_tp_points = dynamic_tp_points * tp_multi

            # v6.2: Ranging Regime Adaptation -- cap TP when H4 shows no trend
            if is_ranging:
                _ranging_tp_cap = getattr(cfg, 'RANGING_TP_CAP', 3.0)
                ranging_tp = atr_points * _ranging_tp_cap
                if ranging_tp < adjusted_tp_points:
                    adjusted_tp_points = ranging_tp
                if adjusted_tp_points < dynamic_sl_points * 1.5:
                    adjusted_tp_points = dynamic_sl_points * 1.5

            # v8.0f: Seasonal TP tightening during weak months
            if _use_seasonal and is_weak_season:
                _seasonal_tp = getattr(cfg, 'SEASONAL_TP_TIGHTEN', 0.85)
                adjusted_tp_points *= _seasonal_tp
                if adjusted_tp_points < dynamic_sl_points * 1.5:
                    adjusted_tp_points = dynamic_sl_points * 1.5

            # ---- v4.0: Pyramiding support ----
            pos_count = len(self.open_positions)
            can_enter = pos_count < cfg.MAX_PYRAMID_POSITIONS
            is_pyramid = pos_count > 0
            pyramid_ok = True

            if is_pyramid:
                for pos in self.open_positions:
                    if pos["direction"] == "BUY":
                        unrealized = cc - pos["entry"]
                    else:
                        unrealized = pos["entry"] - cc
                    if unrealized <= 0:
                        pyramid_ok = False
                        break

            # ---- Entry ----
            entry_type = "normal"
            entered = False

            # v7.0: Range-Reversion entry (only when ranging AND no open positions)
            _use_range_rev = getattr(cfg, 'USE_RANGE_REVERSION', False)
            if _use_range_rev and is_ranging and not dead_zone_all and pos_count == 0:
                # Compute S/R signal for range confirmation
                _range_sr = get_sr_signal_fast(h1_high_arr, h1_low_arr, h1_pos,
                                                cc, current_atr, cfg) if cfg.USE_SR_LEVELS else 0
                range_dir, range_conf = self.get_range_signal(
                    h1_curr_close, h1_prev_close, h1_curr_rsi,
                    h1_curr_bb_upper, h1_curr_bb_lower, _range_sr, cfg
                )
                if range_dir is not None:
                    # Use tight SL/TP for range trades
                    _range_sl_multi = getattr(cfg, 'RANGE_SL_ATR_MULTI', 1.0)
                    _range_tp_multi = getattr(cfg, 'RANGE_TP_ATR_MULTI', 2.0)
                    range_sl_pts = max(cfg.MIN_SL_POINTS, min(cfg.MAX_SL_POINTS, atr_points * _range_sl_multi))
                    range_tp_pts = atr_points * _range_tp_multi
                    if range_tp_pts < range_sl_pts * 1.5:
                        range_tp_pts = range_sl_pts * 1.5
                    _range_risk = getattr(cfg, 'RANGE_RISK_MULTIPLIER', 0.5)
                    self._open_trade(range_dir, next_bar_open, ct, range_conf, current_dd,
                                     range_sl_pts, range_tp_pts, current_atr,
                                     lot_multiplier * _range_risk, component_mask,
                                     entry_type="range_rev", momentum_burst=False,
                                     entry_bar=i, bar_spread_points=_bar_spread)
                    self.range_trades += 1
                    entered = True

            if not dead_zone_all and not vol_trend_block and can_enter and (not is_pyramid or pyramid_ok) and not entered:
                if dead_zone_normal and not is_pyramid:
                    pass  # Block new positions during session transitions
                else:
                    pyramid_lot_multi = 1.0
                    if is_pyramid:
                        pyramid_lot_multi = cfg.PYRAMID_LOT_DECAY ** pos_count
                        entry_type = "pyramid"

                    # v5.2: Trend-aligned SL adjustment
                    adj_sl = dynamic_sl_points
                    adj_tp = adjusted_tp_points
                    if macro_trend_dir != 0:
                        if (buy_score > sell_score and macro_trend_dir == 1) or \
                           (sell_score > buy_score and macro_trend_dir == -1):
                            adj_sl = min(dynamic_sl_points * cfg.TREND_SL_WIDEN, cfg.MAX_SL_POINTS)
                        elif (buy_score > sell_score and macro_trend_dir == -1) or \
                             (sell_score > buy_score and macro_trend_dir == 1):
                            adj_sl = max(dynamic_sl_points * cfg.TREND_SL_TIGHTEN, cfg.MIN_SL_POINTS)

                    # Optional Score 11 Skip
                    effective_buy = buy_score
                    effective_sell = sell_score
                    if _skip11:
                        if buy_score == 11:
                            effective_buy = 0
                        if sell_score == 11:
                            effective_sell = 0

                    # v7.0: Macro-Trend Filter - block counter-trend entries
                    if getattr(cfg, 'USE_MACRO_TREND_FILTER', False):
                        if h4_row_adx >= cfg.MACRO_TREND_ADX_THRESHOLD:
                            if h4_row_ma_fast > h4_row_ma_slow:  # H4 bullish
                                effective_sell = 0
                            elif h4_row_ma_fast < h4_row_ma_slow:  # H4 bearish
                                effective_buy = 0

                    # v8.0: RSI Momentum Confirmation
                    if getattr(cfg, 'USE_RSI_MOMENTUM_CONFIRM', False):
                        _rsi_lb = getattr(cfg, 'RSI_MOMENTUM_LOOKBACK', 3)
                        if not np.isnan(h1_curr_rsi) and h1_idx >= _rsi_lb:
                            rsi_past = h1_rsi_arr[h1_idx - _rsi_lb]
                            if not np.isnan(rsi_past):
                                if effective_buy > effective_sell:
                                    if not (h1_curr_rsi > 50 and h1_curr_rsi > rsi_past):
                                        effective_buy = 0
                                elif effective_sell > effective_buy:
                                    if not (h1_curr_rsi < 50 and h1_curr_rsi < rsi_past):
                                        effective_sell = 0

                    # v8.0d: H4+H1 Alignment Filter
                    # In weak-trend regime, require both timeframes to agree on direction
                    _use_tf_align = getattr(cfg, 'USE_TF_ALIGNMENT_FILTER', False)
                    if _use_tf_align:
                        _tf_adx_thresh = getattr(cfg, 'TF_ALIGNMENT_ADX_THRESHOLD', 25)
                        if np.isnan(h4_row_adx) or h4_row_adx < _tf_adx_thresh:
                            # H4 direction
                            h4_bullish = not np.isnan(h4_row_ma_fast) and h4_row_ma_fast > h4_row_ma_slow
                            h4_bearish = not np.isnan(h4_row_ma_fast) and h4_row_ma_fast < h4_row_ma_slow
                            # H1 direction
                            h1_bullish = not np.isnan(h1_curr_ma_fast) and h1_curr_ma_fast > h1_curr_ma_slow
                            h1_bearish = not np.isnan(h1_curr_ma_fast) and h1_curr_ma_fast < h1_curr_ma_slow
                            # Block if H4 and H1 disagree
                            if effective_buy > effective_sell:
                                if not (h4_bullish and h1_bullish):
                                    effective_buy = 0
                            elif effective_sell > effective_buy:
                                if not (h4_bearish and h1_bearish):
                                    effective_sell = 0

                    # ---- v9.0: Score Spread Requirement ----
                    _use_score_spread = getattr(cfg, 'USE_SCORE_SPREAD', False)
                    if _use_score_spread:
                        _spread_min = getattr(cfg, 'SCORE_SPREAD_MIN', 3)
                        if effective_buy >= dynamic_min_score and effective_buy > effective_sell:
                            if (effective_buy - effective_sell) < _spread_min:
                                effective_buy = 0
                        elif effective_sell >= dynamic_min_score and effective_sell > effective_buy:
                            if (effective_sell - effective_buy) < _spread_min:
                                effective_sell = 0

                    # ---- v9.0: Consensus Filter ----
                    _use_consensus = getattr(cfg, 'USE_CONSENSUS_FILTER', False)
                    if _use_consensus:
                        _cons_min = getattr(cfg, 'CONSENSUS_MIN', 2)
                        _cons_comps = getattr(cfg, 'CONSENSUS_COMPONENTS', [0, 1, 8, 13, 9])
                        if effective_buy >= dynamic_min_score and effective_buy > effective_sell:
                            agree = sum(1 for ci in _cons_comps if component_mask[ci] == 1)
                            if agree < _cons_min:
                                effective_buy = 0
                        elif effective_sell >= dynamic_min_score and effective_sell > effective_buy:
                            agree = sum(1 for ci in _cons_comps if component_mask[ci] == -1)
                            if agree < _cons_min:
                                effective_sell = 0

                    # ---- v9.0: Directional Consistency ----
                    _use_dir_consist = getattr(cfg, 'USE_DIR_CONSISTENCY', False)
                    if _use_dir_consist:
                        if not hasattr(self, '_signal_history'):
                            self._signal_history = []
                        # Record signal direction every qualifying bar
                        if buy_score > sell_score:
                            self._signal_history.append(1)
                        elif sell_score > buy_score:
                            self._signal_history.append(-1)
                        _dc_window = getattr(cfg, 'DIR_CONSIST_WINDOW', 5)
                        _dc_min = getattr(cfg, 'DIR_CONSIST_MIN', 4)
                        if len(self._signal_history) >= _dc_window:
                            recent = self._signal_history[-_dc_window:]
                            if effective_buy >= dynamic_min_score and effective_buy > effective_sell:
                                if sum(1 for s in recent if s == 1) < _dc_min:
                                    effective_buy = 0
                            elif effective_sell >= dynamic_min_score and effective_sell > effective_buy:
                                if sum(1 for s in recent if s == -1) < _dc_min:
                                    effective_sell = 0

                    if effective_buy >= dynamic_min_score and effective_buy > effective_sell:
                        self._open_trade("BUY", next_bar_open, ct, buy_score, current_dd,
                                         adj_sl, adj_tp, current_atr,
                                         lot_multiplier * pyramid_lot_multi, component_mask,
                                         entry_type=entry_type, momentum_burst=(abs(burst) == 3),
                                         entry_bar=i, bar_spread_points=_bar_spread)
                        entered = True
                    elif effective_sell >= dynamic_min_score and effective_sell > effective_buy:
                        self._open_trade("SELL", next_bar_open, ct, sell_score, current_dd,
                                         adj_sl, adj_tp, current_atr,
                                         lot_multiplier * pyramid_lot_multi, component_mask,
                                         entry_type=entry_type, momentum_burst=(abs(burst) == 3),
                                         entry_bar=i, bar_spread_points=_bar_spread)
                        entered = True

            # v4.0: Reversal mode
            # v5.4: Also blocked during all dead zones (both all and normal-only)
            if not dead_zone_all and not dead_zone_normal and not entered and pos_count == 0:
                reversal = self._check_reversal_fast(
                    h1_close_arr, h1_open_arr, h1_high_arr, h1_low_arr,
                    h1_rsi_arr, h1_pos, ct, cc, current_atr, h1_curr_rsi, cfg
                )
                if reversal == 1:
                    self._open_trade("BUY", next_bar_open, ct, 0, current_dd,
                                     dynamic_sl_points, dynamic_tp_points, current_atr,
                                     lot_multiplier * 0.5, component_mask,
                                     entry_type="reversal", entry_bar=i,
                                     bar_spread_points=_bar_spread)
                elif reversal == -1:
                    self._open_trade("SELL", next_bar_open, ct, 0, current_dd,
                                     dynamic_sl_points, dynamic_tp_points, current_atr,
                                     lot_multiplier * 0.5, component_mask,
                                     entry_type="reversal", entry_bar=i,
                                     bar_spread_points=_bar_spread)

            self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})

        # Final close
        fc = m15_close[-1]
        _final_spread = float(m15_spread[-1]) if m15_spread is not None else None
        for pos in list(self.open_positions):
            self._close_position(pos, fc, m15_df.index[-1], "EndOfPeriod", total_bars - 1,
                                 bar_spread_points=_final_spread)

        print("[OK] Backtest complete")

    def _check_reversal_fast(self, h1_close_arr, h1_open_arr, h1_high_arr, h1_low_arr,
                              h1_rsi_arr, h1_pos, ct, cc, current_atr, h1_curr_rsi, cfg):
        """Optimized reversal check using numpy arrays."""
        if not cfg.USE_REVERSAL_MODE:
            return 0
        rsi = h1_curr_rsi if not np.isnan(h1_curr_rsi) else 50

        div_signal = get_divergence_fast(h1_close_arr, h1_rsi_arr, h1_pos,
                                          cfg.DIV_LOOKBACK, cfg.DIV_SWING_STRENGTH)
        sr_signal = get_sr_signal_fast(h1_high_arr, h1_low_arr, h1_pos,
                                        cc, current_atr, cfg)
        candle_signal = get_candle_pattern_fast(h1_open_arr, h1_high_arr,
                                                 h1_low_arr, h1_close_arr, h1_pos)

        # Bullish reversal
        if rsi < 25 and div_signal > 0 and sr_signal > 0 and candle_signal > 0:
            return 1
        # Bearish reversal
        if rsi > 75 and div_signal < 0 and sr_signal < 0 and candle_signal < 0:
            return -1
        return 0


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    import os
    cfg = GoldConfig()

    # Try CSV files first, fall back to yfinance
    csv_mode = all(os.path.exists(f) for f in [
        "XAUUSD_H4.csv", "XAUUSD_H1.csv", "XAUUSD_M15.csv"
    ])

    if csv_mode:
        from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
        print("[CSV] Loading from local CSV files...")
        m15_real = load_csv("XAUUSD_M15.csv")
        h1_real = load_csv("XAUUSD_H1.csv")
        h4 = load_csv("XAUUSD_H4.csv")
        usdjpy = load_csv("USDJPY_H1.csv")
        h1_gen = generate_h1_from_h4(h4)
        h1 = merge_and_fill(h1_real, h1_gen)
        m15_gen = generate_m15_from_h1(h1)
        m15 = merge_and_fill(m15_real, m15_gen)
    else:
        h4, h1, m15, usdjpy = fetch_gold_data(months=6)

    if m15 is None:
        print("[ERR] Data fetch failed")
        exit()

    bt = GoldBacktesterFast(cfg)
    bt.run(h4, h1, m15, usdjpy_df=usdjpy)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print(" AntigravityMTF EA [GOLD] v4.0 Backtest Results (6 months)")
        print("  [FAST MODE - Optimized]")
        print("=" * 60)
        for k, v in rpt.items():
            if k == "Monthly":
                print(f"\n  Monthly PnL:")
                for m, p in v.items():
                    bar = "#" * max(1, int(abs(p) / 2000))
                    icon = "[+]" if p > 0 else "[-]"
                    print(f"    {m}: {icon} {p:+,.0f} JPY {bar}")
            elif k == "ByReason":
                print(f"\n  Close Reasons:")
                counts = v.get("count", {})
                pnls = v.get("pnl", {})
                for reason in counts:
                    print(f"    {reason}: {int(counts[reason])}x / {pnls[reason]:+,.0f} JPY")
            else:
                print(f"  {k}: {v}")

        bt.analyze_components()

        print(f"\n  --- v4.0 Defense Stats ---")
        print(f"  News filter blocks:   {bt.news_blocks}")
        print(f"  Crash regime skips:   {bt.crash_skips}")
        print(f"  Weekend closes:       {bt.weekend_closes}")
        print(f"  Spread blocks:        {bt.spread_blocks}")
        print(f"  Circuit breaker days: {sum(1 for t in bt.trades if t.get('reason') == 'CircuitBreaker')}")

        reversals = sum(1 for t in bt.trades if t.get('entry_type') == 'reversal')
        pyramids = sum(1 for t in bt.trades if t.get('entry_type') == 'pyramid')
        bursts = sum(1 for t in bt.trades if t.get('momentum_burst', False))
        print(f"\n  --- v4.0 Attack Stats ---")
        print(f"  Reversal trades:      {reversals}")
        print(f"  Pyramid entries:      {pyramids}")
        print(f"  Momentum burst trades:{bursts}")

        print(f"\n  Trade Details (last 10):")
        print(f"  {'DateTime':<20} {'Dir':<5} {'Entry':>10} {'Exit':>10} {'Lot':>5} {'PnL(pt)':>8} {'PnL(JPY)':>10} {'Balance':>12} {'Reason':<10} {'Type':<8}")
        print("  " + "-" * 110)
        for t in bt.trades[-10:]:
            print(f"  {str(t['open_time'])[:19]:<20} {t['direction']:<5} {t['entry']:>10.2f} {t['exit']:>10.2f} {t['lot']:>5.2f} {t['pnl_pts']:>8.0f} {t['pnl_jpy']:>+10,.0f} {t['balance']:>12,.0f} {t['reason']:<10} {t.get('entry_type','normal'):<8}")
    else:
        print("[WARN] No trades occurred")
        print("   Try lowering MinScore or adjusting parameters")
