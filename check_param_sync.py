#!/usr/bin/env python3
"""
Parameter Sync Checker — Single Source of Truth Verification

Reads config/v17_params.json, backtest_gold.py GoldConfig defaults,
and AntigravityMTF_EA_Gold_v17.mq5 input/const parameters.
Reports any mismatches between all three sources.

Exit code 0 if all match, 1 if any mismatch found.
"""

import json
import re
import sys
from pathlib import Path


def load_json_params(filepath: str) -> dict:
    """Load the canonical JSON parameter file."""
    with open(filepath) as f:
        return json.load(f)


def parse_python_defaults(filepath: str) -> dict:
    """Parse GoldConfig class attributes from backtest_gold.py."""
    params = {}
    with open(filepath) as f:
        in_class = False
        for line in f:
            stripped = line.strip()
            # Detect class start
            if stripped.startswith("class GoldConfig"):
                in_class = True
                continue
            # Detect class end (next non-indented, non-empty, non-comment line)
            if in_class and stripped and not line[0].isspace() and not stripped.startswith("#"):
                break
            if not in_class:
                continue

            # Skip comments, empty lines, decorators, methods
            if not stripped or stripped.startswith("#") or stripped.startswith("@") or stripped.startswith("def "):
                continue

            # Parse simple attribute assignments: NAME = value
            m = re.match(r'^(\w+)\s*=\s*(.+?)(?:\s*#.*)?$', stripped)
            if m:
                name = m.group(1)
                val_str = m.group(2).strip()
                # Try to parse the value
                val = _parse_python_value(val_str, name)
                if val is not None:
                    params[name] = val
    return params


def _parse_python_value(val_str: str, name: str):
    """Parse a Python literal value string."""
    # Remove trailing comma if present
    val_str = val_str.rstrip(",").strip()

    # Boolean
    if val_str == "True":
        return True
    if val_str == "False":
        return False

    # Integer with underscores (e.g. 300_000)
    try:
        if re.match(r'^-?\d[\d_]*$', val_str):
            return int(val_str.replace("_", ""))
    except ValueError:
        pass

    # Float
    try:
        if re.match(r'^-?\d+\.\d+$', val_str):
            return float(val_str)
    except ValueError:
        pass

    # Integer
    try:
        return int(val_str)
    except ValueError:
        pass

    # Float (no decimal point but looks numeric)
    try:
        return float(val_str)
    except ValueError:
        pass

    # Dict literal (for SRAT_THRESHOLDS, DD_ESCALATION, etc.)
    if val_str.startswith("{") or val_str.startswith("[") or val_str.startswith("("):
        try:
            return eval(val_str)
        except Exception:
            pass

    # String
    if val_str.startswith('"') or val_str.startswith("'"):
        return val_str.strip("\"'")

    return None


def parse_mq5_params(filepath: str) -> dict:
    """Parse input and const parameters from MQ5 file."""
    params = {}
    srat_values = {}
    dd_levels = []
    dd_scores = []

    with open(filepath) as f:
        content = f.read()

    # Parse input parameters
    for m in re.finditer(
        r'^input\s+\w+\s+(\w+)\s*=\s*([^;]+);', content, re.MULTILINE
    ):
        name = m.group(1)
        val_str = m.group(2).strip()
        params[name] = _parse_mq5_value(val_str)

    # Parse const parameters
    for m in re.finditer(
        r'^const\s+\w+\s+(\w+)\s*=\s*([^;]+);', content, re.MULTILINE
    ):
        name = m.group(1)
        val_str = m.group(2).strip()
        params[name] = _parse_mq5_value(val_str)

    # Parse SRAT from switch/case block
    for m in re.finditer(
        r'case\s+(\d+):\s+currentMinScore\s*=\s*(\d+);', content
    ):
        hour = int(m.group(1))
        score = int(m.group(2))
        srat_values[hour] = score
    if srat_values:
        params["_SRAT"] = srat_values

    # Parse DD escalation from if/else chain
    dd_pattern = re.findall(
        r'currentDD\s*>=\s*([\d.]+)\)\s*currentMinScore\s*=.*?(\d+)\)',
        content
    )
    if dd_pattern:
        # Reverse because MQ5 checks highest first
        dd_pattern.reverse()
        dd_levels = [float(x[0]) for x in dd_pattern]
        dd_scores = [int(x[1]) for x in dd_pattern]
        params["_DD_LEVELS"] = dd_levels
        params["_DD_SCORES"] = dd_scores

    return params


