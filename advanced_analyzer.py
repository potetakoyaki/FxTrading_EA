"""
🧠 高度チャート分析モジュール (advanced_analyzer.py)

フィボナッチ、エリオット波動、ドルインデックス相関、並行チャネル、
サポート/レジスタンス、ADXトレンド強度を組み合わせた複合分析エンジン。
全分析を裏側で実行し、加重スコアで統合的な予測方向・ターゲットを算出する。
"""

import pandas as pd
import numpy as np
import yfinance as yf
from scipy.signal import argrelextrema
from sklearn.linear_model import LinearRegression


# ============================================================
# 1. スイングポイント検出（共通ユーティリティ）
# ============================================================

def detect_swing_points(df: pd.DataFrame, order: int = 5) -> tuple:
    """
    ローソク足データからスイングハイ・スイングローを検出。
    order: 前後何本の足と比較するか（大きいほど大きなスイング）
    Returns: (swing_highs_indices, swing_lows_indices)
    """
    highs = df["High"].values
    lows = df["Low"].values

    swing_high_idx = argrelextrema(highs, np.greater_equal, order=order)[0]
    swing_low_idx = argrelextrema(lows, np.less_equal, order=order)[0]

    return swing_high_idx, swing_low_idx


# ============================================================
# 2. フィボナッチ・リトレースメント & エクステンション
# ============================================================

FIBO_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIBO_EXT_LEVELS = [1.0, 1.272, 1.618, 2.0, 2.618]


