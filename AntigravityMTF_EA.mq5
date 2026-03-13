//+------------------------------------------------------------------+
//|                                    AntigravityMTF_EA.mq5         |
//|            マルチタイムフレーム複合分析 Expert Advisor              |
//|            10万円口座用 — 高勝率・保守的リスク管理                   |
//+------------------------------------------------------------------+
#property copyright "Antigravity Trading System"
#property version   "1.00"
#property description "MTF複合分析EA: ADX/RSI/MA/BB/チャネル/フィボを統合"

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| 入力パラメータ                                                      |
//+------------------------------------------------------------------+
input group "=== リスク管理 ==="
input double RiskPercent       = 0.4;     // リスク% ★複利0.4%
input double MaxLots           = 0.50;    // 最大ロット
input double MinLots           = 0.01;    // 最小ロット
input int    MaxSpread         = 30;      // 最大スプレッド(ポイント)
input int    MaxPositions      = 1;       // 最大同時ポジション数
input int    MagicNumber       = 20260223;// マジックナンバー
input double MaxDrawdownPct    = 6.0;     // DD 6%以上でリスク1/4
input double DDHalfRiskPct     = 2.5;     // DD 2.5%以上でリスク1/2

input group "=== 損益設定 ==="
input int    StopLossPips      = 20;      // SL (pips)
input int    TakeProfitPips    = 50;      // TP (pips) RR1:2.5
input int    TrailingStartPips = 15;      // トレーリング開始
input int    TrailingStepPips  = 8;       // トレーリングステップ
input int    BreakevenPips     = 10;      // 建値移動

input group "=== トレンドフィルター（H4足） ==="
input int    H4_MA_Fast        = 20;      // H4 SMA短期
input int    H4_MA_Slow        = 50;      // H4 SMA長期
input int    H4_ADX_Period     = 14;      // H4 ADX期間
input int    H4_ADX_Threshold  = 20;      // H4 ADX閾値

input group "=== メイン足（H1） ==="
input int    H1_MA_Fast        = 10;      // H1 EMA短期
input int    H1_MA_Slow        = 30;      // H1 EMA長期
input int    H1_RSI_Period     = 14;      // H1 RSI期間
input int    H1_RSI_OB         = 70;      // H1 RSI 買われすぎ
input int    H1_RSI_OS         = 30;      // H1 RSI 売られすぎ
input int    H1_BB_Period      = 20;      // H1 ボリンジャー期間
input double H1_BB_Deviation   = 2.0;     // H1 ボリンジャー偏差

input group "=== エントリー足（M15） ==="
input int    M15_MA_Fast       = 5;       // M15 EMA短期
input int    M15_MA_Slow       = 20;      // M15 EMA長期
input int    M15_RSI_Period    = 10;      // M15 RSI期間

input group "=== スコアリング ==="
input int    MinEntryScore     = 5;       // エントリー最低スコア 5/11

