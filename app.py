from __future__ import annotations

import os
import configparser
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request,
    redirect, url_for, jsonify, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, UserMixin
)
from werkzeug.security import generate_password_hash, check_password_hash

CFG = configparser.ConfigParser()
CFG.read("config.ini", encoding="utf-8")

DB_REL = CFG["DEFAULT"].get("DB_PATH", "instance/finance.db")
DB_PATH = (Path(__file__).parent / DB_REL).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH.as_posix()}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_mgr = LoginManager(app)
login_mgr.login_view = "login"

#SQLite有關
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()


@login_mgr.user_loader
def load_user(user_id: str):  # noqa: D401
    return User.query.get(int(user_id))

SYSTEM_PROMPT = """
You are FinWeb AI Financial Assistant, a professional financial analyst and market strategist.
• 你精通：股票（技術＆基本面）、加密貨幣、市場趨勢、宏觀經濟指標與公司財報分析。
• 回答時請：
  – 條理分明，常用「段落小標題」或「•••」要點列出重點
  – 必要時給出數據例子、定義、參考範圍與時間點
  – 若對使用者提問不在金融範疇，簡短回覆「抱歉，我只專注金融相關問題」。
• 請用繁體中文回覆，保留專有名詞英語原文。
""".strip()

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


#這邊跟user系統有關，盡量不要動到 網頁會開不起來
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form["username"]).first()
        if user and check_password_hash(user.password, request.form["password"]):
            login_user(user)
            return redirect(url_for("index"))
        flash("帳號或密碼錯誤")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        uname = request.form["username"]
        if User.query.filter_by(username=uname).first():
            flash("使用者名稱已存在")
        else:
            db.session.add(
                User(
                    username=uname,
                    email=request.form["email"],
                    password=generate_password_hash(request.form["password"]),
                )
            )
            db.session.commit()
            flash("註冊成功，請登入")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


#這邊是串API的
from crawler.finance import get_stock, get_crypto


@app.route("/api/stock")
def api_stock():
    symbol = request.args.get("symbol", "AAPL")
    return jsonify(get_stock(symbol))


@app.route("/api/crypto")
def api_crypto():
    coin_id = request.args.get("id", "bitcoin")
    return jsonify(get_crypto(coin_id))


#這邊跟Gemini有關
@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    user_msg: str = data.get("message", "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    try:
        import google.generativeai as genai
        from google.generativeai.types import GenerationConfig

        genai.configure(api_key=CFG["Gemini"]["API_KEY"])

        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"{SYSTEM_PROMPT}\n\n使用者：{user_msg}\n助理："

        gen_cfg = GenerationConfig(
            temperature=0.2,
            max_output_tokens=512,
            candidate_count=1,
        )
        resp = model.generate_content(prompt, generation_config=gen_cfg)
        reply = getattr(resp, "text", None) or resp.data.get("text", "")

        return jsonify({"reply": reply})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
