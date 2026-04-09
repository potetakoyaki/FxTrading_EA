#!/bin/bash
# ============================================
#  AntigravityMTF Gold EA - 完全自動テスト (Mac)
#  8パターンテスト → 結果収集 → Git Push
# ============================================

# =============================================
#  ここを自分の環境に合わせて変更（初回のみ）
# =============================================

# MT5がMac上でどう動いているか:
# 1. Parallels/VMware上のWindows
# 2. CrossOver/Wine
# 3. リモートデスクトップ(VPS)

# Parallelsの場合:
# VM_NAME="Windows 11"
# MT5_WIN_PATH="C:\\Program Files\\MetaTrader 5\\terminal64.exe"
# MT5_WIN_DATA="C:\\Users\\YOUR_USER\\AppData\\Roaming\\MetaQuotes\\Terminal\\YOUR_ID"

# VPSの場合:
VPS_HOST=""          # 例: user@123.45.67.89
VPS_MT5_PATH=""      # 例: /home/user/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe
VPS_MT5_DATA=""      # 例: /home/user/.wine/drive_c/Users/user/AppData/Roaming/MetaQuotes/Terminal/YOUR_ID

# リポジトリのパス (このスクリプトの親ディレクトリ)
REPO_PATH="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="${REPO_PATH}/mt5_results"
INI_DIR="$(dirname "$0")/ini"

# =============================================

echo "============================================"
echo " AntigravityMTF Gold EA - 完全自動テスト"
echo " 8パターンテスト → 結果収集 → Git Push"
echo "============================================"
echo ""
echo "Repo: ${REPO_PATH}"
echo "Results: ${RESULTS_DIR}"
echo ""

mkdir -p "${RESULTS_DIR}"
rm -f "${RESULTS_DIR}"/*.xml "${RESULTS_DIR}"/*.htm "${RESULTS_DIR}"/*.html 2>/dev/null

# =============================================
#  MT5の実行方法を選択
# =============================================

# --- 方法A: Mac上のParallels ---
run_parallels() {
    local ini_file="$1"
    local test_name="$2"

    # iniファイルをParallels共有フォルダ経由でコピー
    cp "${ini_file}" "/Volumes/[C] Windows/mt5_test/tester.ini" 2>/dev/null || \
    cp "${ini_file}" "$HOME/Parallels/Windows/mt5_test/tester.ini" 2>/dev/null

    # Parallels経由でMT5実行
    prlctl exec "${VM_NAME}" cmd /c "\"${MT5_WIN_PATH}\" /config:C:\\mt5_test\\tester.ini" 2>/dev/null

    # 結果コピー
    sleep 5
}

# --- 方法B: SSH経由でVPS ---
run_vps() {
    local ini_file="$1"
    local test_num="$2"

    # iniファイルをVPSに転送
    scp "${ini_file}" "${VPS_HOST}:/tmp/tester.ini"

    # VPS上でMT5実行 (Wine経由)
    ssh "${VPS_HOST}" << REMOTE
        cp /tmp/tester.ini "${VPS_MT5_DATA}/config/tester.ini"
        cd "$(dirname "${VPS_MT5_PATH}")"
        wine64 terminal64.exe /config:config/tester.ini &
        WINE_PID=\$!

        # テスト完了を待つ (最大30分)
        for i in \$(seq 1 360); do
            sleep 5
            if ! kill -0 \$WINE_PID 2>/dev/null; then
                break
            fi
        done

        # 結果ファイルを探す
        ls -t "${VPS_MT5_DATA}/tester/"*.xml 2>/dev/null | head -1
REMOTE

    # 結果をダウンロード
    REMOTE_RESULT=$(ssh "${VPS_HOST}" "ls -t ${VPS_MT5_DATA}/tester/*.xml 2>/dev/null | head -1")
    if [ -n "${REMOTE_RESULT}" ]; then
        scp "${VPS_HOST}:${REMOTE_RESULT}" "${RESULTS_DIR}/test${test_num}_report.xml"
        echo "  [${test_num}/8] 結果ダウンロード完了"
    fi
}

# --- 方法C: MetaTrader5 Pythonパッケージ (Mac native) ---
run_python_mt5() {
    echo "  Python MetaTrader5パッケージはMacネイティブでは動作しません"
    echo "  Parallels/VPS方式を使ってください"
    exit 1
}

# =============================================
#  テスト実行
# =============================================

echo ""
echo "MT5の実行環境を選択してください:"
echo "  1) Parallels/VMware (Mac上のWindows VM)"
echo "  2) VPS (SSH経由のリモートサーバー)"
echo "  3) 手動 (iniファイルだけ生成)"
echo ""
read -p "選択 (1/2/3): " CHOICE

for i in $(seq 1 8); do
    echo "[${i}/8] テスト実行中..."

    case "${CHOICE}" in
        1) run_parallels "${INI_DIR}/test${i}.ini" "${i}" ;;
        2) run_vps "${INI_DIR}/test${i}.ini" "${i}" ;;
        3)
            echo "  ini/${i}.ini を MT5 の config/tester.ini にコピーして手動実行してください"
            echo "  結果を mt5_results/test${i}_report.xml として保存してください"
            ;;
        *) echo "無効な選択"; exit 1 ;;
    esac
done

if [ "${CHOICE}" = "3" ]; then
    echo ""
    echo "手動テスト完了後、このスクリプトを再実行するか、"
    echo "以下のコマンドで結果をプッシュしてください:"
    echo ""
    echo "  cd ${REPO_PATH}"
    echo "  git add mt5_results/"
    echo "  git commit -m 'MT5 backtest results'"
    echo "  git push origin main"
    echo ""
    read -p "結果ファイルを配置しましたか？ (y/n): " READY
    if [ "${READY}" != "y" ]; then
        echo "中断しました"
        exit 0
    fi
fi

# =============================================
#  結果をGitHubにプッシュ
# =============================================

echo ""
echo "============================================"
echo " 結果をGitHubにプッシュ中..."
echo "============================================"

# サマリー生成
echo "# MT5 Backtest Results" > "${RESULTS_DIR}/summary.txt"
echo "Generated: $(date)" >> "${RESULTS_DIR}/summary.txt"
echo "" >> "${RESULTS_DIR}/summary.txt"
echo "Files:" >> "${RESULTS_DIR}/summary.txt"
ls -la "${RESULTS_DIR}"/ >> "${RESULTS_DIR}/summary.txt"

cd "${REPO_PATH}"
git add mt5_results/
git commit -m "MT5 auto backtest results: 8 patterns (BE x Partial) - $(date +%Y%m%d)"
git push origin main

echo ""
echo "============================================"
echo " 完了！結果がGitHubにプッシュされました"
echo " Claude Codeで「結果を分析して」と伝えてください"
echo "============================================"