def _parse_mq5_value(val_str: str):
    """Parse an MQ5 literal value."""
    val_str = val_str.strip()
    if val_str == "true":
        return True
    if val_str == "false":
        return False
    # String
    if val_str.startswith('"'):
        return val_str.strip('"')
    try:
        if "." in val_str:
            return float(val_str)
        return int(val_str)
    except ValueError:
        return val_str


def check_sync(json_path: str, python_path: str, mq5_path: str) -> list:
    """Compare parameters across all three sources. Returns list of mismatch descriptions."""
    mismatches = []

    json_params = load_json_params(json_path)
    py_params = parse_python_defaults(python_path)
    mq5_params = parse_mq5_params(mq5_path)

    # Define mapping: (JSON path, Python attr, MQ5 param name, description)
    checks = [
        # Risk
        (("risk", "risk_percent"),     "RISK_PERCENT",       "RiskPercent",       "Risk %"),
        (("risk", "max_lots"),         "MAX_LOT",            "MaxLots",           "Max lots"),
        (("risk", "min_lots"),         "MIN_LOT",            "MinLots",           "Min lots"),
        (("risk", "max_spread"),       "MAX_SPREAD_POINTS",  "MaxSpread",         "Max spread"),
        (("risk", "max_drawdown_pct"), "MAX_DD_PERCENT",     "MaxDrawdownPct",    "Max DD %"),
        (("risk", "dd_half_risk_pct"), "DD_HALF_RISK",       "DDHalfRiskPct",     "DD half risk %"),
        (("risk", "daily_max_loss_pct"), "DAILY_MAX_LOSS_PCT", "DailyMaxLossPct", "Daily max loss %"),

        # SL/TP
        (("sl_tp", "sl_atr_multi"),    "SL_ATR_MULTI",       "SL_ATR_Multi",      "SL ATR multi"),
        (("sl_tp", "tp_atr_multi"),    "TP_ATR_MULTI",       "TP_ATR_Multi",      "TP ATR multi"),
        (("sl_tp", "trail_atr_multi"), "TRAIL_ATR_MULTI",    "Trail_ATR_Multi",   "Trail ATR multi"),
        (("sl_tp", "be_atr_multi"),    "BE_ATR_MULTI",       "BE_ATR_Multi",      "BE ATR multi"),
        (("sl_tp", "min_sl_points"),   "MIN_SL_POINTS",      "MinSL_Points",      "Min SL points"),
        (("sl_tp", "max_sl_points"),   "MAX_SL_POINTS",      "MaxSL_Points",      "Max SL points"),

        # Chandelier
        (("chandelier", "period"),     "CHANDELIER_PERIOD",     "Chandelier_Period",    "Chandelier period"),
        (("chandelier", "atr_multi"),  "CHANDELIER_ATR_MULTI",  "Chandelier_ATR_Multi", "Chandelier ATR multi"),

        # Entry
        (("entry", "min_score"),       "MIN_SCORE",          "MinEntryScore",     "Min entry score"),
        (("entry", "cooldown_minutes"), None,                 "CooldownMinutes",   "Cooldown minutes"),
        (("entry", "score_margin_min"), None,                 "ScoreMarginMin",    "Score margin min"),

        # Time
        (("time", "trade_start_hour"), "TRADE_START_HOUR",   "TradeStartHour",    "Trade start hour"),
        (("time", "trade_end_hour"),   "TRADE_END_HOUR",     "TradeEndHour",      "Trade end hour"),
        (("time", "gmt_offset"),       None,                  "GMTOffset",         "GMT offset"),
        (("time", "avoid_friday"),     None,                  "AvoidFriday",       "Avoid Friday"),
        (("time", "friday_close_hour"), "FRIDAY_CLOSE_HOUR", "FridayCloseHour",   "Friday close hour"),

        # Features
        (("features", "use_rsi_momentum_confirm"), "USE_RSI_MOMENTUM_CONFIRM", "UseRSIMomentumConfirm", "RSI momentum confirm"),
        (("features", "use_partial_close"),  "USE_PARTIAL_CLOSE",  "UsePartialClose",    "Partial close"),
        (("features", "partial_close_ratio"), "PARTIAL_CLOSE_RATIO", "PartialCloseRatio", "Partial close ratio"),
        (("features", "partial_tp_ratio"),   "PARTIAL_TP_RATIO",   "PartialTP_Ratio",    "Partial TP ratio"),
        (("features", "use_reversal_mode"),  "USE_REVERSAL_MODE",  "UseReversalMode",    "Reversal mode"),
        (("features", "use_chandelier_exit"), "USE_CHANDELIER_EXIT", "UseChandelierExit", "Chandelier exit"),
        (("features", "use_equity_curve_filter"), "USE_EQUITY_CURVE", "UseEquityCurveFilter", "Equity curve filter"),
        (("features", "use_news_filter"),    "USE_NEWS_FILTER",    "UseNewsFilter",      "News filter"),
        (("features", "use_weekend_close"),  "USE_WEEKEND_CLOSE",  "UseWeekendClose",    "Weekend close"),
        (("features", "use_correlation"),    "USE_CORRELATION",    "UseCorrelation",     "Correlation filter"),

        # Kelly
        (("kelly", "lookback_trades"), "KELLY_LOOKBACK",     "Kelly_LookbackTrades", "Kelly lookback"),
        (("kelly", "fraction"),        "KELLY_FRACTION",     "Kelly_Fraction",       "Kelly fraction"),
        (("kelly", "min_risk"),        "KELLY_MIN_RISK",     "Kelly_MinRisk",        "Kelly min risk"),
        (("kelly", "max_risk"),        "KELLY_MAX_RISK",     "Kelly_MaxRisk",        "Kelly max risk"),
    ]

    # Helper to get nested JSON value
    def get_json_val(keys):
        val = json_params
        for k in keys:
            val = val[k]
        return val

    # Compare each parameter
    for json_keys, py_attr, mq5_name, desc in checks:
        json_val = get_json_val(json_keys)

        # Python comparison
        if py_attr is not None:
            py_val = py_params.get(py_attr)
            if py_val is not None:
                if not _values_match(json_val, py_val):
                    mismatches.append(
                        f"  [{desc}] JSON={json_val} vs Python({py_attr})={py_val}"
                    )
            else:
                # Python attr not found — may be set dynamically, skip
                pass

        # MQ5 comparison
        mq5_val = mq5_params.get(mq5_name)
        if mq5_val is not None:
            if not _values_match(json_val, mq5_val):
                mismatches.append(
                    f"  [{desc}] JSON={json_val} vs MQ5({mq5_name})={mq5_val}"
                )

    # Special: Cooldown (JSON=minutes, Python=M15 bars)
    json_cooldown_min = get_json_val(("entry", "cooldown_minutes"))
    py_cooldown_bars = py_params.get("COOLDOWN_BARS")
    if py_cooldown_bars is not None:
        expected_bars = json_cooldown_min // 15
        if py_cooldown_bars != expected_bars:
            mismatches.append(
                f"  [Cooldown] JSON={json_cooldown_min}min (={expected_bars} bars) "
                f"vs Python(COOLDOWN_BARS)={py_cooldown_bars}"
            )

    # Special: SRAT thresholds
    json_srat = {int(k): v for k, v in json_params["srat"].items()}
    py_srat = py_params.get("SRAT_THRESHOLDS")
    mq5_srat = mq5_params.get("_SRAT", {})

    if py_srat is not None and json_srat != py_srat:
        mismatches.append(f"  [SRAT] JSON vs Python mismatch:")
        for h in sorted(set(list(json_srat.keys()) + list(py_srat.keys()))):
            jv = json_srat.get(h, "MISSING")
            pv = py_srat.get(h, "MISSING")
            if jv != pv:
                mismatches.append(f"    Hour {h}: JSON={jv}, Python={pv}")

    if mq5_srat and json_srat != mq5_srat:
        mismatches.append(f"  [SRAT] JSON vs MQ5 mismatch:")
        for h in sorted(set(list(json_srat.keys()) + list(mq5_srat.keys()))):
            jv = json_srat.get(h, "MISSING")
            mv = mq5_srat.get(h, "MISSING")
            if jv != mv:
                mismatches.append(f"    Hour {h}: JSON={jv}, MQ5={mv}")

    # Special: DD Escalation
    json_dd_levels = json_params["dd_escalation"]["levels"]
    json_dd_scores = json_params["dd_escalation"]["score_add"]

    py_dd = py_params.get("DD_ESCALATION")
    if py_dd is not None:
        py_dd_levels = [x[0] for x in py_dd]
        py_dd_scores = [x[1] for x in py_dd]
        if py_dd_levels != json_dd_levels or py_dd_scores != json_dd_scores:
            mismatches.append(
                f"  [DD Escalation] JSON levels={json_dd_levels},scores={json_dd_scores} "
                f"vs Python={py_dd}"
            )

    mq5_dd_levels = mq5_params.get("_DD_LEVELS", [])
    mq5_dd_scores = mq5_params.get("_DD_SCORES", [])
    if mq5_dd_levels:
        if mq5_dd_levels != json_dd_levels or mq5_dd_scores != json_dd_scores:
            mismatches.append(
                f"  [DD Escalation] JSON levels={json_dd_levels},scores={json_dd_scores} "
                f"vs MQ5 levels={mq5_dd_levels},scores={mq5_dd_scores}"
            )

    # Special: Regime ML flag
    json_ml = json_params["regime"]["use_ml_regime"]
    mq5_ml = mq5_params.get("UseRegimeML")
    if mq5_ml is not None and not _values_match(json_ml, mq5_ml):
        mismatches.append(
            f"  [ML Regime] JSON={json_ml} vs MQ5(UseRegimeML)={mq5_ml}"
        )

    return mismatches


