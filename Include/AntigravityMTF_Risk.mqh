//+------------------------------------------------------------------+
//|                                          AntigravityMTF_Risk.mqh |
//|                           Cascading Risk & Position Sizing Module |
//|                          Optimized for XAUUSD (Gold) EA Trading  |
//|                                                                  |
//| Inspired by EarnForex PositionSizer patterns.                    |
//| 10-stage cascading validation with DD escalation, Kelly sizing,  |
//| equity curve filtering, and GlobalVariable persistence.          |
//+------------------------------------------------------------------+
#property copyright "AntigravityMTF"
#property link      ""
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Constants                                                         |
//+------------------------------------------------------------------+
#define RISK_MAX_HISTORY       50    // Circular buffer size for trade results
#define RISK_MAX_EQUITY_HIST   50    // Circular buffer size for equity snapshots
#define RISK_GV_PREFIX         "AGRM_"

// DD Escalation levels (v9.3 proven thresholds)
#define DD_LEVEL_0_THRESHOLD   6.0   // Normal → Half risk
#define DD_LEVEL_1_THRESHOLD   10.0  // Half → Quarter risk
#define DD_LEVEL_2_THRESHOLD   15.0  // Quarter with higher min_score
#define DD_LEVEL_3_THRESHOLD   20.0  // Quarter with max min_score

// DD risk multipliers per level
#define DD_MULT_LEVEL_0        1.0   // Normal
#define DD_MULT_LEVEL_1        0.5   // 6-10%
#define DD_MULT_LEVEL_2        0.25  // 10-15%
#define DD_MULT_LEVEL_3        0.25  // 15-20%
#define DD_MULT_LEVEL_4        0.25  // >20%

// MinScore adjustments per DD level (v9.3)
#define DD_MINSCORE_ADJ_0      0     // Normal
#define DD_MINSCORE_ADJ_1      11    // 6-10%
#define DD_MINSCORE_ADJ_2      13    // 10-15%
#define DD_MINSCORE_ADJ_3      16    // 15-20%
#define DD_MINSCORE_ADJ_4      18    // >20%

// Kelly defaults
#define KELLY_DEFAULT_LOOKBACK   30
#define KELLY_DEFAULT_FRACTION   0.5
#define KELLY_DEFAULT_MIN_RISK   0.1
#define KELLY_DEFAULT_MAX_RISK   1.5

// Equity curve filter
#define EQUITY_FILTER_MA_PERIOD  10
#define EQUITY_FILTER_REDUCTION  0.5

// Gold-specific constants
#define GOLD_TYPICAL_LOT_STEP    0.01
#define GOLD_MIN_SL_POINTS       50    // Minimum 50 points SL for Gold
#define GOLD_MAX_SL_POINTS       5000  // Maximum 5000 points SL for Gold

//+------------------------------------------------------------------+
//| Rejection reason enum for diagnostics                             |
//+------------------------------------------------------------------+
enum ENUM_RISK_REJECT
{
   RISK_REJECT_NONE = 0,              // No rejection
   RISK_REJECT_SYSTEM,                // Stage 1: System validation failed
   RISK_REJECT_SPREAD,                // Stage 2: Spread too wide
   RISK_REJECT_SLTP,                  // Stage 3: SL/TP invalid
   RISK_REJECT_RISK_ZERO,             // Stage 4: Risk computed to zero
   RISK_REJECT_KELLY_ZERO,            // Stage 5: Kelly risk is zero
   RISK_REJECT_EQUITY_CURVE,          // Stage 6: Equity curve filter
   RISK_REJECT_REGIME,                // Stage 7: Regime filter
   RISK_REJECT_DD_REDUCTION,          // Stage 8: DD reduction to zero
   RISK_REJECT_PORTFOLIO,             // Stage 9: Portfolio risk exceeded
   RISK_REJECT_CLAMP_ZERO,            // Stage 10: Clamped to zero
   RISK_REJECT_DAILY_LIMIT,           // Circuit breaker: daily loss limit
   RISK_REJECT_COOLDOWN               // Cooldown active
};

//+------------------------------------------------------------------+
//| CRiskManager class                                                |
//+------------------------------------------------------------------+
class CRiskManager
{
private:
   //--- Configuration (set via Init)
   double         m_riskPct;           // Base risk percentage
   double         m_maxLots;           // Maximum lot size
   double         m_minLots;           // Minimum lot size
   double         m_maxDD_Pct;         // Max drawdown % for quarter risk
   double         m_ddHalfPct;         // DD % for half risk
   double         m_dailyMaxLossPct;   // Daily max loss as % of balance

   //--- Runtime state: Drawdown tracking
   double         m_peakBalance;       // Highest balance recorded
   double         m_currentDD;         // Current drawdown %

   //--- Runtime state: Daily loss tracking
   double         m_dailyPnL;          // Accumulated daily PnL (account currency)
   int            m_lastDay;           // Day of month for daily reset
   bool           m_dailyLimitHit;     // Circuit breaker flag

   //--- Runtime state: Cooldown
   datetime       m_cooldownUntil;     // Cooldown expiry time

   //--- Runtime state: Trade history (circular buffer)
   double         m_tradeResults[RISK_MAX_HISTORY];  // PnL of each trade
   int            m_tradeCount;        // Total trades recorded
   int            m_tradeIndex;        // Current write position

   //--- Runtime state: Equity curve (circular buffer)
   double         m_equityHistory[RISK_MAX_EQUITY_HIST];  // Equity snapshot at each trade close
   int            m_equityCount;       // Total equity snapshots
   int            m_equityIndex;       // Current write position

   //--- Broker info (cached)
   double         m_lotStep;           // Symbol lot step
   int            m_lotStepDigits;     // Digits for lot step rounding
   double         m_brokerMinLot;      // Broker's minimum lot
   double         m_brokerMaxLot;      // Broker's maximum lot

   //--- Diagnostics
   ENUM_RISK_REJECT m_lastRejectReason;
   string         m_lastRejectMsg;
   int            m_magicNumber;       // For GV scoping
   string         m_symbol;            // For GV scoping

   //--- Internal helpers
   string         GVKey(string suffix);
   double         RoundDown(double value, double step);
   int            CalcLotStepDigits(double step);
   void           CacheBrokerInfo(string symbol);
   double         CalcRiskAmountFromSL(string symbol, ENUM_ORDER_TYPE order_type,
                                       double entry_price, double sl_price, double lot_size);
   void           SetReject(ENUM_RISK_REJECT reason, string msg);
   double         GetEquitySMA(int period);

public:
   //--- Constructor / Destructor
                  CRiskManager();
                 ~CRiskManager();

   //--- Initialization
   bool           Init(double risk_pct, double max_lots, double min_lots,
                       double max_dd_pct, double dd_half_pct, double daily_max_loss_pct);
   void           SetMagicNumber(int magic) { m_magicNumber = magic; }
   void           SetSymbol(string symbol)  { m_symbol = symbol; }

   //--- Main entry point: 10-stage cascading validation
   double         CalcLotSize(string symbol, ENUM_ORDER_TYPE order_type,
                              double entry_price, double sl_price, double tp_price);

