#!/bin/bash
# GoldAlpha v30 WFA: 5-period Walk-Forward Analysis
# T2 (MaxPos=3) and T4 (Trail+MaxPos=3)
export DISPLAY=:99
export WINEPREFIX=/home/claude-user/.wine_mt5_v11
export WINEDEBUG=-all
MT5="/home/claude-user/.wine_mt5_v11/drive_c/Program Files/MetaTrader 5"
RESULTS="/tmp/v30_wfa.txt"

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
    print(f'PF={pf}|T={trades}|DD={dd}|Sharp={sharp}|Profit={total}')
except Exception as e: print(f'ERROR|{e}')
" 2>/dev/null
}

run_test() {
  local REPORT="$1" EXPERT="$2" FROM="$3" TO="$4" PERIOD="$5"
  shift 5
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
Period=${PERIOD}
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

# === T2: MaxPos=3 (no trail) ===
T2_PARAMS='W1_FastEMA=8||0||0||0||N
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
ATR_Filter=0.55||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=3||0||0||0||N
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=0.0005||0||0||0||N
D1_Strong_Slope=0.004||0||0||0||N
W1_Min_Sep=0.005||0||0||0||N
H4_Slope_Strong=8||0||0||0||N
H4_Slope_Weak=3||0||0||0||N
MinLot=0.01||0||0||0||N
RiskPct=0.5||0||0||0||N
MaxLot=0.50||0||0||0||N
MagicNumber=330030||0||0||0||N
UseProgressiveTrail=false||0||0||0||N
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
CheckBar3Strong=true||0||0||0||N'

# === T4: Trail + MaxPos=3 ===
T4_PARAMS='W1_FastEMA=8||0||0||0||N
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
ATR_Filter=0.55||0||0||0||N
D1_Tolerance=0.003||0||0||0||N
MaxPositions=3||0||0||0||N
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=0.0005||0||0||0||N
D1_Strong_Slope=0.004||0||0||0||N
W1_Min_Sep=0.005||0||0||0||N
H4_Slope_Strong=8||0||0||0||N
H4_Slope_Weak=3||0||0||0||N
MinLot=0.01||0||0||0||N
RiskPct=0.5||0||0||0||N
MaxLot=0.50||0||0||0||N
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
CheckBar3Strong=true||0||0||0||N'

echo "========================================" | tee -a "$RESULTS"
echo "WFA VALIDATION at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"

# WFA periods (2-year windows, R=0.5%)
PERIODS=("2016.01.01 2018.01.01" "2018.01.01 2020.01.01" "2020.01.01 2022.01.01" "2022.01.01 2024.01.01" "2024.01.01 2026.04.01")
NAMES=("2016" "2018" "2020" "2022" "2024")

echo "" | tee -a "$RESULTS"
echo "=== T2: MaxPos=3 WFA (R=0.5%) ===" | tee -a "$RESULTS"
for i in 0 1 2 3 4; do
  FROM=$(echo ${PERIODS[$i]} | cut -d' ' -f1)
  TO=$(echo ${PERIODS[$i]} | cut -d' ' -f2)
  NAME=${NAMES[$i]}
  echo "--- T2 WFA ${NAME} ---" | tee -a "$RESULTS"
  run_test "v30_t2_${NAME}" "GoldAlpha_v30" "$FROM" "$TO" "H4" "$T2_PARAMS"
  R=$(parse_report "v30_t2_${NAME}")
  echo "T2_${NAME}|$R" | tee -a "$RESULTS"
done

echo "" | tee -a "$RESULTS"
echo "=== T4: Trail+MaxPos=3 WFA (R=0.5%) ===" | tee -a "$RESULTS"
for i in 0 1 2 3 4; do
  FROM=$(echo ${PERIODS[$i]} | cut -d' ' -f1)
  TO=$(echo ${PERIODS[$i]} | cut -d' ' -f2)
  NAME=${NAMES[$i]}
  echo "--- T4 WFA ${NAME} ---" | tee -a "$RESULTS"
  run_test "v30_t4_${NAME}" "GoldAlpha_v30" "$FROM" "$TO" "H4" "$T4_PARAMS"
  R=$(parse_report "v30_t4_${NAME}")
  echo "T4_${NAME}|$R" | tee -a "$RESULTS"
done

echo "" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
echo "WFA COMPLETE at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
cat "$RESULTS"
