//+------------------------------------------------------------------+
//| GoldBuyDip.mq5 - Simple Buy the Dip in Gold Uptrend             |
//| Philosophy: Gold trends strongly. Buy when it dips, hold for TP  |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

input int      MA_Fast       = 50;       // D1 EMA fast
input int      MA_Slow       = 200;      // D1 EMA slow (golden cross)
input double   SL_ATR_Mult   = 2.0;      // SL = 2.0 x H4 ATR (wide, stay in)
input double   TP_ATR_Mult   = 3.0;      // TP = 3.0 x H4 ATR
input int      ATR_Period    = 14;       // ATR period
input double   DipPercent    = 0.3;      // Dip: price < MA_Fast by X x ATR
input double   LotSize       = 0.05;     // Fixed lot size
input int      MagicNumber   = 111010;

CTrade trade;
int handleMAFast, handleMASlow, handleATR;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   handleMAFast = iMA(_Symbol, PERIOD_D1, MA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   handleMASlow = iMA(_Symbol, PERIOD_D1, MA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   handleATR    = iATR(_Symbol, PERIOD_H4, ATR_Period);

   if(handleMAFast == INVALID_HANDLE || handleMASlow == INVALID_HANDLE ||
      handleATR == INVALID_HANDLE)
   {
      Print("Failed to create indicators");
      return INIT_FAILED;
   }
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(handleMAFast);
   IndicatorRelease(handleMASlow);
   IndicatorRelease(handleATR);
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Only on new H4 bar
   static datetime lastBar = 0;
   datetime currentBar = iTime(_Symbol, PERIOD_H4, 0);
   if(currentBar == lastBar) return;
   lastBar = currentBar;

   // Max 1 position
   if(PositionSelect(_Symbol)) return;

   // Skip weekends
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;

   // D1 MAs - completed bar
   double maFast[1], maSlow[1];
   if(CopyBuffer(handleMAFast, 0, 1, 1, maFast) < 1) return;
   if(CopyBuffer(handleMASlow, 0, 1, 1, maSlow) < 1) return;

   // Golden cross: fast > slow = uptrend
   if(maFast[0] <= maSlow[0]) return;

   // H4 ATR - completed bar
   double atr[1];
   if(CopyBuffer(handleATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // Buy the dip: price is near or below D1 fast EMA
   double dist = maFast[0] - ask; // positive means price is below MA
   if(dist < 0) return; // Price must be at or below fast MA (dip)
   if(dist > 2.0 * atrVal) return; // Don't buy too deep a dip

   // H4 bar confirmation: last H4 bar should be bullish
   double h4Open  = iOpen(_Symbol, PERIOD_H4, 1);
   double h4Close = iClose(_Symbol, PERIOD_H4, 1);
   if(h4Close <= h4Open) return; // Need bullish H4

   double sl_dist = NormalizeDouble(SL_ATR_Mult * atrVal, _Digits);
   double tp_dist = NormalizeDouble(TP_ATR_Mult * atrVal, _Digits);
   double sl = NormalizeDouble(ask - sl_dist, _Digits);
   double tp = NormalizeDouble(ask + tp_dist, _Digits);

   trade.Buy(LotSize, _Symbol, ask, sl, tp, "GoldBuyDip BUY");
}
//+------------------------------------------------------------------+
