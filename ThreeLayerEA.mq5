//+------------------------------------------------------------------+
//|                                              ThreeLayerEA.mq5    |
//|            XAUUSD 5分足 3層フィルター + 資金管理 EA               |
//|            v2.0: ボラレジーム + セッション + モメンタム + 半利確     |
//+------------------------------------------------------------------+
#property copyright "ThreeLayer Trading System"
#property version   "2.00"
#property description "3層フィルターEA v2.0: ボラレジーム + セッション + 半利確"

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| 入力パラメータ                                                      |
//+------------------------------------------------------------------+
input group "=== 第1層: 一目均衡表（環境認識） ==="
input int    Ichi_Tenkan       = 9;
input int    Ichi_Kijun        = 26;
input int    Ichi_SenkouB      = 52;

input group "=== 第2層: UT Bot Alerts ==="
input double UT_KeyValue       = 2.0;     // UT Bot KeyValue（基準値）
input int    UT_ATR_Period     = 10;
input double UT_HighVol_Key    = 2.5;     // ★ 高ボラ時のKeyValue（感度を下げる）

input group "=== 第2層: SMC（構造分析） ==="
input int    SMC_Lookback      = 30;
input int    SMC_SwingLen      = 5;

input group "=== 第3層: RSI + ATR フィルター ==="
input int    RSI_Period        = 14;
input double RSI_OB            = 70.0;
input double RSI_OS            = 30.0;
input int    ATR_Period        = 14;
input double ATR_MinThreshold  = 2.0;     // ATR最低閾値（低ボラ排除）

input group "=== 資金管理 ==="
input double RiskPercent       = 1.0;
input double SL_ATR_Multi      = 2.0;
input double TP_ATR_Multi      = 4.0;
input double MaxLots           = 5.0;
input double MinLots           = 0.01;

input group "=== ボラティリティレジーム ==="
input int    VolRegime_Period  = 50;      // ★ ATR平均期間
input double VolRegime_Low     = 0.7;     // ★ 低ボラ閾値（スキップ）
input double VolRegime_High    = 1.5;     // ★ 高ボラ閾値
input double HighVol_SL_Bonus  = 0.5;     // ★ 高ボラ時SL追加倍率

input group "=== 一般設定 ==="
input int    MaxPositions      = 1;
input int    MagicNumber       = 30260313;
input int    MaxSpread         = 50;
input int    CooldownMinutes   = 120;
input bool   UseSessionFilter  = true;    // ★ セッションフィルター
input bool   UseMomentum       = true;    // ★ モメンタム確認

input group "=== 半利確 ==="
input bool   UsePartialClose   = true;    // ★ 半分利確を有効化
input double PartialCloseRatio = 0.5;     // 利確するポジション割合
input double PartialTP_Ratio   = 0.5;     // TP距離の何%で半利確

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;

int            h_ichimoku;
int            h_rsi;
int            h_atr;
int            h_ut_atr;

// UT Bot内部状態
double         utTrailStop;
bool           utBuySignal;
bool           utSellSignal;

// バー管理
datetime       lastBarTime;
datetime       lastSLTime;

