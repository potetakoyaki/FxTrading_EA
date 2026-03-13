//+------------------------------------------------------------------+
//|                              AntigravityMTF_EA_Gold.mq5          |
//|            ゴールド(XAUUSD)専用 マルチタイムフレーム EA             |
//|            10万円口座 デュアル運用版（USDJPY EAと併用）              |
//+------------------------------------------------------------------+
#property copyright "Antigravity Trading System"
#property version   "1.10"
#property description "XAUUSD専用: MTF複合分析EA (USDJPY版と併用可)"

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| 入力パラメータ                                                      |
//+------------------------------------------------------------------+
input group "=== リスク管理 ==="
input double RiskPercent       = 0.2;     // リスク% ★デュアル運用0.2%
input double MaxLots           = 0.50;    // 最大ロット
input double MinLots           = 0.01;    // 最小ロット
input int    MaxSpread         = 50;      // 最大スプレッド(ポイント) ★ゴールド用
input int    MaxPositions      = 1;       // 最大同時ポジション数
input int    MagicNumber       = 20260224;// マジックナンバー ★USDJPY版と異なる
input double MaxDrawdownPct    = 6.0;     // DD 6%以上でリスク1/4
input double DDHalfRiskPct     = 2.5;     // DD 2.5%以上でリスク1/2

input group "=== 損益設定（ゴールド用: ポイント単位） ==="
input int    StopLossPoints    = 500;     // SL (ポイント) ≒ $5.00 ★拡大
input int    TakeProfitPoints  = 1250;    // TP (ポイント) ≒ $12.50 RR1:2.5
input int    TrailingStartPts  = 350;     // トレーリング開始 ≒ $3.50
input int    TrailingStepPts   = 150;     // トレーリングステップ ≒ $1.50
input int    BreakevenPts      = 250;     // 建値移動 ≒ $2.50

input group "=== トレンドフィルター（H4足） ==="
input int    H4_MA_Fast        = 20;      // H4 SMA短期
input int    H4_MA_Slow        = 50;      // H4 SMA長期
input int    H4_ADX_Period     = 14;      // H4 ADX期間
input int    H4_ADX_Threshold  = 20;      // H4 ADX閾値

input group "=== メイン足（H1） ==="
input int    H1_MA_Fast        = 10;      // H1 EMA短期
input int    H1_MA_Slow        = 30;      // H1 EMA長期
input int    H1_RSI_Period     = 14;      // H1 RSI期間
input int    H1_BB_Period      = 20;      // H1 ボリンジャー期間
input double H1_BB_Deviation   = 2.0;     // H1 ボリンジャー偏差

input group "=== エントリー足（M15） ==="
input int    M15_MA_Fast       = 5;       // M15 EMA短期
input int    M15_MA_Slow       = 20;      // M15 EMA長期

input group "=== スコアリング ==="
input int    MinEntryScore     = 6;       // エントリー最低スコア 6/10 ★引き上げ

