import logging
import traceback
import random
import configparser
import os

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import numpy as np
import cv2
import google.generativeai as genai

config = configparser.ConfigParser()
cfg_path = os.path.join(os.path.dirname(__file__), 'config.ini')
config.read(cfg_path, encoding='utf-8-sig')  

gemini_key = config.get('Gemini', 'API_KEY', fallback=None)
if not gemini_key:
    raise RuntimeError("請在 config.ini [Gemini] 區段設定 API_KEY")

genai.configure(api_key=gemini_key)

logger = logging.getLogger(__name__)

cv_bp = Blueprint("cv_pattern", __name__, template_folder="templates")

@cv_bp.route("/cv-pattern")
@login_required
def cv_pattern():
    return render_template("cv_pattern.html")


@cv_bp.route("/api/cv_detect", methods=["POST"])
@login_required
def api_cv_detect():
    file = request.files.get("file")
    if not file:
        return jsonify([])

    try:
        data = file.read()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("無法解碼圖像檔案")
        h, w = img.shape[:2]

        dets = []
        seg_w = w / 9
        for i in range(8):
            x1 = (seg_w * (i + 1) - seg_w / 2) / w
            y1 = 0.2
            x2 = (seg_w * (i + 1) + seg_w / 2) / w
            y2 = 0.6
            conf = round(random.uniform(0.6, 0.9), 3)
            dets.append({
                "label": f"Pattern{i+1}",
                "conf":  conf,
                "bbox":  [x1, y1, x2, y2]
            })

        return jsonify(dets)

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[cv_detect] {e}\n{tb}")
        return jsonify({"error": str(e)}), 500


@cv_bp.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    data = request.get_json() or {}
    prompt = data.get("message", "").strip()
    if not prompt:
        return jsonify({"reply": "請提供要詢問的內容"}), 400

    try:
        resp = genai.chat.create(
            model="chat-bison-001",
            messages=[{"author": "user", "content": prompt}],
            temperature=0.7,
            max_output_tokens=100
        )
        reply = resp.choices[0].message.content.strip()
        return jsonify({"reply": reply})

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[api_chat] {e}\n{tb}")
        return jsonify({"reply": "AI 服務暫時無法使用，請稍後再試"}), 500
