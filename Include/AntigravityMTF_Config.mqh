//+------------------------------------------------------------------+
//|                                      AntigravityMTF_Config.mqh   |
//|         JSON-based Configuration Management for Gold EA          |
//|         Uses JAson library for serialization/deserialization      |
//|                                                                  |
//|  Purpose: Single source of truth for all EA parameters.          |
//|  Prevents the v12.1 disaster where MQ5 had stale defaults        |
//|  that diverged from Python backtest-validated values.             |
//|                                                                  |
//|  Default values: v9.3 PROVEN parameters (16/16 WFA PASS)         |
//+------------------------------------------------------------------+
#property copyright "Antigravity Trading System"
#property version   "17.00"
#property strict

#include <JAson.mqh>

//+------------------------------------------------------------------+
//| Configuration version string                                      |
//+------------------------------------------------------------------+
#define CONFIG_VERSION "17.0"

//+------------------------------------------------------------------+
//| CAntigravityConfig - Central configuration store                  |
//|                                                                   |
//| All EA parameters in one place. Load/save as JSON for:            |
//|   - Version-controlled config files                               |
//|   - Python<->MT5 parameter sync verification                      |
//|   - Live parameter hot-reload (future)                            |
//+------------------------------------------------------------------+
class CAntigravityConfig {
public:
    //--- Risk Management ---
    double RiskPercent;          // Risk % per trade
    double MaxLots;              // Maximum lot size
    double MinLots;              // Minimum lot size
    int    MaxSpread;            // Max spread in points
    double MaxDrawdownPct;       // DD % threshold for 1/4 risk
    double DDHalfRiskPct;        // DD % threshold for half risk
    double DailyMaxLossPct;      // Daily max loss %

    //--- SL/TP (ATR-based) ---
    double SL_ATR_Multi;         // SL = M15 ATR x multiplier
    double TP_ATR_Multi;         // TP = M15 ATR x multiplier
    double Trail_ATR_Multi;      // Trailing = ATR x multiplier
    double BE_ATR_Multi;         // Break-even = ATR x multiplier (v9.3 proven: 0.5)
    double MinSL_Points;         // Minimum SL in points
    double MaxSL_Points;         // Maximum SL in points

    //--- Chandelier Exit ---
    int    Chandelier_Period;    // Chandelier lookback period
    double Chandelier_ATR_Multi; // Chandelier ATR multiplier (v9.3 proven: 1.5)

    //--- Entry ---
    int    MinEntryScore;        // Minimum entry score
    int    CooldownMinutes;      // Post-SL cooldown in minutes

    //--- Time ---
    int    TradeStartHour;       // Trading start hour (server time)
    int    TradeEndHour;         // Trading end hour (server time)
    int    GMTOffset;            // Broker GMT offset
    bool   AvoidFriday;          // Block Friday late entries
    int    FridayCloseHour;      // Friday close hour (server time)

    //--- Regime ---
    int    RangingADXThreshold;  // ADX below = ranging market
    double RangingTPCap;         // TP cap in ranging regime
    bool   UseRegimeML;          // Toggle ML regime detection (future)

    //--- Feature toggles ---
    bool   UseRSIMomentumConfirm; // RSI momentum confirmation
    bool   UsePartialClose;       // Partial close at TP ratio
    double PartialCloseRatio;     // Position ratio to close
    double PartialTP_Ratio;       // TP distance ratio for partial close
    bool   UseReversalMode;       // Reversal mode
    bool   UseChandelierExit;     // Chandelier trailing exit
    bool   UseEquityCurveFilter;  // Equity curve filter
    bool   UseNewsFilter;         // Economic news filter
    bool   UseWeekendClose;       // Friday weekend close
    bool   UseCorrelation;        // USD correlation filter

    //--- Kelly Sizing ---
    int    Kelly_Lookback;       // Lookback trades for Kelly calc
    double Kelly_Fraction;       // Fraction of full Kelly to use
    double Kelly_MinRisk;        // Minimum Kelly risk %
    double Kelly_MaxRisk;        // Maximum Kelly risk %

    //--- SRAT: Session-Regime Adaptive Thresholds ---
    //    hour -> min_score. Index = server hour (0-23).
    //    Value 0 means "use default MinEntryScore".
    //    Value 99 means "block this hour entirely".
    int    SRAT[24];

    //--- DD Escalation ---
    //    4 levels of drawdown escalation.
    //    DD_Levels[i] = DD% threshold, DD_ScoreAdd[i] = min_score override
    double DD_Levels[4];
    int    DD_ScoreAdd[4];

    //--- Constructor ---
    CAntigravityConfig() {
        SetDefaults();
    }

    //--- Methods ---
    bool   LoadFromFile(string filename);
    bool   SaveToFile(string filename);
    string ToJSON();
    bool   FromJSON(string json_str);
    void   SetDefaults();
    bool   ValidateSync(CAntigravityConfig &other);
    string GetDiffReport(CAntigravityConfig &other);

private:
    void   SerializeRiskGroup(CJAVal &root);
    void   SerializeSLTPGroup(CJAVal &root);
    void   SerializeChandelierGroup(CJAVal &root);
    void   SerializeEntryGroup(CJAVal &root);
    void   SerializeTimeGroup(CJAVal &root);
    void   SerializeRegimeGroup(CJAVal &root);
    void   SerializeFeaturesGroup(CJAVal &root);
    void   SerializeKellyGroup(CJAVal &root);
    void   SerializeSRATGroup(CJAVal &root);
    void   SerializeDDGroup(CJAVal &root);

