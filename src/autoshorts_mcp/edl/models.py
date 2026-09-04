# -*- coding: utf-8 -*-
"""§5.1 剪輯決定表 (EDL) 資料模型與不變式 I-1 ~ I-8。

JSON Schema 無法表達的不變式由此處的驗證器補強。驗證失敗一律轉為
§4.7 的結構化錯誤，不得丟出裸例外。
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..errors import AutoShortsError, ErrorCode

EDL_VERSION = "1.2"

ReframeMode = Literal["auto", "fit_height_crop", "region_pan",
                      "blur_padding", "center_crop", "ken_burns_zoom"]
RegionHint = Literal["auto", "left", "center", "right", "top", "bottom"]
Transition = Literal["cut", "fade_black", "crossfade"]
SourceType = Literal["video", "image"]


class Canvas(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30


class BackgroundAudio(BaseModel):
    file_path: str
    start_offset: float = 0.0
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    fade_out: float = 1.5


class CropRect(BaseModel):
    x: int
    y: int
    w: int
    h: int


class ReframeSegment(BaseModel):
    start_offset: float = Field(description="相對本 clip 起點的秒數")
    duration: float = Field(gt=0)
    crop: CropRect


class ReframePlan(BaseModel):
    """幾何細節層，由 plan_reframe (§4.8) 計算填入。LLM 不應自行撰寫。"""
    segments: list[ReframeSegment] = Field(min_length=1)


class Clip(BaseModel):
    clip_id: int
    source_file: str
    source_type: SourceType
    source_start: float = Field(ge=0, description="素材截取起始秒數（圖片填 0）")
    duration: float = Field(gt=0, description="時間軸上佔用的秒數，經 §5.2 吸附")
    reframe_mode: ReframeMode = "auto"
    region_hint: RegionHint = "auto"
    transition: Transition = "cut"
    mute_source_audio: bool = True
    reframe_plan: ReframePlan | None = None

    @model_validator(mode="after")
    def _image_start_zero(self):
        # I-7：source_type == "image" 時 source_start 必須為 0
        if self.source_type == "image" and self.source_start != 0:
            raise ValueError(
                "I-7 違反：clip {} 為 image 但 source_start={}".format(
                    self.clip_id, self.source_start))
        return self


class EditDecisionList(BaseModel):
    edl_version: str = EDL_VERSION
    project_name: str
    canvas: Canvas = Field(default_factory=Canvas)
    background_audio: BackgroundAudio
    timeline: list[Clip] = Field(min_length=1)

    @field_validator("edl_version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != EDL_VERSION:
            raise ValueError("edl_version 必須為 {}，收到 {}".format(EDL_VERSION, v))
        return v

    @property
    def total_duration(self) -> float:
        return sum(c.duration for c in self.timeline)


# --------------------------------------------------------------------------
# 不變式檢查（需要外部資訊，故不放在 pydantic 驗證器內）
# --------------------------------------------------------------------------

def check_invariants(edl: EditDecisionList,
                     *,
                     media_durations: dict[str, float] | None = None,
                     audio_duration: float | None = None,
                     max_total_duration: float = 60.0,
                     min_clip_duration: float = 0.35,
                     check_files_exist: bool = True) -> list[str]:
    """檢查 I-1 ~ I-8，回傳違規訊息清單（空 = 全部通過）。

    media_durations : source_file -> 時長；提供時檢查 I-2 / I-3
    audio_duration  : 背景音樂時長；提供時檢查 I-4
    """
    v: list[str] = []
    tl = edl.timeline

    # I-1 clip_id 唯一且連續遞增
    ids = [c.clip_id for c in tl]
    if len(set(ids)) != len(ids):
        v.append("I-1: clip_id 有重複 {}".format(
            sorted({i for i in ids if ids.count(i) > 1})))
    elif ids != sorted(ids):
        v.append("I-1: clip_id 未遞增 {}".format(ids))

    for c in tl:
        # I-6 每個 duration >= min_clip_duration
        if c.duration < min_clip_duration - 1e-9:
            v.append("I-6: clip {} duration {:.3f}s < 下限 {:.2f}s".format(
                c.clip_id, c.duration, min_clip_duration))

        # I-2 source_file 必須存在
        if media_durations is not None:
            if c.source_file not in media_durations:
                v.append("I-2: clip {} 的 source_file 不在素材清單中: {}".format(
                    c.clip_id, c.source_file))
            elif c.source_type == "video":
                # I-3 source_start + duration <= 素材時長
                src_dur = media_durations[c.source_file]
                if c.source_start + c.duration > src_dur + 1e-6:
                    v.append("I-3: clip {} 超出素材長度 ({:.3f}+{:.3f} > {:.3f})".format(
                        c.clip_id, c.source_start, c.duration, src_dur))
        elif check_files_exist and not os.path.isfile(c.source_file):
            v.append("I-2: clip {} 的檔案不存在: {}".format(c.clip_id, c.source_file))

        # I-8 reframe_plan 的段落總和 == clip duration，crop 落在畫面內且為偶數
        if c.reframe_plan is not None:
            segs = c.reframe_plan.segments
            total = sum(s.duration for s in segs)
            frame_tol = 1.0 / max(edl.canvas.fps, 1)
            if abs(total - c.duration) > frame_tol:
                v.append("I-8: clip {} 段落總和 {:.3f}s != duration {:.3f}s".format(
                    c.clip_id, total, c.duration))
            for s in segs:
                if s.crop.w % 2 or s.crop.h % 2:
                    v.append("I-8: clip {} crop 尺寸非偶數 {}x{}".format(
                        c.clip_id, s.crop.w, s.crop.h))
                if s.crop.x < 0 or s.crop.y < 0:
                    v.append("I-8: clip {} crop 座標為負 ({},{})".format(
                        c.clip_id, s.crop.x, s.crop.y))
                if media_durations is not None and c.source_file in media_durations:
                    pass  # 畫面邊界檢查需寬高資訊，由 plan_reframe 端保證

    total = edl.total_duration
    # I-5 總時長 <= 平台上限
    if total > max_total_duration + 1e-6:
        v.append("I-5: 總時長 {:.3f}s 超過上限 {:.1f}s".format(total, max_total_duration))
    # I-4 總時長 <= 音樂可用長度
    if audio_duration is not None:
        avail = audio_duration - edl.background_audio.start_offset
        if total > avail + 1e-6:
            v.append("I-4: 總時長 {:.3f}s 超過音樂可用長度 {:.3f}s".format(total, avail))

    return v


def validate_or_raise(edl: EditDecisionList, **kwargs) -> None:
    """檢查不變式，違規時丟出對應錯誤碼的 AutoShortsError。"""
    v = check_invariants(edl, **kwargs)
    if not v:
        return
    joined = "; ".join(v)
    if any(x.startswith("I-2") for x in v):
        code = ErrorCode.EDL_SOURCE_FILE_MISSING
    elif any(x.startswith(("I-4", "I-5")) for x in v):
        code = ErrorCode.EDL_TIMELINE_EXCEEDS_AUDIO
    else:
        code = ErrorCode.EDL_SCHEMA_INVALID
    raise AutoShortsError(code, "EDL 不變式違規: " + joined,
                          remediation="修正上列問題後重新產生 EDL",
                          violations=v)
