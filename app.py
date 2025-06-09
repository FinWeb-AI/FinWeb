from __future__ import annotations
import os
import uuid
import logging
import configparser
import requests
import time
import re
import hashlib
from sqlalchemy.orm import synonym
from pathlib import Path
from datetime import datetime, timedelta
from cryptography.fernet import Fernet,InvalidToken
import base64

FERNET_KEY = "9gfBuQFUmVv1_iGpUk3X8N3zPBsGPv0TQlf60OjYH9U="
f = Fernet(FERNET_KEY.encode())
from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, flash, abort, session, Blueprint
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    login_required, logout_user, current_user
)
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, SubmitField,
    SelectField, FileField
)
from wtforms.validators import DataRequired, Email, EqualTo, Length
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from passlib.hash import argon2
from sqlalchemy import text, ForeignKey,func
import yfinance as yf
import google.generativeai as genai

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from stocks import stock_bp
from sentiment import sent_bp
from cv_pattern import cv_bp
from forecast import fc_bp
from backtest import bt_bp
from portfolio import pf_bp
from metaverse import mv_bp
from trend_analysis import trend_bp

try:
    from zoneinfo import ZoneInfo
except ImportError:
    class ZoneInfo:
        def __init__(self, key="UTC"):
            self.key = key
        __str__ = __repr__ = lambda self: self.key

# 讀取 config.ini
CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")

# 資料庫路徑（SQLite），確保路徑存在
DB_PATH = Path(CFG["DEFAULT"]["DB_PATH"]).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 建立 Flask app
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32).hex())
app.register_blueprint(stock_bp, url_prefix="/stocks")
app.register_blueprint(trend_bp, url_prefix="/analysis")

# 強制 HTTPS，允許所有 CSP
Talisman(app, force_https=True, content_security_policy=None)

# 速率限制
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    app=app
)

# Flask 其他設定
app.config.update(
    SQLALCHEMY_DATABASE_URI        = f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS = False,
    MAIL_SERVER                   = CFG["Email"]["MAIL_SERVER"],
    MAIL_PORT                     = int(CFG["Email"]["MAIL_PORT"]),
    MAIL_USE_TLS                  = CFG["Email"].getboolean("MAIL_USE_TLS", False),
    MAIL_USE_SSL                  = CFG["Email"].getboolean("MAIL_USE_SSL", False),
    MAIL_USERNAME                 = CFG["Email"]["MAIL_USERNAME"],
    MAIL_PASSWORD                 = CFG["Email"]["MAIL_PASSWORD"],
    MAIL_DEFAULT_SENDER           = CFG["Email"]["MAIL_DEFAULT_SENDER"],
    RECAPTCHA_SITE_KEY            = CFG["ReCAPTCHA"].get("SITE_KEY", ""),
    RECAPTCHA_SECRET_KEY          = CFG["ReCAPTCHA"].get("SECRET_KEY", ""),
    WTF_CSRF_ENABLED              = False,
    SESSION_COOKIE_SECURE         = False
)

# PEPPER 用於加強密碼安全
PEPPER = CFG.get("Security","PEPPER", fallback=os.getenv("PW_PEPPER","super-secret-pepper")).encode()

# 建立資料庫、Mail、Serializer、LoginManager
db   = SQLAlchemy(app)
mail = Mail(app)
ts   = URLSafeTimedSerializer(app.secret_key)
login_mgr = LoginManager(app)
login_mgr.login_view    = "login"
login_mgr.login_message = None


GEMINI_API_KEY = CFG["Gemini"].get("API_KEY","").strip() or os.getenv("GEMINI_API_KEY","")
if not GEMINI_API_KEY:
    raise RuntimeError("尚未設定 Gemini API_KEY，請檢查 config.ini 或環境變數。")

# 設定 genai 全域 API KEY
genai.configure(api_key=GEMINI_API_KEY)

