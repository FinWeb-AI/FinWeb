import os
import argparse
import datetime

import joblib
import yfinance as yf
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

def fetch_multiple_historical_prices(symbols: list[str], days: int = 1000) -> pd.DataFrame:
    """
    使用 yfinance 一次性下載多個 symbols 的歷史股價 (Close)，
    回傳 DataFrame，columns = MultiIndex [(symbol, 'Open'), (symbol, 'High'), (symbol, 'Low'), (symbol, 'Close'), ...]
    index = 日期 (DatetimeIndex)。如果下載失敗拋出 ValueError。
    """
    end_date = datetime.datetime.now().date()
    start_date = end_date - datetime.timedelta(days=int(days * 1.5))

    df_all = yf.download(
        tickers=symbols,
        start=start_date.strftime("%Y-%m-%d"),
        end=(end_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
        threads=True,
        group_by='ticker',
    )

    if df_all is None or df_all.empty:
        raise ValueError(f"無法用 yfinance 一次性取得這些股票的歷史股價。")

    return df_all


def prepare_features_labels(df_close: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    以 df_close 只含一檔股票的 Close 欄，製作特徵 X、標籤 y：
      – 特徵：過去 10 天的對數報酬率 log_return = ln(c_t / c_{t-1})
      – 標籤：明天收盤價上漲(1) 或 下跌/持平(0)
    回傳：
      – X.shape = (N_samples, 10)
      – y.shape = (N_samples,)
    """
    if df_close.shape[0] < 11:
        raise ValueError(f"資料筆數不足 (至少要 11 筆)，目前只有 {df_close.shape[0]} 筆。")

    closes: np.ndarray = df_close.to_numpy(dtype=float).ravel()
    log_returns: np.ndarray = np.log(closes[1:] / closes[:-1])

    X_list, y_list = [], []
    for i in range(9, len(log_returns) - 1):
        feat = log_returns[i - 9 : i + 1]
        label = 1 if log_returns[i + 1] > 0 else 0
        X_list.append(feat)
        y_list.append(label)

    X: np.ndarray = np.stack(X_list, axis=0)
    y: np.ndarray = np.array(y_list, dtype=int)
    return X, y


def train_and_save_model_per_symbol(symbol: str, df_all: pd.DataFrame, days: int = 1000):
    """
    1. 從 df_all（一次性抓下來的所有股票）、找出 symbol 的 Close 欄位
    2. 產生特徵 X, y
    3. 訓練 RandomForestClassifier
    4. 儲存模型到 models/{symbol}_model.pkl
    """

    if not isinstance(df_all.columns, pd.MultiIndex):
        raise ValueError("df_all 欄位不是 MultiIndex，不知道哪個層級是 ticker。")

    try:
        df_close = df_all[(symbol, "Close")].dropna()
    except KeyError:
        raise ValueError(f"在 df_all 中找不到 {symbol} 的 Close 資料。")

    if df_close.shape[0] < days:
        print(f"[警告] {symbol} 實際交易日 {df_close.shape[0]} < {days}，將以現有全部資料訓練。")
    else:

        df_close = df_close.iloc[-days:]

    X, y = prepare_features_labels(pd.DataFrame({"Close": df_close}))

    if X.ndim != 2 or y.ndim != 1:
        raise ValueError(f"{symbol} 特徵維度錯誤：X.ndim={X.ndim}, y.ndim={y.ndim}。")

    if X.shape[0] < 50:
        print(f"[警告] {symbol} 特徵樣本數不足 (僅 {X.shape[0]} 筆)，跳過訓練。")
        return

    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf.fit(X_train, y_train)

    acc = clf.score(X_test, y_test)
    print(f"{symbol} RandomForest 測試集準確度: {acc:.3f}")

    os.makedirs("models", exist_ok=True)
    model_path = os.path.join("models", f"{symbol}_model.pkl")
    joblib.dump(clf, model_path)
    print(f"已儲存模型到：{model_path}")


def main():
    parser = argparse.ArgumentParser(
        description="訓練並儲存多檔台股機器學習模型 (使用 yfinance 批次下載 + Pandas)"
    )
    parser.add_argument(
        "--tickers-file",
        type=str,
        default="taiwan_tickers.txt",
        help="要訓練的股票代號清單（每行一個，格式須加上 .TW，例如 2330.TW）"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1000,
        help="要下載的近 {days} 個交易日資料 (預設 1000)。"
    )
    args = parser.parse_args()

    if not os.path.exists(args.tickers_file):
        print(f"找不到檔案：{args.tickers_file}")
        return

    with open(args.tickers_file, "r", encoding="utf-8") as f:
        symbols = [line.strip().upper() for line in f if line.strip()]
    if not symbols:
        print("ticker 清單為空，請檢查檔案內容。")
        return

    try:
        print(f"正在一次性下載 {len(symbols)} 檔台股的歷史股價（最近 {args.days} 個交易日）...")
        df_all = fetch_multiple_historical_prices(symbols, days=args.days)
        print("下載完成。")
    except Exception as e:
        print(f"下載過程出錯：{e}")
        return

    for sym in symbols:
        try:
            print(f"\n--- 開始訓練 {sym} ---")
            train_and_save_model_per_symbol(sym, df_all, days=args.days)
        except Exception as e:
            print(f"[錯誤] {sym} 訓練失敗：{e}")

if __name__ == "__main__":
    main()
