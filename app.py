from __future__ import annotations
import os, uuid, logging, configparser, requests, time, re, hashlib, hmac
from pathlib import Path
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for, jsonify,
                   flash, abort, session)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, login_required,
                         logout_user, current_user)
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from passlib.hash import argon2
from sqlalchemy import text
from stocks import stock_bp

try:
    from zoneinfo import ZoneInfo
except Exception:
    class ZoneInfo:
        def __init__(self, key="UTC"):
            self.key = key
        __str__ = __repr__ = lambda self: self.key
        
CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")
# load資料庫路徑，並確保目錄存在
DB_PATH = Path(CFG["DEFAULT"]["DB_PATH"]).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32).hex())
app.register_blueprint(stock_bp, url_prefix="/stocks")
Talisman(app, force_https=True, content_security_policy=None)
# 設定 CSP 為 None 以允許所有資源載入
limiter = Limiter(key_func=get_remote_address,
                  default_limits=["200 per day", "50 per hour"], app=app)
# 設定速率限制器，預設每天200次，每小時50次
app.config.update(
    SQLALCHEMY_DATABASE_URI        = f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS = False,
    MAIL_SERVER         = CFG["Email"]["MAIL_SERVER"],
    MAIL_PORT           = int(CFG["Email"]["MAIL_PORT"]),
    MAIL_USE_TLS        = CFG["Email"].getboolean("MAIL_USE_TLS", False),
    MAIL_USE_SSL        = CFG["Email"].getboolean("MAIL_USE_SSL", False),
    MAIL_USERNAME       = CFG["Email"]["MAIL_USERNAME"],
    MAIL_PASSWORD       = CFG["Email"]["MAIL_PASSWORD"],
    MAIL_DEFAULT_SENDER = CFG["Email"]["MAIL_DEFAULT_SENDER"],
    RECAPTCHA_SITE_KEY  = CFG["ReCAPTCHA"].get("SITE_KEY", ""),
    RECAPTCHA_SECRET_KEY= CFG["ReCAPTCHA"].get("SECRET_KEY", ""),
    WTF_CSRF_ENABLED    = False,
    SESSION_COOKIE_SECURE = False
)
# PEPPER 用於密碼加複雜，確保安全性
PEPPER = (CFG.get("Security","PEPPER",
          fallback=os.getenv("PW_PEPPER","super-secret-pepper"))).encode()

db   = SQLAlchemy(app)
mail = Mail(app)
ts   = URLSafeTimedSerializer(app.secret_key)
login_mgr = LoginManager(app)
login_mgr.login_view = "login"
login_mgr.login_message = None

@login_mgr.unauthorized_handler
def _unauth():
    if request.path.startswith("/assistant"):
        return redirect(url_for("login", next=request.url))
    now = int(time.time())
    last = session.get("_unauth_flash_ts", 0)
    if now - last > 5:
        flash("請先登入", "warning")
        session["_unauth_flash_ts"] = now
    return redirect(url_for("login", next=request.url))
#創建SQLite資料庫
class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password      = db.Column(db.String(128), nullable=False)
    created       = db.Column(db.Integer, default=lambda: int(time.time()))
    confirm_code  = db.Column(db.String(6))
    confirm_expire= db.Column(db.Integer)
    confirmed     = db.Column(db.Boolean, default=False)
    failed_login  = db.Column(db.Integer, default=0)
    locked_until  = db.Column(db.Integer)

with app.app_context():
    db.create_all()

    col_defs = {
        "confirm_code"  : "TEXT",
        "confirm_expire": "INTEGER",
        "confirmed"     : "BOOLEAN DEFAULT 0",
        "failed_login"  : "INTEGER DEFAULT 0",
        "locked_until"  : "INTEGER"
    }
    existing = {row["name"] for row in db.session.execute(
        text("PRAGMA table_info(user)")).mappings()}
    for col, ddl in col_defs.items():
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE user ADD COLUMN {col} {ddl}"))
            db.session.commit()
            app.logger.info(f"ALTER TABLE user ADD COLUMN {col} ({ddl})")

@login_mgr.user_loader
def load_user(uid:str)->User|None:
    return db.session.get(User,int(uid))

class _F(FlaskForm):
    class Meta: csrf=False
# 這邊定義了註冊和登入表單的欄位和驗證規則
class RegisterForm(_F):
    username = StringField(validators=[DataRequired(),Length(3,20)])
    email    = StringField(validators=[DataRequired(),Email()])
    password = PasswordField(validators=[DataRequired(),Length(6,64)])
    confirm  = PasswordField(validators=[EqualTo("password")])
    submit   = SubmitField()

class LoginForm(_F):
    email    = StringField(validators=[DataRequired(),Email()])
    password = PasswordField(validators=[DataRequired()])
    submit   = SubmitField()

def verify_recaptcha(tok:str)->bool:
    secret=app.config["RECAPTCHA_SECRET_KEY"]
    if not (secret and tok): return True
    try:
        r=requests.post("https://www.google.com/recaptcha/api/siteverify",
                        data={"secret":secret,"response":tok},timeout=4)
        return r.json().get("success",False)
    except: return False
