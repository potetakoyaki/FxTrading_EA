"""
テクニカル指標の計算モジュール
SMA, EMA, ボリンジャーバンド, RSI, MACD を計算する
"""

import pandas as pd
import ta


def add_sma(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
    """単純移動平均線 (SMA) を追加"""
    if periods is None:
        periods = [20, 50, 200]
    for period in periods:
        if len(df) >= period:
            df[f"SMA_{period}"] = ta.trend.sma_indicator(df["Close"], window=period)
    return df


def add_ema(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
    """指数移動平均線 (EMA) を追加"""
    if periods is None:
        periods = [12, 26]
    for period in periods:
        if len(df) >= period:
            df[f"EMA_{period}"] = ta.trend.ema_indicator(df["Close"], window=period)
    return df


def add_bollinger_bands(df: pd.DataFrame, window: int = 20, std: int = 2) -> pd.DataFrame:
    """ボリンジャーバンドを追加"""
    if len(df) >= window:
        indicator = ta.volatility.BollingerBands(close=df["Close"], window=window, window_dev=std)
        df["BB_Upper"] = indicator.bollinger_hband()
        df["BB_Middle"] = indicator.bollinger_mavg()
        df["BB_Lower"] = indicator.bollinger_lband()
    return df


def add_rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """RSI (Relative Strength Index) を追加"""
    if len(df) >= window:
        df["RSI"] = ta.momentum.rsi(df["Close"], window=window)
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD を追加"""
    if len(df) >= slow:
        macd_indicator = ta.trend.MACD(close=df["Close"], window_fast=fast, window_slow=slow, window_sign=signal)
        df["MACD"] = macd_indicator.macd()
        df["MACD_Signal"] = macd_indicator.macd_signal()
        df["MACD_Hist"] = macd_indicator.macd_diff()
    return df


def get_analysis_summary(df: pd.DataFrame) -> dict:
    """
    最新データに基づく分析サマリーを生成する。
    """
    if df.empty:
        return {}

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    price = latest["Close"]
    change = price - prev["Close"]
    change_pct = (change / prev["Close"]) * 100 if prev["Close"] != 0 else 0

    summary = {
        "price": round(price, 4),
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
        "high": round(latest["High"], 4),
        "low": round(latest["Low"], 4),
        "open": round(latest["Open"], 4),
        "volume": int(latest["Volume"]) if "Volume" in df.columns and pd.notna(latest.get("Volume")) else 0,
    }

    # RSI シグナル
    if "RSI" in df.columns and pd.notna(latest.get("RSI")):
        rsi_val = round(latest["RSI"], 1)
        summary["rsi_value"] = rsi_val
        if rsi_val >= 70:
            summary["rsi_signal"] = "買われ過ぎ"
            summary["rsi_color"] = "🔴"
        elif rsi_val <= 30:
            summary["rsi_signal"] = "売られ過ぎ"
            summary["rsi_color"] = "🟢"
        else:
            summary["rsi_signal"] = "中立"
            summary["rsi_color"] = "🟡"

    # MACD シグナル
    if "MACD" in df.columns and "MACD_Signal" in df.columns:
        macd_val = latest.get("MACD")
        macd_sig = latest.get("MACD_Signal")
        if pd.notna(macd_val) and pd.notna(macd_sig):
            summary["macd_value"] = round(macd_val, 4)
            if macd_val > macd_sig:
                summary["macd_signal"] = "買いシグナル"
                summary["macd_color"] = "🟢"
            else:
                summary["macd_signal"] = "売りシグナル"
                summary["macd_color"] = "🔴"

    # トレンド判定 (SMA ベース)
    if "SMA_20" in df.columns and "SMA_50" in df.columns:
        sma20 = latest.get("SMA_20")
        sma50 = latest.get("SMA_50")
        if pd.notna(sma20) and pd.notna(sma50):
            if price > sma20 > sma50:
                summary["trend_signal"] = "上昇トレンド"
                summary["trend_color"] = "🟢"
            elif price < sma20 < sma50:
                summary["trend_signal"] = "下降トレンド"
                summary["trend_color"] = "🔴"
            else:
                summary["trend_signal"] = "レンジ相場"
                summary["trend_color"] = "🟡"

    # ボリンジャーバンド シグナル
    if "BB_Upper" in df.columns and "BB_Lower" in df.columns:
        bb_upper = latest.get("BB_Upper")
        bb_lower = latest.get("BB_Lower")
        if pd.notna(bb_upper) and pd.notna(bb_lower):
            if price >= bb_upper:
                summary["bb_signal"] = "上限バンド接触"
                summary["bb_color"] = "🔴"
            elif price <= bb_lower:
                summary["bb_signal"] = "下限バンド接触"
                summary["bb_color"] = "🟢"
            else:
                summary["bb_signal"] = "バンド内"
                summary["bb_color"] = "🟡"

    return summary
