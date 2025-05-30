from __future__ import annotations
import os, uuid, logging, configparser, requests, time, sys, types, re, hashlib, hmac

try:
    from zoneinfo import ZoneInfo
except Exception:
    z = types.ModuleType("zoneinfo")
    class ZoneInfo:
        def __init__(self, key="UTC"): self.key = key
        __str__ = __repr__ = lambda self: self.key
    z.ZoneInfo = ZoneInfo
    sys.modules["zoneinfo"] = z

from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, flash, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, login_required, logout_user
)
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")

DB_PATH = Path("instance/finance.db").resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

Talisman(app, force_https=True, content_security_policy=None)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    app=app
)

app.config.update(
    SQLALCHEMY_DATABASE_URI   = f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS = False,
    MAIL_SERVER               = CFG["Email"]["MAIL_SERVER"],
    MAIL_PORT                 = int(CFG["Email"]["MAIL_PORT"]),
    MAIL_USE_TLS              = CFG["Email"].getboolean("MAIL_USE_TLS"),
    MAIL_USERNAME             = CFG["Email"]["MAIL_USERNAME"],
    MAIL_PASSWORD             = CFG["Email"]["MAIL_PASSWORD"],
    MAIL_DEFAULT_SENDER       = CFG["Email"]["MAIL_DEFAULT_SENDER"],
    RECAPTCHA_SITE_KEY        = CFG["ReCAPTCHA"].get("SITE_KEY",""),
    RECAPTCHA_SECRET_KEY      = CFG["ReCAPTCHA"].get("SECRET_KEY","")
)

db   = SQLAlchemy(app)
mail = Mail(app)
ts   = URLSafeTimedSerializer(app.secret_key)
login_mgr = LoginManager(app)
login_mgr.login_view = "login"

class User(UserMixin, db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    username       = db.Column(db.String(80), unique=True, nullable=False)
    email          = db.Column(db.String(120), unique=True, nullable=False)
    password       = db.Column(db.String(64), nullable=False)  # hex sha256
    created        = db.Column(db.Integer, default=lambda: int(time.time()))
    confirm_code   = db.Column(db.String(6))
    confirm_expire = db.Column(db.Integer)
    confirmed      = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()

@login_mgr.user_loader
def load_user(uid):
    return User.query.get(int(uid))

class _F(FlaskForm):
    class Meta: csrf = False

class RegisterForm(_F):
    username = StringField(validators=[DataRequired(), Length(3,20)])
    email    = StringField(validators=[DataRequired(), Email()])
    password = PasswordField(validators=[DataRequired(), Length(6,30)])
    confirm  = PasswordField(validators=[EqualTo("password")])
    honeypot = StringField(default='', render_kw={"style":"display:none"})
    submit   = SubmitField()

class LoginForm(_F):
    email    = StringField(validators=[DataRequired(), Email()])
    password = PasswordField(validators=[DataRequired()])
    honeypot = StringField(default='', render_kw={"style":"display:none"})
    submit   = SubmitField()

def verify_recaptcha(tok: str) -> bool:
    s = app.config["RECAPTCHA_SECRET_KEY"]
    if not (s and tok):
        return True
    try:
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret":s,"response":tok}, timeout=4
        )
        return r.json().get("success", False)
    except Exception as e:
        app.logger.error(f"reCAPTCHA 驗證失敗：{e}")
        return False

@app.before_request
def block_bad_ua():
    ua = request.headers.get("User-Agent", "").lower()
    if not ua or re.search(r"curl|python-requests|scrapy", ua):
        abort(403)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/assistant")
def assistant():
    return render_template("assistant.html")

@app.route("/ai-analysis")
def ai_analysis():
    return render_template("ai_analysis.html")

@app.route("/market-overview")
def market_overview():
    return render_template("market_overview.html")