# 設定驗證碼發送函式
def send_verification_email(user:User):
    link = url_for("confirm", _external=True)
    body = f"親愛的 {user.username} 您好：\n\n" \
           f"以下為您的 FinWeb 電子郵件驗證碼（1 小時內有效）：\n\n" \
           f"{user.confirm_code}\n\n" \
           f"請前往 {link} 完成驗證。\n\nFinWeb 團隊敬上"

    msg = Message("FinWeb 電子郵件驗證", recipients=[user.email], body=body)
    mail.send(msg)
    
@app.before_request
def block_bad_ua():
    ua=request.headers.get("User-Agent","").lower()
    if re.search(r"curl|python-requests|scrapy",ua):
        abort(403)

def account_locked(u:User)->bool:
    return u.locked_until and u.locked_until>int(time.time())

@app.route("/")
def index(): return render_template("index.html")

@app.route("/stocks")
@login_required
def stocks(): return render_template("stocks.html")

@app.route("/crypto")
@login_required
def crypto(): return render_template("index.html")

@app.route("/assistant")
@login_required
def assistant(): return render_template("assistant.html")

@app.route("/ai-analysis")
@login_required
def ai_analysis(): return render_template("ai_analysis.html")

@app.route("/market-overview")
@login_required
def market_overview(): return render_template("market_overview.html")
# 註冊頁面
@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        tok = request.form.get("g-recaptcha-response", "")
        if not verify_recaptcha(tok):
            flash("請完成 reCAPTCHA 驗證", "danger")
        elif User.query.filter_by(username=form.username.data).first():
            flash("使用者名稱已存在", "danger")
        elif User.query.filter_by(email=form.email.data.lower()).first():
            flash("此信箱已註冊過", "danger")
        else:
            code = f"{uuid.uuid4().int % 1_000_000:06d}"
            expire = int(time.time()) + 3600
            hashed = argon2.hash(form.password.data + PEPPER.decode())   # ← 加上 PEPPER
            user = User(username=form.username.data,
                        email=form.email.data.lower(),
                        password=hashed,
                        confirm_code=code,
                        confirm_expire=expire)
            db.session.add(user)
            db.session.commit()

            try:
                send_verification_email(user)
                flash("註冊成功！驗證碼已寄至信箱", "success")
            except Exception as e:
                app.logger.error(f"mail send error: {e}")
                flash("註冊成功，但郵件發送失敗，請檢查信箱設定", "danger")

            return redirect(url_for("confirm"))
    return render_template("register.html",
                           form=form,
                           recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"])
# 確認電子郵件驗證頁面
@app.route("/confirm", methods=["GET", "POST"])
def confirm():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        code  = request.form.get("code", "").strip()
        user  = User.query.filter_by(email=email).first()

        if not user:
            flash("查無此信箱帳號", "danger")
        elif user.confirmed:
            flash("已完成驗證，請直接登入", "info")
            return redirect(url_for("login"))
        elif user.confirm_code != code:
            flash("驗證碼錯誤", "danger")
        elif int(time.time()) > user.confirm_expire:
            flash("驗證碼已過期，請重新註冊", "warning")
        else:
            user.confirmed = True
            user.confirm_code = None
            user.confirm_expire = None
            db.session.commit()
            flash("電子郵件驗證完成，請登入", "success")
            return redirect(url_for("login"))
    return render_template("confirm.html")
# 登入頁面
@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.confirmed and argon2.verify(form.password.data + PEPPER.decode(), user.password):
            login_user(user)
            flash(f"歡迎回來，{user.username}", "success")
            return redirect(url_for("index"))
        flash("帳號或密碼錯誤，或尚未完成驗證", "danger")
    return render_template("login.html",
                           form=form,
                           recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"])
# 登出功能
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))
# 密碼重設頁面
PRICE_CACHE  = {}; FAIL_PRICE  = {}
DETAIL_CACHE = {}; FAIL_DETAIL = {}
OHLC_CACHE   = {}; FAIL_OHLC   = {}
PRICE_TTL, DETAIL_TTL, OHLC_TTL = 15, 120, 120
BACKOFF = 60
def _cached(c,k,t): return k in c and time.time()-c[k]["ts"]<t
def _backoff(f,k): return time.time()-f.get(k,0)<BACKOFF
# 密碼重設請求頁面
@app.route("/api/coins")
def api_coins():
    try:
        r=requests.get("https://api.coingecko.com/api/v3/coins/markets", timeout=6,
                       params=dict(vs_currency="usd", order="market_cap_desc",
                                   per_page=100, page=1, sparkline="false"))
        r.raise_for_status()
        return jsonify([{"id":d["id"],"symbol":d["symbol"],"name":d["name"]}
                        for d in r.json()])
    except Exception as e:
        app.logger.error(f"/api/coins error: {e}")
        return jsonify({"error":"service unavailable"}),503