    void   DeserializeRiskGroup(CJAVal &root);
    void   DeserializeSLTPGroup(CJAVal &root);
    void   DeserializeChandelierGroup(CJAVal &root);
    void   DeserializeEntryGroup(CJAVal &root);
    void   DeserializeTimeGroup(CJAVal &root);
    void   DeserializeRegimeGroup(CJAVal &root);
    void   DeserializeFeaturesGroup(CJAVal &root);
    void   DeserializeKellyGroup(CJAVal &root);
    void   DeserializeSRATGroup(CJAVal &root);
    void   DeserializeDDGroup(CJAVal &root);

    //--- Diff helper ---
    void   AppendDiffDouble(string &report, string name, double a, double b, double tol=0.0001);
    void   AppendDiffInt(string &report, string name, int a, int b);
    void   AppendDiffBool(string &report, string name, bool a, bool b);
};


//+------------------------------------------------------------------+
//| SetDefaults - v9.3 PROVEN parameters (16/16 WFA PASS)            |
//|                                                                   |
//| CRITICAL: These values are the single source of truth.            |
//| DO NOT change without re-running WFA validation.                  |
//+------------------------------------------------------------------+
void CAntigravityConfig::SetDefaults() {
    //--- Risk Management ---
    RiskPercent      = 0.75;     // WFA-validated, Python-MT5 synced
    MaxLots          = 0.50;
    MinLots          = 0.01;
    MaxSpread        = 50;
    MaxDrawdownPct   = 6.0;      // DD% for risk 1/4
    DDHalfRiskPct    = 2.5;      // DD% for risk halving
    DailyMaxLossPct  = 2.0;

    //--- SL/TP (ATR-based) ---
    SL_ATR_Multi     = 1.2;      // WFA: PF+0.36
    TP_ATR_Multi     = 4.0;      // v16: Python PF=2.35, ATR=SMA corrected
    Trail_ATR_Multi  = 1.0;      // v16: Python synced
    BE_ATR_Multi     = 0.5;      // v9.3: 0.8->0.5 (16/16 PASS, earlier BE protects profits)
    MinSL_Points     = 200.0;
    MaxSL_Points     = 1500.0;

    //--- Chandelier Exit ---
    Chandelier_Period    = 22;
    Chandelier_ATR_Multi = 1.5;  // v9.3: 2.0->1.5 (16/16 PASS, faster profit lock)

    //--- Entry ---
    MinEntryScore    = 12;       // WFA: 12/27, PF+0.16
    CooldownMinutes  = 480;      // WFA: 480=8h, DD reduction

    //--- Time ---
    TradeStartHour   = 8;
    TradeEndHour     = 22;
    GMTOffset        = 2;        // Broker GMT+2 default
    AvoidFriday      = true;
    FridayCloseHour  = 20;

    //--- Regime ---
    RangingADXThreshold = 20;    // v8.0: ADX<20 = ranging
    RangingTPCap        = 5.0;   // v8.1: 3.5->5.0 (WFA 12->13/14)
    UseRegimeML         = false;

    //--- Feature toggles ---
    UseRSIMomentumConfirm = true;  // v8.0: RSI momentum (WFA 15/16 -> 16/16)
    UsePartialClose       = true;  // v16: 50% TP distance partial close
    PartialCloseRatio     = 0.5;
    PartialTP_Ratio       = 0.5;
    UseReversalMode       = true;
    UseChandelierExit     = true;
    UseEquityCurveFilter  = true;
    UseNewsFilter         = true;
    UseWeekendClose       = true;
    UseCorrelation        = true;

    //--- Kelly Sizing ---
    Kelly_Lookback   = 30;
    Kelly_Fraction   = 0.5;      // Half-Kelly for safety
    Kelly_MinRisk    = 0.1;
    Kelly_MaxRisk    = 1.5;

    //--- SRAT: Session-Regime Adaptive Thresholds ---
    // v8.1 validated. 0 = use default MinEntryScore, 99 = block hour.
    // Hours not in London/NY sessions use MinEntryScore (0 = default).
    ArrayInitialize(SRAT, 0);
    SRAT[8]  = 7;   // London open - highest quality session, loose threshold
    SRAT[9]  = 9;
    SRAT[10] = 9;
    SRAT[11] = 99;  // Lunch dead zone - blocked
    SRAT[12] = 99;  // Lunch dead zone - blocked
    SRAT[13] = 12;  // Post-lunch transition, PF=0.78, raised bar
    SRAT[14] = 11;  // LN/NY overlap transition
    SRAT[15] = 9;
    SRAT[16] = 9;
    SRAT[17] = 9;   // NY session
    SRAT[18] = 12;  // NY transition hour
    SRAT[19] = 9;
    SRAT[20] = 9;
    SRAT[21] = 12;  // Late NY transition

    //--- DD Escalation ---
    // (DD% threshold, min_score override)
    // Start tightening early at 6% to prevent DD deepening.
    DD_Levels[0]   = 6.0;    DD_ScoreAdd[0] = 11;
    DD_Levels[1]   = 10.0;   DD_ScoreAdd[1] = 13;
    DD_Levels[2]   = 15.0;   DD_ScoreAdd[2] = 16;
    DD_Levels[3]   = 20.0;   DD_ScoreAdd[3] = 18;
}


