from flask import Blueprint, jsonify, request, render_template
import requests, logging, time, configparser, random
from datetime import datetime, timedelta

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(levelname)s %(message)s")

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

PRICE_TTL, BACKOFF        = 10, 5
HIST_TTL, HIST_BK         = 300, 60
CATEGORY_TTL              = 3600 * 24
price_cache,    price_backoff    = {}, {}
history_cache,  history_backoff  = {}, {}
category_cache                  = {"ts": 0, "data": {}}

UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "curl/8.0.1"
]


@stock_bp.route("")
def stocks_page():
    return render_template("stocks.html")


def _fetch_categories():
    now = time.time()
    if category_cache["data"] and now - category_cache["ts"] < CATEGORY_TTL:
        logging.debug("使用快取的分類資料")
        return category_cache["data"]

    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {"dataset": "TaiwanStockInfo", "token": FM_TOKEN}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        arr = r.json().get("data", [])
        logging.debug(f"[FinMind] 取回 {len(arr)} 筆股票資料")

        pairs = {}
        fm_type_counts = {}

        for i, d in enumerate(arr):
            raw       = str(d["stock_id"]).zfill(4)
            fm_type_r = d.get("type", "")
            fm_type   = fm_type_r.lower()
            fm_type_counts[fm_type] = fm_type_counts.get(fm_type, 0) + 1

            if fm_type == "twse":
                market, suffix = "上市", ".TW"
            elif fm_type == "otc":
                market, suffix = "上櫃", ".TWO"
            else:
                ind = d.get("industry_category", "")
                if ind.startswith("上櫃") or ind.startswith("興櫃"):
                    market, suffix = "上櫃", ".TWO"
                else:
                    market, suffix = "上市", ".TW"

            code     = raw + suffix
            name     = d.get("stock_name", "")
            industry = d.get("industry_category", "").strip() or "其它"
            key      = f"{market}_{industry}"

            if i < 10:
                logging.debug(f"Rec#{i+1}: id={raw}, type='{fm_type_r}' → market={market}, key={key}")

            pairs.setdefault(key, {"market":market, "industry":industry, "members":[]})
            pairs[key]["members"].append({"code":code, "name":name})

        logging.debug(f"Type 欄位統計: {fm_type_counts}")

        all_keys = list(pairs.keys())
        logging.debug(f"最終分類 keys 共 {len(all_keys)} 筆：{all_keys}")

        category_cache["data"] = pairs
        category_cache["ts"]   = now
        return pairs

    except Exception as e:
        logging.warning(f"[categories] FinMind 取分類失敗，退回靜態資料：{e}")
        static = {}
        for key, lst in CATEGORY_TO_MEMBERS.items():
            mkt, ind = key.split("_", 1)
            static[key] = {"market":mkt, "industry":ind, "members":lst}
        category_cache["data"] = static
        category_cache["ts"]   = now
        return static


@stock_bp.route("/api/stock_categories")
def api_stock_categories():
    try:
        pairs = _fetch_categories()
        out = {"上市": [], "上櫃": []}
        for key, v in pairs.items():
            m = v["market"]
            if m in out:
                out[m].append({"key": key, "label": v["industry"]})
        for m in out:
            out[m].sort(key=lambda x: x["label"])
            logging.debug(f"{m} 分類數: {len(out[m])}")
        return jsonify(out), 200
    except Exception as e:
        logging.error(f"[api/stock_categories] 未預期錯誤: {e}")
        return jsonify({"error": "load categories failed"}), 200

@stock_bp.route("/api/stock_category_members")
def api_stock_category_members():
    cat = request.args.get("category", "").strip()
    members = _fetch_categories().get(cat, {}).get("members", [])
    simple = [{"code": m["code"], "name": m["name"]} for m in members]
    return jsonify(simple), 200

@stock_bp.route("/api/stock_search")
def api_stock_search():
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify({"error": "q needed"}), 400
    pairs = _fetch_categories()
    matches = []
    for key, v in pairs.items():
        for m in v["members"]:
            if q in m["code"].lower() or q in m["name"].lower():
                matches.append({"category": key, "code": m["code"], "name": m["name"]})
    if matches:
        return jsonify(matches), 200
    else:
        return jsonify({"error": "not found"}), 404

