from __future__ import annotations
import os,secrets
import uuid
import logging
import configparser
import requests
import sys, logging  
sys.modules['app'] = sys.modules[__name__]
import time
import re
import hashlib
from math import ceil  
from sqlalchemy.orm import synonym
from pathlib import Path
from datetime import datetime, timedelta
from cryptography.fernet import Fernet,InvalidToken
from requests_oauthlib import OAuth2Session
import base64
import qrcode
import tempfile
from io import BytesIO,StringIO
import io, csv
FERNET_KEY = "9gfBuQFUmVv1_iGpUk3X8N3zPBsGPv0TQlf60OjYH9U="
f = Fernet(FERNET_KEY.encode())
from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, flash, abort, session, Blueprint, send_file,make_response,current_app
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
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO, emit, join_room, leave_room
from passlib.hash import argon2
from sqlalchemy import text, ForeignKey,func

import yfinance as yf
import google.generativeai as genai
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import pyotp
import time
from stocks import stock_bp
from sentiment import sent_bp
from cv_pattern import cv_bp
from forecast import fc_bp
from backtest import bt_bp
from portfolio import pf_bp
from metaverse import mv_bp
from trend_analysis import trend_bp
from member_bp import member_bp


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
TMP_DIR = tempfile.gettempdir()

# 建立 Flask app
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32).hex())
app.register_blueprint(stock_bp, url_prefix="/stocks")
app.register_blueprint(trend_bp, url_prefix="/analysis")
socketio = SocketIO(app, cors_allowed_origins="*") 
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
_model = genai.GenerativeModel("gemini-1.5-flash-latest")
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
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
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
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    label        = db.Column(db.String(50))
    key_hash     = db.Column(db.String(64), nullable=False)
    key_prefix   = db.Column(db.String(8),  nullable=False)
    scopes       = db.Column(db.String(50))          # "read,trade"
    ip_whitelist = db.Column(db.String(255))
    created_at   = db.Column(db.Integer, default=lambda: int(time.time()))
    expires_at   = db.Column(db.Integer)
    last_used    = db.Column(db.Integer)
    revoked      = db.Column(db.Boolean, default=False)

    @staticmethod
    def _plain() -> str: return secrets.token_hex(32)   # 64 hex
    
    @classmethod
    def create(cls, user_id:int, **kw):
        plain = cls._plain()
        inst  = cls(
            user_id    = user_id,
            key_hash   = hashlib.sha256(plain.encode()).hexdigest(),
            key_prefix = plain[:4] + '…',
            **kw
        )
        db.session.add(inst); db.session.commit()

        return inst, plain
# WTForms：註冊與登入表單
class _F(FlaskForm):
    class Meta:
        csrf = False

# ----------------- WTForm -----------------
class NewApiKeyForm(_F):
    label      = StringField("金鑰名稱", validators=[Optional(), Length(0,50)])
    scopes     = SelectField("權限", choices=[
                   ("read","READ"),("trade","TRADE"),("write","WRITE")])
    expires_at = StringField("到期日 (YYYY-MM-DD)", validators=[Optional()])
    submit     = SubmitField("建立")

class Team(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(100), nullable=False)
    owner_id  = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    created   = db.Column(db.Integer, default=lambda: int(time.time()))
    owner     = db.relationship("User", backref="teams_owned", lazy="joined")
    members = db.relationship(
        "TeamMember",
        backref="team",
        cascade="all, delete-orphan"
    )

