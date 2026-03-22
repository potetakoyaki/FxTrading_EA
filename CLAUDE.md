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

### v11.0 (current) - Range Market Guard
- **Macro-ER構造的レンジ検出**: H4の60期間ERで長期的なレンジ構造を検出（短期ER=20期間では偽トレンド判定が多い）
  - V11_MACRO_ER_PERIOD=60, V11_MACRO_ER_THRESHOLD=0.20
- **トレンドコンポーネント減衰（range regime限定）**: レンジ判定時にMomentum Burst(3pt→1pt)とH4 Trend(3pt→1pt)をキャップ
  - データ根拠: 2022-24でScore≥16のWR=37%（スコアが高いほど負ける逆転現象）
  - Burst/H4Trendは「トレンドの存在」を検出するが、レンジでは偽シグナル
- **スコア天井（range regime限定）**: max score=15で過信を防止
- **Macro-rangeピラミッドブロック**: 長期ERが低い時はtrend判定でもピラミッド不可
- Config: `USE_V11_RANGE=True`, `V11_BURST_CAP_IN_RANGE=1`, `V11_H4TREND_CAP_IN_RANGE=1`, `V11_RANGE_MAX_SCORE=15`
- **敗因分析**: 2022-24のSL決済率86%（1231/1428）、TP到達率8.5%。トレンド用TP=4.0xがレンジで到達不能
- **5期間A/Bテスト結果 (v10.1 → v11.0)**:

| 期間 | 市場環境 | v10.1 PF | v11.0 PF | v10.1 DD | v11.0 DD | v10.1 Ret | v11.0 Ret | 判定 |
|------|----------|---------|---------|---------|---------|----------|----------|------|
| 2016-18 | 低ボラ | 1.57 | **1.71** | 5.4% | 5.7% | +129% | +156% | BETTER |
| 2018-20 | トレンド | 1.71 | 1.62 | 7.3% | **5.6%** | +287% | +210% | WORSE(PF) |
| 2020-22 | コロナ | 1.60 | **1.76** | 16.6% | **9.7%** | +448% | +478% | BETTER |
| 2022-24 | レンジ | 1.23 | **1.28** | 14.6% | **11.8%** | +69% | +67% | BETTER |
| 2024-26 | 高ボラ | 1.31 | 1.28 | 10.4% | **10.0%** | +179% | +135% | NEUTRAL |

- **3/5 BETTER, 1 NEUTRAL, 1 WORSE。DD改善が顕著（2020-22: 16.6%→9.7%）**
- **2018-20 PF低下はピラミッドブロックによるトレンド利益減少。DD -1.7%はトレードオフとして許容**

### v10.1 - HIGH_VOL Exit Fix + MSS Removal
- **HIGH_VOL exit params をデフォルトに変更**: タイトすぎるexit（Partial 35%, BE 1.0x, Trail 0.6x, Ratchet 0.3）が早期決済→再エントリーの損失ループを引き起こしていた
  - HIGHVOL_PARTIAL_TP_RATIO: 0.35 → 0.5
  - HIGHVOL_BE_ATR_MULTI: 1.0 → 1.5
  - HIGHVOL_TRAIL_ATR_MULTI: 0.6 → 1.0
  - HIGHVOL_RATCHET_STEP: 0.3 → 0.5
- **MSS v11.0b 完全削除**: 10エージェント議論で不採用決定。V-reversal/Vol acceleration/Trend exhaustion検出は2024-26 Return -11%、オーバーフィッティングリスク
- **検証・不採用**: MSS（Market Structure Score）- 危険パターン検出フィルタ。5期間テストで一貫した改善なし
- **5期間リグレッションテスト結果**:

| 期間 | 市場環境 | Return | PF | WR | MaxDD | Sharpe | Trades |
|------|----------|--------|------|------|-------|--------|--------|
| 2016-18 | 低ボラ | +205.8% | 1.65 | 72.0% | 5.9% | 4.43 | 1,716 |
| 2018-20 | トレンド | +516.1% | 1.93 | 75.9% | 5.4% | 5.80 | 1,624 |
| 2020-22 | コロナ | +408.1% | 1.78 | 66.1% | 11.0% | 4.96 | 1,657 |
| 2022-24 | レンジ | -5.5% | 0.97 | 48.4% | 13.5% | -0.41 | 1,315 |
| 2024-26 | 高ボラ | +241.7% | 1.43 | 55.2% | 10.1% | 3.60 | 1,631 |

- **v10.0 → v10.1 改善 (2024-26)**: Return +174%→+242%, PF 1.33→1.43, DD 10.8%→10.1%
- **2020-22 DD大幅改善**: 17.2% → 11.0%（タイトHV exitが根本原因だった）

