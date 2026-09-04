#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spike 2：驗證音訊分析與節拍吸附。

驗證對象：
  R-10     librosa -> numba 對 Python 3.12 的相容性
  §4.2     節拍偵測（T-01）
  §5.2     節拍吸附與「累積誤差不漂移」保證（T-02）
  §5.2.1   起音能量加權——規格中為 C 級證據，本 spike 首次實測
  §5.2.1   hop_length 一致性要求（實作陷阱）
  §5.2.1   穩健正規化 vs 樸素 E/E_max

用法:
    python scripts/spike_audio.py [--fixtures tests/fixtures] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HOP = 512          # §6.2 [beat] hop_length，必須同時傳給兩個函式
SR = 44100         # 與 §4.1 的 WAV 輸出一致；22050 會使量化誤差達 23.2ms（A-3 預算的 46%）
C_MIN = 0.35       # 不變式 I-6


# ---------------------------------------------------------------- 演算法
def snap_timeline(desired, beats, onset_norm=None, gamma=0.0, bpm=120.0,
                  start=0.0, c_min=C_MIN):
    """§5.2 節拍吸附 + §5.2.1 能量加權。

    desired      : 各鏡頭的估算長度 (list[float])
    beats        : 節拍時間戳 (遞增 ndarray)
    onset_norm   : 與 beats 等長的正規化起音強度 [0,1]，None 表示不加權
    gamma        : §5.2.1 的 gamma，beta = gamma * (60/BPM)
    回傳 (T列表, 實際長度列表, truncated)
    """
    import numpy as np
    beta = gamma * (60.0 / bpm) if gamma else 0.0
    T_prev, Ts, cs = start, [], []
    for d_hat in desired:
        T_hat = T_prev + d_hat
        mask = beats >= (T_prev + c_min)
        if not mask.any():
            return Ts, cs, True
        cand = beats[mask]
        cost = np.abs(cand - T_hat)
        if beta and onset_norm is not None:
            cost = cost - beta * onset_norm[mask]
        T_k = float(cand[int(np.argmin(cost))])
        Ts.append(T_k); cs.append(T_k - T_prev); T_prev = T_k
    return Ts, cs, False


def robust_norm(vals, lo_pct=10, hi_pct=90):
    """§5.2.1 穩健正規化：以節拍點上的百分位而非最大值。"""
    import numpy as np
    v = np.asarray(vals, float)
    p_lo, p_hi = np.percentile(v, lo_pct), np.percentile(v, hi_pct)
    if p_hi - p_lo < 1e-9:
        return np.zeros_like(v)
    return np.clip((v - p_lo) / (p_hi - p_lo), 0.0, 1.0)


def naive_norm(vals):
    """外部建議書原式：E / E_max。"""
    import numpy as np
    v = np.asarray(vals, float)
    m = v.max()
    return v / m if m > 0 else np.zeros_like(v)


# ---------------------------------------------------------------- 測試
def t_import(res):
    try:
        import librosa, numba
        res.append(dict(test="R-10 librosa/numba 相容性", ok=True,
                        detail="librosa {} / numba {} / Python {}.{}".format(
                            librosa.__version__, numba.__version__,
                            sys.version_info[0], sys.version_info[1])))
        return True
    except Exception as e:
        res.append(dict(test="R-10 librosa/numba 相容性", ok=False,
                        detail="{}: {}".format(type(e).__name__, str(e)[:110])))
        return False


def t_beat_detect(path, res, label, expect_bpm=120.0):
    """T-01：已知 BPM 的合成音訊，BPM 誤差 <= 2，節拍間隔標準差 < 0.02s。"""
    import numpy as np, librosa
    y, sr = librosa.load(path, sr=SR, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr,
                                            hop_length=HOP, units="frames")
    tempo_reported = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    iv = np.diff(beats)
    # §4.2 規範：BPM 由平均節拍間隔計算，不採用 librosa 的 tempo 標量。
    # tempo 標量等同「中位間隔」，受 hop_length 量化偏移影響（實測 sr=22050 時偏 2%）。
    bpm = 60.0 / iv.mean()
    ok = abs(bpm - expect_bpm) <= 2.0 and iv.std() < 0.02
    res.append(dict(test="T-01 節拍偵測 ({})".format(label), ok=ok,
                    detail="BPM {:.2f}（平均間隔法，預期 {}±2）  librosa tempo 回報 {:.2f}  "
                           "{} 拍  間隔 {:.4f}±{:.4f}s  量化 {:.1f}ms".format(
                        bpm, expect_bpm, tempo_reported, len(beats),
                        iv.mean(), iv.std(), HOP / sr * 1000)))
    return onset_env, beats, bpm, sr