   //--- Individual validation stages (public for testing)
   bool           ValidateSystem();
   bool           ValidateSpread(int max_spread);
   bool           ValidateSLTP(double sl_dist, double tp_dist);
   double         CalcBaseRisk();
   double         CalcKellyRisk(int lookback = KELLY_DEFAULT_LOOKBACK,
                                double fraction = KELLY_DEFAULT_FRACTION,
                                double min_risk = KELLY_DEFAULT_MIN_RISK,
                                double max_risk = KELLY_DEFAULT_MAX_RISK);
   double         ApplyEquityCurveFilter(double lot, int ma_period = EQUITY_FILTER_MA_PERIOD);
   double         ApplyRegimeMultiplier(double lot, int regime);
   double         ApplyDDReduction(double lot);
   double         ApplyPortfolioRisk(double lot);
   double         FinalClamp(double lot);

   //--- Drawdown tracking
   void           UpdatePeakBalance();
   double         GetCurrentDD();
   int            GetDDEscalationLevel();
   int            GetMinScoreAdjustment();

   //--- Daily loss tracking
   void           OnNewDay();
   void           AddTradeResult(double pnl);
   bool           IsDailyLimitHit();
   double         GetDailyPnL();

   //--- Cooldown management
   void           StartCooldown(int minutes);
   bool           IsInCooldown();
   int            CooldownRemaining();

   //--- Statistics
   double         GetWinRate(int lookback);
   double         GetAvgWin(int lookback);
   double         GetAvgLoss(int lookback);
   double         GetExpectancy(int lookback);

   //--- Persistence
   bool           SaveState();
   bool           LoadState();

   //--- Diagnostics
   ENUM_RISK_REJECT GetLastRejectReason() { return m_lastRejectReason; }
   string         GetLastRejectMsg()      { return m_lastRejectMsg; }
   int            GetTradeCount()         { return m_tradeCount; }
};

//+------------------------------------------------------------------+
//| Constructor                                                       |
//+------------------------------------------------------------------+
CRiskManager::CRiskManager()
{
   m_riskPct         = 1.0;
   m_maxLots         = 1.0;
   m_minLots         = 0.01;
   m_maxDD_Pct       = 6.0;
   m_ddHalfPct       = 2.5;
   m_dailyMaxLossPct = 2.0;

   m_peakBalance     = 0;
   m_currentDD       = 0;

   m_dailyPnL        = 0;
   m_lastDay         = 0;
   m_dailyLimitHit   = false;

   m_cooldownUntil   = 0;

   m_tradeCount      = 0;
   m_tradeIndex      = 0;

   m_equityCount     = 0;
   m_equityIndex     = 0;

   m_lotStep         = GOLD_TYPICAL_LOT_STEP;
   m_lotStepDigits   = 2;
   m_brokerMinLot    = 0.01;
   m_brokerMaxLot    = 100.0;

   m_lastRejectReason = RISK_REJECT_NONE;
   m_lastRejectMsg    = "";
   m_magicNumber      = 0;
   m_symbol           = "";

   ArrayInitialize(m_tradeResults, 0.0);
   ArrayInitialize(m_equityHistory, 0.0);
}

//+------------------------------------------------------------------+
//| Destructor                                                        |
//+------------------------------------------------------------------+
CRiskManager::~CRiskManager()
{
   SaveState();
}

//+------------------------------------------------------------------+
//| Init - configure risk parameters                                  |
//+------------------------------------------------------------------+
bool CRiskManager::Init(double risk_pct, double max_lots, double min_lots,
                        double max_dd_pct, double dd_half_pct, double daily_max_loss_pct)
{
   // Validate inputs
   if(risk_pct <= 0 || risk_pct > 100)
   {
      Print("[RiskManager] ERROR: risk_pct out of range (0,100]: ", risk_pct);
      return false;
   }
   if(max_lots <= 0)
   {
      Print("[RiskManager] ERROR: max_lots must be > 0: ", max_lots);
      return false;
   }
   if(min_lots <= 0)
   {
      Print("[RiskManager] ERROR: min_lots must be > 0: ", min_lots);
      return false;
   }
   if(min_lots > max_lots)
   {
      Print("[RiskManager] ERROR: min_lots (", min_lots, ") > max_lots (", max_lots, ")");
      return false;
   }
   if(max_dd_pct <= 0 || max_dd_pct > 100)
   {
      Print("[RiskManager] ERROR: max_dd_pct out of range: ", max_dd_pct);
      return false;
   }
   if(dd_half_pct <= 0 || dd_half_pct >= max_dd_pct)
   {
      Print("[RiskManager] ERROR: dd_half_pct must be in (0, max_dd_pct): ", dd_half_pct);
      return false;
   }
   if(daily_max_loss_pct <= 0 || daily_max_loss_pct > 100)
   {
      Print("[RiskManager] ERROR: daily_max_loss_pct out of range: ", daily_max_loss_pct);
      return false;
   }

   m_riskPct         = risk_pct;
   m_maxLots         = max_lots;
   m_minLots         = min_lots;
   m_maxDD_Pct       = max_dd_pct;
   m_ddHalfPct       = dd_half_pct;
   m_dailyMaxLossPct = daily_max_loss_pct;

   // Auto-detect symbol if not set
   if(m_symbol == "")
      m_symbol = _Symbol;

   // Cache broker info for this symbol
   CacheBrokerInfo(m_symbol);

   // Clamp min/max lots to broker limits
   m_minLots = MathMax(m_minLots, m_brokerMinLot);
   m_maxLots = MathMin(m_maxLots, m_brokerMaxLot);

   // Initialize peak balance
   m_peakBalance = AccountInfoDouble(ACCOUNT_BALANCE);

   // Initialize daily tracker
   MqlDateTime dt;
   TimeCurrent(dt);
   m_lastDay = dt.day;

   // Attempt to restore persisted state
   LoadState();

   Print("[RiskManager] Initialized: Risk=", DoubleToString(m_riskPct, 2), "%",
         " Lots=[", DoubleToString(m_minLots, m_lotStepDigits), "-",
         DoubleToString(m_maxLots, m_lotStepDigits), "]",
         " DD_Half=", DoubleToString(m_ddHalfPct, 1), "%",
         " DD_Max=", DoubleToString(m_maxDD_Pct, 1), "%",
         " DailyLimit=", DoubleToString(m_dailyMaxLossPct, 1), "%",
         " Symbol=", m_symbol,
         " Magic=", m_magicNumber,
         " LotStep=", DoubleToString(m_lotStep, m_lotStepDigits),
         " PeakBal=", DoubleToString(m_peakBalance, 2));

   return true;
}

