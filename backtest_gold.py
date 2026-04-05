"""
AntigravityMTF EA Gold v4.0 -- Backtester (6 months)
ATR-based dynamic SL/TP, volatility regime, session bonus, momentum, partial close
v3.0: USD Correlation, RSI Divergence, S/R Levels, Candle Patterns, H4 RSI,
      Chandelier Exit, Equity Curve Filter, Adaptive Sizing (Half-Kelly)
v4.0: News Filter, Dynamic Spread, Weekend Close, 4-State Regime (Crash/Ranging/Trending/Volatile),
      Stale Trade Exit, Daily Circuit Breaker, Momentum Burst (+3pt), Volume Climax (+2pt),
      Pyramiding (up to 3), Reversal Mode, Risk Metrics (Sharpe/Sortino/Calmar)
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")


class GoldConfig:
    SYMBOL = "GC=F"
    INITIAL_BALANCE = 300_000  # 30万円
    RISK_PERCENT = 0.75        # v5.1: 0.5→0.75% バランス型
    MAX_POSITIONS = 1          # v6.0: pyramid disabled (PF 1.14→1.45, DD 21.6%→11.0%)
    MIN_SCORE = 12             # v6.0: raised from 9 (PF 1.45→1.61, WR 52.7%→55.5%)
    COOLDOWN_BARS = 32         # v5.4: SL後32本(=8時間)エントリー禁止 (16→32: DD 14.7%→13.3%)
    MAX_SPREAD_POINTS = 50
    POINT = 0.01               # Gold 1point = $0.01
    MAX_DD_PERCENT = 6.0
    DD_HALF_RISK = 2.5
    MAX_LOT = 0.50
    MIN_LOT = 0.01
    CONTRACT_SIZE = 100        # 1lot = 100oz (standard)

    # ATR-based SL/TP (v2.0)
    ATR_PERIOD = 14
    SL_ATR_MULTI = 1.2         # v6.1: tighter SL for better RR (1.5→1.2, PF 1.60→1.96)
    TP_ATR_MULTI = 4.0         # v8.1: tighter TP (5.0→4.0: WFA 12→13/14 with RTP=5.0)
    TRAIL_ATR_MULTI = 1.0
    BE_ATR_MULTI = 0.5         # v9.3: 0.8→0.5 (16/16 PASS, 早期BEで利益保護)
    MIN_SL_POINTS = 200
    MAX_SL_POINTS = 1500

    # Volatility regime (v2.0)
    VOL_REGIME_PERIOD = 50
    VOL_REGIME_LOW = 0.7
    VOL_REGIME_HIGH = 1.5
    HIGH_VOL_SL_BONUS = 0.0    # v6.1: no extra SL in volatile conditions (0.5→0.0, PF +0.07)

    # Session bonus (v2.0)
    USE_SESSION_BONUS = True

    # Momentum (v2.0)
    USE_MOMENTUM = True

    # Partial close (v2.0)
    USE_PARTIAL_CLOSE = True
    PARTIAL_CLOSE_RATIO = 0.5
    PARTIAL_TP_RATIO = 0.5

    H4_MA_FAST = 20
    H4_MA_SLOW = 50
    H4_ADX_PERIOD = 14
    H4_ADX_THRESHOLD = 20
    H4_SLOPE_PERIOD = 20          # SMA(50) slope lookback (H4 bars, ~3.5 days)
    TREND_SL_WIDEN = 1.5           # v6.0: widened (was 1.3) for better trend capture
    TREND_SL_TIGHTEN = 0.6         # v6.0: tightened (was 0.7) for faster counter-trend exit

    H1_MA_FAST = 10
    H1_MA_SLOW = 30
    H1_RSI_PERIOD = 14
    H1_BB_PERIOD = 20
    H1_BB_DEV = 2.0

    M15_MA_FAST = 5
    M15_MA_SLOW = 20

    TRADE_START_HOUR = 8
    TRADE_END_HOUR = 22

    # v3.0: USD Correlation
    USE_CORRELATION = True
    CORR_SYMBOL = "USDJPY=X"
    CORR_MA_FAST = 10
    CORR_MA_SLOW = 30
    CORR_THRESHOLD = 0.3

    # v3.0: RSI Divergence
    USE_DIVERGENCE = True
    DIV_LOOKBACK = 30
    DIV_SWING_STRENGTH = 3

    # v3.0: Support/Resistance
    USE_SR_LEVELS = True
    SR_LOOKBACK = 100
    SR_SWING_STRENGTH = 5
    SR_CLUSTER_ATR = 1.0
    SR_PROXIMITY_ATR = 0.5

    # v3.0: Candle Patterns
    USE_CANDLE_PATTERNS = True

    # v3.0: H4 RSI
    H4_RSI_PERIOD = 14
    USE_H4_RSI = True

    # v3.0: Chandelier Exit
    USE_CHANDELIER_EXIT = True
    CHANDELIER_PERIOD = 22
    CHANDELIER_ATR_MULTI = 1.5  # v9.3: 2.0→1.5 (16/16 PASS, 利益ロック高速化)

    # v3.0: Equity Curve Filter
    USE_EQUITY_CURVE = True
    EQUITY_MA_PERIOD = 10
    EQUITY_REDUCE_FACTOR = 0.5

    # v3.0: Adaptive Sizing (Half-Kelly)
    USE_ADAPTIVE_SIZING = True
    KELLY_LOOKBACK = 30
    KELLY_FRACTION = 0.5
    KELLY_MIN_RISK = 0.1
    KELLY_MAX_RISK = 1.5       # v5.0: 好調時のリスク上限引き上げ

    # v4.0 Defense
    USE_NEWS_FILTER = True
    NEWS_BLOCK_MINUTES = 30
    MAX_DYNAMIC_SPREAD = 80
    USE_WEEKEND_CLOSE = True
    FRIDAY_CLOSE_HOUR = 20
    STALE_TRADE_HOURS = 48
    DAILY_MAX_LOSS_PCT = 2.0
    CRASH_ATR_MULTI = 3.0

    # v4.0 Attack
    USE_MOMENTUM_BURST = True
    USE_VOLUME_CLIMAX = False              # v6.0: disabled (34% WR, harmful noise)
    MAX_PYRAMID_POSITIONS = 1  # v6.0: pyramid disabled for quality
    PYRAMID_LOT_DECAY = 0.5
    USE_REVERSAL_MODE = True

    # v5.3: Hard Session Filter (extreme volatility block)
    USE_HARD_SESSION_FILTER = False  # SYNC-FIX: never applied in run() loop; disabled to match MQ5 sync
    BLOCK_FOMC_HOURS = (18, 21)       # FOMC announcement window (UTC)
    BLOCK_NFP_HOURS = (13, 15)        # NFP release window (UTC)
    BLOCK_CPI_HOURS = (13, 15)        # CPI release window (UTC)
    BLOCK_ASIAN_LOW_LIQ = (0, 3)      # Asian ultra-low liquidity (UTC)
    BLOCK_SUNDAY_OPEN_HOURS = 3       # Hours after Sunday market open to block

    # v5.4: Dead Zone Hour Filter -- block structurally unprofitable hours
    USE_DEAD_ZONE_FILTER = True
    DEAD_ZONE_ALL_HOURS = {11, 12}           # Block ALL entries (lunch dead zone)
    DEAD_ZONE_NORMAL_HOURS = {14, 18, 21}    # SYNC-FIX: applied in fast.py; matches MQ5 (block new positions at session transitions)

    # v5.4: Score 11 Skip -- score=11 consistently underperforms across all years
    SKIP_SCORE_11 = True   # SYNC-FIX: applied in fast.py; matches MQ5 (score=11→0)

    # v6.0: Session-Regime Adaptive Threshold (SRAT)
    # Replace fixed MIN_SCORE with per-session base thresholds.
    # Each session has a statistically-derived base threshold reflecting
    # its historical profitability. DD escalation and regime+3 stack on top.
    USE_SRAT = True
    SRAT_THRESHOLDS = {
        # hour: base_min_score
        # London early (8-10): Best session, keep loose
        # v8.1: SRAT[8]=7 (was 11: WFA 11→13/14, London open is highest-quality session)
        8: 7, 9: 9, 10: 9,
        # London mid (11-12): Blocked by dead zone, 13: PF=0.78, raise bar
        11: 99, 12: 99, 13: 12,
        # LN/NY overlap (14-16): 14 transition hour, 15-16 decent
        14: 11, 15: 9, 16: 9,
        # NY session (17-21): Strong but mixed. 18/21 transition hours.
        17: 9, 18: 12, 19: 9, 20: 9, 21: 12,
    }

    # v6.0: Configurable DD escalation thresholds
    # Start tightening early (6%) to prevent DD from deepening.
    # (dd_threshold_pct, min_score_override)
    DD_ESCALATION = [(6, 11), (10, 13), (15, 16), (20, 18)]

    # v6.2: Ranging Regime Adaptation
    # When H4 ADX < threshold (no clear trend), tighten TP to take quick profits
    USE_RANGING_ADAPTATION = True
    RANGING_ADX_THRESHOLD = 20       # v8.0: lowered back to 20 (with RSI_MOM: WFA 13→14/16)
    RANGING_TP_CAP = 5.0             # v8.1: Relaxed cap (3.5→5.0: WFA 12→13/14, allows bigger ranging wins)
    RANGING_SCORE_BOOST = 0          # v6.2: disabled, TP cap alone is better for WFA

    # v7.0: Macro-Trend Filter
    # When H4 shows confirmed directional trend (ADX>=threshold),
    # block counter-trend entries to prevent bleeding in trending markets
    USE_MACRO_TREND_FILTER = True
    MACRO_TREND_ADX_THRESHOLD = 20

    # v8.0: RSI Momentum Confirmation
    # Require H1 RSI to confirm momentum direction before entry
    # BUY: RSI > 50 AND rising (vs N bars ago)
    # SELL: RSI < 50 AND falling (vs N bars ago)
    USE_RSI_MOMENTUM_CONFIRM = True
    RSI_MOMENTUM_LOOKBACK = 3

    # v7.0: Range-Reversion Strategy
    # Activates ONLY when H4 ADX < RANGING_ADX_THRESHOLD (ranging market detected)
    # Uses mean-reversion signals (BB bounce, RSI extreme, S/R) instead of trend-following
    USE_RANGE_REVERSION = False  # SYNC-FIX: get_range_signal() never called in run(); disabled to match MQ5 sync
    RANGE_BB_ENABLED = True
    RANGE_RSI_OVERSOLD = 30
    RANGE_RSI_OVERBOUGHT = 70
    RANGE_TP_ATR_MULTI = 2.0        # Tight TP for range trades (small moves)
    RANGE_SL_ATR_MULTI = 1.0        # Tight SL for quick exit on breakout
    RANGE_RISK_MULTIPLIER = 0.5     # Reduced position size for safety
    RANGE_MIN_CONFIRMATIONS = 2     # Need at least 2 of: BB bounce, RSI extreme, S/R level

    # v8.0: Structural Algorithm Changes

    # v8.0a: Volatility Trend Filter
    # Rising ATR without trend = danger (choppy); Rising ATR with trend = opportunity
    USE_VOL_TREND_FILTER = False
    VOL_TREND_LOOKBACK = 10          # ATR slope lookback (M15 bars)
    VOL_TREND_EXPANSION_BLOCK = True # Block entries when ATR expanding but no H4 trend

    # v8.0b: H4 ADX Slope Filter
    # Not just ADX level, but whether trend is strengthening or weakening
    USE_ADX_SLOPE = False
    ADX_SLOPE_LOOKBACK = 5           # H4 bars for ADX slope calc
    ADX_FALLING_PENALTY = 2          # Raise min_score when ADX is falling (trend weakening)

    # v8.0c: Confirmation Escalation (adaptive entry based on recent performance)
    USE_CONFIRMATION_ESCALATION = False
    CONF_ESC_LOOKBACK = 20           # Number of recent trades to evaluate
    CONF_ESC_WR_THRESHOLD = 0.40     # Win rate below this triggers escalation
    CONF_ESC_SCORE_BOOST = 2         # Extra points required when losing

    # v8.0d: H4+H1 Alignment Filter
    # In weak-trend regime, require both H4 and H1 MA directions to agree
    USE_TF_ALIGNMENT_FILTER = False
    TF_ALIGNMENT_ADX_THRESHOLD = 25  # Apply alignment filter when ADX < this

    # v8.0e: Range Compression Detector
    # Detect narrow price ranges (consolidation) -> higher false breakout risk
    USE_RANGE_COMPRESSION = False
    RANGE_COMP_LOOKBACK = 20         # H1 bars for range measurement
    RANGE_COMP_RATIO = 0.5           # If recent range < ratio * historical range, it's compressed
    RANGE_COMP_HIST_LOOKBACK = 100   # Historical range lookback (H1 bars)
    RANGE_COMP_SCORE_BOOST = 2       # Extra min_score during compression

    # v8.0f: Seasonal/Quarterly Adaptation
    # Tighten entry criteria during historically weak Q1/Q2 periods
    USE_SEASONAL_ADAPT = False
    SEASONAL_WEAK_MONTHS = {1, 2, 3, 4, 5, 6}  # Months with weaker performance
    SEASONAL_SCORE_BOOST = 1         # Extra min_score during weak months
    SEASONAL_TP_TIGHTEN = 0.85       # TP multiplier during weak months (tighter)

    # v8.0g: Losing Streak Cooldown
    # After consecutive losses, pause longer before next entry
    USE_LOSING_STREAK_COOLDOWN = False
    STREAK_THRESHOLD = 3             # After N consecutive losses
    STREAK_EXTRA_COOLDOWN = 16       # Extra M15 bars of cooldown

    # v9.1: Chop Filter -- detect directional whipsaw via H4 bar direction changes
    # When H4 shows frequent direction reversals, market is choppy → raise min_score
    USE_CHOP_FILTER = False  # Tested: hurts more than helps
    CHOP_LOOKBACK = 8              # Look at last N H4 bars
    CHOP_THRESHOLD = 5             # If direction changes >= threshold → choppy
    CHOP_SCORE_BOOST = 3           # Add to dynamic_min_score when choppy

    # v9.2: Adaptive Chandelier in Ranging
    # Tighten chandelier exit when H4 ADX is low to lock profits faster
    USE_ADAPTIVE_CHANDELIER = False  # Tested: helps some quarters, hurts 2023-Q2
    ADAPTIVE_CHAND_ADX_THRESHOLD = 20  # When ADX below this
    ADAPTIVE_CHAND_ATR_MULTI = 1.5     # Use this instead of CHANDELIER_ATR_MULTI(2.0)

    # v9.0: Range Strategy v2 -- BB Mean Reversion + Stochastic
    # Activates INSTEAD of trend-following when H4 ADX < threshold (ranging detected)
    # Uses BB touch + RSI extreme + Stochastic cross + M15 confirmation
    USE_RANGE_STRATEGY_V2 = False
    RANGE_V2_ADX_THRESHOLD = 20     # H4 ADX below this = ranging
    RANGE_V2_BB_PERIOD = 20
    RANGE_V2_BB_DEV = 2.0
    RANGE_V2_RSI_OS = 35            # oversold threshold
    RANGE_V2_RSI_OB = 65            # overbought threshold
    RANGE_V2_USE_STOCH = True
    RANGE_V2_STOCH_PERIOD = 14
    RANGE_V2_STOCH_SMOOTH = 3       # %K and %D smoothing
    RANGE_V2_TP_MODE = 'bb_mid'     # TP at BB middle band
    RANGE_V2_SL_ATR = 1.5           # SL = 1.5 * ATR beyond entry
    RANGE_V2_TIME_EXIT_HOURS = 24   # Close after 24 hours if no TP/SL
    RANGE_V2_PARTIAL_ATR = 0.5      # Partial close at 0.5 * ATR profit
    RANGE_V2_PARTIAL_RATIO = 0.5    # Close 50% at partial target
    RANGE_V2_RISK_MULTI = 0.6       # Position size multiplier for range trades
    RANGE_V2_USE_KELTNER = False    # Alternative: Keltner Channel + RSI
    RANGE_V2_KELTNER_PERIOD = 20
    RANGE_V2_KELTNER_ATR_MULTI = 1.5
    RANGE_V2_KELTNER_RSI_OS = 30
    RANGE_V2_KELTNER_RSI_OB = 70
    RANGE_V2_USE_M15_CONFIRM = True # Require M15 candle direction confirmation
    RANGE_V2_TREND_FILTER = True    # Block counter-trend entries even in ranging

    # v10.0: Realistic Execution Simulation
    # Spread: use actual Spread column from CSV data instead of fixed MAX_SPREAD_POINTS
    USE_REALISTIC_SPREAD = True       # Use per-bar Spread from CSV data
    SLIPPAGE_POINTS = 3               # Additional slippage in points per trade
    COMMISSION_PER_LOT = 7.0          # USD per round-trip lot (ECN Gold typical)
    USE_INTRABAR_SLTP_ORDER = True    # Determine SL/TP hit order using OHLC proximity


# ============================================================
# Indicators
# ============================================================
def calc_sma(s, p):
    return s.rolling(window=p, min_periods=p).mean()

def calc_ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def calc_rsi(series, period):
    """RSI using Wilder's Smoothing (RMA) to match MT5 iRSI()."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1.0/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1.0/period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def _wilder_smooth(series, period):
    """Wilder's smoothing matching MT5 exactly.

    Initialization: SMA of first `period` valid values.
    Then: result[i] = result[i-1] * (period-1)/period + value[i] / period
    """
    values = series.values.astype(float)
    result = np.full_like(values, np.nan)
    alpha = 1.0 / period

    # Find first valid window of `period` consecutive non-NaN values
    count = 0
    start = -1
    for i in range(len(values)):
        if np.isnan(values[i]):
            count = 0
        else:
            count += 1
            if count == period:
                start = i - period + 1
                break

    if start < 0:
        return pd.Series(result, index=series.index)

    # Initialize with SMA of first `period` values (matches MT5)
    result[start + period - 1] = np.mean(values[start:start + period])

    # Wilder's smoothing from there
    for i in range(start + period, len(values)):
        if np.isnan(values[i]):
            result[i] = result[i - 1]
        else:
            result[i] = result[i - 1] * (1 - alpha) + values[i] * alpha

    return pd.Series(result, index=series.index)


