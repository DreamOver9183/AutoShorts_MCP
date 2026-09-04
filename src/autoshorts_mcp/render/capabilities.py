# -*- coding: utf-8 -*-
"""Tool 6 §4.6 probe_system_capabilities。

§3.2.2：僅列於 -encoders 不代表驅動可用，必須以 lavfi 合成源實際編碼 1 秒。
Spike 1 實測驗證此設計必要——h264_amf 有編入 build 但實測失敗。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict, field

from ..errors import AutoShortsError, ErrorCode

# §3.2.2 編碼器優先序
ENCODER_PRIORITY = ["h264_nvenc", "h264_qsv", "h264_amf",
                    "h264_videotoolbox", "h264_vaapi", "libx264"]

# §3.2.4 各家品質參數語意不同，不可共用數值
QUALITY_PARAMS = {
    "libx264":           ["-preset", "medium", "-crf", "18"],
    "h264_nvenc":        ["-preset", "p5", "-rc", "vbr", "-cq", "23"],
    "h264_qsv":          ["-preset", "medium", "-global_quality", "23"],
    "h264_amf":          ["-quality", "balanced", "-qp_i", "22", "-qp_p", "22"],
    "h264_videotoolbox": ["-q:v", "60"],
    "h264_vaapi":        ["-qp", "23"],
}

CACHE_NAME = "capabilities.json"
CACHE_TTL_S = 7 * 24 * 3600


@dataclass
class Capabilities:
    ffmpeg_version: str = ""
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    available_encoders: list = field(default_factory=list)
    selected_encoder: str | None = None
    hwaccel_decode_enabled: bool = False
    free_disk_gb: float = 0.0
    total_ram_gb: float = 0.0
    memory_limit_mb: int = 3072
    probed_at: float = 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["free_disk_gb"] = round(self.free_disk_gb, 1)
        d["total_ram_gb"] = round(self.total_ram_gb, 1)
        return d


def _run(cmd, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def find_ffmpeg(configured: str = "") -> str:
    p = configured or shutil.which("ffmpeg")
    if not p:
        raise AutoShortsError(
            ErrorCode.FFMPEG_NOT_FOUND,
            "找不到 ffmpeg 執行檔。",
            remediation="Windows: winget install --id Gyan.FFmpeg（注意不是 "
                        "Gyan.FFmpeg.Full，該 ID 不存在），安裝後須重開 shell 才會套用 PATH。"
                        "或在 config.toml 的 [ffmpeg] binary_path 指定絕對路徑。",
            searched=["PATH"])
    return p


def smoke_test(ffmpeg: str, encoder: str, *, timeout: int = 90) -> tuple[bool, str]:
    """§3.2.2 冒煙測試：以 lavfi 合成源實際編碼 1 秒。"""
    rc, out = _run([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30",
        "-t", "1", "-c:v", encoder, "-f", "null", "-",
    ], timeout=timeout)
    if rc == 0:
        return True, ""
    tail = out.strip().splitlines()
    return False, (tail[-1][:160] if tail else "冒煙測試失敗")


def probe(*, ffmpeg_path: str = "", ffprobe_path: str = "",
          cache_dir: str = "", force_refresh: bool = False,
          hwaccel_decode: bool = False) -> Capabilities:
    """探測系統能力。冒煙測試結果會快取，避免每次呼叫付出數秒成本。"""
    cache_file = os.path.join(cache_dir, CACHE_NAME) if cache_dir else ""
    if cache_file and not force_refresh and os.path.isfile(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                d = json.load(f)
            if time.time() - d.get("probed_at", 0) < CACHE_TTL_S:
                return Capabilities(**d)
        except Exception:
            pass

    ff = find_ffmpeg(ffmpeg_path)
    fp = ffprobe_path or shutil.which("ffprobe") or ""
    rc, ver_out = _run([ff, "-hide_banner", "-version"])
    version = ver_out.splitlines()[0] if ver_out else ""

    rc, listed = _run([ff, "-hide_banner", "-encoders"])
    available = []
    for enc in ENCODER_PRIORITY:
        if enc not in listed:
            continue
        ok, _ = smoke_test(ff, enc)
        if ok:
            available.append(enc)

    if not available:
        raise AutoShortsError(
            ErrorCode.FFMPEG_ENCODER_UNAVAILABLE,
            "沒有任何可用編碼器（libx264 亦失敗）。",
            remediation="FFmpeg 建置可能不完整，請改用 full build。")

    try:
        import psutil
        total_ram_gb = psutil.virtual_memory().total / 1e9
    except Exception:
        total_ram_gb = 0.0

    caps = Capabilities(
        ffmpeg_version=version,
        ffmpeg_path=ff.replace("\\", "/"),
        ffprobe_path=fp.replace("\\", "/"),
        available_encoders=available,
        selected_encoder=available[0],
        hwaccel_decode_enabled=hwaccel_decode,
        free_disk_gb=shutil.disk_usage(os.getcwd()).free / 1e9,
        total_ram_gb=total_ram_gb,
        memory_limit_mb=int(min(3072, 0.25 * total_ram_gb * 1024)) if total_ram_gb else 3072,
        probed_at=time.time(),
    )

    if cache_file:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(asdict(caps), f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    return caps


def resolve_encoder(caps: Capabilities, requested: str = "auto") -> str:
    """§4.5 規範：auto 沿降級鏈自動下降；顯式指定但不可用時回傳錯誤，
    不靜默改用其他編碼器——靜默降級會讓使用者誤判效能表現。
    """
    if requested in ("auto", "", None):
        return caps.selected_encoder
    alias = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf",
             "videotoolbox": "h264_videotoolbox", "vaapi": "h264_vaapi",
             "cpu": "libx264"}
    enc = alias.get(requested, requested)
    if enc not in caps.available_encoders:
        raise AutoShortsError(
            ErrorCode.FFMPEG_ENCODER_UNAVAILABLE,
            "指定的編碼器 {} 不可用。".format(requested),
            remediation="可用者：{}。或改用 hw_accel=\"auto\"。".format(
                ", ".join(caps.available_encoders)),
            requested=requested, available=caps.available_encoders)
    return enc
