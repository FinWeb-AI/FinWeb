from __future__ import annotations
import os, json, time, datetime, configparser, logging
from typing import List, Dict

import requests
import yfinance as yf
import pandas as pd
from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, current_app, request,
)
from flask_login import login_required
import google.generativeai as genai

from ml_model import predict_action

trend_bp = Blueprint("trend", __name__, template_folder="templates")

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")

GEMINI_KEY   = CFG["Gemini"].get("API_KEY", "").strip()
OPENAI_KEY   = CFG.get("OpenAI", "API_KEY", fallback="").strip()
COINGECKO_URL = CFG["CoinGecko"].get(
    "API_URL", "https://api.coingecko.com/api/v3"
).strip()

genai.configure(api_key=GEMINI_KEY or None)

POPULAR_STOCKS  = [("Apple Inc.", "AAPL"), ("Microsoft Corp.", "MSFT"),
                   ("Alphabet Inc.", "GOOGL"), ("Tesla, Inc.", "TSLA"),
                   ("Amazon.com, Inc.", "AMZN")]
POPULAR_CRYPTOS = [("Bitcoin", "bitcoin"), ("Ethereum", "ethereum"),
                   ("Litecoin", "litecoin"), ("Ripple", "ripple"),
                   ("Cardano", "cardano")]

ALL_STOCK_SYMBOLS = [
    "1101.TW","1102.TW","1103.TW","1104.TW","1108.TW","1109.TW","1110.TW",
    "1201.TW","1203.TW","1210.TW","1216.TW","1217.TW","1218.TW","1219.TW",
    "1220.TW","1225.TW","1227.TW","1229.TW","1231.TW","1232.TW","1233.TW",
    "1234.TW","1235.TW","1236.TW","1237.TW","1238.TW","1239.TW","1240.TW",
    "1241.TW","1243.TW","1244.TW","1245.TW","1246.TW","1247.TW","1248.TW",
    "1249.TW","1250.TW","1252.TW","1253.TW","1254.TW","1255.TW","1256.TW",
    "1257.TW","1258.TW","1259.TW","1260.TW","1261.TW","1262.TW","1263.TW",
    "1301.TW","1303.TW","1304.TW","1305.TW","1307.TW","1308.TW","1309.TW",
    "1310.TW","1312.TW","1313.TW","1314.TW","1315.TW","1316.TW","1319.TW",
    "1321.TW","1323.TW","1324.TW","1325.TW","1326.TW","1337.TW","1338.TW",
    "1339.TW","1340.TW","1341.TW","1342.TW","1402.TW","1409.TW","1410.TW",
    "1413.TW","1414.TW","1416.TW","1417.TW","1418.TW","1423.TW","1434.TW",
    "1435.TW","1436.TW","1437.TW","1440.TW","1444.TW","1445.TW","1446.TW",
    "1447.TW","1449.TW","1455.TW","1456.TW","1457.TW","1459.TW","1460.TW",
    "1470.TW","1476.TW","1477.TW","1479.TW","1480.TW","1481.TW","1504.TW",
    "1507.TW","1512.TW","1513.TW","1514.TW","1515.TW","1516.TW","1517.TW",
    "1519.TW","1521.TW","1522.TW","1523.TW","1524.TW","1525.TW","1526.TW",
    "1527.TW","1528.TW","1529.TW","1530.TW","1531.TW","1532.TW","1533.TW",
    "1536.TW","1537.TW","1539.TW","1540.TW","1560.TW","1568.TW","1570.TW",
    "1582.TW","1583.TW","1584.TW","1587.TW","1588.TW","1590.TW","1592.TW",
    "1598.TW","1603.TW","1604.TW","1605.TW","1608.TW","1609.TW","1610.TW",
    "1611.TW","1612.TW","1614.TW","1615.TW","1616.TW","1617.TW","1626.TW",
    "1701.TW","1702.TW","1704.TW","1707.TW","1720.TW","1721.TW","1722.TW",
    "1723.TW","1724.TW","1725.TW","1726.TW","1727.TW","1729.TW","1730.TW",
    "1731.TW","1732.TW","1733.TW","1734.TW","1735.TW","1736.TW","1737.TW",
    "1738.TW","1739.TW","1740.TW","1741.TW","1802.TW","1805.TW","1806.TW",
    "1808.TW","1902.TW","1903.TW","1904.TW","1905.TW","1906.TW","1907.TW",
    "1908.TW","1909.TW","2002.TW","2006.TW","2007.TW","2009.TW","2010.TW",
    "2012.TW","2013.TW","2014.TW","2015.TW","2020.TW","2023.TW","2025.TW",
    "2027.TW","2028.TW","2029.TW","2030.TW","2031.TW","2032.TW","2034.TW",
    "2038.TW","2049.TW","2059.TW","2062.TW","2069.TW","2073.TW","2074.TW",
    "2079.TW","2080.TW","2081.TW","2082.TW","2101.TW","2102.TW","2104.TW",
    "2105.TW","2106.TW","2107.TW","2108.TW","2109.TW","2114.TW","2115.TW",
    "2116.TW","2201.TW","2204.TW","2206.TW","2207.TW","2208.TW","2227.TW",
    "2228.TW","2231.TW","2233.TW","2236.TW","2239.TW"
]

def _extract_close_series(df: pd.DataFrame, sym: str) -> pd.Series:
    """兼容單/雙層欄位，回傳單一 float Series。"""
    if isinstance(df.columns, pd.MultiIndex):
        if ("Close", sym) in df.columns:
            ser = df[("Close", sym)]
        else:
            ser = df.xs("Close", level=0, axis=1).iloc[:, 0]
    else:
        ser = df["Close"]
    return pd.to_numeric(ser, errors="coerce")

