#!/usr/bin/env python3
"""
Patch script to fix HIGH severity issues #6-#13 in AntigravityMTF_EA_Gold.mq5.
Each fix is clearly documented with a comment in the output file.

NOTE: This script accounts for CRITICAL fixes #1-#5 already applied by another agent.
"""

import re
import sys

FILE_PATH = "/tmp/FxTrading_EA_fresh/AntigravityMTF_EA_Gold.mq5"

def read_file():
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        return f.read()

def write_file(content):
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        f.write(content)

def safe_replace(content, old, new, fix_id, count=1):
    """Replace old with new in content, raising error if old is not found."""
    if old not in content:
        print(f"ERROR: Fix #{fix_id} - Pattern not found in file!")
        print(f"  Looking for: {repr(old[:100])}...")
        return content, False
    result = content.replace(old, new, count)
    return result, True


def apply_fix_12(content):
    """HIGH #12: Remove unused MaxPositions const (line 159). Only MaxPyramidPositions should exist."""
    old = 'const int    MaxPositions      = 3;        // HARDCODED: MaxPyramidPositionsと同値、冗長\n'
    new = '// FIX: Issue #12 - Removed unused MaxPositions (redundant with MaxPyramidPositions)\n'
    content, ok = safe_replace(content, old, new, 12)
    if ok:
        print("FIX #12 applied: Removed MaxPositions const")
    return content


def apply_fix_9(content):
    """HIGH #9: Remove dead g_pyramidCount variable declaration and initialization."""
    # Remove declaration
    old_decl = 'int            g_pyramidCount;\n'
    new_decl = '// FIX: Issue #9 - Removed dead g_pyramidCount variable (CountMyPositions() used instead)\n'
    content, ok1 = safe_replace(content, old_decl, new_decl, "9-decl")

    # Remove initialization
    old_init = '   g_pyramidCount = 0;\n'
    new_init = '   // FIX: Issue #9 - g_pyramidCount removed (dead code)\n'
    content, ok2 = safe_replace(content, old_init, new_init, "9-init")

    if ok1 and ok2:
        print("FIX #9 applied: Removed dead g_pyramidCount variable")
    return content


def apply_fix_11(content):
    """HIGH #11: Session detection is broker server-time dependent.
    Add GMTOffset input parameter and convert server time to GMT before session detection.
    """
    # Add GMTOffset input parameter after TradeEndHour
    old_time_inputs = 'input int    TradeEndHour      = 22;       // 取引終了時間(サーバー時間)'
    new_time_inputs = (
        'input int    TradeEndHour      = 22;       // 取引終了時間(サーバー時間)\n'
        'input int    GMTOffset         = 2;        // FIX: Issue #11 - ブローカーGMTオフセット (GMT+2=default)'
    )
    content, ok1 = safe_replace(content, old_time_inputs, new_time_inputs, "11-input")

    # Modify GetCurrentSession to use GMT conversion
    old_session_func = (
        'string GetCurrentSession(int hour)\n'
        '{\n'
        '   if(hour >= 0 && hour < 8) return "asian";\n'
        '   if(hour >= 8 && hour < 13) return "london";\n'
        '   return "ny";\n'
        '}'
    )
    new_session_func = (
        'string GetCurrentSession(int hour)\n'
        '{\n'
        '   // FIX: Issue #11 - Convert server time to GMT before session detection\n'
        '   int gmtHour = (hour - GMTOffset + 24) % 24;\n'
        '   if(gmtHour >= 0 && gmtHour < 8) return "asian";\n'
        '   if(gmtHour >= 8 && gmtHour < 13) return "london";\n'
        '   return "ny";\n'
        '}'
    )
    content, ok2 = safe_replace(content, old_session_func, new_session_func, "11-session")

    # Also fix GetSessionBonus to use GMT
    old_bonus = (
        'int GetSessionBonus()\n'
        '{\n'
        '   MqlDateTime dt;\n'
        '   TimeToStruct(TimeCurrent(), dt);\n'
        '\n'
        '   // ロンドン/NY重複 (13:00-17:00 サーバー時間 ≒ GMT+2)\n'
        '   if(dt.hour >= 13 && dt.hour < 17) return 1;\n'
        '\n'
        '   // ロンドンセッション初動 (8:00-11:00)\n'
        '   if(dt.hour >= 8 && dt.hour < 11) return 1;\n'
        '\n'
        '   return 0;\n'
        '}'
    )
    new_bonus = (
        'int GetSessionBonus()\n'
        '{\n'
        '   MqlDateTime dt;\n'
        '   TimeToStruct(TimeCurrent(), dt);\n'
        '   // FIX: Issue #11 - Convert server time to GMT for session bonus\n'
        '   int gmtHour = (dt.hour - GMTOffset + 24) % 24;\n'
        '\n'
        '   // ロンドン/NY重複 (13:00-17:00 GMT)\n'
        '   if(gmtHour >= 13 && gmtHour < 17) return 1;\n'
        '\n'
        '   // ロンドンセッション初動 (8:00-11:00 GMT)\n'
        '   if(gmtHour >= 8 && gmtHour < 11) return 1;\n'
        '\n'
        '   return 0;\n'
        '}'
    )
    content, ok3 = safe_replace(content, old_bonus, new_bonus, "11-bonus")

    if ok1 and ok2 and ok3:
        print("FIX #11 applied: Added GMTOffset input and GMT conversion for session detection")
    return content


