//+------------------------------------------------------------------+
//|                              AntigravityMTF_EA_Gold.mq5          |
//|            ゴールド(XAUUSD)専用 マルチタイムフレーム EA             |
//|            v13.0: Multi-scale ER regime + MAE/MFE + Spike detect   |
//+------------------------------------------------------------------+
// CODEX-FIX: NEW HIGH #9 - Structural overfit risk documentation
// This EA uses 47 input parameters and ~200 hardcoded parameters across 15 scoring
// components. Mitigations against structural overfitting:
//   1. Walk-Forward Analysis (WFA) with 16 windows validates parameter stability
//   2. Monte Carlo simulation (1000 iterations) tests robustness to trade order/timing
//   3. Out-of-sample holdout period (6+ months) confirms generalization
//   4. Most parameters are hardcoded at WFA-validated defaults to prevent over-optimization
// Traders should re-run WFA periodically (quarterly) and monitor live vs backtest divergence.
#property copyright "Antigravity Trading System"
#property version   "13.00"
#property description "XAUUSD専用 v13.0: マルチスケールER + コンポーネント相関キャップ + スパイク検知 + MAE/MFE品質 + 段階的リバーサル"

#include <Trade/Trade.mqh>


//+------------------------------------------------------------------+
//| 入力パラメータ (v12.2: 156→47に削減、内部パラメータはconst化)       |
//| トレーダーが調整すべきパラメータのみUIに表示                         |
//| 内部実装の詳細はconst化しロジックは完全保持                          |
//+------------------------------------------------------------------+

// ================================================================
//  ESSENTIAL INPUT PARAMETERS — トレーダーが調整すべきパラメータ (47個)
// ================================================================

input group "=== リスク管理 ==="
input double RiskPercent       = 0.3;      // リスク% (ATR-SLで自動調整)
input double MaxLots           = 0.50;     // 最大ロット
input double MinLots           = 0.01;     // 最小ロット
input int    MaxSpread         = 50;       // 最大スプレッド(ポイント)
input int    MagicNumber       = 20260224; // マジックナンバー
input double MaxDrawdownPct    = 6.0;      // DD%でリスク1/4
input double DDHalfRiskPct     = 2.5;      // DD%でリスク半減

input group "=== 損益設定（ATRベース） ==="
input double SL_ATR_Multi      = 1.2;      // SL = M15 ATR x 倍率 (WFA: 1.2, PF+0.36)
input double TP_ATR_Multi      = 4.0;      // TP = M15 ATR x 倍率 (WFA: 4.0, トレンド追従)
input double Trail_ATR_Multi   = 1.0;      // トレーリング = ATR x 倍率
input double BE_ATR_Multi      = 0.8;      // 建値移動 = ATR x 倍率 (WFA: 0.8, 早期資本保護)
input double MinSL_Points      = 200.0;    // 最小SL (ポイント)
input double MaxSL_Points      = 1500.0;   // 最大SL (ポイント)

input group "=== スコアリング・エントリー ==="
input int    MinEntryScore     = 12;       // 最低スコア 12/27 (WFA: 12, PF+0.16)
input int    ScoreMarginMin    = 2;        // buy/sellスコア差の最低要件
input int    CooldownMinutes   = 480;      // SL後クールダウン(分) (WFA: 480=8時間, DD削減)

input group "=== 時間フィルター ==="
input int    TradeStartHour    = 8;        // 取引開始時間(サーバー時間)
input int    TradeEndHour      = 22;       // 取引終了時間(サーバー時間)
input int    GMTOffset         = 2;        // FIX: Issue #11 - ブローカーGMTオフセット (GMT+2=default)
input bool   AvoidFriday       = true;     // 金曜18時以降エントリー禁止

input group "=== 防御 ==="
input bool   UseNewsFilter     = true;     // 経済指標フィルター
input bool   UseWeekendClose   = true;     // 金曜クローズ
input int    FridayCloseHour   = 20;       // 金曜クローズ時刻(サーバー時間)
input int    StaleTradeHours   = 48;       // 塩漬け決済(時間)
input double DailyMaxLossPct   = 2.0;      // 日次最大損失%

input group "=== 半利確 ==="
input bool   UsePartialClose   = true;     // 半分利確を有効化
input double PartialCloseRatio = 0.5;      // 利確するポジション割合
input double PartialTP_Ratio   = 0.5;      // TP距離の何%で半利確

input group "=== ピラミッディング ==="
input int    MaxPyramidPositions = 1;      // ピラミッディング上限 (WFA: 1, DD 21.6→11.0%)
input double PyramidLotDecay   = 0.5;      // ピラミッド追加ロット減衰率

input group "=== トレンドSL/TP調整 ==="
input double Trend_SL_Widen    = 1.5;      // 順トレンドSL倍率 (WFA: 1.5, プルバック耐性)
input double Trend_SL_Tighten  = 0.6;      // 逆トレンドSL倍率 (WFA: 0.6, 素早い損切り)
input double Trend_TP_Extend   = 1.2;      // 順トレンドTP倍率
input double Trend_TP_Tighten  = 0.8;      // 逆トレンドTP倍率

input group "=== レジーム適応 ==="
input bool   UseRegimeAdaptive = true;     // レジーム適応戦略ON/OFF
input double TrendSLMulti      = 1.5;      // トレンド時SL倍率
input double TrendTPMulti      = 4.0;      // トレンド時TP倍率
input double RangeSLMulti      = 1.1;      // レンジ時SL倍率
input double RangeTPMulti      = 1.8;      // レンジ時TP倍率
input double HighVolSLMulti    = 2.0;      // 高ボラ時SL倍率
input double HighVolTPMulti    = 3.5;      // 高ボラ時TP倍率

input group "=== USD相関フィルター ==="
input bool   UseCorrelation    = true;     // USD相関フィルター
input string CorrelationSymbol = "USDJPY"; // 相関シンボル

// ================================================================
//  HARDCODED PARAMETERS — 内部実装の詳細、WFA検証済みデフォルト値
//  input -> const に変更。変数名・型・値は完全に保持。
//  ロジックの変更は一切なし。
// ================================================================

// --- インジケーター期間 (標準値、全バージョンで固定) ---
const int    ATR_Period_SL     = 14;       // HARDCODED: 標準ATR期間、全テストで14固定
const int    VolRegime_Period  = 50;       // HARDCODED: ボラ平均期間、変更実績なし
const double VolRegime_Low     = 0.7;      // HARDCODED: WFA検証済み低ボラ閾値
const double VolRegime_High    = 1.5;      // HARDCODED: WFA検証済み高ボラ閾値
const double HighVol_SL_Bonus  = 0.0;      // HARDCODED: WFA検証済み (0.0=ボーナスなし, PF+0.07)
const int    H4_MA_Fast        = 20;       // HARDCODED: H4 SMA(20)、全バージョンで固定
const int    H4_MA_Slow        = 50;       // HARDCODED: H4 SMA(50)、全バージョンで固定
const int    H4_ADX_Period     = 14;       // HARDCODED: 標準ADX期間
const int    H4_ADX_Threshold  = 20;       // HARDCODED: WFA検証済み ADX閾値
const int    H1_MA_Fast        = 10;       // HARDCODED: H1 EMA(10)、全バージョンで固定
const int    H1_MA_Slow        = 30;       // HARDCODED: H1 EMA(30)、全バージョンで固定
const int    H1_RSI_Period     = 14;       // HARDCODED: 標準RSI期間
const int    H1_BB_Period      = 20;       // HARDCODED: 標準BB期間
const double H1_BB_Deviation   = 2.0;      // HARDCODED: 標準BB偏差
const int    M15_MA_Fast       = 5;        // HARDCODED: M15 EMA(5)、全バージョンで固定
const int    M15_MA_Slow       = 20;       // HARDCODED: M15 EMA(20)、全バージョンで固定
const int    H4_RSI_Period     = 14;       // HARDCODED: 標準RSI期間
const int    H4_Slope_Period   = 20;       // HARDCODED: H4スロープ計算期間、WFA固定

// --- 常時ON機能トグル (無効にすると性能低下が確認済み) ---
const bool   UseSessionBonus   = true;     // HARDCODED: WFA全テストでtrue
const bool   UseMomentum       = true;     // HARDCODED: WFA全テストでtrue
const bool   UseDivergence     = true;     // HARDCODED: WFA検証済みtrue
const bool   UseSRLevels       = true;     // HARDCODED: WFA検証済みtrue
const bool   UseCandlePatterns = true;     // HARDCODED: WFA検証済みtrue
const bool   UseH4RSI          = true;     // HARDCODED: WFA検証済みtrue
const bool   UseChandelierExit = true;     // HARDCODED: WFA検証済みtrue
const bool   UseEquityCurveFilter = true;  // HARDCODED: WFA検証済みtrue
const bool   UseAdaptiveSizing = true;     // HARDCODED: WFA検証済みtrue
const bool   UseMomentumBurst  = true;     // HARDCODED: WFA検証済みtrue
const bool   UseVolumeClimax   = false;    // HARDCODED: WFA検証済み (false=無効, WR34%ノイズ)
const bool   UseReversalMode   = true;     // HARDCODED: WFA検証済みtrue
const bool   UseTimeDecaySL    = true;     // HARDCODED: WFA検証済みtrue
const bool   UseATRRatchetTrail = true;    // HARDCODED: WFA検証済みtrue
const bool   UseSessionRegime  = true;     // HARDCODED: WFA検証済みtrue
const bool   UseAdaptiveExit   = true;     // HARDCODED: WFA検証済みtrue
const bool   UseRSIMomentumConfirm = true; // HARDCODED: v10.1 WFA検証済みtrue
const bool   UseV11Range       = true;     // HARDCODED: v11.0 WFA検証済みtrue

// --- v13.0 機能トグル ---
const bool   UseCorrelationCap    = true;  // HARDCODED: v13.0 コンポーネント相関キャップ
const bool   UseRealtimeSpike     = true;  // HARDCODED: v13.0 リアルタイムスパイク検知
const bool   UseMultiscaleRegime  = true;  // HARDCODED: v13.0 マルチスケールレジーム検出
const bool   UseTradeQuality      = true;  // HARDCODED: v13.0 MAE/MFE品質トラッカー

// --- USD相関フィルター詳細 ---
const int    Corr_MA_Fast      = 10;       // HARDCODED: Python backtesterと同値
const int    Corr_MA_Slow      = 30;       // HARDCODED: Python backtesterと同値
const double Corr_Threshold    = 0.3;      // HARDCODED: WFA検証済み

// --- RSIダイバージェンス詳細 ---
const int    Div_Lookback      = 30;       // HARDCODED: WFA固定
const int    Div_SwingStrength  = 3;       // HARDCODED: WFA固定

// --- サポート/レジスタンス詳細 ---
const int    SR_Lookback       = 100;      // HARDCODED: WFA固定
const int    SR_SwingStrength   = 5;       // HARDCODED: WFA固定
const double SR_Cluster_ATR    = 1.0;      // HARDCODED: WFA固定
const double SR_Proximity_ATR  = 0.5;      // HARDCODED: WFA固定

// --- シャンデリアイグジット詳細 ---
const int    Chandelier_Period = 22;       // HARDCODED: WFA固定
const double Chandelier_ATR_Multi = 2.0;   // HARDCODED: WFA検証済み (2.0, 利益ロック強化)

// --- エクイティカーブ/Kelly詳細 ---
const int    EquityMA_Period      = 10;    // HARDCODED: WFA固定
const double EquityReduce_Factor  = 0.5;   // HARDCODED: WFA固定
const int    Kelly_LookbackTrades = 30;    // HARDCODED: WFA固定
const double Kelly_Fraction       = 0.5;   // HARDCODED: ハーフKelly標準値
const double Kelly_MinRisk        = 0.1;   // HARDCODED: Kelly最小リスク%
const double Kelly_MaxRisk        = 1.5;   // HARDCODED: WFA検証済み (1.5, 好調時リスク上限)

// --- 防御詳細 ---
// FIX: Issue #12 - Removed unused MaxPositions (redundant with MaxPyramidPositions)
const int    NewsBlockMinutes  = 30;       // HARDCODED: WFA固定
const int    MaxDynamicSpread  = 80;       // HARDCODED: WFA固定
const double CrashATRMulti     = 3.0;      // HARDCODED: WFA固定

// --- 時間経過SL/ラチェット詳細 ---
const int    TimeDecayStartBars = 48;      // HARDCODED: 48 M15 = 12h、WFA固定
const double TimeDecayRate      = 0.85;    // HARDCODED: SL減衰率、WFA固定
const double RatchetStepATR     = 0.5;     // HARDCODED: ラチェットステップ、WFA固定
const double SlippagePoints     = 3.0;     // HARDCODED: スリッページ、通常固定

// FIX: Issue #26 - Dedicated reversal SL/TP multipliers (tighter for counter-trend entries)
const double ReversalSL_Multi  = 0.8;     // HARDCODED: Tighter SL for counter-trend reversal
const double ReversalTP_Multi  = 0.6;     // HARDCODED: More conservative TP for counter-trend reversal

// --- v8.0 ERレジーム検出 ---
const string RegimeMethod       = "er";    // HARDCODED: ER方式のみ使用
const int    RegimeERPeriod     = 20;      // HARDCODED: ER計算期間、WFA固定
const double RegimeERThreshold  = 0.3;     // HARDCODED: ERレンジ閾値、WFA固定
const int    RegimeScoreBoost   = 3;       // HARDCODED: レンジ時スコア加算、WFA固定

// --- v8.2 ピラミッドボラゲート ---
const double HighVolPyramidBlock = 1.2;    // HARDCODED: 高ボラピラミッドブロック倍率

// --- v9.0 レジーム分類閾値 ---
const double RegimeERTrend      = 0.3;     // HARDCODED: RegimeERThresholdと同値
const double RegimeVolHigh      = 1.5;     // HARDCODED: VolRegime_Highと同値
const double RegimeVolCrash     = 3.0;     // HARDCODED: CrashATRMultiと同値
const double RegimeVolRangeCap  = 1.2;     // HARDCODED: WFA固定

// --- レジーム適応プロファイル詳細 (SL/TPは上のinput、残りはconst) ---
const double TrendLotScale      = 1.0;     // HARDCODED: トレンド時ロットスケール
const int    TrendMinScore      = 9;       // HARDCODED: トレンド時最低スコア
const int    TrendScoreMargin   = 2;       // HARDCODED: トレンド時スコアマージン
const int    TrendCooldownBars  = 12;      // HARDCODED: トレンド時クールダウン
const double RangeLotScale      = 0.6;     // HARDCODED: レンジ時ロットスケール
const int    RangeMinScore      = 10;      // HARDCODED: レンジ時最低スコア
const int    RangeScoreMargin   = 3;       // HARDCODED: レンジ時スコアマージン
const int    RangeCooldownBars  = 22;      // HARDCODED: レンジ時クールダウン
const double HighVolLotScale    = 0.3;     // HARDCODED: 高ボラ時ロットスケール
const int    HighVolMinScore    = 13;      // HARDCODED: 高ボラ時最低スコア
const int    HighVolScoreMargin = 3;       // HARDCODED: 高ボラ時スコアマージン
const int    HighVolCooldownBars = 24;     // HARDCODED: 高ボラ時クールダウン

// --- v10.0 セッションxレジーム ロット倍率 ---
const double SessAsianTrendLot  = 0.9;     // HARDCODED: WFA固定
const double SessAsianRangeLot  = 1.0;     // HARDCODED: WFA固定
const double SessAsianHVLot     = 0.5;     // HARDCODED: WFA固定
const double SessLondonTrendLot = 1.1;     // HARDCODED: WFA固定
const double SessLondonRangeLot = 0.9;     // HARDCODED: WFA固定
const double SessLondonHVLot    = 0.5;     // HARDCODED: WFA固定
const double SessNYTrendLot     = 1.0;     // HARDCODED: WFA固定
const double SessNYRangeLot     = 1.0;     // HARDCODED: WFA固定
const double SessNYHVLot        = 0.4;     // HARDCODED: WFA固定

// --- v10.0 レジーム別出口パラメータ ---
const double TrendPartialTP     = 0.55;    // HARDCODED: WFA固定
const double TrendBEMulti       = 1.6;     // HARDCODED: WFA固定
const double TrendTrailMulti    = 1.1;     // HARDCODED: WFA固定
const double RangePartialTP     = 0.38;    // HARDCODED: WFA固定
const double RangeBEMulti       = 1.15;    // HARDCODED: WFA固定
const double RangeTrailMulti    = 0.75;    // HARDCODED: WFA固定
const double HVPartialTP        = 0.5;     // HARDCODED: WFA固定
const double HVBEMulti          = 1.5;     // HARDCODED: WFA固定
const double HVTrailMulti       = 1.0;     // HARDCODED: WFA固定

// --- v10.1 RSIモメンタム確認詳細 ---
const int    RSIMomentumLookback = 3;      // HARDCODED: WFA固定

