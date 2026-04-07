#!/bin/bash
# ============================================
#  AntigravityMTF Gold EA - Mac完全自動最適化
#  MT5最適化実行 → 結果収集 → Git Push
# ============================================

MT5_APP="/Applications/MetaTrader 5.app/Contents/MacOS/MetaTrader 5"
REPO_PATH="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="${REPO_PATH}/mt5_results"
INI_DIR="$(dirname "$0")/ini"

# MT5のWineデータフォルダを自動検出
MT5_WINE_PREFIX="$HOME/.wine"
MT5_DATA_CANDIDATES=(
    "$HOME/Library/Application Support/MetaTrader 5"
    "$HOME/.wine/drive_c/Program Files/MetaTrader 5"
    "$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5"
)

echo "============================================"
echo " AntigravityMTF Gold EA - Mac自動最適化"
echo "============================================"

# MT5データフォルダを探す
MT5_DATA=""
for candidate in "${MT5_DATA_CANDIDATES[@]}"; do
    if [ -d "$candidate" ]; then
        MT5_DATA="$candidate"
        echo "MT5 Data: $MT5_DATA"
        break
    fi
done

if [ -z "$MT5_DATA" ]; then
    echo "MT5データフォルダが見つかりません。"
    echo "MT5を開いて ファイル→データフォルダを開く でパスを確認し、"
    echo "このスクリプトの MT5_DATA 変数を手動設定してください。"
    echo ""
    read -p "MT5データフォルダのパスを入力: " MT5_DATA
fi

mkdir -p "${RESULTS_DIR}"
rm -f "${RESULTS_DIR}"/*.xml "${RESULTS_DIR}"/*.htm "${RESULTS_DIR}"/*.txt 2>/dev/null

echo ""
echo "8パターンのバックテストを実行します..."
echo ""

for i in $(seq 1 8); do
    echo "[${i}/8] テスト実行中..."

    # iniファイルをMT5のconfigフォルダにコピー
    CONFIG_DIR="${MT5_DATA}/config"
    mkdir -p "${CONFIG_DIR}" 2>/dev/null
    cp "${INI_DIR}/test${i}.ini" "${CONFIG_DIR}/tester.ini"

    # MT5を起動してバックテスト実行
    # ShutdownTerminal=1 により完了後に自動終了
    "${MT5_APP}" "/config:config/tester.ini" &
    MT5_PID=$!

    # MT5の終了を待つ（最大30分）
    WAITED=0
    while kill -0 $MT5_PID 2>/dev/null; do
        sleep 10
        WAITED=$((WAITED + 10))
        if [ $WAITED -ge 1800 ]; then
            echo "  タイムアウト(30分)。MT5を強制終了..."
            kill $MT5_PID 2>/dev/null
            sleep 5
            break
        fi
        # 進捗表示
        if [ $((WAITED % 60)) -eq 0 ]; then
            echo "  ${WAITED}秒経過..."
        fi
    done

    # 結果ファイルを収集
    TESTER_DIR="${MT5_DATA}/tester"
    if [ -d "${TESTER_DIR}" ]; then
        # 最新のレポートファイルをコピー
        LATEST=$(ls -t "${TESTER_DIR}"/*.xml 2>/dev/null | head -1)
        if [ -n "$LATEST" ]; then
            cp "$LATEST" "${RESULTS_DIR}/test${i}_report.xml"
            echo "  [${i}/8] 結果取得: $(basename $LATEST)"
        fi

        LATEST_HTM=$(ls -t "${TESTER_DIR}"/*.htm 2>/dev/null | head -1)
        if [ -n "$LATEST_HTM" ]; then
            cp "$LATEST_HTM" "${RESULTS_DIR}/test${i}_report.htm"
        fi
    fi

    sleep 3
done

# ============================================
#  結果サマリー生成
# ============================================

echo ""
echo "結果サマリーを生成中..."

cat > "${RESULTS_DIR}/summary.txt" << SUMMARY
# MT5 Backtest Results
Generated: $(date)
MT5 Data: ${MT5_DATA}

## Test Configurations
Test 1: BE=0.5, Partial=ON
Test 2: BE=0.5, Partial=OFF
Test 3: BE=1.0, Partial=ON
Test 4: BE=1.0, Partial=OFF
Test 5: BE=1.5, Partial=ON
Test 6: BE=1.5, Partial=OFF
Test 7: BE=2.0, Partial=ON
Test 8: BE=2.0, Partial=OFF

## Files
$(ls -la "${RESULTS_DIR}"/ 2>/dev/null)
SUMMARY

# ============================================
#  GitHubにプッシュ
# ============================================

echo ""
echo "GitHubにプッシュ中..."

cd "${REPO_PATH}"
git add mt5_results/
git commit -m "MT5 auto backtest results (Mac): 8 patterns - $(date +%Y%m%d_%H%M)"
git push origin main

echo ""
echo "============================================"
echo " 完了！"
echo " 結果: ${RESULTS_DIR}/"
echo " GitHubにプッシュ済み"
echo " Claude Codeで「結果を分析して」と伝えてください"
echo "============================================"
