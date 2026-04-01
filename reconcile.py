#!/usr/bin/env python3
"""Reconcile Python vs MT5 diagnostic indicator values.

Compares diagnostic_python.csv and diagnostic_mt5.csv to identify
systematic divergences in indicator calculations.

Usage:
    python reconcile.py [mt5_csv] [python_csv]
    python reconcile.py                          # uses defaults
    python reconcile.py --detail H4_ADX          # show detailed analysis for one indicator

Output:
    1. Summary table with pass/warn/fail for each indicator
    2. Alignment check (H1/H4 bar time matching)
    3. First divergence point for each failed indicator
    4. Actionable recommendations
"""

import argparse
import sys

import pandas as pd
import numpy as np


# Indicator columns to compare
INDICATOR_COLS = [
    "M15_EMA5", "M15_EMA20", "M15_ATR",
    "H1_EMA10", "H1_EMA30", "H1_RSI", "H1_BB_Upper", "H1_BB_Mid", "H1_BB_Lower", "H1_ATR",
    "H4_SMA20", "H4_SMA50", "H4_ADX", "H4_PDI", "H4_MDI", "H4_RSI", "H4_ATR",
    "USDJPY_EMA10", "USDJPY_EMA30",
]

# OHLC columns (should match exactly if same data source)
OHLC_COLS = ["M15_Open", "M15_High", "M15_Low", "M15_Close"]

# Thresholds for pass/warn/fail (relative P95 error %)
PASS_THRESHOLD = 0.1   # < 0.1% = PASS
WARN_THRESHOLD = 1.0   # < 1.0% = WARN, >= 1.0% = FAIL


def load_csv(filepath):
    """Load diagnostic CSV with flexible datetime parsing."""
    df = pd.read_csv(filepath)
    # Normalize DateTime column
    if "DateTime" not in df.columns:
        raise ValueError(f"No 'DateTime' column in {filepath}")
    # Handle both "YYYY-MM-DD" and "YYYY.MM.DD" formats
    df["DateTime"] = df["DateTime"].str.replace(".", "-", regex=False)
    df["DateTime"] = pd.to_datetime(df["DateTime"])
    return df


def check_ohlc_alignment(merged):
    """Check if OHLC data matches between MT5 and Python."""
    print("\n" + "=" * 78)
    print("PHASE 0: DATA ALIGNMENT CHECK")
    print("=" * 78)

    issues = []
    for col in OHLC_COLS:
        mt5_col = f"{col}_mt5"
        py_col = f"{col}_py"
        if mt5_col not in merged.columns or py_col not in merged.columns:
            continue
        diff = (merged[mt5_col] - merged[py_col]).abs()
        mismatch = (diff > 0.01).sum()
        if mismatch > 0:
            issues.append((col, mismatch, diff.max()))

    if not issues:
        print("  OHLC data matches perfectly between MT5 and Python.")
        print("  -> Same data source confirmed.")
    else:
        print("  WARNING: OHLC data mismatches detected!")
        print(f"  {'Column':<15} {'Mismatches':>12} {'MaxDiff':>12}")
        print("  " + "-" * 39)
        for col, n, maxd in issues:
            print(f"  {col:<15} {n:>12,} {maxd:>12.4f}")
        print()
        print("  -> MT5 and Python may be using DIFFERENT data sources.")
        print("     Fix this first: re-export data from MT5 using ExportHistory.mq5")

    return len(issues) == 0