def calc_fibonacci(df: pd.DataFrame, lookback: int = 100) -> dict:
    """
    直近のスイングハイ/ローからフィボナッチ・リトレースメントを計算。
    上昇トレンド: 安値→高値のリトレースメント
    下降トレンド: 高値→安値のリトレースメント
    """
    data = df.tail(lookback)
    swing_high_idx, swing_low_idx = detect_swing_points(data, order=max(3, len(data) // 20))

    if len(swing_high_idx) == 0 or len(swing_low_idx) == 0:
        return {"levels": {}, "trend": "unknown", "target_levels": {}}

    # 直近のスイング
    last_high_idx = swing_high_idx[-1]
    last_low_idx = swing_low_idx[-1]
    swing_high = float(data["High"].iloc[last_high_idx])
    swing_low = float(data["Low"].iloc[last_low_idx])
    price_range = swing_high - swing_low

    if price_range <= 0:
        return {"levels": {}, "trend": "unknown", "target_levels": {}}

    # トレンド判定: 直近のスイングがハイかローか
    is_uptrend = last_low_idx < last_high_idx

    # リトレースメント
    levels = {}
    for fib in FIBO_LEVELS:
        if is_uptrend:
            levels[f"{fib:.1%}"] = swing_high - price_range * fib
        else:
            levels[f"{fib:.1%}"] = swing_low + price_range * fib

    # エクステンション
    target_levels = {}
    for ext in FIBO_EXT_LEVELS:
        if is_uptrend:
            target_levels[f"{ext:.1%}"] = swing_low + price_range * ext
        else:
            target_levels[f"{ext:.1%}"] = swing_high - price_range * ext

    current_price = float(df["Close"].iloc[-1])

    # 最も近いフィボサポート/レジスタンス
    support_levels = [v for v in levels.values() if v < current_price]
    resist_levels = [v for v in levels.values() if v > current_price]
    nearest_support = max(support_levels) if support_levels else swing_low
    nearest_resist = min(resist_levels) if resist_levels else swing_high

    return {
        "levels": levels,
        "trend": "uptrend" if is_uptrend else "downtrend",
        "swing_high": swing_high,
        "swing_low": swing_low,
        "nearest_support": nearest_support,
        "nearest_resist": nearest_resist,
        "target_levels": target_levels,
    }


# ============================================================
# 3. エリオット波動（簡易パターン検出）
# ============================================================

def detect_elliott_wave(df: pd.DataFrame, lookback: int = 100) -> dict:
    """
    簡易エリオット波動検出。
    5波インパルス(上昇: 1↑2↓3↑4↓5↑) を検出し、
    次の波動（修正波A-B-C or 継続）を予測。
    """
    data = df.tail(lookback)
    swing_high_idx, swing_low_idx = detect_swing_points(data, order=max(3, len(data) // 25))

    # スイングポイントを時系列順にマージ
    points = []
    for i in swing_high_idx:
        points.append({"idx": i, "type": "high", "price": float(data["High"].iloc[i])})
    for i in swing_low_idx:
        points.append({"idx": i, "type": "low", "price": float(data["Low"].iloc[i])})
    points.sort(key=lambda x: x["idx"])

    if len(points) < 5:
        return {"wave_count": 0, "phase": "不明", "direction": 0, "confidence": 0}

    # 交互のスイング（H-L-H-L-H... or L-H-L-H-L...）にフィルタ
    filtered = [points[0]]
    for p in points[1:]:
        if p["type"] != filtered[-1]["type"]:
            filtered.append(p)

    if len(filtered) < 5:
        return {"wave_count": len(filtered), "phase": "形成中", "direction": 0, "confidence": 0.2}

    # 直近5波を取得
    last5 = filtered[-5:]

    # インパルス波（上昇5波動）の条件チェック
    # wave1: 上, wave2: 下(wave1を超えない), wave3: 上(最大), wave4: 下(wave1高値を超えない), wave5: 上
    if last5[0]["type"] == "low":
        # 上昇インパルス候補
        w1_start = last5[0]["price"]
        w1_end = last5[1]["price"]
        w2_end = last5[2]["price"]
        w3_end = last5[3]["price"]
        w4_end = last5[4]["price"]

        is_impulse_up = (
            w1_end > w1_start and
            w2_end > w1_start and
            w3_end > w1_end and
            w4_end > w2_end
        )

        if is_impulse_up:
            # 5波完了後なら修正波(下降)が来る
            current_price = float(df["Close"].iloc[-1])
            if current_price <= w4_end:
                return {
                    "wave_count": 5, "phase": "修正波(A-B-C)進行中",
                    "direction": -1, "confidence": 0.6,
                    "expected_target": w2_end,  # 修正波はwave2近辺まで
                }
            else:
                return {
                    "wave_count": 5, "phase": "第5波 or 修正波入り",
                    "direction": -0.3, "confidence": 0.5,
                    "expected_target": w4_end,
                }
    elif last5[0]["type"] == "high":
        # 下降インパルス候補
        w1_start = last5[0]["price"]
        w1_end = last5[1]["price"]
        w2_end = last5[2]["price"]
        w3_end = last5[3]["price"]
        w4_end = last5[4]["price"]

        is_impulse_down = (
            w1_end < w1_start and
            w2_end < w1_start and
            w3_end < w1_end and
            w4_end < w2_end
        )

        if is_impulse_down:
            current_price = float(df["Close"].iloc[-1])
            if current_price >= w4_end:
                return {
                    "wave_count": 5, "phase": "修正波(A-B-C)進行中",
                    "direction": 1, "confidence": 0.6,
                    "expected_target": w2_end,
                }
            else:
                return {
                    "wave_count": 5, "phase": "第5波 or 修正波入り",
                    "direction": 0.3, "confidence": 0.5,
                    "expected_target": w4_end,
                }

    # パターンが不明瞭な場合、直近の動きから推定
    recent_moves = [filtered[-1]["price"] - filtered[-2]["price"]]
    avg_move = np.mean(recent_moves)
    direction = 1 if avg_move > 0 else -1

    return {
        "wave_count": len(filtered),
        "phase": "波動形成中",
        "direction": direction * 0.3,
        "confidence": 0.3,
    }


# ============================================================
# 4. ドルインデックス（DXY）相関分析
# ============================================================

def analyze_dxy_correlation(df: pd.DataFrame, ticker: str, period: str = "6mo") -> dict:
    """
    ドルインデックス (DX-Y.NYB) を取得し、対象ティッカーとの相関を分析。
    FXペアでは逆相関/正相関のシグナルとして使う。
    """
    result = {"correlation": 0, "dxy_trend": "unknown", "signal": 0, "confidence": 0}

    try:
        dxy = yf.Ticker("DX-Y.NYB")
        dxy_df = dxy.history(period=period, interval="1d")
        if dxy_df.empty:
            return result
    except Exception:
        return result

    # 日次Close を揃える
    target_close = df["Close"].resample("1D").last().dropna() if hasattr(df.index, 'freq') or True else df["Close"]
    dxy_close = dxy_df["Close"].resample("1D").last().dropna()

    # インデックスの tz を除去して合わせる
    if hasattr(target_close.index, "tz") and target_close.index.tz is not None:
        target_close.index = target_close.index.tz_localize(None)
    if hasattr(dxy_close.index, "tz") and dxy_close.index.tz is not None:
        dxy_close.index = dxy_close.index.tz_localize(None)

    # 共通日付でマージ
    merged = pd.DataFrame({"target": target_close, "dxy": dxy_close}).dropna()

    if len(merged) < 20:
        return result

    # 相関係数
    correlation = float(merged["target"].corr(merged["dxy"]))

    # DXY のトレンド（直近20日SMA vs 直近価格）
    dxy_recent = merged["dxy"].tail(20)
    dxy_sma = dxy_recent.mean()
    dxy_last = dxy_recent.iloc[-1]
    dxy_trend = "up" if dxy_last > dxy_sma else "down"

    # DXY → ターゲットへのシグナル
    # 正相関: DXY↑ → target↑
    # 逆相関: DXY↑ → target↓
    if abs(correlation) > 0.3:
        if dxy_trend == "up":
            signal = np.sign(correlation)  # 正相関なら+1, 逆相関なら-1
        else:
            signal = -np.sign(correlation)
        confidence = min(abs(correlation), 0.8)
    else:
        signal = 0
        confidence = 0.1

    return {
        "correlation": round(correlation, 3),
        "dxy_trend": dxy_trend,
        "dxy_last": round(float(dxy_last), 2),
        "signal": signal,
        "confidence": confidence,
    }


# ============================================================
# 5. 並行チャネル / 回帰チャネル検出
# ============================================================

def detect_channel(df: pd.DataFrame, lookback: int = 60) -> dict:
    """
    線形回帰チャネルを検出。
    中心線 + 上限/下限チャネルラインを計算し、
    ブレイクアウト/バウンスのシグナルを生成。
    """
    data = df.tail(lookback)
    n = len(data)
    X = np.arange(n).reshape(-1, 1)
    close = data["Close"].values

    # 線形回帰（中心線）
    model = LinearRegression()
    model.fit(X, close)
    center_line = model.predict(X)
    slope = float(model.coef_[0])

    # 残差からチャネル幅
    residuals = close - center_line
    channel_width = np.std(residuals) * 2

    upper_channel = center_line + channel_width
    lower_channel = center_line - channel_width

    current_price = float(close[-1])
    current_center = float(center_line[-1])
    current_upper = float(upper_channel[-1])
    current_lower = float(lower_channel[-1])

    # チャネル内の相対位置 (0=下限, 1=上限)
    if current_upper != current_lower:
        channel_position = (current_price - current_lower) / (current_upper - current_lower)
    else:
        channel_position = 0.5

    # シグナル: チャネル上限付近→反転下降, 下限付近→反転上昇, ブレイクアウト
    if channel_position > 1.05:
        signal = 0.5  # 上方ブレイクアウト → 強い上昇
        condition = "上方ブレイクアウト"
    elif channel_position > 0.85:
        signal = -0.4  # 上限接近 → 反落リスク
        condition = "チャネル上限接近"
    elif channel_position < -0.05:
        signal = -0.5  # 下方ブレイクアウト → 強い下降
        condition = "下方ブレイクアウト"
    elif channel_position < 0.15:
        signal = 0.4  # 下限接近 → 反発期待
        condition = "チャネル下限接近"
    else:
        signal = slope / abs(slope) * 0.2 if slope != 0 else 0  # トレンド方向に微弱
        condition = "チャネル内推移"

    # 将来のチャネル延長値（大量に生成して forecast が長くても対応）
    future_periods = 400
    X_future = np.arange(n, n + future_periods).reshape(-1, 1)
    future_center = model.predict(X_future)
    future_upper = future_center + channel_width
    future_lower = future_center - channel_width

    return {
        "slope": round(slope, 6),
        "slope_per_day": round(slope, 6),
        "channel_width": round(channel_width, 4),
        "channel_position": round(channel_position, 3),
        "condition": condition,
        "signal": signal,
        "confidence": 0.5,
        "center_line": center_line.tolist(),
        "upper_channel": upper_channel.tolist(),
        "lower_channel": lower_channel.tolist(),
        "future_center": future_center.flatten().tolist(),
        "future_upper": future_upper.flatten().tolist(),
        "future_lower": future_lower.flatten().tolist(),
        "current_center": current_center,
        "current_upper": current_upper,
        "current_lower": current_lower,
    }


# ============================================================
# 6. サポート/レジスタンス自動検出
# ============================================================

def detect_support_resistance(df: pd.DataFrame, lookback: int = 100, num_levels: int = 4) -> dict:
    """
    価格が複数回反応した水準をサポート/レジスタンスとして検出。
    """
    data = df.tail(lookback)
    swing_high_idx, swing_low_idx = detect_swing_points(data, order=max(3, len(data) // 20))

    current_price = float(df["Close"].iloc[-1])

    # スイングポイントの価格を収集
    high_prices = [float(data["High"].iloc[i]) for i in swing_high_idx]
    low_prices = [float(data["Low"].iloc[i]) for i in swing_low_idx]
    all_levels = high_prices + low_prices

    if not all_levels:
        return {"supports": [], "resistances": []}

    # 近い価格をクラスタリング（価格の1%以内は同一レベルとみなす）
    all_levels.sort()
    clusters = []
    current_cluster = [all_levels[0]]

    for price in all_levels[1:]:
        threshold = current_cluster[-1] * 0.01
        if price - current_cluster[-1] <= threshold:
            current_cluster.append(price)
        else:
            clusters.append(current_cluster)
            current_cluster = [price]
    clusters.append(current_cluster)

    # クラスタの中央値をレベルとし、タッチ回数でソート
    levels_with_strength = [(np.median(c), len(c)) for c in clusters]
    levels_with_strength.sort(key=lambda x: x[1], reverse=True)

    supports = []
    resistances = []

    for level, strength in levels_with_strength:
        if level < current_price:
            supports.append({"price": round(level, 4), "strength": strength})
        else:
            resistances.append({"price": round(level, 4), "strength": strength})

    supports.sort(key=lambda x: x["price"], reverse=True)
    resistances.sort(key=lambda x: x["price"])

    return {
        "supports": supports[:num_levels],
        "resistances": resistances[:num_levels],
    }


# ============================================================
# 7. ADX / トレンド強度
# ============================================================

def calc_trend_strength(df: pd.DataFrame, window: int = 14) -> dict:
    """ADX ベースのトレンド強度とモメンタム分析"""
    import ta

    if len(df) < window * 2:
        return {"adx": 0, "trend_strength": "不明", "direction": 0, "confidence": 0}

    adx_indicator = ta.trend.ADXIndicator(
        high=df["High"], low=df["Low"], close=df["Close"], window=window
    )
    adx = adx_indicator.adx().iloc[-1]
    plus_di = adx_indicator.adx_pos().iloc[-1]
    minus_di = adx_indicator.adx_neg().iloc[-1]

    if pd.isna(adx):
        return {"adx": 0, "trend_strength": "不明", "direction": 0, "confidence": 0}

    adx = float(adx)
    plus_di = float(plus_di)
    minus_di = float(minus_di)

    # 方向: +DI > -DI → 上昇, else → 下降
    if plus_di > minus_di:
        direction = 1
        direction_label = "上昇"
    else:
        direction = -1
        direction_label = "下降"

    # 強度判定
    if adx >= 40:
        strength = "非常に強い"
        confidence = 0.8
    elif adx >= 25:
        strength = "強い"
        confidence = 0.6
    elif adx >= 20:
        strength = "やや弱い"
        confidence = 0.3
    else:
        strength = "レンジ"
        direction = 0
        direction_label = "横ばい"
        confidence = 0.2

    return {
        "adx": round(adx, 1),
        "plus_di": round(plus_di, 1),
        "minus_di": round(minus_di, 1),
        "trend_strength": strength,
        "direction": direction,
        "direction_label": direction_label,
        "confidence": confidence,
    }


# ============================================================
# 8. 総合分析 & 複合スコアリング
# ============================================================

def run_full_analysis(df: pd.DataFrame, ticker: str = "", fetch_dxy: bool = True) -> dict:
    """
    全分析を実行し、結果を統合する。
    """
    results = {}

    # フィボナッチ
    results["fibonacci"] = calc_fibonacci(df)

    # エリオット波動
    results["elliott"] = detect_elliott_wave(df)

    # チャネル
    results["channel"] = detect_channel(df)

    # サポレジ
    results["support_resistance"] = detect_support_resistance(df)

    # ADX
    results["trend_strength"] = calc_trend_strength(df)

    # DXY 相関（FX関連のみ、かつオプション）
    if fetch_dxy and ticker:
        fx_tickers = {"USDJPY=X", "EURUSD=X", "GBPUSD=X", "EURJPY=X", "GBPJPY=X",
                      "AUDUSD=X", "AUDJPY=X", "USDCHF=X"}
        if ticker in fx_tickers or ticker.endswith("=X"):
            results["dxy"] = analyze_dxy_correlation(df, ticker)
        else:
            results["dxy"] = {"correlation": 0, "signal": 0, "confidence": 0}
    else:
        results["dxy"] = {"correlation": 0, "signal": 0, "confidence": 0}

    # ──────── 複合スコアリング ────────
    scores = []
    weights = []
    score_details = []  # 根拠を蓄積

    # Elliott Wave
    ew = results["elliott"]
    if ew.get("confidence", 0) > 0:
        scores.append(ew["direction"])
        weights.append(ew["confidence"] * 1.5)
        score_details.append({
            "name": "エリオット波動",
            "signal": ew["direction"],
            "weight": ew["confidence"] * 1.5,
            "detail": ew.get("phase", "不明"),
        })

    # Channel
    ch = results["channel"]
    if ch.get("confidence", 0) > 0:
        scores.append(ch["signal"])
        weights.append(ch["confidence"])
        score_details.append({
            "name": "回帰チャネル",
            "signal": ch["signal"],
            "weight": ch["confidence"],
            "detail": ch.get("condition", "不明"),
        })

    # ADX/Trend
    ts = results["trend_strength"]
    if ts.get("confidence", 0) > 0:
        scores.append(ts["direction"] * ts["confidence"])
        weights.append(ts["confidence"])
        score_details.append({
            "name": "ADXトレンド",
            "signal": ts["direction"] * ts["confidence"],
            "weight": ts["confidence"],
            "detail": f"ADX={ts.get('adx',0):.0f}, {ts.get('trend_strength','不明')}",
        })

    # DXY
    dxy = results["dxy"]
    if dxy.get("confidence", 0) > 0:
        scores.append(dxy["signal"])
        weights.append(dxy["confidence"] * 0.8)
        score_details.append({
            "name": "ドルインデックス",
            "signal": dxy["signal"],
            "weight": dxy["confidence"] * 0.8,
            "detail": f"相関={dxy.get('correlation',0):.2f}, DXY{dxy.get('dxy_trend','?')}",
        })

    # Fibonacci
    fib = results["fibonacci"]
    current_price = float(df["Close"].iloc[-1])
    if fib.get("nearest_support") and fib.get("nearest_resist"):
        fib_range = fib["nearest_resist"] - fib["nearest_support"]
        if fib_range > 0:
            fib_position = (current_price - fib["nearest_support"]) / fib_range
            fib_signal = (0.5 - fib_position) * 0.8
            scores.append(fib_signal)
            weights.append(0.5)
            score_details.append({
                "name": "フィボナッチ",
                "signal": fib_signal,
                "weight": 0.5,
                "detail": f"S={fib['nearest_support']:.4f}, R={fib['nearest_resist']:.4f}, 位置={fib_position:.0%}",
            })

    # 加重平均スコア
    if weights:
        total_weight = sum(weights)
        composite_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
    else:
        composite_score = 0

    composite_score = max(-1.0, min(1.0, composite_score))

    results["composite"] = {
        "score": round(composite_score, 3),
        "direction": "上昇" if composite_score > 0.1 else ("下降" if composite_score < -0.1 else "中立"),
        "strength": round(abs(composite_score), 3),
        "total_signals": len(scores),
        "score_details": score_details,
    }

    return results


def apply_analysis_to_forecast(forecast_values: list, analysis: dict, periods: int) -> list:
    """
    複合分析スコアを予測値に適用し、方向性バイアスを加える。
    """
    composite = analysis.get("composite", {})
    score = composite.get("score", 0)
    channel = analysis.get("channel", {})
    fib = analysis.get("fibonacci", {})

    adjusted = []
    base_values = list(forecast_values)

    for i, val in enumerate(base_values):
        progress = (i + 1) / periods

        # 複合スコアによるバイアス（最大5%）
        bias_factor = score * progress * val * 0.05

        # チャネルの引力
        if channel.get("future_center") and i < len(channel["future_center"]):
            channel_center = channel["future_center"][i]
            channel_pull = (channel_center - val) * 0.15 * progress
            if channel.get("condition", "").endswith("ブレイクアウト"):
                channel_pull *= 0.3
        else:
            channel_pull = 0

        # フィボナッチターゲット引力
        fib_pull = 0
        if fib.get("target_levels"):
            targets = list(fib["target_levels"].values())
            if targets:
                nearest_target = min(targets, key=lambda t: abs(t - val))
                fib_pull = (nearest_target - val) * 0.05 * progress

        adjusted.append(val + bias_factor + channel_pull + fib_pull)

    return adjusted


# ============================================================
# 9. 予測根拠テキスト生成
# ============================================================

def generate_rationale(analysis: dict, current_price: float, forecast_summary: dict = None) -> str:
    """
    全分析結果から、なぜこの予測になったのかを日本語で詳細に説明する。
    """
    lines = []
    comp = analysis.get("composite", {})
    fib = analysis.get("fibonacci", {})
    ew = analysis.get("elliott", {})
    ch = analysis.get("channel", {})
    ts = analysis.get("trend_strength", {})
    dxy = analysis.get("dxy", {})
    sr = analysis.get("support_resistance", {})

    direction = comp.get("direction", "中立")
    score = comp.get("score", 0)

    # ──── 総合判定 ────
    if forecast_summary:
        final_p = forecast_summary.get("final_price", current_price)
        change_pct = forecast_summary.get("change_pct", 0)
        days = forecast_summary.get("days", 0)
        lines.append(f"📊 {days}本先の予測価格は **{final_p:,.4f}**（現在比 {'+' if change_pct>=0 else ''}{change_pct:.2f}%）です。")
        lines.append(f"総合判定は **{direction}**（スコア: {score:+.2f}）。以下がその根拠です。")
    else:
        lines.append(f"📊 総合判定: **{direction}**（スコア: {score:+.2f}）")

    lines.append("")

    # ──── 個別根拠 ────
    # 1. フィボナッチ
    if fib.get("levels"):
        trend_jp = "上昇トレンド" if fib["trend"] == "uptrend" else "下降トレンド"
        lines.append(f"**① フィボナッチ分析** — {trend_jp}")
        lines.append(f"直近のスイングレンジは {fib.get('swing_low',0):,.4f}（安値）〜 {fib.get('swing_high',0):,.4f}（高値）です。")
        lines.append(f"現在価格 {current_price:,.4f} は、最寄りサポート **{fib.get('nearest_support',0):,.4f}** と "
                     f"レジスタンス **{fib.get('nearest_resist',0):,.4f}** の間に位置しています。")
        if fib.get("nearest_resist") and fib.get("nearest_support"):
            rng = fib["nearest_resist"] - fib["nearest_support"]
            if rng > 0:
                pos = (current_price - fib["nearest_support"]) / rng
                if pos < 0.3:
                    lines.append("→ サポート付近にあるため、**反発上昇しやすい**位置です。")
                elif pos > 0.7:
                    lines.append("→ レジスタンス付近にあるため、**上値が重い**可能性があります。")
                else:
                    lines.append("→ フィボレンジの中央付近で、方向感は限定的です。")
        lines.append("")

    # 2. エリオット波動
    if ew.get("phase"):
        dir_text = "上方向" if ew.get("direction", 0) > 0 else ("下方向" if ew.get("direction", 0) < 0 else "不明")
        lines.append(f"**② エリオット波動** — {ew['phase']}")
        lines.append(f"検出された波動数: {ew.get('wave_count', 0)}波。")
        if ew.get("expected_target"):
            lines.append(f"次の波動ターゲット: **{ew['expected_target']:,.4f}**")
        if ew.get("confidence", 0) >= 0.5:
            lines.append(f"→ 信頼度 {ew['confidence']:.0%} で **{dir_text}** のバイアスを予測に反映。")
        else:
            lines.append(f"→ 信頼度 {ew.get('confidence',0):.0%} のため、参考程度の反映です。")
        lines.append("")

    # 3. チャネル分析
    if ch.get("condition"):
        slope_dir = "上向き" if ch.get("slope", 0) > 0 else "下向き"
        lines.append(f"**③ 回帰チャネル** — {ch['condition']}（傾き: {slope_dir}）")
        lines.append(f"チャネル幅: {ch.get('channel_width',0):,.4f}、現在のチャネル内位置: {ch.get('channel_position',0):.0%}")
        lines.append(f"チャネル上限: {ch.get('current_upper',0):,.4f}、下限: {ch.get('current_lower',0):,.4f}")
        if ch.get("condition") == "チャネル上限接近":
            lines.append("→ 上限に接近しており、**反落リスク**があります。チャネル中央線への回帰が予測に反映されています。")
        elif ch.get("condition") == "チャネル下限接近":
            lines.append("→ 下限に接近しており、**反発上昇**の可能性があります。")
        elif "ブレイクアウト" in ch.get("condition", ""):
            lines.append("→ チャネルをブレイクアウトしており、**トレンド加速**の可能性があります。")
        else:
            lines.append("→ チャネル内で推移しており、チャネル方向へのバイアスを予測に適用しています。")
        lines.append("")

    # 4. ADX
    if ts.get("adx", 0) > 0:
        lines.append(f"**④ トレンド強度 (ADX)** — {ts.get('trend_strength','不明')}（ADX: {ts.get('adx',0):.1f}）")
        lines.append(f"+DI: {ts.get('plus_di',0):.1f}、-DI: {ts.get('minus_di',0):.1f}")
        if ts.get("adx", 0) >= 25:
            lines.append(f"→ ADXが25以上で**明確なトレンドが存在**。{ts.get('direction_label','不明')}方向のバイアスを強めに反映。")
        elif ts.get("adx", 0) >= 20:
            lines.append("→ トレンドはやや弱めですが、方向感は検出されています。")
        else:
            lines.append("→ ADXが20未満で**レンジ相場**と判定。方向性バイアスは抑制されています。")
        lines.append("")

    # 5. DXY
    if dxy.get("confidence", 0) > 0.1:
        corr = dxy.get("correlation", 0)
        corr_type = "正相関" if corr > 0 else "逆相関"
        lines.append(f"**⑤ ドルインデックス (DXY)** — 相関: {corr:.3f}（{corr_type}）")
        lines.append(f"DXYトレンド: {'上昇' if dxy.get('dxy_trend')=='up' else '下落'}（{dxy.get('dxy_last',0):.2f}）")
        if abs(corr) >= 0.5:
            lines.append(f"→ {corr_type}が強い（{abs(corr):.2f}）ため、DXYの動向が予測に**大きく影響**しています。")
        elif abs(corr) >= 0.3:
            lines.append(f"→ 中程度の{corr_type}があり、DXYの方向を予測の補助要因として採用。")
        lines.append("")

    # 6. サポレジ
    supports = sr.get("supports", [])
    resists = sr.get("resistances", [])
    if supports or resists:
        lines.append("**⑥ サポート/レジスタンス**")
        if supports:
            s_list = ", ".join([f"{s['price']:,.4f}（{s['strength']}回タッチ）" for s in supports[:3]])
            lines.append(f"サポート: {s_list}")
        if resists:
            r_list = ", ".join([f"{r['price']:,.4f}（{r['strength']}回タッチ）" for r in resists[:3]])
            lines.append(f"レジスタンス: {r_list}")
        lines.append("→ これらの水準は過去に複数回反応しており、今後も意識される可能性が高いです。")
        lines.append("")

    # ──── スコア内訳 ────
    details = comp.get("score_details", [])
    if details:
        lines.append("**📐 スコア内訳**")
        for d in details:
            sig_mark = "🟢+" if d["signal"] > 0 else ("🔴" if d["signal"] < 0 else "⚪")
            lines.append(f"- {d['name']}: {sig_mark}{d['signal']:.2f}（重み: {d['weight']:.2f}）— {d['detail']}")
        lines.append(f"→ 合計スコア: **{score:+.3f}**（{direction}）")

    return "\n".join(lines)
