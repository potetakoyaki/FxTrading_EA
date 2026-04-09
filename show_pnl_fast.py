#!/usr/bin/env python3
"""Show PnL per quarter with current config -- FAST version

Optimizations applied:
1. Parallel quarter execution via fork-based multiprocessing (zero-copy globals)
2. Pickle-cached data loading (10s CSV parse -> <0.1s pickle load)
3. Pre-compute all indicators ONCE on full data in parent, share via fork
4. Vectorized get_sr_signal using numpy sliding_window_view (8x faster)
5. Vectorized get_divergence using numpy sliding_window_view
6. Pre-resolve all getattr(cfg, ...) calls before the main loop
7. Inline precompute_correlation_signals into parent (compute once, not per quarter)
"""
import sys, os, time, warnings, io, pickle, hashlib
from multiprocessing import Process, Queue, cpu_count
from numpy.lib.stride_tricks import sliding_window_view

warnings.filterwarnings('ignore')
os.chdir('/tmp/FxTrading_EA')

import pandas as pd, numpy as np
from backtest_gold import (
    GoldConfig, GoldBacktester,
    calc_sma, calc_ema, calc_rsi, calc_atr, calc_adx, calc_bb,
    calc_stochastic, calc_keltner,
    get_h4_rsi_alignment,
)
from backtest_gold_fast import (
    GoldBacktesterFast,
    precompute_correlation_signals,
    calc_channel_signal_fast,
    get_candle_pattern_fast,
)


# ============================================================
# Vectorized S/R signal (replaces per-bar Python loop)
# ============================================================
def get_sr_signal_vec(h1_highs, h1_lows, h1_end_idx, current_price, current_atr, cfg):
    """Support/Resistance signal using vectorized numpy operations.

    Drop-in replacement for get_sr_signal_fast with identical output.
    """
    if h1_end_idx < cfg.SR_LOOKBACK:
        return 0

    start = h1_end_idx - cfg.SR_LOOKBACK
    highs = h1_highs[start:h1_end_idx]
    lows = h1_lows[start:h1_end_idx]
    strength = cfg.SR_SWING_STRENGTH
    length = len(highs)

    if length < 2 * strength + 1:
        return 0

    # Vectorized swing detection using sliding_window_view
    idx_range = np.arange(strength, length - strength)

    # Swing highs
    hw = sliding_window_view(highs, strength)
    hw_max = hw.max(axis=1)
    left_max_h = hw_max[idx_range - strength]
    right_max_h = hw_max[idx_range + 1]
    center_h = highs[idx_range]
    swing_high_mask = (center_h > left_max_h) & (center_h > right_max_h)
    resistance_levels = center_h[swing_high_mask]

    # Swing lows
    lw = sliding_window_view(lows, strength)
    lw_min = lw.min(axis=1)
    left_min_l = lw_min[idx_range - strength]
    right_min_l = lw_min[idx_range + 1]
    center_l = lows[idx_range]
    swing_low_mask = (center_l < left_min_l) & (center_l < right_min_l)
    support_levels = center_l[swing_low_mask]

    if len(resistance_levels) == 0 and len(support_levels) == 0:
        return 0

    levels = np.concatenate([resistance_levels, support_levels])
    levels.sort()

    # Cluster levels within SR_CLUSTER_ATR * ATR
    cluster_dist = cfg.SR_CLUSTER_ATR * current_atr
    clustered = []
    cluster_sum = levels[0]
    cluster_count = 1
    for i in range(1, len(levels)):
        if levels[i] - levels[i - 1] <= cluster_dist:
            cluster_sum += levels[i]
            cluster_count += 1
        else:
            clustered.append(cluster_sum / cluster_count)
            cluster_sum = levels[i]
            cluster_count = 1
    clustered.append(cluster_sum / cluster_count)

    proximity = cfg.SR_PROXIMITY_ATR * current_atr

    # Find nearest support and resistance
    nearest_support_dist = float('inf')
    nearest_resistance_dist = float('inf')
    has_support = False
    has_resistance = False

    for lv in clustered:
        if lv < current_price:
            d = current_price - lv
            if d < nearest_support_dist:
                nearest_support_dist = d
                has_support = True
        elif lv > current_price:
            d = lv - current_price
            if d < nearest_resistance_dist:
                nearest_resistance_dist = d
                has_resistance = True

    if has_support and nearest_support_dist <= proximity:
        return 1
    if has_resistance and nearest_resistance_dist <= proximity:
        return -1
    return 0


# ============================================================
# Vectorized divergence detection
# ============================================================
def get_divergence_vec(h1_close_arr, h1_rsi_arr, h1_end_idx, lookback=30, swing_strength=3):
    """Divergence detection using vectorized swing finding.

    Drop-in replacement for get_divergence_fast with identical output.
    """
    if h1_end_idx < lookback:
        return 0

    closes = h1_close_arr[h1_end_idx - lookback:h1_end_idx]
    rsi = h1_rsi_arr[h1_end_idx - lookback:h1_end_idx]

    if np.any(np.isnan(closes)) or np.any(np.isnan(rsi)):
        return 0

    length = lookback
    if length < 2 * swing_strength + 1:
        return 0

    idx_range = np.arange(swing_strength, length - swing_strength)

    # Vectorized swing detection
    cw = sliding_window_view(closes, swing_strength)
    cw_min = cw.min(axis=1)
    cw_max = cw.max(axis=1)

    center = closes[idx_range]

    # Swing lows
    left_min = cw_min[idx_range - swing_strength]
    right_min = cw_min[idx_range + 1]
    swing_low_mask = (center < left_min) & (center < right_min)
    swing_low_indices = idx_range[swing_low_mask]

    # Swing highs
    left_max = cw_max[idx_range - swing_strength]
    right_max = cw_max[idx_range + 1]
    swing_high_mask = (center > left_max) & (center > right_max)
    swing_high_indices = idx_range[swing_high_mask]

    # Bullish divergence (swing lows)
    if len(swing_low_indices) >= 2:
        i1 = swing_low_indices[-2]
        i2 = swing_low_indices[-1]
        if closes[i2] < closes[i1] and rsi[i2] > rsi[i1]:
            return 1
        if closes[i2] > closes[i1] and rsi[i2] < rsi[i1]:
            return 1

    # Bearish divergence (swing highs)
    if len(swing_high_indices) >= 2:
        i1 = swing_high_indices[-2]
        i2 = swing_high_indices[-1]
        if closes[i2] > closes[i1] and rsi[i2] < rsi[i1]:
            return -1
        if closes[i2] < closes[i1] and rsi[i2] > rsi[i1]:
            return -1

    return 0


