//+------------------------------------------------------------------+
//| GoldRegime.mq5 - Multi-Strategy Regime-Switching EA              |
//| Strategy A: Asian Range Breakout (trend days)                    |
//| Strategy B: BB + RSI Mean Reversion (range days)                 |
//| Regime: ADX-based switching                                       |
//| Target: 2-5 trades/day, all market conditions                    |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

// --- Regime Detection ---
input int      ADX_Period       = 14;
input double   ADX_Trend_Thresh = 25.0;   // Above = trend mode
input double   ADX_Range_Thresh = 20.0;   // Below = range mode

// --- Session Breakout (Strategy A) ---
input int      Asian_Start_Hour = 0;      // Asian session start (server time)
input int      Asian_End_Hour   = 8;      // Asian session end
input int      Trade_Start_Hour = 8;      // London open
input int      Trade_End_Hour   = 20;     // Last entry hour
input double   Breakout_Buffer  = 0.5;    // ATR fraction above/below range
input double   BO_SL_ATR        = 1.5;    // SL in ATR multiples
input double   BO_RR_Ratio      = 2.0;    // Risk:Reward ratio for TP

// --- Mean Reversion (Strategy B) ---
input int      BB_Period        = 20;
input double   BB_Deviation     = 2.0;
input int      RSI_Period       = 14;
input double   RSI_OB           = 70.0;   // Overbought
input double   RSI_OS           = 30.0;   // Oversold
input double   MR_SL_ATR        = 1.0;    // SL for mean reversion
input double   MR_TP_BB_Mid     = 1.0;    // TP at BB midline (1.0 = exactly mid)

// --- Risk Management ---
input double   RiskPct          = 1.0;    // Risk per trade %
input double   MinLot           = 0.01;
input double   MaxLot           = 0.50;
input int      MaxPositions     = 2;
input double   MaxDailyLoss_Pct = 3.0;    // Daily loss limit %
input int      MagicNumber      = 330100;

// --- Trailing ---
input double   Trail_ATR        = 1.5;    // Trailing stop ATR
input double   BE_ATR           = 1.0;    // Break-even trigger ATR

CTrade trade;
int hADX, hATR, hBBUpper, hBBLower, hBBMid, hRSI, hATR_H1;

// Asian range tracking
double asianHigh, asianLow;
bool   asianRangeSet;
datetime lastAsianCalcDay;

// Daily tracking
double dailyStartBalance;
datetime lastDayReset;
int    dailyTradeCount;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);

   // H1 indicators for regime + breakout
   hADX    = iADX(_Symbol, PERIOD_H1, ADX_Period);
   hATR    = iATR(_Symbol, PERIOD_H1, 14);
   hATR_H1 = hATR;

   // M15 indicators for mean reversion
   hBBUpper = iBands(_Symbol, PERIOD_M15, BB_Period, 0, BB_Deviation, PRICE_CLOSE);
   hBBLower = hBBUpper;  // Same handle, different buffers
   hBBMid   = hBBUpper;
   hRSI     = iRSI(_Symbol, PERIOD_M15, RSI_Period, PRICE_CLOSE);

   if(hADX == INVALID_HANDLE || hATR == INVALID_HANDLE ||
      hBBUpper == INVALID_HANDLE || hRSI == INVALID_HANDLE)
   {
      Print("Indicator init failed");
      return INIT_FAILED;
   }

   asianRangeSet = false;
   lastAsianCalcDay = 0;
   dailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   lastDayReset = 0;
   dailyTradeCount = 0;

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hADX);
   IndicatorRelease(hATR);
   IndicatorRelease(hBBUpper);
   IndicatorRelease(hRSI);
}

//--- Helper functions ---
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
   return MathMax(MinLot, MathMin(MaxLot, lot));
}

bool IsDailyLossExceeded()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   datetime today = StringToTime(StringFormat("%04d.%02d.%02d", dt.year, dt.mon, dt.day));

   if(today != lastDayReset)
   {
      lastDayReset = today;
      dailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      dailyTradeCount = 0;
   }

   double currentBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double dailyLoss = dailyStartBalance - currentBalance;
   double maxLoss = dailyStartBalance * MaxDailyLoss_Pct / 100.0;

   return (dailyLoss >= maxLoss);
}

