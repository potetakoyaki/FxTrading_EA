//+------------------------------------------------------------------+
//| GoldAlpha_v27.mq5 - High-Freq v12 Dip-Buy + Smart Filters      |
//| v12 base + D1 regime + W1 sep + EMA slope + Time Decay           |
//|                                                                  |
//| Optimization results (Python backtest, 10yr):                    |
//|   PF=2.23, T=554, WFA 5/8, DD=23.2%, WR=61.7%                  |
//| Key improvements over v12:                                       |
//|   - D1 slope filter: skip flat D1 EMA (ranging markets)          |
//|   - W1 EMA separation: skip weak W1 trends                      |
//|   - H4 EMA slope: require EMA direction alignment                |
//|   - Time decay: close after MAX_HOLD_BARS H4 bars               |
//|   - JPY account lot calculation fix                              |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "27.00"
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

// --- Risk/Exit ---
input double   SL_ATR_Mult   = 3.0;
input double   Trail_ATR     = 3.5;
input double   BE_ATR        = 1.0;
input double   RiskPct       = 1.0;   // Higher than v26 for JPY daily target
input double   BodyRatio     = 0.32;

// --- Entry Filters ---
input double   EMA_Zone_ATR  = 0.30;
input double   ATR_Filter    = 0.30;
input double   D1_Tolerance  = 0.007;
input int      MaxPositions  = 2;

// --- D1 Regime Filter ---
input int      D1_Slope_Bars  = 5;
input double   D1_Min_Slope   = 0.002;  // Skip when D1 EMA slope < this

// --- W1 EMA Separation ---
input double   W1_Min_Sep     = 0.005;  // Skip when W1 EMAs too close

// --- H4 EMA Slope Filter ---
input int      EMA_Slope_Bars = 5;      // Bars to check H4 EMA direction

// --- Time Decay Exit ---
input int      Max_Hold_Bars  = 30;     // Close after this many H4 bars

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 0.50;
input int      MagicNumber   = 330027;

CTrade trade;
int hW1Fast, hW1Slow, hD1EMA, hH4EMA, hATR;

// Track position open bar for time decay
struct PositionTrack
{
   ulong    ticket;
   datetime openBar;
   int      barCount;
   bool     active;
};
PositionTrack posTrack[];

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

   ArrayResize(posTrack, 0);
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
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0 || slDist <= 0) return MinLot;

   // JPY account fix: convert tick_value if needed
   string acctCcy = AccountInfoString(ACCOUNT_CURRENCY);
   if(acctCcy == "JPY" && tv < 10.0)
   {
      double usdjpy = SymbolInfoDouble("USDJPY", SYMBOL_BID);
      if(usdjpy <= 0) usdjpy = 150.0;
      tv *= usdjpy;
   }

   double ticks = slDist / ts;
   double riskPerLot = ticks * tv;
   if(riskPerLot <= 0) return MinLot;
   double lot = riskMoney / riskPerLot;
   lot = MathFloor(lot / 0.01) * 0.01;
   return MathMax(MinLot, MathMin(MaxLot, lot));
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
//| D1 Slope: abs(EMA_now - EMA_N_bars_ago) / EMA_N_bars_ago        |
//+------------------------------------------------------------------+
double GetD1Slope()
{
   double cur[], prev[];
   if(CopyBuffer(hD1EMA, 0, 1, 1, cur) < 1) return 0;
   if(CopyBuffer(hD1EMA, 0, D1_Slope_Bars + 1, 1, prev) < 1) return 0;
   if(prev[0] <= 0) return 0;
   return MathAbs(cur[0] - prev[0]) / prev[0];
}

//+------------------------------------------------------------------+
//| Time Decay Management                                            |
//+------------------------------------------------------------------+
void TrackNewPosition(ulong ticket)
{
   int size = ArraySize(posTrack);
   ArrayResize(posTrack, size + 1);
   posTrack[size].ticket = ticket;
   posTrack[size].openBar = iTime(_Symbol, PERIOD_H4, 0);
   posTrack[size].barCount = 0;
   posTrack[size].active = true;
}

void UpdateBarCounts()
{
   for(int i = ArraySize(posTrack) - 1; i >= 0; i--)
   {
      if(!posTrack[i].active) continue;

      // Check if position still exists
      bool found = false;
      for(int j = PositionsTotal() - 1; j >= 0; j--)
      {
         if(PositionGetSymbol(j) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetInteger(POSITION_TICKET) == (long)posTrack[i].ticket)
         {
            found = true;
            break;
         }
      }
      if(!found)
      {
         posTrack[i].active = false;
         continue;
      }

      posTrack[i].barCount++;

      // Time decay: close if held too long
      if(posTrack[i].barCount >= Max_Hold_Bars)
      {
         trade.PositionClose(posTrack[i].ticket);
         posTrack[i].active = false;
      }
   }
}

