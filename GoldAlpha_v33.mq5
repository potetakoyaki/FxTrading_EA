//+------------------------------------------------------------------+
//| GoldAlpha_v33.mq5 - v30 + Regime Trail + Time Decay SL          |
//| v30 base (Adaptive MaxPos + Progressive Trail) +                 |
//|   1. Regime-Conditional Trail: weak D1 = tighter trail           |
//|   2. Time Decay SL: tighten SL after N bars without BE           |
//|                                                                  |
//| Hypothesis: Tighter trail in weak regime captures more of small  |
//| moves in 2016 range market. Time decay cuts losers faster.       |
//| Previous "soft trail 2.7/2.3" was global; this is regime-only.   |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "33.00"
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

// --- Regime-Conditional Trail (NEW v33) ---
input bool     UseRegimeTrail   = true;
input double   Trail_ATR_Weak   = 2.5;   // Trail width in weak D1 regime

// --- Time Decay SL (NEW v33) ---
input bool     UseTimeDecay     = true;
input int      DecayBars        = 20;    // H4 bars before decay kicks in
input double   DecayFactor      = 0.75;  // SL tightened to 75% of original

// --- Adaptive MaxPositions ---
input bool     UseAdaptiveMaxPos = true;
input int      MaxPos_Strong     = 3;
input int      MaxPos_Weak       = 2;

// --- General ---
input double   MinLot        = 0.01;
input double   MaxLot        = 1.00;
input int      MagicNumber   = 330033;

CTrade trade;
int hW1Fast, hW1Slow, hD1EMA, hH4EMA, hATR;

// Track entry time per ticket for time decay
struct PosInfo { ulong ticket; datetime entryTime; double origSL; };
PosInfo posTracker[];
int ptCount = 0;

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
   ArrayResize(posTracker, 0);
   ptCount = 0;
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
      else usdjpy = 150.0;
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

// --- Position tracker helpers ---
int FindTracker(ulong ticket)
{ for(int i=0; i<ptCount; i++) if(posTracker[i].ticket==ticket) return i; return -1; }

void AddTracker(ulong ticket, double origSL)
{
   if(FindTracker(ticket) >= 0) return;
   ArrayResize(posTracker, ptCount + 1);
   posTracker[ptCount].ticket = ticket;
   posTracker[ptCount].entryTime = TimeCurrent();
   posTracker[ptCount].origSL = origSL;
   ptCount++;
}

void CleanTrackers()
{
   int j = 0;
   for(int i=0; i<ptCount; i++)
   {
      bool found = false;
      for(int k=PositionsTotal()-1; k>=0; k--)
         if(PositionGetSymbol(k)==_Symbol && (ulong)PositionGetInteger(POSITION_TICKET)==posTracker[i].ticket)
         { found=true; break; }
      if(found) { if(j<i) posTracker[j] = posTracker[i]; j++; }
   }
   ptCount = j;
   if(ptCount < ArraySize(posTracker)) ArrayResize(posTracker, MathMax(ptCount, 1));
}

