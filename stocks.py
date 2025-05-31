from flask import Blueprint, jsonify, request, render_template
import requests, logging, time, configparser, random, csv, io
from datetime import datetime, timedelta

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")
AV_KEY   = CFG.get("AlphaVantage", "API_KEY",  fallback="").strip()
FM_TOKEN = CFG.get("FinMind",      "API_TOKEN",fallback="").strip()

stock_bp = Blueprint("stocks", __name__)

CATEGORY_TO_MEMBERS = {
    "上市_電子零組件": [
        {"code": "2316.TW", "name": "楠梓電"},
        {"code": "2303.TW", "name": "聯電"},
        {"code": "2454.TW", "name": "聯發科"},
    ],
    "上市_半導體": [
        {"code": "2330.TW", "name": "台積電"},
        {"code": "2303.TW", "name": "聯電"},
        {"code": "2454.TW", "name": "聯發科"},
    ],
    "上市_光電": [
        {"code": "2330.TW", "name": "台積電"},
        {"code": "2308.TW", "name": "台達電"},
        {"code": "2316.TW", "name": "楠梓電"},
    ],
    "上市_電子通路": [
        {"code": "2345.TW", "name": "智邦"},
        {"code": "2311.TW", "name": "日月光"},
        {"code": "2327.TW", "name": "國巨"},
    ],
    "上櫃_光電": [
        {"code": "3514.TWO", "name": "陽程光電"},
        {"code": "3698.TWO", "name": "隆達光電"},
        {"code": "3667.TWO", "name": "圓剛"},
    ],
    "上櫃_生技": [
        {"code": "4123.TWO", "name": "漢翔生技"},
        {"code": "4161.TWO", "name": "富喬生技"},
        {"code": "4142.TWO", "name": "國光生技"},
    ],
    "上櫃_半導體": [
        {"code": "6488.TWO", "name": "環球晶"},
        {"code": "6669.TWO", "name": "榮昌"},
        {"code": "6274.TWO", "name": "台燿"},
    ],
}

price_cache, price_backoff   = {}, {}
history_cache, history_backoff = {}, {}
PRICE_TTL,  BACKOFF = 10, 5
HIST_TTL,   HIST_BK = 300, 60

UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "curl/8.0.1"
]

@stock_bp.route("")
def stocks_page():
    return render_template("stocks.html")

@stock_bp.route("/api/stock_category_members")
def api_stock_category_members():
    cat = request.args.get("category","").strip()
    members = CATEGORY_TO_MEMBERS.get(cat, [])
    if not members:
        return jsonify([]),200
    out, now = [], time.time()
    for m in members:
        sym, name = m["code"], m["name"]
        if price_backoff.get(sym) and now-price_backoff[sym] < BACKOFF:
            out.append(_null_price(sym, name)); continue
        if sym in price_cache and now-price_cache[sym]["ts"] < PRICE_TTL:
            out.append(price_cache[sym]["data"]); continue
        try:
            data = _fetch_twse_price(sym, name)
            price_cache[sym] = {"ts":now,"data":data}
            price_backoff.pop(sym, None)
        except Exception as e:
            logging.warning(f"[price] {sym} {e}")
            price_backoff[sym] = now
            data = _null_price(sym, name)
        out.append(data)
    return jsonify(out),200

@stock_bp.route("/api/stock_price")
def api_stock_price():
    sym = request.args.get("symbol","").strip()
    if not sym:
        return jsonify({"error":"symbol needed"}),400
    now=time.time()
    if price_backoff.get(sym) and now-price_backoff[sym] < BACKOFF:
        return jsonify({"error":"Too Many"}),429
    if sym in price_cache and now-price_cache[sym]["ts"] < PRICE_TTL:
        return jsonify(price_cache[sym]["data"]),200
    try:
        data = _fetch_twse_price(sym, sym)
        price_cache[sym]={"ts":now,"data":data}
        return jsonify(data),200
    except Exception as e:
        logging.warning(f"[price] {sym} {e}")
        price_backoff[sym]=now
        return jsonify({"error":"service"}),500

