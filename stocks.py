from flask import Blueprint, jsonify, request, render_template
import requests, logging, time, configparser
from datetime import datetime

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")
AV_API_KEY = CFG.get("AlphaVantage", "API_KEY", fallback="").strip()
if not AV_API_KEY:
    raise RuntimeError("請在 config.ini 的 [AlphaVantage] 區段設定 API_KEY")

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

price_cache   = {}
price_backoff = {}

PRICE_CACHE_TTL   = 10
BACKOFF_DURATION  = 5

history_cache   = {}
history_backoff = {}

HISTORY_CACHE_TTL = 300
HISTORY_BACKOFF   = 60

@stock_bp.route("", methods=["GET"])
def stocks_page():
    return render_template("stocks.html")

@stock_bp.route("/api/stock_category_members", methods=["GET"])
def api_stock_category_members():
    category = request.args.get("category", "").strip()
    members = CATEGORY_TO_MEMBERS.get(category, [])

    if not members:
        return jsonify([]), 200

    result_list = []
    now = time.time()

    for item in members:
        sym  = item["code"]
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

@stock_bp.route("/api/stock_price", methods=["GET"])
def api_stock_price():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "symbol 參數為必要"}), 400

    now = time.time()
    last_fail = price_backoff.get(symbol)
    if last_fail and (now - last_fail) < BACKOFF_DURATION:
        return jsonify({"error": "Too Many Requests"}), 429

    cache_entry = price_cache.get(symbol)
    if cache_entry and (now - cache_entry["ts"]) < PRICE_CACHE_TTL:
        return jsonify(cache_entry["data"]), 200

    if symbol.endswith(".TW"):
        tws_code = symbol.replace(".TW", "")
        exchange = "tse"
    else:
        tws_code = symbol.replace(".TWO", "")
        exchange = "otc"

    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={exchange}_{tws_code}.tw"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        arr = resp.json().get("msgArray", [])
        if not arr:
            return jsonify({"error": "no data"}), 404

        info = arr[0]
        price_str  = info.get("z")   or ""
        prev_str   = info.get("y")   or ""
        volume_str = info.get("tv")  or "0"
        time_str   = info.get("t")   or ""

        if not price_str or price_str in ["-", "--"]:
            return jsonify({"error": "no price"}), 404

        price  = float(price_str.replace(",", ""))
        prev   = float(prev_str.replace(",", "")) if prev_str not in ["", "-", "--"] else 0.0
        volume = int(volume_str.replace(",", "")) if volume_str not in ["", "-", "--"] else 0

        change = round(price - prev, 2)
        pct    = round((change / prev) * 100, 2) if prev != 0 else 0.0

        data = {
            "code": symbol,
            "price": price,
            "change": change,
            "pct": pct,
            "volume": volume,
            "time": time_str
        }

        price_cache[symbol] = {"ts": now, "data": data}
        price_backoff.pop(symbol, None)
        return jsonify(data), 200

    except requests.exceptions.HTTPError as e:
        logging.error(f"[stock_price] HTTPError: {e} – {resp.text}")
        if resp.status_code == 429:
            price_backoff[symbol] = now
            return jsonify({"error": "Too Many Requests"}), 429
        return jsonify({"error": "service error"}), 500

    except Exception as e:
        logging.error(f"[stock_price] Exception: {e}")
        price_backoff[symbol] = now
        return jsonify({"error": str(e)}), 500

@stock_bp.route("/api/stock_history", methods=["GET"])
def api_stock_history():
    symbol = request.args.get("symbol", "").strip()
    ival   = request.args.get("interval", "5min").strip()

    if not symbol:
        return jsonify({"error": "symbol 參數為必要"}), 400

    av_symbol = symbol

    now = time.time()

    last_fail = history_backoff.get(symbol)
    if last_fail and (now - last_fail) < HISTORY_BACKOFF:
        return jsonify({"error": "Too Many Requests (backoff)"}), 429

    cache_entry = history_cache.get(symbol)
    if cache_entry and (now - cache_entry["ts"]) < HISTORY_CACHE_TTL:
        return jsonify({
            "symbol": symbol,
            "timestamps": cache_entry["timestamps"],
            "prices": cache_entry["prices"]
        }), 200

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_INTRADAY",
        "symbol": av_symbol,
        "interval": ival,
        "outputsize": "full",
        "apikey": AV_API_KEY
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        j = resp.json()

        if "Note" in j and "Thank you for using Alpha Vantage" in j["Note"]:
            history_backoff[symbol] = now
            return jsonify({"error": "Too Many Requests"}), 429

        if "Error Message" in j:
            return jsonify({"error": j["Error Message"]}), 404

        key_name = f"Time Series ({ival})"
        time_series = j.get(key_name, {})

        if not time_series:
            return jsonify({"error": "無歷史資料"}), 404

        sorted_datetimes = sorted(time_series.keys())
        timestamps = []
        prices     = []

        for dt_str in sorted_datetimes:
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                timestamps.append(dt.strftime("%H:%M"))
            except:
                timestamps.append(dt_str)

            close_price = time_series[dt_str].get("4. close", None)
            try:
                prices.append(float(close_price))
            except:
                prices.append(0.0)

        history_cache[symbol] = {
            "ts": now,
            "timestamps": timestamps,
            "prices": prices
        }
        history_backoff.pop(symbol, None)

        return (
            jsonify({
                "symbol": symbol,
                "timestamps": timestamps,
                "prices": prices
            }),
            200
        )

    except requests.exceptions.HTTPError as e:
        logging.error(f"[stock_history] HTTPError: {e} – {resp.text}")
        if resp.status_code == 429:
            history_backoff[symbol] = now
            return jsonify({"error": "Too Many Requests"}), 429
        return jsonify({"error": "AlphaVantage API error"}), 500

    except Exception as e:
        logging.error(f"[stock_history] Exception: {e}")
        history_backoff[symbol] = now
        return jsonify({"error": str(e)}), 500
