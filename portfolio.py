import datetime, functools, json, logging, re, warnings
from typing import List, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

warnings.filterwarnings("ignore", category=UserWarning, module="yfinance")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("portfolio")

pf_bp = Blueprint("portfolio", __name__, template_folder="templates")

_FALLBACK50: List[Tuple[str, str]] = [
    ("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"), ("2308", "台達電"),
    ("2882", "國泰金"), ("2303", "聯電"), ("2412", "中華電"), ("1301", "台塑"),
    ("2881", "富邦金"), ("2891", "中信金"), ("2357", "華碩"), ("2603", "長榮"),
    ("1216", "統一"), ("1101", "台泥"), ("3008", "大立光"), ("2886", "兆豐金"),
    ("2884", "玉山金"), ("2002", "中鋼"), ("1303", "南亞"), ("5871", "中租-KY"),
    ("2609", "陽明"), ("9933", "中鼎"), ("3702", "大聯大"), ("2801", "彰銀"),
    ("2207", "和泰車"), ("9917", "中保科"), ("2615", "萬海"), ("2912", "統一超"),
    ("1210", "大成"), ("2885", "元大金"), ("2883", "開發金"), ("9910", "豐泰"),
    ("3045", "台灣大"), ("2606", "裕民"), ("1434", "福懋"), ("1590", "亞德客"),
    ("3711", "日月光投控"), ("2356", "英業達"), ("3706", "神達"), ("2887", "台新金"),
    ("2892", "第一金"), ("2888", "新光金"), ("2889", "國票金"), ("1102", "亞泥"),
    ("9904", "寶成"), ("1722", "台肥"), ("1907", "永豐餘"), ("4958", "臻鼎"),
]

def _parse_twse_json(js: list[dict]) -> list[tuple[str, str]]:
    """
    自動找出含「代號」「名稱」關鍵字欄位
    """
    if not js:
        return []
    keys = js[0].keys()
    code_key = next((k for k in keys if re.search("代號|code", k, re.I)), None)
    name_key = next((k for k in keys if re.search("名稱|name", k, re.I)), None)
    if not code_key or not name_key:
        raise KeyError("找不到代號/名稱欄位")
    return [(d[code_key], d[name_key]) for d in js]

def _dl_twse_list() -> list[tuple[str, str]]:
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L?TYPEK=%E6%AD%A3%E8%82%A1%2CETF"
    try:
        js = requests.get(url, timeout=8).json()
        data = _parse_twse_json(js)
        return data
    except Exception as e:
        log.warning(f"TWSE OpenAPI 失敗：{e}")
        return []

def _dl_finmind_list() -> list[tuple[str, str]]:
    url = "https://api.finmindtrade.com/v4/data?dataset=TaiwanStockInfo"
    try:
        txt = requests.get(url, timeout=8).text.lstrip("\ufeff")
        if not txt.strip().startswith("{"):
            txt = txt[txt.find("{"):]
        js = json.loads(txt)
        if js.get("status") != 200 or not js.get("data"):
            raise RuntimeError(f"FinMind status={js.get('status')}")
        return [(d["stock_id"], d["stock_name"]) for d in js["data"]]
    except Exception as e:
        log.warning(f"FinMind 失敗：{e}")
        return []

@functools.lru_cache(maxsize=1)
def _get_tw_symbols() -> list[tuple[str, str]]:
    data = _dl_twse_list()
    if data:
        log.info(f"TWSE 取得 {len(data)} 檔")
        return data
    data = _dl_finmind_list()
    if data:
        log.info(f"FinMind 取得 {len(data)} 檔")
        return data
    log.warning("兩來源皆失敗 → fallback 50")
    return _FALLBACK50

def _yf_history(code: str, period="1y") -> pd.Series | None:
    try:
        df = yf.Ticker(code).history(period=period, auto_adjust=False)
        ser = df["Close"].dropna()
        return ser if not ser.empty else None
    except Exception:
        return None

def _finmind_price(code: str, days=365) -> pd.Series | None:
    today  = datetime.date.today()
    start  = today - datetime.timedelta(days=days+5)
    url    = "https://api.finmindtrade.com/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": code,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": today.strftime("%Y-%m-%d"),
    }
    try:
        js = requests.get(url, params=params, timeout=8).json()
        if js.get("status") != 200 or not js.get("data"):
            return None
        df = pd.DataFrame(js["data"])
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        ser = df["close"].astype(float).dropna()
        return ser if not ser.empty else None
    except Exception:
        return None

def smart_download(symbol: str) -> pd.Series | None:
    sym = symbol.strip().upper()
    ser = _yf_history(sym)
    if ser is not None:
        ser.name = sym
        return ser
    if sym.isdigit() and not sym.endswith(".TW"):
        ser = _yf_history(sym + ".TW")
        if ser is not None:
            ser.name = sym
            return ser
        ser = _finmind_price(sym)
        if ser is not None:
            ser.name = sym
            return ser
    return None

@pf_bp.route("/portfolio")
@login_required
def portfolio_home():
    return render_template("portfolio.html")

@pf_bp.route("/api/search_symbol")
@login_required
def api_search_symbol():
    q     = request.args.get("q", "").upper()
    debug = request.args.get("debug") == "1"

    symbols = _get_tw_symbols()
    via = "twse" if symbols and symbols != _FALLBACK50 else "fallback"

    out = []
    for sid, name in symbols:
        if (not q) or (q in sid or q in name.upper()):
            out.append({"symbol": sid, "name": name})
            if len(out) >= (50 if not q else 20):
                break
    return jsonify({"via": via, "data": out} if debug else out)

@pf_bp.route("/api/portfolio", methods=["POST"])
@login_required
def api_portfolio():
    items = request.get_json(force=True, silent=True) or []
    items = [i for i in items if i.get("symbol") and i.get("weight")]

    if not items:
        return jsonify({"error": "請至少選擇 1 檔"}), 400

    w = np.array([i["weight"] for i in items], float)
    if w.sum() == 0:
        return jsonify({"error": "權重不得全為 0"}), 400
    w = w / w.sum()

    prices = {}
    for it in items:
        ser = smart_download(it["symbol"])
        if ser is not None:
            prices[it["symbol"]] = ser
        else:
            log.warning(f"無法下載價格：{it['symbol']}")

    if len(prices) < 2:
        return jsonify({"error": "有效股票不足 2 檔"}), 400

    df = pd.concat(prices.values(), axis=1).dropna()
    df.columns = df.columns.astype(str)
    if df.empty or df.shape[1] < 2:
        return jsonify({"error": "資料無交集"}), 400

    ret  = df.pct_change().dropna()
    port = ret.dot(w[:len(df.columns)])

    ann   = port.mean()*252
    vol   = port.std()*np.sqrt(252)
    sharpe= ann/vol if vol else 0
    var95 = np.percentile(port,5)
    cvar95= port[port<=var95].mean()

    metrics = dict(ann_return=round(ann,4), volatility=round(vol,4),
                   sharpe=round(sharpe,4), var=round(-var95,4),
                   cvar=round(-cvar95,4))

    corr = ret.corr().round(2)
    corr.columns=corr.columns.astype(str)
    corr.index  =corr.index.astype(str)
    corr_json={c:corr.loc[c].to_dict() for c in corr.columns}

    cum=(1+ret).cumprod(); cum["Portfolio"]=(1+port).cumprod()
    cum.columns=cum.columns.astype(str)
    ts = cum.reset_index(); ts["Date"]=ts["Date"].dt.strftime("%Y-%m-%d")
    return jsonify({"metrics":metrics,"corr":corr_json,
                    "timeseries":ts.to_dict("records")})
