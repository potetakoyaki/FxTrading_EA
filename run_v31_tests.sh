#!/bin/bash
# GoldAlpha v31: Full test + WFA + OOS
export DISPLAY=:99
export WINEPREFIX=/home/claude-user/.wine_mt5_v11
export WINEDEBUG=-all
MT5="/home/claude-user/.wine_mt5_v11/drive_c/Program Files/MetaTrader 5"
RESULTS="/tmp/v31_results.txt"

> "$RESULTS"

parse_report() {
  local REPORT="$1"
  python3 -c "
import re
try:
    with open('${MT5}/${REPORT}.htm', 'rb') as f:
        text = f.read().decode('utf-16-le', errors='replace')
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    pf='?'; trades='?'; dd='?'; sharp='?'; total='?'; recov='?'
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        vals = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        line = ' '.join([v for v in vals if v])
        if 'プロフィットファクター:' in line: pf = vals[1].strip()
        if 'リカバリファクター:' in line: recov = vals[1].strip(); sharp = vals[3].strip()
        if '取引数:' in line and 'ショート' in line: trades = vals[1].strip()
        if '残高最大ドローダウン' in line: dd = vals[3].strip()
        if '総損益:' in line: total = vals[1].strip()
    print(f'PF={pf}|T={trades}|DD={dd}|Sharp={sharp}|Recov={recov}|Profit={total}')
except Exception as e: print(f'ERROR|{e}')
" 2>/dev/null
}

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
  timeout 180 wine terminal64.exe /portable /config:"Config\tester.ini" 2>/dev/null
  sleep 2
}

# Compile
echo "--- Compiling v31 ---" | tee -a "$RESULTS"
cp /tmp/FxTrading_EA/GoldAlpha_v31.mq5 "$MT5/MQL5/Experts/GoldAlpha_v31.mq5"
cd "$MT5"
timeout 60 wine metaeditor64.exe /compile:"MQL5\Experts\GoldAlpha_v31.mq5" /log 2>/dev/null
sleep 2
if ls "$MT5/MQL5/Experts/GoldAlpha_v31.ex5" &>/dev/null; then
    echo "COMPILE: SUCCESS" | tee -a "$RESULTS"
else
    echo "COMPILE: FAILED" | tee -a "$RESULTS"
    cat "$MT5/MQL5/Experts/GoldAlpha_v31.log" 2>/dev/null
    exit 1
fi

# v31 default params
V31='W1_FastEMA=8||0||0||0||N
W1_SlowEMA=21||0||0||0||N
D1_EMA=50||0||0||0||N
H4_EMA=20||0||0||0||N
ATR_Period=14||0||0||0||N
ATR_SMA=50||0||0||0||N
SL_ATR_Mult=2.5||0||0||0||N
Trail_ATR=3.0||0||0||0||N
BE_ATR=1.0||0||0||0||N
SL_Weak_Mult=1.8||0||0||0||N
BodyRatio=0.32||0||0||0||N
EMA_Zone_ATR=0.50||0||0||0||N
ATR_Filter=0.66||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=2||0||0||0||N
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=0.0005||0||0||0||N
D1_Strong_Slope=0.004||0||0||0||N
W1_Min_Sep=0.005||0||0||0||N
H4_Slope_Strong=8||0||0||0||N
H4_Slope_Weak=3||0||0||0||N
MinLot=0.01||0||0||0||N
MagicNumber=330031||0||0||0||N
UseProgressiveTrail=true||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N
UseH1Entry=false||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=4||0||0||0||N
UseRelaxedStrong=false||0||0||0||N
VeryStrongSlope=0.006||0||0||0||N
StrongBodyRatio=0.22||0||0||0||N
StrongZone_ATR=0.65||0||0||0||N
CheckBar3Strong=true||0||0||0||N
UseAdaptiveMaxPos=true||0||0||0||N
MaxPos_Strong=3||0||0||0||N'

echo "========================================" | tee -a "$RESULTS"
echo "v31 TESTING at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"

# Test 1: TP=4.0/5.0 (default v31)
echo "--- Full: TP=4.0/5.0, ATR_F=0.66 (R=0.2%) ---" | tee -a "$RESULTS"
run_test "v31_full" "GoldAlpha_v31" "2016.01.01" "2026.04.01" "${V31}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
TP_ATR_Mult=4.0||0||0||0||N
TP_Strong_Mult=5.0||0||0||0||N"
R=$(parse_report "v31_full")
echo "v31_default|$R" | tee -a "$RESULTS"

