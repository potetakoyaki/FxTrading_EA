#!/bin/bash
# ============================================
#  AntigravityMTF Gold EA - Mac完全自動最適化
#  MT5 Mac app → tester.ini差替 → 自動テスト
# ============================================

MT5_APP="/Applications/MetaTrader 5.app"
MT5_DATA="$HOME/Library/Application Support/MetaTrader 5/Bottles/metatrader5/drive_c/users/crossover/Application Data/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075"

REPO_PATH="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="${REPO_PATH}/mt5_results"
INI_DIR="$(dirname "$0")/ini"

echo "============================================"
echo " AntigravityMTF Gold EA - Mac完全自動最適化"
echo "============================================"

# 前提チェック
if [ ! -d "$MT5_APP" ]; then
    echo "ERROR: MetaTrader 5.app not found"
    exit 1
fi

if [ ! -d "$MT5_DATA" ]; then
    echo "ERROR: MT5 data folder not found at:"
    echo "  $MT5_DATA"
    echo "MT5を開いて ファイル→データフォルダを開く でパスを確認してください"
    exit 1
fi

# 結果フォルダ準備
mkdir -p "${RESULTS_DIR}"
rm -f "${RESULTS_DIR}"/*.xml "${RESULTS_DIR}"/*.htm "${RESULTS_DIR}"/*.txt 2>/dev/null

# MT5を閉じる
echo "MT5を閉じています..."
pkill -f "MetaTrader 5" 2>/dev/null
pkill -f "terminal64" 2>/dev/null
sleep 5

# configフォルダ確認
CONFIG_DIR="${MT5_DATA}/Config"
if [ ! -d "$CONFIG_DIR" ]; then
    CONFIG_DIR="${MT5_DATA}/config"
fi
mkdir -p "${CONFIG_DIR}"

# testerフォルダ確認
TESTER_DIR="${MT5_DATA}/Tester"
if [ ! -d "$TESTER_DIR" ]; then
    TESTER_DIR="${MT5_DATA}/tester"
fi
mkdir -p "${TESTER_DIR}"

echo "Config: ${CONFIG_DIR}"
echo "Tester: ${TESTER_DIR}"
echo ""

# === 8テスト実行 ===
for i in $(seq 1 8); do
    echo "=========================================="
    echo " [${i}/8] テスト実行中..."
    echo "=========================================="

    # iniファイルをconfigフォルダにコピー
    cp "${INI_DIR}/test${i}.ini" "${CONFIG_DIR}/tester.ini"
    echo "  ini: test${i}.ini → ${CONFIG_DIR}/tester.ini"

    # テスト前のtesterフォルダのファイル一覧を記録
    BEFORE_FILES=$(ls -1 "${TESTER_DIR}"/*.xml 2>/dev/null | sort)

    # MT5をconfig引数付きで起動
    open -a "MetaTrader 5" --args "/config:Config\\tester.ini"
    MT5_PID=""
    sleep 10

    # MT5のPIDを取得
    MT5_PID=$(pgrep -f "MetaTrader 5" | head -1)
    if [ -z "$MT5_PID" ]; then
        MT5_PID=$(pgrep -f "terminal64" | head -1)
    fi
    echo "  MT5 PID: ${MT5_PID:-unknown}"

    # MT5の終了を待つ（最大45分）
    WAITED=0
    MAX_WAIT=2700
    while true; do
        sleep 15
        WAITED=$((WAITED + 15))

        # MT5がまだ動いているか確認
        if [ -n "$MT5_PID" ]; then
            if ! kill -0 $MT5_PID 2>/dev/null; then
                echo "  MT5終了検出 (${WAITED}秒)"
                break
            fi
        else
            # PIDが取れない場合はプロセス名で確認
            if ! pgrep -f "terminal64" > /dev/null 2>&1 && ! pgrep -f "MetaTrader 5" > /dev/null 2>&1; then
                echo "  MT5終了検出 (${WAITED}秒)"
                break
            fi
        fi

        if [ $WAITED -ge $MAX_WAIT ]; then
            echo "  タイムアウト(45分)。強制終了..."
            pkill -f "MetaTrader 5" 2>/dev/null
            pkill -f "terminal64" 2>/dev/null
            sleep 5
            break
        fi

        if [ $((WAITED % 120)) -eq 0 ]; then
            echo "  ${WAITED}秒 ($((WAITED/60))分) 経過..."
        fi
    done

    # 結果ファイルを収集（テスト後に新しく生成されたもの）
    sleep 3
    AFTER_FILES=$(ls -1 "${TESTER_DIR}"/*.xml 2>/dev/null | sort)
    NEW_FILE=$(comm -13 <(echo "$BEFORE_FILES") <(echo "$AFTER_FILES") | head -1)

    if [ -n "$NEW_FILE" ] && [ -f "$NEW_FILE" ]; then
        cp "$NEW_FILE" "${RESULTS_DIR}/test${i}_report.xml"
        echo "  結果: $(basename $NEW_FILE) → test${i}_report.xml"
    else
        # 最新ファイルをコピー
        LATEST=$(ls -t "${TESTER_DIR}"/*.xml 2>/dev/null | head -1)
        if [ -n "$LATEST" ] && [ -f "$LATEST" ]; then
            cp "$LATEST" "${RESULTS_DIR}/test${i}_report.xml"
            echo "  結果(最新): $(basename $LATEST)"
        else
            echo "  警告: 結果ファイルなし"
            ls -lt "${TESTER_DIR}"/ 2>/dev/null | head -5 > "${RESULTS_DIR}/debug_test${i}.txt"
        fi
    fi

    # HTMレポートも収集
    LATEST_HTM=$(ls -t "${TESTER_DIR}"/*.htm 2>/dev/null | head -1)
    if [ -n "$LATEST_HTM" ] && [ -f "$LATEST_HTM" ]; then
        cp "$LATEST_HTM" "${RESULTS_DIR}/test${i}_report.htm"
    fi

    # XLSXレポートも収集
    LATEST_XLSX=$(ls -t "${TESTER_DIR}"/*.xlsx 2>/dev/null | head -1)
    if [ -n "$LATEST_XLSX" ] && [ -f "$LATEST_XLSX" ]; then
        cp "$LATEST_XLSX" "${RESULTS_DIR}/test${i}_report.xlsx"
    fi

    # 次のテスト前に完全停止
    pkill -f "MetaTrader 5" 2>/dev/null
    pkill -f "terminal64" 2>/dev/null
    sleep 8
done

# ============================================
#  サマリー生成
# ============================================
echo ""
echo "結果サマリー生成中..."

cat > "${RESULTS_DIR}/summary.txt" << EOF
# MT5 Backtest Results (Mac Auto)
Generated: $(date)

## Configurations
Test 1: BE=0.5, Partial=ON  (現行v9.3)
Test 2: BE=0.5, Partial=OFF
Test 3: BE=1.0, Partial=ON
Test 4: BE=1.0, Partial=OFF
Test 5: BE=1.5, Partial=ON
Test 6: BE=1.5, Partial=OFF
Test 7: BE=2.0, Partial=ON
Test 8: BE=2.0, Partial=OFF

## Collected Files
$(ls -la "${RESULTS_DIR}"/ 2>/dev/null)
EOF

echo ""
echo "収集された結果:"
ls -la "${RESULTS_DIR}"/

# ============================================
#  GitHubにプッシュ
# ============================================
echo ""
echo "GitHubにプッシュ中..."

cd "${REPO_PATH}"
git add mt5_results/
git commit -m "MT5 Mac auto backtest: 8 patterns (BE x Partial) - $(date +%Y%m%d_%H%M)"
git push origin main

echo ""
echo "============================================"
echo " 完了！GitHubにプッシュ済み"
echo " Claude Codeで「結果を分析して」と伝えてください"
echo "============================================"
