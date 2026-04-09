#!/usr/bin/env python3 -u
"""
Exit Management Diagnostic: 3 Tests
====================================
Test 1: SimpleExitMode (Fixed SL/TP only, NO BE/Trail/Chandelier/Partial)
Test 2: Partial Close OFF, BE/Trail ON (BE=0.5, Chand=1.5)
Test 3: BE raised to 1.5 (based on Test 1/2 results)

All tests: SL=1.2*ATR, TP=4.0*ATR, 16 quarterly WFA
Report: PF, WR%, avg_win, avg_loss, avg_win/avg_loss, total PnL,
        EV/trade, max consecutive losses, largest win/loss
"""
import sys
sys.stdout.reconfigure(line_buffering=True)
import os, io, time as time_mod
import warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/tmp/FxTrading_EA')
os.chdir('/tmp/FxTrading_EA')

# Force fresh imports
for mod in list(sys.modules.keys()):
    if 'backtest_gold' in mod or 'backtest_csv' in mod or 'indicators' in mod:
        del sys.modules[mod]

import pandas as pd, numpy as np
from backtest_csv import load_csv, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold_fast import GoldBacktesterFast
from backtest_gold import GoldConfig

# ============================================================
# Load data
# ============================================================
print("Loading data...", flush=True)
h4 = load_csv('XAUUSD_H4.csv')
h1_real = load_csv('XAUUSD_H1.csv')
h1 = merge_and_fill(h1_real, generate_h1_from_h4(h4))
m15_real = load_csv('XAUUSD_M15.csv')
m15 = merge_and_fill(m15_real, generate_m15_from_h1(h1))
usdjpy = load_csv('USDJPY_H1.csv')
print(f"M15: {len(m15):,} bars  ({m15.index[0]} ~ {m15.index[-1]})", flush=True)

# ============================================================
# Build 16 quarterly walk-forward windows
# ============================================================
walks = []
for year in range(2022, 2026):
    for q in range(1, 5):
        ms = (q - 1) * 3 + 1
        me = q * 3
        start = pd.Timestamp(f"{year}-{ms:02d}-01")
        if me == 12:
            end = pd.Timestamp(f"{year+1}-01-01")
        else:
            end = pd.Timestamp(f"{year}-{me+1:02d}-01")
        if start >= m15.index[0] and start < m15.index[-1]:
            walks.append({"name": f"{year}-Q{q}", "start": start, "end": end})
walks = walks[:16]
print(f"Walks: {len(walks)}, {walks[0]['name']} to {walks[-1]['name']}", flush=True)

# ============================================================
# Pre-slice data for each walk
# ============================================================
print("Pre-slicing data...", flush=True)
WD = []
for w in walks:
    lb = w['start'] - pd.Timedelta(days=60)
    WD.append({
        'm15': m15[(m15.index >= lb) & (m15.index < w['end'])].copy(),
        'h1': h1[(h1.index >= lb) & (h1.index < w['end'])].copy(),
        'h4': h4[(h4.index >= lb - pd.Timedelta(days=30)) & (h4.index < w['end'])].copy(),
        'uj': usdjpy[(usdjpy.index >= lb) & (usdjpy.index < w['end'])].copy() if usdjpy is not None else None,
        's': w['start'], 'e': w['end'], 'name': w['name'],
    })


