import os, joblib, math
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

def _feat_10_logret(closes: list[float]) -> np.ndarray:
    if len(closes) < 11:
        raise ValueError("需要至少 11 筆收盤價才能組 10 維特徵")
    c = np.asarray(closes[-11:], dtype=float)
    log_r = np.log(c[1:] / c[:-1])
    return log_r.reshape(1, -1)

def _feat_2_return(closes: list[float]) -> np.ndarray:
    if len(closes) < 6:
        raise ValueError("需要至少 6 筆收盤價才能組 2 維特徵")
    s  = pd.Series(closes, dtype=float)
    r1 = (s.iloc[-1] / s.iloc[-2]) - 1
    r5 = (s.iloc[-1] / s.iloc[-6]) - 1
    return np.array([[r1, r5]], dtype=float)

def predict_action(model_path: str, closes: list[float]) -> tuple[str, float]:
    if not model_path or not os.path.exists(model_path):
        return "Hold", 0.0

    clf: RandomForestClassifier = joblib.load(model_path)
    n_feat = getattr(clf, "n_features_in_", None)

    if n_feat == 10:
        X = _feat_10_logret(closes)
    elif n_feat == 2:
        X = _feat_2_return(closes)
    else:
        raise ValueError(f"未知的特徵維度：{n_feat}")

    proba = clf.predict_proba(X)[0]
    label = int(clf.classes_[np.argmax(proba)])
    action_map = {1: "Buy", 0: "Hold", -1: "Sell"}
    return action_map.get(label, "Hold"), float(np.max(proba))
