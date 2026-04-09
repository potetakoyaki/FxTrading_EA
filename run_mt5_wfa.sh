#!/bin/bash
# Self-contained MT5 WFA + optimization script
# No user interaction needed. Runs to completion.

export DISPLAY=:99
export WINEPREFIX=/home/claude-user/.wine_mt5_v11
export WINEDEBUG=-all
# NO WINEDLLOVERRIDES - MetaEditor needs dbghelp
MT5="/home/claude-user/.wine_mt5_v11/drive_c/Program Files/MetaTrader 5"
RESULTS="/tmp/mt5_wfa_results.txt"

> "$RESULTS"

run_test() {
  local REPORT="$1" EXPERT="$2" FROM="$3" TO="$4"
  shift 4
  local INPUTS="$*"

  python3 -c "
content = '''[Common]
Login=105234611
Password=@rFlQv6y
Server=MetaQuotes-Demo
KeepPrivate=0
CertInstall=1

[Experts]
AllowLiveTrading=1
AllowDllImport=1
Enabled=1

[Tester]
Expert=${EXPERT}
Symbol=XAUUSD
Period=H4
Optimization=0
Model=1
FromDate=${FROM}
ToDate=${TO}
Deposit=10000
Currency=USD
Leverage=100
Visual=0
ReplaceReport=1
ShutdownTerminal=1
Report=${REPORT}
UseLocal=1
UseCloud=0

[TesterInputs]
${INPUTS}
'''
with open('${MT5}/Config/tester.ini', 'wb') as f:
    f.write(b'\xff\xfe')
    f.write(content.encode('utf-16-le'))
"

  cd "$MT5"
  wine terminal64.exe /portable /config:"Config\tester.ini" 2>/dev/null &
  local PID=$!
  for i in $(seq 1 90); do
    sleep 5
    kill -0 $PID 2>/dev/null || break
  done
  sleep 2

  python3 -c "
import re
try:
    with open('${MT5}/${REPORT}.htm', 'rb') as f:
        text = f.read().decode('utf-16-le', errors='replace')
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    pf='?'; trades='?'; dd='?'; sharp='?'; total='?'
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        vals = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        line = ' | '.join([v for v in vals if v])
        if 'ファクター:' in line and '期待利得' in line: pf = vals[1]
        if '取引数:' in line and 'ショート' in line: trades = vals[1]
        if '残高最大ドローダウン' in line: dd = vals[3]
        if 'リカバリファクター' in line and 'シャープ' in line: sharp = vals[3]
        if '総損益:' in line and '残高絶対' in line: total = vals[1]
    print(f'${REPORT}|{pf}|{trades}|{dd}|{sharp}|{total}')
except: print(f'${REPORT}|ERROR|0|0|0|0')
" 2>/dev/null
}

echo "Starting MT5 WFA batch at $(date)" | tee -a "$RESULTS"

# ============================================================
# F5: ATR=0.7, D1_Tol=0.003, MaxPos=2
# ============================================================
F5_INPUTS='SL_ATR_Mult=2.5||0||0||0||N
Trail_ATR=3.0||0||0||0||N
BE_ATR=1.0||0||0||0||N
EMA_Zone_ATR=0.4||0||0||0||N
ATR_Filter=0.7||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=2||0||0||0||N
RiskPct=0.2||0||0||0||N
BodyRatio=0.32||0||0||0||N
MinLot=0.01||0||0||0||N
MaxLot=0.15||0||0||0||N
MagicNumber=330023||0||0||0||N'

echo "=== F5 WFA ===" | tee -a "$RESULTS"
for PD in "2016.01.01 2018.01.01" "2018.01.01 2020.01.01" "2020.01.01 2022.01.01" "2022.01.01 2024.01.01" "2024.01.01 2026.04.01"; do
  read FROM TO <<< "$PD"
  YR=${FROM:0:4}
  RESULT=$(run_test "f5w_${YR}" "GoldAlpha_v12" "$FROM" "$TO" "$F5_INPUTS")
  echo "$RESULT" | tee -a "$RESULTS"
