//+------------------------------------------------------------------+
//|                                   AntigravityMTF_Regime_ML.mqh   |
//|           Machine Learning Regime Detection for Gold (XAUUSD)    |
//|           Optional replacement for ADX threshold-based detection  |
//|                                                                  |
//|  4-State Classification: Trend / Range / HighVol / Crash         |
//|  Algorithm: Weighted KNN with online learning                    |
//|  Features: 7-dimensional (ADX, ER, VolRatio, RSI, Hour, BB, Pos)|
//|  Fallback: ADX-based detection when < MIN_TRAINING_SAMPLES       |
//|                                                                  |
//|  Usage:                                                          |
//|    #include <AntigravityMTF_Regime_ML.mqh>                       |
//|    CRegimeML regimeML;                                           |
//|    regimeML.Init(h4_adx, h4_atr, m15_atr, h1_rsi, h1_bb);      |
//|    int regime = regimeML.DetectRegime();                          |
//|    double lots = baseLots * regimeML.GetLotMultiplier();          |
//|                                                                  |
//|  Copyright 2026, Antigravity Trading System                      |
//+------------------------------------------------------------------+
#ifndef __ANTIGRAVITY_REGIME_ML_MQH__
#define __ANTIGRAVITY_REGIME_ML_MQH__

#property copyright "Antigravity Trading System"
#property version   "1.00"
// #property strict  // Removed: MQL4 only, not valid in MQL5

//+------------------------------------------------------------------+
//| Constants                                                        |
//+------------------------------------------------------------------+

// Regime labels
#define REGIME_TREND    0
#define REGIME_RANGE    1
#define REGIME_HIGHVOL  2
#define REGIME_CRASH    3
#define REGIME_COUNT    4

// Feature count
#define FEATURE_COUNT   7

// Feature indices (for readability)
#define FEAT_H4_ADX          0   // H4 ADX value (0-100, normalized 0-1)
#define FEAT_H4_ER           1   // H4 Efficiency Ratio (0-1 native)
#define FEAT_M15_VOL_RATIO   2   // M15 ATR / 50-period ATR avg
#define FEAT_H1_RSI          3   // H1 RSI (0-100, normalized 0-1)
#define FEAT_HOUR_NORM       4   // Hour of day (0-23, normalized 0-1)
#define FEAT_BB_WIDTH        5   // H1 Bollinger Band width (normalized)
#define FEAT_DAILY_POS       6   // Price position within daily range (0-1)

// Training constraints
#define MIN_TRAINING_SAMPLES 100  // Minimum samples before ML prediction
#define MAX_TRAINING_SAMPLES 2000 // Ring buffer capacity
#define DEFAULT_K            7    // KNN neighbor count (odd number)
#define ER_CALC_PERIOD       20   // Efficiency Ratio lookback
#define VOL_AVG_PERIOD       50   // ATR average lookback for vol ratio

// Online learning
#define ONLINE_UPDATE_INTERVAL 300 // Seconds between online updates (5 min)
#define REGIME_WEIGHT_DECAY    0.995 // Exponential decay per update cycle

// Fallback thresholds (ADX-based, matching EA v9.0 defaults)
#define FALLBACK_ER_TREND      0.3
#define FALLBACK_VOL_HIGH      1.5
#define FALLBACK_VOL_CRASH     3.0
#define FALLBACK_VOL_RANGE_CAP 1.2

//+------------------------------------------------------------------+
//| Training sample structure                                        |
//| Stored in ring buffer for memory-efficient online learning       |
//+------------------------------------------------------------------+
struct STrainingSample
{
   double features[FEATURE_COUNT]; // Normalized feature vector
   int    label;                   // Regime label (0-3)
   double weight;                  // Sample weight (adjusted by trade outcomes)
   datetime timestamp;             // When this sample was recorded
};

//+------------------------------------------------------------------+
//| Trade outcome record for online learning                         |
//+------------------------------------------------------------------+
struct STradeOutcome
{
   int    regime;      // Regime at time of trade entry
   bool   profitable;  // Was the trade profitable?
   double pf;          // Profit factor of the trade
   datetime timestamp; // When the outcome was recorded
};

//+------------------------------------------------------------------+
//| Feature normalization statistics (online min-max scaling)        |
//+------------------------------------------------------------------+
struct SFeatureStats
{
   double min_val;
   double max_val;
   double running_mean;
   int    count;
};

//+------------------------------------------------------------------+
//| CRegimeML - Machine Learning Regime Detection                    |
//|                                                                  |
//| Implements weighted KNN classification with online learning      |
//| for 4-state market regime detection on Gold (XAUUSD).            |
//|                                                                  |
//| The model self-trains during OnInit() using historical bars,     |
//| then continuously refines via OnTradeResult() feedback.          |
//+------------------------------------------------------------------+
class CRegimeML
{
private:
   //--- Indicator handles (external, not owned)
   int            m_h4_adx;
   int            m_h4_atr;      // H4 ATR handle (created internally if not provided)
   int            m_m15_atr;
   int            m_h1_rsi;
   int            m_h1_bb;
   bool           m_h4_atr_owned; // Whether we created the H4 ATR handle

   //--- KNN parameters
   int            m_k;           // Number of neighbors

   //--- Training data (ring buffer)
   STrainingSample m_samples[];
   int            m_sample_count;  // Total samples inserted (may exceed MAX)
   int            m_buffer_size;   // Current valid entries in ring buffer

   //--- Feature normalization
   SFeatureStats  m_feat_stats[FEATURE_COUNT];

   //--- Online learning: regime performance weights
   double         m_regime_weight[REGIME_COUNT]; // Weight multiplier per regime
   int            m_regime_win_count[REGIME_COUNT];
   int            m_regime_loss_count[REGIME_COUNT];
   double         m_regime_pf_sum[REGIME_COUNT];

   //--- Trade outcome history for online learning
   STradeOutcome  m_outcomes[];
   int            m_outcome_count;

   //--- State
   bool           m_initialized;
   int            m_current_regime;
   double         m_current_confidence;
   datetime       m_last_update_time;
   datetime       m_last_train_time;

   //--- Internal methods: Feature extraction
   bool           ExtractFeatures(double &features[]);
   double         CalcEfficiencyRatio(ENUM_TIMEFRAMES tf, int period, int shift);
   double         CalcVolatilityRatio(int shift);
   double         CalcBBWidth(int shift);
   double         CalcDailyPosition();
   double         GetIndicatorBuffer(int handle, int buffer, int shift);

   //--- Internal methods: Normalization
   void           InitFeatureStats();
   void           UpdateFeatureStats(const double &features[]);
   void           NormalizeFeatures(const double &raw[], double &normalized[]);
   double         NormalizeValue(double val, int feat_idx);

   //--- Internal methods: KNN
   double         EuclideanDistance(const double &v1[], const double &v2[]);
   double         WeightedEuclideanDistance(const double &v1[], const double &v2[]);
   int            PredictKNN(const double &features[], double &confidence);

   //--- Internal methods: Training
   void           AddSample(const double &features[], int label, double weight);
   int            LabelFromRules(const double &raw_features[]);
   void           TrainFromHistory(int bars_back);
   void           ApplyOnlineLearning();

   //--- Internal methods: Fallback
   int            FallbackDetect(const double &raw_features[]);

