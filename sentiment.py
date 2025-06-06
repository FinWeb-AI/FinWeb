from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
import random, datetime

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
except Exception:
    _vader = None

sent_bp = Blueprint("sentiment", __name__)
# 這邊是情緒分析的路由
@sent_bp.route("/sentiment")
@login_required
def sentiment():
    return render_template("sentiment.html")
# 這邊是情緒分析的 API 路由
@sent_bp.route("/api/sentiment_feed")
def api_sentiment_feed():
    q = request.args.get("q", "bitcoin")
    demo_posts = [
        f"{q} to the moon! ",
        f"I think {q} will dump hard…",
        f"Is {q} still a good buy?",
        f"HODL {q}, stay strong!",
        f"{q} is the future of finance.",
    ]
    rows = []
    for txt in demo_posts:
        if _vader:
            s = _vader.polarity_scores(txt)
            rows.append({
                "text": txt,
                "positive": s["pos"],
                "neutral":  s["neu"],
                "negative": s["neg"],
                "ts": int(datetime.datetime.utcnow().timestamp()*1000)
            })
        else:
            pos = random.random()
            neg = random.random()*(1-pos)
            neu = 1-pos-neg
            rows.append({"text": txt,"positive":pos,"neutral":neu,"negative":neg})
    return jsonify(rows)
