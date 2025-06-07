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
log = logging.getLogger("backtest")

bt_bp = Blueprint("backtest", __name__, template_folder="templates")

_FALLBACK50: List[Tuple[str, str]] = [
    ("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"),
    ("2308", "台達電"), ("2882", "國泰金")
]

def _parse_twse_json(js: List[dict]) -> List[Tuple[str, str]]:
    if not js:
        return []
    keys = js[0].keys()
    code_key = next((k for k in keys if re.search("代號|code", k, re.I)), None)
    name_key = next((k for k in keys if re.search("名稱|name", k, re.I)), None)
    if not code_key or not name_key:
        raise KeyError("找不到代號/名稱欄位")
    return [(d[code_key], d[name_key]) for d in js]

def _dl_twse_list() -> List[Tuple[str, str]]:
    url = (
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        "?TYPEK=%E6%AD%A3%E8%82%A1%2CETF"
    )
    try:
        js = requests.get(url, timeout=8).json()
        return _parse_twse_json(js)
    except Exception as e:
        log.warning(f"TWSE OpenAPI 失敗：{e}")
        return []

@functools.lru_cache(maxsize=1)
def _get_twse_symbols() -> List[Tuple[str, str]]:
    data = _dl_twse_list()
    if data:
        log.info(f"從 TWSE 拿到 {len(data)} 檔股票/ETF")
        return data
    log.warning("TWSE 失敗，使用 fallback 列表")
    return _FALLBACK50

def _yf_history(code: str, start: str, end: str) -> pd.Series:
    df = yf.download(code,
                     start=start,
                     end=end,
                     auto_adjust=False,
                     progress=False)
    ser = df["Close"].dropna()
    return ser if not ser.empty else None

def smart_download(symbol: str, start: str, end: str) -> pd.Series | pd.DataFrame:
    sym = symbol.strip().upper()
    if sym.endswith(".TW") or sym.isdigit():
        for code in (sym, sym if sym.endswith(".TW") else sym + ".TW"):
            ser = _yf_history(code, start, end)
            if ser is not None:
                ser.name = symbol
                return ser
    if sym.endswith("USDT"):
        base = sym[:-4]
        yf_code = f"{base}-USD"
        ser = _yf_history(yf_code, start, end)
        if ser is not None:
            ser.name = symbol
            return ser
    return None

@bt_bp.route("/backtest")
@login_required
def backtest_home():
    return render_template("backtest.html")

@bt_bp.route("/api/symbols")
@login_required
def api_symbols():
    asset = request.args.get("asset", "stock")
    if asset == "stock":
        return jsonify([f"{sid}.TW" for sid, _ in _get_twse_symbols()])
    else:
        resp = requests.get("https://api.binance.com/api/v3/ticker/price").json()
        usdt = sorted(i["symbol"] for i in resp if i["symbol"].endswith("USDT"))
        return jsonify(usdt)

@bt_bp.route("/api/backtest")
@login_required
def api_backtest():
    sym   = request.args.get("symbol","").strip()
    strat = request.args.get("strategy","dma")
    start = request.args.get("start","")
    end   = request.args.get("end","")
    if not (start and end):
        today = pd.Timestamp.today()
        end   = today.strftime("%Y-%m-%d")
        start = (today - pd.Timedelta(days=365)).strftime("%Y-%m-%d")

    ser = smart_download(sym, start, end)
    if ser is None: return jsonify(error=f"無法下載價格：{sym}")

    df = ser.to_frame("close") if isinstance(ser, pd.Series) else ser.rename(columns={ser.columns[0]: "close"})
    df = df.loc[start:end].dropna()
    if len(df) < 2: return jsonify(error="資料不足")

    df["ret"] = df["close"].pct_change().fillna(0)

    if strat == "dma":
        df["pos"] = (df["close"].rolling(20).mean() > df["close"].rolling(60).mean()).astype(int)
    elif strat == "rsi":
        delta = df["close"].diff()
        up = delta.clip(lower=0).rolling(14).mean()
        dn = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100/(1+up/dn)
        df["pos"] = np.where(rsi < 30, 1, np.where(rsi > 70, 0, np.nan))
        df["pos"] = df["pos"].ffill()
    else:
        macd = df["close"].ewm(span=12).mean() - df["close"].ewm(span=26).mean()
        sig  = macd.ewm(span=9).mean()
        df["pos"] = (macd > sig).astype(int)
    df.iloc[0, df.columns.get_loc("pos")] = 0

    df["strat_ret"] = df["pos"].shift(1) * df["ret"]
    df["strat_ret"] = df["strat_ret"].fillna(0)
    df["equity"]    = (1 + df["strat_ret"]).cumprod()
    df["drawdown"]  = df["equity"] / df["equity"].cummax() - 1
    df.replace([np.inf, -np.inf], 0, inplace=True)

    tot  = float(df["equity"].iat[-1] - 1)
    ann  = float(np.nan_to_num(df["strat_ret"].mean() * 252))
    shar = float(np.nan_to_num(
        df["strat_ret"].mean() / df["strat_ret"].std() * np.sqrt(252)
        if df["strat_ret"].std() else 0))
    mdd  = float(np.nan_to_num(df["drawdown"].min()))

    eq = df["equity"].round(4).fillna(0).tolist()
    dd = df["drawdown"].round(4).fillna(0).tolist()
    if abs(tot) < 1e-9:
        return jsonify(error="在此日期區間內策略沒有任何持倉變化，請拉長期間或換標的")
    return jsonify({
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "equity": eq, "drawdown": dd,
        "total_return": round(tot,4),
        "ann_return":   round(ann,4),
        "sharpe":       round(shar,4),
        "max_dd":       round(mdd,4)
    })

