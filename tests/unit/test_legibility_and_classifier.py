# -*- coding: utf-8 -*-
"""T-11 分類器訊號方向性、T-12 可讀性恆等式。

T-11 依 §8.3 僅驗證方向，不驗證門檻——合成樣本不可作為門檻依據（§5.6.3.6）。
"""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from autoshorts_mcp.preprocess import legibility as leg
from autoshorts_mcp.preprocess import classifier as clf


# ---------------------------------------------------------------- T-12
def test_t12_crop_identity_is_resolution_independent():
    """h_out = phi * H_pane / z，與原始解析度無關。"""
    phi, z = 0.0176, 1.0
    expected = phi * 1920 / z
    for src_h in (1080, 1440, 2160):
        assert leg.crop_text_height(phi, z) == pytest.approx(expected, abs=1e-9)
        assert leg.crop_text_height(phi, z) == pytest.approx(33.79, abs=0.01)


def test_t12_letterbox_identity():
    """16:9 來源：h_out = phi * 1080 * 9/16 = phi * 607.5。"""
    phi = 0.0176
    for w, h in ((1920, 1080), (3840, 2160), (2560, 1440)):
        assert leg.letterbox_text_height(phi, w, h) == pytest.approx(phi * 607.5, abs=0.01)


def test_t12_ratio_between_modes():
    """兩種模式的字級比為 1920/607.5 = 3.16 倍。"""
    assert 1920 / 607.5 == pytest.approx(3.16, abs=0.01)


def test_t12_required_zoom():
    assert leg.required_zoom(0.0108, 28) == pytest.approx(0.7406, abs=1e-3)
    assert leg.required_zoom(0.0108, 40) == pytest.approx(0.5184, abs=1e-3)


@pytest.mark.parametrize("name,phi,w,h,area_min,area_comfy", [
    ("powershell", 0.0172, 1728, 812, 0.264, 0.180),
    ("ide_dark", 0.0108, 1920, 1020, 0.164, 0.080),
    ("mobile", 0.0054, 1080, 2400, 0.171, 0.084),
])
def test_measured_phi_crop_areas(name, phi, w, h, area_min, area_comfy):
    """§5.6.2.1：以 v1.6 實測 phi 重現可見面積。"""
    assert leg.evaluate(phi, w, h, target_px=leg.H_MIN).visible_area == pytest.approx(area_min, abs=0.01)
    assert leg.evaluate(phi, w, h, target_px=leg.H_COMFY).visible_area == pytest.approx(area_comfy, abs=0.01)


def test_ide_fails_letterbox():
    """真實 IDE 錄影在 letterbox 下僅 6.2px，遠低於門檻（R-16）。"""
    h = leg.letterbox_text_height(0.0108, 1920, 1020)
    assert h == pytest.approx(6.2, abs=0.1)
    assert leg.verdict_of(h) == "fail"


def test_portrait_taller_no_magnification():
    """§4.3 直式來源：填入 1080x1920 時縮放比為 1.0，文字完全不放大（R-16）。

    來源 1080x2400 的寬度已與目標相符，cover 縮放比 = max(1080/1080, 1920/2400)
    = 1.0，只是垂直裁切。實測手機素材 13px 進、13px 出。
    """
    src_w, src_h, phi = 1080, 2400, 0.0054
    cover_scale = max(leg.OUT_W / src_w, leg.OUT_H / src_h)
    assert cover_scale == pytest.approx(1.0)
    text_in = phi * src_h                      # 原始字高 12.96px
    assert text_in * cover_scale == pytest.approx(text_in)   # 進出相同
    assert leg.verdict_of(text_in * cover_scale) == "fail"


def test_portrait_taller_letterbox_is_worse():
    """對照：若改用 letterbox（contain），文字反而縮小至 0.8 倍。"""
    h = leg.letterbox_text_height(0.0054, 1080, 2400)
    assert h == pytest.approx(0.0054 * 2400 * 0.8, abs=0.01)
    assert h < 0.0054 * 2400


def test_verdict_thresholds():
    assert leg.verdict_of(45) == "ok"
    assert leg.verdict_of(30) == "marginal"
    assert leg.verdict_of(20) == "fail"


# ---------------------------------------------------------------- T-11
def _ui_frame(w=1280, h=720, dark=False):
    """合成 UI：純色底 + 軸向矩形 + 多行等高文字。"""
    bg, fg = ((28, 28, 28), (225, 225, 225)) if dark else ((246, 246, 246), (32, 32, 32))
    img = np.full((h, w, 3), bg, np.uint8)
    cv2.rectangle(img, (0, 0), (w, 30), (70, 70, 70), -1)
    cv2.rectangle(img, (0, 30), (210, h), (int(bg[0]*0.95),)*3, -1)
    for i in range(16):
        cv2.putText(img, "def process(idx, cfg): return frame[idx]",
                    (230, 70 + i * 34), cv2.FONT_HERSHEY_SIMPLEX, 0.55, fg, 1, cv2.LINE_AA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _natural_frame(w=1280, h=720):
    """合成自然影像：平滑漸層 + 雜訊 + 不規則橢圓。"""
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.sin(xx / 80) * 40 + np.cos(yy / 60) * 40 + 128
    img = np.dstack([base * 0.9, base, base * 1.1]).clip(0, 255).astype(np.uint8)
    for _ in range(8):
        cv2.ellipse(img, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                    (int(rng.integers(50, 160)), int(rng.integers(40, 120))),
                    float(rng.integers(0, 180)), 0, 360,
                    tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    img = cv2.GaussianBlur(img, (7, 7), 0)
    img = np.clip(img.astype(np.int16) + rng.normal(0, 8, img.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def test_t11_direction_only_ui_above_natural():
    """T-11：僅驗證方向——UI 的文字密度高於自然影像。不驗證門檻。"""
    ui, nat = _ui_frame(), _natural_frame()
    d_ui, _, _ = clf.text_density(ui)
    d_nat, _, _ = clf.text_density(nat)
    assert d_ui > d_nat


def test_mser_min_area_matters():
    """§5.6.3.9：預設 min_area=60 會漏掉密集小字。"""
    ui = _ui_frame()
    n_default = len(clf.components(ui, min_area=60))
    n_fixed = len(clf.components(ui, min_area=15))
    assert n_fixed > n_default


def test_collinear_filter_reduces_but_preserves():
    """共線過濾是軟判別器：兩類都被壓低，UI 保留比例應高於自然影像。"""
    ui, nat = _ui_frame(), _natural_frame()
    ui_c, ui_raw, _ = clf.text_density(ui, collinear=True)
    nat_c, nat_raw, _ = clf.text_density(nat, collinear=True)
    r_ui = ui_c / ui_raw if ui_raw else 0
    r_nat = nat_c / nat_raw if nat_raw else 0
    assert r_ui > r_nat


def test_classify_refuses_when_not_calibrated():
    """§5.6.3.5：門檻未校準時拒絕自動分類，不以猜測值運行。"""
    r = clf.classify([_ui_frame()], threshold=-1)
    assert r.content_class == "unknown"
    assert r.signals.get("reason") == "threshold_not_calibrated"


def test_classify_aggregates_over_frames():
    """§5.6.3.2：素材層級聚合，回報使用的幀數。"""
    r = clf.classify([_ui_frame(), _ui_frame(dark=True), _natural_frame()])
    assert r.aggregated_over_frames == 3
    assert r.method == "pixel"


def test_classify_empty_input():
    assert clf.classify([]).content_class == "unknown"
