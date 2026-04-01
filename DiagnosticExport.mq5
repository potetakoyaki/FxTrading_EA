//+------------------------------------------------------------------+
//| DiagnosticExport.mq5                                              |
//| Export indicator values per M15 bar for Python reconciliation.     |
//|                                                                    |
//| USAGE:                                                             |
//|   1. Copy this file to: MT5_DataFolder/MQL5/Scripts/               |
//|   2. Open any XAUUSD chart in MT5                                  |
//|   3. Navigator -> Scripts -> DiagnosticExport (drag onto chart)     |
//|   4. Output: MT5_DataFolder/MQL5/Files/diagnostic_mt5.csv          |
//|   5. Copy the CSV to /tmp/FxTrading_EA/ and run reconcile.py       |
//+------------------------------------------------------------------+
#property copyright "AntigravityMTF EA Diagnostic"
#property version   "1.00"
#property script_show_inputs

//--- User Configuration
input string InpSymbol     = "XAUUSD";              // Main symbol
input string InpCorrSymbol = "USDJPY";              // Correlation symbol
input int    InpYears      = 5;                     // Years of data to export
input string InpFilename   = "diagnostic_mt5.csv";  // Output filename

//--- Indicator Parameters (MUST match Python GoldConfig exactly)
input int    H4_MA_FAST_P   = 20;   // H4 SMA fast period
input int    H4_MA_SLOW_P   = 50;   // H4 SMA slow period
input int    H4_ADX_P       = 14;   // H4 ADX period
input int    H4_RSI_P       = 14;   // H4 RSI period
input int    H1_MA_FAST_P   = 10;   // H1 EMA fast period
input int    H1_MA_SLOW_P   = 30;   // H1 EMA slow period
input int    H1_RSI_P       = 14;   // H1 RSI period
input int    H1_BB_P        = 20;   // H1 Bollinger period
input double H1_BB_D        = 2.0;  // H1 Bollinger deviation
input int    M15_MA_FAST_P  = 5;    // M15 EMA fast period
input int    M15_MA_SLOW_P  = 20;   // M15 EMA slow period
input int    ATR_P          = 14;   // ATR period (all TFs)
input int    CORR_MA_FAST_P = 10;   // USDJPY EMA fast
input int    CORR_MA_SLOW_P = 30;   // USDJPY EMA slow

//+------------------------------------------------------------------+
//| Format datetime as ISO: "YYYY-MM-DD HH:MM:SS"                    |
//+------------------------------------------------------------------+
string FormatDT(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

//+------------------------------------------------------------------+
//| Safe double-to-string (EMPTY_VALUE -> empty)                      |
//+------------------------------------------------------------------+
string D2S(double val, int digits=6)
{
   if(val == EMPTY_VALUE || val >= DBL_MAX / 2.0 || MathIsValidNumber(val) == false)
      return "";
   return DoubleToString(val, digits);
}

//+------------------------------------------------------------------+
//| Wait for indicator handle to be calculated                        |
//+------------------------------------------------------------------+
bool WaitReady(int handle, string name, int timeout_sec=30)
{
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("ERROR: Invalid handle for %s", name);
      return false;
   }
   for(int i = 0; i < timeout_sec; i++)
   {
      if(BarsCalculated(handle) > 0)
         return true;
      Sleep(1000);
   }
   PrintFormat("TIMEOUT: %s not ready after %d sec", name, timeout_sec);
   return false;
}

//+------------------------------------------------------------------+
//| Binary search: last index where rates[idx].time <= target         |
//+------------------------------------------------------------------+
int FindBar(const MqlRates &rates[], int count, datetime target)
{
   if(count <= 0 || target < rates[0].time)
      return -1;
   if(target >= rates[count - 1].time)
      return count - 1;

   int lo = 0, hi = count - 1;
   while(lo < hi)
   {
      int mid = (lo + hi + 1) / 2;
      if(rates[mid].time <= target)
         lo = mid;
      else
         hi = mid - 1;
   }
   return lo;
}

