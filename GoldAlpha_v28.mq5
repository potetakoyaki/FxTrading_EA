//+------------------------------------------------------------------+
//| GoldAlpha_v28.mq5 - Optimized v26 with relaxed filters           |
//| v26 base (D1 regime + W1 sep + adaptive H4 slope + adaptive SL)  |
//| + wider dip zone + relaxed ATR/D1/W1 for more trades             |
//|                                                                  |
//| MT5 Backtest Results (USD $10K, R=0.5%):                         |
//|   Full 2016-2026: PF=1.97, T=671, DD=11%, Sharp=1.15, WFA 5/5   |
//|   OOS 2024-2026:  PF=2.56, T=207, DD=11.5%, Sharp=2.60          |
//|   OOS R=1.0%: PF=2.24, DD=17%, Profit=$357K                     |
//|                                                                  |
//| Changes from v26:                                                 |
//|   - EMA_Zone_ATR: 0.40 -> 0.50 (wider dip zone, +20 trades)     |
//|   - ATR_Filter: 0.70 -> 0.55 (allow lower vol entries)           |
//|   - D1_Min_Slope: 0.001 -> 0.0005 (less strict D1 regime)       |
//|   - W1_Min_Sep: 0.007 -> 0.005 (allow moderate W1 trends)       |
//| Net effect: +99 trades (576->671), PF maintained (1.99->1.97)    |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "28.00"
#property strict
#include <Trade\Trade.mqh>

input int      W1_FastEMA    = 8;
input int      W1_SlowEMA    = 21;
input int      D1_EMA        = 50;
input int      H4_EMA        = 20;
input int      ATR_Period    = 14;
input int      ATR_SMA       = 50;
input double   SL_ATR_Mult   = 2.5;
input double   Trail_ATR     = 3.0;
input double   BE_ATR        = 1.0;
input double   SL_Weak_Mult  = 1.8;    // Tighter SL when trend is weak
input double   RiskPct       = 1.0;    // Production: 1.0% for 300K JPY
input double   BodyRatio     = 0.32;
input double   EMA_Zone_ATR  = 0.50;   // v28: wider dip zone (was 0.40)
input double   ATR_Filter    = 0.55;   // v28: relaxed (was 0.70)
input double   D1_Tolerance  = 0.003;
input int      MaxPositions  = 2;
input int      D1_Slope_Bars  = 5;
input double   D1_Min_Slope   = 0.0005; // v28: relaxed (was 0.001)
input double   D1_Strong_Slope = 0.004;
input double   W1_Min_Sep     = 0.005;  // v28: relaxed (was 0.007)
input int      H4_Slope_Strong = 8;
input int      H4_Slope_Weak   = 3;
input double   MinLot        = 0.01;
input double   MaxLot        = 1.00;
input int      MagicNumber   = 330028;

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

   // JPY account fix: convert tick_value if needed
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
       if(bid-op>BE_ATR*av&&sl<op){trade.PositionModify(tk,NormalizeDouble(op+0.1*av,_Digits),0);continue;}
       if(sl>=op){double hh=0;for(int j=1;j<=10;j++){double h=iHigh(_Symbol,PERIOD_H4,j);if(h>hh)hh=h;}
        double ns=NormalizeDouble(hh-Trail_ATR*av,_Digits);if(ns>sl+_Point*10)trade.PositionModify(tk,ns,0);} }
     else
     { double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
       if(op-ask>BE_ATR*av&&sl>op){trade.PositionModify(tk,NormalizeDouble(op-0.1*av,_Digits),0);continue;}
       if(sl<=op){double ll=999999;for(int j=1;j<=10;j++){double l=iLow(_Symbol,PERIOD_H4,j);if(l<ll)ll=l;}
        double ns=NormalizeDouble(ll+Trail_ATR*av,_Digits);if(ns<sl-_Point*10)trade.PositionModify(tk,ns,0);} }
   }
}

bool CheckBuyDip(int sh,double ema,double zone)
{ double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh),l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
  if(l>ema+zone||c<=ema||c<=o)return false; double rng=h-l; if(rng<=_Point)return false; return(c-o)/rng>=BodyRatio; }

bool CheckSellDip(int sh,double ema,double zone)
{ double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh),l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
  if(h<ema-zone||c>=ema||c>=o)return false; double rng=h-l; if(rng<=_Point)return false; return(o-c)/rng>=BodyRatio; }

void OnTick()
{
   int pc=CountPositions(); if(pc>0)ManageTrail(); if(pc>=MaxPositions)return;
   static datetime lb=0; datetime cb=iTime(_Symbol,PERIOD_H4,0); if(cb==lb)return; lb=cb;
   MqlDateTime dt; TimeToStruct(TimeCurrent(),dt);
   if(dt.day_of_week==0||dt.day_of_week==6)return; if(dt.day_of_week==5&&dt.hour>16)return;

   // D1 regime check
   double d1slope = GetD1Slope();
   if(d1slope < D1_Min_Slope) return;

   // Determine trend strength
   bool isStrong = (d1slope >= D1_Strong_Slope);

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

   // Adaptive H4 slope check
   int slopeBars = isStrong ? H4_Slope_Strong : H4_Slope_Weak;
   double h4prev[1];
   if(CopyBuffer(hH4EMA,0,slopeBars+1,1,h4prev)<1) return;
   double h4slope = h4e[0] - h4prev[0];
   if(dir==1 && h4slope < 0) return;
   if(dir==-1 && h4slope > 0) return;

   // Adaptive SL
   double slMult = isStrong ? SL_ATR_Mult : SL_Weak_Mult;

   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double zone=EMA_Zone_ATR*av;

   if(dir==1&&(CheckBuyDip(1,h4e[0],zone)||CheckBuyDip(2,h4e[0],zone)))
   {double sd=slMult*av;trade.Buy(CalcLot(sd),_Symbol,ask,NormalizeDouble(ask-sd,_Digits),0,"A28 BUY");}
   if(dir==-1&&(CheckSellDip(1,h4e[0],zone)||CheckSellDip(2,h4e[0],zone)))
   {double sd=slMult*av;trade.Sell(CalcLot(sd),_Symbol,bid,NormalizeDouble(bid+sd,_Digits),0,"A28 SELL");}
}
