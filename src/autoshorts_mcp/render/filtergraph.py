# -*- coding: utf-8 -*-
"""§5.3 / §5.6.8 濾鏡鏈組裝。

Spike 1 已在 FFmpeg 9.0.1 上實測全部通過：
  - blur_padding 輸出 1080x1920 / yuv420p / 30fps
  - BLUR_FAST 相對 BLUR_FULL 加速 1.31 倍、SSIM 0.974、記憶體無差異
"""
from __future__ import annotations

OUT_W, OUT_H = 1080, 1920


def _even(n: int) -> int:
    return int(n) // 2 * 2


def blur_padding(out_w: int = OUT_W, out_h: int = OUT_H,
                 *, fast: bool = True) -> str:
    """§5.3.1 模糊填充。

    fast=True 採「先縮小 → 模糊 → 放大」（實測 1.31 倍加速、SSIM 0.974）。
    注意：畫質僅以合成素材驗證，依 §5.6.3.6 尚須以真實攝影素材複驗。
    """
    if fast:
        bw, bh = _even(out_w // 8), _even(out_h // 8)
        bg = ("[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
              "crop={bw}:{bh},boxblur=luma_radius=4:luma_power=2,"
              "scale={w}:{h}[blurred];").format(bw=bw, bh=bh, w=out_w, h=out_h)
    else:
        bg = ("[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
              "crop={w}:{h},boxblur=luma_radius=25:luma_power=3[blurred];"
              ).format(w=out_w, h=out_h)
    return (
        "[0:v]split=2[fg][bg];"
        + bg +
        "[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[foreground];"
        "[blurred][foreground]overlay=(W-w)/2:(H-h)/2[outv]"
    ).format(w=out_w, h=out_h)


def center_crop(out_w: int = OUT_W, out_h: int = OUT_H) -> str:
    """§5.3.2 中央裁切。"""
    return ("[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            "crop={w}:{h}[outv]").format(w=out_w, h=out_h)


def ken_burns(total_frames: int, out_w: int = OUT_W, out_h: int = OUT_H,
              fps: int = 30, zoom_max: float = 1.15) -> str:
    """§5.3.3 緩慢推近。

    zoompan 的 d 參數單位為「幀數」而非秒數，須由 duration x fps 換算。
    先放大至 2 倍解析度再 zoompan，避免推近時產生鋸齒。
    """
    step = max((zoom_max - 1.0) / max(total_frames, 1), 1e-6)
    return ("[0:v]scale={w2}:{h2}:force_original_aspect_ratio=increase,"
            "crop={w2}:{h2},"
            "zoompan=z='min(zoom+{step:.6f},{zmax})':d={d}:s={w}x{h}:fps={fps}[outv]"
            ).format(w2=out_w * 2, h2=out_h * 2, step=step, zmax=zoom_max,
                     d=int(total_frames), w=out_w, h=out_h, fps=fps)


def static_crop(crop_x: int, crop_y: int, crop_w: int, crop_h: int,
                out_w: int = OUT_W, out_h: int = OUT_H,
                *, border: tuple[int, int, int, int] | None = None) -> str:
    """§5.6.8 分段靜態裁切（fit_height_crop / region_pan）。

    border : (x, y, w, h) 靜態黑邊剝除，須先於內容裁切（§5.6.3.3）。
    尾端的 scale+crop 僅用於吸收奇偶數捨入誤差。
    """
    chain = "[0:v]"
    if border:
        bx, by, bw, bh = border
        chain += "crop={}:{}:{}:{},".format(_even(bw), _even(bh), _even(bx), _even(by))
    chain += "crop={}:{}:{}:{},".format(
        _even(crop_w), _even(crop_h), _even(crop_x), _even(crop_y))
    chain += ("scale={w}:{h}:force_original_aspect_ratio=increase,"
              "crop={w}:{h}[outv]").format(w=out_w, h=out_h)
    return chain


def portrait_taller(out_w: int = OUT_W, out_h: int = OUT_H) -> str:
    """§4.3 直式來源（長寬比 < 9:16，比目標更長）。

    注意：此路徑的縮放比為 1.0（寬度已相符），文字不會放大。
    實測手機素材 13px 進、13px 出——可讀性完全取決於來源（R-16）。
    """
    return ("[0:v]scale={w}:-2,crop={w}:{h}[outv]").format(w=out_w, h=out_h)


def build(mode: str, *, out_w: int = OUT_W, out_h: int = OUT_H,
          fps: int = 30, crop: dict | None = None,
          border: tuple | None = None, total_frames: int = 0,
          fast_blur: bool = True) -> str:
    """依 reframe_mode 組裝濾鏡鏈。"""
    if mode in ("fit_height_crop", "region_pan"):
        if not crop:
            raise ValueError("{} 需要 crop 參數".format(mode))
        return static_crop(crop["x"], crop["y"], crop["w"], crop["h"],
                           out_w, out_h, border=border)
    if mode == "center_crop":
        return center_crop(out_w, out_h)
    if mode == "ken_burns_zoom":
        return ken_burns(total_frames or fps, out_w, out_h, fps)
    if mode == "portrait_taller":
        return portrait_taller(out_w, out_h)
    return blur_padding(out_w, out_h, fast=fast_blur)