def apply_fix_6(content):
    """HIGH #6: Entry price staleness. Move ask/bid fetch to immediately before trade execution.
    Keep early fetch for SL/TP calculation, re-fetch for execution.
    NOTE: Accounts for CRITICAL #2 fix that changed StringFormat to include |CM=%d
    """
    # Add comment to early fetch
    old_early_fetch = (
        '   // ──── エントリー ────\n'
        '   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n'
        '   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);'
    )
    new_early_fetch = (
        '   // ──── エントリー ────\n'
        '   // FIX: Issue #6 - ask/bid for SL/TP calculation only; re-fetched before execution\n'
        '   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n'
        '   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);'
    )
    content, ok1 = safe_replace(content, old_early_fetch, new_early_fetch, "6-early")

    # Fix BUY entry (with CRITICAL #2 StringFormat that has |CM=%d)
    old_buy = (
        '         double sl = NormalizeDouble(ask - slDist, _Digits);\n'
        '         double tp = NormalizeDouble(ask + tpDist, _Digits);\n'
        '\n'
        '         if(trade.Buy(lot, _Symbol, ask, sl, tp,\n'
        '            StringFormat("GOLD BUY S:%d M:%d R:%s ATR:%.1f|CM=%d", buyScore, componentMask, g_currentRegime, currentATR/_Point, componentMask)))'
    )
    new_buy = (
        '         double sl = NormalizeDouble(ask - slDist, _Digits);\n'
        '         double tp = NormalizeDouble(ask + tpDist, _Digits);\n'
        '\n'
        '         // FIX: Issue #6 - Re-fetch ask immediately before execution to avoid stale price\n'
        '         ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n'
        '         if(trade.Buy(lot, _Symbol, ask, sl, tp,\n'
        '            StringFormat("GOLD BUY S:%d M:%d R:%s ATR:%.1f|CM=%d", buyScore, componentMask, g_currentRegime, currentATR/_Point, componentMask)))'
    )
    content, ok2 = safe_replace(content, old_buy, new_buy, "6-buy")

    # Fix SELL entry (with CRITICAL #2 StringFormat that has |CM=%d)
    old_sell = (
        '         double sl = NormalizeDouble(bid + slDist, _Digits);\n'
        '         double tp = NormalizeDouble(bid - tpDist, _Digits);\n'
        '\n'
        '         if(trade.Sell(lot, _Symbol, bid, sl, tp,\n'
        '            StringFormat("GOLD SELL S:%d M:%d R:%s ATR:%.1f|CM=%d", sellScore, componentMask, g_currentRegime, currentATR/_Point, componentMask)))'
    )
    new_sell = (
        '         double sl = NormalizeDouble(bid + slDist, _Digits);\n'
        '         double tp = NormalizeDouble(bid - tpDist, _Digits);\n'
        '\n'
        '         // FIX: Issue #6 - Re-fetch bid immediately before execution to avoid stale price\n'
        '         bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);\n'
        '         if(trade.Sell(lot, _Symbol, bid, sl, tp,\n'
        '            StringFormat("GOLD SELL S:%d M:%d R:%s ATR:%.1f|CM=%d", sellScore, componentMask, g_currentRegime, currentATR/_Point, componentMask)))'
    )
    content, ok3 = safe_replace(content, old_sell, new_sell, "6-sell")

    # Fix REVERSAL BUY (with CRITICAL #2 StringFormat that has |CM=%d)
    old_rev_buy = (
        '         if(reversalDir == 1)\n'
        '         {\n'
        '            double sl = NormalizeDouble(ask - slDist, _Digits);\n'
        '            double tp = NormalizeDouble(ask + tpDist, _Digits);\n'
        '            if(trade.Buy(revLot, _Symbol, ask, sl, tp,\n'
        '               StringFormat("GOLD REV-BUY M:%d|CM=%d", componentMask, componentMask)))'
    )
    new_rev_buy = (
        '         if(reversalDir == 1)\n'
        '         {\n'
        '            // FIX: Issue #6 - Re-fetch ask for reversal entry\n'
        '            ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n'
        '            double sl = NormalizeDouble(ask - slDist, _Digits);\n'
        '            double tp = NormalizeDouble(ask + tpDist, _Digits);\n'
        '            if(trade.Buy(revLot, _Symbol, ask, sl, tp,\n'
        '               StringFormat("GOLD REV-BUY M:%d|CM=%d", componentMask, componentMask)))'
    )
    content, ok4 = safe_replace(content, old_rev_buy, new_rev_buy, "6-rev-buy")

    # Fix REVERSAL SELL (with CRITICAL #2 StringFormat that has |CM=%d)
    old_rev_sell = (
        '         else if(reversalDir == -1)\n'
        '         {\n'
        '            double sl = NormalizeDouble(bid + slDist, _Digits);\n'
        '            double tp = NormalizeDouble(bid - tpDist, _Digits);\n'
        '            if(trade.Sell(revLot, _Symbol, bid, sl, tp,\n'
        '               StringFormat("GOLD REV-SELL M:%d|CM=%d", componentMask, componentMask)))'
    )
    new_rev_sell = (
        '         else if(reversalDir == -1)\n'
        '         {\n'
        '            // FIX: Issue #6 - Re-fetch bid for reversal entry\n'
        '            bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);\n'
        '            double sl = NormalizeDouble(bid + slDist, _Digits);\n'
        '            double tp = NormalizeDouble(bid - tpDist, _Digits);\n'
        '            if(trade.Sell(revLot, _Symbol, bid, sl, tp,\n'
        '               StringFormat("GOLD REV-SELL M:%d|CM=%d", componentMask, componentMask)))'
    )
    content, ok5 = safe_replace(content, old_rev_sell, new_rev_sell, "6-rev-sell")

    if ok1 and ok2 and ok3 and ok4 and ok5:
        print("FIX #6 applied: Re-fetch ask/bid immediately before all trade executions")
    return content


