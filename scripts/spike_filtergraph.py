#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spike 1：驗證規格中從未實際執行過的濾鏡鏈與管線假設。

驗證對象：
  §3.2.4  硬體編碼器品質參數
  §5.3.1  blur_padding 濾鏡鏈 + 縮小-模糊-放大最佳化（規格標為「待實測」）
  §5.4    記憶體峰值模型（預期 655 MB）
  §5.5    影格精確切片（-ss 前置 + 重新編碼）
  §5.6.8  裁切重構濾鏡鏈
  T-04 / T-06 測試案例

用法:
    python scripts/spike_filtergraph.py [--fixtures tests/fixtures] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

FF = shutil.which("ffmpeg")
FP = shutil.which("ffprobe")

OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30

# §5.3.1 原始模糊鏈
BLUR_FULL = (
    "[0:v]split=2[fg][bg];"
    "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
    "boxblur=luma_radius=25:luma_power=3[blurred];"
    "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[foreground];"
    "[blurred][foreground]overlay=(W-w)/2:(H-h)/2[outv]"
)
# §5.3.1 註記的最佳化方案：先縮小 → 模糊 → 放大
BLUR_FAST = (
    "[0:v]split=2[fg][bg];"
    "[bg]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,"
    "boxblur=luma_radius=4:luma_power=2,scale=1080:1920[blurred];"
    "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[foreground];"
    "[blurred][foreground]overlay=(W-w)/2:(H-h)/2[outv]"
)


def sh(cmd, timeout=600):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def probe(path, entries, stream="v:0"):
    rc, out, err = sh([FP, "-v", "error", "-select_streams", stream,
                       "-show_entries", entries, "-of", "json", path])
    if rc != 0:
        return {}
    try:
        d = json.loads(out)
    except Exception:
        return {}
    if "streams" in d and d["streams"]:
        return d["streams"][0]
    return d.get("format", {})


def count_frames(path):
    rc, out, err = sh([FP, "-v", "error", "-select_streams", "v:0",
                       "-count_frames", "-show_entries", "stream=nb_read_frames",
                       "-of", "default=nk=1:nw=1", path])
    try:
        return int(out.strip())
    except Exception:
        return -1


def run_timed_with_rss(cmd, timeout=900):
    """執行並取樣進程樹 RSS 峰值（§5.4.3）。"""
    try:
        import psutil
    except ImportError:
        t0 = time.time(); rc, o, e = sh(cmd, timeout); return rc, time.time()-t0, None, e
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ps = psutil.Process(p.pid)
    peak = 0
    while p.poll() is None:
        try:
            rss = ps.memory_info().rss
            for c in ps.children(recursive=True):
                try: rss += c.memory_info().rss
                except Exception: pass
            peak = max(peak, rss)
        except Exception:
            pass
        time.sleep(0.05)
    _, err = p.communicate()
    return p.returncode, time.time()-t0, peak/1e6, (err or b"").decode("utf-8", "replace")


# ----------------------------------------------------------------------------
def test_blur_filtergraphs(src, tmp, results):
    """§5.3.1：兩種模糊鏈是否可執行、輸出規格是否正確、效能差多少。"""
    for tag, graph in (("BLUR_FULL", BLUR_FULL), ("BLUR_FAST", BLUR_FAST)):
        out = os.path.join(tmp, "blur_{}.mp4".format(tag))
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-y",
               "-ss", "0", "-i", src, "-t", "5",
               "-filter_complex", graph, "-map", "[outv]",
               "-r", str(OUT_FPS), "-pix_fmt", "yuv420p",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-an", "-video_track_timescale", "15360", out]
        rc, secs, rss, err = run_timed_with_rss(cmd)
        if rc != 0:
            results.append(dict(test="§5.3.1 " + tag, ok=False,
                                detail=err.strip().splitlines()[-1][:120] if err.strip() else "執行失敗"))
            continue
        st = probe(out, "stream=width,height,pix_fmt,r_frame_rate")
        spec_ok = (int(st.get("width", 0)) == OUT_W and int(st.get("height", 0)) == OUT_H
                   and st.get("pix_fmt") == "yuv420p")
        results.append(dict(
            test="§5.3.1 " + tag, ok=spec_ok,
            detail="{}x{} {} {}  耗時 {:.2f}s  峰值RSS {:.0f}MB".format(
                st.get("width"), st.get("height"), st.get("pix_fmt"),
                st.get("r_frame_rate"), secs, rss or 0),
            secs=secs, rss_mb=rss))


