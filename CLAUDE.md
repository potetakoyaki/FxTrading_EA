# AntigravityMTF EA - CLAUDE.md

## Project Overview
Gold (XAUUSD) 自動売買EA。MT5用MQL5コードとPythonバックテストシステム。
30万円スタート、M15エントリー、H1/H4マルチタイムフレーム分析。

## Architecture

### Core Files
- `backtest_gold.py` - メインバックテスター (GoldConfig, GoldBacktester)
- `backtest_csv.py` - CSV読み込み + バックテスト実行ラッパー
- `AntigravityMTF_EA_Gold.mq5` - MT5用EA本体 (最新版)

### Data Files (MT5 ExportHistory)
- `XAUUSD_M15.csv`, `XAUUSD_H1.csv`, `XAUUSD_H4.csv` - Gold OHLCV
- `USDJPY_H1.csv` - USD相関用
- Period: 2024-03 ~ 2026-03 (約2年分)

### Support Files
- `backtest_csv.py` - `load_csv()`, `generate_h1_from_h4()`, `generate_m15_from_h1()`, `merge_and_fill()`
- `backtest_usdjpy.py` - USDJPY単体バックテスター
- `backtest_ea.py`, `backtest_bearmarket.py`, `backtest_threelayer.py` - 他EA用
- `advanced_analyzer.py`, `multi_tf_analyzer.py`, `forecaster.py` - 分析ツール
- `app.py` - Webダッシュボード
- `ExportHistory.mq5` - MT5データエクスポートスクリプト

## Scoring System (v4.0: max 27 points)
15コンポーネント。MIN_SCORE=9で閾値判定。buy_score > sell_scoreでBUY、逆でSELL。

| # | Component | Points | Source |
|---|-----------|--------|--------|
| 1 | H4 Trend (MA cross + DI) | 3 | H4 |
| 2 | H1 MA Direction | 2 | H1 |
| 3 | H1 RSI | 1 | H1 |
| 4 | H1 BB Bounce | 1 | H1 |
| 5 | M15 MA Cross | 2 | M15 |
| 6 | Channel Regression | 1 | H1 |
| 7 | Momentum | 1 | M15 |
| 8 | Session Bonus | 1 | Time |
| 9 | USD Correlation | 2 | USDJPY |
| 10 | RSI Divergence | 2 | H1 |
| 11 | S/R Level | +1/-1 | H1 |
| 12 | Candle Pattern | 1 | H1 |
| 13 | H4 RSI Alignment | 1 | H4 |
| 14 | Momentum Burst | 3 | Multi |
| 15 | Volume Climax | 2 | H1 |

## Version History

### v5.2 (current) - Trend-aligned SL + CSV fallback
- **H4 SMA(50) slope** (20-bar) でマクロトレンド方向を判定
- **順トレンド**: SL x1.3 (プルバック耐性向上)
- **逆トレンド**: SL x0.7 (素早い損切り)
- `__main__` でCSVファイル優先読み込み (yfinanceフォールバック)
- Config: `H4_SLOPE_PERIOD=20`, `TREND_SL_WIDEN=1.3`, `TREND_SL_TIGHTEN=0.7`
- Result: +220.4%, PF=1.35, WR=53.4%, DD=14.7%

### v5.1 - MIN_LOT adjustment
- MIN_LOT 0.01 確定 (0.02/0.05は2月に停止する問題)

### v4.0 - Defense + Attack
- Defense: News filter, Weekend close, Circuit breaker, Crash ATR
- Attack: Momentum burst, Volume climax, Pyramiding (max 3), Reversal mode
- Dynamic MIN_SCORE: DD 10%→12, 15%→15, 20%→18

### v3.0 - Multi-indicator scoring (27pt scale)
- USD Correlation, RSI Divergence, S/R Levels, Candle Patterns
- H4 RSI Alignment, Chandelier Exit, Equity Curve Filter, Adaptive Sizing

### v2.0 - ATR-based risk management
- ATR SL/TP, Volatility regime, Session bonus, Momentum, Partial close

## Key Design Decisions
- **スコアペナルティ方式は不採用**: カウンタートレンドへのスコア減点はフルバックテストを大幅に悪化させる (逆張りでも利益が出るケースが多いため)
- **SL非対称調整を採用**: トレード自体はブロックせず、SL幅で順/逆トレンドを差別化
- **ゴールドの特性**: 下落トレンドでも反発が強いため、BEAR期間のBUY比率が46%残るのは合理的
- **2月問題**: 500ドル上昇中にSELL 12件 (-19K JPY) は既知。SLタイト化で-14Kに軽減済み

## Running Backtests

```bash
# Full backtest (CSV auto-detect)
python3 backtest_gold.py

# CSV backtest (explicit)
python3 backtest_csv.py

# Quick test in Python
from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester
h4 = load_csv('XAUUSD_H4.csv')
h1 = merge_and_fill(load_csv('XAUUSD_H1.csv'), generate_h1_from_h4(h4))
m15 = merge_and_fill(load_csv('XAUUSD_M15.csv'), generate_m15_from_h1(h1))
usdjpy = load_csv('USDJPY_H1.csv')
bt = GoldBacktester(GoldConfig())
bt.run(h4, h1, m15, usdjpy_df=usdjpy)
print(bt.get_report())
```

## Important Notes
- yfinanceはプロキシ環境で403エラーになることがある → CSV優先で回避済み
- バックテスト期間を短縮する場合、H4のSMA(50)+slope(20)に70バー以上の先行データが必要
- MQL5コードの更新時は `AntigravityMTF_EA_Gold.mq5` を編集
