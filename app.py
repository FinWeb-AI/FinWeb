from __future__ import annotations
import os, uuid, logging, configparser, requests, time, sys, types

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
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")

DB_PATH = Path("instance/finance.db").resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config.update(
    MAIL_SERVER=CFG["Email"]["MAIL_SERVER"],
    MAIL_PORT=int(CFG["Email"]["MAIL_PORT"]),
    MAIL_USE_TLS=CFG["Email"].getboolean("MAIL_USE_TLS"),
    MAIL_USERNAME=CFG["Email"]["MAIL_USERNAME"],
    MAIL_PASSWORD=CFG["Email"]["MAIL_PASSWORD"],
    MAIL_DEFAULT_SENDER=CFG["Email"]["MAIL_DEFAULT_SENDER"],
    RECAPTCHA_SITE_KEY=CFG["ReCAPTCHA"].get("SITE_KEY", ""),
    RECAPTCHA_SECRET_KEY=CFG["ReCAPTCHA"].get("SECRET_KEY", "")
)

db = SQLAlchemy(app)
mail = Mail(app)
ts = URLSafeTimedSerializer(app.secret_key)
login_mgr = LoginManager(app)
login_mgr.login_view = "login"

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created = db.Column(db.Integer, default=lambda: int(time.time()))
    confirm_code = db.Column(db.String(6))
    confirm_expire = db.Column(db.Integer)
    confirmed = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()

@login_mgr.user_loader
def load_user(uid):
    return User.query.get(int(uid))

class _F(FlaskForm):
    class Meta: csrf = False

class RegisterForm(_F):
    username = StringField(validators=[DataRequired(), Length(3,20)])
    email = StringField(validators=[DataRequired(), Email()])
    password = PasswordField(validators=[DataRequired(), Length(6,30)])
    confirm = PasswordField(validators=[EqualTo("password")])
    submit = SubmitField()

class LoginForm(_F):
    email = StringField(validators=[DataRequired(), Email()])
    password = PasswordField(validators=[DataRequired()])
    submit = SubmitField()

def verify_recaptcha(tok:str)->bool:
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
        logging.error(f"reCAPTCHA 驗證失敗：{e}")
        return False

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
def register():
    if request.method=="POST":
        u = request.form["username"].strip()
        e = request.form["email"].strip().lower()
        p = request.form["password"]
        if User.query.filter_by(username=u).first():
            flash("使用者名稱已存在","danger")
        elif User.query.filter_by(email=e).first():
            flash("此信箱已註冊過","danger")
        else:
            c = f"{uuid.uuid4().int % 1000000:06d}"
            exp = int(time.time()) + 3600
            db.session.add(User(
                username=u, email=e,
                password=generate_password_hash(p),
                confirm_code=c, confirm_expire=exp
            ))
            db.session.commit()
            link = url_for("confirm", _external=True)
            mail.send(Message(
                "FinWeb 電子郵件驗證",
                recipients=[e],
                body=f"驗證碼：{c}\n請於一小時內前往 {link} 輸入"
            ))
            flash("註冊成功！驗證碼已寄至信箱","success")
            return redirect(url_for("confirm"))
    return render_template("register.html",
        recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
    )

@app.route("/confirm", methods=["GET","POST"])
def confirm():
    if request.method=="POST":
        em = request.form["email"].lower().strip()
        cd = request.form["code"].strip()
        u = User.query.filter_by(email=em).first()
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
            u.confirmed = True
            u.confirm_code = None
            u.confirm_expire = None
            db.session.commit()
            flash("電子郵件驗證成功！請登入。","success")
            return redirect(url_for("login"))
    return render_template("confirm.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        e = request.form["email"].lower().strip()
        p = request.form["password"]
        u = User.query.filter_by(email=e).first()
        if not u or not check_password_hash(u.password, p):
            flash("帳號或密碼錯誤","danger")
        elif not u.confirmed:
            flash("請先完成電子郵件驗證！","warning")
        else:
            login_user(u)
            flash(f"歡迎回來，{u.username}","success")
            return redirect(url_for("index"))
    return render_template("login.html",
        recaptcha_site_key=app.config["RECAPTCHA_SITE_KEY"]
    )

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

@app.route("/api/stock")
def api_stock():
    sym = request.args.get("symbol","AAPL")
    return jsonify({"symbol":sym,"price":0})

@app.route("/api/crypto")
def api_crypto():
    cid = request.args.get("id","bitcoin")
    return jsonify({"id":cid,"price":0})

@app.route("/api/coins")
def api_coins():
    resp = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency":"usd",
            "order":"market_cap_desc",
            "per_page":100,
            "page":1,
            "sparkline":False
        }, timeout=5
    )
    data = [
        {"id":c["id"], "symbol":c["symbol"], "name":c["name"]}
        for c in resp.json()
    ]
    return jsonify(data)

@app.route("/api/crypto_price")
def api_crypto_price():
    cid = request.args.get("id","bitcoin")
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids":cid, "vs_currencies":"usd"}, timeout=5
    )
    price = resp.json().get(cid, {}).get("usd", 0)
    return jsonify({
        "id": cid,
        "price": price,
        "timestamp": int(time.time()*1000)
    })

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
    if not msg:
        return jsonify({"error":"empty"}),400
    return jsonify({"reply":"此環境無法呼叫 Gemini API，僅示範回覆。"})

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    app.run(debug=True, threaded=True, port=5000)
