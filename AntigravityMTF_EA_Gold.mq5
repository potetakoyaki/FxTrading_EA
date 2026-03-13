//+------------------------------------------------------------------+
//|                              AntigravityMTF_EA_Gold.mq5          |
//|            ゴールド(XAUUSD)専用 マルチタイムフレーム EA             |
//|            v2.0: ATR動的SL/TP + ボラレジーム + セッション最適化     |
//+------------------------------------------------------------------+
#property copyright "Antigravity Trading System"
#property version   "2.00"
#property description "XAUUSD専用 v2.0: 動的SL/TP + ボラレジーム + セッション + 半利確"

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
input int    MinEntryScore     = 6;       // 最低スコア 6/12
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

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;
double         peakBalance;
int            h_h4_ma_fast, h_h4_ma_slow, h_h4_adx;
int            h_h1_ma_fast, h_h1_ma_slow, h_h1_rsi, h_h1_bb;
int            h_m15_ma_fast, h_m15_ma_slow;
int            h_m15_atr;                 // ★ M15 ATR（動的SL/TP用）
datetime       lastBarTime;
datetime       lastSLTime;
ulong          partialClosedTickets[];

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

   // ★ M15 ATR（動的SL/TP計算用）
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

   Print("AntigravityMTF EA [GOLD] v2.0 初期化完了");
   Print("   動的SL/TP: SL=ATR×", SL_ATR_Multi, " TP=ATR×", TP_ATR_Multi);
   Print("   ボラレジーム: Low<", VolRegime_Low, " High>", VolRegime_High);
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

   // ★ ATR取得 & ボラティリティレジーム判定
   double currentATR = GetCurrentATR();
   if(currentATR <= 0) return;

   int volRegime = GetVolatilityRegime(currentATR);
   if(volRegime == 0) return;  // 低ボラ → スキップ

   // 動的リスクスケーリング
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

   // ──── スコアリング ────
   int buyScore  = 0;
   int sellScore = 0;
   string buyReasons  = "";
   string sellReasons = "";

   // 1. H4 トレンド（3点）
   int h4Trend = GetH4Trend();
   if(h4Trend == 1)       { buyScore += 3;  buyReasons  += "H4^ "; }
   else if(h4Trend == -1) { sellScore += 3;  sellReasons += "H4v "; }

   // 2. H1 MA方向（2点）
   int h1MACross = GetH1MACross();
   if(h1MACross == 1)       { buyScore += 2;  buyReasons  += "H1MA^ "; }
   else if(h1MACross == -1) { sellScore += 2;  sellReasons += "H1MAv "; }

   // 3. H1 RSI（1点）
   double h1Rsi = GetIndicatorValue(h_h1_rsi, 0, 1);
   if(h1Rsi > 40 && h1Rsi < 60)         { buyScore += 1;  sellScore += 1;  buyReasons += "RSIn "; sellReasons += "RSIn "; }
   else if(h1Rsi >= 60 && h1Rsi < 65)   { buyScore += 1;  buyReasons  += "RSIb "; }
   else if(h1Rsi > 35 && h1Rsi <= 40)   { sellScore += 1;  sellReasons += "RSIs "; }

   // 4. H1 BB（1点）
   int bbSignal = GetBBSignal();
   if(bbSignal == 1)       { buyScore += 1;  buyReasons  += "BB^ "; }
   else if(bbSignal == -1) { sellScore += 1;  sellReasons += "BBv "; }

   // 5. M15 MAクロス（2点）
   int m15Cross = GetM15MACross();
   if(m15Cross == 1)       { buyScore += 2;  buyReasons  += "M15^ "; }
   else if(m15Cross == -1) { sellScore += 2;  sellReasons += "M15v "; }

   // 6. チャネル回帰（1点）
   int channelSignal = GetChannelSignal();
   if(channelSignal == 1)       { buyScore += 1;  buyReasons  += "CH^ "; }
   else if(channelSignal == -1) { sellScore += 1;  sellReasons += "CHv "; }

   // 7. ★ モメンタム確認（1点）
   if(UseMomentum)
   {
      int momentum = GetMomentum();
      if(momentum == 1)       { buyScore += 1;  buyReasons  += "MOM^ "; }
      else if(momentum == -1) { sellScore += 1;  sellReasons += "MOMv "; }
   }

   // 8. ★ セッションボーナス（1点）— Gold はロンドン/NY重複が有利
   if(UseSessionBonus)
   {
      int sessionBonus = GetSessionBonus();
      if(sessionBonus > 0)
      {
         buyScore += 1;  sellScore += 1;
         buyReasons += "SES "; sellReasons += "SES ";
      }
   }

   // ──── エントリー ────
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // ★ 動的SL/TP計算（ATRベース）
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

   // 動的スコア防壁
   int currentMinScore = MinEntryScore;
   if(currentDD >= 20.0)      currentMinScore = 10;
   else if(currentDD >= 15.0) currentMinScore = 9;
   else if(currentDD >= 10.0) currentMinScore = 8;

   if(buyScore >= currentMinScore && buyScore > sellScore)
   {
      double sl = NormalizeDouble(ask - slDist, _Digits);
      double tp = NormalizeDouble(ask + tpDist, _Digits);

      if(trade.Buy(lot, _Symbol, ask, sl, tp,
         StringFormat("GOLD BUY S:%d ATR:%.1f", buyScore, currentATR/_Point)))
         Print("GOLD BUY Score:", buyScore, "/12 ATR:", DoubleToString(currentATR/_Point,0),
               "pt SL:", DoubleToString(slDist/_Point,0), " TP:", DoubleToString(tpDist/_Point,0),
               " [", buyReasons, "]");
   }

   if(sellScore >= currentMinScore && sellScore > buyScore)
   {
      double sl = NormalizeDouble(bid + slDist, _Digits);
      double tp = NormalizeDouble(bid - tpDist, _Digits);

      if(trade.Sell(lot, _Symbol, bid, sl, tp,
         StringFormat("GOLD SELL S:%d ATR:%.1f", sellScore, currentATR/_Point)))
         Print("GOLD SELL Score:", sellScore, "/12 ATR:", DoubleToString(currentATR/_Point,0),
               "pt SL:", DoubleToString(slDist/_Point,0), " TP:", DoubleToString(tpDist/_Point,0),
               " [", sellReasons, "]");
   }
}