def _values_match(a, b) -> bool:
    """Compare values with type coercion (int vs float tolerance)."""
    if a == b:
        return True
    # Compare numerically
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        pass
    # Compare bool/int (Python True == 1)
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return False


def main():
    base = Path(__file__).parent
    json_path = base / "config" / "v17_params.json"
    python_path = base / "backtest_gold.py"
    mq5_path = base / "AntigravityMTF_EA_Gold_v17.mq5"

    # Verify files exist
    for p in [json_path, python_path, mq5_path]:
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(2)

    print("=" * 60)
    print("Parameter Sync Check: JSON vs Python vs MQ5")
    print("=" * 60)

    json_params = load_json_params(str(json_path))
    print(f"JSON version: {json_params['version']}")
    print(f"JSON description: {json_params['description']}")
    print()

    mismatches = check_sync(str(json_path), str(python_path), str(mq5_path))

    if mismatches:
        print(f"MISMATCH FOUND ({len(mismatches)} issues):")
        print()
        for m in mismatches:
            print(m)
        print()
        print("NOTE: Some mismatches may be intentional (e.g. ablation-proven")
        print("changes in JSON that need to be propagated to Python/MQ5).")
        print()
        print("RESULT: FAIL")
        sys.exit(1)
    else:
        print("All checked parameters are in sync.")
        print()
        print("RESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
