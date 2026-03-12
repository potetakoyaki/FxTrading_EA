"""
🔮 予測モジュール (forecaster.py)
Prophet / 回帰 / MA外挿 + 高度分析統合
"""

import pandas as pd
import numpy as np
from datetime import timedelta


def _generate_future_dates(last_date, periods: int, interval: str):
    if hasattr(last_date, "tz") and last_date.tz is not None:
        last_date = last_date.tz_localize(None)

    interval_map = {
        "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30), "1h": timedelta(hours=1),
        "4h": timedelta(hours=4), "1d": timedelta(days=1), "1wk": timedelta(weeks=1),
    }
    delta = interval_map.get(interval, timedelta(days=1))
    dates = []
    current = last_date
    for _ in range(periods):
        current = current + delta
        dates.append(current)
    return dates


def prophet_forecast(df: pd.DataFrame, periods: int = 30, interval: str = "1d") -> dict:
    from prophet import Prophet
    import logging
    logging.getLogger("prophet").setLevel(logging.WARNING)
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    prophet_df = df[["Close"]].reset_index()
    prophet_df.columns = ["ds", "y"]
    prophet_df["ds"] = pd.to_datetime(prophet_df["ds"])
    if prophet_df["ds"].dt.tz is not None:
        prophet_df["ds"] = prophet_df["ds"].dt.tz_localize(None)

    is_intraday = interval in ("5m", "15m", "30m", "1h", "4h")
    model = Prophet(
        daily_seasonality=is_intraday,
        weekly_seasonality=True,
        yearly_seasonality=not is_intraday,
        changepoint_prior_scale=0.05,
    )
    model.fit(prophet_df)

    freq_map = {
        "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "h", "4h": "4h", "1d": "D", "1wk": "W",
    }
    future = model.make_future_dataframe(periods=periods, freq=freq_map.get(interval, "D"))
    forecast = model.predict(future)

    last_date = prophet_df["ds"].max()
    future_forecast = forecast[forecast["ds"] > last_date].copy()

    return {
        "dates": future_forecast["ds"].tolist(),
        "predicted": future_forecast["yhat"].tolist(),
        "upper": future_forecast["yhat_upper"].tolist(),
        "lower": future_forecast["yhat_lower"].tolist(),
        "method": "Prophet",
    }


def regression_forecast(df: pd.DataFrame, periods: int = 30, degree: int = 2, interval: str = "1d") -> dict:
    from sklearn.preprocessing import PolynomialFeatures
    from sklearn.linear_model import LinearRegression
    from sklearn.pipeline import make_pipeline

    close = df["Close"].values
    n = len(close)
    X = np.arange(n).reshape(-1, 1)
    model = make_pipeline(PolynomialFeatures(degree), LinearRegression())
    model.fit(X, close)

    y_pred_train = model.predict(X)
    residual_std = np.std(close - y_pred_train)

    X_future = np.arange(n, n + periods).reshape(-1, 1)
    y_future = model.predict(X_future)
    future_dates = _generate_future_dates(df.index[-1], periods, interval)

    distance_factor = np.sqrt(np.arange(1, periods + 1))
    upper = y_future + 1.96 * residual_std * distance_factor
    lower = y_future - 1.96 * residual_std * distance_factor

    return {
        "dates": future_dates,
        "predicted": y_future.tolist(),
        "upper": upper.tolist(),
        "lower": lower.tolist(),
        "method": f"回帰 (次数{degree})",
    }


def ma_extrapolation_forecast(df: pd.DataFrame, periods: int = 30, ma_window: int = 20, interval: str = "1d") -> dict:
    close = df["Close"].values
    if len(close) < ma_window:
        ma_window = max(5, len(close) // 2)

    sma = pd.Series(close).rolling(window=ma_window).mean().dropna().values
    slopes = np.diff(sma[-ma_window:])
    avg_slope = np.mean(slopes)

    last_sma = sma[-1]
    predicted = [last_sma + avg_slope * (i + 1) for i in range(periods)]

    lookback = min(ma_window * 2, len(close))
    returns = np.diff(close[-lookback:]) / close[-lookback:-1]
    volatility = np.std(returns) * np.sqrt(np.arange(1, periods + 1))
    last_price = close[-1]

    upper = [p + last_price * v * 1.96 for p, v in zip(predicted, volatility)]
    lower = [p - last_price * v * 1.96 for p, v in zip(predicted, volatility)]
    future_dates = _generate_future_dates(df.index[-1], periods, interval)

    return {
        "dates": future_dates,
        "predicted": predicted,
        "upper": upper,
        "lower": lower,
        "method": f"MA外挿 ({ma_window})",
    }


def composite_forecast(df: pd.DataFrame, analysis: dict, periods: int = 30, interval: str = "1d") -> dict:
    """
    全分析結果を統合した複合予測。
    チャネル回帰 + フィボターゲット + エリオット方向 + DXY相関 + ADXトレンド
    を加重ブレンドして予測ラインを生成する。
    """
    from advanced_analyzer import apply_analysis_to_forecast

    close = df["Close"].values
    n = len(close)
    current_price = float(close[-1])

    # ベース: チャネルの中心線を使う（最もバランスが良い）
    channel = analysis.get("channel", {})
    if channel.get("future_center") and len(channel["future_center"]) >= periods:
        base_forecast = channel["future_center"][:periods]
    else:
        # フォールバック: 直近トレンドの線形延長
        recent = close[-min(20, n):]
        X = np.arange(len(recent)).reshape(-1, 1)
        from sklearn.linear_model import LinearRegression
        model = LinearRegression()
        model.fit(X, recent)
        X_future = np.arange(len(recent), len(recent) + periods).reshape(-1, 1)
        base_forecast = model.predict(X_future).flatten().tolist()

    # 高度分析によるバイアス適用
    adjusted = apply_analysis_to_forecast(base_forecast, analysis, periods)

    # 信頼区間（ボラティリティベース + チャネル幅）
    lookback = min(40, n)
    returns = np.diff(close[-lookback:]) / close[-lookback:-1]
    vol = np.std(returns)
    channel_width = channel.get("channel_width", current_price * vol * 5)

    upper = []
    lower = []
    for i, val in enumerate(adjusted):
        distance = np.sqrt(i + 1)
        band = max(channel_width * 0.5, current_price * vol * distance * 1.5)
        upper.append(val + band)
        lower.append(val - band)

    future_dates = _generate_future_dates(df.index[-1], periods, interval)

    # スコアを方法名に含める
    composite = analysis.get("composite", {})
    score = composite.get("score", 0)
    direction = composite.get("direction", "中立")

    return {
        "dates": future_dates,
        "predicted": adjusted,
        "upper": upper,
        "lower": lower,
        "method": f"複合分析 ({direction})",
    }


def get_forecast_summary(forecast: dict, current_price: float) -> dict:
    if not forecast or not forecast.get("predicted"):
        return {}

    predicted = forecast["predicted"]
    upper = forecast["upper"]
    lower = forecast["lower"]
    final_pred = predicted[-1]

    change = final_pred - current_price
    change_pct = (change / current_price) * 100 if current_price != 0 else 0

    return {
        "method": forecast["method"],
        "final_price": round(final_pred, 4),
        "mid_price": round(predicted[len(predicted) // 2], 4),
        "upper_bound": round(upper[-1], 4),
        "lower_bound": round(lower[-1], 4),
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
        "days": len(predicted),
        "direction": "上昇" if change > 0 else ("下降" if change < 0 else "横ばい"),
        "direction_color": "🟢" if change > 0 else ("🔴" if change < 0 else "🟡"),
    }
