//+------------------------------------------------------------------+
//| TrendPB_v5.mq5 - BUY-ONLY Trend Pullback v5                     |
//| Goal: PF > 1.5                                                   |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "5.00"
#property strict

#include <Trade\Trade.mqh>

input int      ADX_Period    = 14;       // ADX period
input int      ADX_Level     = 25;       // ADX trend threshold (stricter)
input int      MA_Period     = 20;       // H1 EMA period for pullback
input int      TrendMA       = 100;      // H4 EMA for long-term trend
input double   SL_ATR_Mult   = 1.5;      // SL multiplier x ATR
input double   TP_ATR_Mult   = 2.2;      // TP multiplier x ATR
input int      ATR_Period    = 14;       // ATR period
input double   PullbackNear  = 0.5;      // Max distance above MA (x ATR)
input double   PullbackFar   = 0.2;      // Max distance below MA (x ATR)
input double   LotSize       = 0.05;     // Fixed lot size
input int      SessionStart  = 8;        // Session start
input int      SessionEnd    = 17;       // Session end
input int      CooldownHrs   = 8;        // Cooldown hours after SL
input int      RSI_Period    = 14;       // RSI period
input int      RSI_Max       = 60;       // Max RSI (stricter)
input int      RSI_Min       = 35;       // Min RSI
input int      MagicNumber   = 111009;

CTrade trade;
int handleADX, handleMA_H1, handleATR, handleTrendMA, handleRSI;
datetime lastSLTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   handleADX     = iADX(_Symbol, PERIOD_H4, ADX_Period);
   handleMA_H1   = iMA(_Symbol, PERIOD_H1, MA_Period, 0, MODE_EMA, PRICE_CLOSE);
   handleATR     = iATR(_Symbol, PERIOD_H1, ATR_Period);
   handleTrendMA = iMA(_Symbol, PERIOD_H4, TrendMA, 0, MODE_EMA, PRICE_CLOSE);
   handleRSI     = iRSI(_Symbol, PERIOD_H1, RSI_Period, PRICE_CLOSE);

   if(handleADX == INVALID_HANDLE || handleMA_H1 == INVALID_HANDLE ||
      handleATR == INVALID_HANDLE || handleTrendMA == INVALID_HANDLE ||
      handleRSI == INVALID_HANDLE)
   {
      Print("Failed to create indicators");
      return INIT_FAILED;
   }
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(handleADX);
   IndicatorRelease(handleMA_H1);
   IndicatorRelease(handleATR);
   IndicatorRelease(handleTrendMA);
   IndicatorRelease(handleRSI);
}

//+------------------------------------------------------------------+
bool IsBullishM15()
{
   double open1  = iOpen(_Symbol, PERIOD_M15, 1);
   double close1 = iClose(_Symbol, PERIOD_M15, 1);
   double high2  = iHigh(_Symbol, PERIOD_M15, 2);
   double low1   = iLow(_Symbol, PERIOD_M15, 1);
   double low2   = iLow(_Symbol, PERIOD_M15, 2);

   // Bullish bar that closes above previous high
   // Also check body size is meaningful
   double body = close1 - open1;
   double range = iHigh(_Symbol, PERIOD_M15, 1) - low1;
   if(range < _Point) return false;
   double bodyRatio = body / range;

   return (close1 > open1) && (close1 > high2) && (bodyRatio > 0.4);
}

//+------------------------------------------------------------------+
void OnTick()
{
   static datetime lastBar = 0;
   datetime currentBar = iTime(_Symbol, PERIOD_M15, 0);
   if(currentBar == lastBar) return;
   lastBar = currentBar;

   if(PositionSelect(_Symbol)) return;

   // Cooldown
   CheckLastDealForSL();
   if(lastSLTime > 0 && (TimeCurrent() - lastSLTime) < CooldownHrs * 3600)
      return;

   // Session
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour < SessionStart || dt.hour >= SessionEnd) return;
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.day_of_week == 1 && dt.hour < 10) return;
   if(dt.day_of_week == 5 && dt.hour > 14) return;

   // ADX (H4)
   double adx[1], plusDI[1], minusDI[1];
   if(CopyBuffer(handleADX, 0, 1, 1, adx) < 1) return;
   if(CopyBuffer(handleADX, 1, 1, 1, plusDI) < 1) return;
   if(CopyBuffer(handleADX, 2, 1, 1, minusDI) < 1) return;

   if(adx[0] < ADX_Level) return;
   if(plusDI[0] <= minusDI[0]) return;

   // H1 MA
   double ma[3];
   if(CopyBuffer(handleMA_H1, 0, 1, 3, ma) < 3) return;
   if(!(ma[2] > ma[1] && ma[1] > ma[0])) return;

   // Long-term trend
   double trendMA[1];
   if(CopyBuffer(handleTrendMA, 0, 1, 1, trendMA) < 1) return;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(ask <= trendMA[0]) return;

   // RSI
   double rsi[1];
   if(CopyBuffer(handleRSI, 0, 1, 1, rsi) < 1) return;
   if(rsi[0] > RSI_Max || rsi[0] < RSI_Min) return;

   // ATR
   double atr[1];
   if(CopyBuffer(handleATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;

   // Pullback: price near MA
   double distToMA = ask - ma[2];
   if(distToMA > PullbackNear * atrVal) return;  // Too far above
   if(distToMA < -PullbackFar * atrVal) return;   // Too far below

   // Candle confirmation
   if(!IsBullishM15()) return;

   double sl_dist = NormalizeDouble(SL_ATR_Mult * atrVal, _Digits);
   double tp_dist = NormalizeDouble(TP_ATR_Mult * atrVal, _Digits);
   double sl = NormalizeDouble(ask - sl_dist, _Digits);
   double tp = NormalizeDouble(ask + tp_dist, _Digits);

   trade.Buy(LotSize, _Symbol, ask, sl, tp, "TrendPB5 BUY");
}

//+------------------------------------------------------------------+
void CheckLastDealForSL()
{
   HistorySelect(TimeCurrent() - 7*86400, TimeCurrent());
   int total = HistoryDealsTotal();
   for(int i = total - 1; i >= MathMax(0, total - 10); i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(magic != MagicNumber) continue;
      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT) continue;
      long reason = HistoryDealGetInteger(ticket, DEAL_REASON);
      if(reason == DEAL_REASON_SL)
      {
         datetime dealTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
         if(dealTime > lastSLTime)
            lastSLTime = dealTime;
      }
      break;
   }
}
//+------------------------------------------------------------------+