@app.route("/register", methods=["GET","POST"])
@limiter.limit("5 per minute")
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        if form.honeypot.data:
            abort(400)
        if not verify_recaptcha(request.form.get("g-recaptcha-response","")):
            flash("請完成 reCAPTCHA 驗證","danger")
            return render_template("register.html",
                form=form, recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
            )
        u = User.query.filter_by(username=form.username.data).first()
        e = User.query.filter_by(email=form.email.data.lower()).first()
        if u or e:
            flash("使用者名稱或信箱已存在","danger")
        else:
            raw    = form.password.data.encode()
            digest = hmac.new(
                app.secret_key.encode(),
                raw,
                digestmod=hashlib.sha256
            ).hexdigest()
            code = f"{uuid.uuid4().int % 1000000:06d}"
            exp  = int(time.time()) + 3600
            user = User(
                username=form.username.data,
                email=form.email.data.lower(),
                password=digest,
                confirm_code=code,
                confirm_expire=exp
            )
            db.session.add(user)
            db.session.commit()
            link = url_for("confirm", _external=True)
            mail.send(Message(
                "FinWeb 電子郵件驗證",
                recipients=[user.email],
                body=f"驗證碼：{code}\n請於一小時內前往 {link} 輸入"
            ))
            flash("註冊成功！驗證碼已寄至信箱","success")
            return redirect(url_for("confirm"))
    return render_template("register.html",
        form=form, recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
    )

@app.route("/confirm", methods=["GET","POST"])
def confirm():
    if request.method=="POST":
        em  = request.form["email"].lower().strip()
        cd  = request.form["code"].strip()
        u   = User.query.filter_by(email=em).first()
        now = int(time.time())
        if not u:
            flash("查無此信箱帳號","danger")
        elif u.confirmed:
            flash("已完成驗證，請直接登入。","info")
            return redirect(url_for("login"))
        elif u.confirm_code != cd:
            flash("驗證碼錯誤","danger")
        elif now > u.confirm_expire:
            flash("驗證碼已過期，請重新註冊。","warning")
        else:
            u.confirmed      = True
            u.confirm_code   = None
            u.confirm_expire = None
            db.session.commit()
            flash("電子郵件驗證成功！請登入。","success")
            return redirect(url_for("login"))
    return render_template("confirm.html")

@app.route("/login", methods=["GET","POST"])
@limiter.limit("10 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        if form.honeypot.data:
            abort(400)
        u = User.query.filter_by(email=form.email.data.lower()).first()
        if u:
            raw    = form.password.data.encode()
            digest = hmac.new(
                app.secret_key.encode(),
                raw,
                digestmod=hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(digest, u.password):
                if not u.confirmed:
                    flash("請先完成電子郵件驗證！","warning")
                else:
                    login_user(u)
                    flash(f"歡迎回來，{u.username}","success")
                    return redirect(url_for("index"))
        flash("帳號或密碼錯誤","danger")
    return render_template("login.html",
        form=form, recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
    )

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

PRICE_CACHE : dict[str, dict]  = {}
DETAIL_CACHE: dict[str, dict]  = {}
OHLC_CACHE  : dict[str, dict]  = {}
FAIL_PRICE  : dict[str, float] = {}
FAIL_DETAIL : dict[str, float] = {}
FAIL_OHLC   : dict[str, float] = {}
PRICE_TTL   = 3600
DETAIL_TTL  = 3600
OHLC_TTL    = 3600
BACKOFF     = 300

def _cached(cache,key,ttl):  return key in cache and time.time()-cache[key]["ts"]<ttl
def _in_backoff(fail,key):   return time.time()-fail.get(key,0)<BACKOFF

@app.route("/api/coins")
def api_coins():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         params=dict(vs_currency="usd",order="market_cap_desc",
                                     per_page=100,page=1,sparkline="false"),
                         timeout=6)
        r.raise_for_status()
        return jsonify([{ "id":d["id"], "symbol":d["symbol"], "name":d["name"] }
                        for d in r.json()])
    except Exception as e:
        app.logger.error(f"/api/coins error: {e}")
        return jsonify({"error":"service unavailable"}),503

@app.route("/api/crypto_price")
def api_crypto_price():
    cid = (request.args.get("id") or "bitcoin").lower().strip()
    if _cached(PRICE_CACHE,cid,PRICE_TTL):
        return jsonify(PRICE_CACHE[cid]["data"])
    if _in_backoff(FAIL_PRICE,cid):
        return jsonify(PRICE_CACHE.get(cid,{},).get("data",{"error":"backoff"}))
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params=dict(ids=cid,vs_currencies="usd,twd"),timeout=6
        )
        if r.status_code == 429:
            app.logger.warning(f"/api/crypto_price rate limited for {cid}")
            return jsonify(PRICE_CACHE.get(cid,{}).get("data",{
                "timestamp":int(time.time()*1000),
                "price_usd":0,"price_twd":0
            }))
        r.raise_for_status()
        src = r.json().get(cid)
        if not src: raise ValueError("coin not found")
        data = dict(
            timestamp=int(time.time()*1000),
            price_usd = float(src.get("usd",0)),
            price_twd = float(src.get("twd",0))
        )
        PRICE_CACHE[cid]=dict(ts=time.time(),data=data)
        return jsonify(data)
    except Exception as e:
        FAIL_PRICE[cid]=time.time()
        app.logger.warning(f"/api/crypto_price error: {e}")
        return jsonify(PRICE_CACHE.get(cid,{},).get("data",{"error":"not found"})),200