   //--- Internal methods: Serialization helpers
   string         DoubleArrayToStr(const double &arr[], int size);
   bool           StrToDoubleArray(string str, double &arr[], int expected_size);

public:
   //--- Constructor / Destructor
                  CRegimeML();
                 ~CRegimeML();

   //--- Primary API
   bool           Init(int h4_adx_handle, int h4_atr_handle,
                       int m15_atr_handle, int h1_rsi_handle, int h1_bb_handle);
   int            DetectRegime();
   double         GetRegimeConfidence();
   double         GetLotMultiplier();
   int            GetMinScoreAdjust();
   void           OnTradeResult(int regime, bool profitable, double pf);

   //--- Serialization
   string         ToJSON();
   bool           FromJSON(string json);

   //--- Diagnostics
   string         GetRegimeName(int regime);
   int            GetTrainingSampleCount();
   bool           IsMLActive();
   string         GetDiagnostics();
};

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CRegimeML::CRegimeML()
{
   m_h4_adx = INVALID_HANDLE;
   m_h4_atr = INVALID_HANDLE;
   m_m15_atr = INVALID_HANDLE;
   m_h1_rsi = INVALID_HANDLE;
   m_h1_bb = INVALID_HANDLE;
   m_h4_atr_owned = false;

   m_k = DEFAULT_K;
   m_sample_count = 0;
   m_buffer_size = 0;
   m_outcome_count = 0;
   m_initialized = false;
   m_current_regime = REGIME_TREND;
   m_current_confidence = 0.0;
   m_last_update_time = 0;
   m_last_train_time = 0;

   ArrayResize(m_samples, MAX_TRAINING_SAMPLES);
   ArrayResize(m_outcomes, 0);

   for(int i = 0; i < REGIME_COUNT; i++)
   {
      m_regime_weight[i] = 1.0;
      m_regime_win_count[i] = 0;
      m_regime_loss_count[i] = 0;
      m_regime_pf_sum[i] = 0.0;
   }

   InitFeatureStats();
}

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CRegimeML::~CRegimeML()
{
   if(m_h4_atr_owned && m_h4_atr != INVALID_HANDLE)
      IndicatorRelease(m_h4_atr);
}

//+------------------------------------------------------------------+
//| Initialize the ML regime detector                                |
//|                                                                  |
//| Parameters:                                                      |
//|   h4_adx_handle  - iADX handle on H4 timeframe                  |
//|   h4_atr_handle  - iATR handle on H4 (INVALID_HANDLE to auto-   |
//|                    create with period 14)                         |
//|   m15_atr_handle - iATR handle on M15 timeframe                  |
//|   h1_rsi_handle  - iRSI handle on H1 timeframe                  |
//|   h1_bb_handle   - iBands handle on H1 timeframe                 |
//|                                                                  |
//| Returns: true if initialization succeeded                        |
//+------------------------------------------------------------------+
bool CRegimeML::Init(int h4_adx_handle, int h4_atr_handle,
                     int m15_atr_handle, int h1_rsi_handle, int h1_bb_handle)
{
   //--- Validate required handles
   if(h4_adx_handle == INVALID_HANDLE ||
      m15_atr_handle == INVALID_HANDLE ||
      h1_rsi_handle == INVALID_HANDLE ||
      h1_bb_handle == INVALID_HANDLE)
   {
      Print("[RegimeML] ERROR: One or more required indicator handles are invalid");
      return false;
   }

   m_h4_adx = h4_adx_handle;
   m_m15_atr = m15_atr_handle;
   m_h1_rsi = h1_rsi_handle;
   m_h1_bb = h1_bb_handle;

   //--- H4 ATR: use provided handle or create our own
   if(h4_atr_handle != INVALID_HANDLE)
   {
      m_h4_atr = h4_atr_handle;
      m_h4_atr_owned = false;
   }
   else
   {
      m_h4_atr = iATR(_Symbol, PERIOD_H4, 14);
      if(m_h4_atr == INVALID_HANDLE)
      {
         Print("[RegimeML] ERROR: Failed to create H4 ATR indicator");
         return false;
      }
      m_h4_atr_owned = true;
   }

   //--- Initialize feature statistics
   InitFeatureStats();

   //--- Train from historical data
   //    Use ~500 H4 bars of history (roughly 3 months of trading data)
   //    This provides diverse market conditions for initial training
   TrainFromHistory(500);

   Print("[RegimeML] Initialized: ", m_buffer_size, " training samples from history",
         " | ML active: ", (m_buffer_size >= MIN_TRAINING_SAMPLES ? "YES" : "NO (fallback)"));

   m_initialized = true;
   m_last_update_time = TimeCurrent();

   return true;
}

//+------------------------------------------------------------------+
//| Detect current market regime                                     |
//|                                                                  |
//| Returns: REGIME_TREND(0), REGIME_RANGE(1),                       |
//|          REGIME_HIGHVOL(2), REGIME_CRASH(3)                      |
//+------------------------------------------------------------------+
int CRegimeML::DetectRegime()
{
   if(!m_initialized)
   {
      Print("[RegimeML] WARNING: Not initialized, returning REGIME_TREND");
      return REGIME_TREND;
   }

   //--- Extract current features
   double raw_features[];
   ArrayResize(raw_features, FEATURE_COUNT);

   if(!ExtractFeatures(raw_features))
   {
      Print("[RegimeML] WARNING: Feature extraction failed, using last regime");
      return m_current_regime;
   }

   //--- Decide: ML prediction or fallback
   int regime;
   double confidence = 0.0;

   if(m_buffer_size >= MIN_TRAINING_SAMPLES)
   {
      //--- Normalize features for KNN
      double norm_features[];
      ArrayResize(norm_features, FEATURE_COUNT);
      NormalizeFeatures(raw_features, norm_features);

      regime = PredictKNN(norm_features, confidence);

      //--- Apply online learning weight adjustment
      //    If a regime has consistently poor performance, reduce confidence
      //    and potentially shift toward adjacent regime
      if(m_regime_weight[regime] < 0.5 && confidence < 0.7)
      {
         //--- Low-confidence prediction in a poorly-performing regime
         //    Fall back to rule-based for safety
         int fb = FallbackDetect(raw_features);
         if(fb != regime)
         {
            regime = fb;
            confidence *= 0.5; // Halve confidence for fallback override
         }
      }
   }
   else
   {
      //--- Insufficient training data: use rule-based fallback
      regime = FallbackDetect(raw_features);
      confidence = 0.3; // Low confidence for fallback
   }

   //--- Update state
   m_current_regime = regime;
   m_current_confidence = confidence;

   //--- Periodically add current observation to training set
   //    Label using rule-based heuristic for self-supervised learning
   datetime now = TimeCurrent();
   if(now - m_last_update_time >= ONLINE_UPDATE_INTERVAL)
   {
      int heuristic_label = LabelFromRules(raw_features);
      UpdateFeatureStats(raw_features);

      double norm_for_store[];
      ArrayResize(norm_for_store, FEATURE_COUNT);
      NormalizeFeatures(raw_features, norm_for_store);
      AddSample(norm_for_store, heuristic_label, 1.0);

      m_last_update_time = now;
   }

   return m_current_regime;
}

//+------------------------------------------------------------------+
//| Get confidence of last regime detection (0.0-1.0)                |
//+------------------------------------------------------------------+
double CRegimeML::GetRegimeConfidence()
{
   return m_current_confidence;
}