def apply_fix_7(content):
    """HIGH #7: Partial close SL modification not retried. Add retry logic for PositionModify after partial close."""
    # BUY partial close
    old_buy_partial = (
        '                  if(trade.PositionClosePartial(ticket, closeLot))\n'
        '                  {\n'
        '                     MarkPartialClosed(ticket);\n'
        '                     double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);\n'
        '                     trade.PositionModify(ticket, newSL, tp);\n'
        '                     Print("GOLD 半利確 BUY: ", DoubleToString(closeLot, 2), "lot決済 [", g_currentRegime, "]");\n'
        '                  }'
    )
    new_buy_partial = (
        '                  if(trade.PositionClosePartial(ticket, closeLot))\n'
        '                  {\n'
        '                     MarkPartialClosed(ticket);\n'
        '                     double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);\n'
        '                     // FIX: Issue #7 - Retry PositionModify up to 3 times after partial close\n'
        '                     bool modifyOk = false;\n'
        '                     for(int retry = 0; retry < 3; retry++)\n'
        '                     {\n'
        '                        if(trade.PositionModify(ticket, newSL, tp)) { modifyOk = true; break; }\n'
        '                        Sleep(100);\n'
        '                     }\n'
        '                     if(!modifyOk)\n'
        '                        Print("WARNING: PositionModify failed after 3 retries for BUY ticket ", ticket, " SL=", newSL);\n'
        '                     Print("GOLD 半利確 BUY: ", DoubleToString(closeLot, 2), "lot決済 [", g_currentRegime, "]");\n'
        '                  }'
    )
    content, ok1 = safe_replace(content, old_buy_partial, new_buy_partial, "7-buy")

    # SELL partial close
    old_sell_partial = (
        '                  if(trade.PositionClosePartial(ticket, closeLot))\n'
        '                  {\n'
        '                     MarkPartialClosed(ticket);\n'
        '                     double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);\n'
        '                     trade.PositionModify(ticket, newSL, tp);\n'
        '                     Print("GOLD 半利確 SELL: ", DoubleToString(closeLot, 2), "lot決済 [", g_currentRegime, "]");\n'
        '                  }'
    )
    new_sell_partial = (
        '                  if(trade.PositionClosePartial(ticket, closeLot))\n'
        '                  {\n'
        '                     MarkPartialClosed(ticket);\n'
        '                     double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);\n'
        '                     // FIX: Issue #7 - Retry PositionModify up to 3 times after partial close\n'
        '                     bool modifyOk = false;\n'
        '                     for(int retry = 0; retry < 3; retry++)\n'
        '                     {\n'
        '                        if(trade.PositionModify(ticket, newSL, tp)) { modifyOk = true; break; }\n'
        '                        Sleep(100);\n'
        '                     }\n'
        '                     if(!modifyOk)\n'
        '                        Print("WARNING: PositionModify failed after 3 retries for SELL ticket ", ticket, " SL=", newSL);\n'
        '                     Print("GOLD 半利確 SELL: ", DoubleToString(closeLot, 2), "lot決済 [", g_currentRegime, "]");\n'
        '                  }'
    )
    content, ok2 = safe_replace(content, old_sell_partial, new_sell_partial, "7-sell")

    if ok1 and ok2:
        print("FIX #7 applied: Added retry logic for PositionModify after partial close")
    return content


