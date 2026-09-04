# -*- coding: utf-8 -*-
"""T-03：EDL 不變式 I-1 ~ I-8，每條各有一個違反案例。"""
import pytest
from pydantic import ValidationError

from autoshorts_mcp.edl.models import (
    EditDecisionList, Clip, BackgroundAudio, Canvas,
    ReframePlan, ReframeSegment, CropRect,
    check_invariants, validate_or_raise, EDL_VERSION,
)
from autoshorts_mcp.errors import AutoShortsError, ErrorCode

MEDIA = {"a.mp4": 30.0, "b.mp4": 30.0}


def _clip(cid=1, src="a.mp4", start=0.0, dur=2.0, stype="video", **kw):
    return Clip(clip_id=cid, source_file=src, source_type=stype,
                source_start=start, duration=dur, **kw)


def _edl(clips, *, offset=0.0):
    return EditDecisionList(
        edl_version=EDL_VERSION, project_name="t",
        canvas=Canvas(), timeline=clips,
        background_audio=BackgroundAudio(file_path="bgm.wav", start_offset=offset),
    )


def _check(edl, **kw):
    kw.setdefault("media_durations", MEDIA)
    kw.setdefault("audio_duration", 120.0)
    return check_invariants(edl, **kw)


def test_valid_edl_passes():
    edl = _edl([_clip(1), _clip(2)])
    assert _check(edl) == []


def test_i1_duplicate_clip_id():
    v = _check(_edl([_clip(1), _clip(1)]))
    assert any(x.startswith("I-1") for x in v)


def test_i1_non_increasing():
    v = _check(_edl([_clip(2), _clip(1)]))
    assert any(x.startswith("I-1") for x in v)


def test_i2_source_not_in_manifest():
    v = _check(_edl([_clip(1, src="missing.mp4")]))
    assert any(x.startswith("I-2") for x in v)


def test_i3_exceeds_source_length():
    v = _check(_edl([_clip(1, start=29.0, dur=5.0)]))
    assert any(x.startswith("I-3") for x in v)


def test_i4_exceeds_audio():
    v = _check(_edl([_clip(1, dur=10.0)], offset=115.0), audio_duration=120.0)
    assert any(x.startswith("I-4") for x in v)


def test_i5_exceeds_max_duration():
    clips = [_clip(i + 1, dur=5.0) for i in range(13)]      # 65s > 60s
    v = _check(_edl(clips), max_total_duration=60.0)
    assert any(x.startswith("I-5") for x in v)


def test_i6_below_min_clip_duration():
    v = _check(_edl([_clip(1, dur=0.2)]))
    assert any(x.startswith("I-6") for x in v)


def test_i7_image_with_nonzero_start():
    # I-7 由 pydantic 驗證器攔截，建構時即失敗
    with pytest.raises(ValidationError):
        _clip(1, stype="image", start=1.5)


def test_i8_segment_sum_mismatch():
    plan = ReframePlan(segments=[
        ReframeSegment(start_offset=0.0, duration=1.0,
                       crop=CropRect(x=0, y=0, w=608, h=1080)),
    ])
    v = _check(_edl([_clip(1, dur=2.0, reframe_plan=plan)]))
    assert any(x.startswith("I-8") for x in v)


def test_i8_odd_crop_dimensions():
    plan = ReframePlan(segments=[
        ReframeSegment(start_offset=0.0, duration=2.0,
                       crop=CropRect(x=0, y=0, w=607, h=1079)),
    ])
    v = _check(_edl([_clip(1, dur=2.0, reframe_plan=plan)]))
    assert any("非偶數" in x for x in v)


def test_i8_valid_plan_passes():
    plan = ReframePlan(segments=[
        ReframeSegment(start_offset=0.0, duration=1.0,
                       crop=CropRect(x=0, y=0, w=608, h=1080)),
        ReframeSegment(start_offset=1.0, duration=1.0,
                       crop=CropRect(x=100, y=0, w=608, h=1080)),
    ])
    assert _check(_edl([_clip(1, dur=2.0, reframe_plan=plan)])) == []


def test_error_code_mapping():
    with pytest.raises(AutoShortsError) as ei:
        validate_or_raise(_edl([_clip(1, src="missing.mp4")]),
                          media_durations=MEDIA, audio_duration=120.0)
    assert ei.value.error.code is ErrorCode.EDL_SOURCE_FILE_MISSING
    assert ei.value.as_dict()["ok"] is False


def test_edl_version_enforced():
    with pytest.raises(ValidationError):
        EditDecisionList(edl_version="1.0", project_name="t",
                         background_audio=BackgroundAudio(file_path="b.wav"),
                         timeline=[_clip(1)])