//+------------------------------------------------------------------+
//| Get lot size multiplier for current regime                       |
//|                                                                  |
//| Trend:   1.0 (full position - trending markets offer best R:R)  |
//| Range:   0.6 (reduced - lower directional edge)                  |
//| HighVol: 0.3 (defensive - wider stops eat into position size)    |
//| Crash:   0.0 (no trading - extreme conditions)                   |
//+------------------------------------------------------------------+
double CRegimeML::GetLotMultiplier()
{
   //--- Base multipliers per regime
   double base_mult[] = {1.0, 0.6, 0.3, 0.0};

   double mult = base_mult[m_current_regime];

   //--- Scale by confidence: low confidence -> more conservative
   //    At 50% confidence, use midpoint between base and 0.5
   if(m_current_confidence < 0.6 && m_current_regime != REGIME_CRASH)
   {
      double conservative = 0.5;
      double alpha = m_current_confidence / 0.6;
      mult = conservative + alpha * (mult - conservative);
   }

   //--- Apply regime performance weight from online learning
   //    Poor-performing regime gets further reduction
   if(m_current_regime != REGIME_CRASH)
   {
      mult *= MathMin(m_regime_weight[m_current_regime], 1.0);
   }

   return MathMax(0.0, MathMin(1.0, mult));
}

//+------------------------------------------------------------------+
//| Get minimum score adjustment for current regime                  |
//|                                                                  |
//| In volatile/uncertain regimes, require higher entry scores       |
//| to filter out noise-driven signals.                              |
//|                                                                  |
//| Trend:   0  (standard threshold)                                 |
//| Range:   0  (standard - already filtered by lot reduction)       |
//| HighVol: +1 (higher bar for entry in volatile conditions)        |
//| Crash:   +3 (very high bar - almost no entries pass)             |
//+------------------------------------------------------------------+
int CRegimeML::GetMinScoreAdjust()
{
   int base_adjust[] = {0, 0, 1, 3};
   int adj = base_adjust[m_current_regime];

   //--- Additional adjustment if regime has poor trade outcomes
   if(m_regime_weight[m_current_regime] < 0.7 && m_current_regime != REGIME_CRASH)
      adj += 1;

   return adj;
}

//+------------------------------------------------------------------+
//| Record trade outcome for online learning                         |
//|                                                                  |
//| This is the key feedback mechanism. By tracking which regime     |
//| produces profitable trades, the model adjusts its behavior:      |
//|   - Regimes with high win rates get weight boosts                |
//|   - Regimes with poor results get weight penalties               |
//|   - Training samples from profitable regimes get upweighted      |
//+------------------------------------------------------------------+
void CRegimeML::OnTradeResult(int regime, bool profitable, double pf)
{
   if(regime < 0 || regime >= REGIME_COUNT) return;

   //--- Record outcome
   int idx = m_outcome_count;
   m_outcome_count++;
   ArrayResize(m_outcomes, m_outcome_count);
   m_outcomes[idx].regime = regime;
   m_outcomes[idx].profitable = profitable;
   m_outcomes[idx].pf = pf;
   m_outcomes[idx].timestamp = TimeCurrent();

   //--- Update regime statistics
   if(profitable)
      m_regime_win_count[regime]++;
   else
      m_regime_loss_count[regime]++;

   m_regime_pf_sum[regime] += pf;

   //--- Recalculate regime weight
   //    Weight = smoothed win rate * PF factor
   int total = m_regime_win_count[regime] + m_regime_loss_count[regime];
   if(total >= 5) // Need minimum trades for statistical significance
   {
      double win_rate = (double)m_regime_win_count[regime] / total;
      double avg_pf = m_regime_pf_sum[regime] / total;

      // Weight combines win rate and profit factor
      // win_rate=0.5, avg_pf=1.0 -> weight~0.7 (baseline)
      // win_rate=0.6, avg_pf=1.5 -> weight~1.0 (good regime)
      // win_rate=0.3, avg_pf=0.5 -> weight~0.3 (poor regime)
      double raw_weight = win_rate * MathSqrt(MathMax(avg_pf, 0.01));
      m_regime_weight[regime] = MathMax(0.2, MathMin(1.5, raw_weight / 0.7));
   }

   //--- Upweight or downweight matching training samples
   ApplyOnlineLearning();
}

//+------------------------------------------------------------------+
//| Serialize model state to JSON string                             |
//|                                                                  |
//| Lightweight JSON serialization without external dependencies.    |
//| Saves: regime weights, feature stats, sample count, outcomes.    |
//| Does NOT save full training data (re-trained from history).      |
//+------------------------------------------------------------------+
string CRegimeML::ToJSON()
{
   string json = "{";

   //--- Version
   json += "\"version\":1,";

   //--- Regime weights
   json += "\"regime_weights\":[";
   for(int i = 0; i < REGIME_COUNT; i++)
   {
      json += DoubleToString(m_regime_weight[i], 6);
      if(i < REGIME_COUNT - 1) json += ",";
   }
   json += "],";

   //--- Win/loss counts
   json += "\"regime_wins\":[";
   for(int i = 0; i < REGIME_COUNT; i++)
   {
      json += IntegerToString(m_regime_win_count[i]);
      if(i < REGIME_COUNT - 1) json += ",";
   }
   json += "],";

   json += "\"regime_losses\":[";
   for(int i = 0; i < REGIME_COUNT; i++)
   {
      json += IntegerToString(m_regime_loss_count[i]);
      if(i < REGIME_COUNT - 1) json += ",";
   }
   json += "],";

   //--- PF sums
   json += "\"regime_pf_sum\":[";
   for(int i = 0; i < REGIME_COUNT; i++)
   {
      json += DoubleToString(m_regime_pf_sum[i], 4);
      if(i < REGIME_COUNT - 1) json += ",";
   }
   json += "],";

   //--- Feature stats
   json += "\"feat_stats\":[";
   for(int i = 0; i < FEATURE_COUNT; i++)
   {
      json += "{\"min\":" + DoubleToString(m_feat_stats[i].min_val, 8) +
              ",\"max\":" + DoubleToString(m_feat_stats[i].max_val, 8) +
              ",\"mean\":" + DoubleToString(m_feat_stats[i].running_mean, 8) +
              ",\"count\":" + IntegerToString(m_feat_stats[i].count) + "}";
      if(i < FEATURE_COUNT - 1) json += ",";
   }
   json += "],";

   //--- Metadata
   json += "\"sample_count\":" + IntegerToString(m_sample_count) + ",";
   json += "\"buffer_size\":" + IntegerToString(m_buffer_size) + ",";
   json += "\"outcome_count\":" + IntegerToString(m_outcome_count) + ",";
   json += "\"k\":" + IntegerToString(m_k) + ",";
   json += "\"last_regime\":" + IntegerToString(m_current_regime) + ",";
   json += "\"last_confidence\":" + DoubleToString(m_current_confidence, 4);

   json += "}";
   return json;
}

