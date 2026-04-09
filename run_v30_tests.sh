#!/bin/bash
# GoldAlpha v30 comprehensive testing
# Phase 1: Compile + Baseline tests (Progressive Trail vs H1 Entry)
# Phase 2: WFA on best variant
# Phase 3: Sensitivity + OOS

export DISPLAY=:99
export WINEPREFIX=/home/claude-user/.wine_mt5_v11
export WINEDEBUG=-all
MT5="/home/claude-user/.wine_mt5_v11/drive_c/Program Files/MetaTrader 5"
RESULTS="/tmp/v30_results.txt"

> "$RESULTS"

parse_report() {
  local REPORT="$1"
  python3 -c "
import re
try:
    with open('${MT5}/${REPORT}.htm', 'rb') as f:
        text = f.read().decode('utf-16-le', errors='replace')
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    pf='?'; trades='?'; dd='?'; sharp='?'; total='?'; recov='?'; wr='?'; longs='?'; shorts='?'
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        vals = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        line = ' '.join([v for v in vals if v])
        if 'プロフィットファクター:' in line: pf = vals[1].strip()
        if 'リカバリファクター:' in line: recov = vals[1].strip(); sharp = vals[3].strip()
        if '取引数:' in line and 'ショート' in line: trades = vals[1].strip(); shorts = vals[3].strip(); longs = vals[5].strip()
        if '残高最大ドローダウン' in line: dd = vals[3].strip()
        if '総損益:' in line: total = vals[1].strip()
        if '勝ちトレード' in line: wr = vals[3].strip()
    print(f'PF={pf}|T={trades}|DD={dd}|Sharp={sharp}|Recov={recov}|WR={wr}|Profit={total}|S={shorts}|L={longs}')
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

# Common params (v29 base)
BASE_PARAMS='W1_FastEMA=8||0||0||0||N
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
MaxPositions=2||0||0||0||N
D1_Slope_Bars=5||0||0||0||N
D1_Min_Slope=0.0005||0||0||0||N
D1_Strong_Slope=0.004||0||0||0||N
W1_Min_Sep=0.005||0||0||0||N
H4_Slope_Strong=8||0||0||0||N
H4_Slope_Weak=3||0||0||0||N
MinLot=0.01||0||0||0||N
MagicNumber=330030||0||0||0||N'

echo "========================================" | tee -a "$RESULTS"
echo "v30 TESTING at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"

# =============================================
# STEP 1: Compile v30
# =============================================
echo "--- Compiling v30 ---" | tee -a "$RESULTS"
cd "$MT5"
timeout 60 wine metaeditor64.exe /compile:"MQL5\Experts\GoldAlpha_v30.mq5" /log 2>/dev/null
sleep 2
if [ -f "$MT5/MQL5/Experts/GoldAlpha_v30.ex5" ]; then
    echo "COMPILE: SUCCESS" | tee -a "$RESULTS"
else
    echo "COMPILE: FAILED" | tee -a "$RESULTS"
    # Check log
    cat "$MT5/MQL5/Experts/GoldAlpha_v30.log" 2>/dev/null | tee -a "$RESULTS"
    exit 1
fi

# =============================================
# STEP 2: Test A - v30 Progressive Trail only (no H1)
# Same as v29 entry + progressive trail exit
# =============================================
echo "" | tee -a "$RESULTS"
echo "--- TEST A: Progressive Trail (R=0.2%, 2016-2026) ---" | tee -a "$RESULTS"
run_test "v30_trail" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE_PARAMS}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
UseProgressiveTrail=true||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N
UseH1Entry=false||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=4||0||0||0||N"
R=$(parse_report "v30_trail")
echo "v30_trail|$R" | tee -a "$RESULTS"

# =============================================
# STEP 3: Test B - v30 H1 Entry (no progressive trail)
# H1 entry + standard trail
# =============================================
echo "" | tee -a "$RESULTS"
echo "--- TEST B: H1 Entry (R=0.2%, 2016-2026) ---" | tee -a "$RESULTS"
run_test "v30_h1" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H1" "${BASE_PARAMS}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
UseProgressiveTrail=false||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N
UseH1Entry=true||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=4||0||0||0||N"
R=$(parse_report "v30_h1")
echo "v30_h1|$R" | tee -a "$RESULTS"

# =============================================
# STEP 4: Test C - v30 Both features combined
# =============================================
echo "" | tee -a "$RESULTS"
echo "--- TEST C: Progressive Trail + H1 Entry (R=0.2%, 2016-2026) ---" | tee -a "$RESULTS"
run_test "v30_both" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H1" "${BASE_PARAMS}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
UseProgressiveTrail=true||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N
UseH1Entry=true||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=4||0||0||0||N"
R=$(parse_report "v30_both")
echo "v30_both|$R" | tee -a "$RESULTS"

# =============================================
# STEP 5: Test D - v30 H1 Entry with different cooldown
# =============================================
echo "" | tee -a "$RESULTS"
echo "--- TEST D: H1 Entry Cooldown=8 (R=0.2%, 2016-2026) ---" | tee -a "$RESULTS"
run_test "v30_h1c8" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H1" "${BASE_PARAMS}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
UseProgressiveTrail=false||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N
UseH1Entry=true||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=8||0||0||0||N"
R=$(parse_report "v30_h1c8")
echo "v30_h1c8|$R" | tee -a "$RESULTS"

# =============================================
# STEP 6: Test E - v29 baseline for comparison
# =============================================
echo "" | tee -a "$RESULTS"
echo "--- BASELINE: v29 (R=0.2%, 2016-2026) ---" | tee -a "$RESULTS"
run_test "v30_baseline" "GoldAlpha_v30" "2016.01.01" "2026.04.01" "H4" "${BASE_PARAMS}
RiskPct=0.2||0||0||0||N
MaxLot=0.50||0||0||0||N
UseProgressiveTrail=false||0||0||0||N
TrailProfit1=2.0||0||0||0||N
TrailProfit2=4.0||0||0||0||N
Trail_ATR_Med=2.5||0||0||0||N
Trail_ATR_Tight=2.0||0||0||0||N
UseH1Entry=false||0||0||0||N
H1_BodyRatio=0.30||0||0||0||N
H1_Cooldown=4||0||0||0||N"
R=$(parse_report "v30_baseline")
echo "v30_baseline|$R" | tee -a "$RESULTS"

echo "" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"
echo "PHASE 1 COMPLETE at $(date)" | tee -a "$RESULTS"
echo "========================================" | tee -a "$RESULTS"

cat "$RESULTS"