def check_bar_alignment(merged):
    """Check if H1/H4 bar time alignment matches."""
    print("\n" + "=" * 78)
    print("PHASE 0.5: TIMEFRAME ALIGNMENT CHECK")
    print("=" * 78)

    for bar_col in ["H1_BarTime", "H4_BarTime"]:
        mt5_col = f"{bar_col}_mt5"
        py_col = f"{bar_col}_py"
        if mt5_col not in merged.columns or py_col not in merged.columns:
            print(f"  {bar_col}: column not found in one or both files (skipped)")
            continue

        # Normalize datetime strings
        mt5_times = pd.to_datetime(merged[mt5_col].astype(str).str.replace(".", "-", regex=False),
                                   errors="coerce")
        py_times = pd.to_datetime(merged[py_col].astype(str).str.replace(".", "-", regex=False),
                                  errors="coerce")

        valid = mt5_times.notna() & py_times.notna()
        mismatch = (mt5_times[valid] != py_times[valid]).sum()
        total = valid.sum()

        if mismatch == 0:
            print(f"  {bar_col}: {total:,} bars checked, ALL MATCH")
        else:
            pct = 100 * mismatch / total if total > 0 else 0
            print(f"  {bar_col}: {mismatch:,} / {total:,} mismatches ({pct:.1f}%)")
            # Show first few mismatches
            mismatch_idx = (mt5_times[valid] != py_times[valid])
            first_mismatches = merged[valid][mismatch_idx].head(3)
            for _, row in first_mismatches.iterrows():
                print(f"    M15={row['DateTime']}  MT5={row[mt5_col]}  PY={row[py_col]}")
            print(f"    -> Timezone or bar boundary issue detected")


def analyze_indicators(merged):
    """Compare each indicator between MT5 and Python."""
    print("\n" + "=" * 78)
    print("PHASE 1: INDICATOR ACCURACY")
    print("=" * 78)

    header = (f"{'Indicator':<18} {'MeanErr':>10} {'P95Err':>10} {'MaxErr':>10} "
              f"{'RelP95%':>9} {'Bias':>10} {'Status':>6}")
    print(header)
    print("-" * 75)

    results = {}
    for col in INDICATOR_COLS:
        mt5_col = f"{col}_mt5"
        py_col = f"{col}_py"

        if mt5_col not in merged.columns or py_col not in merged.columns:
            print(f"{col:<18} {'--- column missing ---':>57}")
            continue

        mt5_vals = pd.to_numeric(merged[mt5_col], errors="coerce")
        py_vals = pd.to_numeric(merged[py_col], errors="coerce")

        valid = mt5_vals.notna() & py_vals.notna()
        if valid.sum() < 10:
            print(f"{col:<18} {'--- insufficient data ---':>57}")
            continue

        diff = mt5_vals[valid] - py_vals[valid]
        abs_diff = diff.abs()
        mean_val = pd.concat([mt5_vals[valid], py_vals[valid]]).mean()

        mean_err = abs_diff.mean()
        p95_err = abs_diff.quantile(0.95)
        max_err = abs_diff.max()
        rel_p95 = (p95_err / abs(mean_val) * 100) if mean_val != 0 else 0
        bias = diff.mean()

        if rel_p95 < PASS_THRESHOLD:
            status = "PASS"
        elif rel_p95 < WARN_THRESHOLD:
            status = "WARN"
        else:
            status = "FAIL"

        print(f"{col:<18} {mean_err:>10.4f} {p95_err:>10.4f} {max_err:>10.4f} "
              f"{rel_p95:>8.3f}% {bias:>+10.4f} {status:>6}")

        results[col] = {
            "mean_err": mean_err,
            "p95_err": p95_err,
            "max_err": max_err,
            "rel_p95": rel_p95,
            "bias": bias,
            "status": status,
            "valid_count": valid.sum(),
            "diff_series": diff,
            "abs_diff_series": abs_diff,
        }

    return results


