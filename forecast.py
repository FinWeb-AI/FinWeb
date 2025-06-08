import functools, json, logging, re, requests, traceback
import pandas as pd
import numpy as np
import datetime, math

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
import yfinance as yf

import configparser
cfg = configparser.ConfigParser()
cfg.read("config.ini", encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("forecast")

try:
    from prophet import Prophet
    _HAS_PROPHET = True
except ImportError:
    _HAS_PROPHET = False

fc_bp = Blueprint("forecast", __name__, template_folder="templates")

_FALLBACK50 = [
    ("2330","台積電"),("2317","鴻海"),("2454","聯發科"),("2308","台達電"),
    ("2882","國泰金"),("2303","聯電"),("2412","中華電"),("1301","台塑"),
]

def _parse_twse_json(js):
    if not js: return []
    keys = js[0].keys()
    code = next((k for k in keys if re.search("代號|code", k, re.I)), None)
    name = next((k for k in keys if re.search("名稱|name", k, re.I)), None)
    if not code or not name:
        raise KeyError("找不到代號/名稱欄位")
    return [(d[code], d[name]) for d in js]

def _dl_twse_list():
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L?TYPEK=%E6%AD%A3%E8%82%A1%2CETF"
    try:
        return _parse_twse_json(requests.get(url, timeout=8).json())
    except Exception as e:
        log.warning(f"TWSE 失敗：{e}")
        return []

def _dl_finmind_list():
    url = "https://api.finmindtrade.com/v4/data?dataset=TaiwanStockInfo"
    try:
        txt = requests.get(url, timeout=8).text.lstrip("\ufeff")
        if not txt.strip().startswith("{"):
            txt = txt[txt.find("{"):]
        js = json.loads(txt)
        if js.get("status")!=200 or not js.get("data"):
            raise RuntimeError(f"FinMind status={js.get('status')}")
        return [(d["stock_id"], d["stock_name"]) for d in js["data"]]
    except Exception as e:
        log.warning(f"FinMind 失敗：{e}")
        return []

@functools.lru_cache(maxsize=1)
def _get_tw_symbols():
    data = _dl_twse_list()
    if data:
        log.info(f"TWSE 取得 {len(data)} 檔")
        return data
    data = _dl_finmind_list()
    if data:
        log.info(f"FinMind 取得 {len(data)} 檔")
        return data
    log.warning("兩來源皆失敗 → fallback50")
    return _FALLBACK50

def fetch_crypto_symbols():
    out=[]; url_tmpl = ("https://api.coingecko.com/api/v3/coins/markets"
                        "?vs_currency=usd&order=market_cap_desc&per_page=250&page={}")
    for pg in range(1,5):
        js = requests.get(url_tmpl.format(pg), timeout=10).json()
        if not js: break
        for it in js:
            sym = it["symbol"].upper()+"-USD"
            out.append({"value":sym, "text":f"{sym} ({it['name']})"})
    return out

def fetch_tw_symbols():
    return [{"value":f"{c}.TW","text":f"{c}.TW ({n})"} for c,n in _get_tw_symbols()]

def fetch_us_symbols():
    out=[]; hdr={"User-Agent":"Mozilla/5.0"}
    for exch in ("NASDAQ","NYSE"):
        url=f"https://api.nasdaq.com/api/screener/stocks?exchange={exch}&download=true"
        try:
            js = requests.get(url,headers=hdr,timeout=10).json()
            for it in js.get("data",{}).get("rows",[]):
                s,n=it.get("symbol"),it.get("name")
                if s and n:
                    out.append({"value":s,"text":f"{s} ({n})"})
        except: pass
    return out

@fc_bp.route("/symbols.json")
@login_required
def api_symbols():
    return jsonify({
        "加密貨幣": fetch_crypto_symbols(),
        "台股":    fetch_tw_symbols(),
        "美股":    fetch_us_symbols()
    })


@fc_bp.route("/forecast")
@login_required
def forecast():
    return render_template("forecast.html")

@fc_bp.route("/api/forecast")
@login_required
def api_forecast():
    try:
        sym    = request.args.get("symbol","BTC-USD")
        hist_d = int(request.args.get("history","365"))
        fut_d  = int(request.args.get("forecast","30"))
        model  = request.args.get("model","prophet")

        raw = yf.download(sym, period=f"{hist_d}d", progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            series = raw["Close"][sym]
            df = pd.DataFrame({"ds": series.index, "y": series.values})
        else:
            tmp = raw.reset_index()[["Date","Close"]]
            tmp.columns = ["ds","y"]
            df = tmp
        df["y"] = df["y"].astype(float)
        df = df.dropna().reset_index(drop=True)

        if model=="prophet" and _HAS_PROPHET:
            try:
                m = Prophet(daily_seasonality=True,
                            weekly_seasonality=True,
                            yearly_seasonality=True)
                m.add_country_holidays(country_name="TW")
                m.fit(df)
                fut  = m.make_future_dataframe(periods=fut_d)
                pred = m.predict(fut)[["ds","yhat","yhat_lower","yhat_upper"]]
                hist_df, future_df = df.copy(), pred.tail(fut_d)
            except Exception as e:
                log.warning(f"Prophet 失敗，切 Linear：{e}")
                model = "linear"

        if model!="prophet" or not _HAS_PROPHET:
            x   = np.arange(len(df))
            a,b = np.polyfit(x, df["y"], 1)
            last = df["ds"].iloc[-1]
            dates = [last + datetime.timedelta(days=i+1) for i in range(fut_d)]
            yhat   = a*(len(df)+np.arange(fut_d)) + b
            hist_df   = df.copy()
            future_df = pd.DataFrame({
                "ds": dates,
                "yhat": yhat,
                "yhat_lower": yhat*0.95,
                "yhat_upper": yhat*1.05
            })

        history = [{"ds": r["ds"].strftime("%Y-%m-%d"), "y": float(r["y"])}
                   for r in hist_df.to_dict("records")]
        future  = [{
            "ds": r["ds"].strftime("%Y-%m-%d"),
            "yhat": float(r["yhat"]),
            "yhat_lower": float(r["yhat_lower"]),
            "yhat_upper": float(r["yhat_upper"])
        } for r in future_df.to_dict("records")]

        n    = min(fut_d, len(hist_df))
        tv   = np.array([h["y"]    for h in history[-n:]])
        pv   = np.array([f["yhat"] for f in future[:n]])
        mape = float(np.mean(np.abs(tv-pv)/tv)*100)
        rmse = float(math.sqrt(np.mean((tv-pv)**2)))

        return jsonify({
            "symbol":  sym,
            "history": history,
            "future":  future,
            "metrics": {"mape": round(mape,2), "rmse": round(rmse,2)}
        })

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"api_forecast 失敗：{e}\n{tb}")
        return jsonify({"error": str(e), "trace": tb}), 500
