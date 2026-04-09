//+------------------------------------------------------------------+
//| GoldAlpha_v24.mq5 - Regime-Switching: Trend + Range Logic        |
//| Trend mode: v23 (W1 trend + dip-buy/sell at H4 EMA)             |
//| Range mode: Mean-reversion at H4 EMA extremes                    |
//| Regime detection: D1 slope + W1 EMA separation                   |
//+------------------------------------------------------------------+
#property copyright "Test"
#property version   "24.00"
#property strict

#include <Trade\Trade.mqh>

input int      W1_FastEMA    = 8;
input int      W1_SlowEMA    = 21;
input int      D1_EMA        = 50;
input int      H4_EMA        = 20;
input int      ATR_Period    = 14;
input int      ATR_SMA       = 50;

// Trend mode params
input double   SL_ATR_Mult   = 2.5;
input double   Trail_ATR     = 3.0;
input double   BE_ATR        = 1.0;

// Range mode params
input double   Range_SL_ATR  = 1.5;  // Tighter SL for range trades
input double   Range_TP_ATR  = 1.0;  // Fixed TP for range (mean reversion)

input double   RiskPct       = 0.2;
input double   BodyRatio     = 0.32;
input double   EMA_Zone_ATR  = 0.40;
input double   ATR_Filter    = 0.70;
input double   D1_Tolerance  = 0.003;
input int      MaxPositions  = 2;
input int      D1_Slope_Bars  = 5;
input double   D1_Min_Slope   = 0.001;
input double   W1_Min_Sep     = 0.007;
input double   MinLot        = 0.01;
input double   MaxLot        = 0.15;
input int      MagicNumber   = 330024;

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
{
   IndicatorRelease(hW1Fast); IndicatorRelease(hW1Slow);
   IndicatorRelease(hD1EMA);  IndicatorRelease(hH4EMA); IndicatorRelease(hATR);
}

int CountPositions()
{
   int count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
      if(PositionGetSymbol(i)==_Symbol && PositionGetInteger(POSITION_MAGIC)==MagicNumber) count++;
   return count;
}

double CalcLot(double slDist)
{
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   double rm=eq*RiskPct/100.0;
   double tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tv<=0||ts<=0||slDist<=0) return MinLot;
   double lot=rm/(slDist/ts*tv);
   lot=MathFloor(lot/0.01)*0.01;
   return MathMax(MinLot,MathMin(MaxLot,lot));
}

double GetAvgATR()
{
   double buf[];
   if(CopyBuffer(hATR,0,1,ATR_SMA+1,buf)<ATR_SMA+1) return -1;
   double s=0; for(int i=0;i<ATR_SMA;i++) s+=buf[i];
   return s/ATR_SMA;
}

bool IsTrendRegime()
{
   // D1 slope check
   double cur[],prev[];
   if(CopyBuffer(hD1EMA,0,1,1,cur)<1) return false;
   if(CopyBuffer(hD1EMA,0,D1_Slope_Bars+1,1,prev)<1) return false;
   if(prev[0]<=0) return false;
   if(MathAbs(cur[0]-prev[0])/prev[0] < D1_Min_Slope) return false;

   // W1 separation check
   double wf[1],ws[1];
   if(CopyBuffer(hW1Fast,0,1,1,wf)<1) return false;
   if(CopyBuffer(hW1Slow,0,1,1,ws)<1) return false;
   double mid=(wf[0]+ws[0])/2.0;
   if(mid<=0) return false;
   if(MathAbs(wf[0]-ws[0])/mid < W1_Min_Sep) return false;

   return true;
}

void ManageTrail()
{
   static datetime lastTrailBar=0;
   datetime cb=iTime(_Symbol,PERIOD_H4,0);
   if(cb==lastTrailBar) return;
   lastTrailBar=cb;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetSymbol(i)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=MagicNumber) continue;
      double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1) return;
      double av=a[0],op=PositionGetDouble(POSITION_PRICE_OPEN),sl=PositionGetDouble(POSITION_SL);
      long pt=PositionGetInteger(POSITION_TYPE); ulong tk=PositionGetInteger(POSITION_TICKET);
      // Only trail trend trades (no TP set)
      double tp=PositionGetDouble(POSITION_TP);
      if(tp > 0) continue;  // Range trades have TP, skip trailing
      if(pt==POSITION_TYPE_BUY)
      {
         double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID),pr=bid-op;
         if(pr>BE_ATR*av&&sl<op){trade.PositionModify(tk,NormalizeDouble(op+0.1*av,_Digits),tp);continue;}
         if(sl>=op){double hh=0;for(int j=1;j<=10;j++){double h=iHigh(_Symbol,PERIOD_H4,j);if(h>hh)hh=h;}
          double ns=NormalizeDouble(hh-Trail_ATR*av,_Digits);if(ns>sl+_Point*10)trade.PositionModify(tk,ns,tp);}
      }
      else
      {
         double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),pr=op-ask;
         if(pr>BE_ATR*av&&sl>op){trade.PositionModify(tk,NormalizeDouble(op-0.1*av,_Digits),tp);continue;}
         if(sl<=op){double ll=999999;for(int j=1;j<=10;j++){double l=iLow(_Symbol,PERIOD_H4,j);if(l<ll)ll=l;}
          double ns=NormalizeDouble(ll+Trail_ATR*av,_Digits);if(ns<sl-_Point*10)trade.PositionModify(tk,ns,tp);}
      }
   }
}