//+------------------------------------------------------------------+
//| CalcLotSize - Main entry point with 10-stage cascading validation |
//|                                                                   |
//| Each stage can reject the trade by returning 0.0. Stages execute  |
//| sequentially; if any stage fails, subsequent stages are skipped.  |
//+------------------------------------------------------------------+
double CRiskManager::CalcLotSize(string symbol, ENUM_ORDER_TYPE order_type,
                                 double entry_price, double sl_price, double tp_price)
{
   m_lastRejectReason = RISK_REJECT_NONE;
   m_lastRejectMsg    = "";

   // Cache broker info for this symbol
   CacheBrokerInfo(symbol);

   //--- Pre-check: Circuit breaker
   if(IsDailyLimitHit())
   {
      SetReject(RISK_REJECT_DAILY_LIMIT,
                StringFormat("Daily loss limit hit: PnL=%.2f, Limit=%.2f%%",
                             m_dailyPnL, m_dailyMaxLossPct));
      return 0.0;
   }

   //--- Pre-check: Cooldown
   if(IsInCooldown())
   {
      SetReject(RISK_REJECT_COOLDOWN,
                StringFormat("Cooldown active: %d minutes remaining", CooldownRemaining()));
      return 0.0;
   }

   //--- Stage 1: System validation
   if(!ValidateSystem())
      return 0.0;

   //--- Stage 2: Spread validation
   int maxSpread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD) * 3;
   // For Gold, use a reasonable max spread based on typical conditions.
   // Caller can override by calling ValidateSpread() directly before CalcLotSize().
   // Here we just do a sanity check: spread must not exceed 300 points (extreme news).
   int currentSpread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   if(currentSpread > 300)
   {
      if(!ValidateSpread(300))
         return 0.0;
   }

   //--- Stage 3: SL/TP distance validation
   double sl_dist = MathAbs(entry_price - sl_price);
   double tp_dist = (tp_price > 0) ? MathAbs(tp_price - entry_price) : 0;

   // Convert to points for validation
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0) point = 0.01; // Gold fallback
   double sl_points = sl_dist / point;
   double tp_points = (tp_dist > 0) ? tp_dist / point : 0;

   if(!ValidateSLTP(sl_points, tp_points))
      return 0.0;

   //--- Stage 4: Base risk calculation (with DD adjustment)
   double riskPct = CalcBaseRisk();
   if(riskPct <= 0)
   {
      SetReject(RISK_REJECT_RISK_ZERO, "Base risk computed to zero after DD adjustment");
      return 0.0;
   }

   //--- Stage 5: Kelly criterion risk override
   double kellyRisk = CalcKellyRisk();
   if(kellyRisk < 0)
   {
      // Negative return = negative expectancy, reject trade
      // (SetReject already called inside CalcKellyRisk)
      return 0.0;
   }
   if(kellyRisk > 0)
   {
      // Use the more conservative of base risk and Kelly risk
      riskPct = MathMin(riskPct, kellyRisk);
   }
   // If Kelly returns 0 (insufficient data), keep base risk.

   //--- Compute lot size from risk
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= 0)
   {
      SetReject(RISK_REJECT_SYSTEM, "Account balance is zero or negative");
      return 0.0;
   }

   double riskAmount = balance * riskPct / 100.0;

   // Use OrderCalcProfit for accurate per-lot loss calculation (Gold-specific)
   double lossForOneLot = CalcRiskAmountFromSL(symbol, order_type, entry_price, sl_price, 1.0);
   if(lossForOneLot <= 0)
   {
      Print("[RiskManager] WARNING: CalcRiskAmountFromSL returned ", lossForOneLot,
            " — using fallback calculation");
      // Fallback: Estimate from tick value
      double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tickValue > 0 && tickSize > 0)
         lossForOneLot = sl_dist / tickSize * tickValue;
      else
         lossForOneLot = sl_points * 1.0; // Rough fallback: $1 per point per 0.01 lot * 100
   }

   if(lossForOneLot <= 0)
   {
      SetReject(RISK_REJECT_RISK_ZERO, "Cannot calculate per-lot loss amount");
      return 0.0;
   }

   double lot = riskAmount / lossForOneLot;

   //--- Stage 6: Equity curve filter
   lot = ApplyEquityCurveFilter(lot, EQUITY_FILTER_MA_PERIOD);
   if(lot <= 0)
   {
      SetReject(RISK_REJECT_EQUITY_CURVE, "Equity curve filter reduced lot to zero");
      return 0.0;
   }

   //--- Stage 7: Regime multiplier (pass 0 = no regime override)
   // The caller should pass the regime type. Here we accept it as-is.
   // Regime 0 = neutral (1.0x), defined per the EA's regime detection.
   // This stage is a no-op when called with regime=0.
   // For direct CalcLotSize() calls, regime is not applied.
   // The caller can chain: lot = ApplyRegimeMultiplier(lot, detectedRegime);

   //--- Stage 8: DD reduction
   lot = ApplyDDReduction(lot);
   if(lot <= 0)
   {
      SetReject(RISK_REJECT_DD_REDUCTION, "DD reduction zeroed out lot");
      return 0.0;
   }

   //--- Stage 9: Portfolio risk check
   lot = ApplyPortfolioRisk(lot);
   if(lot <= 0)
   {
      SetReject(RISK_REJECT_PORTFOLIO, "Portfolio exposure limit exceeded");
      return 0.0;
   }

   //--- Stage 10: Final clamp to min/max and broker step
   lot = FinalClamp(lot);

   if(lot > 0)
   {
      Print("[RiskManager] CalcLotSize APPROVED: ", DoubleToString(lot, m_lotStepDigits),
            " lots | Risk=", DoubleToString(riskPct, 2), "%",
            " | SL=", DoubleToString(sl_points, 0), " pts",
            " | DD=", DoubleToString(GetCurrentDD(), 1), "%",
            " | DDLevel=", GetDDEscalationLevel(),
            " | Spread=", currentSpread);
   }

   return lot;
}