void ManageTrail()
{
   static datetime lt=0; datetime cb=iTime(_Symbol,PERIOD_H4,0); if(cb==lt)return; lt=cb;
   double d1slope = GetD1Slope();
   bool isStrong = (d1slope >= D1_Strong_Slope);

   static int cleanCtr = 0;
   if(++cleanCtr >= 10) { CleanTrackers(); cleanCtr = 0; }

   for(int i=PositionsTotal()-1;i>=0;i--)
   { if(PositionGetSymbol(i)!=_Symbol||PositionGetInteger(POSITION_MAGIC)!=MagicNumber)continue;
     double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1)return;
     double av=a[0],op=PositionGetDouble(POSITION_PRICE_OPEN),sl=PositionGetDouble(POSITION_SL);
     long pt=PositionGetInteger(POSITION_TYPE); ulong tk=(ulong)PositionGetInteger(POSITION_TICKET);

     // --- Time Decay SL (v33) ---
     if(UseTimeDecay)
     {
        int tidx = FindTracker(tk);
        if(tidx >= 0)
        {
           bool atBE = (pt==POSITION_TYPE_BUY) ? (sl >= op) : (sl > 0 && sl <= op);
           if(!atBE)
           {
              int barsOpen = (int)((TimeCurrent() - posTracker[tidx].entryTime) / (4 * 3600));
              if(barsOpen >= DecayBars)
              {
                 double origDist = MathAbs(posTracker[tidx].origSL - op);
                 double newDist = origDist * DecayFactor;
                 double newSL;
                 if(pt==POSITION_TYPE_BUY)
                 {
                    newSL = NormalizeDouble(op - newDist, _Digits);
                    if(newSL > sl + _Point*10)
                       trade.PositionModify(tk, newSL, 0);
                 }
                 else
                 {
                    newSL = NormalizeDouble(op + newDist, _Digits);
                    if(newSL < sl - _Point*10)
                       trade.PositionModify(tk, newSL, 0);
                 }
              }
           }
        }
     }

     // --- Trail logic ---
     if(pt==POSITION_TYPE_BUY)
     { double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
       double profit=bid-op;
       if(profit>BE_ATR*av&&sl<op){trade.PositionModify(tk,NormalizeDouble(op+0.1*av,_Digits),0);continue;}
       if(sl>=op)
       { double hh=0;for(int j=1;j<=10;j++){double h=iHigh(_Symbol,PERIOD_H4,j);if(h>hh)hh=h;}
         double baseTrail = (UseRegimeTrail && !isStrong) ? Trail_ATR_Weak : Trail_ATR;
         double trailWidth = baseTrail;
         if(UseProgressiveTrail)
         {
            double profitATR = profit / av;
            if(profitATR >= TrailProfit2) trailWidth = Trail_ATR_Tight;
            else if(profitATR >= TrailProfit1) trailWidth = MathMin(Trail_ATR_Med, baseTrail);
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
         double baseTrail = (UseRegimeTrail && !isStrong) ? Trail_ATR_Weak : Trail_ATR;
         double trailWidth = baseTrail;
         if(UseProgressiveTrail)
         {
            double profitATR = profit / av;
            if(profitATR >= TrailProfit2) trailWidth = Trail_ATR_Tight;
            else if(profitATR >= TrailProfit1) trailWidth = MathMin(Trail_ATR_Med, baseTrail);
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

   double d1slope = GetD1Slope();
   if(d1slope < D1_Min_Slope) return;
   bool isStrong = (d1slope >= D1_Strong_Slope);

   int effectiveMaxPos = MaxPositions;
   if(UseAdaptiveMaxPos) effectiveMaxPos = isStrong ? MaxPos_Strong : MaxPos_Weak;
   if(pc >= effectiveMaxPos) return;

   double wf[1],ws[1]; if(CopyBuffer(hW1Fast,0,1,1,wf)<1||CopyBuffer(hW1Slow,0,1,1,ws)<1)return;
   int dir=0; if(wf[0]>ws[0])dir=1; if(wf[0]<ws[0])dir=-1; if(dir==0)return;
   double mid=(wf[0]+ws[0])/2; if(mid>0&&MathAbs(wf[0]-ws[0])/mid<W1_Min_Sep)return;

   double d1e[1]; if(CopyBuffer(hD1EMA,0,1,1,d1e)<1)return;
   double d1c=iClose(_Symbol,PERIOD_D1,1),dd=(d1c-d1e[0])/d1e[0];
   if(dir==1&&dd<-D1_Tolerance)return; if(dir==-1&&dd>D1_Tolerance)return;

   double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1)return;
   double av=a[0]; if(av<_Point)return;
   double aa=GetAvgATR(); if(aa<=0||av<aa*ATR_Filter)return;

   double h4e[1]; if(CopyBuffer(hH4EMA,0,1,1,h4e)<1)return;
   int slopeBars = isStrong ? H4_Slope_Strong : H4_Slope_Weak;
   double h4prev[1];
   if(CopyBuffer(hH4EMA,0,slopeBars+1,1,h4prev)<1) return;
   double h4slope = h4e[0] - h4prev[0];
   if(dir==1 && h4slope < 0) return;
   if(dir==-1 && h4slope > 0) return;

   double slMult = isStrong ? SL_ATR_Mult : SL_Weak_Mult;
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entryZone = EMA_Zone_ATR * av;

   bool buySignal = false, sellSignal = false;
   if(dir==1)
      buySignal = CheckBuyDip(1,h4e[0],entryZone,BodyRatio) || CheckBuyDip(2,h4e[0],entryZone,BodyRatio);
   if(dir==-1)
      sellSignal = CheckSellDip(1,h4e[0],entryZone,BodyRatio) || CheckSellDip(2,h4e[0],entryZone,BodyRatio);

   if(buySignal)
   {
      double sd=slMult*av;
      double slPrice = NormalizeDouble(ask-sd, _Digits);
      if(trade.Buy(CalcLot(sd),_Symbol,ask,slPrice,0,"A33 BUY"))
      {
         if(UseTimeDecay)
            for(int p=PositionsTotal()-1; p>=0; p--)
               if(PositionGetSymbol(p)==_Symbol && PositionGetInteger(POSITION_MAGIC)==MagicNumber)
               { ulong ptk=(ulong)PositionGetInteger(POSITION_TICKET); if(FindTracker(ptk)<0) AddTracker(ptk,slPrice); }
      }
   }
   if(sellSignal)
   {
      double sd=slMult*av;
      double slPrice = NormalizeDouble(bid+sd, _Digits);
      if(trade.Sell(CalcLot(sd),_Symbol,bid,slPrice,0,"A33 SELL"))
      {
         if(UseTimeDecay)
            for(int p=PositionsTotal()-1; p>=0; p--)
               if(PositionGetSymbol(p)==_Symbol && PositionGetInteger(POSITION_MAGIC)==MagicNumber)
               { ulong ptk=(ulong)PositionGetInteger(POSITION_TICKET); if(FindTracker(ptk)<0) AddTracker(ptk,slPrice); }
      }
   }
}
//+------------------------------------------------------------------+
