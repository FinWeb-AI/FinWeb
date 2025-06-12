import logging, traceback, random, configparser, os
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import numpy as np, cv2
import google.generativeai as genai
cfg = configparser.ConfigParser()
cfg.read(os.path.join(os.path.dirname(__file__), 'config.ini'), encoding='utf-8-sig')
gemini_key = cfg.get('Gemini', 'API_KEY', fallback=None)
if not gemini_key:
    raise RuntimeError('請在 config.ini [Gemini] 區段設定 API_KEY')
genai.configure(api_key=gemini_key)

logger = logging.getLogger(__name__)
cv_bp = Blueprint('cv_pattern', __name__, template_folder='templates')

@cv_bp.route('/cv-pattern')
@login_required
def cv_pattern():
    return render_template('cv_pattern.html')

@cv_bp.route('/api/cv_detect', methods=['POST'])
@login_required
def api_cv_detect():
    file = request.files.get('file')
    if not file:
        return jsonify([])

    try:
        img = cv2.imdecode(np.frombuffer(file.read(), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError('無法解碼圖像檔案')
        h, w = img.shape[:2]

        dets = []
        seg_w = w / 9
        for i in range(8):
            x1 = (seg_w*(i+1) - seg_w/2) / w
            y1 = 0.2
            x2 = (seg_w*(i+1) + seg_w/2) / w
            y2 = 0.6
            dets.append({
                'label': f'Pattern{i+1}',
                'conf' : round(random.uniform(0.6,0.9),3),
                'bbox' : [x1,y1,x2,y2]
            })
        return jsonify(dets)

    except Exception as e:
        logger.error('[cv_detect] %s\n%s', e, traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@cv_bp.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    """
    支援兩種格式：
      1. {"dets":[{"label":"Hammer","conf":0.82}, ...]}  ← ★ 新
      2. {"labels":[...]} 或 {"message": "..."}          ← 舊
    """
    data = request.get_json() or {}

    dets = data.get('dets')
    if dets:
        parts = [f"{d['label']} {int(d['conf']*100)}%" for d in dets if 'label' in d and 'conf' in d]
        prompt = "蠟燭型態偵測結果：" + "、".join(parts) + "。請給台灣散戶50字內交易建議。"
    else:
        labels = data.get('labels', [])
        prompt = data.get('message', '').strip()
        if labels:
            prompt = "以下蠟燭型態：" + "、".join(labels) + "，請給台灣散戶簡短交易建議(50字內)。"

    if not prompt:
        return jsonify({'reply': '請提供要詢問的內容'}), 400

    try:
        model   = genai.GenerativeModel('gemini-1.5-flash-latest')
        gen_cfg = genai.types.GenerationConfig(
            temperature=0.7, max_output_tokens=100, top_p=0.9, top_k=50)
        resp  = model.generate_content(prompt, generation_config=gen_cfg)
        reply = (resp.text or '').strip() or 'AI 無回應，請稍後再試'
        return jsonify({'reply': reply})

    except Exception as e:
        logger.error('[api_chat] %s', e, exc_info=True)
        return jsonify({'reply': 'AI 服務暫時無法使用，請稍後再試'}), 500