# 密碼重設請求處理
@app.route("/api/crypto_price")
def api_crypto_price():
    cid=(request.args.get("id") or "bitcoin").lower().strip()
    if _cached(PRICE_CACHE,cid,PRICE_TTL):
        return jsonify(PRICE_CACHE[cid]["data"])
    if _backoff(FAIL_PRICE,cid):
        return jsonify({"error":"backoff"})
    try:
        r=requests.get("https://api.coingecko.com/api/v3/simple/price", timeout=6,
                       params={"ids":cid, "vs_currencies":"usd,twd"})
        if r.status_code==429:
            FAIL_PRICE[cid]=time.time();return jsonify({"error":"rate limited"})
        r.raise_for_status()
        src=r.json().get(cid,{})
        data={"timestamp":int(time.time()*1000),
              "price_usd":float(src.get("usd",0)),
              "price_twd":float(src.get("twd",0))}
        PRICE_CACHE[cid]={"ts":time.time(),"data":data}
        return jsonify(data)
    except Exception as e:
        FAIL_PRICE[cid]=time.time()
        app.logger.error(f"/api/crypto_price error: {e}")
        return jsonify({"error":"not found"}),200
# 加密貨幣詳細資訊API
@app.route("/api/crypto_detail")
def api_crypto_detail():
    cid=(request.args.get("id") or "bitcoin").lower().strip()
    if _cached(DETAIL_CACHE,cid,DETAIL_TTL):
        return jsonify(DETAIL_CACHE[cid]["data"])
    if _backoff(FAIL_DETAIL,cid):
        return jsonify({"error":"backoff"})
    try:
        m=requests.get("https://api.coingecko.com/api/v3/coins/markets",
                       timeout=6,
                       params={"vs_currency":"usd","ids":cid,"sparkline":"false"}).json()[0]
        twd=requests.get("https://api.coingecko.com/api/v3/simple/price",
                         timeout=6,
                         params={"ids":cid,"vs_currencies":"twd"}).json().get(cid,{}).get("twd",0)
        data={"id":cid,"symbol":m["symbol"],"name":m["name"],
              "usd":m["current_price"],"twd":twd,
              "chg":m["price_change_24h"],
              "chg_pct":m["price_change_percentage_24h"],
              "high":m["high_24h"],"low":m["low_24h"],
              "vol":m["total_volume"],
              "market_cap":m.get("market_cap"),
              "market_cap_rank":m.get("market_cap_rank"),
              "total_supply":m.get("total_supply"),
              "max_supply":m.get("max_supply")}
        DETAIL_CACHE[cid]={"ts":time.time(),"data":data}
        return jsonify(data)
    except Exception as e:
        FAIL_DETAIL[cid]=time.time()
        app.logger.error(f"/api/crypto_detail error: {e}")
        return jsonify({"error":"not found"}),200
# 加密貨幣OHLC數據API
@app.route("/api/ohlc")
def api_ohlc():
    cid=(request.args.get("id") or "bitcoin").lower().strip()
    days=request.args.get("days","1")
    key=f"{cid}_{days}"
    if _cached(OHLC_CACHE,key,OHLC_TTL):
        return jsonify(OHLC_CACHE[key]["data"])
    if _backoff(FAIL_OHLC,key):
        return jsonify({"error":"backoff"})
    try:
        r=requests.get(f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc",timeout=6,
                       params={"vs_currency":"usd","days":days})
        if r.status_code==429:
            FAIL_OHLC[key]=time.time();return jsonify([])
        data=r.json()
        OHLC_CACHE[key]={"ts":time.time(),"data":data}
        return jsonify(data)
    except Exception as e:
        FAIL_OHLC[key]=time.time()
        app.logger.error(f"/api_ohlc error: {e}")
        return jsonify({"error":"not found"}),200

SYSTEM_PROMPT="""
You are FinWeb AI Financial Assistant, a professional financial analyst and market strategist.
• 你精通：股票、加密貨幣、市場趨勢、宏觀經濟指標與公司財報分析。
• 回答時請：
  – 條理分明，用段落小標題或要點列出重點
  – 必要時給出數據、定義、參考範圍與時間點
• 以繁體中文回答，保留專有名詞英語。
""".strip()
# Gemini AI 聊天 API
@app.route("/api/chat",methods=["POST"])
@login_required
def api_chat():
    msg=(request.json or {}).get("message","").strip()
    if not msg: return jsonify({"reply":""}),400
    api_key=CFG["Gemini"].get("API_KEY","").strip()
    if not api_key: return jsonify({"reply":"尚未設定 Gemini API_KEY"}),200
    url=("https://generativelanguage.googleapis.com/v1beta2"
         "/models/text-bison-001:generateText?key="+api_key)
    payload={"prompt":{"text":f"{SYSTEM_PROMPT}\n\nUser: {msg}\nAssistant:"},
             "temperature":0.7,"maxOutputTokens":512}
    try:
        r=requests.post(url,json=payload,
                        headers={"Content-Type":"application/json"},timeout=15)
        r.raise_for_status()
        reply=r.json().get("candidates",[{}])[0].get("output","")
        return jsonify({"reply":reply or "無回應"}),200
    except Exception as e:
        logging.error(f"/api/chat error: {e}")
        return jsonify({"reply":"AI 回覆錯誤"}),200

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s",
                        datefmt="%H:%M:%S")
    app.run(debug=True, threaded=True, port=5000)