// 半利確トラッキング
ulong          partialClosedTickets[];

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   h_ichimoku = iIchimoku(_Symbol, PERIOD_CURRENT, Ichi_Tenkan, Ichi_Kijun, Ichi_SenkouB);
   h_rsi = iRSI(_Symbol, PERIOD_CURRENT, RSI_Period, PRICE_CLOSE);
   h_atr = iATR(_Symbol, PERIOD_CURRENT, ATR_Period);
   h_ut_atr = iATR(_Symbol, PERIOD_CURRENT, UT_ATR_Period);

   if(h_ichimoku == INVALID_HANDLE || h_rsi == INVALID_HANDLE ||
      h_atr == INVALID_HANDLE || h_ut_atr == INVALID_HANDLE)
   {
      Print("インジケーターハンドルの作成に失敗");
      return INIT_FAILED;
   }

   utTrailStop = 0;
   utBuySignal = false;
   utSellSignal = false;
   lastBarTime = 0;

   Print("ThreeLayerEA v2.0 初期化完了");
   Print("   UT Bot: Key=", UT_KeyValue, " (高ボラ時:", UT_HighVol_Key, ")");
   Print("   ボラレジーム: Low<", VolRegime_Low, " High>", VolRegime_High);
   Print("   半利確: ", UsePartialClose ? "ON" : "OFF",
         " セッション: ", UseSessionFilter ? "ON" : "OFF");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(h_ichimoku != INVALID_HANDLE) IndicatorRelease(h_ichimoku);
   if(h_rsi != INVALID_HANDLE)      IndicatorRelease(h_rsi);
   if(h_atr != INVALID_HANDLE)      IndicatorRelease(h_atr);
   if(h_ut_atr != INVALID_HANDLE)   IndicatorRelease(h_ut_atr);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // 半利確管理（毎ティック）
   ManagePartialClose();

   // 新バー時のみ判定
   if(!IsNewBar()) return;

   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > MaxSpread) return;

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
      return;

   if(CountMyPositions() >= MaxPositions) return;

   // SL後クールダウン
   if(lastSLTime > 0 && TimeCurrent() - lastSLTime < CooldownMinutes * 60)
      return;

   // ★ セッションフィルター（Gold は非活発時間帯を回避）
   if(UseSessionFilter && !IsActiveSession())
      return;

   //--- インジケーター値取得 ---
   double senkouA[], senkouB[];
   double rsi[], atr[], utAtr[];

   if(!GetBuffer(h_ichimoku, 2, 0, 3, senkouA)) return;
   if(!GetBuffer(h_ichimoku, 3, 0, 3, senkouB)) return;
   if(!GetBuffer(h_rsi, 0, 1, 1, rsi)) return;
   if(!GetBuffer(h_atr, 0, 1, 1, atr)) return;
   if(!GetBuffer(h_ut_atr, 0, 1, 2, utAtr)) return;

   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double close2 = iClose(_Symbol, PERIOD_CURRENT, 2);
   double close3 = iClose(_Symbol, PERIOD_CURRENT, 3);

   if(close1 == 0 || close2 == 0) return;

   double cloudUpper = MathMax(senkouA[1], senkouB[1]);
   double cloudLower = MathMin(senkouA[1], senkouB[1]);

   //=== 第1層: 一目均衡表（環境認識） ===
   bool allowBuy  = (close1 > cloudUpper);
   bool allowSell = (close1 < cloudLower);

   if(!allowBuy && !allowSell) return;

   //=== ★ ボラティリティレジーム判定 ===
   double atrVal = atr[0];
   int volRegime = GetVolatilityRegime(atrVal);
   if(volRegime == 0) return;  // 低ボラ → スキップ

   //=== 第2層: UT Bot Alerts ===
   // ★ 高ボラ時はKeyValueを上げて偽シグナルを減らす
   double effectiveKey = (volRegime == 2) ? UT_HighVol_Key : UT_KeyValue;
   UpdateUTBot(close1, close2, utAtr[0], utAtr[1], effectiveKey);

   //=== 第2層: SMC（構造分析） ===
   bool smcAllowBuy  = false;
   bool smcAllowSell = false;
   CheckSMC(smcAllowBuy, smcAllowSell);

   //=== 第3層: RSI + ATR フィルター ===
   double rsiVal = rsi[0];

   bool rsiAllowBuy  = (rsiVal < RSI_OB);
   bool rsiAllowSell = (rsiVal > RSI_OS);
   bool atrAllow     = (atrVal >= ATR_MinThreshold);

   //=== ★ モメンタム確認 ===
   bool momentumOK = true;
   if(UseMomentum && close3 > 0)
   {
      double momThreshold = atrVal * 0.1;
      if(allowBuy)  momentumOK = (close1 - close3 > -momThreshold);  // 下降モメンタムでなければOK
      if(allowSell) momentumOK = (close3 - close1 > -momThreshold);  // 上昇モメンタムでなければOK
   }

   //=== エントリー判定（全AND） ===
   // ★ 動的SL/TP（高ボラ時はSL拡大）
   double slMulti = SL_ATR_Multi;
   if(volRegime == 2) slMulti += HighVol_SL_Bonus;

   if(allowBuy && utBuySignal && smcAllowBuy && rsiAllowBuy && atrAllow && momentumOK)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double slDist = atrVal * slMulti;
      double tpDist = atrVal * TP_ATR_Multi;

      double sl = NormalizeDouble(ask - slDist, _Digits);
      double tp = NormalizeDouble(ask + tpDist, _Digits);
      double lot = CalcLotSize(slDist);

      if(lot > 0 && trade.Buy(lot, _Symbol, ask, sl, tp,
         StringFormat("3L BUY R:%d ATR:%.1f", volRegime, atrVal)))
         Print("BUY Regime:", volRegime,
               " RSI:", DoubleToString(rsiVal,1),
               " ATR:", DoubleToString(atrVal,2),
               " SL:", DoubleToString(slDist,2),
               " TP:", DoubleToString(tpDist,2),
               " Lot:", DoubleToString(lot,2));
   }

   if(allowSell && utSellSignal && smcAllowSell && rsiAllowSell && atrAllow && momentumOK)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double slDist = atrVal * slMulti;
      double tpDist = atrVal * TP_ATR_Multi;

      double sl = NormalizeDouble(bid + slDist, _Digits);
      double tp = NormalizeDouble(bid - tpDist, _Digits);
      double lot = CalcLotSize(slDist);

      if(lot > 0 && trade.Sell(lot, _Symbol, bid, sl, tp,
         StringFormat("3L SELL R:%d ATR:%.1f", volRegime, atrVal)))
         Print("SELL Regime:", volRegime,
               " RSI:", DoubleToString(rsiVal,1),
               " ATR:", DoubleToString(atrVal,2),
               " SL:", DoubleToString(slDist,2),
               " TP:", DoubleToString(tpDist,2),
               " Lot:", DoubleToString(lot,2));
   }
}

