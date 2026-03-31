"""
AntigravityMTF EA USDJPY v2.0 — バックテスター（直近1年）
ATR動的SL/TP + ボラティリティレジーム + セッション + モメンタム + 半利確
"""

import pandas as pd
import numpy as np
try:
    import yfinance as yf
except ImportError:
    yf = None
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")


class USDJPYConfig:
    SYMBOL = "USDJPY=X"
    INITIAL_BALANCE = 100_000
    RISK_PERCENT = 0.5         # ATR-SLで自動調整されるため少し引き上げ
    MAX_POSITIONS = 1
    MIN_SCORE = 7              # 最低スコア 7/12
    COOLDOWN_BARS = 16         # SL後16本(=4時間)
    MAX_SPREAD_PIPS = 3.0
    PIP_VALUE = 0.01
    MAX_DD_PERCENT = 6.0
    DD_HALF_RISK = 2.5
    MAX_LOT = 0.50
    MIN_LOT = 0.01

    # ATR動的SL/TP
    ATR_PERIOD = 14
    SL_ATR_MULTI = 1.5
    TP_ATR_MULTI = 3.0
    TRAIL_ATR_MULTI = 1.0
    BE_ATR_MULTI = 1.5
    MIN_SL_PIPS = 10
    MAX_SL_PIPS = 50

    # ボラティリティレジーム
    VOL_REGIME_PERIOD = 50
    VOL_REGIME_LOW = 0.7
    VOL_REGIME_HIGH = 1.5
    HIGH_VOL_SL_BONUS = 0.5

    # セッション・モメンタム
    USE_SESSION_BONUS = True
    USE_MOMENTUM = True

    # 半利確
    USE_PARTIAL_CLOSE = True
    PARTIAL_CLOSE_RATIO = 0.5
    PARTIAL_TP_RATIO = 0.5

    H4_MA_FAST = 20
    H4_MA_SLOW = 50
    H4_ADX_PERIOD = 14
    H4_ADX_THRESHOLD = 20

    H1_MA_FAST = 10
    H1_MA_SLOW = 30
    H1_RSI_PERIOD = 14
    H1_BB_PERIOD = 20
    H1_BB_DEV = 2.0

    M15_MA_FAST = 5
    M15_MA_SLOW = 20

    TRADE_START_HOUR = 8
    TRADE_END_HOUR = 22


# ============================================================
# インジケーター
# ============================================================
def calc_sma(s, p):
    return s.rolling(window=p, min_periods=p).mean()

def calc_ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def calc_rsi(series, period):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calc_adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, 1e-10))
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10)
    adx = dx.rolling(window=period).mean()
    return adx, plus_di, minus_di

def calc_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def calc_bb(series, period, deviation):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    return sma + deviation * std, sma, sma - deviation * std

def calc_channel_signal(close_series, lookback=40):
    if len(close_series) < lookback + 1:
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
# データ取得
# ============================================================
def fetch_usdjpy_data(months=12):
    print(f"USDJPY data ({months}months)...")
    end = datetime.now()
    start = end - timedelta(days=months * 30 + 90)

    t = yf.Ticker("USDJPY=X")

    h1_raw = t.history(start=start, end=end, interval="1h")
    if h1_raw.empty:
        print("H1 failed, using daily")
        daily = t.history(start=start, end=end, interval="1d")
        if daily.empty:
            return None, None, None
        return _generate_from_daily(daily, months)

    print(f"   H1: {len(h1_raw)} bars ({h1_raw.index[0].date()} ~ {h1_raw.index[-1].date()})")

    h4_df = h1_raw.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna()

    m15_list = []
    for idx, row in h1_raw.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        for j in range(4):
            frac = j / 4
            frac_next = (j + 1) / 4
            seg_o = o + (c - o) * frac
            seg_c = o + (c - o) * frac_next
            seg_h = max(seg_o, seg_c) + (h - max(o, c)) * (1 - abs(frac - 0.5) * 2) * 0.5
            seg_l = min(seg_o, seg_c) - (min(o, c) - l) * (1 - abs(frac - 0.5) * 2) * 0.5
            ts = idx + timedelta(minutes=j * 15)
            m15_list.append({"Open": seg_o, "High": seg_h, "Low": seg_l, "Close": seg_c, "time": ts})

    m15_df = pd.DataFrame(m15_list).set_index("time")

    cutoff = end - timedelta(days=months * 30)
    cutoff_ts = pd.Timestamp(cutoff, tz=m15_df.index.tz) if m15_df.index.tz else pd.Timestamp(cutoff)
    m15_df = m15_df[m15_df.index >= cutoff_ts]

    print(f"   H4: {len(h4_df)} / M15: {len(m15_df)} bars")
    print(f"   Period: {m15_df.index[0].date()} ~ {m15_df.index[-1].date()}")

    return h4_df, h1_raw, m15_df


