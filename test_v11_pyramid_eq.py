#!/usr/bin/env python3
"""
v11 Pyramid Equity Curve Filter Test

Concept: Track recent pyramid trade PnL separately.
If last N pyramids have negative average PnL, block new pyramids.
This preserves base trade frequency while stopping pyramid loss spirals.

Variants:
  baseline  - v10.0 current
  F  - Pyramid equity filter: last 5 pyramids avg < 0 → block
  G  - Pyramid equity filter: last 3 pyramids avg < 0 → block (more responsive)
  H  - Pyramid equity filter: last 5 pyramids, 4+ losers → block (win-rate based)
  I  - Pyramid equity filter: last 3 consecutive pyramid losses → block (streak)
  J  - F + lot reduction instead of block (soft version)

Usage:
  python3 test_v11_pyramid_eq.py <variant>
"""
import sys
import numpy as np
import pandas as pd

from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester


def load_data():
    h4 = load_csv('XAUUSD_H4.csv')
    h1_raw = load_csv('XAUUSD_H1.csv')
    h1 = merge_and_fill(h1_raw, generate_h1_from_h4(h4))
    m15_raw = load_csv('XAUUSD_M15.csv')
    m15 = merge_and_fill(m15_raw, generate_m15_from_h1(h1))
    usdjpy = load_csv('USDJPY_H1.csv')
    h4 = h4[h4.index >= '2024-01-01']
    h1 = h1[h1.index >= '2024-01-01']
    m15 = m15[m15.index >= '2024-01-01']
    usdjpy = usdjpy[usdjpy.index >= '2024-01-01']
    return h4, h1, m15, usdjpy


