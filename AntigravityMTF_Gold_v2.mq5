//+------------------------------------------------------------------+
//|                              AntigravityMTF_Gold_v2.mq5          |
//|            XAUUSD Simplified EA - 6-Component Scoring            |
//|            v2.3: +H1 BB Bounce component                        |
//|            Design: Judge's Verdict - Deterministic exits only    |
//+------------------------------------------------------------------+
// DESIGN RATIONALE:
// - 6 scoring components (max 12 points with H1 MA cross + BB bounce) + ADX gate
// - Fixed SL/TP (ATR-based, never modified after entry)
// - Signal Exit: H4 SMA50 slope reversal -> close at market
// - Stale Exit: 48h + profit >= 0
// - Weekend Close: Friday 20:00 server time
// - NO BE, NO trailing, NO chandelier, NO partial close
// - v2.1 CHANGES (Python WFA optimized, 72 combos tested):
//   - BuyOnly=true default (SELL PF=1.03 -> not worth taking)
//   - TP reduced 2.5->2.0 (tighter TP improves WR 51%->56%)
//   - Added OnTester() for MT5 optimizer (PF+DD+trades composite)
//   - WFA: 8/16 -> 12/16 pass, PF: 1.31 -> 1.50
// - v2.2 CHANGES (Optimizer-Ready):
//   - Promoted ADXThreshold, H4SlopePeriod, RSIMomLookback to inputs
//   - Added H1 EMA cross as optional 5th scoring component (+2pt)
//   - Added UseSignalExit toggle for optimizer
//   - Added optimization range comments for all key parameters
//   - OnTester() enhanced with win-rate component
//   - TradeStartHour/TradeEndHour with optimization ranges
// - v2.3 CHANGES (H1 BB Bounce):
//   - Added H1 Bollinger Band bounce as optional 6th scoring component (+1pt)
//   - Near lower band + turning up -> BUY; near upper band + turning down -> SELL
//   - Max score: 9 base + 2 (H1 MA) + 1 (BB) = 12
//+------------------------------------------------------------------+
//
// === MT5 OPTIMIZER INSTRUCTIONS ===
// 1. Open Strategy Tester (Ctrl+R)
// 2. Select this EA, XAUUSD, M15, Every tick
// 3. Set date: 2020-01-01 to 2024-06-30 (optimization period)
// 4. Forward: Custom, 2024-07-01 (out-of-sample validation)
// 5. Optimization: Fast genetic based algorithm
// 6. Optimization criterion: Custom max (OnTester)
// 7. Go to Inputs tab, check boxes for parameters to optimize
// 8. Set Start/Step/Stop values as noted in comments
// 9. Click Start
// 10. After completion, check Forward tab for OOS results
//
// === RECOMMENDED OPTIMIZATION PRIORITY ===
// Phase 1 (Core): SL_ATR_Multi, TP_ATR_Multi, MinEntryScore
// Phase 2 (Filters): ADXThreshold, TradeStartHour, TradeEndHour
// Phase 3 (Signals): H4SlopePeriod, RSIMomLookback, UseH1MACross, UseH1BBBounce
// Phase 4 (Mode): BuyOnly, UseSignalExit
//
// === PARAMETER OPTIMIZATION RANGES ===
// All ranges noted as (Start | Step | Stop) in input comments
//
//+------------------------------------------------------------------+
#property copyright "Antigravity Trading System"
#property version   "2.30"
#property description "XAUUSD v2.3: 6-Component Scoring, +H1 BB Bounce. MT5 genetic optimization support."

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| Input Parameters - Optimizable via MT5 Strategy Tester            |
//+------------------------------------------------------------------+
// Optimization ranges noted as (Start | Step | Stop)

input group "=== Risk Management ==="
input double RiskPercent      = 0.75;      // Risk % per trade
input double SL_ATR_Multi     = 1.5;       // SL = H4 ATR(14) x multiplier (1.0|0.25|2.5)
input double TP_ATR_Multi     = 2.0;       // TP = H4 ATR(14) x multiplier (1.5|0.25|3.0)

input group "=== Scoring ==="
input int    MinEntryScore    = 4;         // Min entry score (3|1|6)
input int    ADXThreshold     = 25;        // ADX gate threshold (20|5|35)
input int    H4SlopePeriod    = 20;        // H4 SMA slope lookback bars (10|5|30)
input int    RSIMomLookback   = 3;         // RSI momentum lookback bars (2|1|5)
input bool   UseH1MACross    = true;       // Add H1 MA cross component (+2pt)
input bool   UseH1BBBounce   = true;       // v2.3: Add H1 BB bounce component (+1pt, WFA+1, PF+0.34)

