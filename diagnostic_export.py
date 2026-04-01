#!/usr/bin/env python3
"""Export diagnostic indicator values from Python backtest for MT5 reconciliation.

Outputs diagnostic_python.csv with the same columns as DiagnosticExport.mq5.
Run reconcile.py to compare the two CSVs and identify divergences.

Usage:
    python diagnostic_export.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""

import argparse
import sys
import os

import pandas as pd
import numpy as np

# Import from existing codebase (same indicator calculations as backtest)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_csv import load_csv
from backtest_gold import calc_sma, calc_ema, calc_rsi, calc_atr, calc_adx, calc_bb


def main():
    parser = argparse.ArgumentParser(description="Export Python indicator values for MT5 reconciliation")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, default="diagnostic_python.csv")
    parser.add_argument("--data-dir", type=str, default=".", help="Directory containing CSV data files")
    args = parser.parse_args()

    data_dir = args.data_dir

    # --- Load data (same as backtest_gold.py) ---
    print("Loading data...")
    m15 = load_csv(os.path.join(data_dir, "XAUUSD_M15.csv"))
    h1 = load_csv(os.path.join(data_dir, "XAUUSD_H1.csv"))
    h4 = load_csv(os.path.join(data_dir, "XAUUSD_H4.csv"))

    usdjpy_path = os.path.join(data_dir, "USDJPY_H1.csv")
    uj = load_csv(usdjpy_path) if os.path.exists(usdjpy_path) else None

    print(f"  M15: {len(m15)} bars [{m15.index[0]} ~ {m15.index[-1]}]")
    print(f"  H1:  {len(h1)} bars")
    print(f"  H4:  {len(h4)} bars")
    if uj is not None:
        print(f"  UJ:  {len(uj)} bars")

    # --- Filter date range ---
    if args.start:
        m15 = m15[m15.index >= args.start]
        h1 = h1[h1.index >= args.start]
        h4 = h4[h4.index >= args.start]
        if uj is not None:
            uj = uj[uj.index >= args.start]
    if args.end:
        m15 = m15[m15.index <= args.end]

    # --- Calculate indicators (exactly matching backtest_gold.py) ---
    print("Calculating indicators...")

    # M15 indicators
    m15 = m15.copy()
    m15["M15_EMA5"] = calc_ema(m15["Close"], 5)
    m15["M15_EMA20"] = calc_ema(m15["Close"], 20)
    m15["M15_ATR"] = calc_atr(m15["High"], m15["Low"], m15["Close"], 14)

    # H1 indicators
    h1_ind = pd.DataFrame(index=h1.index)
    h1_ind["H1_EMA10"] = calc_ema(h1["Close"], 10)
    h1_ind["H1_EMA30"] = calc_ema(h1["Close"], 30)
    h1_ind["H1_RSI"] = calc_rsi(h1["Close"], 14)
    bb_u, bb_m, bb_l = calc_bb(h1["Close"], 20, 2.0)
    h1_ind["H1_BB_Upper"] = bb_u
    h1_ind["H1_BB_Mid"] = bb_m
    h1_ind["H1_BB_Lower"] = bb_l
    h1_ind["H1_ATR"] = calc_atr(h1["High"], h1["Low"], h1["Close"], 14)
    h1_ind["H1_BarTime"] = h1.index

    # H4 indicators (note: H4 uses SMA, not EMA)
    h4_ind = pd.DataFrame(index=h4.index)
    h4_ind["H4_SMA20"] = calc_sma(h4["Close"], 20)
    h4_ind["H4_SMA50"] = calc_sma(h4["Close"], 50)
    adx, pdi, mdi = calc_adx(h4["High"], h4["Low"], h4["Close"], 14)
    h4_ind["H4_ADX"] = adx
    h4_ind["H4_PDI"] = pdi
    h4_ind["H4_MDI"] = mdi
    h4_ind["H4_RSI"] = calc_rsi(h4["Close"], 14)
    h4_ind["H4_ATR"] = calc_atr(h4["High"], h4["Low"], h4["Close"], 14)
    h4_ind["H4_BarTime"] = h4.index

    # USDJPY H1 indicators
    uj_ind = None
    if uj is not None:
        uj_ind = pd.DataFrame(index=uj.index)
        uj_ind["USDJPY_EMA10"] = calc_ema(uj["Close"], 10)
        uj_ind["USDJPY_EMA30"] = calc_ema(uj["Close"], 30)

    # --- Build result: align all TFs to M15 timestamps ---
    print("Aligning timeframes...")

    # Start with M15 OHLC
    result = m15[["Open", "High", "Low", "Close"]].copy()
    result.columns = ["M15_Open", "M15_High", "M15_Low", "M15_Close"]
    result["M15_Spread"] = m15["Spread"].astype(int) if "Spread" in m15.columns else 0
    result["M15_EMA5"] = m15["M15_EMA5"]
    result["M15_EMA20"] = m15["M15_EMA20"]
    result["M15_ATR"] = m15["M15_ATR"]

    # Merge H1 indicators (backward: most recent H1 bar with time <= M15 time)
    # This matches: h1_mask = h1_df.index <= ct; h1_curr = h1_df[h1_mask].iloc[-1]
    result = pd.merge_asof(
        result, h1_ind,
        left_index=True, right_index=True,
        direction="backward"
    )

    # Merge H4 indicators (backward)
    result = pd.merge_asof(
        result, h4_ind,
        left_index=True, right_index=True,
        direction="backward"
    )

    # Merge USDJPY indicators (backward)
    if uj_ind is not None:
        result = pd.merge_asof(
            result, uj_ind,
            left_index=True, right_index=True,
            direction="backward"
        )
    else:
        result["USDJPY_EMA10"] = np.nan
        result["USDJPY_EMA30"] = np.nan

    # Drop warmup period (first 100 M15 bars, matching backtest_gold.py)
    result = result.iloc[100:]

    # Drop rows with NaN in critical columns (indicator warmup)
    result = result.dropna(subset=["M15_EMA20", "H1_EMA30", "H4_SMA50"])

    # --- Format output ---
    # Ensure column order matches MQL5 output
    cols = [
        "M15_Open", "M15_High", "M15_Low", "M15_Close", "M15_Spread",
        "M15_EMA5", "M15_EMA20", "M15_ATR",
        "H1_EMA10", "H1_EMA30", "H1_RSI", "H1_BB_Upper", "H1_BB_Mid", "H1_BB_Lower", "H1_ATR",
        "H4_SMA20", "H4_SMA50", "H4_ADX", "H4_PDI", "H4_MDI", "H4_RSI", "H4_ATR",
        "USDJPY_EMA10", "USDJPY_EMA30",
        "H1_BarTime", "H4_BarTime",
    ]
    result = result[cols]

    # Format DateTime index
    result.index.name = "DateTime"
    result.index = result.index.strftime("%Y-%m-%d %H:%M:%S")

    # Format BarTime columns
    result["H1_BarTime"] = pd.to_datetime(result["H1_BarTime"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    result["H4_BarTime"] = pd.to_datetime(result["H4_BarTime"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    # Write CSV
    output_path = os.path.join(data_dir, args.output)
    result.to_csv(output_path, float_format="%.6f")
    print(f"\nExported {len(result)} bars to {output_path}")
    print(f"Date range: {result.index[0]} ~ {result.index[-1]}")


if __name__ == "__main__":
    main()