# 讀 line key
LINE_TOKEN  = CFG["LINE"]["CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = CFG["LINE"]["CHANNEL_SECRET"]

# LINE Bot 設定 (V2 SDK)
LINE_TOKEN  = CFG["LINE"]["CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = CFG["LINE"]["CHANNEL_SECRET"]
line_api     = LineBotApi(LINE_TOKEN)
line_handler = WebhookHandler(LINE_SECRET)

@login_mgr.unauthorized_handler
def _unauth():
    # 如果要進 /assistant，就導到登入頁
    if request.path.startswith("/assistant"):
        return redirect(url_for("login", next=request.url))
    now  = int(time.time())
    last = session.get("_unauth_flash_ts", 0)
    if now - last > 5:
        flash("請先登入", "warning")
        session["_unauth_flash_ts"] = now
    return redirect(url_for("login", next=request.url))

# 使用者資料表
class User(UserMixin, db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    username            = db.Column(db.String(80),  unique=True, nullable=False)
    _email              = db.Column("email", db.LargeBinary, nullable=False)
    email_hash          = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password            = db.Column(db.String(128), nullable=False)
    created             = db.Column(db.Integer, default=lambda: int(time.time()))
    confirm_code        = db.Column(db.String(6))
    confirm_expire      = db.Column(db.Integer)
    confirmed           = db.Column(db.Boolean, default=False)
    failed_login        = db.Column(db.Integer, default=0)
    locked_until        = db.Column(db.Integer)
    avatar_url          = db.Column(db.String(256))
    timezone            = db.Column(db.String(64), default="UTC")
    language            = db.Column(db.String(8), default="zh-TW")
    last_login          = db.Column(db.Integer)
    membership_level    = db.Column(db.String(32), default="free")
    subscription_status = db.Column(db.String(32), default="inactive")
    two_factor_secret   = db.Column(db.String(64))
    @property
    def email(self) -> str:
        try:
            return f.decrypt(self._email).decode()
        except InvalidToken:
            return ""

    @email.setter
    def email(self, val: str):
        self._email = f.encrypt(val.encode())
        self.email_hash = hashlib.sha256(val.encode()).hexdigest()

    email = synonym('_email', descriptor=email)
    
class ApiKey(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    key       = db.Column(db.String(64), unique=True, nullable=False)
    created   = db.Column(db.Integer, default=lambda: int(time.time()))

class Team(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(100), nullable=False)
    owner_id  = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    created   = db.Column(db.Integer, default=lambda: int(time.time()))

class TeamMember(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    team_id   = db.Column(db.Integer, ForeignKey("team.id"), nullable=False)
    user_id   = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    role      = db.Column(db.String(32), default="member")
    joined    = db.Column(db.Integer, default=lambda: int(time.time()))

class PortfolioItem(db.Model):
    __tablename__ = 'portfolio_item'
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    symbol    = db.Column(db.String(20), nullable=False)
    quantity  = db.Column(db.Float,   nullable=False, default=0.0)

class ApiCallLog(db.Model):
    __tablename__ = 'api_call_log'
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
with app.app_context():
    db.create_all()
    col_defs = {
        "email_hash":          "TEXT UNIQUE",
        "confirm_code":        "TEXT",
        "confirm_expire":      "INTEGER",
        "confirmed":           "BOOLEAN DEFAULT 0",
        "failed_login":        "INTEGER DEFAULT 0",
        "locked_until":        "INTEGER",
        "avatar_url":          "TEXT",
        "timezone":            "TEXT DEFAULT 'UTC'",
        "language":            "TEXT DEFAULT 'zh-TW'",
        "last_login":          "INTEGER",
        "membership_level":    "TEXT DEFAULT 'free'",
        "subscription_status": "TEXT DEFAULT 'inactive'",
        "two_factor_secret":   "TEXT"
    }
    existing = {row["name"] for row in db.session.execute(
        text("PRAGMA table_info(user)")).mappings()
    }
    for col, ddl in col_defs.items():
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE user ADD COLUMN {col} {ddl}"))
            db.session.commit()
            app.logger.info(f"ALTER TABLE user ADD COLUMN {col} ({ddl})")

app.register_blueprint(sent_bp,   url_prefix="/")
app.register_blueprint(cv_bp,     url_prefix="/")
app.register_blueprint(fc_bp,     url_prefix="/")
app.register_blueprint(bt_bp,     url_prefix="/")
app.register_blueprint(pf_bp,     url_prefix="/")
app.register_blueprint(mv_bp,     url_prefix="/")

@login_mgr.user_loader
def load_user(uid: str) -> User | None:
    return db.session.get(User, int(uid))

# WTForms：註冊與登入表單
class _F(FlaskForm):
    class Meta:
        csrf = False

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

# 會員中心 Forms
class ProfileForm(_F):
    avatar   = FileField()
    username = StringField(validators=[DataRequired(),Length(3,20)])
    email    = StringField(validators=[DataRequired(),Email()])
    submit   = SubmitField("儲存變更")

class SettingsForm(_F):
    timezone = SelectField("時區", choices=[("UTC","UTC"),("Asia/Taipei","Asia/Taipei")])
    language = SelectField("語言", choices=[("zh-TW","繁體中文"),("en-US","English")])
    submit   = SubmitField("儲存設定")

class SecurityForm(_F):
    old_password         = PasswordField(validators=[DataRequired()])
    new_password         = PasswordField(validators=[DataRequired(),Length(6,64)])
    confirm_new_password = PasswordField(validators=[EqualTo("new_password")])
    submit_password      = SubmitField("更新密碼")
    enable_2fa           = SubmitField("啟用 2FA")

class SubscriptionForm(_F):
    membership_level = SelectField(
        "方案等級",
        choices=[("free","免費"),("pro","進階"),("enterprise","企業")])
    submit           = SubmitField("更新訂閱")

class ApiKeyForm(_F):
    submit_new_key = SubmitField("產生新 API Key")

class TeamForm(_F):
    name   = StringField("團隊名稱", validators=[DataRequired(), Length(3,50)])
    submit = SubmitField("新增團隊")

class InviteForm(_F):
    email  = StringField("邀請人電子郵件", validators=[DataRequired(),Email()])
    team_id= StringField(validators=[DataRequired()])
    submit = SubmitField("發送邀請")
    
# reCAPTCHA 驗證
def verify_recaptcha(tok: str) -> bool:
    secret = app.config["RECAPTCHA_SECRET_KEY"]
    if not (secret and tok):
        return True
    try:
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": tok},
            timeout=4
        )
        return r.json().get("success", False)
    except:
        return False

# 寄發電子郵件驗證碼
def send_verification_email(user: User):
    link = url_for("confirm", _external=True)
    body = (
        f"親愛的 {user.username} 您好：\n\n"
        f"以下為您的 FinWeb 電子郵件驗證碼（1 小時內有效）：\n\n"
        f"{user.confirm_code}\n\n"
        f"請前往 {link} 完成驗證。\n\n"
        f"FinWeb 團隊敬上"
    )
    msg = Message("FinWeb 電子郵件驗證", recipients=[user.email], body=body)
    mail.send(msg)

# 阻擋可疑 User-Agent
@app.before_request
def block_bad_ua():
    ua = request.headers.get("User-Agent","").lower()
    if re.search(r"curl|python-requests|scrapy", ua):
        abort(403)

def account_locked(u: User) -> bool:
    return u.locked_until and u.locked_until > int(time.time())

# 定義所有公告（可放在檔案頂端或 home() 上方）
ALL_ANNOUNCEMENTS = [
    {
      "date": "2025-06-05",
      "title": "系統維護公告",
      "summary": "系統將於 2025-06-10 06:00 ~ 08:00 維護，請提前儲存您的資料。",
      "summary_full": "系統將於 2025-06-10 06:00 ~ 08:00 進行維護，請提前儲存您的資料。維護期間部分功能將暫停，造成不便敬請見諒。",
      "link": None
    },
    {
      "date": "2025-05-28",
      "title": "新增廣告收益分析報表",
      "summary": "月度廣告收益統計功能上線，提供多種圖表幫助您優化投資策略。",
      "summary_full": "平台新增月度廣告收益統計功能，提供長條圖、折線圖與圓餅圖等多種視覺化報表，助您深入了解數據走勢與組合績效。",
      "link": None
    },
    {
      "date": "2025-05-20",
      "title": "Q&A 社區功能上線",
      "summary": "全新 Q&A 區域開放，投資人可相互提問與分享交易心得。",
      "summary_full": "我們推出了 Q&A 社區，讓使用者能在平台上發布問題、回覆與點讚，促進知識交流與經驗分享。",
      "link": None
    },
    {
      "date": "2025-04-15",
      "title": "系統升級公告",
      "summary": "新增多圖表下載功能，上線時間 2025-04-20。",
      "summary_full": "本次系統升級加入了 CSV/PNG 一鍵下載功能，並優化了圖表載入速度，預計於 2025-04-20 00:00 上線。",
      "link": None
    },
    {
      "date": "2025-04-01",
      "title": "愚人節特別活動",
      "summary": "4/1 一日限定遊戲，挑戰限時任務領好禮！",
      "summary_full": "歡慶愚人節，我們準備了限時答題遊戲，完成任務即有機會獲得專屬優惠券，活動僅限 2025-04-01。",
      "link": None
    },
]

@app.template_filter('comma_separator')
def comma_separator_filter(val):
    return f"{val:,.0f}"

# 首頁與其他靜態頁面
@app.route("/")
@login_required
def home():
    latest_three = ALL_ANNOUNCEMENTS[:3]
    return render_template("home.html", announcements=latest_three)

@app.route("/announcements")
@login_required
def announcements():
    return render_template("announcements.html", announcements=ALL_ANNOUNCEMENTS)

@app.route("/stocks")
@login_required
def stocks():
    return render_template("stocks.html")

@app.route("/crypto")
@login_required
def crypto():
    return render_template("index.html")

@app.route("/assistant")
@login_required
def assistant():
    return render_template("assistant.html")

@app.route("/ai-analysis")
@login_required
def ai_analysis():
    return render_template("ai_analysis.html")

@app.route("/market-overview")
@login_required
def market_overview():
    return render_template("market_overview.html")

@app.route("/sponsor")
@login_required
def sponsor():
    return render_template("sponsor.html")

@app.route("/about")
def about():
    return render_template("about.html")

# 使用者註冊
@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        # reCAPTCHA 驗證
        tok = request.form.get("g-recaptcha-response", "")
        if not verify_recaptcha(tok):
            flash("請完成 reCAPTCHA 驗證", "danger")
        # 使用者名稱不可重複
        elif User.query.filter_by(username=form.username.data).first():
            flash("使用者名稱已存在", "danger")
        else:
            # Email 欄位先做 SHA256 hash 查重
            raw_email = form.email.data.lower().strip()
            email_hash = hashlib.sha256(raw_email.encode()).hexdigest()
            if User.query.filter_by(email_hash=email_hash).first():
                flash("此信箱已註冊過", "danger")
            else:
                # 產生驗證碼與過期時間
                code   = f"{uuid.uuid4().int % 1_000_000:06d}"
                expire = int(time.time()) + 3600
                # 密碼 Argon2 雜湊
                hashed = argon2.hash(form.password.data + PEPPER.decode())
                # 建立 User，setter 會同時設定 _email 與 email_hash
                user = User(
                    username       = form.username.data,
                    email          = raw_email,
                    password       = hashed,
                    confirm_code   = code,
                    confirm_expire = expire
                )
                db.session.add(user)
                db.session.commit()
                # 寄驗證信
                try:
                    send_verification_email(user)
                    flash("註冊成功！驗證碼已寄至信箱", "success")
                except Exception as e:
                    app.logger.error(f"mail send error: {e}")
                    flash("註冊成功，但郵件發送失敗，請檢查信箱設定", "danger")
                return redirect(url_for("confirm"))
    return render_template(
        "register.html",
        form=form,
        recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
    )


@app.route("/confirm", methods=["GET", "POST"])
def confirm():
    if request.method == "POST":
        raw_email = request.form.get("email", "").lower().strip()
        code      = request.form.get("code", "").strip()
        email_hash = hashlib.sha256(raw_email.encode()).hexdigest()
        user = User.query.filter_by(email_hash=email_hash).first()
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
            user.confirmed      = True
            user.confirm_code   = None
            user.confirm_expire = None
            db.session.commit()
            flash("電子郵件驗證完成，請登入", "success")
            return redirect(url_for("login"))
    return render_template("confirm.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        raw_email = form.email.data.lower().strip()
        email_hash = hashlib.sha256(raw_email.encode()).hexdigest()
        user = User.query.filter_by(email_hash=email_hash).first()
        if user and user.confirmed and argon2.verify(form.password.data + PEPPER.decode(), user.password):
            login_user(user)
            flash(f"歡迎回來，{user.username}", "success")
            return redirect(url_for("home"))
        flash("帳號或密碼錯誤，或尚未完成驗證", "danger")
    return render_template(
        "login.html",
        form=form,
        recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
    )

# 使用者登出
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))

# 加密貨幣與股價相關 API
PRICE_CACHE  = {}; FAIL_PRICE  = {}
DETAIL_CACHE = {}; FAIL_DETAIL = {}
OHLC_CACHE   = {}; FAIL_OHLC   = {}
PRICE_TTL, DETAIL_TTL, OHLC_TTL = 15, 120, 120
BACKOFF = 60

def _cached(c,k,t): return k in c and time.time()-c[k]["ts"]<t
def _backoff(f,k): return time.time()-f.get(k,0)<BACKOFF

@app.route("/api/coins")
def api_coins():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            timeout=6,
            params=dict(
                vs_currency="usd",
                order="market_cap_desc",
                per_page=100,
                page=1,
                sparkline="false"
            )
        )
        r.raise_for_status()
        return jsonify([
            {"id":d["id"], "symbol":d["symbol"], "name":d["name"]}
            for d in r.json()
        ])
    except Exception as e:
        app.logger.error(f"/api/coins error: {e}")
        return jsonify({"error":"service unavailable"}), 503

@app.route("/api/crypto_price")
def api_crypto_price():
    cid = (request.args.get("id") or "bitcoin").lower().strip()
    if _cached(PRICE_CACHE, cid, PRICE_TTL):
        return jsonify(PRICE_CACHE[cid]["data"])
    if _backoff(FAIL_PRICE, cid):
        return jsonify({"error":"backoff"})
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            timeout=6,
            params={"ids":cid, "vs_currencies":"usd,twd"}
        )
        if r.status_code == 429:
            FAIL_PRICE[cid] = time.time()
            return jsonify({"error":"rate limited"})
        r.raise_for_status()
        src  = r.json().get(cid, {})
        data = {
            "timestamp": int(time.time()*1000),
            "price_usd": float(src.get("usd",0)),
            "price_twd": float(src.get("twd",0))
        }
        PRICE_CACHE[cid] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except Exception as e:
        FAIL_PRICE[cid] = time.time()
        app.logger.error(f"/api/crypto_price error: {e}")
        return jsonify({"error":"not found"}), 200

@app.route("/api/crypto_detail")
def api_crypto_detail():
    cid = (request.args.get("id") or "bitcoin").lower().strip()
    if _cached(DETAIL_CACHE, cid, DETAIL_TTL):
        return jsonify(DETAIL_CACHE[cid]["data"])
    if _backoff(FAIL_DETAIL, cid):
        return jsonify({"error":"backoff"})
    try:
        m = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            timeout=6,
            params={"vs_currency":"usd","ids":cid,"sparkline":"false"}
        ).json()[0]
        twd = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            timeout=6,
            params={"ids":cid,"vs_currencies":"twd"}
        ).json().get(cid, {}).get("twd",0)
        data = {
            "id": cid, "symbol": m["symbol"], "name": m["name"],
            "usd": m["current_price"], "twd": twd,
            "chg": m["price_change_24h"],
            "chg_pct": m["price_change_percentage_24h"],
            "high": m["high_24h"], "low": m["low_24h"],
            "vol": m["total_volume"], "market_cap": m.get("market_cap"),
            "market_cap_rank": m.get("market_cap_rank"),
            "total_supply": m.get("total_supply"), "max_supply": m.get("max_supply")
        }
        DETAIL_CACHE[cid] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except Exception as e:
        FAIL_DETAIL[cid] = time.time()
        app.logger.error(f"/api/crypto_detail error: {e}")
        return jsonify({"error":"not found"}), 200

