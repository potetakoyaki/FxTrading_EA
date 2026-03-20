//+------------------------------------------------------------------+
//| ExportHistory.mq5 - チャート履歴をCSVエクスポート (10年対応版)      |
//| 使い方: 任意のチャートにドラッグ&ドロップで実行                          |
//|         MQL5/Files/ フォルダにCSVが生成されます                       |
//+------------------------------------------------------------------+
#property script_show_inputs

//--- 設定
input string   InpSymbol1    = "XAUUSD";     // シンボル1
input string   InpSymbol2    = "USDJPY";     // シンボル2 (空欄で無視)
input int      InpYears      = 10;           // エクスポート年数 (最大10)
input bool     InpExportM15  = true;         // M15をエクスポート
input bool     InpExportH1   = true;         // H1をエクスポート
input bool     InpExportH4   = true;         // H4をエクスポート
input bool     InpExportD1   = true;         // D1をエクスポート (長期補完用)

//+------------------------------------------------------------------+
//| 履歴データの事前ダウンロードを試みる                                   |
//+------------------------------------------------------------------+
bool RequestHistoryData(string symbol, ENUM_TIMEFRAMES tf, datetime startDate)
{
   // まずシンボルを気配値に追加
   SymbolSelect(symbol, true);

   // 履歴データのダウンロードをリクエスト
   MqlRates tempRates[];
   int attempts = 0;
   int maxAttempts = 5;

   while(attempts < maxAttempts)
   {
      int copied = CopyRates(symbol, tf, startDate, TimeCurrent(), tempRates);
      if(copied > 0)
      {
         Print("   履歴リクエスト成功: ", symbol, " ", EnumToString(tf),
               " → ", copied, "本 (試行", attempts + 1, ")");
         return true;
      }

      attempts++;
      Print("   履歴データ待機中... (試行", attempts, "/", maxAttempts, ")");
      Sleep(3000);  // 3秒待機してリトライ
   }

   Print("   WARNING: 履歴データが十分に取得できない可能性があります: ",
         symbol, " ", EnumToString(tf));
   return false;
}

//+------------------------------------------------------------------+
//| タイムフレーム名を取得                                               |
//+------------------------------------------------------------------+
string GetTimeframeName(ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M15: return "M15";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      default:         return "Unknown";
   }
}

//+------------------------------------------------------------------+
//| 小数点桁数を自動判定                                                |
//+------------------------------------------------------------------+
int GetDigits(string symbol)
{
   // SymbolInfoInteger で取得
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   if(digits > 0) return digits;

   // フォールバック: シンボル名で判定
   if(StringFind(symbol, "JPY") >= 0) return 3;
   if(StringFind(symbol, "XAU") >= 0) return 2;
   if(StringFind(symbol, "XAG") >= 0) return 3;
   return 5;
}

//+------------------------------------------------------------------+
//| 1つの時間足をCSVにエクスポート                                       |
//+------------------------------------------------------------------+
bool ExportTimeframe(string symbol, ENUM_TIMEFRAMES tf, int years)
{
   string tfName = GetTimeframeName(tf);
   string filename = symbol + "_" + tfName + ".csv";
   int digits = GetDigits(symbol);

   Print("--- ", symbol, " ", tfName, " エクスポート開始 (", years, "年分) ---");

   //--- 開始日の計算
   datetime startDate = TimeCurrent() - (datetime)(years * 365.25 * 24 * 3600);

   //--- 履歴データの事前ダウンロード
   RequestHistoryData(symbol, tf, startDate);

   //--- データ取得
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(symbol, tf, startDate, TimeCurrent(), rates);

   if(copied <= 0)
   {
      Print("ERROR: データ取得失敗: ", symbol, " ", tfName, " エラー=", GetLastError());
      Print("       シンボルが気配値表示にあるか確認してください");
      Print("       ツール→オプション→チャート→チャートの最大バー数を増やしてください");
      return false;
   }

   //--- 実際の期間を表示
   datetime actualStart = rates[0].time;
   datetime actualEnd   = rates[copied - 1].time;
   int actualDays = (int)((actualEnd - actualStart) / 86400);
   double actualYears = actualDays / 365.25;

   Print("   取得件数: ", copied, "本");
   Print("   実際の期間: ", TimeToString(actualStart, TIME_DATE), " ~ ",
         TimeToString(actualEnd, TIME_DATE),
         " (約", DoubleToString(actualYears, 1), "年)");

   //--- ファイル書き込み
   int handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: ファイルを開けません: ", filename, " エラー=", GetLastError());
      return false;
   }

   //--- ヘッダー
   FileWrite(handle, "DateTime", "Open", "High", "Low", "Close", "TickVolume", "Spread");

   //--- データ書き込み
   for(int i = 0; i < copied; i++)
   {
      string dt = TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES | TIME_SECONDS);
      FileWrite(handle,
         dt,
         DoubleToString(rates[i].open, digits),
         DoubleToString(rates[i].high, digits),
         DoubleToString(rates[i].low, digits),
         DoubleToString(rates[i].close, digits),
         IntegerToString(rates[i].tick_volume),
         IntegerToString(rates[i].spread)
      );
   }

   FileClose(handle);
   Print("OK: ", filename, " → ", copied, "本 (",
         DoubleToString(actualYears, 1), "年分)");
   return true;
}