@stock_bp.route("/api/stock_price")
def api_stock_price():
    sym = request.args.get("symbol", "").strip()
    if not sym:
        return jsonify({"error": "symbol needed"}), 400

    now = time.time()
    if price_backoff.get(sym) and now - price_backoff[sym] < BACKOFF:
        return jsonify({"error": "Too Many"}), 429
    cache = price_cache.get(sym)
    if cache and now - cache["ts"] < PRICE_TTL:
        return jsonify(cache["data"]), 200

    attempts = [sym]
    if sym.endswith(".TWO"):
        attempts.append(sym[:-4] + ".TW")

    for s in attempts:
        try:
            data = _fetch_twse_price(s, s)
            price_cache[s] = {"ts": now, "data": data}
            price_backoff.pop(s, None)
            return jsonify(data), 200
        except Exception as e:
            logging.debug(f"TWSE fail for {s}: {e}")
            price_backoff[s] = now

    try:
        data = _fetch_yahoo_price(sym)
        price_cache[sym] = {"ts": now, "data": data}
        price_backoff.pop(sym, None)
        return jsonify(data), 200
    except Exception as e:
        logging.warning(f"[price][all fail] {sym} {e}")
        price_backoff[sym] = now
        return jsonify({"error": "service"}), 500

@stock_bp.route("/api/stock_history")
def api_stock_history():
    sym  = request.args.get("symbol", "").strip()
    ival = request.args.get("interval", "5min").strip()
    if not sym:
        return jsonify({"error": "symbol needed"}), 400
    now = time.time()
    if history_backoff.get(sym) and now - history_backoff[sym] < HIST_BK:
        return jsonify({"error": "Too Many"}), 429
    if sym in history_cache and now - history_cache[sym]["ts"] < HIST_TTL:
        return jsonify(history_cache[sym]), 200

    yf_int = ival.replace("min", "m")
    try:
        ts, pr = _yahoo(sym, yf_int)
    except Exception as e1:
        try:
            ts, pr = _alpha(sym, yf_int)
        except Exception as e2:
            try:
                ts, pr = _finmind(sym, yf_int)
            except Exception as e3:
                logging.warning(f"[history] {sym} intraday fail. yahoo:{e1} av:{e2} fm:{e3}")

                try:
                    ts, pr = _finmind_daily(sym)
                    logging.debug(f"[history-fallback] {sym} 使用 FinMind 日線，共 {len(ts)} 筆")
                except Exception as e4:
                    logging.error(f"[history] {sym} 日線也取不到: {e4}")
                    history_backoff[sym] = now
                    return jsonify({"error": "no history"}), 404

    hdata = {"symbol": sym, "timestamps": ts, "prices": pr}
    history_cache[sym] = {"ts": now, **hdata}
    history_backoff.pop(sym, None)
    return jsonify(hdata), 200

def _finmind_daily(sym):
    """
    FinMind 日線備援：抓最近 30 天的日收盤
    """
    if not FM_TOKEN:
        raise RuntimeError("fm skip")

    code = sym.replace(".TW", "").replace(".TWO", "")
    start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset":    "TaiwanStockPrice",
        "data_id":    code,
        "start_date": start,
        "token":      FM_TOKEN,
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    arr = r.json().get("data", [])
    if not arr:
        raise RuntimeError("fm daily empty")

    arr.sort(key=lambda x: x["date"])
    ts = [row["date"] for row in arr]
    pr = [round(float(row["close"]), 2) for row in arr]
    return ts, pr

def _null_price(code, name):
    return dict(code=code, name=name,
                price=None, change=None, pct=None, volume=None, time=None)