//+------------------------------------------------------------------+
//| 新バー判定                                                         |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime currentBar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(currentBar == 0) return false;
   if(currentBar == lastBarTime) return false;
   lastBarTime = currentBar;
   return true;
}

//+------------------------------------------------------------------+
//| ★ ボラティリティレジーム判定                                        |
//+------------------------------------------------------------------+
int GetVolatilityRegime(double currentATR)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(h_atr, 0, 1, VolRegime_Period, atr) < VolRegime_Period) return 1;

   double sum = 0;
   for(int i = 0; i < VolRegime_Period; i++)
      sum += atr[i];
   double avgATR = sum / VolRegime_Period;

   if(avgATR <= 0) return 1;
   double ratio = currentATR / avgATR;

   if(ratio < VolRegime_Low) return 0;   // 低ボラ → スキップ
   if(ratio > VolRegime_High) return 2;  // 高ボラ
   return 1;                              // 通常
}

//+------------------------------------------------------------------+
//| ★ セッションフィルター（Gold用: 非活発時間帯を回避）                |
//+------------------------------------------------------------------+
bool IsActiveSession()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   // アジア深夜 (22:00-2:00 サーバー時間) はGoldの流動性低い
   if(dt.hour >= 22 || dt.hour < 2) return false;

   // 金曜夜
   if(dt.day_of_week == 5 && dt.hour >= 18) return false;

   return true;
}

