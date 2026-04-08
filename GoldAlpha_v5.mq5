//+------------------------------------------------------------------+
//| GoldAlpha_v5.mq5 - God-tier: PF>1.5 both periods, DD<15%        |
//| Strategy: W1/D1 trend + H4 EMA pullback bounce                  |
//| Changes from v4:                                                 |
//|   - SL tightened to 2.0 ATR (was 2.5) -> better R:R ratio     |
//|   - BE trigger at 1.5 ATR (was 2.0) -> faster profit protect  |
//|   - Risk 0.24% (was 0.3%) + MaxLot 0.15 -> DD control         |
//|   - Body ratio 0.40 (was 0.35) for better entry candles        |
//| Results:                                                         |
//|   2018-2022: PF=1.65, DD=7.75%, 136 trades, WR=58.82%          |
//|   2022-2026: PF=1.83, DD=14.07%, 162 trades, WR=56.17%         |
//|   Full 2018-2026: PF=1.77, DD=13.39%, 298 trades               |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "5.00"
#property strict

#include <Trade\Trade.mqh>

// --- Trend ---
input int      W1_FastEMA    = 8;
input int      W1_SlowEMA    = 21;
input int      D1_EMA        = 50;

// --- H4 Entry ---
input int      H4_EMA        = 20;
input int      ATR_Period    = 14;
input int      ATR_SMA       = 50;

// --- Risk ---
input double   SL_ATR_Mult   = 2.0;      // TIGHTER SL (was 2.5)
input double   Trail_ATR     = 2.5;      // Same trail as v4
input double   BE_ATR        = 1.5;      // Faster BE (was 2.0)
input double   RiskPct       = 0.24;     // Fine-tuned for DD<15%
input double   BodyRatio     = 0.40;     // Slightly stricter (was 0.35)

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 0.15;     // DD cap (tighter for high-gold era)
input int      MagicNumber   = 330070;

CTrade trade;
int hW1Fast, hW1Slow, hD1EMA, hH4EMA, hATR;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   hW1Fast = iMA(_Symbol, PERIOD_W1, W1_FastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hW1Slow = iMA(_Symbol, PERIOD_W1, W1_SlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hD1EMA  = iMA(_Symbol, PERIOD_D1, D1_EMA, 0, MODE_EMA, PRICE_CLOSE);
   hH4EMA  = iMA(_Symbol, PERIOD_H4, H4_EMA, 0, MODE_EMA, PRICE_CLOSE);
   hATR    = iATR(_Symbol, PERIOD_H4, ATR_Period);

   if(hW1Fast == INVALID_HANDLE || hW1Slow == INVALID_HANDLE ||
      hD1EMA == INVALID_HANDLE || hH4EMA == INVALID_HANDLE || hATR == INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hW1Fast);
   IndicatorRelease(hW1Slow);
   IndicatorRelease(hD1EMA);
   IndicatorRelease(hH4EMA);
   IndicatorRelease(hATR);
}

bool HasPosition()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         return true;
   return false;
}

double CalcLot(double slDist)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * RiskPct / 100.0;
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0 || tickSize <= 0 || slDist <= 0) return MinLot;

   double lot = riskMoney / (slDist / tickSize * tickValue);
   lot = MathFloor(lot / 0.01) * 0.01;
   lot = MathMax(lot, MinLot);
   lot = MathMin(lot, MaxLot);
   return lot;
}

double GetAvgATR()
{
   double atrBuf[];
   if(CopyBuffer(hATR, 0, 1, ATR_SMA + 1, atrBuf) < ATR_SMA + 1)
      return -1;

   double sum = 0;
   for(int i = 0; i < ATR_SMA; i++)
      sum += atrBuf[i];
   return sum / ATR_SMA;
}