@app.route("/api/ohlc")
def api_ohlc():
    cid  = (request.args.get("id") or "bitcoin").lower().strip()
    days = request.args.get("days","1")
    key  = f"{cid}_{days}"
    if _cached(OHLC_CACHE, key, OHLC_TTL):
        return jsonify(OHLC_CACHE[key]["data"])
    if _backoff(FAIL_OHLC, key):
        return jsonify({"error":"backoff"})
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc",
            timeout=6,
            params={"vs_currency":"usd","days":days}
        )
        if r.status_code == 429:
            FAIL_OHLC[key] = time.time()
            return jsonify([])
        data = r.json()
        OHLC_CACHE[key] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except Exception as e:
        FAIL_OHLC[key] = time.time()
        app.logger.error(f"/api_ohlc error: {e}")
        return jsonify({"error":"not found"}), 200

member_bp = Blueprint(
    'member',
    __name__,
    template_folder='member',
    url_prefix='/member'
)

@member_bp.route('/audit_logs')
@login_required
def audit_logs():
    # TODO:
    return render_template('member/audit_logs.html')


@member_bp.route('/notifications')
@login_required
def notifications():
    # TODO:
    return render_template('member/notifications.html')


@member_bp.route('/data_export')
@login_required
def data_export():
    # TODO:
    return render_template('member/data_export.html')

