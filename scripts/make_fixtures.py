#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以 FFmpeg lavfi 合成測試素材。

對應規格 §8.2：測試素材一律合成，不將影音二進位檔提交進版控。

用法:
    python scripts/make_fixtures.py [--outdir tests/fixtures] [--force]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# (檔名, 說明, lavfi 參數)
VIDEO_FIXTURES = [
    ("video_4k_16x9.mp4", "4K 16:9，30 秒 — OOM 與裁切測試主素材",
     dict(size="3840x2160", rate=30, dur=30)),
    ("video_1080p_24fps.mp4", "1080p 24fps — concat 混合幀率測試",
     dict(size="1920x1080", rate=24, dur=8)),
    ("video_1080p_25fps.mp4", "1080p 25fps — concat 混合幀率測試",
     dict(size="1920x1080", rate=25, dur=8)),
    ("video_1080p_60fps.mp4", "1080p 60fps — concat 混合幀率測試",
     dict(size="1920x1080", rate=60, dur=8)),
    ("video_portrait_9x20.mp4", "1080x2400 直式 — §4.3 直式來源測試",
     dict(size="1080x2400", rate=30, dur=8)),
]

# 影格精確性測試用：每秒一個可辨識的數字，便於驗證切在哪一幀
TIMECODE_FIXTURE = "video_timecode_1080p.mp4"


def run(cmd, quiet=True):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0 and not quiet:
        print(p.stderr[-800:], file=sys.stderr)
    return p.returncode == 0


def make_video(ff, out, size, rate, dur):
    return run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        "testsrc2=size={}:rate={}".format(size, rate),
        "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", out,
    ])


def make_timecode(ff, out, dur=20, rate=30):
    """每幀燒入影格號與時間，供影格精確切片驗證（T-04）。
    無字型時退回 testsrc（其內建計時器亦可辨識）。"""
    vf = ("drawtext=text='F\\:%{frame_num}  T\\:%{pts\\:hms}'"
          ":fontsize=64:fontcolor=white:box=1:boxcolor=black@0.7:x=40:y=40")
    ok = run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate={}".format(rate),
        "-t", str(dur), "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out,
    ])
    if not ok:      # drawtext 需要字型支援，退回無標註版本
        ok = make_video(ff, out, "1920x1080", rate, dur)
    return ok


def make_beat_audio(ff, out, bpm=120, dur=60):
    """已知 BPM 的節拍音訊（§8.2）：每拍一個短脈衝，其餘靜音。
    120 BPM = 每 0.5 秒一拍，使節拍偵測正確性成為可斷言的測試。"""
    beat_s = 60.0 / bpm
    click, gap = 0.05, beat_s - 0.05
    n_loop = int(dur / beat_s)
    return run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=1200:duration={}".format(click),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d={}".format(gap),
        "-filter_complex",
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[c];"
        "[c][1:a]concat=n=2:v=0:a=1[one];"
        "[one]aloop=loop={}:size={}[out]".format(n_loop, int(44100 * beat_s)),
        "-map", "[out]", "-t", str(dur),
        "-c:a", "pcm_s16le", "-ar", "44100", out,
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=os.path.join("tests", "fixtures"))
    ap.add_argument("--force", action="store_true", help="覆寫既有檔案")
    args = ap.parse_args()

    ff = shutil.which("ffmpeg")
    if not ff:
        print("找不到 ffmpeg，請先執行 scripts/check_env.py", file=sys.stderr)
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    made, skipped, failed = 0, 0, 0

    def target(name):
        return os.path.join(args.outdir, name)

    for name, desc, kw in VIDEO_FIXTURES:
        out = target(name)
        if os.path.exists(out) and not args.force:
            print("  跳過（已存在） {}".format(name)); skipped += 1; continue
        ok = make_video(ff, out, **kw)
        print("  {} {:<28} {}".format("建立" if ok else "失敗", name, desc))
        made += ok; failed += (not ok)

    out = target(TIMECODE_FIXTURE)
    if os.path.exists(out) and not args.force:
        print("  跳過（已存在） {}".format(TIMECODE_FIXTURE)); skipped += 1
    else:
        ok = make_timecode(ff, out)
        print("  {} {:<28} 燒入影格號，供影格精確性驗證".format(
            "建立" if ok else "失敗", TIMECODE_FIXTURE))
        made += ok; failed += (not ok)

    out = target("beat_120bpm.wav")
    if os.path.exists(out) and not args.force:
        print("  跳過（已存在） beat_120bpm.wav"); skipped += 1
    else:
        ok = make_beat_audio(ff, out)
        print("  {} {:<28} 120 BPM，節拍偵測的可斷言真值".format(
            "建立" if ok else "失敗", "beat_120bpm.wav"))
        made += ok; failed += (not ok)

    print("\n建立 {} 個、跳過 {} 個、失敗 {} 個 -> {}".format(
        made, skipped, failed, os.path.abspath(args.outdir)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
