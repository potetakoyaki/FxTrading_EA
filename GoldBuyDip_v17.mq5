//+------------------------------------------------------------------+
//| GoldBuyDip_v17.mq5 - v13 optimized: TP=2.5, tighter dip zone   |
//| D1 EMA(10/50) trend, dip to EMA(20), H1 bullish confirm         |
//| BUY+SELL, SL=2.0 ATR, TP=2.5 ATR, London/NY session 7-20      |
//| Tighter dip zone: DipNear=0.3, DipFar=1.5                      |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "17.00"
#property strict

#include <Trade\Trade.mqh>

input int      TrendFast     = 10;
input int      TrendSlow     = 50;
input int      DipMA_Period  = 20;
input double   SL_ATR_Mult   = 2.0;
input double   TP_ATR_Mult   = 2.5;      // Sweet spot
input int      ATR_Period    = 14;
input double   DipNear       = 0.3;       // Tighter zone
input double   DipFar        = 1.5;       // Avoid deep dips
input int      SessionStart  = 7;
input int      SessionEnd    = 20;
input double   LotSize       = 0.05;
input int      MagicNumber   = 111027;

CTrade trade;
int handleTrendFast, handleTrendSlow, handleDipMA, handleATR;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   handleTrendFast = iMA(_Symbol, PERIOD_D1, TrendFast, 0, MODE_EMA, PRICE_CLOSE);
   handleTrendSlow = iMA(_Symbol, PERIOD_D1, TrendSlow, 0, MODE_EMA, PRICE_CLOSE);
   handleDipMA     = iMA(_Symbol, PERIOD_D1, DipMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   handleATR       = iATR(_Symbol, PERIOD_H4, ATR_Period);
   if(handleTrendFast == INVALID_HANDLE || handleTrendSlow == INVALID_HANDLE ||
      handleDipMA == INVALID_HANDLE || handleATR == INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(handleTrendFast);
   IndicatorRelease(handleTrendSlow);
   IndicatorRelease(handleDipMA);
   IndicatorRelease(handleATR);
}

void OnTick()
{
   static datetime lastBar = 0;
   datetime currentBar = iTime(_Symbol, PERIOD_H1, 0);
   if(currentBar == lastBar) return;
   lastBar = currentBar;

   if(PositionSelect(_Symbol)) return;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.hour < SessionStart || dt.hour >= SessionEnd) return;

   double tFast[1], tSlow[1], dipMA[1];
   if(CopyBuffer(handleTrendFast, 0, 1, 1, tFast) < 1) return;
   if(CopyBuffer(handleTrendSlow, 0, 1, 1, tSlow) < 1) return;
   if(CopyBuffer(handleDipMA, 0, 1, 1, dipMA) < 1) return;

   double atr[1];
   if(CopyBuffer(handleATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double h1Open  = iOpen(_Symbol, PERIOD_H1, 1);
   double h1Close = iClose(_Symbol, PERIOD_H1, 1);

   // BUY: uptrend + dip to EMA(20)
   if(tFast[0] > tSlow[0])
   {
      double dist = dipMA[0] - ask;
      if(dist > -DipNear * atrVal && dist <= DipFar * atrVal)
      {
         if(h1Close > h1Open)
         {
            double sl = NormalizeDouble(ask - SL_ATR_Mult * atrVal, _Digits);
            double tp = NormalizeDouble(ask + TP_ATR_Mult * atrVal, _Digits);
            trade.Buy(LotSize, _Symbol, ask, sl, tp, "v17 BUY");
            return;
         }
      }
   }

   // SELL: downtrend + rally to EMA(20)
   if(tFast[0] < tSlow[0])
   {
      double dist = bid - dipMA[0];
      if(dist > -DipNear * atrVal && dist <= DipFar * atrVal)
      {
         if(h1Close < h1Open)
         {
            double sl = NormalizeDouble(bid + SL_ATR_Mult * atrVal, _Digits);
            double tp = NormalizeDouble(bid - TP_ATR_Mult * atrVal, _Digits);
            trade.Sell(LotSize, _Symbol, bid, sl, tp, "v17 SELL");
            return;
         }
      }
   }
}
//+------------------------------------------------------------------+