input group "=== Trade Mode ==="
input bool   BuyOnly          = true;      // BUY only mode (true|false)
input bool   UseSignalExit    = true;      // Signal exit enabled (true|false)
input int    MagicNumber      = 20260401;  // Magic number

input group "=== Filters ==="
input int    GMTOffset        = 2;         // Broker GMT offset (e.g. GMT+2)
input int    MaxSpread        = 50;        // Max spread (points)
input int    CooldownMinutes  = 480;       // Cooldown after SL (minutes)
input int    TradeStartHour   = 8;         // Trading start hour, server time (7|1|10)
input int    TradeEndHour     = 22;        // Trading end hour, server time (18|1|22)
input int    StaleTradeHours  = 48;        // Close if profit >= 0 after N hours

//+------------------------------------------------------------------+
//| Hardcoded Constants                                              |
//+------------------------------------------------------------------+
const int    H4_SMA_Period     = 50;       // H4 SMA(50) for trend slope
const int    H4_ADX_Period     = 14;       // H4 ADX(14)
const int    H1_RSI_Period     = 14;       // H1 RSI(14)
const int    H4_RSI_Period     = 14;       // H4 RSI(14)
const int    H4_ATR_Period     = 14;       // H4 ATR(14) for SL/TP
const int    H1_EMA_Fast       = 10;       // H1 EMA fast period for MA cross
const int    H1_EMA_Slow       = 30;       // H1 EMA slow period for MA cross
const int    FridayCloseHour   = 20;       // Weekend close hour (server)
const double MinLots           = 0.01;     // Minimum lot size
const double MaxLots           = 5.00;     // Maximum lot size
const double SlippagePoints    = 3.0;      // Slippage tolerance
const double MinSL_Points      = 200.0;    // Minimum SL in points
const double MaxSL_Points      = 1500.0;   // Maximum SL in points