done

# ============================================================
# F9: ATR=0.75, D1_Tol=0.003, MaxPos=2, Body=0.40
# ============================================================
F9_INPUTS='SL_ATR_Mult=2.5||0||0||0||N
Trail_ATR=3.0||0||0||0||N
BE_ATR=1.0||0||0||0||N
EMA_Zone_ATR=0.4||0||0||0||N
ATR_Filter=0.75||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=2||0||0||0||N
RiskPct=0.2||0||0||0||N
BodyRatio=0.40||0||0||0||N
MinLot=0.01||0||0||0||N
MaxLot=0.15||0||0||0||N
MagicNumber=330023||0||0||0||N'

echo "=== F9 WFA ===" | tee -a "$RESULTS"
for PD in "2016.01.01 2018.01.01" "2018.01.01 2020.01.01" "2020.01.01 2022.01.01" "2022.01.01 2024.01.01" "2024.01.01 2026.04.01"; do
  read FROM TO <<< "$PD"
  YR=${FROM:0:4}
  RESULT=$(run_test "f9w_${YR}" "GoldAlpha_v12" "$FROM" "$TO" "$F9_INPUTS")
  echo "$RESULT" | tee -a "$RESULTS"
done

# ============================================================
# F10: v22 D1 regime + F5 params
# ============================================================
F10_INPUTS='SL_ATR_Mult=2.5||0||0||0||N
Trail_ATR=3.0||0||0||0||N
BE_ATR=1.0||0||0||0||N
EMA_Zone_ATR=0.4||0||0||0||N
ATR_Filter=0.7||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=2||0||0||0||N
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=0.001||0||0||0||N
RiskPct=0.2||0||0||0||N
BodyRatio=0.32||0||0||0||N
MinLot=0.01||0||0||0||N
MaxLot=0.15||0||0||0||N
MagicNumber=330022||0||0||0||N'

echo "=== F10 WFA (v22 regime + F5) ===" | tee -a "$RESULTS"
for PD in "2016.01.01 2018.01.01" "2018.01.01 2020.01.01" "2020.01.01 2022.01.01" "2022.01.01 2024.01.01" "2024.01.01 2026.04.01"; do
  read FROM TO <<< "$PD"
  YR=${FROM:0:4}
  RESULT=$(run_test "f10w_${YR}" "GoldAlpha_v22" "$FROM" "$TO" "$F10_INPUTS")
  echo "$RESULT" | tee -a "$RESULTS"
done

# ============================================================
# F11: Tighter SL + wider Trail (let winners run, cut losers fast)
# ============================================================
F11_INPUTS='SL_ATR_Mult=2.0||0||0||0||N
Trail_ATR=3.5||0||0||0||N
BE_ATR=0.8||0||0||0||N
EMA_Zone_ATR=0.4||0||0||0||N
ATR_Filter=0.7||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=2||0||0||0||N
RiskPct=0.2||0||0||0||N
BodyRatio=0.32||0||0||0||N
MinLot=0.01||0||0||0||N
MaxLot=0.15||0||0||0||N
MagicNumber=330023||0||0||0||N'

echo "=== F11 WFA (tight SL + wide trail) ===" | tee -a "$RESULTS"
for PD in "2016.01.01 2018.01.01" "2018.01.01 2020.01.01" "2020.01.01 2022.01.01" "2022.01.01 2024.01.01" "2024.01.01 2026.04.01"; do
  read FROM TO <<< "$PD"
  YR=${FROM:0:4}
  RESULT=$(run_test "f11w_${YR}" "GoldAlpha_v12" "$FROM" "$TO" "$F11_INPUTS")
  echo "$RESULT" | tee -a "$RESULTS"
done

echo "" | tee -a "$RESULTS"
echo "=== ALL DONE at $(date) ===" | tee -a "$RESULTS"
echo "Results saved to $RESULTS"