# ============================================================
# Run single walk-forward window (uses standard GoldBacktesterFast)
# ============================================================
def run_walk(cfg, wd):
    """Run one OOS quarter, return detailed stats dict."""
    if len(wd['m15']) < 200:
        return {"pf": 0, "trades": 0, "pass": False, "wins": 0, "losses": 0,
                "avg_win": 0, "avg_loss": 0, "total_pnl": 0, "win_rate": 0,
                "walk_trades": [], "name": wd['name']}

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        bt = GoldBacktesterFast(cfg)
        bt.run(wd['h4'], wd['h1'], wd['m15'], usdjpy_df=wd['uj'])
    finally:
        sys.stdout = old_stdout

    # Filter trades to OOS window only
    wt = [t for t in bt.trades
          if pd.Timestamp(t['open_time']) >= wd['s']
          and pd.Timestamp(t['open_time']) < wd['e']]

    # Filter NaN/inf
    wt = [t for t in wt if not (np.isnan(t['pnl_jpy']) or np.isinf(t['pnl_jpy']))]

    if not wt:
        return {"pf": 0, "trades": 0, "pass": False, "wins": 0, "losses": 0,
                "avg_win": 0, "avg_loss": 0, "total_pnl": 0, "win_rate": 0,
                "walk_trades": [], "name": wd['name']}

    win_pnls = [t['pnl_jpy'] for t in wt if t['pnl_jpy'] > 0]
    loss_pnls = [t['pnl_jpy'] for t in wt if t['pnl_jpy'] <= 0]
    gross_win = sum(win_pnls)
    gross_loss = abs(sum(loss_pnls))
    pf = gross_win / gross_loss if gross_loss > 0 else 999.0
    avg_win = np.mean(win_pnls) if win_pnls else 0
    avg_loss = np.mean([abs(x) for x in loss_pnls]) if loss_pnls else 0
    wr = len(win_pnls) / len(wt) * 100 if wt else 0

    # Exit reason breakdown
    tp_exits = sum(1 for t in wt if t.get('reason') == 'TP')
    sl_exits = sum(1 for t in wt if t.get('reason') == 'SL')
    partial_exits = sum(1 for t in wt if t.get('reason') == 'Partial')
    other_exits = len(wt) - tp_exits - sl_exits - partial_exits

    return {
        "pf": pf,
        "trades": len(wt),
        "pass": pf >= 1.30 and len(wt) >= 3,
        "wins": len(win_pnls),
        "losses": len(loss_pnls),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "total_pnl": sum(t['pnl_jpy'] for t in wt),
        "win_rate": wr,
        "walk_trades": wt,
        "name": wd['name'],
        "tp_exits": tp_exits,
        "sl_exits": sl_exits,
        "partial_exits": partial_exits,
        "other_exits": other_exits,
    }


# ============================================================
# Run full WFA and compute detailed metrics
# ============================================================
def run_full_wfa(cfg, label):
    """Run full WFA (16 quarters), return detailed summary."""
    t0 = time_mod.time()
    results = []
    for wd in WD:
        r = run_walk(cfg, wd)
        results.append(r)
    elapsed = time_mod.time() - t0

    n_pass = sum(1 for r in results if r['pass'])

    # Collect ALL individual trades across all quarters
    all_trades_pnl = []
    all_win_pnls = []
    all_loss_pnls = []
    for r in results:
        for t in r['walk_trades']:
            all_trades_pnl.append(t['pnl_jpy'])
            if t['pnl_jpy'] > 0:
                all_win_pnls.append(t['pnl_jpy'])
            else:
                all_loss_pnls.append(t['pnl_jpy'])

    total_trades = len(all_trades_pnl)
    total_wins = len(all_win_pnls)
    total_losses = len(all_loss_pnls)
    total_pnl = sum(all_trades_pnl) if all_trades_pnl else 0
    win_rate = total_wins / total_trades if total_trades > 0 else 0

    grand_avg_win = np.mean(all_win_pnls) if all_win_pnls else 0
    grand_avg_loss = np.mean([abs(x) for x in all_loss_pnls]) if all_loss_pnls else 0
    rr_ratio = grand_avg_win / grand_avg_loss if grand_avg_loss > 0 else 0

    gross_win = sum(all_win_pnls) if all_win_pnls else 0
    gross_loss = abs(sum(all_loss_pnls)) if all_loss_pnls else 0
    total_pf = gross_win / gross_loss if gross_loss > 0 else 999.0

    # Expected Value per trade
    ev_per_trade = (win_rate * grand_avg_win) - ((1 - win_rate) * grand_avg_loss)

    # Max consecutive losses
    max_consec_loss = 0
    current_consec = 0
    for pnl in all_trades_pnl:
        if pnl <= 0:
            current_consec += 1
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_consec = 0

    # Largest single win and loss
    largest_win = max(all_win_pnls) if all_win_pnls else 0
    largest_loss = min(all_loss_pnls) if all_loss_pnls else 0  # most negative

    # Exit reason totals
    total_tp = sum(r['tp_exits'] for r in results)
    total_sl = sum(r['sl_exits'] for r in results)
    total_partial = sum(r['partial_exits'] for r in results)
    total_other = sum(r['other_exits'] for r in results)

    return {
        "label": label,
        "n_pass": n_pass,
        "n_walks": len(walks),
        "total_pf": total_pf,
        "total_trades": total_trades,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "win_rate": win_rate,
        "avg_win": grand_avg_win,
        "avg_loss": grand_avg_loss,
        "rr_ratio": rr_ratio,
        "total_pnl": total_pnl,
        "ev_per_trade": ev_per_trade,
        "max_consec_loss": max_consec_loss,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
        "elapsed": elapsed,
        "results": results,
        "total_tp": total_tp,
        "total_sl": total_sl,
        "total_partial": total_partial,
        "total_other": total_other,
    }


