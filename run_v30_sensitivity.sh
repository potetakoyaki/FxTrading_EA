#!/bin/bash
# GoldAlpha v30 Sensitivity Analysis + OOS Profit Estimation
# ±20% perturbation on 6 key parameters + OOS at multiple risk levels
export DISPLAY=:99
export WINEPREFIX=/home/claude-user/.wine_mt5_v11
export WINEDEBUG=-all
MT5="/home/claude-user/.wine_mt5_v11/drive_c/Program Files/MetaTrader 5"
RESULTS="/tmp/v30_sensitivity.txt"

> "$RESULTS"

parse_report() {
  local REPORT="$1"
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
        line = ' '.join([v for v in vals if v])
        if 'プロフィットファクター:' in line: pf = vals[1].strip()
        if 'リカバリファクター:' in line: sharp = vals[3].strip()
        if '取引数:' in line and 'ショート' in line: trades = vals[1].strip()
        if '残高最大ドローダウン' in line: dd = vals[3].strip()
        if '総損益:' in line: total = vals[1].strip()
    print(f'PF={pf}|T={trades}|DD={dd}|Sharp={sharp}|Profit={total}')
except Exception as e: print(f'ERROR|{e}')
" 2>/dev/null
}

run_test() {
  local REPORT="$1" FROM="$2" TO="$3"
  shift 3
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
Expert=GoldAlpha_v30
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

# Winner config: Trail + AdaptMaxPos
make_params() {
  # $1=SL_ATR, $2=Trail, $3=Zone, $4=ATR_Filter, $5=D1_Min, $6=BodyRatio, $7=Risk, $8=MaxLot
  echo "W1_FastEMA=8||0||0||0||N
W1_SlowEMA=21||0||0||0||N
D1_EMA=50||0||0||0||N
H4_EMA=20||0||0||0||N
ATR_Period=14||0||0||0||N
ATR_SMA=50||0||0||0||N
SL_ATR_Mult=$1||0||0||0||N
Trail_ATR=$2||0||0||0||N
BE_ATR=1.0||0||0||0||N
SL_Weak_Mult=1.8||0||0||0||N
BodyRatio=$6||0||0||0||N
EMA_Zone_ATR=$3||0||0||0||N
ATR_Filter=$4||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=2||0||0||0||N
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=$5||0||0||0||N
D1_Strong_Slope=0.004||0||0||0||N
W1_Min_Sep=0.005||0||0||0||N
H4_Slope_Strong=8||0||0||0||N
H4_Slope_Weak=3||0||0||0||N
MinLot=0.01||0||0||0||N
RiskPct=$7||0||0||0||N
MaxLot=$8||0||0||0||N
MagicNumber=330030||0||0||0||N
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
MaxPos_Strong=3||0||0||0||N"
}

echo "========================================" | tee -a "$RESULTS"
echo "SENSITIVITY + OOS at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"

# Defaults: SL=2.5, Trail=3.0, Zone=0.50, ATR_F=0.55, D1_Min=0.0005, Body=0.32

# === SENSITIVITY (R=0.2%, full period) ===
echo "=== SENSITIVITY ±20% ===" | tee -a "$RESULTS"

# SL_ATR_Mult: 2.0 and 3.0
echo "--- SL_ATR_Mult=2.0 ---" | tee -a "$RESULTS"
run_test "v30_s_sl20" "2016.01.01" "2026.04.01" "$(make_params 2.0 3.0 0.50 0.55 0.0005 0.32 0.2 0.50)"
echo "SL=2.0|$(parse_report v30_s_sl20)" | tee -a "$RESULTS"

echo "--- SL_ATR_Mult=3.0 ---" | tee -a "$RESULTS"
run_test "v30_s_sl30" "2016.01.01" "2026.04.01" "$(make_params 3.0 3.0 0.50 0.55 0.0005 0.32 0.2 0.50)"
echo "SL=3.0|$(parse_report v30_s_sl30)" | tee -a "$RESULTS"

# Trail_ATR: 2.4 and 3.6
echo "--- Trail_ATR=2.4 ---" | tee -a "$RESULTS"
run_test "v30_s_tr24" "2016.01.01" "2026.04.01" "$(make_params 2.5 2.4 0.50 0.55 0.0005 0.32 0.2 0.50)"
echo "Trail=2.4|$(parse_report v30_s_tr24)" | tee -a "$RESULTS"

echo "--- Trail_ATR=3.6 ---" | tee -a "$RESULTS"
run_test "v30_s_tr36" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.6 0.50 0.55 0.0005 0.32 0.2 0.50)"
echo "Trail=3.6|$(parse_report v30_s_tr36)" | tee -a "$RESULTS"