//+------------------------------------------------------------------+
//| ToJSON - Serialize all parameters to JSON string                  |
//+------------------------------------------------------------------+
string CAntigravityConfig::ToJSON() {
    CJAVal root;
    root["version"] = CONFIG_VERSION;

    SerializeRiskGroup(root);
    SerializeSLTPGroup(root);
    SerializeChandelierGroup(root);
    SerializeEntryGroup(root);
    SerializeTimeGroup(root);
    SerializeRegimeGroup(root);
    SerializeFeaturesGroup(root);
    SerializeKellyGroup(root);
    SerializeSRATGroup(root);
    SerializeDDGroup(root);

    return root.Serialize();
}


//+------------------------------------------------------------------+
//| Serialize helper: Risk group                                      |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeRiskGroup(CJAVal &root) {
    CJAVal *risk = root["risk"];
    risk["percent"]         = RiskPercent;
    risk["max_lots"]        = MaxLots;
    risk["min_lots"]        = MinLots;
    risk["max_spread"]      = MaxSpread;
    risk["max_drawdown_pct"]= MaxDrawdownPct;
    risk["dd_half_risk_pct"]= DDHalfRiskPct;
    risk["daily_max_loss"]  = DailyMaxLossPct;
}


//+------------------------------------------------------------------+
//| Serialize helper: SL/TP group                                     |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeSLTPGroup(CJAVal &root) {
    CJAVal *sltp = root["sl_tp"];
    sltp["sl_atr"]       = SL_ATR_Multi;
    sltp["tp_atr"]       = TP_ATR_Multi;
    sltp["trail_atr"]    = Trail_ATR_Multi;
    sltp["be_atr"]       = BE_ATR_Multi;
    sltp["min_sl_points"]= MinSL_Points;
    sltp["max_sl_points"]= MaxSL_Points;
}


//+------------------------------------------------------------------+
//| Serialize helper: Chandelier group                                |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeChandelierGroup(CJAVal &root) {
    CJAVal *ch = root["chandelier"];
    ch["period"]    = Chandelier_Period;
    ch["atr_multi"] = Chandelier_ATR_Multi;
}


//+------------------------------------------------------------------+
//| Serialize helper: Entry group                                     |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeEntryGroup(CJAVal &root) {
    CJAVal *entry = root["entry"];
    entry["min_score"] = MinEntryScore;
    entry["cooldown"]  = CooldownMinutes;
}


//+------------------------------------------------------------------+
//| Serialize helper: Time group                                      |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeTimeGroup(CJAVal &root) {
    CJAVal *t = root["time"];
    t["start_hour"]       = TradeStartHour;
    t["end_hour"]         = TradeEndHour;
    t["gmt_offset"]       = GMTOffset;
    t["avoid_friday"]     = AvoidFriday;
    t["friday_close_hour"]= FridayCloseHour;
}


//+------------------------------------------------------------------+
//| Serialize helper: Regime group                                    |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeRegimeGroup(CJAVal &root) {
    CJAVal *reg = root["regime"];
    reg["ranging_adx_threshold"] = RangingADXThreshold;
    reg["ranging_tp_cap"]        = RangingTPCap;
    reg["use_regime_ml"]         = UseRegimeML;
}


//+------------------------------------------------------------------+
//| Serialize helper: Features group                                  |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeFeaturesGroup(CJAVal &root) {
    CJAVal *f = root["features"];
    f["rsi_momentum_confirm"] = UseRSIMomentumConfirm;
    f["partial_close"]        = UsePartialClose;
    f["partial_close_ratio"]  = PartialCloseRatio;
    f["partial_tp_ratio"]     = PartialTP_Ratio;
    f["reversal_mode"]        = UseReversalMode;
    f["chandelier_exit"]      = UseChandelierExit;
    f["equity_curve_filter"]  = UseEquityCurveFilter;
    f["news_filter"]          = UseNewsFilter;
    f["weekend_close"]        = UseWeekendClose;
    f["correlation"]          = UseCorrelation;
}


//+------------------------------------------------------------------+
//| Serialize helper: Kelly group                                     |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeKellyGroup(CJAVal &root) {
    CJAVal *k = root["kelly"];
    k["lookback"]  = Kelly_Lookback;
    k["fraction"]  = Kelly_Fraction;
    k["min_risk"]  = Kelly_MinRisk;
    k["max_risk"]  = Kelly_MaxRisk;
}


//+------------------------------------------------------------------+
//| Serialize helper: SRAT group                                      |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeSRATGroup(CJAVal &root) {
    CJAVal *s = root["srat"];
    for (int h = 0; h < 24; h++) {
        // Only serialize non-zero hours to keep JSON clean
        if (SRAT[h] != 0) {
            s[IntegerToString(h)] = SRAT[h];
        }
    }
}


