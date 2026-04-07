@echo off
chcp 65001 > nul
echo ============================================
echo  AntigravityMTF Gold EA - 完全自動テスト
echo  8パターンテスト → 結果収集 → Git Push
echo ============================================

REM =============================================
REM  ここを自分の環境に合わせて変更（初回のみ）
REM =============================================
set MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
set MT5_DATA=C:\Users\%USERNAME%\AppData\Roaming\MetaQuotes\Terminal\YOUR_TERMINAL_ID
set REPO_PATH=%~dp0..
REM =============================================

set RESULTS_DIR=%REPO_PATH%\mt5_results
if not exist "%RESULTS_DIR%" mkdir "%RESULTS_DIR%"

REM 古い結果を削除
del /Q "%RESULTS_DIR%\*" 2>nul

echo.
echo MT5: %MT5_PATH%
echo Data: %MT5_DATA%
echo Repo: %REPO_PATH%
echo Results: %RESULTS_DIR%
echo.

REM === 8テスト実行 ===
for /L %%i in (1,1,8) do (
    echo [%%i/8] テスト実行中...
    copy /Y "%~dp0ini\test%%i.ini" "%MT5_DATA%\config\tester.ini" > nul
    start /wait "" "%MT5_PATH%" /config:config\tester.ini

    REM レポートをコピー
    if exist "%MT5_DATA%\tester\test%%i*.xml" (
        copy /Y "%MT5_DATA%\tester\test%%i*.xml" "%RESULTS_DIR%\test%%i.xml" > nul
        echo   [%%i/8] XML結果コピー完了
    )
    if exist "%MT5_DATA%\tester\test%%i*.htm" (
        copy /Y "%MT5_DATA%\tester\test%%i*.htm" "%RESULTS_DIR%\test%%i.htm" > nul
        echo   [%%i/8] HTML結果コピー完了
    )

    REM testerフォルダの最新レポートもコピー
    for /f "delims=" %%f in ('dir /b /od "%MT5_DATA%\tester\*.xml" 2^>nul') do set LATEST_XML=%%f
    if defined LATEST_XML (
        copy /Y "%MT5_DATA%\tester\%LATEST_XML%" "%RESULTS_DIR%\test%%i_report.xml" > nul
    )
    set LATEST_XML=

    timeout /t 3 /nobreak > nul
)

echo.
echo ============================================
echo  全テスト完了。結果をGitHubにプッシュ中...
echo ============================================

REM 結果一覧を生成
echo # MT5 Backtest Results > "%RESULTS_DIR%\summary.txt"
echo Generated: %date% %time% >> "%RESULTS_DIR%\summary.txt"
echo. >> "%RESULTS_DIR%\summary.txt"
dir /b "%RESULTS_DIR%\*" >> "%RESULTS_DIR%\summary.txt"

REM Git push
cd /d "%REPO_PATH%"
git add mt5_results\
git commit -m "MT5 auto backtest results: 8 patterns (BE x Partial)"
git push origin main

echo.
echo ============================================
echo  完了！結果がGitHubにプッシュされました
echo  Claude Codeで分析を依頼してください
echo ============================================
pause