def calc_atr(high, low, close, period=14):
    """ATR using Simple Moving Average to match MT5 iATR().

    MT5's iATR() returns SMA of True Range, NOT Wilder's smoothing.
    Verified empirically: SMA matches MT5 output with zero error.
    """
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def calc_adx(high, low, close, period=14):
    """ADX using Wilder's exact step-by-step algorithm to match MT5 iADX().

    Wilder's ADX algorithm:
    1. Compute TR, +DM, -DM per bar
    2. Smooth TR/+DM/-DM with Wilder's method (SMA init, then RMA)
    3. +DI = 100 * Smoothed(+DM) / Smoothed(TR)
    4. -DI = 100 * Smoothed(-DM) / Smoothed(TR)
    5. DX  = 100 * |+DI - -DI| / (+DI + -DI)
    6. ADX = Wilder's smoothing of DX (SMA of first `period` DX values, then RMA)
    """
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder-smooth TR, +DM, -DM independently
    sm_tr = _wilder_smooth(tr, period)
    sm_plus = _wilder_smooth(plus_dm, period)
    sm_minus = _wilder_smooth(minus_dm, period)

    plus_di = 100 * (sm_plus / sm_tr.replace(0, np.nan))
    minus_di = 100 * (sm_minus / sm_tr.replace(0, np.nan))

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)

    # ADX = Wilder's smoothing of DX
    adx = _wilder_smooth(dx, period)

    return adx, plus_di, minus_di

def calc_bb(series, period, deviation):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std(ddof=0)  # MT5 uses population std
    return sma + deviation * std, sma, sma - deviation * std

