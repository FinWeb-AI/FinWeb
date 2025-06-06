from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import yfinance as yf, pandas as pd, numpy as np, datetime

pf_bp = Blueprint("portfolio", __name__)

@pf_bp.route("/portfolio")
@login_required
def portfolio_home():
    return render_template("portfolio.html")

@pf_bp.route("/api/portfolio", methods=["POST"])
def api_portfolio():
    items = request.get_json(force=True, silent=True) or []
    items = [i for i in items if i.get("symbol") and i.get("weight")]

    if not items:
        return jsonify({"error":"no data"}), 400

    weights = np.array([i["weight"] for i in items], dtype=float)
    weights /= weights.sum() / 1.0

    end = datetime.date.today()
    start = end - datetime.timedelta(days=90)
    closes = {}
    for it in items:
        try:
            s = it["symbol"]
            closes[s] = yf.download(s, start=start, end=end)["Close"]
        except Exception:
            pass
    df = pd.DataFrame(closes).dropna()
    returns = df.pct_change().dropna()

    port_ret = returns.dot(weights)
    vol  = port_ret.std()*np.sqrt(252)
    VaR  = np.percentile(port_ret, 5)
    CVaR = port_ret[port_ret<=VaR].mean()

    corr = returns.corr().round(2).to_dict()

    return jsonify({
        "volatility": round(float(vol),4),
        "var": round(float(-VaR),4),
        "cvar": round(float(-CVaR),4),
        "corr": corr
    })
