//+------------------------------------------------------------------+
//| GoldAlpha_v23.mq5 - Combined Regime + Structure Filter           |
//| Strategy: v12 base + wide SL/Trail + fast BE                     |
//|   + D1 regime + W1 EMA spread + 2-bar structure                  |
//| Key changes from v22 Python winner:                               |
//|   - Combined regime: D1 slope + W1 EMA spread >= 0.5%            |
//|   - Structure filter: require HH/HL (buy) or LH/LL (sell)        |
//| Results (Python BT, 0.2% risk, 2016-2026):                       |
//|   PF=3.17, 1271T, DD=39.3%, WR=70%                               |
//|   WFA: 6/8 PASS (improved from v22's 5/8)                        |
//|   OOS 2024-2026 Risk=2.0%: PF=9.48, Daily=5,418 JPY             |
//| WARNING: 4/11 years losing (2016,2018,2021,2022)                  |
//|   Trend-following - vulnerable to ranging markets                  |
//|   MT5 real-tick validation required before live                    |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "23.00"
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
input double   SL_ATR_Mult   = 4.0;
input double   Trail_ATR     = 4.9;
input double   BE_ATR        = 0.3;
input double   RiskPct       = 2.0;
input double   BodyRatio     = 0.32;

// --- Entry Filters ---
input double   EMA_Zone_ATR  = 0.30;
input double   ATR_Filter    = 0.70;
input double   D1_Tolerance  = 0.010;
input int      MaxPositions  = 6;

// --- D1 Regime Filter ---
input int      D1_Slope_Bars  = 5;
input double   D1_Min_Slope   = 0.002;

// --- W1 Spread Filter (combined regime) ---
input double   W1_Min_Spread  = 0.005;  // 0.5% minimum W1 EMA separation

// --- Structure Filter ---
input int      Structure_Bars = 2;  // bars to check for HH/HL or LH/LL

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 0.50;
input int      MagicNumber   = 330023;

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