@member_bp.route('/dashboard')
@login_required
def dashboard():
    uid = current_user.id

    items = PortfolioItem.query.filter_by(user_id=uid).all()
    total = 0.0
    for it in items:
        info  = yf.Ticker(it.symbol).fast_info
        price = info.get("last_price", 0)
        total += it.quantity * price

    yesterday = datetime.utcnow() - timedelta(hours=24)
    prev_total = 0.0
    for it in items:
        hist = yf.Ticker(it.symbol).history(
            start=yesterday, end=yesterday + timedelta(minutes=1)
        )
        if not hist.empty:
            prev_price = hist['Close'].iloc[0]
            prev_total += it.quantity * prev_price

    change_pct = ((total - prev_total) / prev_total * 100) if prev_total else 0

    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    api_calls = ApiCallLog.query.filter(
        ApiCallLog.user_id==uid,
        ApiCallLog.timestamp >= today_start
    ).count()

    asset_trend = [
      {'label':'1 天前',     'value': round(prev_total)},
      {'label':'12 小時前',  'value': round((prev_total + total)/2)},
      {'label':'現在',       'value': round(total)},
    ]

    portfolio_dist = []
    for it in items:
        info  = yf.Ticker(it.symbol).fast_info
        price = info.get("last_price", 0)
        portfolio_dist.append({
            'label': it.symbol,
            'value': it.quantity * price
        })

    return render_template(
        'member/dashboard.html',
        total_assets   = round(total,2),
        change_24h     = round(change_pct,2),
        api_calls      = api_calls,
        asset_trend    = asset_trend,
        portfolio_dist = portfolio_dist
    )

