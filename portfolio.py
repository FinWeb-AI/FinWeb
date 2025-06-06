from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import yfinance as yf, pandas as pd, numpy as np, datetime, warnings

warnings.filterwarnings("ignore", category=UserWarning, module="yfinance")

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
        return jsonify({"error": "請至少輸入一個標的"}), 400

    weights = np.array([i["weight"] for i in items], dtype=float)
    if weights.sum() == 0:
        return jsonify({"error": "權重不得全為 0"}), 400
    weights /= weights.sum()

    end = datetime.date.today()
    start = end - datetime.timedelta(days=120)
    closes = {}

    for i in items:
        sym = i["symbol"].strip().upper()
        try:
            df = yf.download(sym, start=start, end=end, progress=False)
            series = df["Close"].dropna()
            if not series.empty:
                closes[sym] = series.rename(sym)
        except Exception:
            continue

    if len(closes) < 2:
        return jsonify({"error": "無足夠有效資料可計算（至少需要 2 檔）"}), 400

    df = pd.concat(closes.values(), axis=1, keys=closes.keys()).dropna()
    returns = df.pct_change().dropna()

    port_ret = returns.dot(weights[: len(returns.columns)])
    vol = port_ret.std() * np.sqrt(252)
    var = np.percentile(port_ret, 5)
    cvar = port_ret[port_ret <= var].mean()
    corr = returns.corr().round(2).to_dict()

    return jsonify(
        {
            "volatility": round(float(vol), 4),
            "var": round(float(-var), 4),
            "cvar": round(float(-cvar), 4),
            "corr": corr,
        }
    )