void CleanupTracks()
{
   int newSize = 0;
   for(int i = 0; i < ArraySize(posTrack); i++)
   {
      if(posTrack[i].active)
      {
         if(newSize != i)
            posTrack[newSize] = posTrack[i];
         newSize++;
      }
   }
   ArrayResize(posTrack, newSize);
}

//+------------------------------------------------------------------+
//| Trailing Stop Management                                         |
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

         // Break-even
         if(profit > BE_ATR * atrVal && currentSL < openPrice)
         {
            double beSL = NormalizeDouble(openPrice + 0.1 * atrVal, _Digits);
            trade.PositionModify(ticket, beSL, 0);
            continue;
         }

         // Trailing (after BE)
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

         // Break-even
         if(profit > BE_ATR * atrVal && currentSL > openPrice)
         {
            double beSL = NormalizeDouble(openPrice - 0.1 * atrVal, _Digits);
            trade.PositionModify(ticket, beSL, 0);
            continue;
         }

         // Trailing (after BE)
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
//| Dip Entry Checks (same as v12)                                   |
//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
//| Main OnTick                                                      |
//+------------------------------------------------------------------+
void OnTick()
{
   int posCount = CountPositions();

   // Manage existing positions
   if(posCount > 0)
      ManageTrail();

   // Check for new entries if under max
   if(posCount >= MaxPositions) return;

   static datetime lastBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_H4, 0);
   if(currBar == lastBar) return;
   lastBar = currBar;

   // Update time decay bar counts on new H4 bar
   UpdateBarCounts();
   CleanupTracks();

   // Day-of-week filter
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.day_of_week == 5 && dt.hour > 16) return;

   // === D1 REGIME FILTER ===
   double d1slope = GetD1Slope();
   if(d1slope < D1_Min_Slope) return;  // Skip ranging D1

   // === W1 TREND ===
   double w1Fast[1], w1Slow[1];
   if(CopyBuffer(hW1Fast, 0, 1, 1, w1Fast) < 1) return;
   if(CopyBuffer(hW1Slow, 0, 1, 1, w1Slow) < 1) return;
   int w1Dir = 0;
   if(w1Fast[0] > w1Slow[0]) w1Dir = 1;
   if(w1Fast[0] < w1Slow[0]) w1Dir = -1;
   if(w1Dir == 0) return;

   // === W1 EMA SEPARATION ===
   double w1Mid = (w1Fast[0] + w1Slow[0]) / 2.0;
   if(w1Mid > 0 && MathAbs(w1Fast[0] - w1Slow[0]) / w1Mid < W1_Min_Sep) return;

   // === D1 TOLERANCE ===
   double d1ema[1];
   if(CopyBuffer(hD1EMA, 0, 1, 1, d1ema) < 1) return;
   double d1Close = iClose(_Symbol, PERIOD_D1, 1);
   double d1Diff = (d1Close - d1ema[0]) / d1ema[0];
   if(w1Dir == 1 && d1Diff < -D1_Tolerance) return;
   if(w1Dir == -1 && d1Diff > D1_Tolerance) return;

   // === ATR FILTER ===
   double atr[1];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;
   double avgATR = GetAvgATR();
   if(avgATR <= 0) return;
   if(atrVal < avgATR * ATR_Filter) return;

   // === H4 EMA ===
   double h4ema[1];
   if(CopyBuffer(hH4EMA, 0, 1, 1, h4ema) < 1) return;

   // === H4 EMA SLOPE FILTER ===
   double h4emaPrev[1];
   if(CopyBuffer(hH4EMA, 0, EMA_Slope_Bars + 1, 1, h4emaPrev) < 1) return;
   double h4slope = h4ema[0] - h4emaPrev[0];
   if(w1Dir == 1 && h4slope < 0) return;   // EMA falling but trying to buy
   if(w1Dir == -1 && h4slope > 0) return;   // EMA rising but trying to sell

   // === ENTRY ===
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
         if(trade.Buy(lot, _Symbol, ask, sl, 0, "A27 BUY"))
         {
            // Track for time decay
            ulong ticket = trade.ResultDeal();
            if(ticket > 0) TrackNewPosition(ticket);
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
         if(trade.Sell(lot, _Symbol, bid, sl, 0, "A27 SELL"))
         {
            ulong ticket = trade.ResultDeal();
            if(ticket > 0) TrackNewPosition(ticket);
         }
      }
   }
}
//+------------------------------------------------------------------+