//+------------------------------------------------------------------+
//| UT Bot Alerts 計算（★ 動的KeyValue対応）                           |
//+------------------------------------------------------------------+
void UpdateUTBot(double close1, double close2, double curATR, double prevATR, double keyValue)
{
   double nLoss = curATR * keyValue;

   double prevTrail = utTrailStop;

   if(close1 > prevTrail && close2 > prevTrail)
      utTrailStop = MathMax(prevTrail, close1 - nLoss);
   else if(close1 < prevTrail && close2 < prevTrail)
      utTrailStop = MathMin(prevTrail, close1 + nLoss);
   else if(close1 > prevTrail)
      utTrailStop = close1 - nLoss;
   else
      utTrailStop = close1 + nLoss;

   utBuySignal  = (close1 > utTrailStop && close2 <= prevTrail);
   utSellSignal = (close1 < utTrailStop && close2 >= prevTrail);
}

//+------------------------------------------------------------------+
//| SMC（スマートマネーコンセプト）分析                                 |
//+------------------------------------------------------------------+
void CheckSMC(bool &allowBuy, bool &allowSell)
{
   allowBuy = false;
   allowSell = false;

   int totalBars = SMC_Lookback + SMC_SwingLen + 1;

   double high[], low[], close[];
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);

   if(CopyHigh(_Symbol, PERIOD_CURRENT, 1, totalBars, high) < totalBars) return;
   if(CopyLow(_Symbol, PERIOD_CURRENT, 1, totalBars, low) < totalBars) return;
   if(CopyClose(_Symbol, PERIOD_CURRENT, 1, totalBars, close) < totalBars) return;

   double swingHighs[];
   double swingLows[];
   int    swingHighIdx[];
   int    swingLowIdx[];
   ArrayResize(swingHighs, 0);
   ArrayResize(swingLows, 0);
   ArrayResize(swingHighIdx, 0);
   ArrayResize(swingLowIdx, 0);

   for(int i = SMC_SwingLen; i < totalBars - SMC_SwingLen; i++)
   {
      bool isSwingHigh = true;
      for(int j = 1; j <= SMC_SwingLen; j++)
      {
         if(high[i] <= high[i-j] || high[i] <= high[i+j])
         {
            isSwingHigh = false;
            break;
         }
      }
      if(isSwingHigh)
      {
         int sz = ArraySize(swingHighs);
         ArrayResize(swingHighs, sz + 1);
         ArrayResize(swingHighIdx, sz + 1);
         swingHighs[sz] = high[i];
         swingHighIdx[sz] = i;
      }

      bool isSwingLow = true;
      for(int j = 1; j <= SMC_SwingLen; j++)
      {
         if(low[i] >= low[i-j] || low[i] >= low[i+j])
         {
            isSwingLow = false;
            break;
         }
      }
      if(isSwingLow)
      {
         int sz = ArraySize(swingLows);
         ArrayResize(swingLows, sz + 1);
         ArrayResize(swingLowIdx, sz + 1);
         swingLows[sz] = low[i];
         swingLowIdx[sz] = i;
      }
   }

   bool bearishCHoCH = false;
   bool bullishCHoCH = false;

   double latestClose = close[0];

   int shCount = ArraySize(swingHighs);
   int slCount = ArraySize(swingLows);

   if(shCount >= 2)
   {
      if(swingHighs[0] > swingHighs[1] && slCount >= 1)
      {
         if(latestClose < swingLows[0])
            bearishCHoCH = true;
      }
   }

   if(slCount >= 2)
   {
      if(swingLows[0] < swingLows[1] && shCount >= 1)
      {
         if(latestClose > swingHighs[0])
            bullishCHoCH = true;
      }
   }

   // ブロッカー型: CHoCHが出ていなければ許可
   allowBuy = !bearishCHoCH;
   allowSell = !bullishCHoCH;
}

