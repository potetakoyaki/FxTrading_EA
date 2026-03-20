//+------------------------------------------------------------------+
//| ExportHistory.mq5 - チャート履歴をCSVエクスポート                    |
//| 使い方: 任意のチャートにドラッグ&ドロップで実行                          |
//|         MQL5/Files/ フォルダにCSVが生成されます                       |
//+------------------------------------------------------------------+
#property script_show_inputs

//--- 設定
input string   InpSymbol1    = "XAUUSD";     // シンボル1
input string   InpSymbol2    = "USDJPY";     // シンボル2 (空欄で無視)
input int      InpYears      = 2;            // エクスポート年数
input bool     InpExportM15  = true;         // M15をエクスポート
input bool     InpExportH1   = true;         // H1をエクスポート
input bool     InpExportH4   = true;         // H4をエクスポート

//+------------------------------------------------------------------+
//| 1つの時間足をCSVにエクスポート                                       |
//+------------------------------------------------------------------+
bool ExportTimeframe(string symbol, ENUM_TIMEFRAMES tf, int years)
{
   string tfName;
   switch(tf)
   {
      case PERIOD_M15: tfName = "M15"; break;
      case PERIOD_H1:  tfName = "H1";  break;
      case PERIOD_H4:  tfName = "H4";  break;
      default:         tfName = "Unknown"; break;
   }

   string filename = symbol + "_" + tfName + ".csv";

   int handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: ファイルを開けません: ", filename, " エラー=", GetLastError());
      return false;
   }

   //--- ヘッダー
   FileWrite(handle, "DateTime", "Open", "High", "Low", "Close", "TickVolume", "Spread");

   //--- データ取得
   datetime startDate = TimeCurrent() - years * 365 * 24 * 3600;

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(symbol, tf, startDate, TimeCurrent(), rates);

   if(copied <= 0)
   {
      Print("ERROR: データ取得失敗: ", symbol, " ", tfName, " エラー=", GetLastError());
      Print("       シンボルが気配値表示にあるか確認してください");
      FileClose(handle);
      return false;
   }

   //--- 書き込み
   for(int i = 0; i < copied; i++)
   {
      string dt = TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES | TIME_SECONDS);
      FileWrite(handle,
         dt,
         DoubleToString(rates[i].open, 5),
         DoubleToString(rates[i].high, 5),
         DoubleToString(rates[i].low, 5),
         DoubleToString(rates[i].close, 5),
         IntegerToString(rates[i].tick_volume),
         IntegerToString(rates[i].spread)
      );
   }

   FileClose(handle);
   Print("OK: ", filename, " → ", copied, "本のバーをエクスポート");
   return true;
}

//+------------------------------------------------------------------+
//| スクリプト実行                                                      |
//+------------------------------------------------------------------+
void OnStart()
{
   Print("========================================");
   Print("  チャート履歴エクスポート開始");
   Print("  期間: 過去", InpYears, "年分");
   Print("========================================");

   int success = 0;
   int total   = 0;

   //--- シンボル1
   if(InpSymbol1 != "")
   {
      if(InpExportM15) { total++; if(ExportTimeframe(InpSymbol1, PERIOD_M15, InpYears)) success++; }
      if(InpExportH1)  { total++; if(ExportTimeframe(InpSymbol1, PERIOD_H1,  InpYears)) success++; }
      if(InpExportH4)  { total++; if(ExportTimeframe(InpSymbol1, PERIOD_H4,  InpYears)) success++; }
   }

   //--- シンボル2
   if(InpSymbol2 != "")
   {
      if(InpExportH1)  { total++; if(ExportTimeframe(InpSymbol2, PERIOD_H1, InpYears))  success++; }
   }

   Print("========================================");
   Print("  完了: ", success, "/", total, " ファイル成功");
   Print("  保存先: MQL5/Files/ フォルダ");
   Print("========================================");

   if(success == total)
      MessageBox("エクスポート完了!\n\n" +
                 IntegerToString(success) + "個のCSVファイルを生成しました。\n" +
                 "場所: ファイル→データフォルダを開く→MQL5→Files",
                 "ExportHistory", MB_ICONINFORMATION);
   else
      MessageBox("一部エクスポート失敗\n\n" +
                 "成功: " + IntegerToString(success) + "/" + IntegerToString(total) + "\n" +
                 "エキスパートタブでエラーを確認してください。\n" +
                 "シンボルが気配値表示に追加されているか確認してください。",
                 "ExportHistory", MB_ICONWARNING);
}
