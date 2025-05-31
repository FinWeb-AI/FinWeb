from flask import Blueprint, jsonify, request, render_template
import requests, logging, time

stock_bp = Blueprint("stocks", __name__)

# 股票清單(可手動擴充 這邊後面再弄)
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

#快取清除的部分不要動到 已經是最優化解 動了會有429問題
price_cache   = {}
price_backoff = {}

PRICE_CACHE_TTL   = 10
BACKOFF_DURATION  = 5


#這邊跟stock.html有關
@stock_bp.route("/stocks", methods=["GET"])
def stocks_page():
    """
    當使用者瀏覽 http://<host>:<port>/stocks 時，
    此函式會將 templates/stocks.html 回傳給瀏覽器。
    """
    return render_template("stocks.html")

#這邊是跟API有關 主要是讀入API key
@stock_bp.route("/api/stock_category_members", methods=["GET"])
def api_stock_category_members():
    """
    範例：GET /api/stock_category_members?category=上市_半導體
    回傳格式：[
      {
        "code": "2330.TW",
        "name": "台積電",
        "price": 560.0,
        "change": 2.0,
        "pct": 0.36,
        "volume": 123456,
        "time": "13:30:05"
      },
      ...
    ]
    """
    category = request.args.get("category", "").strip()
    members = CATEGORY_TO_MEMBERS.get(category, [])

    if not members:
        return jsonify([]), 200

    result_list = []
    now = time.time()

    for item in members:
        sym = item["code"]
        name = item["name"]
        last_fail = price_backoff.get(sym)
        if last_fail and (now - last_fail) < BACKOFF_DURATION:
            result_list.append({
                "code": sym,
                "name": name,
                "price": None,
                "change": None,
                "pct": None,
                "volume": None,
                "time": None
            })
            continue

        cache_entry = price_cache.get(sym)
        if cache_entry and (now - cache_entry["ts"]) < PRICE_CACHE_TTL:
            result_list.append(cache_entry["data"])
            continue

        if sym.endswith(".TW"):
            tws_code = sym.replace(".TW", "")
            exchange = "tse"
        else:
            tws_code = sym.replace(".TWO", "")
            exchange = "otc"

        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={exchange}_{tws_code}.tw"
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            j = resp.json()
            arr = j.get("msgArray", [])
            if not arr:
                data = {
                    "code": sym,
                    "name": name,
                    "price": None,
                    "change": None,
                    "pct": None,
                    "volume": None,
                    "time": None
                }
            else:
                info = arr[0]
                price_str  = info.get("z")   or ""
                prev_str   = info.get("y")   or ""
                volume_str = info.get("tv")  or "0"
                time_str   = info.get("t")   or ""
                if not price_str or price_str in ["-", "--"]:
                    data = {
                        "code": sym,
                        "name": name,
                        "price": None,
                        "change": None,
                        "pct": None,
                        "volume": None,
                        "time": None
                    }
                else:
                    price  = float(price_str.replace(",", ""))
                    prev   = float(prev_str.replace(",", "")) if prev_str not in ["", "-", "--"] else 0.0
                    volume = int(volume_str.replace(",", "")) if volume_str not in ["", "-", "--"] else 0

                    change = round(price - prev, 2)
                    pct    = round((change / prev) * 100, 2) if prev != 0 else 0.0

                    data = {
                        "code": sym,
                        "name": name,
                        "price": price,
                        "change": change,
                        "pct": pct,
                        "volume": volume,
                        "time": time_str
                    }

            price_cache[sym] = {"ts": now, "data": data}
            price_backoff.pop(sym, None)

        except requests.exceptions.HTTPError as e:
            logging.error(f"[stock_category_members][{sym}] HTTPError: {e} – {resp.text}")
            if resp.status_code == 429:
                price_backoff[sym] = now
            data = {
                "code": sym,
                "name": name,
                "price": None,
                "change": None,
                "pct": None,
                "volume": None,
                "time": None
            }

        except Exception as e:
            logging.error(f"[stock_category_members][{sym}] Exception: {e}")
            price_backoff[sym] = now
            data = {
                "code": sym,
                "name": name,
                "price": None,
                "change": None,
                "pct": None,
                "volume": None,
                "time": None
            }

        result_list.append(data)

    return jsonify(result_list), 200