class TeamMember(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    team_id   = db.Column(db.Integer, ForeignKey("team.id"), nullable=False)
    user_id   = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    role      = db.Column(db.String(32), default="member")
    joined    = db.Column(db.Integer, default=lambda: int(time.time()))
    user = db.relationship("User", backref="team_members", lazy="joined")
    
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
    
class Notification(db.Model):
    __tablename__ = 'notification'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    body        = db.Column(db.Text, nullable=False)
    link        = db.Column(db.String(300))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_read     = db.Column(db.Boolean, default=False, nullable=False)    

class ChatMessage(db.Model):
    __tablename__ = 'chat_message'
    id       = db.Column(db.Integer, primary_key=True)
    team_id  = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    user_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content  = db.Column(db.Text, nullable=False)
    ts       = db.Column(db.Integer, default=lambda: int(time.time()))
    user     = db.relationship("User", lazy="joined")
        
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

app.register_blueprint(sent_bp,   url_prefix="/")
app.register_blueprint(cv_bp,     url_prefix="/")
app.register_blueprint(fc_bp,     url_prefix="/")
app.register_blueprint(bt_bp,     url_prefix="/")
app.register_blueprint(pf_bp,     url_prefix="/")
app.register_blueprint(mv_bp,     url_prefix="/")

@login_mgr.user_loader
def load_user(uid: str) -> User | None:
    return db.session.get(User, int(uid))

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

class SecurityForm(_F):
    old_password         = PasswordField("舊密碼", validators=[DataRequired()])
    new_password         = PasswordField("新密碼", validators=[DataRequired(), Length(6,64)])
    confirm_new_password = PasswordField("確認新密碼", validators=[EqualTo("new_password")])
    submit_password      = SubmitField("更新密碼")
    enable_2fa           = SubmitField("啟用 2FA")
    disable_2fa          = SubmitField("關閉 2FA")
    resend_email         = SubmitField("重新寄送驗證信")

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
    email  = StringField(
        "邀請人電子郵件",
        validators=[DataRequired(), Email()],
        render_kw={"placeholder": "輸入使用者電子郵件"}
    )
    submit = SubmitField("邀請")

class ChangePasswordForm(_F):
    old_password         = PasswordField("舊密碼", validators=[DataRequired()])
    new_password         = PasswordField("新密碼", validators=[DataRequired(), Length(6,64)])
    confirm_new_password = PasswordField("確認新密碼", validators=[EqualTo("new_password")])
    submit_password      = SubmitField("更新密碼")

class ResendEmailForm(_F):
    resend_email = SubmitField("重新寄送驗證信")

class TwoFactorForm(_F):
    enable_2fa  = SubmitField("啟用 2FA")
    disable_2fa = SubmitField("關閉 2FA")

class DataExportForm(_F):
    record_type = SelectField(
        "資料類型",
        choices=[
            ("api_calls",     "API 呼叫紀錄"),
            ("portfolio",     "投資組合項目"),
            ("teams",         "團隊列表"),
            ("team_members",  "團隊成員清單"),
        ],
        validators=[DataRequired()]
    )
    start_date    = StringField("起始日期", validators=[Optional()])
    end_date      = StringField("結束日期", validators=[Optional()])
    submit_export = SubmitField("匯出 CSV")

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
    """
    將數字加上千分號；若 val 為 None 或無法轉為 float，
    則回傳 '0'（或原值）。
    """
    if val is None:
        return "0"
    try:
        return f"{val:,.0f}"
    except (ValueError, TypeError):
        pass
    try:
        num = float(val)
        return f"{num:,.0f}"
    except (ValueError, TypeError):

        return str(val)

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

@limiter.exempt
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

@member_bp.route('/admin', methods=['GET'])
@login_required
def admin_panel():
    if not current_user.is_admin:
        abort(403)
    return render_template('member/admin_panel.html')

@member_bp.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        abort(403)
    # 分頁參數
    page    = request.args.get('page', 1, type=int)
    per_page = 10
    q = User.query.order_by(User.created.desc())
    total = q.count()
    users = q.offset((page-1)*per_page).limit(per_page).all()
    pages = ceil(total / per_page)
    return render_template(
        'member/admin_users.html',
        users=users,
        page=page,
        pages=pages,
        total=total
    )

# 顯示 & 處理單一使用者編輯表單
@member_bp.route('/admin/users/<int:uid>/edit', methods=['GET','POST'])
@login_required
def admin_edit_user(uid):
    if not current_user.is_admin:
        abort(403)
    user = User.query.get_or_404(uid)
    class EditUserForm(FlaskForm):
        username = StringField('使用者名稱', validators=[DataRequired(), Length(3,20)])
        is_admin = SelectField('管理員權限', choices=[('0','否'),('1','是')])
        submit   = SubmitField('儲存變更')

    form = EditUserForm(obj=user)
    form.is_admin.data = '1' if user.is_admin else '0'

    if form.validate_on_submit():
        user.username = form.username.data
        user.is_admin = (form.is_admin.data == '1')
        db.session.commit()
        flash(f"使用者 {user.username} 已更新", "success")
        return redirect(url_for('member.admin_users'))
    return render_template('member/admin_edit_user.html', form=form, user=user)

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

@member_bp.route('/admin/users/<int:uid>/delete', methods=['POST'])
@login_required
def admin_delete_user(uid):
    if not current_user.is_admin:
        abort(403)
    user = User.query.get_or_404(uid)
    name = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"使用者「{name}」已被刪除。", "warning")
    return redirect(url_for('member.admin_users'))

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