### v10.0 - Intelligent Regime Engine
- **Session-Regime Interaction**: セッション（Asian/London/NY）×レジーム（Trend/Range/HighVol）のマトリクスでロットスケール調整
  - London TREND: lot x1.1（トレンドフォロー最適セッション）
  - Asian TREND: lot x0.9（アジアはレンジ傾向）
  - HIGH_VOL全セッション: lot x0.4-0.5（保守的）
- **Dynamic Component Effectiveness**: 各スコアリングコンポーネントの直近精度をトラッキング
  - 勝率がベースラインの1.3倍超: +0.3ポイントブースト
  - 勝率がベースラインの0.7倍未満: -0.3ポイントペナルティ
  - H4 RSI Alignment（WR 47.2%）やVolume Climax（WR 38.3%）を自動的に減点
- **Adaptive Exit per Regime**: レジーム別出口戦略
  - TREND: パーシャルクローズ遅延（55%TP）、BE閾値高め（1.6x ATR）、ワイドトレーリング（1.1x）
  - RANGE: 早期パーシャル（40%TP）、早期BE（1.2x ATR）、タイトトレーリング（0.8x）
  - HIGH_VOL: 最速パーシャル（35%TP）、最速BE（1.0x ATR）、最タイトトレーリング（0.6x）
- **検証・不採用**: Regime Blending（パラメータ平均化でエッジ喪失）、Regime Memory（DD +5%悪化）
- **A/Bテスト**: 各機能を個別にテスト → Session(PF+0.04), Exit(PF+0.04), CompEff(DD-0.1%) が有効
- Config: `USE_V10_ENGINE=True`, `USE_SESSION_REGIME=True`, `USE_ADAPTIVE_EXIT=True`, `USE_COMPONENT_EFFECTIVENESS=True`
- **2024-2026バックテスト結果 (v9.0 → v10.0)**:

| Metric | v9.0 | v10.0 | Delta |
|--------|------|-------|-------|
| PF | 1.28 | **1.33** | **+0.05** |
| WinRate | 52.7% | **54.7%** | **+2.0%** |
| MaxDD | 10.4% | 10.8% | +0.4% |
| Return | +138.0% | **+174.1%** | **+36.1%** |
| Trades | 1734 | 1737 | +3 |
| Sharpe | 2.76 | **3.03** | **+0.27** |
| Calmar | 5.89 | **7.19** | **+1.30** |
| Expectancy | +239/trade | **+301/trade** | **+62** |
| Monthly WR | 56% | **59%** | **+3%** |
| SELL PnL | +171K | **+210K** | **+39K** |

- **PF, WR, Return, Sharpe, Calmar全指標改善。DDは微増(+0.4%)のみ**
- **SELL方向の収益性大幅改善**: +171K → +210K（+23%）

### v8.2 - Tighter Pyramid Volatility Gate
- **ピラミッドブロック閾値引き下げ**: vol_ratio > 1.2 でピラミッドをブロック（v8.1は1.5）
- vol_ratio 1.2-1.5の「デッドゾーン」でもピラミッドを制限し、高ボラ時の追加ポジション損失を抑制
- Config: `HIGH_VOL_PYRAMID_BLOCK=1.2`
- **検証・不採用**: Graduated SL (DD +4.5% 悪化), Consecutive Loss Cooldown (PF 1.07 壊滅)
- **2024-2026バックテスト結果 (v8.1 → v8.2)**:

| Metric | v8.1 | v8.2 | Delta |
|--------|------|------|-------|
| PF | 1.39 | 1.34 | -0.05 |
| WinRate | 54.7% | 54.1% | -0.6% |
| MaxDD | 11.0% | **10.9%** | -0.1% |
| Return | +187.3% | +149.6% | -37.7% |
| Trades | 1383 | 1256 | -127 |
| Pyramids | 728 | 588 | -140 |
| Jan2026 PnL | -27,305 | **-14,878** | +12,427 |

- **DD微改善、Jan2026損失ほぼ半減**: 高ボラ期のピラミッド140件を追加ブロック
- **リターン減少**: ピラミッド制限によりアグレッシブなポジション拡大を抑制。DDリスク低減とのトレードオフ

### v8.1 - High-Volatility Pyramid Block
- **高ボラ時ピラミッド制限**: vol_ratio > 1.5 の時にピラミッドエントリーをブロック
- ボラティリティが平均の1.5倍を超える局面では追加ポジションのリスクが大きいため抑制
- Config: `HIGH_VOL_PYRAMID_BLOCK=1.5`
- **2024-2026バックテスト結果 (v8.0 → v8.1)**:

