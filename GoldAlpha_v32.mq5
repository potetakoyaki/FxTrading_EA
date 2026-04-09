//+------------------------------------------------------------------+
//| GoldAlpha_v32.mq5 - v30 + Partial Close (Scale-Out)             |
//| v30 base (Adaptive MaxPos + Progressive Trail) +                 |
//|   1. Partial Close: lock in 40% profit at 1.5 ATR               |
//|   2. Remaining 60% runs with progressive trail                   |
//|                                                                  |
//| Hypothesis: Partial close improves 2016 range PF by locking     |
//| small wins that would otherwise be given back by wide trail.     |
//| Trend periods unaffected: 60% still captures full move.          |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "32.00"
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
input double   SL_Weak_Mult  = 1.8;
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

// --- Progressive Trail ---
input bool     UseProgressiveTrail = true;
input double   TrailProfit1   = 2.0;
input double   TrailProfit2   = 4.0;
input double   Trail_ATR_Med  = 2.5;
input double   Trail_ATR_Tight = 2.0;

// --- Partial Close (NEW v32) ---
input bool     UsePartialClose   = true;
input double   PartialProfitATR  = 1.5;   // Take partial at this ATR profit
input double   PartialClosePct   = 40.0;  // Close this % of position

// --- Adaptive MaxPositions ---
input bool     UseAdaptiveMaxPos = true;
input int      MaxPos_Strong     = 3;
input int      MaxPos_Weak       = 2;

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 1.00;
input int      MagicNumber   = 330032;

CTrade trade;
int hW1Fast, hW1Slow, hD1EMA, hH4EMA, hATR;

// --- Partial close tracking ---
ulong pcTickets[];
int   pcCount = 0;

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
   ArrayResize(pcTickets, 0);
   pcCount = 0;
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

// --- Partial close helpers ---
bool IsPartialClosed(ulong ticket)
{
   for(int i=0; i<pcCount; i++)
      if(pcTickets[i] == ticket) return true;
   return false;
}

void MarkPartialClosed(ulong ticket)
{
   if(pcCount >= 50)
   {
      // Compact: remove tickets no longer open
      int j = 0;
      for(int i=0; i<pcCount; i++)
      {
         bool found = false;
         for(int k=PositionsTotal()-1; k>=0; k--)
         {
            if(PositionGetSymbol(k)==_Symbol && (ulong)PositionGetInteger(POSITION_TICKET)==pcTickets[i])
            { found=true; break; }
         }
         if(found) pcTickets[j++] = pcTickets[i];
      }
      pcCount = j;
   }
   ArrayResize(pcTickets, pcCount + 1);
   pcTickets[pcCount++] = ticket;
}

