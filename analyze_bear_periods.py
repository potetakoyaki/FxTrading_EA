"""
ベア相場期間の詳細分析スクリプト
10年分バックテストデータからベア相場での成績を検証
"""
import pandas as pd
import numpy as np
from datetime import datetime
from backtest_csv import load_csv, generate_h4_from_d1, generate_h1_from_h4, generate_m15_from_h1, merge_and_fill
from backtest_gold import GoldConfig, GoldBacktester

# ============================================================
# ゴールドの主要ベア相場期間 (10年間)
# ============================================================
BEAR_PERIODS = {
    "2016 Brexit後調整": ("2016-07-01", "2016-12-31"),      # $1375→$1125 (-18%)
    "2017 FRB利上げ": ("2017-09-01", "2017-12-31"),          # $1350→$1240 (-8%)
    "2018 ドル高ベア": ("2018-04-01", "2018-09-30"),         # $1350→$1180 (-13%)
    "2020 コロナ暴落": ("2020-03-01", "2020-03-31"),         # 急落フェーズ
    "2020 夏高値調整": ("2020-08-01", "2020-11-30"),         # $2075→$1764 (-15%)
    "2022 FRB利上げベア": ("2022-03-01", "2022-09-30"),      # $2070→$1620 (-22%) ★最大
    "2022-2023 長期低迷": ("2022-04-01", "2023-09-30"),      # 18ヶ月の低迷期
    "2025 調整局面": ("2025-03-01", "2025-03-31"),           # 短期急落
}

