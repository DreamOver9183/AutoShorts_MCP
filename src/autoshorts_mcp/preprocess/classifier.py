# -*- coding: utf-8 -*-
"""§5.6.3 素材分類（像素路徑）。

v1.6 根因修正（§5.6.3.9）：cv2.MSER_create() 的 min_area 預設為 60，
在 1280 代理解析度下 IDE 程式碼文字面積約 45px²，低於門檻而完全不被回傳。
此單一參數曾使 v1.2–v1.5 的四輪實測全部得出錯誤結論。

實測表現（3 支 screen vs 20 支 natural，B 級證據）：
    未過濾          AUC 0.983  門檻 9.94  召回 1.00  特異度 0.95
    共線過濾 + 去重   AUC 1.000  門檻 2.96  召回 1.00  特異度 1.00
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import numpy as np

MSER_MIN_AREA = 15          # §5.6.3.9 必須顯式設定
DENSITY_UNIT = 1e5          # 每 10 萬像素


@dataclass
class ClassifyResult:
    content_class: str          # natural | screen_recording | slideshow | mixed | unknown
    confidence: float
    text_density: float
    text_density_raw: float
    phi: float | None
    signals: dict = field(default_factory=dict)
    aggregated_over_frames: int = 0
    method: str = "pixel"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["text_density"] = round(self.text_density, 3)
        d["text_density_raw"] = round(self.text_density_raw, 3)
        if self.phi is not None:
            d["phi"] = round(self.phi, 4)
        return d


def _mser(min_area: int):
    import cv2
    return cv2.MSER_create(min_area=min_area)


def _dedupe_nested(boxes: list[tuple], iou_thresh: float = 0.5) -> list[tuple]:
    """移除 MSER 的巢狀／重疊區域。

    MSER 對同一個字元會回傳多個嵌套的穩定區域。若不去重，§5.6.3.5 的
    共線分組會把同一字元的多個嵌套框視為不同元件。去重後真實語料的判別力
    由 AUC 0.983 提升至 1.000（natural 最大值由 3.85 降至 0.16）。
    """
    if not boxes:
        return []
    bs = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple] = []
    for x, y, w, h in bs:
        a1 = w * h
        dup = False
        for kx, ky, kw, kh in kept:
            ix = max(0, min(x + w, kx + kw) - max(x, kx))
            iy = max(0, min(y + h, ky + kh) - max(y, ky))
            inter = ix * iy
            if inter and inter / min(a1, kw * kh) > iou_thresh:
                dup = True
                break
        if not dup:
            kept.append((x, y, w, h))
    return kept


def components(gray: np.ndarray, *, min_area: int = MSER_MIN_AREA,
               max_h_frac: float = 0.10, min_h: int = 6,
               dedupe: bool = True) -> list[tuple]:
    """回傳通過文字狀篩選的 MSER 元件 (x, y, w, h)。"""
    import cv2
    try:
        regions, _ = _mser(min_area).detectRegions(gray)
    except Exception:
        return []
    H, W = gray.shape
    out = []
    for r in regions:
        x, y, w, h = cv2.boundingRect(np.asarray(r).reshape(-1, 1, 2))
        if min_h <= h <= H * max_h_frac and 0.15 <= w / max(h, 1) <= 12:
            out.append((int(x), int(y), int(w), int(h)))
    return _dedupe_nested(out) if dedupe else out


def collinear_lines(comps: list[tuple], *, min_group: int = 3,
                    height_cv: float = 0.35) -> list[list[tuple]]:
    """§5.6.3.5 共線群組：文字排成列、基線對齊、高度一致。

    這是軟判別器而非硬分類器——兩類都被壓低，但 screen 被壓得少。
    去重後的實測（3 支 screen vs 20 支 natural）：
        screen 2.18–4.54 / game 0.00 / natural 中位 0.00、最大 0.16

    v0.2 移除間距檢查：原本以「字高」為尺度限制列內水平間距，但該尺度
    隱含假設 MSER 回傳字元級元件。實測 MSER 在乾淨抗鋸齒文字上會回傳
    詞級區域，此時間距相對字高過大而使整列被誤拒。三種變體（字高尺度／
    寬度尺度／完全移除）在真實語料上 AUC 均為 1.000——間距檢查不貢獻
    判別力，故移除以消除對 MSER 粒度的依賴。
    """
    if len(comps) < min_group:
        return []
    cs = sorted(comps, key=lambda c: c[1] + c[3] / 2)
    med_h = float(np.median([c[3] for c in cs]))
    lines, cur = [], [cs[0]]
    for c in cs[1:]:
        if abs((c[1] + c[3] / 2) - (cur[-1][1] + cur[-1][3] / 2)) <= 0.5 * med_h:
            cur.append(c)
        else:
            lines.append(cur); cur = [c]
    lines.append(cur)

    kept = []
    for ln in lines:
        if len(ln) < min_group:
            continue
        hs = np.array([c[3] for c in ln], dtype=float)
        if hs.std() / max(hs.mean(), 1e-6) > height_cv:
            continue
        kept.append(sorted(ln, key=lambda c: c[0]))
    return kept


def text_density(gray: np.ndarray, *, min_area: int = MSER_MIN_AREA,
                 collinear: bool = True, **kw) -> tuple[float, float, float | None]:
    """回傳 (過濾後密度, 原始密度, phi)。phi = 共線列的中位字高 / 畫面高。"""
    H, W = gray.shape
    unit = (W * H) / DENSITY_UNIT
    comps = components(gray, min_area=min_area)
    raw = len(comps) / unit
    if not collinear:
        heights = [c[3] for c in comps]
        return raw, raw, (float(np.median(heights)) / H if heights else None)
    lines = collinear_lines(comps, **kw)
    n = sum(len(l) for l in lines)
    heights = [c[3] for l in lines for c in l]
    return n / unit, raw, (float(np.median(heights)) / H if heights else None)


def classify(frames_gray: list[np.ndarray], *,
             threshold: float = 2.96,
             min_area: int = MSER_MIN_AREA,
             collinear: bool = True,
             min_confidence: float = 0.70) -> ClassifyResult:
    """§5.6.3.2 素材層級聚合：對 k 幀取中位數。

    畫格層級的分類結果不可信，一律禁止採用。素材層級（k=8）實測 AUC 1.000
    （screen 2.18–4.54 / natural 中位 0.00、最大 0.16）。取中位數而非平均，
    以抵抗混合型素材的離群畫格。
    """
    if not frames_gray:
        return ClassifyResult("unknown", 0.0, 0.0, 0.0, None, {}, 0)

    if threshold is None or threshold < 0:
        # §5.6.3.5 未校準時拒絕自動分類，不以猜測值靜默運行
        return ClassifyResult("unknown", 0.0, 0.0, 0.0, None,
                              {"reason": "threshold_not_calibrated"}, len(frames_gray))

    per = [text_density(g, min_area=min_area, collinear=collinear) for g in frames_gray]
    td = float(np.median([p[0] for p in per]))
    raw = float(np.median([p[1] for p in per]))
    phis = [p[2] for p in per if p[2]]
    phi = float(np.median(phis)) if phis else None

    # 距離門檻越遠信心越高；以門檻本身為尺度
    margin = abs(td - threshold) / max(threshold, 1e-6)
    confidence = float(min(1.0, 0.5 + margin / 2))
    cls = "screen_recording" if td > threshold else "natural"
    if confidence < min_confidence:
        cls = "unknown"

    return ClassifyResult(
        content_class=cls,
        confidence=round(confidence, 3),
        text_density=td,
        text_density_raw=raw,
        phi=phi,
        signals={"threshold": threshold, "collinear_filter": collinear,
                 "mser_min_area": min_area},
        aggregated_over_frames=len(frames_gray),
    )