def t_hop_alignment(onset_env, beats, sr, res):
    """§5.2.1：onset_strength 與 beat_track 必須用同一 hop_length。"""
    import numpy as np, librosa
    f_ok = librosa.time_to_frames(beats, sr=sr, hop_length=HOP)
    in_range = bool((f_ok < len(onset_env)).all() and (f_ok >= 0).all())
    # 刻意用錯的 hop_length，展示索引錯位
    f_bad = librosa.time_to_frames(beats, sr=sr, hop_length=HOP * 2)
    drift = int(np.abs(f_ok - f_bad).max())
    res.append(dict(test="§5.2.1 hop_length 一致性", ok=in_range,
                    detail="一致時索引全部落在包絡線內；hop 減半則最大錯位 {} 幀"
                           "（約 {:.2f}s），足以取到完全錯誤的能量值".format(
                               drift, drift * HOP / sr)))


def t_no_drift(beats, res, n=20):
    """T-02：20 個鏡頭吸附後，終點與最近節拍的誤差 < 1ms。"""
    import numpy as np
    rng = np.random.default_rng(42)
    desired = list(rng.uniform(0.8, 2.5, n))
    Ts, cs, trunc = snap_timeline(desired, beats, bpm=120.0)
    if trunc or not Ts:
        res.append(dict(test="T-02 吸附不漂移", ok=False, detail="節拍點不足"))
        return
    err = float(np.min(np.abs(beats - Ts[-1])))
    all_on = all(float(np.min(np.abs(beats - t))) < 1e-9 for t in Ts)
    res.append(dict(test="T-02 吸附不漂移", ok=(err < 1e-3 and all_on),
                    detail="{} 個鏡頭，終點誤差 {:.2e}s，全部切點落在節拍上：{}".format(
                        len(Ts), err, all_on)))


def t_error_bound(beats, onset_norm, res, gamma=0.3, bpm=120.0, n=20):
    """§5.2.1 改寫後的誤差上界：|c_k - c_hat| <= (1/2 + gamma) * 60/BPM。"""
    import numpy as np
    rng = np.random.default_rng(7)
    desired = list(rng.uniform(0.8, 2.5, n))
    _, cs, _ = snap_timeline(desired, beats, onset_norm, gamma=gamma, bpm=bpm)
    if not cs:
        res.append(dict(test="§5.2.1 誤差上界", ok=False, detail="無結果")); return
    errs = [abs(c - d) for c, d in zip(cs, desired[:len(cs)])]
    bound = (0.5 + gamma) * (60.0 / bpm)
    res.append(dict(test="§5.2.1 誤差上界", ok=max(errs) <= bound + 1e-9,
                    detail="最大誤差 {:.4f}s / 上界 {:.4f}s（gamma={}，理論 (1/2+g)*60/BPM）".format(
                        max(errs), bound, gamma)))


