"""
📊 EA バックテスター v2 (backtest_ea.py)
AntigravityMTF EA — 複利運用 + 高頻度版
DD 10%以内で10万円→100万円以上を目指す。
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# バックテスト設定
# ============================================================
class Config:
    SYMBOL = "USDJPY=X"
    INITIAL_BALANCE = 300_000
    RISK_PERCENT = 0.4          # 複利0.4%でDD10%以内
    SL_PIPS = 20
    TP_PIPS = 50                # RR 1:2.5
    TRAILING_START = 15
    TRAILING_STEP = 8
    BREAKEVEN_PIPS = 10
    MAX_POSITIONS = 1
    MIN_SCORE = 5               # 5/11
    MAX_SPREAD_PIPS = 3.0
    PIP_VALUE = 0.01
    MAX_DD_PERCENT = 6.0        # DD 6%以上でリスク1/4
    DD_HALF_RISK = 2.5          # DD 2.5%以上でリスク1/2
    COMPOUND = True
    MAX_LOT = 0.5

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
def calc_sma(s, p): return s.rolling(window=p, min_periods=p).mean()
def calc_ema(s, p): return s.ewm(span=p, adjust=False).mean()

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

def calc_bb(series, period, deviation):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    return sma + deviation * std, sma, sma - deviation * std

def calc_channel_signal(close_series, lookback=40):
    if len(close_series) < lookback:
        return 0
    y = close_series[-lookback:].values
    x = np.arange(lookback)
    slope, intercept = np.polyfit(x, y, 1)
    predicted = intercept + slope * x
    std = np.std(y - predicted)
    upper = predicted[-1] + 2 * std
    lower = predicted[-1] - 2 * std
    if upper == lower: return 0
    pos = (y[-1] - lower) / (upper - lower)
    if pos < 0.25 and slope > 0: return 1
    if pos > 0.75 and slope < 0: return -1
    return 0


# ============================================================
# バックテストエンジン
# ============================================================
class Backtester:
    def __init__(self, cfg):
        self.cfg = cfg
        self.balance = cfg.INITIAL_BALANCE
        self.equity_curve = []
        self.trades = []
        self.open_positions = []
        self.peak_balance = cfg.INITIAL_BALANCE

    def run(self, h4_df, h1_df, m15_df):
        cfg = self.cfg

        h4_df["ma_fast"] = calc_sma(h4_df["Close"], cfg.H4_MA_FAST)
        h4_df["ma_slow"] = calc_sma(h4_df["Close"], cfg.H4_MA_SLOW)
        h4_df["adx"], h4_df["plus_di"], h4_df["minus_di"] = calc_adx(
            h4_df["High"], h4_df["Low"], h4_df["Close"], cfg.H4_ADX_PERIOD)

        h1_df["ma_fast"] = calc_ema(h1_df["Close"], cfg.H1_MA_FAST)
        h1_df["ma_slow"] = calc_ema(h1_df["Close"], cfg.H1_MA_SLOW)
        h1_df["rsi"] = calc_rsi(h1_df["Close"], cfg.H1_RSI_PERIOD)
        h1_df["bb_upper"], h1_df["bb_mid"], h1_df["bb_lower"] = calc_bb(
            h1_df["Close"], cfg.H1_BB_PERIOD, cfg.H1_BB_DEV)
        # H1 モメンタム（直近3本の方向）
        h1_df["momentum"] = h1_df["Close"].diff(3)

        m15_df["ma_fast"] = calc_ema(m15_df["Close"], cfg.M15_MA_FAST)
        m15_df["ma_slow"] = calc_ema(m15_df["Close"], cfg.M15_MA_SLOW)

        total_bars = len(m15_df)
        print(f"📊 バックテスト開始: {m15_df.index[0].date()} → {m15_df.index[-1].date()}")
        print(f"   M15バー数: {total_bars:,}")
        print(f"   設定: Risk={cfg.RISK_PERCENT}% SL={cfg.SL_PIPS} TP={cfg.TP_PIPS} Score≥{cfg.MIN_SCORE} 複利={'ON' if cfg.COMPOUND else 'OFF'}")

        for i in range(100, total_bars):
            ct = m15_df.index[i]
            cc = m15_df["Close"].iloc[i]
            ch = m15_df["High"].iloc[i]
            cl = m15_df["Low"].iloc[i]

            self._manage_positions(ch, cl, cc, ct)

            # 動的リスクスケーリング（DDが深いほどリスクを下げるが完全には止めない）
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

            # ──── スコアリング ────
            buy_score = 0
            sell_score = 0

            # 1. H4 トレンド（3点）
            if pd.notna(h4_row.get("adx")) and h4_row["adx"] >= cfg.H4_ADX_THRESHOLD:
                if h4_row["ma_fast"] > h4_row["ma_slow"] and h4_row["plus_di"] > h4_row["minus_di"]:
                    buy_score += 3
                elif h4_row["ma_fast"] < h4_row["ma_slow"] and h4_row["minus_di"] > h4_row["plus_di"]:
                    sell_score += 3

            # 2. H1 MAクロス方向（2点） — 方向一致でもポイント付与
            if pd.notna(h1_curr["ma_fast"]) and pd.notna(h1_curr["ma_slow"]):
                if h1_curr["ma_fast"] > h1_curr["ma_slow"]:
                    buy_score += 2
                elif h1_curr["ma_fast"] < h1_curr["ma_slow"]:
                    sell_score += 2

            # 3. H1 RSI（1点）
            if pd.notna(h1_curr["rsi"]):
                if 40 < h1_curr["rsi"] < 65: buy_score += 1
                if 35 < h1_curr["rsi"] < 60: sell_score += 1

            # 4. H1 BB バウンス（1点）
            if pd.notna(h1_curr.get("bb_upper")) and pd.notna(h1_curr.get("bb_lower")):
                bw = h1_curr["bb_upper"] - h1_curr["bb_lower"]
                if bw > 0:
                    bp = (h1_curr["Close"] - h1_curr["bb_lower"]) / bw
                    if bp < 0.25 and h1_curr["Close"] > h1_prev["Close"]: buy_score += 1
                    if bp > 0.75 and h1_curr["Close"] < h1_prev["Close"]: sell_score += 1

            # 5. M15 MA方向一致（2点）— クロス直後でなくても方向一致でポイント
            if pd.notna(m15_curr["ma_fast"]) and pd.notna(m15_curr["ma_slow"]):
                fast_above = m15_curr["ma_fast"] > m15_curr["ma_slow"]
                prev_fast_above = m15_prev["ma_fast"] > m15_prev["ma_slow"] if pd.notna(m15_prev["ma_fast"]) else None

                if fast_above:
                    # クロス直後なら2点、方向一致なら1点
                    if prev_fast_above is False:
                        buy_score += 2
                    else:
                        buy_score += 1
                elif not fast_above:
                    if prev_fast_above is True:
                        sell_score += 2
                    else:
                        sell_score += 1

            # 6. チャネル回帰（1点）
            h1_closes = h1_df[h1_mask]["Close"]
            cs = calc_channel_signal(h1_closes, 40)
            if cs == 1: buy_score += 1
            elif cs == -1: sell_score += 1

            # 7. ★追加: H1 モメンタム（1点ボーナス, 合計11点満点）
            if pd.notna(h1_curr.get("momentum")):
                if h1_curr["momentum"] > 0: buy_score += 1
                elif h1_curr["momentum"] < 0: sell_score += 1

            # ──── エントリー ────
            if buy_score >= cfg.MIN_SCORE and buy_score > sell_score:
                self._open_trade("BUY", cc, ct, buy_score)
            elif sell_score >= cfg.MIN_SCORE and sell_score > buy_score:
                self._open_trade("SELL", cc, ct, sell_score)

            self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized_pnl(cc)})

        # 最終クローズ
        fc = m15_df["Close"].iloc[-1]
        for pos in list(self.open_positions):
            self._close_position(pos, fc, m15_df.index[-1], "期間終了")

        print(f"✅ バックテスト完了")

    def _calc_lot(self, dd_pct=0):
        cfg = self.cfg
        base = self.balance if cfg.COMPOUND else cfg.INITIAL_BALANCE

        # DDベースのリスクスケーリング
        risk_pct = cfg.RISK_PERCENT
        if dd_pct >= cfg.MAX_DD_PERCENT:
            risk_pct *= 0.25  # DD 9%以上: 1/4リスク
        elif dd_pct >= cfg.DD_HALF_RISK:
            risk_pct *= 0.5   # DD 5%以上: 1/2リスク

        risk_amount = base * risk_pct / 100
        risk_per_lot = cfg.SL_PIPS * cfg.PIP_VALUE * 100_000
        if risk_per_lot <= 0: return 0.1
        lot = risk_amount / risk_per_lot
        lot = max(0.1, min(cfg.MAX_LOT, round(lot, 2)))
        return lot

    def _open_trade(self, direction, price, time, score):
        cfg = self.cfg
        pip = cfg.PIP_VALUE
        spread = cfg.MAX_SPREAD_PIPS * pip

        entry = price + spread / 2 if direction == "BUY" else price - spread / 2
        if direction == "BUY":
            sl = entry - cfg.SL_PIPS * pip
            tp = entry + cfg.TP_PIPS * pip
        else:
            sl = entry + cfg.SL_PIPS * pip
            tp = entry - cfg.TP_PIPS * pip

        # ★ 動的スコア防壁（DDが深い時はパーフェクトなセットアップしか狙わない）
        current_min_score = cfg.MIN_ENTRY_SCORE
        dd_pct = ((self.peak_balance - self.balance) / self.peak_balance * 100) if self.peak_balance > 0 else 0
        
        if dd_pct >= 20.0:
            current_min_score = 9
        elif dd_pct >= 15.0:
            current_min_score = 8
        elif dd_pct >= 10.0:
            current_min_score = 7

        if score < current_min_score: return

        lot = self._calc_lot(dd_pct=dd_pct)
        self.open_positions.append({
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "open_time": time,
            "score": score,
            "breakeven_done": False
        })

    def _manage_positions(self, high, low, close, time):
        cfg = self.cfg
        pip = cfg.PIP_VALUE
        for pos in list(self.open_positions):
            if pos["direction"] == "BUY":
                if low <= pos["sl"]:
                    self._close_position(pos, pos["sl"], time, "SL"); continue
                if high >= pos["tp"]:
                    self._close_position(pos, pos["tp"], time, "TP"); continue
                profit = (close - pos["entry"]) / pip
                if not pos["breakeven_done"] and profit >= cfg.BREAKEVEN_PIPS:
                    pos["sl"] = pos["entry"] + 1 * pip
                    pos["breakeven_done"] = True
                elif profit >= cfg.TRAILING_START:
                    ns = close - cfg.TRAILING_STEP * pip
                    if ns > pos["sl"]: pos["sl"] = ns
            else:
                if high >= pos["sl"]:
                    self._close_position(pos, pos["sl"], time, "SL"); continue
                if low <= pos["tp"]:
                    self._close_position(pos, pos["tp"], time, "TP"); continue
                profit = (pos["entry"] - close) / pip
                if not pos["breakeven_done"] and profit >= cfg.BREAKEVEN_PIPS:
                    pos["sl"] = pos["entry"] - 1 * pip
                    pos["breakeven_done"] = True
                elif profit >= cfg.TRAILING_START:
                    ns = close + cfg.TRAILING_STEP * pip
                    if ns < pos["sl"]: pos["sl"] = ns

    def _close_position(self, pos, exit_price, time, reason):
        pip = self.cfg.PIP_VALUE
        pnl_pips = ((exit_price - pos["entry"]) if pos["direction"] == "BUY"
                     else (pos["entry"] - exit_price)) / pip
        pnl_jpy = pnl_pips * pos["lot"] * 1000

        self.balance += pnl_jpy
        self.peak_balance = max(self.peak_balance, self.balance)

        self.trades.append({
            "open_time": pos["open_time"], "close_time": time,
            "direction": pos["direction"],
            "entry": round(pos["entry"], 3), "exit": round(exit_price, 3),
            "lot": pos["lot"],
            "pnl_pips": round(pnl_pips, 1), "pnl_jpy": round(pnl_jpy, 0),
            "balance": round(self.balance, 0), "reason": reason, "score": pos["score"],
        })
        self.open_positions.remove(pos)

    def _unrealized_pnl(self, price):
        pip = self.cfg.PIP_VALUE
        return sum((price - p["entry"] if p["direction"] == "BUY" else p["entry"] - price)
                   / pip * p["lot"] * 1000 for p in self.open_positions)

    def get_report(self):
        if not self.trades: return {"error": "取引なし"}
        df = pd.DataFrame(self.trades)
        wins = df[df["pnl_pips"] > 0]
        losses = df[df["pnl_pips"] <= 0]
        total_pnl = df["pnl_jpy"].sum()
        win_rate = len(wins) / len(df) * 100

        avg_win = wins["pnl_pips"].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses["pnl_pips"].mean()) if len(losses) > 0 else 0
        pf = (wins["pnl_jpy"].sum() / abs(losses["pnl_jpy"].sum())) if len(losses) > 0 and losses["pnl_jpy"].sum() != 0 else float("inf")

        eq = pd.DataFrame(self.equity_curve)
        max_dd = 0
        if len(eq) > 0:
            eq["peak"] = eq["equity"].cummax()
            eq["dd"] = (eq["peak"] - eq["equity"]) / eq["peak"] * 100
            max_dd = eq["dd"].max()

        df["year"] = pd.to_datetime(df["close_time"]).dt.year
        yearly = df.groupby("year")["pnl_jpy"].sum()

        df["month"] = pd.to_datetime(df["close_time"]).dt.to_period("M")
        monthly = df.groupby("month")["pnl_jpy"].sum()
        pm = (monthly > 0).sum()
        tm = len(monthly)

        return {
            "期間": f"{df['open_time'].iloc[0]} ~ {df['close_time'].iloc[-1]}",
            "初期資金": f"{self.cfg.INITIAL_BALANCE:,.0f}円",
            "最終残高": f"{self.balance:,.0f}円",
            "総損益": f"{total_pnl:+,.0f}円",
            "リターン": f"{(self.balance / self.cfg.INITIAL_BALANCE - 1) * 100:+.1f}%",
            "取引回数": len(df),
            "勝率": f"{win_rate:.1f}%（{len(wins)}勝/{len(losses)}敗）",
            "平均勝ち": f"{avg_win:.1f} pips",
            "平均負け": f"{avg_loss:.1f} pips",
            "RR比": f"1:{avg_win/avg_loss:.2f}" if avg_loss > 0 else "N/A",
            "PF": f"{pf:.2f}" if pf != float("inf") else "∞",
            "最大DD": f"{max_dd:.1f}%",
            "月間勝率": f"{pm}/{tm} ({pm/tm*100:.0f}%)" if tm > 0 else "N/A",
            "年別": yearly.to_dict(),
        }


# ============================================================
# データ取得
# ============================================================
def fetch_data(symbol, years=10):
    print(f"📥 {symbol} 取得中（{years}年分）...")
    end = datetime.now()
    start = end - timedelta(days=years * 365)
    t = yf.Ticker(symbol)
    daily = t.history(start=start, end=end, interval="1d")
    if daily.empty:
        print("❌ 失敗"); return None, None, None

    print(f"   {len(daily)}日分（{daily.index[0].date()} ~ {daily.index[-1].date()}）")

    # 日足→疑似M15（1日4本に展開して複利でのバー数を増やす）
    m15 = []
    for idx, row in daily.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        for j, (so, sh, sl, sc) in enumerate([
            (o, max(o, (o+h)/2), min(o, o-(h-o)*0.2), (o+h)/2),
            ((o+h)/2, h, (h+l)/2, (h+c)/2),
            ((h+c)/2, max((h+c)/2, (h+c)/2*1.001), l, (l+c)/2),
            ((l+c)/2, max(c, (l+c)/2), min(c, (l+c)/2), c),
        ]):
            ts = idx + timedelta(hours=j * 4)
            m15.append({"Open": so, "High": sh, "Low": sl, "Close": sc, "time": ts})

    m15_df = pd.DataFrame(m15).set_index("time")
    h1_df = daily.copy()
    h4_df = daily.resample("W").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()

    return h4_df, h1_df, m15_df


if __name__ == "__main__":
    cfg = Config()
    h4, h1, m15 = fetch_data(cfg.SYMBOL, 10)
    if m15 is None:
        exit()

    bt = Backtester(cfg)
    bt.run(h4, h1, m15)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print("📊 AntigravityMTF EA バックテスト結果（10年間・複利版）")
        print("=" * 60)
        for k, v in rpt.items():
            if k == "年別":
                print(f"\n📅 年別損益:")
                for y, p in v.items():
                    bar = "█" * max(1, int(abs(p) / 5000))
                    icon = "🟢" if p > 0 else "🔴"
                    print(f"  {y}: {icon} {p:+,.0f}円 {bar}")
            else:
                print(f"  {k}: {v}")