@app.route("/api/crypto_detail")
def api_crypto_detail():
    cid=(request.args.get("id") or "bitcoin").lower().strip()
    if _cached(DETAIL_CACHE,cid,DETAIL_TTL):
        return jsonify(DETAIL_CACHE[cid]["data"])
    if _in_backoff(FAIL_DETAIL,cid):
        return jsonify(DETAIL_CACHE.get(cid,{},).get("data",{"error":"backoff"}))
    try:
        mkt = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params=dict(vs_currency="usd",ids=cid,sparkline="false"),timeout=6
        )
        if mkt.status_code == 429:
            app.logger.warning(f"/api/crypto_detail rate limited for {cid}")
            return jsonify(DETAIL_CACHE.get(cid,{},).get("data",{"error":"backoff"}))
        mkt.raise_for_status()
        m = (mkt.json() or [None])[0]
        if not m: raise ValueError("coin not found")
        twd = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params=dict(ids=cid,vs_currencies="twd"),timeout=6
        ).json().get(cid,{}).get("twd",0)
        data = dict(
            id=cid,
            symbol=m["symbol"], name=m["name"],
            usd=m["current_price"], twd=twd,
            chg=m["price_change_24h"],
            chg_pct=m["price_change_percentage_24h"],
            high=m["high_24h"], low=m["low_24h"],
            vol=m["total_volume"],
            market_cap=m.get("market_cap"),
            market_cap_rank=m.get("market_cap_rank"),
            total_supply=m.get("total_supply"),
            max_supply=m.get("max_supply"),
        )
        DETAIL_CACHE[cid]=dict(ts=time.time(),data=data)
        return jsonify(data)
    except Exception as e:
        FAIL_DETAIL[cid]=time.time()
        app.logger.warning(f"/api/crypto_detail error: {e}")
        return jsonify(DETAIL_CACHE.get(cid,{},).get("data",{"error":"not found"})),200

@app.route("/api/ohlc")
def api_ohlc():
    cid=(request.args.get("id") or "bitcoin").lower().strip()
    days=request.args.get("days","1"); key=f"{cid}_{days}"
    if _cached(OHLC_CACHE,key,OHLC_TTL):
        return jsonify(OHLC_CACHE[key]["data"])
    if _in_backoff(FAIL_OHLC,key):
        return jsonify(OHLC_CACHE.get(key,{},).get("data",{"error":"backoff"}))
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc",
            params=dict(vs_currency="usd",days=days),timeout=6
        )
        if r.status_code == 429:
            app.logger.warning(f"/api/ohlc rate limited for {cid}")
            return jsonify(OHLC_CACHE.get(key,{"data":[]})["data"])
        r.raise_for_status()
        data=r.json()
        OHLC_CACHE[key]=dict(ts=time.time(),data=data)
        return jsonify(data)
    except Exception as e:
        FAIL_OHLC[key]=time.time()
        app.logger.warning(f"/api/ohlc error: {e}")
        return jsonify(OHLC_CACHE.get(key,{},).get("data",{"error":"not found"})),200

SYSTEM_PROMPT = """
You are FinWeb AI Financial Assistant, a professional financial analyst and market strategist.
• 你精通：股票（技術＆基本面）、加密貨幣、市場趨勢、宏觀經濟指標與公司財報分析。
• 回答時請：
  – 條理分明，使用段落小標題或 ••• 要點列出重點
  – 必要時給出數據、定義、參考範圍與時間點
  – 若提問不在金融範疇，簡短回覆「抱歉，我只專注金融相關問題」。
• 以繁體中文回答，保留專有名詞英語。
""".strip()

@app.route("/api/chat", methods=["POST"])
def api_chat():
    msg = (request.json or {}).get("message","").strip()
    if not msg: return jsonify({"error":"empty"}),400
    return jsonify({"reply":"此環境無法呼叫 Gemini API，僅示範回覆。"})

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S")
    app.run(debug=True, threaded=True, port=5000)