// --- v11.0 レンジマーケットガード詳細 ---
const int    MacroERPeriod      = 60;      // HARDCODED: WFA固定
const double MacroERThreshold   = 0.20;    // HARDCODED: WFA固定
const int    BurstCapInRange    = 1;       // HARDCODED: WFA固定
const int    H4TrendCapInRange  = 1;       // HARDCODED: WFA固定
const int    RangeMaxScore      = 15;      // HARDCODED: WFA固定
const double MacroRangeTPMulti  = 2.5;     // HARDCODED: WFA固定
const bool   MacroRangePyramid  = false;   // HARDCODED: WFA固定

// --- v13.0 コンポーネント相関キャップ ---
// Correlated component groups: secondary gets CORRELATION_CAP_RATIO of its points
// Group 1: trend_alignment = H4 Trend(0, 3pt) + Momentum Burst(12, 3pt)
// Group 2: rsi_family = H1 RSI(2, 1pt) + H4 RSI Alignment(no CE tracking)
// Group 3: ma_family = H1 MA(1, 2pt) + M15 MA Cross(4, 2pt)
const double CorrelationCapRatio  = 0.5;   // HARDCODED: Secondary comp gets 50% points

// --- v13.0 リアルタイムスパイク検知 ---
const double SpikeATRMulti        = 2.5;   // HARDCODED: Single bar range > ATR x 2.5 = spike
const int    SpikeCooldownBars    = 8;     // HARDCODED: 8 M15 bars (2 hours) cooldown after spike
const bool   SpikeCloseLosing     = true;  // HARDCODED: Close losing positions on spike

// --- v13.0 マルチスケールレジーム検出 ---
const int    RegimeERFast         = 8;     // HARDCODED: H4 8 bars (~32h) short-term ER
const int    RegimeERSlow         = 40;    // HARDCODED: H4 40 bars (~160h) structural ER
const int    RegimeStabilityBars  = 3;     // HARDCODED: Consecutive bars for confirmed regime
const double RegimeTransitionPenalty = 0.7; // HARDCODED: Lot scale during regime transition

// --- v13.0 MAE/MFE品質トラッカー ---
const int    TQTradeCount         = 50;    // HARDCODED: Track last 50 trades for quality
const int    TQMinTrades          = 15;    // HARDCODED: Min trades before quality filtering
const double TQMAEThreshold       = 0.7;   // HARDCODED: MAE > 70% of SL = bad entry
const double TQBadEntryLimit      = 0.5;   // HARDCODED: If >50% bad entries, raise MIN_SCORE
const int    TQScorePenalty       = 1;     // HARDCODED: MIN_SCORE += 1 when entry quality poor

// --- v13.0 段階的リバーサル ---
const int    ReversalMinScore     = 2;     // HARDCODED: Minimum 2/5 reversal score threshold

// --- v13.0 trend_weak レジームプロファイル ---
const int    TrendWeakMinScore    = 10;    // HARDCODED: trend_weak最低スコア
const double TrendWeakSLMulti     = 1.3;   // HARDCODED: trend_weak SL倍率
const double TrendWeakTPMulti     = 2.5;   // HARDCODED: trend_weak TP倍率
const double TrendWeakLotScale    = 0.8;   // HARDCODED: trend_weak ロットスケール
const int    TrendWeakCooldown    = 16;    // HARDCODED: trend_weak クールダウン

// --- v13.0 high_vol_trend/high_vol_range レジームプロファイル ---
const double HVTrendLotScale      = 0.4;   // HARDCODED: high_vol_trend ロットスケール
const int    HVRangeMinScore      = 14;    // HARDCODED: high_vol_range 最低スコア
const double HVRangeSLMulti       = 1.5;   // HARDCODED: high_vol_range SL倍率
const double HVRangeTPMulti       = 2.0;   // HARDCODED: high_vol_range TP倍率
const double HVRangeLotScale      = 0.25;  // HARDCODED: high_vol_range ロットスケール

// --- v12.1 動的コンポーネント有効性 ---
const bool   UseDynamicComponentScoring = true;   // HARDCODED: WFA検証済みtrue
const int    CompEffectMinTrades        = 10;     // HARDCODED: WFA固定
const double CompEffectBoostWR          = 0.6;    // HARDCODED: WFA固定
const double CompEffectPenaltyWR        = 0.4;    // HARDCODED: WFA固定
const double CompEffectBoostWeight      = 1.2;    // HARDCODED: WFA固定
const double CompEffectPenaltyWeight    = 0.6;    // HARDCODED: WFA固定

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;
double         peakBalance;
int            h_h4_ma_fast, h_h4_ma_slow, h_h4_adx, h_h4_sma50;
int            h_h1_ma_fast, h_h1_ma_slow, h_h1_rsi, h_h1_bb;
int            h_m15_ma_fast, h_m15_ma_slow;
int            h_m15_atr;                 // M15 ATR（動的SL/TP用）
datetime       lastBarTime;   // FIX: Issue #21 - Resets on restart; acceptable since CountMyPositions guards double entry
datetime       lastSLTime;
ulong          partialClosedTickets[];

// v3.0 新規グローバル変数
int            h_h4_rsi;
// v4.0 グローバル変数
double         g_dailyPnL;
int            g_lastDay;
bool           g_circuitBreaker;
// FIX: Issue #9 - Removed dead g_pyramidCount variable (CountMyPositions() used instead)
int            h_usdjpy_ma_fast, h_usdjpy_ma_slow, h_usdjpy_atr;
double         recentTradeResults[50];
int            tradeResultIndex;
int            tradeResultCount;
int            totalTradesTracked;
bool           g_UseCorrelation;          // 実行時フラグ（シンボル不可時false）
// v9.0 regime globals
string         g_currentRegime;     // "trend", "range", "high_vol", "crash"
double         g_volRatio;          // current ATR / avg ATR
double         g_h4ER;              // H4 Efficiency Ratio (20-period)
double         g_macroER;           // H4 Macro ER (60-period)
bool           g_isMacroRange;      // macro ER < threshold
// v10.0 session globals
string         g_currentSession;    // "asian", "london", "ny"
// v12.1: Dynamic Component Effectiveness
#define COMP_COUNT 15
int            g_compWins[COMP_COUNT];     // wins per component
int            g_compTotal[COMP_COUNT];    // total trades per component
// Open position → component mask mapping
#define COMP_TRACK_MAX 64
ulong          g_trackPosIDs[COMP_TRACK_MAX];   // position IDs
int            g_trackMasks[COMP_TRACK_MAX];     // component masks at entry
int            g_trackCount;

// v13.0: Multi-scale ER regime globals
double         g_fastER;             // H4 fast ER (8-period)
double         g_slowER;             // H4 slow ER (40-period)
string         g_detailedRegime;     // v13.0 detailed regime string
int            g_regimeStableCount;  // Consecutive bars same regime
string         g_lastStableRegime;   // Last detected regime
bool           g_regimeConfirmed;    // Regime stability confirmed
datetime       g_regimeTransitionTime; // Time of last regime change
double         g_regimeTransitionMult; // Lot multiplier during transition

// v13.0: Spike detection globals
datetime       g_spikeCooldownUntil; // M15 bar time after which spike cooldown ends
int            g_spikeBlocks;        // Count of spike blocks