# EMA_Zone_ATR: 0.40 and 0.60
echo "--- Zone=0.40 ---" | tee -a "$RESULTS"
run_test "v30_s_z40" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.40 0.55 0.0005 0.32 0.2 0.50)"
echo "Zone=0.40|$(parse_report v30_s_z40)" | tee -a "$RESULTS"

echo "--- Zone=0.60 ---" | tee -a "$RESULTS"
run_test "v30_s_z60" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.60 0.55 0.0005 0.32 0.2 0.50)"
echo "Zone=0.60|$(parse_report v30_s_z60)" | tee -a "$RESULTS"

# ATR_Filter: 0.44 and 0.66
echo "--- ATR_F=0.44 ---" | tee -a "$RESULTS"
run_test "v30_s_af44" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.44 0.0005 0.32 0.2 0.50)"
echo "ATR_F=0.44|$(parse_report v30_s_af44)" | tee -a "$RESULTS"

echo "--- ATR_F=0.66 ---" | tee -a "$RESULTS"
run_test "v30_s_af66" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.66 0.0005 0.32 0.2 0.50)"
echo "ATR_F=0.66|$(parse_report v30_s_af66)" | tee -a "$RESULTS"

# D1_Min_Slope: 0.0004 and 0.0006
echo "--- D1_Min=0.0004 ---" | tee -a "$RESULTS"
run_test "v30_s_d14" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.55 0.0004 0.32 0.2 0.50)"
echo "D1=0.0004|$(parse_report v30_s_d14)" | tee -a "$RESULTS"

echo "--- D1_Min=0.0006 ---" | tee -a "$RESULTS"
run_test "v30_s_d16" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.55 0.0006 0.32 0.2 0.50)"
echo "D1=0.0006|$(parse_report v30_s_d16)" | tee -a "$RESULTS"

# BodyRatio: 0.26 and 0.38
echo "--- Body=0.26 ---" | tee -a "$RESULTS"
run_test "v30_s_b26" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.55 0.0005 0.26 0.2 0.50)"
echo "Body=0.26|$(parse_report v30_s_b26)" | tee -a "$RESULTS"

echo "--- Body=0.38 ---" | tee -a "$RESULTS"
run_test "v30_s_b38" "2016.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.55 0.0005 0.38 0.2 0.50)"
echo "Body=0.38|$(parse_report v30_s_b38)" | tee -a "$RESULTS"

echo "" | tee -a "$RESULTS"

# === OOS PROFIT ESTIMATION (2024-2026 at different risk levels) ===
echo "=== OOS 2024-2026 PROFIT ===" | tee -a "$RESULTS"

for R in 0.2 0.5 1.0 1.5 2.0; do
  MLOT=$(echo "$R * 1.0" | bc)
  echo "--- OOS R=${R}% ---" | tee -a "$RESULTS"
  run_test "v30_oos_r${R//.}" "2024.01.01" "2026.04.01" "$(make_params 2.5 3.0 0.50 0.55 0.0005 0.32 ${R} ${MLOT})"
  echo "OOS_R${R}|$(parse_report "v30_oos_r${R//.}")" | tee -a "$RESULTS"
done

echo "" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
echo "SENSITIVITY + OOS COMPLETE at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
cat "$RESULTS"