//+------------------------------------------------------------------+
//| ★ 現在のM15 ATR取得                                               |
//+------------------------------------------------------------------+
double GetCurrentATR()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(h_m15_atr, 0, 1, 1, atr) < 1) return 0;
   return atr[0];
}

//+------------------------------------------------------------------+
//| ★ ボラティリティレジーム判定                                        |
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
//| ★ モメンタム判定（M15の直近3本の方向）                              |
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
//| ★ セッションボーナス（Gold用: ロンドン/NY重複が最も有利）           |
//+------------------------------------------------------------------+
int GetSessionBonus()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   // ロンドン/NY重複 (13:00-17:00 サーバー時間 ≒ GMT+2)
   // = ゴールドが最も流動性が高い時間帯
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
//| ロット計算（ゴールド用・OrderCalcProfit使用）                       |
//+------------------------------------------------------------------+
double CalcLotSize(double entryPrice, double slDist)
{
   if(slDist <= 0) return MinLots;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

   double riskPct = RiskPercent;
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
//| ポジション管理（ATRベース + 半利確）                                 |
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
//| SL検知 — クールダウン用                                            |
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

         if(dealMagic == MagicNumber && dealEntry == DEAL_ENTRY_OUT && dealReason == DEAL_REASON_SL)
         {
            lastSLTime = TimeCurrent();
            Print("SLクールダウン開始: ", CooldownMinutes, "分間エントリー停止");
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