//+------------------------------------------------------------------+
//| Serialize helper: DD Escalation group                             |
//+------------------------------------------------------------------+
void CAntigravityConfig::SerializeDDGroup(CJAVal &root) {
    CJAVal *dd = root["dd_escalation"];
    for (int i = 0; i < 4; i++) {
        CJAVal *level = dd["levels"][i];
        level["dd_pct"]    = DD_Levels[i];
        level["min_score"] = DD_ScoreAdd[i];
    }
}


//+------------------------------------------------------------------+
//| FromJSON - Deserialize JSON string into config                    |
//|                                                                   |
//| Missing keys keep their current (default) values, making this     |
//| forward-compatible with older config files.                       |
//+------------------------------------------------------------------+
bool CAntigravityConfig::FromJSON(string json_str) {
    CJAVal root;
    if (!root.Deserialize(json_str)) {
        Print("[Config] ERROR: Failed to parse JSON");
        return false;
    }

    // Version check (informational, not blocking)
    if (root.HasKey("version")) {
        string ver = root["version"].ToStr();
        if (ver != CONFIG_VERSION) {
            PrintFormat("[Config] WARNING: Config version %s differs from expected %s",
                        ver, CONFIG_VERSION);
        }
    }

    DeserializeRiskGroup(root);
    DeserializeSLTPGroup(root);
    DeserializeChandelierGroup(root);
    DeserializeEntryGroup(root);
    DeserializeTimeGroup(root);
    DeserializeRegimeGroup(root);
    DeserializeFeaturesGroup(root);
    DeserializeKellyGroup(root);
    DeserializeSRATGroup(root);
    DeserializeDDGroup(root);

    return true;
}


//+------------------------------------------------------------------+
//| Deserialize helper: Risk group                                    |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeRiskGroup(CJAVal &root) {
    if (!root.HasKey("risk")) return;
    CJAVal *risk = root["risk"];
    if (risk.HasKey("percent"))          RiskPercent      = risk["percent"].ToDbl();
    if (risk.HasKey("max_lots"))         MaxLots          = risk["max_lots"].ToDbl();
    if (risk.HasKey("min_lots"))         MinLots          = risk["min_lots"].ToDbl();
    if (risk.HasKey("max_spread"))       MaxSpread        = (int)risk["max_spread"].ToInt();
    if (risk.HasKey("max_drawdown_pct")) MaxDrawdownPct   = risk["max_drawdown_pct"].ToDbl();
    if (risk.HasKey("dd_half_risk_pct")) DDHalfRiskPct    = risk["dd_half_risk_pct"].ToDbl();
    if (risk.HasKey("daily_max_loss"))   DailyMaxLossPct  = risk["daily_max_loss"].ToDbl();
}


//+------------------------------------------------------------------+
//| Deserialize helper: SL/TP group                                   |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeSLTPGroup(CJAVal &root) {
    if (!root.HasKey("sl_tp")) return;
    CJAVal *sltp = root["sl_tp"];
    if (sltp.HasKey("sl_atr"))       SL_ATR_Multi   = sltp["sl_atr"].ToDbl();
    if (sltp.HasKey("tp_atr"))       TP_ATR_Multi   = sltp["tp_atr"].ToDbl();
    if (sltp.HasKey("trail_atr"))    Trail_ATR_Multi= sltp["trail_atr"].ToDbl();
    if (sltp.HasKey("be_atr"))       BE_ATR_Multi   = sltp["be_atr"].ToDbl();
    if (sltp.HasKey("min_sl_points"))MinSL_Points   = sltp["min_sl_points"].ToDbl();
    if (sltp.HasKey("max_sl_points"))MaxSL_Points   = sltp["max_sl_points"].ToDbl();
}


//+------------------------------------------------------------------+
//| Deserialize helper: Chandelier group                              |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeChandelierGroup(CJAVal &root) {
    if (!root.HasKey("chandelier")) return;
    CJAVal *ch = root["chandelier"];
    if (ch.HasKey("period"))    Chandelier_Period    = (int)ch["period"].ToInt();
    if (ch.HasKey("atr_multi")) Chandelier_ATR_Multi = ch["atr_multi"].ToDbl();
}


//+------------------------------------------------------------------+
//| Deserialize helper: Entry group                                   |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeEntryGroup(CJAVal &root) {
    if (!root.HasKey("entry")) return;
    CJAVal *entry = root["entry"];
    if (entry.HasKey("min_score")) MinEntryScore   = (int)entry["min_score"].ToInt();
    if (entry.HasKey("cooldown"))  CooldownMinutes = (int)entry["cooldown"].ToInt();
}


//+------------------------------------------------------------------+
//| Deserialize helper: Time group                                    |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeTimeGroup(CJAVal &root) {
    if (!root.HasKey("time")) return;
    CJAVal *t = root["time"];
    if (t.HasKey("start_hour"))       TradeStartHour  = (int)t["start_hour"].ToInt();
    if (t.HasKey("end_hour"))         TradeEndHour    = (int)t["end_hour"].ToInt();
    if (t.HasKey("gmt_offset"))       GMTOffset       = (int)t["gmt_offset"].ToInt();
    if (t.HasKey("avoid_friday"))     AvoidFriday     = t["avoid_friday"].ToBool();
    if (t.HasKey("friday_close_hour"))FridayCloseHour = (int)t["friday_close_hour"].ToInt();
}