//+------------------------------------------------------------------+
//| Global Variables                                                 |
//+------------------------------------------------------------------+
CTrade         trade;
int            h_h4_sma50;       // H4 SMA(50) handle
int            h_h4_adx;         // H4 ADX(14) handle
int            h_h1_rsi;         // H1 RSI(14) handle
int            h_h4_rsi;         // H4 RSI(14) handle
int            h_h4_atr;         // H4 ATR(14) handle
int            h_h1_ema_fast;    // H1 EMA(10) handle
int            h_h1_ema_slow;    // H1 EMA(30) handle
int            h_h1_bb;          // v2.3: H1 BB(20,2) handle
datetime       lastBarTime;      // Last processed M15 bar time
datetime       lastSLTime;       // Last SL hit time (for cooldown)

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Verify hedging account mode
   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE)
      != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("ERROR: This EA requires a hedging account. EA disabled.");
      return INIT_FAILED;
   }

   //--- Input validation
   if(ADXThreshold < 0 || ADXThreshold > 100)
   {
      Print("ERROR: ADXThreshold must be 0-100, got ", ADXThreshold);
      return INIT_PARAMETERS_INCORRECT;
   }
   if(H4SlopePeriod < 2 || H4SlopePeriod > 100)
   {
      Print("ERROR: H4SlopePeriod must be 2-100, got ", H4SlopePeriod);
      return INIT_PARAMETERS_INCORRECT;
   }
   if(RSIMomLookback < 1 || RSIMomLookback > 20)
   {
      Print("ERROR: RSIMomLookback must be 1-20, got ", RSIMomLookback);
      return INIT_PARAMETERS_INCORRECT;
   }
   if(MinEntryScore < 1 || MinEntryScore > 12)
   {
      Print("ERROR: MinEntryScore must be 1-12, got ", MinEntryScore);
      return INIT_PARAMETERS_INCORRECT;
   }
   if(TradeStartHour < 0 || TradeStartHour > 23 || TradeEndHour < 1 || TradeEndHour > 24)
   {
      Print("ERROR: Invalid trading hours: ", TradeStartHour, "-", TradeEndHour);
      return INIT_PARAMETERS_INCORRECT;
   }
   if(SL_ATR_Multi <= 0 || TP_ATR_Multi <= 0)
   {
      Print("ERROR: ATR multipliers must be > 0");
      return INIT_PARAMETERS_INCORRECT;
   }

   //--- Trade object setup
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints((int)SlippagePoints);

   //--- Fill policy detection
   ENUM_ORDER_TYPE_FILLING fillType = ORDER_FILLING_FOK;
   long fillMode = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fillMode & SYMBOL_FILLING_FOK) != 0)
      fillType = ORDER_FILLING_FOK;
   else if((fillMode & SYMBOL_FILLING_IOC) != 0)
      fillType = ORDER_FILLING_IOC;
   else
      fillType = ORDER_FILLING_RETURN;
   trade.SetTypeFilling(fillType);

   //--- Create indicator handles
   h_h4_sma50 = iMA(_Symbol, PERIOD_H4, H4_SMA_Period, 0, MODE_SMA, PRICE_CLOSE);
   h_h4_adx   = iADX(_Symbol, PERIOD_H4, H4_ADX_Period);
   h_h1_rsi   = iRSI(_Symbol, PERIOD_H1, H1_RSI_Period, PRICE_CLOSE);
   h_h4_rsi   = iRSI(_Symbol, PERIOD_H4, H4_RSI_Period, PRICE_CLOSE);
   h_h4_atr   = iATR(_Symbol, PERIOD_H4, H4_ATR_Period);
   h_h1_ema_fast = iMA(_Symbol, PERIOD_H1, H1_EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_h1_ema_slow = iMA(_Symbol, PERIOD_H1, H1_EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   h_h1_bb       = iBands(_Symbol, PERIOD_H1, 20, 0, 2.0, PRICE_CLOSE);

   //--- Validate all handles
   if(h_h4_sma50 == INVALID_HANDLE || h_h4_adx == INVALID_HANDLE ||
      h_h1_rsi == INVALID_HANDLE   || h_h4_rsi == INVALID_HANDLE ||
      h_h4_atr == INVALID_HANDLE   ||
      h_h1_ema_fast == INVALID_HANDLE || h_h1_ema_slow == INVALID_HANDLE ||
      h_h1_bb == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create indicator handles.");
      if(h_h4_sma50    != INVALID_HANDLE) IndicatorRelease(h_h4_sma50);
      if(h_h4_adx      != INVALID_HANDLE) IndicatorRelease(h_h4_adx);
      if(h_h1_rsi      != INVALID_HANDLE) IndicatorRelease(h_h1_rsi);
      if(h_h4_rsi      != INVALID_HANDLE) IndicatorRelease(h_h4_rsi);
      if(h_h4_atr      != INVALID_HANDLE) IndicatorRelease(h_h4_atr);
      if(h_h1_ema_fast != INVALID_HANDLE) IndicatorRelease(h_h1_ema_fast);
      if(h_h1_ema_slow != INVALID_HANDLE) IndicatorRelease(h_h1_ema_slow);
      if(h_h1_bb       != INVALID_HANDLE) IndicatorRelease(h_h1_bb);
      return INIT_FAILED;
   }

   //--- Restore lastSLTime from GlobalVariable (survives EA restart)
   string slKey = GVKey("lastSL");
   if(GlobalVariableCheck(slKey))
      lastSLTime = (datetime)(long)GlobalVariableGet(slKey);

   lastBarTime = 0;

   int maxScore = 9;
   if(UseH1MACross) maxScore += 2;
   if(UseH1BBBounce) maxScore += 1;
   Print("AntigravityMTF Gold v2.3 initialized (+H1 BB Bounce)");
   Print("  SL=ATR*", DoubleToString(SL_ATR_Multi, 2),
         " TP=ATR*", DoubleToString(TP_ATR_Multi, 2),
         " MinScore=", MinEntryScore, "/", maxScore,
         " ADX>=", ADXThreshold,
         " BuyOnly=", BuyOnly);
   Print("  H4SlopePeriod=", H4SlopePeriod,
         " RSIMomLookback=", RSIMomLookback,
         " H1MACross=", UseH1MACross,
         " H1BBBounce=", UseH1BBBounce,
         " SignalExit=", UseSignalExit);
   Print("  Hours=", TradeStartHour, "-", TradeEndHour,
         " Cooldown=", CooldownMinutes, "min",
         " Stale=", StaleTradeHours, "h",
         " GMTOffset=", GMTOffset);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(h_h4_sma50    != INVALID_HANDLE) IndicatorRelease(h_h4_sma50);
   if(h_h4_adx      != INVALID_HANDLE) IndicatorRelease(h_h4_adx);
   if(h_h1_rsi      != INVALID_HANDLE) IndicatorRelease(h_h1_rsi);
   if(h_h4_rsi      != INVALID_HANDLE) IndicatorRelease(h_h4_rsi);
   if(h_h4_atr      != INVALID_HANDLE) IndicatorRelease(h_h4_atr);
   if(h_h1_ema_fast != INVALID_HANDLE) IndicatorRelease(h_h1_ema_fast);
   if(h_h1_ema_slow != INVALID_HANDLE) IndicatorRelease(h_h1_ema_slow);
   if(h_h1_bb       != INVALID_HANDLE) IndicatorRelease(h_h1_bb);
}

//+------------------------------------------------------------------+
//| OnTester - Custom optimization criterion for MT5 Strategy Tester |
//| Composite score: PF (35pt) + DD (25pt) + Trades (15pt)          |
//|                + WinRate (15pt) + Profit (10pt)                  |
//| Rejects: <50 trades, >25% DD, PF<1.0                            |
//+------------------------------------------------------------------+
double OnTester()
{
   double pf     = TesterStatistics(STAT_PROFIT_FACTOR);
   double dd     = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);
   double trades = TesterStatistics(STAT_TRADES);
   double profit = TesterStatistics(STAT_PROFIT);
   double wins   = TesterStatistics(STAT_PROFIT_TRADES);

   //--- Hard filters: reject bad configurations
   if(trades < 50)  return -1000;
   if(dd > 25.0)    return -500;
   if(pf < 1.0)     return -100;

   //--- Win rate
   double winRate = (trades > 0) ? wins / trades : 0;

   //--- Composite score (0-100)
   double score = MathMin(pf, 4.0) / 4.0 * 35          // PF component (max 35)
                + MathMax(0, (25 - dd) / 25) * 25       // DD component (max 25)
                + MathMin(trades / 200, 1) * 15          // Trade count (max 15)
                + MathMin(winRate / 0.65, 1) * 15        // Win rate (max 15, cap at 65%)
                + (profit > 0 ? 10 : 0);                 // Profitability (max 10)

   return score;
}

