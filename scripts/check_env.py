#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""環境自檢：驗證 AutoShorts-MCP 的執行前提。

對應規格 §3.2.2（硬體編碼器偵測與冒煙測試）、§3.3（軟體依賴）、
§5.6.3.9（第三方函式庫預設參數驗證）。

用法:
    python scripts/check_env.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, field

# §3.2.2 編碼器優先序
ENCODER_PRIORITY = [
    ("h264_nvenc", "NVIDIA"),
    ("h264_qsv", "Intel QSV"),
    ("h264_amf", "AMD AMF"),
    ("h264_videotoolbox", "macOS VideoToolbox"),
    ("h264_vaapi", "Linux VAAPI"),
    ("libx264", "CPU 軟體編碼（保底）"),
]

PY_MIN, PY_MAX = (3, 11), (3, 12)      # §3.3 支援區間
FFMPEG_MIN = (6, 1)                     # §3.3


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = False


@dataclass
class Report:
    results: list = field(default_factory=list)
    ffmpeg: str | None = None
    ffprobe: str | None = None
    available_encoders: list = field(default_factory=list)
    selected_encoder: str | None = None

    def add(self, name, ok, detail="", blocking=False):
        self.results.append(Result(name, ok, detail, blocking))
        return ok

    @property
    def blocked(self):
        return any((not r.ok) and r.blocking for r in self.results)


def _run(cmd, timeout=60):
    """執行外部命令，回傳 (returncode, stdout+stderr)。shell=False（§9 R-03）。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "executable not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def find_binary(name, config_path=None):
    if config_path and os.path.isfile(config_path):
        return config_path
    return shutil.which(name)


def check_python(rep: Report):
    v = sys.version_info[:2]
    ok = PY_MIN <= v <= PY_MAX
    rep.add("Python 版本", ok,
            "{}.{}（支援 {}.{}–{}.{}）".format(v[0], v[1], *PY_MIN, *PY_MAX),
            blocking=not ok)
    if not ok and v > PY_MAX:
        rep.results[-1].detail += "  ← numba/librosa 對新版 Python 的支援通常落後數月"


def check_packages(rep: Report):
    import importlib.metadata as md
    # (套件, 是否為阻擋項)
    for pkg, blocking in [("numpy", True), ("opencv-python-headless", True),
                          ("psutil", True), ("librosa", False),
                          ("scenedetect", False), ("pydantic", False),
                          ("mcp", False), ("yt-dlp", False)]:
        try:
            rep.add("套件 " + pkg, True, md.version(pkg))
        except Exception:
            rep.add("套件 " + pkg, False, "未安裝", blocking=blocking)


def check_mser_params(rep: Report):
    """§5.6.3.9：驗證 MSER 預設參數，這是曾使四輪實測全錯的根因。"""
    try:
        import cv2
    except ImportError:
        rep.add("MSER 參數", False, "opencv 未安裝，無法檢查")
        return
    default = cv2.MSER_create().getMinArea()
    rep.add("MSER 預設 min_area", True,
            "{}（本專案必須顯式設為 15，見 §5.6.3.9）".format(default))


def check_ffmpeg(rep: Report):
    ff = find_binary("ffmpeg")
    fp = find_binary("ffprobe")
    rep.ffmpeg, rep.ffprobe = ff, fp
    if not ff:
        rep.add("FFmpeg", False, "找不到執行檔（PATH 或 config.toml）", blocking=True)
        return False
    rc, out = _run([ff, "-hide_banner", "-version"])
    ver = out.splitlines()[0] if out else ""
    num = ver.split("version", 1)[-1].strip().split()[0] if "version" in ver else "?"
    try:
        parts = tuple(int(x) for x in num.split(".")[:2] if x.isdigit())
        ok = (len(parts) >= 2 and parts >= FFMPEG_MIN) or num.startswith("7") or num.startswith("8")
    except Exception:
        ok = True
    rep.add("FFmpeg", ok, "{}  ({})".format(num, ff), blocking=not ok)
    rep.add("FFprobe", bool(fp), fp or "找不到", blocking=not fp)
    return bool(ff)


def smoke_test_encoders(rep: Report):
    """§3.2.2：僅列於 -encoders 不代表驅動可用，必須實際編碼 1 秒。"""
    ff = rep.ffmpeg
    if not ff:
        return
    rc, listed = _run([ff, "-hide_banner", "-encoders"])
    for enc, vendor in ENCODER_PRIORITY:
        if enc not in listed:
            rep.add("編碼器 " + enc, False, "未編入此 FFmpeg 建置")
            continue
        rc, out = _run([
            ff, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30",
            "-t", "1", "-c:v", enc, "-f", "null", "-",
        ], timeout=90)
        ok = rc == 0
        if ok:
            rep.available_encoders.append(enc)
        detail = vendor if ok else (out.strip().splitlines()[-1][:90] if out.strip() else "冒煙測試失敗")
        rep.add("編碼器 " + enc, ok, detail)
    rep.selected_encoder = rep.available_encoders[0] if rep.available_encoders else None
    rep.add("選定編碼器", bool(rep.selected_encoder),
            rep.selected_encoder or "無可用編碼器（libx264 亦失敗）",
            blocking=not rep.selected_encoder)


def check_disk(rep: Report):
    free_gb = shutil.disk_usage(os.getcwd()).free / 1e9
    rep.add("可用磁碟空間", free_gb >= 20, "{:.1f} GB（建議 ≥ 20 GB）".format(free_gb))


def check_memory(rep: Report):
    try:
        import psutil
    except ImportError:
        return
    total_gb = psutil.virtual_memory().total / 1e9
    limit_mb = min(3072, int(0.25 * total_gb * 1024))     # §5.4.3 保護型公式
    rep.add("系統記憶體", total_gb >= 16, "{:.1f} GB（建議 ≥ 16 GB）".format(total_gb))
    rep.add("熔斷閾值", True, "{} MB = min(3072, 0.25 × RAM)".format(limit_mb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出")
    args = ap.parse_args()

    rep = Report()
    check_python(rep)
    check_packages(rep)
    check_mser_params(rep)
    if check_ffmpeg(rep):
        smoke_test_encoders(rep)
    check_disk(rep)
    check_memory(rep)

    if args.json:
        print(json.dumps({
            "blocked": rep.blocked,
            "ffmpeg": rep.ffmpeg,
            "available_encoders": rep.available_encoders,
            "selected_encoder": rep.selected_encoder,
            "results": [asdict(r) for r in rep.results],
        }, ensure_ascii=False, indent=1))
    else:
        print("AutoShorts-MCP 環境自檢")
        print("=" * 62)
        for r in rep.results:
            mark = "OK  " if r.ok else ("阻擋" if r.blocking else "警告")
            print("  [{}] {:<26} {}".format(mark, r.name, r.detail))
        print("=" * 62)
        if rep.blocked:
            print("結果：有阻擋項，環境尚未就緒。")
        else:
            print("結果：環境就緒。選定編碼器 = {}".format(rep.selected_encoder))
    return 1 if rep.blocked else 0


if __name__ == "__main__":
    sys.exit(main())