input group "=== 時間フィルター ==="
input int    TradeStartHour    = 8;       // 取引開始時間(サーバー時間)
input int    TradeEndHour      = 22;      // 取引終了時間
input bool   AvoidFriday       = true;    // 金曜夜のエントリー回避
input int    CooldownMinutes   = 240;    // SL後のエントリー禁止時間(分) ★追加

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;
double         peakBalance;
int            h_h4_ma_fast, h_h4_ma_slow, h_h4_adx;
int            h_h1_ma_fast, h_h1_ma_slow, h_h1_rsi, h_h1_bb;
int            h_m15_ma_fast, h_m15_ma_slow;
datetime       lastBarTime;
datetime       lastSLTime;      // 直近SL時刻（クールダウン用）

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);  // ゴールドはスリッページ大きめ
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

   // ハンドル検証
   if(h_h4_ma_fast == INVALID_HANDLE || h_h4_ma_slow == INVALID_HANDLE ||
      h_h4_adx == INVALID_HANDLE || h_h1_ma_fast == INVALID_HANDLE ||
      h_h1_ma_slow == INVALID_HANDLE || h_h1_rsi == INVALID_HANDLE ||
      h_h1_bb == INVALID_HANDLE || h_m15_ma_fast == INVALID_HANDLE ||
      h_m15_ma_slow == INVALID_HANDLE)
   {
      Print("❌ インジケーターハンドルの作成に失敗");
      return INIT_FAILED;
   }

   Print("✅ AntigravityMTF EA [GOLD] 初期化完了");
   Print("   リスク: ", RiskPercent, "% / SL: ", StopLossPoints, "pt / TP: ", TakeProfitPoints, "pt");
   Print("   マジックナンバー: ", MagicNumber);
   Print("   1ポイント = ", _Point, " / 桁数 = ", _Digits);
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

   // ★ SL後クールダウン: 連続負けを防止
   if(lastSLTime > 0 && TimeCurrent() - lastSLTime < CooldownMinutes * 60)
      return;

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
   if(h4Trend == 1)       { buyScore += 3;  buyReasons  += "H4↑ "; }
   else if(h4Trend == -1) { sellScore += 3;  sellReasons += "H4↓ "; }

   // 2. H1 MA方向（2点）
   int h1MACross = GetH1MACross();
   if(h1MACross == 1)       { buyScore += 2;  buyReasons  += "H1MA↑ "; }
   else if(h1MACross == -1) { sellScore += 2;  sellReasons += "H1MA↓ "; }

   // 3. H1 RSI（1点）— 買いと売りで排他的な範囲
   double h1Rsi = GetIndicatorValue(h_h1_rsi, 0, 1);
   if(h1Rsi > 40 && h1Rsi < 60)         { buyScore += 1;  sellScore += 1;  buyReasons += "RSI中立 ";  sellReasons += "RSI中立 "; }
   else if(h1Rsi >= 60 && h1Rsi < 65)   { buyScore += 1;  buyReasons  += "RSI買適正 "; }
   else if(h1Rsi > 35 && h1Rsi <= 40)   { sellScore += 1;  sellReasons += "RSI売適正 "; }

   // 4. H1 BB（1点）
   int bbSignal = GetBBSignal();
   if(bbSignal == 1)       { buyScore += 1;  buyReasons  += "BB↑ "; }
   else if(bbSignal == -1) { sellScore += 1;  sellReasons += "BB↓ "; }

   // 5. M15 MAクロス（2点）
   int m15Cross = GetM15MACross();
   if(m15Cross == 1)       { buyScore += 2;  buyReasons  += "M15↑ "; }
   else if(m15Cross == -1) { sellScore += 2;  sellReasons += "M15↓ "; }

   // 6. チャネル回帰（1点）
   int channelSignal = GetChannelSignal();
   if(channelSignal == 1)       { buyScore += 1;  buyReasons  += "CH↑ "; }
   else if(channelSignal == -1) { sellScore += 1;  sellReasons += "CH↓ "; }

   // ──── エントリー ────
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lot = CalcLotSize();

   // ★ 動的スコア防壁（DDが深い時はパーフェクトなセットアップしか狙わない）
   int currentMinScore = MinEntryScore;
   if(currentDD >= 20.0)      currentMinScore = 9;  // 鉄壁モード
   else if(currentDD >= 15.0) currentMinScore = 8;  // 超厳格モード
   else if(currentDD >= 10.0) currentMinScore = 7;  // 厳格モード

   // ★ ゴールドは _Point をそのまま使う（ポイント単位）
   if(buyScore >= currentMinScore && buyScore > sellScore)
   {
      double sl = ask - StopLossPoints * _Point;
      double tp = ask + TakeProfitPoints * _Point;

      sl = NormalizeDouble(sl, _Digits);
      tp = NormalizeDouble(tp, _Digits);

      if(trade.Buy(lot, _Symbol, ask, sl, tp, StringFormat("GOLD BUY Score:%d [%s]", buyScore, buyReasons)))
         Print("🟢 GOLD BUY — Score: ", buyScore, "/11 — ", buyReasons);
   }

   if(sellScore >= currentMinScore && sellScore > buyScore)
   {
      double sl = bid + StopLossPoints * _Point;
      double tp = bid - TakeProfitPoints * _Point;

      sl = NormalizeDouble(sl, _Digits);
      tp = NormalizeDouble(tp, _Digits);

      if(trade.Sell(lot, _Symbol, bid, sl, tp, StringFormat("GOLD SELL Score:%d [%s]", sellScore, sellReasons)))
         Print("🔴 GOLD SELL — Score: ", sellScore, "/11 — ", sellReasons);
   }
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
   double close = iClose(_Symbol, PERIOD_H1, 1);  // 確定足を使用

   if(upperChannel == lowerChannel) return 0;
   double channelPos = (close - lowerChannel) / (upperChannel - lowerChannel);

   if(channelPos < 0.2 && slope > 0) return 1;
   if(channelPos > 0.8 && slope < 0) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| ロット計算（ゴールド用）                                            |