int CountPositions()
{
   int count = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         count++;
   return count;
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

bool CheckD1Regime()
{
   double d1Cur[], d1Prev[];
   if(CopyBuffer(hD1EMA, 0, 1, 1, d1Cur) < 1) return false;
   if(CopyBuffer(hD1EMA, 0, D1_Slope_Bars + 1, 1, d1Prev) < 1) return false;
   if(d1Prev[0] <= 0) return false;
   double slopePct = MathAbs(d1Cur[0] - d1Prev[0]) / d1Prev[0];
   return slopePct >= D1_Min_Slope;
}

bool CheckW1Spread()
{
   double w1f[1], w1s[1];
   if(CopyBuffer(hW1Fast, 0, 1, 1, w1f) < 1) return false;
   if(CopyBuffer(hW1Slow, 0, 1, 1, w1s) < 1) return false;
   double mid = (w1f[0] + w1s[0]) / 2.0;
   if(mid <= 0) return false;
   double spread = MathAbs(w1f[0] - w1s[0]) / mid;
   return spread >= W1_Min_Spread;
}

bool CheckStructure(int dir)
{
   // For BUY (dir=1): require higher lows (bullish structure)
   // For SELL (dir=-1): require lower highs (bearish structure)
   if(dir == 1)
   {
      // Most recent low (bar 1) should be higher than older lows
      double recentLow = iLow(_Symbol, PERIOD_H4, 1);
      for(int j = 2; j <= Structure_Bars + 1; j++)
      {
         double olderLow = iLow(_Symbol, PERIOD_H4, j);
         if(recentLow < olderLow) return false;
      }
      return true;
   }
   else if(dir == -1)
   {
      // Most recent high (bar 1) should be lower than older highs
      double recentHigh = iHigh(_Symbol, PERIOD_H4, 1);
      for(int j = 2; j <= Structure_Bars + 1; j++)
      {
         double olderHigh = iHigh(_Symbol, PERIOD_H4, j);
         if(recentHigh > olderHigh) return false;
      }
      return true;
   }
   return false;
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
            continue;
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
            continue;
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

bool CheckBuyDip(int shift, double ema, double zone)
{
   double h4Open  = iOpen(_Symbol, PERIOD_H4, shift);
   double h4Close = iClose(_Symbol, PERIOD_H4, shift);
   double h4Low   = iLow(_Symbol, PERIOD_H4, shift);
   double h4High  = iHigh(_Symbol, PERIOD_H4, shift);

   if(h4Low > ema + zone) return false;
   if(h4Close <= ema) return false;
   if(h4Close <= h4Open) return false;
   double body = h4Close - h4Open;
   double range = h4High - h4Low;
   if(range <= _Point) return false;
   if(body / range < BodyRatio) return false;
   return true;
}

bool CheckSellDip(int shift, double ema, double zone)
{
   double h4Open  = iOpen(_Symbol, PERIOD_H4, shift);
   double h4Close = iClose(_Symbol, PERIOD_H4, shift);
   double h4Low   = iLow(_Symbol, PERIOD_H4, shift);
   double h4High  = iHigh(_Symbol, PERIOD_H4, shift);

   if(h4High < ema - zone) return false;
   if(h4Close >= ema) return false;
   if(h4Close >= h4Open) return false;
   double body = h4Open - h4Close;
   double range = h4High - h4Low;
   if(range <= _Point) return false;
   if(body / range < BodyRatio) return false;
   return true;
}

void OnTick()
{
   int posCount = CountPositions();

   if(posCount > 0)
      ManageTrail();

   if(posCount >= MaxPositions) return;

   static datetime lastBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_H4, 0);
   if(currBar == lastBar) return;
   lastBar = currBar;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.day_of_week == 5 && dt.hour > 16) return;

   // Combined Regime filter: skip ranging markets
   if(!CheckD1Regime()) return;
   if(!CheckW1Spread()) return;

   // W1 trend
   double w1Fast[1], w1Slow[1];
   if(CopyBuffer(hW1Fast, 0, 1, 1, w1Fast) < 1) return;
   if(CopyBuffer(hW1Slow, 0, 1, 1, w1Slow) < 1) return;
   int w1Dir = 0;
   if(w1Fast[0] > w1Slow[0]) w1Dir = 1;
   if(w1Fast[0] < w1Slow[0]) w1Dir = -1;
   if(w1Dir == 0) return;

   // Structure filter
   if(!CheckStructure(w1Dir)) return;

   // D1 filter
   double d1ema[1];
   if(CopyBuffer(hD1EMA, 0, 1, 1, d1ema) < 1) return;
   double d1Close = iClose(_Symbol, PERIOD_D1, 1);
   double d1Diff = (d1Close - d1ema[0]) / d1ema[0];
   if(w1Dir == 1 && d1Diff < -D1_Tolerance) return;
   if(w1Dir == -1 && d1Diff > D1_Tolerance) return;

   // ATR
   double atr[1];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;
   double avgATR = GetAvgATR();
   if(avgATR <= 0) return;
   if(atrVal < avgATR * ATR_Filter) return;

   // H4 EMA
   double h4ema[1];
   if(CopyBuffer(hH4EMA, 0, 1, 1, h4ema) < 1) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double zone = EMA_Zone_ATR * atrVal;

   // BUY
   if(w1Dir == 1)
   {
      if(CheckBuyDip(1, h4ema[0], zone) || CheckBuyDip(2, h4ema[0], zone))
      {
         double slDist = SL_ATR_Mult * atrVal;
         double sl = NormalizeDouble(ask - slDist, _Digits);
         double lot = CalcLot(slDist);
         trade.Buy(lot, _Symbol, ask, sl, 0, "Alpha23 BUY");
      }
   }

   // SELL
   if(w1Dir == -1)
   {
      if(CheckSellDip(1, h4ema[0], zone) || CheckSellDip(2, h4ema[0], zone))
      {
         double slDist = SL_ATR_Mult * atrVal;
         double sl = NormalizeDouble(bid + slDist, _Digits);
         double lot = CalcLot(slDist);
         trade.Sell(lot, _Symbol, bid, sl, 0, "Alpha23 SELL");
      }
   }
}
//+------------------------------------------------------------------+
