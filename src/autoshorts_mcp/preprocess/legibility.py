# -*- coding: utf-8 -*-
"""§5.6.2 可讀性預算模型。

核心恆等式（與原始解析度無關）：
    滿寬 letterbox : h_out = phi * W_pane * (H_src / W_src)
    裁切至窗格同比例: h_out = phi * H_pane / z

v1.6 實測 phi（原生解析度、MSER min_area=15）：
    PowerShell 0.0172 / IDE 0.0108 / 手機社群 0.0054
v1.5 曾假設 IDE phi=0.0176，實測顯示樂觀 63%。
"""
from __future__ import annotations

from dataclasses import dataclass

OUT_W, OUT_H = 1080, 1920
H_MIN = 28        # 勉強可讀
H_COMFY = 40      # 舒適

# v1.6 實測值（§5.6.2）
PHI_MEASURED = {
    "powershell": 0.0172,
    "ide_dark": 0.0108,
    "mobile_social": 0.0054,
}


@dataclass
class LegibilityVerdict:
    text_height_px: float
    verdict: str                # "ok" | "marginal" | "fail"
    visible_area: float         # 可見原始畫面面積比例
    crop_w: int = 0
    crop_h: int = 0


def verdict_of(h: float, h_min: int = H_MIN, h_comfy: int = H_COMFY) -> str:
    if h >= h_comfy:
        return "ok"
    return "marginal" if h >= h_min else "fail"


def letterbox_text_height(phi: float, src_w: int, src_h: int,
                          pane_w: int = OUT_W, pane_h: int = OUT_H) -> float:
    """整幀縮放置入窗格（不裁切）後的輸出字高。"""
    if src_w <= 0 or src_h <= 0:
        return 0.0
    s = min(pane_w / src_w, pane_h / src_h)
    return phi * src_h * s


def crop_text_height(phi: float, zoom_h: float, pane_h: int = OUT_H) -> float:
    """裁切至與窗格同比例後填滿的輸出字高：h_out = phi * H_pane / z。"""
    if zoom_h <= 0:
        return 0.0
    return phi * pane_h / zoom_h


def required_zoom(phi: float, target_px: float, pane_h: int = OUT_H) -> float:
    """達到 target_px 所需的裁切高度佔比 z（越小代表裁得越兇）。"""
    if target_px <= 0:
        return 1.0
    return phi * pane_h / target_px


def evaluate(phi: float, src_w: int, src_h: int,
             *, target_px: float = H_MIN,
             pane_w: int = OUT_W, pane_h: int = OUT_H) -> LegibilityVerdict:
    """計算達到 target_px 所需的裁切，以及可見面積。

    來源比目標更長（portrait_taller）時，裁切受寬度限制——此時縮放比為 1.0，
    文字完全不會放大（R-16）。
    """
    if src_w <= 0 or src_h <= 0:
        return LegibilityVerdict(0.0, "fail", 0.0)

    z = min(1.0, required_zoom(phi, target_px, pane_h))
    crop_h = src_h * z
    crop_w = crop_h * (pane_w / pane_h)

    if crop_w > src_w:                       # 來源不夠寬（直式素材）
        crop_w = src_w
        crop_h = src_w * (pane_h / pane_w)
        crop_h = min(crop_h, src_h)
        h_out = phi * src_h * (pane_w / crop_w)
    else:
        h_out = crop_text_height(phi, crop_h / src_h, pane_h)

    area = (crop_w / src_w) * (crop_h / src_h)
    return LegibilityVerdict(
        text_height_px=round(h_out, 1),
        verdict=verdict_of(h_out),
        visible_area=round(area, 4),
        crop_w=int(crop_w) // 2 * 2,
        crop_h=int(crop_h) // 2 * 2,
    )


def is_hopeless(phi: float, src_w: int, src_h: int,
                *, min_visible_area: float = 0.05) -> bool:
    """§5.6.2.1 / R-16：即使裁到 min_visible_area 仍達不到最低門檻。

    此時應回傳 SOURCE_TEXT_TOO_SMALL 並建議來源端調整（提高錄製 UI 縮放比），
    不得靜默產出看不清楚的成品。
    """
    v = evaluate(phi, src_w, src_h, target_px=H_MIN)
    return v.verdict == "fail" or v.visible_area < min_visible_area
