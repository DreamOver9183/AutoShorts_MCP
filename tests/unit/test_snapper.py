# -*- coding: utf-8 -*-
"""T-02 吸附不漂移、T-14 分段與吸附、§5.2.1 誤差上界與穩健正規化。"""
import numpy as np
import pytest

from autoshorts_mcp.edl.snapper import snap, robust_norm, error_bound

BPM = 120.0
BEAT = 60.0 / BPM                      # 0.5s
BEATS = np.arange(0, 60, BEAT)         # 120 個節拍點


def test_t02_no_drift_over_20_clips():
    """T-02：20 個鏡頭吸附後，每個切點嚴格落在節拍上，終點誤差 < 1ms。"""
    rng = np.random.default_rng(42)
    desired = list(rng.uniform(0.8, 2.5, 20))
    r = snap(desired, BEATS, bpm=BPM)
    assert not r.truncated
    assert len(r.ends) == 20
    for t in r.ends:
        assert float(np.min(np.abs(BEATS - t))) < 1e-9
    assert float(np.min(np.abs(BEATS - r.ends[-1]))) < 1e-3


def test_no_drift_is_structural():
    """誤差不累積：以 T_{k-1} 為基準，故第 20 個鏡頭的誤差與第 1 個同量級。"""
    desired = [1.0] * 20                      # 1.0s 恰好是 2 個節拍
    r = snap(desired, BEATS, bpm=BPM)
    assert all(abs(c - 1.0) < 1e-9 for c in r.durations)


def test_min_clip_duration_enforced():
    """不變式 I-6：吸附後的長度不得低於 c_min。"""
    r = snap([0.05] * 10, BEATS, bpm=BPM, min_clip_duration=0.35)
    assert all(c >= 0.35 - 1e-9 for c in r.durations)


def test_truncates_when_beats_exhausted():
    short = np.arange(0, 3, BEAT)
    r = snap([1.0] * 20, short, bpm=BPM)
    assert r.truncated
    assert len(r.ends) < 20


def test_error_bound_gamma_zero():
    rng = np.random.default_rng(7)
    desired = list(rng.uniform(0.8, 2.5, 30))
    r = snap(desired, BEATS, bpm=BPM)
    bound = error_bound(0.0, BPM)
    assert bound == pytest.approx(0.25)
    assert max(abs(c - d) for c, d in zip(r.durations, desired)) <= bound + 1e-9


def test_error_bound_with_energy_weighting():
    """§5.2.1：加權後上界放寬為 (1/2 + gamma) * 60/BPM。"""
    rng = np.random.default_rng(7)
    desired = list(rng.uniform(0.8, 2.5, 30))
    en = rng.uniform(0, 1, len(BEATS))
    g = 0.3
    r = snap(desired, BEATS, onset_norm=en, gamma=g, bpm=BPM)
    bound = error_bound(g, BPM)
    assert bound == pytest.approx(0.4)
    assert max(abs(c - d) for c, d in zip(r.durations, desired)) <= bound + 1e-9


def test_gamma_is_reach_in_beats():
    """§5.2.1 結構性限制：gamma 直接就是「能跨越幾個節拍」。

    這是能量加權無法替代 R-07（重拍偵測）的原因——4/4 重拍間距為 4 拍，
    需 gamma >= 4，屆時誤差上界已不可接受。
    """
    for g in (0.3, 1.0, 4.0):
        beta = g * (60.0 / BPM)
        assert beta / BEAT == pytest.approx(g)
    assert error_bound(4.0, BPM) == pytest.approx(2.25)


def test_robust_norm_resists_outlier():
    """§5.2.1：注入離群值後，E/E_max 塌陷而百分位法不受影響。"""
    v = np.full(40, 20.0) + np.random.default_rng(0).normal(0, 2, 40)
    spiked = v.copy()
    spiked[20] = v.max() * 12.0

    naive_before = v / v.max()
    naive_after = spiked / spiked.max()
    assert naive_before.mean() - naive_after.mean() > 0.3      # 明顯塌陷

    r_before, r_after = robust_norm(v), robust_norm(spiked)
    assert abs(r_before.mean() - r_after.mean()) < 0.05        # 幾乎不變


def test_robust_norm_flat_input():
    """能量分布過於平坦時退化為 0，即停用加權。"""
    assert np.allclose(robust_norm(np.full(10, 5.0)), 0.0)


def test_robust_norm_clips_to_unit_range():
    v = np.arange(100, dtype=float)
    n = robust_norm(v)
    assert n.min() >= 0.0 and n.max() <= 1.0


def test_downbeat_snapping_for_transitions():
    """§5.2 步驟 4：非 cut 的轉場優先吸附至重拍。"""
    downbeats = BEATS[::4]
    r = snap([2.0, 2.0, 2.0], BEATS, bpm=BPM,
             downbeats=downbeats, transitions=["crossfade"] * 3)
    for t in r.ends:
        assert float(np.min(np.abs(downbeats - t))) < 1e-9


def test_empty_beats_returns_truncated():
    r = snap([1.0], np.array([]), bpm=BPM)
    assert r.truncated and r.ends == []
