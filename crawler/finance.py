import requests, datetime
import yfinance as yf

def get_stock(symbol: str):
    ticker = yf.Ticker(symbol)
    data   = ticker.history(period="1d")
    if data.empty:
        return {"error": "無資料"}
    last = data.iloc[-1]
    return {
        "symbol": symbol.upper(),
        "price": round(float(last['Close']), 2),
        "time":  last.name.isoformat()
    }

def get_crypto(coin_id: str = "bitcoin"):
    url = ("https://api.coingecko.com/api/v3/simple/price"
           f"?ids={coin_id}&vs_currencies=usd")
    r = requests.get(url, timeout=10)
    if not r.ok:
        return {"error": "API 失敗"}
    price = r.json().get(coin_id, {}).get("usd")
    return {
        "id":    coin_id,
        "price": price,
        "time":  datetime.datetime.utcnow().isoformat() + "Z"
    }