def t_norm_robustness(onset_at_beats, res):
    """§5.2.1：樸素 E/E_max 對離群值脆弱，穩健正規化不受影響。"""
    import numpy as np
    v = np.asarray(onset_at_beats, float).copy()
    spike = v.copy(); spike[len(spike) // 2] = v.max() * 12.0   # 注入一次鈸擊
    n_before, n_after = naive_norm(v), naive_norm(spike)
    r_before, r_after = robust_norm(v), robust_norm(spike)
    naive_collapse = float(n_before.mean() - n_after.mean())
    robust_shift = float(abs(r_before.mean() - r_after.mean()))
    res.append(dict(test="§5.2.1 正規化穩健性",
                    ok=(naive_collapse > 0.3 and robust_shift < 0.05),
                    detail="注入 12 倍離群值後：E/Emax 均值 {:.3f}->{:.3f}（塌陷 {:.3f}）；"
                           "百分位法 {:.3f}->{:.3f}（變動 {:.3f}）".format(
                               n_before.mean(), n_after.mean(), naive_collapse,
                               r_before.mean(), r_after.mean(), robust_shift)))


def t_energy_weighting(path, res, meter=4):
    """§5.2.1 的核心宣稱：能量加權能把切點吸向重拍（R-07 的替代解）。"""
    import numpy as np, librosa
    if not os.path.exists(path):
        res.append(dict(test="§5.2.1 能量加權對重拍的效果", ok=False,
                        detail="缺少帶重音的測試素材")); return
    y, sr = librosa.load(path, sr=SR, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr,
                                            hop_length=HOP, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    if len(beats) < 20:
        res.append(dict(test="§5.2.1 能量加權對重拍的效果", ok=False,
                        detail="偵測到的節拍過少 ({})".format(len(beats)))); return
    E = onset_env[np.clip(frames, 0, len(onset_env) - 1)]
    En = robust_norm(E)
    # 以能量前 1/meter 者視為「重拍」
    thr = np.percentile(E, 100 * (1 - 1.0 / meter))
    is_strong = E >= thr

    # n=25 單次抽樣的標準差約 10%，不足以分辨效果；改為 300 次重抽
    bpm = 60.0 / float(np.mean(np.diff(beats)))
    rng = np.random.default_rng(11)
    hits = {}
    for g in (0.0, 0.3):
        acc = []
        for _ in range(300):
            desired = list(rng.uniform(1.0, 2.2, 20))
            Ts, _, _ = snap_timeline(desired, beats, En, gamma=g, bpm=bpm)
            if not Ts: continue
            idx = [int(np.argmin(np.abs(beats - t))) for t in Ts]
            acc.append(float(np.mean(is_strong[idx])))
        hits[g] = (float(np.mean(acc)), float(np.std(acc)))
    (b_m, b_s), (w_m, w_s) = hits[0.0], hits[0.3]
    beta = 0.3 * (60.0 / bpm)
    reach = beta / float(np.median(np.diff(beats)))
    res.append(dict(test="§5.2.1 能量加權對重拍的效果",
                    ok=w_m > b_m,
                    detail="命中強拍 gamma=0: {:.1%}±{:.1%} -> gamma=0.3: {:.1%}±{:.1%}"
                           "（基準 {:.0%}）。beta={:.3f}s 僅可跨 {:.2f} 個節拍間隔，"
                           "構不到 {:.0f} 拍外的重拍".format(
                               b_m, b_s, w_m, w_s, 1.0 / meter, beta, reach, meter),
                    baseline=b_m, weighted=w_m))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default=os.path.join("tests", "fixtures"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = []
    if not t_import(res):
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
              "R-10 失敗：{}".format(res[0]["detail"]))
        return 1

    import numpy as np, librosa
    plain = os.path.join(args.fixtures, "beat_120bpm.wav")
    accent = os.path.join(args.fixtures, "beat_120bpm_accented.wav")
    if not os.path.exists(plain):
        print("缺少 beat_120bpm.wav，請先執行 scripts/make_fixtures.py", file=sys.stderr)
        return 1

    onset_env, beats, tempo, sr = t_beat_detect(plain, res, "等強度")
    t_hop_alignment(onset_env, beats, sr, res)
    t_no_drift(beats, res)

    frames = librosa.time_to_frames(beats, sr=sr, hop_length=HOP)
    E = onset_env[np.clip(frames, 0, len(onset_env) - 1)]
    t_norm_robustness(E, res)
    t_error_bound(beats, robust_norm(E), res)
    t_energy_weighting(accent, res)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("Spike 2：音訊分析與節拍吸附")
        print("=" * 82)
        for r in res:
            print("  [{}] {:<30} {}".format("OK  " if r["ok"] else "FAIL",
                                            r["test"], r["detail"]))
        print("=" * 82)
        f = sum(1 for r in res if not r["ok"])
        print("通過 {} / {}".format(len(res) - f, len(res)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