//+------------------------------------------------------------------+
//| Deserialize helper: Regime group                                  |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeRegimeGroup(CJAVal &root) {
    if (!root.HasKey("regime")) return;
    CJAVal *reg = root["regime"];
    if (reg.HasKey("ranging_adx_threshold")) RangingADXThreshold = (int)reg["ranging_adx_threshold"].ToInt();
    if (reg.HasKey("ranging_tp_cap"))        RangingTPCap        = reg["ranging_tp_cap"].ToDbl();
    if (reg.HasKey("use_regime_ml"))         UseRegimeML         = reg["use_regime_ml"].ToBool();
}


//+------------------------------------------------------------------+
//| Deserialize helper: Features group                                |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeFeaturesGroup(CJAVal &root) {
    if (!root.HasKey("features")) return;
    CJAVal *f = root["features"];
    if (f.HasKey("rsi_momentum_confirm")) UseRSIMomentumConfirm = f["rsi_momentum_confirm"].ToBool();
    if (f.HasKey("partial_close"))        UsePartialClose       = f["partial_close"].ToBool();
    if (f.HasKey("partial_close_ratio"))  PartialCloseRatio     = f["partial_close_ratio"].ToDbl();
    if (f.HasKey("partial_tp_ratio"))     PartialTP_Ratio       = f["partial_tp_ratio"].ToDbl();
    if (f.HasKey("reversal_mode"))        UseReversalMode       = f["reversal_mode"].ToBool();
    if (f.HasKey("chandelier_exit"))      UseChandelierExit     = f["chandelier_exit"].ToBool();
    if (f.HasKey("equity_curve_filter"))  UseEquityCurveFilter  = f["equity_curve_filter"].ToBool();
    if (f.HasKey("news_filter"))          UseNewsFilter         = f["news_filter"].ToBool();
    if (f.HasKey("weekend_close"))        UseWeekendClose       = f["weekend_close"].ToBool();
    if (f.HasKey("correlation"))          UseCorrelation        = f["correlation"].ToBool();
}


//+------------------------------------------------------------------+
//| Deserialize helper: Kelly group                                   |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeKellyGroup(CJAVal &root) {
    if (!root.HasKey("kelly")) return;
    CJAVal *k = root["kelly"];
    if (k.HasKey("lookback"))  Kelly_Lookback = (int)k["lookback"].ToInt();
    if (k.HasKey("fraction"))  Kelly_Fraction = k["fraction"].ToDbl();
    if (k.HasKey("min_risk"))  Kelly_MinRisk  = k["min_risk"].ToDbl();
    if (k.HasKey("max_risk"))  Kelly_MaxRisk  = k["max_risk"].ToDbl();
}


//+------------------------------------------------------------------+
//| Deserialize helper: SRAT group                                    |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeSRATGroup(CJAVal &root) {
    if (!root.HasKey("srat")) return;
    CJAVal *s = root["srat"];

    // Reset to 0 before loading (so missing hours revert to default behavior)
    ArrayInitialize(SRAT, 0);

    for (int h = 0; h < 24; h++) {
        string hkey = IntegerToString(h);
        if (s.HasKey(hkey)) {
            SRAT[h] = (int)s[hkey].ToInt();
        }
    }
}


//+------------------------------------------------------------------+
//| Deserialize helper: DD Escalation group                           |
//+------------------------------------------------------------------+
void CAntigravityConfig::DeserializeDDGroup(CJAVal &root) {
    if (!root.HasKey("dd_escalation")) return;
    CJAVal *dd = root["dd_escalation"];

    if (!dd.HasKey("levels")) return;
    CJAVal *levels = dd["levels"];

    int count = MathMin(levels.Size(), 4);
    for (int i = 0; i < count; i++) {
        CJAVal *level = levels[i];
        if (level.HasKey("dd_pct"))    DD_Levels[i]   = level["dd_pct"].ToDbl();
        if (level.HasKey("min_score")) DD_ScoreAdd[i]  = (int)level["min_score"].ToInt();
    }
}


//+------------------------------------------------------------------+
//| SaveToFile - Write JSON config to MQL5 Files directory            |
//|                                                                   |
//| filename: e.g. "AntigravityMTF_Config.json"                       |
//| Saved to: <Terminal>/MQL5/Files/<filename>                        |
//+------------------------------------------------------------------+
bool CAntigravityConfig::SaveToFile(string filename) {
    string json = ToJSON();
    if (StringLen(json) == 0) {
        Print("[Config] ERROR: Serialization produced empty JSON");
        return false;
    }

    int handle = FileOpen(filename, FILE_WRITE | FILE_TXT | FILE_ANSI);
    if (handle == INVALID_HANDLE) {
        PrintFormat("[Config] ERROR: Cannot open file for writing: %s (error %d)",
                    filename, GetLastError());
        return false;
    }

    uint bytes_written = FileWriteString(handle, json);
    FileClose(handle);

    if (bytes_written == 0) {
        PrintFormat("[Config] ERROR: Failed to write to file: %s", filename);
        return false;
    }

    PrintFormat("[Config] Saved %d bytes to %s", bytes_written, filename);
    return true;
}