//+------------------------------------------------------------------+
//| Deserialize model state from JSON string                         |
//|                                                                  |
//| Restores: regime weights, feature stats, counters.               |
//| Training samples are NOT restored (re-built from history).       |
//| Uses minimal hand-rolled JSON parser for MQL5 compatibility.     |
//+------------------------------------------------------------------+
bool CRegimeML::FromJSON(string json)
{
   if(StringLen(json) < 10)
   {
      Print("[RegimeML] FromJSON: Invalid JSON (too short)");
      return false;
   }

   //--- Parse version
   int vpos = StringFind(json, "\"version\":");
   if(vpos < 0)
   {
      Print("[RegimeML] FromJSON: Missing version field");
      return false;
   }

   //--- Parse regime weights
   int rw_start = StringFind(json, "\"regime_weights\":[");
   if(rw_start >= 0)
   {
      rw_start += StringLen("\"regime_weights\":[");
      int rw_end = StringFind(json, "]", rw_start);
      if(rw_end > rw_start)
      {
         string rw_str = StringSubstr(json, rw_start, rw_end - rw_start);
         string parts[];
         int count = StringSplit(rw_str, ',', parts);
         for(int i = 0; i < MathMin(count, REGIME_COUNT); i++)
            m_regime_weight[i] = StringToDouble(parts[i]);
      }
   }

   //--- Parse regime wins
   int wins_start = StringFind(json, "\"regime_wins\":[");
   if(wins_start >= 0)
   {
      wins_start += StringLen("\"regime_wins\":[");
      int wins_end = StringFind(json, "]", wins_start);
      if(wins_end > wins_start)
      {
         string wins_str = StringSubstr(json, wins_start, wins_end - wins_start);
         string parts[];
         int count = StringSplit(wins_str, ',', parts);
         for(int i = 0; i < MathMin(count, REGIME_COUNT); i++)
            m_regime_win_count[i] = (int)StringToInteger(parts[i]);
      }
   }

   //--- Parse regime losses
   int losses_start = StringFind(json, "\"regime_losses\":[");
   if(losses_start >= 0)
   {
      losses_start += StringLen("\"regime_losses\":[");
      int losses_end = StringFind(json, "]", losses_start);
      if(losses_end > losses_start)
      {
         string losses_str = StringSubstr(json, losses_start, losses_end - losses_start);
         string parts[];
         int count = StringSplit(losses_str, ',', parts);
         for(int i = 0; i < MathMin(count, REGIME_COUNT); i++)
            m_regime_loss_count[i] = (int)StringToInteger(parts[i]);
      }
   }

   //--- Parse PF sums
   int pf_start = StringFind(json, "\"regime_pf_sum\":[");
   if(pf_start >= 0)
   {
      pf_start += StringLen("\"regime_pf_sum\":[");
      int pf_end = StringFind(json, "]", pf_start);
      if(pf_end > pf_start)
      {
         string pf_str = StringSubstr(json, pf_start, pf_end - pf_start);
         string parts[];
         int count = StringSplit(pf_str, ',', parts);
         for(int i = 0; i < MathMin(count, REGIME_COUNT); i++)
            m_regime_pf_sum[i] = StringToDouble(parts[i]);
      }
   }

   //--- Parse feature stats
   int fs_start = StringFind(json, "\"feat_stats\":[");
   if(fs_start >= 0)
   {
      fs_start += StringLen("\"feat_stats\":[");
      for(int i = 0; i < FEATURE_COUNT; i++)
      {
         int obj_start = StringFind(json, "{", fs_start);
         int obj_end = StringFind(json, "}", obj_start);
         if(obj_start < 0 || obj_end < 0) break;

         string obj = StringSubstr(json, obj_start, obj_end - obj_start + 1);

         // Parse min
         int min_pos = StringFind(obj, "\"min\":");
         if(min_pos >= 0)
         {
            int val_start = min_pos + StringLen("\"min\":");
            int val_end = StringFind(obj, ",", val_start);
            if(val_end > val_start)
               m_feat_stats[i].min_val = StringToDouble(StringSubstr(obj, val_start, val_end - val_start));
         }

         // Parse max
         int max_pos = StringFind(obj, "\"max\":");
         if(max_pos >= 0)
         {
            int val_start = max_pos + StringLen("\"max\":");
            int val_end = StringFind(obj, ",", val_start);
            if(val_end > val_start)
               m_feat_stats[i].max_val = StringToDouble(StringSubstr(obj, val_start, val_end - val_start));
         }

         // Parse mean
         int mean_pos = StringFind(obj, "\"mean\":");
         if(mean_pos >= 0)
         {
            int val_start = mean_pos + StringLen("\"mean\":");
            int val_end = StringFind(obj, ",", val_start);
            if(val_end > val_start)
               m_feat_stats[i].running_mean = StringToDouble(StringSubstr(obj, val_start, val_end - val_start));
         }

         // Parse count
         int cnt_pos = StringFind(obj, "\"count\":");
         if(cnt_pos >= 0)
         {
            int val_start = cnt_pos + StringLen("\"count\":");
            int val_end = StringFind(obj, "}", val_start);
            if(val_end > val_start)
               m_feat_stats[i].count = (int)StringToInteger(StringSubstr(obj, val_start, val_end - val_start));
         }

         fs_start = obj_end + 1;
      }
   }

   //--- Parse scalar metadata
   int sc_pos = StringFind(json, "\"sample_count\":");
   if(sc_pos >= 0)
   {
      int val_start = sc_pos + StringLen("\"sample_count\":");
      int val_end = StringFind(json, ",", val_start);
      if(val_end > val_start)
         m_sample_count = (int)StringToInteger(StringSubstr(json, val_start, val_end - val_start));
   }

   int oc_pos = StringFind(json, "\"outcome_count\":");
   if(oc_pos >= 0)
   {
      int val_start = oc_pos + StringLen("\"outcome_count\":");
      int val_end = StringFind(json, ",", val_start);
      if(val_end > val_start)
         m_outcome_count = (int)StringToInteger(StringSubstr(json, val_start, val_end - val_start));
   }

   int k_pos = StringFind(json, "\"k\":");
   if(k_pos >= 0)
   {
      int val_start = k_pos + StringLen("\"k\":");
      int val_end = StringFind(json, ",", val_start);
      if(val_end > val_start)
         m_k = (int)StringToInteger(StringSubstr(json, val_start, val_end - val_start));
   }

   Print("[RegimeML] FromJSON: Restored state | outcomes=", m_outcome_count,
         " | weights=[", DoubleToString(m_regime_weight[0], 2), ",",
         DoubleToString(m_regime_weight[1], 2), ",",
         DoubleToString(m_regime_weight[2], 2), ",",
         DoubleToString(m_regime_weight[3], 2), "]");

   return true;
}

//+------------------------------------------------------------------+
//| Get human-readable regime name                                   |
//+------------------------------------------------------------------+
string CRegimeML::GetRegimeName(int regime)
{
   switch(regime)
   {
      case REGIME_TREND:   return "Trend";
      case REGIME_RANGE:   return "Range";
      case REGIME_HIGHVOL: return "HighVol";
      case REGIME_CRASH:   return "Crash";
      default:             return "Unknown";
   }
}

//+------------------------------------------------------------------+
//| Get current training sample count                                |
//+------------------------------------------------------------------+
int CRegimeML::GetTrainingSampleCount()
{
   return m_buffer_size;
}

//+------------------------------------------------------------------+
//| Check if ML model is active (vs fallback mode)                   |
//+------------------------------------------------------------------+
bool CRegimeML::IsMLActive()
{
   return m_buffer_size >= MIN_TRAINING_SAMPLES;
}

