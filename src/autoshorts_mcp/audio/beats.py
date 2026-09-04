# -*- coding: utf-8 -*-
"""Tool 2 §4.2 analyze_audio_beats。

Spike 2 實測發現並修正的兩項缺陷（§4.2.1）：
  1. librosa.load() 預設重取樣至 22050 Hz，使時間解析度達 23.2 ms
     （佔驗收 A-3 預算 46%）。必須顯式指定 sr=44100。
  2. beat_track 回傳的 tempo 標量對應「中位」節拍間隔，受 hop_length
     量化偏移影響（實測偏 2%）。BPM 必須由「平均」間隔計算。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from ..errors import AutoShortsError, ErrorCode


@dataclass
class BeatAnalysis:
    bpm: float
    total_beats: int
    beat_timestamps: list[float]
    downbeats: list[float]
    downbeat_confidence: str          # "inferred" | "detected"
    onset_strength: list[float]       # 各節拍點的起音強度（未正規化）
    analyzed_duration: float
    time_resolution_ms: float
    analysis_sr: int
    hop_length: int

    def as_dict(self) -> dict:
        d = asdict(self)
        d["beat_timestamps"] = [round(x, 4) for x in self.beat_timestamps]
        d["downbeats"] = [round(x, 4) for x in self.downbeats]
        d["onset_strength"] = [round(x, 3) for x in self.onset_strength]
        return d


def infer_downbeats(beats: np.ndarray, meter: int = 4) -> list[float]:
    """R-07 的暫定解：librosa 無原生 downbeat 偵測能力。

    假設 4/4 拍，取每第 meter 個節拍作為重拍。輸出必須標示為 "inferred"，
    不得偽裝成偵測結果。

    注意（v1.8）：§5.2.1 的能量加權「可取得部分重拍效果」之宣稱已撤回——
    beta 的作用半徑構不到 meter 拍外的重拍。R-07 仍未解決。
    """
    return [float(b) for b in beats[::meter]]


def analyze(audio_path: str,
            *,
            max_duration: float = 60.0,
            analysis_sr: int = 44100,
            hop_length: int = 512,
            bpm_from_mean_interval: bool = True,
            downbeat_strategy: str = "infer_4_4",
            meter: int = 4) -> BeatAnalysis:
    """分析音訊節拍。失敗時丟出 AutoShortsError（由呼叫端轉為 §4.7 格式）。"""
    try:
        import librosa
    except ImportError as e:
        raise AutoShortsError(
            ErrorCode.AUDIO_BEAT_DETECTION_FAILED,
            "librosa 未安裝: {}".format(e),
            remediation="pip install librosa") from e

    try:
        # §4.2.1 必須顯式指定 sr，勿用預設的 22050
        y, sr = librosa.load(audio_path, sr=analysis_sr, mono=True,
                             duration=max_duration)
    except Exception as e:
        raise AutoShortsError(
            ErrorCode.AUDIO_BEAT_DETECTION_FAILED,
            "音訊載入失敗: {}".format(e),
            remediation="確認檔案存在且為支援格式",
            path=audio_path) from e

    duration = len(y) / sr if sr else 0.0
    if duration < 2.0:
        raise AutoShortsError(
            ErrorCode.AUDIO_TOO_SHORT,
            "音訊長度僅 {:.2f}s，不足以偵測節拍".format(duration),
            remediation="提供至少 2 秒的音訊")

    # §5.2.1 兩個函式必須共用同一 hop_length，否則索引無法對齊
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tempo_reported, frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=hop_length, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=hop_length)

    if beats.size < 2:
        raise AutoShortsError(
            ErrorCode.AUDIO_BEAT_DETECTION_FAILED,
            "偵測到的節拍點不足（{}）".format(beats.size),
            remediation="確認音訊有明確節奏，或改用其他音樂")

    intervals = np.diff(beats)
    if bpm_from_mean_interval:
        bpm = float(60.0 / intervals.mean())
    else:
        bpm = float(np.atleast_1d(tempo_reported)[0])

    E = onset_env[np.clip(frames, 0, len(onset_env) - 1)]
    downbeats = (infer_downbeats(beats, meter)
                 if downbeat_strategy == "infer_4_4" else [])

    return BeatAnalysis(
        bpm=round(bpm, 2),
        total_beats=int(beats.size),
        beat_timestamps=[float(b) for b in beats],
        downbeats=downbeats,
        downbeat_confidence="inferred" if downbeats else "none",
        onset_strength=[float(x) for x in E],
        analyzed_duration=round(duration, 3),
        time_resolution_ms=round(hop_length / sr * 1000.0, 2),
        analysis_sr=sr,
        hop_length=hop_length,
    )