import notifications

@app.template_filter("datetime")
def _dt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "-"
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
    
@app.template_filter("tsfmt")
def tsfmt(ts: int | None, fmt: str = "%Y-%m-%d %H:%M:%S"):
    """timestamp → 指定格式字串；ts 為 None 回傳 '-'"""
    return datetime.fromtimestamp(ts).strftime(fmt) if ts else "-"

@member_bp.route('/2fa/qrcode')
@login_required
def two_factor_qr():
    # 確保已經有 secret
    if not current_user.two_factor_secret:
        abort(404)

    # 產生 otpauth URI
    totp = pyotp.TOTP(current_user.two_factor_secret)
    uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="FinWeb"
    )

    # 用 qrcode 庫畫圖
    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(buf, mimetype='image/png')

@member_bp.app_template_filter("ts2date")
def _fmt(ts:int|None): return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "-"

@member_bp.route('/audit_logs')
@login_required
def audit_logs():
    start_date = request.args.get('start_date', '')
    end_date   = request.args.get('end_date', '')
    page       = request.args.get('page', 1, type=int)

    q = ApiCallLog.query.filter_by(user_id=current_user.id)

    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            q = q.filter(ApiCallLog.timestamp >= dt)
        except ValueError:
            pass
    if end_date:
        try:
            dt2 = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            q = q.filter(ApiCallLog.timestamp < dt2)
        except ValueError:
            pass

    q = q.order_by(ApiCallLog.timestamp.desc())
    pagination = q.paginate(page=page, per_page=10)
    logs       = pagination.items

    return render_template(
        'member/audit_logs.html',
        logs=logs,
        pagination=pagination,
        start_date=start_date,
        end_date=end_date
    )

@member_bp.route('/notifications', methods=['GET'])
@login_required
def notifications():
    notes = (Notification.query
             .filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc())
             .all())
    return render_template('member/notifications.html', notes=notes)

@member_bp.route('/notifications/mark_read/<int:note_id>', methods=['POST'])
@login_required
def mark_notification_read(note_id):
    note = Notification.query.get_or_404(note_id)
    if note.user_id != current_user.id:
        abort(403)
    note.is_read = True
    db.session.commit()
    return redirect(url_for('member.notifications'))

@member_bp.route('/notifications/mark_all_read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    (Notification.query
         .filter_by(user_id=current_user.id, is_read=False)
         .update({Notification.is_read: True}))
    db.session.commit()
    return redirect(url_for('member.notifications'))


@member_bp.route('/data_export/download/<filename>')
@login_required
def download_export(filename):
    path = os.path.join(TMP_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name=filename,
        mimetype='text/csv'
    )
    