//+------------------------------------------------------------------+
//| Get diagnostic string for logging                                |
//+------------------------------------------------------------------+
string CRegimeML::GetDiagnostics()
{
   string diag = "[RegimeML] ";
   diag += "Regime=" + GetRegimeName(m_current_regime);
   diag += " Conf=" + DoubleToString(m_current_confidence, 2);
   diag += " ML=" + (IsMLActive() ? "ON" : "OFF");
   diag += " Samples=" + IntegerToString(m_buffer_size);
   diag += " Outcomes=" + IntegerToString(m_outcome_count);
   diag += " Weights=[";
   for(int i = 0; i < REGIME_COUNT; i++)
   {
      diag += DoubleToString(m_regime_weight[i], 2);
      if(i < REGIME_COUNT - 1) diag += ",";
   }
   diag += "]";
   diag += " LotMult=" + DoubleToString(GetLotMultiplier(), 2);
   diag += " ScoreAdj=" + IntegerToString(GetMinScoreAdjust());
   return diag;
}

//+------------------------------------------------------------------+
//|                                                                  |
//|  === PRIVATE METHODS ===                                         |
//|                                                                  |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Extract 7 raw features from current market state                 |
//|                                                                  |
//| Features are extracted in their natural scale. Normalization      |
//| is applied separately before KNN distance calculation.           |
//+------------------------------------------------------------------+
bool CRegimeML::ExtractFeatures(double &features[])
{
   ArrayResize(features, FEATURE_COUNT);

   //--- Feature 0: H4 ADX (0-100)
   double adx_val = GetIndicatorBuffer(m_h4_adx, 0, 1); // Buffer 0 = main ADX line
   if(adx_val <= 0) return false;
   features[FEAT_H4_ADX] = adx_val;

   //--- Feature 1: H4 Efficiency Ratio (0-1)
   double er = CalcEfficiencyRatio(PERIOD_H4, ER_CALC_PERIOD, 1);
   if(er < 0) return false;
   features[FEAT_H4_ER] = er;

   //--- Feature 2: M15 ATR / 50-period ATR average (volatility ratio)
   double vol_ratio = CalcVolatilityRatio(1);
   if(vol_ratio <= 0) return false;
   features[FEAT_M15_VOL_RATIO] = vol_ratio;

   //--- Feature 3: H1 RSI (0-100)
   double rsi_val = GetIndicatorBuffer(m_h1_rsi, 0, 1);
   if(rsi_val <= 0) return false;
   features[FEAT_H1_RSI] = rsi_val;

   //--- Feature 4: Hour of day, normalized 0-1
   MqlDateTime dt;
   TimeCurrent(dt);
   features[FEAT_HOUR_NORM] = (double)dt.hour / 23.0;

   //--- Feature 5: H1 Bollinger Band width (normalized by middle band)
   double bb_width = CalcBBWidth(1);
   if(bb_width < 0) return false;
   features[FEAT_BB_WIDTH] = bb_width;

   //--- Feature 6: Price position within daily range (0-1)
   double daily_pos = CalcDailyPosition();
   if(daily_pos < 0) return false;
   features[FEAT_DAILY_POS] = daily_pos;

   return true;
}

//+------------------------------------------------------------------+
//| Calculate Efficiency Ratio for a given timeframe                 |
//|                                                                  |
//| ER = |net price change| / sum of |individual bar changes|        |
//| ER near 1.0 = strong trend (price moves efficiently)             |
//| ER near 0.0 = ranging/noisy (price goes nowhere despite moving)  |
//+------------------------------------------------------------------+
double CRegimeML::CalcEfficiencyRatio(ENUM_TIMEFRAMES tf, int period, int shift)
{
   double close_arr[];
   ArraySetAsSeries(close_arr, true);

   if(CopyClose(_Symbol, tf, shift, period + 1, close_arr) < period + 1)
      return -1.0;

   double net_change = MathAbs(close_arr[0] - close_arr[period]);
   double sum_abs = 0.0;

   for(int i = 0; i < period; i++)
      sum_abs += MathAbs(close_arr[i] - close_arr[i + 1]);

   if(sum_abs <= 0) return 0.0;

   return net_change / sum_abs;
}

//+------------------------------------------------------------------+
//| Calculate M15 ATR volatility ratio                               |
//|                                                                  |
//| Ratio = current ATR / average ATR over VOL_AVG_PERIOD bars       |
//| Ratio > 1.5 = high volatility                                    |
//| Ratio > 3.0 = crash/extreme volatility                           |
//| Ratio < 0.7 = unusually low volatility                           |
//+------------------------------------------------------------------+
double CRegimeML::CalcVolatilityRatio(int shift)
{
   double atr_arr[];
   ArraySetAsSeries(atr_arr, true);

   if(CopyBuffer(m_m15_atr, 0, shift, VOL_AVG_PERIOD, atr_arr) < VOL_AVG_PERIOD)
      return -1.0;

   double current_atr = atr_arr[0];
   double sum = 0.0;

   for(int i = 0; i < VOL_AVG_PERIOD; i++)
      sum += atr_arr[i];

   double avg_atr = sum / VOL_AVG_PERIOD;

   if(avg_atr <= 0) return -1.0;

   return current_atr / avg_atr;
}

//+------------------------------------------------------------------+
//| Calculate H1 Bollinger Band width (normalized)                   |
//|                                                                  |
//| BB Width = (Upper - Lower) / Middle                              |
//| Narrow BB = low volatility, potential breakout                    |
//| Wide BB = high volatility, trending or volatile                   |
//+------------------------------------------------------------------+
double CRegimeML::CalcBBWidth(int shift)
{
   double upper = GetIndicatorBuffer(m_h1_bb, 1, shift); // Buffer 1 = upper band
   double lower = GetIndicatorBuffer(m_h1_bb, 2, shift); // Buffer 2 = lower band
   double middle = GetIndicatorBuffer(m_h1_bb, 0, shift); // Buffer 0 = middle band

   if(middle <= 0 || upper <= 0 || lower <= 0) return -1.0;

   return (upper - lower) / middle;
}

//+------------------------------------------------------------------+
//| Calculate price position within daily range (0-1)                |
//|                                                                  |
//| 0.0 = price at daily low                                         |
//| 1.0 = price at daily high                                        |
//| Useful for session context and mean-reversion signals            |
//+------------------------------------------------------------------+
double CRegimeML::CalcDailyPosition()
{
   double high_arr[], low_arr[], close_arr[];
   ArraySetAsSeries(high_arr, true);
   ArraySetAsSeries(low_arr, true);
   ArraySetAsSeries(close_arr, true);

   //--- Use the current (incomplete) daily bar
   if(CopyHigh(_Symbol, PERIOD_D1, 0, 1, high_arr) < 1) return -1.0;
   if(CopyLow(_Symbol, PERIOD_D1, 0, 1, low_arr) < 1) return -1.0;
   if(CopyClose(_Symbol, PERIOD_D1, 0, 1, close_arr) < 1) return -1.0;

   double range = high_arr[0] - low_arr[0];
   if(range <= 0) return 0.5; // No range yet, assume middle

   return (close_arr[0] - low_arr[0]) / range;
}