def print_wfa_result(s):
    """Print full WFA result with per-quarter breakdown."""
    print(f"\n{'='*130}", flush=True)
    print(f"  {s['label']}", flush=True)
    print(f"{'='*130}", flush=True)

    # Per-quarter detail
    print(f"  {'Quarter':<12s} {'PF':>6s} {'Trades':>7s} {'W':>4s} {'L':>4s} {'WR%':>6s} "
          f"{'AvgWin':>12s} {'AvgLoss':>12s} {'RR':>6s} {'PnL':>13s} {'TP':>4s} {'SL':>4s} {'Part':>4s} {'Oth':>4s} {'Pass':>5s}", flush=True)
    print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*4} {'-'*4} {'-'*6} "
          f"{'-'*12} {'-'*12} {'-'*6} {'-'*13} {'-'*4} {'-'*4} {'-'*4} {'-'*4} {'-'*5}", flush=True)

    for i, (w, r) in enumerate(zip(walks, s['results'])):
        wr = r['win_rate']
        rr = r['avg_win'] / r['avg_loss'] if r['avg_loss'] > 0 else 0
        p = 'PASS' if r['pass'] else 'fail'
        print(f"  {w['name']:<12s} {r['pf']:>6.2f} {r['trades']:>7d} {r['wins']:>4d} {r['losses']:>4d} {wr:>5.1f}% "
              f"{r['avg_win']:>+11,.0f} {r['avg_loss']:>11,.0f} {rr:>6.2f} {r['total_pnl']:>+12,.0f} "
              f"{r.get('tp_exits',0):>4d} {r.get('sl_exits',0):>4d} {r.get('partial_exits',0):>4d} {r.get('other_exits',0):>4d} {p:>5s}", flush=True)

    # Aggregate
    print(f"\n  --- AGGREGATE ---", flush=True)
    print(f"  WFA Pass Rate:     {s['n_pass']}/{s['n_walks']}", flush=True)
    print(f"  Total PF:          {s['total_pf']:.2f}", flush=True)
    print(f"  Total Trades:      {s['total_trades']} (W:{s['total_wins']} L:{s['total_losses']})", flush=True)
    print(f"  Win Rate:          {s['win_rate']*100:.1f}%", flush=True)
    print(f"  Avg Win (JPY):     {s['avg_win']:+,.0f}", flush=True)
    print(f"  Avg Loss (JPY):    {s['avg_loss']:,.0f}", flush=True)
    print(f"  Avg Win / Avg Loss:{s['rr_ratio']:.2f}  (reward:risk)", flush=True)
    print(f"  Total PnL (JPY):   {s['total_pnl']:+,.0f}", flush=True)
    print(f"  EV per trade:      {s['ev_per_trade']:+,.0f} JPY", flush=True)
    print(f"  Max Consec Losses: {s['max_consec_loss']}", flush=True)
    print(f"  Largest Win:       {s['largest_win']:+,.0f} JPY", flush=True)
    print(f"  Largest Loss:      {s['largest_loss']:+,.0f} JPY", flush=True)
    tp_pct = s['total_tp'] / s['total_trades'] * 100 if s['total_trades'] > 0 else 0
    sl_pct = s['total_sl'] / s['total_trades'] * 100 if s['total_trades'] > 0 else 0
    part_pct = s['total_partial'] / s['total_trades'] * 100 if s['total_trades'] > 0 else 0
    oth_pct = s['total_other'] / s['total_trades'] * 100 if s['total_trades'] > 0 else 0
    print(f"  Exit Breakdown:    TP={s['total_tp']}({tp_pct:.1f}%)  SL={s['total_sl']}({sl_pct:.1f}%)  "
          f"Partial={s['total_partial']}({part_pct:.1f}%)  Other={s['total_other']}({oth_pct:.1f}%)", flush=True)
    print(f"  Elapsed:           {s['elapsed']:.0f}s", flush=True)


