//+------------------------------------------------------------------+
//| GoldAlpha_v30.mq5 - Adaptive MaxPos + Progressive Trail          |
//| v29 base + two architectural enhancements:                       |
//|   1. Adaptive MaxPositions: MaxPos=3 in strong D1 only           |
//|   2. Progressive Trail: tighter trail as profit grows            |
//|   3. SL_Weak_Mult optimized: 1.8 -> 1.5 (tighter weak SL)      |
//|                                                                  |
//| MT5 Backtest Results (USD $10K, R=0.2%, 2016-2026):              |
//|   PF=2.15, T=877, DD=7.69%, Sharp=1.79                          |
//|   WFA 5/5: 1.07, 1.13, 1.31, 1.44, 2.18                        |
//|   Sensitivity 12/12 PASS (PF range 2.06-2.22 at +/-20%)         |
//|                                                                  |
//| OOS 2024-2026 (R=1.0%): PF=1.90, T=272, $394K profit            |
//| Production: R=1.0%, MaxLot=1.00, JPY 300K -> ~7100+/day          |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "30.00"
#property strict
#include <Trade\Trade.mqh>

// --- Trend ---
input int      W1_FastEMA    = 8;
input int      W1_SlowEMA    = 21;
input int      D1_EMA        = 50;
input int      H4_EMA        = 20;
input int      ATR_Period    = 14;
input int      ATR_SMA       = 50;

// --- Risk / SL ---
input double   SL_ATR_Mult   = 2.5;
input double   Trail_ATR     = 3.0;
input double   BE_ATR        = 1.0;
input double   SL_Weak_Mult  = 1.5;
input double   RiskPct       = 1.0;

// --- Entry Filters ---
input double   BodyRatio     = 0.32;
input double   EMA_Zone_ATR  = 0.50;
input double   ATR_Filter    = 0.55;
input double   D1_Tolerance  = 0.003;
input int      MaxPositions  = 2;

// --- D1/W1/H4 Regime ---
input int      D1_Slope_Bars  = 5;
input double   D1_Min_Slope   = 0.0005;
input double   D1_Strong_Slope = 0.004;
input double   W1_Min_Sep     = 0.005;
input int      H4_Slope_Strong = 8;
input int      H4_Slope_Weak   = 3;

// --- Progressive Trail (NEW) ---
input bool     UseProgressiveTrail = true;
input double   TrailProfit1   = 2.0;    // ATR profit level for first tighten
input double   TrailProfit2   = 4.0;    // ATR profit level for second tighten
input double   Trail_ATR_Med  = 2.5;    // Trail at profit level 1
input double   Trail_ATR_Tight = 2.0;   // Trail at profit level 2

// --- H1 Entry Mode ---
input bool     UseH1Entry     = false;
input double   H1_BodyRatio   = 0.30;   // BodyRatio for H1 candles
input int      H1_Cooldown    = 4;      // Min H1 bars between entries

// --- Regime-Conditional Relaxation ---
input bool     UseRelaxedStrong = false;
input double   VeryStrongSlope  = 0.006; // D1 slope for "very strong" regime
input double   StrongBodyRatio  = 0.22;  // Relaxed body ratio in very strong D1
input double   StrongZone_ATR   = 0.65;  // Wider zone in very strong D1
input bool     CheckBar3Strong  = true;  // Check bar 3 in very strong D1

// --- Adaptive MaxPositions ---
input bool     UseAdaptiveMaxPos = true;   // Enable adaptive max positions
input int      MaxPos_Strong     = 3;    // MaxPositions in strong D1 regime
input int      MaxPos_Weak       = 2;    // MaxPositions in weak D1 regime

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 1.00;
input int      MagicNumber   = 330030;