//+------------------------------------------------------------------+
//| ロット計算（リスク%ベース）                                         |
//+------------------------------------------------------------------+
double CalcLotSize(double slDistance)
{
   if(slDistance <= 0) return 0;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * RiskPercent / 100.0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0 || tickSize <= 0) return 0;

   double riskPerLot = (slDistance / tickSize) * tickValue;
   if(riskPerLot <= 0) return 0;

   double lots = riskAmount / riskPerLot;

   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;

   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(MinLots, MathMin(MaxLots, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| ★ 半利確管理（毎ティック実行）                                      |
//+------------------------------------------------------------------+
void ManagePartialClose()
{
   if(!UsePartialClose) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      if(IsPartialClosed(ticket)) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double tp        = PositionGetDouble(POSITION_TP);
      double volume    = PositionGetDouble(POSITION_VOLUME);
      long   posType   = PositionGetInteger(POSITION_TYPE);

      if(posType == POSITION_TYPE_BUY && tp > openPrice)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double tpDist = tp - openPrice;
         double halfTPDist = tpDist * PartialTP_Ratio;

         if(bid - openPrice >= halfTPDist)
         {
            double closeLot = NormalizeDouble(volume * PartialCloseRatio, 2);
            if(closeLot >= MinLots)
            {
               if(trade.PositionClosePartial(ticket, closeLot))
               {
                  MarkPartialClosed(ticket);
                  // SLを建値+少し上に移動
                  double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);
                  trade.PositionModify(ticket, newSL, tp);
                  Print("3Layer 半利確 BUY: ", DoubleToString(closeLot, 2), "lot決済");
               }
            }
         }
      }
      else if(posType == POSITION_TYPE_SELL && tp < openPrice)
      {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double tpDist = openPrice - tp;
         double halfTPDist = tpDist * PartialTP_Ratio;

         if(openPrice - ask >= halfTPDist)
         {
            double closeLot = NormalizeDouble(volume * PartialCloseRatio, 2);
            if(closeLot >= MinLots)
            {
               if(trade.PositionClosePartial(ticket, closeLot))
               {
                  MarkPartialClosed(ticket);
                  double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);
                  trade.PositionModify(ticket, newSL, tp);
                  Print("3Layer 半利確 SELL: ", DoubleToString(closeLot, 2), "lot決済");
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| 半利確トラッキング                                                  |
//+------------------------------------------------------------------+
bool IsPartialClosed(ulong ticket)
{
   for(int i = 0; i < ArraySize(partialClosedTickets); i++)
      if(partialClosedTickets[i] == ticket) return true;
   return false;
}

void MarkPartialClosed(ulong ticket)
{
   int sz = ArraySize(partialClosedTickets);
   ArrayResize(partialClosedTickets, sz + 1);
   partialClosedTickets[sz] = ticket;

   if(sz > 100)
   {
      for(int i = 0; i < 50; i++)
         partialClosedTickets[i] = partialClosedTickets[i + 50];
      ArrayResize(partialClosedTickets, sz - 49);
   }
}

//+------------------------------------------------------------------+
//| SL検知 — クールダウン用                                            |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      if(HistoryDealSelect(trans.deal))
      {
         long dealMagic  = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
         long dealEntry  = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
         long dealReason = HistoryDealGetInteger(trans.deal, DEAL_REASON);

         if(dealMagic == MagicNumber && dealEntry == DEAL_ENTRY_OUT && dealReason == DEAL_REASON_SL)
         {
            lastSLTime = TimeCurrent();
            Print("SLクールダウン開始: ", CooldownMinutes, "分間エントリー停止");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| 自ポジション数カウント                                              |
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
//| インジケーターバッファ取得ユーティリティ                             |
//+------------------------------------------------------------------+
bool GetBuffer(int handle, int buffer, int shift, int count, double &result[])
{
   ArraySetAsSeries(result, true);
   if(CopyBuffer(handle, buffer, shift, count, result) < count)
      return false;
   return true;
}

//+------------------------------------------------------------------+