@member_bp.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    form = ProfileForm()
    if form.validate_on_submit():
        user = current_user
        f = form.avatar.data
        if f:
            ext = os.path.splitext(f.filename)[1]
            fn  = f"avatar_{user.id}{ext}"
            path = os.path.join(app.static_folder, 'uploads', fn)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            f.save(path)
            user.avatar_url = url_for('static', filename=f'uploads/{fn}')
        user.username = form.username.data
        user.email    = form.email.data
        db.session.commit()
        flash("個人檔案已更新", "success")
        return redirect(url_for('member.profile'))
    if request.method=='GET':
        form.username.data = current_user.username
        form.email.data    = current_user.email
    return render_template('member/profile.html', form=form)

@member_bp.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    form = SettingsForm()
    if form.validate_on_submit():
        user = current_user
        user.timezone = form.timezone.data
        user.language = form.language.data
        db.session.commit()
        flash("帳戶設定已儲存", "success")
        return redirect(url_for('member.settings'))
    if request.method=='GET':
        form.timezone.data = current_user.timezone
        form.language.data = current_user.language
    return render_template('member/settings.html', form=form)

@member_bp.route('/security', methods=['GET','POST'])
@login_required
def security():
    form = SecurityForm()
    if form.validate_on_submit():
        user = current_user
        if form.submit_password.data:
            if argon2.verify(form.old_password.data + PEPPER.decode(), user.password):
                user.password = argon2.hash(form.new_password.data + PEPPER.decode())
                db.session.commit()
                flash("密碼已更新", "success")
            else:
                flash("舊密碼不正確", "danger")
        if form.enable_2fa.data:
            flash("2FA 功能尚在開發中", "info")
        return redirect(url_for('member.security'))
    return render_template('member/security.html', form=form)

