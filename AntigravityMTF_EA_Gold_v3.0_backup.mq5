//+------------------------------------------------------------------+
//|                              AntigravityMTF_EA_Gold.mq5          |
//|            ゴールド(XAUUSD)専用 マルチタイムフレーム EA             |
//|            v3.0: USD相関+RSIダイバージェンス+S/R+ローソク足+適応型  |
//+------------------------------------------------------------------+
#property copyright "Antigravity Trading System"
#property version   "3.00"
#property description "XAUUSD専用 v3.0: 動的SL/TP + ボラレジーム + セッション + 半利確 + USD相関 + ダイバージェンス + S/R + ローソク足 + シャンデリア + 適応型サイジング"

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| 入力パラメータ                                                      |
//+------------------------------------------------------------------+
input group "=== リスク管理 ==="
input double RiskPercent       = 0.3;     // リスク% ★ATR-SLで自動調整
input double MaxLots           = 0.50;
input double MinLots           = 0.01;
input int    MaxSpread         = 50;
input int    MaxPositions      = 1;
input int    MagicNumber       = 20260224;
input double MaxDrawdownPct    = 6.0;
input double DDHalfRiskPct     = 2.5;

input group "=== 動的損益設定（ATRベース） ==="
input int    ATR_Period_SL     = 14;      // ATR期間（SL/TP計算用・M15）
input double SL_ATR_Multi      = 1.5;     // SL = M15 ATR × 倍率
input double TP_ATR_Multi      = 3.5;     // TP = M15 ATR × 倍率 (RR 1:2.3)
input double Trail_ATR_Multi   = 1.0;     // トレーリングステップ = ATR × 倍率
input double BE_ATR_Multi      = 1.5;     // 建値移動 = ATR × 倍率
input double MinSL_Points      = 200.0;   // 最小SL (ポイント)
input double MaxSL_Points      = 1500.0;  // 最大SL (ポイント)

input group "=== ボラティリティレジーム ==="
input int    VolRegime_Period  = 50;      // ATR平均期間（レジーム判定）
input double VolRegime_Low     = 0.7;     // 低ボラ閾値（これ以下はスキップ）
input double VolRegime_High    = 1.5;     // 高ボラ閾値（SL倍率を拡大）
input double HighVol_SL_Bonus  = 0.5;     // 高ボラ時のSL追加倍率

input group "=== トレンドフィルター（H4足） ==="
input int    H4_MA_Fast        = 20;
input int    H4_MA_Slow        = 50;
input int    H4_ADX_Period     = 14;
input int    H4_ADX_Threshold  = 20;

input group "=== メイン足（H1） ==="
input int    H1_MA_Fast        = 10;
input int    H1_MA_Slow        = 30;
input int    H1_RSI_Period     = 14;
input int    H1_BB_Period      = 20;
input double H1_BB_Deviation   = 2.0;

input group "=== エントリー足（M15） ==="
input int    M15_MA_Fast       = 5;
input int    M15_MA_Slow       = 20;

input group "=== スコアリング ==="
input int    MinEntryScore     = 9;       // 最低スコア 9/22
input bool   UseSessionBonus   = true;    // セッションボーナス有効
input bool   UseMomentum       = true;    // モメンタム確認有効

input group "=== 時間フィルター ==="
input int    TradeStartHour    = 8;
input int    TradeEndHour      = 22;
input bool   AvoidFriday       = true;
input int    CooldownMinutes   = 240;

input group "=== 半利確 ==="
input bool   UsePartialClose   = true;    // 半分利確を有効化
input double PartialCloseRatio = 0.5;     // 利確するポジション割合
input double PartialTP_Ratio   = 0.5;     // TP距離の何%で半利確

input group "=== USD相関フィルター ==="
input bool   UseCorrelation    = true;
input string CorrelationSymbol = "USDJPY";
input int    Corr_MA_Fast      = 10;
input int    Corr_MA_Slow      = 30;
input double Corr_Threshold    = 0.3;

input group "=== RSIダイバージェンス ==="
input bool   UseDivergence     = true;
input int    Div_Lookback      = 30;
input int    Div_SwingStrength  = 3;

input group "=== サポート/レジスタンス ==="
input bool   UseSRLevels       = true;
input int    SR_Lookback       = 100;
input int    SR_SwingStrength   = 5;
input double SR_Cluster_ATR    = 1.0;
input double SR_Proximity_ATR  = 0.5;

input group "=== ローソク足パターン ==="
input bool   UseCandlePatterns = true;