//+------------------------------------------------------------------+
//| Stage 1: ValidateSystem                                           |
//| Check account, connection, and trade permissions                  |
//+------------------------------------------------------------------+
bool CRiskManager::ValidateSystem()
{
   // Check algo trading is enabled at terminal level
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      SetReject(RISK_REJECT_SYSTEM, "Algo trading disabled in terminal settings");
      return false;
   }

   // Check algo trading is enabled for this EA
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      SetReject(RISK_REJECT_SYSTEM, "Algo trading disabled for this EA (check AutoTrading button)");
      return false;
   }

   // Check connection to trade server
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
   {
      SetReject(RISK_REJECT_SYSTEM, "No connection to trade server");
      return false;
   }

   // Check if trading is allowed for the symbol
   if(!SymbolInfoInteger(m_symbol, SYMBOL_TRADE_MODE))
   {
      // SYMBOL_TRADE_MODE == 0 means SYMBOL_TRADE_MODE_DISABLED
      SetReject(RISK_REJECT_SYSTEM, "Trading disabled for symbol: " + m_symbol);
      return false;
   }

   // Check account balance is positive
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= 0)
   {
      SetReject(RISK_REJECT_SYSTEM,
                StringFormat("Account balance invalid: %.2f", balance));
      return false;
   }

   // Check free margin is positive
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(freeMargin <= 0)
   {
      SetReject(RISK_REJECT_SYSTEM,
                StringFormat("No free margin available: %.2f", freeMargin));
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Stage 2: ValidateSpread                                           |
//| Gold typically 20-80 points, can spike to 200+ during news        |
//+------------------------------------------------------------------+
bool CRiskManager::ValidateSpread(int max_spread)
{
   if(max_spread <= 0)
      return true; // No spread filter applied

   int currentSpread = (int)SymbolInfoInteger(m_symbol, SYMBOL_SPREAD);
   if(currentSpread > max_spread)
   {
      SetReject(RISK_REJECT_SPREAD,
                StringFormat("Spread %d > max %d for %s", currentSpread, max_spread, m_symbol));
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Stage 3: ValidateSLTP                                             |
//| Check SL/TP distances are valid for Gold                          |
//+------------------------------------------------------------------+
bool CRiskManager::ValidateSLTP(double sl_dist, double tp_dist)
{
   // SL must be positive
   if(sl_dist <= 0)
   {
      SetReject(RISK_REJECT_SLTP, "Stop-loss distance is zero or negative");
      return false;
   }

   // Gold-specific: SL too tight
   if(sl_dist < GOLD_MIN_SL_POINTS)
   {
      SetReject(RISK_REJECT_SLTP,
                StringFormat("SL distance %.0f pts < minimum %d pts for Gold",
                             sl_dist, GOLD_MIN_SL_POINTS));
      return false;
   }

   // Gold-specific: SL too wide (likely an error)
   if(sl_dist > GOLD_MAX_SL_POINTS)
   {
      SetReject(RISK_REJECT_SLTP,
                StringFormat("SL distance %.0f pts > maximum %d pts for Gold (possible error)",
                             sl_dist, GOLD_MAX_SL_POINTS));
      return false;
   }

   // Check against broker's STOPS_LEVEL
   int stopsLevel = (int)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > 0 && sl_dist < stopsLevel)
   {
      SetReject(RISK_REJECT_SLTP,
                StringFormat("SL distance %.0f pts < broker STOPS_LEVEL %d pts",
                             sl_dist, stopsLevel));
      return false;
   }

   // TP validation (optional - tp_dist=0 means no TP)
   if(tp_dist > 0 && stopsLevel > 0 && tp_dist < stopsLevel)
   {
      SetReject(RISK_REJECT_SLTP,
                StringFormat("TP distance %.0f pts < broker STOPS_LEVEL %d pts",
                             tp_dist, stopsLevel));
      return false;
   }

   // Check against broker's FREEZE_LEVEL
   int freezeLevel = (int)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   if(freezeLevel > 0 && sl_dist < freezeLevel)
   {
      Print("[RiskManager] WARNING: SL distance ", DoubleToString(sl_dist, 0),
            " pts < FREEZE_LEVEL ", freezeLevel,
            " pts — order may not be modifiable");
      // Warning only, do not reject
   }

   return true;
}

//+------------------------------------------------------------------+
//| Stage 4: CalcBaseRisk                                             |
//| Per-trade risk % with drawdown adjustment                         |
//+------------------------------------------------------------------+
double CRiskManager::CalcBaseRisk()
{
   // Update drawdown tracking first
   UpdatePeakBalance();

   double riskPct = m_riskPct;
   int ddLevel = GetDDEscalationLevel();

   switch(ddLevel)
   {
      case 0: riskPct *= DD_MULT_LEVEL_0; break; // Normal
      case 1: riskPct *= DD_MULT_LEVEL_1; break; // 6-10%
      case 2: riskPct *= DD_MULT_LEVEL_2; break; // 10-15%
      case 3: riskPct *= DD_MULT_LEVEL_3; break; // 15-20%
      case 4: riskPct *= DD_MULT_LEVEL_4; break; // >20%
      default: riskPct *= DD_MULT_LEVEL_4; break;
   }

   if(ddLevel > 0)
   {
      Print("[RiskManager] DD Escalation Level ", ddLevel,
            ": DD=", DoubleToString(GetCurrentDD(), 1), "%",
            " Risk adjusted from ", DoubleToString(m_riskPct, 2), "%",
            " to ", DoubleToString(riskPct, 2), "%",
            " MinScore+=", GetMinScoreAdjustment());
   }

   return riskPct;
}

//+------------------------------------------------------------------+
//| Stage 5: CalcKellyRisk                                            |
//| Half-Kelly position sizing from recent trade results              |
//+------------------------------------------------------------------+
double CRiskManager::CalcKellyRisk(int lookback, double fraction,
                                   double min_risk, double max_risk)
{
   // Not enough trade history for Kelly — return 0 to signal "use base risk"
   if(m_tradeCount < lookback)
      return 0.0;

   int effectiveLookback = MathMin(m_tradeCount, lookback);

   int wins = 0;
   double totalWin = 0.0;
   double totalLoss = 0.0;
   int losses = 0;

   int startIdx = m_tradeIndex - effectiveLookback;
   if(startIdx < 0) startIdx += RISK_MAX_HISTORY;

   for(int i = 0; i < effectiveLookback; i++)
   {
      int idx = (startIdx + i) % RISK_MAX_HISTORY;
      if(m_tradeResults[idx] > 0)
      {
         wins++;
         totalWin += m_tradeResults[idx];
      }
      else if(m_tradeResults[idx] < 0)
      {
         losses++;
         totalLoss += MathAbs(m_tradeResults[idx]);
      }
      // Zero-result trades are ignored
   }

   // Need both wins and losses for Kelly
   if(wins == 0 || losses == 0)
      return 0.0; // Signal "use base risk"

   // Guard: all trades are wins or all are losses
   if(wins >= effectiveLookback)
      return 0.0;

   double W = (double)wins / effectiveLookback;            // Win rate
   double R = (totalWin / wins) / (totalLoss / losses);    // Payoff ratio (AvgWin / AvgLoss)

   // Kelly formula: f = W - (1 - W) / R
   double kelly = W - (1.0 - W) / R;

   // Negative Kelly means negative expectancy — do not trade
   if(kelly <= 0)
   {
      Print("[RiskManager] Kelly NEGATIVE: ", DoubleToString(kelly * 100, 2), "%",
            " (WR=", DoubleToString(W * 100, 1), "%",
            " R=", DoubleToString(R, 2), ")",
            " — trade rejected by Kelly criterion");
      SetReject(RISK_REJECT_KELLY_ZERO,
                StringFormat("Negative Kelly (%.2f%%) — negative expectancy", kelly * 100));
      return -1.0; // Special signal: reject trade
   }

   // Apply fractional Kelly
   kelly *= fraction;

   // Clamp to configured bounds
   kelly = MathMax(min_risk, MathMin(max_risk, kelly)) ;

   // Convert to percentage
   double kellyPct = kelly;

   Print("[RiskManager] Kelly: ", DoubleToString(kellyPct, 2), "%",
         " (WR=", DoubleToString(W * 100, 1), "%",
         " R=", DoubleToString(R, 2),
         " raw=", DoubleToString((W - (1.0 - W) / R) * 100, 2), "%",
         " frac=", DoubleToString(fraction, 2), ")");

   return kellyPct;
}

//+------------------------------------------------------------------+
//| Stage 6: ApplyEquityCurveFilter                                   |
//| If current equity < SMA of equity snapshots, reduce lot by 50%    |
//+------------------------------------------------------------------+
double CRiskManager::ApplyEquityCurveFilter(double lot, int ma_period)
{
   if(lot <= 0) return 0.0;

   // Not enough equity history — allow trade at full size
   if(m_equityCount < ma_period)
      return lot;

   double equitySMA = GetEquitySMA(ma_period);
   double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);

   if(currentEquity < equitySMA)
   {
      double reducedLot = lot * EQUITY_FILTER_REDUCTION;
      Print("[RiskManager] Equity Curve Filter ACTIVE: Equity=",
            DoubleToString(currentEquity, 2),
            " < SMA(", ma_period, ")=", DoubleToString(equitySMA, 2),
            " — lot reduced from ", DoubleToString(lot, m_lotStepDigits),
            " to ", DoubleToString(reducedLot, m_lotStepDigits));
      return reducedLot;
   }

   return lot;
}

//+------------------------------------------------------------------+
//| Stage 7: ApplyRegimeMultiplier                                    |
//| Regime-based lot scaling:                                         |
//|   0 = neutral (1.0x)                                              |
//|   1 = trend (1.0x)                                                |
//|   2 = range (0.6x)                                                |
//|   3 = high_vol (0.3x)                                             |
//|   4 = trend_weak (0.8x)                                           |
//|   5 = high_vol_trend (0.5x)                                       |
//|   6 = high_vol_range (0.3x)                                       |
//+------------------------------------------------------------------+
double CRiskManager::ApplyRegimeMultiplier(double lot, int regime)
{
   if(lot <= 0) return 0.0;

   double multiplier = 1.0;
   string regimeName = "neutral";

   switch(regime)
   {
      case 0: multiplier = 1.0; regimeName = "neutral";        break;
      case 1: multiplier = 1.0; regimeName = "trend";          break;
      case 2: multiplier = 0.6; regimeName = "range";          break;
      case 3: multiplier = 0.3; regimeName = "high_vol";       break;
      case 4: multiplier = 0.8; regimeName = "trend_weak";     break;
      case 5: multiplier = 0.5; regimeName = "high_vol_trend"; break;
      case 6: multiplier = 0.3; regimeName = "high_vol_range"; break;
      default:
         Print("[RiskManager] WARNING: Unknown regime=", regime, " — using 1.0x");
         multiplier = 1.0;
         regimeName = "unknown";
         break;
   }

   if(multiplier < 1.0)
   {
      double adjustedLot = lot * multiplier;
      Print("[RiskManager] Regime '", regimeName, "' multiplier=",
            DoubleToString(multiplier, 1),
            " — lot ", DoubleToString(lot, m_lotStepDigits),
            " -> ", DoubleToString(adjustedLot, m_lotStepDigits));
      return adjustedLot;
   }

   return lot;
}

//+------------------------------------------------------------------+
//| Stage 8: ApplyDDReduction                                         |
//| Additional DD-based lot reduction (applied after regime scaling)   |
//| This catches extreme DD scenarios where CalcBaseRisk already       |
//| reduced risk%, but lot size may still be too large.                |
//+------------------------------------------------------------------+
double CRiskManager::ApplyDDReduction(double lot)
{
   if(lot <= 0) return 0.0;

   double dd = GetCurrentDD();

   // At extreme DD (>= m_maxDD_Pct), apply additional 50% cut
   // This stacks with the CalcBaseRisk DD adjustment for aggressive protection
   if(dd >= m_maxDD_Pct)
   {
      double reduced = lot * 0.5;
      Print("[RiskManager] DD Reduction ACTIVE: DD=", DoubleToString(dd, 1), "%",
            " >= MaxDD=", DoubleToString(m_maxDD_Pct, 1), "%",
            " — additional 50% cut: ", DoubleToString(lot, m_lotStepDigits),
            " -> ", DoubleToString(reduced, m_lotStepDigits));
      return reduced;
   }

   return lot;
}

//+------------------------------------------------------------------+
//| Stage 9: ApplyPortfolioRisk                                       |
//| Check total exposure across all open positions                     |
//+------------------------------------------------------------------+
double CRiskManager::ApplyPortfolioRisk(double lot)
{
   if(lot <= 0) return 0.0;

   // Calculate total exposure from existing positions on this symbol
   double totalLots = 0;
   int totalPositions = PositionsTotal();

   for(int i = 0; i < totalPositions; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;

      // Only count positions for our symbol
      if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;

      // If magic number is set, only count our positions
      if(m_magicNumber > 0 && PositionGetInteger(POSITION_MAGIC) != m_magicNumber)
         continue;

      totalLots += PositionGetDouble(POSITION_VOLUME);
   }

   // Also count pending orders
   int totalOrders = OrdersTotal();
   for(int i = 0; i < totalOrders; i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != m_symbol) continue;
      if(m_magicNumber > 0 && OrderGetInteger(ORDER_MAGIC) != m_magicNumber)
         continue;
      totalLots += OrderGetDouble(ORDER_VOLUME_CURRENT);
   }

   // Check if adding this lot would exceed max_lots for portfolio
   double projectedTotal = totalLots + lot;
   if(projectedTotal > m_maxLots)
   {
      double available = m_maxLots - totalLots;
      if(available <= 0)
      {
         Print("[RiskManager] Portfolio FULL: existing=",
               DoubleToString(totalLots, m_lotStepDigits),
               " + requested=", DoubleToString(lot, m_lotStepDigits),
               " > max=", DoubleToString(m_maxLots, m_lotStepDigits));
         return 0.0;
      }

      Print("[RiskManager] Portfolio limit: reducing lot from ",
            DoubleToString(lot, m_lotStepDigits), " to ",
            DoubleToString(available, m_lotStepDigits),
            " (existing=", DoubleToString(totalLots, m_lotStepDigits), ")");
      return available;
   }

   // Also check margin availability
   double marginRequired = 0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, m_symbol,
                       lot, SymbolInfoDouble(m_symbol, SYMBOL_ASK), marginRequired))
   {
      Print("[RiskManager] WARNING: OrderCalcMargin failed — skipping margin check");
      return lot;
   }

   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(marginRequired > freeMargin)
   {
      // Try to scale down to fit available margin
      double ratio = freeMargin / marginRequired * 0.95; // 5% safety buffer
      double adjustedLot = lot * ratio;
      if(adjustedLot < m_minLots)
      {
         Print("[RiskManager] Insufficient margin: required=",
               DoubleToString(marginRequired, 2),
               " available=", DoubleToString(freeMargin, 2));
         return 0.0;
      }
      Print("[RiskManager] Margin adjusted: ", DoubleToString(lot, m_lotStepDigits),
            " -> ", DoubleToString(adjustedLot, m_lotStepDigits),
            " (margin: req=", DoubleToString(marginRequired, 2),
            " free=", DoubleToString(freeMargin, 2), ")");
      return adjustedLot;
   }

   return lot;
}

