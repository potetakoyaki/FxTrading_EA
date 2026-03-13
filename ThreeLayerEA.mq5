//+------------------------------------------------------------------+
//|                                              ThreeLayerEA.mq5    |
//|            XAUUSD 5分足 3層フィルター + 資金管理 EA               |
//|            一目均衡表 + UT Bot/SMC + RSI/ATR                      |
//+------------------------------------------------------------------+
#property copyright "ThreeLayer Trading System"
#property version   "1.10"
#property description "3層フィルターEA: 一目均衡表(環境) + UT Bot+SMC(エントリー) + RSI+ATR(フィルター)"

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| 入力パラメータ                                                      |
//+------------------------------------------------------------------+
input group "=== 第1層: 一目均衡表（環境認識） ==="
input int    Ichi_Tenkan       = 9;       // 転換線期間
input int    Ichi_Kijun        = 26;      // 基準線期間
input int    Ichi_SenkouB      = 52;      // 先行スパンB期間

input group "=== 第2層: UT Bot Alerts ==="
input double UT_KeyValue       = 2.0;     // UT Bot KeyValue ★偽シグナル削減
input int    UT_ATR_Period     = 10;      // UT Bot ATR期間

input group "=== 第2層: SMC（構造分析） ==="
input int    SMC_Lookback      = 30;      // SMC ルックバック本数
input int    SMC_SwingLen      = 5;       // スイングハイ/ロー判定の左右本数

input group "=== 第3層: RSI + ATR フィルター ==="
input int    RSI_Period        = 14;      // RSI期間
input double RSI_OB            = 70.0;    // RSI 買われすぎ
input double RSI_OS            = 30.0;    // RSI 売られすぎ
input int    ATR_Period        = 14;      // ATR期間
input double ATR_MinThreshold  = 2.0;     // ATR最低閾値（低ボラ排除）

input group "=== 資金管理 ==="
input double RiskPercent       = 1.0;     // 1トレードリスク（口座の%） ★DD抑制
input double SL_ATR_Multi      = 2.0;     // SL = ATR × この倍率 ★拡大
input double TP_ATR_Multi      = 4.0;     // TP = ATR × この倍率 ★RR1:2維持
input double MaxLots           = 5.0;     // 最大ロット
input double MinLots           = 0.01;    // 最小ロット

input group "=== 一般設定 ==="
input int    MaxPositions      = 1;       // 最大同時ポジション数
input int    MagicNumber       = 30260313;// マジックナンバー
input int    MaxSpread         = 50;      // 最大スプレッド(ポイント)
input int    CooldownMinutes   = 120;     // SL後のエントリー禁止時間(分) ★追加

//+------------------------------------------------------------------+
//| グローバル変数                                                      |
//+------------------------------------------------------------------+
CTrade         trade;

// インジケーターハンドル
int            h_ichimoku;
int            h_rsi;
int            h_atr;
int            h_ut_atr;       // UT Bot用ATR

// UT Bot内部状態
double         utTrailStop;
bool           utBuySignal;
bool           utSellSignal;