| Metric | v8.0 | v8.1 | Delta |
|--------|------|------|-------|
| PF | 1.27 | **1.32** | +0.04 |
| WinRate | 53.5% | **54.3%** | +0.7% |
| MaxDD | 15.5% | **14.8%** | -0.7% |
| Return | +131.8% | **+157.4%** | +25.7% |
| Trades | 1749 | 1738 | -11 |
| Pyramids | 928 | 903 | -25 |

- **全指標改善、副作用ゼロ**: 25件の高ボラピラミッドをブロックしただけでリターン+25.7%改善

### v8.0 - ER Regime Detection (Range Market Filter)
- **Efficiency Ratio (ER) regime detection**: H4のER(20期間) < 0.3でレンジ/チョッピー相場と判定
- **レンジ相場でMIN_SCORE += 3**: チョッピー相場では高品質シグナルのみエントリー
- **トレード数約25%減少**: 低品質トレードをフィルタリング、勝率・PF向上
- Config: `REGIME_METHOD='er'`, `REGIME_ER_PERIOD=20`, `REGIME_ER_THRESHOLD=0.3`, `REGIME_SCORE_BOOST=3`
- **10年バックテスト結果 (v7.0 → v8.0)**:

| 期間 | 市場環境 | v7.0 PF | v8.0 PF | v7.0 DD | v8.0 DD | v8.0 Return | v8.0 Trades |
|------|----------|---------|---------|---------|---------|-------------|-------------|
| 2016-18 | 低ボラ | 1.72 | **1.76** | 7.2% | 6.9% | +298% | 1,552 |
| 2018-20 | トレンド | 1.61 | **1.88** | 6.2% | 6.3% | +225% | 1,427 |
| 2020-22 | コロナ | 1.63 | **1.82** | 18.0% | **6.7%** | +676% | 1,798 |
| 2022-24 | レンジ | 1.07 | **1.13** | 17.1% | 15.4% | +24% | 1,223 |
| 2024-26 | 高ボラ | 1.22 | **1.38** | 15.0% | 12.1% | +167% | 1,365 |

- **全5期間でPF改善（副作用ゼロ）**
- **10年通算**: PF=1.38→改善、WR=65.4%→改善、DD=10.1%→改善
- **Professional Grade Assessment: 10/10**
- 検証済み代替手法: ADX（悪化）、BBWidth（ER以下）、Mean-Reversion層（微小効果、不採用）

### v7.0 - Symmetric Trend-Following (Bull/Bear balanced)
- **H1 RSI symmetric scoring**: 60-70 (BUY) / 30-40 (SELL) に拡大 (旧: 60-65/35-40)
- **H4 RSI alignment symmetric**: H1フィルタを<75/>25に対称化 (旧: <70/>30)
- **S/R Level penalty撤廃**: 逆方向ペナルティ(-1)を削除、+1ボーナスのみ
- **Trend-aligned TP adjustment**: 順トレンドTP x1.2拡大、逆トレンドTP x0.8縮小
- **BUY/SELL directional breakdown**: レポートにBUY/SELL別勝率・損益を追加
- Config: `TREND_TP_EXTEND=1.2`, `TREND_TP_TIGHTEN=0.8`
- **10年バックテスト結果 (5期間)**:

| 期間 | 市場環境 | Return | PF | WR | DD | BUY PnL | SELL PnL |
|------|----------|--------|-----|------|------|---------|----------|
| 2016 | 低ボラ | +234% | 2.10 | 75% | 6.9% | +176K | +528K |
| 2017-18 | トレンド | +83% | 1.35 | 73% | 6.5% | +87K | +162K |
| 2019-20 | コロナ | +495% | 1.66 | 72% | 6.4% | +962K | +522K |
| 2021-22 | 急騰急落 | +231% | 1.44 | 60% | 18.2% | +262K | +431K |
| 2023-24 | 高金利レンジ | +14% | 1.07 | 49% | 17.2% | +58K | -16K |

- **v7.1 weak-trendフィルタ検証**: ADX/slope基準、score boost/TP縮小/lot縮小の3パターンを検証
  → 2023-24のPFは改善せず (スコアの予測力がゼロのため)。トレンド期間のみ改善。リバートして不採用。

### v6.0 - Professional Grade
- **Realistic transaction costs**: CSV実スプレッド + スリッページ(3pts) + コミッション($7/lot)
- **Score margin filter**: buy/sell score差が2以上必要（曖昧シグナル排除）
- **Time-decay SL tightening**: 12h以上含み損ポジションのSLを段階的に縮小
- **ATR ratchet trailing**: 利益拡大に応じてトレール幅を自動縮小
- **Walk-forward validation**: 6m train / 2m OOS / 2m step (9 fold, 78% pass)
- **Monte Carlo simulation**: 1000回トレード順序シャッフル (100%利益確率、95%CI DD=20.4%)
- **Parameter sensitivity analysis**: 主要パラメータの感度検証 (--sensitivity)
- **Professional grade assessment**: 自動評価スコア (9/10 PROFESSIONAL GRADE)
- Result: +155.4%, PF=1.30, WR=52.7%, DD=15.1%, 1739 trades
- SL損失 v5.2: -101万→v6.0: -28万 (72%削減)
- 月次WR: 64%→68%

