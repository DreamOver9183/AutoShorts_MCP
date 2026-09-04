# -*- coding: utf-8 -*-
"""§6 設定管理。

優先序（§6.1）：CLI 參數 > 環境變數 > config.toml > 內建預設值。
機密（§6.3）一律不進 config.toml，只從環境變數或 .env 讀取。
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAMES = ("config.toml", "config.local.toml")


@dataclass
class LLMConfig:
    provider: str = "gemini"
    model: str = ""
    temperature: float = 0.7
    max_retries: int = 3                 # §2.3.3 Schema 修復迴圈上限
    thumb_width: int = 858               # §4.9.5 LLM 判讀縮圖寬度
    contact_sheet: bool = True           # §4.9.4 圖片數上限
    contact_sheet_grid: str = "3x3"
    classify_by_llm: bool = True         # §5.6.3.8
    count_tokens_on_start: bool = True   # §4.9.6


@dataclass
class FFmpegConfig:
    binary_path: str = ""
    ffprobe_path: str = ""
    threads: int = 0


@dataclass
class RenderConfig:
    hw_accel: str = "auto"                    # §3.2.2
    hwaccel_decode_enabled: bool = False      # §3.2.3
    memory_limit_mb: int = 0                  # 0 = 自動，見 resolve_memory_limit()
    temp_dir: str = ""
    keep_temp: bool = False
    max_segments_per_clip: int = 8            # §5.6.5 分段上限


@dataclass
class CanvasConfig:
    width: int = 1080
    height: int = 1920
    fps: int = 30


@dataclass
class BeatConfig:
    analysis_sr: int = 44100          # §4.2.1 必須顯式指定，勿用 librosa 預設 22050
    hop_length: int = 512             # §5.2.1 必須同時傳給兩個 librosa 函式
    bpm_from_mean_interval: bool = True   # §4.2.1
    onset_weight_gamma: float = 0.3   # §5.2.1，0 = 停用能量加權
    onset_norm_low_pct: int = 10
    onset_norm_high_pct: int = 90

    @property
    def time_resolution_ms(self) -> float:
        """§4.2.1 節拍時間戳的量化解析度。"""
        return self.hop_length / self.analysis_sr * 1000.0


@dataclass
class EditConfig:
    max_total_duration: float = 60.0   # 不變式 I-5
    min_clip_duration: float = 0.35    # 不變式 I-6 / §5.2 c_min
    sample_max_frames: int = 8         # §4.3，硬上限 12


@dataclass
class ReframeConfig:
    # 可讀性門檻（§5.6.2）
    min_text_height_px: int = 28
    comfy_text_height_px: int = 40
    # 分類器（§5.6.3）
    mser_min_area: int = 15            # §5.6.3.9 必須顯式設定，不得用函式庫預設 60
    classify_aggregate_frames: int = 8
    classify_text_density_threshold: float = 2.96   # §5.6.3.5 共線過濾+去重後
    classify_collinear_filter: bool = True
    classify_collinear_min_group: int = 3
    classify_collinear_height_cv: float = 0.35
    classify_min_confidence: float = 0.70
    # 活動熱區（§5.6.4）
    activity_fps: int = 2
    activity_probe_width: int = 128
    activity_window_sec: float = 2.0
    activity_percentile: int = 85
    min_activity_energy: float = 0.02
    max_activity_area_ratio: float = 0.70
    # 分段與裁切（§5.6.5 / §5.6.6）
    segment_threshold: float = 0.15
    crop_padding: float = 0.12
    min_activity_coverage: float = 0.85
    on_legibility_conflict: str = "prefer_coverage"
    strip_static_border: bool = True


@dataclass
class SamplingConfig:
    scene_detect: bool = True                 # §4.3 PySceneDetect
    scene_detect_threshold: float = 27.0
    scene_detect_on_screen: bool = False      # 螢幕錄影：捲動非切換
    proxy_width: int = 360                    # 像素分析代理寬度


@dataclass
class CapCutConfig:
    draft_root: str = ""
    backend: str = "builtin"          # §4.4.2：社群後端待授權釐清


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    ffmpeg: FFmpegConfig = field(default_factory=FFmpegConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    beat: BeatConfig = field(default_factory=BeatConfig)
    edit: EditConfig = field(default_factory=EditConfig)
    reframe: ReframeConfig = field(default_factory=ReframeConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    capcut: CapCutConfig = field(default_factory=CapCutConfig)

    def as_dict(self) -> dict:
        return asdict(self)


_SECTIONS = {
    "llm": LLMConfig, "ffmpeg": FFmpegConfig, "render": RenderConfig,
    "canvas": CanvasConfig, "beat": BeatConfig, "edit": EditConfig,
    "reframe": ReframeConfig, "sampling": SamplingConfig, "capcut": CapCutConfig,
}


def _apply(section_obj: Any, values: dict, prefix: str, warnings: list) -> None:
    known = set(vars(section_obj))
    for k, v in values.items():
        if k in known:
            setattr(section_obj, k, v)
        else:
            warnings.append("未知設定項 [{}] {}".format(prefix, k))


def resolve_memory_limit(configured_mb: int = 0) -> int:
    """§5.4.3 保護型公式：min(3072, 0.25 * RAM_total)。

    外部建議曾提議在大記憶體機器上放寬至 min(0.5*RAM, 6144)，該提議遭否決——
    熔斷器的目的是抓 bug 而非榨乾資源。本式只下調，不上調。
    """
    if configured_mb and configured_mb > 0:
        return int(configured_mb)
    try:
        import psutil
        total_mb = psutil.virtual_memory().total / (1024 ** 2)
        return int(min(3072, 0.25 * total_mb))
    except Exception:
        return 3072


def load(path: str | os.PathLike | None = None,
         *, env: dict | None = None) -> tuple[Config, list[str]]:
    """載入設定。回傳 (Config, warnings)。

    找不到設定檔不是錯誤——全部使用內建預設值。
    """
    cfg = Config()
    warnings: list[str] = []
    env = env if env is not None else os.environ

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        for name in DEFAULT_CONFIG_NAMES:
            candidates.append(Path.cwd() / name)

    for p in candidates:
        if not p.is_file():
            continue
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            warnings.append("設定檔解析失敗 {}: {}".format(p, e))
            continue
        for sec, values in data.items():
            if sec in _SECTIONS and isinstance(values, dict):
                _apply(getattr(cfg, sec), values, sec, warnings)
            else:
                warnings.append("未知設定區段 [{}]".format(sec))
        break

    # 環境變數覆寫（僅少數常用項；機密不在此處，見 §6.3）
    if env.get("AUTOSHORTS_FFMPEG"):
        cfg.ffmpeg.binary_path = env["AUTOSHORTS_FFMPEG"]
    if env.get("AUTOSHORTS_HW_ACCEL"):
        cfg.render.hw_accel = env["AUTOSHORTS_HW_ACCEL"]
    if env.get("AUTOSHORTS_LLM_MODEL"):
        cfg.llm.model = env["AUTOSHORTS_LLM_MODEL"]

    cfg.render.memory_limit_mb = resolve_memory_limit(cfg.render.memory_limit_mb)

    # §4.3 硬上限夾制
    if cfg.edit.sample_max_frames > 12:
        warnings.append("sample_max_frames 超過硬上限 12，已夾制")
        cfg.edit.sample_max_frames = 12

    return cfg, warnings


def api_key(provider: str, env: dict | None = None) -> str | None:
    """§6.3 機密管理：僅從環境變數讀取，絕不從設定檔。"""
    env = env if env is not None else os.environ
    return env.get({
        "gemini": "GEMINI_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(provider, ""), None)