# ============================================================
# TEST 1: SimpleExitMode = True (Fixed SL/TP only)
# NO breakeven, NO trailing, NO chandelier, NO partial close
# SL = 1.2*ATR, TP = 4.0*ATR (fixed)
# ============================================================
print(f"\n\n{'#'*130}", flush=True)
print(f"#  TEST 1: SIMPLE EXIT MODE (Fixed SL/TP ONLY)", flush=True)
print(f"#  SL = 1.2 * ATR, TP = 4.0 * ATR", flush=True)
print(f"#  NO Breakeven, NO Trailing, NO Chandelier, NO Partial Close", flush=True)
print(f"{'#'*130}", flush=True)

cfg1 = GoldConfig()
cfg1.SL_ATR_MULTI = 1.2
cfg1.TP_ATR_MULTI = 4.0
cfg1.BE_ATR_MULTI = 9999.0         # Effectively disable BE
cfg1.TRAIL_ATR_MULTI = 9999.0      # Effectively disable trailing
cfg1.USE_CHANDELIER_EXIT = False    # Disable chandelier
cfg1.USE_PARTIAL_CLOSE = False      # Disable partial close
cfg1.HIGH_VOL_SL_BONUS = 0.0       # No SL modification

test1 = run_full_wfa(cfg1, "TEST 1: SimpleExitMode (Fixed SL=1.2 TP=4.0, No BE/Trail/Chand/Partial)")
print_wfa_result(test1)


# ============================================================
# TEST 2: Partial Close OFF, BE/Trail/Chandelier ON
# SL = 1.2*ATR, TP = 4.0*ATR
# BE = 0.5*ATR, Chandelier = 1.5*ATR
# NO partial close
# ============================================================
print(f"\n\n{'#'*130}", flush=True)
print(f"#  TEST 2: BE/TRAIL/CHANDELIER ON, PARTIAL CLOSE OFF", flush=True)
print(f"#  SL = 1.2 * ATR, TP = 4.0 * ATR", flush=True)
print(f"#  BE = 0.5 * ATR, Trail = 1.0 * ATR, Chandelier = 1.5 * ATR", flush=True)
print(f"#  NO Partial Close", flush=True)
print(f"{'#'*130}", flush=True)

cfg2 = GoldConfig()
cfg2.SL_ATR_MULTI = 1.2
cfg2.TP_ATR_MULTI = 4.0
cfg2.BE_ATR_MULTI = 0.5            # Production value (v9.3)
cfg2.TRAIL_ATR_MULTI = 1.0         # Production value
cfg2.USE_CHANDELIER_EXIT = True     # Production value
cfg2.CHANDELIER_ATR_MULTI = 1.5    # Production value (v9.3)
cfg2.USE_PARTIAL_CLOSE = False      # DISABLED
cfg2.HIGH_VOL_SL_BONUS = 0.0

test2 = run_full_wfa(cfg2, "TEST 2: BE=0.5/Trail=1.0/Chand=1.5 ON, Partial OFF")
print_wfa_result(test2)


# ============================================================
# TEST 3: BE raised to 1.5 (less aggressive breakeven)
# Decision on partial close based on Test 2 results
# ============================================================
print(f"\n\n{'#'*130}", flush=True)
print(f"#  TEST 3: BE RAISED TO 1.5 (LESS AGGRESSIVE BREAKEVEN)", flush=True)
print(f"#  SL = 1.2 * ATR, TP = 4.0 * ATR", flush=True)
print(f"#  BE = 1.5 * ATR (was 0.5), Trail = 1.0 * ATR, Chandelier = 1.5 * ATR", flush=True)
print(f"#  Partial Close = OFF", flush=True)
print(f"{'#'*130}", flush=True)

cfg3 = GoldConfig()
cfg3.SL_ATR_MULTI = 1.2
cfg3.TP_ATR_MULTI = 4.0
cfg3.BE_ATR_MULTI = 1.5             # RAISED from 0.5 to 1.5
cfg3.TRAIL_ATR_MULTI = 1.0          # Production value
cfg3.USE_CHANDELIER_EXIT = True      # Production value
cfg3.CHANDELIER_ATR_MULTI = 1.5     # Production value
cfg3.USE_PARTIAL_CLOSE = False       # OFF
cfg3.HIGH_VOL_SL_BONUS = 0.0

test3 = run_full_wfa(cfg3, "TEST 3: BE=1.5/Trail=1.0/Chand=1.5 ON, Partial OFF")
print_wfa_result(test3)