CTrade trade;
int hW1Fast, hW1Slow, hD1EMA, hH4EMA, hATR;
datetime lastEntryTime = 0;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   hW1Fast = iMA(_Symbol, PERIOD_W1, W1_FastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hW1Slow = iMA(_Symbol, PERIOD_W1, W1_SlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hD1EMA  = iMA(_Symbol, PERIOD_D1, D1_EMA, 0, MODE_EMA, PRICE_CLOSE);
   hH4EMA  = iMA(_Symbol, PERIOD_H4, H4_EMA, 0, MODE_EMA, PRICE_CLOSE);
   hATR    = iATR(_Symbol, PERIOD_H4, ATR_Period);
   if(hW1Fast==INVALID_HANDLE||hW1Slow==INVALID_HANDLE||hD1EMA==INVALID_HANDLE||hH4EMA==INVALID_HANDLE||hATR==INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{ IndicatorRelease(hW1Fast); IndicatorRelease(hW1Slow); IndicatorRelease(hD1EMA); IndicatorRelease(hH4EMA); IndicatorRelease(hATR); }

int CountPositions()
{ int c=0; for(int i=PositionsTotal()-1;i>=0;i--) if(PositionGetSymbol(i)==_Symbol&&PositionGetInteger(POSITION_MAGIC)==MagicNumber)c++; return c; }

double CalcLot(double sd)
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double rm = eq * RiskPct / 100.0;
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0 || sd <= 0) return MinLot;

   string acctCcy = AccountInfoString(ACCOUNT_CURRENCY);
   if(acctCcy == "JPY" && tv < 10.0)
   {
      double usdjpy = 0;
      if(SymbolInfoDouble("USDJPY", SYMBOL_BID) > 0)
         usdjpy = SymbolInfoDouble("USDJPY", SYMBOL_BID);
      else
         usdjpy = 150.0;
      tv *= usdjpy;
   }

   double ticks = sd / ts;
   double riskPerLot = ticks * tv;
   if(riskPerLot <= 0) return MinLot;
   double l = rm / riskPerLot;
   l = MathFloor(l / 0.01) * 0.01;
   return MathMax(MinLot, MathMin(MaxLot, l));
}

double GetAvgATR()
{ double b[]; if(CopyBuffer(hATR,0,1,ATR_SMA+1,b)<ATR_SMA+1)return -1; double s=0; for(int i=0;i<ATR_SMA;i++)s+=b[i]; return s/ATR_SMA; }

double GetD1Slope()
{
   double c[],p[];
   if(CopyBuffer(hD1EMA,0,1,1,c)<1) return 0;
   if(CopyBuffer(hD1EMA,0,D1_Slope_Bars+1,1,p)<1) return 0;
   if(p[0]<=0) return 0;
   return MathAbs(c[0]-p[0])/p[0];
}

void ManageTrail()
{
   static datetime lt=0; datetime cb=iTime(_Symbol,PERIOD_H4,0); if(cb==lt)return; lt=cb;
   for(int i=PositionsTotal()-1;i>=0;i--)
   { if(PositionGetSymbol(i)!=_Symbol||PositionGetInteger(POSITION_MAGIC)!=MagicNumber)continue;
     double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1)return;
     double av=a[0],op=PositionGetDouble(POSITION_PRICE_OPEN),sl=PositionGetDouble(POSITION_SL);
     long pt=PositionGetInteger(POSITION_TYPE); ulong tk=PositionGetInteger(POSITION_TICKET);
     if(pt==POSITION_TYPE_BUY)
     { double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
       double profit=bid-op;
       // BE check
       if(profit>BE_ATR*av&&sl<op){trade.PositionModify(tk,NormalizeDouble(op+0.1*av,_Digits),0);continue;}
       // Trail
       if(sl>=op)
       { double hh=0;for(int j=1;j<=10;j++){double h=iHigh(_Symbol,PERIOD_H4,j);if(h>hh)hh=h;}
         // Progressive trail: select trail width based on profit level
         double trailWidth = Trail_ATR;
         if(UseProgressiveTrail)
         {
            double profitATR = profit / av;
            if(profitATR >= TrailProfit2)
               trailWidth = Trail_ATR_Tight;
            else if(profitATR >= TrailProfit1)
               trailWidth = Trail_ATR_Med;
         }
         double ns=NormalizeDouble(hh-trailWidth*av,_Digits);
         if(ns>sl+_Point*10)trade.PositionModify(tk,ns,0);
       }
     }
     else
     { double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
       double profit=op-ask;
       if(profit>BE_ATR*av&&sl>op){trade.PositionModify(tk,NormalizeDouble(op-0.1*av,_Digits),0);continue;}
       if(sl<=op)
       { double ll=999999;for(int j=1;j<=10;j++){double l=iLow(_Symbol,PERIOD_H4,j);if(l<ll)ll=l;}
         double trailWidth = Trail_ATR;
         if(UseProgressiveTrail)
         {
            double profitATR = profit / av;
            if(profitATR >= TrailProfit2)
               trailWidth = Trail_ATR_Tight;
            else if(profitATR >= TrailProfit1)
               trailWidth = Trail_ATR_Med;
         }
         double ns=NormalizeDouble(ll+trailWidth*av,_Digits);
         if(ns<sl-_Point*10)trade.PositionModify(tk,ns,0);
       }
     }
   }
}