@member_bp.route('/data_export', methods=['GET','POST'])
@login_required
def data_export():
    form = DataExportForm()
    csv_ready    = False
    csv_filename = ""

    if form.validate_on_submit():
        rt = form.record_type.data
        sd, ed = None, None
        if rt == 'api_calls':
            try:
                if form.start_date.data:
                    sd = datetime.strptime(form.start_date.data, "%Y-%m-%d")
                if form.end_date.data:
                    ed = datetime.strptime(form.end_date.data, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                flash("日期格式錯誤，請使用 YYYY-MM-DD", "danger")
                return redirect(url_for('member.data_export'))

            q = ApiCallLog.query.filter_by(user_id=current_user.id)
            if sd: q = q.filter(ApiCallLog.timestamp >= sd)
            if ed: q = q.filter(ApiCallLog.timestamp < ed)
            records = q.order_by(ApiCallLog.timestamp).all()

            headers = ["ID","User ID","呼叫時間"]
            rows = [[r.id, r.user_id, r.timestamp.strftime("%Y-%m-%d %H:%M:%S")]
                    for r in records]

        elif rt == 'portfolio':
            items = PortfolioItem.query.filter_by(user_id=current_user.id).all()
            headers = ["ID","Symbol","數量"]
            rows = [[i.id, i.symbol, i.quantity] for i in items]

        elif rt == 'teams':
            teams = Team.query.filter_by(owner_id=current_user.id).all()
            headers = ["ID","團隊名稱","建立時間"]
            rows = [
                [t.id, t.name, datetime.fromtimestamp(t.created).strftime("%Y-%m-%d")]
                for t in teams
            ]

        elif rt == 'team_members':
            members = (TeamMember.query
                       .join(Team, TeamMember.team_id == Team.id)
                       .filter(Team.owner_id == current_user.id)
                       .all())
            headers = ["ID","Team ID","User ID","角色","加入時間"]
            rows = [
                [m.id, m.team_id, m.user_id, m.role,
                 datetime.fromtimestamp(m.joined).strftime("%Y-%m-%d")]
                for m in members
            ]

        else:
            headers = []
            rows = []
        if rows:
            sio = StringIO()
            writer = csv.writer(sio)
            writer.writerow(headers)
            writer.writerows(rows)

            filename = f"{rt}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
            tmp_path = os.path.join(TMP_DIR, filename)
            with open(tmp_path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(sio.getvalue())

            csv_ready    = True
            csv_filename = filename
        else:
            flash("找不到符合條件的資料", "warning")

    return render_template(
        'member/data_export.html',
        form=form,
        csv_ready=csv_ready,
        csv_filename=csv_filename
    )

@member_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ProfileForm()
    if form.validate_on_submit():
        user = current_user
        f = form.avatar.data
        if f:
            ext  = os.path.splitext(f.filename)[1]
            fn   = f"avatar_{user.id}{ext}"
            path = os.path.join(app.static_folder, 'uploads', fn)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            f.save(path)
            user.avatar_url = url_for('static', filename=f'uploads/{fn}')
        user.username = form.username.data
        user.email    = form.email.data
        user.timezone = request.form.get("timezone", user.timezone)
        user.language = request.form.get("language", user.language)

        db.session.commit()
        flash("個人檔案已更新", "success")
        return redirect(url_for('member.profile'))

    if request.method == 'GET':
        form.username.data = current_user.username
        form.email.data    = current_user.email

    return render_template("member/profile.html", form=form)

@member_bp.route('/security', methods=['GET','POST'])
@login_required
def security():
    user = current_user

    # 建立三個 form instance
    pwd_form  = ChangePasswordForm()
    mail_form = ResendEmailForm()
    twof_form = TwoFactorForm()

    # 1) 如果按下「更新密碼」
    if pwd_form.submit_password.data and pwd_form.validate_on_submit():
        if argon2.verify(pwd_form.old_password.data + PEPPER.decode(), user.password):
            user.password = argon2.hash(pwd_form.new_password.data + PEPPER.decode())
            db.session.commit()
            flash("密碼已更新", "success")
        else:
            flash("舊密碼不正確", "danger")
        return redirect(url_for('member.security'))

    # 2) 如果按下「重新寄送驗證信」
    if mail_form.resend_email.data:
        if not user.confirmed:
            send_verification_email(user)
            flash("驗證信已重新寄送", "success")
        else:
            flash("電子郵件已經驗證", "info")
        return redirect(url_for('member.security'))

    # 3) 如果按下「啟用 2FA」
    if twof_form.enable_2fa.data:
        if not user.two_factor_secret:
            secret = pyotp.random_base32()
            user.two_factor_secret = secret
            db.session.commit()
            flash("2FA 已啟用，請掃描下方 QR Code 或保存秘密鑰匙", "success")
        else:
            flash("2FA 已在使用中", "info")
        return redirect(url_for('member.security'))

    # 4) 如果按下「關閉 2FA」
    if twof_form.disable_2fa.data:
        user.two_factor_secret = None
        db.session.commit()
        flash("2FA 已關閉", "warning")
        return redirect(url_for('member.security'))

    # GET 時：準備 QR Code URI
    provisioning_uri = None
    if user.two_factor_secret:
        totp = pyotp.TOTP(user.two_factor_secret)
        provisioning_uri = totp.provisioning_uri(
            name=user.email,
            issuer_name="FinWeb"
        )

    now_ts = int(time.time())
    return render_template(
        'member/security.html',
        pwd_form=pwd_form,
        mail_form=mail_form,
        twof_form=twof_form,
        provisioning_uri=provisioning_uri,
        now_ts=now_ts
    )

@member_bp.route("/subscription", methods=["GET", "POST"])
@login_required
def subscription():
    """
    訂閱 / 帳單頁（簡化版，不再使用全域 PLANS）。
    僅靠 WTForms 的 <select> 來升／降級，並顯示帳單紀錄。
    """
    user = current_user
    form = SubscriptionForm()

    if form.validate_on_submit():
        chosen = form.membership_level.data
        user.membership_level    = chosen
        user.subscription_status = "inactive" if chosen == "free" else "active"
        db.session.commit()
        flash("✅ 方案已更新！", "success")
        return redirect(url_for("member.subscription"))

    billing_history = [
        {"date": datetime(2025, 5, 10), "item": "進階方案（月費）",
         "amount": 9.9, "status": "已付款", "invoice": "#INV-20250510-001"},
        {"date": datetime(2025, 4, 10), "item": "進階方案（月費）",
         "amount": 9.9, "status": "已付款", "invoice": "#INV-20250410-001"},
    ]

    if request.method == "GET":
        form.membership_level.data = user.membership_level

    return render_template(
        "member/subscription.html",
        form=form,
        status=user.subscription_status,
        billing_history=billing_history,
    )

@member_bp.route('/api-keys', methods=['GET', 'POST'])
@limiter.exempt
@login_required
def api_keys():
    if request.method == "POST":
        label = request.form.get('label','').strip()
        if not label:
            label = datetime.utcnow().strftime("Key-%Y%m%d-%H%M%S")
        scopes = request.form.get('scopes', 'read')
        exp_ts = None
        if request.form.get('expires_at'):
            try:
                exp_ts = int(datetime.strptime(
                    request.form['expires_at'], "%Y-%m-%d"
                ).timestamp())
            except ValueError:
                return jsonify(ok=False, msg="日期格式錯誤"), 400

        inst, plain = ApiKey.create(
            user_id=current_user.id,
            label=label,
            scopes=scopes,
            expires_at=exp_ts
        )
        return jsonify(ok=True, key=plain), 201

    keys = ApiKey.query.filter_by(user_id=current_user.id)\
                      .order_by(ApiKey.created_at.desc()).all()
    return render_template(
        "member/api_keys.html",
        keys=keys,
        now=int(time.time())
    )

@member_bp.get('/api-keys/debug')
@login_required
def api_keys_debug():
    rows = ApiKey.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        "id": k.id, "label": k.label, "revoked": k.revoked,
        "prefix": k.key_prefix, "scopes": k.scopes,
        "created": k.created_at
    } for k in rows])