// v13.0: MAE/MFE Trade Quality tracker
#define TQ_MAX 50
double         g_tqMAERatios[TQ_MAX]; // MAE / SL_distance for recent trades
int            g_tqCount;             // Number of entries in MAE buffer
int            g_tqIndex;             // Current write position (circular)

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   // CODEX-FIX: NEW CRITICAL #2 - Verify hedging account mode
   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("ERROR: This EA requires a hedging account. Current mode: netting. EA disabled.");
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   // FIX: Issue #24 - Use SlippagePoints instead of hardcoded 30
   trade.SetDeviationInPoints((int)SlippagePoints);
   // FIX: Issue #20 - Restore peakBalance from GlobalVariable to survive EA restart
   // CODEX-FIX: NEW HIGH #4 - Use GVKey for symbol/magic scoping
   string pvKey = GVKey("peakBal");
   if(GlobalVariableCheck(pvKey))
      peakBalance = GlobalVariableGet(pvKey);
   else
      peakBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   // ブローカー環境に応じたフィルポリシーを動的判定
   ENUM_ORDER_TYPE_FILLING fillType = ORDER_FILLING_FOK;  // デフォルト
   long fillMode = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fillMode & SYMBOL_FILLING_FOK) != 0)
      fillType = ORDER_FILLING_FOK;
   else if((fillMode & SYMBOL_FILLING_IOC) != 0)
      fillType = ORDER_FILLING_IOC;
   else
      fillType = ORDER_FILLING_RETURN;
   trade.SetTypeFilling(fillType);

   // H4 インジケーター
   h_h4_ma_fast = iMA(_Symbol, PERIOD_H4, H4_MA_Fast, 0, MODE_SMA, PRICE_CLOSE);
   h_h4_ma_slow = iMA(_Symbol, PERIOD_H4, H4_MA_Slow, 0, MODE_SMA, PRICE_CLOSE);
   h_h4_adx     = iADX(_Symbol, PERIOD_H4, H4_ADX_Period);
   h_h4_sma50   = iMA(_Symbol, PERIOD_H4, 50, 0, MODE_SMA, PRICE_CLOSE);

   // H1 インジケーター
   h_h1_ma_fast = iMA(_Symbol, PERIOD_H1, H1_MA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_h1_ma_slow = iMA(_Symbol, PERIOD_H1, H1_MA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   h_h1_rsi     = iRSI(_Symbol, PERIOD_H1, H1_RSI_Period, PRICE_CLOSE);
   h_h1_bb      = iBands(_Symbol, PERIOD_H1, H1_BB_Period, 0, H1_BB_Deviation, PRICE_CLOSE);

   // M15 インジケーター
   h_m15_ma_fast = iMA(_Symbol, PERIOD_M15, M15_MA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_m15_ma_slow = iMA(_Symbol, PERIOD_M15, M15_MA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   // M15 ATR（動的SL/TP計算用）
   h_m15_atr = iATR(_Symbol, PERIOD_M15, ATR_Period_SL);

   // ハンドル検証
   if(h_h4_ma_fast == INVALID_HANDLE || h_h4_ma_slow == INVALID_HANDLE ||
      h_h4_adx == INVALID_HANDLE || h_h4_sma50 == INVALID_HANDLE ||
      h_h1_ma_fast == INVALID_HANDLE ||
      h_h1_ma_slow == INVALID_HANDLE || h_h1_rsi == INVALID_HANDLE ||
      h_h1_bb == INVALID_HANDLE || h_m15_ma_fast == INVALID_HANDLE ||
      h_m15_ma_slow == INVALID_HANDLE || h_m15_atr == INVALID_HANDLE)
   {
      Print("インジケーターハンドルの作成に失敗");
      return INIT_FAILED;
   }

   // v3.0: H4 RSI
   h_h4_rsi = iRSI(_Symbol, PERIOD_H4, H4_RSI_Period, PRICE_CLOSE);
   if(h_h4_rsi == INVALID_HANDLE)
   {
      Print("H4 RSIハンドルの作成に失敗");
      return INIT_FAILED;
   }

   // v3.0: USDJPY相関ハンドル（シンボル利用不可時はグレースフルに無効化）
   g_UseCorrelation = UseCorrelation;
   h_usdjpy_ma_fast = INVALID_HANDLE;
   h_usdjpy_ma_slow = INVALID_HANDLE;
   h_usdjpy_atr     = INVALID_HANDLE;
   if(g_UseCorrelation)
   {
      bool symbolAvailable = SymbolSelect(CorrelationSymbol, true);
      if(!symbolAvailable)
      {
         Print("警告: ", CorrelationSymbol, " が利用不可。USD相関フィルターを無効化");
         g_UseCorrelation = false;
      }
      else
      {
         h_usdjpy_ma_fast = iMA(CorrelationSymbol, PERIOD_H1, Corr_MA_Fast, 0, MODE_EMA, PRICE_CLOSE);
         h_usdjpy_ma_slow = iMA(CorrelationSymbol, PERIOD_H1, Corr_MA_Slow, 0, MODE_EMA, PRICE_CLOSE);
         h_usdjpy_atr     = iATR(CorrelationSymbol, PERIOD_H1, 14);
         if(h_usdjpy_ma_fast == INVALID_HANDLE || h_usdjpy_ma_slow == INVALID_HANDLE || h_usdjpy_atr == INVALID_HANDLE)
         {
            Print("警告: ", CorrelationSymbol, " インジケーター作成失敗。USD相関フィルターを無効化");
            g_UseCorrelation = false;
            if(h_usdjpy_ma_fast != INVALID_HANDLE) IndicatorRelease(h_usdjpy_ma_fast);
            if(h_usdjpy_ma_slow != INVALID_HANDLE) IndicatorRelease(h_usdjpy_ma_slow);
            if(h_usdjpy_atr != INVALID_HANDLE) IndicatorRelease(h_usdjpy_atr);
            h_usdjpy_ma_fast = INVALID_HANDLE;
            h_usdjpy_ma_slow = INVALID_HANDLE;
            h_usdjpy_atr     = INVALID_HANDLE;
         }
      }
   }

   tradeResultIndex  = 0;
   tradeResultCount  = 0;
   totalTradesTracked = 0;
   ArrayInitialize(recentTradeResults, 0.0);
   LoadTradeResults();

   // v4.0: 初期化
   // CODEX-FIX: NEW HIGH #6 - Restore circuit breaker state from GlobalVariable
   {
      string cbDateKey = GVKey("cbDate");
      string cbPnLKey  = GVKey("cbPnL");
      MqlDateTime dtInit;
      TimeCurrent(dtInit);
      g_lastDay = dtInit.day;
      g_dailyPnL = 0;
      g_circuitBreaker = false;
      if(GlobalVariableCheck(cbDateKey) && GlobalVariableCheck(cbPnLKey))
      {
         int savedDay = (int)GlobalVariableGet(cbDateKey);
         if(savedDay == g_lastDay)
         {
            g_dailyPnL = GlobalVariableGet(cbPnLKey);
            if(g_dailyPnL <= -(AccountInfoDouble(ACCOUNT_BALANCE) * DailyMaxLossPct / 100.0))
               g_circuitBreaker = true;
         }
         // else: different day, reset (already 0/false)
      }
   }
   // FIX: Issue #9 - g_pyramidCount removed (dead code)

   // FIX: Issue #22 - Restore lastSLTime from GlobalVariable to survive EA restart
   {
      string slKey = GVKey("lastSL");
      if(GlobalVariableCheck(slKey))
         lastSLTime = (datetime)(long)GlobalVariableGet(slKey);
   }

   // v9.0: レジーム初期化
   g_currentRegime = "trend";
   g_volRatio = 1.0;
   g_h4ER = 0.5;
   g_macroER = 0.5;
   g_isMacroRange = false;
   g_currentSession = "ny";

   // v12.1: コンポーネント有効性初期化
   ArrayInitialize(g_compWins, 0);
   ArrayInitialize(g_compTotal, 0);
   ArrayInitialize(g_trackPosIDs, 0);
   ArrayInitialize(g_trackMasks, 0);
   g_trackCount = 0;
   LoadComponentStats();

   // v13.0: Multi-scale ER初期化
   g_fastER = 0.5;
   g_slowER = 0.5;
   g_detailedRegime = "trend";
   g_regimeStableCount = 0;
   g_lastStableRegime = "trend";
   g_regimeConfirmed = true;
   g_regimeTransitionTime = 0;
   g_regimeTransitionMult = 1.0;

   // v13.0: Spike detection初期化
   g_spikeCooldownUntil = 0;
   g_spikeBlocks = 0;

   // v13.0: MAE/MFE品質トラッカー初期化
   ArrayInitialize(g_tqMAERatios, 0.0);
   g_tqCount = 0;
   g_tqIndex = 0;

   Print("AntigravityMTF EA [GOLD] v13.0 初期化完了");
   Print("   動的SL/TP: SL=ATR×", SL_ATR_Multi, " TP=ATR×", TP_ATR_Multi);
   Print("   ボラレジーム: Low<", VolRegime_Low, " High>", VolRegime_High);
   Print("   v3.0: USD相関=", (g_UseCorrelation ? "有効" : "無効"),
         " Div=", (UseDivergence ? "有効" : "無効"),
         " S/R=", (UseSRLevels ? "有効" : "無効"),
         " Candle=", (UseCandlePatterns ? "有効" : "無効"));
   Print("   v3.0: Chandelier=", (UseChandelierExit ? "有効" : "無効"),
         " AdaptSize=", (UseAdaptiveSizing ? "有効" : "無効"));
   Print("   v4.0 防御: News=", (UseNewsFilter ? "有効" : "無効"),
         " Weekend=", (UseWeekendClose ? "有効" : "無効"),
         " CircuitBreaker=", DailyMaxLossPct, "% CrashATR=", CrashATRMulti, "x");
   Print("   v4.0 攻撃: MomentumBurst=", (UseMomentumBurst ? "有効" : "無効"),
         " VolClimax=", (UseVolumeClimax ? "有効" : "無効"),
         " Pyramid=", MaxPyramidPositions, " Reversal=", (UseReversalMode ? "有効" : "無効"));
   Print("   v12.1: DynamicCompScoring=", (UseDynamicComponentScoring ? "有効" : "無効"),
         " MinTrades=", CompEffectMinTrades,
         " BoostWR>=", DoubleToString(CompEffectBoostWR*100, 0), "% PenaltyWR<=", DoubleToString(CompEffectPenaltyWR*100, 0), "%");
   Print("   v13.0: MultiscaleER=", (UseMultiscaleRegime ? "有効" : "無効"),
         " CorrelationCap=", (UseCorrelationCap ? "有効" : "無効"),
         " SpikeDetect=", (UseRealtimeSpike ? "有効" : "無効"),
         " TradeQuality=", (UseTradeQuality ? "有効" : "無効"));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(h_h4_ma_fast);
   IndicatorRelease(h_h4_ma_slow);
   IndicatorRelease(h_h4_adx);
   IndicatorRelease(h_h4_sma50);
   IndicatorRelease(h_h1_ma_fast);
   IndicatorRelease(h_h1_ma_slow);
   IndicatorRelease(h_h1_rsi);
   IndicatorRelease(h_h1_bb);
   IndicatorRelease(h_m15_ma_fast);
   IndicatorRelease(h_m15_ma_slow);
   IndicatorRelease(h_m15_atr);

   // v3.0: 追加ハンドル解放
   IndicatorRelease(h_h4_rsi);
   if(h_usdjpy_ma_fast != INVALID_HANDLE) IndicatorRelease(h_usdjpy_ma_fast);
   if(h_usdjpy_ma_slow != INVALID_HANDLE) IndicatorRelease(h_usdjpy_ma_slow);
   if(h_usdjpy_atr != INVALID_HANDLE)     IndicatorRelease(h_usdjpy_atr);

   SaveTradeResults();
   SaveComponentStats();
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // v4.0: 日次サーキットブレーカーリセット
   MqlDateTime dtNow;
   TimeCurrent(dtNow);
   if(dtNow.day != g_lastDay)
   {
      g_lastDay = dtNow.day;
      g_dailyPnL = 0;
      g_circuitBreaker = false;
      // CODEX-FIX: NEW HIGH #6 - Persist reset state
      GlobalVariableSet(GVKey("cbDate"), (double)g_lastDay);
      GlobalVariableSet(GVKey("cbPnL"), g_dailyPnL);
   }
   if(g_circuitBreaker) return;  // 日次損失上限到達

   ManageOpenPositions();

   // v4.0: 塩漬けトレード決済チェック
   CheckStaleTradeExit();

   // v4.0: 週末クローズ
   if(IsWeekendClose())
   {
      CloseAllPositions();
      return;
   }

   datetime currentBar = iTime(_Symbol, PERIOD_M15, 0);
   if(currentBar == lastBarTime) return;
   lastBarTime = currentBar;

   if(!IsTradeAllowed()) return;
   if(!CheckTimeFilter()) return;
   if(!CheckSpread()) return;

   // v13.0: Realtime volatility spike detection
   if(UseRealtimeSpike)
   {
      double spikeATR = GetCurrentATR();
      if(spikeATR > 0 && DetectRealtimeSpike(spikeATR))
      {
         g_spikeBlocks++;
         g_spikeCooldownUntil = currentBar + SpikeCooldownBars * PeriodSeconds(PERIOD_M15);
         // Close losing positions immediately on spike
         if(SpikeCloseLosing) CloseLosingSpikePositions();
         return;
      }
      // Spike cooldown
      if(currentBar < g_spikeCooldownUntil) return;
   }

   // v4.0: ニュースフィルター
   if(IsNewsTime()) return;

   // v4.0: 動的スプレッドチェック
   if(!IsDynamicSpreadOK()) return;

   int posCount = CountMyPositions();
   if(posCount >= MaxPyramidPositions) return;

   // SL後クールダウン
   if(lastSLTime > 0 && TimeCurrent() - lastSLTime < CooldownMinutes * 60)
      return;

   // ATR取得 & ボラティリティレジーム判定
   double currentATR = GetCurrentATR();
   if(currentATR <= 0) return;

   // v4.0: 4状態レジーム判定
   int advRegime = GetAdvancedRegime(currentATR);
   if(advRegime == 0) return;  // Crash → 新規エントリー禁止

   int volRegime = GetVolatilityRegime(currentATR);

   // --- CRITICAL #1 FIX: Compute regime BEFORE scoring so g_currentRegime is current-bar ---
   // v8.0: Efficiency Ratio on H4
   double h4_er = 0;
   if(RegimeMethod == "er")
   {
      double h4CloseArr[];
      ArraySetAsSeries(h4CloseArr, true);
      // CODEX-FIX: NEW HIGH #5 - Use shift 1 (confirmed H4 bar) instead of shift 0
      if(CopyClose(_Symbol, PERIOD_H4, 1, RegimeERPeriod + 1, h4CloseArr) >= RegimeERPeriod + 1)
      {
         double netChange = MathAbs(h4CloseArr[0] - h4CloseArr[RegimeERPeriod]);
         double sumAbsChanges = 0;
         for(int k = 0; k < RegimeERPeriod; k++)
            sumAbsChanges += MathAbs(h4CloseArr[k] - h4CloseArr[k+1]);
         if(sumAbsChanges > 0)
            h4_er = netChange / sumAbsChanges;
      }
   }

   // v9.0: Store h4 ER globally and compute vol_ratio for regime detection
   g_h4ER = h4_er;
   double avgATR = GetAverageATR();
   g_volRatio = (avgATR > 0) ? currentATR / avgATR : 1.0;

   // v13.0: Multi-scale ER computation
   if(UseMultiscaleRegime)
   {
      g_fastER = CalcERForPeriod(RegimeERFast);
      g_slowER = CalcERForPeriod(RegimeERSlow);
   }

   // v9.0/v13.0: Regime Classification
   if(UseRegimeAdaptive) {
      if(UseMultiscaleRegime)
      {
         g_detailedRegime = DetectRegimeV13(g_fastER, h4_er, g_slowER, g_volRatio);
         UpdateRegimeStability(g_detailedRegime);
         // Map detailed regime to base regime for backward compatibility
         if(g_detailedRegime == "trend_strong" || g_detailedRegime == "trend_weak")
            g_currentRegime = "trend";
         else if(g_detailedRegime == "high_vol_trend" || g_detailedRegime == "high_vol_range")
            g_currentRegime = "high_vol";
         else
            g_currentRegime = g_detailedRegime;
      }
      else
      {
         g_currentRegime = DetectRegimeV9(h4_er, g_volRatio);
         g_detailedRegime = g_currentRegime;
      }
      if(g_currentRegime == "crash") return; // No trading in crash
   } else {
      g_currentRegime = "trend";
      g_detailedRegime = "trend";
   }

   // v11.0: Macro ER
   g_macroER = CalcMacroER();
   g_isMacroRange = (UseV11Range && g_macroER < MacroERThreshold);

   // v10.0: Session detection
   {
      MqlDateTime dtSess;
      TimeCurrent(dtSess);
      g_currentSession = GetCurrentSession(dtSess.hour);
   }
   // --- END CRITICAL #1 FIX ---

   // 動的リスクスケーリング
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance)
   {
      peakBalance = balance;
      // FIX: Issue #20 - Persist peakBalance across EA restarts
      GlobalVariableSet(GVKey("peakBal"), peakBalance);
   }
   // CODEX-FIX: NEW HIGH #7 - Use Equity (not Balance) for DD calculation
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double currentDD = (peakBalance > 0) ? (peakBalance - equity) / peakBalance * 100 : 0;

   // ──── スコアリング（v4.0: 最大27点, v12.1: 動的コンポーネント有効性） ────
   int buyScore  = 0;
   int sellScore = 0;
   string buyReasons  = "";
   string sellReasons = "";
   int componentMask = 0;

   // v12.1: Pre-compute effectiveness weights for each component
   double ce0  = GetComponentEffectiveness(0);   // H4 Trend
   double ce1  = GetComponentEffectiveness(1);   // H1 MA
   double ce2  = GetComponentEffectiveness(2);   // H1 RSI
   double ce3  = GetComponentEffectiveness(3);   // H1 BB
   double ce4  = GetComponentEffectiveness(4);   // M15 MA
   double ce5  = GetComponentEffectiveness(5);   // Channel
   double ce6  = GetComponentEffectiveness(6);   // Momentum
   double ce7  = GetComponentEffectiveness(7);   // Session
   double ce8  = GetComponentEffectiveness(8);   // USD Corr
   double ce9  = GetComponentEffectiveness(9);   // Divergence
   double ce10 = GetComponentEffectiveness(10);  // S/R
   double ce11 = GetComponentEffectiveness(11);  // Candle
   double ce12 = GetComponentEffectiveness(12);  // Momentum Burst (bit 12)
   double ce13 = GetComponentEffectiveness(13);  // Volume Climax (bit 13)
   // Note: H4 RSI (scoring component #13) has no mask bit — not tracked for CE

   // 1. H4 トレンド（3点）
   int h4Trend = GetH4Trend();
   // FIX: Issue #25 - Use bit 16 instead of bit 15 for direction flag to avoid future component collision
   if(h4Trend == 1)       { buyScore += (int)MathFloor(3 * ce0);  buyReasons  += "H4^ "; componentMask |= (1 << 0); componentMask |= (1 << 16); } // bit16 = buy direction
   else if(h4Trend == -1) { sellScore += (int)MathFloor(3 * ce0);  sellReasons += "H4v "; componentMask |= (1 << 0); } // bit16 absent = sell direction

   // 2. H1 MA方向（2点）
   int h1MACross = GetH1MACross();
   if(h1MACross == 1)       { buyScore += (int)MathFloor(2 * ce1);  buyReasons  += "H1MA^ "; componentMask |= (1 << 1); }
   else if(h1MACross == -1) { sellScore += (int)MathFloor(2 * ce1);  sellReasons += "H1MAv "; componentMask |= (1 << 1); }

   // 3. H1 RSI（1点）
   double h1Rsi = GetIndicatorValue(h_h1_rsi, 0, 1);
   if(h1Rsi > 40 && h1Rsi < 60)         { int rsiPts = (int)MathFloor(1 * ce2); buyScore += rsiPts;  sellScore += rsiPts;  buyReasons += "RSIn "; sellReasons += "RSIn "; componentMask |= (1 << 2); }
   else if(h1Rsi >= 60 && h1Rsi < 70)   { buyScore += (int)MathFloor(1 * ce2);  buyReasons  += "RSIb "; componentMask |= (1 << 2); }
   else if(h1Rsi > 30 && h1Rsi <= 40)   { sellScore += (int)MathFloor(1 * ce2);  sellReasons += "RSIs "; componentMask |= (1 << 2); }

   // 4. H1 BB（1点）
   int bbSignal = GetBBSignal();
   if(bbSignal == 1)       { buyScore += (int)MathFloor(1 * ce3);  buyReasons  += "BB^ "; componentMask |= (1 << 3); }
   else if(bbSignal == -1) { sellScore += (int)MathFloor(1 * ce3);  sellReasons += "BBv "; componentMask |= (1 << 3); }

   // 5. M15 MAクロス（2点）
   int m15Cross = GetM15MACross();
   if(m15Cross == 1)       { buyScore += (int)MathFloor(2 * ce4);  buyReasons  += "M15^ "; componentMask |= (1 << 4); }
   else if(m15Cross == -1) { sellScore += (int)MathFloor(2 * ce4);  sellReasons += "M15v "; componentMask |= (1 << 4); }

   // 6. チャネル回帰（1点）
   int channelSignal = GetChannelSignal();
   if(channelSignal == 1)       { buyScore += (int)MathFloor(1 * ce5);  buyReasons  += "CH^ "; componentMask |= (1 << 5); }
   else if(channelSignal == -1) { sellScore += (int)MathFloor(1 * ce5);  sellReasons += "CHv "; componentMask |= (1 << 5); }

   // 7. モメンタム確認（1点）
   if(UseMomentum)
   {
      int momentum = GetMomentum();
      if(momentum == 1)       { buyScore += (int)MathFloor(1 * ce6);  buyReasons  += "MOM^ "; componentMask |= (1 << 6); }
      else if(momentum == -1) { sellScore += (int)MathFloor(1 * ce6);  sellReasons += "MOMv "; componentMask |= (1 << 6); }
   }

   // 8. セッションボーナス（1点）— Gold はロンドン/NY重複が有利
   if(UseSessionBonus)
   {
      int sessionBonus = GetSessionBonus();
      if(sessionBonus > 0)
      {
         int sesPts = (int)MathFloor(1 * ce7);
         buyScore += sesPts;  sellScore += sesPts;
         buyReasons += "SES "; sellReasons += "SES ";
         componentMask |= (1 << 7);
      }
   }

   // 9. USD相関フィルター（2点）
   if(g_UseCorrelation)
   {
      int corrSignal = GetCorrelationSignal();
      if(corrSignal == 1)       { buyScore += (int)MathFloor(2 * ce8);  buyReasons  += "USD- "; componentMask |= (1 << 8); }
      else if(corrSignal == -1) { sellScore += (int)MathFloor(2 * ce8);  sellReasons += "USD+ "; componentMask |= (1 << 8); }
   }

   // 10. RSIダイバージェンス（2点）
   if(UseDivergence)
   {
      int divSignal = GetDivergence();
      if(divSignal == 1)       { buyScore += (int)MathFloor(2 * ce9);  buyReasons  += "DIV^ "; componentMask |= (1 << 9); }
      else if(divSignal == -1) { sellScore += (int)MathFloor(2 * ce9);  sellReasons += "DIVv "; componentMask |= (1 << 9); }
   }

   // 11. S/Rレベル（+1/-1点）
   if(UseSRLevels)
   {
      int srSignal = GetSRSignal(iClose(_Symbol, PERIOD_H1, 1), currentATR);
      if(srSignal == 1)       { buyScore += (int)MathFloor(1 * ce10);  buyReasons  += "SR^ "; componentMask |= (1 << 10); }
      else if(srSignal == -1) { sellScore += (int)MathFloor(1 * ce10);  sellReasons += "SRv "; componentMask |= (1 << 10); }
   }

   // 12. ローソク足パターン（1点）
   if(UseCandlePatterns)
   {
      int candleSignal = GetCandlePattern();
      if(candleSignal == 1)       { buyScore += (int)MathFloor(1 * ce11);  buyReasons  += "CDL^ "; componentMask |= (1 << 11); }
      else if(candleSignal == -1) { sellScore += (int)MathFloor(1 * ce11);  sellReasons += "CDLv "; componentMask |= (1 << 11); }
   }

   // 13. H4 RSIアライメント（1点） — no mask bit, no CE tracking
   if(UseH4RSI)
   {
      int h4RsiSignal = GetH4RSIAlignment();
      if(h4RsiSignal == 1)       { buyScore += 1;  buyReasons  += "H4R^ "; }
      else if(h4RsiSignal == -1) { sellScore += 1;  sellReasons += "H4Rv "; }
   }

   // 14. v4.0: モメンタムバースト（3点）
   int burstScore = GetMomentumBurst();
   if(burstScore > 0)        { buyScore += (int)MathFloor(3 * ce12);  buyReasons  += "BURST^ "; componentMask |= (1 << 12); }
   else if(burstScore < 0)   { sellScore += (int)MathFloor(3 * ce12);  sellReasons += "BURSTv "; componentMask |= (1 << 12); }

   // 15. v4.0: ボリュームクライマックス（2点）
   int climaxScore = GetVolumeClimax();
   if(climaxScore > 0)       { buyScore += (int)MathFloor(2 * ce13);  buyReasons  += "VCLX^ "; componentMask |= (1 << 13); }
   else if(climaxScore < 0)  { sellScore += (int)MathFloor(2 * ce13);  sellReasons += "VCLXv "; componentMask |= (1 << 13); }

   // v11.0: Dampen trend components in range
   if(UseV11Range && (g_currentRegime == "range" || g_isMacroRange)) {
      // Cap Momentum Burst (use CE-adjusted points)
      if(burstScore != 0) {
         int burstPts = (int)MathFloor(3 * ce12);
         int reduction = burstPts - BurstCapInRange;
         if(reduction > 0) {
            if(burstScore > 0) buyScore -= reduction;
            else sellScore -= reduction;
         }
      }
      // Cap H4 Trend (use CE-adjusted points)
      if(componentMask & 1) { // H4 trend was scored
         int h4Pts = (int)MathFloor(3 * ce0);
         int reduction2 = h4Pts - H4TrendCapInRange;
         if(reduction2 > 0) {
            if(componentMask & (1 << 16)) // FIX: Issue #25 - direction bit moved to bit 16
               buyScore -= reduction2;
            else
               sellScore -= reduction2;
         }
      }
      buyScore = (int)MathMax(0, buyScore);
      sellScore = (int)MathMax(0, sellScore);

      // Score ceiling
      buyScore = MathMin(buyScore, RangeMaxScore);
      sellScore = MathMin(sellScore, RangeMaxScore);
   }

   // Clamp scores to minimum 0
   buyScore = (int)MathMax(0, buyScore);
   sellScore = (int)MathMax(0, sellScore);

   // v10.1: RSI Momentum Confirmation
   // BUY requires H1 RSI > 50 AND rising over lookback bars
   // SELL requires H1 RSI < 50 AND falling over lookback bars
   if(UseRSIMomentumConfirm)
   {
      double rsiNow  = GetIndicatorValue(h_h1_rsi, 0, 1);
      double rsiPrev = GetIndicatorValue(h_h1_rsi, 0, 1 + RSIMomentumLookback);

      if(rsiNow > 0 && rsiPrev > 0)
      {
         // Block BUY if RSI not bullish momentum
         if(buyScore > 0 && !(rsiNow > 50.0 && rsiNow > rsiPrev))
         {
            buyScore = 0;
            buyReasons += "!RSImom ";
         }

         // Block SELL if RSI not bearish momentum
         if(sellScore > 0 && !(rsiNow < 50.0 && rsiNow < rsiPrev))
         {
            sellScore = 0;
            sellReasons += "!RSImom ";
         }
      }
   }

   // v13.0: Component Correlation Cap - reduce double-counting from correlated components
   if(UseCorrelationCap)
   {
      ApplyCorrelationCap(buyScore, sellScore, componentMask);
   }

   // ──── エントリー ────
   // FIX: Issue #6 - ask/bid for SL/TP calculation only; re-fetched before execution
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // v9.0/v13.0: Regime-specific SL/TP (v13.0 uses detailed regime for finer control)
   double slMulti, tpMulti, regimeLotScale;
   int regimeMinScore, regimeScoreMargin, regimeCooldown;
   bool regimeAllowPyramid;

   if(UseRegimeAdaptive) {
      if(UseMultiscaleRegime) {
         // v13.0: Use detailed regime for finer profile selection
         if(g_detailedRegime == "trend_strong") {
            slMulti = TrendSLMulti; tpMulti = TrendTPMulti; regimeLotScale = TrendLotScale;
            regimeMinScore = TrendMinScore; regimeScoreMargin = TrendScoreMargin;
            regimeCooldown = TrendCooldownBars; regimeAllowPyramid = true;
            if(g_volRatio >= 1.2) slMulti += 0.3;
         } else if(g_detailedRegime == "trend_weak") {
            slMulti = TrendWeakSLMulti; tpMulti = TrendWeakTPMulti; regimeLotScale = TrendWeakLotScale;
            regimeMinScore = TrendWeakMinScore; regimeScoreMargin = TrendScoreMargin;
            regimeCooldown = TrendWeakCooldown; regimeAllowPyramid = false;
         } else if(g_detailedRegime == "range") {
            slMulti = RangeSLMulti; tpMulti = RangeTPMulti; regimeLotScale = RangeLotScale;
            regimeMinScore = RangeMinScore; regimeScoreMargin = RangeScoreMargin;
            regimeCooldown = RangeCooldownBars; regimeAllowPyramid = false;
         } else if(g_detailedRegime == "high_vol_trend") {
            slMulti = HighVolSLMulti; tpMulti = HighVolTPMulti; regimeLotScale = HVTrendLotScale;
            regimeMinScore = HighVolMinScore; regimeScoreMargin = HighVolScoreMargin;
            regimeCooldown = HighVolCooldownBars; regimeAllowPyramid = false;
         } else if(g_detailedRegime == "high_vol_range") {
            slMulti = HVRangeSLMulti; tpMulti = HVRangeTPMulti; regimeLotScale = HVRangeLotScale;
            regimeMinScore = HVRangeMinScore; regimeScoreMargin = HighVolScoreMargin;
            regimeCooldown = HighVolCooldownBars; regimeAllowPyramid = false;
         } else { // fallback to base regime
            slMulti = TrendSLMulti; tpMulti = TrendTPMulti; regimeLotScale = TrendLotScale;
            regimeMinScore = TrendMinScore; regimeScoreMargin = TrendScoreMargin;
            regimeCooldown = TrendCooldownBars; regimeAllowPyramid = true;
         }
      } else {
         // Legacy v9.0 regime selection
         if(g_currentRegime == "trend") {
            slMulti = TrendSLMulti; tpMulti = TrendTPMulti; regimeLotScale = TrendLotScale;
            regimeMinScore = TrendMinScore; regimeScoreMargin = TrendScoreMargin;
            regimeCooldown = TrendCooldownBars; regimeAllowPyramid = true;
            if(g_volRatio >= 1.2) slMulti += 0.3;
         } else if(g_currentRegime == "range") {
            slMulti = RangeSLMulti; tpMulti = RangeTPMulti; regimeLotScale = RangeLotScale;
            regimeMinScore = RangeMinScore; regimeScoreMargin = RangeScoreMargin;
            regimeCooldown = RangeCooldownBars; regimeAllowPyramid = false;
         } else { // high_vol
            slMulti = HighVolSLMulti; tpMulti = HighVolTPMulti; regimeLotScale = HighVolLotScale;
            regimeMinScore = HighVolMinScore; regimeScoreMargin = HighVolScoreMargin;
            regimeCooldown = HighVolCooldownBars; regimeAllowPyramid = false;
         }
      }
   } else {
      slMulti = SL_ATR_Multi;
      tpMulti = TP_ATR_Multi;
      regimeLotScale = 1.0;
      regimeMinScore = MinEntryScore;
      regimeScoreMargin = ScoreMarginMin;
      regimeCooldown = (int)(CooldownMinutes / 15);
      regimeAllowPyramid = true;
      if(volRegime == 2) slMulti += HighVol_SL_Bonus;
   }

   double slDist = currentATR * slMulti;
   double tpDist = currentATR * tpMulti;

   // v4.0: モメンタムバースト時はTP×1.5
   if(MathAbs(burstScore) > 0)
      tpDist *= 1.5;

   // v7.0: トレンドアライメントSL/TP調整
   // H4 SMA(50) slope で順/逆トレンド判定（OnInitで作成済みのh_h4_sma50を使用）
   double h4Sma50_now  = 0;
   double h4Sma50_prev = 0;
   {
      double smaValues[];
      ArraySetAsSeries(smaValues, true);
      // CODEX-FIX: NEW HIGH #5 - Use shift 1 (confirmed H4 bar) instead of shift 0
      if(CopyBuffer(h_h4_sma50, 0, 1, H4_Slope_Period + 1, smaValues) >= H4_Slope_Period + 1)
      {
         h4Sma50_now  = smaValues[0];
         h4Sma50_prev = smaValues[H4_Slope_Period];
      }
   }
   double h4Slope = h4Sma50_now - h4Sma50_prev;

   // NOTE: h4_er, g_volRatio, g_currentRegime, g_macroER, g_isMacroRange,
   // g_currentSession are now computed BEFORE scoring (CRITICAL #1 FIX above)

   bool isBuyWithTrend  = (buyScore > sellScore && h4Slope > 0);
   bool isSellWithTrend = (sellScore > buyScore && h4Slope < 0);
   bool isWithTrend = isBuyWithTrend || isSellWithTrend;

   if(h4Slope != 0)
   {
      if(isWithTrend)
      {
         slDist *= Trend_SL_Widen;    // 順トレンド: SL広め（プルバック耐性）
         tpDist *= Trend_TP_Extend;   // 順トレンド: TP広め（利益伸ばし）
      }
      else
      {
         slDist *= Trend_SL_Tighten;  // 逆トレンド: SL狭め（素早い損切り）
         tpDist *= Trend_TP_Tighten;  // 逆トレンド: TP狭め（早め利確）
      }
   }

   // SLの最小/最大制限（ポイント単位）
   double minSL = MinSL_Points * _Point;
   double maxSL = MaxSL_Points * _Point;
   slDist = MathMax(minSL, MathMin(maxSL, slDist));
   tpDist = MathMax(slDist * 1.5, tpDist);  // RR最低1:1.5保証

   double lot = CalcLotSize(ask, slDist);

   // v3.0: エクイティカーブフィルター
   if(UseEquityCurveFilter && !IsEquityCurveAboveMA())
   {
      lot = NormalizeDouble(lot * EquityReduce_Factor, 2);
      lot = MathMax(MinLots, lot);
   }

   // v4.0: ピラミッディング — 追加エントリー時はロット減衰
   bool isPyramid = (posCount > 0);
   if(isPyramid)
   {
      // 既存ポジションが利益出ているか確認
      bool existingProfitable = true;
      for(int pi = PositionsTotal() - 1; pi >= 0; pi--)
      {
         ulong pTicket = PositionGetTicket(pi);
         if(pTicket == 0) continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(PositionGetDouble(POSITION_PROFIT) <= 0) { existingProfitable = false; break; }
      }
      if(!existingProfitable) isPyramid = false;  // 利益出てなければピラミッド不可
      else
      {
         double decay = MathPow(PyramidLotDecay, posCount);
         lot = NormalizeDouble(lot * decay, 2);
         lot = MathMax(MinLots, lot);
      }
   }

   // 動的スコア防壁（v9.0: レジーム適応）
   int currentMinScore = regimeMinScore;
   if(currentDD >= 20.0) currentMinScore = (int)MathMax(currentMinScore, 18);
   else if(currentDD >= 15.0) currentMinScore = (int)MathMax(currentMinScore, 15);
   else if(currentDD >= 10.0) currentMinScore = (int)MathMax(currentMinScore, 12);
   // v4.0: Ranging regime → +3
   if(advRegime == 1) currentMinScore += 3;

   // Legacy ER boost (only when regime adaptive is off)
   if(!UseRegimeAdaptive && RegimeMethod == "er" && h4_er < RegimeERThreshold)
      currentMinScore += RegimeScoreBoost;

   // v13.0: Trade quality penalty (MAE/MFE)
   currentMinScore += GetTradeQualityPenalty();

   // v6.0: Score Margin Filter
   int scoreMargin = UseRegimeAdaptive ? regimeScoreMargin : ScoreMarginMin;

   // Apply regime lot scale
   lot = NormalizeDouble(lot * regimeLotScale, 2);
   lot = MathMax(MinLots, lot);

   // v13.0: Regime transition lot penalty
   if(UseMultiscaleRegime)
   {
      lot = NormalizeDouble(lot * g_regimeTransitionMult, 2);
      lot = MathMax(MinLots, lot);
   }

   // v10.0: Session-Regime lot modifier
   double sessLotMod = GetSessionLotModifier(g_currentSession, g_currentRegime);
   lot = NormalizeDouble(lot * sessLotMod, 2);
   lot = MathMax(MinLots, lot);

   // ATR spike cap
   if(g_volRatio > 2.0) {
      lot = MathMin(lot, MinLots * 3);
   }

   // CODEX-FIX: NEW HIGH #1 - Final lot cap after ALL multipliers (regime, session, ATR spike)
   lot = MathMin(lot, MaxLots);
   lot = MathMax(lot, MinLots);

   // v8.2: High-vol pyramid block
   if(isPyramid && g_volRatio > HighVolPyramidBlock)
      isPyramid = false;
   // v11.0: Macro-range pyramid block
   if(isPyramid && UseV11Range && g_isMacroRange && !MacroRangePyramid)
      isPyramid = false;
   // v9.0: Regime pyramid block
   if(isPyramid && UseRegimeAdaptive && !regimeAllowPyramid)
      isPyramid = false;

   bool entered = false;

   // CODEX-FIX: NEW CRITICAL #1 - Fix anti-pyramiding: only enter if no positions, or pyramid allowed
   if((posCount == 0 || (isPyramid && posCount < MaxPyramidPositions)) && buyScore >= currentMinScore && (buyScore - sellScore) >= scoreMargin)
   {
      // FIX: Issue #6 - Re-fetch ask immediately before execution to avoid stale price
      // CODEX-FIX: NEW HIGH #10 - Recompute SL/TP from refreshed price
      double slDistPts = slDist / _Point;
      double tpDistPts = tpDist / _Point;
      ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - slDistPts * _Point, _Digits);
      double tp = NormalizeDouble(ask + tpDistPts * _Point, _Digits);
      // CODEX-FIX: NEW HIGH #6 - Validate SL/TP against STOPS_LEVEL/FREEZE_LEVEL
      ValidateStopsDistance(ask, sl, tp, true);
      // CODEX-FIX: NEW HIGH #8 - Store regime in trade comment for exit logic
      if(trade.Buy(lot, _Symbol, ask, sl, tp,
         StringFormat("GOLD BUY S:%d M:%d R:%s ATR:%.1f|CM=%d|RG=%s", buyScore, componentMask, g_currentRegime, currentATR/_Point, componentMask, g_currentRegime)))
      {
         Print("GOLD BUY Score:", buyScore, "/27 Regime:", g_detailedRegime, " ATR:", DoubleToString(currentATR/_Point,0),
               "pt SL:", DoubleToString(slDist/_Point,0), " TP:", DoubleToString(tpDist/_Point,0),
               isPyramid ? " [PYRAMID]" : "", " [", buyReasons, "]");
         entered = true;
      }
   }

   // CODEX-FIX: NEW CRITICAL #1 - Fix anti-pyramiding: only enter if no positions, or pyramid allowed
   if(!entered && (posCount == 0 || (isPyramid && posCount < MaxPyramidPositions)) && sellScore >= currentMinScore && (sellScore - buyScore) >= scoreMargin)
   {
      // FIX: Issue #6 - Re-fetch bid immediately before execution to avoid stale price
      // CODEX-FIX: NEW HIGH #10 - Recompute SL/TP from refreshed price
      double slDistPtsSell = slDist / _Point;
      double tpDistPtsSell = tpDist / _Point;
      bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + slDistPtsSell * _Point, _Digits);
      double tp = NormalizeDouble(bid - tpDistPtsSell * _Point, _Digits);
      // CODEX-FIX: NEW HIGH #6 - Validate SL/TP against STOPS_LEVEL/FREEZE_LEVEL
      ValidateStopsDistance(bid, sl, tp, false);
      // CODEX-FIX: NEW HIGH #8 - Store regime in trade comment for exit logic
      if(trade.Sell(lot, _Symbol, bid, sl, tp,
         StringFormat("GOLD SELL S:%d M:%d R:%s ATR:%.1f|CM=%d|RG=%s", sellScore, componentMask, g_currentRegime, currentATR/_Point, componentMask, g_currentRegime)))
      {
         Print("GOLD SELL Score:", sellScore, "/27 Regime:", g_detailedRegime, " ATR:", DoubleToString(currentATR/_Point,0),
               "pt SL:", DoubleToString(slDist/_Point,0), " TP:", DoubleToString(tpDist/_Point,0),
               isPyramid ? " [PYRAMID]" : "", " [", sellReasons, "]");
         entered = true;
      }
   }

   // v13.0: 段階的リバーサルモード — confidence-scaled lot sizing
   // Only in non-range, non-crash regimes (matching Python logic)
   if(!entered && posCount == 0 && g_currentRegime != "range" && g_currentRegime != "crash")
   {
      int reversalDir = 0;
      double reversalConfidence = 0.0;
      if(CheckReversal(reversalDir, reversalConfidence))
      {
         // v13.0: Scale lot by confidence: 2/5=0.2x, 3/5=0.3x, 4/5=0.4x, 5/5=0.5x
         double revLotScale = reversalConfidence * 0.5;
         double revLot = NormalizeDouble(lot * revLotScale, 2);
         revLot = MathMax(MinLots, revLot);

         // FIX: Issue #26 - Use dedicated reversal SL/TP multipliers for counter-trend entries
         double revSlDist = slDist * ReversalSL_Multi;  // Tighter SL for reversals
         double revTpDist = tpDist * ReversalTP_Multi;  // More conservative TP
         revSlDist = MathMax(MinSL_Points * _Point, revSlDist);  // Enforce minimum SL
         revTpDist = MathMax(revSlDist * 1.5, revTpDist);        // Enforce minimum RR

         // CODEX-FIX: NEW HIGH #1 - Final lot cap for reversal entries
         revLot = MathMin(revLot, MaxLots);

         if(reversalDir == 1)
         {
            // FIX: Issue #6 - Re-fetch ask for reversal entry
            // CODEX-FIX: NEW HIGH #10 - Recompute SL/TP from refreshed price
            ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            double sl = NormalizeDouble(ask - revSlDist, _Digits);
            double tp = NormalizeDouble(ask + revTpDist, _Digits);
            // CODEX-FIX: NEW HIGH #6 - Validate SL/TP against STOPS_LEVEL/FREEZE_LEVEL
            ValidateStopsDistance(ask, sl, tp, true);
            // CODEX-FIX: NEW HIGH #8 - Store regime in trade comment
            if(trade.Buy(revLot, _Symbol, ask, sl, tp,
               StringFormat("GOLD REV-BUY M:%d|CM=%d|RG=%s", componentMask, componentMask, g_currentRegime)))
               Print("GOLD REVERSAL BUY lot:", DoubleToString(revLot,2),
                     " SL:", DoubleToString(revSlDist/_Point,0), " TP:", DoubleToString(revTpDist/_Point,0));
         }
         else if(reversalDir == -1)
         {
            // FIX: Issue #6 - Re-fetch bid for reversal entry
            // CODEX-FIX: NEW HIGH #10 - Recompute SL/TP from refreshed price
            bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            double sl = NormalizeDouble(bid + revSlDist, _Digits);
            double tp = NormalizeDouble(bid - revTpDist, _Digits);
            // CODEX-FIX: NEW HIGH #6 - Validate SL/TP against STOPS_LEVEL/FREEZE_LEVEL
            ValidateStopsDistance(bid, sl, tp, false);
            // CODEX-FIX: NEW HIGH #8 - Store regime in trade comment
            if(trade.Sell(revLot, _Symbol, bid, sl, tp,
               StringFormat("GOLD REV-SELL M:%d|CM=%d|RG=%s", componentMask, componentMask, g_currentRegime)))
               Print("GOLD REVERSAL SELL lot:", DoubleToString(revLot,2),
                     " SL:", DoubleToString(revSlDist/_Point,0), " TP:", DoubleToString(revTpDist/_Point,0));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| v9.0: 2Dレジーム分類                                               |
//+------------------------------------------------------------------+
string DetectRegimeV9(double h4_er_val, double vol_ratio)
{
   if(vol_ratio >= RegimeVolCrash) return "crash";
   if(vol_ratio >= RegimeVolHigh) return "high_vol";
   if(h4_er_val < RegimeERTrend && vol_ratio <= RegimeVolRangeCap) return "range";
   if(h4_er_val < RegimeERTrend) return "high_vol";
   return "trend";
}

//+------------------------------------------------------------------+
//| v10.0: セッション×レジーム ロット倍率                               |
//+------------------------------------------------------------------+
double GetSessionLotModifier(string session, string regime)
{
   if(!UseSessionRegime) return 1.0;
   if(session == "asian") {
      if(regime == "trend") return SessAsianTrendLot;
      if(regime == "range") return SessAsianRangeLot;
      return SessAsianHVLot;
   }
   if(session == "london") {
      if(regime == "trend") return SessLondonTrendLot;
      if(regime == "range") return SessLondonRangeLot;
      return SessLondonHVLot;
   }
   // NY
   if(regime == "trend") return SessNYTrendLot;
   if(regime == "range") return SessNYRangeLot;
   return SessNYHVLot;
}

//+------------------------------------------------------------------+
//| v10.0: 現在セッション判定                                          |
//+------------------------------------------------------------------+
string GetCurrentSession(int hour)
{
   // FIX: Issue #11 - Convert server time to GMT before session detection
   int gmtHour = (hour - GMTOffset + 24) % 24;
   if(gmtHour >= 0 && gmtHour < 8) return "asian";
   if(gmtHour >= 8 && gmtHour < 13) return "london";
   return "ny";
}

//+------------------------------------------------------------------+
//| v11.0: マクロER計算 (H4 60期間)                                    |
//+------------------------------------------------------------------+
double CalcMacroER()
{
   if(!UseV11Range) return 1.0;
   double h4CloseArr[];
   ArraySetAsSeries(h4CloseArr, true);
   // CODEX-FIX: NEW HIGH #5 - Use shift 1 (confirmed H4 bar) instead of shift 0
   if(CopyClose(_Symbol, PERIOD_H4, 1, MacroERPeriod + 1, h4CloseArr) < MacroERPeriod + 1)
      return 1.0;
   double netChange = MathAbs(h4CloseArr[0] - h4CloseArr[MacroERPeriod]);
   double sumAbsChanges = 0;
   for(int k = 0; k < MacroERPeriod; k++)
      sumAbsChanges += MathAbs(h4CloseArr[k] - h4CloseArr[k+1]);
   if(sumAbsChanges <= 0) return 0;
   return netChange / sumAbsChanges;
}

//+------------------------------------------------------------------+
//| v9.0: 平均ATR取得                                                  |
//+------------------------------------------------------------------+
double GetAverageATR()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(h_m15_atr, 0, 1, VolRegime_Period, atr) < VolRegime_Period) return 0;
   double sum = 0;
   for(int i = 0; i < VolRegime_Period; i++) sum += atr[i];
   return sum / VolRegime_Period;
}

//+------------------------------------------------------------------+
//| 現在のM15 ATR取得                                                  |
//+------------------------------------------------------------------+
double GetCurrentATR()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(h_m15_atr, 0, 1, 1, atr) < 1) return 0;
   return atr[0];
}

