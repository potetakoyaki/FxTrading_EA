#!/usr/bin/env python3
"""
Ablation Test: Measure individual contribution of v17.0 modules
================================================================
4 configurations, 16-quarter WFA each:

  Config A (Baseline v9.3): All v17 modules OFF
    - Standard lot sizing (no cascading risk)
    - ADX threshold regime detection (no ML)
    - Hardcoded parameters (no JSON)
    - USE_INTRABAR_SIM = False

  Config B (+ Risk Manager): Simulate cascading risk effects
    - Stricter spread validation (reject if spread > 50)
    - Portfolio risk check (max 2% total exposure)
    - Enhanced DD escalation (verify existing)
    - Everything else same as A

  Config C (+ ML Regime): Simulate multi-feature regime detection
    - ADX + ER + VolRatio + RSI for regime classification
    - Trend/Range/HighVol/Crash with lot multipliers
    - Everything else same as A

  Config D (Full v17): Both B and C active
"""
import sys
sys.stdout.reconfigure(line_buffering=True)
import os, io, time as time_mod, copy
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, '/tmp/FxTrading_EA')
os.chdir('/tmp/FxTrading_EA')

# Force fresh imports
for mod in list(sys.modules.keys()):
    if 'backtest_gold' in mod or 'backtest_csv' in mod:
        del sys.modules[mod]

import pandas as pd
import numpy as np
from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold_fast import GoldBacktesterFast
from backtest_gold import (
    GoldConfig, GoldBacktester,
    calc_sma, calc_ema, calc_rsi, calc_atr, calc_adx, calc_bb,
)

# ============================================================
# Data Loading
# ============================================================
print("=" * 110)
print("  ABLATION TEST: v17.0 Module Contribution Analysis")
print("  4 configs x 16-quarter WFA")
print("=" * 110)

t_global = time_mod.time()
print("\nLoading data...", flush=True)
t0 = time_mod.time()
h4_global = load_csv('XAUUSD_H4.csv')
h1_global = merge_and_fill(load_csv('XAUUSD_H1.csv'), generate_h1_from_h4(h4_global))
m15_global = merge_and_fill(load_csv('XAUUSD_M15.csv'), generate_m15_from_h1(h1_global))
usdjpy_global = load_csv('USDJPY_H1.csv')
print(f"Data loaded in {time_mod.time()-t0:.1f}s")
print(f"M15: {len(m15_global):,} bars ({m15_global.index[0]} ~ {m15_global.index[-1]})")

# ============================================================
# Walk Definitions (16 quarters: 2022-Q1 to 2025-Q4)
# ============================================================
walks = []
for year in range(2022, 2026):
    for q in range(1, 5):
        month_start = (q - 1) * 3 + 1
        month_end = q * 3
        start = pd.Timestamp(f"{year}-{month_start:02d}-01")
        if month_end == 12:
            end = pd.Timestamp(f"{year+1}-01-01")
        else:
            end = pd.Timestamp(f"{year}-{month_end+1:02d}-01")
        label = f"{year}-Q{q}"
        if start >= m15_global.index[0] and start < m15_global.index[-1]:
            walks.append({"name": label, "start": start, "end": end})
walks = walks[:16]

print(f"\nWalk windows ({len(walks)}):")
for w in walks:
    print(f"  {w['name']}: {w['start'].date()} -> {w['end'].date()}")


# ============================================================
# Efficiency Ratio calculation (for Config C ML Regime)
# ============================================================
def calc_efficiency_ratio(close_series, period=14):
    """Kaufman's Efficiency Ratio: directional movement / total movement.
    ER close to 1.0 = trending, close to 0.0 = ranging/noisy.
    """
    if len(close_series) < period + 1:
        return pd.Series(np.nan, index=close_series.index)
    direction = abs(close_series - close_series.shift(period))
    volatility = abs(close_series.diff()).rolling(window=period).sum()
    er = direction / volatility.replace(0, np.nan)
    return er