def fetch_stock_prices(symbol: str, days: int = 200) -> List[Dict]:
    try:
        raw = yf.download(
            symbol, period=f"{days}d", interval="1d",
            auto_adjust=True, progress=False, threads=False
        )
        if raw is None or raw.empty:
            return []
        ser = _extract_close_series(raw, symbol).dropna().tail(days)
        return [
            {"date": idx.strftime("%Y-%m-%d"), "close": float(v)}
            for idx, v in ser.items()
        ]
    except Exception as e:
        current_app.logger.error(f"yfinance error for {symbol}: {e}")
        return []

def fetch_crypto_prices(coin_id: str, days: int = 200) -> List[Dict]:
    try:
        url = f"{COINGECKO_URL}/coins/{coin_id}/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": "daily"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return [
            {"date": time.strftime("%Y-%m-%d", time.gmtime(ts/1000)), "close": float(p)}
            for ts, p in r.json().get("prices", [])[-days:]
        ]
    except Exception as e:
        current_app.logger.error(f"fetch_crypto_prices error: {e}")
        return []

def sma(seq: List[float], win: int) -> List[float|None]:
    out, tot = [], 0.0
    for i, v in enumerate(seq):
        tot += v
        if i >= win:
            tot -= seq[i-win]
        out.append(round(tot/win,4) if i+1>=win else None)
    return out

def add_indicators(rows: List[Dict]) -> List[Dict]:
    closes = [r["close"] for r in rows]
    sma20, sma50 = sma(closes,20), sma(closes,50)
    for i,r in enumerate(rows):
        r["sma20"], r["sma50"] = sma20[i], sma50[i]
    return rows

def _call_gemini(
    prompt: str,
    temperature: float = 0.6,
    max_tokens: int = 256,
    model_name: str = "gemini-1.5-flash-latest"
) -> str:
    """使用官方 SDK 呼叫 Gemini，失敗回 ''。"""
    if not GEMINI_KEY:
        return ""
    try:
        model = genai.GenerativeModel(model_name)
        resp  = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                top_p=0.9,
                top_k=50,
            )
        )
        return (resp.text or "").strip()
    except Exception as e:
        current_app.logger.error(f"Gemini SDK error: {e}")
        return ""

def gemini_trend(symbol: str, closes: List[float], asset_type: str) -> str:
    prompt = (
        f"你是一位專業金融分析師，請根據以下 {asset_type} {symbol} "
        f"最近 60 個收盤價：{closes}\n"
        "用繁體中文撰寫約 120 字的走勢分析，包括多空趨勢、均線訊號及支撐 / 壓力。\n"
    )
    txt = _call_gemini(prompt, temperature=0.5, max_tokens=256)
    return txt or "AI 分析錯誤，請稍後再試。"

def llm_recommend(symbol: str, company_name: str, action: str, conf: float) -> str:
    prompt = (
        f"機器學習模型對 {symbol}（{company_name}）給出的動作為 {action}，"
        f"信心 {conf:.2f}。請用繁體中文在 100 字內說明原因並給出投資建議。"
    )
    txt = _call_gemini(prompt, temperature=0.6, max_tokens=128)
    if txt:
        return txt

    if OPENAI_KEY:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.6
                }, timeout=10
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            current_app.logger.error(f"OpenAI error: {e}")
    return "（AI 投資建議功能尚未啟用）"

@trend_bp.route("/trend", methods=["GET","POST"])
@login_required
def trend_home():
    if request.method=="POST":
        atype = request.form.get("asset_type","")
        sym   = request.form.get("symbol","").strip()
        if not atype or not sym:
            flash("請先選擇資產類型並輸入 / 點選代號。","warning")
            return redirect(url_for("trend.trend_home"))
        sym = sym.upper() if atype=="stock" else sym.lower()
        return redirect(url_for("trend.trend_result", asset_type=atype, symbol=sym))
    return render_template(
        "trend_home.html",
        popular_stocks=POPULAR_STOCKS,
        popular_cryptos=POPULAR_CRYPTOS,
        all_stocks=ALL_STOCK_SYMBOLS
    )

@trend_bp.route("/trend/result")
@login_required
def trend_result():
    atype = request.args.get("asset_type","")
    sym   = request.args.get("symbol","").strip()
    if not atype or not sym:
        flash("參數錯誤，請重新操作。","danger")
        return redirect(url_for("trend.trend_home"))

    rows = fetch_stock_prices(sym) if atype=="stock" else fetch_crypto_prices(sym)
    if not rows:
        flash("取得價格失敗，請檢查代號或稍後再試。","danger")
        return redirect(url_for("trend.trend_home"))

    company_name = sym
    if atype == "stock":
        try:
            ticker = yf.Ticker(sym)
            info = ticker.info
            company_name = info.get("longName") or info.get("shortName") or sym
        except Exception:
            company_name = sym

        rows = add_indicators(rows)
        closes60 = [r["close"] for r in rows][-60:]
        ai_analysis = gemini_trend(f"{sym} ({company_name})", closes60, "股票")
    else:
        ai_analysis = gemini_trend(sym, [r["close"] for r in rows][-60:], "加密貨幣")

    model_path = os.path.join("models", f"{sym}_model.pkl")
    action, conf = predict_action(model_path, [r["close"] for r in rows][-60:])
    dl_message   = f"{action}（信心 {conf:.2f}）"

    invest_advice = llm_recommend(sym, company_name, action, conf)

    label = sym.upper() if atype=="stock" else sym.capitalize()

    return render_template(
        "trend.html",
        asset_type=atype,
        symbol=sym,
        label=label,
        data=json.dumps(rows, ensure_ascii=False),
        ai_analysis=ai_analysis,
        dl_message=dl_message,
        invest_advice=invest_advice
    )
