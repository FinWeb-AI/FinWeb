from __future__ import annotations
import os, uuid, logging, configparser, requests, time, re, hashlib, hmac
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from stocks import stock_bp

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")

DB_PATH = Path("instance/finance.db").resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

app.register_blueprint(stock_bp, url_prefix="/stocks")

Talisman(app, force_https=True, content_security_policy=None)

#這邊限制api速率 反爬蟲的東西
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    app=app
)
# 這邊是flask相關的設定 有安全驗證 email之類的
app.config.update(
    SQLALCHEMY_DATABASE_URI        = f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS = False,
    MAIL_SERVER                    = CFG["Email"]["MAIL_SERVER"],
    MAIL_PORT                      = int(CFG["Email"]["MAIL_PORT"]),
    MAIL_USE_TLS                   = CFG["Email"].getboolean("MAIL_USE_TLS"),
    MAIL_USERNAME                  = CFG["Email"]["MAIL_USERNAME"],
    MAIL_PASSWORD                  = CFG["Email"]["MAIL_PASSWORD"],
    MAIL_DEFAULT_SENDER            = CFG["Email"]["MAIL_DEFAULT_SENDER"],
    RECAPTCHA_SITE_KEY             = CFG["ReCAPTCHA"].get("SITE_KEY",""),
    RECAPTCHA_SECRET_KEY           = CFG["ReCAPTCHA"].get("SECRET_KEY","")
)
#下面跟資料庫有關 是用SQLite
db   = SQLAlchemy(app)
mail = Mail(app)
ts   = URLSafeTimedSerializer(app.secret_key)
login_mgr = LoginManager(app)
login_mgr.login_view = "login"

class User(UserMixin, db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    username       = db.Column(db.String(80), unique=True, nullable=False)
    email          = db.Column(db.String(120), unique=True, nullable=False)
    password       = db.Column(db.String(64), nullable=False)
    created        = db.Column(db.Integer, default=lambda: int(time.time()))
    confirm_code   = db.Column(db.String(6))
    confirm_expire = db.Column(db.Integer)
    confirmed      = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()

@login_mgr.user_loader
def load_user(uid: str) -> User | None:
    return db.session.get(User, int(uid))

class _F(FlaskForm):
    class Meta: csrf = False

class RegisterForm(_F):
    username = StringField(validators=[DataRequired(), Length(3,20)])
    email    = StringField(validators=[DataRequired(), Email()])
    password = PasswordField(validators=[DataRequired(), Length(6,30)])
    confirm  = PasswordField(validators=[EqualTo("password")])
    submit   = SubmitField()

class LoginForm(_F):
    email    = StringField(validators=[DataRequired(), Email()])
    password = PasswordField(validators=[DataRequired()])
    submit   = SubmitField()

def verify_recaptcha(tok: str) -> bool:
    secret = app.config["RECAPTCHA_SECRET_KEY"]
    if not (secret and tok):
        return True
    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": tok}, timeout=4
        )
        return resp.json().get("success", False)
    except:
        return False

@app.before_request
def block_bad_ua():
    ua = request.headers.get("User-Agent","").lower()
    if re.search(r"curl|python-requests|scrapy", ua):
        abort(403)

@app.route("/stocks")
@login_required
def stocks():
    return render_template("stocks.html")

@app.route("/crypto")
@login_required
def crypto():
    return render_template("index.html")

@app.route("/")
def index():
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