# ============================================================
# COMPARISON TABLE
# ============================================================
print(f"\n\n{'='*140}", flush=True)
print(f"  COMPARISON: ALL 3 TESTS", flush=True)
print(f"{'='*140}", flush=True)

header = (f"  {'Test':<55s} {'WFA':>7s} {'PF':>6s} {'Trades':>7s} {'WR%':>6s} "
          f"{'AvgWin':>10s} {'AvgLoss':>10s} {'RR':>5s} {'EV/trade':>10s} "
          f"{'MaxConsL':>9s} {'PnL':>14s}")
print(header, flush=True)
divider = f"  {'-'*55} {'-'*7} {'-'*6} {'-'*7} {'-'*6} {'-'*10} {'-'*10} {'-'*5} {'-'*10} {'-'*9} {'-'*14}"
print(divider, flush=True)

for s in [test1, test2, test3]:
    lbl = s['label'][:55]
    wfa_str = f"{s['n_pass']}/{s['n_walks']}"
    pf_str = f"{s['total_pf']:.2f}" if s['total_pf'] < 999 else "INF"
    print(f"  {lbl:<55s} {wfa_str:>7s} {pf_str:>6s} {s['total_trades']:>7d} {s['win_rate']*100:>5.1f}% "
          f"{s['avg_win']:>+9,.0f} {s['avg_loss']:>9,.0f} {s['rr_ratio']:>5.2f} {s['ev_per_trade']:>+9,.0f} "
          f"{s['max_consec_loss']:>9d} {s['total_pnl']:>+13,.0f}", flush=True)


# ============================================================
# BASELINE (current production: v9.3 config) for reference
# ============================================================
print(f"\n\n{'#'*130}", flush=True)
print(f"#  BASELINE REFERENCE: Current Production Config (v9.3)", flush=True)
print(f"#  SL=1.2, TP=4.0, BE=0.5, Trail=1.0, Chand=1.5, Partial=ON", flush=True)
print(f"{'#'*130}", flush=True)

cfg_base = GoldConfig()
# All defaults (production v9.3)

baseline = run_full_wfa(cfg_base, "BASELINE: Production v9.3 (BE=0.5/Trail=1.0/Chand=1.5/Partial=ON)")
print_wfa_result(baseline)


# ============================================================
# FINAL COMPARISON: All 3 tests + baseline
# ============================================================
print(f"\n\n{'='*140}", flush=True)
print(f"  FINAL COMPARISON: 3 TESTS + BASELINE", flush=True)
print(f"{'='*140}", flush=True)

print(header, flush=True)
print(divider, flush=True)

for s in [test1, test2, test3, baseline]:
    lbl = s['label'][:55]
    wfa_str = f"{s['n_pass']}/{s['n_walks']}"
    pf_str = f"{s['total_pf']:.2f}" if s['total_pf'] < 999 else "INF"
    print(f"  {lbl:<55s} {wfa_str:>7s} {pf_str:>6s} {s['total_trades']:>7d} {s['win_rate']*100:>5.1f}% "
          f"{s['avg_win']:>+9,.0f} {s['avg_loss']:>9,.0f} {s['rr_ratio']:>5.2f} {s['ev_per_trade']:>+9,.0f} "
          f"{s['max_consec_loss']:>9d} {s['total_pnl']:>+13,.0f}", flush=True)


# ============================================================
# KEY DIAGNOSTIC INSIGHTS
# ============================================================
print(f"\n\n{'='*130}", flush=True)
print(f"  KEY DIAGNOSTIC INSIGHTS", flush=True)
print(f"{'='*130}", flush=True)

# Compare Test 1 vs Baseline: effect of ALL exit management
print(f"\n  1. EFFECT OF EXIT MANAGEMENT (Test 1 vs Baseline):", flush=True)
print(f"     Simple Exit:      WFA {test1['n_pass']}/{test1['n_walks']}, PF={test1['total_pf']:.2f}, RR={test1['rr_ratio']:.2f}, WR={test1['win_rate']*100:.1f}%, PnL={test1['total_pnl']:+,.0f}", flush=True)
print(f"     Production:       WFA {baseline['n_pass']}/{baseline['n_walks']}, PF={baseline['total_pf']:.2f}, RR={baseline['rr_ratio']:.2f}, WR={baseline['win_rate']*100:.1f}%, PnL={baseline['total_pnl']:+,.0f}", flush=True)
pf_delta = baseline['total_pf'] - test1['total_pf']
rr_delta = baseline['rr_ratio'] - test1['rr_ratio']
if pf_delta > 0:
    print(f"     --> Exit management ADDS value: PF +{pf_delta:.2f}, RR +{rr_delta:.2f}", flush=True)
