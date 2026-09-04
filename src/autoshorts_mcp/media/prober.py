# -*- coding: utf-8 -*-
"""Tool 3 §4.3 probe_and_sample_media 的探測部分。

v1.6 實測發現並處理的兩類素材（§4.3）：
  - VFR：螢幕錄影器對靜態畫面降幀，實測回報幀率 7.4 / 22.7 / 48.2 / 115.3 fps。
    CAP_PROP_FPS 在 VFR 上是平均值，定位須用時間而非幀號。
  - 直式來源：手機錄影 1080x2400（長寬比 0.45）比目標 9:16 更長。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from fractions import Fraction

from ..errors import AutoShortsError, ErrorCode

TARGET_AR = 9 / 16          # 0.5625
AR_TOLERANCE = 0.02

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class MediaInfo:
    file_path: str
    source_type: str            # "video" | "image"
    duration: float
    width: int
    height: int
    aspect_ratio: float
    orientation: str            # landscape | portrait_match | portrait_taller
    fps: float
    codec: str
    rotation: int
    is_vfr: bool

    def as_dict(self) -> dict:
        d = asdict(self)
        d["aspect_ratio"] = round(self.aspect_ratio, 4)
        d["duration"] = round(self.duration, 3)
        d["fps"] = round(self.fps, 3)
        return d


def _ffprobe_bin(configured: str = "") -> str:
    p = configured or shutil.which("ffprobe")
    if not p:
        raise AutoShortsError(
            ErrorCode.FFMPEG_NOT_FOUND,
            "找不到 ffprobe 執行檔。",
            remediation="安裝 FFmpeg（winget install --id Gyan.FFmpeg）並重開 shell，"
                        "或在 config.toml 的 [ffmpeg] ffprobe_path 指定絕對路徑。")
    return p


def classify_orientation(width: int, height: int) -> str:
    """§4.3 直式來源分類。"""
    if height <= 0:
        return "landscape"
    ar = width / height
    if ar > TARGET_AR + AR_TOLERANCE:
        return "landscape"
    if ar < TARGET_AR - AR_TOLERANCE:
        return "portrait_taller"      # 比 9:16 更長，需垂直裁切且不放大文字
    return "portrait_match"


def _parse_rate(s: str) -> float:
    try:
        return float(Fraction(s))
    except Exception:
        return 0.0


def probe(path: str, *, ffprobe: str = "") -> MediaInfo:
    """探測單一素材。shell=False（§9 R-03）。"""
    if not os.path.isfile(path):
        raise AutoShortsError(ErrorCode.INPUT_FILE_NOT_FOUND,
                              "檔案不存在: {}".format(path))
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return _probe_image(path, ffprobe)
    return _probe_video(path, ffprobe)


def _run_probe(binp: str, args: list[str]) -> dict:
    p = subprocess.run([binp, "-v", "error", "-of", "json"] + args,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if p.returncode != 0:
        raise AutoShortsError(ErrorCode.FFMPEG_EXEC_FAILED,
                              "ffprobe 失敗: {}".format((p.stderr or "")[-200:]))
    try:
        return json.loads(p.stdout or "{}")
    except Exception as e:
        raise AutoShortsError(ErrorCode.FFMPEG_EXEC_FAILED,
                              "ffprobe 輸出無法解析: {}".format(e)) from e


def _probe_video(path: str, ffprobe: str) -> MediaInfo:
    b = _ffprobe_bin(ffprobe)
    d = _run_probe(b, [
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,codec_name,r_frame_rate,avg_frame_rate,nb_frames:"
        "stream_side_data=rotation:format=duration",
        path,
    ])
    streams = d.get("streams") or []
    if not streams:
        raise AutoShortsError(ErrorCode.FFMPEG_EXEC_FAILED,
                              "找不到視訊串流: {}".format(path))
    st = streams[0]
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    r_rate = _parse_rate(st.get("r_frame_rate") or "0")
    avg_rate = _parse_rate(st.get("avg_frame_rate") or "0")
    duration = float((d.get("format") or {}).get("duration") or 0.0)

    # VFR 判定：r_frame_rate（容器宣告的最大幀率）與 avg_frame_rate 顯著不符
    is_vfr = bool(r_rate and avg_rate and abs(r_rate - avg_rate) / max(r_rate, 1e-9) > 0.05)

    rotation = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(round(float(sd["rotation"]))) % 360
            except Exception:
                pass

    # 旋轉 90/270 時，有效寬高互換（T-05）
    if rotation in (90, 270):
        w, h = h, w

    return MediaInfo(
        file_path=os.path.abspath(path).replace("\\", "/"),
        source_type="video",
        duration=duration,
        width=w, height=h,
        aspect_ratio=(w / h) if h else 0.0,
        orientation=classify_orientation(w, h),
        fps=avg_rate or r_rate,
        codec=st.get("codec_name") or "",
        rotation=rotation,
        is_vfr=is_vfr,
    )


def _probe_image(path: str, ffprobe: str) -> MediaInfo:
    b = _ffprobe_bin(ffprobe)
    d = _run_probe(b, ["-select_streams", "v:0",
                       "-show_entries", "stream=width,height,codec_name", path])
    st = (d.get("streams") or [{}])[0]
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    return MediaInfo(
        file_path=os.path.abspath(path).replace("\\", "/"),
        source_type="image", duration=0.0,
        width=w, height=h,
        aspect_ratio=(w / h) if h else 0.0,
        orientation=classify_orientation(w, h),
        fps=0.0, codec=st.get("codec_name") or "",
        rotation=0, is_vfr=False,
    )


def probe_directory(directory: str, *, ffprobe: str = "") -> tuple[list[MediaInfo], list[dict]]:
    """探測目錄內所有素材。無法探測者列入 skipped，不整批中止（§4.3）。"""
    if not os.path.isdir(directory):
        raise AutoShortsError(ErrorCode.INPUT_DIR_NOT_FOUND,
                              "素材目錄不存在: {}".format(directory))
    items, skipped = [], []
    for name in sorted(os.listdir(directory)):
        p = os.path.join(directory, name)
        if not os.path.isfile(p):
            continue
        if os.path.splitext(name)[1].lower() not in (VIDEO_EXT | IMAGE_EXT):
            continue
        try:
            items.append(probe(p, ffprobe=ffprobe))
        except AutoShortsError as e:
            skipped.append({"file_path": p, "reason": e.error.code.value})
        except Exception as e:
            skipped.append({"file_path": p, "reason": type(e).__name__})
    if not items:
        raise AutoShortsError(ErrorCode.INPUT_NO_MEDIA,
                              "目錄中沒有可用素材: {}".format(directory),
                              remediation="放入 .mp4/.mov/.jpg 等支援格式的檔案",
                              skipped=skipped)
    return items, skipped
