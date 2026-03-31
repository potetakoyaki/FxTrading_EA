# MEDIUM Severity Fixes (#15-#26) for AntigravityMTF_EA_Gold.mq5

## Fix #15: Component Effectiveness half-decay distorts win rates (line ~2010-2011)
**Problem**: `(g_compWins[c] + 1) / 2` adds a +1 bias that inflates win rates during decay.
**Fix**: Use proper halving without the +1 bias: `g_compWins[c] / 2` and `g_compTotal[c] / 2`.

## Fix #16: Channel regression calls iClose 40 times in a loop (line ~1192-1211)
**Problem**: `GetChannelSignal()` calls `iClose(_Symbol, PERIOD_H1, i)` individually 40x2 times in two loops, which is slow.
**Fix**: Use `CopyClose()` to batch-fetch all 40 close prices once, then index the array.

## Fix #17: GetSRSignal ArrayResize grows 1 element at a time (line ~1362)
**Problem**: `ArrayResize(levels, levelCount)` is called for every new level found, causing O(n^2) memory allocations.
**Fix**: Pre-allocate with `ArrayResize(levels, 0, 50)` to reserve 50 elements upfront.

## Fix #18: Stale trade exit only when profitable (line ~2118)
**Problem**: `CheckStaleTradeExit()` only closes trades when `POSITION_PROFIT >= 0`, so deeply negative stale trades are never closed.
**Fix**: Add a second timeout at `StaleTradeHours * 2` (96 hours) that closes regardless of profit.

## Fix #19: HistoryDealSelect without HistorySelect (line ~1938)
**Problem**: `HistoryDealSelect(trans.deal)` is called without first calling `HistorySelect(0, TimeCurrent())` to load deal history.
**Fix**: Add `HistorySelect(0, TimeCurrent())` before `HistoryDealSelect`.

## Fix #20: peakBalance resets on EA restart (line ~245, 292)
**Problem**: `peakBalance` is initialized to current balance on every restart, losing the historical peak.
**Fix**: Save/load `peakBalance` using `GlobalVariable` with key `AGMTF_{MagicNumber}_peakBal`.

## Fix #21: lastBarTime resets on restart (line ~250, 480)
**Problem**: `lastBarTime` resets to 0 on restart, potentially allowing a duplicate entry on the current bar.
**Fix**: Not critical since `CountMyPositions()` guards against double entry. Added comment documenting this.

## Fix #22: lastSLTime resets on restart (line ~251, 497)
**Problem**: `lastSLTime` resets to 0 on restart, losing the cooldown timer.
**Fix**: Save/load using `GlobalVariable` with key `AGMTF_{MagicNumber}_lastSL`.

## Fix #23: g_dailyPnL inaccurate tracking (line ~1979)
**Problem**: `g_dailyPnL` only accumulates from `OnTradeTransaction` and resets on day change via `g_lastDay`, but the initial daily PnL is not reconstructed on restart.
**Fix**: Added comment documenting the limitation. Added daily PnL reset check in OnTick date-change block (already exists). No further fix needed since the existing date-change check in OnTick is correct.

## Fix #24: SlippagePoints declared but unused (line ~168, 291)
**Problem**: `SlippagePoints` is declared as `const double = 3.0` but `trade.SetDeviationInPoints(30)` uses a hardcoded 30.
**Fix**: Use `SlippagePoints` in the `SetDeviationInPoints` call: `trade.SetDeviationInPoints((int)SlippagePoints)`.

## Fix #25: componentMask bit 15 used as direction flag (line ~584)
**Problem**: Bit 15 is used as a direction flag (buy=set, sell=unset), but with 15 components (0-14), bit 15 could collide with a future component.
**Fix**: Use bit 16 instead of bit 15 for the direction flag, update all references.

## Fix #26: Reversal mode uses same SL/TP as main entry (line ~946-958)
**Problem**: Counter-trend reversal entries use the same SL/TP distances as trend-following entries, which is inappropriate for mean-reversion trades.
**Fix**: Add dedicated reversal SL/TP multipliers (0.8x SL, 0.6x TP) and apply them to reversal entries only.