//+------------------------------------------------------------------+
//| ボラティリティレジーム判定                                          |
//+------------------------------------------------------------------+
int GetVolatilityRegime(double currentATR)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(h_m15_atr, 0, 1, VolRegime_Period, atr) < VolRegime_Period) return 1;

   double sum = 0;
   for(int i = 0; i < VolRegime_Period; i++)
      sum += atr[i];
   double avgATR = sum / VolRegime_Period;

   if(avgATR <= 0) return 1;
   double ratio = currentATR / avgATR;

   if(ratio < VolRegime_Low) return 0;
   if(ratio > VolRegime_High) return 2;
   return 1;
}

//+------------------------------------------------------------------+
//| モメンタム判定（M15の直近3本の方向）                                |
//+------------------------------------------------------------------+
int GetMomentum()
{
   double close1 = iClose(_Symbol, PERIOD_M15, 1);
   double close3 = iClose(_Symbol, PERIOD_M15, 3);

   if(close1 == 0 || close3 == 0) return 0;

   // ゴールドはATRの10%以上の動きで判定
   double atr = GetCurrentATR();
   double threshold = (atr > 0) ? atr * 0.1 : 1.0 * _Point;

   if(close1 - close3 > threshold) return 1;
   if(close3 - close1 > threshold) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| セッションボーナス（Gold用: ロンドン/NY重複が最も有利）             |
//+------------------------------------------------------------------+
int GetSessionBonus()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   // FIX: Issue #11 - Convert server time to GMT for session bonus
   int gmtHour = (dt.hour - GMTOffset + 24) % 24;

   // ロンドン/NY重複 (13:00-17:00 GMT)
   if(gmtHour >= 13 && gmtHour < 17) return 1;

   // ロンドンセッション初動 (8:00-11:00 GMT)
   if(gmtHour >= 8 && gmtHour < 11) return 1;

   return 0;
}