//--- Calculate Asian Session Range ---
void CalcAsianRange()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   datetime today = StringToTime(StringFormat("%04d.%02d.%02d", dt.year, dt.mon, dt.day));

   if(today == lastAsianCalcDay) return;  // Already calculated today

   // Only calculate after Asian session ends
   if(dt.hour < Asian_End_Hour) return;

   // Find Asian session bars
   datetime asianStart = today + Asian_Start_Hour * 3600;
   datetime asianEnd   = today + Asian_End_Hour * 3600;

   double high = -1, low = 999999;
   int bars = iBars(_Symbol, PERIOD_M15);
   if(bars < 100) return;

   for(int i = 1; i < 100; i++)
   {
      datetime barTime = iTime(_Symbol, PERIOD_M15, i);
      if(barTime >= asianStart && barTime < asianEnd)
      {
         double h = iHigh(_Symbol, PERIOD_M15, i);
         double l = iLow(_Symbol, PERIOD_M15, i);
         if(h > high) high = h;
         if(l < low) low = l;
      }
   }

   if(high > 0 && low < 999999 && (high - low) > _Point * 10)
   {
      asianHigh = high;
      asianLow  = low;
      asianRangeSet = true;
      lastAsianCalcDay = today;
   }
}

//--- Get current regime ---
int GetRegime()
{
   double adxVal[1], plusDI[1], minusDI[1];
   if(CopyBuffer(hADX, 0, 1, 1, adxVal) < 1) return 0;
   if(CopyBuffer(hADX, 1, 1, 1, plusDI) < 1) return 0;
   if(CopyBuffer(hADX, 2, 1, 1, minusDI) < 1) return 0;

   if(adxVal[0] >= ADX_Trend_Thresh)
   {
      if(plusDI[0] > minusDI[0]) return 1;   // Bullish trend
      else return -1;                         // Bearish trend
   }
   else if(adxVal[0] <= ADX_Range_Thresh)
   {
      return 2;  // Range mode
   }

   return 0;  // Ambiguous - no trade
}

//--- Strategy A: Asian Range Breakout ---
void CheckBreakout()
{
   if(!asianRangeSet) return;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour < Trade_Start_Hour || dt.hour >= Trade_End_Hour) return;
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.day_of_week == 5 && dt.hour > 16) return;

   // Use H1 bar close for confirmation
   static datetime lastBOBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_H1, 0);
   if(currBar == lastBOBar) return;
   lastBOBar = currBar;

   double atr[1];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;

   double buffer = Breakout_Buffer * atrVal;
   double h1Close = iClose(_Symbol, PERIOD_H1, 1);
   double h1Open  = iOpen(_Symbol, PERIOD_H1, 1);

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Bullish breakout: H1 close above Asian high + buffer
   if(h1Close > asianHigh + buffer && h1Close > h1Open)
   {
      int regime = GetRegime();
      if(regime == 1 || regime == 0)  // Trend up or neutral OK
      {
         double slDist = BO_SL_ATR * atrVal;
         double sl = NormalizeDouble(ask - slDist, _Digits);
         double tp = NormalizeDouble(ask + slDist * BO_RR_Ratio, _Digits);
         double lot = CalcLot(slDist);
         if(trade.Buy(lot, _Symbol, ask, sl, tp, "GR_BO_BUY"))
            dailyTradeCount++;
      }
   }

   // Bearish breakout: H1 close below Asian low - buffer
   if(h1Close < asianLow - buffer && h1Close < h1Open)
   {
      int regime = GetRegime();
      if(regime == -1 || regime == 0)  // Trend down or neutral OK
      {
         double slDist = BO_SL_ATR * atrVal;
         double sl = NormalizeDouble(bid + slDist, _Digits);
         double tp = NormalizeDouble(bid - slDist * BO_RR_Ratio, _Digits);
         double lot = CalcLot(slDist);
         if(trade.Sell(lot, _Symbol, bid, sl, tp, "GR_BO_SELL"))
            dailyTradeCount++;
      }
   }
}