@stock_bp.route("/api/stock_history")
def api_stock_history():
    sym   = request.args.get("symbol","").strip()
    ival  = request.args.get("interval","5min").strip()
    if not sym: return jsonify({"error":"symbol needed"}),400
    now=time.time()
    if history_backoff.get(sym) and now-history_backoff[sym] < HIST_BK:
        return jsonify({"error":"Too Many"}),429
    if sym in history_cache and now-history_cache[sym]["ts"] < HIST_TTL:
        h=history_cache[sym]; return jsonify(h),200
    yf_int = ival.replace("min","m")
    try:
        ts,pr = _yahoo(sym,yf_int)
    except Exception as e1:
        try:
            ts,pr = _alpha(sym,yf_int)
        except Exception as e2:
            try:
                ts,pr = _finmind(sym,yf_int)
            except Exception as e3:
                logging.warning(f"[history] {sym} yahoo:{e1} av:{e2} fm:{e3}")
                history_backoff[sym]=now
                return jsonify({"error":"no history"}),404
    history_cache[sym]={"ts":now,"symbol":sym,"timestamps":ts,"prices":pr}
    history_backoff.pop(sym,None)
    return jsonify({"symbol":sym,"timestamps":ts,"prices":pr}),200

# ---------- helpers ----------
def _null_price(code,name):
    return dict(code=code,name=name,price=None,change=None,pct=None,volume=None,time=None)

def _fetch_twse_price(sym,name):
    ex,tws = ("tse",sym.replace(".TW","")) if sym.endswith(".TW") else ("otc",sym.replace(".TWO",""))
    r = requests.get(f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex}_{tws}.tw", timeout=5)
    r.raise_for_status()
    arr = r.json().get("msgArray",[])
    if not arr: return _null_price(sym,name)
    info = arr[0]
    z,pv,vol,t = info.get("z",""),info.get("y",""),info.get("tv","0"),info.get("t","")
    if z in ["","-","--"]:
        z = pv
    if z in ["","-","--"]:
        return _null_price(sym,name)
    price=float(z.replace(",","")); prev=float(pv.replace(",","")) if pv not in ["","-","--"] else price
    ch=round(price-prev,2); pct=round(ch/prev*100,2) if prev else 0
    return dict(code=sym,name=name,price=price,change=ch,pct=pct,volume=int(vol.replace(",","")),time=t)

def _yahoo(sym,iv):
    hdr={"User-Agent":random.choice(UA)}
    r=requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",params={"range":"1d","interval":iv,"indicators":"quote"},headers=hdr,timeout=10)
    if r.status_code==429: raise RuntimeError("yahoo 429")
    r.raise_for_status()
    j=r.json()
    res=j["chart"]["result"][0]
    ts=res.get("timestamp",[]); cl=res["indicators"]["quote"][0].get("close",[])
    if not ts or not cl: raise RuntimeError("yahoo empty")
    out_t,out_p=[],[]
    for t,p in zip(ts,cl):
        if p is None: continue
        dt=datetime.fromtimestamp(t)+timedelta(hours=8)
        out_t.append(dt.strftime("%H:%M")); out_p.append(round(float(p),3))
    return out_t,out_p

def _alpha(sym,iv):
    if not AV_KEY: raise RuntimeError("av skip")
    avs = f"TSE:{sym.replace('.TW','')}" if sym.endswith(".TW") else f"OTC:{sym.replace('.TWO','')}"
    r=requests.get("https://www.alphavantage.co/query",params={"function":"TIME_SERIES_INTRADAY","symbol":avs,"interval":iv,"outputsize":"compact","apikey":AV_KEY},timeout=10)
    if r.status_code==429: raise RuntimeError("av 429")
    j=r.json(); k=f"Time Series ({iv})"
    d=j.get(k,{})
    if not d: raise RuntimeError("av empty")
    out_t,out_p=[],[]
    for dt_str in sorted(d.keys())[-288:]:
        dt=datetime.strptime(dt_str,"%Y-%m-%d %H:%M:%S")+timedelta(hours=8)
        out_t.append(dt.strftime("%H:%M")); out_p.append(round(float(d[dt_str]['4. close']),3))
    return out_t,out_p

def _finmind(sym,iv):
    if not FM_TOKEN: raise RuntimeError("fm skip")
    code=sym.replace(".TW","").replace(".TWO","")
    start=(datetime.today()-timedelta(days=1)).strftime("%Y-%m-%d")
    r=requests.get("https://api.finmindtrade.com/api/v4/data",params={"dataset":"TaiwanStockPriceMinute","data_id":code,"start_date":start,"token":FM_TOKEN},timeout=10)
    if r.status_code==429: raise RuntimeError("fm 429")
    d=r.json().get("data",[])
    if not d: raise RuntimeError("fm empty")
    step=int(iv.replace("m",""))
    out_t,out_p=[],[]
    for row in d:
        t=datetime.strptime(row["datetime"],"%Y-%m-%d %H:%M:%S")
        if t.minute%step: continue
        out_t.append(t.strftime("%H:%M")); out_p.append(round(float(row["close"]),3))
    return out_t[-288:],out_p[-288:]