bool CheckBuyDip(int sh,double ema,double zone)
{
   double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh);
   double l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
   if(l>ema+zone||c<=ema||c<=o) return false;
   double rng=h-l; if(rng<=_Point) return false;
   return (c-o)/rng>=BodyRatio;
}

bool CheckSellDip(int sh,double ema,double zone)
{
   double o=iOpen(_Symbol,PERIOD_H4,sh),c=iClose(_Symbol,PERIOD_H4,sh);
   double l=iLow(_Symbol,PERIOD_H4,sh),h=iHigh(_Symbol,PERIOD_H4,sh);
   if(h<ema-zone||c>=ema||c>=o) return false;
   double rng=h-l; if(rng<=_Point) return false;
   return (o-c)/rng>=BodyRatio;
}

void OnTick()
{
   int pc=CountPositions();
   if(pc>0) ManageTrail();
   if(pc>=MaxPositions) return;

   static datetime lastBar=0;
   datetime cb=iTime(_Symbol,PERIOD_H4,0);
   if(cb==lastBar) return;
   lastBar=cb;

   MqlDateTime dt; TimeToStruct(TimeCurrent(),dt);
   if(dt.day_of_week==0||dt.day_of_week==6) return;
   if(dt.day_of_week==5&&dt.hour>16) return;

   double a[1]; if(CopyBuffer(hATR,0,1,1,a)<1) return;
   double av=a[0]; if(av<_Point) return;
   double aa=GetAvgATR(); if(aa<=0) return;
   if(av<aa*ATR_Filter) return;

   double h4e[1]; if(CopyBuffer(hH4EMA,0,1,1,h4e)<1) return;
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double zone=EMA_Zone_ATR*av;

   bool isTrend = IsTrendRegime();

   if(isTrend)
   {
      // === TREND MODE: Original v23 logic ===
      double wf[1],ws[1];
      if(CopyBuffer(hW1Fast,0,1,1,wf)<1) return;
      if(CopyBuffer(hW1Slow,0,1,1,ws)<1) return;
      int dir=0;
      if(wf[0]>ws[0]) dir=1;
      if(wf[0]<ws[0]) dir=-1;
      if(dir==0) return;

      double d1e[1]; if(CopyBuffer(hD1EMA,0,1,1,d1e)<1) return;
      double d1c=iClose(_Symbol,PERIOD_D1,1);
      double dd=(d1c-d1e[0])/d1e[0];
      if(dir==1&&dd<-D1_Tolerance) return;
      if(dir==-1&&dd>D1_Tolerance) return;

      if(dir==1&&(CheckBuyDip(1,h4e[0],zone)||CheckBuyDip(2,h4e[0],zone)))
      {double sd=SL_ATR_Mult*av;trade.Buy(CalcLot(sd),_Symbol,ask,NormalizeDouble(ask-sd,_Digits),0,"A24T BUY");}
      if(dir==-1&&(CheckSellDip(1,h4e[0],zone)||CheckSellDip(2,h4e[0],zone)))
      {double sd=SL_ATR_Mult*av;trade.Sell(CalcLot(sd),_Symbol,bid,NormalizeDouble(bid+sd,_Digits),0,"A24T SELL");}
   }
   else
   {
      // === RANGE MODE: Mean-reversion at EMA ===
      // Buy when price dips below EMA and bounces back (same dip pattern but with TP)
      // Sell when price spikes above EMA and drops back

      // Range buy: price was below EMA, closed above (bounce off support)
      if(CheckBuyDip(1,h4e[0],zone)||CheckBuyDip(2,h4e[0],zone))
      {
         double sd=Range_SL_ATR*av;
         double tp_price=NormalizeDouble(ask+Range_TP_ATR*av,_Digits);
         trade.Buy(CalcLot(sd),_Symbol,ask,NormalizeDouble(ask-sd,_Digits),tp_price,"A24R BUY");
      }

      // Range sell: price was above EMA, closed below (bounce off resistance)
      if(CheckSellDip(1,h4e[0],zone)||CheckSellDip(2,h4e[0],zone))
      {
         double sd=Range_SL_ATR*av;
         double tp_price=NormalizeDouble(bid-Range_TP_ATR*av,_Digits);
         trade.Sell(CalcLot(sd),_Symbol,bid,NormalizeDouble(bid+sd,_Digits),tp_price,"A24R SELL");
      }
   }
}
