"""
ThreeLayerEA バックテスター v2.0 — XAUUSD 3層フィルター戦略
第1層: 一目均衡表（環境認識）
第2層: UT Bot Alerts + SMC（エントリー）
第3層: RSI + ATR（フィルター）
v2.0: ボラティリティレジーム検出, セッションフィルタ, モメンタムチェック, 部分決済
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")


class ThreeLayerConfig:
    SYMBOL = "GC=F"
    INITIAL_BALANCE = 100_000  # 10万円

    # 第1層: 一目均衡表
    ICHI_TENKAN = 9
    ICHI_KIJUN = 26
    ICHI_SENKOU_B = 52

    # 第2層: UT Bot
    UT_KEY_VALUE = 2.0         # ★偽シグナル削減
    UT_HIGH_VOL_KEY = 2.5      # ★高ボラ時キー値
    UT_ATR_PERIOD = 10

    # 第2層: SMC
    SMC_LOOKBACK = 30
    SMC_SWING_LEN = 5

    # 第3層: RSI + ATR
    RSI_PERIOD = 14
    RSI_OB = 70.0
    RSI_OS = 30.0
    ATR_PERIOD = 14
    ATR_MIN_THRESHOLD = 2.0

    # ボラティリティレジーム
    VOL_REGIME_PERIOD = 50
    VOL_REGIME_LOW = 0.7       # ATR/ATR_avg < 0.7 → スキップ
    VOL_REGIME_HIGH = 1.5      # ATR/ATR_avg > 1.5 → 高ボラ
    HIGH_VOL_SL_BONUS = 0.5    # 高ボラ時SL_ATR_MULTI加算

    # セッションフィルタ
    USE_SESSION_FILTER = True   # 非活発時間帯スキップ

    # モメンタムチェック
    USE_MOMENTUM = True         # 逆方向モメンタム抑制

    # 部分決済
    USE_PARTIAL_CLOSE = True
    PARTIAL_CLOSE_RATIO = 0.5   # 50%決済
    PARTIAL_TP_RATIO = 0.5      # TP距離の50%で発動

    # 資金管理
    RISK_PERCENT = 1.0         # ★DD抑制
    SL_ATR_MULTI = 2.0         # ★拡大（ノイズ耐性）
    TP_ATR_MULTI = 4.0         # ★RR1:2維持
    COOLDOWN_BARS = 2          # SL後2本(=2H)エントリー禁止
    MAX_LOT = 5.0
    MIN_LOT = 0.01
    MAX_POSITIONS = 1
    CONTRACT_SIZE = 100     # 1lot = 100oz
    POINT = 0.01


# ============================================================
# インジケーター計算
# ============================================================
def calc_ichimoku(df, tenkan_p, kijun_p, senkou_b_p):
    """一目均衡表の計算"""
    high = df["High"]
    low = df["Low"]

    # 転換線
    tenkan = (high.rolling(tenkan_p).max() + low.rolling(tenkan_p).min()) / 2
    # 基準線
    kijun = (high.rolling(kijun_p).max() + low.rolling(kijun_p).min()) / 2
    # 先行スパンA = (転換線 + 基準線) / 2 を26本先にシフト
    senkou_a = ((tenkan + kijun) / 2).shift(kijun_p)
    # 先行スパンB = (52本高値 + 52本安値) / 2 を26本先にシフト
    senkou_b = ((high.rolling(senkou_b_p).max() + low.rolling(senkou_b_p).min()) / 2).shift(kijun_p)

    return tenkan, kijun, senkou_a, senkou_b


def calc_rsi(series, period):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calc_atr(high, low, close, period):
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# ============================================================
# UT Bot Alerts
# ============================================================
class UTBot:
    def __init__(self, key_value):
        self.key_value = key_value
        self.trail_stop = 0.0

    def update(self, close_cur, close_prev, atr_cur, key_value=None):
        kv = key_value if key_value is not None else self.key_value
        n_loss = atr_cur * kv
        prev_trail = self.trail_stop

        if close_cur > prev_trail and close_prev > prev_trail:
            self.trail_stop = max(prev_trail, close_cur - n_loss)
        elif close_cur < prev_trail and close_prev < prev_trail:
            self.trail_stop = min(prev_trail, close_cur + n_loss)
        elif close_cur > prev_trail:
            self.trail_stop = close_cur - n_loss
        else:
            self.trail_stop = close_cur + n_loss

        buy_signal = (close_cur > self.trail_stop and close_prev <= prev_trail)
        sell_signal = (close_cur < self.trail_stop and close_prev >= prev_trail)

        return buy_signal, sell_signal


# ============================================================
# SMC (Smart Money Concepts)
# ============================================================
def detect_swing_points(highs, lows, swing_len):
    """スイングハイ/ローを検出"""
    swing_highs = []
    swing_lows = []
    n = len(highs)

    for i in range(swing_len, n - swing_len):
        # スイングハイ
        is_sh = True
        for j in range(1, swing_len + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_sh = False
                break
        if is_sh:
            swing_highs.append((i, highs[i]))

        # スイングロー
        is_sl = True
        for j in range(1, swing_len + 1):
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_sl = False
                break
        if is_sl:
            swing_lows.append((i, lows[i]))

    return swing_highs, swing_lows


def check_smc(highs, lows, closes, lookback, swing_len):
    """SMC分析: BOS/CHoCH検出"""
    n = len(highs)
    if n < lookback + swing_len + 1:
        return False, False

    # ルックバック範囲の切り出し
    start = max(0, n - lookback - swing_len - 1)
    h_slice = highs[start:n]
    l_slice = lows[start:n]

    swing_highs, swing_lows = detect_swing_points(
        h_slice, l_slice, swing_len
    )

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return False, False

    # 直近2つのスイングポイント（時系列順 → 最新が最後）
    sh0 = swing_highs[-1][1]  # 最新のスイングハイ
    sh1 = swing_highs[-2][1]  # 1つ前のスイングハイ
    sl0 = swing_lows[-1][1]   # 最新のスイングロー
    sl1 = swing_lows[-2][1]   # 1つ前のスイングロー

    latest_close = closes[-1]

    # BOS検出
    bullish_bos = latest_close > sh0
    bearish_bos = latest_close < sl0

    # CHoCH検出
    bearish_choch = False
    bullish_choch = False

    # 上昇構造中 (HH) にスイングローを下抜け → Bearish CHoCH
    if sh0 > sh1 and latest_close < sl0:
        bearish_choch = True

    # 下降構造中 (LL) にスイングハイを上抜け → Bullish CHoCH
    if sl0 < sl1 and latest_close > sh0:
        bullish_choch = True

    # SMCトレンド
    smc_bullish = (sh0 > sh1 and sl0 > sl1)  # HH + HL
    smc_bearish = (sh0 < sh1 and sl0 < sl1)  # LH + LL

    # ★ ブロッカー型: CHoCH時のみ禁止、確認は不要
    allow_buy = not bearish_choch
    allow_sell = not bullish_choch

    return allow_buy, allow_sell


# ============================================================
# データ取得
# ============================================================
def fetch_data(months=6):
    print(f"📥 ゴールド(GC=F) 取得中（{months}ヶ月分）...")
    end = datetime.now()
    start = end - timedelta(days=months * 30 + 90)

    t = yf.Ticker("GC=F")

    # 1時間足を取得
    h1 = t.history(start=start, end=end, interval="1h")
    if h1.empty:
        print("❌ データ取得失敗")
        return None

    # バックテスト期間を制限
    cutoff = end - timedelta(days=months * 30)
    cutoff_ts = pd.Timestamp(cutoff, tz=h1.index.tz) if h1.index.tz else pd.Timestamp(cutoff)

    print(f"   H1: {len(h1)}本 ({h1.index[0]} ~ {h1.index[-1]})")
    print(f"   ※ 5分足データ不足のため1時間足で代用バックテスト")

    return h1


# ============================================================
# バックテストエンジン
# ============================================================
class ThreeLayerBacktester:
    def __init__(self, cfg):
        self.cfg = cfg
        self.balance = cfg.INITIAL_BALANCE
        self.equity_curve = []
        self.trades = []
        self.open_pos = None
        self.peak_balance = cfg.INITIAL_BALANCE
        self.cooldown_until = 0

    def run(self, df):
        cfg = self.cfg
        df = df.copy()

        # インジケーター計算
        df["tenkan"], df["kijun"], df["senkou_a"], df["senkou_b"] = calc_ichimoku(
            df, cfg.ICHI_TENKAN, cfg.ICHI_KIJUN, cfg.ICHI_SENKOU_B)
        df["rsi"] = calc_rsi(df["Close"], cfg.RSI_PERIOD)
        df["atr"] = calc_atr(df["High"], df["Low"], df["Close"], cfg.ATR_PERIOD)
        df["ut_atr"] = calc_atr(df["High"], df["Low"], df["Close"], cfg.UT_ATR_PERIOD)

        # ボラティリティレジーム: ATR平均
        df["atr_avg"] = df["atr"].rolling(window=cfg.VOL_REGIME_PERIOD).mean()

        # ウォームアップ
        warmup = max(cfg.ICHI_SENKOU_B + cfg.ICHI_KIJUN, cfg.SMC_LOOKBACK + cfg.SMC_SWING_LEN + 10,
                     cfg.VOL_REGIME_PERIOD + 10, 100)
        total_bars = len(df)

        print(f"\n📊 バックテスト開始 v2.0: {df.index[warmup].date()} → {df.index[-1].date()}")
        print(f"   バー数: {total_bars:,}（ウォームアップ: {warmup}）")
        print(f"   設定: Risk={cfg.RISK_PERCENT}% SL=ATR×{cfg.SL_ATR_MULTI} TP=ATR×{cfg.TP_ATR_MULTI}")
        print(f"   一目: {cfg.ICHI_TENKAN}/{cfg.ICHI_KIJUN}/{cfg.ICHI_SENKOU_B}")
        print(f"   UT Bot: Key={cfg.UT_KEY_VALUE} HighVolKey={cfg.UT_HIGH_VOL_KEY} ATR={cfg.UT_ATR_PERIOD}")
        print(f"   VolRegime: Period={cfg.VOL_REGIME_PERIOD} Low={cfg.VOL_REGIME_LOW} High={cfg.VOL_REGIME_HIGH}")
        print(f"   Session={cfg.USE_SESSION_FILTER} Momentum={cfg.USE_MOMENTUM} PartialClose={cfg.USE_PARTIAL_CLOSE}")

        ut_bot = UTBot(cfg.UT_KEY_VALUE)
        # UT Bot初期化（ウォームアップ期間で状態を構築）
        for i in range(1, warmup):
            c1 = df["Close"].iloc[i]
            c2 = df["Close"].iloc[i - 1]
            ua = df["ut_atr"].iloc[i]
            if pd.notna(ua) and ua > 0:
                ut_bot.update(c1, c2, ua)

        for i in range(warmup, total_bars):
            ct = df.index[i]
            cc = df["Close"].iloc[i]
            ch = df["High"].iloc[i]
            cl = df["Low"].iloc[i]

            # ポジション管理（部分決済含む）
            self._manage_position(ch, cl, cc, ct, i)

            # 必要値取得
            sa = df["senkou_a"].iloc[i]
            sb = df["senkou_b"].iloc[i]
            rsi_val = df["rsi"].iloc[i]
            atr_val = df["atr"].iloc[i]
            atr_avg_val = df["atr_avg"].iloc[i]
            ut_atr_val = df["ut_atr"].iloc[i]
            close_prev = df["Close"].iloc[i - 1]

            if pd.isna(sa) or pd.isna(sb) or pd.isna(rsi_val) or pd.isna(atr_val) or pd.isna(ut_atr_val) or pd.isna(atr_avg_val):
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized(cc)})
                continue

            # ★ SL後クールダウン
            if i < self.cooldown_until:
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized(cc)})
                continue

            # === v2.0: セッションフィルタ ===
            if cfg.USE_SESSION_FILTER:
                hour = ct.hour if hasattr(ct, 'hour') else pd.Timestamp(ct).hour
                dow = ct.dayofweek if hasattr(ct, 'dayofweek') else pd.Timestamp(ct).dayofweek
                # 非活発時間帯スキップ: 22:00-2:00 および 金曜18時以降
                if hour >= 22 or hour < 2 or (dow == 4 and hour >= 18):
                    # UT Botの状態は更新し続ける
                    ut_bot.update(cc, close_prev, ut_atr_val)
                    self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized(cc)})
                    continue

            # === v2.0: ボラティリティレジーム検出 ===
            vol_ratio = atr_val / atr_avg_val if atr_avg_val > 0 else 1.0
            if vol_ratio < cfg.VOL_REGIME_LOW:
                vol_regime = 0  # 低ボラ → スキップ
            elif vol_ratio > cfg.VOL_REGIME_HIGH:
                vol_regime = 2  # 高ボラ
            else:
                vol_regime = 1  # 通常

            # 低ボラ時スキップ
            if vol_regime == 0:
                ut_bot.update(cc, close_prev, ut_atr_val)
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized(cc)})
                continue

            # === 第1層: 一目均衡表 ===
            cloud_upper = max(sa, sb)
            cloud_lower = min(sa, sb)
            allow_buy = cc > cloud_upper
            allow_sell = cc < cloud_lower

            if not allow_buy and not allow_sell:
                ut_bot.update(cc, close_prev, ut_atr_val)
                self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized(cc)})
                continue

            # === 第2層: UT Bot (高ボラ時はキー値変更) ===
            ut_key = cfg.UT_HIGH_VOL_KEY if vol_regime == 2 else None
            ut_buy, ut_sell = ut_bot.update(cc, close_prev, ut_atr_val, key_value=ut_key)

            # === 第2層: SMC ===
            lookback_start = max(0, i - cfg.SMC_LOOKBACK - cfg.SMC_SWING_LEN - 1)
            h_slice = df["High"].iloc[lookback_start:i + 1].values
            l_slice = df["Low"].iloc[lookback_start:i + 1].values
            c_slice = df["Close"].iloc[lookback_start:i + 1].values
            smc_buy, smc_sell = check_smc(h_slice, l_slice, c_slice, cfg.SMC_LOOKBACK, cfg.SMC_SWING_LEN)

            # === 第3層: RSI + ATR ===
            rsi_allow_buy = rsi_val < cfg.RSI_OB
            rsi_allow_sell = rsi_val > cfg.RSI_OS
            atr_allow = atr_val >= cfg.ATR_MIN_THRESHOLD

            # === v2.0: モメンタムチェック ===
            momentum_buy = True
            momentum_sell = True
            if cfg.USE_MOMENTUM and i >= 2:
                close_i2 = df["Close"].iloc[i - 2]
                mom_threshold = atr_val * 0.1
                # BUY: 価格が強く下落していないこと
                momentum_buy = (cc - close_i2) > -mom_threshold
                # SELL: 価格が強く上昇していないこと
                momentum_sell = (close_i2 - cc) > -mom_threshold

            # === エントリー（全AND） ===
            if self.open_pos is None:
                if allow_buy and ut_buy and smc_buy and rsi_allow_buy and atr_allow and momentum_buy:
                    # 高ボラ時SLボーナス
                    sl_multi = cfg.SL_ATR_MULTI + (cfg.HIGH_VOL_SL_BONUS if vol_regime == 2 else 0)
                    sl_dist = atr_val * sl_multi
                    tp_dist = atr_val * cfg.TP_ATR_MULTI
                    entry = cc
                    sl = entry - sl_dist
                    tp = entry + tp_dist
                    lot = self._calc_lot(sl_dist)
                    if lot > 0:
                        self.open_pos = {
                            "direction": "BUY", "entry": entry, "sl": sl, "tp": tp,
                            "lot": lot, "open_time": ct,
                            "rsi": rsi_val, "atr": atr_val,
                            "partial_closed": False, "original_lot": lot,
                            "sl_dist": sl_dist, "tp_dist": tp_dist,
                        }

                elif allow_sell and ut_sell and smc_sell and rsi_allow_sell and atr_allow and momentum_sell:
                    sl_multi = cfg.SL_ATR_MULTI + (cfg.HIGH_VOL_SL_BONUS if vol_regime == 2 else 0)
                    sl_dist = atr_val * sl_multi
                    tp_dist = atr_val * cfg.TP_ATR_MULTI
                    entry = cc
                    sl = entry + sl_dist
                    tp = entry - tp_dist
                    lot = self._calc_lot(sl_dist)
                    if lot > 0:
                        self.open_pos = {
                            "direction": "SELL", "entry": entry, "sl": sl, "tp": tp,
                            "lot": lot, "open_time": ct,
                            "rsi": rsi_val, "atr": atr_val,
                            "partial_closed": False, "original_lot": lot,
                            "sl_dist": sl_dist, "tp_dist": tp_dist,
                        }

            self.equity_curve.append({"time": ct, "equity": self.balance + self._unrealized(cc)})

        # 期間終了でクローズ
        if self.open_pos:
            fc = df["Close"].iloc[-1]
            self._close(fc, df.index[-1], "期間終了", total_bars - 1)

        print("✅ バックテスト完了")

    def _manage_position(self, high, low, close, time, bar_idx=0):
        if self.open_pos is None:
            return
        pos = self.open_pos
        cfg = self.cfg

        if pos["direction"] == "BUY":
            # SL/TP判定
            if low <= pos["sl"]:
                self._close(pos["sl"], time, "SL", bar_idx)
                return
            elif high >= pos["tp"]:
                self._close(pos["tp"], time, "TP", bar_idx)
                return

            # v2.0: 部分決済チェック
            if cfg.USE_PARTIAL_CLOSE and not pos.get("partial_closed", False):
                profit_dist = close - pos["entry"]
                tp_dist = pos.get("tp_dist", pos["tp"] - pos["entry"])
                if profit_dist >= tp_dist * cfg.PARTIAL_TP_RATIO:
                    self._partial_close(close, time, bar_idx)

        else:  # SELL
            if high >= pos["sl"]:
                self._close(pos["sl"], time, "SL", bar_idx)
                return
            elif low <= pos["tp"]:
                self._close(pos["tp"], time, "TP", bar_idx)
                return

            # v2.0: 部分決済チェック
            if cfg.USE_PARTIAL_CLOSE and not pos.get("partial_closed", False):
                profit_dist = pos["entry"] - close
                tp_dist = pos.get("tp_dist", pos["entry"] - pos["tp"])
                if profit_dist >= tp_dist * cfg.PARTIAL_TP_RATIO:
                    self._partial_close(close, time, bar_idx)

    def _partial_close(self, exit_price, time, bar_idx=0):
        """v2.0: 部分決済 — 50%決済しSLをブレイクイーブンに移動"""
        pos = self.open_pos
        if pos is None:
            return
        cfg = self.cfg
        pt = cfg.POINT

        # 決済するロット数
        close_lot = round(pos["lot"] * cfg.PARTIAL_CLOSE_RATIO, 2)
        if close_lot < cfg.MIN_LOT:
            return

        # 部分決済のPnL計算
        if pos["direction"] == "BUY":
            pnl_pts = (exit_price - pos["entry"]) / pt
        else:
            pnl_pts = (pos["entry"] - exit_price) / pt

        pnl_usd = pnl_pts * pt * cfg.CONTRACT_SIZE * close_lot
        pnl_jpy = pnl_usd * 150.0

        self.balance += pnl_jpy
        self.peak_balance = max(self.peak_balance, self.balance)

        # HALF取引を記録
        self.trades.append({
            "open_time": pos["open_time"], "close_time": time,
            "direction": pos["direction"],
            "entry": round(pos["entry"], 2), "exit": round(exit_price, 2),
            "lot": close_lot,
            "pnl_pts": round(pnl_pts, 1),
            "pnl_usd": round(pnl_usd, 2),
            "pnl_jpy": round(pnl_jpy, 0),
            "balance": round(self.balance, 0),
            "reason": "HALF",
            "rsi": round(pos["rsi"], 1),
            "atr": round(pos["atr"], 2),
        })

        # 残りロットを更新、SLをブレイクイーブンに移動
        remaining_lot = round(pos["lot"] - close_lot, 2)
        if remaining_lot < cfg.MIN_LOT:
            remaining_lot = cfg.MIN_LOT
        pos["lot"] = remaining_lot
        pos["sl"] = pos["entry"]  # ブレイクイーブン
        pos["partial_closed"] = True

    def _close(self, exit_price, time, reason, bar_idx=0):
        pos = self.open_pos
        if pos is None:
            return
        cfg = self.cfg
        pt = cfg.POINT

        # ★ SL時クールダウン
        if reason == "SL" and bar_idx > 0:
            self.cooldown_until = bar_idx + cfg.COOLDOWN_BARS

        if pos["direction"] == "BUY":
            pnl_pts = (exit_price - pos["entry"]) / pt
        else:
            pnl_pts = (pos["entry"] - exit_price) / pt

        pnl_usd = pnl_pts * pt * cfg.CONTRACT_SIZE * pos["lot"]
        pnl_jpy = pnl_usd * 150.0

        self.balance += pnl_jpy
        self.peak_balance = max(self.peak_balance, self.balance)

        self.trades.append({
            "open_time": pos["open_time"], "close_time": time,
            "direction": pos["direction"],
            "entry": round(pos["entry"], 2), "exit": round(exit_price, 2),
            "lot": pos["lot"],
            "pnl_pts": round(pnl_pts, 1),
            "pnl_usd": round(pnl_usd, 2),
            "pnl_jpy": round(pnl_jpy, 0),
            "balance": round(self.balance, 0),
            "reason": reason,
            "rsi": round(pos["rsi"], 1),
            "atr": round(pos["atr"], 2),
        })
        self.open_pos = None

    def _calc_lot(self, sl_dist):
        cfg = self.cfg
        risk_amount = self.balance * cfg.RISK_PERCENT / 100.0
        sl_dollars = sl_dist
        loss_per_lot = sl_dollars * cfg.CONTRACT_SIZE
        loss_per_lot_jpy = loss_per_lot * 150.0
        if loss_per_lot_jpy <= 0:
            return 0
        lot = risk_amount / loss_per_lot_jpy
        lot = max(cfg.MIN_LOT, min(cfg.MAX_LOT, round(lot, 2)))
        return lot

    def _unrealized(self, price):
        if self.open_pos is None:
            return 0
        pos = self.open_pos
        cfg = self.cfg
        pt = cfg.POINT
        if pos["direction"] == "BUY":
            pnl_pts = (price - pos["entry"]) / pt
        else:
            pnl_pts = (pos["entry"] - price) / pt
        return pnl_pts * pt * cfg.CONTRACT_SIZE * pos["lot"] * 150.0

    def get_report(self):
        if not self.trades:
            return {"error": "取引なし"}
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

        reason_stats = df.groupby("reason").agg(
            count=("pnl_jpy", "count"),
            pnl=("pnl_jpy", "sum")
        )

        return {
            "期間": f"{df['open_time'].iloc[0]} ~ {df['close_time'].iloc[-1]}",
            "初期資金": f"{self.cfg.INITIAL_BALANCE:,.0f}円",
            "最終残高": f"{self.balance:,.0f}円",
            "総損益": f"{total_pnl:+,.0f}円",
            "リターン": f"{(self.balance / self.cfg.INITIAL_BALANCE - 1) * 100:+.1f}%",
            "取引回数": len(df),
            "勝率": f"{win_rate:.1f}%（{len(wins)}勝/{len(losses)}敗）",
            "平均勝ち": f"{avg_win_pts:.0f}pt ({avg_win_jpy:+,.0f}円)",
            "平均負け": f"{avg_loss_pts:.0f}pt ({avg_loss_jpy:,.0f}円)",
            "RR比": f"1:{avg_win_pts/avg_loss_pts:.2f}" if avg_loss_pts > 0 else "N/A",
            "PF": f"{pf:.2f}" if pf != float("inf") else "∞",
            "最大DD": f"{max_dd:.1f}% ({max_dd_jpy:,.0f}円)",
            "月間勝率": f"{pm}/{tm} ({pm/tm*100:.0f}%)" if tm > 0 else "N/A",
            "月別": monthly.to_dict(),
            "理由別": reason_stats.to_dict(),
        }


# ============================================================
# メイン
# ============================================================
if __name__ == "__main__":
    cfg = ThreeLayerConfig()
    df = fetch_data(months=6)
    if df is None:
        print("❌ データ取得失敗")
        exit()

    bt = ThreeLayerBacktester(cfg)
    bt.run(df)
    rpt = bt.get_report()

    if rpt and "error" not in rpt:
        print("\n" + "=" * 60)
        print("📊 ThreeLayerEA v2.0 [XAUUSD] バックテスト結果（直近半年）")
        print("=" * 60)
        for k, v in rpt.items():
            if k == "月別":
                print(f"\n📅 月別損益:")
                for m, p in v.items():
                    bar = "█" * max(1, int(abs(p) / 5000))
                    icon = "🟢" if p > 0 else "🔴"
                    print(f"  {m}: {icon} {p:+,.0f}円 {bar}")
            elif k == "理由別":
                print(f"\n📋 決済理由別:")
                counts = v.get("count", {})
                pnls = v.get("pnl", {})
                for reason in counts:
                    print(f"  {reason}: {int(counts[reason])}回 / {pnls[reason]:+,.0f}円")
            else:
                print(f"  {k}: {v}")

        print(f"\n📋 取引詳細（直近10件）:")
        print(f"  {'日時':<20} {'方向':<5} {'Entry':>10} {'Exit':>10} {'Lot':>5} {'損益(pt)':>8} {'損益(円)':>10} {'残高':>12} {'理由':<5} {'RSI':>5} {'ATR':>6}")
        print("  " + "-" * 110)
        for t in bt.trades[-10:]:
            print(f"  {str(t['open_time'])[:19]:<20} {t['direction']:<5} {t['entry']:>10.2f} {t['exit']:>10.2f} {t['lot']:>5.2f} {t['pnl_pts']:>8.0f} {t['pnl_jpy']:>+10,.0f} {t['balance']:>12,.0f} {t['reason']:<5} {t['rsi']:>5.1f} {t['atr']:>6.2f}")
    else:
        print("❌ 取引が発生しませんでした")
        print("   フィルターが厳しすぎる可能性があります")