def analyze_temporal(merged, results):
    """Check if errors are growing, constant, or periodic."""
    print("\n" + "=" * 78)
    print("PHASE 2: TEMPORAL ANALYSIS")
    print("=" * 78)

    failed = {k: v for k, v in results.items() if v["status"] in ("FAIL", "WARN")}
    if not failed:
        print("  All indicators passed. No temporal analysis needed.")
        return

    for col, info in failed.items():
        diff = info["diff_series"]
        # Compute rolling error over time
        abs_diff = info["abs_diff_series"]
        # Split into 4 quarters
        n = len(abs_diff)
        q_size = n // 4
        if q_size < 10:
            continue

        quarters = []
        for q in range(4):
            start = q * q_size
            end = start + q_size
            q_mean = abs_diff.iloc[start:end].mean()
            quarters.append(q_mean)

        trend = "CONSTANT"
        if quarters[-1] > quarters[0] * 1.5:
            trend = "GROWING"
        elif quarters[-1] < quarters[0] * 0.5:
            trend = "SHRINKING"

        bias_dir = "positive" if info["bias"] > 0 else "negative"
        print(f"  {col}: {info['status']} | bias={bias_dir} | trend={trend}")
        print(f"    Q1={quarters[0]:.4f}  Q2={quarters[1]:.4f}  Q3={quarters[2]:.4f}  Q4={quarters[3]:.4f}")

        if trend == "GROWING":
            print(f"    -> Error is increasing over time. Possible EMA/RMA initialization divergence.")
        elif info["bias"] != 0:
            print(f"    -> Systematic {bias_dir} bias. Likely calculation method difference (SMA vs RMA, etc.)")


def show_first_divergence(merged, results):
    """Show the first few bars where each failed indicator significantly diverges."""
    print("\n" + "=" * 78)
    print("PHASE 3: FIRST DIVERGENCE POINTS")
    print("=" * 78)

    failed = {k: v for k, v in results.items() if v["status"] in ("FAIL", "WARN")}
    if not failed:
        print("  All indicators passed.")
        return

    for col, info in failed.items():
        mt5_col = f"{col}_mt5"
        py_col = f"{col}_py"
        abs_diff = info["abs_diff_series"]
        threshold = info["p95_err"]

        # Find first bar where error exceeds threshold
        divergent = abs_diff[abs_diff > threshold * 0.5]
        if len(divergent) == 0:
            continue

        print(f"\n  {col} (P95={info['p95_err']:.4f}, RelP95={info['rel_p95']:.3f}%):")
        first_5 = divergent.head(5)
        for idx in first_5.index:
            row = merged.loc[idx]
            dt = row["DateTime"]
            mt5_v = row[mt5_col]
            py_v = row[py_col]
            d = abs_diff.loc[idx]
            print(f"    {dt}  MT5={mt5_v:>12}  PY={py_v:>12}  diff={d:>10.4f}")


def show_detail(merged, results, col_name):
    """Show detailed analysis for a specific indicator."""
    print(f"\n{'=' * 78}")
    print(f"DETAILED ANALYSIS: {col_name}")
    print(f"{'=' * 78}")

    if col_name not in results:
        print(f"  Indicator '{col_name}' not found in results.")
        return

    info = results[col_name]
    mt5_col = f"{col_name}_mt5"
    py_col = f"{col_name}_py"

    mt5_vals = pd.to_numeric(merged[mt5_col], errors="coerce")
    py_vals = pd.to_numeric(merged[py_col], errors="coerce")
    valid = mt5_vals.notna() & py_vals.notna()
    diff = mt5_vals[valid] - py_vals[valid]

    print(f"  Valid samples: {valid.sum():,}")
    print(f"  MT5 range:    [{mt5_vals[valid].min():.4f}, {mt5_vals[valid].max():.4f}]")
    print(f"  Python range: [{py_vals[valid].min():.4f}, {py_vals[valid].max():.4f}]")
    print()
    print(f"  Error distribution:")
    for pct in [50, 75, 90, 95, 99, 100]:
        val = diff.abs().quantile(pct / 100) if pct < 100 else diff.abs().max()
        label = f"P{pct}" if pct < 100 else "Max"
        print(f"    {label:>4}: {val:.6f}")
    print()
    print(f"  Bias (mean signed error): {diff.mean():+.6f}")
    print(f"  Std of error:             {diff.std():.6f}")

    # Correlation between values
    corr = mt5_vals[valid].corr(py_vals[valid])
    print(f"  Correlation (MT5 vs PY):  {corr:.8f}")

    # Show 10 worst mismatches
    abs_diff = diff.abs()
    worst = abs_diff.nlargest(10)
    print(f"\n  Top 10 largest mismatches:")
    print(f"  {'DateTime':<22} {'MT5':>14} {'Python':>14} {'Diff':>12}")
    print("  " + "-" * 64)
    for idx in worst.index:
        row = merged.loc[idx]
        print(f"  {row['DateTime']!s:<22} {row[mt5_col]:>14} {row[py_col]:>14} {abs_diff.loc[idx]:>12.4f}")