// --- H4 dip-buy/sell checks (original v29) ---
bool CheckBuyDip(int sh, double ema, double zone, double bodyRat)
{ double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh),l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
  if(l>ema+zone||c<=ema||c<=o)return false; double rng=h-l; if(rng<=_Point)return false; return(c-o)/rng>=bodyRat; }

bool CheckSellDip(int sh, double ema, double zone, double bodyRat)
{ double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh),l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
  if(h<ema-zone||c>=ema||c>=o)return false; double rng=h-l; if(rng<=_Point)return false; return(o-c)/rng>=bodyRat; }

// --- H1 dip-buy/sell checks (NEW: same logic, H1 bars, H4 EMA reference) ---
bool CheckBuyDipH1(int sh, double ema, double zone, double bodyRat)
{ double o=iOpen(_Symbol,PERIOD_H1,sh),c=iClose(_Symbol,PERIOD_H1,sh),l=iLow(_Symbol,PERIOD_H1,sh),h=iHigh(_Symbol,PERIOD_H1,sh);
  if(l>ema+zone||c<=ema||c<=o)return false; double rng=h-l; if(rng<=_Point)return false; return(c-o)/rng>=bodyRat; }

bool CheckSellDipH1(int sh, double ema, double zone, double bodyRat)
{ double o=iOpen(_Symbol,PERIOD_H1,sh),c=iClose(_Symbol,PERIOD_H1,sh),l=iLow(_Symbol,PERIOD_H1,sh),h=iHigh(_Symbol,PERIOD_H1,sh);
  if(h<ema-zone||c>=ema||c>=o)return false; double rng=h-l; if(rng<=_Point)return false; return(o-c)/rng>=bodyRat; }