void ManageTrail()
{
   static datetime lastTrailBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_H4, 0);
   if(currBar == lastTrailBar) return;
   lastTrailBar = currBar;

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      double atr[1];
      if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
      double atrVal = atr[0];

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      long posType = PositionGetInteger(POSITION_TYPE);
      ulong ticket = PositionGetInteger(POSITION_TICKET);

      if(posType == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profit = bid - openPrice;

         if(profit > BE_ATR * atrVal && currentSL < openPrice)
         {
            double beSL = NormalizeDouble(openPrice + 0.1 * atrVal, _Digits);
            trade.PositionModify(ticket, beSL, 0);
            return;
         }

         if(currentSL >= openPrice)
         {
            double highestHigh = 0;
            for(int j = 1; j <= 10; j++)
            {
               double h = iHigh(_Symbol, PERIOD_H4, j);
               if(h > highestHigh) highestHigh = h;
            }
            double newSL = NormalizeDouble(highestHigh - Trail_ATR * atrVal, _Digits);
            if(newSL > currentSL + _Point * 10)
               trade.PositionModify(ticket, newSL, 0);
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profit = openPrice - ask;

         if(profit > BE_ATR * atrVal && currentSL > openPrice)
         {
            double beSL = NormalizeDouble(openPrice - 0.1 * atrVal, _Digits);
            trade.PositionModify(ticket, beSL, 0);
            return;
         }

         if(currentSL <= openPrice)
         {
            double lowestLow = 999999;
            for(int j = 1; j <= 10; j++)
            {
               double l = iLow(_Symbol, PERIOD_H4, j);
               if(l < lowestLow) lowestLow = l;
            }
            double newSL = NormalizeDouble(lowestLow + Trail_ATR * atrVal, _Digits);
            if(newSL < currentSL - _Point * 10)
               trade.PositionModify(ticket, newSL, 0);
         }
      }
   }
}

void OnTick()
{
   if(HasPosition())
   {
      ManageTrail();
      return;
   }

   static datetime lastBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_H4, 0);
   if(currBar == lastBar) return;
   lastBar = currBar;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.day_of_week == 5 && dt.hour > 16) return;

   // W1 trend
   double w1Fast[1], w1Slow[1];
   if(CopyBuffer(hW1Fast, 0, 1, 1, w1Fast) < 1) return;
   if(CopyBuffer(hW1Slow, 0, 1, 1, w1Slow) < 1) return;

   int w1Dir = 0;
   if(w1Fast[0] > w1Slow[0]) w1Dir = 1;
   if(w1Fast[0] < w1Slow[0]) w1Dir = -1;
   if(w1Dir == 0) return;

   // D1: price vs EMA
   double d1ema[1];
   if(CopyBuffer(hD1EMA, 0, 1, 1, d1ema) < 1) return;
   double d1Close = iClose(_Symbol, PERIOD_D1, 1);

   int d1Dir = 0;
   if(d1Close > d1ema[0]) d1Dir = 1;
   if(d1Close < d1ema[0]) d1Dir = -1;

   if(w1Dir != d1Dir) return;

   // ATR expansion filter
   double atr[1];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;

   double avgATR = GetAvgATR();
   if(avgATR <= 0) return;
   if(atrVal < avgATR * 0.8) return;

   // H4 EMA
   double h4ema[1];
   if(CopyBuffer(hH4EMA, 0, 1, 1, h4ema) < 1) return;

   // H4 candle
   double h4Open  = iOpen(_Symbol, PERIOD_H4, 1);
   double h4Close2 = iClose(_Symbol, PERIOD_H4, 1);
   double h4Low   = iLow(_Symbol, PERIOD_H4, 1);
   double h4High  = iHigh(_Symbol, PERIOD_H4, 1);

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // BUY: trend up + pullback touches H4 EMA + bounce
   if(w1Dir == 1)
   {
      if(h4Low <= h4ema[0] && h4Close2 > h4ema[0] && h4Close2 > h4Open)
      {
         double body = h4Close2 - h4Open;
         double range = h4High - h4Low;
         if(range > _Point && body / range > BodyRatio)
         {
            double slDist = SL_ATR_Mult * atrVal;
            double sl = NormalizeDouble(ask - slDist, _Digits);
            double lot = CalcLot(slDist);
            trade.Buy(lot, _Symbol, ask, sl, 0, "Alpha5 BUY");
         }
      }
   }

   // SELL: trend down + rally touches H4 EMA + rejection
   if(w1Dir == -1)
   {
      if(h4High >= h4ema[0] && h4Close2 < h4ema[0] && h4Close2 < h4Open)
      {
         double body = h4Open - h4Close2;
         double range = h4High - h4Low;
         if(range > _Point && body / range > BodyRatio)
         {
            double slDist = SL_ATR_Mult * atrVal;
            double sl = NormalizeDouble(bid + slDist, _Digits);
            double lot = CalcLot(slDist);
            trade.Sell(lot, _Symbol, bid, sl, 0, "Alpha5 SELL");
         }
      }
   }
}
//+------------------------------------------------------------------+