//+------------------------------------------------------------------+
//| LoadFromFile - Read JSON config from MQL5 Files directory         |
//|                                                                   |
//| filename: e.g. "AntigravityMTF_Config.json"                       |
//| Reads from: <Terminal>/MQL5/Files/<filename>                      |
//|                                                                   |
//| Returns false if file doesn't exist or parse fails.               |
//| On failure, current config values are unchanged.                  |
//+------------------------------------------------------------------+
bool CAntigravityConfig::LoadFromFile(string filename) {
    if (!FileIsExist(filename)) {
        PrintFormat("[Config] File not found: %s (will use defaults)", filename);
        return false;
    }

    int handle = FileOpen(filename, FILE_READ | FILE_TXT | FILE_ANSI);
    if (handle == INVALID_HANDLE) {
        PrintFormat("[Config] ERROR: Cannot open file for reading: %s (error %d)",
                    filename, GetLastError());
        return false;
    }

    // Read entire file content
    string json = "";
    while (!FileIsEnding(handle)) {
        string line = FileReadString(handle);
        json += line;
    }
    FileClose(handle);

    if (StringLen(json) == 0) {
        PrintFormat("[Config] ERROR: File is empty: %s", filename);
        return false;
    }

    PrintFormat("[Config] Loaded %d chars from %s", StringLen(json), filename);
    return FromJSON(json);
}


//+------------------------------------------------------------------+
//| ValidateSync - Compare two configs for parameter mismatches       |
//|                                                                   |
//| Use case: After loading EA input params AND JSON config,          |
//| verify they agree. Prevents the v12.1 disaster where MQ5          |
//| had old defaults different from Python-validated values.           |
//|                                                                   |
//| Returns true if all parameters match within tolerance.            |
//+------------------------------------------------------------------+
bool CAntigravityConfig::ValidateSync(CAntigravityConfig &other) {
    string diff = GetDiffReport(other);
    if (StringLen(diff) == 0) {
        Print("[Config] SYNC OK: All parameters match");
        return true;
    }

    Print("[Config] SYNC MISMATCH detected:");
    Print(diff);
    return false;
}


