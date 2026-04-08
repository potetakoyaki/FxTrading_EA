//+------------------------------------------------------------------+
//| GoldAlpha_v15.mq5 - Optimized trend-following dip EA             |
//| Strategy: W1/D1 trend + H4 EMA dip + Structure + Time Decay      |
//| Base: v13 (10K grid optimized) + new exit/entry tuning            |
//|                                                                    |
//| Changes from v13:                                                  |
//|   - EMA_Zone_ATR: 0.40 -> 0.60 (wider dip zone, +60% trades)     |
//|   - ATR_Filter: 0.35 -> 0.25 (accept lower vol)                   |
//|   - MaxPositions: 3 -> 4 (more concurrent)                        |
//|   - SL_ATR_Mult: 2.5 -> 3.0 (wider SL, fewer stop-outs)          |
//|   - Trail_ATR: 3.5 -> 3.0 (tighter trailing, capture more)        |
//|   - BE_ATR: 1.5 -> 1.0 (earlier breakeven)                        |
//|   - NEW: Structure filter (2-bar HH/HL or LH/LL)                  |
//|   - NEW: Time Decay (30 H4 bars auto-close)                       |
//|                                                                    |
//| Backtest (Python, 2016-2026, 300K JPY):                           |
//|   Low risk (0.18%): PF=1.83, 1625T, DD=33.4%, WR=62.6%           |
//|   OOS 2024-2026:    PF=2.88, 427T, DD=18.6% (1% risk)            |
//|   OOS 2024-2026:    PF=3.76, 427T, Daily=5044 (1.5% risk)        |
//|   WFA: 3/8 (trend strategy, fails in range markets)               |
//| NOTE: DD at 2.5%+ risk exceeds 60%. Start with 1-1.5% risk.      |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "15.00"
#property strict

#include <Trade\Trade.mqh>

// --- Trend ---
input int      W1_FastEMA    = 8;
input int      W1_SlowEMA    = 21;
input int      D1_EMA        = 50;

// --- H4 Entry ---
input int      H4_EMA        = 20;
input int      ATR_Period     = 14;
input int      ATR_SMA        = 50;

// --- Risk / Exit ---
input double   SL_ATR_Mult    = 3.0;    // Stop loss width (ATR × this)
input double   Trail_ATR      = 3.0;    // Trailing stop distance (ATR × this)
input double   BE_ATR         = 1.0;    // Break-even trigger (ATR × this)
input double   RiskPct        = 1.5;    // Risk % per position
input double   BodyRatio      = 0.34;   // Candle body/range minimum

// --- Entry Filters ---
input double   EMA_Zone_ATR   = 0.60;   // Dip detection zone width
input double   ATR_Filter     = 0.25;   // Min ATR vs average ratio
input double   D1_Tolerance   = 0.003;  // D1 trend alignment tolerance
input int      MaxPositions   = 4;      // Max concurrent positions

// --- Structure Filter ---
input bool     UseStructure   = true;   // Enable HH/HL structure check
input int      StructureBars  = 2;      // Bars to check structure

// --- Time Decay ---
input bool     UseTimeDecay   = true;   // Auto-close stale positions
input int      MaxHoldBars    = 30;     // Max H4 bars before auto-close

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 0.50;
input int      MagicNumber   = 330085;

CTrade trade;
int hW1Fast, hW1Slow, hD1EMA, hH4EMA, hATR;

// Track entry bar for time decay
struct PosInfo
{
   ulong  ticket;
   int    entryBarIndex;
};
PosInfo posInfos[];
int barCounter = 0;

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

void AddPosInfo(ulong ticket)
{
   int sz = ArraySize(posInfos);
   ArrayResize(posInfos, sz + 1);
   posInfos[sz].ticket = ticket;
   posInfos[sz].entryBarIndex = barCounter;
}

void RemovePosInfo(ulong ticket)
{
   for(int i = ArraySize(posInfos)-1; i >= 0; i--)
   {
      if(posInfos[i].ticket == ticket)
      {
         int last = ArraySize(posInfos) - 1;
         if(i < last)
            posInfos[i] = posInfos[last];
         ArrayResize(posInfos, last);
         break;
      }
   }
}

int GetPosEntryBar(ulong ticket)
{
   for(int i = 0; i < ArraySize(posInfos); i++)
      if(posInfos[i].ticket == ticket)
         return posInfos[i].entryBarIndex;
   return barCounter;  // fallback: treat as just opened
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

      // Time Decay: close if held too long
      if(UseTimeDecay)
      {
         int barsHeld = barCounter - GetPosEntryBar(ticket);
         if(barsHeld >= MaxHoldBars)
         {
            trade.PositionClose(ticket);
            RemovePosInfo(ticket);
            continue;
         }
      }

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

bool CheckStructure(int w1Dir)
{
   if(!UseStructure) return true;

   if(w1Dir == 1)
   {
      // BUY: require recent low is NOT lower than preceding lows (higher lows)
      double recentLow = iLow(_Symbol, PERIOD_H4, 1);
      for(int j = 2; j <= StructureBars; j++)
      {
         if(recentLow < iLow(_Symbol, PERIOD_H4, j))
            return false;
      }
   }
   else if(w1Dir == -1)
   {
      // SELL: require recent high is NOT higher than preceding highs (lower highs)
      double recentHigh = iHigh(_Symbol, PERIOD_H4, 1);
      for(int j = 2; j <= StructureBars; j++)
      {
         if(recentHigh > iHigh(_Symbol, PERIOD_H4, j))
            return false;
      }
   }
   return true;
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

void CleanupPosInfos()
{
   // Remove entries for positions that no longer exist
   for(int i = ArraySize(posInfos)-1; i >= 0; i--)
   {
      bool found = false;
      for(int j = PositionsTotal()-1; j >= 0; j--)
      {
         if(PositionGetSymbol(j) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetInteger(POSITION_TICKET) == (long)posInfos[i].ticket)
         {
            found = true;
            break;
         }
      }
      if(!found)
         RemovePosInfo(posInfos[i].ticket);
   }
}

void OnTick()
{
   int posCount = CountPositions();

   // Manage existing positions
   if(posCount > 0)
      ManageTrail();

   // Clean up stale position tracking
   CleanupPosInfos();

   // Check for new entries
   if(posCount >= MaxPositions) return;

   static datetime lastBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_H4, 0);
   if(currBar == lastBar) return;
   lastBar = currBar;
   barCounter++;

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

   // D1 filter
   double d1ema[1];
   if(CopyBuffer(hD1EMA, 0, 1, 1, d1ema) < 1) return;
   double d1Close = iClose(_Symbol, PERIOD_D1, 1);
   double d1Diff = (d1Close - d1ema[0]) / d1ema[0];

   if(w1Dir == 1 && d1Diff < -D1_Tolerance) return;
   if(w1Dir == -1 && d1Diff > D1_Tolerance) return;

   // ATR filter
   double atr[1];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;
   double avgATR = GetAvgATR();
   if(avgATR <= 0) return;
   if(atrVal < avgATR * ATR_Filter) return;

   // Structure filter
   if(!CheckStructure(w1Dir)) return;

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
         if(trade.Buy(lot, _Symbol, ask, sl, 0, "Alpha15 BUY"))
         {
            ulong ticket = trade.ResultOrder();
            if(ticket > 0)
               AddPosInfo(ticket);
         }
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
         if(trade.Sell(lot, _Symbol, bid, sl, 0, "Alpha15 SELL"))
         {
            ulong ticket = trade.ResultOrder();
            if(ticket > 0)
               AddPosInfo(ticket);
         }
      }
   }
}
//+------------------------------------------------------------------+