input group "=== H4 RSI ==="
input int    H4_RSI_Period     = 14;
input bool   UseH4RSI          = true;

input group "=== シャンデリアイグジット ==="
input bool   UseChandelierExit = true;
input int    Chandelier_Period = 22;
input double Chandelier_ATR_Multi = 3.0;

input group "=== エクイティカーブ取引 ==="
input bool   UseEquityCurveFilter = true;
input int    EquityMA_Period      = 10;
input double EquityReduce_Factor  = 0.5;

input group "=== 適応的ポジションサイジング ==="
input bool   UseAdaptiveSizing   = true;
input int    Kelly_LookbackTrades = 30;
input double Kelly_Fraction       = 0.5;
input double Kelly_MinRisk        = 0.1;
input double Kelly_MaxRisk        = 1.0;

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;
double         peakBalance;
int            h_h4_ma_fast, h_h4_ma_slow, h_h4_adx;
int            h_h1_ma_fast, h_h1_ma_slow, h_h1_rsi, h_h1_bb;
int            h_m15_ma_fast, h_m15_ma_slow;
int            h_m15_atr;                 // M15 ATR（動的SL/TP用）
datetime       lastBarTime;
datetime       lastSLTime;
ulong          partialClosedTickets[];

// v3.0 新規グローバル変数
int            h_h4_rsi;
int            h_usdjpy_ma_fast, h_usdjpy_ma_slow, h_usdjpy_atr;
double         recentTradeResults[50];
int            tradeResultIndex;
int            tradeResultCount;
int            compWins[12];
int            compTotal[12];
double         compWeights[12];
int            totalTradesTracked;
bool           g_UseCorrelation;          // 実行時フラグ（シンボル不可時false）

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   peakBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   // H4 インジケーター
   h_h4_ma_fast = iMA(_Symbol, PERIOD_H4, H4_MA_Fast, 0, MODE_SMA, PRICE_CLOSE);
   h_h4_ma_slow = iMA(_Symbol, PERIOD_H4, H4_MA_Slow, 0, MODE_SMA, PRICE_CLOSE);
   h_h4_adx     = iADX(_Symbol, PERIOD_H4, H4_ADX_Period);

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
      h_h4_adx == INVALID_HANDLE || h_h1_ma_fast == INVALID_HANDLE ||
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

   // v3.0: コンポーネントウェイト初期化
   for(int i = 0; i < 12; i++)
   {
      compWeights[i] = 1.0;
      compWins[i]    = 0;
      compTotal[i]   = 0;
   }
   tradeResultIndex  = 0;
   tradeResultCount  = 0;
   totalTradesTracked = 0;
   ArrayInitialize(recentTradeResults, 0.0);
   LoadWeights();

   Print("AntigravityMTF EA [GOLD] v3.0 初期化完了");
   Print("   動的SL/TP: SL=ATR×", SL_ATR_Multi, " TP=ATR×", TP_ATR_Multi);
   Print("   ボラレジーム: Low<", VolRegime_Low, " High>", VolRegime_High);
   Print("   USD相関: ", (g_UseCorrelation ? "有効" : "無効"));
   Print("   ダイバージェンス: ", (UseDivergence ? "有効" : "無効"));
   Print("   S/Rレベル: ", (UseSRLevels ? "有効" : "無効"));
   Print("   ローソク足パターン: ", (UseCandlePatterns ? "有効" : "無効"));
   Print("   シャンデリアイグジット: ", (UseChandelierExit ? "有効" : "無効"));
   Print("   適応的サイジング: ", (UseAdaptiveSizing ? "有効" : "無効"));
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

   SaveWeights();
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpenPositions();

   datetime currentBar = iTime(_Symbol, PERIOD_M15, 0);
   if(currentBar == lastBarTime) return;
   lastBarTime = currentBar;

   if(!IsTradeAllowed()) return;
   if(!CheckTimeFilter()) return;
   if(!CheckSpread()) return;
   if(CountMyPositions() >= MaxPositions) return;

   // SL後クールダウン
   if(lastSLTime > 0 && TimeCurrent() - lastSLTime < CooldownMinutes * 60)
      return;

   // ATR取得 & ボラティリティレジーム判定
   double currentATR = GetCurrentATR();
   if(currentATR <= 0) return;

   int volRegime = GetVolatilityRegime(currentATR);
   if(volRegime == 0) return;  // 低ボラ → スキップ

   // 動的リスクスケーリング
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

   // ──── スコアリング（v3.0: 最大22点） ────
   int buyScore  = 0;
   int sellScore = 0;
   string buyReasons  = "";
   string sellReasons = "";
   int componentMask = 0;

   // 1. H4 トレンド（3点）
   int h4Trend = GetH4Trend();
   if(h4Trend == 1)       { buyScore += 3;  buyReasons  += "H4^ "; componentMask |= (1 << 0); }
   else if(h4Trend == -1) { sellScore += 3;  sellReasons += "H4v "; componentMask |= (1 << 0); }

   // 2. H1 MA方向（2点）
   int h1MACross = GetH1MACross();
   if(h1MACross == 1)       { buyScore += 2;  buyReasons  += "H1MA^ "; componentMask |= (1 << 1); }
   else if(h1MACross == -1) { sellScore += 2;  sellReasons += "H1MAv "; componentMask |= (1 << 1); }

   // 3. H1 RSI（1点）
   double h1Rsi = GetIndicatorValue(h_h1_rsi, 0, 1);
   if(h1Rsi > 40 && h1Rsi < 60)         { buyScore += 1;  sellScore += 1;  buyReasons += "RSIn "; sellReasons += "RSIn "; componentMask |= (1 << 2); }
   else if(h1Rsi >= 60 && h1Rsi < 65)   { buyScore += 1;  buyReasons  += "RSIb "; componentMask |= (1 << 2); }
   else if(h1Rsi > 35 && h1Rsi <= 40)   { sellScore += 1;  sellReasons += "RSIs "; componentMask |= (1 << 2); }

   // 4. H1 BB（1点）
   int bbSignal = GetBBSignal();
   if(bbSignal == 1)       { buyScore += 1;  buyReasons  += "BB^ "; componentMask |= (1 << 3); }
   else if(bbSignal == -1) { sellScore += 1;  sellReasons += "BBv "; componentMask |= (1 << 3); }

   // 5. M15 MAクロス（2点）
   int m15Cross = GetM15MACross();
   if(m15Cross == 1)       { buyScore += 2;  buyReasons  += "M15^ "; componentMask |= (1 << 4); }
   else if(m15Cross == -1) { sellScore += 2;  sellReasons += "M15v "; componentMask |= (1 << 4); }

   // 6. チャネル回帰（1点）
   int channelSignal = GetChannelSignal();
   if(channelSignal == 1)       { buyScore += 1;  buyReasons  += "CH^ "; componentMask |= (1 << 5); }
   else if(channelSignal == -1) { sellScore += 1;  sellReasons += "CHv "; componentMask |= (1 << 5); }

   // 7. モメンタム確認（1点）
   if(UseMomentum)
   {
      int momentum = GetMomentum();
      if(momentum == 1)       { buyScore += 1;  buyReasons  += "MOM^ "; componentMask |= (1 << 6); }
      else if(momentum == -1) { sellScore += 1;  sellReasons += "MOMv "; componentMask |= (1 << 6); }
   }

   // 8. セッションボーナス（1点）— Gold はロンドン/NY重複が有利
   if(UseSessionBonus)
   {
      int sessionBonus = GetSessionBonus();
      if(sessionBonus > 0)
      {
         buyScore += 1;  sellScore += 1;
         buyReasons += "SES "; sellReasons += "SES ";
         componentMask |= (1 << 7);
      }
   }

   // 9. USD相関フィルター（2点）
   if(g_UseCorrelation)
   {
      int corrSignal = GetCorrelationSignal();
      if(corrSignal == 1)       { buyScore += 2;  buyReasons  += "USD- "; componentMask |= (1 << 8); }
      else if(corrSignal == -1) { sellScore += 2;  sellReasons += "USD+ "; componentMask |= (1 << 8); }
   }

   // 10. RSIダイバージェンス（2点）
   if(UseDivergence)
   {
      int divSignal = GetDivergence();
      if(divSignal == 1)       { buyScore += 2;  buyReasons  += "DIV^ "; componentMask |= (1 << 9); }
      else if(divSignal == -1) { sellScore += 2;  sellReasons += "DIVv "; componentMask |= (1 << 9); }
   }

   // 11. S/Rレベル（+1/-1点）
   if(UseSRLevels)
   {
      int srSignal = GetSRSignal(iClose(_Symbol, PERIOD_H1, 1), currentATR);
      if(srSignal == 1)       { buyScore += 1;  sellScore -= 1; buyReasons  += "SR^ "; componentMask |= (1 << 10); }
      else if(srSignal == -1) { sellScore += 1;  buyScore -= 1; sellReasons += "SRv "; componentMask |= (1 << 10); }
   }

   // 12. ローソク足パターン（1点）
   if(UseCandlePatterns)
   {
      int candleSignal = GetCandlePattern();
      if(candleSignal == 1)       { buyScore += 1;  buyReasons  += "CDL^ "; componentMask |= (1 << 11); }
      else if(candleSignal == -1) { sellScore += 1;  sellReasons += "CDLv "; componentMask |= (1 << 11); }
   }

   // 13. H4 RSIアライメント（1点）
   if(UseH4RSI)
   {
      int h4RsiSignal = GetH4RSIAlignment();
      if(h4RsiSignal == 1)       { buyScore += 1;  buyReasons  += "H4R^ "; }
      else if(h4RsiSignal == -1) { sellScore += 1;  sellReasons += "H4Rv "; }
   }

   // Clamp scores to minimum 0
   buyScore = (int)MathMax(0, buyScore);
   sellScore = (int)MathMax(0, sellScore);

   // ──── エントリー ────
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // 動的SL/TP計算（ATRベース）
   double slMulti = SL_ATR_Multi;
   if(volRegime == 2) slMulti += HighVol_SL_Bonus;

   double slDist = currentATR * slMulti;
   double tpDist = currentATR * TP_ATR_Multi;

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

   // 動的スコア防壁（v3.0: 22点スケール）
   int currentMinScore = MinEntryScore;  // default 9
   if(currentDD >= 20.0)      currentMinScore = 15;
   else if(currentDD >= 15.0) currentMinScore = 13;
   else if(currentDD >= 10.0) currentMinScore = 11;

   if(buyScore >= currentMinScore && buyScore > sellScore)
   {
      double sl = NormalizeDouble(ask - slDist, _Digits);
      double tp = NormalizeDouble(ask + tpDist, _Digits);

      if(trade.Buy(lot, _Symbol, ask, sl, tp,
         StringFormat("GOLD BUY S:%d M:%d ATR:%.1f", buyScore, componentMask, currentATR/_Point)))
         Print("GOLD BUY Score:", buyScore, "/22 ATR:", DoubleToString(currentATR/_Point,0),
               "pt SL:", DoubleToString(slDist/_Point,0), " TP:", DoubleToString(tpDist/_Point,0),
               " [", buyReasons, "]");
   }

   if(sellScore >= currentMinScore && sellScore > buyScore)
   {
      double sl = NormalizeDouble(bid + slDist, _Digits);
      double tp = NormalizeDouble(bid - tpDist, _Digits);

      if(trade.Sell(lot, _Symbol, bid, sl, tp,
         StringFormat("GOLD SELL S:%d M:%d ATR:%.1f", sellScore, componentMask, currentATR/_Point)))
         Print("GOLD SELL Score:", sellScore, "/22 ATR:", DoubleToString(currentATR/_Point,0),
               "pt SL:", DoubleToString(slDist/_Point,0), " TP:", DoubleToString(tpDist/_Point,0),
               " [", sellReasons, "]");
   }
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

   // ロンドン/NY重複 (13:00-17:00 サーバー時間 ≒ GMT+2)
   if(dt.hour >= 13 && dt.hour < 17) return 1;

   // ロンドンセッション初動 (8:00-11:00)
   if(dt.hour >= 8 && dt.hour < 11) return 1;

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

   for(int i = lookback - 1; i >= 0; i--)
   {
      double x = lookback - 1 - i;
      double y = iClose(_Symbol, PERIOD_H1, i);
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
      double actual    = iClose(_Symbol, PERIOD_H1, i);
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
   double levels[];
   int levelCount = 0;
   ArrayResize(levels, 0);

   for(int i = SR_SwingStrength; i < SR_Lookback - SR_SwingStrength; i++)
   {
      double high_i = iHigh(_Symbol, PERIOD_H1, i);
      double low_i  = iLow(_Symbol, PERIOD_H1, i);

      // スイングハイ判定
      bool isSwingHigh = true;
      for(int j = 1; j <= SR_SwingStrength; j++)
      {
         if(high_i < iHigh(_Symbol, PERIOD_H1, i - j) || high_i < iHigh(_Symbol, PERIOD_H1, i + j))
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
         if(low_i > iLow(_Symbol, PERIOD_H1, i - j) || low_i > iLow(_Symbol, PERIOD_H1, i + j))
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

   // H4 RSI 50-75 + H1 RSI < 70 → bullish
   if(h4RsiVal >= 50 && h4RsiVal <= 75 && h1RsiVal < 70)
      return 1;

   // H4 RSI 25-50 + H1 RSI > 30 → bearish
   if(h4RsiVal >= 25 && h4RsiVal <= 50 && h1RsiVal > 30)
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
//| v3.0: ウェイト保存（GlobalVariable使用）                           |
//+------------------------------------------------------------------+
void SaveWeights()
{
   string prefix = "AGMTF_";
   for(int i = 0; i < 12; i++)
   {
      GlobalVariableSet(prefix + "W_" + IntegerToString(i), compWeights[i]);
      GlobalVariableSet(prefix + "CW_" + IntegerToString(i), (double)compWins[i]);
      GlobalVariableSet(prefix + "CT_" + IntegerToString(i), (double)compTotal[i]);
   }
   GlobalVariableSet(prefix + "TotalTracks", (double)totalTradesTracked);
   GlobalVariableSet(prefix + "ResultCount", (double)tradeResultCount);
   GlobalVariableSet(prefix + "ResultIndex", (double)tradeResultIndex);

   for(int i = 0; i < 50; i++)
      GlobalVariableSet(prefix + "TR_" + IntegerToString(i), recentTradeResults[i]);
}

//+------------------------------------------------------------------+
//| v3.0: ウェイト読込（GlobalVariable使用）                           |
//+------------------------------------------------------------------+
void LoadWeights()
{
   string prefix = "AGMTF_";
   if(!GlobalVariableCheck(prefix + "TotalTracks")) return;

   for(int i = 0; i < 12; i++)
   {
      if(GlobalVariableCheck(prefix + "W_" + IntegerToString(i)))
         compWeights[i] = GlobalVariableGet(prefix + "W_" + IntegerToString(i));
      if(GlobalVariableCheck(prefix + "CW_" + IntegerToString(i)))
         compWins[i] = (int)GlobalVariableGet(prefix + "CW_" + IntegerToString(i));
      if(GlobalVariableCheck(prefix + "CT_" + IntegerToString(i)))
         compTotal[i] = (int)GlobalVariableGet(prefix + "CT_" + IntegerToString(i));
   }
   totalTradesTracked = (int)GlobalVariableGet(prefix + "TotalTracks");
   tradeResultCount   = (int)GlobalVariableGet(prefix + "ResultCount");
   tradeResultIndex   = (int)GlobalVariableGet(prefix + "ResultIndex");

   for(int i = 0; i < 50; i++)
   {
      if(GlobalVariableCheck(prefix + "TR_" + IntegerToString(i)))
         recentTradeResults[i] = GlobalVariableGet(prefix + "TR_" + IntegerToString(i));
   }
}

//+------------------------------------------------------------------+
//| v3.0: コンポーネントウェイト再計算                                 |
//+------------------------------------------------------------------+
void RecalcWeights()
{
   for(int i = 0; i < 12; i++)
   {
      if(compTotal[i] >= 5)
      {
         double winRate = (double)compWins[i] / (double)compTotal[i];
         compWeights[i] = 0.5 + winRate;  // 0.5〜1.5の範囲
      }
      else
      {
         compWeights[i] = 1.0;
      }
   }
}

//+------------------------------------------------------------------+
//| ロット計算（ゴールド用・OrderCalcProfit使用）                       |
//+------------------------------------------------------------------+
double CalcLotSize(double entryPrice, double slDist)
{
   if(slDist <= 0) return MinLots;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

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

      double beDist    = (curATR > 0) ? curATR * BE_ATR_Multi : MathAbs(tp - openPrice) * 0.4;
      double trailStep = (curATR > 0) ? curATR * Trail_ATR_Multi : MathAbs(tp - openPrice) * 0.3;

      if(posType == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profitDist = bid - openPrice;

         // 半利確
         if(UsePartialClose && !IsPartialClosed(ticket) && tp > openPrice)
         {
            double tpDist = tp - openPrice;
            if(profitDist >= tpDist * PartialTP_Ratio)
            {
               double closeLot = NormalizeDouble(volume * PartialCloseRatio, 2);
               if(closeLot >= MinLots)
               {
                  if(trade.PositionClosePartial(ticket, closeLot))
                  {
                     MarkPartialClosed(ticket);
                     double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);
                     trade.PositionModify(ticket, newSL, tp);
                     Print("GOLD 半利確 BUY: ", DoubleToString(closeLot, 2), "lot決済");
                  }
               }
            }
         }

         // 建値移動
         if(profitDist >= beDist && sl < openPrice)
         {
            double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);
            trade.PositionModify(ticket, newSL, tp);
         }
         // トレーリング
         else if(profitDist >= beDist * 1.5)
         {
            double newSL = NormalizeDouble(bid - trailStep, _Digits);
            if(newSL > sl + 5 * _Point)
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
            if(chandelierSL > sl + 5 * _Point)
               trade.PositionModify(ticket, chandelierSL, tp);
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
            if(profitDist >= tpDist * PartialTP_Ratio)
            {
               double closeLot = NormalizeDouble(volume * PartialCloseRatio, 2);
               if(closeLot >= MinLots)
               {
                  if(trade.PositionClosePartial(ticket, closeLot))
                  {
                     MarkPartialClosed(ticket);
                     double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);
                     trade.PositionModify(ticket, newSL, tp);
                     Print("GOLD 半利確 SELL: ", DoubleToString(closeLot, 2), "lot決済");
                  }
               }
            }
         }

         // 建値移動
         if(profitDist >= beDist && (sl > openPrice || sl == 0))
         {
            double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);
            trade.PositionModify(ticket, newSL, tp);
         }
         // トレーリング
         else if(profitDist >= beDist * 1.5)
         {
            double newSL = NormalizeDouble(ask + trailStep, _Digits);
            if(newSL < sl - 5 * _Point || sl == 0)
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
            if(chandelierSL < sl - 5 * _Point)
               trade.PositionModify(ticket, chandelierSL, tp);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| 半利確トラッキング                                                  |
//+------------------------------------------------------------------+
bool IsPartialClosed(ulong ticket)
{
   for(int i = 0; i < ArraySize(partialClosedTickets); i++)
      if(partialClosedTickets[i] == ticket) return true;
   return false;
}

void MarkPartialClosed(ulong ticket)
{
   int sz = ArraySize(partialClosedTickets);
   ArrayResize(partialClosedTickets, sz + 1);
   partialClosedTickets[sz] = ticket;

   if(sz > 100)
   {
      for(int i = 0; i < 50; i++)
         partialClosedTickets[i] = partialClosedTickets[i + 50];
      ArrayResize(partialClosedTickets, sz - 49);
   }
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
      if(HistoryDealSelect(trans.deal))
      {
         long dealMagic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
         long dealEntry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
         long dealReason = HistoryDealGetInteger(trans.deal, DEAL_REASON);

         if(dealMagic == MagicNumber && dealEntry == DEAL_ENTRY_OUT)
         {
            // SLクールダウン
            if(dealReason == DEAL_REASON_SL)
            {
               lastSLTime = TimeCurrent();
               Print("SLクールダウン開始: ", CooldownMinutes, "分間エントリー停止");
            }

            // v3.0: トレード結果を循環バッファに記録
            double dealProfit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
            double dealComm   = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
            double dealSwap   = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
            double netResult  = dealProfit + dealComm + dealSwap;

            recentTradeResults[tradeResultIndex] = netResult;
            tradeResultIndex = (tradeResultIndex + 1) % 50;
            if(tradeResultCount < 50) tradeResultCount++;

            totalTradesTracked++;

            // コンポーネントマスクをコメントから解析
            string dealComment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
            int maskPos = StringFind(dealComment, "M:");
            if(maskPos >= 0)
            {
               string maskStr = StringSubstr(dealComment, maskPos + 2);
               // maskStrから数値部分を抽出（スペースまたは次の非数字まで）
               int spacePos = StringFind(maskStr, " ");
               if(spacePos > 0) maskStr = StringSubstr(maskStr, 0, spacePos);
               int mask = (int)StringToInteger(maskStr);

               bool isWin = (netResult > 0);
               for(int bit = 0; bit < 12; bit++)
               {
                  if((mask & (1 << bit)) != 0)
                  {
                     compTotal[bit]++;
                     if(isWin) compWins[bit]++;
                  }
               }
            }

            // 20トレード以降、5トレードごとにウェイト再計算
            if(totalTradesTracked >= 20 && totalTradesTracked % 5 == 0)
            {
               RecalcWeights();
               Print("v3.0: コンポーネントウェイト再計算完了 (トレード#", totalTradesTracked, ")");
            }
         }
      }
   }
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
   if(dt.hour < TradeStartHour || dt.hour >= TradeEndHour) return false;
   if(AvoidFriday && dt.day_of_week == 5 && dt.hour >= 18) return false;
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