//+------------------------------------------------------------------+
//| GetDiffReport - Report all parameter differences between configs  |
//|                                                                   |
//| Returns empty string if configs are identical.                    |
//| Returns human-readable diff report otherwise.                     |
//+------------------------------------------------------------------+
string CAntigravityConfig::GetDiffReport(CAntigravityConfig &other) {
    string report = "";

    //--- Risk ---
    AppendDiffDouble(report, "RiskPercent",      RiskPercent,     other.RiskPercent);
    AppendDiffDouble(report, "MaxLots",          MaxLots,         other.MaxLots);
    AppendDiffDouble(report, "MinLots",          MinLots,         other.MinLots);
    AppendDiffInt(report,    "MaxSpread",        MaxSpread,       other.MaxSpread);
    AppendDiffDouble(report, "MaxDrawdownPct",   MaxDrawdownPct,  other.MaxDrawdownPct);
    AppendDiffDouble(report, "DDHalfRiskPct",    DDHalfRiskPct,   other.DDHalfRiskPct);
    AppendDiffDouble(report, "DailyMaxLossPct",  DailyMaxLossPct, other.DailyMaxLossPct);

    //--- SL/TP ---
    AppendDiffDouble(report, "SL_ATR_Multi",     SL_ATR_Multi,    other.SL_ATR_Multi);
    AppendDiffDouble(report, "TP_ATR_Multi",     TP_ATR_Multi,    other.TP_ATR_Multi);
    AppendDiffDouble(report, "Trail_ATR_Multi",  Trail_ATR_Multi, other.Trail_ATR_Multi);
    AppendDiffDouble(report, "BE_ATR_Multi",     BE_ATR_Multi,    other.BE_ATR_Multi);
    AppendDiffDouble(report, "MinSL_Points",     MinSL_Points,    other.MinSL_Points);
    AppendDiffDouble(report, "MaxSL_Points",     MaxSL_Points,    other.MaxSL_Points);

    //--- Chandelier ---
    AppendDiffInt(report,    "Chandelier_Period",    Chandelier_Period,    other.Chandelier_Period);
    AppendDiffDouble(report, "Chandelier_ATR_Multi", Chandelier_ATR_Multi, other.Chandelier_ATR_Multi);

    //--- Entry ---
    AppendDiffInt(report, "MinEntryScore",   MinEntryScore,   other.MinEntryScore);
    AppendDiffInt(report, "CooldownMinutes", CooldownMinutes, other.CooldownMinutes);

    //--- Time ---
    AppendDiffInt(report,  "TradeStartHour",  TradeStartHour,  other.TradeStartHour);
    AppendDiffInt(report,  "TradeEndHour",    TradeEndHour,    other.TradeEndHour);
    AppendDiffInt(report,  "GMTOffset",       GMTOffset,       other.GMTOffset);
    AppendDiffBool(report, "AvoidFriday",     AvoidFriday,     other.AvoidFriday);
    AppendDiffInt(report,  "FridayCloseHour", FridayCloseHour, other.FridayCloseHour);

    //--- Regime ---
    AppendDiffInt(report,    "RangingADXThreshold", RangingADXThreshold, other.RangingADXThreshold);
    AppendDiffDouble(report, "RangingTPCap",        RangingTPCap,        other.RangingTPCap);
    AppendDiffBool(report,   "UseRegimeML",         UseRegimeML,         other.UseRegimeML);

    //--- Features ---
    AppendDiffBool(report, "UseRSIMomentumConfirm", UseRSIMomentumConfirm, other.UseRSIMomentumConfirm);
    AppendDiffBool(report, "UsePartialClose",       UsePartialClose,       other.UsePartialClose);
    AppendDiffDouble(report, "PartialCloseRatio",   PartialCloseRatio,     other.PartialCloseRatio);
    AppendDiffDouble(report, "PartialTP_Ratio",     PartialTP_Ratio,       other.PartialTP_Ratio);
    AppendDiffBool(report, "UseReversalMode",       UseReversalMode,       other.UseReversalMode);
    AppendDiffBool(report, "UseChandelierExit",     UseChandelierExit,     other.UseChandelierExit);
    AppendDiffBool(report, "UseEquityCurveFilter",  UseEquityCurveFilter,  other.UseEquityCurveFilter);
    AppendDiffBool(report, "UseNewsFilter",         UseNewsFilter,         other.UseNewsFilter);
    AppendDiffBool(report, "UseWeekendClose",       UseWeekendClose,       other.UseWeekendClose);
    AppendDiffBool(report, "UseCorrelation",        UseCorrelation,        other.UseCorrelation);

    //--- Kelly ---
    AppendDiffInt(report,    "Kelly_Lookback", Kelly_Lookback, other.Kelly_Lookback);
    AppendDiffDouble(report, "Kelly_Fraction", Kelly_Fraction, other.Kelly_Fraction);
    AppendDiffDouble(report, "Kelly_MinRisk",  Kelly_MinRisk,  other.Kelly_MinRisk);
    AppendDiffDouble(report, "Kelly_MaxRisk",  Kelly_MaxRisk,  other.Kelly_MaxRisk);

    //--- SRAT ---
    for (int h = 0; h < 24; h++) {
        if (SRAT[h] != other.SRAT[h]) {
            string name = StringFormat("SRAT[%d]", h);
            AppendDiffInt(report, name, SRAT[h], other.SRAT[h]);
        }
    }

    //--- DD Escalation ---
    for (int i = 0; i < 4; i++) {
        string name_dd  = StringFormat("DD_Levels[%d]", i);
        string name_sc  = StringFormat("DD_ScoreAdd[%d]", i);
        AppendDiffDouble(report, name_dd, DD_Levels[i], other.DD_Levels[i]);
        AppendDiffInt(report, name_sc, DD_ScoreAdd[i], other.DD_ScoreAdd[i]);
    }

    return report;
}


//+------------------------------------------------------------------+
//| Diff helpers                                                      |
//+------------------------------------------------------------------+
void CAntigravityConfig::AppendDiffDouble(string &report, string name, double a, double b, double tol/*=0.0001*/) {
    if (MathAbs(a - b) > tol) {
        report += StringFormat("  DIFF %-28s: %.4f vs %.4f\n", name, a, b);
    }
}

void CAntigravityConfig::AppendDiffInt(string &report, string name, int a, int b) {
    if (a != b) {
        report += StringFormat("  DIFF %-28s: %d vs %d\n", name, a, b);
    }
}

void CAntigravityConfig::AppendDiffBool(string &report, string name, bool a, bool b) {
    if (a != b) {
        report += StringFormat("  DIFF %-28s: %s vs %s\n", name,
                               (a ? "true" : "false"), (b ? "true" : "false"));
    }
}