//+------------------------------------------------------------------+
//| Safely read a single value from an indicator buffer              |
//+------------------------------------------------------------------+
double CRegimeML::GetIndicatorBuffer(int handle, int buffer, int shift)
{
   if(handle == INVALID_HANDLE) return -1.0;

   double val[];
   ArraySetAsSeries(val, true);

   if(CopyBuffer(handle, buffer, shift, 1, val) < 1)
      return -1.0;

   return val[0];
}

//+------------------------------------------------------------------+
//| Initialize feature normalization statistics                      |
//+------------------------------------------------------------------+
void CRegimeML::InitFeatureStats()
{
   //--- Set reasonable initial ranges based on domain knowledge for Gold
   //    These will be refined as data flows in

   // ADX: typical range 10-60
   m_feat_stats[FEAT_H4_ADX].min_val = 10.0;
   m_feat_stats[FEAT_H4_ADX].max_val = 60.0;
   m_feat_stats[FEAT_H4_ADX].running_mean = 25.0;
   m_feat_stats[FEAT_H4_ADX].count = 0;

   // ER: natural range 0-1
   m_feat_stats[FEAT_H4_ER].min_val = 0.0;
   m_feat_stats[FEAT_H4_ER].max_val = 1.0;
   m_feat_stats[FEAT_H4_ER].running_mean = 0.3;
   m_feat_stats[FEAT_H4_ER].count = 0;

   // Vol Ratio: typical 0.3-4.0
   m_feat_stats[FEAT_M15_VOL_RATIO].min_val = 0.3;
   m_feat_stats[FEAT_M15_VOL_RATIO].max_val = 4.0;
   m_feat_stats[FEAT_M15_VOL_RATIO].running_mean = 1.0;
   m_feat_stats[FEAT_M15_VOL_RATIO].count = 0;

   // RSI: range 0-100
   m_feat_stats[FEAT_H1_RSI].min_val = 15.0;
   m_feat_stats[FEAT_H1_RSI].max_val = 85.0;
   m_feat_stats[FEAT_H1_RSI].running_mean = 50.0;
   m_feat_stats[FEAT_H1_RSI].count = 0;

   // Hour: 0-1 (already normalized)
   m_feat_stats[FEAT_HOUR_NORM].min_val = 0.0;
   m_feat_stats[FEAT_HOUR_NORM].max_val = 1.0;
   m_feat_stats[FEAT_HOUR_NORM].running_mean = 0.5;
   m_feat_stats[FEAT_HOUR_NORM].count = 0;

   // BB Width: typical 0.005-0.05 for Gold
   m_feat_stats[FEAT_BB_WIDTH].min_val = 0.005;
   m_feat_stats[FEAT_BB_WIDTH].max_val = 0.05;
   m_feat_stats[FEAT_BB_WIDTH].running_mean = 0.015;
   m_feat_stats[FEAT_BB_WIDTH].count = 0;

   // Daily Position: 0-1 (natural range)
   m_feat_stats[FEAT_DAILY_POS].min_val = 0.0;
   m_feat_stats[FEAT_DAILY_POS].max_val = 1.0;
   m_feat_stats[FEAT_DAILY_POS].running_mean = 0.5;
   m_feat_stats[FEAT_DAILY_POS].count = 0;
}

//+------------------------------------------------------------------+
//| Update feature statistics with new observation                   |
//| Uses online min-max update with exponential smoothing             |
//+------------------------------------------------------------------+
void CRegimeML::UpdateFeatureStats(const double &features[])
{
   for(int i = 0; i < FEATURE_COUNT; i++)
   {
      double val = features[i];
      m_feat_stats[i].count++;

      //--- Update min/max with 1% padding to avoid division by zero
      if(val < m_feat_stats[i].min_val)
         m_feat_stats[i].min_val = val;
      if(val > m_feat_stats[i].max_val)
         m_feat_stats[i].max_val = val;

      //--- Exponential moving average for mean
      double alpha = (m_feat_stats[i].count < 100) ? 0.1 : 0.01;
      m_feat_stats[i].running_mean = (1.0 - alpha) * m_feat_stats[i].running_mean + alpha * val;
   }
}

//+------------------------------------------------------------------+
//| Normalize raw features to [0, 1] using min-max scaling           |
//+------------------------------------------------------------------+
void CRegimeML::NormalizeFeatures(const double &raw[], double &normalized[])
{
   ArrayResize(normalized, FEATURE_COUNT);

   for(int i = 0; i < FEATURE_COUNT; i++)
      normalized[i] = NormalizeValue(raw[i], i);
}

//+------------------------------------------------------------------+
//| Normalize a single value for a given feature index               |
//+------------------------------------------------------------------+
double CRegimeML::NormalizeValue(double val, int feat_idx)
{
   double range = m_feat_stats[feat_idx].max_val - m_feat_stats[feat_idx].min_val;

   if(range <= 0) return 0.5; // Degenerate case

   double norm = (val - m_feat_stats[feat_idx].min_val) / range;

   //--- Clip to [0, 1]
   return MathMax(0.0, MathMin(1.0, norm));
}

//+------------------------------------------------------------------+
//| Euclidean distance between two feature vectors                   |
//+------------------------------------------------------------------+
double CRegimeML::EuclideanDistance(const double &v1[], const double &v2[])
{
   double sum = 0.0;

   for(int i = 0; i < FEATURE_COUNT; i++)
   {
      double diff = v1[i] - v2[i];
      sum += diff * diff;
   }

   return MathSqrt(sum);
}

//+------------------------------------------------------------------+
//| Weighted Euclidean distance with feature importance              |
//|                                                                  |
//| Feature weights reflect importance for regime detection:         |
//|   ADX (1.5)      - Direct trend strength measure                 |
//|   ER  (1.5)      - Direction efficiency, key differentiator      |
//|   VolRatio (2.0)  - Critical for HighVol/Crash detection         |
//|   RSI (0.8)      - Secondary momentum context                    |
//|   Hour (0.5)     - Weak session context                          |
//|   BB Width (1.2)  - Volatility confirmation                      |
//|   Daily Pos (0.5) - Weak positional context                      |
//+------------------------------------------------------------------+
double CRegimeML::WeightedEuclideanDistance(const double &v1[], const double &v2[])
{
   //--- Feature importance weights (domain-knowledge priors for Gold)
   double feat_weights[] = {1.5, 1.5, 2.0, 0.8, 0.5, 1.2, 0.5};

   double sum = 0.0;

   for(int i = 0; i < FEATURE_COUNT; i++)
   {
      double diff = v1[i] - v2[i];
      sum += feat_weights[i] * diff * diff;
   }

   return MathSqrt(sum);
}

