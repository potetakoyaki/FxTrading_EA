"""
📊 マルチタイムフレーム分析モジュール (multi_tf_analyzer.py)

全足種（5m〜週足）でそれぞれ独立に高度分析を実行し、
加重統合して1つの総合予測を算出する。
長期足ほど重みを大きくすることでブレの少ない安定した予測を実現。
"""

import pandas as pd
import numpy as np
import yfinance as yf
from advanced_analyzer import run_full_analysis, generate_rationale

# ──────────────────────────────────────────────
# 足種設定
# ──────────────────────────────────────────────

TIMEFRAMES = {
    "5m":  {"period": "5d",  "weight": 0.05, "label": "5分足",   "forecast": 144},
    "15m": {"period": "1mo", "weight": 0.08, "label": "15分足",  "forecast": 96},
    "30m": {"period": "1mo", "weight": 0.10, "label": "30分足",  "forecast": 48},
    "1h":  {"period": "3mo", "weight": 0.15, "label": "1時間足", "forecast": 72},
    "4h":  {"period": "6mo", "weight": 0.20, "label": "4時間足", "forecast": 42},
    "1d":  {"period": "2y",  "weight": 0.25, "label": "日足",    "forecast": 90},
    "1wk": {"period": "5y",  "weight": 0.17, "label": "週足",    "forecast": 26},
}


def fetch_all_timeframes(ticker: str) -> dict:
    """全足種のデータを一括取得"""
    results = {}
    t = yf.Ticker(ticker)

    for interval, cfg in TIMEFRAMES.items():
        try:
            df = t.history(period=cfg["period"], interval=interval)
            if not df.empty and len(df) >= 30:
                results[interval] = df
        except Exception:
            pass

    return results


def analyze_all_timeframes(ticker: str, dfs: dict) -> dict:
    """
    全足種で独立に分析を実行し、統合結果を返す。

    Returns:
        {
            "individual": {interval: analysis_result, ...},
            "unified": {composite score, direction, rationale},
            "tf_summary": [{interval info + score}, ...],
        }
    """
    individual = {}
    tf_summary = []

    for interval, df in dfs.items():
        cfg = TIMEFRAMES[interval]
        # 分足/時足のDXYは不要（日足以上のみ）
        fetch_dxy = interval in ("1d", "1wk")

        try:
            analysis = run_full_analysis(df, ticker=ticker, fetch_dxy=fetch_dxy)
            individual[interval] = analysis

            comp = analysis.get("composite", {})
            tf_summary.append({
                "interval": interval,
                "label": cfg["label"],
                "weight": cfg["weight"],
                "score": comp.get("score", 0),
                "direction": comp.get("direction", "中立"),
                "strength": comp.get("strength", 0),
                "signals": comp.get("total_signals", 0),
                # 個別分析サマリ
                "elliott": analysis.get("elliott", {}).get("phase", "—"),
                "adx": analysis.get("trend_strength", {}).get("adx", 0),
                "adx_strength": analysis.get("trend_strength", {}).get("trend_strength", "—"),
                "channel": analysis.get("channel", {}).get("condition", "—"),
                "fibo_trend": analysis.get("fibonacci", {}).get("trend", "—"),
            })
        except Exception as e:
            tf_summary.append({
                "interval": interval, "label": cfg["label"],
                "weight": cfg["weight"], "score": 0,
                "direction": "エラー", "strength": 0, "signals": 0,
                "elliott": "—", "adx": 0, "adx_strength": "—",
                "channel": "—", "fibo_trend": "—",
            })

    # ──────── 統合スコア（全足種の加重平均） ────────
    total_weight = 0
    weighted_score = 0

    for item in tf_summary:
        if item["direction"] != "エラー":
            w = item["weight"]
            weighted_score += item["score"] * w
            total_weight += w

    if total_weight > 0:
        unified_score = weighted_score / total_weight
    else:
        unified_score = 0

    unified_score = max(-1.0, min(1.0, unified_score))

    if unified_score > 0.15:
        unified_direction = "上昇"
    elif unified_score < -0.15:
        unified_direction = "下降"
    else:
        unified_direction = "中立"

    # ──── 方向の一致度（全足種） ────
    bullish_count = sum(1 for t in tf_summary if t["score"] > 0.1 and t["direction"] != "エラー")
    bearish_count = sum(1 for t in tf_summary if t["score"] < -0.1 and t["direction"] != "エラー")
    neutral_count = sum(1 for t in tf_summary if abs(t["score"]) <= 0.1 and t["direction"] != "エラー")
    valid_count = sum(1 for t in tf_summary if t["direction"] != "エラー")

    if valid_count > 0:
        consensus = max(bullish_count, bearish_count) / valid_count
    else:
        consensus = 0

    unified = {
        "score": round(unified_score, 3),
        "direction": unified_direction,
        "strength": round(abs(unified_score), 3),
        "consensus": round(consensus, 2),
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "valid_count": valid_count,
    }

    return {
        "individual": individual,
        "unified": unified,
        "tf_summary": tf_summary,
    }