def calc_stochastic(high, low, close, k_period=14, k_smooth=3, d_smooth=3):
    """Calculate Stochastic Oscillator %K and %D.

    %K = SMA(k_smooth) of raw %K
    %D = SMA(d_smooth) of %K
    """
    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()
    raw_k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, 1e-10)
    k = raw_k.rolling(window=k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(window=d_smooth, min_periods=d_smooth).mean()
    return k, d

def calc_keltner(close, high, low, ema_period=20, atr_multi=1.5, atr_period=14):
    """Calculate Keltner Channel: EMA ± ATR multiplier.

    Returns: (upper, middle, lower)
    """
    middle = calc_ema(close, ema_period)
    atr = calc_atr(high, low, close, atr_period)
    upper = middle + atr_multi * atr
    lower = middle - atr_multi * atr
    return upper, middle, lower

def calc_channel_signal(close_series, lookback=40):
    if len(close_series) < lookback:
        return 0
    y = close_series[-(lookback+1):-1].values
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
# v3.0 Indicator Functions
# ============================================================
def get_correlation_signal(usdjpy_df, current_time, cfg):
    """USD correlation signal from USDJPY data."""
    mask = usdjpy_df.index <= current_time
    if mask.sum() < max(cfg.CORR_MA_SLOW, 14) + 6:
        return 0

    data = usdjpy_df[mask]
    close = data["Close"]

    fast_ema = calc_ema(close, cfg.CORR_MA_FAST)
    slow_ema = calc_ema(close, cfg.CORR_MA_SLOW)

    atr = calc_atr(data["High"], data["Low"], data["Close"], 14)

    fast_current = fast_ema.iloc[-1]
    slow_current = slow_ema.iloc[-1]
    current_atr = atr.iloc[-1]

    if pd.isna(fast_current) or pd.isna(slow_current) or pd.isna(current_atr) or current_atr <= 0:
        return 0

    # Speed: how fast the fast EMA moved over last 5 bars
    if len(fast_ema) < 6:
        return 0
    fast_5bars_ago = fast_ema.iloc[-6]
    if pd.isna(fast_5bars_ago):
        return 0

    move_speed = (fast_current - fast_5bars_ago) / current_atr

    # USD weak (fast < slow, speed negative) -> gold buy
    if fast_current < slow_current and move_speed < -cfg.CORR_THRESHOLD:
        return 1
    # USD strong (fast > slow, speed positive) -> gold sell
    if fast_current > slow_current and move_speed > cfg.CORR_THRESHOLD:
        return -1
    return 0


def get_divergence(h1_closes, h1_rsi, lookback=30, swing_strength=3):
    """Detect RSI divergence (classic and hidden)."""
    if len(h1_closes) < lookback or len(h1_rsi) < lookback:
        return 0

    closes = h1_closes.values[-lookback:]
    rsi = h1_rsi.values[-lookback:]

    # Check for NaN
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
        # Classic bullish: price lower low, RSI higher low
        if closes[i2] < closes[i1] and rsi[i2] > rsi[i1]:
            return 1
        # Hidden bullish: price higher low, RSI lower low
        if closes[i2] > closes[i1] and rsi[i2] < rsi[i1]:
            return 1

    # Bearish divergence (swing highs)
    if len(swing_highs) >= 2:
        i1, i2 = swing_highs[-2], swing_highs[-1]
        # Classic bearish: price higher high, RSI lower high
        if closes[i2] > closes[i1] and rsi[i2] < rsi[i1]:
            return -1
        # Hidden bearish: price lower high, RSI higher high
        if closes[i2] < closes[i1] and rsi[i2] > rsi[i1]:
            return -1

    return 0


def get_sr_signal(h1_df, current_time, current_price, current_atr, cfg):
    """Support/Resistance level proximity signal."""
    mask = h1_df.index <= current_time
    if mask.sum() < cfg.SR_LOOKBACK:
        return 0

    data = h1_df[mask].iloc[-cfg.SR_LOOKBACK:]
    highs = data["High"].values
    lows = data["Low"].values
    strength = cfg.SR_SWING_STRENGTH

    levels = []

    # Find swing highs (resistance candidates)
    for i in range(strength, len(highs) - strength):
        left = highs[i - strength:i]
        right = highs[i + 1:i + 1 + strength]
        if len(left) > 0 and len(right) > 0 and highs[i] > left.max() and highs[i] > right.max():
            levels.append(highs[i])

    # Find swing lows (support candidates)
    for i in range(strength, len(lows) - strength):
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
    supports = [l for l in clustered if l < current_price]
    resistances = [l for l in clustered if l > current_price]

    proximity = cfg.SR_PROXIMITY_ATR * current_atr

    # Near support -> buy signal
    if supports:
        nearest_support = max(supports)
        if current_price - nearest_support <= proximity:
            return 1

    # Near resistance -> sell signal
    if resistances:
        nearest_resistance = min(resistances)
        if nearest_resistance - current_price <= proximity:
            return -1

    return 0


def get_candle_pattern(h1_df, current_time):
    """Detect common candlestick patterns from last 3 H1 bars."""
    mask = h1_df.index <= current_time
    if mask.sum() < 3:
        return 0

    bars = h1_df[mask].iloc[-3:]
    c0, c1, c2 = bars.iloc[0], bars.iloc[1], bars.iloc[2]

    o2, h2, l2, cl2 = c2["Open"], c2["High"], c2["Low"], c2["Close"]
    o1, h1_v, l1, cl1 = c1["Open"], c1["High"], c1["Low"], c1["Close"]
    o0, h0, l0, cl0 = c0["Open"], c0["High"], c0["Low"], c0["Close"]

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

    # Bullish Engulfing: prev bearish, curr bullish, curr body engulfs prev body
    if body1 < 0 and body2 > 0 and o2 <= cl1 and cl2 >= o1:
        return 1

    # Bearish Engulfing: prev bullish, curr bearish, curr body engulfs prev body
    if body1 > 0 and body2 < 0 and o2 >= cl1 and cl2 <= o1:
        return -1

    # Hammer: small body at top, long lower shadow, bullish
    lower_shadow2 = min(o2, cl2) - l2
    upper_shadow2 = h2 - max(o2, cl2)
    if abs_body2 > 0 and lower_shadow2 >= abs_body2 * 2 and upper_shadow2 <= abs_body2 * 0.5:
        return 1

    # Shooting Star: small body at bottom, long upper shadow, bearish
    if abs_body2 > 0 and upper_shadow2 >= abs_body2 * 2 and lower_shadow2 <= abs_body2 * 0.5:
        return -1

    # Morning Star: bearish candle, small body, bullish candle
    if body0 < 0 and abs_body1 < abs(body0) * 0.3 and body2 > 0 and cl2 > (o0 + cl0) / 2:
        return 1

    # Evening Star: bullish candle, small body, bearish candle
    if body0 > 0 and abs_body1 < abs(body0) * 0.3 and body2 < 0 and cl2 < (o0 + cl0) / 2:
        return -1

    return 0


def get_h4_rsi_alignment(h4_rsi_val, h1_rsi_val):
    """Check H4 RSI alignment with H1 RSI."""
    if pd.isna(h4_rsi_val) or pd.isna(h1_rsi_val):
        return 0

    # H4 RSI 50-75 + H1 RSI < 70 -> bullish alignment
    if 50 <= h4_rsi_val <= 75 and h1_rsi_val < 70:
        return 1
    # H4 RSI 25-50 + H1 RSI > 30 -> bearish alignment
    if 25 <= h4_rsi_val <= 50 and h1_rsi_val > 30:
        return -1
    return 0


# ============================================================
# Data fetching (H4, H1, M15, USDJPY)
# ============================================================
def fetch_gold_data(months=6):
    print(f"[DL] Gold (GC=F) fetching ({months} months)...")
    end = datetime.now()
    start = end - timedelta(days=months * 30 + 90)

    t = yf.Ticker("GC=F")

    h1_raw = t.history(start=start, end=end, interval="1h")
    if h1_raw.empty:
        print("[ERR] H1 fetch failed, falling back to daily")
        daily = t.history(start=start, end=end, interval="1d")
        if daily.empty:
            print("[ERR] Data fetch failed")
            return None, None, None, None
        h4, h1, m15 = _generate_from_daily(daily, months)
        return h4, h1, m15, None

    print(f"   H1: {len(h1_raw)} bars ({h1_raw.index[0]} ~ {h1_raw.index[-1]})")

    # Generate H4
    h4_df = h1_raw.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna()

    # Generate M15 from H1
    m15_list = []
    for idx, row in h1_raw.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        vol = row.get("Volume", 0)
        for j in range(4):
            frac = j / 4
            frac_next = (j + 1) / 4
            seg_o = o + (c - o) * frac
            seg_c = o + (c - o) * frac_next
            seg_h = max(seg_o, seg_c) + (h - max(o, c)) * (1 - abs(frac - 0.5) * 2) * 0.5
            seg_l = min(seg_o, seg_c) - (min(o, c) - l) * (1 - abs(frac - 0.5) * 2) * 0.5
            ts = idx + timedelta(minutes=j * 15)
            m15_list.append({"Open": seg_o, "High": seg_h, "Low": seg_l, "Close": seg_c,
                             "Volume": vol / 4, "time": ts})

    m15_df = pd.DataFrame(m15_list).set_index("time")

    cutoff = end - timedelta(days=months * 30)
    cutoff_ts = pd.Timestamp(cutoff, tz=m15_df.index.tz) if m15_df.index.tz else pd.Timestamp(cutoff)
    m15_df = m15_df[m15_df.index >= cutoff_ts]

    print(f"   H4: {len(h4_df)} bars / M15: {len(m15_df)} bars")
    print(f"   Backtest period: {m15_df.index[0].date()} ~ {m15_df.index[-1].date()}")

    # v3.0: Fetch USDJPY data
    usdjpy_df = None
    try:
        print("[DL] USDJPY fetching for correlation...")
        usdjpy_t = yf.Ticker("USDJPY=X")
        usdjpy_df = usdjpy_t.history(start=start, end=end, interval="1h")
        if usdjpy_df.empty:
            print("[WARN] USDJPY fetch returned empty, correlation disabled")
            usdjpy_df = None
        else:
            print(f"   USDJPY: {len(usdjpy_df)} bars")
    except Exception as e:
        print(f"[WARN] USDJPY fetch failed: {e}, correlation disabled")
        usdjpy_df = None

    return h4_df, h1_raw, m15_df, usdjpy_df


def _generate_from_daily(daily, months):
    """Generate H4/H1/M15 from daily (fallback)"""
    m15_list = []
    for idx, row in daily.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        for j, (so, sh, sl, sc) in enumerate([
            (o, max(o, (o+h)/2), min(o, o-(h-o)*0.2 if h > o else o), (o+h)/2),
            ((o+h)/2, h, (h+l)/2, (h+c)/2),
            ((h+c)/2, max((h+c)/2, (h+c)/2*1.001), l, (l+c)/2),
            ((l+c)/2, max(c, (l+c)/2), min(c, (l+c)/2), c),
        ]):
            ts = idx + timedelta(hours=j * 4)
            m15_list.append({"Open": so, "High": sh, "Low": sl, "Close": sc, "time": ts})

    m15_df = pd.DataFrame(m15_list).set_index("time")
    h1_df = daily.copy()
    h4_df = daily.resample("W").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna()

    end = datetime.now()
    cutoff = end - timedelta(days=months * 30)
    m15_df = m15_df[m15_df.index >= pd.Timestamp(cutoff, tz=m15_df.index.tz) if m15_df.index.tz else pd.Timestamp(cutoff)]

    return h4_df, h1_df, m15_df


# ============================================================
# Backtest Engine (Gold v4.0)
# ============================================================
class GoldBacktester:
    def __init__(self, cfg):
        self.cfg = cfg
        self.balance = cfg.INITIAL_BALANCE
        self.equity_curve = []
        self.trades = []
        self.open_positions = []
        self.peak_balance = cfg.INITIAL_BALANCE
        self.cooldown_until = 0
        # v3.0 additions
        self.recent_trade_pnls = []
        self.component_stats = {i: {"wins": 0, "total": 0} for i in range(15)}  # v4.0: 15 components
        # v4.0 tracking
        self.daily_pnl = 0.0
        self.current_day = None
        self.circuit_breaker = False
        self.news_blocks = 0
        self.crash_skips = 0
        self.weekend_closes = 0
        self.spread_blocks = 0
        # v7.0: Range-reversion tracking
        self.range_trades = 0
        self.range_wins = 0
        # v8.0g: Losing streak tracking
        self.consecutive_losses = 0

    # ---- v7.0 Range-Reversion Method ----

    def get_range_signal(self, h1_curr_close, h1_prev_close, h1_curr_rsi,
                         h1_curr_bb_upper, h1_curr_bb_lower, sr_signal, cfg):
        """Mean-reversion signal for ranging markets.

        Uses BB bounce, RSI extremes, and S/R proximity.
        Returns: (direction, confirmations) where direction is 'BUY'/'SELL'/None
        """
        if not getattr(cfg, 'USE_RANGE_REVERSION', False):
            return None, 0

        buy_confirms = 0
        sell_confirms = 0

        # 1. Bollinger Band bounce
        if getattr(cfg, 'RANGE_BB_ENABLED', True):
            if not (np.isnan(h1_curr_bb_upper) or np.isnan(h1_curr_bb_lower)):
                bw = h1_curr_bb_upper - h1_curr_bb_lower
                if bw > 0:
                    bp = (h1_curr_close - h1_curr_bb_lower) / bw
                    # Price near lower band AND turning up -> BUY
                    if bp < 0.15 and h1_curr_close > h1_prev_close:
                        buy_confirms += 1
                    # Price near upper band AND turning down -> SELL
                    elif bp > 0.85 and h1_curr_close < h1_prev_close:
                        sell_confirms += 1

        # 2. RSI oversold/overbought reversal
        rsi_os = getattr(cfg, 'RANGE_RSI_OVERSOLD', 30)
        rsi_ob = getattr(cfg, 'RANGE_RSI_OVERBOUGHT', 70)
        if not np.isnan(h1_curr_rsi):
            if h1_curr_rsi < rsi_os:
                buy_confirms += 1
            elif h1_curr_rsi > rsi_ob:
                sell_confirms += 1

        # 3. S/R proximity (already computed: +1=near support, -1=near resistance)
        if sr_signal == 1:
            buy_confirms += 1
        elif sr_signal == -1:
            sell_confirms += 1

        min_conf = getattr(cfg, 'RANGE_MIN_CONFIRMATIONS', 2)

        if buy_confirms >= min_conf and buy_confirms > sell_confirms:
            return 'BUY', buy_confirms
        elif sell_confirms >= min_conf and sell_confirms > buy_confirms:
            return 'SELL', sell_confirms
        return None, 0

    # ---- v4.0 Defense Methods ----

    def simulate_news_filter(self, timestamp):
        """Simulate news filter - block trading around known high-impact times"""
        if not self.cfg.USE_NEWS_FILTER:
            return False
        hour = timestamp.hour if hasattr(timestamp, 'hour') else 12
        weekday = timestamp.weekday() if hasattr(timestamp, 'weekday') else 0
        day = timestamp.day if hasattr(timestamp, 'day') else 15
        # NFP: First Friday of month, 13:30 UTC
        if weekday == 4 and day <= 7 and 13 <= hour <= 14:
            return True
        # FOMC: Wednesday, 19:00 UTC
        if weekday == 2 and 18 <= hour <= 20:
            return True
        # ECB: Thursday, 12:45 UTC
        if weekday == 3 and 12 <= hour <= 13:
            return True
        # CPI: Around 10th-15th of month, 13:30 UTC
        if 10 <= day <= 15 and hour == 13:
            return True
        return False

    def check_dynamic_spread(self, current_atr, atr_avg):
        """Simulate spread check - widen during volatile periods"""
        if atr_avg > 0 and current_atr / atr_avg > 2.0:
            return False  # Spread likely too wide
        return True

    def check_weekend(self, timestamp):
        """Check if it's Friday close time"""
        if not self.cfg.USE_WEEKEND_CLOSE:
            return False
        weekday = timestamp.weekday() if hasattr(timestamp, 'weekday') else 0
        hour = timestamp.hour if hasattr(timestamp, 'hour') else 12
        return weekday == 4 and hour >= self.cfg.FRIDAY_CLOSE_HOUR

    def get_advanced_regime(self, current_atr, atr_avg):
        """Return regime: 0=Crash, 1=Ranging, 2=Trending, 3=Volatile"""
        if atr_avg <= 0:
            return 2
        ratio = current_atr / atr_avg
        if ratio >= self.cfg.CRASH_ATR_MULTI:
            return 0  # Crash
        if ratio <= self.cfg.VOL_REGIME_LOW:
            return 1  # Ranging
        if ratio >= self.cfg.VOL_REGIME_HIGH:
            return 3  # Volatile
        return 2  # Trending

    def check_stale_trade(self, pos, bar_idx):
        """Check if trade has been open too long"""
        if self.cfg.STALE_TRADE_HOURS <= 0:
            return False
        bars_elapsed = bar_idx - pos.get('entry_bar', bar_idx)
        hours_elapsed = bars_elapsed * 0.25  # M15 bars = 0.25 hours
        return hours_elapsed >= self.cfg.STALE_TRADE_HOURS

    def check_daily_circuit(self):
        """Check if daily loss limit hit"""
        max_loss = self.balance * self.cfg.DAILY_MAX_LOSS_PCT / 100.0
        return self.daily_pnl <= -max_loss

    # ---- v4.0 Attack Methods ----

    def get_momentum_burst(self, h4_row, h1_curr, m15_curr, m15_prev):
        """Check if all timeframes are aligned for momentum burst (+3 points)"""
        if not self.cfg.USE_MOMENTUM_BURST:
            return 0
        h4_bull = pd.notna(h4_row.get("ma_fast")) and h4_row["ma_fast"] > h4_row["ma_slow"]
        h4_bear = pd.notna(h4_row.get("ma_fast")) and h4_row["ma_fast"] < h4_row["ma_slow"]
        h1_bull = pd.notna(h1_curr.get("ma_fast")) and h1_curr["ma_fast"] > h1_curr["ma_slow"]
        h1_bear = pd.notna(h1_curr.get("ma_fast")) and h1_curr["ma_fast"] < h1_curr["ma_slow"]
        m15_bull = pd.notna(m15_curr.get("ma_fast")) and m15_curr["ma_fast"] > m15_curr["ma_slow"]
        m15_bear = pd.notna(m15_curr.get("ma_fast")) and m15_curr["ma_fast"] < m15_curr["ma_slow"]

        if h4_bull and h1_bull and m15_bull:
            return 3
        if h4_bear and h1_bear and m15_bear:
            return -3
        return 0

    def get_volume_climax(self, m15_df, i):
        """Detect volume climax (2x average)"""
        if not self.cfg.USE_VOLUME_CLIMAX:
            return 0
        if i < 21 or 'Volume' not in m15_df.columns:
            return 0
        current_vol = m15_df['Volume'].iloc[i]
        avg_vol = m15_df['Volume'].iloc[i-20:i].mean()
        if avg_vol > 0 and current_vol > avg_vol * 2.0:
            row = m15_df.iloc[i]
            if row['Close'] > row['Open']:
                return 2   # Bullish climax
            elif row['Close'] < row['Open']:
                return -2  # Bearish climax
        return 0

    def check_reversal(self, h1_df, h1_mask, ct, cc, current_atr, h1_curr, cfg):
        """Check for reversal setup: RSI extreme + divergence + S/R + candle pattern"""
        if not cfg.USE_REVERSAL_MODE:
            return 0
        rsi = h1_curr["rsi"] if pd.notna(h1_curr.get("rsi")) else 50

        h1_closes_series = h1_df[h1_mask]["Close"]
        h1_rsi_series = h1_df[h1_mask]["rsi"]
        div_signal = get_divergence(h1_closes_series, h1_rsi_series, cfg.DIV_LOOKBACK, cfg.DIV_SWING_STRENGTH)
        sr_signal = get_sr_signal(h1_df, ct, cc, current_atr, cfg)
        candle_signal = get_candle_pattern(h1_df, ct)

        # Bullish reversal: RSI oversold + bullish divergence + support + bullish candle
        if rsi < 25 and div_signal > 0 and sr_signal > 0 and candle_signal > 0:
            return 1
        # Bearish reversal: RSI overbought + bearish divergence + resistance + bearish candle
        if rsi > 75 and div_signal < 0 and sr_signal < 0 and candle_signal < 0:
            return -1
        return 0

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None):
        cfg = self.cfg

        # Store USDJPY data
        self.usdjpy_df = usdjpy_df

        # Indicator calculation
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

        m15_df["ma_fast"] = calc_ema(m15_df["Close"], cfg.M15_MA_FAST)
        m15_df["ma_slow"] = calc_ema(m15_df["Close"], cfg.M15_MA_SLOW)

        # v2.0: M15 ATR calculation
        m15_df["atr"] = calc_atr(m15_df["High"], m15_df["Low"], m15_df["Close"], cfg.ATR_PERIOD)
        m15_df["atr_avg"] = m15_df["atr"].rolling(window=cfg.VOL_REGIME_PERIOD).mean()

        # v3.0: Pre-compute USDJPY indicators for performance
        if self.usdjpy_df is not None:
            self.usdjpy_df = self.usdjpy_df.copy()
            self.usdjpy_df["ema_fast"] = calc_ema(self.usdjpy_df["Close"], cfg.CORR_MA_FAST)
            self.usdjpy_df["ema_slow"] = calc_ema(self.usdjpy_df["Close"], cfg.CORR_MA_SLOW)
            self.usdjpy_df["atr"] = calc_atr(
                self.usdjpy_df["High"], self.usdjpy_df["Low"], self.usdjpy_df["Close"], 14)

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
        print(f"   v5.3: HardSessionFilter={cfg.USE_HARD_SESSION_FILTER}")
        print(f"   v5.4: DeadZoneFilter={cfg.USE_DEAD_ZONE_FILTER} SkipScore11={cfg.SKIP_SCORE_11}")
        print(f"   v6.0: SRAT={cfg.USE_SRAT} VolClimax={cfg.USE_VOLUME_CLIMAX} DD_ESC={cfg.DD_ESCALATION}")

        for i in range(100, total_bars):
            ct = m15_df.index[i]
            cc = m15_df["Close"].iloc[i]
            ch = m15_df["High"].iloc[i]
            cl = m15_df["Low"].iloc[i]
            co = m15_df["Open"].iloc[i]
            # v11.0: Use next bar's Open for entry to avoid look-ahead bias
            # Signal generated at bar[i] Close -> entry at bar[i+1] Open (matches MT5 live)
            next_bar_open = m15_df["Open"].iloc[i + 1] if i + 1 < total_bars else cc

            # v10.0: Get bar spread from CSV data
            _bar_spread = m15_df["Spread"].iloc[i] if "Spread" in m15_df.columns else None

            # v4.0: Daily circuit breaker reset
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

            if self.balance > self.peak_balance:
                self.peak_balance = self.balance
            current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0

            hour = ct.hour if hasattr(ct, "hour") else 12

            # v4.0: Weekend close - close all positions
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

            # v2.0: ATR and volatility regime check
            current_atr = m15_df["atr"].iloc[i]
            current_atr_avg = m15_df["atr_avg"].iloc[i]
            if pd.isna(current_atr) or pd.isna(current_atr_avg) or current_atr_avg <= 0:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v4.0: Dynamic spread check
            if not self.check_dynamic_spread(current_atr, current_atr_avg):
                self.spread_blocks += 1
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # v4.0: Advanced 4-state regime
            regime = self.get_advanced_regime(current_atr, current_atr_avg)
            if regime == 0:  # Crash - no new entries, only manage
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
            # Ensure minimum RR 1:1.5
            if dynamic_tp_points < dynamic_sl_points * 1.5:
                dynamic_tp_points = dynamic_sl_points * 1.5

            # H4 data lookup
            h4_mask = h4_df.index <= ct
            if h4_mask.sum() < 2:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h4_row = h4_df[h4_mask].iloc[-1]

            # v6.2: Ranging Regime Adaptation — detect if H4 shows no trend
            is_ranging = False
            if cfg.USE_RANGING_ADAPTATION:
                h4_adx_val = h4_row.get("adx") if pd.notna(h4_row.get("adx")) else 25
                if h4_adx_val < cfg.RANGING_ADX_THRESHOLD:
                    is_ranging = True

            # H1 data lookup
            h1_mask = h1_df.index <= ct
            if h1_mask.sum() < 4:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h1_curr = h1_df[h1_mask].iloc[-1]
            h1_prev = h1_df[h1_mask].iloc[-2]

            m15_curr = m15_df.iloc[i]
            m15_prev = m15_df.iloc[i - 1]

            # ---- Scoring (Gold EA v4.0: max 27 points) ----
            buy_score = 0
            sell_score = 0
            component_mask = [0] * 15  # v4.0: 15 components

            # 1. H4 Trend (3 pts) — original MA crossover + DI alignment
            if pd.notna(h4_row.get("adx")) and h4_row["adx"] >= cfg.H4_ADX_THRESHOLD:
                if h4_row["ma_fast"] > h4_row["ma_slow"] and h4_row["plus_di"] > h4_row["minus_di"]:
                    buy_score += 3
                    component_mask[0] = 1
                elif h4_row["ma_fast"] < h4_row["ma_slow"] and h4_row["minus_di"] > h4_row["plus_di"]:
                    sell_score += 3
                    component_mask[0] = -1

            # 1b. v5.2: Macro trend direction from MA50 slope
            macro_trend_dir = 0  # +1=up, -1=down
            if pd.notna(h4_row.get("ma_slow_slope")):
                slope = h4_row["ma_slow_slope"]
                if slope > 0:
                    macro_trend_dir = 1
                elif slope < 0:
                    macro_trend_dir = -1

            # 2. H1 MA direction (2 pts)
            if pd.notna(h1_curr["ma_fast"]) and pd.notna(h1_curr["ma_slow"]):
                if h1_curr["ma_fast"] > h1_curr["ma_slow"]:
                    buy_score += 2
                    component_mask[1] = 1
                elif h1_curr["ma_fast"] < h1_curr["ma_slow"]:
                    sell_score += 2
                    component_mask[1] = -1

            # 3. H1 RSI (1 pt) -- exclusive ranges
            if pd.notna(h1_curr["rsi"]):
                rsi_val = h1_curr["rsi"]
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
            if pd.notna(h1_curr.get("bb_upper")) and pd.notna(h1_curr.get("bb_lower")):
                bw = h1_curr["bb_upper"] - h1_curr["bb_lower"]
                if bw > 0:
                    bp = (h1_curr["Close"] - h1_curr["bb_lower"]) / bw
                    if bp < 0.2 and h1_curr["Close"] > h1_prev["Close"]:
                        buy_score += 1
                        component_mask[3] = 1
                    if bp > 0.8 and h1_curr["Close"] < h1_prev["Close"]:
                        sell_score += 1
                        component_mask[3] = -1

            # 5. M15 MA cross (2 pts) -- cross just occurred
            if pd.notna(m15_curr["ma_fast"]) and pd.notna(m15_curr["ma_slow"]):
                fast_above = m15_curr["ma_fast"] > m15_curr["ma_slow"]
                prev_fast_above = m15_prev["ma_fast"] > m15_prev["ma_slow"] if pd.notna(m15_prev["ma_fast"]) else None
                if fast_above and prev_fast_above is False:
                    buy_score += 2
                    component_mask[4] = 1
                elif not fast_above and prev_fast_above is True:
                    sell_score += 2
                    component_mask[4] = -1

            # 6. Channel regression (1 pt) -- confirmed bars
            h1_closes = h1_df[h1_mask]["Close"]
            cs = calc_channel_signal(h1_closes, 40)
            if cs == 1:
                buy_score += 1
                component_mask[5] = 1
            elif cs == -1:
                sell_score += 1
                component_mask[5] = -1

            # 7. v2.0: Momentum scoring (+1 pt)
            if cfg.USE_MOMENTUM and i >= 2:
                close_now = m15_df["Close"].iloc[i]
                close_2ago = m15_df["Close"].iloc[i - 2]
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

            # 9. v3.0: USD Correlation (+2)
            if cfg.USE_CORRELATION and self.usdjpy_df is not None:
                corr = get_correlation_signal(self.usdjpy_df, ct, cfg)
                if corr == 1:
                    buy_score += 2
                    component_mask[8] = 1
                elif corr == -1:
                    sell_score += 2
                    component_mask[8] = -1

            # 10. v3.0: RSI Divergence (+2)
            if cfg.USE_DIVERGENCE:
                h1_closes_series = h1_df[h1_mask]["Close"]
                h1_rsi_series = h1_df[h1_mask]["rsi"]
                div = get_divergence(h1_closes_series, h1_rsi_series, cfg.DIV_LOOKBACK, cfg.DIV_SWING_STRENGTH)
                if div == 1:
                    buy_score += 2
                    component_mask[9] = 1
                elif div == -1:
                    sell_score += 2
                    component_mask[9] = -1

            # 11. v3.0: S/R Level (+1/-1)
            if cfg.USE_SR_LEVELS:
                sr = get_sr_signal(h1_df, ct, cc, current_atr, cfg)
                if sr == 1:
                    buy_score += 1
                    sell_score -= 1
                    component_mask[10] = 1
                elif sr == -1:
                    sell_score += 1
                    buy_score -= 1
                    component_mask[10] = -1

            # 12. v3.0: Candle Pattern (+1)
            if cfg.USE_CANDLE_PATTERNS:
                cdl = get_candle_pattern(h1_df, ct)
                if cdl == 1:
                    buy_score += 1
                    component_mask[11] = 1
                elif cdl == -1:
                    sell_score += 1
                    component_mask[11] = -1

            # 13. v3.0: H4 RSI Alignment (+1)
            if cfg.USE_H4_RSI and pd.notna(h4_row.get("rsi")):
                h4r = get_h4_rsi_alignment(h4_row["rsi"], h1_curr["rsi"] if pd.notna(h1_curr["rsi"]) else 50)
                if h4r == 1:
                    buy_score += 1
                    component_mask[12] = 1
                elif h4r == -1:
                    sell_score += 1
                    component_mask[12] = -1

            # 14. v4.0: Momentum Burst (+3)
            burst = self.get_momentum_burst(h4_row, h1_curr, m15_curr, m15_prev)
            if burst > 0:
                buy_score += burst
                component_mask[13] = 1
            elif burst < 0:
                sell_score += abs(burst)
                component_mask[13] = -1

            # 15. v4.0: Volume Climax (+2)
            climax = self.get_volume_climax(m15_df, i)
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
            if cfg.USE_SRAT and hour in cfg.SRAT_THRESHOLDS:
                dynamic_min_score = cfg.SRAT_THRESHOLDS[hour]
            else:
                dynamic_min_score = cfg.MIN_SCORE  # 9
            # DD escalation stacks on top of session base
            dd_esc = getattr(cfg, 'DD_ESCALATION', [(10, 12), (15, 15), (20, 18)])
            for dd_thresh, dd_score in sorted(dd_esc, reverse=True):
                if current_dd >= dd_thresh:
                    dynamic_min_score = max(dynamic_min_score, dd_score)
                    break
            if regime == 1:  # Ranging
                dynamic_min_score += 3
            # v6.2: Additional boost when H4 ADX confirms no trend
            if is_ranging and cfg.USE_RANGING_ADAPTATION:
                dynamic_min_score = max(dynamic_min_score, dynamic_min_score + cfg.RANGING_SCORE_BOOST)

            # ---- v3.0: Equity Curve Filter ----
            lot_multiplier = 1.0
            if cfg.USE_EQUITY_CURVE and len(self.recent_trade_pnls) >= cfg.EQUITY_MA_PERIOD:
                recent = self.recent_trade_pnls[-cfg.EQUITY_MA_PERIOD:]
                if np.mean(recent) < 0:
                    lot_multiplier = cfg.EQUITY_REDUCE_FACTOR

            # v4.0: Momentum burst TP multiplier
            tp_multi = 1.5 if abs(burst) == 3 else 1.0
            adjusted_tp_points = dynamic_tp_points * tp_multi

            # v6.2: Ranging Regime Adaptation -- cap TP after burst multiplier
            if is_ranging and cfg.USE_RANGING_ADAPTATION:
                ranging_tp = atr_points * cfg.RANGING_TP_CAP
                if ranging_tp < adjusted_tp_points:
                    adjusted_tp_points = ranging_tp
                if adjusted_tp_points < dynamic_sl_points * 1.5:
                    adjusted_tp_points = dynamic_sl_points * 1.5

            # ---- v4.0: Pyramiding support ----
            pos_count = len(self.open_positions)
            can_enter = pos_count < cfg.MAX_PYRAMID_POSITIONS
            is_pyramid = pos_count > 0
            pyramid_ok = True

            if is_pyramid:
                # Check if existing positions are profitable
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

            if can_enter and (not is_pyramid or pyramid_ok):
                pyramid_lot_multi = 1.0
                if is_pyramid:
                    pyramid_lot_multi = cfg.PYRAMID_LOT_DECAY ** pos_count
                    entry_type = "pyramid"

                # v5.2: Trend-aligned SL adjustment
                # With macro trend: wider SL (survive pullbacks)
                # Against macro trend: tighter SL (cut losses faster)
                adj_sl = dynamic_sl_points
                adj_tp = adjusted_tp_points
                if macro_trend_dir != 0:
                    if (buy_score > sell_score and macro_trend_dir == 1) or \
                       (sell_score > buy_score and macro_trend_dir == -1):
                        adj_sl = min(dynamic_sl_points * cfg.TREND_SL_WIDEN, cfg.MAX_SL_POINTS)
                    elif (buy_score > sell_score and macro_trend_dir == -1) or \
                         (sell_score > buy_score and macro_trend_dir == 1):
                        adj_sl = max(dynamic_sl_points * cfg.TREND_SL_TIGHTEN, cfg.MIN_SL_POINTS)

                # v8.0: RSI Momentum Confirmation
                # Block entries where H1 RSI doesn't confirm momentum direction
                if getattr(cfg, 'USE_RSI_MOMENTUM_CONFIRM', False):
                    rsi_val = h1_curr["rsi"] if pd.notna(h1_curr.get("rsi")) else 50
                    rsi_lb = getattr(cfg, 'RSI_MOMENTUM_LOOKBACK', 3)
                    h1_data = h1_df[h1_mask]
                    rsi_past = 50
                    if len(h1_data) > rsi_lb and pd.notna(h1_data["rsi"].iloc[-1 - rsi_lb]):
                        rsi_past = h1_data["rsi"].iloc[-1 - rsi_lb]
                    if buy_score > sell_score:
                        if not (rsi_val > 50 and rsi_val > rsi_past):
                            buy_score = 0
                    elif sell_score > buy_score:
                        if not (rsi_val < 50 and rsi_val < rsi_past):
                            sell_score = 0

                # v7.0: Macro-Trend Filter - block counter-trend entries
                effective_buy = buy_score
                effective_sell = sell_score
                if getattr(cfg, 'USE_MACRO_TREND_FILTER', False):
                    h4_adx_for_filter = h4_row.get("adx") if pd.notna(h4_row.get("adx")) else 0
                    if h4_adx_for_filter >= cfg.MACRO_TREND_ADX_THRESHOLD:
                        h4_bull = pd.notna(h4_row.get("ma_fast")) and h4_row["ma_fast"] > h4_row["ma_slow"]
                        h4_bear = pd.notna(h4_row.get("ma_fast")) and h4_row["ma_fast"] < h4_row["ma_slow"]
                        if h4_bull:
                            effective_sell = 0  # Block SELL in H4 uptrend
                        elif h4_bear:
                            effective_buy = 0   # Block BUY in H4 downtrend

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

            # v4.0: Reversal mode - only when no normal entry and no open positions
            if not entered and pos_count == 0:
                reversal = self.check_reversal(h1_df, h1_mask, ct, cc, current_atr, h1_curr, cfg)
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
        fc = m15_df["Close"].iloc[-1]
        _final_spread = m15_df["Spread"].iloc[-1] if "Spread" in m15_df.columns else None
        for pos in list(self.open_positions):
            self._close_position(pos, fc, m15_df.index[-1], "EndOfPeriod", total_bars - 1,
                                 bar_spread_points=_final_spread)

        print("[OK] Backtest complete")

    def _calc_lot(self, dd_pct, sl_points):
        cfg = self.cfg
        risk_pct = cfg.RISK_PERCENT

        # v3.0: Half-Kelly adaptive sizing
        if cfg.USE_ADAPTIVE_SIZING and len(self.recent_trade_pnls) >= cfg.KELLY_LOOKBACK:
            recent = self.recent_trade_pnls[-cfg.KELLY_LOOKBACK:]
            wins = [p for p in recent if p > 0]
            losses = [abs(p) for p in recent if p <= 0]
            if wins and losses:
                win_rate = len(wins) / len(recent)
                payoff = np.mean(wins) / np.mean(losses)
                kelly = win_rate - (1 - win_rate) / payoff
                kelly *= cfg.KELLY_FRACTION
                kelly = max(cfg.KELLY_MIN_RISK / 100, min(cfg.KELLY_MAX_RISK / 100, kelly))
                risk_pct = kelly * 100

        # existing DD scaling
        if dd_pct >= cfg.MAX_DD_PERCENT:
            risk_pct *= 0.25
        elif dd_pct >= cfg.DD_HALF_RISK:
            risk_pct *= 0.5

        risk_amount = self.balance * risk_pct / 100.0

        # Loss per lot at SL (USD)
        sl_dollars = sl_points * cfg.POINT
        loss_per_lot = sl_dollars * cfg.CONTRACT_SIZE

        # JPY conversion (approx 150 JPY/USD)
        usd_jpy = 150.0
        loss_per_lot_jpy = loss_per_lot * usd_jpy

        if loss_per_lot_jpy <= 0:
            return cfg.MIN_LOT

        lot = risk_amount / loss_per_lot_jpy
        lot = max(cfg.MIN_LOT, min(cfg.MAX_LOT, round(lot, 2)))
        return lot

    def _open_trade(self, direction, price, time, score, dd_pct,
                    sl_points, tp_points, current_atr,
                    lot_multiplier=1.0, component_mask=None,
                    entry_type="normal", momentum_burst=False, entry_bar=0,
                    bar_spread_points=None):
        cfg = self.cfg
        pt = cfg.POINT

        # v10.0: Use actual bar spread if available, otherwise fall back to fixed estimate
        if getattr(cfg, 'USE_REALISTIC_SPREAD', False) and bar_spread_points is not None:
            half_spread = bar_spread_points * pt * 0.5
            slippage = getattr(cfg, 'SLIPPAGE_POINTS', 0) * pt
        else:
            half_spread = cfg.MAX_SPREAD_POINTS * pt * 0.5
            slippage = 0

        # BUY entry at Ask (mid + half_spread + slippage), SELL entry at Bid (mid - half_spread - slippage)
        if direction == "BUY":
            entry = price + half_spread + slippage
        else:
            entry = price - half_spread - slippage
        if direction == "BUY":
            sl = entry - sl_points * pt
            tp = entry + tp_points * pt
        else:
            sl = entry + sl_points * pt
            tp = entry - tp_points * pt

        lot = self._calc_lot(dd_pct, sl_points)
        # v3.0: Apply equity curve lot multiplier + v4.0 pyramid decay
        lot = max(cfg.MIN_LOT, round(lot * lot_multiplier, 2))

        tp_dist = tp_points * pt  # TP distance in price units

        pos = {
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "original_lot": lot,
            "open_time": time,
            "score": score,
            "breakeven_done": False,
            "partial_done": False,
            "sl_points": sl_points,
            "tp_points": tp_points,
            "tp_dist": tp_dist,
            "atr_at_entry": current_atr,
            "entry_type": entry_type,
            "momentum_burst": momentum_burst,
            "entry_bar": entry_bar,
        }
        # v3.0: Store component mask
        if component_mask is not None:
            pos["component_mask"] = component_mask[:]
        self.open_positions.append(pos)

    def _manage_positions(self, high, low, close, time, bar_idx, m15_df,
                          bar_open=None, bar_spread_points=None):
        cfg = self.cfg
        pt = cfg.POINT

        # v10.0: Compute spread-adjusted exit prices for SL/TP
        _realistic = getattr(cfg, 'USE_REALISTIC_SPREAD', False)
        _intrabar = getattr(cfg, 'USE_INTRABAR_SLTP_ORDER', False)
        if _realistic and bar_spread_points is not None:
            half_spread = bar_spread_points * pt * 0.5
        else:
            half_spread = 0  # legacy: no spread on exits

        # v10.0: Commission for partial closes
        commission_per_lot = getattr(cfg, 'COMMISSION_PER_LOT', 0)

        for pos in list(self.open_positions):
            # v4.0: Stale trade exit
            if self.check_stale_trade(pos, bar_idx):
                # Only close if not losing (close at current price if profitable or breakeven)
                if pos["direction"] == "BUY":
                    unrealized = close - pos["entry"]
                else:
                    unrealized = pos["entry"] - close
                if unrealized >= 0:
                    self._close_position(pos, close, time, "Stale", bar_idx,
                                         bar_spread_points=bar_spread_points)
                    continue

            if pos["direction"] == "BUY":
                # BUY exits at Bid: Low already represents Bid-side
                # SL triggered when Bid <= SL, TP triggered when Bid >= TP
                # Since OHLC data is Bid-based for most brokers, use as-is
                sl_hit = (low <= pos["sl"])
                tp_hit = (high >= pos["tp"])

                # v10.0: Intra-bar SL/TP order determination
                if sl_hit and tp_hit and _intrabar and bar_open is not None:
                    # Both SL and TP within this bar's range
                    dist_to_sl = abs(bar_open - pos["sl"])
                    dist_to_tp = abs(pos["tp"] - bar_open)
                    if dist_to_sl <= dist_to_tp:
                        # Open closer to SL -> SL hit first
                        self._close_position(pos, pos["sl"], time, "SL", bar_idx)
                    else:
                        # Open closer to TP -> TP hit first
                        self._close_position(pos, pos["tp"], time, "TP", bar_idx)
                    continue
                elif sl_hit:
                    self._close_position(pos, pos["sl"], time, "SL", bar_idx)
                    continue
                elif tp_hit:
                    self._close_position(pos, pos["tp"], time, "TP", bar_idx)
                    continue

                profit_price = close - pos["entry"]
                profit_pts = profit_price / pt
                atr_entry = pos["atr_at_entry"]

                # v2.0: Partial close at 50% of TP distance
                if cfg.USE_PARTIAL_CLOSE and not pos["partial_done"]:
                    if profit_price >= pos["tp_dist"] * cfg.PARTIAL_TP_RATIO:
                        # Close 50% of position
                        closed_lot = pos["original_lot"] * cfg.PARTIAL_CLOSE_RATIO
                        remaining_lot = pos["lot"] - closed_lot
                        if remaining_lot < cfg.MIN_LOT:
                            remaining_lot = cfg.MIN_LOT
                            closed_lot = pos["lot"] - remaining_lot

                        if closed_lot > 0:
                            # v10.1: Apply exit spread to partial close (BUY sells at Bid)
                            partial_exit = close - half_spread  # half_spread=0 if not realistic
                            partial_profit = partial_exit - pos["entry"]
                            pnl_pts_partial = partial_profit / pt
                            pnl_usd = pnl_pts_partial * pt * cfg.CONTRACT_SIZE * closed_lot
                            # v10.0: Commission on partial close (proportional)
                            if commission_per_lot > 0:
                                pnl_usd -= commission_per_lot * closed_lot * 0.5  # half of round-trip
                            pnl_jpy = pnl_usd * 150.0
                            self.balance += pnl_jpy
                            self.peak_balance = max(self.peak_balance, self.balance)
                            self.daily_pnl += pnl_jpy
                            self.trades.append({
                                "open_time": pos["open_time"],
                                "close_time": time,
                                "direction": pos["direction"],
                                "entry": round(pos["entry"], 2),
                                "exit": round(partial_exit, 2),
                                "lot": closed_lot,
                                "pnl_pts": round(pnl_pts_partial, 1),
                                "pnl_usd": round(pnl_usd, 2),
                                "pnl_jpy": round(pnl_jpy, 0),
                                "balance": round(self.balance, 0),
                                "reason": "Partial",
                                "score": pos["score"],
                                "entry_type": pos.get("entry_type", "normal"),
                                "momentum_burst": pos.get("momentum_burst", False),
                            })
                            # v3.0: Track partial close PnL
                            self.recent_trade_pnls.append(pnl_jpy)
                            pos["lot"] = remaining_lot
                        # Move SL to breakeven
                        pos["sl"] = pos["entry"] + 10 * pt
                        pos["partial_done"] = True
                        pos["breakeven_done"] = True

                # v2.0: Breakeven at ATR * BE_ATR_MULTI
                # v9.2: Adaptive BE -- tighter in ranging to protect capital
                _be_multi = cfg.BE_ATR_MULTI
                if getattr(cfg, 'USE_ADAPTIVE_CHANDELIER', False):
                    _h4adx = getattr(self, '_current_h4_adx', 99)
                    if not (_h4adx != _h4adx) and _h4adx < getattr(cfg, 'ADAPTIVE_CHAND_ADX_THRESHOLD', 20):
                        _be_multi = min(_be_multi, 0.4)  # BE at 0.4*ATR in ranging
                if not pos["breakeven_done"] and profit_price >= atr_entry * _be_multi:
                    pos["sl"] = pos["entry"] + 10 * pt
                    pos["breakeven_done"] = True

                # v2.0: Trailing at BE * 1.5, step = ATR * TRAIL_ATR_MULTI
                be_price = atr_entry * _be_multi
                if profit_price >= be_price * 1.5:
                    trail_step = atr_entry * cfg.TRAIL_ATR_MULTI
                    ns = close - trail_step
                    if ns > pos["sl"] + 5 * pt:
                        pos["sl"] = ns

                # v3.0: Chandelier Exit for BUY
                # v9.2: Adaptive chandelier -- tighter in ranging (ADX < threshold)
                if cfg.USE_CHANDELIER_EXIT and profit_price >= atr_entry * cfg.BE_ATR_MULTI:
                    start_idx = max(0, bar_idx - cfg.CHANDELIER_PERIOD)
                    highest_high = m15_df["High"].iloc[start_idx:bar_idx + 1].max()
                    _chand_multi = cfg.CHANDELIER_ATR_MULTI
                    if getattr(cfg, 'USE_ADAPTIVE_CHANDELIER', False):
                        _h4adx = getattr(self, '_current_h4_adx', 99)
                        if not (_h4adx != _h4adx) and _h4adx < getattr(cfg, 'ADAPTIVE_CHAND_ADX_THRESHOLD', 20):
                            _chand_multi = getattr(cfg, 'ADAPTIVE_CHAND_ATR_MULTI', 1.2)
                    chandelier_sl = highest_high - atr_entry * _chand_multi
                    if chandelier_sl > pos["sl"] + 5 * pt:
                        pos["sl"] = chandelier_sl

            else:  # SELL
                # SELL exits at Ask: High + spread represents Ask-side
                # SL triggered when Ask >= SL, TP triggered when Ask <= TP
                # For SELL: SL hit check uses high (Ask side moves against us)
                # TP hit check uses low (Bid side moves in our favor)
                sl_hit = (high >= pos["sl"])
                tp_hit = (low <= pos["tp"])

                # v10.0: Intra-bar SL/TP order determination
                if sl_hit and tp_hit and _intrabar and bar_open is not None:
                    dist_to_sl = abs(pos["sl"] - bar_open)
                    dist_to_tp = abs(bar_open - pos["tp"])
                    if dist_to_sl <= dist_to_tp:
                        self._close_position(pos, pos["sl"], time, "SL", bar_idx)
                    else:
                        self._close_position(pos, pos["tp"], time, "TP", bar_idx)
                    continue
                elif sl_hit:
                    self._close_position(pos, pos["sl"], time, "SL", bar_idx)
                    continue
                elif tp_hit:
                    self._close_position(pos, pos["tp"], time, "TP", bar_idx)
                    continue

                profit_price = pos["entry"] - close
                profit_pts = profit_price / pt
                atr_entry = pos["atr_at_entry"]

                # v2.0: Partial close at 50% of TP distance
                if cfg.USE_PARTIAL_CLOSE and not pos["partial_done"]:
                    if profit_price >= pos["tp_dist"] * cfg.PARTIAL_TP_RATIO:
                        closed_lot = pos["original_lot"] * cfg.PARTIAL_CLOSE_RATIO
                        remaining_lot = pos["lot"] - closed_lot
                        if remaining_lot < cfg.MIN_LOT:
                            remaining_lot = cfg.MIN_LOT
                            closed_lot = pos["lot"] - remaining_lot

                        if closed_lot > 0:
                            # v10.1: Apply exit spread to partial close (SELL buys at Ask)
                            partial_exit = close + half_spread  # half_spread=0 if not realistic
                            partial_profit = pos["entry"] - partial_exit
                            pnl_pts_partial = partial_profit / pt
                            pnl_usd = pnl_pts_partial * pt * cfg.CONTRACT_SIZE * closed_lot
                            # v10.0: Commission on partial close (proportional)
                            if commission_per_lot > 0:
                                pnl_usd -= commission_per_lot * closed_lot * 0.5
                            pnl_jpy = pnl_usd * 150.0
                            self.balance += pnl_jpy
                            self.peak_balance = max(self.peak_balance, self.balance)
                            self.daily_pnl += pnl_jpy
                            self.trades.append({
                                "open_time": pos["open_time"],
                                "close_time": time,
                                "direction": pos["direction"],
                                "entry": round(pos["entry"], 2),
                                "exit": round(partial_exit, 2),
                                "lot": closed_lot,
                                "pnl_pts": round(pnl_pts_partial, 1),
                                "pnl_usd": round(pnl_usd, 2),
                                "pnl_jpy": round(pnl_jpy, 0),
                                "balance": round(self.balance, 0),
                                "reason": "Partial",
                                "score": pos["score"],
                                "entry_type": pos.get("entry_type", "normal"),
                                "momentum_burst": pos.get("momentum_burst", False),
                            })
                            # v3.0: Track partial close PnL
                            self.recent_trade_pnls.append(pnl_jpy)
                            pos["lot"] = remaining_lot
                        pos["sl"] = pos["entry"] - 10 * pt
                        pos["partial_done"] = True
                        pos["breakeven_done"] = True

                # v2.0: Breakeven
                # v9.2: Adaptive BE for SELL -- tighter in ranging
                _be_multi = cfg.BE_ATR_MULTI
                if getattr(cfg, 'USE_ADAPTIVE_CHANDELIER', False):
                    _h4adx = getattr(self, '_current_h4_adx', 99)
                    if not (_h4adx != _h4adx) and _h4adx < getattr(cfg, 'ADAPTIVE_CHAND_ADX_THRESHOLD', 20):
                        _be_multi = min(_be_multi, 0.4)
                if not pos["breakeven_done"] and profit_price >= atr_entry * _be_multi:
                    pos["sl"] = pos["entry"] - 10 * pt
                    pos["breakeven_done"] = True

                # v2.0: Trailing
                be_price = atr_entry * _be_multi
                if profit_price >= be_price * 1.5:
                    trail_step = atr_entry * cfg.TRAIL_ATR_MULTI
                    ns = close + trail_step
                    if ns < pos["sl"] - 5 * pt or pos["sl"] == 0:
                        pos["sl"] = ns

                # v3.0: Chandelier Exit for SELL
                # v9.2: Adaptive chandelier -- tighter in ranging
                if cfg.USE_CHANDELIER_EXIT and profit_price >= atr_entry * cfg.BE_ATR_MULTI:
                    start_idx = max(0, bar_idx - cfg.CHANDELIER_PERIOD)
                    lowest_low = m15_df["Low"].iloc[start_idx:bar_idx + 1].min()
                    _chand_multi = cfg.CHANDELIER_ATR_MULTI
                    if getattr(cfg, 'USE_ADAPTIVE_CHANDELIER', False):
                        _h4adx = getattr(self, '_current_h4_adx', 99)
                        if not (_h4adx != _h4adx) and _h4adx < getattr(cfg, 'ADAPTIVE_CHAND_ADX_THRESHOLD', 20):
                            _chand_multi = getattr(cfg, 'ADAPTIVE_CHAND_ATR_MULTI', 1.2)
                    chandelier_sl = lowest_low + atr_entry * _chand_multi
                    if chandelier_sl < pos["sl"] - 5 * pt:
                        pos["sl"] = chandelier_sl

    def _close_position(self, pos, exit_price, time, reason, bar_idx=0,
                         bar_spread_points=None):
        cfg = self.cfg
        pt = cfg.POINT
        _realistic = getattr(cfg, 'USE_REALISTIC_SPREAD', False)

        # v10.1: Realistic execution model for exits
        actual_exit = exit_price
        if _realistic:
            _sl_slip_pts = getattr(cfg, 'SLIPPAGE_POINTS', 0)

            if reason == "SL":
                # SL is a stop order -> slips by SLIPPAGE_POINTS (worsens exit)
                sl_slippage = _sl_slip_pts * pt
                if pos["direction"] == "BUY":
                    actual_exit = exit_price - sl_slippage  # BUY SL fills lower
                else:
                    actual_exit = exit_price + sl_slippage  # SELL SL fills higher

            elif reason == "TP":
                # TP is a limit order -> no slippage, fills at exact price
                actual_exit = exit_price

            else:
                # Market close (Stale, Weekend, EndOfPeriod, TimeExit, BB_Mid_TP, etc.)
                # Exit at market: BUY sells at Bid, SELL buys at Ask
                if bar_spread_points is not None:
                    half_spread = bar_spread_points * pt * 0.5
                else:
                    half_spread = 0
                if pos["direction"] == "BUY":
                    actual_exit = exit_price - half_spread  # BUY exits at Bid
                else:
                    actual_exit = exit_price + half_spread  # SELL exits at Ask

        pnl_pts = ((actual_exit - pos["entry"]) if pos["direction"] == "BUY"
                    else (pos["entry"] - actual_exit)) / pt
        # PnL in USD: points * $0.01 * 100oz * lot
        pnl_usd = pnl_pts * pt * cfg.CONTRACT_SIZE * pos["lot"]

        # v10.0: Deduct commission (round-trip per lot)
        commission_per_lot = getattr(cfg, 'COMMISSION_PER_LOT', 0)
        if commission_per_lot > 0:
            commission_usd = commission_per_lot * pos["lot"]
            pnl_usd -= commission_usd

        # JPY conversion
        pnl_jpy = pnl_usd * 150.0

        # v8.0g: Track consecutive losses for streak cooldown
        if pnl_jpy > 0:
            self.consecutive_losses = 0
        elif pnl_jpy <= 0 and reason in ("SL", "Stale", "Weekend", "EndOfPeriod"):
            self.consecutive_losses += 1

        # Cooldown after SL (with optional streak extension)
        if reason == "SL" and bar_idx > 0:
            base_cooldown = cfg.COOLDOWN_BARS
            if getattr(cfg, 'USE_LOSING_STREAK_COOLDOWN', False):
                _streak_thresh = getattr(cfg, 'STREAK_THRESHOLD', 3)
                _streak_extra = getattr(cfg, 'STREAK_EXTRA_COOLDOWN', 16)
                if self.consecutive_losses >= _streak_thresh:
                    base_cooldown += _streak_extra
            self.cooldown_until = bar_idx + base_cooldown

        self.balance += pnl_jpy
        self.peak_balance = max(self.peak_balance, self.balance)

        # v4.0: Track daily PnL for circuit breaker
        self.daily_pnl += pnl_jpy
        if self.check_daily_circuit():
            self.circuit_breaker = True

        # v3.0: Track PnL for equity curve filter and adaptive sizing
        self.recent_trade_pnls.append(pnl_jpy)

        # v3.0: Track component stats
        if "component_mask" in pos:
            is_win = pnl_jpy > 0
            for comp_idx, val in enumerate(pos["component_mask"]):
                if val != 0:
                    self.component_stats[comp_idx]["total"] += 1
                    if is_win:
                        self.component_stats[comp_idx]["wins"] += 1

        self.trades.append({
            "open_time": pos["open_time"],
            "close_time": time,
            "direction": pos["direction"],
            "entry": round(pos["entry"], 2),
            "exit": round(actual_exit, 2),
            "lot": pos["lot"],
            "pnl_pts": round(pnl_pts, 1),
            "pnl_usd": round(pnl_usd, 2),
            "pnl_jpy": round(pnl_jpy, 0),
            "balance": round(self.balance, 0),
            "reason": reason,
            "score": pos["score"],
            "entry_type": pos.get("entry_type", "normal"),
            "momentum_burst": pos.get("momentum_burst", False),
        })
        self.open_positions.remove(pos)

    def _unrealized_pnl(self, price):
        cfg = self.cfg
        pt = cfg.POINT
        total = 0
        for p in self.open_positions:
            if p["direction"] == "BUY":
                pnl_pts = (price - p["entry"]) / pt
            else:
                pnl_pts = (p["entry"] - price) / pt
            total += pnl_pts * pt * cfg.CONTRACT_SIZE * p["lot"] * 150.0
        return total

    def analyze_components(self):
        """v4.0: Print win rate analysis for each scoring component."""
        names = [
            "1. H4 Trend (3pt)",
            "2. H1 MA Dir (2pt)",
            "3. H1 RSI (1pt)",
            "4. H1 BB Bounce (1pt)",
            "5. M15 MA Cross (2pt)",
            "6. Channel Regr (1pt)",
            "7. Momentum (1pt)",
            "8. Session Bonus (1pt)",
            "9. USD Correlation (2pt)",
            "10. RSI Divergence (2pt)",
            "11. S/R Level (+/-1pt)",
            "12. Candle Pattern (1pt)",
            "13. H4 RSI Align (1pt)",
            "14. Momentum Burst (3pt)",
            "15. Volume Climax (2pt)",
        ]
        print("\n  Component Analysis (v4.0):")
        print(f"  {'Component':<30} {'Trades':>7} {'Wins':>7} {'Win%':>7}")
        print("  " + "-" * 55)
        for i in range(15):
            stats = self.component_stats[i]
            total = stats["total"]
            wins = stats["wins"]
            wr = (wins / total * 100) if total > 0 else 0.0
            print(f"  {names[i]:<30} {total:>7} {wins:>7} {wr:>6.1f}%")

    def get_report(self):
        if not self.trades:
            return {"error": "No trades"}
        df = pd.DataFrame(self.trades)
        wins = df[df["pnl_pts"] > 0]
        losses = df[df["pnl_pts"] <= 0]
        total_pnl = df["pnl_jpy"].sum()
        win_rate = len(wins) / len(df) * 100

        avg_win_pts = wins["pnl_pts"].mean() if len(wins) > 0 else 0
        avg_loss_pts = abs(losses["pnl_pts"].mean()) if len(losses) > 0 else 0
        avg_win_jpy = wins["pnl_jpy"].mean() if len(wins) > 0 else 0
        avg_loss_jpy = abs(losses["pnl_jpy"].mean()) if len(losses) > 0 else 0
        pf = (wins["pnl_jpy"].sum() / abs(losses["pnl_jpy"].sum())) if len(losses) > 0 and losses["pnl_jpy"].sum() != 0 else float("inf")

        eq = pd.DataFrame(self.equity_curve)
        max_dd = 0
        max_dd_jpy = 0
        if len(eq) > 0:
            eq["peak"] = eq["equity"].cummax()
            eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
            eq["dd_jpy"] = eq["peak"] - eq["equity"]
            max_dd = eq["dd"].max()
            max_dd_jpy = eq["dd_jpy"].max()

        df["month"] = pd.to_datetime(df["close_time"]).dt.to_period("M")
        monthly = df.groupby("month")["pnl_jpy"].sum()
        pm = (monthly > 0).sum()
        tm = len(monthly)

        # By-reason stats
        reason_stats = df.groupby("reason").agg(
            count=("pnl_jpy", "count"),
            pnl=("pnl_jpy", "sum")
        )

        # v4.0: Risk Metrics
        returns = df["pnl_jpy"]
        sharpe = sortino = calmar = 0
        max_consec_wins = max_consec_losses = 0
        expectancy = 0

        if len(returns) > 1:
            sharpe = returns.mean() / returns.std() * np.sqrt(252 * 4) if returns.std() > 0 else 0
            downside = returns[returns < 0].std()
            sortino = returns.mean() / downside * np.sqrt(252 * 4) if pd.notna(downside) and downside > 0 else 0

            total_return_pct = (self.balance / self.cfg.INITIAL_BALANCE - 1) * 100
            months_count = max(tm, 1)
            annual_return = total_return_pct / months_count * 12
            calmar = annual_return / max_dd if max_dd > 0 else 0

            expectancy = returns.mean()

            # Consecutive stats
            current_streak = 0
            for t_pnl in returns:
                if t_pnl > 0:
                    current_streak = current_streak + 1 if current_streak > 0 else 1
                else:
                    current_streak = current_streak - 1 if current_streak < 0 else -1
                max_consec_wins = max(max_consec_wins, current_streak)
                max_consec_losses = min(max_consec_losses, current_streak)

        return {
            "Period": f"{df['open_time'].iloc[0]} ~ {df['close_time'].iloc[-1]}",
            "Initial Balance": f"{self.cfg.INITIAL_BALANCE:,.0f} JPY",
            "Final Balance": f"{self.balance:,.0f} JPY",
            "Total PnL": f"{total_pnl:+,.0f} JPY",
            "Return": f"{(self.balance / self.cfg.INITIAL_BALANCE - 1) * 100:+.1f}%",
            "Trades": len(df),
            "Win Rate": f"{win_rate:.1f}% ({len(wins)}W/{len(losses)}L)",
            "Avg Win": f"{avg_win_pts:.0f}pt ({avg_win_jpy:+,.0f} JPY)",
            "Avg Loss": f"{avg_loss_pts:.0f}pt ({avg_loss_jpy:,.0f} JPY)",
            "RR Ratio": f"1:{avg_win_pts/avg_loss_pts:.2f}" if avg_loss_pts > 0 else "N/A",
            "PF": f"{pf:.2f}" if pf != float("inf") else "INF",
            "Max DD": f"{max_dd:.1f}% ({max_dd_jpy:,.0f} JPY)",
            "Monthly WR": f"{pm}/{tm} ({pm/tm*100:.0f}%)" if tm > 0 else "N/A",
            "Sharpe": f"{sharpe:.2f}",
            "Sortino": f"{sortino:.2f}",
            "Calmar": f"{calmar:.2f}",
            "Max Consec Wins": max_consec_wins,
            "Max Consec Losses": abs(max_consec_losses),
            "Expectancy": f"{expectancy:+,.0f} JPY/trade",
            "Monthly": monthly.to_dict(),
            "ByReason": reason_stats.to_dict(),
        }


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    import os, sys
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

    bt = GoldBacktester(cfg)
    bt.run(h4, h1, m15, usdjpy_df=usdjpy)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print(" AntigravityMTF EA [GOLD] v4.0 Backtest Results (6 months)")
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

        # v4.0: Component analysis
        bt.analyze_components()

        # v4.0: Defense stats
        print(f"\n  --- v4.0 Defense Stats ---")
        print(f"  News filter blocks:   {bt.news_blocks}")
        print(f"  Crash regime skips:   {bt.crash_skips}")
        print(f"  Weekend closes:       {bt.weekend_closes}")
        print(f"  Spread blocks:        {bt.spread_blocks}")
        print(f"  Circuit breaker days: {sum(1 for t in bt.trades if t.get('reason') == 'CircuitBreaker')}")

        # v4.0: Attack stats
        reversals = sum(1 for t in bt.trades if t.get('entry_type') == 'reversal')
        pyramids = sum(1 for t in bt.trades if t.get('entry_type') == 'pyramid')
        bursts = sum(1 for t in bt.trades if t.get('momentum_burst', False))
        print(f"\n  --- v4.0 Attack Stats ---")
        print(f"  Reversal trades:      {reversals}")
        print(f"  Pyramid entries:      {pyramids}")
        print(f"  Momentum burst trades:{bursts}")

        # Last 10 trades
        print(f"\n  Trade Details (last 10):")
        print(f"  {'DateTime':<20} {'Dir':<5} {'Entry':>10} {'Exit':>10} {'Lot':>5} {'PnL(pt)':>8} {'PnL(JPY)':>10} {'Balance':>12} {'Reason':<10} {'Type':<8}")
        print("  " + "-" * 110)
        for t in bt.trades[-10:]:
            print(f"  {str(t['open_time'])[:19]:<20} {t['direction']:<5} {t['entry']:>10.2f} {t['exit']:>10.2f} {t['lot']:>5.2f} {t['pnl_pts']:>8.0f} {t['pnl_jpy']:>+10,.0f} {t['balance']:>12,.0f} {t['reason']:<10} {t.get('entry_type','normal'):<8}")
    else:
        print("[WARN] No trades occurred")
        print("   Try lowering MinScore or adjusting parameters")
