"""实时会议助理 — 段分析器单元测试（LLM 用鸭子类型 Fake 封装）。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from iris.assistant._analyzer import SegmentAnalyzer
from iris.assistant.models import MeetingState, SegmentAnalysis, VoiceSegment


class _FakeLLM:
    """鸭子类型 .generate(prompt, route_context, **kw) -> .text"""

    def __init__(self, text: str = "", raise_on_generate: bool = False):
        self._text = text
        self.raise_on_generate = raise_on_generate
        self.calls: list[dict] = []

    def generate(self, prompt, route_context=None, **kwargs):
        self.calls.append({"prompt": prompt, "route_context": route_context, "kwargs": kwargs})
        if self.raise_on_generate:
            raise RuntimeError("fake LLM failure")
        return SimpleNamespace(text=self._text)


class _FakeLoader:
    def __init__(self, template: str = "TMPL {{segment_text}} | {{retrieval_context}} | {{meeting_summary}}"):
        self._template = template

    def render(self, name, variables):
        assert name == "meeting_live_analyze.md"
        rendered = self._template
        for key, value in variables.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered


def _analyzer(llm=None, model=""):
    return SegmentAnalyzer(llm or _FakeLLM(), _FakeLoader(), model=model)


class TestAnalyze:
    def test_valid_json(self):
        llm = _FakeLLM('{"key_points": ["要点A"], "risks": ["风险1"], '
                        '"questions": [], "decisions": ["决策X"], '
                        '"suggested_questions": ["追问？"]}')
        result = _analyzer(llm).analyze("段文本", "上下文", "摘要")
        assert isinstance(result, SegmentAnalysis)
        assert result.key_points == ["要点A"]
        assert result.risks == ["风险1"]
        assert result.questions == []
        assert [d.text for d in result.decisions] == ["决策X"]
        assert result.decisions[0].confidence == "proposed"  # 纯字符串默认 proposed
        assert result.suggested_questions == ["追问？"]

    def test_json_with_fence(self):
        llm = _FakeLLM('```json\n{"key_points": ["要点A"]}\n```')
        result = _analyzer(llm).analyze("段", "", "")
        assert result is not None
        assert result.key_points == ["要点A"]

    def test_non_json_returns_none(self):
        llm = _FakeLLM("这是纯文字，不是 JSON")
        assert _analyzer(llm).analyze("段", "", "") is None

    def test_raise_on_generate_returns_none(self):
        llm = _FakeLLM(raise_on_generate=True)
        assert _analyzer(llm).analyze("段", "", "") is None

    def test_normalize_missing_fields(self):
        llm = _FakeLLM('{"key_points": ["A"], "unexpected": 1}')
        result = _analyzer(llm).analyze("段", "", "")
        assert result is not None
        assert result.key_points == ["A"]
        assert result.risks == [] and result.decisions == []

    def test_normalize_wrong_types(self):
        llm = _FakeLLM('{"key_points": "not-a-list", "risks": [123, null, "ok"]}')
        result = _analyzer(llm).analyze("段", "", "")
        assert result is not None
        assert result.key_points == []
        assert result.risks == ["123", "ok"]  # 非 str 转 str，null 跳过

    def test_truncates_long_items(self):
        long_item = "长" * 500
        llm = _FakeLLM(f'{{"key_points": ["{long_item}"]}}')
        result = _analyzer(llm).analyze("段", "", "")
        assert len(result.key_points[0]) == 120

    def test_renders_all_three_variables(self):
        llm = _FakeLLM('{"key_points": []}')
        _analyzer(llm).analyze("段文本", "检索块", "摘要块")
        prompt = llm.calls[0]["prompt"]
        assert "段文本" in prompt and "检索块" in prompt and "摘要块" in prompt


class TestSummarize:
    """会议结束总结：成功返回 Markdown，失败返回 None（不阻塞退出）。"""

    def _sum_loader(self):
        class _SumLoader:
            def render(self, name, variables):
                assert name == "meeting_live_summary.md"
                rendered = "TMPL 累计={{meeting_summary}} | 转写={{transcript}}"
                for key, value in variables.items():
                    rendered = rendered.replace("{{" + key + "}}", str(value))
                return rendered
        return _SumLoader()

    def test_summarize_success(self):
        llm = _FakeLLM(text="## 会议主题\n本场会议讨论了目标")
        analyzer = SegmentAnalyzer(llm, self._sum_loader())
        state = MeetingState(started_at=datetime(2026, 8, 10, 12, 0))
        state.decisions = ["决策X"]
        seg = VoiceSegment(seq=1, started_at=datetime(2026, 8, 10, 12, 1),
                           raw_text="我们决定采用方案A", corrected_text="我们决定采用方案A")
        state.add_analysis(seg)
        result = analyzer.summarize(state)
        assert result == "## 会议主题\n本场会议讨论了目标"
        # 转写与累计都已渲染进 Prompt
        assert "我们决定采用方案A" in llm.calls[0]["prompt"]
        assert "决策: 决策X" in llm.calls[0]["prompt"]
        # 路由与实时参数
        assert llm.calls[0]["route_context"]["task_type"] == "meeting_summary"
        assert llm.calls[0]["kwargs"]["max_retries"] == 0

    def test_summarize_failure_returns_none(self):
        llm = _FakeLLM(raise_on_generate=True)
        analyzer = SegmentAnalyzer(llm, self._sum_loader())
        assert analyzer.summarize(MeetingState()) is None

    def test_summarize_empty_state(self):
        llm = _FakeLLM(text="（暂无内容）")
        analyzer = SegmentAnalyzer(llm, self._sum_loader())
        assert analyzer.summarize(MeetingState()) == "（暂无内容）"

    def test_summarize_empty_result_returns_none(self):
        llm = _FakeLLM(text="   ")
        analyzer = SegmentAnalyzer(llm, self._sum_loader())
        assert analyzer.summarize(MeetingState()) is None


class TestAnalyzeRouting:
    def test_route_context(self):
        llm = _FakeLLM('{"key_points": []}')
        _analyzer(llm).analyze("段", "", "")
        ctx = llm.calls[0]["route_context"]
        assert ctx["task_type"] == "meeting_analysis"
        assert ctx["use_case"] == "meeting_analysis"

    def test_force_model_passed(self):
        llm = _FakeLLM('{"key_points": []}')
        _analyzer(llm, model="custom-model").analyze("段", "", "")
        kwargs = llm.calls[0]["kwargs"]
        assert kwargs["force_model"] == "custom-model"

    def test_empty_model_no_force(self):
        llm = _FakeLLM('{"key_points": []}')
        _analyzer(llm).analyze("段", "", "")
        kwargs = llm.calls[0]["kwargs"]
        assert kwargs["force_model"] is None


class _SuggestLoader:
    """渲染 meeting_live_suggest.md 的 Fake loader。"""

    def __init__(self):
        self.rendered_vars: dict | None = None

    def render(self, name, variables):
        assert name == "meeting_live_suggest.md"
        self.rendered_vars = variables
        return "SUGGEST_PROMPT"


class TestSuggestQuestionsWithDecisions:
    """v3.28.1 回归：decisions 是 List[DecisionItem]，不得对其直接 join。

    历史 bug：`"；".join(analysis.decisions)` 对 Pydantic 模型列表抛 TypeError
    且在上层被静默吞——恰好在「存在决策点」（事件驱动触发建议的条件）时
    让建议提问功能整体失效；decisions 为空时 join 返回 "" 反而正常，测试难发现。
    """

    def _analysis_with_decisions(self):
        from iris.assistant.models import DecisionItem
        return SegmentAnalysis(
            key_points=["要点A"],
            decisions=[
                DecisionItem(text="采用方案B", confidence="tentative"),
                DecisionItem(text="下周上线", confidence="confirmed"),
            ],
        )

    def test_decisions_rendered_not_typeerror(self):
        """含决策点时必须正常生成（回归核心断言：不再 TypeError → []）。"""
        import time
        llm = _FakeLLM('["建议问一下上线风险？"]')
        loader = _SuggestLoader()
        analyzer = SegmentAnalyzer(llm, loader, model="")

        result = analyzer.suggest_questions(
            self._analysis_with_decisions(), "会议摘要", "检索上下文",
            deadline=time.monotonic() + 30,
        )

        assert result == ["建议问一下上线风险？"]
        assert loader.rendered_vars is not None, "prompt 必须真正渲染"
        # 决策文本 + 置信度图标进入 prompt
        assert "采用方案B" in loader.rendered_vars["decisions"]
        assert "下周上线" in loader.rendered_vars["decisions"]
        assert "✅" in loader.rendered_vars["decisions"]  # confirmed 图标
        assert "❓" in loader.rendered_vars["decisions"]  # tentative 图标

    def test_empty_decisions_placeholder(self):
        import time
        llm = _FakeLLM('[]')
        loader = _SuggestLoader()
        analyzer = SegmentAnalyzer(llm, loader, model="")

        analyzer.suggest_questions(
            SegmentAnalysis(key_points=["要点A"]), "摘要", "上下文",
            deadline=time.monotonic() + 30,
        )
        assert loader.rendered_vars["decisions"] == "（无）"

    def test_render_failure_degrades_to_empty(self):
        """渲染异常（在 try 内）降级返回 []，不上抛。"""
        import time

        class _BoomLoader:
            def render(self, name, variables):
                raise RuntimeError("render boom")

        analyzer = SegmentAnalyzer(_FakeLLM("[]"), _BoomLoader(), model="")
        result = analyzer.suggest_questions(
            self._analysis_with_decisions(), "摘要", "上下文",
            deadline=time.monotonic() + 30,
        )
        assert result == []