//+------------------------------------------------------------------+
//| スクリプト実行                                                      |
//+------------------------------------------------------------------+
void OnStart()
{
   Print("========================================");
   Print("  チャート履歴エクスポート (最大", InpYears, "年分)");
   Print("  注意: 10年分を取得するには以下の設定が必要:");
   Print("  1. ツール→オプション→チャート→");
   Print("     「チャートの最大バー数」を Unlimited に設定");
   Print("  2. 各時間足のチャートを一度開いて履歴を");
   Print("     ダウンロードしておくと確実です");
   Print("========================================");

   int success = 0;
   int total   = 0;

   //--- シンボル1
   if(InpSymbol1 != "")
   {
      Print("\n=== ", InpSymbol1, " ===");

      // D1 (最も長期間取得可能)
      if(InpExportD1)
      {
         total++;
         if(ExportTimeframe(InpSymbol1, PERIOD_D1, InpYears)) success++;
      }

      // H4 (通常10年程度取得可能)
      if(InpExportH4)
      {
         total++;
         if(ExportTimeframe(InpSymbol1, PERIOD_H4, InpYears)) success++;
      }

      // H1 (ブローカーにより5〜10年)
      if(InpExportH1)
      {
         total++;
         if(ExportTimeframe(InpSymbol1, PERIOD_H1, InpYears)) success++;
      }

      // M15 (通常2〜3年が限界)
      if(InpExportM15)
      {
         total++;
         if(ExportTimeframe(InpSymbol1, PERIOD_M15, InpYears)) success++;
      }
   }

   //--- シンボル2
   if(InpSymbol2 != "")
   {
      Print("\n=== ", InpSymbol2, " ===");

      if(InpExportD1)
      {
         total++;
         if(ExportTimeframe(InpSymbol2, PERIOD_D1, InpYears)) success++;
      }

      if(InpExportH4)
      {
         total++;
         if(ExportTimeframe(InpSymbol2, PERIOD_H4, InpYears)) success++;
      }

      if(InpExportH1)
      {
         total++;
         if(ExportTimeframe(InpSymbol2, PERIOD_H1, InpYears)) success++;
      }

      if(InpExportM15)
      {
         total++;
         if(ExportTimeframe(InpSymbol2, PERIOD_M15, InpYears)) success++;
      }
   }

   Print("\n========================================");
   Print("  完了: ", success, "/", total, " ファイル成功");
   Print("  保存先: MQL5/Files/ フォルダ");
   Print("========================================");

   // 結果サマリーをメッセージボックスで表示
   string msg = "エクスポート完了!\n\n" +
                IntegerToString(success) + "/" + IntegerToString(total) +
                " ファイル成功\n\n" +
                "保存先: ファイル→データフォルダを開く→MQL5→Files\n\n" +
                "注意:\n" +
                "- M15は通常2〜3年分が上限です\n" +
                "- H1は5〜10年分取得できることが多いです\n" +
                "- D1/H4は10年以上取得可能です\n" +
                "- 取得量はブローカーのサーバーに依存します";

   if(success == total)
      MessageBox(msg, "ExportHistory", MB_ICONINFORMATION);
   else
      MessageBox("一部エクスポート失敗\n\n" +
                 "成功: " + IntegerToString(success) + "/" + IntegerToString(total) + "\n\n" +
                 "対処法:\n" +
                 "1. シンボルが気配値表示に追加されているか確認\n" +
                 "2. ツール→オプション→チャート→最大バー数をUnlimitedに\n" +
                 "3. 各時間足のチャートを開いて履歴をロード\n" +
                 "4. エキスパートタブでエラー詳細を確認",
                 "ExportHistory", MB_ICONWARNING);
}
