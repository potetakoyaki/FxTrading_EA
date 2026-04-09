//+------------------------------------------------------------------+
//| MARibbon.mq5 - Moving Average Ribbon Strategy on D1              |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

input double LotSize      = 0.10;
input double SL_ATR_Multi = 2.0;
input double TP_ATR_Multi = 3.0;
input int    ATR_Period    = 14;
input int    MagicNumber   = 10002;

CTrade trade;
int hEMA8, hEMA13, hEMA21, hEMA55, hATR;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   hEMA8  = iMA(_Symbol, PERIOD_D1, 8, 0, MODE_EMA, PRICE_CLOSE);
   hEMA13 = iMA(_Symbol, PERIOD_D1, 13, 0, MODE_EMA, PRICE_CLOSE);
   hEMA21 = iMA(_Symbol, PERIOD_D1, 21, 0, MODE_EMA, PRICE_CLOSE);
   hEMA55 = iMA(_Symbol, PERIOD_D1, 55, 0, MODE_EMA, PRICE_CLOSE);
   hATR   = iATR(_Symbol, PERIOD_D1, ATR_Period);
   if(hEMA8==INVALID_HANDLE || hEMA13==INVALID_HANDLE ||
      hEMA21==INVALID_HANDLE || hEMA55==INVALID_HANDLE || hATR==INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hEMA8);
   IndicatorRelease(hEMA13);
   IndicatorRelease(hEMA21);
   IndicatorRelease(hEMA55);
   IndicatorRelease(hATR);
}

bool HasPosition()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         return true;
   return false;
}

void OnTick()
{
   if(HasPosition()) return;

   // Only trade on new D1 bar
   static datetime lastBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_D1, 0);
   if(currBar == lastBar) return;
   lastBar = currBar;

   double ema8[2], ema13[2], ema21[2], ema55[2], atr[1];
   if(CopyBuffer(hEMA8, 0, 1, 2, ema8) < 2) return;
   if(CopyBuffer(hEMA13, 0, 1, 2, ema13) < 2) return;
   if(CopyBuffer(hEMA21, 0, 1, 2, ema21) < 2) return;
   if(CopyBuffer(hEMA55, 0, 1, 2, ema55) < 2) return;
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;

   double close = iClose(_Symbol, PERIOD_D1, 1);
   double low   = iLow(_Symbol, PERIOD_D1, 1);
   double high  = iHigh(_Symbol, PERIOD_D1, 1);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Bullish ribbon: 8>13>21>55 AND price pulled back to EMA21
   if(ema8[1] > ema13[1] && ema13[1] > ema21[1] && ema21[1] > ema55[1])
   {
      // Pullback: low touched EMA21 zone
      if(low <= ema21[1] * 1.002 && close > ema21[1])
      {
         double sl = NormalizeDouble(ask - atr[0] * SL_ATR_Multi, _Digits);
         double tp = NormalizeDouble(ask + atr[0] * TP_ATR_Multi, _Digits);
         trade.Buy(LotSize, _Symbol, ask, sl, tp);
      }
   }

   // Bearish ribbon: 8<13<21<55 AND price pulled back to EMA21
   if(ema8[1] < ema13[1] && ema13[1] < ema21[1] && ema21[1] < ema55[1])
   {
      if(high >= ema21[1] * 0.998 && close < ema21[1])
      {
         double sl = NormalizeDouble(bid + atr[0] * SL_ATR_Multi, _Digits);
         double tp = NormalizeDouble(bid - atr[0] * TP_ATR_Multi, _Digits);
         trade.Sell(LotSize, _Symbol, bid, sl, tp);
      }
   }
}