//+------------------------------------------------------------------+
//| Request data download for a symbol/timeframe                      |
//+------------------------------------------------------------------+
bool RequestData(string symbol, ENUM_TIMEFRAMES tf, datetime start)
{
   MqlRates tmp[];
   for(int attempt = 0; attempt < 5; attempt++)
   {
      int n = CopyRates(symbol, tf, start, TimeCurrent(), tmp);
      if(n > 100)
         return true;
      PrintFormat("  Requesting %s %s data (attempt %d)...",
                  symbol, EnumToString(tf), attempt + 1);
      Sleep(3000);
   }
   return false;
}

//+------------------------------------------------------------------+
//| Main script entry point                                           |
//+------------------------------------------------------------------+
void OnStart()
{
   Print("============================================");
   Print("  DiagnosticExport v1.0");
   Print("  Symbol: ", InpSymbol, " | Corr: ", InpCorrSymbol);
   Print("  Years: ", InpYears);
   Print("============================================");

   datetime start_time = TimeCurrent() - (datetime)(InpYears * 365.25 * 24 * 3600);

   //--- Pre-download data
   Print("Requesting historical data...");
   if(!RequestData(InpSymbol, PERIOD_M15, start_time)) { Print("ERROR: No M15 data"); return; }
   if(!RequestData(InpSymbol, PERIOD_H1,  start_time)) { Print("ERROR: No H1 data");  return; }
   if(!RequestData(InpSymbol, PERIOD_H4,  start_time)) { Print("ERROR: No H4 data");  return; }

   bool has_usdjpy = RequestData(InpCorrSymbol, PERIOD_H1, start_time);
   if(!has_usdjpy)
      Print("WARNING: No USDJPY data available. Columns will be empty.");

   //--- Create indicator handles
   Print("Creating indicators...");

   // H4
   int h_h4_sma_f = iMA(InpSymbol, PERIOD_H4, H4_MA_FAST_P, 0, MODE_SMA, PRICE_CLOSE);
   int h_h4_sma_s = iMA(InpSymbol, PERIOD_H4, H4_MA_SLOW_P, 0, MODE_SMA, PRICE_CLOSE);
   int h_h4_adx   = iADX(InpSymbol, PERIOD_H4, H4_ADX_P);
   int h_h4_rsi   = iRSI(InpSymbol, PERIOD_H4, H4_RSI_P, PRICE_CLOSE);
   int h_h4_atr   = iATR(InpSymbol, PERIOD_H4, ATR_P);

   // H1
   int h_h1_ema_f = iMA(InpSymbol, PERIOD_H1, H1_MA_FAST_P, 0, MODE_EMA, PRICE_CLOSE);
   int h_h1_ema_s = iMA(InpSymbol, PERIOD_H1, H1_MA_SLOW_P, 0, MODE_EMA, PRICE_CLOSE);
   int h_h1_rsi   = iRSI(InpSymbol, PERIOD_H1, H1_RSI_P, PRICE_CLOSE);
   int h_h1_bb    = iBands(InpSymbol, PERIOD_H1, H1_BB_P, 0, H1_BB_D, PRICE_CLOSE);
   int h_h1_atr   = iATR(InpSymbol, PERIOD_H1, ATR_P);

   // M15
   int h_m15_ema_f = iMA(InpSymbol, PERIOD_M15, M15_MA_FAST_P, 0, MODE_EMA, PRICE_CLOSE);
   int h_m15_ema_s = iMA(InpSymbol, PERIOD_M15, M15_MA_SLOW_P, 0, MODE_EMA, PRICE_CLOSE);
   int h_m15_atr   = iATR(InpSymbol, PERIOD_M15, ATR_P);

   // USDJPY H1
   int h_uj_ema_f = INVALID_HANDLE;
   int h_uj_ema_s = INVALID_HANDLE;
   if(has_usdjpy)
   {
      h_uj_ema_f = iMA(InpCorrSymbol, PERIOD_H1, CORR_MA_FAST_P, 0, MODE_EMA, PRICE_CLOSE);
      h_uj_ema_s = iMA(InpCorrSymbol, PERIOD_H1, CORR_MA_SLOW_P, 0, MODE_EMA, PRICE_CLOSE);
   }

   //--- Wait for indicators
   Print("Waiting for indicator calculation...");
   if(!WaitReady(h_h4_sma_f, "H4_SMA_F"))  return;
   if(!WaitReady(h_h4_sma_s, "H4_SMA_S"))  return;
   if(!WaitReady(h_h4_adx,   "H4_ADX"))    return;
   if(!WaitReady(h_h4_rsi,   "H4_RSI"))    return;
   if(!WaitReady(h_h4_atr,   "H4_ATR"))    return;
   if(!WaitReady(h_h1_ema_f, "H1_EMA_F"))  return;
   if(!WaitReady(h_h1_ema_s, "H1_EMA_S"))  return;
   if(!WaitReady(h_h1_rsi,   "H1_RSI"))    return;
   if(!WaitReady(h_h1_bb,    "H1_BB"))     return;
   if(!WaitReady(h_h1_atr,   "H1_ATR"))    return;
   if(!WaitReady(h_m15_ema_f,"M15_EMA_F")) return;
   if(!WaitReady(h_m15_ema_s,"M15_EMA_S")) return;
   if(!WaitReady(h_m15_atr,  "M15_ATR"))   return;
   if(has_usdjpy)
   {
      if(!WaitReady(h_uj_ema_f, "UJ_EMA_F", 10)) has_usdjpy = false;
      if(has_usdjpy && !WaitReady(h_uj_ema_s, "UJ_EMA_S", 10)) has_usdjpy = false;
   }

   //--- Load all bar data
   Print("Loading bar data...");

   // M15 rates
   MqlRates m15[];
   int n_m15 = CopyRates(InpSymbol, PERIOD_M15, start_time, TimeCurrent(), m15);
   if(n_m15 <= 0) { Print("ERROR: CopyRates M15 failed"); return; }
   PrintFormat("  M15: %d bars [%s ~ %s]", n_m15, FormatDT(m15[0].time), FormatDT(m15[n_m15-1].time));

   // H1 rates
   MqlRates h1[];
   int n_h1 = CopyRates(InpSymbol, PERIOD_H1, start_time, TimeCurrent(), h1);
   if(n_h1 <= 0) { Print("ERROR: CopyRates H1 failed"); return; }
   PrintFormat("  H1:  %d bars", n_h1);

   // H4 rates
   MqlRates h4[];
   int n_h4 = CopyRates(InpSymbol, PERIOD_H4, start_time, TimeCurrent(), h4);
   if(n_h4 <= 0) { Print("ERROR: CopyRates H4 failed"); return; }
   PrintFormat("  H4:  %d bars", n_h4);

   // USDJPY H1 rates
   MqlRates uj[];
   int n_uj = 0;
   if(has_usdjpy)
   {
      n_uj = CopyRates(InpCorrSymbol, PERIOD_H1, start_time, TimeCurrent(), uj);
      if(n_uj <= 0) has_usdjpy = false;
      else PrintFormat("  UJ:  %d bars", n_uj);
   }

   //--- Batch-copy all indicator buffers
   Print("Copying indicator buffers...");

   // M15 indicators (aligned with m15[] by construction)
   double m15_ef[], m15_es[], m15_at[];
   if(CopyBuffer(h_m15_ema_f, 0, m15[n_m15-1].time, n_m15, m15_ef) != n_m15)
   { Print("ERROR: M15 EMA_F buffer mismatch"); return; }
   if(CopyBuffer(h_m15_ema_s, 0, m15[n_m15-1].time, n_m15, m15_es) != n_m15)
   { Print("ERROR: M15 EMA_S buffer mismatch"); return; }
   if(CopyBuffer(h_m15_atr,   0, m15[n_m15-1].time, n_m15, m15_at) != n_m15)
   { Print("ERROR: M15 ATR buffer mismatch"); return; }

   // H1 indicators
   double h1_ef[], h1_es[], h1_rs[], h1_bb_base[], h1_bb_up[], h1_bb_lo[], h1_at[];
   if(CopyBuffer(h_h1_ema_f, 0, h1[n_h1-1].time, n_h1, h1_ef) != n_h1)
   { Print("ERROR: H1 EMA_F buffer"); return; }
   if(CopyBuffer(h_h1_ema_s, 0, h1[n_h1-1].time, n_h1, h1_es) != n_h1)
   { Print("ERROR: H1 EMA_S buffer"); return; }
   if(CopyBuffer(h_h1_rsi,   0, h1[n_h1-1].time, n_h1, h1_rs) != n_h1)
   { Print("ERROR: H1 RSI buffer"); return; }
   // iBands: buffer 0=BASE, 1=UPPER, 2=LOWER
   if(CopyBuffer(h_h1_bb, 0, h1[n_h1-1].time, n_h1, h1_bb_base) != n_h1)
   { Print("ERROR: H1 BB_BASE buffer"); return; }
   if(CopyBuffer(h_h1_bb, 1, h1[n_h1-1].time, n_h1, h1_bb_up) != n_h1)
   { Print("ERROR: H1 BB_UP buffer"); return; }
   if(CopyBuffer(h_h1_bb, 2, h1[n_h1-1].time, n_h1, h1_bb_lo) != n_h1)
   { Print("ERROR: H1 BB_LO buffer"); return; }
   if(CopyBuffer(h_h1_atr, 0, h1[n_h1-1].time, n_h1, h1_at) != n_h1)
   { Print("ERROR: H1 ATR buffer"); return; }

   // H4 indicators
   double h4_sf[], h4_ss[], h4_adx_v[], h4_pdi[], h4_mdi[], h4_rs[], h4_at[];
   if(CopyBuffer(h_h4_sma_f, 0, h4[n_h4-1].time, n_h4, h4_sf) != n_h4)
   { Print("ERROR: H4 SMA_F buffer"); return; }
   if(CopyBuffer(h_h4_sma_s, 0, h4[n_h4-1].time, n_h4, h4_ss) != n_h4)
   { Print("ERROR: H4 SMA_S buffer"); return; }
   // iADX: buffer 0=MAIN(ADX), 1=+DI, 2=-DI
   if(CopyBuffer(h_h4_adx, 0, h4[n_h4-1].time, n_h4, h4_adx_v) != n_h4)
   { Print("ERROR: H4 ADX buffer"); return; }
   if(CopyBuffer(h_h4_adx, 1, h4[n_h4-1].time, n_h4, h4_pdi) != n_h4)
   { Print("ERROR: H4 PDI buffer"); return; }
   if(CopyBuffer(h_h4_adx, 2, h4[n_h4-1].time, n_h4, h4_mdi) != n_h4)
   { Print("ERROR: H4 MDI buffer"); return; }
   if(CopyBuffer(h_h4_rsi, 0, h4[n_h4-1].time, n_h4, h4_rs) != n_h4)
   { Print("ERROR: H4 RSI buffer"); return; }
   if(CopyBuffer(h_h4_atr, 0, h4[n_h4-1].time, n_h4, h4_at) != n_h4)
   { Print("ERROR: H4 ATR buffer"); return; }

   // USDJPY H1 indicators
   double uj_ef[], uj_es[];
   if(has_usdjpy)
   {
      if(CopyBuffer(h_uj_ema_f, 0, uj[n_uj-1].time, n_uj, uj_ef) != n_uj)
         has_usdjpy = false;
      if(has_usdjpy && CopyBuffer(h_uj_ema_s, 0, uj[n_uj-1].time, n_uj, uj_es) != n_uj)
         has_usdjpy = false;
   }

   //--- Open output file
   int file = FileOpen(InpFilename, FILE_WRITE | FILE_ANSI);
   if(file == INVALID_HANDLE)
   {
      PrintFormat("ERROR: Cannot open %s for writing", InpFilename);
      return;
   }

   //--- Write CSV header
   string header = "DateTime,M15_Open,M15_High,M15_Low,M15_Close,M15_Spread,"
                    "M15_EMA5,M15_EMA20,M15_ATR,"
                    "H1_EMA10,H1_EMA30,H1_RSI,H1_BB_Upper,H1_BB_Mid,H1_BB_Lower,H1_ATR,"
                    "H4_SMA20,H4_SMA50,H4_ADX,H4_PDI,H4_MDI,H4_RSI,H4_ATR,"
                    "USDJPY_EMA10,USDJPY_EMA30,"
                    "H1_BarTime,H4_BarTime\n";
   FileWriteString(file, header);

   //--- Main loop: sweep pointers for cross-TF alignment
   Print("Exporting data...");
   int h1_ptr = 0;
   int h4_ptr = 0;
   int uj_ptr = 0;
   int digits = (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS);
   if(digits < 2) digits = 2;

   int written = 0;
   for(int i = 0; i < n_m15; i++)
   {
      datetime bar_time = m15[i].time;

      // Advance H1 pointer: last H1 bar with time <= bar_time
      while(h1_ptr + 1 < n_h1 && h1[h1_ptr + 1].time <= bar_time)
         h1_ptr++;

      // Advance H4 pointer: last H4 bar with time <= bar_time
      while(h4_ptr + 1 < n_h4 && h4[h4_ptr + 1].time <= bar_time)
         h4_ptr++;

      // Advance USDJPY pointer
      if(has_usdjpy)
      {
         while(uj_ptr + 1 < n_uj && uj[uj_ptr + 1].time <= bar_time)
            uj_ptr++;
      }

      // Build CSV line
      string line = FormatDT(bar_time) + ","
         + DoubleToString(m15[i].open, digits) + ","
         + DoubleToString(m15[i].high, digits) + ","
         + DoubleToString(m15[i].low, digits) + ","
         + DoubleToString(m15[i].close, digits) + ","
         + IntegerToString(m15[i].spread) + ","
         + D2S(m15_ef[i]) + ","
         + D2S(m15_es[i]) + ","
         + D2S(m15_at[i]) + ","
         + D2S(h1_ef[h1_ptr]) + ","
         + D2S(h1_es[h1_ptr]) + ","
         + D2S(h1_rs[h1_ptr]) + ","
         + D2S(h1_bb_up[h1_ptr]) + ","
         + D2S(h1_bb_base[h1_ptr]) + ","
         + D2S(h1_bb_lo[h1_ptr]) + ","
         + D2S(h1_at[h1_ptr]) + ","
         + D2S(h4_sf[h4_ptr]) + ","
         + D2S(h4_ss[h4_ptr]) + ","
         + D2S(h4_adx_v[h4_ptr]) + ","
         + D2S(h4_pdi[h4_ptr]) + ","
         + D2S(h4_mdi[h4_ptr]) + ","
         + D2S(h4_rs[h4_ptr]) + ","
         + D2S(h4_at[h4_ptr]) + ","
         + (has_usdjpy ? D2S(uj_ef[uj_ptr]) : "") + ","
         + (has_usdjpy ? D2S(uj_es[uj_ptr]) : "") + ","
         + FormatDT(h1[h1_ptr].time) + ","
         + FormatDT(h4[h4_ptr].time)
         + "\n";

      FileWriteString(file, line);
      written++;

      // Progress
      if(i % 50000 == 0 && i > 0)
         PrintFormat("  Progress: %d / %d (%.0f%%)", i, n_m15, 100.0 * i / n_m15);
   }

   FileClose(file);

   //--- Release indicator handles
   IndicatorRelease(h_h4_sma_f);
   IndicatorRelease(h_h4_sma_s);
   IndicatorRelease(h_h4_adx);
   IndicatorRelease(h_h4_rsi);
   IndicatorRelease(h_h4_atr);
   IndicatorRelease(h_h1_ema_f);
   IndicatorRelease(h_h1_ema_s);
   IndicatorRelease(h_h1_rsi);
   IndicatorRelease(h_h1_bb);
   IndicatorRelease(h_h1_atr);
   IndicatorRelease(h_m15_ema_f);
   IndicatorRelease(h_m15_ema_s);
   IndicatorRelease(h_m15_atr);
   if(h_uj_ema_f != INVALID_HANDLE) IndicatorRelease(h_uj_ema_f);
   if(h_uj_ema_s != INVALID_HANDLE) IndicatorRelease(h_uj_ema_s);

   PrintFormat("============================================");
   PrintFormat("  DONE: %d bars written to %s", written, InpFilename);
   PrintFormat("  File location: MQL5/Files/%s", InpFilename);
   PrintFormat("============================================");
}