@member_bp.post('/teams/<int:team_id>/leave')
@login_required
def leave_team(team_id):
    """一般成員自行退出團隊"""
    team = Team.query.get_or_404(team_id)
    if team.owner_id == current_user.id:
        flash("您是團隊擁有者，無法直接退出。", "warning")
        return redirect(url_for('member.teams'))
    tm = TeamMember.query.filter_by(team_id=team.id,
                                    user_id=current_user.id).first()
    if tm:
        db.session.delete(tm)
        db.session.commit()
        flash("✅ 已退出團隊", "success")
    return redirect(url_for('member.teams'))
    
@member_bp.post('/api-keys/<int:key_id>/rotate')
@limiter.exempt
@login_required
def rotate_api_key(key_id):
    k = ApiKey.query.get_or_404(key_id)
    if k.user_id != current_user.id:
        abort(403)
    plain = ApiKey._plain()
    k.key_hash   = hashlib.sha256(plain.encode()).hexdigest()
    k.key_prefix = plain[:4] + '…'
    k.created_at = int(time.time())
    k.revoked    = False
    db.session.commit()
    session['new_key'] = plain
    flash("已重新產生金鑰", "success")
    return redirect(url_for('member.api_keys'))

@member_bp.post('/api-keys/delete/<int:key_id>')
@limiter.exempt
@login_required
def delete_api_key(key_id):
    k = ApiKey.query.get_or_404(key_id)
    if k.user_id != current_user.id:
        abort(403)
    db.session.delete(k)
    db.session.commit()
    return jsonify(ok=True), 200

