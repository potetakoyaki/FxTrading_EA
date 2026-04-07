# MT5 自動バックテスト

## セットアップ（1回だけ）

1. `mt5_auto` フォルダをWindowsの任意の場所にコピー
2. `run_all_tests.bat` をテキストエディタで開く
3. 2箇所を修正:

```
set MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```
→ MT5のインストール先を指定（terminal64.exeのフルパス）

```
set MT5_DATA=C:\Users\%USERNAME%\AppData\Roaming\MetaQuotes\Terminal\YOUR_TERMINAL_ID
```
→ MT5のデータフォルダパスを指定
  - MT5 → ファイル → データフォルダを開く でパスを確認
  - 例: `C:\Users\Taro\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075`

4. MT5を閉じる

## 実行

1. `run_all_tests.bat` をダブルクリック
2. 8テストが順番に自動実行される
3. 各テストでMT5が起動→バックテスト→自動終了を繰り返す
4. 結果は `MT5_DATA\tester\` フォルダに保存される

## テスト内容

| # | BE_ATR | PartialClose | 目的 |
|---|--------|-------------|------|
| 1 | 0.5 | ON | 現行v9.3設定 |
| 2 | 0.5 | OFF | Partial効果検証 |
| 3 | 1.0 | ON | BE緩和テスト |
| 4 | 1.0 | OFF | BE緩和+Partial OFF |
| 5 | 1.5 | ON | BE大幅緩和 |
| 6 | 1.5 | OFF | BE大幅緩和+Partial OFF |
| 7 | 2.0 | ON | BE最大緩和 |
| 8 | 2.0 | OFF | BE最大緩和+Partial OFF |