class PyramidEqBacktester(GoldBacktester):
    """Backtester with pyramid-specific equity curve filter."""

    def __init__(self, cfg, variant='baseline'):
        super().__init__(cfg)
        self.variant = variant
        self.pyramid_pnls = []  # Track pyramid trade results
        self.pyramid_blocks_by_eq = 0
        self.pyramid_lot_reductions = 0

    def _close_position(self, pos, exit_price, time, reason, bar_idx=None):
        """Override to track pyramid PnL separately."""
        entry_type = pos.get("entry_type", "normal")
        trades_before = len(self.trades)

        # Call parent (which appends to self.trades)
        super()._close_position(pos, exit_price, time, reason, bar_idx)

        # Track pyramid PnL from the trade record just added
        if entry_type == "pyramid" and len(self.trades) > trades_before:
            pnl_jpy = self.trades[-1]["pnl_jpy"]
            self.pyramid_pnls.append(pnl_jpy)

    def _should_block_pyramid(self):
        """Check if pyramids should be blocked based on recent pyramid performance."""
        variant = self.variant
        cfg = self.cfg

        if variant == 'F':
            # Last 5 pyramids avg PnL < 0 → block
            lookback = getattr(cfg, 'PYR_EQ_LOOKBACK', 5)
            if len(self.pyramid_pnls) >= lookback:
                recent = self.pyramid_pnls[-lookback:]
                if np.mean(recent) < 0:
                    return True
            return False

        elif variant == 'G':
            # Last 3 pyramids avg PnL < 0 → block (more responsive)
            lookback = getattr(cfg, 'PYR_EQ_LOOKBACK', 3)
            if len(self.pyramid_pnls) >= lookback:
                recent = self.pyramid_pnls[-lookback:]
                if np.mean(recent) < 0:
                    return True
            return False

        elif variant == 'H':
            # Last 5 pyramids, 4+ losers → block (win-rate based)
            lookback = getattr(cfg, 'PYR_EQ_LOOKBACK', 5)
            min_losers = getattr(cfg, 'PYR_EQ_MIN_LOSERS', 4)
            if len(self.pyramid_pnls) >= lookback:
                recent = self.pyramid_pnls[-lookback:]
                losers = sum(1 for p in recent if p <= 0)
                if losers >= min_losers:
                    return True
            return False

        elif variant == 'I':
            # Last 3 consecutive pyramid losses → block (streak-based)
            streak = getattr(cfg, 'PYR_EQ_STREAK', 3)
            if len(self.pyramid_pnls) >= streak:
                recent = self.pyramid_pnls[-streak:]
                if all(p <= 0 for p in recent):
                    return True
            return False

        elif variant == 'J':
            # Same as F but returns "reduce" instead of "block"
            return False  # Never hard block, lot reduction in _get_pyramid_lot_scale

        return False

    def _get_pyramid_lot_scale(self):
        """For variant J: get lot scale factor for pyramids."""
        if self.variant != 'J':
            return 1.0
        lookback = getattr(self.cfg, 'PYR_EQ_LOOKBACK', 5)
        if len(self.pyramid_pnls) >= lookback:
            recent = self.pyramid_pnls[-lookback:]
            if np.mean(recent) < 0:
                self.pyramid_lot_reductions += 1
                return 0.3  # Reduce to 30% lot
        return 1.0

    def run(self, h4_df, h1_df, m15_df, usdjpy_df=None):
        """Override run to inject pyramid equity curve filter into pyramid_ok check."""
        # We need to hook into the pyramid decision. Override _open_trade won't work
        # (as proven in previous tests). Instead, we'll monkey-patch the internal state.
        #
        # Strategy: Override the entire pyramid check by saving/restoring the
        # `open_positions` count check and adding our filter.
        #
        # Actually, the cleanest way is to override the parent's run by calling it
        # but intercepting at _open_trade for pyramid entries only.
        # The key difference from before: we're not changing scores, we're only
        # blocking/scaling pyramid entries based on pyramid-specific history.

        self._variant_active = True
        super().run(h4_df, h1_df, m15_df, usdjpy_df=usdjpy_df)

    def _open_trade(self, direction, price, time, score, current_dd,
                    sl_pts, tp_pts, current_atr, lot_multiplier,
                    component_mask, **kwargs):
        """Override to apply pyramid equity curve filter."""
        entry_type = kwargs.get('entry_type', 'normal')

        if entry_type == 'pyramid' and self.variant != 'baseline':
            # Check pyramid equity curve filter
            if self._should_block_pyramid():
                self.pyramid_blocks_by_eq += 1
                return  # BLOCK this pyramid

            # For variant J: reduce lot instead of blocking
            pyr_lot_scale = self._get_pyramid_lot_scale()
            if pyr_lot_scale < 1.0:
                lot_multiplier *= pyr_lot_scale

        super()._open_trade(direction, price, time, score, current_dd,
                           sl_pts, tp_pts, current_atr, lot_multiplier,
                           component_mask, **kwargs)


