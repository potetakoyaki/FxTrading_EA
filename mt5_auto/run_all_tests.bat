@echo off
chcp 65001
echo ============================================
echo  AntigravityMTF Gold EA - 自動バックテスト
echo  8パターンを順番にテストします
echo ============================================

REM === ここを自分の環境に合わせて変更 ===
set MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
set SCRIPT_DIR=%~dp0

REM MT5のデータフォルダのパス (ファイル→データフォルダを開く で確認)
set MT5_DATA=C:\Users\%USERNAME%\AppData\Roaming\MetaQuotes\Terminal\YOUR_TERMINAL_ID

echo.
echo [注意] MT5_PATH と MT5_DATA を自分の環境に合わせてください
echo MT5_PATH: %MT5_PATH%
echo MT5_DATA: %MT5_DATA%
echo.
pause

REM テスト1: BE=0.5, Partial=ON
echo [1/8] BE=0.5, Partial=ON ...
copy /Y "%SCRIPT_DIR%ini\test1.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト2: BE=0.5, Partial=OFF
echo [2/8] BE=0.5, Partial=OFF ...
copy /Y "%SCRIPT_DIR%ini\test2.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト3: BE=1.0, Partial=ON
echo [3/8] BE=1.0, Partial=ON ...
copy /Y "%SCRIPT_DIR%ini\test3.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト4: BE=1.0, Partial=OFF
echo [4/8] BE=1.0, Partial=OFF ...
copy /Y "%SCRIPT_DIR%ini\test4.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト5: BE=1.5, Partial=ON
echo [5/8] BE=1.5, Partial=ON ...
copy /Y "%SCRIPT_DIR%ini\test5.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト6: BE=1.5, Partial=OFF
echo [6/8] BE=1.5, Partial=OFF ...
copy /Y "%SCRIPT_DIR%ini\test6.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト7: BE=2.0, Partial=ON
echo [7/8] BE=2.0, Partial=ON ...
copy /Y "%SCRIPT_DIR%ini\test7.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

REM テスト8: BE=2.0, Partial=OFF
echo [8/8] BE=2.0, Partial=OFF ...
copy /Y "%SCRIPT_DIR%ini\test8.ini" "%MT5_DATA%\config\tester.ini"
"%MT5_PATH%" /config:tester.ini
timeout /t 5

echo.
echo ============================================
echo  全8テスト完了
echo  結果は MT5_DATA\tester\ フォルダにあります
echo ============================================
pause