@member_bp.post('/api-keys/<int:key_id>/rename')
@limiter.exempt
@login_required
def rename_api_key(key_id):
    k = ApiKey.query.get_or_404(key_id)
    if k.user_id != current_user.id:
        abort(403)
    new_label = request.form.get('label','').strip()
    if not new_label:
        return jsonify(ok=False, msg="名稱不可為空"), 400
    k.label = new_label
    db.session.commit()
    return jsonify(ok=True), 200

@member_bp.get('/api-keys/<int:key_id>/reveal')
@login_required
def reveal_api_key(key_id):
    k = ApiKey.query.get_or_404(key_id)
    if k.user_id != current_user.id: abort(403)
    return jsonify({"key": "僅示範，請在 session['new_key'] 顯示一次"}), 200

@member_bp.route('/teams', methods=['GET', 'POST'])
@login_required
def teams():
    """團隊列表 + 建立新團隊"""
    form = TeamForm()
    owned = Team.query.filter_by(owner_id=current_user.id).all()
    joined = (
        Team.query.join(TeamMember, Team.id == TeamMember.team_id)
                  .filter(TeamMember.user_id == current_user.id,
                          Team.owner_id != current_user.id)
                  .all()
    )
    if form.validate_on_submit():
        new_team = Team(name=form.name.data, owner_id=current_user.id)
        db.session.add(new_team)
        db.session.commit()
        flash("✅ 團隊已建立", "success")
        return redirect(url_for('member.teams'))

    return render_template('member/teams.html',
                           form=form, owned=owned, joined=joined)

def send_team_invite_email(inviter: User, target: User, team: Team):
    """寄發團隊邀請通知 Email."""
    msg = Message(
        subject=f"[FinWeb] 您已被邀請加入團隊「{team.name}」",
        recipients=[target.email]
    )
    msg.body = (
        f"親愛的 {target.username} 您好：\n\n"
        f"使用者 {inviter.username} 已邀請您加入團隊「{team.name}」。\n"
        f"請登入 FinWeb 後台，在「團隊管理」中查看並接受邀請。\n\n"
        "FinWeb 團隊敬上"
    )
    mail.send(msg)

@member_bp.route('/teams/<int:team_id>', methods=['GET', 'POST'])
@login_required
def team_detail(team_id):
    """團隊成員管理與邀請"""
    team = Team.query.get_or_404(team_id)
    is_owner  = team.owner_id == current_user.id
    is_member = TeamMember.query.filter_by(team_id=team.id,
                                           user_id=current_user.id).first()
    if not (is_owner or is_member):
        abort(403)

    form = InviteForm()
    members = (TeamMember.query.filter_by(team_id=team.id)
                               .order_by(TeamMember.joined.desc())
                               .all())

    if is_owner and form.validate_on_submit():
        raw_email  = form.email.data.lower().strip()
        target = User.query.filter_by(
            email_hash=hashlib.sha256(raw_email.encode()).hexdigest()
        ).first()

        if not target:
            flash("❌ 查無此使用者", "danger")
        elif TeamMember.query.filter_by(team_id=team.id,
                                        user_id=target.id).first():
            flash("該使用者已在此團隊", "info")
        else:
            db.session.add(TeamMember(team_id=team.id, user_id=target.id))
            db.session.commit()
            flash(f"✅ 已將 {raw_email} 加入團隊！", "success")
        return redirect(url_for('member.team_detail', team_id=team.id))

    return render_template('member/team_detail.html',
                           team=team, members=members,
                           form=form, is_owner=is_owner)