def run_backtest(variant):
    h4, h1, m15, usdjpy = load_data()
    cfg = GoldConfig()

    bt = PyramidEqBacktester(cfg, variant)
    bt.run(h4, h1, m15, usdjpy_df=usdjpy)

    # Report
    trades = bt.trades
    df = pd.DataFrame(trades)
    df['month'] = df['close_time'].apply(lambda x: x.month)
    df['year'] = df['close_time'].apply(lambda x: x.year)
    df['ym'] = df['close_time'].apply(lambda x: f'{x.year}-{x.month:02d}')

    total_pnl = df['pnl_jpy'].sum()
    total_trades = len(df)
    win_rate = (df['pnl_jpy'] > 0).mean() * 100
    gross_profit = df[df['pnl_jpy'] > 0]['pnl_jpy'].sum()
    gross_loss = abs(df[df['pnl_jpy'] <= 0]['pnl_jpy'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    equity = 300000; peak = equity; max_dd = 0
    for _, row in df.iterrows():
        equity += row['pnl_jpy']
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)

    monthly_pnl = df.groupby('ym')['pnl_jpy'].sum()
    sharpe = monthly_pnl.mean() / monthly_pnl.std() * np.sqrt(12) if monthly_pnl.std() > 0 else 0
    calmar = (total_pnl / 300000 * 100 / 2.2) / max_dd if max_dd > 0 else 0
    expectancy = total_pnl / total_trades if total_trades > 0 else 0

    # Jan-Feb 2026
    jf = df[(df['year'] == 2026) & (df['month'].isin([1, 2]))]
    jf_pnl = jf['pnl_jpy'].sum()
    jf_trades = len(jf)
    jf_wr = (jf['pnl_jpy'] > 0).mean() * 100 if len(jf) > 0 else 0
    jf_pyr = (jf['entry_type'] == 'pyramid').sum()

    # Pyramid stats
    all_pyr = df[df['entry_type'] == 'pyramid']
    pyr_pnl = all_pyr['pnl_jpy'].sum()
    pyr_count = len(all_pyr)
    pyr_wr = (all_pyr['pnl_jpy'] > 0).mean() * 100 if len(all_pyr) > 0 else 0

    print(f"\n{'='*60}")
    print(f"VARIANT: {variant}")
    print(f"{'='*60}")
    print(f"Total PnL:     {total_pnl:+,.0f} JPY")
    print(f"Total Trades:  {total_trades}")
    print(f"Win Rate:      {win_rate:.1f}%")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Max Drawdown:  {max_dd:.1f}%")
    print(f"Sharpe:        {sharpe:.2f}")
    print(f"Calmar:        {calmar:.2f}")
    print(f"Return:        {total_pnl/300000*100:+.1f}%")
    print(f"Expectancy:    {expectancy:+,.0f}/trade")

    print(f"\n--- Pyramid Stats ---")
    print(f"Total pyramids:    {pyr_count}")
    print(f"Pyramid PnL:       {pyr_pnl:+,.0f}")
    print(f"Pyramid WR:        {pyr_wr:.1f}%")
    print(f"Eq filter blocks:  {bt.pyramid_blocks_by_eq}")
    print(f"Lot reductions:    {bt.pyramid_lot_reductions}")

    print(f"\n--- Target: 2026 Jan-Feb ---")
    print(f"PnL:       {jf_pnl:+,.0f}")
    print(f"Trades:    {jf_trades}")
    print(f"Win Rate:  {jf_wr:.1f}%")
    print(f"Pyramids:  {jf_pyr}")

    # Key months
    print(f"\n--- Key months ---")
    for ym_target in ['2025-02', '2025-10', '2025-12', '2026-01', '2026-02', '2026-03']:
        sub = df[df['ym'] == ym_target]
        if len(sub) > 0:
            sp = sub['pnl_jpy'].sum()
            sn = len(sub)
            sw = (sub['pnl_jpy'] > 0).mean() * 100
            pyr = (sub['entry_type'] == 'pyramid').sum()
            print(f"  {ym_target}: {sn:3d} trades WR={sw:4.1f}% PnL={sp:+10,.0f} Pyr={pyr}")

    # Monthly PnL
    print(f"\n--- Monthly PnL ---")
    for ym, grp in df.groupby('ym'):
        pnl = grp['pnl_jpy'].sum()
        wr = (grp['pnl_jpy'] > 0).mean() * 100
        n = len(grp)
        pyr = (grp['entry_type'] == 'pyramid').sum()
        marker = ' <<<' if ym in ['2026-01', '2026-02'] else ''
        print(f"  {ym}: {n:3d} trades WR={wr:4.1f}% PnL={pnl:+10,.0f} Pyr={pyr}{marker}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 test_v11_pyramid_eq.py <variant>")
        print("Variants: baseline, F, G, H, I, J")
        sys.exit(1)
    variant = sys.argv[1].upper()
    if variant == 'BASELINE':
        variant = 'baseline'
    run_backtest(variant)
