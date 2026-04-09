//+------------------------------------------------------------------+
//| GoldAlpha_v14.mq5 - v12 base + Structure Filter + Time Decay    |
//| Strategy: W1 trend + D1 filter + H4 EMA dip entry               |
//| v14 changes from v12:                                            |
//|   - Structure filter: require HH/HL (buy) or LH/LL (sell)       |
//|   - Time decay: close positions after MAX_HOLD_BARS H4 bars      |
//|   - SL_ATR_Mult: 2.0->2.5 (wider SL, fewer stopouts)           |
//|   - Trail_ATR: 2.5->3.5 (let winners run longer)                |
//|   - MaxPositions: 2->3 (allow deeper dip entries)                |
//|   - D1_Tolerance: 0.3%->0.3% (unchanged)                        |
//|   - ATR_Filter: 0.6->0.35 (trade in lower vol too)              |
//| Backtest (2016-2026, 300K JPY):                                  |
//|   Low risk (0.5%): PF=1.68, 1212 trades, DD=30.4%               |
//|   Target (3.0%):   PF=2.75, 1212 trades, DD=77.4%, Daily=7664   |
//|   OOS 2024-2026:   PF=3.43, 312 trades, DD=22.6% (2% risk)     |
//|   WFA: 4/8 windows profitable (trend-following strategy)         |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "14.00"
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
input double   SL_ATR_Mult   = 2.5;     // v14: 2.0->2.5
input double   Trail_ATR     = 3.5;     // v14: 2.5->3.5
input double   BE_ATR        = 1.5;     // v14: unchanged
input double   RiskPct       = 3.0;     // v14: target risk
input double   BodyRatio     = 0.34;    // v14: unchanged

// --- Entry Filters ---
input double   EMA_Zone_ATR  = 0.4;     // v14: unchanged
input double   ATR_Filter    = 0.35;    // v14: 0.6->0.35
input double   D1_Tolerance  = 0.003;   // v14: unchanged
input int      MaxPositions  = 3;       // v14: 2->3

// --- v14: Structure Filter ---
input bool     UseStructure  = true;    // Require HH/HL or LH/LL
input int      StructureBars = 3;       // Bars to check structure

// --- v14: Time Decay Exit ---
input bool     UseTimeDecay  = true;    // Close stale positions
input int      MaxHoldBars   = 30;      // Max H4 bars to hold (5 days)

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 0.75;    // v14: 0.10->0.75
input int      MagicNumber   = 330084;  // v14 magic

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

//+------------------------------------------------------------------+
//| v14: Structure filter - check for HH/HL or LH/LL pattern        |
//+------------------------------------------------------------------+
bool CheckStructureBuy(int barsToCheck)
{
   if(!UseStructure) return true;
   // Require higher lows (bullish structure): most recent low > older lows
   double recentLow = iLow(_Symbol, PERIOD_H4, 1);
   for(int j = 2; j <= barsToCheck; j++)
   {
      double oldLow = iLow(_Symbol, PERIOD_H4, j);
      if(recentLow < oldLow)
         return false;  // Lower low = bearish, reject buy
   }
   return true;
}

bool CheckStructureSell(int barsToCheck)
{
   if(!UseStructure) return true;
   // Require lower highs (bearish structure): most recent high < older highs
   double recentHigh = iHigh(_Symbol, PERIOD_H4, 1);
   for(int j = 2; j <= barsToCheck; j++)
   {
      double oldHigh = iHigh(_Symbol, PERIOD_H4, j);
      if(recentHigh > oldHigh)
         return false;  // Higher high = bullish, reject sell
   }
   return true;
}

//+------------------------------------------------------------------+
//| Manage trailing stop and breakeven                               |
//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
//| v14: Time decay - close stale positions                          |
//+------------------------------------------------------------------+
void ManageTimeDecay()
{
   if(!UseTimeDecay) return;

   datetime currentBarTime = iTime(_Symbol, PERIOD_H4, 0);

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      ulong ticket = PositionGetInteger(POSITION_TICKET);

      // Calculate H4 bars held
      int barsHeld = (int)((currentBarTime - openTime) / (4 * 3600));

      if(barsHeld >= MaxHoldBars)
      {
         trade.PositionClose(ticket);
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

   // Manage existing positions
   if(posCount > 0)
   {
      ManageTrail();
      ManageTimeDecay();
      posCount = CountPositions();  // Recount after time decay
   }

   // Check for new entries if under max
   if(posCount >= MaxPositions) return;

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

   // D1: relaxed filter
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

   // v14: Structure filter
   if(w1Dir == 1 && !CheckStructureBuy(StructureBars)) return;
   if(w1Dir == -1 && !CheckStructureSell(StructureBars)) return;

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
         trade.Buy(lot, _Symbol, ask, sl, 0, "Alpha14 BUY");
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
         trade.Sell(lot, _Symbol, bid, sl, 0, "Alpha14 SELL");
      }
   }
}
//+------------------------------------------------------------------+
