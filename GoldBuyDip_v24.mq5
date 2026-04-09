//+------------------------------------------------------------------+
//| GoldBuyDip_v24.mq5 - v17 + ADX-based position sizing            |
//| ADX < 25 => half lot, ADX > 30 => full lot                      |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "24.00"
#property strict

#include <Trade\Trade.mqh>

input int      TrendFast     = 10;
input int      TrendSlow     = 50;
input int      DipMA_Period  = 20;
input double   SL_ATR_Mult   = 2.0;
input double   TP_ATR_Mult   = 2.5;
input int      ATR_Period    = 14;
input double   DipNear       = 0.3;
input double   DipFar        = 1.5;
input int      SessionStart  = 7;
input int      SessionEnd    = 20;
input double   LotSize       = 0.05;
input int      MagicNumber   = 111034;
input int      ADX_Period    = 14;
input double   ADX_HalfLot   = 25.0;    // Below this: half lot
input double   ADX_FullLot   = 30.0;    // Above this: full lot

CTrade trade;
int handleTrendFast, handleTrendSlow, handleDipMA, handleATR, handleADX;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   handleTrendFast = iMA(_Symbol, PERIOD_D1, TrendFast, 0, MODE_EMA, PRICE_CLOSE);
   handleTrendSlow = iMA(_Symbol, PERIOD_D1, TrendSlow, 0, MODE_EMA, PRICE_CLOSE);
   handleDipMA     = iMA(_Symbol, PERIOD_D1, DipMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   handleATR       = iATR(_Symbol, PERIOD_H4, ATR_Period);
   handleADX       = iADX(_Symbol, PERIOD_H4, ADX_Period);
   if(handleTrendFast == INVALID_HANDLE || handleTrendSlow == INVALID_HANDLE ||
      handleDipMA == INVALID_HANDLE || handleATR == INVALID_HANDLE ||
      handleADX == INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(handleTrendFast);
   IndicatorRelease(handleTrendSlow);
   IndicatorRelease(handleDipMA);
   IndicatorRelease(handleATR);
   IndicatorRelease(handleADX);
}

double GetAdjustedLot(double adxValue)
{
   if(adxValue >= ADX_FullLot)
      return LotSize;
   else if(adxValue < ADX_HalfLot)
      return NormalizeDouble(LotSize * 0.5, 2);
   else
   {
      // Linear interpolation between half and full
      double ratio = (adxValue - ADX_HalfLot) / (ADX_FullLot - ADX_HalfLot);
      double lot = LotSize * (0.5 + 0.5 * ratio);
      return NormalizeDouble(MathMax(lot, 0.01), 2);
   }
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

   // Get ADX for position sizing
   double adxMain[1];
   if(CopyBuffer(handleADX, 0, 1, 1, adxMain) < 1) return;
   double actualLot = GetAdjustedLot(adxMain[0]);

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
            trade.Buy(actualLot, _Symbol, ask, sl, tp, "v24 BUY");
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
            trade.Sell(actualLot, _Symbol, bid, sl, tp, "v24 SELL");
            return;
         }
      }
   }
}
//+------------------------------------------------------------------+
