from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import yfinance as yf, pandas as pd, numpy as np

bt_bp = Blueprint("backtest", __name__)

@bt_bp.route("/backtest")
@login_required
def backtest_home():
    return render_template("backtest.html")

@bt_bp.route("/api/backtest")
def api_backtest():
    sym  = request.args.get("symbol","AAPL")
    strat= request.args.get("strategy","dma")
    df   = yf.download(sym, period="2y")["Close"].dropna().to_frame("close")
    df["ret"] = df["close"].pct_change().fillna(0)

    if strat == "dma":
        df["ma_s"] = df["close"].rolling(20).mean()
        df["ma_l"] = df["close"].rolling(60).mean()
        df["pos"]  = np.where(df["ma_s"]>df["ma_l"], 1, 0)
    elif strat == "rsi":
        delta = df["close"].diff()
        up = delta.clip(lower=0).rolling(14).mean()
        dn = (-delta.clip(upper=0)).rolling(14).mean()
        rsi=100-100/(1+up/dn)
        df["pos"] = np.where(rsi<30,1,np.where(rsi>70,0,np.nan))
        df["pos"].ffill(inplace=True)
    else:
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        macd  = ema12-ema26
        signal= macd.ewm(span=9, adjust=False).mean()
        df["pos"]=np.where(macd>signal,1,0)

    df["pos"].iloc[0]=0
    df["strategy_ret"] = df["pos"].shift(1)*df["ret"]
    df["equity"] = (1+df["strategy_ret"]).cumprod()
    equity = df["equity"].tolist()
    dates  = df.index.strftime("%Y-%m-%d").tolist()

    total_return = equity[-1]-1
    sharpe = (df["strategy_ret"].mean()/df["strategy_ret"].std())*np.sqrt(252)
    dd = 1 - df["equity"]/df["equity"].cummax()
    max_dd = dd.max()

    return jsonify({
        "dates": dates,
        "equity": equity,
        "total_return": round(float(total_return),4),
        "sharpe": round(float(sharpe),2) if not np.isnan(sharpe) else 0,
        "max_dd": round(float(max_dd),4)
    })