input group "=== 時間フィルター ==="
input int    TradeStartHour    = 8;       // 取引開始時間(サーバー時間)
input int    TradeEndHour      = 22;      // 取引終了時間
input bool   AvoidFriday       = true;    // 金曜夜のエントリー回避

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;
double         peakBalance;
int            h_h4_ma_fast, h_h4_ma_slow, h_h4_adx;
int            h_h1_ma_fast, h_h1_ma_slow, h_h1_rsi, h_h1_bb;
int            h_m15_ma_fast, h_m15_ma_slow, h_m15_rsi;
datetime       lastBarTime;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(10);
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
   h_m15_rsi     = iRSI(_Symbol, PERIOD_M15, M15_RSI_Period, PRICE_CLOSE);

   // ハンドル検証
   if(h_h4_ma_fast == INVALID_HANDLE || h_h4_ma_slow == INVALID_HANDLE ||
      h_h4_adx == INVALID_HANDLE || h_h1_ma_fast == INVALID_HANDLE ||
      h_h1_ma_slow == INVALID_HANDLE || h_h1_rsi == INVALID_HANDLE ||
      h_h1_bb == INVALID_HANDLE || h_m15_ma_fast == INVALID_HANDLE ||
      h_m15_ma_slow == INVALID_HANDLE || h_m15_rsi == INVALID_HANDLE)
   {
      Print("❌ インジケーターハンドルの作成に失敗");
      return INIT_FAILED;
   }

   Print("✅ AntigravityMTF EA 初期化完了 — 10万円モード");
   Print("   リスク: ", RiskPercent, "% / SL: ", StopLossPips, "pips / TP: ", TakeProfitPips, "pips");
   Print("   最低スコア: ", MinEntryScore, "/10");
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
   IndicatorRelease(h_m15_rsi);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // トレーリングストップ & 建値管理（毎ティック）
   ManageOpenPositions();

   // 新しいM15バーでのみ判定
   datetime currentBar = iTime(_Symbol, PERIOD_M15, 0);
   if(currentBar == lastBarTime) return;
   lastBarTime = currentBar;

   // フィルターチェック
   if(!IsTradeAllowed()) return;
   if(!CheckTimeFilter()) return;
   if(!CheckSpread()) return;
   if(CountMyPositions() >= MaxPositions) return;

   // ★ 動的リスクスケーリング（DDが深いほどリスクを下げるが完全には止めない）
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

   // ──── スコアリング ────
   int buyScore  = 0;
   int sellScore = 0;
   string buyReasons  = "";
   string sellReasons = "";

   // 1. H4 トレンド方向（最重要: 3点）
   int h4Trend = GetH4Trend();
   if(h4Trend == 1)       { buyScore += 3;  buyReasons  += "H4↑ "; }
   else if(h4Trend == -1) { sellScore += 3;  sellReasons += "H4↓ "; }

   // 2. H1 MA クロス方向（2点）
   int h1MACross = GetH1MACross();
   if(h1MACross == 1)       { buyScore += 2;  buyReasons  += "H1MA↑ "; }
   else if(h1MACross == -1) { sellScore += 2;  sellReasons += "H1MA↓ "; }

   // 3. H1 RSI フィルター（1点）— 買いと売りで排他的な範囲
   double h1Rsi = GetIndicatorValue(h_h1_rsi, 0, 1);
   if(h1Rsi > 40 && h1Rsi < 60)         { buyScore += 1;  sellScore += 1;  buyReasons += "RSI中立 ";  sellReasons += "RSI中立 "; }
   else if(h1Rsi >= 60 && h1Rsi < 65)   { buyScore += 1;  buyReasons  += "RSI買適正 "; }
   else if(h1Rsi > 35 && h1Rsi <= 40)   { sellScore += 1;  sellReasons += "RSI売適正 "; }

   // 4. H1 ボリンジャーバンド位置（1点）
   int bbSignal = GetBBSignal();
   if(bbSignal == 1)       { buyScore += 1;  buyReasons  += "BB下限↑ "; }
   else if(bbSignal == -1) { sellScore += 1;  sellReasons += "BB上限↓ "; }

   // 5. M15 MAクロス（エントリータイミング: 2点）
   int m15Cross = GetM15MACross();
   if(m15Cross == 1)       { buyScore += 2;  buyReasons  += "M15↑ "; }
   else if(m15Cross == -1) { sellScore += 2;  sellReasons += "M15↓ "; }

   // 6. チャネル/回帰分析（1点）
   int channelSignal = GetChannelSignal();
   if(channelSignal == 1)       { buyScore += 1;  buyReasons  += "CH↑ "; }
   else if(channelSignal == -1) { sellScore += 1;  sellReasons += "CH↓ "; }

   // ──── エントリー判定 ────
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lot = CalcLotSize();
   
   // ★ 動的スコア防壁（DDが深い時はパーフェクトなセットアップしか狙わない）
   int currentMinScore = MinEntryScore;
   if(currentDD >= 20.0)      currentMinScore = 9;  // 鉄壁モード
   else if(currentDD >= 15.0) currentMinScore = 8;  // 超厳格モード
   else if(currentDD >= 10.0) currentMinScore = 7;  // 厳格モード

   // 買いエントリー
   if(buyScore >= currentMinScore && buyScore > sellScore)
   {
      double sl = ask - StopLossPips * _Point * 10;
      double tp = ask + TakeProfitPips * _Point * 10;

      if(trade.Buy(lot, _Symbol, ask, sl, tp, StringFormat("BUY Score:%d [%s]", buyScore, buyReasons)))
         Print("🟢 BUY — Score: ", buyScore, "/11 — ", buyReasons);
   }

   // 売りエントリー
   if(sellScore >= currentMinScore && sellScore > buyScore)
   {
      double sl = bid + StopLossPips * _Point * 10;
      double tp = bid - TakeProfitPips * _Point * 10;

      if(trade.Sell(lot, _Symbol, bid, sl, tp, StringFormat("SELL Score:%d [%s]", sellScore, sellReasons)))
         Print("🔴 SELL — Score: ", sellScore, "/11 — ", sellReasons);
   }
}

