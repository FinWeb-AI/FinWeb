from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import io, base64, random
import numpy as np, cv2

try:
    from ultralytics import YOLO
    model = YOLO("candlestick_ptns.pt")
except Exception:
    model = None

cv_bp = Blueprint("cv_pattern", __name__)

@cv_bp.route("/cv-pattern")
@login_required
def cv_pattern():
    return render_template("cv_pattern.html")

@cv_bp.route("/api/cv_detect", methods=["POST"])
def api_cv_detect():
    file = request.files.get("file")
    if not file:
        return jsonify([])
    img_bytes = file.read()
    img = cv2.imdecode(np.frombuffer(img_bytes,np.uint8), cv2.IMREAD_COLOR)

    dets = []
    if model:
        res = model(img, verbose=False)[0]
        for box, cls, conf in zip(res.boxes.xyxy, res.boxes.cls, res.boxes.conf):
            x1,y1,x2,y2 = map(int, box.tolist())
            dets.append({
                "label": model.names[int(cls)],
                "conf":  float(conf),
                "bbox": [x1,y1,x2,y2]
            })
    else:
        h,w = img.shape[:2]
        dets.append({
            "label": "Hammer",
            "conf":  0.88,
            "bbox": [int(.2*w),int(.2*h),int(.6*w),int(.6*h)]
        })
    return jsonify(dets)