//+------------------------------------------------------------------+
//| Stage 10: FinalClamp                                              |
//| Round down to lot step and clamp to min/max                       |
//+------------------------------------------------------------------+
double CRiskManager::FinalClamp(double lot)
{
   if(lot <= 0) return 0.0;

   // Round down to lot step (never round up to avoid exceeding risk)
   lot = RoundDown(lot, m_lotStep);

   // Clamp to broker limits
   lot = MathMax(m_brokerMinLot, lot);
   lot = MathMin(m_brokerMaxLot, lot);

   // Clamp to configured limits
   if(lot < m_minLots)
   {
      // Position too small — reject rather than trade at min with excess risk
      SetReject(RISK_REJECT_CLAMP_ZERO,
                "Calculated lot " + DoubleToString(lot, m_lotStepDigits) +
                " < min_lots " + DoubleToString(m_minLots, m_lotStepDigits));
      Print("[RiskManager] FinalClamp REJECT: lot=",
            DoubleToString(lot, m_lotStepDigits),
            " < min=", DoubleToString(m_minLots, m_lotStepDigits));
      return 0.0;
   }

   lot = MathMin(lot, m_maxLots);

   return NormalizeDouble(lot, m_lotStepDigits);
}

//+------------------------------------------------------------------+
//| UpdatePeakBalance                                                 |
//| Track the highest balance for drawdown calculation                 |
//+------------------------------------------------------------------+
void CRiskManager::UpdatePeakBalance()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance > m_peakBalance)
   {
      m_peakBalance = balance;
      GlobalVariableSet(GVKey("peakBal"), m_peakBalance);
   }

   // Calculate current DD using equity (not balance) for real-time accuracy
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   m_currentDD = (m_peakBalance > 0) ? (m_peakBalance - equity) / m_peakBalance * 100.0 : 0.0;
   if(m_currentDD < 0) m_currentDD = 0.0; // Equity above peak (new high territory)
}