def _fetch_yahoo_price(sym):
    """
    呼叫 Yahoo Finance chart API，取 meta.regularMarketPrice。
    """
    hdr = {"User-Agent": random.choice(UA)}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    r = requests.get(url,
                     params={"range":"1d","interval":"5m","indicators":"quote"},
                     headers=hdr, timeout=10)
    r.raise_for_status()
    j = r.json()
    meta = j["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev  = meta.get("chartPreviousClose")
    if price is None or prev is None:
        raise RuntimeError("yahoo empty")

    ch  = round(price - prev, 2)
    pct = round(ch / prev * 100, 2) if prev else 0

    return {
        "code": sym,
        "name": meta.get("shortName") or sym,
        "price": price,
        "change": ch,
        "pct": pct,
        "volume": meta.get("regularMarketVolume"),
        "time": datetime.fromtimestamp(meta.get("regularMarketTime", time.time()))
                           .strftime("%H:%M:%S")
    }
    
def _fetch_twse_price(sym, name):
    if sym.endswith(".TW"):
        ex, tws = "tse", sym[:-3]
    elif sym.endswith(".TWO"):
        ex, tws = "otc", sym[:-4]
    else:
        ex, tws = "tse", sym

    headers = {"User-Agent": random.choice(UA)}
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex}_{tws}.tw"
    r = requests.get(url, timeout=10, headers=headers)
    r.raise_for_status()

    arr = r.json().get("msgArray", [])
    if not arr:
        return _null_price(sym, name)

    info    = arr[0]
    z       = info.get("z", "")
    pv      = info.get("y", "")
    vol_str = info.get("tv", "")
    t       = info.get("t", "")

    if z in ["", "-", "--"]:
        z = pv
    if z in ["", "-", "--"]:
        return _null_price(sym, name)

    price = float(z.replace(",", ""))
    prev  = float(pv.replace(",", "")) if pv not in ["", "-", "--"] else price
    ch    = round(price - prev, 2)
    pct   = round(ch / prev * 100, 2) if prev else 0
    try:
        volume = int(vol_str.replace(",", "")) if vol_str not in ["-", "--"] else None
    except:
        volume = None

    return {"code": sym, "name": name, "price": price,
            "change": ch, "pct": pct, "volume": volume, "time": t}

def _yahoo(sym, iv):
    hdr = {"User-Agent": random.choice(UA)}
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
        params={"range":"1d","interval":iv,"indicators":"quote"},
        headers=hdr, timeout=10
    )
    if r.status_code == 429:
        raise RuntimeError("yahoo 429")
    r.raise_for_status()

    j = r.json()
    res = j["chart"]["result"][0]
    ts = res.get("timestamp", [])
    cl = res["indicators"]["quote"][0].get("close", [])
    if not ts or not cl:
        raise RuntimeError("yahoo empty")

    out_t, out_p = [], []
    for t, p in zip(ts, cl):
        if p is None:
            continue
        dt = datetime.fromtimestamp(t) + timedelta(hours=8)
        out_t.append(dt.strftime("%H:%M"))
        out_p.append(round(float(p), 3))
    return out_t, out_p

def _alpha(sym, iv):
    if not AV_KEY:
        raise RuntimeError("av skip")
    avs = f"TSE:{sym[:-3]}" if sym.endswith(".TW") else f"OTC:{sym[:-4]}"
    r = requests.get(
        "https://www.alphavantage.co/query",
        params={"function":"TIME_SERIES_INTRADAY","symbol":avs,"interval":iv,
                "outputsize":"compact","apikey":AV_KEY},
        timeout=10
    )
    if r.status_code == 429:
        raise RuntimeError("av 429")
    j = r.json()
    k = f"Time Series ({iv})"
    d = j.get(k, {})
    if not d:
        raise RuntimeError("av empty")

    out_t, out_p = [], []
    for dt_str in sorted(d.keys())[-288:]:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
        out_t.append(dt.strftime("%H:%M"))
        out_p.append(round(float(d[dt_str]['4. close']), 3))
    return out_t, out_p

def _finmind(sym, iv):
    if not FM_TOKEN:
        raise RuntimeError("fm skip")
    code = sym.replace(".TW", "").replace(".TWO", "")
    start = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    r = requests.get(
        "https://api.finmindtrade.com/api/v4/data",
        params={"dataset":"TaiwanStockPriceMinute","data_id":code,
                "start_date":start,"token":FM_TOKEN},
        timeout=10
    )
    if r.status_code == 429:
        raise RuntimeError("fm 429")
    d = r.json().get("data", [])
    if not d:
        raise RuntimeError("fm empty")

    step = int(iv.replace("m",""))
    out_t, out_p = [], []
    for row in d:
        t = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
        if t.minute % step:
            continue
        out_t.append(t.strftime("%H:%M"))
        out_p.append(round(float(row["close"]), 3))
    return out_t[-288:], out_p[-288:]