//+------------------------------------------------------------------+
//| H4 トレンド判定（SMA + ADX）                                       |
//+------------------------------------------------------------------+
int GetH4Trend()
{
   double maFast = GetIndicatorValue(h_h4_ma_fast, 0, 1);
   double maSlow = GetIndicatorValue(h_h4_ma_slow, 0, 1);
   double adx    = GetIndicatorValue(h_h4_adx, 0, 1);       // ADX
   double plusDI = GetIndicatorValue(h_h4_adx, 1, 1);       // +DI
   double minusDI= GetIndicatorValue(h_h4_adx, 2, 1);       // -DI

   if(maFast == 0 || maSlow == 0) return 0;

   // ADXがトレンド閾値以上で、MA方向が一致
   if(adx >= H4_ADX_Threshold)
   {
      if(maFast > maSlow && plusDI > minusDI) return 1;   // 上昇トレンド
      if(maFast < maSlow && minusDI > plusDI) return -1;  // 下降トレンド
   }

   return 0; // レンジまたは不明瞭
}

//+------------------------------------------------------------------+
//| H1 MAクロス判定                                                    |
//+------------------------------------------------------------------+
int GetH1MACross()
{
   double fastCurr = GetIndicatorValue(h_h1_ma_fast, 0, 1);
   double slowCurr = GetIndicatorValue(h_h1_ma_slow, 0, 1);
   double fastPrev = GetIndicatorValue(h_h1_ma_fast, 0, 2);
   double slowPrev = GetIndicatorValue(h_h1_ma_slow, 0, 2);

   if(fastCurr == 0 || slowCurr == 0) return 0;

   // MA方向判定（Gold版と同一ロジック）
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
   double bbMid   = GetIndicatorValue(h_h1_bb, 0, 1);

   if(bbUpper == 0 || bbLower == 0) return 0;

   double close = iClose(_Symbol, PERIOD_H1, 1);
   double prevClose = iClose(_Symbol, PERIOD_H1, 2);
   double bbWidth = bbUpper - bbLower;
   if(bbWidth <= 0) return 0;

   double position = (close - bbLower) / bbWidth;

   // 下限バウンス → 買い
   if(position < 0.2 && close > prevClose) return 1;
   // 上限バウンス → 売り
   if(position > 0.8 && close < prevClose) return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| M15 MAクロス（エントリータイミング）                                  |
//+------------------------------------------------------------------+
int GetM15MACross()
{
   double fastCurr = GetIndicatorValue(h_m15_ma_fast, 0, 1);
   double slowCurr = GetIndicatorValue(h_m15_ma_slow, 0, 1);
   double fastPrev = GetIndicatorValue(h_m15_ma_fast, 0, 2);
   double slowPrev = GetIndicatorValue(h_m15_ma_slow, 0, 2);

   if(fastCurr == 0 || slowCurr == 0) return 0;

   // クロス直後のみ（タイミング精度）
   if(fastCurr > slowCurr && fastPrev <= slowPrev) return 1;
   if(fastCurr < slowCurr && fastPrev >= slowPrev) return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| チャネル（回帰分析）シグナル                                        |
//+------------------------------------------------------------------+
int GetChannelSignal()
{
   // H1足の直近40本で線形回帰チャネルを近似
   int lookback = 40;
   double sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;

   for(int i = lookback - 1; i >= 0; i--)
   {
      double x = lookback - 1 - i;
      double y = iClose(_Symbol, PERIOD_H1, i);
      sumX  += x;
      sumY  += y;
      sumXY += x * y;
      sumX2 += x * x;
   }

   double n = lookback;
   double slope     = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
   double intercept = (sumY - slope * sumX) / n;

   // 残差の標準偏差
   double sumRes2 = 0;
   for(int i = lookback - 1; i >= 0; i--)
   {
      double x = lookback - 1 - i;
      double predicted = intercept + slope * x;
      double actual    = iClose(_Symbol, PERIOD_H1, i);
      sumRes2 += MathPow(actual - predicted, 2);
   }
   double stdDev = MathSqrt(sumRes2 / n);

   // 現在価格のチャネル内位置
   double currentPredicted = intercept + slope * (n - 1);
   double upperChannel = currentPredicted + stdDev * 2;
   double lowerChannel = currentPredicted - stdDev * 2;
   double close = iClose(_Symbol, PERIOD_H1, 1);  // 確定足を使用

   if(upperChannel == lowerChannel) return 0;
   double channelPos = (close - lowerChannel) / (upperChannel - lowerChannel);

   // チャネル下限付近 + 上向き → 買い
   if(channelPos < 0.2 && slope > 0) return 1;
   // チャネル上限付近 + 下向き → 売り
   if(channelPos > 0.8 && slope < 0) return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| ロット計算（リスク%ベース）                                         |
//+------------------------------------------------------------------+
double CalcLotSize()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > peakBalance) peakBalance = balance;
   double currentDD = (peakBalance > 0) ? (peakBalance - balance) / peakBalance * 100 : 0;

   // 動的リスクスケーリング
   double riskPct = RiskPercent;
   if(currentDD >= MaxDrawdownPct)
      riskPct *= 0.25;  // DD 6%以上: 1/4
   else if(currentDD >= DDHalfRiskPct)
      riskPct *= 0.5;   // DD 2.5%以上: 1/2

   double riskAmount = balance * riskPct / 100.0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0 || tickSize <= 0) return MinLots;

   double slPoints = StopLossPips * _Point * 10;
   double riskPerLot = (slPoints / tickSize) * tickValue;

   if(riskPerLot <= 0) return MinLots;

   double lots = riskAmount / riskPerLot;

   // ロットサイズ制限
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(MinLots, MathMin(MaxLots, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| ポジション管理（トレーリング + 建値移動）                             |
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

      double pipValue = _Point * 10;

      if(posType == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profit = (bid - openPrice) / pipValue;

         // 建値移動
         if(profit >= BreakevenPips && sl < openPrice)
         {
            double newSL = openPrice + 1 * pipValue;  // 1pip上に
            trade.PositionModify(ticket, newSL, tp);
         }
         // トレーリング
         else if(profit >= TrailingStartPips)
         {
            double newSL = bid - TrailingStepPips * pipValue;
            if(newSL > sl + 0.5 * pipValue)
               trade.PositionModify(ticket, newSL, tp);
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profit = (openPrice - ask) / pipValue;

         // 建値移動
         if(profit >= BreakevenPips && (sl > openPrice || sl == 0))
         {
            double newSL = openPrice - 1 * pipValue;
            trade.PositionModify(ticket, newSL, tp);
         }
         // トレーリング
         else if(profit >= TrailingStartPips)
         {
            double newSL = ask + TrailingStepPips * pipValue;
            if(newSL < sl - 0.5 * pipValue || sl == 0)
               trade.PositionModify(ticket, newSL, tp);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| 自ポジション数カウント                                              |
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

//+------------------------------------------------------------------+
//| 時間フィルター                                                     |
//+------------------------------------------------------------------+
bool CheckTimeFilter()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   // 取引時間外
   if(dt.hour < TradeStartHour || dt.hour >= TradeEndHour)
      return false;

   // 金曜夜の回避
   if(AvoidFriday && dt.day_of_week == 5 && dt.hour >= 18)
      return false;

   return true;
}

//+------------------------------------------------------------------+
//| スプレッドチェック                                                  |
//+------------------------------------------------------------------+
bool CheckSpread()
{
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return spread <= MaxSpread;
}

//+------------------------------------------------------------------+
//| 取引許可チェック                                                    |
//+------------------------------------------------------------------+
bool IsTradeAllowed()
{
   return MQLInfoInteger(MQL_TRADE_ALLOWED) &&
          TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) &&
          AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
}

//+------------------------------------------------------------------+
//| インジケーター値取得ユーティリティ                                   |
//+------------------------------------------------------------------+
double GetIndicatorValue(int handle, int buffer, int shift)
{
   double val[];
   if(CopyBuffer(handle, buffer, shift, 1, val) <= 0)
      return 0;
   return val[0];
}

//+------------------------------------------------------------------+