// バー管理
datetime       lastBarTime;
datetime       lastSLTime;      // SLクールダウン用

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   // 一目均衡表
   h_ichimoku = iIchimoku(_Symbol, PERIOD_CURRENT, Ichi_Tenkan, Ichi_Kijun, Ichi_SenkouB);

   // RSI
   h_rsi = iRSI(_Symbol, PERIOD_CURRENT, RSI_Period, PRICE_CLOSE);

   // ATR（フィルター用）
   h_atr = iATR(_Symbol, PERIOD_CURRENT, ATR_Period);

   // ATR（UT Bot用）
   h_ut_atr = iATR(_Symbol, PERIOD_CURRENT, UT_ATR_Period);

   // ハンドル検証
   if(h_ichimoku == INVALID_HANDLE || h_rsi == INVALID_HANDLE ||
      h_atr == INVALID_HANDLE || h_ut_atr == INVALID_HANDLE)
   {
      Print("❌ インジケーターハンドルの作成に失敗");
      return INIT_FAILED;
   }

   // UT Bot初期化
   utTrailStop = 0;
   utBuySignal = false;
   utSellSignal = false;
   lastBarTime = 0;

   Print("✅ ThreeLayerEA 初期化完了");
   Print("   一目: ", Ichi_Tenkan, "/", Ichi_Kijun, "/", Ichi_SenkouB);
   Print("   UT Bot: Key=", UT_KeyValue, " ATR=", UT_ATR_Period);
   Print("   RSI: ", RSI_Period, " ATR閾値: ", ATR_MinThreshold);
   Print("   リスク: ", RiskPercent, "% SL=ATR×", SL_ATR_Multi, " TP=ATR×", TP_ATR_Multi);

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
   // 新バー時のみ判定
   if(!IsNewBar()) return;

   // スプレッドチェック
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > MaxSpread) return;

   // 取引許可チェック
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
      return;

   // ポジション数チェック
   if(CountMyPositions() >= MaxPositions) return;

   // ★ SL後クールダウン
   if(lastSLTime > 0 && TimeCurrent() - lastSLTime < CooldownMinutes * 60)
      return;

   //--- インジケーター値取得 ---
   double senkouA[], senkouB[];
   double rsi[], atr[], utAtr[];

   if(!GetBuffer(h_ichimoku, 2, 0, 3, senkouA)) return;  // 先行スパンA
   if(!GetBuffer(h_ichimoku, 3, 0, 3, senkouB)) return;  // 先行スパンB
   if(!GetBuffer(h_rsi, 0, 1, 1, rsi)) return;
   if(!GetBuffer(h_atr, 0, 1, 1, atr)) return;
   if(!GetBuffer(h_ut_atr, 0, 1, 2, utAtr)) return;

   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double close2 = iClose(_Symbol, PERIOD_CURRENT, 2);

   if(close1 == 0 || close2 == 0) return;

   // 雲の上限・下限（現在のシフト位置）
   double cloudUpper = MathMax(senkouA[1], senkouB[1]);
   double cloudLower = MathMin(senkouA[1], senkouB[1]);

   //=== 第1層: 一目均衡表（環境認識） ===
   bool allowBuy  = (close1 > cloudUpper);
   bool allowSell = (close1 < cloudLower);

   if(!allowBuy && !allowSell) return;  // 雲の中 → 禁止

   //=== 第2層: UT Bot Alerts ===
   UpdateUTBot(close1, close2, utAtr[0], utAtr[1]);

   //=== 第2層: SMC（構造分析） ===
   bool smcAllowBuy  = false;
   bool smcAllowSell = false;
   CheckSMC(smcAllowBuy, smcAllowSell);

   //=== 第3層: RSI + ATR フィルター ===
   double rsiVal = rsi[0];
   double atrVal = atr[0];

   bool rsiAllowBuy  = (rsiVal < RSI_OB);
   bool rsiAllowSell = (rsiVal > RSI_OS);
   bool atrAllow     = (atrVal >= ATR_MinThreshold);

   //=== エントリー判定（全AND） ===
   if(allowBuy && utBuySignal && smcAllowBuy && rsiAllowBuy && atrAllow)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double slDist = atrVal * SL_ATR_Multi;
      double tpDist = atrVal * TP_ATR_Multi;

      double sl = NormalizeDouble(ask - slDist, _Digits);
      double tp = NormalizeDouble(ask + tpDist, _Digits);
      double lot = CalcLotSize(slDist);

      if(lot > 0 && trade.Buy(lot, _Symbol, ask, sl, tp, "3Layer BUY"))
         Print("🟢 BUY — RSI:", DoubleToString(rsiVal,1),
               " ATR:", DoubleToString(atrVal,2),
               " Lot:", DoubleToString(lot,2));
   }

   if(allowSell && utSellSignal && smcAllowSell && rsiAllowSell && atrAllow)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double slDist = atrVal * SL_ATR_Multi;
      double tpDist = atrVal * TP_ATR_Multi;

      double sl = NormalizeDouble(bid + slDist, _Digits);
      double tp = NormalizeDouble(bid - tpDist, _Digits);
      double lot = CalcLotSize(slDist);

      if(lot > 0 && trade.Sell(lot, _Symbol, bid, sl, tp, "3Layer SELL"))
         Print("🔴 SELL — RSI:", DoubleToString(rsiVal,1),
               " ATR:", DoubleToString(atrVal,2),
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
//| UT Bot Alerts 計算                                                |
//+------------------------------------------------------------------+
void UpdateUTBot(double close1, double close2, double curATR, double prevATR)
{
   double nLoss = curATR * UT_KeyValue;

   double prevTrail = utTrailStop;

   // トレーリングストップ更新
   if(close1 > prevTrail && close2 > prevTrail)
      utTrailStop = MathMax(prevTrail, close1 - nLoss);
   else if(close1 < prevTrail && close2 < prevTrail)
      utTrailStop = MathMin(prevTrail, close1 + nLoss);
   else if(close1 > prevTrail)
      utTrailStop = close1 - nLoss;
   else
      utTrailStop = close1 + nLoss;

   // シグナル検出（前バーからのクロス）
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

   // スイングハイ/ロー検出
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
      // スイングハイ判定
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

      // スイングロー判定
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

   // BOS / CHoCH 検出
   bool bullishBOS = false;
   bool bearishBOS = false;
   bool bullishCHoCH = false;
   bool bearishCHoCH = false;

   double latestClose = close[0];

   // --- Bullish BOS: 直近のスイングハイを上抜け ---
   // --- Bearish CHoCH: 上昇トレンド中にスイングローを下抜け ---
   int shCount = ArraySize(swingHighs);
   int slCount = ArraySize(swingLows);

   if(shCount >= 2)
   {
      // 直近のスイングハイをブレイク → Bullish BOS
      if(latestClose > swingHighs[0])
         bullishBOS = true;

      // 前回のスイングハイ > 前々回 = 上昇構造中
      // その中でスイングローを下抜け → Bearish CHoCH
      if(swingHighs[0] > swingHighs[1] && slCount >= 1)
      {
         if(latestClose < swingLows[0])
            bearishCHoCH = true;
      }
   }

   if(slCount >= 2)
   {
      // 直近のスイングローを下抜け → Bearish BOS
      if(latestClose < swingLows[0])
         bearishBOS = true;

      // 前回のスイングロー < 前々回 = 下降構造中
      // その中でスイングハイを上抜け → Bullish CHoCH
      if(swingLows[0] < swingLows[1] && shCount >= 1)
      {
         if(latestClose > swingHighs[0])
            bullishCHoCH = true;
      }
   }

   // SMCトレンド判定
   bool smcBullish = false;
   bool smcBearish = false;

   // Higher High + Higher Low → bullish
   if(shCount >= 2 && slCount >= 2)
   {
      if(swingHighs[0] > swingHighs[1] && swingLows[0] > swingLows[1])
         smcBullish = true;
      if(swingHighs[0] < swingHighs[1] && swingLows[0] < swingLows[1])
         smcBearish = true;
   }

   // --- Buy許可判定（ブロッカー型） ---
   // Bearish CHoCH が出ていなければBuy許可
   // （SMCトレンド確認は不要 — 一目+UT Botで十分フィルター済み）
   allowBuy = !bearishCHoCH;

   // --- Sell許可判定（ブロッカー型） ---
   // Bullish CHoCH が出ていなければSell許可
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

   if(tickValue <= 0 || tickSize <= 0)
   {
      Print("⚠️ TickValue/TickSize取得失敗");
      return 0;
   }

   double riskPerLot = (slDistance / tickSize) * tickValue;
   if(riskPerLot <= 0) return 0;

   double lots = riskAmount / riskPerLot;

   // ロットサイズ制限
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;

   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(MinLots, MathMin(MaxLots, lots));

   return NormalizeDouble(lots, 2);
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
            Print("⏸️ SLクールダウン開始: ", CooldownMinutes, "分間エントリー停止");
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
   {
      Print("⚠️ CopyBuffer失敗: handle=", handle, " buffer=", buffer);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
