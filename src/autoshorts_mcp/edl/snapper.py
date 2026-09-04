# -*- coding: utf-8 -*-
"""§5.2 節拍吸附 + §5.2.1 起音能量加權。

Spike 2 已驗證：
  - T-02 累積誤差不漂移（20 鏡頭終點誤差 0.00 s）
  - 誤差上界 (1/2 + gamma) * 60/BPM 成立
  - 穩健正規化必要（E/E_max 在單一離群值下均值塌陷 90%）
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SnapResult:
    ends: list[float]          # 各鏡頭吸附後的結束時刻 T_k
    durations: list[float]     # 各鏡頭實際長度 c_k
    truncated: bool            # 節拍點用盡而截斷
    error_bound: float         # 本次組態的理論誤差上界（秒）


def robust_norm(values, low_pct: int = 10, high_pct: int = 90) -> np.ndarray:
    """§5.2.1 穩健正規化。

    librosa 的 onset_strength 回傳非負、無上界、未正規化的 spectral flux。
    因此不得使用 E/E_max——單一離群峰值（如一次鈸擊）會把其餘節拍壓到近 0，
    使能量項失效。實測：注入 12 倍離群值後 E/E_max 均值由 0.746 塌陷至 0.070，
    百分位法則為 0.516 -> 0.516（完全不受影響）。
    """
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return v
    lo, hi = np.percentile(v, low_pct), np.percentile(v, high_pct)
    if hi - lo < 1e-9:
        return np.zeros_like(v)
    return np.clip((v - lo) / (hi - lo), 0.0, 1.0)


def snap(desired_durations,
         beats,
         *,
         onset_norm=None,
         gamma: float = 0.0,
         bpm: float = 120.0,
         start_offset: float = 0.0,
         min_clip_duration: float = 0.35,
         downbeats=None,
         transitions=None) -> SnapResult:
    """將估算長度序列吸附至節拍網格。

    desired_durations : LLM 估算的各鏡頭長度
    beats             : 節拍時間戳（遞增）
    onset_norm        : 與 beats 等長、範圍 [0,1] 的正規化起音強度
    gamma             : §5.2.1 偏置強度，beta = gamma * (60/BPM)。0 = 停用
    downbeats         : 重拍時間戳；transitions 非 "cut" 時優先吸附至此
    transitions       : 與 desired_durations 等長的轉場型別

    關鍵設計（§5.2）：步驟 1 以 T_{k-1}（已吸附的絕對時刻）而非累加的估算值
    為基準，故誤差不會沿 timeline 累積。這是採「吸附結束時刻」而非「吸附長度」
    的理由。
    """
    beats = np.asarray(beats, dtype=float)
    if beats.ndim != 1 or beats.size == 0:
        return SnapResult([], [], True, 0.0)

    beta = gamma * (60.0 / bpm) if gamma else 0.0
    bound = (0.5 + gamma) * (60.0 / bpm)
    db = np.asarray(downbeats, dtype=float) if downbeats is not None else None

    T_prev = float(start_offset)
    ends: list[float] = []
    durs: list[float] = []

    for i, d_hat in enumerate(desired_durations):
        T_hat = T_prev + float(d_hat)
        floor = T_prev + min_clip_duration          # 步驟 2：候選集合下限

        pool = beats
        # 步驟 4：段落級轉場優先吸附至重拍
        if db is not None and transitions is not None:
            tr = transitions[i] if i < len(transitions) else "cut"
            if tr != "cut":
                cand_db = db[db >= floor]
                if cand_db.size:
                    pool = cand_db

        mask = pool >= floor
        if not mask.any():
            return SnapResult(ends, durs, True, bound)   # 步驟 6：節拍點用盡

        cand = pool[mask]
        cost = np.abs(cand - T_hat)
        if beta and onset_norm is not None and pool is beats:
            cost = cost - beta * np.asarray(onset_norm, dtype=float)[mask]

        T_k = float(cand[int(np.argmin(cost))])
        ends.append(T_k)
        durs.append(T_k - T_prev)
        T_prev = T_k

    return SnapResult(ends, durs, False, bound)


def error_bound(gamma: float, bpm: float) -> float:
    """§5.2.1 誤差上界：(1/2 + gamma) * 60/BPM。

    gamma=0 時退化為 §5.2 原本的 (1/2) * 60/BPM。
    注意 gamma 的物理意義：beta 的作用半徑以節拍數計就等於 gamma，
    故要跨越 m 拍的重拍間距需 gamma >= m——屆時誤差上界已不可接受。
    這是 §5.2.1 無法替代 R-07（重拍偵測）的結構性原因。
    """
    return (0.5 + gamma) * (60.0 / bpm)
