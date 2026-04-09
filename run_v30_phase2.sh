#!/bin/bash
# GoldAlpha v30 Phase 2: Regime relaxation + MaxPos=3 + Trail combinations
export DISPLAY=:99
export WINEPREFIX=/home/claude-user/.wine_mt5_v11
export WINEDEBUG=-all
MT5="/home/claude-user/.wine_mt5_v11/drive_c/Program Files/MetaTrader 5"
RESULTS="/tmp/v30_phase2.txt"

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

# Compile updated v30
echo "--- Compiling v30 (updated) ---" | tee -a "$RESULTS"
cp /tmp/FxTrading_EA/GoldAlpha_v30.mq5 "$MT5/MQL5/Experts/GoldAlpha_v30.mq5"
cd "$MT5"
timeout 60 wine metaeditor64.exe /compile:"MQL5\Experts\GoldAlpha_v30.mq5" /log 2>/dev/null
sleep 2
if [ -f "$MT5/MQL5/Experts/GoldAlpha_v30.ex5" ]; then
    echo "COMPILE: SUCCESS" | tee -a "$RESULTS"
else
    echo "COMPILE: FAILED" | tee -a "$RESULTS"
    cat "$MT5/MQL5/Experts/GoldAlpha_v30.log" 2>/dev/null | tee -a "$RESULTS"
    exit 1
fi

# Common base params
BASE='W1_FastEMA=8||0||0||0||N
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
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=0.0005||0||0||0||N
D1_Strong_Slope=0.004||0||0||0||N
W1_Min_Sep=0.005||0||0||0||N
H4_Slope_Strong=8||0||0||0||N
H4_Slope_Weak=3||0||0||0||N
MinLot=0.01||0||0||0||N
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
MagicNumber=330030||0||0||0||N
UseH1Entry=false||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=4||0||0||0||N'

# Trail params (on/off)
TRAIL_ON='UseProgressiveTrail=true||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N'

TRAIL_OFF='UseProgressiveTrail=false||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N'

# Relaxed regime params (on/off)
RELAX_ON='UseRelaxedStrong=true||0||0||0||N
VeryStrongSlope=0.006||0||0||0||N
StrongBodyRatio=0.22||0||0||0||N
StrongZone_ATR=0.65||0||0||0||N
CheckBar3Strong=true||0||0||0||N'

RELAX_OFF='UseRelaxedStrong=false||0||0||0||N
VeryStrongSlope=0.006||0||0||0||N
StrongBodyRatio=0.22||0||0||0||N
StrongZone_ATR=0.65||0||0||0||N
CheckBar3Strong=true||0||0||0||N'

echo "========================================" | tee -a "$RESULTS"
echo "PHASE 2 at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"

# Test 1: Regime relaxation only (MaxPos=2, no trail)
echo "--- T1: Regime Relaxation only ---" | tee -a "$RESULTS"
run_test "v30p2_relax" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=2||0||0||0||N
${TRAIL_OFF}
${RELAX_ON}"
R=$(parse_report "v30p2_relax")
echo "relax|$R" | tee -a "$RESULTS"

# Test 2: MaxPos=3 only (no trail, no relax)
echo "--- T2: MaxPos=3 only ---" | tee -a "$RESULTS"
run_test "v30p2_mp3" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=3||0||0||0||N
${TRAIL_OFF}
${RELAX_OFF}"
R=$(parse_report "v30p2_mp3")
echo "mp3|$R" | tee -a "$RESULTS"

# Test 3: Trail + Regime relaxation (MaxPos=2)
echo "--- T3: Trail + Relax ---" | tee -a "$RESULTS"
run_test "v30p2_trail_relax" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=2||0||0||0||N
${TRAIL_ON}
${RELAX_ON}"
R=$(parse_report "v30p2_trail_relax")
echo "trail+relax|$R" | tee -a "$RESULTS"

# Test 4: Trail + MaxPos=3 (no relax)
echo "--- T4: Trail + MaxPos=3 ---" | tee -a "$RESULTS"
run_test "v30p2_trail_mp3" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=3||0||0||0||N
${TRAIL_ON}
${RELAX_OFF}"
R=$(parse_report "v30p2_trail_mp3")
echo "trail+mp3|$R" | tee -a "$RESULTS"

# Test 5: All three (Trail + Relax + MaxPos=3)
echo "--- T5: Trail + Relax + MaxPos=3 ---" | tee -a "$RESULTS"
run_test "v30p2_all" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=3||0||0||0||N
${TRAIL_ON}
${RELAX_ON}"
R=$(parse_report "v30p2_all")
echo "all|$R" | tee -a "$RESULTS"

# Test 6: Regime relaxation with lower VeryStrongSlope=0.004 (more trades)
echo "--- T6: Relax VeryStrong=0.004 ---" | tee -a "$RESULTS"
run_test "v30p2_relax_low" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=2||0||0||0||N
${TRAIL_OFF}
UseRelaxedStrong=true||0||0||0||N
VeryStrongSlope=0.004||0||0||0||N
StrongBodyRatio=0.22||0||0||0||N
StrongZone_ATR=0.65||0||0||0||N
CheckBar3Strong=true||0||0||0||N"
R=$(parse_report "v30p2_relax_low")
echo "relax_low|$R" | tee -a "$RESULTS"

# Test 7: Softer progressive trail (Med=2.7, Tight=2.3)
echo "--- T7: Soft Trail ---" | tee -a "$RESULTS"
run_test "v30p2_softtrail" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE}
MaxPositions=2||0||0||0||N
UseProgressiveTrail=true||0||0||0||N
TrailProfit1=2.5||0||0||0||N
TrailProfit2=5.0||0||0||0||N
Trail_ATR_Med=2.7||0||0||0||N
Trail_ATR_Tight=2.3||0||0||0||N
${RELAX_OFF}"
R=$(parse_report "v30p2_softtrail")
echo "softtrail|$R" | tee -a "$RESULTS"

echo "" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
echo "PHASE 2 COMPLETE at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
cat "$RESULTS"