# Test 2: TP=3.5/5.0 (tighter in weak)
echo "--- Full: TP=3.5/5.0 ---" | tee -a "$RESULTS"
run_test "v31_tp35" "GoldAlpha_v31" "2016.01.01" "2026.04.01" "${V31}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
TP_ATR_Mult=3.5||0||0||0||N
TP_Strong_Mult=5.0||0||0||0||N"
R=$(parse_report "v31_tp35")
echo "v31_tp35|$R" | tee -a "$RESULTS"

# Test 3: TP=0 (disabled, same as v30 but with ATR_F=0.66)
echo "--- Full: TP=0 (no TP, ATR_F=0.66 only) ---" | tee -a "$RESULTS"
run_test "v31_notp" "GoldAlpha_v31" "2016.01.01" "2026.04.01" "${V31}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
TP_ATR_Mult=0||0||0||0||N
TP_Strong_Mult=0||0||0||0||N"
R=$(parse_report "v31_notp")
echo "v31_noTP|$R" | tee -a "$RESULTS"

# Test 4: TP=5.0/6.0 (wider TP)
echo "--- Full: TP=5.0/6.0 ---" | tee -a "$RESULTS"
run_test "v31_tp50" "GoldAlpha_v31" "2016.01.01" "2026.04.01" "${V31}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
TP_ATR_Mult=5.0||0||0||0||N
TP_Strong_Mult=6.0||0||0||0||N"
R=$(parse_report "v31_tp50")
echo "v31_tp50|$R" | tee -a "$RESULTS"

echo "" | tee -a "$RESULTS"

# === WFA on best config ===
echo "=== WFA v31 (R=0.5%) ===" | tee -a "$RESULTS"

# Using TP=4.0/5.0 as default
WFA_PARAMS="${V31}
RiskPct=0.5||0||0||0||N
MaxLot=0.50||0||0||0||N
TP_ATR_Mult=4.0||0||0||0||N
TP_Strong_Mult=5.0||0||0||0||N"

PERIODS=("2016.01.01 2018.01.01" "2018.01.01 2020.01.01" "2020.01.01 2022.01.01" "2022.01.01 2024.01.01" "2024.01.01 2026.04.01")
NAMES=("2016" "2018" "2020" "2022" "2024")

for i in 0 1 2 3 4; do
  FROM=$(echo ${PERIODS[$i]} | cut -d' ' -f1)
  TO=$(echo ${PERIODS[$i]} | cut -d' ' -f2)
  NAME=${NAMES[$i]}
  echo "--- WFA ${NAME} ---" | tee -a "$RESULTS"
  run_test "v31_wfa_${NAME}" "GoldAlpha_v31" "$FROM" "$TO" "$WFA_PARAMS"
  R=$(parse_report "v31_wfa_${NAME}")
  echo "WFA_${NAME}|$R" | tee -a "$RESULTS"
done

# Also run WFA with no TP (ATR_F=0.66 only improvement)
echo "" | tee -a "$RESULTS"
echo "=== WFA v31-noTP (R=0.5%) ===" | tee -a "$RESULTS"

WFA2_PARAMS="${V31}
RiskPct=0.5||0||0||0||N
MaxLot=0.50||0||0||0||N
TP_ATR_Mult=0||0||0||0||N
TP_Strong_Mult=0||0||0||0||N"

for i in 0 1 2 3 4; do
  FROM=$(echo ${PERIODS[$i]} | cut -d' ' -f1)
  TO=$(echo ${PERIODS[$i]} | cut -d' ' -f2)
  NAME=${NAMES[$i]}
  echo "--- WFA2 ${NAME} ---" | tee -a "$RESULTS"
  run_test "v31_wfa2_${NAME}" "GoldAlpha_v31" "$FROM" "$TO" "$WFA2_PARAMS"
  R=$(parse_report "v31_wfa2_${NAME}")
  echo "WFA2_${NAME}|$R" | tee -a "$RESULTS"
done

# === OOS estimation ===
echo "" | tee -a "$RESULTS"
echo "=== OOS 2024-2026 ===" | tee -a "$RESULTS"
for R in 0.5 1.0 1.5; do
  MLOT=$(echo "$R * 1.0" | bc)
  echo "--- OOS R=${R}% ---" | tee -a "$RESULTS"
  run_test "v31_oos_r${R//.}" "GoldAlpha_v31" "2024.01.01" "2026.04.01" "${V31}
RiskPct=${R}||0||0||0||N
MaxLot=${MLOT}||0||0||0||N
TP_ATR_Mult=4.0||0||0||0||N
TP_Strong_Mult=5.0||0||0||0||N"
  echo "OOS_R${R}|$(parse_report "v31_oos_r${R//.}")" | tee -a "$RESULTS"
done

echo "" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
echo "v31 COMPLETE at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
cat "$RESULTS"