//+------------------------------------------------------------------+
//| KNN prediction with weighted voting                              |
//|                                                                  |
//| Algorithm:                                                       |
//| 1. Compute weighted distance to all training samples             |
//| 2. Find k nearest neighbors                                      |
//| 3. Weight votes by 1/distance and sample weight                  |
//| 4. Return class with highest weighted vote                       |
//| 5. Confidence = winning vote share / total votes                 |
//+------------------------------------------------------------------+
int CRegimeML::PredictKNN(const double &features[], double &confidence)
{
   if(m_buffer_size == 0)
   {
      confidence = 0.0;
      return REGIME_TREND;
   }

   //--- Calculate distances to all training samples
   double distances[];
   int    indices[];
   ArrayResize(distances, m_buffer_size);
   ArrayResize(indices, m_buffer_size);

   for(int i = 0; i < m_buffer_size; i++)
   {
      distances[i] = WeightedEuclideanDistance(features, m_samples[i].features);
      indices[i] = i;
   }

   //--- Find k nearest neighbors using partial sort
   //    Simple selection: repeatedly find minimum and swap to front
   int effective_k = MathMin(m_k, m_buffer_size);

   for(int i = 0; i < effective_k; i++)
   {
      int min_idx = i;
      for(int j = i + 1; j < m_buffer_size; j++)
      {
         if(distances[j] < distances[min_idx])
            min_idx = j;
      }
      //--- Swap
      if(min_idx != i)
      {
         double tmp_d = distances[i];
         distances[i] = distances[min_idx];
         distances[min_idx] = tmp_d;

         int tmp_i = indices[i];
         indices[i] = indices[min_idx];
         indices[min_idx] = tmp_i;
      }
   }

   //--- Weighted voting among k neighbors
   double votes[REGIME_COUNT];
   ArrayInitialize(votes, 0.0);

   for(int i = 0; i < effective_k; i++)
   {
      int sample_idx = indices[i];
      double dist = distances[i];
      int label = m_samples[sample_idx].label;
      double sample_weight = m_samples[sample_idx].weight;

      //--- Inverse distance weighting (add epsilon to avoid division by zero)
      double vote_weight = 1.0 / (dist + 1e-8);

      //--- Apply sample weight from online learning
      vote_weight *= sample_weight;

      //--- Apply regime performance weight
      vote_weight *= m_regime_weight[label];

      votes[label] += vote_weight;
   }

   //--- Find winning class
   int best_class = 0;
   double best_vote = votes[0];
   double total_votes = votes[0];

   for(int i = 1; i < REGIME_COUNT; i++)
   {
      total_votes += votes[i];
      if(votes[i] > best_vote)
      {
         best_vote = votes[i];
         best_class = i;
      }
   }

   //--- Calculate confidence
   if(total_votes > 0)
      confidence = best_vote / total_votes;
   else
      confidence = 0.0;

   return best_class;
}

//+------------------------------------------------------------------+
//| Add a training sample to the ring buffer                         |
//|                                                                  |
//| Uses modular arithmetic for O(1) insertion. When buffer is full, |
//| oldest samples are overwritten (FIFO behavior).                  |
//+------------------------------------------------------------------+
void CRegimeML::AddSample(const double &features[], int label, double weight)
{
   int idx = m_sample_count % MAX_TRAINING_SAMPLES;

   ArrayCopy(m_samples[idx].features, features, 0, 0, FEATURE_COUNT);
   m_samples[idx].label = label;
   m_samples[idx].weight = weight;
   m_samples[idx].timestamp = TimeCurrent();

   m_sample_count++;
   m_buffer_size = MathMin(m_sample_count, MAX_TRAINING_SAMPLES);
}

//+------------------------------------------------------------------+
//| Label a feature vector using rule-based heuristic                |
//|                                                                  |
//| This provides "pseudo-labels" for self-supervised learning.      |
//| The rules match the EA's existing v9.0 regime detection logic    |
//| extended with additional features for finer classification.      |
//|                                                                  |
//| The ML model can learn non-linear boundaries between these       |
//| heuristic regions that simple thresholds cannot capture.          |
//+------------------------------------------------------------------+
int CRegimeML::LabelFromRules(const double &raw_features[])
{
   double er        = raw_features[FEAT_H4_ER];
   double vol_ratio = raw_features[FEAT_M15_VOL_RATIO];
   double adx       = raw_features[FEAT_H4_ADX];
   double bb_width  = raw_features[FEAT_BB_WIDTH];

   //--- Crash detection: extreme volatility
   if(vol_ratio >= FALLBACK_VOL_CRASH)
      return REGIME_CRASH;

   //--- HighVol: elevated volatility (not crash level)
   if(vol_ratio >= FALLBACK_VOL_HIGH)
      return REGIME_HIGHVOL;

   //--- HighVol via ADX + BB width: strong ADX with very wide BBs
   //    indicates volatile trending (not smooth trend)
   if(adx > 35.0 && bb_width > 0.03)
      return REGIME_HIGHVOL;

   //--- Range: low efficiency ratio with contained volatility
   if(er < FALLBACK_ER_TREND && vol_ratio <= FALLBACK_VOL_RANGE_CAP)
      return REGIME_RANGE;

   //--- Ambiguous: low ER but elevated vol -> high vol rather than trend
   if(er < FALLBACK_ER_TREND)
      return REGIME_HIGHVOL;

   //--- Trend: high ER with normal volatility
   return REGIME_TREND;
}

//+------------------------------------------------------------------+
//| Fallback detection using simple thresholds                       |
//| Identical logic to v9.0 DetectRegimeV9                           |
//+------------------------------------------------------------------+
int CRegimeML::FallbackDetect(const double &raw_features[])
{
   double er        = raw_features[FEAT_H4_ER];
   double vol_ratio = raw_features[FEAT_M15_VOL_RATIO];

   if(vol_ratio >= FALLBACK_VOL_CRASH)       return REGIME_CRASH;
   if(vol_ratio >= FALLBACK_VOL_HIGH)        return REGIME_HIGHVOL;
   if(er < FALLBACK_ER_TREND && vol_ratio <= FALLBACK_VOL_RANGE_CAP)
                                              return REGIME_RANGE;
   if(er < FALLBACK_ER_TREND)                return REGIME_HIGHVOL;
   return REGIME_TREND;
}