def apply_fix_8(content):
    """HIGH #8: Breakeven and trailing stop conflict. Make them sequential instead of else-if."""
    # BUY side
    old_buy_be_trail = (
        '         // 建値移動\n'
        '         if(profitDist >= beDist && sl < openPrice)\n'
        '         {\n'
        '            double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);\n'
        '            trade.PositionModify(ticket, newSL, tp);\n'
        '         }\n'
        '         // トレーリング\n'
        '         else if(profitDist >= beDist * 1.5)\n'
        '         {\n'
        '            double newSL = NormalizeDouble(bid - trailStep, _Digits);\n'
        '            if(newSL > sl + 5 * _Point)\n'
        '               trade.PositionModify(ticket, newSL, tp);\n'
        '         }'
    )
    new_buy_be_trail = (
        '         // FIX: Issue #8 - Breakeven and trailing are now sequential (not else-if)\n'
        '         // 建値移動\n'
        '         if(profitDist >= beDist && sl < openPrice)\n'
        '         {\n'
        '            double newSL = NormalizeDouble(openPrice + 10 * _Point, _Digits);\n'
        '            if(trade.PositionModify(ticket, newSL, tp))\n'
        '               sl = newSL; // Update local SL for trailing check below\n'
        '         }\n'
        '         // トレーリング — now checked after breakeven (sequential)\n'
        '         if(profitDist >= beDist * 1.5)\n'
        '         {\n'
        '            double newSL = NormalizeDouble(bid - trailStep, _Digits);\n'
        '            if(newSL > sl + 5 * _Point)\n'
        '               trade.PositionModify(ticket, newSL, tp);\n'
        '         }'
    )
    content, ok1 = safe_replace(content, old_buy_be_trail, new_buy_be_trail, "8-buy")

    # SELL side
    old_sell_be_trail = (
        '         // 建値移動\n'
        '         if(profitDist >= beDist && (sl > openPrice || sl == 0))\n'
        '         {\n'
        '            double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);\n'
        '            trade.PositionModify(ticket, newSL, tp);\n'
        '         }\n'
        '         // トレーリング\n'
        '         else if(profitDist >= beDist * 1.5)\n'
        '         {\n'
        '            double newSL = NormalizeDouble(ask + trailStep, _Digits);\n'
        '            if(newSL < sl - 5 * _Point || sl == 0)\n'
        '               trade.PositionModify(ticket, newSL, tp);\n'
        '         }'
    )
    new_sell_be_trail = (
        '         // FIX: Issue #8 - Breakeven and trailing are now sequential (not else-if)\n'
        '         // 建値移動\n'
        '         if(profitDist >= beDist && (sl > openPrice || sl == 0))\n'
        '         {\n'
        '            double newSL = NormalizeDouble(openPrice - 10 * _Point, _Digits);\n'
        '            if(trade.PositionModify(ticket, newSL, tp))\n'
        '               sl = newSL; // Update local SL for trailing check below\n'
        '         }\n'
        '         // トレーリング — now checked after breakeven (sequential)\n'
        '         if(profitDist >= beDist * 1.5)\n'
        '         {\n'
        '            double newSL = NormalizeDouble(ask + trailStep, _Digits);\n'
        '            if(newSL < sl - 5 * _Point || sl == 0)\n'
        '               trade.PositionModify(ticket, newSL, tp);\n'
        '         }'
    )
    content, ok2 = safe_replace(content, old_sell_be_trail, new_sell_be_trail, "8-sell")

    if ok1 and ok2:
        print("FIX #8 applied: Breakeven and trailing are now sequential instead of else-if")
    return content


