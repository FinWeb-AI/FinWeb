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
git clone https://github.com/FinWeb-AI/FinWeb.git
cd FinWeb
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

## 檔案介紹

| #  | 檔案 / 目錄                                   | 功能與說明                                  |
| -- | ----------------------------------------- | -------------------------------------- |
| 1  | **`finance.db`**                          | SQLite 資料庫：儲存會員、API 金鑰、投資組合…等資料        |
| 2  | **`model/`**                              | 股票預測用之 LSTM / XGBoost 訓練模型與特徵檔（約 1 GB） |
| 3  | **`static/css/style.css`**                | 全站樣式（Bootstrap 5 微調＋玻璃擬態）              |
| 4  | **`static/img/`**                         | UI 圖片與橫幅 banner                        |
| 5  | **`static/js/main.js`**                   | 首頁與一般頁面共用的前端互動邏輯                       |
| 6  | **`static/js/metaverse.js`**              | 元宇宙 3D 粒子新聞牆                           |
| 7  | **`static/js/tsp-bg.js`**                 | tsparticles 動態背景設定                     |
| 8  | **`templates/member/_sidebar.html`**      | 會員中心側邊導覽                               |
| 9  | **`templates/member/api_keys.html`**      | API 金鑰管理頁                              |
| 10 | **`templates/member/audit_logs.html`**    | API 呼叫審計紀錄                             |
| 11 | **`templates/member/base_member.html`**   | 會員中心頁面母版                               |
| 12 | **`templates/member/dashboard.html`**     | 個人儀表板 (資產、API 次數、圖表)                   |
| 13 | **`templates/member/data_export.html`**   | CSV 匯出介面                               |
| 14 | **`templates/member/notifications.html`** | 系統通知列表                                 |
| 15 | **`templates/member/profile.html`**       | 個人資料 / 頭像上傳                            |
| 16 | **`templates/member/security.html`**      | 密碼修改、2FA、Email 驗證                      |
| 17 | **`templates/member/subscription.html`**  | 方案與帳單設定                                |
| 18 | **`templates/member/team_detail.html`**   | 團隊詳情 + 邀請成員                            |
| 19 | **`templates/member/teams.html`**         | 團隊列表與新增                                |
| 20 | **`templates/about.html`**                | 關於我們                                   |
| 21 | **`templates/ai_analysis.html`**          | AI 分析入口（情緒＋趨勢）                         |
| 22 | **`templates/announcements.html`**        | 平台公告                                   |
| 23 | **`templates/assistant.html`**            | ChatGPT / Gemini 助手 iframe             |
| 24 | **`templates/backtest.html`**             | 回測結果頁                                  |
| 25 | **`templates/base.html`**                 | 全站母版（Navbar / Chat Panel）              |
| 26 | **`templates/confirm.html`**              | Email 驗證碼輸入                            |
| 27 | **`templates/cv_pattern.html`**           | 上傳蠟燭圖 → 形態辨識                           |
| 28 | **`templates/forecast.html`**             | 股票價格預測圖表                               |
| 29 | **`templates/home.html`**                 | 首頁 Hero + 功能捷徑                         |
| 30 | **`templates/index.html`**                | 舊版首頁 (保留)                              |
| 31 | **`templates/login.html`**                | 登入                                     |
| 32 | **`templates/market_overview.html`**      | 全球指數 / 大宗商品總覽                          |
| 33 | **`templates/metaverse.html`**            | 元宇宙 3D 新聞粒子視覺化                         |
| 34 | **`templates/nav_items.html`**            | Navbar 主要選單片段                          |
| 35 | **`templates/portfolio.html`**            | 投資組合編輯與圖表                              |
| 36 | **`templates/register.html`**             | 註冊表單                                   |
| 37 | **`templates/sentiment.html`**            | 關鍵字情緒分析                                |
| 38 | **`templates/sponsor.html`**              | 贊助頁                                    |
| 39 | **`templates/stock_trends.html`**         | 台股趨勢排行榜                                |
| 40 | **`templates/stocks.html`**               | 即時行情＆K 線頁                              |
| 41 | **`templates/trends.html`**               | 趨勢細節頁                                  |
| 42 | **`app.py`**                              | Flask 入口、藍圖註冊、API / Web 視圖             |
| 43 | **`backtest.py`**                         | Backtrader 回測 API                      |
| 44 | **`config.ini`**                          | 機密設定：API KEY、DB 路徑…                    |
| 45 | **`cv_pattern.py`**                       | YOLOv8 形態辨識 API                        |
| 46 | **`forecast.py`**                         | LSTM / XGBoost 預測服務                    |
| 47 | **`metaverse.py`**                        | 3D 新聞粒子資料 API                          |
| 48 | **`ml_models.py`**                        | 機器學習共用函式                               |
| 49 | **`portfolio.py`**                        | 投資組合 CRUD 與市值計算                        |
| 50 | **`requirements.txt`**                    | Python 依賴套件列表                          |
| 51 | **`sentiment.py`**                        | 關鍵字情緒爬蟲 + 分析                           |
| 52 | **`stock.py`**                            | 股票即時報價 & KLine                         |
| 53 | **`taiwan_ticker.txt`**                   | TWSE/OTC 全股票代碼                         |
| 54 | **`train_ml_models.py`**                  | 模型訓練腳本                                 |
| 55 | **`trend_analysis.py`**                   | 熱門股票趨勢 (成交量/搜尋量)                       |
| 56 | **`templates/admin_panel.html`**          | 管理員儀表板                                 |
| 57 | **`templates/admin_users.html`**          | 使用者列表 (Admin)                          |
| 58 | **`templates/admin_edit_user.html`**      | 編輯使用者 (Admin)                          |
| 59 | **`templates/teams_chat.html`**           | 團隊聊天室 (WebSocket)                      |
| 60 | **`templates/grent_admin.html`**          | 管理員授權頁 (權限調整)                          |