### v5.2 - Trend-aligned SL + CSV fallback
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
- **v11.0: レンジ相場スコア逆転問題**: 2022-24分析でScore≥16のWR=37%（高スコアほど負ける）を発見。原因はMomentum Burst(3pt)+H4 Trend(3pt)がレンジで偽トレンドシグナルを発信。range regime限定でキャップ(3pt→1pt)
- **v11.0: Macro ER vs Short ER**: ER(20)は短期変動で"trend"判定が揺れる。ER(60)で構造的レンジを捕捉し、ピラミッドのみブロック。スコア変更はregime限定（トレンド期間への副作用防止）
- **v11.0: TP制限を不採用**: 初期テストではtrend regime時のTP=4.0→2.5を試したが、2018-20/2024-26のリターンが-77%/-112%大幅悪化。TP制限はトレンド利益を直接削るため却下
- **v10.1: HIGH_VOL exit デフォルト化**: タイトなexit（BE 1.0x, Trail 0.6x）が高ボラ時に早期決済→同方向再エントリー→再度SLの損失ループを生んでいた。デフォルト（BE 1.5x, Trail 1.0x）で2020-22 DD 17.2%→11.0%
- **v10.1不採用: MSS（Market Structure Score）**: V-reversal/Vol acceleration/Trend exhaustion検出。10エージェント議論（全員一致）で不採用。2024-26 Return -11%、閾値チューニングがオーバーフィッティング
- **v10.0: A/Bテスト駆動開発**: 5機能を個別テスト→3機能採用、2機能不採用。データ駆動で判断
- **v10.0不採用: Regime Blending**: レジーム間パラメータのEMA平均化。Trend TP=4.0とRange TP=2.0を混ぜると3.0になり、どちらでも最適でなくなる。PF 1.17に悪化
- **v10.0不採用: Regime Memory**: レジーム別PFトラッキングでMIN_SCORE調整。トレード数半減でDDが+5%悪化。分散投資効果の喪失
- **v10.0: Session-Regime**: London×Trendで+10%ロットブーストが最適。1.2以上はDD悪化
- **v10.0: Component Effectiveness**: WR 47%以下のコンポーネント（H4 RSI, Volume Climax）を自動減点。DDコントロール効果
- **v10.0: Adaptive Exit**: TREND=ワイド→利益拡大、RANGE=タイト→早期利確、HIGH_VOL=最タイト→保護。Exit単体ではDD増だがCompEffと組合せでDD制御
- **v8.2: ピラミッドブロック閾値1.2**: vol_ratio 1.2-1.5の「デッドゾーン」でもピラミッドを制限。Jan2026損失-27K→-15K（ほぼ半減）
- **v8.2不採用: Graduated SL**: SLを段階的に広げるとDD +4.5%悪化。SLが広い=損切り時の損失額が大きい
- **v8.2不採用: Consecutive Loss Cooldown**: 3連続SL後のクールダウン延長でPF 1.07に壊滅。トレード数半減で利益機会を逃す
- **v8.1: 高ボラピラミッド制限**: vol_ratio > 1.5（ATRが平均の1.5倍超）の局面ではピラミッドをブロック。高ボラ時の追加ポジションは損失が拡大しやすく、25件ブロックだけでリターン+25.7%改善
- **v8.0: ERレジーム検出**: Efficiency Ratio（方向効率比）でトレンド/レンジを判定。ADXやBBWidthよりも優れた結果。レンジ相場ではMIN_SCOREを+3して低品質トレードをフィルタ
- **v8.0: Mean-Reversion不採用**: RSI極値・BB反転の逆張り層を検証したが、ER regime detectionより効果が小さく複雑性が増すため不採用
- **v7.0: BUY/SELL対称スコアリング**: ベア相場でもSELLが適切にトリガーされるよう、RSI・H4RSI・S/Rのスコアリングを対称化
- **v7.0: S/Rペナルティ撤廃**: S/Rレベルで逆方向にペナルティを課すのを廃止（ベア相場でサポート付近のBUY偏重を防ぐ）
- **v7.0: TP非対称調整**: SLだけでなくTPも順/逆トレンドで調整。順トレンドはTP拡大でトレンドに乗り、逆トレンドはTP縮小で素早く利確
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