//+------------------------------------------------------------------+
double CalcLotSize()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

   double riskPct = RiskPercent;
   if(currentDD >= MaxDrawdownPct)
      riskPct *= 0.25;
   else if(currentDD >= DDHalfRiskPct)
      riskPct *= 0.5;

   double riskAmount = balance * riskPct / 100.0;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double slPrice = ask - StopLossPoints * _Point;
   double profitOrLoss = 0.0;
   
   // ★ MT5内蔵の利益計算関数に「1ロットでSLにかかった場合の口座通貨ベースの損失額」を直接聞く
   // スタンダード口座（100oz）、マイクロ口座（1oz）など、証券会社の仕様を完全に自動で判別します。
   if(!OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, ask, slPrice, profitOrLoss))
   {
      // 計算失敗時のフェールセーフ
      double usdJpyRate = SymbolInfoDouble("USDJPY", SYMBOL_BID);
      if(usdJpyRate <= 0) usdJpyRate = 150.0;
      profitOrLoss = -((StopLossPoints / 100.0) * 100.0 * usdJpyRate);
   }
   
   double lossForOneLot = MathAbs(profitOrLoss);
   if(lossForOneLot <= 0) lossForOneLot = 1000.0; // ゼロ割防止
   
   double lots = riskAmount / lossForOneLot;

   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;
   
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(MinLots, MathMin(MaxLots, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| ポジション管理（ゴールド用: ポイント単位）                           |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl        = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);
      long   posType   = PositionGetInteger(POSITION_TYPE);

      // ★ ゴールドは _Point をそのまま使用
      double pointVal = _Point;

      if(posType == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profitPts = (bid - openPrice) / pointVal;

         if(profitPts >= BreakevenPts && sl < openPrice)
         {
            double newSL = NormalizeDouble(openPrice + 10 * pointVal, _Digits);
            trade.PositionModify(ticket, newSL, tp);
         }
         else if(profitPts >= TrailingStartPts)
         {
            double newSL = NormalizeDouble(bid - TrailingStepPts * pointVal, _Digits);
            if(newSL > sl + 5 * pointVal)
               trade.PositionModify(ticket, newSL, tp);
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profitPts = (openPrice - ask) / pointVal;

         if(profitPts >= BreakevenPts && (sl > openPrice || sl == 0))
         {
            double newSL = NormalizeDouble(openPrice - 10 * pointVal, _Digits);
            trade.PositionModify(ticket, newSL, tp);
         }
         else if(profitPts >= TrailingStartPts)
         {
            double newSL = NormalizeDouble(ask + TrailingStepPts * pointVal, _Digits);
            if(newSL < sl - 5 * pointVal || sl == 0)
               trade.PositionModify(ticket, newSL, tp);
         }
      }
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
            Print("⏸️ SLクールダウン開始: ", CooldownMinutes, "分間エントリー停止");
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