//+------------------------------------------------------------------+
//| Expert tick function - Main Logic                                |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Manage open positions (Signal Exit, Stale, Weekend)
   //    These run on every tick for immediate execution
   ManageOpenPositions();

   //--- Weekend close: close all and block new entries
   if(IsWeekendClose())
   {
      CloseAllPositions();
      return;
   }

   //--- New M15 bar check (entry logic runs once per bar)
   datetime currentBar = iTime(_Symbol, PERIOD_M15, 0);
   if(currentBar == lastBarTime) return;
   lastBarTime = currentBar;

   //--- Pre-entry checks
   if(!IsTradeAllowed()) return;
   if(!CheckTimeFilter()) return;
   if(!CheckSpread())     return;
   if(!CheckDeadZone())   return;

   //--- Already have a position? No pyramiding in v2
   if(CountMyPositions() > 0) return;

   //--- Cooldown after SL
   if(lastSLTime > 0 && TimeCurrent() - lastSLTime < CooldownMinutes * 60)
      return;

   //--- Get H4 ATR for SL/TP calculation
   double atr = GetH4ATR();
   if(atr <= 0) return;

   //--- ADX Gate: must be >= threshold
   double adx = GetIndicatorValue(h_h4_adx, 0, 1);  // Main ADX line, shift 1
   if(adx < ADXThreshold)
      return;

   //--- Calculate entry scores
   int buyScore  = 0;
   int sellScore = 0;
   string buyReasons  = "";
   string sellReasons = "";

   //--- Component 1: H4 SMA50 Slope (3 points)
   int slopeDir = GetH4SMASlope();
   if(slopeDir > 0)       { buyScore  += 3; buyReasons  += "SMA50^3 "; }
   else if(slopeDir < 0)  { sellScore += 3; sellReasons += "SMA50v3 "; }

   //--- Component 2: H1 RSI Momentum (2 points)
   int rsiMom = GetH1RSIMomentum();
   if(rsiMom > 0)       { buyScore  += 2; buyReasons  += "RSImom^2 "; }
   else if(rsiMom < 0)  { sellScore += 2; sellReasons += "RSImomv2 "; }

   //--- Component 3: H4 RSI Alignment (1 point)
   int h4RsiAlign = GetH4RSIAlignment();
   if(h4RsiAlign > 0)       { buyScore  += 1; buyReasons  += "H4RSI^1 "; }
   else if(h4RsiAlign < 0)  { sellScore += 1; sellReasons += "H4RSIv1 "; }

   //--- Component 4: ADX gate (already checked above, not a score component)

   //--- Component 5: H1 EMA Cross (2 points, optional)
   if(UseH1MACross)
   {
      int maCross = GetH1MACross();
      if(maCross > 0)       { buyScore  += 2; buyReasons  += "H1MA^2 "; }
      else if(maCross < 0)  { sellScore += 2; sellReasons += "H1MAv2 "; }
   }

   //--- Component 6: H1 BB Bounce (+1pt)
   if(UseH1BBBounce)
   {
      double bbUpper = GetIndicatorValue(h_h1_bb, 1, 1);  // Upper band, shift 1
      double bbLower = GetIndicatorValue(h_h1_bb, 2, 1);  // Lower band, shift 1
      double h1Close = iClose(_Symbol, PERIOD_H1, 1);
      double h1PrevClose = iClose(_Symbol, PERIOD_H1, 2);

      if(bbUpper > 0 && bbLower > 0)
      {
         double bbWidth = bbUpper - bbLower;
         if(bbWidth > 0)
         {
            double bbPos = (h1Close - bbLower) / bbWidth;
            // Near lower band AND turning up -> BUY
            if(bbPos < 0.2 && h1Close > h1PrevClose) { buyScore += 1; buyReasons += "BBv1 "; }
            // Near upper band AND turning down -> SELL
            if(bbPos > 0.8 && h1Close < h1PrevClose) { sellScore += 1; sellReasons += "BBv1 "; }
         }
      }
   }

   //--- Entry decision
   int totalBuy  = buyScore;
   int totalSell = sellScore;

   bool canBuy  = (totalBuy  >= MinEntryScore && totalBuy  > totalSell);
   bool canSell = (totalSell >= MinEntryScore && totalSell > totalBuy);

   //--- BuyOnly filter
   if(BuyOnly) canSell = false;

   if(!canBuy && !canSell) return;

   //--- Calculate SL/TP distances
   double slDist = atr * SL_ATR_Multi;
   double tpDist = atr * TP_ATR_Multi;

   //--- Enforce min/max SL
   double slPoints = slDist / _Point;
   if(slPoints < MinSL_Points)
      slDist = MinSL_Points * _Point;
   else if(slPoints > MaxSL_Points)
      slDist = MaxSL_Points * _Point;

   //--- Max score for logging
   int maxScore = 9;
   if(UseH1MACross) maxScore += 2;
   if(UseH1BBBounce) maxScore += 1;

   //--- Execute trade
   if(canBuy)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl  = NormalizeDouble(ask - slDist, _Digits);
      double tp  = NormalizeDouble(ask + tpDist, _Digits);

      ValidateStopsDistance(ask, sl, tp, true);

      double lots = CalcLotSize(ask, slDist);

      //--- Store entry slope direction for Signal Exit
      string comment = "v23|B|" + IntegerToString(slopeDir);

      if(trade.Buy(lots, _Symbol, ask, sl, tp, comment))
      {
         //--- Store entry slope in GlobalVariable for persistence
         ulong dealTicket = trade.ResultDeal();
         if(dealTicket > 0)
            StoreEntrySlopeDir(dealTicket, slopeDir);

         Print("GOLD v2.3 BUY: lots=", DoubleToString(lots, 2),
               " ask=", DoubleToString(ask, _Digits),
               " SL=", DoubleToString(sl, _Digits),
               " TP=", DoubleToString(tp, _Digits),
               " score=", totalBuy, "/", maxScore,
               " ADX=", DoubleToString(adx, 1),
               " [", buyReasons, "]");
      }
      else
      {
         Print("GOLD v2.3 BUY FAILED: error=", GetLastError(),
               " retcode=", trade.ResultRetcode(),
               " comment=", trade.ResultComment());
      }
   }
   else if(canSell)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl  = NormalizeDouble(bid + slDist, _Digits);
      double tp  = NormalizeDouble(bid - tpDist, _Digits);

      ValidateStopsDistance(bid, sl, tp, false);

      double lots = CalcLotSize(bid, slDist);

      //--- Store entry slope direction for Signal Exit
      string comment = "v23|S|" + IntegerToString(slopeDir);

      if(trade.Sell(lots, _Symbol, bid, sl, tp, comment))
      {
         ulong dealTicket = trade.ResultDeal();
         if(dealTicket > 0)
            StoreEntrySlopeDir(dealTicket, slopeDir);

         Print("GOLD v2.3 SELL: lots=", DoubleToString(lots, 2),
               " bid=", DoubleToString(bid, _Digits),
               " SL=", DoubleToString(sl, _Digits),
               " TP=", DoubleToString(tp, _Digits),
               " score=", totalSell, "/", maxScore,
               " ADX=", DoubleToString(adx, 1),
               " [", sellReasons, "]");
      }
      else
      {
         Print("GOLD v2.3 SELL FAILED: error=", GetLastError(),
               " retcode=", trade.ResultRetcode(),
               " comment=", trade.ResultComment());
      }
   }
}

