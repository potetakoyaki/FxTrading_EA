"""Quick verification of v13 strategy at multiple risk levels."""
from backtest_alpha import (
    AlphaConfig, load_data, build_weekly, prepare_indicators,
    run_backtest, calc_metrics
)

DATA_DIR = "/tmp/FxTrading_EA_clone"
h4_raw, d1_raw = load_data(f"{DATA_DIR}/XAUUSD_H4.csv", f"{DATA_DIR}/XAUUSD_D1.csv")
w1_raw = build_weekly(d1_raw)

# Best strategy from final_optimize
STRAT = dict(BodyRatio=0.34, EMA_Zone_ATR=0.40, ATR_Filter=0.35,
             D1_Tolerance=0.003, MaxPositions=3,
             BE_ATR=1.5, Trail_ATR=3.5, SL_ATR_Mult=2.5)

print(f"{'Risk%':>6} {'MaxLot':>6} | {'Trades':>6} {'PF':>6} {'DD%':>6} "
      f"{'Daily':>8} {'OOS_PF':>7} {'OOS_Daily':>10} {'Net_JPY':>14}")
print("-" * 90)

for risk, mlot in [(0.18, 0.10), (0.5, 0.10), (0.75, 0.20), (1.0, 0.20),
                    (1.0, 0.30), (1.5, 0.30), (1.5, 0.50), (2.0, 0.30),
                    (2.0, 0.50), (2.5, 0.50), (3.0, 0.50), (3.0, 1.00)]:
    cfg = AlphaConfig(**STRAT, RiskPct=risk, MaxLot=mlot)
    h4, d1, w1 = prepare_indicators(h4_raw.copy(), d1_raw.copy(), w1_raw.copy(), cfg)

    t, dd = run_backtest(h4, d1, w1, cfg)
    m = calc_metrics(t, cfg, dd)

    t_o, dd_o = run_backtest(h4, d1, w1, cfg, start_date="2022-01-01")
    m_o = calc_metrics(t_o, cfg, dd_o)

    marker = " <<<" if m["daily_jpy"] >= 5000 else ""
    marker += " ***" if 3000 <= m["daily_jpy"] < 5000 else ""
    print(f"{risk:6.2f} {mlot:6.2f} | {m['trades']:6d} {m['pf']:6.3f} {m['dd']:6.1f} "
          f"{m['daily_jpy']:>8,} {m_o['pf']:>7.3f} {m_o['daily_jpy']:>10,} "
          f"{m['net_jpy']:>14,}{marker}")