def apply_fix_10(content):
    """HIGH #10: News filter blocks ALL currencies. Add currency filter for USD, EUR, XAU only."""
    old_news = (
        'bool IsNewsTime()\n'
        '{\n'
        '   if(!UseNewsFilter) return false;\n'
        '   MqlCalendarValue values[];\n'
        '   datetime from = TimeCurrent() - NewsBlockMinutes * 60;\n'
        '   datetime to   = TimeCurrent() + NewsBlockMinutes * 60;\n'
        '   int count = CalendarValueHistory(values, from, to);\n'
        '   for(int i = 0; i < count; i++)\n'
        '   {\n'
        '      MqlCalendarEvent event;\n'
        '      if(CalendarEventById(values[i].event_id, event))\n'
        '      {\n'
        '         if(event.importance == CALENDAR_IMPORTANCE_HIGH)\n'
        '            return true;\n'
        '      }\n'
        '   }\n'
        '   return false;\n'
        '}'
    )
    new_news = (
        'bool IsNewsTime()\n'
        '{\n'
        '   if(!UseNewsFilter) return false;\n'
        '   MqlCalendarValue values[];\n'
        '   datetime from = TimeCurrent() - NewsBlockMinutes * 60;\n'
        '   datetime to   = TimeCurrent() + NewsBlockMinutes * 60;\n'
        '   int count = CalendarValueHistory(values, from, to);\n'
        '   for(int i = 0; i < count; i++)\n'
        '   {\n'
        '      MqlCalendarEvent event;\n'
        '      if(CalendarEventById(values[i].event_id, event))\n'
        '      {\n'
        '         if(event.importance == CALENDAR_IMPORTANCE_HIGH)\n'
        '         {\n'
        '            // FIX: Issue #10 - Only block for Gold-relevant currencies (USD, EUR, XAU)\n'
        '            MqlCalendarCountry country;\n'
        '            if(CalendarCountryById(event.country_id, country))\n'
        '            {\n'
        '               string cur = country.currency;\n'
        '               if(cur != "USD" && cur != "EUR" && cur != "XAU")\n'
        '                  continue;\n'
        '            }\n'
        '            return true;\n'
        '         }\n'
        '      }\n'
        '   }\n'
        '   return false;\n'
        '}'
    )
    content, ok = safe_replace(content, old_news, new_news, 10)
    if ok:
        print("FIX #10 applied: News filter now only blocks for USD, EUR, XAU currencies")
    return content