//+------------------------------------------------------------------+
//| OnTradeTransaction - Detect SL hits for cooldown                 |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   //--- Only process deal transactions
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

   //--- Select the deal
   ulong dealTicket = trans.deal;
   if(dealTicket == 0) return;
   if(!HistoryDealSelect(dealTicket)) return;

   //--- Only our EA's deals
   if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) return;
   if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) return;

   //--- Only exit deals
   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT) return;

   //--- Check if closed by SL
   ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(dealTicket, DEAL_REASON);
   if(reason == DEAL_REASON_SL)
   {
      lastSLTime = TimeCurrent();
      GlobalVariableSet(GVKey("lastSL"), (double)(long)lastSLTime);
      Print("GOLD v2.3: SL hit, cooldown ", CooldownMinutes, " minutes");
   }

   //--- Clean up stored slope GlobalVariable for closed position
   ulong posID = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
   if(posID > 0)
   {
      string slopeKey = GVKey("slope_" + IntegerToString(posID));
      if(GlobalVariableCheck(slopeKey))
         GlobalVariableDel(slopeKey);
   }
}

//+------------------------------------------------------------------+
//| Manage Open Positions: Signal Exit, Stale Exit                   |
//| Called on every tick. SL/TP are NEVER modified.                   |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long posType     = PositionGetInteger(POSITION_TYPE);
      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      double profit    = PositionGetDouble(POSITION_PROFIT)
                       + PositionGetDouble(POSITION_SWAP);

      //--- 1. Stale Exit: close if profit >= 0 after StaleTradeHours
      if(StaleTradeHours > 0)
      {
         double hours = (double)(TimeCurrent() - openTime) / 3600.0;
         if(hours >= StaleTradeHours && profit >= 0)
         {
            if(trade.PositionClose(ticket))
               Print("GOLD v2.3: Stale exit (", DoubleToString(hours, 1),
                     "h, profit=", DoubleToString(profit, 2), ")");
            continue;
         }
      }

      //--- 2. Signal Exit: H4 SMA50 slope reversed from entry direction
      //       Only active when UseSignalExit is enabled
      if(UseSignalExit)
      {
         int entrySlopeDir = ReadEntrySlopeDir(ticket);
         if(entrySlopeDir != 0)
         {
            int currentSlope = GetH4SMASlope();
            if(currentSlope != 0 && currentSlope != entrySlopeDir)
            {
               if(trade.PositionClose(ticket))
                  Print("GOLD v2.3: Signal exit - slope reversed (",
                        entrySlopeDir, " -> ", currentSlope,
                        ", profit=", DoubleToString(profit, 2), ")");
               continue;
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| SCORING COMPONENT 1: H4 SMA(50) Slope                           |
//| Returns: +1 (rising), -1 (falling), 0 (flat/error)              |
//| Slope = SMA50[1] - SMA50[H4SlopePeriod] over N H4 bars          |
//+------------------------------------------------------------------+
int GetH4SMASlope()
{
   double sma[];
   ArraySetAsSeries(sma, true);

   //--- Need bars at shift 1 and shift H4SlopePeriod
   if(CopyBuffer(h_h4_sma50, 0, 1, H4SlopePeriod + 1, sma) < H4SlopePeriod + 1)
      return 0;

   //--- sma[0] = shift 1 (most recent confirmed), sma[H4SlopePeriod] = older bar
   double slope = sma[0] - sma[H4SlopePeriod];

   if(slope > 0) return  1;  // Rising
   if(slope < 0) return -1;  // Falling
   return 0;
}

//+------------------------------------------------------------------+
//| SCORING COMPONENT 2: H1 RSI Momentum (2 points)                 |
//| BUY:  RSI(14) > 50 AND RSI[1] > RSI[1+RSIMomLookback]           |
//| SELL: RSI(14) < 50 AND RSI[1] < RSI[1+RSIMomLookback]           |
//+------------------------------------------------------------------+
int GetH1RSIMomentum()
{
   double rsi[];
   ArraySetAsSeries(rsi, true);

   //--- Need RSI at shift 1 and shift 1+RSIMomLookback
   if(CopyBuffer(h_h1_rsi, 0, 1, RSIMomLookback + 1, rsi) < RSIMomLookback + 1)
      return 0;

   double rsiNow  = rsi[0];                   // H1 RSI at shift 1
   double rsiPast = rsi[RSIMomLookback];       // H1 RSI at shift 1+N

   //--- BUY: RSI > 50 AND rising
   if(rsiNow > 50.0 && rsiNow > rsiPast)
      return 1;

   //--- SELL: RSI < 50 AND falling
   if(rsiNow < 50.0 && rsiNow < rsiPast)
      return -1;

   return 0;
}

//+------------------------------------------------------------------+
//| SCORING COMPONENT 3: H4 RSI Alignment (1 point)                 |
//| BUY:  H4 RSI in 50-75 range                                     |
//| SELL: H4 RSI in 25-50 range                                     |
//+------------------------------------------------------------------+
int GetH4RSIAlignment()
{
   double h4Rsi = GetIndicatorValue(h_h4_rsi, 0, 1);
   if(h4Rsi <= 0) return 0;

   if(h4Rsi >= 50.0 && h4Rsi <= 75.0) return  1;   // Bullish alignment
   if(h4Rsi >= 25.0 && h4Rsi <  50.0) return -1;   // Bearish alignment

   return 0;  // Extreme RSI (< 25 or > 75) -> no score
}

//+------------------------------------------------------------------+
//| SCORING COMPONENT 5: H1 EMA Cross (2 points)                    |
//| BUY:  H1 EMA(10) > H1 EMA(30)                                   |
//| SELL: H1 EMA(10) < H1 EMA(30)                                   |
//+------------------------------------------------------------------+
int GetH1MACross()
{
   double emaFast = GetIndicatorValue(h_h1_ema_fast, 0, 1);
   double emaSlow = GetIndicatorValue(h_h1_ema_slow, 0, 1);

   if(emaFast <= 0 || emaSlow <= 0) return 0;

   if(emaFast > emaSlow) return  1;   // Bullish: fast above slow
   if(emaFast < emaSlow) return -1;   // Bearish: fast below slow

   return 0;
}

//+------------------------------------------------------------------+
//| Get H4 ATR value (Wilder's smoothing, shift 1)                   |
//+------------------------------------------------------------------+
double GetH4ATR()
{
   return GetIndicatorValue(h_h4_atr, 0, 1);
}

//+------------------------------------------------------------------+
//| Calculate lot size based on risk percentage                      |
//+------------------------------------------------------------------+
double CalcLotSize(double entryPrice, double slDist)
{
   if(slDist <= 0) return MinLots;

   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * RiskPercent / 100.0;

   //--- Use MT5's built-in profit calculator for accurate risk
   double slPrice = entryPrice - slDist;
   double profitOrLoss = 0.0;

   if(!OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, entryPrice, slPrice, profitOrLoss))
   {
      //--- Fallback: estimate using USDJPY rate for JPY accounts
      double usdJpyRate = SymbolInfoDouble("USDJPY", SYMBOL_BID);
      if(usdJpyRate <= 0) usdJpyRate = 150.0;
      profitOrLoss = -((slDist / _Point / 100.0) * 100.0 * usdJpyRate);
   }

   double lossForOneLot = MathAbs(profitOrLoss);
   if(lossForOneLot <= 0) lossForOneLot = 1000.0;

   double lots = riskAmount / lossForOneLot;

   //--- Normalize to lot step
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(MinLots, MathMin(MaxLots, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Weekend Close Check                                              |
//+------------------------------------------------------------------+
bool IsWeekendClose()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   return (dt.day_of_week == 5 && dt.hour >= FridayCloseHour);
}

//+------------------------------------------------------------------+
//| Close all positions for this EA                                  |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
//| Time Filter: server hours TradeStartHour - TradeEndHour          |
//+------------------------------------------------------------------+
bool CheckTimeFilter()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   if(dt.hour < TradeStartHour || dt.hour >= TradeEndHour) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Dead Zone Filter: block hours 11-12 GMT                          |
//| Uses GMTOffset to convert server time to GMT                     |
//+------------------------------------------------------------------+
bool CheckDeadZone()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   int gmtHour = (dt.hour - GMTOffset + 24) % 24;
   if(gmtHour == 11 || gmtHour == 12) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Spread Check                                                     |
//+------------------------------------------------------------------+
bool CheckSpread()
{
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return (spread <= MaxSpread);
}

//+------------------------------------------------------------------+
//| Trade Allowed Check                                              |
//+------------------------------------------------------------------+
bool IsTradeAllowed()
{
   return MQLInfoInteger(MQL_TRADE_ALLOWED) &&
          TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) &&
          AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
}

//+------------------------------------------------------------------+
//| Count positions for this EA/Symbol                               |
//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Get single indicator value at buffer/shift                       |
//+------------------------------------------------------------------+
double GetIndicatorValue(int handle, int buffer, int shift)
{
   double val[];
   if(CopyBuffer(handle, buffer, shift, 1, val) <= 0) return 0;
   return val[0];
}

//+------------------------------------------------------------------+
//| Validate SL/TP against broker STOPS_LEVEL and FREEZE_LEVEL       |
//+------------------------------------------------------------------+
void ValidateStopsDistance(double price, double &sl, double &tp, bool isBuy)
{
   long stopsLevel  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double minDist   = MathMax((double)stopsLevel, (double)freezeLevel) * _Point;
   if(minDist <= 0) return;

   if(isBuy)
   {
      if(price - sl < minDist) sl = NormalizeDouble(price - minDist, _Digits);
      if(tp - price < minDist) tp = NormalizeDouble(price + minDist, _Digits);
   }
   else
   {
      if(sl - price < minDist) sl = NormalizeDouble(price + minDist, _Digits);
      if(price - tp < minDist) tp = NormalizeDouble(price - minDist, _Digits);
   }
}

//+------------------------------------------------------------------+
//| GlobalVariable key scoped by magic + symbol                      |
//+------------------------------------------------------------------+
string GVKey(string suffix)
{
   return "AGv23_" + IntegerToString(MagicNumber) + "_" + _Symbol + "_" + suffix;
}

//+------------------------------------------------------------------+
//| Store entry slope direction in GlobalVariable                    |
//| Key: slope_{positionID} = slopeDir (+1 or -1)                   |
//+------------------------------------------------------------------+
void StoreEntrySlopeDir(ulong dealTicket, int slopeDir)
{
   //--- Get position ID from the deal
   if(!HistoryDealSelect(dealTicket)) return;
   ulong posID = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
   if(posID == 0) return;

   string key = GVKey("slope_" + IntegerToString(posID));
   GlobalVariableSet(key, (double)slopeDir);
}

//+------------------------------------------------------------------+
//| Read entry slope direction from GlobalVariable or trade comment  |
//| Returns: +1, -1, or 0 if not found                              |
//+------------------------------------------------------------------+
int ReadEntrySlopeDir(ulong posTicket)
{
   //--- Try GlobalVariable first (survives restart)
   if(!PositionSelectByTicket(posTicket)) return 0;
   ulong posID = PositionGetInteger(POSITION_IDENTIFIER);
   if(posID > 0)
   {
      string key = GVKey("slope_" + IntegerToString(posID));
      if(GlobalVariableCheck(key))
      {
         int dir = (int)GlobalVariableGet(key);
         if(dir == 1 || dir == -1) return dir;
      }
   }

   //--- Fallback: parse from trade comment "v23|B|1" or "v22|S|-1"
   string comment = PositionGetString(POSITION_COMMENT);
   if(StringLen(comment) >= 5 &&
      (StringSubstr(comment, 0, 4) == "v23|" ||
       StringSubstr(comment, 0, 4) == "v22|" ||
       StringSubstr(comment, 0, 4) == "v21|" ||
       StringSubstr(comment, 0, 3) == "v2|"))
   {
      //--- Find last '|' and parse slope direction
      int lastPipe = -1;
      for(int i = StringLen(comment) - 1; i >= 0; i--)
      {
         if(StringGetCharacter(comment, i) == '|')
         {
            lastPipe = i;
            break;
         }
      }
      if(lastPipe >= 0 && lastPipe < StringLen(comment) - 1)
      {
         string slopeStr = StringSubstr(comment, lastPipe + 1);
         int dir = (int)StringToInteger(slopeStr);
         if(dir == 1 || dir == -1) return dir;
      }
   }

   return 0;
}
//+------------------------------------------------------------------+