# ============================================================
# Ultra-fast backtester: same logic, optimized hot path
# ============================================================
class GoldBacktesterUltraFast(GoldBacktester):
    """Drop-in replacement with aggressively optimized run() method.

    Key differences from GoldBacktesterFast:
    - Vectorized S/R signal detection (sliding_window_view)
    - Vectorized divergence detection
    - All getattr(cfg, ...) calls resolved once before the main loop
    - Accepts pre-computed indicator DataFrames to skip redundant computation
    """

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None,
            _indicators_precomputed=False):
        cfg = self.cfg
        self.usdjpy_df = usdjpy_df

        # Indicator computation (skip if already done by caller)
        if not _indicators_precomputed:
            h4_df = h4_df.copy()
            h1_df = h1_df.copy()
            m15_df = m15_df.copy()

            h4_df["ma_fast"] = calc_sma(h4_df["Close"], cfg.H4_MA_FAST)
            h4_df["ma_slow"] = calc_sma(h4_df["Close"], cfg.H4_MA_SLOW)
            h4_df["adx"], h4_df["plus_di"], h4_df["minus_di"] = calc_adx(
                h4_df["High"], h4_df["Low"], h4_df["Close"], cfg.H4_ADX_PERIOD)
            h4_df["ma_slow_slope"] = h4_df["ma_slow"] - h4_df["ma_slow"].shift(cfg.H4_SLOPE_PERIOD)
            h4_df["rsi"] = calc_rsi(h4_df["Close"], cfg.H4_RSI_PERIOD)

            h1_df["ma_fast"] = calc_ema(h1_df["Close"], cfg.H1_MA_FAST)
            h1_df["ma_slow"] = calc_ema(h1_df["Close"], cfg.H1_MA_SLOW)
            h1_df["rsi"] = calc_rsi(h1_df["Close"], cfg.H1_RSI_PERIOD)
            h1_df["bb_upper"], h1_df["bb_mid"], h1_df["bb_lower"] = calc_bb(
                h1_df["Close"], cfg.H1_BB_PERIOD, cfg.H1_BB_DEV)

            m15_df["ma_fast"] = calc_ema(m15_df["Close"], cfg.M15_MA_FAST)
            m15_df["ma_slow"] = calc_ema(m15_df["Close"], cfg.M15_MA_SLOW)
            m15_df["atr"] = calc_atr(m15_df["High"], m15_df["Low"], m15_df["Close"], cfg.ATR_PERIOD)
            m15_df["atr_avg"] = m15_df["atr"].rolling(window=cfg.VOL_REGIME_PERIOD).mean()

            if self.usdjpy_df is not None:
                self.usdjpy_df = self.usdjpy_df.copy()
                self.usdjpy_df["ema_fast"] = calc_ema(self.usdjpy_df["Close"], cfg.CORR_MA_FAST)
                self.usdjpy_df["ema_slow"] = calc_ema(self.usdjpy_df["Close"], cfg.CORR_MA_SLOW)
                self.usdjpy_df["atr"] = calc_atr(
                    self.usdjpy_df["High"], self.usdjpy_df["Low"], self.usdjpy_df["Close"], 14)

        # v9.0: Range Strategy v2 indicators
        _use_rv2 = getattr(cfg, 'USE_RANGE_STRATEGY_V2', False)
        if _use_rv2 and "stoch_k" not in h1_df.columns:
            _stoch_p = getattr(cfg, 'RANGE_V2_STOCH_PERIOD', 14)
            _stoch_s = getattr(cfg, 'RANGE_V2_STOCH_SMOOTH', 3)
            h1_df["stoch_k"], h1_df["stoch_d"] = calc_stochastic(
                h1_df["High"], h1_df["Low"], h1_df["Close"],
                k_period=_stoch_p, k_smooth=_stoch_s, d_smooth=_stoch_s)
            _rv2_bb_p = getattr(cfg, 'RANGE_V2_BB_PERIOD', 20)
            _rv2_bb_d = getattr(cfg, 'RANGE_V2_BB_DEV', 2.0)
            h1_df["rv2_bb_upper"], h1_df["rv2_bb_mid"], h1_df["rv2_bb_lower"] = calc_bb(
                h1_df["Close"], _rv2_bb_p, _rv2_bb_d)
            if getattr(cfg, 'RANGE_V2_USE_KELTNER', False):
                _kc_p = getattr(cfg, 'RANGE_V2_KELTNER_PERIOD', 20)
                _kc_m = getattr(cfg, 'RANGE_V2_KELTNER_ATR_MULTI', 1.5)
                h1_df["kc_upper"], h1_df["kc_mid"], h1_df["kc_lower"] = calc_keltner(
                    h1_df["Close"], h1_df["High"], h1_df["Low"],
                    ema_period=_kc_p, atr_multi=_kc_m)

        # Ensure range column exists
        if "range" not in h1_df.columns:
            h1_df["range"] = h1_df["High"] - h1_df["Low"]

        # ============================================================
        # Pre-compute numpy arrays and searchsorted indices
        # ============================================================
        h4_index_i64 = h4_df.index.values.astype(np.int64)
        h1_index_i64 = h1_df.index.values.astype(np.int64)
        m15_index_i64 = m15_df.index.values.astype(np.int64)

        h4_ma_fast = h4_df["ma_fast"].values
        h4_ma_slow = h4_df["ma_slow"].values
        h4_adx = h4_df["adx"].values
        h4_plus_di = h4_df["plus_di"].values
        h4_minus_di = h4_df["minus_di"].values
        h4_ma_slow_slope = h4_df["ma_slow_slope"].values
        h4_rsi = h4_df["rsi"].values

        h1_range_arr = h1_df["range"].values
        h1_close_arr = h1_df["Close"].values
        h1_open_arr = h1_df["Open"].values
        h1_high_arr = h1_df["High"].values
        h1_low_arr = h1_df["Low"].values
        h1_ma_fast = h1_df["ma_fast"].values
        h1_ma_slow = h1_df["ma_slow"].values
        h1_rsi_arr = h1_df["rsi"].values
        h1_bb_upper = h1_df["bb_upper"].values
        h1_bb_lower = h1_df["bb_lower"].values
        h1_bb_mid = h1_df["bb_mid"].values

        h1_stoch_k = h1_df["stoch_k"].values if "stoch_k" in h1_df.columns else None
        h1_stoch_d = h1_df["stoch_d"].values if "stoch_d" in h1_df.columns else None
        h1_rv2_bb_upper = h1_df["rv2_bb_upper"].values if "rv2_bb_upper" in h1_df.columns else None
        h1_rv2_bb_mid = h1_df["rv2_bb_mid"].values if "rv2_bb_mid" in h1_df.columns else None
        h1_rv2_bb_lower = h1_df["rv2_bb_lower"].values if "rv2_bb_lower" in h1_df.columns else None
        h1_kc_upper = h1_df["kc_upper"].values if "kc_upper" in h1_df.columns else None
        h1_kc_mid = h1_df["kc_mid"].values if "kc_mid" in h1_df.columns else None
        h1_kc_lower = h1_df["kc_lower"].values if "kc_lower" in h1_df.columns else None

        m15_close = m15_df["Close"].values
        m15_high = m15_df["High"].values
        m15_low = m15_df["Low"].values
        m15_open = m15_df["Open"].values
        m15_ma_fast = m15_df["ma_fast"].values
        m15_ma_slow = m15_df["ma_slow"].values
        m15_atr = m15_df["atr"].values
        m15_atr_avg = m15_df["atr_avg"].values
        m15_volume = m15_df["Volume"].values if "Volume" in m15_df.columns else None
        m15_spread = m15_df["Spread"].values if "Spread" in m15_df.columns else None

        # USDJPY correlation signals
        usdjpy_corr_signals = None
        usdjpy_index_i64 = None
        if self.usdjpy_df is not None and cfg.USE_CORRELATION:
            usdjpy_corr_signals = precompute_correlation_signals(self.usdjpy_df, cfg)
            usdjpy_index_i64 = self.usdjpy_df.index.values.astype(np.int64)

        total_bars = len(m15_df)
        print(f"\n[BT] Backtest start: {m15_df.index[0].date()} -> {m15_df.index[-1].date()}")
        print(f"   [ULTRAFAST] M15 bars: {total_bars:,}")

        # ============================================================
        # PRE-RESOLVE all getattr(cfg, ...) calls ONCE (avoid per-bar overhead)
        # ============================================================
        _hsf = getattr(cfg, 'USE_HARD_SESSION_FILTER', False)
        _srat = getattr(cfg, 'USE_SRAT', False)
        _dd_esc = sorted(getattr(cfg, 'DD_ESCALATION', [(10, 12), (15, 15), (20, 18)]), reverse=True)
        _dz = getattr(cfg, 'USE_DEAD_ZONE_FILTER', False)
        _skip11 = getattr(cfg, 'SKIP_SCORE_11', False)
        _real_spread = getattr(cfg, 'USE_REALISTIC_SPREAD', False)
        _slippage = getattr(cfg, 'SLIPPAGE_POINTS', 0)
        _commission = getattr(cfg, 'COMMISSION_PER_LOT', 0)
        _intrabar_order = getattr(cfg, 'USE_INTRABAR_SLTP_ORDER', False)
        _dz_all_hours = getattr(cfg, 'DEAD_ZONE_ALL_HOURS', set()) if _dz else set()
        _dz_norm_hours = getattr(cfg, 'DEAD_ZONE_NORMAL_HOURS', set()) if _dz else set()

        # v8.0 feature flags (resolved once)
        _use_vol_trend = getattr(cfg, 'USE_VOL_TREND_FILTER', False)
        _vt_lookback = getattr(cfg, 'VOL_TREND_LOOKBACK', 10)
        _vt_expansion_block = getattr(cfg, 'VOL_TREND_EXPANSION_BLOCK', True)
        _use_adx_slope = getattr(cfg, 'USE_ADX_SLOPE', False)
        _adx_slope_lb = getattr(cfg, 'ADX_SLOPE_LOOKBACK', 5)
        _adx_falling_penalty = getattr(cfg, 'ADX_FALLING_PENALTY', 2)
        _use_conf_esc = getattr(cfg, 'USE_CONFIRMATION_ESCALATION', False)
        _esc_lb = getattr(cfg, 'CONF_ESC_LOOKBACK', 20)
        _esc_wr_thresh = getattr(cfg, 'CONF_ESC_WR_THRESHOLD', 0.40)
        _esc_boost = getattr(cfg, 'CONF_ESC_SCORE_BOOST', 2)
        _use_range_comp = getattr(cfg, 'USE_RANGE_COMPRESSION', False)
        _rc_lb = getattr(cfg, 'RANGE_COMP_LOOKBACK', 20)
        _rc_hist_lb = getattr(cfg, 'RANGE_COMP_HIST_LOOKBACK', 100)
        _rc_ratio = getattr(cfg, 'RANGE_COMP_RATIO', 0.5)
        _rc_boost = getattr(cfg, 'RANGE_COMP_SCORE_BOOST', 2)
        _use_seasonal = getattr(cfg, 'USE_SEASONAL_ADAPT', False)
        _weak_months = getattr(cfg, 'SEASONAL_WEAK_MONTHS', {1,2,3,4,5,6})
        _seasonal_boost = getattr(cfg, 'SEASONAL_SCORE_BOOST', 1)
        _seasonal_tp = getattr(cfg, 'SEASONAL_TP_TIGHTEN', 0.85)
        _use_ranging_adapt = getattr(cfg, 'USE_RANGING_ADAPTATION', False)
        _ranging_adx_thresh = getattr(cfg, 'RANGING_ADX_THRESHOLD', 20)
        _ranging_score_boost = getattr(cfg, 'RANGING_SCORE_BOOST', 0)
        _ranging_tp_cap = getattr(cfg, 'RANGING_TP_CAP', 3.0)
        _use_macro_trend = getattr(cfg, 'USE_MACRO_TREND_FILTER', False)
        _use_rsi_mom = getattr(cfg, 'USE_RSI_MOMENTUM_CONFIRM', False)
        _rsi_lb = getattr(cfg, 'RSI_MOMENTUM_LOOKBACK', 3)
        _use_range_rev = getattr(cfg, 'USE_RANGE_REVERSION', False)
        _use_tf_align = getattr(cfg, 'USE_TF_ALIGNMENT_FILTER', False)
        _tf_adx_thresh = getattr(cfg, 'TF_ALIGNMENT_ADX_THRESHOLD', 25)
        _use_score_spread = getattr(cfg, 'USE_SCORE_SPREAD', False)
        _spread_min = getattr(cfg, 'SCORE_SPREAD_MIN', 3)
        _use_consensus = getattr(cfg, 'USE_CONSENSUS_FILTER', False)
        _cons_min = getattr(cfg, 'CONSENSUS_MIN', 2)
        _cons_comps = getattr(cfg, 'CONSENSUS_COMPONENTS', [0, 1, 8, 13, 9])
        _use_dir_consist = getattr(cfg, 'USE_DIR_CONSISTENCY', False)
        _dc_window = getattr(cfg, 'DIR_CONSIST_WINDOW', 5)
        _dc_min = getattr(cfg, 'DIR_CONSIST_MIN', 4)

        # Range v2 pre-resolved
        _rv2_adx_thresh = getattr(cfg, 'RANGE_V2_ADX_THRESHOLD', 20) if _use_rv2 else 20
        _rv2_rsi_os = getattr(cfg, 'RANGE_V2_RSI_OS', 35) if _use_rv2 else 35
        _rv2_rsi_ob = getattr(cfg, 'RANGE_V2_RSI_OB', 65) if _use_rv2 else 65
        _rv2_use_stoch = getattr(cfg, 'RANGE_V2_USE_STOCH', True) if _use_rv2 else True
        _rv2_use_keltner = getattr(cfg, 'RANGE_V2_USE_KELTNER', False) if _use_rv2 else False
        _rv2_use_m15_confirm = getattr(cfg, 'RANGE_V2_USE_M15_CONFIRM', True) if _use_rv2 else True
        _rv2_use_trend_filter = getattr(cfg, 'RANGE_V2_TREND_FILTER', True) if _use_rv2 else True
        _rv2_sl_atr = getattr(cfg, 'RANGE_V2_SL_ATR', 1.5) if _use_rv2 else 1.5
        _rv2_risk = getattr(cfg, 'RANGE_V2_RISK_MULTI', 0.6) if _use_rv2 else 0.6
        _rv2_tp_mode = getattr(cfg, 'RANGE_V2_TP_MODE', 'bb_mid') if _use_rv2 else 'bb_mid'
        _rv2_time_exit_hours = getattr(cfg, 'RANGE_V2_TIME_EXIT_HOURS', 24) if _use_rv2 else 24
        _rv2_partial_atr_multi = getattr(cfg, 'RANGE_V2_PARTIAL_ATR', 0.5) if _use_rv2 else 0.5
        _rv2_partial_ratio = getattr(cfg, 'RANGE_V2_PARTIAL_RATIO', 0.5) if _use_rv2 else 0.5
        _kc_rsi_os = getattr(cfg, 'RANGE_V2_KELTNER_RSI_OS', 30) if _use_rv2 else 30
        _kc_rsi_ob = getattr(cfg, 'RANGE_V2_KELTNER_RSI_OB', 70) if _use_rv2 else 70

        # Range reversion pre-resolved
        _range_sl_multi = getattr(cfg, 'RANGE_SL_ATR_MULTI', 1.0)
        _range_tp_multi = getattr(cfg, 'RANGE_TP_ATR_MULTI', 2.0)
        _range_risk = getattr(cfg, 'RANGE_RISK_MULTIPLIER', 0.5)

        # Losing streak cooldown
        _use_losing_streak = getattr(cfg, 'USE_LOSING_STREAK_COOLDOWN', False)
        _streak_threshold = getattr(cfg, 'STREAK_THRESHOLD', 3)
        _streak_extra_cooldown = getattr(cfg, 'STREAK_EXTRA_COOLDOWN', 16)

        # Cache frequently used cfg attributes as locals
        _POINT = cfg.POINT
        _MIN_SL = cfg.MIN_SL_POINTS
        _MAX_SL = cfg.MAX_SL_POINTS
        _SL_ATR_MULTI = cfg.SL_ATR_MULTI
        _TP_ATR_MULTI = cfg.TP_ATR_MULTI
        _VOL_REGIME_HIGH = cfg.VOL_REGIME_HIGH
        _VOL_REGIME_LOW = cfg.VOL_REGIME_LOW
        _HIGH_VOL_SL_BONUS = cfg.HIGH_VOL_SL_BONUS
        _CRASH_ATR_MULTI = cfg.CRASH_ATR_MULTI
        _H4_ADX_THRESHOLD = cfg.H4_ADX_THRESHOLD
        _TRADE_START = cfg.TRADE_START_HOUR
        _TRADE_END = cfg.TRADE_END_HOUR
        _MACRO_TREND_ADX_THRESHOLD = cfg.MACRO_TREND_ADX_THRESHOLD
        _MIN_SCORE = cfg.MIN_SCORE
        _SRAT_THRESHOLDS = cfg.SRAT_THRESHOLDS if _srat else {}
        _TREND_SL_WIDEN = cfg.TREND_SL_WIDEN
        _TREND_SL_TIGHTEN = cfg.TREND_SL_TIGHTEN
        _MAX_PYRAMID = cfg.MAX_PYRAMID_POSITIONS
        _PYRAMID_DECAY = cfg.PYRAMID_LOT_DECAY
        _EQUITY_MA_PERIOD = cfg.EQUITY_MA_PERIOD
        _EQUITY_REDUCE = cfg.EQUITY_REDUCE_FACTOR
        _DIV_LOOKBACK = cfg.DIV_LOOKBACK
        _DIV_SWING = cfg.DIV_SWING_STRENGTH
        _FRIDAY_CLOSE = cfg.FRIDAY_CLOSE_HOUR

        # M15 index as python list for fast .hour / .date() access
        m15_index_ts = m15_df.index

        for i in range(100, total_bars):
            ct = m15_index_ts[i]
            cc = m15_close[i]
            ch = m15_high[i]
            cl = m15_low[i]
            co = m15_open[i]
            next_bar_open = m15_open[i + 1] if i + 1 < total_bars else cc

            _bar_spread = float(m15_spread[i]) if m15_spread is not None else None

            bar_day = ct.date() if hasattr(ct, 'date') else ct
            if bar_day != self.current_day:
                self.current_day = bar_day
                self.daily_pnl = 0.0
                self.circuit_breaker = False

            if self.circuit_breaker:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            self._manage_positions(ch, cl, cc, ct, i, m15_df,
                                   bar_open=co, bar_spread_points=_bar_spread)

            # v9.0: Range v2 position management
            if _use_rv2:
                for pos in list(self.open_positions):
                    if pos.get('entry_type') != 'range_v2':
                        continue
                    _time_bar = pos.get('rv2_time_exit_bar', 0)
                    if _time_bar > 0 and i >= _time_bar:
                        self._close_position(pos, cc, ct, "TimeExit", i,
                                             bar_spread_points=_bar_spread)
                        continue
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
                                    pt = _POINT
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
                                    if _commission > 0:
                                        pnl_usd -= _commission * closed_lot * 0.5
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
                                pos['sl'] = pos['entry'] + (10 * _POINT if pos['direction'] == 'BUY' else -10 * _POINT)
                                pos['rv2_partial_done'] = True
                                pos['breakeven_done'] = True

            if self.balance > self.peak_balance:
                self.peak_balance = self.balance
            current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0

            hour = ct.hour if hasattr(ct, "hour") else 12

            # Weekend close
            if cfg.USE_WEEKEND_CLOSE:
                weekday = ct.weekday() if hasattr(ct, 'weekday') else 0
                if weekday == 4 and hour >= _FRIDAY_CLOSE:
                    if self.open_positions:
                        for pos in list(self.open_positions):
                            self._close_position(pos, cc, ct, "Weekend", i,
                                                 bar_spread_points=_bar_spread)
                        self.weekend_closes += 1
                    self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                    continue

            if hour < _TRADE_START or hour >= _TRADE_END:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            if hasattr(ct, "dayofweek") and ct.dayofweek == 4 and hour >= 18:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            if i < self.cooldown_until:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            if self.simulate_news_filter(ct):
                self.news_blocks += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            if _hsf and hasattr(self, 'check_hard_session_filter') and self.check_hard_session_filter(ct):
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            current_atr = m15_atr[i]
            current_atr_avg = m15_atr_avg[i]
            if np.isnan(current_atr) or np.isnan(current_atr_avg) or current_atr_avg <= 0:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            if not self.check_dynamic_spread(current_atr, current_atr_avg):
                self.spread_blocks += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            regime = self.get_advanced_regime(current_atr, current_atr_avg)
            if regime == 0:
                self.crash_skips += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            vol_ratio = current_atr / current_atr_avg

            sl_multi = _SL_ATR_MULTI
            if vol_ratio > _VOL_REGIME_HIGH:
                sl_multi += _HIGH_VOL_SL_BONUS

            atr_points = current_atr / _POINT
            dynamic_sl_points = atr_points * sl_multi
            dynamic_sl_points = max(_MIN_SL, min(_MAX_SL, dynamic_sl_points))
            dynamic_tp_points = atr_points * _TP_ATR_MULTI
            if dynamic_tp_points < dynamic_sl_points * 1.5:
                dynamic_tp_points = dynamic_sl_points * 1.5

            # H4 lookup via searchsorted
            ct_i64 = m15_index_i64[i]
            h4_pos = np.searchsorted(h4_index_i64, ct_i64, side='right')
            if h4_pos < 2:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h4_idx = h4_pos - 1

            h4_row_ma_fast = h4_ma_fast[h4_idx]
            h4_row_ma_slow = h4_ma_slow[h4_idx]
            h4_row_adx = h4_adx[h4_idx]
            h4_row_plus_di = h4_plus_di[h4_idx]
            h4_row_minus_di = h4_minus_di[h4_idx]
            h4_row_ma_slow_slope = h4_ma_slow_slope[h4_idx]
            h4_row_rsi = h4_rsi[h4_idx]

            # H1 lookup via searchsorted
            h1_pos = np.searchsorted(h1_index_i64, ct_i64, side='right')
            if h1_pos < 4:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h1_idx = h1_pos - 1
            h1_idx_prev = h1_pos - 2

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

            # v9.0: Range v2 BB mid TP check
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

            # Dead zone
            dead_zone_all = hour in _dz_all_hours
            dead_zone_normal = hour in _dz_norm_hours if not dead_zone_all else False

            # v9.0: Range Strategy v2 entry
            _rv2_entered = False
            if _use_rv2 and len(self.open_positions) == 0:
                _rv2_is_ranging = (not np.isnan(h4_row_adx) and h4_row_adx < _rv2_adx_thresh)

                if _rv2_is_ranging and not dead_zone_all:
                    rv2_buy = False
                    rv2_sell = False

                    if not _rv2_use_keltner:
                        _bb_up = h1_rv2_bb_upper[h1_idx] if h1_rv2_bb_upper is not None else h1_bb_upper[h1_idx]
                        _bb_low = h1_rv2_bb_lower[h1_idx] if h1_rv2_bb_lower is not None else h1_bb_lower[h1_idx]
                        _bb_m = h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]

                        if not (np.isnan(_bb_up) or np.isnan(_bb_low) or np.isnan(h1_curr_rsi)):
                            _bw = _bb_up - _bb_low
                            _bb_proximity = _bw * 0.10 if _bw > 0 else 0

                            bb_buy = (h1_curr_close <= _bb_low + _bb_proximity)
                            rsi_buy = (h1_curr_rsi < _rv2_rsi_os)
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
                                    stoch_buy = (_sk < 30 and
                                                 ((_sk > _sd and _sk_prev <= _sd_prev) or
                                                  (_sk > _sk_prev and _sk_prev < 20)))
                                    stoch_sell = (_sk > 70 and
                                                  ((_sk < _sd and _sk_prev >= _sd_prev) or
                                                   (_sk < _sk_prev and _sk_prev > 80)))
                                else:
                                    stoch_buy = False
                                    stoch_sell = False

                            m15_bull = True
                            m15_bear = True
                            if _rv2_use_m15_confirm:
                                m15_bull = (m15_close[i] > m15_open[i])
                                m15_bear = (m15_close[i] < m15_open[i])

                            rv2_buy = (bb_buy and rsi_buy and stoch_buy and m15_bull)
                            rv2_sell = (bb_sell and rsi_sell and stoch_sell and m15_bear)

                    if _rv2_use_trend_filter and not np.isnan(h4_row_ma_fast) and not np.isnan(h4_row_ma_slow):
                        if h4_row_ma_fast < h4_row_ma_slow:
                            rv2_buy = False
                        elif h4_row_ma_fast > h4_row_ma_slow:
                            rv2_sell = False

                    if _rv2_use_keltner and h1_kc_upper is not None:
                        _kc_up = h1_kc_upper[h1_idx]
                        _kc_low = h1_kc_lower[h1_idx]

                        if not (np.isnan(_kc_up) or np.isnan(_kc_low) or np.isnan(h1_curr_rsi)):
                            prev_bearish = (h1_close_arr[h1_idx_prev] < h1_open_arr[h1_idx_prev])
                            curr_bullish = (h1_close_arr[h1_idx] > h1_open_arr[h1_idx])
                            rv2_buy = (h1_curr_close < _kc_low and h1_curr_rsi < _kc_rsi_os
                                       and prev_bearish and curr_bullish)

                            prev_bullish = (h1_close_arr[h1_idx_prev] > h1_open_arr[h1_idx_prev])
                            curr_bearish = (h1_close_arr[h1_idx] < h1_open_arr[h1_idx])
                            rv2_sell = (h1_curr_close > _kc_up and h1_curr_rsi > _kc_rsi_ob
                                        and prev_bullish and curr_bearish)

                    if rv2_buy or rv2_sell:
                        rv2_sl_pts = max(_MIN_SL, min(_MAX_SL, atr_points * _rv2_sl_atr))

                        if _rv2_tp_mode == 'bb_mid':
                            _tp_bb_mid = h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]
                            if not np.isnan(_tp_bb_mid):
                                if rv2_buy:
                                    rv2_tp_pts = abs(_tp_bb_mid - cc) / _POINT
                                else:
                                    rv2_tp_pts = abs(cc - _tp_bb_mid) / _POINT
                                rv2_tp_pts = max(rv2_tp_pts, rv2_sl_pts * 1.0)
                            else:
                                rv2_tp_pts = atr_points * 2.0
                        else:
                            rv2_tp_pts = atr_points * 2.0

                        rv2_dir = "BUY" if rv2_buy else "SELL"
                        component_mask_rv2 = [0] * 15
                        lot_multiplier_rv2 = 1.0
                        if cfg.USE_EQUITY_CURVE and len(self.recent_trade_pnls) >= _EQUITY_MA_PERIOD:
                            recent = self.recent_trade_pnls[-_EQUITY_MA_PERIOD:]
                            if np.mean(recent) < 0:
                                lot_multiplier_rv2 = _EQUITY_REDUCE

                        self._open_trade(rv2_dir, next_bar_open, ct, 0, current_dd,
                                         rv2_sl_pts, rv2_tp_pts, current_atr,
                                         lot_multiplier_rv2 * _rv2_risk, component_mask_rv2,
                                         entry_type="range_v2", momentum_burst=False,
                                         entry_bar=i, bar_spread_points=_bar_spread)
                        pos = self.open_positions[-1]
                        pos["rv2_bb_mid_target"] = _tp_bb_mid if _rv2_tp_mode == 'bb_mid' and not np.isnan(h1_rv2_bb_mid[h1_idx] if h1_rv2_bb_mid is not None else h1_bb_mid[h1_idx]) else None
                        pos["rv2_time_exit_bar"] = i + int(_rv2_time_exit_hours * 4)
                        pos["rv2_partial_done"] = False
                        pos["rv2_partial_atr"] = _rv2_partial_atr_multi * current_atr
                        pos["rv2_partial_ratio"] = _rv2_partial_ratio
                        self.range_trades += 1
                        _rv2_entered = True

            if _rv2_entered:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # ---- Scoring ----
            buy_score = 0
            sell_score = 0
            component_mask = [0] * 15

            # 1. H4 Trend (3 pts)
            if not np.isnan(h4_row_adx) and h4_row_adx >= _H4_ADX_THRESHOLD:
                if h4_row_ma_fast > h4_row_ma_slow and h4_row_plus_di > h4_row_minus_di:
                    buy_score += 3
                    component_mask[0] = 1
                elif h4_row_ma_fast < h4_row_ma_slow and h4_row_minus_di > h4_row_plus_di:
                    sell_score += 3
                    component_mask[0] = -1

            # 1b. Macro trend direction
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

            # 5. M15 MA cross (2 pts)
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

            # 6. Channel regression (1 pt)
            cs = calc_channel_signal_fast(h1_close_arr, h1_pos, 40)
            if cs == 1:
                buy_score += 1
                component_mask[5] = 1
            elif cs == -1:
                sell_score += 1
                component_mask[5] = -1

            # 7. Momentum scoring (+1 pt)
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

            # 8. Session bonus (+1 pt)
            if cfg.USE_SESSION_BONUS:
                if (13 <= hour <= 16) or (8 <= hour <= 10):
                    buy_score += 1
                    sell_score += 1
                    component_mask[7] = 1

            # 9. USD Correlation (+2) -- pre-computed signals
            if cfg.USE_CORRELATION and usdjpy_corr_signals is not None:
                uj_pos = np.searchsorted(usdjpy_index_i64, ct_i64, side='right')
                if uj_pos > 0:
                    corr = int(usdjpy_corr_signals[uj_pos - 1])
                    if corr == 1:
                        buy_score += 2
                        component_mask[8] = 1
                    elif corr == -1:
                        sell_score += 2
                        component_mask[8] = -1

            # 10. RSI Divergence (+2) -- VECTORIZED
            if cfg.USE_DIVERGENCE:
                div = get_divergence_vec(h1_close_arr, h1_rsi_arr, h1_pos,
                                         _DIV_LOOKBACK, _DIV_SWING)
                if div == 1:
                    buy_score += 2
                    component_mask[9] = 1
                elif div == -1:
                    sell_score += 2
                    component_mask[9] = -1

            # 11. S/R Level (+1/-1) -- VECTORIZED
            if cfg.USE_SR_LEVELS:
                sr = get_sr_signal_vec(h1_high_arr, h1_low_arr, h1_pos,
                                       cc, current_atr, cfg)
                if sr == 1:
                    buy_score += 1
                    sell_score -= 1
                    component_mask[10] = 1
                elif sr == -1:
                    sell_score += 1
                    buy_score -= 1
                    component_mask[10] = -1

            # 12. Candle Pattern (+1)
            if cfg.USE_CANDLE_PATTERNS:
                cdl = get_candle_pattern_fast(h1_open_arr, h1_high_arr,
                                              h1_low_arr, h1_close_arr, h1_pos)
                if cdl == 1:
                    buy_score += 1
                    component_mask[11] = 1
                elif cdl == -1:
                    sell_score += 1
                    component_mask[11] = -1

            # 13. H4 RSI Alignment (+1)
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

            # 14. Momentum Burst (+3)
            _h4_row = {"ma_fast": h4_row_ma_fast, "ma_slow": h4_row_ma_slow}
            _h1_curr = {"ma_fast": h1_curr_ma_fast, "ma_slow": h1_curr_ma_slow}
            _m15_curr = {"ma_fast": m15_curr_ma_fast, "ma_slow": m15_curr_ma_slow}
            burst = self.get_momentum_burst(_h4_row, _h1_curr, _m15_curr, None)
            if burst > 0:
                buy_score += burst
                component_mask[13] = 1
            elif burst < 0:
                sell_score += abs(burst)
                component_mask[13] = -1

            # 15. Volume Climax (+2)
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

            buy_score = max(0, buy_score)
            sell_score = max(0, sell_score)

            # SRAT threshold
            if _srat and hour in _SRAT_THRESHOLDS:
                dynamic_min_score = _SRAT_THRESHOLDS[hour]
            else:
                dynamic_min_score = _MIN_SCORE
            for dd_thresh, dd_score in _dd_esc:
                if current_dd >= dd_thresh:
                    dynamic_min_score = max(dynamic_min_score, dd_score)
                    break
            if regime == 1:
                dynamic_min_score += 3

            is_ranging = False
            if _use_ranging_adapt:
                if not np.isnan(h4_row_adx) and h4_row_adx < _ranging_adx_thresh:
                    is_ranging = True
                if is_ranging and _ranging_score_boost > 0:
                    dynamic_min_score += _ranging_score_boost

            # v8.0a: Volatility Trend Filter
            vol_trend_block = False
            if _use_vol_trend and i >= _vt_lookback:
                atr_now = m15_atr[i]
                atr_past = m15_atr[i - _vt_lookback]
                if not np.isnan(atr_now) and not np.isnan(atr_past) and atr_past > 0:
                    if atr_now / atr_past > 1.20 and (np.isnan(h4_row_adx) or h4_row_adx < 20):
                        if _vt_expansion_block:
                            vol_trend_block = True

            # v8.0b: ADX Slope Filter
            if _use_adx_slope and not np.isnan(h4_row_adx) and h4_idx >= _adx_slope_lb:
                adx_prev = h4_adx[h4_idx - _adx_slope_lb]
                if not np.isnan(adx_prev) and h4_row_adx - adx_prev < 0:
                    dynamic_min_score += _adx_falling_penalty

            # v8.0c: Confirmation Escalation
            if _use_conf_esc and len(self.recent_trade_pnls) >= _esc_lb:
                recent_wins = sum(1 for p in self.recent_trade_pnls[-_esc_lb:] if p > 0)
                if recent_wins / _esc_lb < _esc_wr_thresh:
                    dynamic_min_score += _esc_boost

            # v8.0e: Range Compression
            if _use_range_comp and h1_pos >= _rc_hist_lb:
                recent_range = np.nanmean(h1_range_arr[h1_pos - _rc_lb:h1_pos])
                hist_range = np.nanmean(h1_range_arr[h1_pos - _rc_hist_lb:h1_pos - _rc_lb])
                if hist_range > 0 and recent_range / hist_range < _rc_ratio:
                    dynamic_min_score += _rc_boost

            # v8.0f: Seasonal Adaptation
            is_weak_season = False
            if _use_seasonal:
                bar_month = ct.month if hasattr(ct, 'month') else 1
                if bar_month in _weak_months:
                    is_weak_season = True
                    dynamic_min_score += _seasonal_boost

            # Equity Curve Filter
            lot_multiplier = 1.0
            if cfg.USE_EQUITY_CURVE and len(self.recent_trade_pnls) >= _EQUITY_MA_PERIOD:
                recent = self.recent_trade_pnls[-_EQUITY_MA_PERIOD:]
                if np.mean(recent) < 0:
                    lot_multiplier = _EQUITY_REDUCE

            tp_multi = 1.5 if abs(burst) == 3 else 1.0
            adjusted_tp_points = dynamic_tp_points * tp_multi

            if is_ranging:
                ranging_tp = atr_points * _ranging_tp_cap
                if ranging_tp < adjusted_tp_points:
                    adjusted_tp_points = ranging_tp
                if adjusted_tp_points < dynamic_sl_points * 1.5:
                    adjusted_tp_points = dynamic_sl_points * 1.5

            if _use_seasonal and is_weak_season:
                adjusted_tp_points *= _seasonal_tp
                if adjusted_tp_points < dynamic_sl_points * 1.5:
                    adjusted_tp_points = dynamic_sl_points * 1.5

            # Pyramiding
            pos_count = len(self.open_positions)
            can_enter = pos_count < _MAX_PYRAMID
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

            # Entry
            entry_type = "normal"
            entered = False

            # Range-Reversion entry
            if _use_range_rev and is_ranging and not dead_zone_all and pos_count == 0:
                _range_sr = get_sr_signal_vec(h1_high_arr, h1_low_arr, h1_pos,
                                              cc, current_atr, cfg) if cfg.USE_SR_LEVELS else 0
                range_dir, range_conf = self.get_range_signal(
                    h1_curr_close, h1_prev_close, h1_curr_rsi,
                    h1_curr_bb_upper, h1_curr_bb_lower, _range_sr, cfg
                )
                if range_dir is not None:
                    range_sl_pts = max(_MIN_SL, min(_MAX_SL, atr_points * _range_sl_multi))
                    range_tp_pts = atr_points * _range_tp_multi
                    if range_tp_pts < range_sl_pts * 1.5:
                        range_tp_pts = range_sl_pts * 1.5
                    self._open_trade(range_dir, next_bar_open, ct, range_conf, current_dd,
                                     range_sl_pts, range_tp_pts, current_atr,
                                     lot_multiplier * _range_risk, component_mask,
                                     entry_type="range_rev", momentum_burst=False,
                                     entry_bar=i, bar_spread_points=_bar_spread)
                    self.range_trades += 1
                    entered = True

            if not dead_zone_all and not vol_trend_block and can_enter and (not is_pyramid or pyramid_ok) and not entered:
                if dead_zone_normal and not is_pyramid:
                    pass
                else:
                    pyramid_lot_multi = 1.0
                    if is_pyramid:
                        pyramid_lot_multi = _PYRAMID_DECAY ** pos_count
                        entry_type = "pyramid"

                    adj_sl = dynamic_sl_points
                    adj_tp = adjusted_tp_points
                    if macro_trend_dir != 0:
                        if (buy_score > sell_score and macro_trend_dir == 1) or \
                           (sell_score > buy_score and macro_trend_dir == -1):
                            adj_sl = min(dynamic_sl_points * _TREND_SL_WIDEN, _MAX_SL)
                        elif (buy_score > sell_score and macro_trend_dir == -1) or \
                             (sell_score > buy_score and macro_trend_dir == 1):
                            adj_sl = max(dynamic_sl_points * _TREND_SL_TIGHTEN, _MIN_SL)

                    effective_buy = buy_score
                    effective_sell = sell_score
                    if _skip11:
                        if buy_score == 11:
                            effective_buy = 0
                        if sell_score == 11:
                            effective_sell = 0

                    # Macro-Trend Filter
                    if _use_macro_trend:
                        if h4_row_adx >= _MACRO_TREND_ADX_THRESHOLD:
                            if h4_row_ma_fast > h4_row_ma_slow:
                                effective_sell = 0
                            elif h4_row_ma_fast < h4_row_ma_slow:
                                effective_buy = 0

                    # RSI Momentum Confirmation
                    if _use_rsi_mom:
                        if not np.isnan(h1_curr_rsi) and h1_idx >= _rsi_lb:
                            rsi_past = h1_rsi_arr[h1_idx - _rsi_lb]
                            if not np.isnan(rsi_past):
                                if effective_buy > effective_sell:
                                    if not (h1_curr_rsi > 50 and h1_curr_rsi > rsi_past):
                                        effective_buy = 0
                                elif effective_sell > effective_buy:
                                    if not (h1_curr_rsi < 50 and h1_curr_rsi < rsi_past):
                                        effective_sell = 0

                    # H4+H1 Alignment Filter
                    if _use_tf_align:
                        if np.isnan(h4_row_adx) or h4_row_adx < _tf_adx_thresh:
                            h4_bullish = not np.isnan(h4_row_ma_fast) and h4_row_ma_fast > h4_row_ma_slow
                            h4_bearish = not np.isnan(h4_row_ma_fast) and h4_row_ma_fast < h4_row_ma_slow
                            h1_bullish = not np.isnan(h1_curr_ma_fast) and h1_curr_ma_fast > h1_curr_ma_slow
                            h1_bearish = not np.isnan(h1_curr_ma_fast) and h1_curr_ma_fast < h1_curr_ma_slow
                            if effective_buy > effective_sell:
                                if not (h4_bullish and h1_bullish):
                                    effective_buy = 0
                            elif effective_sell > effective_buy:
                                if not (h4_bearish and h1_bearish):
                                    effective_sell = 0

                    # Score Spread Requirement
                    if _use_score_spread:
                        if effective_buy >= dynamic_min_score and effective_buy > effective_sell:
                            if (effective_buy - effective_sell) < _spread_min:
                                effective_buy = 0
                        elif effective_sell >= dynamic_min_score and effective_sell > effective_buy:
                            if (effective_sell - effective_buy) < _spread_min:
                                effective_sell = 0

                    # Consensus Filter
                    if _use_consensus:
                        if effective_buy >= dynamic_min_score and effective_buy > effective_sell:
                            agree = sum(1 for ci in _cons_comps if component_mask[ci] == 1)
                            if agree < _cons_min:
                                effective_buy = 0
                        elif effective_sell >= dynamic_min_score and effective_sell > effective_buy:
                            agree = sum(1 for ci in _cons_comps if component_mask[ci] == -1)
                            if agree < _cons_min:
                                effective_sell = 0

                    # Directional Consistency
                    if _use_dir_consist:
                        if not hasattr(self, '_signal_history'):
                            self._signal_history = []
                        if buy_score > sell_score:
                            self._signal_history.append(1)
                        elif sell_score > buy_score:
                            self._signal_history.append(-1)
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

            # Reversal mode
            if not dead_zone_all and not dead_zone_normal and not entered and pos_count == 0:
                reversal = self._check_reversal_vec(
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

    def _check_reversal_vec(self, h1_close_arr, h1_open_arr, h1_high_arr, h1_low_arr,
                             h1_rsi_arr, h1_pos, ct, cc, current_atr, h1_curr_rsi, cfg):
        """Optimized reversal check using vectorized functions."""
        if not cfg.USE_REVERSAL_MODE:
            return 0
        rsi = h1_curr_rsi if not np.isnan(h1_curr_rsi) else 50

        div_signal = get_divergence_vec(h1_close_arr, h1_rsi_arr, h1_pos,
                                         cfg.DIV_LOOKBACK, cfg.DIV_SWING_STRENGTH)
        sr_signal = get_sr_signal_vec(h1_high_arr, h1_low_arr, h1_pos,
                                       cc, current_atr, cfg)
        candle_signal = get_candle_pattern_fast(h1_open_arr, h1_high_arr,
                                                 h1_low_arr, h1_close_arr, h1_pos)

        if rsi < 25 and div_signal > 0 and sr_signal > 0 and candle_signal > 0:
            return 1
        if rsi > 75 and div_signal < 0 and sr_signal < 0 and candle_signal < 0:
            return -1
        return 0


# ============================================================
# Data loading with pickle cache
# ============================================================
def _csv_hash(*paths):
    h = hashlib.md5()
    for p in paths:
        st = os.stat(p)
        h.update(f"{p}:{st.st_mtime}:{st.st_size}".encode())
    return h.hexdigest()


def load_data_cached():
    """Load CSV data with pickle cache for fast subsequent loads."""
    csv_files = ['XAUUSD_H4.csv', 'XAUUSD_H1.csv', 'XAUUSD_M15.csv', 'USDJPY_H1.csv']
    cache_path = '/tmp/_gold_bt_cache.pkl'

    current_hash = _csv_hash(*csv_files)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                cached = pickle.load(f)
            if cached.get('hash') == current_hash:
                return cached['h4'], cached['h1'], cached['m15'], cached['usdjpy']
        except Exception:
            pass

    from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
    h4 = load_csv('XAUUSD_H4.csv')
    h1 = merge_and_fill(load_csv('XAUUSD_H1.csv'), generate_h1_from_h4(h4))
    m15 = merge_and_fill(load_csv('XAUUSD_M15.csv'), generate_m15_from_h1(h1))
    usdjpy = load_csv('USDJPY_H1.csv')

    try:
        with open(cache_path, 'wb') as f:
            pickle.dump({'hash': current_hash, 'h4': h4, 'h1': h1, 'm15': m15, 'usdjpy': usdjpy}, f,
                        protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass

    return h4, h1, m15, usdjpy


# ============================================================
# Pre-compute indicators once on full data
# ============================================================
def precompute_all_indicators(h4, h1, m15, usdjpy, cfg):
    """Compute all indicators once on the full dataset.

    Returns new DataFrames with indicator columns added.
    Workers can then slice these without recomputing.
    """
    h4 = h4.copy()
    h1 = h1.copy()
    m15 = m15.copy()
    usdjpy = usdjpy.copy()

    # H4 indicators
    h4["ma_fast"] = calc_sma(h4["Close"], cfg.H4_MA_FAST)
    h4["ma_slow"] = calc_sma(h4["Close"], cfg.H4_MA_SLOW)
    h4["adx"], h4["plus_di"], h4["minus_di"] = calc_adx(
        h4["High"], h4["Low"], h4["Close"], cfg.H4_ADX_PERIOD)
    h4["ma_slow_slope"] = h4["ma_slow"] - h4["ma_slow"].shift(cfg.H4_SLOPE_PERIOD)
    h4["rsi"] = calc_rsi(h4["Close"], cfg.H4_RSI_PERIOD)

    # H1 indicators
    h1["ma_fast"] = calc_ema(h1["Close"], cfg.H1_MA_FAST)
    h1["ma_slow"] = calc_ema(h1["Close"], cfg.H1_MA_SLOW)
    h1["rsi"] = calc_rsi(h1["Close"], cfg.H1_RSI_PERIOD)
    h1["bb_upper"], h1["bb_mid"], h1["bb_lower"] = calc_bb(
        h1["Close"], cfg.H1_BB_PERIOD, cfg.H1_BB_DEV)
    h1["range"] = h1["High"] - h1["Low"]

    # M15 indicators
    m15["ma_fast"] = calc_ema(m15["Close"], cfg.M15_MA_FAST)
    m15["ma_slow"] = calc_ema(m15["Close"], cfg.M15_MA_SLOW)
    m15["atr"] = calc_atr(m15["High"], m15["Low"], m15["Close"], cfg.ATR_PERIOD)
    m15["atr_avg"] = m15["atr"].rolling(window=cfg.VOL_REGIME_PERIOD).mean()

    # USDJPY indicators
    usdjpy["ema_fast"] = calc_ema(usdjpy["Close"], cfg.CORR_MA_FAST)
    usdjpy["ema_slow"] = calc_ema(usdjpy["Close"], cfg.CORR_MA_SLOW)
    usdjpy["atr"] = calc_atr(usdjpy["High"], usdjpy["Low"], usdjpy["Close"], 14)

    return h4, h1, m15, usdjpy


# ============================================================
# Global data for fork-based workers
# ============================================================
_G_H4 = None
_G_H1 = None
_G_M15 = None
_G_USDJPY = None


def _worker(task_queue, result_queue):
    """Worker process: reads from globals (inherited via fork)."""
    while True:
        task = task_queue.get()
        if task is None:
            break

        idx, name, start_ts, end_ts = task

        m15w = _G_M15[(_G_M15.index >= start_ts) & (_G_M15.index < end_ts)]
        if len(m15w) < 200:
            result_queue.put((idx, None))
            continue

        h1w = _G_H1[_G_H1.index < end_ts]
        h4w = _G_H4[_G_H4.index < end_ts]
        uw = _G_USDJPY[_G_USDJPY.index < end_ts]

        cfg = GoldConfig()
        bt = GoldBacktesterUltraFast(cfg)

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            bt.run(h4w, h1w, m15w, uw, _indicators_precomputed=True)
        finally:
            sys.stdout = old_stdout

        trades = bt.trades
        if len(trades) < 3:
            result_queue.put((idx, None))
            continue

        gp = sum(t['pnl_jpy'] for t in trades if t['pnl_jpy'] > 0)
        gl = abs(sum(t['pnl_jpy'] for t in trades if t['pnl_jpy'] <= 0))
        pf = gp / gl if gl > 0 else 99
        pnl = sum(t['pnl_jpy'] for t in trades)
        wins = sum(1 for t in trades if t['pnl_jpy'] > 0)
        losses = len(trades) - wins
        wr = wins / len(trades) * 100
        verdict = 'PASS' if pf >= 1.3 and len(trades) >= 3 else 'FAIL'

        result_queue.put((idx, {
            'name': name,
            'pf': pf,
            'trades': len(trades),
            'wr': wr,
            'pnl': pnl,
            'wins': wins,
            'losses': losses,
            'gp': gp,
            'gl': gl,
            'verdict': verdict,
        }))


# ============================================================
# Main
# ============================================================
def main():
    global _G_H4, _G_H1, _G_M15, _G_USDJPY

    t_start = time.time()

    # Load raw data (cached)
    h4_raw, h1_raw, m15_raw, usdjpy_raw = load_data_cached()
    t_load = time.time()

    # Pre-compute all indicators ONCE on full data
    cfg = GoldConfig()
    _G_H4, _G_H1, _G_M15, _G_USDJPY = precompute_all_indicators(
        h4_raw, h1_raw, m15_raw, usdjpy_raw, cfg)
    t_indicators = time.time()

    # Build quarter list
    walks = []
    for year in range(2022, 2026):
        for q in range(1, 5):
            ms = (q-1)*3+1
            me = q*3
            start = pd.Timestamp(f'{year}-{ms:02d}-01')
            end = pd.Timestamp(f'{year}-{me+1:02d}-01') if me < 12 else pd.Timestamp(f'{year+1}-01-01')
            if start >= _G_M15.index[0] and start < _G_M15.index[-1]:
                walks.append((f'{year}-Q{q}', start, end))

    print(f'Config: TP={cfg.TP_ATR_MULTI} SL={cfg.SL_ATR_MULTI} SRAT[8]={cfg.SRAT_THRESHOLDS[8]} RSI_MOM={cfg.USE_RSI_MOMENTUM_CONFIRM} RADX={cfg.RANGING_ADX_THRESHOLD}', flush=True)

    # Launch workers
    n_workers = min(cpu_count(), len(walks))
    task_queue = Queue()
    result_queue = Queue()

    for idx, (name, start, end) in enumerate(walks):
        task_queue.put((idx, name, start, end))
    for _ in range(n_workers):
        task_queue.put(None)

    workers = []
    for _ in range(n_workers):
        p = Process(target=_worker, args=(task_queue, result_queue))
        p.start()
        workers.append(p)

    results = [None] * len(walks)
    for _ in range(len(walks)):
        idx, result = result_queue.get()
        results[idx] = result

    for p in workers:
        p.join()

    t_compute = time.time()

    # Print results
    total_pnl = 0
    total_trades = 0
    total_wins = 0
    total_gross_profit = 0
    total_gross_loss = 0
    passes = 0

    print(f'\n{"期間":<12} {"PF":>6} {"トレード":>8} {"勝率":>6} {"損益(JPY)":>12} {"勝":>4} {"負":>4}  {"判定"}', flush=True)
    print('-' * 75, flush=True)

    for r in results:
        if r is None:
            continue
        total_pnl += r['pnl']
        total_trades += r['trades']
        total_wins += r['wins']
        total_gross_profit += r['gp']
        total_gross_loss += r['gl']
        if r['verdict'] == 'PASS':
            passes += 1
        print(f'{r["name"]:<12} {r["pf"]:>6.2f} {r["trades"]:>8} {r["wr"]:>5.1f}% {r["pnl"]:>+12,.0f} {r["wins"]:>4} {r["losses"]:>4}  {r["verdict"]}', flush=True)

    print('-' * 75, flush=True)
    total_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    total_pf = total_gross_profit / total_gross_loss if total_gross_loss > 0 else 99
    print(f'{"合計":<12} {total_pf:>6.2f} {total_trades:>8} {total_wr:>5.1f}% {total_pnl:>+12,.0f} {total_wins:>4} {total_trades-total_wins:>4}  {passes}/{len(walks)}', flush=True)

    t_end = time.time()
    print(f'\n[timing] load={t_load-t_start:.1f}s indicators={t_indicators-t_load:.1f}s compute={t_compute-t_indicators:.1f}s total={t_end-t_start:.1f}s workers={n_workers}', flush=True)


if __name__ == '__main__':
    main()
