# forecast.py
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
import yfinance as yf, pandas as pd, numpy as np, datetime, json, math

try:
    from prophet import Prophet
    _prophet = True
except Exception:
    _prophet = False
fc_bp = Blueprint("forecast", __name__)

@fc_bp.route("/forecast")
@login_required
def forecast():
    return render_template("forecast.html")

@fc_bp.route("/api/forecast")
def api_forecast():
    sym   = request.args.get("symbol","BTC-USD")
    period= int(request.args.get("period", "365"))
    df = yf.download(sym, period=f"{period}d")["Close"].dropna().reset_index()
    df.columns = ["ds","y"]

    if _prophet:
        m = Prophet(daily_seasonality=True)
        m.fit(df)
        fut  = m.make_future_dataframe(periods=7)
        pred = m.predict(fut)[["ds","yhat"]].tail(7)
        hist = df.tail(60)
    else:
        coef = np.polyfit(range(len(df)), df["y"], 1)
        fut_dates = [df["ds"].iloc[-1] + datetime.timedelta(days=i+1) for i in range(7)]
        yhat = [coef[0]*(len(df)+i)+coef[1] for i in range(7)]
        pred = pd.DataFrame({"ds":fut_dates, "yhat":yhat})
        hist = df.tail(60)
    return jsonify({
        "history": hist.to_dict("records"),
        "future" : pred.to_dict("records")
    })