//+------------------------------------------------------------------+
//| GetCurrentDD - Current drawdown percentage                        |
//+------------------------------------------------------------------+
double CRiskManager::GetCurrentDD()
{
   UpdatePeakBalance();
   return m_currentDD;
}

//+------------------------------------------------------------------+
//| GetDDEscalationLevel                                              |
//| Returns 0-4 based on DD thresholds (v9.3 proven settings)         |
//|   0: DD < 6%   — Normal                                          |
//|   1: 6-10%     — Risk x 0.5, MinScore += 11                      |
//|   2: 10-15%    — Risk x 0.25, MinScore += 13                     |
//|   3: 15-20%    — Risk x 0.25, MinScore += 16                     |
//|   4: >20%      — Risk x 0.25, MinScore += 18                     |
//+------------------------------------------------------------------+
int CRiskManager::GetDDEscalationLevel()
{
   double dd = GetCurrentDD();

   if(dd >= DD_LEVEL_3_THRESHOLD) return 4;  // >20%
   if(dd >= DD_LEVEL_2_THRESHOLD) return 3;  // 15-20%
   if(dd >= DD_LEVEL_1_THRESHOLD) return 2;  // 10-15%
   if(dd >= DD_LEVEL_0_THRESHOLD) return 1;  // 6-10%

   return 0; // Normal
}

//+------------------------------------------------------------------+
//| GetMinScoreAdjustment                                             |
//| Returns MinScore increase based on DD level                        |
//+------------------------------------------------------------------+
int CRiskManager::GetMinScoreAdjustment()
{
   int level = GetDDEscalationLevel();

   switch(level)
   {
      case 0: return DD_MINSCORE_ADJ_0;
      case 1: return DD_MINSCORE_ADJ_1;
      case 2: return DD_MINSCORE_ADJ_2;
      case 3: return DD_MINSCORE_ADJ_3;
      case 4: return DD_MINSCORE_ADJ_4;
      default: return DD_MINSCORE_ADJ_4;
   }
}

//+------------------------------------------------------------------+
//| OnNewDay - Reset daily PnL tracking                               |
//+------------------------------------------------------------------+
void CRiskManager::OnNewDay()
{
   MqlDateTime dt;
   TimeCurrent(dt);

   if(dt.day != m_lastDay)
   {
      Print("[RiskManager] New day detected: day ", m_lastDay, " -> ", dt.day,
            " | Yesterday PnL=", DoubleToString(m_dailyPnL, 2));
      m_lastDay       = dt.day;
      m_dailyPnL      = 0.0;
      m_dailyLimitHit = false;

      // Persist reset
      GlobalVariableSet(GVKey("cbDate"), (double)m_lastDay);
      GlobalVariableSet(GVKey("cbPnL"), m_dailyPnL);
   }
}

//+------------------------------------------------------------------+
//| AddTradeResult                                                    |
//| Record a trade result for Kelly, equity curve, and daily tracking  |
//+------------------------------------------------------------------+
void CRiskManager::AddTradeResult(double pnl)
{
   //--- Trade history (circular buffer for Kelly)
   m_tradeResults[m_tradeIndex] = pnl;
   m_tradeIndex = (m_tradeIndex + 1) % RISK_MAX_HISTORY;
   if(m_tradeCount < RISK_MAX_HISTORY)
      m_tradeCount++;

   //--- Equity curve snapshot
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   m_equityHistory[m_equityIndex] = equity;
   m_equityIndex = (m_equityIndex + 1) % RISK_MAX_EQUITY_HIST;
   if(m_equityCount < RISK_MAX_EQUITY_HIST)
      m_equityCount++;

   //--- Daily PnL tracking
   m_dailyPnL += pnl;

   // Check circuit breaker
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double dailyLimit = -(balance * m_dailyMaxLossPct / 100.0);
   if(m_dailyPnL <= dailyLimit)
   {
      m_dailyLimitHit = true;
      Print("[RiskManager] CIRCUIT BREAKER: Daily PnL=",
            DoubleToString(m_dailyPnL, 2),
            " <= limit=", DoubleToString(dailyLimit, 2),
            " (", DoubleToString(m_dailyMaxLossPct, 1), "% of ",
            DoubleToString(balance, 2), ")");
   }

   // Persist state after every trade
   GlobalVariableSet(GVKey("cbPnL"), m_dailyPnL);
   GlobalVariableSet(GVKey("cbDate"), (double)m_lastDay);

   // Persist trade history
   SaveState();

   Print("[RiskManager] Trade recorded: PnL=", DoubleToString(pnl, 2),
         " DailyPnL=", DoubleToString(m_dailyPnL, 2),
         " TradeCount=", m_tradeCount,
         " Equity=", DoubleToString(equity, 2));
}

//+------------------------------------------------------------------+
//| IsDailyLimitHit - Circuit breaker check                           |
//+------------------------------------------------------------------+
bool CRiskManager::IsDailyLimitHit()
{
   // Check for new day first (may reset the flag)
   OnNewDay();
   return m_dailyLimitHit;
}

//+------------------------------------------------------------------+
//| GetDailyPnL                                                       |
//+------------------------------------------------------------------+
double CRiskManager::GetDailyPnL()
{
   return m_dailyPnL;
}

//+------------------------------------------------------------------+
//| StartCooldown - Begin cooldown period (e.g. after SL hit)         |
//+------------------------------------------------------------------+
void CRiskManager::StartCooldown(int minutes)
{
   if(minutes <= 0) return;

   m_cooldownUntil = TimeCurrent() + minutes * 60;
   GlobalVariableSet(GVKey("cooldownUntil"), (double)m_cooldownUntil);

   Print("[RiskManager] Cooldown started: ", minutes, " minutes",
         " (until ", TimeToString(m_cooldownUntil, TIME_DATE | TIME_MINUTES), ")");
}