else:
    print(f"     --> Exit management HURTS: PF {pf_delta:+.2f}, RR {rr_delta:+.2f}", flush=True)

# Compare Test 2 vs Baseline: effect of partial close alone
print(f"\n  2. EFFECT OF PARTIAL CLOSE (Test 2 vs Baseline):", flush=True)
print(f"     Without Partial:  WFA {test2['n_pass']}/{test2['n_walks']}, PF={test2['total_pf']:.2f}, RR={test2['rr_ratio']:.2f}, WR={test2['win_rate']*100:.1f}%, PnL={test2['total_pnl']:+,.0f}", flush=True)
print(f"     With Partial:     WFA {baseline['n_pass']}/{baseline['n_walks']}, PF={baseline['total_pf']:.2f}, RR={baseline['rr_ratio']:.2f}, WR={baseline['win_rate']*100:.1f}%, PnL={baseline['total_pnl']:+,.0f}", flush=True)
if baseline['total_pf'] > test2['total_pf']:
    print(f"     --> Partial close HELPS", flush=True)
else:
    print(f"     --> Partial close HURTS or negligible", flush=True)

# Compare Test 2 vs Test 3: effect of BE aggressiveness
print(f"\n  3. EFFECT OF BE AGGRESSIVENESS (Test 2 BE=0.5 vs Test 3 BE=1.5):", flush=True)
print(f"     Aggressive BE=0.5: WFA {test2['n_pass']}/{test2['n_walks']}, PF={test2['total_pf']:.2f}, RR={test2['rr_ratio']:.2f}, WR={test2['win_rate']*100:.1f}%, PnL={test2['total_pnl']:+,.0f}", flush=True)
print(f"     Relaxed BE=1.5:    WFA {test3['n_pass']}/{test3['n_walks']}, PF={test3['total_pf']:.2f}, RR={test3['rr_ratio']:.2f}, WR={test3['win_rate']*100:.1f}%, PnL={test3['total_pnl']:+,.0f}", flush=True)
if test3['rr_ratio'] > test2['rr_ratio']:
    print(f"     --> Relaxed BE allows bigger winners (RR improved: {test2['rr_ratio']:.2f} -> {test3['rr_ratio']:.2f})", flush=True)
else:
    print(f"     --> Relaxed BE does NOT improve RR ({test2['rr_ratio']:.2f} -> {test3['rr_ratio']:.2f})", flush=True)

# The core structural question: is BE=0.5 choking winners?
print(f"\n  4. STRUCTURAL DIAGNOSIS:", flush=True)
print(f"     {'Config':<50s} {'Avg Win':>12s} {'Avg Loss':>12s} {'RR':>6s} {'EV/trade':>12s}", flush=True)
print(f"     {'-'*50} {'-'*12} {'-'*12} {'-'*6} {'-'*12}", flush=True)
for s in [test1, test2, test3, baseline]:
    print(f"     {s['label'][:50]:<50s} {s['avg_win']:>+11,.0f} {s['avg_loss']:>11,.0f} {s['rr_ratio']:>6.2f} {s['ev_per_trade']:>+11,.0f}", flush=True)

best_rr = max([test1, test2, test3, baseline], key=lambda x: x['rr_ratio'])
best_pf = max([test1, test2, test3, baseline], key=lambda x: x['total_pf'])
best_wfa = max([test1, test2, test3, baseline], key=lambda x: x['n_pass'])
best_ev = max([test1, test2, test3, baseline], key=lambda x: x['ev_per_trade'])

print(f"\n     Best RR:    {best_rr['label'][:60]} (RR={best_rr['rr_ratio']:.2f})", flush=True)
print(f"     Best PF:    {best_pf['label'][:60]} (PF={best_pf['total_pf']:.2f})", flush=True)
print(f"     Best WFA:   {best_wfa['label'][:60]} ({best_wfa['n_pass']}/{best_wfa['n_walks']})", flush=True)
print(f"     Best EV:    {best_ev['label'][:60]} (EV={best_ev['ev_per_trade']:+,.0f})", flush=True)

print(f"\nDone.", flush=True)