@member_bp.route('/subscription', methods=['GET','POST'])
@login_required
def subscription():
    form = SubscriptionForm()
    if form.validate_on_submit():
        current_user.membership_level = form.membership_level.data
        current_user.subscription_status = 'active'
        db.session.commit()
        flash("訂閱方案已更新", "success")
        return redirect(url_for('member.subscription'))
    if request.method=='GET':
        form.membership_level.data = current_user.membership_level
    return render_template('member/subscription.html',
                          form=form,
                          status=current_user.subscription_status)

@member_bp.route('/api-keys', methods=['GET','POST'])
@login_required
def api_keys():
    form = ApiKeyForm()
    keys = ApiKey.query.filter_by(user_id=current_user.id).all()
    if form.validate_on_submit():
        new_key = uuid.uuid4().hex
        ak = ApiKey(user_id=current_user.id, key=new_key)
        db.session.add(ak)
        db.session.commit()
        flash("已產生新 API Key", "success")
        return redirect(url_for('member.api_keys'))
    return render_template('member/api_keys.html',
                           form=form,
                           keys=keys)

@member_bp.route('/api-keys/delete/<int:key_id>', methods=['POST'])
@login_required
def delete_api_key(key_id):
    ak = ApiKey.query.get_or_404(key_id)
    if ak.user_id != current_user.id:
        abort(403)
    db.session.delete(ak)
    db.session.commit()
    flash("API Key 已刪除", "success")
    return redirect(url_for('member.api_keys'))