def _generate_from_daily(daily, months):
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
    cutoff_ts = pd.Timestamp(cutoff, tz=m15_df.index.tz) if m15_df.index.tz else pd.Timestamp(cutoff)
    m15_df = m15_df[m15_df.index >= cutoff_ts]

    return h4_df, h1_df, m15_df


# ============================================================
# バックテストエンジン v2.0
# ============================================================
class USDJPYBacktester:
    def __init__(self, cfg):
        self.cfg = cfg
        self.balance = cfg.INITIAL_BALANCE
        self.equity_curve = []
        self.trades = []
        self.open_positions = []
        self.peak_balance = cfg.INITIAL_BALANCE
        self.cooldown_until = 0
        self.partial_closed = set()

    def run(self, h4_df, h1_df, m15_df):
        cfg = self.cfg

        h4_df = h4_df.copy()
        h1_df = h1_df.copy()
        m15_df = m15_df.copy()

        h4_df["ma_fast"] = calc_sma(h4_df["Close"], cfg.H4_MA_FAST)
        h4_df["ma_slow"] = calc_sma(h4_df["Close"], cfg.H4_MA_SLOW)
        h4_df["adx"], h4_df["plus_di"], h4_df["minus_di"] = calc_adx(
            h4_df["High"], h4_df["Low"], h4_df["Close"], cfg.H4_ADX_PERIOD)

        h1_df["ma_fast"] = calc_ema(h1_df["Close"], cfg.H1_MA_FAST)
        h1_df["ma_slow"] = calc_ema(h1_df["Close"], cfg.H1_MA_SLOW)
        h1_df["rsi"] = calc_rsi(h1_df["Close"], cfg.H1_RSI_PERIOD)
        h1_df["bb_upper"], h1_df["bb_mid"], h1_df["bb_lower"] = calc_bb(
            h1_df["Close"], cfg.H1_BB_PERIOD, cfg.H1_BB_DEV)

        m15_df["ma_fast"] = calc_ema(m15_df["Close"], cfg.M15_MA_FAST)
        m15_df["ma_slow"] = calc_ema(m15_df["Close"], cfg.M15_MA_SLOW)

        # ATR計算（M15足）
        m15_df["atr"] = calc_atr(m15_df["High"], m15_df["Low"], m15_df["Close"], cfg.ATR_PERIOD)
        # ATR平均（ボラレジーム用）
        m15_df["atr_avg"] = m15_df["atr"].rolling(window=cfg.VOL_REGIME_PERIOD, min_periods=cfg.VOL_REGIME_PERIOD).mean()

        total_bars = len(m15_df)
        print(f"\nBacktest: {m15_df.index[0].date()} -> {m15_df.index[-1].date()}")
        print(f"   M15 bars: {total_bars:,}")
        print(f"   v2.0: Risk={cfg.RISK_PERCENT}% SL=ATRx{cfg.SL_ATR_MULTI} TP=ATRx{cfg.TP_ATR_MULTI} MinScore={cfg.MIN_SCORE}")
        print(f"   VolRegime: Low<{cfg.VOL_REGIME_LOW} High>{cfg.VOL_REGIME_HIGH}")
        print(f"   PartialClose={cfg.USE_PARTIAL_CLOSE} Session={cfg.USE_SESSION_BONUS} Momentum={cfg.USE_MOMENTUM}")

        for i in range(100, total_bars):
            ct = m15_df.index[i]
            cc = m15_df["Close"].iloc[i]
            ch = m15_df["High"].iloc[i]
            cl = m15_df["Low"].iloc[i]
            cur_atr = m15_df["atr"].iloc[i] if pd.notna(m15_df["atr"].iloc[i]) else None
            cur_atr_avg = m15_df["atr_avg"].iloc[i] if pd.notna(m15_df["atr_avg"].iloc[i]) else None

            self._manage_positions(ch, cl, cc, ct, i, cur_atr)

            if self.balance > self.peak_balance:
                self.peak_balance = self.balance
            current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0

            hour = ct.hour if hasattr(ct, "hour") else 12
            if hour < cfg.TRADE_START_HOUR or hour >= cfg.TRADE_END_HOUR:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            if hasattr(ct, "dayofweek") and ct.dayofweek == 4 and hour >= 18:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            if len(self.open_positions) >= cfg.MAX_POSITIONS:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            if i < self.cooldown_until:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            # ★ ボラティリティレジーム判定
            if cur_atr is None or cur_atr <= 0:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            vol_regime = 1  # default: normal
            if cur_atr_avg is not None and cur_atr_avg > 0:
                ratio = cur_atr / cur_atr_avg
                if ratio < cfg.VOL_REGIME_LOW:
                    vol_regime = 0  # low vol -> skip
                elif ratio > cfg.VOL_REGIME_HIGH:
                    vol_regime = 2  # high vol

            if vol_regime == 0:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue

            h4_mask = h4_df.index <= ct
            if h4_mask.sum() < 2:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h4_row = h4_df[h4_mask].iloc[-1]

            h1_mask = h1_df.index <= ct
            if h1_mask.sum() < 4:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})
                continue
            h1_curr = h1_df[h1_mask].iloc[-1]
            h1_prev = h1_df[h1_mask].iloc[-2]

            m15_curr = m15_df.iloc[i]
            m15_prev = m15_df.iloc[i - 1]

            # ──── スコアリング v2.0 (max 12 points) ────
            buy_score = 0
            sell_score = 0

            # 1. H4 Trend (3pt)
            if pd.notna(h4_row.get("adx")) and h4_row["adx"] >= cfg.H4_ADX_THRESHOLD:
                if h4_row["ma_fast"] > h4_row["ma_slow"] and h4_row["plus_di"] > h4_row["minus_di"]:
                    buy_score += 3
                elif h4_row["ma_fast"] < h4_row["ma_slow"] and h4_row["minus_di"] > h4_row["plus_di"]:
                    sell_score += 3

            # 2. H1 MA direction (2pt)
            if pd.notna(h1_curr["ma_fast"]) and pd.notna(h1_curr["ma_slow"]):
                if h1_curr["ma_fast"] > h1_curr["ma_slow"]:
                    buy_score += 2
                elif h1_curr["ma_fast"] < h1_curr["ma_slow"]:
                    sell_score += 2

            # 3. H1 RSI (1pt)
            if pd.notna(h1_curr["rsi"]):
                rsi_val = h1_curr["rsi"]
                if 40 < rsi_val < 60:
                    buy_score += 1
                    sell_score += 1
                elif 60 <= rsi_val < 65:
                    buy_score += 1
                elif 35 < rsi_val <= 40:
                    sell_score += 1

            # 4. H1 BB (1pt)
            if pd.notna(h1_curr.get("bb_upper")) and pd.notna(h1_curr.get("bb_lower")):
                bw = h1_curr["bb_upper"] - h1_curr["bb_lower"]
                if bw > 0:
                    bp = (h1_curr["Close"] - h1_curr["bb_lower"]) / bw
                    if bp < 0.2 and h1_curr["Close"] > h1_prev["Close"]:
                        buy_score += 1
                    if bp > 0.8 and h1_curr["Close"] < h1_prev["Close"]:
                        sell_score += 1

            # 5. M15 MA cross (2pt)
            if pd.notna(m15_curr["ma_fast"]) and pd.notna(m15_curr["ma_slow"]):
                fast_above = m15_curr["ma_fast"] > m15_curr["ma_slow"]
                prev_fast_above = m15_prev["ma_fast"] > m15_prev["ma_slow"] if pd.notna(m15_prev["ma_fast"]) else None
                if fast_above and prev_fast_above is False:
                    buy_score += 2
                elif not fast_above and prev_fast_above is True:
                    sell_score += 2

            # 6. Channel regression (1pt)
            h1_closes = h1_df[h1_mask]["Close"]
            cs = calc_channel_signal(h1_closes, 40)
            if cs == 1:
                buy_score += 1
            elif cs == -1:
                sell_score += 1

            # 7. ★ Momentum (1pt) - M15 close[1] vs close[3]
            if cfg.USE_MOMENTUM and i >= 3:
                close_1 = m15_df["Close"].iloc[i]
                close_3 = m15_df["Close"].iloc[i - 2]
                threshold = cfg.PIP_VALUE * 3  # 3 pips threshold
                if close_1 - close_3 > threshold:
                    buy_score += 1
                elif close_3 - close_1 > threshold:
                    sell_score += 1

            # 8. ★ Session bonus (1pt) - Tokyo/London early for USDJPY
            if cfg.USE_SESSION_BONUS:
                if (0 <= hour < 8) or (8 <= hour < 11):
                    buy_score += 1
                    sell_score += 1

            # Dynamic score barrier
            current_min_score = cfg.MIN_SCORE
            if current_dd >= 20.0:
                current_min_score = 10
            elif current_dd >= 15.0:
                current_min_score = 9
            elif current_dd >= 10.0:
                current_min_score = 8

            # ★ Dynamic SL/TP (ATR-based)
            sl_multi = cfg.SL_ATR_MULTI
            if vol_regime == 2:
                sl_multi += cfg.HIGH_VOL_SL_BONUS

            sl_dist = cur_atr * sl_multi
            tp_dist = cur_atr * cfg.TP_ATR_MULTI

            # Clamp SL to min/max
            min_sl = cfg.MIN_SL_PIPS * cfg.PIP_VALUE
            max_sl = cfg.MAX_SL_PIPS * cfg.PIP_VALUE
            sl_dist = max(min_sl, min(max_sl, sl_dist))
            tp_dist = max(sl_dist * 1.5, tp_dist)  # min RR 1:1.5

            # Entry
            if buy_score >= current_min_score and buy_score > sell_score:
                self._open_trade("BUY", cc, ct, buy_score, current_dd, sl_dist, tp_dist, cur_atr, vol_regime)
            elif sell_score >= current_min_score and sell_score > buy_score:
                self._open_trade("SELL", cc, ct, sell_score, current_dd, sl_dist, tp_dist, cur_atr, vol_regime)

            self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})

        fc = m15_df["Close"].iloc[-1]
        for pos in list(self.open_positions):
            self._close_position(pos, fc, m15_df.index[-1], "END", total_bars - 1)

        print("Backtest complete")

    def _calc_lot(self, sl_dist, dd_pct=0):
        cfg = self.cfg
        risk_pct = cfg.RISK_PERCENT
        if dd_pct >= cfg.MAX_DD_PERCENT:
            risk_pct *= 0.25
        elif dd_pct >= cfg.DD_HALF_RISK:
            risk_pct *= 0.5

        risk_amount = self.balance * risk_pct / 100.0
        # USDJPY: 1lot=100,000 units, loss = sl_dist * 100,000 * lot
        # sl_dist is in price units (e.g., 0.25 = 25 pips)
        risk_per_lot = sl_dist * 100_000
        if risk_per_lot <= 0:
            return cfg.MIN_LOT
        lot = risk_amount / risk_per_lot
        lot = max(cfg.MIN_LOT, min(cfg.MAX_LOT, round(lot, 2)))
        return lot

    def _open_trade(self, direction, price, time, score, dd_pct, sl_dist, tp_dist, atr, vol_regime):
        cfg = self.cfg
        spread = cfg.MAX_SPREAD_PIPS * cfg.PIP_VALUE * 0.5

        entry = price + spread if direction == "BUY" else price - spread
        if direction == "BUY":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        lot = self._calc_lot(sl_dist, dd_pct)

        trade_id = len(self.trades) + len(self.open_positions)
        self.open_positions.append({
            "id": trade_id,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "original_lot": lot,
            "open_time": time,
            "score": score,
            "sl_dist": sl_dist,
            "tp_dist": tp_dist,
            "atr": atr,
            "vol_regime": vol_regime,
            "breakeven_done": False,
            "partial_closed": False,
        })

    def _manage_positions(self, high, low, close, time, bar_idx=0, cur_atr=None):
        cfg = self.cfg
        pip = cfg.PIP_VALUE
        for pos in list(self.open_positions):
            if pos["direction"] == "BUY":
                if low <= pos["sl"]:
                    self._close_position(pos, pos["sl"], time, "SL", bar_idx)
                    continue
                if high >= pos["tp"]:
                    self._close_position(pos, pos["tp"], time, "TP", bar_idx)
                    continue

                profit_dist = close - pos["entry"]

                # ★ Half-TP partial close
                if cfg.USE_PARTIAL_CLOSE and not pos["partial_closed"]:
                    half_tp_dist = pos["tp_dist"] * cfg.PARTIAL_TP_RATIO
                    if profit_dist >= half_tp_dist:
                        partial_lot = round(pos["lot"] * cfg.PARTIAL_CLOSE_RATIO, 2)
                        if partial_lot >= cfg.MIN_LOT:
                            # Close partial: realize profit on half
                            partial_pnl = profit_dist / pip * partial_lot * 1000
                            self.balance += partial_pnl
                            pos["lot"] -= partial_lot
                            pos["partial_closed"] = True
                            # Move SL to breakeven
                            pos["sl"] = pos["entry"] + pip
                            pos["breakeven_done"] = True
                            self.trades.append({
                                "open_time": pos["open_time"],
                                "close_time": time,
                                "direction": pos["direction"],
                                "entry": round(pos["entry"], 3),
                                "exit": round(close, 3),
                                "lot": partial_lot,
                                "pnl_pips": round(profit_dist / pip, 1),
                                "pnl_jpy": round(partial_pnl, 0),
                                "balance": round(self.balance, 0),
                                "reason": "HALF",
                                "score": pos["score"],
                            })

                # Breakeven (ATR-based)
                be_dist = pos.get("atr", cur_atr or 0.15) * cfg.BE_ATR_MULTI
                trail_step = pos.get("atr", cur_atr or 0.15) * cfg.TRAIL_ATR_MULTI

                if not pos["breakeven_done"] and profit_dist >= be_dist:
                    pos["sl"] = pos["entry"] + pip
                    pos["breakeven_done"] = True
                elif profit_dist >= be_dist * 1.5:
                    ns = close - trail_step
                    if ns > pos["sl"] + pip * 0.5:
                        pos["sl"] = ns
            else:
                if high >= pos["sl"]:
                    self._close_position(pos, pos["sl"], time, "SL", bar_idx)
                    continue
                if low <= pos["tp"]:
                    self._close_position(pos, pos["tp"], time, "TP", bar_idx)
                    continue

                profit_dist = pos["entry"] - close

                # Half-TP
                if cfg.USE_PARTIAL_CLOSE and not pos["partial_closed"]:
                    half_tp_dist = pos["tp_dist"] * cfg.PARTIAL_TP_RATIO
                    if profit_dist >= half_tp_dist:
                        partial_lot = round(pos["lot"] * cfg.PARTIAL_CLOSE_RATIO, 2)
                        if partial_lot >= cfg.MIN_LOT:
                            partial_pnl = profit_dist / pip * partial_lot * 1000
                            self.balance += partial_pnl
                            pos["lot"] -= partial_lot
                            pos["partial_closed"] = True
                            pos["sl"] = pos["entry"] - pip
                            pos["breakeven_done"] = True
                            self.trades.append({
                                "open_time": pos["open_time"],
                                "close_time": time,
                                "direction": pos["direction"],
                                "entry": round(pos["entry"], 3),
                                "exit": round(close, 3),
                                "lot": partial_lot,
                                "pnl_pips": round(profit_dist / pip, 1),
                                "pnl_jpy": round(partial_pnl, 0),
                                "balance": round(self.balance, 0),
                                "reason": "HALF",
                                "score": pos["score"],
                            })

                be_dist = pos.get("atr", cur_atr or 0.15) * cfg.BE_ATR_MULTI
                trail_step = pos.get("atr", cur_atr or 0.15) * cfg.TRAIL_ATR_MULTI

                if not pos["breakeven_done"] and profit_dist >= be_dist:
                    pos["sl"] = pos["entry"] - pip
                    pos["breakeven_done"] = True
                elif profit_dist >= be_dist * 1.5:
                    ns = close + trail_step
                    if ns < pos["sl"] - pip * 0.5:
                        pos["sl"] = ns

    def _close_position(self, pos, exit_price, time, reason, bar_idx=0):
        cfg = self.cfg
        pip = cfg.PIP_VALUE

        if reason == "SL" and bar_idx > 0:
            self.cooldown_until = bar_idx + cfg.COOLDOWN_BARS

        pnl_pips = ((exit_price - pos["entry"]) if pos["direction"] == "BUY"
                     else (pos["entry"] - exit_price)) / pip
        pnl_jpy = pnl_pips * pos["lot"] * 1000

        self.balance += pnl_jpy
        self.peak_balance = max(self.peak_balance, self.balance)

        self.trades.append({
            "open_time": pos["open_time"],
            "close_time": time,
            "direction": pos["direction"],
            "entry": round(pos["entry"], 3),
            "exit": round(exit_price, 3),
            "lot": pos["lot"],
            "pnl_pips": round(pnl_pips, 1),
            "pnl_jpy": round(pnl_jpy, 0),
            "balance": round(self.balance, 0),
            "reason": reason,
            "score": pos["score"],
        })
        self.open_positions.remove(pos)

    def _unrealized_pnl(self, price):
        pip = self.cfg.PIP_VALUE
        return sum((price - p["entry"] if p["direction"] == "BUY" else p["entry"] - price)
                   / pip * p["lot"] * 1000 for p in self.open_positions)

    def get_report(self):
        if not self.trades:
            return {"error": "No trades"}
        df = pd.DataFrame(self.trades)
        wins = df[df["pnl_pips"] > 0]
        losses = df[df["pnl_pips"] <= 0]
        total_pnl = df["pnl_jpy"].sum()
        win_rate = len(wins) / len(df) * 100

        avg_win = wins["pnl_pips"].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses["pnl_pips"].mean()) if len(losses) > 0 else 0
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

        reason_stats = df.groupby("reason").agg(
            count=("pnl_jpy", "count"),
            pnl=("pnl_jpy", "sum")
        )

        return {
            "Period": f"{df['open_time'].iloc[0]} ~ {df['close_time'].iloc[-1]}",
            "Initial": f"{self.cfg.INITIAL_BALANCE:,.0f} JPY",
            "Final": f"{self.balance:,.0f} JPY",
            "P&L": f"{total_pnl:+,.0f} JPY",
            "Return": f"{(self.balance / self.cfg.INITIAL_BALANCE - 1) * 100:+.1f}%",
            "Trades": len(df),
            "WinRate": f"{win_rate:.1f}% ({len(wins)}W/{len(losses)}L)",
            "AvgWin": f"{avg_win:.1f}pips ({avg_win_jpy:+,.0f} JPY)",
            "AvgLoss": f"{avg_loss:.1f}pips ({avg_loss_jpy:,.0f} JPY)",
            "RR": f"1:{avg_win/avg_loss:.2f}" if avg_loss > 0 else "N/A",
            "PF": f"{pf:.2f}" if pf != float("inf") else "inf",
            "MaxDD": f"{max_dd:.1f}% ({max_dd_jpy:,.0f} JPY)",
            "Monthly": f"{pm}/{tm} ({pm/tm*100:.0f}%)" if tm > 0 else "N/A",
            "monthly_detail": monthly.to_dict(),
            "reason_stats": reason_stats.to_dict(),
        }


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    cfg = USDJPYConfig()
    h4, h1, m15 = fetch_usdjpy_data(months=12)
    if m15 is None:
        print("Data fetch failed")
        exit()

    bt = USDJPYBacktester(cfg)
    bt.run(h4, h1, m15)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print("AntigravityMTF EA [USDJPY] v2.0 Backtest (1 year)")
        print("=" * 60)
        for k, v in rpt.items():
            if k == "monthly_detail":
                print(f"\nMonthly P&L:")
                for m, p in v.items():
                    bar = "#" * max(1, int(abs(p) / 2000))
                    icon = "+" if p > 0 else "-"
                    print(f"  {m}: [{icon}] {p:+,.0f} JPY {bar}")
            elif k == "reason_stats":
                print(f"\nExit Reasons:")
                counts = v.get("count", {})
                pnls = v.get("pnl", {})
                for reason in counts:
                    print(f"  {reason}: {int(counts[reason])}x / {pnls[reason]:+,.0f} JPY")
            else:
                print(f"  {k}: {v}")

        print(f"\nLast 10 trades:")
        print(f"  {'Time':<20} {'Dir':<5} {'Entry':>9} {'Exit':>9} {'Lot':>5} {'PnL(pip)':>8} {'PnL(JPY)':>10} {'Balance':>12} {'Reason':<6}")
        print("  " + "-" * 95)
        for t in bt.trades[-10:]:
            print(f"  {str(t['open_time'])[:19]:<20} {t['direction']:<5} {t['entry']:>9.3f} {t['exit']:>9.3f} {t['lot']:>5.2f} {t['pnl_pips']:>8.1f} {t['pnl_jpy']:>+10,.0f} {t['balance']:>12,.0f} {t['reason']:<6}")
    else:
        print("No trades generated")