//+------------------------------------------------------------------+
//| H4 トレンド判定                                                    |
//+------------------------------------------------------------------+
int GetH4Trend()
{
   double maFast = GetIndicatorValue(h_h4_ma_fast, 0, 1);
   double maSlow = GetIndicatorValue(h_h4_ma_slow, 0, 1);
   double adx    = GetIndicatorValue(h_h4_adx, 0, 1);
   double plusDI = GetIndicatorValue(h_h4_adx, 1, 1);
   double minusDI= GetIndicatorValue(h_h4_adx, 2, 1);

   if(maFast == 0 || maSlow == 0) return 0;

   if(adx >= H4_ADX_Threshold)
   {
      if(maFast > maSlow && plusDI > minusDI) return 1;
      if(maFast < maSlow && minusDI > plusDI) return -1;
   }
   return 0;
}

//+------------------------------------------------------------------+
//| H1 MA方向判定                                                      |
//+------------------------------------------------------------------+
int GetH1MACross()
{
   double fastCurr = GetIndicatorValue(h_h1_ma_fast, 0, 1);
   double slowCurr = GetIndicatorValue(h_h1_ma_slow, 0, 1);

   if(fastCurr == 0 || slowCurr == 0) return 0;

   if(fastCurr > slowCurr) return 1;
   if(fastCurr < slowCurr) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| ボリンジャーバンド判定                                              |
//+------------------------------------------------------------------+
int GetBBSignal()
{
   double bbUpper = GetIndicatorValue(h_h1_bb, 1, 1);
   double bbLower = GetIndicatorValue(h_h1_bb, 2, 1);

   if(bbUpper == 0 || bbLower == 0) return 0;

   double close = iClose(_Symbol, PERIOD_H1, 1);
   double prevClose = iClose(_Symbol, PERIOD_H1, 2);
   double bbWidth = bbUpper - bbLower;
   if(bbWidth <= 0) return 0;

   double position = (close - bbLower) / bbWidth;

   if(position < 0.2 && close > prevClose) return 1;
   if(position > 0.8 && close < prevClose) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| M15 MAクロス                                                       |
//+------------------------------------------------------------------+
int GetM15MACross()
{
   double fastCurr = GetIndicatorValue(h_m15_ma_fast, 0, 1);
   double slowCurr = GetIndicatorValue(h_m15_ma_slow, 0, 1);
   double fastPrev = GetIndicatorValue(h_m15_ma_fast, 0, 2);
   double slowPrev = GetIndicatorValue(h_m15_ma_slow, 0, 2);

   if(fastCurr == 0 || slowCurr == 0) return 0;

   if(fastCurr > slowCurr && fastPrev <= slowPrev) return 1;
   if(fastCurr < slowCurr && fastPrev >= slowPrev) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| チャネル回帰分析                                                    |
//+------------------------------------------------------------------+
int GetChannelSignal()
{
   int lookback = 40;
   double sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;

   // FIX: Issue #16 - Batch fetch closes instead of calling iClose 40x2 times in loops
   double closes[];
   ArraySetAsSeries(closes, true);
   if(CopyClose(_Symbol, PERIOD_H1, 0, lookback, closes) != lookback) return 0;

   for(int i = lookback - 1; i >= 0; i--)
   {
      double x = lookback - 1 - i;
      double y = closes[i];
      sumX += x; sumY += y; sumXY += x * y; sumX2 += x * x;
   }

   double n = lookback;
   double slope     = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
   double intercept = (sumY - slope * sumX) / n;

   double sumRes2 = 0;
   for(int i = lookback - 1; i >= 0; i--)
   {
      double x = lookback - 1 - i;
      double predicted = intercept + slope * x;
      double actual    = closes[i];
      sumRes2 += MathPow(actual - predicted, 2);
   }
   double stdDev = MathSqrt(sumRes2 / n);

   double currentPredicted = intercept + slope * (n - 1);
   double upperChannel = currentPredicted + stdDev * 2;
   double lowerChannel = currentPredicted - stdDev * 2;
   double close = iClose(_Symbol, PERIOD_H1, 1);

   if(upperChannel == lowerChannel) return 0;
   double channelPos = (close - lowerChannel) / (upperChannel - lowerChannel);

   if(channelPos < 0.2 && slope > 0) return 1;
   if(channelPos > 0.8 && slope < 0) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| v3.0: USD相関シグナル                                              |
//| +1 = USD弱体化（金買いボーナス）, -1 = USD強化（金売りボーナス）    |
//+------------------------------------------------------------------+
int GetCorrelationSignal()
{
   if(!g_UseCorrelation) return 0;

   double fastBuf[], slowBuf[], atrBuf[];
   ArraySetAsSeries(fastBuf, true);
   ArraySetAsSeries(slowBuf, true);
   ArraySetAsSeries(atrBuf, true);

   if(CopyBuffer(h_usdjpy_ma_fast, 0, 1, 6, fastBuf) < 6) return 0;
   if(CopyBuffer(h_usdjpy_ma_slow, 0, 1, 1, slowBuf) < 1) return 0;
   if(CopyBuffer(h_usdjpy_atr, 0, 1, 1, atrBuf) < 1) return 0;

   double fast = fastBuf[0];
   double slow = slowBuf[0];
   double usdjpyATR = atrBuf[0];
   if(usdjpyATR <= 0) return 0;

   // fast_5bars_ago = fastBuf[5] (since fastBuf[0]=bar1, fastBuf[5]=bar6)
   double fast5ago = fastBuf[5];
   double speed = (fast - fast5ago) / usdjpyATR;

   // USD weakening (USDJPY falling) → gold buy bonus
   if(fast < slow && speed < -Corr_Threshold) return 1;
   // USD strengthening (USDJPY rising) → gold sell bonus
   if(fast > slow && speed > Corr_Threshold) return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| v3.0: RSIダイバージェンス検出                                       |
//| +1 = 強気ダイバージェンス, -1 = 弱気ダイバージェンス               |
//+------------------------------------------------------------------+
int GetDivergence()
{
   if(!UseDivergence) return 0;

   double closeArr[];
   double rsiArr[];
   ArraySetAsSeries(closeArr, true);
   ArraySetAsSeries(rsiArr, true);

   // H1足からDiv_Lookback本をbar1から取得
   int copied = 0;
   copied = CopyClose(_Symbol, PERIOD_H1, 1, Div_Lookback, closeArr);
   if(copied < Div_Lookback) return 0;

   if(CopyBuffer(h_h1_rsi, 0, 1, Div_Lookback, rsiArr) < Div_Lookback) return 0;

   // スイングロー検出（最新2つ）
   double swingLowPrice[2], swingLowRSI[2];
   int swingLowCount = 0;

   for(int i = Div_SwingStrength; i < Div_Lookback - Div_SwingStrength && swingLowCount < 2; i++)
   {
      bool isLow = true;
      for(int j = 1; j <= Div_SwingStrength; j++)
      {
         if(closeArr[i] > closeArr[i - j] || closeArr[i] > closeArr[i + j])
         {
            isLow = false;
            break;
         }
      }
      if(isLow)
      {
         swingLowPrice[swingLowCount] = closeArr[i];
         swingLowRSI[swingLowCount]   = rsiArr[i];
         swingLowCount++;
      }
   }

   // スイングハイ検出（最新2つ）
   double swingHighPrice[2], swingHighRSI[2];
   int swingHighCount = 0;

   for(int i = Div_SwingStrength; i < Div_Lookback - Div_SwingStrength && swingHighCount < 2; i++)
   {
      bool isHigh = true;
      for(int j = 1; j <= Div_SwingStrength; j++)
      {
         if(closeArr[i] < closeArr[i - j] || closeArr[i] < closeArr[i + j])
         {
            isHigh = false;
            break;
         }
      }
      if(isHigh)
      {
         swingHighPrice[swingHighCount] = closeArr[i];
         swingHighRSI[swingHighCount]   = rsiArr[i];
         swingHighCount++;
      }
   }

   // 強気ダイバージェンス判定
   if(swingLowCount >= 2)
   {
      // Classic bullish: price lower low, RSI higher low
      if(swingLowPrice[0] < swingLowPrice[1] && swingLowRSI[0] > swingLowRSI[1])
         return 1;
      // Hidden bullish: price higher low, RSI lower low
      if(swingLowPrice[0] > swingLowPrice[1] && swingLowRSI[0] < swingLowRSI[1])
         return 1;
   }

   // 弱気ダイバージェンス判定
   if(swingHighCount >= 2)
   {
      // Classic bearish: price higher high, RSI lower high
      if(swingHighPrice[0] > swingHighPrice[1] && swingHighRSI[0] < swingHighRSI[1])
         return -1;
      // Hidden bearish: price lower high, RSI higher high
      if(swingHighPrice[0] < swingHighPrice[1] && swingHighRSI[0] > swingHighRSI[1])
         return -1;
   }

   return 0;
}

//+------------------------------------------------------------------+
//| v3.0: サポート/レジスタンスシグナル                                 |
//| +1 = サポート付近, -1 = レジスタンス付近                           |
//+------------------------------------------------------------------+
int GetSRSignal(double currentPrice, double currentATR)
{
   if(!UseSRLevels || currentATR <= 0) return 0;

   // H1足からスイングハイ/ローを収集
   // REVIEW-FIX: Issue 3.4 - Batch CopyHigh/CopyLow instead of individual iHigh/iLow calls
   double highs[], lows[];
   if(CopyHigh(_Symbol, PERIOD_H1, 0, SR_Lookback, highs) != SR_Lookback) return 0;
   if(CopyLow(_Symbol, PERIOD_H1, 0, SR_Lookback, lows) != SR_Lookback) return 0;
   // CopyHigh/CopyLow with start_pos=0 returns data in chronological order:
   // index 0 = oldest bar (shift SR_Lookback-1), index SR_Lookback-1 = current bar (shift 0)
   // To map: shift i corresponds to highs[SR_Lookback - 1 - i]

   // FIX: Issue #17 - Pre-allocate array with reserve to avoid O(n^2) resizing
   double levels[];
   int levelCount = 0;
   ArrayResize(levels, 0, 50);

   for(int i = SR_SwingStrength; i < SR_Lookback - SR_SwingStrength; i++)
   {
      int idx = SR_Lookback - 1 - i;
      double high_i = highs[idx];
      double low_i  = lows[idx];

      // スイングハイ判定
      bool isSwingHigh = true;
      for(int j = 1; j <= SR_SwingStrength; j++)
      {
         if(high_i < highs[SR_Lookback - 1 - (i - j)] || high_i < highs[SR_Lookback - 1 - (i + j)])
         {
            isSwingHigh = false;
            break;
         }
      }
      if(isSwingHigh)
      {
         levelCount++;
         ArrayResize(levels, levelCount);
         levels[levelCount - 1] = high_i;
      }

      // スイングロー判定
      bool isSwingLow = true;
      for(int j = 1; j <= SR_SwingStrength; j++)
      {
         if(low_i > lows[SR_Lookback - 1 - (i - j)] || low_i > lows[SR_Lookback - 1 - (i + j)])
         {
            isSwingLow = false;
            break;
         }
      }
      if(isSwingLow)
      {
         levelCount++;
         ArrayResize(levels, levelCount);
         levels[levelCount - 1] = low_i;
      }
   }

   if(levelCount == 0) return 0;

   // ソート
   ArraySort(levels);

   // クラスタリング
   double clustered[];
   int clusterCount = 0;
   ArrayResize(clustered, 1);
   clustered[0] = levels[0];
   clusterCount = 1;

   for(int i = 1; i < levelCount; i++)
   {
      if(MathAbs(levels[i] - clustered[clusterCount - 1]) < SR_Cluster_ATR * currentATR)
      {
         // 同じクラスター - 平均化
         clustered[clusterCount - 1] = (clustered[clusterCount - 1] + levels[i]) / 2.0;
      }
      else
      {
         clusterCount++;
         ArrayResize(clustered, clusterCount);
         clustered[clusterCount - 1] = levels[i];
      }
   }

   // 最も近いサポートとレジスタンスを見つける
   double nearestSupport    = 0;
   double nearestResistance = 0;
   double minSupportDist    = DBL_MAX;
   double minResistDist     = DBL_MAX;

   for(int i = 0; i < clusterCount; i++)
   {
      if(clustered[i] < currentPrice)
      {
         double dist = currentPrice - clustered[i];
         if(dist < minSupportDist)
         {
            minSupportDist = dist;
            nearestSupport = clustered[i];
         }
      }
      else if(clustered[i] > currentPrice)
      {
         double dist = clustered[i] - currentPrice;
         if(dist < minResistDist)
         {
            minResistDist = dist;
            nearestResistance = clustered[i];
         }
      }
   }

   double proximity = SR_Proximity_ATR * currentATR;

   // サポート付近 → 買いシグナル
   if(nearestSupport > 0 && minSupportDist <= proximity)
      return 1;
   // レジスタンス付近 → 売りシグナル
   if(nearestResistance > 0 && minResistDist <= proximity)
      return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| v3.0: ローソク足パターン検出                                       |
//| +1 = 強気パターン, -1 = 弱気パターン                               |
//+------------------------------------------------------------------+
int GetCandlePattern()
{
   if(!UseCandlePatterns) return 0;

   double o1 = iOpen(_Symbol, PERIOD_H1, 1);
   double h1 = iHigh(_Symbol, PERIOD_H1, 1);
   double l1 = iLow(_Symbol, PERIOD_H1, 1);
   double c1 = iClose(_Symbol, PERIOD_H1, 1);

   double o2 = iOpen(_Symbol, PERIOD_H1, 2);
   double h2 = iHigh(_Symbol, PERIOD_H1, 2);
   double l2 = iLow(_Symbol, PERIOD_H1, 2);
   double c2 = iClose(_Symbol, PERIOD_H1, 2);

   double o3 = iOpen(_Symbol, PERIOD_H1, 3);
   double h3 = iHigh(_Symbol, PERIOD_H1, 3);
   double l3 = iLow(_Symbol, PERIOD_H1, 3);
   double c3 = iClose(_Symbol, PERIOD_H1, 3);

   double body1 = MathAbs(c1 - o1);
   double body2 = MathAbs(c2 - o2);
   double body3 = MathAbs(c3 - o3);

   double upperWick1 = h1 - MathMax(o1, c1);
   double lowerWick1 = MathMin(o1, c1) - l1;

   // Bullish Engulfing: bar2 bearish, bar1 bullish, bar1 body engulfs bar2
   if(c2 < o2 && c1 > o1 && o1 <= c2 && c1 >= o2)
      return 1;

   // Bearish Engulfing: bar2 bullish, bar1 bearish, bar1 body engulfs bar2
   if(c2 > o2 && c1 < o1 && o1 >= c2 && c1 <= o2)
      return -1;

   // Hammer: small body at top, lower wick > 2x body
   if(body1 > 0 && lowerWick1 > 2.0 * body1 && upperWick1 < body1 * 0.5)
      return 1;

   // Shooting Star: small body at bottom, upper wick > 2x body
   if(body1 > 0 && upperWick1 > 2.0 * body1 && lowerWick1 < body1 * 0.5)
      return -1;

   // Morning Star: bar3 bearish, bar2 small body, bar1 bullish above bar3 midpoint
   double bar3mid = (o3 + c3) / 2.0;
   if(c3 < o3 && body2 < body3 * 0.3 && c1 > o1 && c1 > bar3mid)
      return 1;

   // Evening Star: bar3 bullish, bar2 small body, bar1 bearish below bar3 midpoint
   if(c3 > o3 && body2 < body3 * 0.3 && c1 < o1 && c1 < bar3mid)
      return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| v3.0: H4 RSIアライメント                                           |
//| +1 = 強気アライメント, -1 = 弱気アライメント                       |
//+------------------------------------------------------------------+
int GetH4RSIAlignment()
{
   if(!UseH4RSI) return 0;

   double h4RsiVal = GetIndicatorValue(h_h4_rsi, 0, 1);
   double h1RsiVal = GetIndicatorValue(h_h1_rsi, 0, 1);

   if(h4RsiVal == 0 || h1RsiVal == 0) return 0;

   // H4 RSI 50-75 + H1 RSI < 75 → bullish
   if(h4RsiVal >= 50 && h4RsiVal <= 75 && h1RsiVal < 75)
      return 1;

   // H4 RSI 25-50 + H1 RSI > 25 → bearish
   if(h4RsiVal >= 25 && h4RsiVal <= 50 && h1RsiVal > 25)
      return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| v3.0: エクイティカーブMA判定                                       |
//+------------------------------------------------------------------+
bool IsEquityCurveAboveMA()
{
   if(!UseEquityCurveFilter) return true;
   if(tradeResultCount < EquityMA_Period) return true;  // データ不足時は許可

   double sumPnL = 0;
   int startIdx = tradeResultIndex - EquityMA_Period;
   if(startIdx < 0) startIdx += 50;

   for(int i = 0; i < EquityMA_Period; i++)
   {
      int idx = (startIdx + i) % 50;
      sumPnL += recentTradeResults[idx];
   }

   double maPnL = sumPnL / EquityMA_Period;
   return (maPnL >= 0);
}

//+------------------------------------------------------------------+
//| v3.0: 適応的リスク計算（Kelly基準）                                 |
//+------------------------------------------------------------------+
double GetAdaptiveRisk()
{
   if(!UseAdaptiveSizing) return RiskPercent;
   if(tradeResultCount < Kelly_LookbackTrades) return RiskPercent;

   int wins = 0;
   double totalWin = 0;
   double totalLoss = 0;
   int lookback = MathMin(tradeResultCount, Kelly_LookbackTrades);

   int startIdx = tradeResultIndex - lookback;
   if(startIdx < 0) startIdx += 50;

   for(int i = 0; i < lookback; i++)
   {
      int idx = (startIdx + i) % 50;
      if(recentTradeResults[idx] > 0)
      {
         wins++;
         totalWin += recentTradeResults[idx];
      }
      else if(recentTradeResults[idx] < 0)
      {
         totalLoss += MathAbs(recentTradeResults[idx]);
      }
   }

   if(wins == 0 || totalLoss == 0) return RiskPercent;
   // CRITICAL #3 FIX: Guard against zero division when wins >= lookback
   if(wins >= lookback) return RiskPercent;
   if(lookback - wins == 0) return RiskPercent;  // belt-and-suspenders

   double W = (double)wins / lookback;         // 勝率
   double R = (totalWin / wins) / (totalLoss / (lookback - wins));  // ペイオフレシオ

   // Kelly formula: f = W - (1-W)/R
   double kelly = W - (1.0 - W) / R;
   kelly *= Kelly_Fraction;  // フラクショナルKelly

   // クランプ
   kelly = MathMax(Kelly_MinRisk, MathMin(Kelly_MaxRisk, kelly));

   return kelly;
}

//+------------------------------------------------------------------+
//| トレード結果保存（GlobalVariable使用・Kelly計算用）                 |
//+------------------------------------------------------------------+
void SaveTradeResults()
{
   // CODEX-FIX: NEW HIGH #4 - Use symbol/magic-scoped GV keys
   GlobalVariableSet(GVKey("TotalTracks"), (double)totalTradesTracked);
   GlobalVariableSet(GVKey("ResultCount"), (double)tradeResultCount);
   GlobalVariableSet(GVKey("ResultIndex"), (double)tradeResultIndex);

   for(int i = 0; i < 50; i++)
      GlobalVariableSet(GVKey("TR_" + IntegerToString(i)), recentTradeResults[i]);
}

//+------------------------------------------------------------------+
//| トレード結果読込（GlobalVariable使用・Kelly計算用）                 |
//+------------------------------------------------------------------+
void LoadTradeResults()
{
   // CODEX-FIX: NEW HIGH #4 - Use symbol/magic-scoped GV keys
   if(!GlobalVariableCheck(GVKey("TotalTracks"))) return;

   totalTradesTracked = (int)GlobalVariableGet(GVKey("TotalTracks"));
   tradeResultCount   = (int)GlobalVariableGet(GVKey("ResultCount"));
   tradeResultIndex   = (int)GlobalVariableGet(GVKey("ResultIndex"));

   for(int i = 0; i < 50; i++)
   {
      if(GlobalVariableCheck(GVKey("TR_" + IntegerToString(i))))
         recentTradeResults[i] = GlobalVariableGet(GVKey("TR_" + IntegerToString(i)));
   }
}

//+------------------------------------------------------------------+
//| ロット計算（ゴールド用・OrderCalcProfit使用）                       |
//+------------------------------------------------------------------+
double CalcLotSize(double entryPrice, double slDist)
{
   if(slDist <= 0) return MinLots;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance)
   {
      peakBalance = balance;
      // FIX: Issue #20 - Persist peakBalance across EA restarts
      GlobalVariableSet(GVKey("peakBal"), peakBalance);
   }
   // CODEX-FIX: NEW HIGH #7 - Use Equity (not Balance) for DD calculation
   double equity_calc = AccountInfoDouble(ACCOUNT_EQUITY);
   double currentDD = (peakBalance > 0) ? (peakBalance - equity_calc) / peakBalance * 100 : 0;

   // v3.0: 適応的リスク
   double riskPct = GetAdaptiveRisk();

   if(currentDD >= MaxDrawdownPct)
      riskPct *= 0.25;
   else if(currentDD >= DDHalfRiskPct)
      riskPct *= 0.5;

   double riskAmount = balance * riskPct / 100.0;

   double slPrice = entryPrice - slDist;
   double profitOrLoss = 0.0;

   // MT5内蔵の利益計算関数で正確なリスク額を算出
   if(!OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, entryPrice, slPrice, profitOrLoss))
   {
      double usdJpyRate = SymbolInfoDouble("USDJPY", SYMBOL_BID);
      if(usdJpyRate <= 0) usdJpyRate = 150.0;
      profitOrLoss = -((slDist / _Point / 100.0) * 100.0 * usdJpyRate);
   }

   double lossForOneLot = MathAbs(profitOrLoss);
   if(lossForOneLot <= 0) lossForOneLot = 1000.0;

   double lots = riskAmount / lossForOneLot;

   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(MinLots, MathMin(MaxLots, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| ポジション管理（ATRベース + 半利確 + シャンデリアイグジット）       |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   double curATR = GetCurrentATR();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl        = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);
      double volume    = PositionGetDouble(POSITION_VOLUME);
      long   posType   = PositionGetInteger(POSITION_TYPE);

      // v10.0: Regime-adaptive exit parameters
      // CODEX-FIX: NEW HIGH #8 - Use entry regime from trade comment, not current regime
      double partialRatio, beMulti, trailMulti;
      string entryRegime = "";
      if(UseAdaptiveExit) {
         string posComment = PositionGetString(POSITION_COMMENT);
         entryRegime = ParseRegimeFromComment(posComment);
         if(entryRegime == "") entryRegime = g_currentRegime;  // Fallback for old positions

         if(entryRegime == "trend") {
            partialRatio = TrendPartialTP; beMulti = TrendBEMulti; trailMulti = TrendTrailMulti;
         } else if(entryRegime == "range") {
            partialRatio = RangePartialTP; beMulti = RangeBEMulti; trailMulti = RangeTrailMulti;
         } else {
            partialRatio = HVPartialTP; beMulti = HVBEMulti; trailMulti = HVTrailMulti;
         }
      } else {
         partialRatio = PartialTP_Ratio; beMulti = BE_ATR_Multi; trailMulti = Trail_ATR_Multi;
      }

      double beDist    = (curATR > 0) ? curATR * beMulti : MathAbs(tp - openPrice) * 0.4;
      double trailStep = (curATR > 0) ? curATR * trailMulti : MathAbs(tp - openPrice) * 0.3;

      if(posType == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profitDist = bid - openPrice;

         // 半利確
         if(UsePartialClose && !IsPartialClosed(ticket) && tp > openPrice)
         {
            double tpDist = tp - openPrice;
            if(profitDist >= tpDist * partialRatio)
            {
               double closeLot = NormalizeDouble(volume * PartialCloseRatio, 2);
               if(closeLot >= MinLots)
               {
                  if(trade.PositionClosePartial(ticket, closeLot))
                  {
                     MarkPartialClosed(ticket);
                     double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);
                     // FIX: Issue #7 - Retry PositionModify up to 3 times after partial close
                     // REVIEW-FIX: Issue 3.3 - Validate STOPS_LEVEL before SL modification
                     if(IsModifySLValid(newSL, true))
                     {
                        bool modifyOk = false;
                        for(int retry = 0; retry < 3; retry++)
                        {
                           if(trade.PositionModify(ticket, newSL, tp)) { modifyOk = true; break; }
                           Sleep(100);
                        }
                        if(!modifyOk)
                           Print("WARNING: PositionModify failed after 3 retries for BUY ticket ", ticket, " SL=", newSL);
                     }
                     Print("GOLD 半利確 BUY: ", DoubleToString(closeLot, 2), "lot決済 [", g_currentRegime, "]");
                  }
               }
            }
         }

         // FIX: Issue #8 - Breakeven and trailing are now sequential (not else-if)
         // REVIEW-FIX: Issue 3.3 - Validate STOPS_LEVEL before SL modification
         // 建値移動
         if(profitDist >= beDist && sl < openPrice)
         {
            double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);
            if(IsModifySLValid(newSL, true) && trade.PositionModify(ticket, newSL, tp))
               sl = newSL; // Update local SL for trailing check below
         }
         // トレーリング — now checked after breakeven (sequential)
         if(profitDist >= beDist * 1.5)
         {
            double newSL = NormalizeDouble(bid - trailStep, _Digits);
            if(newSL > sl + 5 * _Point && IsModifySLValid(newSL, true))
               trade.PositionModify(ticket, newSL, tp);
         }

         // v3.0: シャンデリアイグジット（BUY）
         if(UseChandelierExit && curATR > 0 && sl >= openPrice)
         {
            double highestHigh = 0;
            for(int k = 1; k <= Chandelier_Period; k++)
            {
               double hh = iHigh(_Symbol, PERIOD_M15, k);
               if(hh > highestHigh) highestHigh = hh;
            }
            double chandelierSL = highestHigh - curATR * Chandelier_ATR_Multi;
            chandelierSL = NormalizeDouble(chandelierSL, _Digits);
            if(chandelierSL > sl + 5 * _Point && IsModifySLValid(chandelierSL, true))
               trade.PositionModify(ticket, chandelierSL, tp);
         }

         // v6.0: ATR Ratchet Trailing (BUY)
         if(UseATRRatchetTrail && curATR > 0 && profitDist > 0) {
            double atrMultiples = profitDist / curATR;
            if(atrMultiples >= 2.0) {
               double ratchetStep = curATR * MathMax(0.3, RatchetStepATR * (1.0 / atrMultiples * 2));
               double ratchetSL = bid - ratchetStep;
               ratchetSL = NormalizeDouble(ratchetSL, _Digits);
               if(ratchetSL > sl + 5 * _Point && IsModifySLValid(ratchetSL, true))
                  trade.PositionModify(ticket, ratchetSL, tp);
            }
         }

         // v6.0: Time-decay SL tightening (BUY)
         if(UseTimeDecaySL && sl < openPrice) {
            double hoursOpen = (double)(TimeCurrent() - (datetime)PositionGetInteger(POSITION_TIME)) / 3600.0;
            int barsOpen = (int)(hoursOpen * 4); // M15 bars
            if(barsOpen >= TimeDecayStartBars) {
               double decayPeriods = (double)(barsOpen - TimeDecayStartBars) / TimeDecayStartBars;
               double decayFactor = MathPow(TimeDecayRate, decayPeriods);
               double origSLDist = openPrice - sl;
               double decayedSLDist = MathMax(MinSL_Points * _Point, origSLDist * decayFactor);
               double newSL = NormalizeDouble(openPrice - decayedSLDist, _Digits);
               if(newSL > sl && IsModifySLValid(newSL, true))
                  trade.PositionModify(ticket, newSL, tp);
            }
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profitDist = openPrice - ask;

         // 半利確
         if(UsePartialClose && !IsPartialClosed(ticket) && tp < openPrice)
         {
            double tpDist = openPrice - tp;
            if(profitDist >= tpDist * partialRatio)
            {
               double closeLot = NormalizeDouble(volume * PartialCloseRatio, 2);
               if(closeLot >= MinLots)
               {
                  if(trade.PositionClosePartial(ticket, closeLot))
                  {
                     MarkPartialClosed(ticket);
                     double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);
                     // FIX: Issue #7 - Retry PositionModify up to 3 times after partial close
                     // REVIEW-FIX: Issue 3.3 - Validate STOPS_LEVEL before SL modification
                     if(IsModifySLValid(newSL, false))
                     {
                        bool modifyOk = false;
                        for(int retry = 0; retry < 3; retry++)
                        {
                           if(trade.PositionModify(ticket, newSL, tp)) { modifyOk = true; break; }
                           Sleep(100);
                        }
                        if(!modifyOk)
                           Print("WARNING: PositionModify failed after 3 retries for SELL ticket ", ticket, " SL=", newSL);
                     }
                     Print("GOLD 半利確 SELL: ", DoubleToString(closeLot, 2), "lot決済 [", g_currentRegime, "]");
                  }
               }
            }
         }

         // FIX: Issue #8 - Breakeven and trailing are now sequential (not else-if)
         // REVIEW-FIX: Issue 3.3 - Validate STOPS_LEVEL before SL modification
         // 建値移動
         if(profitDist >= beDist && (sl > openPrice || sl == 0))
         {
            double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);
            if(IsModifySLValid(newSL, false) && trade.PositionModify(ticket, newSL, tp))
               sl = newSL; // Update local SL for trailing check below
         }
         // トレーリング — now checked after breakeven (sequential)
         if(profitDist >= beDist * 1.5)
         {
            double newSL = NormalizeDouble(ask + trailStep, _Digits);
            if((newSL < sl - 5 * _Point || sl == 0) && IsModifySLValid(newSL, false))
               trade.PositionModify(ticket, newSL, tp);
         }

         // v3.0: シャンデリアイグジット（SELL）
         if(UseChandelierExit && curATR > 0 && (sl <= openPrice && sl > 0))
         {
            double lowestLow = DBL_MAX;
            for(int k = 1; k <= Chandelier_Period; k++)
            {
               double ll = iLow(_Symbol, PERIOD_M15, k);
               if(ll < lowestLow) lowestLow = ll;
            }
            double chandelierSL = lowestLow + curATR * Chandelier_ATR_Multi;
            chandelierSL = NormalizeDouble(chandelierSL, _Digits);
            if(chandelierSL < sl - 5 * _Point && IsModifySLValid(chandelierSL, false))
               trade.PositionModify(ticket, chandelierSL, tp);
         }

         // v6.0: ATR Ratchet Trailing (SELL)
         if(UseATRRatchetTrail && curATR > 0 && profitDist > 0) {
            double atrMultiples = profitDist / curATR;
            if(atrMultiples >= 2.0) {
               double ratchetStep = curATR * MathMax(0.3, RatchetStepATR * (1.0 / atrMultiples * 2));
               double ratchetSL = ask + ratchetStep;
               ratchetSL = NormalizeDouble(ratchetSL, _Digits);
               if(ratchetSL < sl - 5 * _Point && IsModifySLValid(ratchetSL, false))
                  trade.PositionModify(ticket, ratchetSL, tp);
            }
         }

         // v6.0: Time-decay SL tightening (SELL)
         if(UseTimeDecaySL && sl > openPrice) {
            double hoursOpen = (double)(TimeCurrent() - (datetime)PositionGetInteger(POSITION_TIME)) / 3600.0;
            int barsOpen = (int)(hoursOpen * 4); // M15 bars
            if(barsOpen >= TimeDecayStartBars) {
               double decayPeriods = (double)(barsOpen - TimeDecayStartBars) / TimeDecayStartBars;
               double decayFactor = MathPow(TimeDecayRate, decayPeriods);
               double origSLDist = sl - openPrice;
               double decayedSLDist = MathMax(MinSL_Points * _Point, origSLDist * decayFactor);
               double newSL = NormalizeDouble(openPrice + decayedSLDist, _Digits);
               if(newSL < sl && IsModifySLValid(newSL, false))
                  trade.PositionModify(ticket, newSL, tp);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| 半利確トラッキング                                                  |
//+------------------------------------------------------------------+
// FIX: Issue #13 - Robust partial close tracking with stale entry cleanup
bool IsPartialClosed(ulong ticket)
{
   for(int i = 0; i < ArraySize(partialClosedTickets); i++)
      if(partialClosedTickets[i] == ticket) return true;
   return false;
}

void MarkPartialClosed(ulong ticket)
{
   // FIX: Issue #13 - Clean up tickets for positions that are no longer open
   CleanupPartialClosedTickets();

   int sz = ArraySize(partialClosedTickets);
   ArrayResize(partialClosedTickets, sz + 1);
   partialClosedTickets[sz] = ticket;
}

// FIX: Issue #13 - Remove stale entries from partialClosedTickets
void CleanupPartialClosedTickets()
{
   int sz = ArraySize(partialClosedTickets);
   if(sz == 0) return;

   ulong cleanTickets[];
   int cleanCount = 0;

   for(int i = 0; i < sz; i++)
   {
      // Check if this ticket still corresponds to an open position
      bool stillOpen = false;
      for(int p = PositionsTotal() - 1; p >= 0; p--)
      {
         ulong posTicket = PositionGetTicket(p);
         if(posTicket == partialClosedTickets[i])
         {
            stillOpen = true;
            break;
         }
      }
      if(stillOpen)
      {
         cleanCount++;
         ArrayResize(cleanTickets, cleanCount);
         cleanTickets[cleanCount - 1] = partialClosedTickets[i];
      }
   }

   ArrayResize(partialClosedTickets, cleanCount);
   for(int i = 0; i < cleanCount; i++)
      partialClosedTickets[i] = cleanTickets[i];
}

//+------------------------------------------------------------------+
//| SL検知 — クールダウン用 + v3.0: トレード結果記録                   |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      // FIX: Issue #19 - Call HistorySelect before accessing deal history
      HistorySelect(0, TimeCurrent());
      if(HistoryDealSelect(trans.deal))
      {
         long dealMagic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
         long dealEntry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
         long dealReason = HistoryDealGetInteger(trans.deal, DEAL_REASON);

         // v12.1: Store component mask when position is opened
         // CRITICAL #2 FIX: Parse CM from deal comment instead of g_pendingComponentMask
         if(dealMagic == MagicNumber && dealEntry == DEAL_ENTRY_IN)
         {
            ulong posID = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
            if(posID > 0)
            {
               string dealComment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
               int cmMask = ParseComponentMaskFromComment(dealComment);
               if(cmMask != 0)
               {
                  int idx = g_trackCount % COMP_TRACK_MAX;
                  g_trackPosIDs[idx] = posID;
                  g_trackMasks[idx]  = cmMask;
                  g_trackCount++;
               }
            }
         }

         if(dealMagic == MagicNumber && dealEntry == DEAL_ENTRY_OUT)
         {
            // CODEX-FIX: NEW HIGH #3 - Skip partial closes for statistics
            ulong posID_out = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
            if(PositionSelectByTicket(posID_out))
            {
               // Position still open = partial close, skip statistics recording
               return;
            }
            // Position gone = full close, record statistics below

            // SLクールダウン
            if(dealReason == DEAL_REASON_SL)
            {
               lastSLTime = TimeCurrent();
               // FIX: Issue #22 - Persist lastSLTime across EA restarts
               GlobalVariableSet(GVKey("lastSL"), (double)lastSLTime);
               Print("SLクールダウン開始: ", CooldownMinutes, "分間エントリー停止");
            }

            // v3.0: トレード結果を循環バッファに記録
            double dealProfit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
            double dealComm   = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
            double dealSwap   = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
            double netResult  = dealProfit + dealComm + dealSwap;

            // v4.0: 日次PnL追跡 + サーキットブレーカー
            g_dailyPnL += netResult;
            // CODEX-FIX: NEW HIGH #6 - Persist circuit breaker state across restarts
            GlobalVariableSet(GVKey("cbPnL"), g_dailyPnL);
            GlobalVariableSet(GVKey("cbDate"), (double)g_lastDay);
            if(g_dailyPnL <= -(AccountInfoDouble(ACCOUNT_BALANCE) * DailyMaxLossPct / 100.0))
            {
               g_circuitBreaker = true;
               Print("v4.0: デイリーサーキットブレーカー発動! 日次PnL: ", g_dailyPnL);
            }

            recentTradeResults[tradeResultIndex] = netResult;
            tradeResultIndex = (tradeResultIndex + 1) % 50;
            if(tradeResultCount < 50) tradeResultCount++;

            totalTradesTracked++;

            // v13.0: Record MAE/MFE trade quality metric
            // Approximate MAE from SL hit: if deal closed by SL, MAE ≈ SL distance (ratio ~1.0)
            // For TP or trailing exits, MAE = how far against position went.
            // We use the loss magnitude relative to entry SL distance as a proxy.
            if(UseTradeQuality)
            {
               double dealVolume = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
               if(dealVolume > 0)
               {
                  double dealPrice = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
                  // Look up entry deal in this position's history to find SL distance
                  // Simplified: use netResult to approximate entry quality
                  // Bad entry = large loss relative to expected risk
                  double currentATR_tq = GetCurrentATR();
                  if(currentATR_tq > 0)
                  {
                     // For SL-exited trades, the loss is approximately the SL distance
                     // MAE ratio = |loss per pt| / (ATR * SL_ATR_Multi)
                     double expectedSLDist = currentATR_tq * SL_ATR_Multi;
                     double maeRatio = 0;
                     if(dealReason == DEAL_REASON_SL && expectedSLDist > 0)
                        maeRatio = 1.0; // Hit SL = MAE reached full SL
                     else if(netResult < 0 && expectedSLDist > 0)
                     {
                        // Partial loss: estimate MAE ratio
                        double lossPerLot = MathAbs(netResult / dealVolume);
                        double contractSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
                        if(contractSize > 0)
                        {
                           double lossPts = lossPerLot / contractSize / _Point;
                           double slPts = expectedSLDist / _Point;
                           maeRatio = (slPts > 0) ? lossPts / slPts : 0;
                        }
                     }
                     // Winning trades: MAE < SL, estimate conservatively
                     else if(netResult >= 0)
                        maeRatio = 0.3; // Assumed low MAE for winners
                     RecordTradeQuality(maeRatio);
                  }
               }
            }

            // v12.1: Update component effectiveness stats
            if(UseDynamicComponentScoring)
            {
               ulong posID = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
               int mask = LookupComponentMask(posID);
               if(mask != 0)
               {
                  bool isWin = (netResult > 0);
                  for(int c = 0; c < COMP_COUNT; c++)
                  {
                     if(mask & (1 << c))
                     {
                        g_compTotal[c]++;
                        if(isWin) g_compWins[c]++;
                        // Cap at rolling window of ~100 trades per component
                        // to keep stats recent (decay oldest by halving)
                        // FIX: Issue #15 - Remove +1 bias that distorts win rates during decay
                        if(g_compTotal[c] > 100)
                        {
                           g_compWins[c]  = g_compWins[c] / 2;
                           g_compTotal[c] = g_compTotal[c] / 2;
                        }
                     }
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| v4.0: ニュースフィルター (CalendarValueHistory使用)                 |
//+------------------------------------------------------------------+
bool IsNewsTime()
{
   if(!UseNewsFilter) return false;
   MqlCalendarValue values[];
   datetime from = TimeCurrent() - NewsBlockMinutes * 60;
   datetime to   = TimeCurrent() + NewsBlockMinutes * 60;
   int count = CalendarValueHistory(values, from, to);
   for(int i = 0; i < count; i++)
   {
      MqlCalendarEvent event;
      if(CalendarEventById(values[i].event_id, event))
      {
         if(event.importance == CALENDAR_IMPORTANCE_HIGH)
         {
            // FIX: Issue #10 - Only block for Gold-relevant currencies (USD, EUR, XAU)
            MqlCalendarCountry country;
            if(CalendarCountryById(event.country_id, country))
            {
               string cur = country.currency;
               if(cur != "USD" && cur != "EUR" && cur != "XAU")
                  continue;
            }
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| v4.0: 動的スプレッドチェック                                       |
//+------------------------------------------------------------------+
bool IsDynamicSpreadOK()
{
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return (spread <= MaxDynamicSpread);
}

//+------------------------------------------------------------------+
//| v4.0: 週末クローズ判定                                             |
//+------------------------------------------------------------------+
bool IsWeekendClose()
{
   if(!UseWeekendClose) return false;
   MqlDateTime dt;
   TimeCurrent(dt);
   // REVIEW-FIX: Issue 3.6 - Apply GMTOffset to FridayCloseHour check
   int gmtHour = (dt.hour - GMTOffset + 24) % 24;
   return (dt.day_of_week == 5 && gmtHour >= FridayCloseHour);
}

//+------------------------------------------------------------------+
//| v4.0: 全ポジション決済                                             |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
//| v4.0: 4状態レジーム判定                                            |
//| 0=Crash, 1=Ranging, 2=Trending, 3=Volatile                       |
//+------------------------------------------------------------------+
int GetAdvancedRegime(double currentATR)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(h_m15_atr, 0, 1, VolRegime_Period, atr) < VolRegime_Period) return 2;

   double sum = 0;
   for(int i = 0; i < VolRegime_Period; i++) sum += atr[i];
   double avgATR = sum / VolRegime_Period;

   if(avgATR <= 0) return 2;
   double ratio = currentATR / avgATR;

   if(ratio >= CrashATRMulti) return 0;  // Crash
   if(ratio <= VolRegime_Low) return 1;  // Ranging
   if(ratio >= VolRegime_High) return 3; // Volatile
   return 2;                              // Trending
}

//+------------------------------------------------------------------+
//| v4.0: 塩漬けトレード決済                                           |
//+------------------------------------------------------------------+
void CheckStaleTradeExit()
{
   if(StaleTradeHours <= 0) return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      double hours = (double)(TimeCurrent() - openTime) / 3600.0;

      // FIX: Issue #18 - Close profitable stale trades at StaleTradeHours,
      // and force-close unprofitable stale trades at StaleTradeHours * 2
      double profit = PositionGetDouble(POSITION_PROFIT);
      if(hours >= StaleTradeHours && profit >= 0)
      {
         trade.PositionClose(ticket);
         Print("v4.0: 塩漬けトレード決済 (", DoubleToString(hours,1), "時間経過, profit>=0)");
      }
      else if(hours >= StaleTradeHours * 2)
      {
         trade.PositionClose(ticket);
         Print("v4.0: 塩漬けトレード強制決済 (", DoubleToString(hours,1), "時間経過, profit=", DoubleToString(profit,2), ")");
      }
   }
}

//+------------------------------------------------------------------+
//| v4.0: モメンタムバースト (全TF整合 +3点)                           |
//+------------------------------------------------------------------+
int GetMomentumBurst()
{
   if(!UseMomentumBurst) return 0;

   double h4_f = GetIndicatorValue(h_h4_ma_fast, 0, 1);
   double h4_s = GetIndicatorValue(h_h4_ma_slow, 0, 1);
   double h1_f = GetIndicatorValue(h_h1_ma_fast, 0, 1);
   double h1_s = GetIndicatorValue(h_h1_ma_slow, 0, 1);
   double m15_f = GetIndicatorValue(h_m15_ma_fast, 0, 1);
   double m15_s = GetIndicatorValue(h_m15_ma_slow, 0, 1);

   if(h4_f == 0 || h4_s == 0 || h1_f == 0 || h1_s == 0 || m15_f == 0 || m15_s == 0) return 0;

   bool allBull = (h4_f > h4_s) && (h1_f > h1_s) && (m15_f > m15_s);
   bool allBear = (h4_f < h4_s) && (h1_f < h1_s) && (m15_f < m15_s);

   if(allBull) return 1;
   if(allBear) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| v4.0: ボリュームクライマックス (+2点)                              |
//+------------------------------------------------------------------+
int GetVolumeClimax()
{
   if(!UseVolumeClimax) return 0;

   // CRITICAL #5 FIX: Use bar 1 (last confirmed bar) instead of bar 0
   long vol[];
   ArraySetAsSeries(vol, true);
   if(CopyTickVolume(_Symbol, PERIOD_M15, 1, 22, vol) < 22) return 0;

   double sum = 0;
   for(int i = 1; i <= 20; i++) sum += (double)vol[i];
   double avgVol = sum / 20.0;

   if(avgVol > 0 && (double)vol[0] > avgVol * 2.0)
   {
      double c1 = iClose(_Symbol, PERIOD_M15, 1);
      double c2 = iClose(_Symbol, PERIOD_M15, 2);
      double o1 = iOpen(_Symbol, PERIOD_M15, 1);
      if(c1 > o1 && c1 > c2) return 1;   // Bullish climax
      if(c1 < o1 && c1 < c2) return -1;  // Bearish climax
   }
   return 0;
}

//+------------------------------------------------------------------+
//| v13.0: 段階的リバーサルモード (スコアベース)                       |
//| Returns direction in reversalDirection, confidence (0.0-1.0) in   |
//| reversalConfidence. Score-based approach: min 2/5 threshold.      |
//+------------------------------------------------------------------+
bool CheckReversal(int &reversalDirection, double &reversalConfidence)
{
   if(!UseReversalMode) return false;
   reversalConfidence = 0.0;

   double rsi = GetIndicatorValue(h_h1_rsi, 0, 1);
   if(rsi == 0) return false;

   int revBuy = 0;
   int revSell = 0;

   // 1. RSI extreme (0-2 pts, graduated)
   if(rsi < 20)       revBuy += 2;
   else if(rsi < 30)  revBuy += 1;
   if(rsi > 80)       revSell += 2;
   else if(rsi > 70)  revSell += 1;

   // 2. RSI Divergence (0-1 pt)
   int divSignal = GetDivergence();
   if(divSignal > 0)  revBuy += 1;
   else if(divSignal < 0) revSell += 1;

   // 3. S/R proximity (0-1 pt)
   int srSignal = GetSRSignal(iClose(_Symbol, PERIOD_H1, 1), GetCurrentATR());
   if(srSignal > 0)   revBuy += 1;
   else if(srSignal < 0) revSell += 1;

   // 4. Candle pattern (0-1 pt)
   int candleSignal = GetCandlePattern();
   if(candleSignal > 0)  revBuy += 1;
   else if(candleSignal < 0) revSell += 1;

   // Minimum 2/5 threshold (relaxed from 4/4 all-or-nothing)
   if(revBuy >= ReversalMinScore && revBuy > revSell)
   {
      reversalDirection = 1;
      reversalConfidence = (double)revBuy / 5.0;
      return true;
   }
   if(revSell >= ReversalMinScore && revSell > revBuy)
   {
      reversalDirection = -1;
      reversalConfidence = (double)revSell / 5.0;
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| CRITICAL #2 FIX: コメント文字列からCM=値をパース                    |
//+------------------------------------------------------------------+
int ParseComponentMaskFromComment(string comment)
{
   int cmPos = StringFind(comment, "|CM=");
   if(cmPos < 0) return 0;
   string cmStr = StringSubstr(comment, cmPos + 4);
   // Truncate at next | if any
   int nextPipe = StringFind(cmStr, "|");
   if(nextPipe >= 0)
      cmStr = StringSubstr(cmStr, 0, nextPipe);
   return (int)StringToInteger(cmStr);
}

// CODEX-FIX: NEW HIGH #8 - Parse entry regime from trade comment
string ParseRegimeFromComment(string comment)
{
   int rgPos = StringFind(comment, "|RG=");
   if(rgPos < 0) return "";
   string rgStr = StringSubstr(comment, rgPos + 4);
   int nextPipe = StringFind(rgStr, "|");
   if(nextPipe >= 0)
      rgStr = StringSubstr(rgStr, 0, nextPipe);
   return rgStr;
}

//+------------------------------------------------------------------+
//| v12.1: コンポーネント有効性 — ポジションIDからマスク検索            |
//+------------------------------------------------------------------+
int LookupComponentMask(ulong posID)
{
   // Search all valid entries in the ring buffer
   for(int i = 0; i < COMP_TRACK_MAX; i++)
   {
      if(g_trackPosIDs[i] == posID)
         return g_trackMasks[i];
   }
   return 0;
}

//+------------------------------------------------------------------+
//| v12.1: コンポーネント有効性ウェイト取得                             |
//+------------------------------------------------------------------+
double GetComponentEffectiveness(int compIdx)
{
   if(!UseDynamicComponentScoring) return 1.0;
   if(compIdx < 0 || compIdx >= COMP_COUNT) return 1.0;
   if(g_compTotal[compIdx] < CompEffectMinTrades) return 1.0;

   double wr = (double)g_compWins[compIdx] / (double)g_compTotal[compIdx];
   if(wr >= CompEffectBoostWR)   return CompEffectBoostWeight;
   if(wr <= CompEffectPenaltyWR) return CompEffectPenaltyWeight;
   return 1.0;
}

//+------------------------------------------------------------------+
//| v12.1: コンポーネント有効性統計の保存                               |
//+------------------------------------------------------------------+
void SaveComponentStats()
{
   if(!UseDynamicComponentScoring) return;
   // CODEX-FIX: NEW HIGH #4 - Use symbol/magic-scoped GV keys
   for(int i = 0; i < COMP_COUNT; i++)
   {
      GlobalVariableSet(GVKey("CE_W_" + IntegerToString(i)), (double)g_compWins[i]);
      GlobalVariableSet(GVKey("CE_T_" + IntegerToString(i)), (double)g_compTotal[i]);
   }
}

//+------------------------------------------------------------------+
//| v12.1: コンポーネント有効性統計の読込                               |
//+------------------------------------------------------------------+
void LoadComponentStats()
{
   if(!UseDynamicComponentScoring) return;
   // CODEX-FIX: NEW HIGH #4 - Use symbol/magic-scoped GV keys
   if(!GlobalVariableCheck(GVKey("CE_T_0"))) return;

   for(int i = 0; i < COMP_COUNT; i++)
   {
      if(GlobalVariableCheck(GVKey("CE_W_" + IntegerToString(i))))
         g_compWins[i] = (int)GlobalVariableGet(GVKey("CE_W_" + IntegerToString(i)));
      if(GlobalVariableCheck(GVKey("CE_T_" + IntegerToString(i))))
         g_compTotal[i] = (int)GlobalVariableGet(GVKey("CE_T_" + IntegerToString(i)));
   }

   // Log loaded stats
   string statsLog = "v12.1: Component Effectiveness loaded — ";
   for(int i = 0; i < COMP_COUNT; i++)
   {
      if(g_compTotal[i] >= CompEffectMinTrades)
      {
         double wr = (double)g_compWins[i] / (double)g_compTotal[i];
         statsLog += StringFormat("C%d:%.0f%%(%d) ", i, wr*100, g_compTotal[i]);
      }
   }
   Print(statsLog);
}

// CODEX-FIX: NEW HIGH #4 - Helper function for symbol/magic-scoped GlobalVariable keys
string GVKey(string suffix)
{
   return "AGMTF_" + IntegerToString(MagicNumber) + "_" + _Symbol + "_" + suffix;
}

// CODEX-FIX: NEW HIGH #6 - Validate SL/TP against STOPS_LEVEL and FREEZE_LEVEL
void ValidateStopsDistance(double price, double &sl, double &tp, bool isBuy)
{
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double minDist = MathMax((double)stopsLevel, (double)freezeLevel) * _Point;
   if(minDist <= 0) return;  // No restriction

   if(isBuy)
   {
      if(price - sl < minDist) sl = NormalizeDouble(price - minDist, _Digits);
      if(tp - price < minDist) tp = NormalizeDouble(price + minDist, _Digits);
   }
   else
   {
      if(sl - price < minDist) sl = NormalizeDouble(price + minDist, _Digits);
      if(price - tp < minDist) tp = NormalizeDouble(price - minDist, _Digits);
   }
}

// CODEX-FIX: NEW HIGH #6 - Check if SL modification is valid (for PositionModify calls)
bool IsModifySLValid(double newSL, bool isBuy)
{
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double minDist = MathMax((double)stopsLevel, (double)freezeLevel) * _Point;
   if(minDist <= 0) return true;

   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double dist = isBuy ? (price - newSL) : (newSL - price);
   return (dist >= minDist);
}

//+------------------------------------------------------------------+
//| v13.0: ER計算（指定期間）                                          |
//+------------------------------------------------------------------+
double CalcERForPeriod(int period)
{
   double h4CloseArr[];
   ArraySetAsSeries(h4CloseArr, true);
   // Use shift 1 (confirmed H4 bar) for consistency
   if(CopyClose(_Symbol, PERIOD_H4, 1, period + 1, h4CloseArr) < period + 1)
      return 0.5; // Default neutral ER
   double netChange = MathAbs(h4CloseArr[0] - h4CloseArr[period]);
   double sumAbsChanges = 0;
   for(int k = 0; k < period; k++)
      sumAbsChanges += MathAbs(h4CloseArr[k] - h4CloseArr[k+1]);
   if(sumAbsChanges <= 0) return 0;
   return netChange / sumAbsChanges;
}

//+------------------------------------------------------------------+
//| v13.0: マルチスケール3層ERレジーム分類                              |
//| fast/med/slow ERで詳細レジーム: crash, high_vol_trend,             |
//| high_vol_range, trend_strong, trend_weak, range                    |
//+------------------------------------------------------------------+
string DetectRegimeV13(double fast_er, double med_er, double slow_er, double vol_ratio)
{
   if(vol_ratio >= RegimeVolCrash) return "crash";

   bool isTrendingFast = (fast_er >= 0.3);
   bool isTrendingMed  = (med_er >= 0.3);
   bool isHighVol      = (vol_ratio >= RegimeVolHigh);

   if(isHighVol)
   {
      if(isTrendingFast) return "high_vol_trend";
      return "high_vol_range";
   }

   if(isTrendingFast && isTrendingMed) return "trend_strong";
   if(isTrendingFast && !isTrendingMed) return "trend_weak";

   return "range";
}

//+------------------------------------------------------------------+
//| v13.0: レジーム安定性追跡                                          |
//+------------------------------------------------------------------+
void UpdateRegimeStability(string newRegime)
{
   if(!UseMultiscaleRegime) return;

   // Map v13 regimes to base regimes for stability check
   string baseNew = MapToBaseRegime(newRegime);
   string baseLast = MapToBaseRegime(g_lastStableRegime);

   if(baseNew == baseLast)
   {
      g_regimeStableCount++;
   }
   else
   {
      g_regimeStableCount = 1;
      g_regimeTransitionTime = TimeCurrent();
   }

   g_lastStableRegime = newRegime;
   g_regimeConfirmed = (g_regimeStableCount >= RegimeStabilityBars);

   // Calculate transition lot multiplier
   double hoursSince = (double)(TimeCurrent() - g_regimeTransitionTime) / 3600.0;
   if(hoursSince < 3.0) // 12 M15 bars = 3 hours
      g_regimeTransitionMult = RegimeTransitionPenalty;
   else
      g_regimeTransitionMult = 1.0;
}

//+------------------------------------------------------------------+
//| v13.0: 詳細レジームをベースレジームにマッピング                     |
//+------------------------------------------------------------------+
string MapToBaseRegime(string regime)
{
   if(regime == "trend_strong" || regime == "trend_weak") return "trend";
   if(regime == "high_vol_trend" || regime == "high_vol_range") return "high_vol";
   return regime; // "range", "crash" - unchanged
}

//+------------------------------------------------------------------+
//| v13.0: リアルタイムスパイク検知                                     |
//+------------------------------------------------------------------+
bool DetectRealtimeSpike(double currentATR)
{
   if(!UseRealtimeSpike || currentATR <= 0) return false;

   // Check current M15 bar range
   double barHigh  = iHigh(_Symbol, PERIOD_M15, 0);
   double barLow   = iLow(_Symbol, PERIOD_M15, 0);
   double barRange = barHigh - barLow;

   if(barRange > currentATR * SpikeATRMulti) return true;

   // 2-bar combined range check (catches gap moves)
   double prevHigh = iHigh(_Symbol, PERIOD_M15, 1);
   double prevLow  = iLow(_Symbol, PERIOD_M15, 1);
   double twoBarHigh = MathMax(barHigh, prevHigh);
   double twoBarLow  = MathMin(barLow, prevLow);
   double twoBarRange = twoBarHigh - twoBarLow;

   if(twoBarRange > currentATR * SpikeATRMulti * 1.5) return true;

   return false;
}

//+------------------------------------------------------------------+
//| v13.0: スパイク時に損失ポジションを即座にクローズ                   |
//+------------------------------------------------------------------+
void CloseLosingSpikePositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetDouble(POSITION_PROFIT) < 0)
      {
         trade.PositionClose(ticket);
         Print("v13.0: Spike detected - closed losing position ticket=", ticket);
      }
   }
}

//+------------------------------------------------------------------+
//| v13.0: コンポーネント相関キャップ適用                               |
//| Correlated component groups get reduced to avoid double-counting  |
//+------------------------------------------------------------------+
void ApplyCorrelationCap(int &buyScore, int &sellScore, int componentMask)
{
   // Group 1: trend_alignment = H4 Trend(bit 0, 3pt) + Momentum Burst(bit 12, 3pt)
   // H4 Trend is stronger (has ADX filter), so Burst is secondary
   {
      bool h4Fired   = (componentMask & (1 << 0)) != 0;
      bool burstFired = (componentMask & (1 << 12)) != 0;
      if(h4Fired && burstFired)
      {
         // Reduce Momentum Burst by 50% (3 * 0.5 = 1.5, floored to 1)
         int reduction = (int)(3.0 * CorrelationCapRatio);
         // Determine burst direction from componentMask
         // If H4 and Burst are same direction, reduce
         bool h4Buy = (componentMask & (1 << 16)) != 0;
         // Burst follows same direction (all TFs aligned), reduce the score
         if(h4Buy)
            buyScore -= reduction;
         else
            sellScore -= reduction;
      }
   }

   // Group 2: rsi_family = H1 RSI(bit 2, 1pt) + H4 RSI (no bit, skip)
   // H4 RSI has no CE tracking bit, so cannot detect overlap reliably. Skip.

   // Group 3: ma_family = H1 MA(bit 1, 2pt) + M15 MA Cross(bit 4, 2pt)
   {
      bool h1maFired  = (componentMask & (1 << 1)) != 0;
      bool m15maFired = (componentMask & (1 << 4)) != 0;
      if(h1maFired && m15maFired)
      {
         // M15 MA Cross is secondary (shorter TF), reduce by 50% = 1pt
         int reduction = (int)(2.0 * CorrelationCapRatio);
         // Determine M15 direction - if H1 MA is buy, M15 likely same
         // We can't easily distinguish direction per component in the bitmask,
         // so apply to the winning side
         if(buyScore >= sellScore)
            buyScore -= reduction;
         else
            sellScore -= reduction;
      }
   }

   // Clamp to 0
   buyScore  = (int)MathMax(0, buyScore);
   sellScore = (int)MathMax(0, sellScore);
}

//+------------------------------------------------------------------+
//| v13.0: MAE/MFEベースのエントリー品質ペナルティ                     |
//+------------------------------------------------------------------+
int GetTradeQualityPenalty()
{
   if(!UseTradeQuality) return 0;
   if(g_tqCount < TQMinTrades) return 0;

   // Calculate ratio of bad entries (MAE > threshold of SL)
   int badEntries = 0;
   int total = MathMin(g_tqCount, TQ_MAX);
   for(int i = 0; i < total; i++)
   {
      if(g_tqMAERatios[i] > TQMAEThreshold)
         badEntries++;
   }
   double badRatio = (double)badEntries / (double)total;
   if(badRatio > TQBadEntryLimit)
      return TQScorePenalty;
   return 0;
}

//+------------------------------------------------------------------+
//| v13.0: MAE/MFE品質トラッカーに記録                                 |
//+------------------------------------------------------------------+
void RecordTradeQuality(double maeRatio)
{
   if(!UseTradeQuality) return;
   g_tqMAERatios[g_tqIndex] = maeRatio;
   g_tqIndex = (g_tqIndex + 1) % TQ_MAX;
   if(g_tqCount < TQ_MAX) g_tqCount++;
}

//+------------------------------------------------------------------+
//| ユーティリティ関数群                                                |
//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         count++;
   }
   return count;
}

bool CheckTimeFilter()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   // REVIEW-FIX: Issue 3.5 - Apply GMTOffset for correct UTC-based time filtering
   // REVIEW-FIX: Issue 3.6 - Apply GMTOffset to AvoidFriday check
   int gmtHour = (dt.hour - GMTOffset + 24) % 24;
   if(gmtHour < TradeStartHour || gmtHour >= TradeEndHour) return false;
   if(AvoidFriday && dt.day_of_week == 5 && gmtHour >= 18) return false;
   return true;
}

bool CheckSpread()
{
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return spread <= MaxSpread;
}

bool IsTradeAllowed()
{
   return MQLInfoInteger(MQL_TRADE_ALLOWED) &&
          TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) &&
          AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
}

double GetIndicatorValue(int handle, int buffer, int shift)
{
   double val[];
   if(CopyBuffer(handle, buffer, shift, 1, val) <= 0) return 0;
   return val[0];
}
//+------------------------------------------------------------------+
