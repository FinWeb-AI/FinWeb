import sys
import datetime
import collections
import importlib
import configparser
import feedparser
import requests
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from textblob import TextBlob

cfg = configparser.ConfigParser()
with open("config.ini", encoding="utf-8") as f:
    cfg.read_file(f)

sent_bp = Blueprint("sentiment", __name__, template_folder="templates")
vader    = SentimentIntensityAnalyzer()

HAS_SNOW = False
try:
    SnowNLP = importlib.import_module("snownlp").SnowNLP
    HAS_SNOW = True
except ModuleNotFoundError:
    pass

def _composite_polarity(text: str) -> float:
    scores = [
        vader.polarity_scores(text)["compound"],
        TextBlob(text).sentiment.polarity
    ]
    if HAS_SNOW:
        scores.append(SnowNLP(text).sentiments * 2 - 1)
    avg = sum(scores) / len(scores)
    return 0.0 if abs(avg) < 0.05 else max(min(avg, 1), -1)

@sent_bp.route("/sentiment")
@login_required
def sentiment():
    return render_template("sentiment.html")

@sent_bp.route("/api/sentiment_feed")
@login_required
def api_sentiment_feed():
    q  = request.args.get("q", "bitcoin").strip()
    tr = request.args.get("timerange", "12h")

    now   = datetime.datetime.utcnow()
    delta = {
        "12h": datetime.timedelta(hours=12),
        "1d":  datetime.timedelta(days=1),
        "3d":  datetime.timedelta(days=3),
        "7d":  datetime.timedelta(days=7),
        "1w":  datetime.timedelta(weeks=1),
    }.get(tr, datetime.timedelta(hours=12))
    since = now - delta
    since_date = since.strftime("%Y-%m-%d")

    items = []

    rss = feedparser.parse(
        "https://news.google.com/rss/search?"
        f"q={q}+股票+when:{since_date}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    for e in rss.entries[:20]:
        title, summary = e.get("title",""), e.get("summary","")
        text_all = f"{title} {summary}"
        pol      = _composite_polarity(text_all)
        pos, neg = max(pol,0), max(-pol,0)
        neu      = max(0, 1-pos-neg)
        items.append({
            "text":      title,
            "link":      e.get("link","#"),
            "published": e.get("published","")[:16],
            "positive":  round(pos,3),
            "neutral":   round(neu,3),
            "negative":  round(neg,3),
            "ts":        int(now.timestamp()*1000)
        })

    after_ts = int(since.timestamp())
    try:
        r = requests.get(
            f"https://api.pushshift.io/reddit/search/submission/"
            f"?q={q}&after={after_ts}&size=30", timeout=5
        )
        for d in r.json().get("data", []):
            title, selft = d.get("title",""), d.get("selftext","")
            text_all     = f"{title} {selft}"
            pol          = _composite_polarity(text_all)
            pos, neg     = max(pol,0), max(-pol,0)
            neu          = max(0, 1-pos-neg)
            items.append({
                "text":      title or selft[:30],
                "link":      d.get("full_link") or f"https://reddit.com/{d.get('id')}",
                "published": datetime.datetime.utcfromtimestamp(
                                 d.get("created_utc",0)
                             ).strftime("%Y-%m-%d %H:%M"),
                "positive":  round(pos,3),
                "neutral":   round(neu,3),
                "negative":  round(neg,3),
                "ts":        int(now.timestamp()*1000)
            })
    except:
        pass

    items.sort(key=lambda x: x["ts"], reverse=True)

    all_text = "\n".join(it["text"] for it in items)

    tokens = []
    try:
        import jieba.analyse
        tags = jieba.analyse.extract_tags(
            all_text,
            topK=20,
            withWeight=True,
            allowPOS=("n","nr","ns","nt","nz")
        )

        merged = []
        for tag, weight in tags:
            found = False
            for i,(h,w) in enumerate(merged):
                if tag in h:
                    merged[i][1] += weight
                    found = True
                    break
                if h in tag:
                    merged[i][0] = tag
                    merged[i][1] += weight
                    found = True
                    break
            if not found:
                merged.append([tag, weight])
        tokens = [[tok, round(w*3,2)] for tok,w in merged]
    except Exception:
        import re
        tok_pat   = re.compile(r"[^\w\u4e00-\u9fa5]+")
        MEDIA_STOP = {
            "新聞","日報","報","媒體","News","news","TV",
            "風傳媒","經濟日報","自由時報","蘋果日報","聯合報","TechNews","東森"
        }
        BASIC_STOP = set("的 了 和 是 與 及 在 於 被 人 也 都 更 還 以 不 會".split())
        freq = collections.Counter()
        for it in items:
            txt = tok_pat.sub(" ", it["text"])
            for w in txt.split():
                w = w.strip()
                if not (2 <= len(w) <= 8):            continue
                if w in BASIC_STOP or w in MEDIA_STOP: continue
                if any(m in w for m in MEDIA_STOP):    continue
                weight = max(it["positive"], it["negative"]) + 0.2
                freq[w] += weight
        tokens = [[k, round(v,2)] for k,v in freq.items()]

    print("DEBUG key-tokens:", tokens, file=sys.stdout)

    return jsonify({"posts": items, "tokens": tokens})
