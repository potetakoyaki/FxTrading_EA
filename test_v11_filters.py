#!/usr/bin/env python3
"""
v11 A/B Test: Cross-market filters for high-volatility regime improvement.

Variants:
  baseline - v10.0 current
  A  - H1 ATR/Price ratio absolute volatility filter (score boost + pyramid block)
  B  - Widen ER threshold for TREND regime (0.3→0.4) to classify more bars as RANGE
  C  - Gold-USDJPY divergence filter (rolling correlation → lot scale + score boost)
  D  - Combined: ATR/Price + USDJPY divergence
  E  - ATR/Price only pyramid block (no score boost)

Usage:
  python3 test_v11_filters.py <variant>
"""
import sys
import numpy as np
import pandas as pd

from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester, calc_sma, calc_ema, calc_rsi, calc_adx, calc_bb, calc_atr, calc_channel_signal


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


def precompute_h1_atr_price(h1_df, period=14):
    """Pre-compute H1 ATR/Price ratio for each H1 bar."""
    atr = calc_atr(h1_df['High'], h1_df['Low'], h1_df['Close'], period)
    ratio = atr / h1_df['Close']
    return ratio


def precompute_usdjpy_corr(h1_df, usdjpy_df, period=100):
    """Pre-compute rolling Gold-USDJPY return correlation."""
    gold_ret = h1_df['Close'].pct_change()
    uj_ret = usdjpy_df['Close'].pct_change()
    combined = pd.DataFrame({'gold': gold_ret, 'uj': uj_ret}).dropna()
    roll_corr = combined['gold'].rolling(period).corr(combined['uj'])
    return roll_corr


class V11Backtester(GoldBacktester):
    """Backtester with v11 cross-market filter hooks in the run loop."""

    def __init__(self, cfg, variant='baseline'):
        super().__init__(cfg)
        self.variant = variant
        self.v11_stats = {
            'atr_price_score_boosts': 0,
            'atr_price_pyramid_blocks': 0,
            'usdjpy_div_score_boosts': 0,
            'usdjpy_div_lot_scales': 0,
            'er_threshold_range_upgrades': 0,
        }
        # Pre-computed signals (set before run)
        self._h1_atr_price = None
        self._usdjpy_corr = None

    def set_precomputed(self, h1_atr_price, usdjpy_corr):
        self._h1_atr_price = h1_atr_price
        self._usdjpy_corr = usdjpy_corr

    def _lookup_h1_atr_price(self, ct):
        """Get latest H1 ATR/Price ratio at or before time ct."""
        if self._h1_atr_price is None:
            return 0
        mask = self._h1_atr_price.index <= ct
        if mask.sum() == 0:
            return 0
        val = self._h1_atr_price[mask].iloc[-1]
        return val if pd.notna(val) else 0

    def _lookup_usdjpy_corr(self, ct):
        """Get latest USDJPY correlation at or before time ct."""
        if self._usdjpy_corr is None:
            return -0.3  # default healthy
        mask = self._usdjpy_corr.index <= ct
        if mask.sum() == 0:
            return -0.3
        val = self._usdjpy_corr[mask].iloc[-1]
        return val if pd.notna(val) else -0.3

    def detect_regime_v9(self, h4_er, vol_ratio):
        """Override for Variant B: widen ER threshold."""
        cfg = self.cfg
        if vol_ratio >= cfg.REGIME_VOL_CRASH:
            return 'crash'
        if vol_ratio >= cfg.REGIME_VOL_HIGH:
            return 'high_vol'

        # Variant B: use wider ER threshold
        er_threshold = getattr(cfg, 'V11_ER_THRESHOLD', cfg.REGIME_ER_TREND)

        if pd.notna(h4_er) and h4_er < er_threshold:
            if vol_ratio <= cfg.REGIME_VOL_RANGE_CAP:
                if er_threshold != cfg.REGIME_ER_TREND:
                    self.v11_stats['er_threshold_range_upgrades'] += 1
                return 'range'
            else:
                return 'high_vol'
        return 'trend'

    def _open_trade(self, direction, price, time, score, current_dd,
                    sl_pts, tp_pts, current_atr, lot_multiplier,
                    component_mask, **kwargs):
        """Override to apply v11 filters at entry time."""
        cfg = self.cfg
        entry_type = kwargs.get('entry_type', 'normal')
        is_pyramid = entry_type == 'pyramid'

        # --- Variant A / D / E: ATR/Price filter ---
        if getattr(cfg, 'V11_ATR_PRICE_FILTER', False):
            atr_price = self._lookup_h1_atr_price(time)
            threshold = getattr(cfg, 'V11_ATR_PRICE_THRESHOLD', 0.003)

            if atr_price > threshold:
                # Block pyramids in extreme absolute volatility
                if is_pyramid and getattr(cfg, 'V11_ATR_PRICE_PYRAMID_BLOCK', True):
                    self.v11_stats['atr_price_pyramid_blocks'] += 1
                    return  # BLOCK

                # Boost effective MIN_SCORE (block marginal entries)
                score_boost = getattr(cfg, 'V11_ATR_PRICE_SCORE_BOOST', 0)
                if score_boost > 0:
                    # Check if score minus boost still passes MIN_SCORE
                    # Use the regime-specific min_score
                    regime_min = getattr(cfg, 'TREND_MIN_SCORE', 9)  # conservative: use lowest
                    if score - score_boost < regime_min:
                        self.v11_stats['atr_price_score_boosts'] += 1
                        return  # BLOCK

        # --- Variant C / D: USDJPY divergence filter ---
        if getattr(cfg, 'V11_USDJPY_DIV_FILTER', False):
            corr = self._lookup_usdjpy_corr(time)
            corr_threshold = getattr(cfg, 'V11_USDJPY_CORR_THRESHOLD', -0.05)

            if corr > corr_threshold:
                # Correlation not negative enough = Gold-USDJPY divergence
                # Block pyramids during divergence
                if is_pyramid:
                    self.v11_stats['usdjpy_div_lot_scales'] += 1
                    return  # BLOCK

                # Reduce lot size
                lot_scale = getattr(cfg, 'V11_USDJPY_DIV_LOT_SCALE', 0.5)
                lot_multiplier *= lot_scale
                self.v11_stats['usdjpy_div_score_boosts'] += 1

                # Additional score boost check
                score_boost = getattr(cfg, 'V11_USDJPY_DIV_SCORE_BOOST', 0)
                if score_boost > 0:
                    regime_min = getattr(cfg, 'TREND_MIN_SCORE', 9)
                    if score - score_boost < regime_min:
                        return  # BLOCK

        # Pass through to parent
        super()._open_trade(direction, price, time, score, current_dd,
                           sl_pts, tp_pts, current_atr, lot_multiplier,
                           component_mask, **kwargs)