# ============================================================
# Custom Backtester: Config B (Risk Manager)
# ============================================================
class RiskManagerBacktester(GoldBacktesterFast):
    """Config B: Adds stricter spread validation + portfolio risk check."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self._max_spread_strict = 50  # Stricter than default 80
        self._max_portfolio_risk_pct = 2.0  # Max 2% total exposure

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None):
        """Override run to inject stricter spread and portfolio risk checks.

        We hook into the parent's run by temporarily replacing the config's
        MAX_DYNAMIC_SPREAD and adding a portfolio risk check via lot scaling.
        """
        # Stricter spread: lower MAX_DYNAMIC_SPREAD to reject more entries
        orig_max_spread = self.cfg.MAX_DYNAMIC_SPREAD
        self.cfg.MAX_DYNAMIC_SPREAD = self._max_spread_strict

        # Store original _open_trade method
        orig_open_trade = self._open_trade

        backtester_self = self

        def risk_managed_open_trade(direction, price, time, score, dd_pct,
                                     sl_points, tp_points, current_atr,
                                     lot_multiplier=1.0, component_mask=None,
                                     entry_type="normal", momentum_burst=False,
                                     entry_bar=0, bar_spread_points=None):
            """Wrapper that adds portfolio risk check before opening trade."""
            # Portfolio risk check: if total open exposure > 2%, reduce lot
            total_exposure_jpy = 0
            for pos in backtester_self.open_positions:
                pos_risk = pos["sl_points"] * backtester_self.cfg.POINT * \
                           backtester_self.cfg.CONTRACT_SIZE * pos["lot"] * 150.0
                total_exposure_jpy += pos_risk

            max_risk_jpy = backtester_self.balance * backtester_self._max_portfolio_risk_pct / 100.0
            if total_exposure_jpy > 0:
                remaining_budget = max(0, max_risk_jpy - total_exposure_jpy)
                new_trade_risk = sl_points * backtester_self.cfg.POINT * \
                                 backtester_self.cfg.CONTRACT_SIZE * backtester_self.cfg.MIN_LOT * 150.0
                if new_trade_risk > 0 and remaining_budget < new_trade_risk:
                    # No budget left for even min lot, skip entry
                    return
                # Scale down lot_multiplier if needed
                if remaining_budget < max_risk_jpy:
                    risk_scale = remaining_budget / max_risk_jpy
                    lot_multiplier *= max(0.3, risk_scale)

            # Stricter spread validation at entry
            if bar_spread_points is not None and not np.isnan(bar_spread_points):
                if bar_spread_points > backtester_self._max_spread_strict:
                    return  # Reject entry

            orig_open_trade(direction, price, time, score, dd_pct,
                           sl_points, tp_points, current_atr,
                           lot_multiplier, component_mask,
                           entry_type, momentum_burst, entry_bar,
                           bar_spread_points)

        self._open_trade = risk_managed_open_trade
        try:
            super().run(h4_df, h1_df, m15_df, usdjpy_df=usdjpy_df)
        finally:
            self._open_trade = orig_open_trade
            self.cfg.MAX_DYNAMIC_SPREAD = orig_max_spread


# ============================================================
# Custom Backtester: Config C (ML Regime)
# ============================================================
class MLRegimeBacktester(GoldBacktesterFast):
    """Config C: Multi-feature regime detection replacing simple ADX threshold.

    Regime classification using ADX + ER + VolRatio + RSI:
      - Crash: VolRatio > 3.0 -> lot_multi = 0.0 (block)
      - HighVol: VolRatio > 1.5 -> lot_multi = 0.3
      - Trend: ADX > 20 AND ER > 0.3 -> lot_multi = 1.0
      - Range: ADX < 20 AND ER < 0.3 -> lot_multi = 0.6
    """

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None):
        """Override run to inject ML regime lot multiplier."""
        # Pre-compute H4 Efficiency Ratio for regime detection
        h4_df_copy = h4_df.copy()
        h4_df_copy["er"] = calc_efficiency_ratio(h4_df_copy["Close"], 14)

        orig_open_trade = self._open_trade
        backtester_self = self

        # We need to track the current regime for each bar
        # Store regime data accessible during entry
        self._ml_regime_h4 = h4_df_copy
        self._ml_lot_multi = 1.0

        def ml_regime_open_trade(direction, price, time, score, dd_pct,
                                  sl_points, tp_points, current_atr,
                                  lot_multiplier=1.0, component_mask=None,
                                  entry_type="normal", momentum_burst=False,
                                  entry_bar=0, bar_spread_points=None):
            """Wrapper that applies ML regime lot multiplier."""
            lot_multiplier *= backtester_self._ml_lot_multi
            if lot_multiplier <= 0:
                return  # Crash regime -> block entry
            orig_open_trade(direction, price, time, score, dd_pct,
                           sl_points, tp_points, current_atr,
                           lot_multiplier, component_mask,
                           entry_type, momentum_burst, entry_bar,
                           bar_spread_points)

        self._open_trade = ml_regime_open_trade

        # We also need to compute regime per bar.
        # Override the run loop by wrapping the parent run and computing
        # regime before each bar. Since we can't easily hook into per-bar,
        # we pre-compute regime for all M15 bars.
        m15_df_c = m15_df.copy()
        m15_atr = calc_atr(m15_df_c["High"], m15_df_c["Low"], m15_df_c["Close"], 14)
        m15_atr_avg = m15_atr.rolling(window=50).mean()
        m15_rsi = calc_rsi(m15_df_c["Close"], 14)

        # For each M15 bar, find corresponding H4 bar's ADX and ER
        h4_times = h4_df_copy.index.values
        h4_adx = h4_df_copy["adx"].values if "adx" in h4_df_copy.columns else np.full(len(h4_df_copy), np.nan)
        h4_er = h4_df_copy["er"].values

        # Pre-compute ADX if not present
        if "adx" not in h4_df_copy.columns:
            h4_df_temp = h4_df_copy.copy()
            adx_vals, _, _ = calc_adx(h4_df_temp["High"], h4_df_temp["Low"], h4_df_temp["Close"], 14)
            h4_adx = adx_vals.values

        # Pre-compute regime multiplier per M15 bar
        regime_multis = np.ones(len(m15_df_c))
        m15_times = m15_df_c.index.values

        for i in range(len(m15_df_c)):
            # Find latest H4 bar
            ct = m15_times[i]
            h4_idx = np.searchsorted(h4_times, ct, side='right') - 1
            if h4_idx < 0:
                continue

            atr_val = m15_atr.iloc[i] if i < len(m15_atr) else np.nan
            atr_avg_val = m15_atr_avg.iloc[i] if i < len(m15_atr_avg) else np.nan
            rsi_val = m15_rsi.iloc[i] if i < len(m15_rsi) else np.nan

            if pd.isna(atr_val) or pd.isna(atr_avg_val) or atr_avg_val <= 0:
                continue

            vol_ratio = atr_val / atr_avg_val
            adx_val = h4_adx[h4_idx] if h4_idx < len(h4_adx) else np.nan
            er_val = h4_er[h4_idx] if h4_idx < len(h4_er) else np.nan

            if pd.isna(adx_val):
                adx_val = 25  # default
            if pd.isna(er_val):
                er_val = 0.5  # default

            # Classify regime
            if vol_ratio > 3.0:
                regime_multis[i] = 0.0  # Crash
            elif vol_ratio > 1.5:
                regime_multis[i] = 0.3  # HighVol
            elif adx_val > 20 and er_val > 0.3:
                regime_multis[i] = 1.0  # Trend
            elif adx_val < 20 and er_val < 0.3:
                regime_multis[i] = 0.6  # Range
            else:
                regime_multis[i] = 0.8  # Mixed/transitional

        self._regime_multis = regime_multis
        self._m15_times = m15_times

        # We need to update _ml_lot_multi per bar during the run.
        # Hook into the bar processing by overriding a method that's called per bar.
        # The most reliable way: use the equity_curve append to track bar index.
        _orig_manage = self._manage_positions

        def hooked_manage(high, low, close, time, bar_idx, m15_df_inner,
                          bar_open=None, bar_spread_points=None):
            # Update regime multiplier for current bar
            if bar_idx < len(backtester_self._regime_multis):
                backtester_self._ml_lot_multi = backtester_self._regime_multis[bar_idx]
            _orig_manage(high, low, close, time, bar_idx, m15_df_inner,
                        bar_open=bar_open, bar_spread_points=bar_spread_points)

        self._manage_positions = hooked_manage

        try:
            super().run(h4_df, h1_df, m15_df, usdjpy_df=usdjpy_df)
        finally:
            self._open_trade = orig_open_trade
            self._manage_positions = _orig_manage


# ============================================================
# Custom Backtester: Config D (Full v17 = B + C)
# ============================================================
class FullV17Backtester(GoldBacktesterFast):
    """Config D: Both Risk Manager (B) and ML Regime (C) active."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self._max_spread_strict = 50
        self._max_portfolio_risk_pct = 2.0
        self._ml_lot_multi = 1.0

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None):
        # Stricter spread
        orig_max_spread = self.cfg.MAX_DYNAMIC_SPREAD
        self.cfg.MAX_DYNAMIC_SPREAD = self._max_spread_strict

        # Pre-compute H4 ER for ML regime
        h4_df_copy = h4_df.copy()
        h4_df_copy["er"] = calc_efficiency_ratio(h4_df_copy["Close"], 14)

        h4_times = h4_df_copy.index.values
        h4_adx = h4_df_copy["adx"].values if "adx" in h4_df_copy.columns else np.full(len(h4_df_copy), np.nan)
        h4_er = h4_df_copy["er"].values

        if "adx" not in h4_df_copy.columns:
            h4_df_temp = h4_df_copy.copy()
            adx_vals, _, _ = calc_adx(h4_df_temp["High"], h4_df_temp["Low"], h4_df_temp["Close"], 14)
            h4_adx = adx_vals.values

        # Pre-compute regime multiplier per M15 bar
        m15_df_c = m15_df.copy()
        m15_atr = calc_atr(m15_df_c["High"], m15_df_c["Low"], m15_df_c["Close"], 14)
        m15_atr_avg = m15_atr.rolling(window=50).mean()
        m15_rsi = calc_rsi(m15_df_c["Close"], 14)
        m15_times = m15_df_c.index.values

        regime_multis = np.ones(len(m15_df_c))
        for i in range(len(m15_df_c)):
            ct = m15_times[i]
            h4_idx = np.searchsorted(h4_times, ct, side='right') - 1
            if h4_idx < 0:
                continue
            atr_val = m15_atr.iloc[i]
            atr_avg_val = m15_atr_avg.iloc[i]
            if pd.isna(atr_val) or pd.isna(atr_avg_val) or atr_avg_val <= 0:
                continue
            vol_ratio = atr_val / atr_avg_val
            adx_val = h4_adx[h4_idx] if h4_idx < len(h4_adx) else 25
            er_val = h4_er[h4_idx] if h4_idx < len(h4_er) else 0.5
            if pd.isna(adx_val):
                adx_val = 25
            if pd.isna(er_val):
                er_val = 0.5

            if vol_ratio > 3.0:
                regime_multis[i] = 0.0
            elif vol_ratio > 1.5:
                regime_multis[i] = 0.3
            elif adx_val > 20 and er_val > 0.3:
                regime_multis[i] = 1.0
            elif adx_val < 20 and er_val < 0.3:
                regime_multis[i] = 0.6
            else:
                regime_multis[i] = 0.8

        self._regime_multis = regime_multis
        backtester_self = self
        orig_open_trade = self._open_trade

        def full_v17_open_trade(direction, price, time, score, dd_pct,
                                 sl_points, tp_points, current_atr,
                                 lot_multiplier=1.0, component_mask=None,
                                 entry_type="normal", momentum_burst=False,
                                 entry_bar=0, bar_spread_points=None):
            """Combined Risk Manager + ML Regime lot control."""
            # --- ML Regime multiplier ---
            ml_multi = backtester_self._ml_lot_multi
            lot_multiplier *= ml_multi
            if lot_multiplier <= 0:
                return  # Crash regime -> block

            # --- Portfolio risk check ---
            total_exposure_jpy = 0
            for pos in backtester_self.open_positions:
                pos_risk = pos["sl_points"] * backtester_self.cfg.POINT * \
                           backtester_self.cfg.CONTRACT_SIZE * pos["lot"] * 150.0
                total_exposure_jpy += pos_risk

            max_risk_jpy = backtester_self.balance * backtester_self._max_portfolio_risk_pct / 100.0
            if total_exposure_jpy > 0:
                remaining_budget = max(0, max_risk_jpy - total_exposure_jpy)
                new_trade_risk = sl_points * backtester_self.cfg.POINT * \
                                 backtester_self.cfg.CONTRACT_SIZE * backtester_self.cfg.MIN_LOT * 150.0
                if new_trade_risk > 0 and remaining_budget < new_trade_risk:
                    return
                if remaining_budget < max_risk_jpy:
                    risk_scale = remaining_budget / max_risk_jpy
                    lot_multiplier *= max(0.3, risk_scale)

            # --- Stricter spread validation ---
            if bar_spread_points is not None and not np.isnan(bar_spread_points):
                if bar_spread_points > backtester_self._max_spread_strict:
                    return

            orig_open_trade(direction, price, time, score, dd_pct,
                           sl_points, tp_points, current_atr,
                           lot_multiplier, component_mask,
                           entry_type, momentum_burst, entry_bar,
                           bar_spread_points)

        self._open_trade = full_v17_open_trade

        # Hook manage_positions for regime multiplier tracking
        _orig_manage = self._manage_positions

        def hooked_manage(high, low, close, time, bar_idx, m15_df_inner,
                          bar_open=None, bar_spread_points=None):
            if bar_idx < len(backtester_self._regime_multis):
                backtester_self._ml_lot_multi = backtester_self._regime_multis[bar_idx]
            _orig_manage(high, low, close, time, bar_idx, m15_df_inner,
                        bar_open=bar_open, bar_spread_points=bar_spread_points)

        self._manage_positions = hooked_manage

        try:
            super().run(h4_df, h1_df, m15_df, usdjpy_df=usdjpy_df)
        finally:
            self._open_trade = orig_open_trade
            self._manage_positions = _orig_manage
            self.cfg.MAX_DYNAMIC_SPREAD = orig_max_spread