@app.route("/register", methods=["GET","POST"])
@limiter.limit("5 per minute")
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        tok = request.form.get("g-recaptcha-response","")
        if not verify_recaptcha(tok):
            flash("請完成 reCAPTCHA 驗證","danger")
        elif User.query.filter_by(username=form.username.data).first():
            flash("使用者名稱已存在","danger")
        elif User.query.filter_by(email=form.email.data.lower()).first():
            flash("此信箱已註冊過","danger")
        else:
            pwd_hex = hmac.new(
                app.secret_key.encode(),
                form.password.data.encode(),
                digestmod=hashlib.sha256
            ).hexdigest()
            code = f"{uuid.uuid4().int % 1000000:06d}"
            exp  = int(time.time()) + 3600
            user = User(
                username=form.username.data,
                email=form.email.data.lower(),
                password=pwd_hex,
                confirm_code=code,
                confirm_expire=exp
            )
            db.session.add(user)
            db.session.commit()
            link = url_for("confirm", _external=True)
            mail.send(Message(
                "FinWeb 電子郵件驗證",
                recipients=[user.email],
                body=f"您的驗證碼：{code}\n請於一小時內前往 {link} 完成驗證"
            ))
            flash("註冊成功！驗證碼已寄至信箱","success")
            return redirect(url_for("confirm"))
    return render_template("register.html", form=form,
                           recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"])

@app.route("/confirm", methods=["GET","POST"])
def confirm():
    if request.method=="POST":
        em = request.form.get("email","").lower().strip()
        cd = request.form.get("code","").strip()
        u  = User.query.filter_by(email=em).first()
        now= int(time.time())
        if not u:
            flash("查無此信箱帳號","danger")
        elif u.confirmed:
            flash("已完成驗證，請直接登入","info")
            return redirect(url_for("login"))
        elif u.confirm_code != cd:
            flash("驗證碼錯誤","danger")
        elif now > u.confirm_expire:
            flash("驗證碼已過期，請重新註冊","warning")
        else:
            u.confirmed = True
            u.confirm_code = None
            u.confirm_expire = None
            db.session.commit()
            flash("電子郵件驗證完成，請登入","success")
            return redirect(url_for("login"))
    return render_template("confirm.html")

#註冊 用到哈希加密 hmac等技術
@app.route("/login", methods=["GET","POST"])
@limiter.limit("10 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        u = User.query.filter_by(email=form.email.data.lower()).first()
        if u:
            digest = hmac.new(
                app.secret_key.encode(),
                form.password.data.encode(),
                digestmod=hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(digest, u.password):
                if not u.confirmed:
                    flash("請先完成電子郵件驗證","warning")
                else:
                    login_user(u)
                    flash(f"歡迎回來，{u.username}","success")
                    return redirect(url_for("index"))
        flash("帳號或密碼錯誤","danger")
    return render_template("login.html", form=form,
                           recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"])

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

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
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets", timeout=6,
                         params=dict(vs_currency="usd", order="market_cap_desc",
                                     per_page=100, page=1, sparkline="false"))
        r.raise_for_status()
        return jsonify([{"id":d["id"],"symbol":d["symbol"],"name":d["name"]}
                        for d in r.json()])
    except Exception as e:
        app.logger.error(f"/api/coins error: {e}")
        return jsonify({"error":"service unavailable"}),503

@app.route("/api/crypto_price")
def api_crypto_price():
    cid = (request.args.get("id") or "bitcoin").lower().strip()
    if _cached(PRICE_CACHE,cid,PRICE_TTL):
        return jsonify(PRICE_CACHE[cid]["data"])
    if _backoff(FAIL_PRICE,cid):
        return jsonify(PRICE_CACHE.get(cid,{},{}).get("data",{"error":"backoff"}))
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price", timeout=6,
                         params={"ids":cid, "vs_currencies":"usd,twd"})
        if r.status_code == 429:
            FAIL_PRICE[cid] = time.time()
            return jsonify({"error":"rate limited"})
        r.raise_for_status()
        src = r.json().get(cid,{})
        data = {
            "timestamp": int(time.time()*1000),
            "price_usd": float(src.get("usd",0)),
            "price_twd": float(src.get("twd",0))
        }
        PRICE_CACHE[cid] = {"ts":time.time(),"data":data}
        return jsonify(data)
    except Exception as e:
        FAIL_PRICE[cid] = time.time()
        app.logger.error(f"/api/crypto_price error: {e}")
        return jsonify({"error":"not found"}),200

@app.route("/api/crypto_detail")
def api_crypto_detail():
    cid = (request.args.get("id") or "bitcoin").lower().strip()
    if _cached(DETAIL_CACHE,cid,DETAIL_TTL):
        return jsonify(DETAIL_CACHE[cid]["data"])
    if _backoff(FAIL_DETAIL,cid):
        return jsonify({"error":"backoff"})
    try:
        m = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         timeout=6,
                         params={"vs_currency":"usd","ids":cid,"sparkline":"false"}).json()[0]
        twd = requests.get("https://api.coingecko.com/api/v3/simple/price",
                           timeout=6,
                           params={"ids":cid,"vs_currencies":"twd"}).json().get(cid,{}).get("twd",0)
        data = {
            "id":cid, "symbol":m["symbol"], "name":m["name"],
            "usd":m["current_price"], "twd":twd,
            "chg":m["price_change_24h"],
            "chg_pct":m["price_change_percentage_24h"],
            "high":m["high_24h"], "low":m["low_24h"],
            "vol":m["total_volume"],
            "market_cap":m.get("market_cap"),
            "market_cap_rank":m.get("market_cap_rank"),
            "total_supply":m.get("total_supply"),
            "max_supply":m.get("max_supply"),
        }
        DETAIL_CACHE[cid] = {"ts":time.time(),"data":data}
        return jsonify(data)
    except Exception as e:
        FAIL_DETAIL[cid] = time.time()
        app.logger.error(f"/api/crypto_detail error: {e}")
        return jsonify({"error":"not found"}),200

@app.route("/api/ohlc")
def api_ohlc():
    cid  = (request.args.get("id") or "bitcoin").lower().strip()
    days = request.args.get("days","1")
    key  = f"{cid}_{days}"
    if _cached(OHLC_CACHE,key,OHLC_TTL):
        return jsonify(OHLC_CACHE[key]["data"])
    if _backoff(FAIL_OHLC,key):
        return jsonify({"error":"backoff"})
    try:
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc", timeout=6,
                         params={"vs_currency":"usd","days":days})
        if r.status_code == 429:
            FAIL_OHLC[key] = time.time()
            return jsonify([])
        data = r.json()
        OHLC_CACHE[key] = {"ts":time.time(),"data":data}
        return jsonify(data)
    except Exception as e:
        FAIL_OHLC[key] = time.time()
        app.logger.error(f"/api/ohlc error: {e}")
        return jsonify({"error":"not found"}),200