void OnTick()
{
   int pc=CountPositions(); if(pc>0)ManageTrail();

   // --- Bar detection: H4 for standard mode, H1 for H1 entry mode ---
   if(UseH1Entry)
   {
      static datetime lbH1=0; datetime cbH1=iTime(_Symbol,PERIOD_H1,0);
      if(cbH1==lbH1)return; lbH1=cbH1;

      // H1 cooldown: min H1_Cooldown bars since last entry
      if(lastEntryTime > 0)
      {
         datetime cooldownEnd = lastEntryTime + H1_Cooldown * 3600;
         if(TimeCurrent() < cooldownEnd) return;
      }
   }
   else
   {
      static datetime lb=0; datetime cb=iTime(_Symbol,PERIOD_H4,0);
      if(cb==lb)return; lb=cb;
   }

   MqlDateTime dt; TimeToStruct(TimeCurrent(),dt);
   if(dt.day_of_week==0||dt.day_of_week==6)return; if(dt.day_of_week==5&&dt.hour>16)return;

   // D1 regime: reject ranging markets
   double d1slope = GetD1Slope();
   if(d1slope < D1_Min_Slope) return;
   bool isStrong = (d1slope >= D1_Strong_Slope);

   // Adaptive MaxPositions: Strong D1 -> MaxPos_Strong, Weak D1 -> MaxPos_Weak
   int effectiveMaxPos = MaxPositions;
   if(UseAdaptiveMaxPos)
   {
      if(isStrong)
         effectiveMaxPos = MaxPos_Strong;
      else
         effectiveMaxPos = MaxPos_Weak;
   }
   if(pc >= effectiveMaxPos) return;

   // W1 trend direction
   double wf[1],ws[1]; if(CopyBuffer(hW1Fast,0,1,1,wf)<1||CopyBuffer(hW1Slow,0,1,1,ws)<1)return;
   int dir=0; if(wf[0]>ws[0])dir=1; if(wf[0]<ws[0])dir=-1; if(dir==0)return;

   // W1 separation: reject weak trends
   double mid=(wf[0]+ws[0])/2; if(mid>0&&MathAbs(wf[0]-ws[0])/mid<W1_Min_Sep)return;

   // D1 alignment check
   double d1e[1]; if(CopyBuffer(hD1EMA,0,1,1,d1e)<1)return;
   double d1c=iClose(_Symbol,PERIOD_D1,1),dd=(d1c-d1e[0])/d1e[0];
   if(dir==1&&dd<-D1_Tolerance)return; if(dir==-1&&dd>D1_Tolerance)return;

   // ATR filter: require sufficient volatility
   double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1)return;
   double av=a[0]; if(av<_Point)return;
   double aa=GetAvgATR(); if(aa<=0||av<aa*ATR_Filter)return;

   double h4e[1]; if(CopyBuffer(hH4EMA,0,1,1,h4e)<1)return;

   // Adaptive H4 slope: stronger bars in strong trends
   int slopeBars = isStrong ? H4_Slope_Strong : H4_Slope_Weak;
   double h4prev[1];
   if(CopyBuffer(hH4EMA,0,slopeBars+1,1,h4prev)<1) return;
   double h4slope = h4e[0] - h4prev[0];
   if(dir==1 && h4slope < 0) return;
   if(dir==-1 && h4slope > 0) return;

   // Adaptive SL: wider in strong trends
   double slMult = isStrong ? SL_ATR_Mult : SL_Weak_Mult;

   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);

   // Regime-conditional entry parameters
   bool isVeryStrong = UseRelaxedStrong && (d1slope >= VeryStrongSlope);
   double entryBodyRatio = isVeryStrong ? StrongBodyRatio : BodyRatio;
   double entryZone = (isVeryStrong ? StrongZone_ATR : EMA_Zone_ATR) * av;

   // --- Entry detection ---
   bool buySignal = false, sellSignal = false;

   if(UseH1Entry)
   {
      double h1br = isVeryStrong ? StrongBodyRatio : H1_BodyRatio;
      if(dir==1)
         buySignal = CheckBuyDipH1(1,h4e[0],entryZone,h1br) || CheckBuyDipH1(2,h4e[0],entryZone,h1br);
      if(dir==-1)
         sellSignal = CheckSellDipH1(1,h4e[0],entryZone,h1br) || CheckSellDipH1(2,h4e[0],entryZone,h1br);
   }
   else
   {
      // Standard H4 entry with regime-conditional parameters
      if(dir==1)
      {
         buySignal = CheckBuyDip(1,h4e[0],entryZone,entryBodyRatio) || CheckBuyDip(2,h4e[0],entryZone,entryBodyRatio);
         if(!buySignal && isVeryStrong && CheckBar3Strong)
            buySignal = CheckBuyDip(3,h4e[0],entryZone,entryBodyRatio);
      }
      if(dir==-1)
      {
         sellSignal = CheckSellDip(1,h4e[0],entryZone,entryBodyRatio) || CheckSellDip(2,h4e[0],entryZone,entryBodyRatio);
         if(!sellSignal && isVeryStrong && CheckBar3Strong)
            sellSignal = CheckSellDip(3,h4e[0],entryZone,entryBodyRatio);
      }
   }

   if(buySignal)
   {
      double sd=slMult*av;
      trade.Buy(CalcLot(sd),_Symbol,ask,NormalizeDouble(ask-sd,_Digits),0,"A30 BUY");
      lastEntryTime = TimeCurrent();
   }
   if(sellSignal)
   {
      double sd=slMult*av;
      trade.Sell(CalcLot(sd),_Symbol,bid,NormalizeDouble(bid+sd,_Digits),0,"A30 SELL");
      lastEntryTime = TimeCurrent();
   }
}