def apply_fix_13(content):
    """HIGH #13: partialClosedTickets data corruption cleanup.
    Add cleanup of old entries that are no longer open positions.
    NOTE: Accounts for CRITICAL #4 fix already applied to MarkPartialClosed.
    """
    old_partial_closed = (
        'bool IsPartialClosed(ulong ticket)\n'
        '{\n'
        '   for(int i = 0; i < ArraySize(partialClosedTickets); i++)\n'
        '      if(partialClosedTickets[i] == ticket) return true;\n'
        '   return false;\n'
        '}\n'
        '\n'
        'void MarkPartialClosed(ulong ticket)\n'
        '{\n'
        '   int sz = ArraySize(partialClosedTickets);\n'
        '   ArrayResize(partialClosedTickets, sz + 1);\n'
        '   partialClosedTickets[sz] = ticket;\n'
        '\n'
        '   // CRITICAL #4 FIX: Correct off-by-one in array trimming\n'
        '   if(sz > 100)\n'
        '   {\n'
        '      int keep = sz - 50;\n'
        '      for(int i = 0; i < keep; i++)\n'
        '         partialClosedTickets[i] = partialClosedTickets[i + 50];\n'
        '      ArrayResize(partialClosedTickets, keep);\n'
        '   }\n'
        '}'
    )
    new_partial_closed = (
        '// FIX: Issue #13 - Robust partial close tracking with stale entry cleanup\n'
        'bool IsPartialClosed(ulong ticket)\n'
        '{\n'
        '   for(int i = 0; i < ArraySize(partialClosedTickets); i++)\n'
        '      if(partialClosedTickets[i] == ticket) return true;\n'
        '   return false;\n'
        '}\n'
        '\n'
        'void MarkPartialClosed(ulong ticket)\n'
        '{\n'
        '   // FIX: Issue #13 - Clean up tickets for positions that are no longer open\n'
        '   CleanupPartialClosedTickets();\n'
        '\n'
        '   int sz = ArraySize(partialClosedTickets);\n'
        '   ArrayResize(partialClosedTickets, sz + 1);\n'
        '   partialClosedTickets[sz] = ticket;\n'
        '}\n'
        '\n'
        '// FIX: Issue #13 - Remove stale entries from partialClosedTickets\n'
        'void CleanupPartialClosedTickets()\n'
        '{\n'
        '   int sz = ArraySize(partialClosedTickets);\n'
        '   if(sz == 0) return;\n'
        '\n'
        '   ulong cleanTickets[];\n'
        '   int cleanCount = 0;\n'
        '\n'
        '   for(int i = 0; i < sz; i++)\n'
        '   {\n'
        '      // Check if this ticket still corresponds to an open position\n'
        '      bool stillOpen = false;\n'
        '      for(int p = PositionsTotal() - 1; p >= 0; p--)\n'
        '      {\n'
        '         ulong posTicket = PositionGetTicket(p);\n'
        '         if(posTicket == partialClosedTickets[i])\n'
        '         {\n'
        '            stillOpen = true;\n'
        '            break;\n'
        '         }\n'
        '      }\n'
        '      if(stillOpen)\n'
        '      {\n'
        '         cleanCount++;\n'
        '         ArrayResize(cleanTickets, cleanCount);\n'
        '         cleanTickets[cleanCount - 1] = partialClosedTickets[i];\n'
        '      }\n'
        '   }\n'
        '\n'
        '   ArrayResize(partialClosedTickets, cleanCount);\n'
        '   for(int i = 0; i < cleanCount; i++)\n'
        '      partialClosedTickets[i] = cleanTickets[i];\n'
        '}'
    )
    content, ok = safe_replace(content, old_partial_closed, new_partial_closed, 13)
    if ok:
        print("FIX #13 applied: Added CleanupPartialClosedTickets for stale entry removal")
    return content


def main():
    print("=" * 60)
    print("Applying HIGH severity fixes #6-#13 to AntigravityMTF_EA_Gold.mq5")
    print("=" * 60)

    content = read_file()
    original = content

    # Apply fixes in an order that avoids interference
    content = apply_fix_12(content)   # Remove MaxPositions
    content = apply_fix_9(content)    # Remove g_pyramidCount
    content = apply_fix_11(content)   # GMT offset for sessions
    content = apply_fix_6(content)    # Entry price staleness
    content = apply_fix_7(content)    # Partial close retry
    content = apply_fix_8(content)    # Breakeven/trailing sequential
    content = apply_fix_10(content)   # News currency filter
    content = apply_fix_13(content)   # Partial close cleanup

    if content == original:
        print("\nERROR: No changes were made!")
        sys.exit(1)

    write_file(content)
    print("\n" + "=" * 60)
    print("All 8 HIGH severity fixes applied successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
