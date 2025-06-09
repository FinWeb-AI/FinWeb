from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import feedparser, re

mv_bp = Blueprint("metaverse", __name__, template_folder="templates")

@mv_bp.route("/metaverse")
@login_required
def metaverse():
    return render_template("metaverse.html")

@mv_bp.route("/api/metaverse_feed")
@login_required
def api_metaverse_feed():
    sym = request.args.get("symbol", "").strip()
    if not sym:
        return jsonify({"error": "請提供標的"}), 400


    url = (
        "https://news.google.com/rss/search?"
        f"q={sym}+股票&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    feed = feedparser.parse(url)

    items = []
    for e in feed.entries:
        img = ""
        if "media_content" in e:
            img = e.media_content[0].get("url", "")
        elif "media_thumbnail" in e:
            img = e.media_thumbnail[0].get("url", "")

        if not img and "summary" in e:
            m = re.search(r'<img[^>]+src="([^">]+)"', e.summary)
            if m: img = m.group(1)

        items.append({
            "title"    : e.get("title", "無標題"),
            "link"     : e.get("link",  "#"),
            "published": e.get("published", e.get("updated",""))[:16],
            "img"      : img
        })

    if not items:
        return jsonify({"error": "查無新聞"}), 200
    return jsonify(items)