SYSTEM_PROMPT = """
You are FinWeb AI Financial Assistant, a professional financial analyst and market strategist.
• 你精通：股票、加密貨幣、市場趨勢、宏觀經濟指標與公司財報分析。
• 回答時請：
  – 條理分明，用段落小標題或要點列出重點
  – 必要時給出數據、定義、參考範圍與時間點
• 以繁體中文回答，保留專有名詞英語。
""".strip()

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    msg = (request.json or {}).get("message","").strip()
    if not msg:
        return jsonify({"reply": ""}), 400

    api_key = CFG["Gemini"].get("API_KEY","").strip()
    if not api_key:
        return jsonify({"reply":"尚未設定 Gemini API_KEY"}), 200

    url = (
        "https://generativelanguage.googleapis.com/v1beta2"
        "/models/text-bison-001:generateText"
        f"?key={api_key}"
    )
    headers = {"Content-Type":"application/json; charset=utf-8"}
    payload = {
        "prompt": {
            "text": f"{SYSTEM_PROMPT}\n\nUser: {msg}\nAssistant:"
        },
        "temperature": 0.7,
        "maxOutputTokens": 512
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        j = r.json()
        reply = j.get("candidates", [{}])[0].get("output", "")
        return jsonify({"reply": reply or "無回應"}), 200

    except requests.exceptions.HTTPError as e:
        logging.error(f"/api/chat HTTPError: {e} – {r.text}")
        return jsonify({"reply":"AI 回覆失敗，請檢查 API_KEY 與 endpoint"}), 200

    except Exception as e:
        logging.error(f"/api/chat error: {e}")
        return jsonify({"reply":"AI 回覆錯誤，請稍後再試。"}), 200


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    app.run(debug=True, threaded=True, port=5000)
