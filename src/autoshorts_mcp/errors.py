# -*- coding: utf-8 -*-
"""§4.7 統一錯誤回傳規範。

所有工具的失敗路徑必須回傳結構化錯誤，不得丟出未處理例外或回傳自由格式
字串——LLM 需要機器可讀的錯誤才能自行決定重試或改道。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """§4.7 錯誤碼。前綴即分類。"""

    # INPUT_ 使用者輸入錯誤
    INPUT_DIR_NOT_FOUND = "INPUT_DIR_NOT_FOUND"
    INPUT_NO_MEDIA = "INPUT_NO_MEDIA"
    INPUT_FILE_NOT_FOUND = "INPUT_FILE_NOT_FOUND"

    # FFMPEG_ FFmpeg 相關
    FFMPEG_NOT_FOUND = "FFMPEG_NOT_FOUND"
    FFMPEG_ENCODER_UNAVAILABLE = "FFMPEG_ENCODER_UNAVAILABLE"
    FFMPEG_EXEC_FAILED = "FFMPEG_EXEC_FAILED"

    # AUDIO_ 音訊處理
    AUDIO_DOWNLOAD_BLOCKED = "AUDIO_DOWNLOAD_BLOCKED"
    AUDIO_TOO_SHORT = "AUDIO_TOO_SHORT"
    AUDIO_BEAT_DETECTION_FAILED = "AUDIO_BEAT_DETECTION_FAILED"

    # EDL_ 資料模型
    EDL_SCHEMA_INVALID = "EDL_SCHEMA_INVALID"
    EDL_TIMELINE_EXCEEDS_AUDIO = "EDL_TIMELINE_EXCEEDS_AUDIO"
    EDL_SOURCE_FILE_MISSING = "EDL_SOURCE_FILE_MISSING"

    # SOURCE_ 素材本身的限制（§5.6.2.1，幾何上無法補救）
    SOURCE_TEXT_TOO_SMALL = "SOURCE_TEXT_TOO_SMALL"
    SOURCE_ORIENTATION_UNSUPPORTED = "SOURCE_ORIENTATION_UNSUPPORTED"

    # RENDER_ 渲染管線
    RENDER_CHUNK_FAILED = "RENDER_CHUNK_FAILED"
    RENDER_CONCAT_MISMATCH = "RENDER_CONCAT_MISMATCH"
    RENDER_DISK_FULL = "RENDER_DISK_FULL"

    # CAPCUT_ 草稿輸出
    CAPCUT_NOT_INSTALLED = "CAPCUT_NOT_INSTALLED"
    CAPCUT_SCHEMA_UNSUPPORTED = "CAPCUT_SCHEMA_UNSUPPORTED"

    # CLASSIFY_ 素材分類（§5.6.3）
    CLASSIFY_NOT_CALIBRATED = "CLASSIFY_NOT_CALIBRATED"


# 可重試者：LLM 調整參數後重試可能成功；其餘需使用者介入
RETRYABLE = frozenset({
    ErrorCode.EDL_SCHEMA_INVALID,
    ErrorCode.EDL_TIMELINE_EXCEEDS_AUDIO,
    ErrorCode.EDL_SOURCE_FILE_MISSING,
    ErrorCode.RENDER_CHUNK_FAILED,
})


@dataclass
class ToolError:
    """§4.7 結構化錯誤。以 as_dict() 回傳給 MCP Client。"""

    code: ErrorCode
    message: str
    remediation: str = ""
    details: dict = field(default_factory=dict)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def as_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "remediation": self.remediation,
                "retryable": self.retryable,
                "details": self.details,
            },
        }


class AutoShortsError(Exception):
    """內部例外。跨越工具邊界前必須轉換為 ToolError。"""

    def __init__(self, code: ErrorCode, message: str,
                 remediation: str = "", **details: Any):
        super().__init__(message)
        self.error = ToolError(code, message, remediation, details)

    def as_dict(self) -> dict:
        return self.error.as_dict()


def ok(**payload: Any) -> dict:
    """成功回傳的統一包裝。"""
    return {"ok": True, **payload}
