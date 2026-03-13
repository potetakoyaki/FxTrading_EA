"""
下落トレンド耐性テスト
- Gold: 2022年4月-10月 ($2050→$1620 の大暴落)
- USDJPY: 2022年10月-2023年1月 (BOJ介入 150→127)
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# Common Indicators
# ============================================================
def calc_sma(s, p): return s.rolling(window=p, min_periods=p).mean()
def calc_ema(s, p): return s.ewm(span=p, adjust=False).mean()

def calc_rsi(series, period):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calc_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

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

def calc_bb(series, period, deviation):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    return sma + deviation * std, sma, sma - deviation * std

def calc_channel_signal(close_series, lookback=40):
    if len(close_series) < lookback + 1: return 0
    y = close_series[-(lookback+1):-1].values
    if len(y) < lookback: return 0
    x = np.arange(lookback)
    slope, intercept = np.polyfit(x, y, 1)
    predicted = intercept + slope * x
    std = np.std(y - predicted)
    upper = predicted[-1] + 2 * std
    lower = predicted[-1] - 2 * std
    if upper == lower: return 0
    pos = (y[-1] - lower) / (upper - lower)
    if pos < 0.2 and slope > 0: return 1
    if pos > 0.8 and slope < 0: return -1
    return 0

def calc_ichimoku(df, tenkan_p, kijun_p, senkou_b_p):
    high, low = df["High"], df["Low"]
    tenkan = (high.rolling(tenkan_p).max() + low.rolling(tenkan_p).min()) / 2
    kijun = (high.rolling(kijun_p).max() + low.rolling(kijun_p).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(kijun_p)
    senkou_b = ((high.rolling(senkou_b_p).max() + low.rolling(senkou_b_p).min()) / 2).shift(kijun_p)
    return tenkan, kijun, senkou_a, senkou_b


# ============================================================
# Data: Generate H1/M15 from H1 or daily
# ============================================================
def fetch_data(symbol, start_date, end_date, extra_days=120):
    """Fetch H1 data (or daily fallback) for backtest"""
    start_ext = start_date - timedelta(days=extra_days)
    t = yf.Ticker(symbol)

    # Try H1 first
    h1 = t.history(start=start_ext, end=end_date, interval="1h")
    if len(h1) > 200:
        print(f"  H1: {len(h1)} bars")
        return h1, "H1"

    # Fallback to daily
    daily = t.history(start=start_ext, end=end_date, interval="1d")
    if daily.empty:
        return None, None
    print(f"  Daily: {len(daily)} bars (H1 unavailable, generating pseudo-H1)")

    # Generate pseudo-H1 from daily (6 bars per day)
    h1_list = []
    for idx, row in daily.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        # 6 segments per day simulating H1 bars
        mid = (o + c) / 2
        patterns = [
            (o,           max(o, mid)*1.001, min(o, mid)*0.999, mid),
            (mid,         h,                 max(mid, (h+l)/2)*0.999, (h+mid)/2),
            ((h+mid)/2,   max((h+mid)/2, h*0.999), (h+l)/2,  (h+l)/2),
            ((h+l)/2,     max((h+l)/2, (h+l)/2*1.001), l,     (l+mid)/2),
            ((l+mid)/2,   max((l+mid)/2, mid), min(l, (l+mid)/2), mid),
            (mid,         max(mid, c)*1.001, min(mid, c)*0.999, c),
        ]
        for j, (so, sh, sl, sc) in enumerate(patterns):
            ts = idx + timedelta(hours=j * 4)
            h1_list.append({"Open": so, "High": sh, "Low": sl, "Close": sc, "Volume": row.get("Volume", 0)/6, "time": ts})

    h1 = pd.DataFrame(h1_list).set_index("time")
    return h1, "pseudo-H1"


def prepare_mtf(h1_df, start_date):
    """Generate H4 and M15 from H1, filter to start_date"""
    h4_df = h1_df.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna()

    m15_list = []
    for idx, row in h1_df.iterrows():
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
    cutoff_ts = pd.Timestamp(start_date, tz=m15_df.index.tz) if m15_df.index.tz else pd.Timestamp(start_date)
    m15_df = m15_df[m15_df.index >= cutoff_ts]

    return h4_df, m15_df


# ============================================================
# Gold/USDJPY Antigravity v2.0 Backtester (unified)
# ============================================================
class AntigravityBacktester:
    def __init__(self, symbol_type, initial_balance=100_000):
        self.symbol_type = symbol_type  # "GOLD" or "USDJPY"
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.equity_curve = []
        self.trades = []
        self.open_positions = []
        self.peak_balance = initial_balance
        self.cooldown_until = 0

        if symbol_type == "GOLD":
            self.risk_pct = 0.3
            self.sl_atr_multi = 1.5
            self.tp_atr_multi = 3.5
            self.be_atr_multi = 1.5
            self.trail_atr_multi = 1.0
            self.min_score = 6
            self.point = 0.01
            self.contract_size = 100
            self.pip_value = None  # not used for gold
            self.usd_jpy = 140.0  # 2022 rate
        else:
            self.risk_pct = 0.5
            self.sl_atr_multi = 1.5
            self.tp_atr_multi = 3.0
            self.be_atr_multi = 1.5
            self.trail_atr_multi = 1.0
            self.min_score = 7
            self.point = None
            self.pip_value = 0.01
            self.usd_jpy = None

    def run(self, h4_df, h1_df, m15_df):
        h4_df = h4_df.copy(); h1_df = h1_df.copy(); m15_df = m15_df.copy()

        h4_df["ma_fast"] = calc_sma(h4_df["Close"], 20)
        h4_df["ma_slow"] = calc_sma(h4_df["Close"], 50)
        h4_df["adx"], h4_df["plus_di"], h4_df["minus_di"] = calc_adx(h4_df["High"], h4_df["Low"], h4_df["Close"], 14)

        h1_df["ma_fast"] = calc_ema(h1_df["Close"], 10)
        h1_df["ma_slow"] = calc_ema(h1_df["Close"], 30)
        h1_df["rsi"] = calc_rsi(h1_df["Close"], 14)
        h1_df["bb_upper"], h1_df["bb_mid"], h1_df["bb_lower"] = calc_bb(h1_df["Close"], 20, 2.0)

        m15_df["ma_fast"] = calc_ema(m15_df["Close"], 5)
        m15_df["ma_slow"] = calc_ema(m15_df["Close"], 20)
        m15_df["atr"] = calc_atr(m15_df["High"], m15_df["Low"], m15_df["Close"], 14)
        m15_df["atr_avg"] = m15_df["atr"].rolling(window=50).mean()

        total_bars = len(m15_df)

        for i in range(100, total_bars):
            ct = m15_df.index[i]
            cc = m15_df["Close"].iloc[i]
            ch = m15_df["High"].iloc[i]
            cl = m15_df["Low"].iloc[i]
            cur_atr = m15_df["atr"].iloc[i]
            cur_atr_avg = m15_df["atr_avg"].iloc[i]

            self._manage_positions(ch, cl, cc, ct, i, cur_atr)

            if self.balance > self.peak_balance: self.peak_balance = self.balance
            dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0

            hour = ct.hour if hasattr(ct, "hour") else 12
            if hour < 8 or hour >= 22: continue
            if len(self.open_positions) >= 1: continue
            if i < self.cooldown_until: continue

            if pd.isna(cur_atr) or pd.isna(cur_atr_avg) or cur_atr_avg <= 0: continue
            vol_ratio = cur_atr / cur_atr_avg
            if vol_ratio < 0.7: continue  # low vol skip
            vol_regime = 2 if vol_ratio > 1.5 else 1

            h4_mask = h4_df.index <= ct
            h1_mask = h1_df.index <= ct
            if h4_mask.sum() < 2 or h1_mask.sum() < 4: continue
            h4_row = h4_df[h4_mask].iloc[-1]
            h1_curr = h1_df[h1_mask].iloc[-1]
            h1_prev = h1_df[h1_mask].iloc[-2]
            m15_curr = m15_df.iloc[i]
            m15_prev = m15_df.iloc[i - 1]

            buy_s, sell_s = 0, 0

            # H4 Trend (3pt)
            if pd.notna(h4_row.get("adx")) and h4_row["adx"] >= 20:
                if h4_row["ma_fast"] > h4_row["ma_slow"] and h4_row["plus_di"] > h4_row["minus_di"]: buy_s += 3
                elif h4_row["ma_fast"] < h4_row["ma_slow"] and h4_row["minus_di"] > h4_row["plus_di"]: sell_s += 3

            # H1 MA (2pt)
            if pd.notna(h1_curr["ma_fast"]) and pd.notna(h1_curr["ma_slow"]):
                if h1_curr["ma_fast"] > h1_curr["ma_slow"]: buy_s += 2
                elif h1_curr["ma_fast"] < h1_curr["ma_slow"]: sell_s += 2

            # H1 RSI (1pt)
            if pd.notna(h1_curr["rsi"]):
                r = h1_curr["rsi"]
                if 40 < r < 60: buy_s += 1; sell_s += 1
                elif 60 <= r < 65: buy_s += 1
                elif 35 < r <= 40: sell_s += 1

            # H1 BB (1pt)
            if pd.notna(h1_curr.get("bb_upper")) and pd.notna(h1_curr.get("bb_lower")):
                bw = h1_curr["bb_upper"] - h1_curr["bb_lower"]
                if bw > 0:
                    bp = (h1_curr["Close"] - h1_curr["bb_lower"]) / bw
                    if bp < 0.2 and h1_curr["Close"] > h1_prev["Close"]: buy_s += 1
                    if bp > 0.8 and h1_curr["Close"] < h1_prev["Close"]: sell_s += 1

            # M15 cross (2pt)
            if pd.notna(m15_curr["ma_fast"]) and pd.notna(m15_curr["ma_slow"]):
                fa = m15_curr["ma_fast"] > m15_curr["ma_slow"]
                pfa = m15_prev["ma_fast"] > m15_prev["ma_slow"] if pd.notna(m15_prev["ma_fast"]) else None
                if fa and pfa is False: buy_s += 2
                elif not fa and pfa is True: sell_s += 2

            # Channel (1pt)
            cs = calc_channel_signal(h1_df[h1_mask]["Close"], 40)
            if cs == 1: buy_s += 1
            elif cs == -1: sell_s += 1

            # Momentum (1pt)
            if i >= 3:
                c1, c3 = m15_df["Close"].iloc[i], m15_df["Close"].iloc[i-2]
                thr = cur_atr * 0.1 if self.symbol_type == "GOLD" else self.pip_value * 3
                if c1 - c3 > thr: buy_s += 1
                elif c3 - c1 > thr: sell_s += 1

            # Session (1pt)
            if self.symbol_type == "GOLD":
                if (13 <= hour <= 16) or (8 <= hour <= 10): buy_s += 1; sell_s += 1
            else:
                if (0 <= hour < 8) or (8 <= hour < 11): buy_s += 1; sell_s += 1

            # Dynamic barrier
            min_s = self.min_score
            if dd >= 20: min_s = 10
            elif dd >= 15: min_s = 9
            elif dd >= 10: min_s = 8

            # Dynamic SL/TP
            sl_m = self.sl_atr_multi + (0.5 if vol_regime == 2 else 0)
            sl_dist = cur_atr * sl_m
            tp_dist = cur_atr * self.tp_atr_multi
            tp_dist = max(sl_dist * 1.5, tp_dist)

            if buy_s >= min_s and buy_s > sell_s:
                self._open("BUY", cc, ct, buy_s, dd, sl_dist, tp_dist, cur_atr)
            elif sell_s >= min_s and sell_s > buy_s:
                self._open("SELL", cc, ct, sell_s, dd, sl_dist, tp_dist, cur_atr)

            self.equity_curve.append({"time": ct, "equity": self.balance + self._unr(cc)})

        for pos in list(self.open_positions):
            self._close_pos(pos, m15_df["Close"].iloc[-1], m15_df.index[-1], "END", total_bars-1)

    def _open(self, d, price, time, score, dd, sl_dist, tp_dist, atr):
        spread_adj = 0
        if self.symbol_type == "GOLD":
            spread_adj = 25 * self.point * 0.5
        else:
            spread_adj = 1.5 * self.pip_value * 0.5

        entry = price + spread_adj if d == "BUY" else price - spread_adj
        sl = entry - sl_dist if d == "BUY" else entry + sl_dist
        tp = entry + tp_dist if d == "BUY" else entry - tp_dist

        lot = self._calc_lot(sl_dist, dd)
        self.open_positions.append({
            "direction": d, "entry": entry, "sl": sl, "tp": tp,
            "lot": lot, "open_time": time, "score": score,
            "sl_dist": sl_dist, "tp_dist": tp_dist, "atr": atr,
            "breakeven_done": False, "partial_done": False,
        })

    def _calc_lot(self, sl_dist, dd):
        rpct = self.risk_pct
        if dd >= 6.0: rpct *= 0.25
        elif dd >= 2.5: rpct *= 0.5
        risk_amount = self.balance * rpct / 100.0

        if self.symbol_type == "GOLD":
            loss_per_lot = sl_dist * self.contract_size * self.usd_jpy
        else:
            loss_per_lot = sl_dist * 100_000

        if loss_per_lot <= 0: return 0.01
        lot = risk_amount / loss_per_lot
        return max(0.01, min(0.5, round(lot, 2)))

    def _manage_positions(self, h, l, c, time, idx, atr):
        for pos in list(self.open_positions):
            if pos["direction"] == "BUY":
                if l <= pos["sl"]: self._close_pos(pos, pos["sl"], time, "SL", idx); continue
                if h >= pos["tp"]: self._close_pos(pos, pos["tp"], time, "TP", idx); continue
                pd_ = c - pos["entry"]
                # Partial close
                if not pos["partial_done"] and pd_ >= pos["tp_dist"] * 0.5:
                    plot = round(pos["lot"] * 0.5, 2)
                    if plot >= 0.01:
                        self._record_partial(pos, c, time, plot, pd_)
                        pos["lot"] -= plot; pos["partial_done"] = True
                        pos["sl"] = pos["entry"]; pos["breakeven_done"] = True
                # BE/Trail
                be = pos["atr"] * self.be_atr_multi
                if not pos["breakeven_done"] and pd_ >= be:
                    pos["sl"] = pos["entry"]; pos["breakeven_done"] = True
                elif pd_ >= be * 1.5:
                    ns = c - pos["atr"] * self.trail_atr_multi
                    if ns > pos["sl"]: pos["sl"] = ns
            else:
                if h >= pos["sl"]: self._close_pos(pos, pos["sl"], time, "SL", idx); continue
                if l <= pos["tp"]: self._close_pos(pos, pos["tp"], time, "TP", idx); continue
                pd_ = pos["entry"] - c
                if not pos["partial_done"] and pd_ >= pos["tp_dist"] * 0.5:
                    plot = round(pos["lot"] * 0.5, 2)
                    if plot >= 0.01:
                        self._record_partial(pos, c, time, plot, pd_)
                        pos["lot"] -= plot; pos["partial_done"] = True
                        pos["sl"] = pos["entry"]; pos["breakeven_done"] = True
                be = pos["atr"] * self.be_atr_multi
                if not pos["breakeven_done"] and pd_ >= be:
                    pos["sl"] = pos["entry"]; pos["breakeven_done"] = True
                elif pd_ >= be * 1.5:
                    ns = c + pos["atr"] * self.trail_atr_multi
                    if ns < pos["sl"]: pos["sl"] = ns

    def _record_partial(self, pos, price, time, lot, pd_):
        if self.symbol_type == "GOLD":
            pnl_usd = pd_ * self.contract_size * lot
            pnl = pnl_usd * self.usd_jpy
        else:
            pnl = pd_ / self.pip_value * lot * 1000
        self.balance += pnl
        self.trades.append({"time": time, "dir": pos["direction"], "lot": lot,
                           "pnl": round(pnl, 0), "reason": "HALF", "bal": round(self.balance, 0)})

    def _close_pos(self, pos, exit_p, time, reason, idx):
        if reason == "SL": self.cooldown_until = idx + 16
        if pos["direction"] == "BUY":
            pd_ = exit_p - pos["entry"]
        else:
            pd_ = pos["entry"] - exit_p

        if self.symbol_type == "GOLD":
            pnl = pd_ * self.contract_size * pos["lot"] * self.usd_jpy
        else:
            pnl = pd_ / self.pip_value * pos["lot"] * 1000

        self.balance += pnl
        self.peak_balance = max(self.peak_balance, self.balance)
        self.trades.append({"time": time, "dir": pos["direction"], "lot": pos["lot"],
                           "pnl": round(pnl, 0), "reason": reason, "bal": round(self.balance, 0)})
        self.open_positions.remove(pos)

    def _unr(self, price):
        total = 0
        for p in self.open_positions:
            pd_ = (price - p["entry"]) if p["direction"] == "BUY" else (p["entry"] - price)
            if self.symbol_type == "GOLD":
                total += pd_ * self.contract_size * p["lot"] * self.usd_jpy
            else:
                total += pd_ / self.pip_value * p["lot"] * 1000
        return total

    def report(self, label):
        if not self.trades:
            print(f"  {label}: No trades")
            return
        df = pd.DataFrame(self.trades)
        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]
        total_pnl = df["pnl"].sum()
        wr = len(wins) / len(df) * 100 if len(df) > 0 else 0
        pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and losses["pnl"].sum() != 0 else float("inf")

        eq = pd.DataFrame(self.equity_curve) if self.equity_curve else pd.DataFrame()
        max_dd = 0
        if len(eq) > 0:
            eq["peak"] = eq["equity"].cummax()
            eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
            max_dd = eq["dd"].max()

        ret = (self.balance / self.initial_balance - 1) * 100

        # Reason breakdown
        reason_pnl = df.groupby("reason")["pnl"].agg(["count", "sum"])

        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(f"  Initial:  {self.initial_balance:>12,.0f} JPY")
        print(f"  Final:    {self.balance:>12,.0f} JPY")
        print(f"  Return:   {ret:>+10.1f}%")
        print(f"  Trades:   {len(df):>10}")
        print(f"  WinRate:  {wr:>10.1f}% ({len(wins)}W/{len(losses)}L)")
        print(f"  PF:       {pf:>10.2f}")
        print(f"  MaxDD:    {max_dd:>10.1f}%")
        print(f"  ---")
        for reason, row in reason_pnl.iterrows():
            print(f"  {reason:>8}: {int(row['count']):>4}x  {row['sum']:>+12,.0f} JPY")
        return {"return": ret, "trades": len(df), "wr": wr, "pf": pf, "dd": max_dd}


# ============================================================
# ThreeLayer Backtester (simplified for bear market test)
# ============================================================
class UTBot:
    def __init__(self, key):
        self.key = key
        self.trail = 0.0

    def update(self, c1, c2, atr, key=None):
        k = key or self.key
        nl = atr * k
        pt = self.trail
        if c1 > pt and c2 > pt: self.trail = max(pt, c1 - nl)
        elif c1 < pt and c2 < pt: self.trail = min(pt, c1 + nl)
        elif c1 > pt: self.trail = c1 - nl
        else: self.trail = c1 + nl
        return (c1 > self.trail and c2 <= pt), (c1 < self.trail and c2 >= pt)


def detect_swing_points(highs, lows, swing_len):
    sh, sl = [], []
    n = len(highs)
    for i in range(swing_len, n - swing_len):
        is_sh = all(highs[i] > highs[i-j] and highs[i] > highs[i+j] for j in range(1, swing_len+1))
        if is_sh: sh.append((i, highs[i]))
        is_sl = all(lows[i] < lows[i-j] and lows[i] < lows[i+j] for j in range(1, swing_len+1))
        if is_sl: sl.append((i, lows[i]))
    return sh, sl


def check_smc(highs, lows, closes, lookback, swing_len):
    n = len(highs)
    if n < lookback + swing_len + 1: return False, False
    start = max(0, n - lookback - swing_len - 1)
    shs, sls = detect_swing_points(highs[start:n], lows[start:n], swing_len)
    if len(shs) < 2 or len(sls) < 2: return False, False
    sh0, sh1 = shs[-1][1], shs[-2][1]
    sl0, sl1 = sls[-1][1], sls[-2][1]
    lc = closes[-1]
    bear_choch = sh0 > sh1 and lc < sl0
    bull_choch = sl0 < sl1 and lc > sh0
    return (not bear_choch), (not bull_choch)


class ThreeLayerBearTest:
    def __init__(self, initial=100_000):
        self.balance = initial
        self.initial = initial
        self.trades = []
        self.equity_curve = []
        self.open_pos = None
        self.peak = initial
        self.cooldown = 0

    def run(self, df):
        df = df.copy()
        df["tenkan"], df["kijun"], df["senkou_a"], df["senkou_b"] = calc_ichimoku(df, 9, 26, 52)
        df["rsi"] = calc_rsi(df["Close"], 14)
        df["atr"] = calc_atr(df["High"], df["Low"], df["Close"], 14)
        df["ut_atr"] = calc_atr(df["High"], df["Low"], df["Close"], 10)
        df["atr_avg"] = df["atr"].rolling(window=50).mean()

        warmup = 110
        ut = UTBot(2.0)
        for i in range(1, warmup):
            c1, c2 = df["Close"].iloc[i], df["Close"].iloc[i-1]
            ua = df["ut_atr"].iloc[i]
            if pd.notna(ua) and ua > 0: ut.update(c1, c2, ua)

        for i in range(warmup, len(df)):
            ct = df.index[i]
            cc, ch, cl = df["Close"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i]
            cp = df["Close"].iloc[i-1]

            # Manage
            if self.open_pos:
                p = self.open_pos
                if p["dir"] == "BUY":
                    if cl <= p["sl"]: self._close(p["sl"], ct, "SL", i); continue
                    if ch >= p["tp"]: self._close(p["tp"], ct, "TP", i); continue
                    pd_ = cc - p["entry"]
                    if not p["pc"] and pd_ >= p["tp_d"] * 0.5:
                        pl = round(p["lot"]*0.5, 2)
                        if pl >= 0.01:
                            pnl = pd_ * 100 * pl * 140
                            self.balance += pnl; self.trades.append({"pnl": round(pnl,0), "reason": "HALF"})
                            p["lot"] -= pl; p["pc"] = True; p["sl"] = p["entry"]
                else:
                    if ch >= p["sl"]: self._close(p["sl"], ct, "SL", i); continue
                    if cl <= p["tp"]: self._close(p["tp"], ct, "TP", i); continue
                    pd_ = p["entry"] - cc
                    if not p["pc"] and pd_ >= p["tp_d"] * 0.5:
                        pl = round(p["lot"]*0.5, 2)
                        if pl >= 0.01:
                            pnl = pd_ * 100 * pl * 140
                            self.balance += pnl; self.trades.append({"pnl": round(pnl,0), "reason": "HALF"})
                            p["lot"] -= pl; p["pc"] = True; p["sl"] = p["entry"]

            sa, sb = df["senkou_a"].iloc[i], df["senkou_b"].iloc[i]
            rsi_v, atr_v = df["rsi"].iloc[i], df["atr"].iloc[i]
            atr_avg_v = df["atr_avg"].iloc[i]
            ut_atr_v = df["ut_atr"].iloc[i]

            if any(pd.isna(x) for x in [sa, sb, rsi_v, atr_v, ut_atr_v, atr_avg_v]): continue
            if i < self.cooldown: continue

            # Session
            hour = ct.hour if hasattr(ct, "hour") else 12
            if hour >= 22 or hour < 2: ut.update(cc, cp, ut_atr_v); continue

            # Vol regime
            vr = atr_v / atr_avg_v if atr_avg_v > 0 else 1
            if vr < 0.7: ut.update(cc, cp, ut_atr_v); continue
            vol = 2 if vr > 1.5 else 1

            cu, cl_ = max(sa, sb), min(sa, sb)
            ab = cc > cu
            as_ = cc < cl_
            if not ab and not as_: ut.update(cc, cp, ut_atr_v); continue

            ut_key = 2.5 if vol == 2 else None
            ub, us = ut.update(cc, cp, ut_atr_v, key=ut_key)

            # SMC
            ls = max(0, i - 30 - 5 - 1)
            smcb, smcs = check_smc(df["High"].iloc[ls:i+1].values, df["Low"].iloc[ls:i+1].values,
                                    df["Close"].iloc[ls:i+1].values, 30, 5)

            rb = rsi_v < 70; rs = rsi_v > 30; aa = atr_v >= 2.0

            # Momentum
            mb = ms = True
            if i >= 2:
                ci2 = df["Close"].iloc[i-2]
                thr = atr_v * 0.1
                mb = (cc - ci2) > -thr
                ms = (ci2 - cc) > -thr

            if self.open_pos is None:
                sl_m = 2.0 + (0.5 if vol == 2 else 0)
                sl_d = atr_v * sl_m
                tp_d = atr_v * 4.0

                if ab and ub and smcb and rb and aa and mb:
                    lot = self._lot(sl_d)
                    self.open_pos = {"dir": "BUY", "entry": cc, "sl": cc-sl_d, "tp": cc+tp_d,
                                    "lot": lot, "tp_d": tp_d, "pc": False}
                elif as_ and us and smcs and rs and aa and ms:
                    lot = self._lot(sl_d)
                    self.open_pos = {"dir": "SELL", "entry": cc, "sl": cc+sl_d, "tp": cc-tp_d,
                                    "lot": lot, "tp_d": tp_d, "pc": False}

            if self.balance > self.peak: self.peak = self.balance
            self.equity_curve.append({"time": ct, "equity": self.balance})

        if self.open_pos:
            self._close(df["Close"].iloc[-1], df.index[-1], "END", len(df)-1)

    def _lot(self, sl_d):
        risk = self.balance * 1.0 / 100
        loss = sl_d * 100 * 140
        if loss <= 0: return 0.01
        return max(0.01, min(5.0, round(risk / loss, 2)))

    def _close(self, ep, time, reason, idx):
        p = self.open_pos
        if reason == "SL": self.cooldown = idx + 2
        pd_ = (ep - p["entry"]) if p["dir"] == "BUY" else (p["entry"] - ep)
        pnl = pd_ * 100 * p["lot"] * 140
        self.balance += pnl; self.peak = max(self.peak, self.balance)
        self.trades.append({"pnl": round(pnl, 0), "reason": reason})
        self.open_pos = None

    def report(self, label):
        if not self.trades:
            print(f"  {label}: No trades"); return
        df = pd.DataFrame(self.trades)
        w = df[df["pnl"] > 0]; l = df[df["pnl"] <= 0]
        wr = len(w)/len(df)*100 if len(df) > 0 else 0
        pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 0
        eq = pd.DataFrame(self.equity_curve)
        mdd = 0
        if len(eq) > 0:
            eq["pk"] = eq["equity"].cummax()
            mdd = ((eq["pk"] - eq["equity"]) / eq["pk"] * 100).max()
        ret = (self.balance / self.initial - 1) * 100
        rp = df.groupby("reason")["pnl"].agg(["count","sum"])
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(f"  Initial:  {self.initial:>12,.0f} JPY")
        print(f"  Final:    {self.balance:>12,.0f} JPY")
        print(f"  Return:   {ret:>+10.1f}%")
        print(f"  Trades:   {len(df):>10}")
        print(f"  WinRate:  {wr:>10.1f}% ({len(w)}W/{len(l)}L)")
        print(f"  PF:       {pf:>10.2f}")
        print(f"  MaxDD:    {mdd:>10.1f}%")
        print(f"  ---")
        for reason, row in rp.iterrows():
            print(f"  {reason:>8}: {int(row['count']):>4}x  {row['sum']:>+12,.0f} JPY")
        return {"return": ret, "trades": len(df), "wr": wr, "pf": pf, "dd": mdd}


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print(" Bear Market Stress Test")
    print(" Gold: 2022/04 - 2022/10 ($2050 -> $1620)")
    print(" USDJPY: 2022/10 - 2023/01 (150 -> 127 BOJ)")
    print("=" * 60)

    # === Gold Bear Market ===
    print("\n[1] Gold Bear Market 2022...")
    gold_start = datetime(2022, 4, 1)
    gold_end = datetime(2022, 11, 1)
    g_h1, g_type = fetch_data("GC=F", gold_start, gold_end)
    if g_h1 is not None:
        g_h4, g_m15 = prepare_mtf(g_h1, gold_start)
        print(f"  H4: {len(g_h4)} / M15: {len(g_m15)} bars")
        first_p = g_m15["Close"].iloc[0]
        last_p = g_m15["Close"].iloc[-1]
        print(f"  Gold: ${first_p:.0f} -> ${last_p:.0f} ({(last_p/first_p-1)*100:+.1f}%)")

        # Gold Antigravity v2.0
        gb = AntigravityBacktester("GOLD")
        gb.run(g_h4, g_h1, g_m15)
        gb.report("Gold Antigravity v2.0 -- Bear Market 2022")

        # ThreeLayer v2.0
        print("\n[2] ThreeLayer Bear Market 2022...")
        tb = ThreeLayerBearTest()
        tb.run(g_h1)
        tb.report("ThreeLayer v2.0 -- Bear Market 2022")
    else:
        print("  Gold data fetch failed")

    # === USDJPY Sharp Drop (BOJ intervention) ===
    print("\n[3] USDJPY BOJ Intervention 2022...")
    jpy_start = datetime(2022, 10, 1)
    jpy_end = datetime(2023, 2, 1)
    j_h1, j_type = fetch_data("USDJPY=X", jpy_start, jpy_end)
    if j_h1 is not None:
        j_h4, j_m15 = prepare_mtf(j_h1, jpy_start)
        print(f"  H4: {len(j_h4)} / M15: {len(j_m15)} bars")
        first_p = j_m15["Close"].iloc[0]
        last_p = j_m15["Close"].iloc[-1]
        print(f"  USDJPY: {first_p:.1f} -> {last_p:.1f} ({(last_p/first_p-1)*100:+.1f}%)")

        jb = AntigravityBacktester("USDJPY")
        jb.run(j_h4, j_h1, j_m15)
        jb.report("USDJPY Antigravity v2.0 -- BOJ Crash 2022")
    else:
        print("  USDJPY data fetch failed")

    # === Summary ===
    print("\n" + "=" * 60)
    print(" Summary: Bear market resilience")
    print("=" * 60)
    print(" Normal market (2025):  Gold +218%, ThreeLayer +51%, USDJPY +6%")
    print(" Bear market results above show how EAs handle adverse conditions")
    print(" Key question: Do they SELL in downtrends or just lose on BUY signals?")
    print("=" * 60)