def generate_recommendations(results, ohlc_ok):
    """Generate actionable recommendations based on analysis."""
    print("\n" + "=" * 78)
    print("RECOMMENDATIONS")
    print("=" * 78)

    if not ohlc_ok:
        print("  [CRITICAL] OHLC data mismatch detected.")
        print("    -> Re-export CSV data from MT5 using ExportHistory.mq5")
        print("    -> Ensure timezone settings match")
        print("    -> Re-run diagnostic after data sync")
        print()

    failed = [(k, v) for k, v in results.items() if v["status"] == "FAIL"]
    warned = [(k, v) for k, v in results.items() if v["status"] == "WARN"]

    if not failed and not warned:
        print("  All indicators PASS. Python and MT5 calculations are aligned.")
        print("  If trade results still differ, the divergence is in:")
        print("    1. Signal scoring logic (entry/exit decisions)")
        print("    2. Trade execution model (spread, slippage, fill price)")
        print("    3. Position management (SL/TP modification, partial close)")
        print()
        print("  Next step: Add signal score columns to diagnostic export.")
        return

    priority = 1
    for col, info in sorted(failed, key=lambda x: -x[1]["rel_p95"]):
        print(f"  [{priority}] FIX: {col} (RelP95 = {info['rel_p95']:.2f}%)")

        if "ATR" in col:
            if info["bias"] > 0:
                print("      MT5 ATR > Python ATR (systematic)")
                print("      -> Likely: Python uses SMA-based ATR, MT5 uses Wilder's RMA")
                print("      -> Fix: Use ewm(alpha=1/period) in Python (already done if using calc_atr)")
                print("      -> Check: calc_atr() in backtest_gold.py uses Wilder's smoothing?")
            else:
                print("      -> Check ATR calculation method matches between Python and MT5")

        elif "RSI" in col:
            print("      -> Check RSI smoothing method: Wilder's RMA vs simple EMA")
            print("      -> MT5 iRSI uses Wilder's smoothing (alpha=1/period)")
            print("      -> Verify calc_rsi() uses ewm(alpha=1.0/period, adjust=False)")

        elif "ADX" in col or "PDI" in col or "MDI" in col:
            print("      -> ADX calculation is complex with multiple smoothing stages")
            print("      -> Check: DM smoothing, TR smoothing, DX smoothing all use Wilder's RMA")
            print("      -> Small differences in each stage compound")

        elif "EMA" in col:
            print("      -> EMA initialization difference")
            print("      -> MT5 initializes EMA with SMA of first N bars")
            print("      -> Python ewm(adjust=False) uses first value as seed")
            if info["bias"] != 0:
                print(f"      -> Systematic {'positive' if info['bias'] > 0 else 'negative'} bias"
                      " suggests initialization difference")

        elif "SMA" in col:
            print("      -> SMA should match exactly if data is the same")
            print("      -> Check: bar alignment (H4 bar boundaries)")
            print("      -> Check: timezone offset between MT5 server and CSV data")

        elif "BB" in col:
            print("      -> Bollinger Bands depend on SMA + std calculation")
            print("      -> Check: population std (ddof=0) vs sample std (ddof=1)")
            print("      -> MT5 uses population std; Python rolling().std() uses ddof=1 by default")
            print("      -> Fix: Use rolling(20).std(ddof=0) in Python")

        print()
        priority += 1

    for col, info in sorted(warned, key=lambda x: -x[1]["rel_p95"]):
        print(f"  [{priority}] MONITOR: {col} (RelP95 = {info['rel_p95']:.2f}%)")
        print(f"      Small divergence, may not significantly affect trade results")
        print()
        priority += 1