//+------------------------------------------------------------------+
//| Train the model from historical bar data                         |
//|                                                                  |
//| Called once during Init(). Walks back through H4 bars,           |
//| extracting features at each bar and labeling with heuristic      |
//| rules. This provides the initial training set for KNN.           |
//|                                                                  |
//| Parameters:                                                      |
//|   bars_back - Number of H4 bars to look back (max ~500)          |
//+------------------------------------------------------------------+
void CRegimeML::TrainFromHistory(int bars_back)
{
   //--- Ensure we have enough bars available
   int available = iBars(_Symbol, PERIOD_H4);
   if(available < bars_back + VOL_AVG_PERIOD + ER_CALC_PERIOD + 10)
   {
      bars_back = available - VOL_AVG_PERIOD - ER_CALC_PERIOD - 10;
      if(bars_back < 50)
      {
         Print("[RegimeML] WARNING: Insufficient H4 history for training. Have ",
               available, " bars, need at least ",
               VOL_AVG_PERIOD + ER_CALC_PERIOD + 60);
         return;
      }
   }

   Print("[RegimeML] Training from ", bars_back, " H4 bars of history...");

   int samples_added = 0;
   int label_counts[REGIME_COUNT];
   ArrayInitialize(label_counts, 0);

   //--- Walk through history from oldest to newest
   //    Start from bars_back and move toward bar 1
   for(int bar = bars_back; bar >= 1; bar--)
   {
      double raw_features[];
      ArrayResize(raw_features, FEATURE_COUNT);

      //--- Feature 0: H4 ADX at this bar
      double adx_val = GetIndicatorBuffer(m_h4_adx, 0, bar);
      if(adx_val <= 0) continue;
      raw_features[FEAT_H4_ADX] = adx_val;

      //--- Feature 1: H4 ER at this bar
      double close_arr[];
      ArraySetAsSeries(close_arr, true);
      if(CopyClose(_Symbol, PERIOD_H4, bar, ER_CALC_PERIOD + 1, close_arr) < ER_CALC_PERIOD + 1)
         continue;

      double net = MathAbs(close_arr[0] - close_arr[ER_CALC_PERIOD]);
      double sum_abs = 0.0;
      for(int k = 0; k < ER_CALC_PERIOD; k++)
         sum_abs += MathAbs(close_arr[k] - close_arr[k + 1]);

      if(sum_abs <= 0) continue;
      raw_features[FEAT_H4_ER] = net / sum_abs;

      //--- Feature 2: M15 volatility ratio at approximate time
      //    H4 bar 'bar' corresponds roughly to M15 bar 'bar * 16'
      //    (each H4 bar = 16 M15 bars)
      int m15_shift = bar * 16;
      double atr_arr[];
      ArraySetAsSeries(atr_arr, true);
      if(CopyBuffer(m_m15_atr, 0, m15_shift, VOL_AVG_PERIOD, atr_arr) < VOL_AVG_PERIOD)
         continue;

      double cur_atr = atr_arr[0];
      double atr_sum = 0.0;
      for(int k = 0; k < VOL_AVG_PERIOD; k++)
         atr_sum += atr_arr[k];
      double avg_atr = atr_sum / VOL_AVG_PERIOD;
      if(avg_atr <= 0) continue;
      raw_features[FEAT_M15_VOL_RATIO] = cur_atr / avg_atr;

      //--- Feature 3: H1 RSI at approximate time
      //    H4 bar 'bar' = H1 bar 'bar * 4'
      int h1_shift = bar * 4;
      double rsi_val = GetIndicatorBuffer(m_h1_rsi, 0, h1_shift);
      if(rsi_val <= 0) continue;
      raw_features[FEAT_H1_RSI] = rsi_val;

      //--- Feature 4: Hour of day from H4 bar time
      datetime bar_time[];
      ArraySetAsSeries(bar_time, true);
      if(CopyTime(_Symbol, PERIOD_H4, bar, 1, bar_time) < 1) continue;
      MqlDateTime dt;
      TimeToStruct(bar_time[0], dt);
      raw_features[FEAT_HOUR_NORM] = (double)dt.hour / 23.0;

      //--- Feature 5: H1 BB width at approximate time
      double bb_upper = GetIndicatorBuffer(m_h1_bb, 1, h1_shift);
      double bb_lower = GetIndicatorBuffer(m_h1_bb, 2, h1_shift);
      double bb_mid   = GetIndicatorBuffer(m_h1_bb, 0, h1_shift);
      if(bb_mid <= 0 || bb_upper <= 0 || bb_lower <= 0) continue;
      raw_features[FEAT_BB_WIDTH] = (bb_upper - bb_lower) / bb_mid;

      //--- Feature 6: Daily price position
      //    For historical training, use D1 bar for the corresponding day
      //    H4 bar 'bar' at 4 bars/day -> D1 shift ~= bar / 6
      int d1_shift = MathMax(bar / 6, 1);
      double d1_high[], d1_low[], d1_close[];
      ArraySetAsSeries(d1_high, true);
      ArraySetAsSeries(d1_low, true);
      ArraySetAsSeries(d1_close, true);
      if(CopyHigh(_Symbol, PERIOD_D1, d1_shift, 1, d1_high) < 1) continue;
      if(CopyLow(_Symbol, PERIOD_D1, d1_shift, 1, d1_low) < 1) continue;
      if(CopyClose(_Symbol, PERIOD_D1, d1_shift, 1, d1_close) < 1) continue;

      double d1_range = d1_high[0] - d1_low[0];
      if(d1_range <= 0)
         raw_features[FEAT_DAILY_POS] = 0.5;
      else
         raw_features[FEAT_DAILY_POS] = (d1_close[0] - d1_low[0]) / d1_range;

      //--- Update normalization stats
      UpdateFeatureStats(raw_features);

      //--- Normalize and label
      double norm_features[];
      ArrayResize(norm_features, FEATURE_COUNT);
      NormalizeFeatures(raw_features, norm_features);

      int label = LabelFromRules(raw_features);
      label_counts[label]++;

      AddSample(norm_features, label, 1.0);
      samples_added++;
   }

   Print("[RegimeML] Training complete: ", samples_added, " samples added",
         " | Distribution: Trend=", label_counts[REGIME_TREND],
         " Range=", label_counts[REGIME_RANGE],
         " HighVol=", label_counts[REGIME_HIGHVOL],
         " Crash=", label_counts[REGIME_CRASH]);

   m_last_train_time = TimeCurrent();
}

//+------------------------------------------------------------------+
//| Apply online learning updates to training sample weights         |
//|                                                                  |
//| When trade outcomes are recorded:                                |
//| - Samples matching profitable regimes get weight boost           |
//| - Samples matching losing regimes get weight decay               |
//| - Overall sample weights decay toward 1.0 over time              |
//+------------------------------------------------------------------+
void CRegimeML::ApplyOnlineLearning()
{
   if(m_outcome_count == 0) return;

   //--- Calculate regime-specific weight adjustments
   for(int regime = 0; regime < REGIME_COUNT; regime++)
   {
      int total = m_regime_win_count[regime] + m_regime_loss_count[regime];
      if(total < 3) continue; // Not enough data

      double win_rate = (double)m_regime_win_count[regime] / total;

      //--- Adjust sample weights for this regime
      //    Good regimes (win_rate > 0.5): boost samples slightly
      //    Bad regimes (win_rate < 0.4): reduce samples
      double weight_factor;
      if(win_rate >= 0.55)
         weight_factor = 1.0 + (win_rate - 0.5) * 0.5; // max ~1.25
      else if(win_rate < 0.4)
         weight_factor = 0.8 + win_rate * 0.5;          // min ~0.8
      else
         weight_factor = 1.0;

      //--- Apply to all matching samples in buffer
      for(int i = 0; i < m_buffer_size; i++)
      {
         if(m_samples[i].label == regime)
         {
            //--- Blend toward target weight (avoid sharp jumps)
            m_samples[i].weight = m_samples[i].weight * REGIME_WEIGHT_DECAY +
                                  weight_factor * (1.0 - REGIME_WEIGHT_DECAY);

            //--- Clamp
            m_samples[i].weight = MathMax(0.3, MathMin(2.0, m_samples[i].weight));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helper: Convert double array to comma-separated string           |
//+------------------------------------------------------------------+
string CRegimeML::DoubleArrayToStr(const double &arr[], int size)
{
   string result = "";
   for(int i = 0; i < size; i++)
   {
      result += DoubleToString(arr[i], 6);
      if(i < size - 1) result += ",";
   }
   return result;
}

//+------------------------------------------------------------------+
//| Helper: Parse comma-separated string to double array             |
//+------------------------------------------------------------------+
bool CRegimeML::StrToDoubleArray(string str, double &arr[], int expected_size)
{
   string parts[];
   int count = StringSplit(str, ',', parts);

   if(count != expected_size)
      return false;

   ArrayResize(arr, expected_size);
   for(int i = 0; i < expected_size; i++)
      arr[i] = StringToDouble(parts[i]);

   return true;
}

//+------------------------------------------------------------------+
#endif // __ANTIGRAVITY_REGIME_ML_MQH__