//+------------------------------------------------------------------+
//| IsInCooldown                                                      |
//+------------------------------------------------------------------+
bool CRiskManager::IsInCooldown()
{
   if(m_cooldownUntil <= 0) return false;

   if(TimeCurrent() >= m_cooldownUntil)
   {
      // Cooldown expired — clear it
      m_cooldownUntil = 0;
      GlobalVariableSet(GVKey("cooldownUntil"), 0.0);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| CooldownRemaining - Minutes remaining in cooldown                  |
//+------------------------------------------------------------------+
int CRiskManager::CooldownRemaining()
{
   if(!IsInCooldown()) return 0;

   int remaining = (int)((m_cooldownUntil - TimeCurrent()) / 60);
   return MathMax(0, remaining);
}

//+------------------------------------------------------------------+
//| GetWinRate - Win rate from recent trades                           |
//+------------------------------------------------------------------+
double CRiskManager::GetWinRate(int lookback)
{
   if(m_tradeCount == 0) return 0.0;

   int effectiveLookback = MathMin(m_tradeCount, lookback);
   int wins = 0;

   int startIdx = m_tradeIndex - effectiveLookback;
   if(startIdx < 0) startIdx += RISK_MAX_HISTORY;

   for(int i = 0; i < effectiveLookback; i++)
   {
      int idx = (startIdx + i) % RISK_MAX_HISTORY;
      if(m_tradeResults[idx] > 0)
         wins++;
   }

   return (double)wins / effectiveLookback;
}

//+------------------------------------------------------------------+
//| GetAvgWin - Average win amount                                    |
//+------------------------------------------------------------------+
double CRiskManager::GetAvgWin(int lookback)
{
   if(m_tradeCount == 0) return 0.0;

   int effectiveLookback = MathMin(m_tradeCount, lookback);
   int wins = 0;
   double totalWin = 0.0;

   int startIdx = m_tradeIndex - effectiveLookback;
   if(startIdx < 0) startIdx += RISK_MAX_HISTORY;

   for(int i = 0; i < effectiveLookback; i++)
   {
      int idx = (startIdx + i) % RISK_MAX_HISTORY;
      if(m_tradeResults[idx] > 0)
      {
         wins++;
         totalWin += m_tradeResults[idx];
      }
   }

   return (wins > 0) ? totalWin / wins : 0.0;
}

//+------------------------------------------------------------------+
//| GetAvgLoss - Average loss amount (returned as positive value)      |
//+------------------------------------------------------------------+
double CRiskManager::GetAvgLoss(int lookback)
{
   if(m_tradeCount == 0) return 0.0;

   int effectiveLookback = MathMin(m_tradeCount, lookback);
   int losses = 0;
   double totalLoss = 0.0;

   int startIdx = m_tradeIndex - effectiveLookback;
   if(startIdx < 0) startIdx += RISK_MAX_HISTORY;

   for(int i = 0; i < effectiveLookback; i++)
   {
      int idx = (startIdx + i) % RISK_MAX_HISTORY;
      if(m_tradeResults[idx] < 0)
      {
         losses++;
         totalLoss += MathAbs(m_tradeResults[idx]);
      }
   }

   return (losses > 0) ? totalLoss / losses : 0.0;
}

//+------------------------------------------------------------------+
//| GetExpectancy - Expected profit per trade                          |
//| E = WinRate * AvgWin - (1 - WinRate) * AvgLoss                   |
//+------------------------------------------------------------------+
double CRiskManager::GetExpectancy(int lookback)
{
   double wr   = GetWinRate(lookback);
   double avgW = GetAvgWin(lookback);
   double avgL = GetAvgLoss(lookback);

   return wr * avgW - (1.0 - wr) * avgL;
}

//+------------------------------------------------------------------+
//| SaveState - Persist all state to GlobalVariables                   |
//+------------------------------------------------------------------+
bool CRiskManager::SaveState()
{
   bool ok = true;

   //--- Peak balance
   ok &= GlobalVariableSet(GVKey("peakBal"), m_peakBalance) != 0;

   //--- Daily tracking
   ok &= GlobalVariableSet(GVKey("cbDate"), (double)m_lastDay) != 0;
   ok &= GlobalVariableSet(GVKey("cbPnL"), m_dailyPnL) != 0;

   //--- Cooldown
   ok &= GlobalVariableSet(GVKey("cooldownUntil"), (double)m_cooldownUntil) != 0;

   //--- Trade history metadata
   ok &= GlobalVariableSet(GVKey("tradeCount"), (double)m_tradeCount) != 0;
   ok &= GlobalVariableSet(GVKey("tradeIndex"), (double)m_tradeIndex) != 0;

   //--- Trade results buffer
   for(int i = 0; i < RISK_MAX_HISTORY; i++)
   {
      ok &= GlobalVariableSet(GVKey("TR_" + IntegerToString(i)),
                               m_tradeResults[i]) != 0;
   }

   //--- Equity history metadata
   ok &= GlobalVariableSet(GVKey("eqCount"), (double)m_equityCount) != 0;
   ok &= GlobalVariableSet(GVKey("eqIndex"), (double)m_equityIndex) != 0;

   //--- Equity history buffer
   for(int i = 0; i < RISK_MAX_EQUITY_HIST; i++)
   {
      ok &= GlobalVariableSet(GVKey("EQ_" + IntegerToString(i)),
                               m_equityHistory[i]) != 0;
   }

   if(!ok)
      Print("[RiskManager] WARNING: Some GlobalVariables failed to save");
   else
      Print("[RiskManager] State saved: Peak=", DoubleToString(m_peakBalance, 2),
            " Trades=", m_tradeCount,
            " DailyPnL=", DoubleToString(m_dailyPnL, 2));

   return ok;
}

//+------------------------------------------------------------------+
//| LoadState - Restore state from GlobalVariables                     |
//+------------------------------------------------------------------+
bool CRiskManager::LoadState()
{
   //--- Peak balance
   if(GlobalVariableCheck(GVKey("peakBal")))
   {
      double savedPeak = GlobalVariableGet(GVKey("peakBal"));
      if(savedPeak > 0)
         m_peakBalance = MathMax(m_peakBalance, savedPeak);
   }

   //--- Daily tracking
   if(GlobalVariableCheck(GVKey("cbDate")) && GlobalVariableCheck(GVKey("cbPnL")))
   {
      int savedDay = (int)GlobalVariableGet(GVKey("cbDate"));
      MqlDateTime dt;
      TimeCurrent(dt);

      if(savedDay == dt.day)
      {
         m_dailyPnL = GlobalVariableGet(GVKey("cbPnL"));
         m_lastDay  = savedDay;

         // Re-check circuit breaker
         double balance = AccountInfoDouble(ACCOUNT_BALANCE);
         double dailyLimit = -(balance * m_dailyMaxLossPct / 100.0);
         if(m_dailyPnL <= dailyLimit)
            m_dailyLimitHit = true;
      }
      // If different day, keep defaults (zero PnL, no circuit breaker)
   }

   //--- Cooldown
   if(GlobalVariableCheck(GVKey("cooldownUntil")))
   {
      m_cooldownUntil = (datetime)(long)GlobalVariableGet(GVKey("cooldownUntil"));
      // Check if cooldown has already expired
      if(m_cooldownUntil > 0 && TimeCurrent() >= m_cooldownUntil)
         m_cooldownUntil = 0;
   }

   //--- Trade history
   if(GlobalVariableCheck(GVKey("tradeCount")))
   {
      m_tradeCount = (int)GlobalVariableGet(GVKey("tradeCount"));
      m_tradeIndex = (int)GlobalVariableGet(GVKey("tradeIndex"));

      // Sanity checks
      if(m_tradeCount < 0 || m_tradeCount > RISK_MAX_HISTORY)
         m_tradeCount = 0;
      if(m_tradeIndex < 0 || m_tradeIndex >= RISK_MAX_HISTORY)
         m_tradeIndex = 0;

      for(int i = 0; i < RISK_MAX_HISTORY; i++)
      {
         string key = GVKey("TR_" + IntegerToString(i));
         if(GlobalVariableCheck(key))
            m_tradeResults[i] = GlobalVariableGet(key);
      }
   }

   //--- Equity history
   if(GlobalVariableCheck(GVKey("eqCount")))
   {
      m_equityCount = (int)GlobalVariableGet(GVKey("eqCount"));
      m_equityIndex = (int)GlobalVariableGet(GVKey("eqIndex"));

      // Sanity checks
      if(m_equityCount < 0 || m_equityCount > RISK_MAX_EQUITY_HIST)
         m_equityCount = 0;
      if(m_equityIndex < 0 || m_equityIndex >= RISK_MAX_EQUITY_HIST)
         m_equityIndex = 0;

      for(int i = 0; i < RISK_MAX_EQUITY_HIST; i++)
      {
         string key = GVKey("EQ_" + IntegerToString(i));
         if(GlobalVariableCheck(key))
            m_equityHistory[i] = GlobalVariableGet(key);
      }
   }

   Print("[RiskManager] State loaded: Peak=", DoubleToString(m_peakBalance, 2),
         " Trades=", m_tradeCount,
         " DailyPnL=", DoubleToString(m_dailyPnL, 2),
         " Cooldown=", (IsInCooldown() ? IntegerToString(CooldownRemaining()) + "min" : "none"),
         " CircuitBreaker=", (m_dailyLimitHit ? "YES" : "no"));

   return true;
}

//+------------------------------------------------------------------+
//| PRIVATE: GVKey - Symbol/Magic scoped GlobalVariable key           |
//+------------------------------------------------------------------+
string CRiskManager::GVKey(string suffix)
{
   // GlobalVariable names are limited to 63 characters in MT5
   // Use a compact format: prefix + magic + symbol abbreviation + suffix
   string sym = m_symbol;
   if(StringLen(sym) > 10)
      sym = StringSubstr(sym, 0, 10);

   string key = RISK_GV_PREFIX + IntegerToString(m_magicNumber) + "_" + sym + "_" + suffix;

   // MT5 GlobalVariable name limit is 63 characters
   if(StringLen(key) > 63)
   {
      Print("[RiskManager] WARNING: GV key truncated: ", key);
      key = StringSubstr(key, 0, 63);
   }

   return key;
}

//+------------------------------------------------------------------+
//| PRIVATE: RoundDown - Round value down to nearest step              |
//+------------------------------------------------------------------+
double CRiskManager::RoundDown(double value, double step)
{
   if(step <= 0) return value;
   return MathFloor(value / step) * step;
}

//+------------------------------------------------------------------+
//| PRIVATE: CalcLotStepDigits                                        |
//| Determine decimal places for lot step                              |
//+------------------------------------------------------------------+
int CRiskManager::CalcLotStepDigits(double step)
{
   if(step >= 1.0) return 0;
   if(step >= 0.1) return 1;
   if(step >= 0.01) return 2;
   if(step >= 0.001) return 3;
   return 4;
}

//+------------------------------------------------------------------+
//| PRIVATE: CacheBrokerInfo                                          |
//| Read and cache broker's symbol specifications                      |
//+------------------------------------------------------------------+
void CRiskManager::CacheBrokerInfo(string symbol)
{
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(lotStep > 0)
      m_lotStep = lotStep;
   else
      m_lotStep = GOLD_TYPICAL_LOT_STEP;

   m_lotStepDigits = CalcLotStepDigits(m_lotStep);

   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   if(minLot > 0)
      m_brokerMinLot = minLot;
   else
      m_brokerMinLot = GOLD_TYPICAL_LOT_STEP;

   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(maxLot > 0)
      m_brokerMaxLot = maxLot;
   else
      m_brokerMaxLot = 100.0;
}

//+------------------------------------------------------------------+
//| PRIVATE: CalcRiskAmountFromSL                                     |
//| Calculate loss amount for a given lot size using OrderCalcProfit   |
//| Gold-specific: handles XAUUSD tick value correctly                 |
//+------------------------------------------------------------------+
double CRiskManager::CalcRiskAmountFromSL(string symbol, ENUM_ORDER_TYPE order_type,
                                          double entry_price, double sl_price, double lot_size)
{
   double profitOrLoss = 0.0;

   // Determine direction from order type
   ENUM_ORDER_TYPE calcType;
   double calcEntry, calcSL;

   if(order_type == ORDER_TYPE_BUY || order_type == ORDER_TYPE_BUY_LIMIT ||
      order_type == ORDER_TYPE_BUY_STOP || order_type == ORDER_TYPE_BUY_STOP_LIMIT)
   {
      calcType  = ORDER_TYPE_BUY;
      calcEntry = entry_price;
      calcSL    = sl_price;
   }
   else
   {
      calcType  = ORDER_TYPE_SELL;
      calcEntry = entry_price;
      calcSL    = sl_price;
   }

   // Use MT5's built-in function for accurate calculation
   if(OrderCalcProfit(calcType, symbol, lot_size, calcEntry, calcSL, profitOrLoss))
   {
      return MathAbs(profitOrLoss);
   }

   // Fallback: Manual calculation using tick value
   Print("[RiskManager] OrderCalcProfit failed for ", symbol,
         " — using manual fallback calculation");

   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double point     = SymbolInfoDouble(symbol, SYMBOL_POINT);

   if(tickValue <= 0 || tickSize <= 0 || point <= 0)
   {
      Print("[RiskManager] ERROR: Cannot get tick info for ", symbol,
            " tickValue=", tickValue, " tickSize=", tickSize, " point=", point);

      // Last resort: hardcoded Gold estimation for JPY accounts
      // XAUUSD: ~$1 per point per 0.01 lot, converted at USDJPY rate
      double sl_dist = MathAbs(entry_price - sl_price);
      double usdJpyRate = SymbolInfoDouble("USDJPY", SYMBOL_BID);
      if(usdJpyRate <= 0) usdJpyRate = 150.0; // Hardcoded fallback

      // For 1.0 lot: sl_dist / point_size * tick_value_per_tick
      // Approximate: Gold 1 lot = 100 oz, $1/point = $100/dollar-move
      double lossEstimate = (sl_dist / 0.01) * 1.0 * lot_size;
      // Convert USD to account currency if needed
      ENUM_ACCOUNT_CURRENCY accCcy;
      string accCurrency = AccountInfoString(ACCOUNT_CURRENCY);
      if(accCurrency == "JPY")
         lossEstimate *= usdJpyRate;

      Print("[RiskManager] Hardcoded fallback loss estimate: ",
            DoubleToString(lossEstimate, 2), " ", accCurrency);
      return lossEstimate;
   }

   double sl_distance = MathAbs(entry_price - sl_price);
   double loss = (sl_distance / tickSize) * tickValue * lot_size;

   return loss;
}

//+------------------------------------------------------------------+
//| PRIVATE: SetReject - Record rejection reason for diagnostics       |
//+------------------------------------------------------------------+
void CRiskManager::SetReject(ENUM_RISK_REJECT reason, string msg)
{
   m_lastRejectReason = reason;
   m_lastRejectMsg    = msg;
   Print("[RiskManager] REJECTED Stage ", (int)reason, ": ", msg);
}

//+------------------------------------------------------------------+
//| PRIVATE: GetEquitySMA - Simple moving average of equity snapshots  |
//+------------------------------------------------------------------+
double CRiskManager::GetEquitySMA(int period)
{
   if(m_equityCount < period) return 0.0;

   double sum = 0.0;
   int startIdx = m_equityIndex - period;
   if(startIdx < 0) startIdx += RISK_MAX_EQUITY_HIST;

   for(int i = 0; i < period; i++)
   {
      int idx = (startIdx + i) % RISK_MAX_EQUITY_HIST;
      sum += m_equityHistory[idx];
   }

   return sum / period;
}

//+------------------------------------------------------------------+