def generate_unified_rationale(mtf_result: dict, ticker: str, current_price: float) -> str:
    """
    全足種の分析結果を統合して日本語の予測根拠を生成。
    """
    unified = mtf_result["unified"]
    tf_summary = mtf_result["tf_summary"]
    individual = mtf_result["individual"]

    lines = []
    direction = unified["direction"]
    score = unified["score"]

    # ──── ヘッダー ────
    lines.append(f"## 📊 マルチタイムフレーム総合判定: **{direction}**（スコア: {score:+.3f}）")
    lines.append("")
    lines.append(f"全{unified['valid_count']}足種を分析。"
                 f"上昇: **{unified['bullish_count']}**足、"
                 f"下降: **{unified['bearish_count']}**足、"
                 f"中立: **{unified['neutral_count']}**足。"
                 f"方向一致度: **{unified['consensus']:.0%}**")
    lines.append("")

    # ──── 各足種の要約 ────
    lines.append("---")
    lines.append("")

    for item in tf_summary:
        if item["direction"] == "エラー":
            continue

        interval = item["interval"]
        label = item["label"]
        s = item["score"]
        w = item["weight"]
        d = item["direction"]

        icon = "🟢" if s > 0.1 else ("🔴" if s < -0.1 else "⚪")
        weight_pct = f"{w:.0%}"

        lines.append(f"### {icon} {label}（重み: {weight_pct}、スコア: {s:+.2f} → {d}）")

        # 各分析のサマリ
        details = []
        details.append(f"エリオット: {item['elliott']}")
        details.append(f"ADX: {item['adx']:.0f}（{item['adx_strength']}）")
        details.append(f"チャネル: {item['channel']}")
        details.append(f"フィボ: {item['fibo_trend']}")
        lines.append("　|　".join(details))

        # 個別分析からの追加コメント
        analysis = individual.get(interval, {})
        fib = analysis.get("fibonacci", {})
        ch = analysis.get("channel", {})
        ew = analysis.get("elliott", {})
        ts = analysis.get("trend_strength", {})

        # 意味のあるコメントを1-2行で
        comments = []
        if ew.get("expected_target"):
            comments.append(f"エリオット波動ターゲット: **{ew['expected_target']:,.4f}**")
        if fib.get("nearest_support") and fib.get("nearest_resist"):
            comments.append(f"フィボS/R: {fib['nearest_support']:,.4f} 〜 {fib['nearest_resist']:,.4f}")
        if ch.get("condition") and "ブレイクアウト" in ch.get("condition", ""):
            comments.append(f"⚡ **{ch['condition']}**を検出")
        if ts.get("adx", 0) >= 25:
            comments.append(f"トレンド明確（ADX={ts['adx']:.0f}、{ts.get('direction_label', '')}）")
        elif ts.get("adx", 0) < 20:
            comments.append("レンジ相場")

        if comments:
            lines.append("→ " + "、".join(comments))

        lines.append("")

    # ──── 日足・週足の詳細（最も重要な足種） ────
    for key_tf in ["1d", "1wk"]:
        if key_tf in individual:
            analysis = individual[key_tf]
            dxy = analysis.get("dxy", {})
            if dxy.get("confidence", 0) > 0.1:
                label = TIMEFRAMES[key_tf]["label"]
                corr = dxy.get("correlation", 0)
                corr_type = "正相関" if corr > 0 else "逆相関"
                lines.append(f"**DXY分析（{label}）**: 相関={corr:.3f}（{corr_type}）、"
                             f"DXYトレンド={'上昇' if dxy.get('dxy_trend')=='up' else '下落'}")

    lines.append("")

    # ──── 総合判断 ────
    lines.append("---")
    lines.append("")
    lines.append("### 🎯 総合判断")

    if unified["consensus"] >= 0.7:
        lines.append(f"全足種の **{unified['consensus']:.0%}** が同じ方向（{direction}）を示しており、"
                     f"**信頼度の高い**シグナルです。")
    elif unified["consensus"] >= 0.5:
        lines.append(f"過半数の足種が{direction}を示していますが、一部で乖離があります。"
                     f"短期的なノイズの可能性があるため、位置確認を推奨します。")
    else:
        lines.append("足種間で方向が分かれており、**明確なトレンドは不在**です。"
                     "レンジ内で推移する可能性が高く、サポート/レジスタンス付近での反転に注意してください。")

    if fib_target := _get_key_fib_target(individual, direction):
        lines.append(f"主要フィボターゲット: **{fib_target:,.4f}**")

    return "\n".join(lines)


def _get_key_fib_target(individual: dict, direction: str) -> float | None:
    """日足のフィボターゲットから最も重要なターゲットを取得"""
    for key_tf in ["1d", "4h", "1wk"]:
        analysis = individual.get(key_tf, {})
        fib = analysis.get("fibonacci", {})
        targets = fib.get("target_levels", {})
        if targets:
            values = list(targets.values())
            if direction == "上昇":
                return min(v for v in values if v > 0)
            elif direction == "下降":
                return max(values)
    return None
