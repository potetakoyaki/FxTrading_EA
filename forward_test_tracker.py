"""
Forward Test Tracker for AntigravityMTF EA Gold
Reads MT5 trade history CSV, compares against backtest expectations,
generates weekly reports, and alerts on performance deviation.

Usage:
    python forward_test_tracker.py --csv trades.csv [--backtest-ref backtest_expectations.json]
    python forward_test_tracker.py --csv trades.csv --report weekly
    python forward_test_tracker.py --csv trades.csv --report monthly
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# Backtest Reference Values (v7.0 WFA-validated)
# ============================================================
BACKTEST_EXPECTATIONS = {
    # Full-period metrics (2022-01 to 2026-03)
    "profit_factor": 1.70,
    "win_rate": 0.67,
    "max_drawdown_pct": 7.3,
    "sharpe_ratio": 3.68,

    # Unknown-data metrics (2025-26) -- more conservative reference
    "unknown_data_pf": 1.51,

    # Spread-adjusted (realistic)
    "spread_adjusted_pf": 1.63,

    # Trade frequency (quarterly, from backtest)
    "trades_per_quarter": 55,  # ~50-60 expected
    "trades_per_quarter_min": 40,
    "trades_per_quarter_max": 75,

    # Deviation threshold: alert if live metric differs by this %
    "deviation_threshold_pct": 20.0,
}

# Use the spread-adjusted PF as the primary benchmark for live trading
REFERENCE_PF = BACKTEST_EXPECTATIONS["spread_adjusted_pf"]
REFERENCE_WR = BACKTEST_EXPECTATIONS["win_rate"]
REFERENCE_DD = BACKTEST_EXPECTATIONS["max_drawdown_pct"]
DEVIATION_PCT = BACKTEST_EXPECTATIONS["deviation_threshold_pct"]


# ============================================================
# Data structures
# ============================================================
@dataclass
class Trade:
    ticket: int
    open_time: datetime
    close_time: datetime
    direction: str       # "buy" or "sell"
    symbol: str
    lots: float
    open_price: float
    close_price: float
    profit: float        # net profit in account currency
    commission: float
    swap: float
    magic: int
    comment: str = ""

    @property
    def net_profit(self) -> float:
        return self.profit + self.commission + self.swap

    @property
    def is_winner(self) -> bool:
        return self.net_profit > 0

    @property
    def holding_hours(self) -> float:
        delta = self.close_time - self.open_time
        return delta.total_seconds() / 3600


@dataclass
class PerformanceMetrics:
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    daily_pnl: dict = field(default_factory=dict)
    cumulative_pnl: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winners / self.total_trades

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float('inf') if self.gross_profit > 0 else 0.0
        return self.gross_profit / abs(self.gross_loss)

    @property
    def avg_win(self) -> float:
        return self.gross_profit / self.winners if self.winners > 0 else 0.0

    @property
    def avg_loss(self) -> float:
        return self.gross_loss / self.losers if self.losers > 0 else 0.0

    @property
    def expectancy(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.net_profit / self.total_trades


# ============================================================
# CSV Parsing
# ============================================================
def parse_datetime(dt_str: str) -> datetime:
    """Parse MT5 datetime formats."""
    formats = [
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: '{dt_str}'")


def load_trades_from_csv(filepath: str, magic_filter: Optional[int] = 20260224) -> list[Trade]:
    """
    Load trades from MT5 CSV export.

    Supports two formats:
    1. MT5 Strategy Tester report CSV
    2. ExportHistory.mq5 custom export (adapted for trade history)

    Expected columns (flexible matching):
        Ticket, OpenTime, CloseTime, Type, Symbol, Lots,
        OpenPrice, ClosePrice, Profit, Commission, Swap, Magic, Comment
    """
    trades = []

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8-sig") as f:
        # Try to detect delimiter
        sample = f.read(2048)
        f.seek(0)

        if "\t" in sample and "," not in sample.split("\n")[0]:
            delimiter = "\t"
        else:
            delimiter = ","

        reader = csv.DictReader(f, delimiter=delimiter)

        # Normalize column names (strip whitespace, lowercase)
        if reader.fieldnames is None:
            print("ERROR: CSV has no header row")
            sys.exit(1)

        col_map = {}
        for col in reader.fieldnames:
            normalized = col.strip().lower().replace(" ", "")
            col_map[normalized] = col

        # Map expected fields to actual column names
        def find_col(candidates: list[str]) -> Optional[str]:
            for c in candidates:
                if c in col_map:
                    return col_map[c]
            return None

        col_ticket = find_col(["ticket", "order", "deal", "position"])
        col_open_time = find_col(["opentime", "timein", "entry", "open"])
        col_close_time = find_col(["closetime", "timeout", "exit", "close"])
        col_type = find_col(["type", "direction", "side"])
        col_symbol = find_col(["symbol", "instrument"])
        col_lots = find_col(["lots", "volume", "size"])
        col_open_price = find_col(["openprice", "pricein", "entryprice"])
        col_close_price = find_col(["closeprice", "priceout", "exitprice"])
        col_profit = find_col(["profit", "pnl", "p&l", "result"])
        col_commission = find_col(["commission", "comm"])
        col_swap = find_col(["swap", "financing"])
        col_magic = find_col(["magic", "magicnumber", "expert"])
        col_comment = find_col(["comment", "note", "remarks"])

        if col_profit is None:
            print("ERROR: Cannot find 'Profit' column in CSV")
            print(f"  Available columns: {reader.fieldnames}")
            sys.exit(1)

        row_num = 0
        for row in reader:
            row_num += 1
            try:
                # Skip non-trade rows (balance operations, etc.)
                trade_type = row.get(col_type, "").strip().lower() if col_type else ""
                if trade_type in ("balance", "credit", "deposit", "withdrawal", ""):
                    if col_type and trade_type in ("balance", "credit", "deposit", "withdrawal"):
                        continue

                # Determine direction
                direction = "buy"
                if trade_type in ("sell", "short", "1"):
                    direction = "sell"
                elif trade_type in ("buy", "long", "0"):
                    direction = "buy"

                # Parse magic number for filtering
                magic = 0
                if col_magic and row.get(col_magic, "").strip():
                    try:
                        magic = int(row[col_magic].strip())
                    except ValueError:
                        magic = 0

                if magic_filter is not None and magic != magic_filter:
                    continue

                # Parse core fields
                profit = float(row[col_profit].strip()) if col_profit else 0.0
                commission = float(row[col_commission].strip()) if col_commission and row.get(col_commission, "").strip() else 0.0
                swap = float(row[col_swap].strip()) if col_swap and row.get(col_swap, "").strip() else 0.0

                open_time = parse_datetime(row[col_open_time]) if col_open_time else datetime.min
                close_time = parse_datetime(row[col_close_time]) if col_close_time else datetime.min

                trade = Trade(
                    ticket=int(row[col_ticket].strip()) if col_ticket and row.get(col_ticket, "").strip() else row_num,
                    open_time=open_time,
                    close_time=close_time,
                    direction=direction,
                    symbol=row[col_symbol].strip() if col_symbol else "XAUUSD",
                    lots=float(row[col_lots].strip()) if col_lots and row.get(col_lots, "").strip() else 0.01,
                    open_price=float(row[col_open_price].strip()) if col_open_price and row.get(col_open_price, "").strip() else 0.0,
                    close_price=float(row[col_close_price].strip()) if col_close_price and row.get(col_close_price, "").strip() else 0.0,
                    profit=profit,
                    commission=commission,
                    swap=swap,
                    magic=magic,
                    comment=row[col_comment].strip() if col_comment and row.get(col_comment, "") else "",
                )
                trades.append(trade)

            except (ValueError, KeyError) as e:
                print(f"  WARNING: Skipping row {row_num}: {e}")
                continue

    # Sort by close time
    trades.sort(key=lambda t: t.close_time)
    print(f"Loaded {len(trades)} trades from {filepath}")
    return trades


# ============================================================
# Performance Calculation
# ============================================================
def calculate_metrics(trades: list[Trade], initial_balance: float = 300_000) -> PerformanceMetrics:
    """Calculate comprehensive performance metrics from trade list."""
    m = PerformanceMetrics()
    m.peak_equity = initial_balance

    equity = initial_balance
    running_dd = 0.0
    consec_losses = 0

    for t in trades:
        m.total_trades += 1
        pnl = t.net_profit

        if pnl > 0:
            m.winners += 1
            m.gross_profit += pnl
            consec_losses = 0
        elif pnl < 0:
            m.losers += 1
            m.gross_loss += pnl  # negative
            consec_losses += 1
            m.max_consecutive_losses = max(m.max_consecutive_losses, consec_losses)
        else:
            # breakeven
            consec_losses = 0

        m.net_profit += pnl
        equity += pnl

        # Track peak and drawdown
        if equity > m.peak_equity:
            m.peak_equity = equity
        dd = m.peak_equity - equity
        if dd > m.max_drawdown:
            m.max_drawdown = dd
            m.max_drawdown_pct = (dd / m.peak_equity) * 100 if m.peak_equity > 0 else 0

        # Daily PnL
        day_key = t.close_time.strftime("%Y-%m-%d")
        m.daily_pnl[day_key] = m.daily_pnl.get(day_key, 0.0) + pnl

        # Cumulative PnL curve
        m.cumulative_pnl.append((t.close_time, equity))

    return m


# ============================================================
# Deviation Alerts
# ============================================================
def check_deviations(metrics: PerformanceMetrics, threshold_pct: float = DEVIATION_PCT) -> list[str]:
    """Check if live performance deviates >threshold% from backtest expectations."""
    alerts = []
    threshold = threshold_pct / 100.0

    if metrics.total_trades < 10:
        alerts.append(
            f"INFO: Only {metrics.total_trades} trades so far. "
            "Need 20+ trades for statistically meaningful comparison."
        )
        return alerts

    # Profit Factor check
    pf = metrics.profit_factor
    pf_ref = REFERENCE_PF
    if pf < pf_ref * (1 - threshold):
        alerts.append(
            f"ALERT: Profit Factor {pf:.2f} is >{threshold_pct:.0f}% below "
            f"backtest reference ({pf_ref:.2f}). "
            f"Threshold: {pf_ref * (1 - threshold):.2f}"
        )

    # Win Rate check
    wr = metrics.win_rate
    wr_ref = REFERENCE_WR
    if wr < wr_ref * (1 - threshold):
        alerts.append(
            f"ALERT: Win Rate {wr:.1%} is >{threshold_pct:.0f}% below "
            f"backtest reference ({wr_ref:.1%}). "
            f"Threshold: {wr_ref * (1 - threshold):.1%}"
        )

    # Max Drawdown check
    dd = metrics.max_drawdown_pct
    dd_ref = REFERENCE_DD
    if dd > dd_ref * (1 + threshold):
        alerts.append(
            f"ALERT: Max Drawdown {dd:.1f}% exceeds backtest reference "
            f"({dd_ref:.1f}%) by >{threshold_pct:.0f}%. "
            f"Threshold: {dd_ref * (1 + threshold):.1f}%"
        )

    # Max consecutive losses (heuristic: >6 is concerning)
    if metrics.max_consecutive_losses >= 6:
        alerts.append(
            f"ALERT: {metrics.max_consecutive_losses} consecutive losses detected. "
            "Review market conditions and EA behavior."
        )

    # Trade frequency check (annualized)
    if metrics.total_trades >= 5 and len(metrics.cumulative_pnl) >= 2:
        first_trade = metrics.cumulative_pnl[0][0]
        last_trade = metrics.cumulative_pnl[-1][0]
        days_elapsed = (last_trade - first_trade).days
        if days_elapsed > 30:
            quarterly_rate = metrics.total_trades / (days_elapsed / 91.25)
            expected_min = BACKTEST_EXPECTATIONS["trades_per_quarter_min"]
            expected_max = BACKTEST_EXPECTATIONS["trades_per_quarter_max"]
            if quarterly_rate < expected_min:
                alerts.append(
                    f"ALERT: Trade frequency ({quarterly_rate:.0f}/quarter) is below "
                    f"expected range ({expected_min}-{expected_max}/quarter). "
                    "EA may not be executing signals properly."
                )
            elif quarterly_rate > expected_max * 1.5:
                alerts.append(
                    f"ALERT: Trade frequency ({quarterly_rate:.0f}/quarter) is abnormally high. "
                    f"Expected: {expected_min}-{expected_max}/quarter. "
                    "Check for duplicate signals or parameter misconfiguration."
                )

    if not alerts:
        alerts.append("OK: All metrics within acceptable deviation from backtest expectations.")

    return alerts


# ============================================================
# Reports
# ============================================================
def generate_summary_report(trades: list[Trade], metrics: PerformanceMetrics) -> str:
    """Generate a text summary report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  AntigravityMTF EA Gold -- Forward Test Report")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 70)

    if not trades:
        lines.append("\nNo trades found.\n")
        return "\n".join(lines)

    first = trades[0].open_time.strftime("%Y-%m-%d")
    last = trades[-1].close_time.strftime("%Y-%m-%d")
    days = (trades[-1].close_time - trades[0].open_time).days

    lines.append(f"\n  Period: {first} to {last} ({days} days)")
    lines.append(f"  Total Trades: {metrics.total_trades}")
    lines.append("")

    # Performance table
    lines.append("  --- Performance Metrics ---")
    lines.append(f"  {'Metric':<30} {'Live':>10} {'Backtest':>10} {'Status':>10}")
    lines.append(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*10}")

    pf_status = "OK" if abs(metrics.profit_factor - REFERENCE_PF) / REFERENCE_PF < DEVIATION_PCT / 100 else "ALERT"
    wr_status = "OK" if abs(metrics.win_rate - REFERENCE_WR) / REFERENCE_WR < DEVIATION_PCT / 100 else "ALERT"
    dd_status = "OK" if metrics.max_drawdown_pct <= REFERENCE_DD * (1 + DEVIATION_PCT / 100) else "ALERT"

    lines.append(f"  {'Profit Factor':<30} {metrics.profit_factor:>10.2f} {REFERENCE_PF:>10.2f} {pf_status:>10}")
    lines.append(f"  {'Win Rate':<30} {metrics.win_rate:>9.1%} {REFERENCE_WR:>9.1%} {wr_status:>10}")
    lines.append(f"  {'Max Drawdown %':<30} {metrics.max_drawdown_pct:>9.1f}% {REFERENCE_DD:>9.1f}% {dd_status:>10}")
    lines.append(f"  {'Net Profit':<30} {metrics.net_profit:>10,.0f}")
    lines.append(f"  {'Gross Profit':<30} {metrics.gross_profit:>10,.0f}")
    lines.append(f"  {'Gross Loss':<30} {metrics.gross_loss:>10,.0f}")
    lines.append(f"  {'Avg Win':<30} {metrics.avg_win:>10,.0f}")
    lines.append(f"  {'Avg Loss':<30} {metrics.avg_loss:>10,.0f}")
    lines.append(f"  {'Expectancy / Trade':<30} {metrics.expectancy:>10,.0f}")
    lines.append(f"  {'Max Consecutive Losses':<30} {metrics.max_consecutive_losses:>10d}")

    # Trade frequency
    if days > 0:
        quarterly_rate = metrics.total_trades / (days / 91.25)
        lines.append(f"  {'Trades / Quarter (annualized)':<30} {quarterly_rate:>10.0f}")

    # Deviation alerts
    lines.append("")
    lines.append("  --- Deviation Alerts ---")
    alerts = check_deviations(metrics)
    for alert in alerts:
        lines.append(f"  {alert}")

    # Daily PnL summary (last 7 days)
    lines.append("")
    lines.append("  --- Recent Daily PnL ---")
    sorted_days = sorted(metrics.daily_pnl.keys(), reverse=True)
    for day in sorted_days[:7]:
        pnl = metrics.daily_pnl[day]
        bar = "+" * int(abs(pnl) / 500) if pnl > 0 else "-" * int(abs(pnl) / 500)
        lines.append(f"  {day}: {pnl:>+10,.0f}  {bar}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def generate_weekly_report(trades: list[Trade], metrics: PerformanceMetrics) -> str:
    """Generate a weekly breakdown report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  Weekly Performance Breakdown")
    lines.append("=" * 70)

    # Group trades by ISO week
    weekly = defaultdict(list)
    for t in trades:
        week_key = t.close_time.strftime("%Y-W%W")
        weekly[week_key].append(t)

    lines.append(f"\n  {'Week':<12} {'Trades':>7} {'Win%':>7} {'PnL':>12} {'PF':>7} {'MaxDD':>10}")
    lines.append(f"  {'-'*12} {'-'*7} {'-'*7} {'-'*12} {'-'*7} {'-'*10}")

    cumulative_pnl = 0.0
    for week in sorted(weekly.keys()):
        week_trades = weekly[week]
        wm = calculate_metrics(week_trades)
        cumulative_pnl += wm.net_profit
        pf_str = f"{wm.profit_factor:.2f}" if wm.profit_factor != float('inf') else "inf"
        lines.append(
            f"  {week:<12} {wm.total_trades:>7} {wm.win_rate:>6.0%} "
            f"{wm.net_profit:>+12,.0f} {pf_str:>7} {wm.max_drawdown:>10,.0f}"
        )

    lines.append(f"\n  Cumulative PnL: {cumulative_pnl:>+,.0f}")
    lines.append("=" * 70)
    return "\n".join(lines)


def generate_monthly_report(trades: list[Trade], metrics: PerformanceMetrics) -> str:
    """Generate a monthly breakdown report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  Monthly Performance Breakdown")
    lines.append("=" * 70)

    monthly = defaultdict(list)
    for t in trades:
        month_key = t.close_time.strftime("%Y-%m")
        monthly[month_key].append(t)

    lines.append(f"\n  {'Month':<10} {'Trades':>7} {'Win%':>7} {'PnL':>12} {'PF':>7} {'MaxDD%':>8}")
    lines.append(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*12} {'-'*7} {'-'*8}")

    for month in sorted(monthly.keys()):
        month_trades = monthly[month]
        mm = calculate_metrics(month_trades)
        pf_str = f"{mm.profit_factor:.2f}" if mm.profit_factor != float('inf') else "inf"
        lines.append(
            f"  {month:<10} {mm.total_trades:>7} {mm.win_rate:>6.0%} "
            f"{mm.net_profit:>+12,.0f} {pf_str:>7} {mm.max_drawdown_pct:>7.1f}%"
        )

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ============================================================
# Go-Live Decision Support (3-month review)
# ============================================================
def three_month_review(trades: list[Trade], metrics: PerformanceMetrics) -> str:
    """Generate 3-month forward test review with go-live recommendation."""
    lines = []
    lines.append("=" * 70)
    lines.append("  3-MONTH FORWARD TEST REVIEW")
    lines.append("=" * 70)

    if not trades:
        lines.append("\n  No trades to review.")
        return "\n".join(lines)

    days = (trades[-1].close_time - trades[0].open_time).days

    # Criteria evaluation
    criteria = []

    # 1. Profit Factor
    pf_pass = metrics.profit_factor >= REFERENCE_PF * 0.7  # Allow 30% degradation
    criteria.append(("PF >= 1.14 (70% of backtest 1.63)", pf_pass, f"{metrics.profit_factor:.2f}"))

    # 2. Win Rate
    wr_pass = metrics.win_rate >= 0.50  # Minimum 50%
    criteria.append(("Win Rate >= 50%", wr_pass, f"{metrics.win_rate:.1%}"))

    # 3. Max Drawdown
    dd_pass = metrics.max_drawdown_pct <= 15.0  # Hard limit
    criteria.append(("Max DD <= 15%", dd_pass, f"{metrics.max_drawdown_pct:.1f}%"))

    # 4. Net positive
    profit_pass = metrics.net_profit > 0
    criteria.append(("Net Profit > 0", profit_pass, f"{metrics.net_profit:+,.0f}"))

    # 5. Trade count reasonable
    if days > 0:
        quarterly_rate = metrics.total_trades / (days / 91.25)
    else:
        quarterly_rate = 0
    freq_pass = 30 <= quarterly_rate <= 100
    criteria.append(("Trade freq 30-100/quarter", freq_pass, f"{quarterly_rate:.0f}/quarter"))

    # 6. No catastrophic streaks
    streak_pass = metrics.max_consecutive_losses < 8
    criteria.append(("Max consec losses < 8", streak_pass, f"{metrics.max_consecutive_losses}"))

    lines.append(f"\n  Test Period: {days} days")
    lines.append(f"  Total Trades: {metrics.total_trades}")
    lines.append("")
    lines.append(f"  {'Criterion':<40} {'Result':>10} {'Value':>15}")
    lines.append(f"  {'-'*40} {'-'*10} {'-'*15}")

    pass_count = 0
    for name, passed, value in criteria:
        status = "PASS" if passed else "FAIL"
        lines.append(f"  {name:<40} {status:>10} {value:>15}")
        if passed:
            pass_count += 1

    lines.append("")
    total = len(criteria)
    if pass_count == total:
        verdict = "GO-LIVE: All criteria passed. Proceed to live trading with minimum lot size."
    elif pass_count >= total - 1:
        verdict = "CONDITIONAL: Most criteria passed. Review the failed criterion. Consider extending the test by 1 month."
    elif pass_count >= total - 2:
        verdict = "CAUTION: Multiple criteria failed. Extend test by 2 months or re-optimize."
    else:
        verdict = "STOP: Too many criteria failed. Do NOT go live. Review EA logic and market conditions."

    lines.append(f"  VERDICT: {verdict}")
    lines.append(f"  Score: {pass_count}/{total} criteria passed")
    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Forward Test Tracker for AntigravityMTF EA Gold"
    )
    parser.add_argument(
        "--csv", required=True,
        help="Path to MT5 trade history CSV file"
    )
    parser.add_argument(
        "--report", choices=["summary", "weekly", "monthly", "review"],
        default="summary",
        help="Report type (default: summary)"
    )
    parser.add_argument(
        "--magic", type=int, default=20260224,
        help="Magic number filter (default: 20260224, use 0 to disable)"
    )
    parser.add_argument(
        "--balance", type=float, default=300_000,
        help="Initial balance for DD calculation (default: 300000)"
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Output file path (default: stdout)"
    )
    args = parser.parse_args()

    magic_filter = args.magic if args.magic != 0 else None
    trades = load_trades_from_csv(args.csv, magic_filter=magic_filter)

    if not trades:
        print("No trades found matching criteria.")
        sys.exit(0)

    metrics = calculate_metrics(trades, initial_balance=args.balance)

    if args.report == "summary":
        report = generate_summary_report(trades, metrics)
    elif args.report == "weekly":
        report = generate_weekly_report(trades, metrics)
    elif args.report == "monthly":
        report = generate_monthly_report(trades, metrics)
    elif args.report == "review":
        report = three_month_review(trades, metrics)
    else:
        report = generate_summary_report(trades, metrics)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved to {args.output}")
    else:
        print(report)

    # Always print deviation alerts to stderr
    alerts = check_deviations(metrics)
    for alert in alerts:
        if alert.startswith("ALERT"):
            print(alert, file=sys.stderr)


if __name__ == "__main__":
    main()