//+------------------------------------------------------------------+
//| Utility: Load EA input parameters into a config object            |
//|                                                                   |
//| Call this from OnInit() to populate a CAntigravityConfig from     |
//| the EA's input variables. Then use ValidateSync() against the     |
//| JSON-loaded config.                                               |
//|                                                                   |
//| Example usage in OnInit():                                        |
//|   CAntigravityConfig jsonCfg;                                     |
//|   jsonCfg.LoadFromFile("AntigravityMTF_Config.json");             |
//|                                                                   |
//|   CAntigravityConfig inputCfg;                                    |
//|   LoadInputsToConfig(inputCfg);  // from EA input params          |
//|                                                                   |
//|   if (!jsonCfg.ValidateSync(inputCfg)) {                          |
//|       Print("WARNING: Input params differ from JSON config!");     |
//|       Print(jsonCfg.GetDiffReport(inputCfg));                     |
//|   }                                                               |
//+------------------------------------------------------------------+
// NOTE: LoadInputsToConfig() must be implemented in the main EA file
// because it references the global `input` variables which are only
// accessible there. Template:
//
// void LoadInputsToConfig(CAntigravityConfig &cfg) {
//     cfg.RiskPercent      = RiskPercent;
//     cfg.MaxLots          = MaxLots;
//     cfg.MinLots          = MinLots;
//     cfg.MaxSpread        = MaxSpread;
//     cfg.MaxDrawdownPct   = MaxDrawdownPct;
//     cfg.DDHalfRiskPct    = DDHalfRiskPct;
//     cfg.DailyMaxLossPct  = DailyMaxLossPct;
//     cfg.SL_ATR_Multi     = SL_ATR_Multi;
//     cfg.TP_ATR_Multi     = TP_ATR_Multi;
//     cfg.Trail_ATR_Multi  = Trail_ATR_Multi;
//     cfg.BE_ATR_Multi     = BE_ATR_Multi;
//     cfg.MinSL_Points     = MinSL_Points;
//     cfg.MaxSL_Points     = MaxSL_Points;
//     cfg.MinEntryScore    = MinEntryScore;
//     cfg.CooldownMinutes  = CooldownMinutes;
//     cfg.TradeStartHour   = TradeStartHour;
//     cfg.TradeEndHour     = TradeEndHour;
//     cfg.GMTOffset        = GMTOffset;
//     cfg.AvoidFriday      = AvoidFriday;
//     cfg.FridayCloseHour  = FridayCloseHour;
//     cfg.UsePartialClose  = UsePartialClose;
//     cfg.PartialCloseRatio= PartialCloseRatio;
//     cfg.PartialTP_Ratio  = PartialTP_Ratio;
//     cfg.UseNewsFilter    = UseNewsFilter;
//     cfg.UseWeekendClose  = UseWeekendClose;
//     cfg.UseCorrelation   = UseCorrelation;
//     // ... etc for all input params
// }


//+------------------------------------------------------------------+
//| Utility: Print config summary to Experts log                      |
//+------------------------------------------------------------------+
void PrintConfigSummary(CAntigravityConfig &cfg) {
    Print("=== AntigravityMTF Config v" + CONFIG_VERSION + " ===");
    PrintFormat("  Risk: %.2f%%, Lots: %.2f-%.2f, Spread<%d",
                cfg.RiskPercent, cfg.MinLots, cfg.MaxLots, cfg.MaxSpread);
    PrintFormat("  SL: %.1f*ATR [%.0f-%.0f pts], TP: %.1f*ATR",
                cfg.SL_ATR_Multi, cfg.MinSL_Points, cfg.MaxSL_Points, cfg.TP_ATR_Multi);
    PrintFormat("  BE: %.1f*ATR, Trail: %.1f*ATR, Chandelier: %d/%.1f",
                cfg.BE_ATR_Multi, cfg.Trail_ATR_Multi,
                cfg.Chandelier_Period, cfg.Chandelier_ATR_Multi);
    PrintFormat("  Entry: score>=%d, cooldown=%dm",
                cfg.MinEntryScore, cfg.CooldownMinutes);
    PrintFormat("  Time: %02d-%02d (GMT+%d), Friday=%s close@%d",
                cfg.TradeStartHour, cfg.TradeEndHour, cfg.GMTOffset,
                (cfg.AvoidFriday ? "avoid" : "trade"), cfg.FridayCloseHour);
    PrintFormat("  Regime: ADX<%d=ranging, TP cap=%.1f, ML=%s",
                cfg.RangingADXThreshold, cfg.RangingTPCap,
                (cfg.UseRegimeML ? "ON" : "OFF"));
    PrintFormat("  Features: RSI=%s Partial=%s(%.0f%%@%.0f%%) Reversal=%s",
                (cfg.UseRSIMomentumConfirm ? "ON" : "OFF"),
                (cfg.UsePartialClose ? "ON" : "OFF"),
                cfg.PartialCloseRatio * 100, cfg.PartialTP_Ratio * 100,
                (cfg.UseReversalMode ? "ON" : "OFF"));
    PrintFormat("  Features: Chandelier=%s EquityCurve=%s News=%s Weekend=%s Corr=%s",
                (cfg.UseChandelierExit ? "ON" : "OFF"),
                (cfg.UseEquityCurveFilter ? "ON" : "OFF"),
                (cfg.UseNewsFilter ? "ON" : "OFF"),
                (cfg.UseWeekendClose ? "ON" : "OFF"),
                (cfg.UseCorrelation ? "ON" : "OFF"));
    PrintFormat("  Kelly: lookback=%d, fraction=%.1f, risk=%.1f-%.1f%%",
                cfg.Kelly_Lookback, cfg.Kelly_Fraction,
                cfg.Kelly_MinRisk, cfg.Kelly_MaxRisk);

    // SRAT active hours
    string srat_str = "  SRAT: ";
    for (int h = 0; h < 24; h++) {
        if (cfg.SRAT[h] != 0) {
            srat_str += StringFormat("%d=%d ", h, cfg.SRAT[h]);
        }
    }
    Print(srat_str);

    // DD Escalation
    string dd_str = "  DD Escalation: ";
    for (int i = 0; i < 4; i++) {
        dd_str += StringFormat("%.0f%%->%d ", cfg.DD_Levels[i], cfg.DD_ScoreAdd[i]);
    }
    Print(dd_str);
    Print("=== End Config ===");
}

//+------------------------------------------------------------------+