# ============================================================
# Run single walk
# ============================================================
def run_walk(bt_class, cfg, walk):
    """Run backtest on a single walk period using specified backtester class."""
    start, end = walk['start'], walk['end']
    lookback_start = start - pd.Timedelta(days=60)

    m15_slice = m15_global[(m15_global.index >= lookback_start) & (m15_global.index < end)].copy()
    h1_slice = h1_global[(h1_global.index >= lookback_start) & (h1_global.index < end)].copy()
    h4_slice = h4_global[(h4_global.index >= lookback_start - pd.Timedelta(days=30)) & (h4_global.index < end)].copy()
    uj_slice = usdjpy_global[(usdjpy_global.index >= lookback_start) & (usdjpy_global.index < end)].copy() if usdjpy_global is not None else None

    if len(m15_slice) < 200 or len(h1_slice) < 50 or len(h4_slice) < 20:
        return {
            "name": walk['name'], "pf": 0, "trades": 0, "wins": 0,
            "losses": 0, "win_rate": 0, "pnl": 0, "pass": False,
            "gross_profit": 0, "gross_loss": 0, "avg_win": 0,
            "avg_loss": 0, "max_dd_pct": 0,
        }

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        bt = bt_class(cfg)
        bt.run(h4_slice, h1_slice, m15_slice, usdjpy_df=uj_slice)
    finally:
        sys.stdout = old_stdout

    # Filter trades to walk period only
    walk_trades = [t for t in bt.trades
                   if pd.Timestamp(t['open_time']) >= start
                   and pd.Timestamp(t['open_time']) < end]

    if not walk_trades:
        return {
            "name": walk['name'], "pf": 0, "trades": 0, "wins": 0,
            "losses": 0, "win_rate": 0, "pnl": 0, "pass": False,
            "gross_profit": 0, "gross_loss": 0, "avg_win": 0,
            "avg_loss": 0, "max_dd_pct": 0,
        }

    wins = [t for t in walk_trades if t['pnl_jpy'] > 0]
    losses = [t for t in walk_trades if t['pnl_jpy'] <= 0]
    gross_profit = sum(t['pnl_jpy'] for t in wins)
    gross_loss = sum(abs(t['pnl_jpy']) for t in losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0)
    total_pnl = sum(t['pnl_jpy'] for t in walk_trades)
    wr = len(wins) / len(walk_trades) * 100 if walk_trades else 0
    avg_win = np.mean([t['pnl_jpy'] for t in wins]) if wins else 0
    avg_loss = np.mean([abs(t['pnl_jpy']) for t in losses]) if losses else 0

    # Calculate max DD from equity curve within walk period
    max_dd_pct = 0
    if bt.equity_curve:
        eq_df = pd.DataFrame(bt.equity_curve)
        eq_walk = eq_df[(eq_df['time'] >= start) & (eq_df['time'] < end)]
        if len(eq_walk) > 0:
            peak = eq_walk['equity'].cummax()
            dd = (peak - eq_walk['equity']) / peak * 100
            max_dd_pct = dd.max()

    is_pass = pf >= 1.3 and len(walk_trades) >= 3

    return {
        "name": walk['name'],
        "trades": len(walk_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": wr,
        "pf": pf,
        "pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_dd_pct": max_dd_pct,
        "pass": is_pass,
    }


# ============================================================
# Run full WFA for a configuration
# ============================================================
def run_full_wfa(bt_class, cfg, config_name):
    """Run 16-quarter WFA and return results."""
    print(f"\n{'='*110}")
    print(f"  {config_name}")
    print(f"{'='*110}")

    results = []
    t0 = time_mod.time()
    for w in walks:
        r = run_walk(bt_class, cfg, w)
        results.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        pf_str = f"{r['pf']:5.2f}" if r['pf'] < 100 else " INF "
        print(f"  {r['name']}: PF={pf_str}  WR={r['win_rate']:5.1f}%  "
              f"T={r['trades']:3d} ({r['wins']}W/{r['losses']}L)  "
              f"PnL={r['pnl']:+10,.0f}  DD={r['max_dd_pct']:5.1f}%  [{status}]",
              flush=True)

    elapsed = time_mod.time() - t0

    # Aggregate stats
    n_pass = sum(1 for r in results if r['pass'])
    total_trades = sum(r['trades'] for r in results)
    total_wins = sum(r['wins'] for r in results)
    total_losses = sum(r['losses'] for r in results)
    total_pnl = sum(r['pnl'] for r in results)
    total_gp = sum(r['gross_profit'] for r in results)
    total_gl = sum(r['gross_loss'] for r in results)
    agg_pf = total_gp / total_gl if total_gl > 0 else 999
    total_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    avg_win = np.mean([r['avg_win'] for r in results if r['avg_win'] > 0]) if any(r['avg_win'] > 0 for r in results) else 0
    avg_loss = np.mean([r['avg_loss'] for r in results if r['avg_loss'] > 0]) if any(r['avg_loss'] > 0 for r in results) else 0
    max_dd = max(r['max_dd_pct'] for r in results) if results else 0

    print(f"\n  SUMMARY: {n_pass}/{len(results)} PASS ({n_pass/len(results)*100:.0f}%)")
    print(f"  Aggregate PF: {agg_pf:.2f}")
    print(f"  Total Trades: {total_trades} ({total_wins}W / {total_losses}L)")
    print(f"  Win Rate: {total_wr:.1f}%")
    print(f"  Avg Win: {avg_win:+,.0f} JPY  |  Avg Loss: {avg_loss:,.0f} JPY")
    print(f"  Max DD: {max_dd:.1f}%")
    print(f"  Total PnL: {total_pnl:+,.0f} JPY")
    print(f"  Elapsed: {elapsed:.1f}s")

    return {
        "config_name": config_name,
        "results": results,
        "n_pass": n_pass,
        "n_total": len(results),
        "pass_rate": n_pass / len(results) * 100 if results else 0,
        "agg_pf": agg_pf,
        "total_trades": total_trades,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "win_rate": total_wr,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_dd": max_dd,
        "total_pnl": total_pnl,
    }


# ============================================================
# Config A: Baseline v9.3 (all v17 modules OFF)
# ============================================================
cfg_a = GoldConfig()
cfg_a.USE_INTRABAR_SIM = False
# Baseline uses standard GoldConfig defaults (which IS v9.3)
summary_a = run_full_wfa(GoldBacktesterFast, cfg_a,
                          "Config A: Baseline v9.3 (all v17 modules OFF)")


# ============================================================
# Config B: + Risk Manager
# ============================================================
cfg_b = GoldConfig()
cfg_b.USE_INTRABAR_SIM = False
summary_b = run_full_wfa(RiskManagerBacktester, cfg_b,
                          "Config B: + Risk Manager (stricter spread + portfolio risk)")


# ============================================================
# Config C: + ML Regime
# ============================================================
cfg_c = GoldConfig()
cfg_c.USE_INTRABAR_SIM = False
summary_c = run_full_wfa(MLRegimeBacktester, cfg_c,
                          "Config C: + ML Regime (ADX+ER+VolRatio+RSI)")


# ============================================================
# Config D: Full v17 (B + C)
# ============================================================
cfg_d = GoldConfig()
cfg_d.USE_INTRABAR_SIM = False
summary_d = run_full_wfa(FullV17Backtester, cfg_d,
                          "Config D: Full v17 (Risk Manager + ML Regime)")


# ============================================================
# COMPARISON TABLE
# ============================================================
print("\n\n")
print("=" * 130)
print("  ABLATION TEST COMPARISON TABLE")
print("=" * 130)

summaries = [summary_a, summary_b, summary_c, summary_d]
labels = [
    "A: Baseline v9.3",
    "B: +Risk Manager",
    "C: +ML Regime",
    "D: Full v17 (B+C)",
]

# Header
print(f"\n  {'Metric':<25}", end="")
for lbl in labels:
    print(f"  {lbl:>22}", end="")
print()
print(f"  {'-'*25}", end="")
for _ in labels:
    print(f"  {'-'*22}", end="")
print()

# Pass Rate
print(f"  {'WFA Pass Rate':<25}", end="")
for s in summaries:
    print(f"  {s['n_pass']:>2}/{s['n_total']:>2} ({s['pass_rate']:4.0f}%){' ':>8}", end="")
print()

# Aggregate PF
print(f"  {'Aggregate PF':<25}", end="")
for s in summaries:
    print(f"  {s['agg_pf']:>22.2f}", end="")
print()

# Total Trades
print(f"  {'Total Trades':<25}", end="")
for s in summaries:
    print(f"  {s['total_trades']:>22,}", end="")
print()

# Win Rate
print(f"  {'Win Rate':<25}", end="")
for s in summaries:
    print(f"  {s['win_rate']:>21.1f}%", end="")
print()

# Avg Win (JPY)
print(f"  {'Avg Win (JPY)':<25}", end="")
for s in summaries:
    print(f"  {s['avg_win']:>+21,.0f}", end="")
print()

# Avg Loss (JPY)
print(f"  {'Avg Loss (JPY)':<25}", end="")
for s in summaries:
    print(f"  {s['avg_loss']:>21,.0f}", end="")
print()

# Max DD
print(f"  {'Max DD (%)':<25}", end="")
for s in summaries:
    print(f"  {s['max_dd']:>21.1f}%", end="")
print()

# Total PnL
print(f"  {'Total PnL (JPY)':<25}", end="")
for s in summaries:
    print(f"  {s['total_pnl']:>+21,.0f}", end="")
print()

# Separator
print(f"\n  {'-'*25}", end="")
for _ in labels:
    print(f"  {'-'*22}", end="")
print()

# Delta vs Baseline
print(f"\n  {'--- Delta vs A ---':<25}")
for metric, key, fmt in [
    ("PF delta", "agg_pf", "+.2f"),
    ("Trades delta", "total_trades", "+,d"),
    ("WR delta (pp)", "win_rate", "+.1f"),
    ("PnL delta (JPY)", "total_pnl", "+,.0f"),
    ("DD delta (pp)", "max_dd", "+.1f"),
]:
    print(f"  {metric:<25}", end="")
    base = summaries[0][key]
    for s in summaries:
        delta = s[key] - base
        if fmt == "+.2f":
            print(f"  {delta:>+22.2f}", end="")
        elif fmt == "+,d":
            print(f"  {int(delta):>+22,}", end="")
        elif fmt == "+.1f":
            print(f"  {delta:>+22.1f}", end="")
        elif fmt == "+,.0f":
            print(f"  {delta:>+22,.0f}", end="")
    print()


# ============================================================
# Per-Quarter Comparison
# ============================================================
print(f"\n\n{'='*130}")
print(f"  PER-QUARTER PF COMPARISON")
print(f"{'='*130}")

print(f"\n  {'Quarter':<10}", end="")
for lbl in labels:
    print(f"  {lbl:>22}", end="")
print(f"  {'Best':>8}")
print(f"  {'-'*10}", end="")
for _ in labels:
    print(f"  {'-'*22}", end="")
print(f"  {'-'*8}")

for qi in range(len(walks)):
    qname = walks[qi]['name']
    pfs = [s['results'][qi]['pf'] for s in summaries]
    best_idx = np.argmax(pfs)
    print(f"  {qname:<10}", end="")
    for si, s in enumerate(summaries):
        r = s['results'][qi]
        pf_str = f"{r['pf']:.2f}" if r['pf'] < 100 else "INF"
        marker = "*" if r['pass'] else " "
        tag = " <-BEST" if si == best_idx else ""
        print(f"  {pf_str + marker:>22}", end="")
    print(f"  {labels[best_idx].split(':')[0]:>8}")


# ============================================================
# Module Contribution Summary
# ============================================================
print(f"\n\n{'='*130}")
print(f"  MODULE CONTRIBUTION SUMMARY")
print(f"{'='*130}")

def contribution(base, module):
    """Calculate contribution of a module relative to baseline."""
    pf_delta = module['agg_pf'] - base['agg_pf']
    pnl_delta = module['total_pnl'] - base['total_pnl']
    pass_delta = module['n_pass'] - base['n_pass']
    wr_delta = module['win_rate'] - base['win_rate']
    dd_delta = module['max_dd'] - base['max_dd']
    return pf_delta, pnl_delta, pass_delta, wr_delta, dd_delta

print(f"\n  {'Module':<35} {'PF delta':>10} {'PnL delta':>15} {'Pass delta':>12} {'WR delta':>10} {'DD delta':>10}")
print(f"  {'-'*35} {'-'*10} {'-'*15} {'-'*12} {'-'*10} {'-'*10}")

# B vs A: Risk Manager contribution
pf_d, pnl_d, pass_d, wr_d, dd_d = contribution(summary_a, summary_b)
print(f"  {'Risk Manager (B vs A)':<35} {pf_d:>+10.2f} {pnl_d:>+15,.0f} {pass_d:>+12d} {wr_d:>+10.1f} {dd_d:>+10.1f}")

# C vs A: ML Regime contribution
pf_d, pnl_d, pass_d, wr_d, dd_d = contribution(summary_a, summary_c)
print(f"  {'ML Regime (C vs A)':<35} {pf_d:>+10.2f} {pnl_d:>+15,.0f} {pass_d:>+12d} {wr_d:>+10.1f} {dd_d:>+10.1f}")

# D vs A: Combined contribution
pf_d, pnl_d, pass_d, wr_d, dd_d = contribution(summary_a, summary_d)
print(f"  {'Combined (D vs A)':<35} {pf_d:>+10.2f} {pnl_d:>+15,.0f} {pass_d:>+12d} {wr_d:>+10.1f} {dd_d:>+10.1f}")

# Interaction effect: D - (B + C - A)  [shows if modules interact positively or negatively]
interaction_pf = summary_d['agg_pf'] - (summary_b['agg_pf'] + summary_c['agg_pf'] - summary_a['agg_pf'])
interaction_pnl = summary_d['total_pnl'] - (summary_b['total_pnl'] + summary_c['total_pnl'] - summary_a['total_pnl'])
interaction_pass = summary_d['n_pass'] - (summary_b['n_pass'] + summary_c['n_pass'] - summary_a['n_pass'])
print(f"\n  Interaction effect (D - B - C + A):")
print(f"    PF:   {interaction_pf:+.2f} ({'synergy' if interaction_pf > 0 else 'interference' if interaction_pf < 0 else 'neutral'})")
print(f"    PnL:  {interaction_pnl:+,.0f} JPY")
print(f"    Pass: {interaction_pass:+d} quarters")

elapsed_total = time_mod.time() - t_global
print(f"\n{'='*130}")
print(f"  Total elapsed: {elapsed_total:.1f}s")
print(f"{'='*130}")