void ManageTrail()
{
   static datetime lt=0; datetime cb=iTime(_Symbol,PERIOD_H4,0); if(cb==lt)return; lt=cb;
   for(int i=PositionsTotal()-1;i>=0;i--)
   { if(PositionGetSymbol(i)!=_Symbol||PositionGetInteger(POSITION_MAGIC)!=MagicNumber)continue;
     double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1)return;
     double av=a[0],op=PositionGetDouble(POSITION_PRICE_OPEN),sl=PositionGetDouble(POSITION_SL);
     long pt=PositionGetInteger(POSITION_TYPE); ulong tk=(ulong)PositionGetInteger(POSITION_TICKET);

     // --- Partial close check (NEW v32) ---
     if(UsePartialClose && !IsPartialClosed(tk))
     {
        double pcProfit = 0;
        if(pt==POSITION_TYPE_BUY)
           pcProfit = SymbolInfoDouble(_Symbol,SYMBOL_BID) - op;
        else
           pcProfit = op - SymbolInfoDouble(_Symbol,SYMBOL_ASK);

        if(pcProfit > PartialProfitATR * av)
        {
           double vol = PositionGetDouble(POSITION_VOLUME);
           double closeVol = MathFloor(vol * PartialClosePct / 100.0 / 0.01) * 0.01;
           if(closeVol >= MinLot && (vol - closeVol) >= MinLot)
           {
              trade.PositionClosePartial(tk, closeVol);
              MarkPartialClosed(tk);
           }
        }
     }

     if(pt==POSITION_TYPE_BUY)
     { double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
       double profit=bid-op;
       if(profit>BE_ATR*av&&sl<op){trade.PositionModify(tk,NormalizeDouble(op+0.1*av,_Digits),0);continue;}
       if(sl>=op)
       { double hh=0;for(int j=1;j<=10;j++){double h=iHigh(_Symbol,PERIOD_H4,j);if(h>hh)hh=h;}
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

bool CheckBuyDip(int sh, double ema, double zone, double bodyRat)
{ double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh),l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
  if(l>ema+zone||c<=ema||c<=o)return false; double rng=h-l; if(rng<=_Point)return false; return(c-o)/rng>=bodyRat; }

bool CheckSellDip(int sh, double ema, double zone, double bodyRat)
{ double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh),l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
  if(h<ema-zone||c>=ema||c>=o)return false; double rng=h-l; if(rng<=_Point)return false; return(o-c)/rng>=bodyRat; }

void OnTick()
{
   int pc=CountPositions(); if(pc>0)ManageTrail();

   static datetime lb=0; datetime cbr=iTime(_Symbol,PERIOD_H4,0);
   if(cbr==lb)return; lb=cbr;

   MqlDateTime dt; TimeToStruct(TimeCurrent(),dt);
   if(dt.day_of_week==0||dt.day_of_week==6)return; if(dt.day_of_week==5&&dt.hour>16)return;

   // D1 regime
   double d1slope = GetD1Slope();
   if(d1slope < D1_Min_Slope) return;
   bool isStrong = (d1slope >= D1_Strong_Slope);

   // Adaptive MaxPositions
   int effectiveMaxPos = MaxPositions;
   if(UseAdaptiveMaxPos)
      effectiveMaxPos = isStrong ? MaxPos_Strong : MaxPos_Weak;
   if(pc >= effectiveMaxPos) return;

   // W1 trend
   double wf[1],ws[1]; if(CopyBuffer(hW1Fast,0,1,1,wf)<1||CopyBuffer(hW1Slow,0,1,1,ws)<1)return;
   int dir=0; if(wf[0]>ws[0])dir=1; if(wf[0]<ws[0])dir=-1; if(dir==0)return;

   // W1 separation
   double mid=(wf[0]+ws[0])/2; if(mid>0&&MathAbs(wf[0]-ws[0])/mid<W1_Min_Sep)return;

   // D1 alignment
   double d1e[1]; if(CopyBuffer(hD1EMA,0,1,1,d1e)<1)return;
   double d1c=iClose(_Symbol,PERIOD_D1,1),dd=(d1c-d1e[0])/d1e[0];
   if(dir==1&&dd<-D1_Tolerance)return; if(dir==-1&&dd>D1_Tolerance)return;

   // ATR filter
   double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1)return;
   double av=a[0]; if(av<_Point)return;
   double aa=GetAvgATR(); if(aa<=0||av<aa*ATR_Filter)return;

   double h4e[1]; if(CopyBuffer(hH4EMA,0,1,1,h4e)<1)return;

   // Adaptive H4 slope
   int slopeBars = isStrong ? H4_Slope_Strong : H4_Slope_Weak;
   double h4prev[1];
   if(CopyBuffer(hH4EMA,0,slopeBars+1,1,h4prev)<1) return;
   double h4slope = h4e[0] - h4prev[0];
   if(dir==1 && h4slope < 0) return;
   if(dir==-1 && h4slope > 0) return;

   // Adaptive SL
   double slMult = isStrong ? SL_ATR_Mult : SL_Weak_Mult;

   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entryZone = EMA_Zone_ATR * av;

   bool buySignal = false, sellSignal = false;
   if(dir==1)
      buySignal = CheckBuyDip(1,h4e[0],entryZone,BodyRatio) || CheckBuyDip(2,h4e[0],entryZone,BodyRatio);
   if(dir==-1)
      sellSignal = CheckSellDip(1,h4e[0],entryZone,BodyRatio) || CheckSellDip(2,h4e[0],entryZone,BodyRatio);

   if(buySignal)
   { double sd=slMult*av; trade.Buy(CalcLot(sd),_Symbol,ask,NormalizeDouble(ask-sd,_Digits),0,"A32 BUY"); }
   if(sellSignal)
   { double sd=slMult*av; trade.Sell(CalcLot(sd),_Symbol,bid,NormalizeDouble(bid+sd,_Digits),0,"A32 SELL"); }
}
//+------------------------------------------------------------------+
