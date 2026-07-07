"""transcribe_meeting pipeline 单元测试。"""

from __future__ import annotations

import pytest

from iris.app.transcribe_meeting.pipeline import TranscribeMeetingPipeline
from iris.llm import LLMProviderError


class TestCalcDuration:
    """_calc_duration: 从转写文本时间戳计算时长。"""

    def test_normal_timestamps(self):
        # 0分5秒 → 30分0秒
        transcript = "说话人  00:00:05 内容\n说话人  00:30:00 结束"
        result = TranscribeMeetingPipeline._calc_duration(transcript)
        assert "29" in result or "30" in result

    def test_empty_transcript(self):
        assert TranscribeMeetingPipeline._calc_duration("") == ""

    def test_no_timestamps(self):
        assert TranscribeMeetingPipeline._calc_duration("这是纯文本没有时间戳，没有冒号格式") == ""

    def test_hours_and_minutes(self):
        transcript = "00:00:00 开始\n01:25:00 结束"
        result = TranscribeMeetingPipeline._calc_duration(transcript)
        assert "1" in result and ("25" in result or "小时" in result)

    def test_less_than_two_timestamps(self):
        assert TranscribeMeetingPipeline._calc_duration("00:05:00 只有一个时间戳") == ""

    def test_first_ge_last_returns_empty(self):
        # 倒序时间戳
        transcript = "00:30:00 结束\n00:00:00 开始"
        # 首时间戳>=末时间戳时返回空
        result = TranscribeMeetingPipeline._calc_duration(transcript)
        # 此时 first_ts=1800, last_ts=0, first>=last → ""
        assert result == ""


class TestFormatMeetingDate:
    """_format_meeting_date: 将8位日期格式化。"""

    def test_standard_format(self):
        d = TranscribeMeetingPipeline._format_meeting_date("20260702")
        assert d == "2026-07-02"

    def test_empty_string(self):
        assert TranscribeMeetingPipeline._format_meeting_date("") == ""

    def test_non_digit(self):
        assert TranscribeMeetingPipeline._format_meeting_date("2026-07-02") == ""

    def test_wrong_length(self):
        assert TranscribeMeetingPipeline._format_meeting_date("202607") == ""


class TestEnsureFooter:
    """_ensure_footer: 尾注追加逻辑。"""

    def test_already_has_footer(self):
        text = "## 会议内容\n内容\n\n---\n*生成说明：基于 2026-07-01 录音转写生成*"
        result = TranscribeMeetingPipeline._ensure_footer(text, "2026-07-01", "2026-07-01")
        assert result.count("生成说明") == 1

    def test_missing_footer_appended(self):
        text = "## 会议内容\n内容"
        result = TranscribeMeetingPipeline._ensure_footer(text, "2026-07-01", "2026-07-02")
        assert "生成说明" in result
        assert "2026-07-01" in result

    def test_not_duplicated(self):
        text = "内容\n\n---\n*生成说明：已存在*"
        result = TranscribeMeetingPipeline._ensure_footer(text, "2026-01-01", "2026-01-01")
        assert result.count("生成说明") == 1


class TestCallLlmErrorHandling:
    """_call_llm: LLM 失败时返回 fallback 而非 crash。"""

    def test_llm_failure_returns_fallback(self, config_bundle):
        from iris.llm import LLMProviderError
        from iris.llm.service import GenerationResult

        pipeline = TranscribeMeetingPipeline(config_bundle)

        class RaisingLLMService:
            def generate(self, prompt, route_context=None, *, temperature=None,
                         max_tokens=None, max_retries=None, force_model=None):
                raise LLMProviderError("fake LLM failure")

        pipeline._llm = RaisingLLMService()

        result = pipeline._call_llm(
            raw_transcript="测试转写文本",
            wiki_context="",
            meeting_type="项目会",
        )
        assert "LLM 生成失败" in result or "LLM 不可用" in result
        assert "测试转写文本" in result