@member_bp.route('/teams', methods=['GET','POST'])
@login_required
def teams():
    form = TeamForm()
    teams = Team.query.filter_by(owner_id=current_user.id).all()
    if form.validate_on_submit():
        t = Team(name=form.name.data, owner_id=current_user.id)
        db.session.add(t)
        db.session.commit()
        flash("團隊已建立", "success")
        return redirect(url_for('member.teams'))
    return render_template('member/teams.html',
                           form=form,
                           teams=teams)

@member_bp.route('/teams/<int:team_id>', methods=['GET','POST'])
@login_required
def team_detail(team_id):
    team = Team.query.get_or_404(team_id)
    if team.owner_id != current_user.id:
        abort(403)
    members = TeamMember.query.filter_by(team_id=team.id).all()
    form = InviteForm()
    if form.validate_on_submit():
        u = User.query.filter_by(email=form.email.data.lower()).first()
        if u:
            tm = TeamMember(team_id=team.id, user_id=u.id)
            db.session.add(tm)
            db.session.commit()
            flash(f"已邀請 {u.email}", "success")
        else:
            flash("查無此使用者", "danger")
        return redirect(url_for('member.team_detail', team_id=team.id))
    return render_template('member/team_detail.html',
                           team=team,
                           members=members,
                           form=form)
app.register_blueprint(member_bp)