def run_backtest(variant):
    h4, h1, m15, usdjpy = load_data()
    cfg = GoldConfig()

    # Configure variant
    if variant == 'A':
        # ATR/Price absolute volatility: score boost + pyramid block
        cfg.V11_ATR_PRICE_FILTER = True
        cfg.V11_ATR_PRICE_THRESHOLD = 0.003  # 0.3% H1 ATR/Price
        cfg.V11_ATR_PRICE_SCORE_BOOST = 3
        cfg.V11_ATR_PRICE_PYRAMID_BLOCK = True
    elif variant == 'B':
        # Widen ER threshold: classify more bars as RANGE (no pyramids, higher min_score)
        cfg.V11_ER_THRESHOLD = 0.4  # was 0.3
    elif variant == 'C':
        # USDJPY divergence: lot reduction + pyramid block when corr breaks down
        cfg.V11_USDJPY_DIV_FILTER = True
        cfg.V11_USDJPY_CORR_THRESHOLD = -0.05
        cfg.V11_USDJPY_DIV_LOT_SCALE = 0.5
        cfg.V11_USDJPY_DIV_SCORE_BOOST = 2
    elif variant == 'D':
        # Combined A + C
        cfg.V11_ATR_PRICE_FILTER = True
        cfg.V11_ATR_PRICE_THRESHOLD = 0.003
        cfg.V11_ATR_PRICE_SCORE_BOOST = 2
        cfg.V11_ATR_PRICE_PYRAMID_BLOCK = True
        cfg.V11_USDJPY_DIV_FILTER = True
        cfg.V11_USDJPY_CORR_THRESHOLD = -0.05
        cfg.V11_USDJPY_DIV_LOT_SCALE = 0.6
        cfg.V11_USDJPY_DIV_SCORE_BOOST = 1
    elif variant == 'E':
        # ATR/Price pyramid-only block (no score boost)
        cfg.V11_ATR_PRICE_FILTER = True
        cfg.V11_ATR_PRICE_THRESHOLD = 0.003
        cfg.V11_ATR_PRICE_SCORE_BOOST = 0  # no score boost
        cfg.V11_ATR_PRICE_PYRAMID_BLOCK = True

    # Pre-compute signals
    h1_atr_price = precompute_h1_atr_price(h1)
    usdjpy_corr = precompute_usdjpy_corr(h1, usdjpy, period=100)

    # Debug: show ATR/Price ratio stats
    if variant in ['A', 'D', 'E', 'debug']:
        threshold = getattr(cfg, 'V11_ATR_PRICE_THRESHOLD', 0.003)
        for yr in [2024, 2025, 2026]:
            for m in [1, 2, 3, 10, 11, 12]:
                sub = h1_atr_price[(h1_atr_price.index.year == yr) & (h1_atr_price.index.month == m)]
                if len(sub) > 0:
                    pct_above = (sub > threshold).mean() * 100
                    if pct_above > 0 or (yr == 2026 and m <= 2):
                        print(f"  ATR/Price {yr}-{m:02d}: mean={sub.mean():.4f} max={sub.max():.4f} >{threshold}={pct_above:.0f}%")

    if variant in ['C', 'D', 'debug']:
        threshold = getattr(cfg, 'V11_USDJPY_CORR_THRESHOLD', -0.05)
        for yr in [2024, 2025, 2026]:
            for m in [1, 2, 3, 10, 11, 12]:
                sub = usdjpy_corr[(usdjpy_corr.index.year == yr) & (usdjpy_corr.index.month == m)]
                if len(sub) > 0:
                    pct_above = (sub > threshold).mean() * 100
                    if pct_above > 0 or (yr == 2026 and m <= 2):
                        print(f"  USDJPY corr {yr}-{m:02d}: mean={sub.mean():.3f} >{threshold}={pct_above:.0f}%")

    # Create backtester
    bt = V11Backtester(cfg, variant)
    bt.set_precomputed(h1_atr_price, usdjpy_corr)
    bt.run(h4, h1, m15, usdjpy_df=usdjpy)

    # Report results
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

    # Max DD
    equity = 300000
    peak = equity
    max_dd = 0
    for _, row in df.iterrows():
        equity += row['pnl_jpy']
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)

    # Sharpe
    monthly_pnl = df.groupby('ym')['pnl_jpy'].sum()
    sharpe = monthly_pnl.mean() / monthly_pnl.std() * np.sqrt(12) if monthly_pnl.std() > 0 else 0

    # Calmar
    annual_return = total_pnl / 300000 * 100 / 2.2  # ~2.2 years
    calmar = annual_return / max_dd if max_dd > 0 else 0

    # Expectancy
    expectancy = total_pnl / total_trades if total_trades > 0 else 0

    # Jan-Feb 2026
    jf = df[(df['year'] == 2026) & (df['month'].isin([1, 2]))]
    jf_pnl = jf['pnl_jpy'].sum()
    jf_trades = len(jf)
    jf_wr = (jf['pnl_jpy'] > 0).mean() * 100 if len(jf) > 0 else 0
    jf_pyr = (jf['entry_type'] == 'pyramid').sum() if 'entry_type' in jf.columns else 0

    # Other problem months for comparison
    dec25 = df[(df['year'] == 2025) & (df['month'] == 12)]
    oct25 = df[(df['year'] == 2025) & (df['month'] == 10)]
    feb25 = df[(df['year'] == 2025) & (df['month'] == 2)]

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

    print(f"\n--- Target: 2026 Jan-Feb ---")
    print(f"PnL:       {jf_pnl:+,.0f}")
    print(f"Trades:    {jf_trades}")
    print(f"Win Rate:  {jf_wr:.1f}%")
    print(f"Pyramids:  {jf_pyr}")

    print(f"\n--- Key months comparison ---")
    for label, sub in [('2025-02 (best)', feb25), ('2025-10 (best)', oct25),
                       ('2025-12', dec25), ('2026-01', jf[jf['month']==1]),
                       ('2026-02', jf[jf['month']==2])]:
        if len(sub) > 0:
            sp = sub['pnl_jpy'].sum()
            sn = len(sub)
            sw = (sub['pnl_jpy'] > 0).mean() * 100
            pyr = (sub['entry_type'] == 'pyramid').sum() if 'entry_type' in sub.columns else 0
            print(f"  {label:16s}: {sn:3d} trades WR={sw:4.1f}% PnL={sp:+10,.0f} Pyr={pyr}")

    print(f"\n--- Monthly PnL ---")
    for ym, grp in df.groupby('ym'):
        pnl = grp['pnl_jpy'].sum()
        wr = (grp['pnl_jpy'] > 0).mean() * 100
        n = len(grp)
        pyr = (grp['entry_type'] == 'pyramid').sum() if 'entry_type' in grp.columns else 0
        marker = ' <<<' if ym in ['2026-01', '2026-02'] else ''
        print(f"  {ym}: {n:3d} trades WR={wr:4.1f}% PnL={pnl:+10,.0f} Pyr={pyr}{marker}")

    print(f"\n--- V11 Filter Stats ---")
    for k, v in bt.v11_stats.items():
        if v > 0:
            print(f"  {k}: {v}")
    if all(v == 0 for v in bt.v11_stats.values()):
        print(f"  (no filters active)")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 test_v11_filters.py <variant>")
        print("Variants: baseline, A, B, C, D, E")
        sys.exit(1)

    variant = sys.argv[1].upper()
    if variant == 'BASELINE':
        variant = 'baseline'

    run_backtest(variant)