def test_frame_accuracy(src, tmp, results):
    """§5.5 / T-04：-ss 前置 + 重新編碼是否影格精確。
    切 2.000s~5.000s @30fps 應恰為 90 幀。"""
    out = os.path.join(tmp, "cut.mp4")
    rc, _, err = sh([FF, "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", "2.0", "-i", src, "-t", "3.0",
                     "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                     "-r", str(OUT_FPS), "-pix_fmt", "yuv420p",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                     "-an", "-video_track_timescale", "15360", out])
    if rc != 0:
        results.append(dict(test="§5.5 影格精確切片", ok=False,
                            detail=err.strip().splitlines()[-1][:120]))
        return
    n = count_frames(out)
    expect = int(3.0 * OUT_FPS)
    results.append(dict(test="§5.5 影格精確切片 (T-04)", ok=abs(n - expect) <= 1,
                        detail="實際 {} 幀 / 預期 {} 幀（誤差 {} 幀）".format(n, expect, n - expect)))


def test_concat_mixed_fps(fixdir, tmp, results):
    """T-06：混合幀率來源正規化後拼接，總時長誤差須 < 100ms。"""
    srcs = [os.path.join(fixdir, n) for n in
            ("video_1080p_24fps.mp4", "video_1080p_25fps.mp4", "video_1080p_60fps.mp4")]
    srcs = [s for s in srcs if os.path.exists(s)]
    if len(srcs) < 2:
        results.append(dict(test="T-06 混合幀率拼接", ok=False, detail="缺少測試素材"))
        return
    chunks, per = [], 4.0
    for i, s in enumerate(srcs):
        c = os.path.join(tmp, "chunk_{}.mp4".format(i))
        rc, _, err = sh([FF, "-hide_banner", "-loglevel", "error", "-y",
                         "-ss", "1.0", "-i", s, "-t", str(per),
                         "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                         "-r", str(OUT_FPS), "-pix_fmt", "yuv420p",
                         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                         "-an", "-video_track_timescale", "15360", c])
        if rc != 0:
            results.append(dict(test="T-06 混合幀率拼接", ok=False,
                                detail="chunk {} 失敗: {}".format(i, err.strip()[-100:])))
            return
        chunks.append(c)
    lst = os.path.join(tmp, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write("file '{}'\n".format(c.replace("\\", "/")))
    out = os.path.join(tmp, "concat_out.mp4")
    rc, _, err = sh([FF, "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "concat", "-safe", "0", "-i", lst,
                     "-c:v", "copy", "-movflags", "+faststart", out])
    if rc != 0:
        results.append(dict(test="T-06 混合幀率拼接", ok=False,
                            detail=err.strip().splitlines()[-1][:120]))
        return
    d = float(probe(out, "format=duration", stream="v:0").get("duration") or
              probe(out, "format=duration").get("duration") or 0)
    if d == 0:
        rc, o, _ = sh([FP, "-v", "error", "-show_entries", "format=duration",
                       "-of", "default=nk=1:nw=1", out])
        d = float(o.strip() or 0)
    expect = per * len(chunks)
    drift_ms = abs(d - expect) * 1000
    results.append(dict(test="T-06 混合幀率拼接", ok=drift_ms < 100,
                        detail="{} 段 x {}s，實際 {:.3f}s，漂移 {:.1f}ms（門檻 100ms）".format(
                            len(chunks), per, d, drift_ms)))


def test_4k_memory(fixdir, tmp, results):
    """§5.4：4K 素材單一 chunk 的峰值 RSS，模型預期約 655 MB、上限 3 GB。"""
    src = os.path.join(fixdir, "video_4k_16x9.mp4")
    if not os.path.exists(src):
        results.append(dict(test="§5.4 4K 記憶體峰值", ok=False, detail="缺少 4K 素材"))
        return
    out = os.path.join(tmp, "chunk_4k.mp4")
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", "5", "-i", src, "-t", "5",
           "-filter_complex", BLUR_FULL, "-map", "[outv]",
           "-r", str(OUT_FPS), "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-an", "-video_track_timescale", "15360", out]
    rc, secs, rss, err = run_timed_with_rss(cmd)
    if rc != 0:
        results.append(dict(test="§5.4 4K 記憶體峰值", ok=False,
                            detail=err.strip().splitlines()[-1][:120]))
        return
    results.append(dict(test="§5.4 4K 記憶體峰值", ok=(rss or 0) < 3072,
                        detail="峰值 {:.0f} MB（模型預期 655 MB，熔斷上限 3072 MB）耗時 {:.1f}s".format(
                            rss or 0, secs),
                        rss_mb=rss))


def test_hw_encoder(src, tmp, results, encoder, qparams):
    """§3.2.4：硬體編碼器品質參數是否可用。"""
    out = os.path.join(tmp, "hw_{}.mp4".format(encoder))
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", "0", "-i", src, "-t", "5",
           "-filter_complex", BLUR_FULL, "-map", "[outv]",
           "-r", str(OUT_FPS), "-pix_fmt", "yuv420p", "-c:v", encoder]
    cmd += qparams + ["-an", "-video_track_timescale", "15360", out]
    rc, secs, rss, err = run_timed_with_rss(cmd)
    ok = rc == 0
    detail = "耗時 {:.2f}s  峰值RSS {:.0f}MB".format(secs, rss or 0) if ok else \
             (err.strip().splitlines()[-1][:110] if err.strip() else "失敗")
    results.append(dict(test="§3.2.4 " + encoder, ok=ok, detail=detail,
                        secs=secs if ok else None, rss_mb=rss))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default=os.path.join("tests", "fixtures"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not FF or not FP:
        print("找不到 ffmpeg/ffprobe，請先執行 scripts/check_env.py", file=sys.stderr)
        return 1

    src4k = os.path.join(args.fixtures, "video_4k_16x9.mp4")
    src1080 = os.path.join(args.fixtures, "video_timecode_1080p.mp4")
    if not os.path.exists(src4k):
        print("缺少測試素材，請先執行 scripts/make_fixtures.py", file=sys.stderr)
        return 1

    rc, out, _ = sh([FF, "-hide_banner", "-version"])
    ver = out.splitlines()[0] if out else "?"
    results = []
    tmp = tempfile.mkdtemp(prefix="spike1_")
    try:
        test_blur_filtergraphs(src4k, tmp, results)
        test_frame_accuracy(src1080 if os.path.exists(src1080) else src4k, tmp, results)
        test_concat_mixed_fps(args.fixtures, tmp, results)
        test_4k_memory(args.fixtures, tmp, results)
        rc, enc_list, _ = sh([FF, "-hide_banner", "-encoders"])
        for enc, q in (("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "23"]),
                       ("h264_qsv", ["-preset", "medium", "-global_quality", "23"])):
            if enc in enc_list:
                test_hw_encoder(src4k, tmp, results, enc, q)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if args.json:
        print(json.dumps({"ffmpeg": ver, "results": results}, ensure_ascii=False, indent=1))
    else:
        print("Spike 1：濾鏡鏈與管線驗證")
        print(ver)
        print("=" * 76)
        for r in results:
            print("  [{}] {:<28} {}".format("OK  " if r["ok"] else "FAIL", r["test"], r["detail"]))
        print("=" * 76)
        f = sum(1 for r in results if not r["ok"])
        print("通過 {} / {}".format(len(results) - f, len(results)))
        # 模糊最佳化的加速比
        full = next((r for r in results if r["test"].endswith("BLUR_FULL") and r["ok"]), None)
        fast = next((r for r in results if r["test"].endswith("BLUR_FAST") and r["ok"]), None)
        if full and fast and fast.get("secs"):
            print("模糊最佳化加速比：{:.2f}x（§5.3.1 待實測項）".format(full["secs"] / fast["secs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