@app.route("/api/market_summary")
@login_required
def api_market_summary():
    """
    回傳 JSON 格式：
    {
      "S&P 500":    {"price": 4500.12, "change_pct": -0.32},
      "NASDAQ":     {"price": 15000.45, "change_pct": +1.23},
      "FTSE 100":   {"price": 7600.34, "change_pct": -0.45},
      ...
    }
    其中「price」是最新價，「change_pct」是相對於前一交易日收盤價的百分比漲跌（可正可負）。
    """
    symbols_map = {

        "S&P 500":    "^GSPC",
        "NASDAQ":     "^IXIC",
        "FTSE 100":   "^FTSE",
        "DOW JONES":  "^DJI",
        "NIKKEI":     "^N225",
        "DAX":        "^GDAXI",
        "CAC 40":     "^FCHI",
        "Hang Seng":  "^HSI",
        # 大宗商品
        "Gold":       "GC=F",
        "Crude Oil":  "CL=F",
        "Copper":     "HG=F",
        "Silver":     "SI=F",
        "Natural Gas":"NG=F",
        "Platinum":   "PL=F",
        "Palladium":  "PA=F",
        # 匯率
        "USD/TWD":    "USDTWD=X",
        "EUR/USD":    "EURUSD=X",
        "USD/JPY":    "USDJPY=X",
        "GBP/USD":    "GBPUSD=X",
        "AUD/USD":    "AUDUSD=X",
        "USD/CNY":    "USDCNY=X",
        "USD/SGD":    "USDSGD=X"
    }

    summary = {}
    for name, ticker in symbols_map.items():
        try:
            tk = yf.Ticker(ticker)
            if hasattr(tk, "fast_info") and tk.fast_info:
                info = tk.fast_info
                price = info.get("last_price")
                prev_close = info.get("previous_close")
            else:
                info = tk.info or {}
                price = info.get("regularMarketPrice")
                prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

            if price is None or prev_close is None:
                hist = tk.history(period="2d", interval="1d")
                if not hist.empty and len(hist["Close"]) >= 2:
                    prev_close = float(hist["Close"].iloc[-2])
                    price = float(hist["Close"].iloc[-1])

            if price is not None and prev_close is not None and prev_close != 0:
                change_pct = (price - prev_close) / prev_close * 100
            else:
                change_pct = 0.0

            summary[name] = {
                "price": round(price, 2) if price is not None else None,
                "change_pct": round(change_pct, 2)
            }
        except Exception as e:
            summary[name] = {"price": None, "change_pct": None}

    return jsonify(summary)

# Gemini AI 投資助理：呼叫 gemini-1.5-flash-latest
SYSTEM_PROMPT = """
You are FinWeb AI Financial Assistant, a professional financial analyst and market strategist.
• 你精通：股票、加密貨幣、市場趨勢、宏觀經濟指標與公司財報分析。
• 回答時請：
  – 條理分明，用段落小標題或要點列出重點
  – 必要時給出數據、定義、參考範圍與時間點
• 以繁體中文回答，保留專有名詞英語。
""".strip()

def call_gemini(user_msg: str) -> str:
    prompt = SYSTEM_PROMPT + "\n\nUser: " + user_msg
    try:
        m = genai.GenerativeModel("gemini-1.5-flash-latest")
        r = m.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7, max_output_tokens=512,
                top_p=0.9, top_k=50
            )
        )
        return (r.text or "").strip() or "AI 無回應，請稍後再試。"
    except Exception:
        app.logger.exception("Gemini 呼叫失敗")
        return "AI 呼叫失敗，請稍後再試。"

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    user_msg = (request.json or {}).get("message","").strip()
    if not user_msg:
        return jsonify({"reply": ""}), 400
    return jsonify({"reply": call_gemini(user_msg)}), 200
   
# LINE Webhook 入口
@app.route("/callback", methods=["POST"])
def line_callback():
    signature = request.headers.get("X-Line-Signature", "")
    body      = request.get_data(as_text=True)
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK", 200

@line_handler.add(MessageEvent, message=TextMessage)
def handle_line_message(event: MessageEvent):
    user_text = event.message.text.strip()
    # 直接呼你共用的 call_gemini（或 inline 你的 SYSTEM_PROMPT + genai 呼叫）
    reply = call_gemini(user_text)
    line_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    app.run(debug=True, threaded=True, port=5000)