//--- Strategy B: BB + RSI Mean Reversion ---
void CheckMeanReversion()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour < Trade_Start_Hour || dt.hour >= Trade_End_Hour) return;
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;
   if(dt.day_of_week == 5 && dt.hour > 16) return;

   // Only in range regime
   int regime = GetRegime();
   if(regime != 2) return;

   // M15 bar close
   static datetime lastMRBar = 0;
   datetime currBar = iTime(_Symbol, PERIOD_M15, 0);
   if(currBar == lastMRBar) return;
   lastMRBar = currBar;

   double bbUpper[1], bbLower[1], bbMid[1], rsi[1], atr[1];
   if(CopyBuffer(hBBUpper, 1, 1, 1, bbUpper) < 1) return;  // Upper band
   if(CopyBuffer(hBBUpper, 2, 1, 1, bbLower) < 1) return;  // Lower band
   if(CopyBuffer(hBBUpper, 0, 1, 1, bbMid) < 1) return;    // Middle band
   if(CopyBuffer(hRSI, 0, 1, 1, rsi) < 1) return;
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];
   if(atrVal < _Point) return;

   double m15Close = iClose(_Symbol, PERIOD_M15, 1);
   double m15Open  = iOpen(_Symbol, PERIOD_M15, 1);
   double m15Low   = iLow(_Symbol, PERIOD_M15, 1);
   double m15High  = iHigh(_Symbol, PERIOD_M15, 1);

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // BUY: Price touched/crossed BB lower + RSI oversold + bullish candle
   if(m15Low <= bbLower[0] && m15Close > bbLower[0] && m15Close > m15Open && rsi[0] < RSI_OS)
   {
      double slDist = MR_SL_ATR * atrVal;
      double sl = NormalizeDouble(ask - slDist, _Digits);
      double tp = NormalizeDouble(bbMid[0] * MR_TP_BB_Mid, _Digits);
      if(tp <= ask) tp = NormalizeDouble(ask + slDist, _Digits);
      double lot = CalcLot(slDist);
      if(trade.Buy(lot, _Symbol, ask, sl, tp, "GR_MR_BUY"))
         dailyTradeCount++;
   }

   // SELL: Price touched/crossed BB upper + RSI overbought + bearish candle
   if(m15High >= bbUpper[0] && m15Close < bbUpper[0] && m15Close < m15Open && rsi[0] > RSI_OB)
   {
      double slDist = MR_SL_ATR * atrVal;
      double sl = NormalizeDouble(bid + slDist, _Digits);
      double tp = NormalizeDouble(bbMid[0] * MR_TP_BB_Mid, _Digits);
      if(tp >= bid) tp = NormalizeDouble(bid - slDist, _Digits);
      double lot = CalcLot(slDist);
      if(trade.Sell(lot, _Symbol, bid, sl, tp, "GR_MR_SELL"))
         dailyTradeCount++;
   }
}

//--- Manage trailing stops ---
void ManagePositions()
{
   double atr[1];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   double atrVal = atr[0];

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      long posType = PositionGetInteger(POSITION_TYPE);
      ulong ticket = PositionGetInteger(POSITION_TICKET);
      string comment = PositionGetString(POSITION_COMMENT);

      // Only trail breakout trades (MR trades use fixed TP)
      if(StringFind(comment, "BO") < 0) continue;

      if(posType == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profit = bid - openPrice;

         // Break-even
         if(profit > BE_ATR * atrVal && currentSL < openPrice)
         {
            double beSL = NormalizeDouble(openPrice + 0.1 * atrVal, _Digits);
            trade.PositionModify(ticket, beSL, PositionGetDouble(POSITION_TP));
            continue;
         }

         // Trail
         if(currentSL >= openPrice)
         {
            double newSL = NormalizeDouble(bid - Trail_ATR * atrVal, _Digits);
            if(newSL > currentSL + _Point * 10)
               trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profit = openPrice - ask;

         if(profit > BE_ATR * atrVal && (currentSL > openPrice || currentSL == 0))
         {
            double beSL = NormalizeDouble(openPrice - 0.1 * atrVal, _Digits);
            trade.PositionModify(ticket, beSL, PositionGetDouble(POSITION_TP));
            continue;
         }

         if(currentSL <= openPrice && currentSL > 0)
         {
            double newSL = NormalizeDouble(ask + Trail_ATR * atrVal, _Digits);
            if(newSL < currentSL - _Point * 10)
               trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
         }
      }
   }
}

//--- Main ---
void OnTick()
{
   // Daily loss circuit breaker
   if(IsDailyLossExceeded()) return;

   // Manage existing positions
   int posCount = CountPositions();
   if(posCount > 0)
      ManagePositions();

   // Max positions check
   if(posCount >= MaxPositions) return;

   // Weekend check
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return;

   // Calculate Asian range each day
   CalcAsianRange();

   // Run strategies
   CheckBreakout();
   CheckMeanReversion();
}
//+------------------------------------------------------------------+