@member_bp.post('/teams/<int:team_id>/members/<int:member_id>/remove')
@login_required
def remove_member(team_id, member_id):
    """擁有者移除成員"""
    team = Team.query.get_or_404(team_id)
    if team.owner_id != current_user.id:
        abort(403)
    db.session.delete(TeamMember.query.get_or_404(member_id))
    db.session.commit()
    flash("🗑️ 已移除該成員", "warning")
    return redirect(url_for('member.team_detail', team_id=team.id))

@member_bp.post('/teams/<int:team_id>/delete')
@login_required
def delete_team(team_id):
    """擁有者刪除整個團隊"""
    team = Team.query.get_or_404(team_id)
    if team.owner_id != current_user.id:
        abort(403)
    TeamMember.query.filter_by(team_id=team.id).delete()
    db.session.delete(team)
    db.session.commit()
    flash("🗑️ 團隊已刪除", "warning")
    return redirect(url_for('member.teams'))

@member_bp.route('/teams/<int:team_id>/chat', endpoint='team_chat')
@login_required
def team_chat(team_id):
    """
    團隊即時聊天頁。
    只有團隊擁有者或成員可進入。
    """
    team = Team.query.get_or_404(team_id)
    is_member = (team.owner_id == current_user.id) or \
        TeamMember.query.filter_by(
            team_id=team.id,
            user_id=current_user.id
        ).first()

    if not is_member:
        abort(403)
    msgs = (ChatMessage.query
            .filter_by(team_id=team.id)
            .order_by(ChatMessage.ts.asc())
            .limit(50).all())

    return render_template(
        'member/team_chat.html',
        team=team,
        messages=msgs
    )

app.register_blueprint(member_bp, url_prefix="/member")

@socketio.on('join')
def handle_join(data):
    team_id = int(data.get('team_id', 0))
    join_room(f"team-{team_id}")
    emit('status', {
        'msg': f"{current_user.username} 已加入聊天"
    }, room=f"team-{team_id}")

@socketio.on('send')
def handle_send(data):
    team_id = int(data.get('team_id', 0))
    msg_txt = (data.get('msg') or '').strip()
    if not msg_txt:
        return
    chat_msg = ChatMessage(
        team_id=team_id,
        user_id=current_user.id,
        content=msg_txt
    )
    db.session.add(chat_msg)
    db.session.commit()
    emit('message', {
        'user': current_user.username,
        'msg' : msg_txt,
        'ts'  : chat_msg.ts
    }, room=f"team-{team_id}")
    
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
SYSTEM_PROMPT = (
    "你是 FinWeb AI Assistant，一位友善又專業的金融顧問，"
    "擅長加密貨幣、股票、原油、外匯等資訊。"
    "所有回覆使用繁體中文，且結尾要附一句「還需要其他協助嗎？」"
)

def call_gemini(user_msg: str) -> str:
    # 直接用 dict 而不是 typed object
    messages = [
        {"author": "system", "content": SYSTEM_PROMPT},
        {"author": "user",   "content": user_msg},
    ]
    response = genai.chat.create(
        model="gemini-1.5-flash-latest",
        messages=messages,
        temperature=0.65,
        top_p=0.9,
        max_output_tokens=512,
    )
    # 取第一個 choice 的內容
    return response.choices[0].message.content.strip()

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_msg = data and data.get('message')
        if not user_msg:
            return jsonify({"error": "缺少 'message' 欄位"}), 400

        reply_text = call_gemini(user_msg)
        return jsonify({"reply": reply_text})
    except Exception as e:
        app.logger.exception("呼叫 /api/chat 發生錯誤")
        return jsonify({"error": str(e)}), 500
   
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
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app.logger.handlers = logging.getLogger().handlers
    app.logger.setLevel(logging.INFO)
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)