"""ASR 覆盖分析 — 单元测试。"""

from iris.wiki.asr._types import CoverageReport, DictQualityReport, AsrTerm
from iris.wiki.asr.coverage import (
    analyze_dict_quality,
    render_coverage_text,
    render_dict_quality_text,
    _is_noise_word,
    _count_chinese,
)


class TestNoiseDetection:
    def test_pure_digits_are_noise(self):
        is_noise, reason = _is_noise_word("123")
        assert is_noise

    def test_length_limit_matches_tokenization(self):
        """长度门槛必须与 tokenization.MAX_HOTWORD_* 一致。

        审计曾自行用 >12 汉字判超长，比 VocoType 的 ≤10 松，于是 11–12 汉字的词
        能过审计、到部署时才被拒。
        """
        from iris.utils.tokenization import MAX_HOTWORD_CHINESE, MAX_HOTWORD_TOTAL

        assert MAX_HOTWORD_CHINESE == 10 and MAX_HOTWORD_TOTAL == 20
        assert _is_noise_word("十" * MAX_HOTWORD_CHINESE)[0] is False
        assert _is_noise_word("十" * (MAX_HOTWORD_CHINESE + 1))[0] is True
        assert _is_noise_word("a" * MAX_HOTWORD_TOTAL)[0] is False
        assert _is_noise_word("a" * (MAX_HOTWORD_TOTAL + 1))[0] is True

    def test_eleven_chinese_chars_reported_as_long(self):
        """long_words 与噪音判定共用同一门槛，不能一个 10 一个 12。"""
        from iris.wiki.asr.coverage import analyze_coverage

        report = analyze_coverage(["十" * 11], [])
        assert report.long_words == ["十" * 11]
        assert any("超长词" in w for w in report.noise_words)

    def test_short_english_is_noise(self):
        is_noise, reason = _is_noise_word("AI")
        assert is_noise
        assert "超短英文" in reason

    def test_long_chinese_is_noise(self):
        is_noise, reason = _is_noise_word("这是一个超过十二个汉字的测试文本字符串")
        assert is_noise

    def test_common_noise_word_is_noise(self):
        is_noise, reason = _is_noise_word("成功")
        assert is_noise

    def test_valid_hotword_passes(self):
        is_noise, reason = _is_noise_word("李蕾")
        assert not is_noise

    def test_short_valid_name_passes(self):
        is_noise, reason = _is_noise_word("数据湖")
        assert not is_noise

    def test_sentence_fragment_is_noise(self):
        # 超长 + 含"的""是""在"等连接词
        is_noise, reason = _is_noise_word("我们团队的核心技术方案和落地方案")
        assert is_noise


class TestChineseCount:
    def test_pure_chinese(self):
        assert _count_chinese("你好世界") == 4

    def test_mixed(self):
        assert _count_chinese("hello世界123") == 2

    def test_no_chinese(self):
        assert _count_chinese("hello world 123") == 0


class TestCoverageReport:
    def test_empty_report(self):
        report = CoverageReport()
        assert report.hotword_count == 0
        assert report.slot_efficiency == 0.0

    def test_default_slots(self):
        report = CoverageReport()
        assert report.max_slots == 500


class TestRenderCoverageText:
    def test_minimal_report(self):
        report = CoverageReport(hotword_count=100)
        text = render_coverage_text(report)
        assert "100/500" in text
        assert "人物覆盖" in text


class TestDictQuality:
    def test_no_rules(self):
        terms = []
        report = analyze_dict_quality(terms)
        assert report.total_rules == 0

    def test_format_error_detection(self):
        terms = [
            AsrTerm(term="张三", category="person", context="",
                    mis_asr=["张山", "张珊(误为章三)"]),
        ]
        report = analyze_dict_quality(terms)
        assert report.total_rules == 2
        assert len(report.format_errors) == 1
        assert "张珊(误为章三)" in report.format_errors[0]

    def test_conflict_detection(self):
        terms = [
            AsrTerm(term="张三", category="person", context="",
                    mis_asr=["张山"]),
            AsrTerm(term="章三", category="person", context="",
                    mis_asr=["张山"]),  # 同一误识别 → 两个正确词
        ]
        report = analyze_dict_quality(terms)
        assert len(report.conflicting_pairs) > 0

    def test_category_distribution(self):
        terms = [
            AsrTerm(term="张三", category="person", context="",
                    mis_asr=["张山", "章三"]),
            AsrTerm(term="数据湖", category="concept", context="",
                    mis_asr=["数据仓库"]),
        ]
        report = analyze_dict_quality(terms)
        assert "person" in report.category_distribution
        assert report.category_distribution["person"] == 2


class TestRenderDictQualityText:
    def test_basic(self):
        report = DictQualityReport(total_rules=10)
        text = render_dict_quality_text(report)
        assert "10" in text
