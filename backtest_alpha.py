"""
backtest_alpha.py -- GoldAlpha strategy backtester
Replicates GoldAlpha v12/v6 MQL5 logic exactly in Python.
Supports parameter grid search, WFA, and JPY profitability calculation.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import itertools
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# Configuration
# ============================================================

@dataclass
class AlphaConfig:
    # Trend
    W1_FastEMA: int = 8
    W1_SlowEMA: int = 21
    D1_EMA: int = 50

    # H4 Entry
    H4_EMA: int = 20
    ATR_Period: int = 14
    ATR_SMA: int = 50

    # Risk / Exit (v13 optimized)
    SL_ATR_Mult: float = 2.5
    Trail_ATR: float = 3.5
    BE_ATR: float = 1.5
    RiskPct: float = 2.5
    BodyRatio: float = 0.34

    # Entry Filters (v13 optimized)
    EMA_Zone_ATR: float = 0.40
    ATR_Filter: float = 0.35
    D1_Tolerance: float = 0.003
    MaxPositions: int = 3

    # Lot
    MinLot: float = 0.01
    MaxLot: float = 0.50

    # Simulation
    initial_balance: float = 300000.0  # JPY
    tick_value_per_lot: float = 100.0  # USD per 1.0 pip per 1.0 lot (XAUUSD)
    usdjpy_rate: float = 150.0  # approximate

    def label(self):
        return (f"Zone={self.EMA_Zone_ATR:.2f}_ATR={self.ATR_Filter:.2f}_"
                f"Body={self.BodyRatio:.2f}_BE={self.BE_ATR:.1f}_"
                f"Trail={self.Trail_ATR:.1f}_SL={self.SL_ATR_Mult:.1f}_"
                f"MaxPos={self.MaxPositions}_Risk={self.RiskPct:.2f}")


# ============================================================
# Indicator calculations
# ============================================================

def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series, period):
    return series.rolling(period).mean()


def calc_atr(high, low, close, period):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ============================================================
# Data loading and preparation
# ============================================================

def load_data(h4_path, d1_path):
    """Load H4 and D1 CSV data."""
    h4 = pd.read_csv(h4_path, parse_dates=["DateTime"])
    d1 = pd.read_csv(d1_path, parse_dates=["DateTime"])
    h4.sort_values("DateTime", inplace=True)
    d1.sort_values("DateTime", inplace=True)
    h4.reset_index(drop=True, inplace=True)
    d1.reset_index(drop=True, inplace=True)
    return h4, d1


def build_weekly(d1: pd.DataFrame) -> pd.DataFrame:
    """Build weekly OHLC from daily data."""
    d1 = d1.copy()
    d1["week"] = d1["DateTime"].dt.isocalendar().week.astype(int)
    d1["year"] = d1["DateTime"].dt.isocalendar().year.astype(int)

    weekly = d1.groupby(["year", "week"]).agg(
        DateTime=("DateTime", "first"),
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
    ).reset_index(drop=True)
    weekly.sort_values("DateTime", inplace=True)
    weekly.reset_index(drop=True, inplace=True)
    return weekly


def prepare_indicators(h4, d1, w1, cfg: AlphaConfig):
    """Pre-compute all indicators."""
    # W1 indicators
    w1["fast_ema"] = calc_ema(w1["Close"], cfg.W1_FastEMA)
    w1["slow_ema"] = calc_ema(w1["Close"], cfg.W1_SlowEMA)

    # D1 indicators
    d1["ema50"] = calc_ema(d1["Close"], cfg.D1_EMA)

    # H4 indicators
    h4["ema20"] = calc_ema(h4["Close"], cfg.H4_EMA)
    h4["atr"] = calc_atr(h4["High"], h4["Low"], h4["Close"], cfg.ATR_Period)
    h4["avg_atr"] = calc_sma(h4["atr"], cfg.ATR_SMA)

    # Body ratio pre-computation
    h4["body"] = (h4["Close"] - h4["Open"]).abs()
    h4["range"] = h4["High"] - h4["Low"]
    h4["body_ratio"] = np.where(h4["range"] > 0.001, h4["body"] / h4["range"], 0)
    h4["bullish"] = h4["Close"] > h4["Open"]
    h4["bearish"] = h4["Close"] < h4["Open"]

    return h4, d1, w1


# ============================================================
# Trade simulation
# ============================================================

@dataclass
class Position:
    ticket: int
    type: str  # "BUY" or "SELL"
    open_price: float
    sl: float
    lot: float
    open_time: pd.Timestamp
    open_bar: int
    be_triggered: bool = False


@dataclass
class ClosedTrade:
    type: str
    open_price: float
    close_price: float
    sl: float
    lot: float
    open_time: pd.Timestamp
    close_time: pd.Timestamp
    pnl_usd: float
    pnl_jpy: float


def run_backtest(h4, d1, w1, cfg: AlphaConfig,
                 start_date=None, end_date=None) -> Tuple[List[ClosedTrade], float]:
    """Run full backtest simulation."""

    if start_date:
        h4_mask = h4["DateTime"] >= pd.Timestamp(start_date)
        h4_start = h4_mask.idxmax() if h4_mask.any() else 0
    else:
        h4_start = max(cfg.ATR_SMA + cfg.ATR_Period + 5, cfg.H4_EMA + 5)

    if end_date:
        h4_mask_end = h4["DateTime"] <= pd.Timestamp(end_date)
        h4_end = h4_mask_end[::-1].idxmax() + 1 if h4_mask_end.any() else len(h4)
    else:
        h4_end = len(h4)

    # Pre-extract numpy arrays for speed
    h4_dt = h4["DateTime"].values
    h4_open = h4["Open"].values
    h4_high = h4["High"].values
    h4_low = h4["Low"].values
    h4_close = h4["Close"].values
    h4_ema20 = h4["ema20"].values
    h4_atr = h4["atr"].values
    h4_avg_atr = h4["avg_atr"].values
    h4_body_ratio = h4["body_ratio"].values
    h4_bullish = h4["bullish"].values
    h4_bearish = h4["bearish"].values

    # W1 lookups
    w1_dt = w1["DateTime"].values
    w1_fast = w1["fast_ema"].values
    w1_slow = w1["slow_ema"].values
    w1_idx_arr = np.searchsorted(w1_dt, h4_dt, side="right") - 1

    # D1 lookups
    d1_dt = d1["DateTime"].values
    d1_close_arr = d1["Close"].values
    d1_ema50 = d1["ema50"].values
    d1_idx_arr = np.searchsorted(d1_dt, h4_dt, side="right") - 1

    positions: List[Position] = []
    closed: List[ClosedTrade] = []
    balance = cfg.initial_balance
    equity_peak = balance
    max_dd = 0.0
    ticket_counter = 0

    usdjpy = cfg.usdjpy_rate

    def calc_lot(sl_dist, equity):
        risk_money_usd = (equity / usdjpy) * cfg.RiskPct / 100.0
        # XAUUSD: 1 lot = 100 oz, 1 pip = $0.01, tick_value per lot per point
        # Actually for XAUUSD: 1 lot = 100 oz, so $1 move = $100 per lot
        cost_per_point = 100.0  # USD per $1 move per lot (standard XAUUSD)
        if sl_dist <= 0:
            return cfg.MinLot
        lot = risk_money_usd / (sl_dist * cost_per_point)
        lot = max(cfg.MinLot, min(cfg.MaxLot, round(lot * 100) / 100))
        return lot

    def close_position(pos, close_price, close_time):
        nonlocal balance
        if pos.type == "BUY":
            pnl_usd = (close_price - pos.open_price) * 100.0 * pos.lot
        else:
            pnl_usd = (pos.open_price - close_price) * 100.0 * pos.lot
        pnl_jpy = pnl_usd * usdjpy
        balance += pnl_jpy

        closed.append(ClosedTrade(
            type=pos.type,
            open_price=pos.open_price,
            close_price=close_price,
            sl=pos.sl,
            lot=pos.lot,
            open_time=pos.open_time,
            close_time=close_time,
            pnl_usd=pnl_usd,
            pnl_jpy=pnl_jpy,
        ))

    for i in range(max(h4_start, 2), h4_end):
        bar_time = pd.Timestamp(h4_dt[i])
        bar_high = h4_high[i]
        bar_low = h4_low[i]
        bar_open = h4_open[i]

        # --- Check SL hits on existing positions ---
        to_remove = []
        for pi, pos in enumerate(positions):
            if pos.type == "BUY":
                if bar_low <= pos.sl:
                    close_position(pos, pos.sl, bar_time)
                    to_remove.append(pi)
            else:
                if bar_high >= pos.sl:
                    close_position(pos, pos.sl, bar_time)
                    to_remove.append(pi)
        for pi in sorted(to_remove, reverse=True):
            positions.pop(pi)

        # --- Trail management (once per H4 bar) ---
        atr_val = h4_atr[i - 1] if i > 0 else h4_atr[i]
        if np.isnan(atr_val) or atr_val <= 0.001:
            atr_val = h4_atr[i]

        for pos in positions:
            if pos.type == "BUY":
                profit = h4_close[i] - pos.open_price
                # BE check
                if not pos.be_triggered and profit > cfg.BE_ATR * atr_val:
                    new_sl = pos.open_price + 0.1 * atr_val
                    if new_sl > pos.sl:
                        pos.sl = round(new_sl, 2)
                        pos.be_triggered = True
                # Trailing after BE
                if pos.be_triggered:
                    highest = max(h4_high[max(0, i-10):i+1])
                    new_sl = highest - cfg.Trail_ATR * atr_val
                    if new_sl > pos.sl + 0.10:
                        pos.sl = round(new_sl, 2)
            else:
                profit = pos.open_price - h4_close[i]
                if not pos.be_triggered and profit > cfg.BE_ATR * atr_val:
                    new_sl = pos.open_price - 0.1 * atr_val
                    if new_sl < pos.sl:
                        pos.sl = round(new_sl, 2)
                        pos.be_triggered = True
                if pos.be_triggered:
                    lowest = min(h4_low[max(0, i-10):i+1])
                    new_sl = lowest + cfg.Trail_ATR * atr_val
                    if new_sl < pos.sl - 0.10:
                        pos.sl = round(new_sl, 2)

        # Track DD
        unrealized_jpy = 0
        for pos in positions:
            if pos.type == "BUY":
                unrealized_jpy += (h4_close[i] - pos.open_price) * 100.0 * pos.lot * usdjpy
            else:
                unrealized_jpy += (pos.open_price - h4_close[i]) * 100.0 * pos.lot * usdjpy
        equity = balance + unrealized_jpy
        equity_peak = max(equity_peak, equity)
        dd = (equity_peak - equity) / equity_peak if equity_peak > 0 else 0
        max_dd = max(max_dd, dd)

        # --- Entry logic ---
        if len(positions) >= cfg.MaxPositions:
            continue

        # Weekend filter
        dow = bar_time.dayofweek  # 0=Mon
        if dow >= 5:
            continue
        if dow == 4 and bar_time.hour > 16:
            continue

        # W1 trend (use previous completed week)
        w1_i = w1_idx_arr[i]
        if w1_i < 1:
            continue
        # Use previous week's EMA (shift 1 equivalent)
        w1_f = w1_fast[w1_i - 1]
        w1_s = w1_slow[w1_i - 1]
        if np.isnan(w1_f) or np.isnan(w1_s):
            continue
        w1_dir = 0
        if w1_f > w1_s:
            w1_dir = 1
        elif w1_f < w1_s:
            w1_dir = -1
        if w1_dir == 0:
            continue

        # D1 filter (use previous completed day)
        d1_i = d1_idx_arr[i]
        if d1_i < 1:
            continue
        d1_c = d1_close_arr[d1_i - 1]
        d1_e = d1_ema50[d1_i - 1]
        if np.isnan(d1_e) or d1_e <= 0:
            continue
        d1_diff = (d1_c - d1_e) / d1_e

        if w1_dir == 1 and d1_diff < -cfg.D1_Tolerance:
            continue
        if w1_dir == -1 and d1_diff > cfg.D1_Tolerance:
            continue

        # ATR filter
        atr_cur = h4_atr[i - 1]
        avg_atr_cur = h4_avg_atr[i - 1]
        if np.isnan(atr_cur) or np.isnan(avg_atr_cur) or avg_atr_cur <= 0:
            continue
        if atr_cur < avg_atr_cur * cfg.ATR_Filter:
            continue

        # H4 EMA
        ema_val = h4_ema20[i - 1]
        if np.isnan(ema_val):
            continue

        zone = cfg.EMA_Zone_ATR * atr_cur

        # Check dip on bar i-1 and i-2 (shift 1 and 2)
        signal = 0
        for shift in [1, 2]:
            si = i - shift
            if si < 0:
                continue

            if w1_dir == 1:
                # BUY dip
                if h4_low[si] <= ema_val + zone and \
                   h4_close[si] > ema_val and \
                   h4_bullish[si] and \
                   h4_body_ratio[si] >= cfg.BodyRatio:
                    signal = 1
                    break
            else:
                # SELL dip
                if h4_high[si] >= ema_val - zone and \
                   h4_close[si] < ema_val and \
                   h4_bearish[si] and \
                   h4_body_ratio[si] >= cfg.BodyRatio:
                    signal = -1
                    break

        if signal == 0:
            continue

        # Execute trade
        sl_dist = cfg.SL_ATR_Mult * atr_cur
        lot = calc_lot(sl_dist, max(balance, cfg.initial_balance * 0.5))

        if signal == 1:
            entry = h4_open[i]  # enter at bar open (next bar after signal)
            sl = round(entry - sl_dist, 2)
        else:
            entry = h4_open[i]
            sl = round(entry + sl_dist, 2)

        ticket_counter += 1
        positions.append(Position(
            ticket=ticket_counter,
            type="BUY" if signal == 1 else "SELL",
            open_price=entry,
            sl=sl,
            lot=lot,
            open_time=bar_time,
            open_bar=i,
        ))

    # Close remaining positions at last bar close
    last_close = h4_close[h4_end - 1] if h4_end > 0 else 0
    last_time = pd.Timestamp(h4_dt[h4_end - 1]) if h4_end > 0 else pd.Timestamp.now()
    for pos in positions:
        close_position(pos, last_close, last_time)

    return closed, max_dd


# ============================================================
# Metrics
# ============================================================

def calc_metrics(trades: List[ClosedTrade], cfg: AlphaConfig, max_dd: float):
    """Calculate performance metrics."""
    if not trades:
        return {"trades": 0, "pf": 0, "wr": 0, "dd": max_dd, "net_jpy": 0,
                "net_usd": 0, "avg_jpy": 0, "daily_jpy": 0}

    gross_profit = sum(t.pnl_jpy for t in trades if t.pnl_jpy > 0)
    gross_loss = abs(sum(t.pnl_jpy for t in trades if t.pnl_jpy < 0))
    wins = sum(1 for t in trades if t.pnl_jpy > 0)
    total = len(trades)
    net_jpy = sum(t.pnl_jpy for t in trades)
    net_usd = sum(t.pnl_usd for t in trades)

    pf = gross_profit / gross_loss if gross_loss > 0 else 999.0
    wr = wins / total * 100 if total > 0 else 0

    # Trading days
    if trades:
        first = trades[0].open_time
        last = trades[-1].close_time
        days = max((last - first).days, 1)
    else:
        days = 1

    daily_jpy = net_jpy / days
    avg_jpy = net_jpy / total if total > 0 else 0

    return {
        "trades": total,
        "pf": round(pf, 3),
        "wr": round(wr, 1),
        "dd": round(max_dd * 100, 2),
        "net_jpy": round(net_jpy),
        "net_usd": round(net_usd),
        "avg_jpy": round(avg_jpy),
        "daily_jpy": round(daily_jpy),
        "gross_profit_jpy": round(gross_profit),
        "gross_loss_jpy": round(gross_loss),
        "days": days,
        "wins": wins,
        "losses": total - wins,
    }


# ============================================================
# Walk-Forward Analysis
# ============================================================

def run_wfa(h4, d1, w1, cfg: AlphaConfig, quarters=8):
    """Run walk-forward analysis by quarters."""
    # Get date range
    min_date = h4["DateTime"].min()
    max_date = h4["DateTime"].max()

    # Create quarterly boundaries
    dates = pd.date_range(min_date, max_date, freq="QS")
    if len(dates) < quarters + 1:
        quarters = len(dates) - 1

    results = []
    for q in range(len(dates) - 1):
        start = dates[q]
        end = dates[q + 1]
        trades, dd = run_backtest(h4, d1, w1, cfg, start_date=start, end_date=end)
        m = calc_metrics(trades, cfg, dd)
        m["quarter"] = f"{start.year}-Q{(start.month-1)//3+1}"
        m["start"] = start
        m["end"] = end
        results.append(m)

    return results


# ============================================================
# Grid Search
# ============================================================

def grid_search(h4, d1, w1, param_grid: dict, base_cfg: AlphaConfig = None):
    """Run parameter grid search."""
    if base_cfg is None:
        base_cfg = AlphaConfig()

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    results = []
    total = len(combos)
    print(f"Grid search: {total} combinations")

    for idx, combo in enumerate(combos):
        cfg = AlphaConfig(
            **{k: getattr(base_cfg, k) for k in base_cfg.__dataclass_fields__}
        )
        for k, v in zip(keys, combo):
            setattr(cfg, k, v)

        trades, dd = run_backtest(h4, d1, w1, cfg)
        m = calc_metrics(trades, cfg, dd)
        m["config"] = cfg.label()
        for k, v in zip(keys, combo):
            m[k] = v
        results.append(m)

        if (idx + 1) % 50 == 0 or idx == total - 1:
            print(f"  [{idx+1}/{total}] Latest: T={m['trades']} PF={m['pf']} DD={m['dd']}%")

    return sorted(results, key=lambda x: (-x["pf"] if x["trades"] >= 500 else 0, -x["trades"]))


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    import sys
    import json

    DATA_DIR = "/tmp/FxTrading_EA_clone"
    h4_raw, d1_raw = load_data(f"{DATA_DIR}/XAUUSD_H4.csv", f"{DATA_DIR}/XAUUSD_D1.csv")
    w1_raw = build_weekly(d1_raw)

    # === Phase 1: Reproduce v12 baseline ===
    print("=" * 70)
    print("Phase 1: v12 baseline reproduction")
    print("=" * 70)

    cfg_v12 = AlphaConfig()  # defaults match v12
    h4, d1, w1 = prepare_indicators(h4_raw.copy(), d1_raw.copy(), w1_raw.copy(), cfg_v12)
    trades, dd = run_backtest(h4, d1, w1, cfg_v12)
    m = calc_metrics(trades, cfg_v12, dd)
    print(f"v12: Trades={m['trades']} PF={m['pf']} WR={m['wr']}% DD={m['dd']}% "
          f"Net={m['net_jpy']:,}JPY Daily={m['daily_jpy']:,}JPY/day")

    # === Phase 1b: v6 baseline ===
    print("\n" + "=" * 70)
    print("Phase 1b: v6 baseline reproduction")
    print("=" * 70)
    cfg_v6 = AlphaConfig(
        EMA_Zone_ATR=0.7, ATR_Filter=0.4, RiskPct=0.24, MaxLot=0.15,
        BodyRatio=0.32, BE_ATR=1.5, Trail_ATR=2.5, SL_ATR_Mult=2.0,
    )
    h4, d1, w1 = prepare_indicators(h4_raw.copy(), d1_raw.copy(), w1_raw.copy(), cfg_v6)
    trades_v6, dd_v6 = run_backtest(h4, d1, w1, cfg_v6)
    m_v6 = calc_metrics(trades_v6, cfg_v6, dd_v6)
    print(f"v6:  Trades={m_v6['trades']} PF={m_v6['pf']} WR={m_v6['wr']}% DD={m_v6['dd']}% "
          f"Net={m_v6['net_jpy']:,}JPY Daily={m_v6['daily_jpy']:,}JPY/day")

    # === Phase 2: Grid search for optimal parameters ===
    print("\n" + "=" * 70)
    print("Phase 2: Grid search (target: 500+ trades, PF 1.5+, DD<15%)")
    print("=" * 70)

    param_grid = {
        "EMA_Zone_ATR": [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
        "ATR_Filter": [0.3, 0.4, 0.5, 0.6],
        "BodyRatio": [0.25, 0.28, 0.30, 0.32, 0.35],
        "BE_ATR": [1.0, 1.2, 1.5, 1.8],
        "Trail_ATR": [2.0, 2.5, 3.0],
        "SL_ATR_Mult": [1.5, 2.0, 2.5],
        "MaxPositions": [2, 3],
        "RiskPct": [0.18, 0.24, 0.30],
    }

    h4, d1, w1 = prepare_indicators(h4_raw.copy(), d1_raw.copy(), w1_raw.copy(), AlphaConfig())

    results = grid_search(h4, d1, w1, param_grid)

    # Filter and show top results
    print("\n" + "=" * 70)
    print("Top results (500+ trades, PF >= 1.5, DD < 20%):")
    print("=" * 70)

    good = [r for r in results if r["trades"] >= 500 and r["pf"] >= 1.5 and r["dd"] < 20]
    good.sort(key=lambda x: (-x["pf"], -x["trades"]))

    for i, r in enumerate(good[:30]):
        print(f"#{i+1:2d}: T={r['trades']:4d} PF={r['pf']:.3f} WR={r['wr']:.1f}% "
              f"DD={r['dd']:.1f}% Daily={r['daily_jpy']:,}JPY "
              f"Zone={r['EMA_Zone_ATR']:.1f} ATR={r['ATR_Filter']:.1f} "
              f"Body={r['BodyRatio']:.2f} BE={r['BE_ATR']:.1f} Trail={r['Trail_ATR']:.1f} "
              f"SL={r['SL_ATR_Mult']:.1f} MaxP={r['MaxPositions']} Risk={r['RiskPct']:.2f}")

    if not good:
        print("No results met all criteria. Showing best by PF with 400+ trades:")
        relaxed = [r for r in results if r["trades"] >= 400 and r["pf"] >= 1.3]
        relaxed.sort(key=lambda x: (-x["pf"], -x["trades"]))
        for i, r in enumerate(relaxed[:20]):
            print(f"#{i+1:2d}: T={r['trades']:4d} PF={r['pf']:.3f} WR={r['wr']:.1f}% "
                  f"DD={r['dd']:.1f}% Daily={r['daily_jpy']:,}JPY "
                  f"Zone={r['EMA_Zone_ATR']:.1f} ATR={r['ATR_Filter']:.1f} "
                  f"Body={r['BodyRatio']:.2f} BE={r['BE_ATR']:.1f} Trail={r['Trail_ATR']:.1f} "
                  f"SL={r['SL_ATR_Mult']:.1f} MaxP={r['MaxPositions']} Risk={r['RiskPct']:.2f}")

    print(f"\nTotal good candidates: {len(good)}")
