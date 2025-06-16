# FinWeb – 即時金融資訊平台
> **Stocks • Crypto • AI Forecast • Pattern Detection • Backtesting**

<p align="center">
  <img src="static/img/finweb_banner.png" width="75%" alt="FinWeb banner">
</p>

---

## 專案特色
* **即時行情聚合**：整合 TWSE、Coingecko、AlphaVantage… 等來源，一支 API 同步回傳多市場報價  
* **AI 模組整合**  
  * YOLOv8 蠟燭形態偵測  
  * LSTM / XGBoost 價格預測  
  * Backtrader 策略回測  
* **權限‧API-Key 管理**：會員、API 金鑰、速率限制、SQLite LRU 快取   

## 系統架構
```

Browser ── HTTPS ──► Nginx (Reverse Proxy)
│
└► Gunicorn (WSGI, 4 workers)
│
├── Flask blueprints
│     • stocks      • crypto
│     • forecast    • cv_pattern
│     • backtest    • member center
│
├── SQLite / SQLAlchemy
└── Cache / Rate-limit layer

````

## 核心功能
| 模組 | 端點 | 說明 |
|------|------|------|
| **Stocks**           | `/api/stocks` | 即時股票報價、K 線、技術指標 |
| **Crypto**           | `/api/crypto_price`<br>`/api/crypto_detail` | 幣價、24h 漲跌、OHLC |
| **Pattern CV**       | `/api/cv_detect` | 上傳蠟燭圖 → YOLOv8 形態偵測 |
| **AI Forecast**      | `/forecast` (BP) | LSTM & XGBoost 次日收盤預測 |
| **Backtest**         | `/backtest` (BP) | Backtrader 回測收益走勢 |
| **Member & API-Key** | `/member/*` | 註冊 / 2FA / API 金鑰 / Audit Log |

## 快速開始

### 本機開發
```bash
git clone https://github.com/your-org/finweb.git
cd finweb
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 建立 .env / config.ini 後執行
python app.py          # → http://127.0.0.1:5000
````

## 資料表說明

| Table            | 主要欄位                                                            | 用途           |
| ---------------- | --------------------------------------------------------------- | ------------ |
| `user`           | `id, username, email_hash, membership_level, two_factor_secret` | 會員資料         |
| `api_key`        | `user_id, key_hash, scopes, revoked`                            | 第三方授權        |
| `portfolio_item` | `user_id, symbol, quantity`                                     | 使用者投資組合      |
| `api_call_log`   | `user_id, timestamp`                                            | 速率統計 / Audit |

## 重要檔案／目錄

| 路徑                     | 說明                                                            |
| ---------------------- | ------------------------------------------------------------- |
| **`finance.db`**       | SQLite 資料庫                                                    |
| **`model/`**           | 機器學習模型與訓練資料 (約 1 GB)                                          |
| **`static/`**          | 前端靜態資源（CSS / JS / 圖片）                                         |
| **`templates/`**       | Jinja2 HTML 模板                                                |
| **`app.py`**           | Flask 入口                                                      |
| **`requirements.txt`** | Python 依賴列表                                                   |
| 其餘 `*.py`              | 各功能 Blueprint（`forecast.py`, `cv_pattern.py`, `backtest.py`…） |