def main():
    parser = argparse.ArgumentParser(description="Reconcile MT5 vs Python indicator values")
    parser.add_argument("mt5_csv", nargs="?", default="diagnostic_mt5.csv",
                        help="MT5 diagnostic CSV file")
    parser.add_argument("python_csv", nargs="?", default="diagnostic_python.csv",
                        help="Python diagnostic CSV file")
    parser.add_argument("--detail", type=str, default=None,
                        help="Show detailed analysis for a specific indicator")
    parser.add_argument("--export-diff", type=str, default=None,
                        help="Export differences to CSV file")
    args = parser.parse_args()

    # Load CSVs
    print("Loading diagnostic files...")
    try:
        mt5 = load_csv(args.mt5_csv)
        print(f"  MT5:    {len(mt5):>8,} bars  [{args.mt5_csv}]")
    except Exception as e:
        print(f"ERROR loading {args.mt5_csv}: {e}")
        sys.exit(1)

    try:
        py = load_csv(args.python_csv)
        print(f"  Python: {len(py):>8,} bars  [{args.python_csv}]")
    except Exception as e:
        print(f"ERROR loading {args.python_csv}: {e}")
        sys.exit(1)

    # Merge on DateTime (inner join)
    merged = pd.merge(mt5, py, on="DateTime", suffixes=("_mt5", "_py"))
    print(f"  Matched: {len(merged):>7,} bars")

    unmatched_mt5 = len(mt5) - len(merged)
    unmatched_py = len(py) - len(merged)
    if unmatched_mt5 > 0 or unmatched_py > 0:
        print(f"  Unmatched: MT5={unmatched_mt5:,}, Python={unmatched_py:,}")
        if unmatched_mt5 > len(mt5) * 0.5 or unmatched_py > len(py) * 0.5:
            print("  WARNING: >50% unmatched. Check date format and timezone.")

    if len(merged) == 0:
        print("\nERROR: No matching bars found. Possible causes:")
        print("  1. Different date ranges")
        print("  2. Different datetime formats")
        print("  3. Timezone mismatch")
        sys.exit(1)

    date_range = f"{merged['DateTime'].min()} ~ {merged['DateTime'].max()}"
    print(f"  Range:  {date_range}")

    # Phase 0: OHLC alignment
    ohlc_ok = check_ohlc_alignment(merged)

    # Phase 0.5: Bar time alignment
    check_bar_alignment(merged)

    # Phase 1: Indicator accuracy
    results = analyze_indicators(merged)

    # Phase 2: Temporal analysis
    analyze_temporal(merged, results)

    # Phase 3: First divergence
    show_first_divergence(merged, results)

    # Detail mode
    if args.detail:
        show_detail(merged, results, args.detail)

    # Recommendations
    generate_recommendations(results, ohlc_ok)

    # Export differences if requested
    if args.export_diff:
        diff_df = pd.DataFrame({"DateTime": merged["DateTime"]})
        for col in INDICATOR_COLS:
            mt5_col = f"{col}_mt5"
            py_col = f"{col}_py"
            if mt5_col in merged.columns and py_col in merged.columns:
                diff_df[f"{col}_mt5"] = merged[mt5_col]
                diff_df[f"{col}_py"] = merged[py_col]
                diff_df[f"{col}_diff"] = pd.to_numeric(merged[mt5_col], errors="coerce") - \
                                         pd.to_numeric(merged[py_col], errors="coerce")
        diff_df.to_csv(args.export_diff, index=False, float_format="%.6f")
        print(f"\nDifferences exported to {args.export_diff}")


if __name__ == "__main__":
    main()
