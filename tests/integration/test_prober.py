# -*- coding: utf-8 -*-
"""T-05 旋轉旗標、§4.3 直式來源與 VFR 判定。需要 FFmpeg 與合成素材。"""
import os
import shutil
import subprocess

import pytest

from autoshorts_mcp.media import prober
from autoshorts_mcp.errors import AutoShortsError, ErrorCode

pytestmark = pytest.mark.ffmpeg

FIXTURES = os.path.join("tests", "fixtures")
FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    pytest.skip("找不到 ffmpeg", allow_module_level=True)


def _fixture(name):
    p = os.path.join(FIXTURES, name)
    if not os.path.isfile(p):
        pytest.skip("缺少素材 {}，請先執行 scripts/make_fixtures.py".format(name))
    return p


@pytest.fixture(scope="module")
def rotated(tmp_path_factory):
    """產生帶 rotation=90 side data 的素材（T-05）。

    注意：舊式的 `-metadata:s:v:0 rotate=90` 在 FFmpeg 9 上不再寫入 side data
    （實測驗證），須改用輸入側的 `-display_rotation`。
    """
    src = _fixture("video_1080p_24fps.mp4")
    out = str(tmp_path_factory.mktemp("rot") / "rotated90.mp4")
    rc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-display_rotation", "90", "-i", src, "-t", "2", "-c", "copy", out],
        capture_output=True).returncode
    if rc != 0 or not os.path.isfile(out):
        pytest.skip("無法產生旋轉素材")
    return out


# ---------------------------------------------------------------- T-05
def test_t05_rotation_swaps_effective_dimensions(rotated):
    """T-05：旋轉 90 度時有效寬高互換，忽略會導致渲染畫面躺平。"""
    base = prober.probe(_fixture("video_1080p_24fps.mp4"))
    rot = prober.probe(rotated)
    if rot.rotation == 0:
        pytest.skip("此 FFmpeg 建置未寫入 rotation side data")
    assert rot.rotation in (90, 270)
    assert (rot.width, rot.height) == (base.height, base.width)


def test_t05_rotation_changes_orientation(rotated):
    """橫式素材旋轉後應被判為直式。"""
    rot = prober.probe(rotated)
    if rot.rotation == 0:
        pytest.skip("此 FFmpeg 建置未寫入 rotation side data")
    assert rot.orientation != "landscape"


# ---------------------------------------------------------------- §4.3
def test_landscape_orientation():
    info = prober.probe(_fixture("video_4k_16x9.mp4"))
    assert info.orientation == "landscape"
    assert info.width == 3840 and info.height == 2160


def test_portrait_taller_orientation():
    """1080x2400 長寬比 0.45，比目標 9:16 更長。"""
    info = prober.probe(_fixture("video_portrait_9x20.mp4"))
    assert info.orientation == "portrait_taller"
    assert info.aspect_ratio < prober.TARGET_AR


def test_classify_orientation_boundaries():
    assert prober.classify_orientation(1920, 1080) == "landscape"
    assert prober.classify_orientation(1080, 1920) == "portrait_match"
    assert prober.classify_orientation(1080, 2400) == "portrait_taller"
    assert prober.classify_orientation(1080, 1900) == "portrait_match"   # 容差內


def test_duration_and_codec():
    info = prober.probe(_fixture("video_1080p_25fps.mp4"))
    assert info.duration == pytest.approx(8.0, abs=0.2)
    assert info.codec == "h264"
    assert info.fps == pytest.approx(25.0, abs=0.5)


def test_probe_directory_skips_unreadable(tmp_path):
    """§4.3：無法探測的檔案列入 skipped，不整批中止。"""
    d = tmp_path / "media"
    d.mkdir()
    shutil.copy(_fixture("video_1080p_24fps.mp4"), d / "good.mp4")
    (d / "broken.mp4").write_bytes(b"not a video")
    items, skipped = prober.probe_directory(str(d))
    assert len(items) == 1
    assert len(skipped) == 1
    assert "broken.mp4" in skipped[0]["file_path"]


def test_empty_directory_raises_structured_error(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(AutoShortsError) as ei:
        prober.probe_directory(str(d))
    assert ei.value.error.code is ErrorCode.INPUT_NO_MEDIA
    assert ei.value.as_dict()["ok"] is False


def test_missing_directory_raises():
    with pytest.raises(AutoShortsError) as ei:
        prober.probe_directory("does/not/exist")
    assert ei.value.error.code is ErrorCode.INPUT_DIR_NOT_FOUND