def analyze_bear_periods():
    print("=" * 70)
    print(" ベア相場期間の詳細分析 (XAUUSD 10年データ)")
    print("=" * 70)

    # データ読み込み
    m15_real = load_csv("XAUUSD_M15.csv")
    h1_real = load_csv("XAUUSD_H1.csv")
    h4_real = load_csv("XAUUSD_H4.csv")
    d1_real = load_csv("XAUUSD_D1.csv")
    usdjpy_h1 = load_csv("USDJPY_H1.csv")
    usdjpy_h4 = load_csv("USDJPY_H4.csv")
    usdjpy_d1 = load_csv("USDJPY_D1.csv")

    # 補間
    h4 = h4_real
    if d1_real is not None:
        h4 = merge_and_fill(h4_real, generate_h4_from_d1(d1_real))
    h1 = merge_and_fill(h1_real, generate_h1_from_h4(h4))
    m15 = merge_and_fill(m15_real, generate_m15_from_h1(h1))
    usdjpy = usdjpy_h1
    if usdjpy_h4 is not None:
        usdjpy = merge_and_fill(usdjpy_h1, generate_h1_from_h4(usdjpy_h4))

    print(f"\n  データ期間: {h4.index[0]} ~ {h4.index[-1]}")
    print(f"  H4: {len(h4):,} bars, H1: {len(h1):,} bars, M15: {len(m15):,} bars")

    # フルバックテスト実行
    print("\n  フルバックテスト実行中...")
    cfg = GoldConfig()
    bt = GoldBacktester(cfg)
    bt.run(h4, h1, m15, usdjpy_df=usdjpy)

    trades = bt.trades
    if not trades:
        print("  [ERR] No trades")
        return

    trades_df = pd.DataFrame(trades)
    trades_df["open_time"] = pd.to_datetime(trades_df["open_time"])
    trades_df["close_time"] = pd.to_datetime(trades_df["close_time"])

    print(f"\n  全トレード数: {len(trades_df):,}")
    print(f"  全体勝率: {len(trades_df[trades_df['pnl_jpy']>0])/len(trades_df)*100:.1f}%")
    print(f"  全体PnL: {trades_df['pnl_jpy'].sum():+,.0f} JPY")

    # ============================================================
    # 各ベア相場期間の分析
    # ============================================================
    print("\n" + "=" * 70)
    print(" 各ベア相場期間のパフォーマンス")
    print("=" * 70)

    summary_rows = []

    for name, (start, end) in BEAR_PERIODS.items():
        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)

        # 期間内のトレード
        mask = (trades_df["open_time"] >= start_dt) & (trades_df["open_time"] <= end_dt)
        period_trades = trades_df[mask]

        if len(period_trades) == 0:
            print(f"\n  [{name}] ({start} ~ {end}): トレードなし")
            continue

        total_pnl = period_trades["pnl_jpy"].sum()
        wins = len(period_trades[period_trades["pnl_jpy"] > 0])
        losses = len(period_trades[period_trades["pnl_jpy"] <= 0])
        win_rate = wins / len(period_trades) * 100
        n_trades = len(period_trades)

        # BUY/SELL内訳
        buys = period_trades[period_trades["direction"] == "BUY"]
        sells = period_trades[period_trades["direction"] == "SELL"]
        buy_pnl = buys["pnl_jpy"].sum()
        sell_pnl = sells["pnl_jpy"].sum()
        buy_wr = len(buys[buys["pnl_jpy"] > 0]) / len(buys) * 100 if len(buys) > 0 else 0
        sell_wr = len(sells[sells["pnl_jpy"] > 0]) / len(sells) * 100 if len(sells) > 0 else 0

        # 勝ちトレード平均 / 負けトレード平均
        avg_win = period_trades[period_trades["pnl_jpy"] > 0]["pnl_jpy"].mean() if wins > 0 else 0
        avg_loss = period_trades[period_trades["pnl_jpy"] <= 0]["pnl_jpy"].mean() if losses > 0 else 0

        # 最大連敗
        consec_loss = 0
        max_consec_loss = 0
        for _, t in period_trades.iterrows():
            if t["pnl_jpy"] <= 0:
                consec_loss += 1
                max_consec_loss = max(max_consec_loss, consec_loss)
            else:
                consec_loss = 0

        # 期間内最大DD (簡易計算)
        cum_pnl = period_trades["pnl_jpy"].cumsum()
        running_max = cum_pnl.cummax()
        dd = (running_max - cum_pnl)
        max_dd = dd.max()

        # Gold価格変動
        h4_period = h4[(h4.index >= start_dt) & (h4.index <= end_dt)]
        if len(h4_period) > 0:
            gold_start = h4_period["Close"].iloc[0]
            gold_end = h4_period["Close"].iloc[-1]
            gold_change = (gold_end - gold_start) / gold_start * 100
        else:
            gold_start = gold_end = gold_change = 0

        # SL理由の内訳
        sl_trades = period_trades[period_trades["reason"] == "SL"]
        sl_count = len(sl_trades)
        sl_pnl = sl_trades["pnl_jpy"].sum()

        print(f"\n  {'='*60}")
        print(f"  [{name}] ({start} ~ {end})")
        print(f"  Gold: ${gold_start:.0f} → ${gold_end:.0f} ({gold_change:+.1f}%)")
        print(f"  {'='*60}")
        print(f"    トレード数: {n_trades} ({wins}W/{losses}L)")
        print(f"    勝率: {win_rate:.1f}%")
        print(f"    総損益: {total_pnl:+,.0f} JPY")
        print(f"    平均勝ち: {avg_win:+,.0f} JPY / 平均負け: {avg_loss:+,.0f} JPY")
        print(f"    BUY:  {len(buys)}件 WR={buy_wr:.1f}% PnL={buy_pnl:+,.0f} JPY")
        print(f"    SELL: {len(sells)}件 WR={sell_wr:.1f}% PnL={sell_pnl:+,.0f} JPY")
        print(f"    SL損切り: {sl_count}件 / {sl_pnl:+,.0f} JPY")
        print(f"    最大連敗: {max_consec_loss}")
        print(f"    期間内最大DD: {max_dd:,.0f} JPY")

        summary_rows.append({
            "Period": name,
            "Gold%": f"{gold_change:+.1f}%",
            "Trades": n_trades,
            "WinRate": f"{win_rate:.1f}%",
            "PnL": total_pnl,
            "BUY_PnL": buy_pnl,
            "SELL_PnL": sell_pnl,
            "MaxDD": max_dd,
            "MaxConsecLoss": max_consec_loss,
        })

    # ============================================================
    # サマリーテーブル
    # ============================================================
    print("\n\n" + "=" * 70)
    print(" ベア相場サマリー")
    print("=" * 70)
    print(f"  {'期間':<24} {'Gold変動':>8} {'取引':>5} {'勝率':>6} {'損益(JPY)':>14} {'最大DD':>12}")
    print("  " + "-" * 75)
    total_bear_pnl = 0
    total_bear_trades = 0
    for r in summary_rows:
        print(f"  {r['Period']:<24} {r['Gold%']:>8} {r['Trades']:>5} {r['WinRate']:>6} {r['PnL']:>+14,.0f} {r['MaxDD']:>12,.0f}")
        total_bear_pnl += r["PnL"]
        total_bear_trades += r["Trades"]

    print("  " + "-" * 75)
    print(f"  {'ベア相場合計':<24} {'':>8} {total_bear_trades:>5} {'':>6} {total_bear_pnl:>+14,.0f}")

    # 非ベア相場期間
    non_bear_pnl = trades_df["pnl_jpy"].sum() - total_bear_pnl
    non_bear_trades = len(trades_df) - total_bear_trades
    print(f"  {'非ベア相場合計':<22} {'':>8} {non_bear_trades:>5} {'':>6} {non_bear_pnl:>+14,.0f}")
    print(f"  {'全期間合計':<24} {'':>8} {len(trades_df):>5} {'':>6} {trades_df['pnl_jpy'].sum():>+14,.0f}")

    # ============================================================
    # 年次パフォーマンス
    # ============================================================
    print("\n\n" + "=" * 70)
    print(" 年次パフォーマンス")
    print("=" * 70)
    trades_df["year"] = trades_df["open_time"].dt.year
    for year in sorted(trades_df["year"].unique()):
        yt = trades_df[trades_df["year"] == year]
        y_pnl = yt["pnl_jpy"].sum()
        y_wr = len(yt[yt["pnl_jpy"] > 0]) / len(yt) * 100
        y_buys = yt[yt["direction"] == "BUY"]
        y_sells = yt[yt["direction"] == "SELL"]
        print(f"  {year}: {len(yt):>5}件 WR={y_wr:.1f}% PnL={y_pnl:>+12,.0f} JPY  BUY={len(y_buys):>4}件({y_buys['pnl_jpy'].sum():>+10,.0f})  SELL={len(y_sells):>4}件({y_sells['pnl_jpy'].sum():>+10,.0f})")

    # ============================================================
    # 最悪の連続損失期間
    # ============================================================
    print("\n\n" + "=" * 70)
    print(" 最悪の連続損失月間 (3ヶ月以上)")
    print("=" * 70)
    trades_df["month"] = trades_df["open_time"].dt.to_period("M")
    monthly_pnl = trades_df.groupby("month")["pnl_jpy"].sum()

    consec_loss_months = []
    current_streak = []
    for m, pnl in monthly_pnl.items():
        if pnl < 0:
            current_streak.append((str(m), pnl))
        else:
            if len(current_streak) >= 3:
                consec_loss_months.append(current_streak)
            current_streak = []
    if len(current_streak) >= 3:
        consec_loss_months.append(current_streak)

    for streak in consec_loss_months:
        total = sum(p for _, p in streak)
        print(f"\n  {streak[0][0]} ~ {streak[-1][0]} ({len(streak)}ヶ月連続マイナス)")
        print(f"  合計損失: {total:+,.0f} JPY")
        for m, p in streak:
            print(f"    {m}: {p:+,.0f} JPY")


if __name__ == "__main__":
    analyze_bear_periods()
