"""scripts/sync_memory.py — 单元测试（Claude Code 系统记忆 → Iris 长期记忆同步）。

脚本非包，按 tests/test_weekly_report_extract.py 的做法以文件路径加载。
所有用例只测真实行为：frontmatter 解析、分类规则、内容提取、去重匹配、
_SyncState 状态累积，以及 run_sync 在 tmp_path 下的端到端落盘。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "sync_memory.py"
_spec = importlib.util.spec_from_file_location("sync_memory", _SCRIPT)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)


# ── 测试素材 ────────────────────────────────────────────────

CORRECTION_MD = """---
name: document-signature-rule
description: 文档签名规则
type: feedback
sync_to_iris: true
iris_target: corrections
---
# 文档签名规则

**Why:**
用户要求每份文档末尾统一签名。

**How to apply:**
输出文档时追加签名行。
"""

LIKES_MD = """---
name: answer-style
description: 回答风格偏好
type: feedback
---
# 回答风格

我喜欢简洁直接的回答。我喜欢先给结论。
"""

SKIP_MD = """---
name: some-project
description: 项目上下文
type: project
sync_to_iris: false
---
# 项目

这是不应同步的项目记忆。
"""


def _write_md(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(text, encoding="utf-8")
    return p


# ── 系统记忆路径 ────────────────────────────────────────────

class TestSystemMemoryDir:
    def test_slug_derived_from_resolved_path(self, tmp_path):
        result = sm._system_memory_dir(tmp_path)
        slug = "-" + str(tmp_path.resolve()).lstrip("/").replace("/", "-")
        assert result == Path.home() / ".claude" / "projects" / slug / "memory"


# ── Frontmatter 解析 ───────────────────────────────────────

class TestParseFrontmatter:
    def test_standard_frontmatter(self):
        fm = sm._parse_frontmatter(CORRECTION_MD)
        assert fm["name"] == "document-signature-rule"
        assert fm["description"] == "文档签名规则"
        assert fm["type"] == "feedback"
        assert fm["sync_to_iris"] is True
        assert fm["iris_target"] == "corrections"

    def test_no_frontmatter_returns_empty(self):
        assert sm._parse_frontmatter("# 标题\n正文") == {}

    def test_unterminated_frontmatter_returns_empty(self):
        assert sm._parse_frontmatter("---\nname: x\n") == {}

    def test_missing_optional_fields(self):
        fm = sm._parse_frontmatter("---\nname: only\n---\nbody")
        assert fm["name"] == "only"
        assert fm["description"] == ""
        assert fm["type"] == ""
        assert fm["sync_to_iris"] is None
        assert fm["iris_target"] == ""

    def test_quotes_stripped(self):
        fm = sm._parse_frontmatter('---\nname: "quoted"\ndescription: \'单引号\'\n---\n')
        assert fm["name"] == "quoted"
        assert fm["description"] == "单引号"

    def test_type_inside_metadata_block(self):
        text = (
            "---\n"
            "name: nested\n"
            "description: 嵌套写法\n"
            "metadata:\n"
            "  type: reference\n"
            "  sync_to_iris: true\n"
            "  iris_target: profile_notes\n"
            "---\n"
            "正文\n"
        )
        fm = sm._parse_frontmatter(text)
        assert fm["type"] == "reference"
        assert fm["sync_to_iris"] is True
        assert fm["iris_target"] == "profile_notes"


class TestExtractBody:
    def test_without_frontmatter_returns_text(self):
        assert sm._extract_body("纯正文") == "纯正文"

    def test_strips_frontmatter(self):
        body = sm._extract_body("---\nname: x\n---\n正文内容\n")
        assert body == "\n正文内容\n"

    def test_unterminated_frontmatter_returns_text(self):
        text = "---\nname: x\n"
        assert sm._extract_body(text) == text


# ── 分类规则 ────────────────────────────────────────────────

class TestClassify:
    def test_sync_false_never_syncs(self):
        assert sm._classify({"sync_to_iris": False, "type": "feedback"}, "我喜欢X。") is None

    def test_project_type_skipped(self):
        assert sm._classify({"type": "project"}, "任意正文") is None

    def test_explicit_iris_target_wins(self):
        assert sm._classify({"sync_to_iris": True, "iris_target": "corrections", "type": "project"}, "") == "corrections"

    def test_sync_true_without_target_falls_back_to_rules(self):
        assert sm._classify({"sync_to_iris": True, "iris_target": "", "type": "user"}, "") == "profile"

    def test_user_type_is_profile(self):
        assert sm._classify({"type": "user"}, "") == "profile"

    def test_reference_type_is_profile_notes(self):
        assert sm._classify({"type": "reference"}, "") == "profile_notes"

    def test_feedback_with_correction_keyword(self):
        assert sm._classify({"type": "feedback"}, "这里需要纠正一个说法。") == "corrections"
        assert sm._classify({"type": "feedback"}, "作业域指的是履约作业域。") == "corrections"

    def test_feedback_with_like_sentence(self):
        assert sm._classify({"type": "feedback"}, "我喜欢简洁。") == "profile_likes"

    def test_feedback_with_dislike_sentence(self):
        assert sm._classify({"type": "feedback"}, "我不喜欢冗长。") == "profile_dislikes"

    def test_feedback_default_is_profile_notes(self):
        assert sm._classify({"type": "feedback"}, "普通说明文字。") == "profile_notes"

    def test_unknown_type_not_synced(self):
        assert sm._classify({"type": ""}, "") is None
        assert sm._classify({}, "") is None


# ── 内容提取 ────────────────────────────────────────────────

class TestConceptOverlap:
    def test_bidirectional_containment(self):
        assert sm._concept_overlap("文档签名规则说明", "文档签名规则") is True
        assert sm._concept_overlap("签名规则", "文档签名规则") is True

    def test_char_overlap_at_least_60_percent(self):
        # 非子串关系，但字符集合重叠率 100%
        assert sm._concept_overlap("飞书操作的两条核心规则", "飞书操作规则") is True
        # 5 字 vs 4 字，公共 3 字：3/4 = 0.75
        assert sm._concept_overlap("甲乙丙丁戊", "甲乙丙己") is True

    def test_char_overlap_below_60_percent(self):
        # 公共 2 字 / 4 = 0.5
        assert sm._concept_overlap("会议纪要", "会议记录") is False
        assert sm._concept_overlap("甲乙丙丁戊", "甲己庚辛") is False

    def test_too_few_chinese_chars(self):
        assert sm._concept_overlap("甲", "甲乙") is False
        assert sm._concept_overlap("hello rule", "文档签名") is False

    def test_empty_strings(self):
        assert sm._concept_overlap("", "文档") is False
        assert sm._concept_overlap("文档", "") is False


class TestExtractLikesDislikes:
    BODY = "我喜欢简洁回答。我不喜欢长篇大论；我喜欢简洁回答。我偏好先给结论；我不希望重复解释。"

    def test_extract_likes_dedup_and_multiple_verbs(self):
        assert sm._extract_likes({}, self.BODY) == ["简洁回答", "先给结论"]

    def test_extract_dislikes_dedup_and_multiple_verbs(self):
        assert sm._extract_dislikes({}, self.BODY) == ["长篇大论", "重复解释"]

    def test_overlong_items_dropped(self):
        long_val = "很" * 40
        assert sm._extract_likes({}, f"我喜欢{long_val}。我喜欢短。") == ["短"]
        assert sm._extract_dislikes({}, f"我不喜欢{long_val}。我不喜欢短。") == ["短"]

    def test_likes_from_description_with_hobby_keyword(self):
        likes = sm._extract_likes({"description": "爱好：跑步、阅读"}, "")
        assert likes == ["爱好：跑步", "阅读"]

    def test_no_match_returns_empty(self):
        assert sm._extract_likes({}, "没有偏好句。") == []
        assert sm._extract_dislikes({}, "没有偏好句。") == []


class TestExtractNote:
    def test_why_section_first_paragraph_truncated(self):
        """有 **Why:** 时取其后首段（"\\n\\n" 前）并截 500。

        注：实现按偏移 7 切片而标记长 8，首字符残留一个 "*"，此处只断言语义内容。
        """
        body = "# 标题\n\n**Why:**\n用户明确要求。\n\n**How to apply:**\n照做。\n"
        note = sm._extract_note({"description": "d"}, body)
        assert note.endswith("用户明确要求。")
        assert "How to apply" not in note
        assert "照做" not in note

    def test_why_section_capped_at_500(self):
        body = "**Why:**\n" + "长" * 800
        note = sm._extract_note({}, body)
        assert len(note) <= 500

    def test_without_why_uses_description_and_first_line(self):
        body = "# 标题\n\n第一行内容\n第二行\n"
        assert sm._extract_note({"description": "参考规则"}, body) == "参考规则：第一行内容"

    def test_without_why_and_description_uses_first_line(self):
        assert sm._extract_note({}, "# 标题\n第一行\n") == "第一行"

    def test_empty_body_without_description(self):
        assert sm._extract_note({}, "") == ""


class TestExtractPersona:
    def test_bold_bullet_line_preferred(self):
        body = "# 身份\n- **数据智能部负责人**：负责数据与质检\n- **其他**：x\n"
        assert sm._extract_persona({"description": "d"}, body) == "数据智能部负责人：负责数据与质检"

    def test_fallback_to_description(self):
        assert sm._extract_persona({"description": "描述文字"}, "普通正文") == "描述文字"


class TestExtractWhyAndHow:
    def test_joins_why_and_how(self):
        body = "**Why:**\n原因A\n\n**How to apply:**\n做法B\n"
        assert sm._extract_why_and_how(body) == "原因A；做法B"

    def test_only_why(self):
        assert sm._extract_why_and_how("**Why:**\n仅原因\n") == "仅原因"

    def test_fallback_first_content_line(self):
        assert sm._extract_why_and_how("# 标题\n\n实质内容\n更多\n") == "实质内容"


class TestExtractCorrectionEntry:
    def test_concept_from_name_and_preferred_prefixed_by_description(self):
        fm = sm._parse_frontmatter(CORRECTION_MD)
        body = sm._extract_body(CORRECTION_MD)
        concept, entry = sm._extract_correction_entry(fm, body)
        assert concept == "document signature rule"
        assert entry["preferred"] == "文档签名规则：用户要求每份文档末尾统一签名。；输出文档时追加签名行。"
        assert entry["update_count"] == 1
        assert entry["last_source"] == "document-signature-rule"
        assert entry["updated_at"]

    def test_overlong_name_falls_back_to_description(self):
        fm = {"name": "x" * 50, "description": "短描述"}
        concept, _ = sm._extract_correction_entry(fm, "正文")
        assert concept == "短描述"

    def test_description_already_in_preferred_not_duplicated(self):
        fm = {"name": "n", "description": "核心"}
        _, entry = sm._extract_correction_entry(fm, "核心内容说明")
        assert entry["preferred"] == "核心内容说明"


# ── 去重匹配 ────────────────────────────────────────────────

class TestFindExistingCorrection:
    def test_exact_concept_hit(self):
        items = {"文档签名规则": {"preferred": "p"}}
        existing, concept, dedup = sm._find_existing_correction(items, {"name": "x"}, "文档签名规则")
        assert existing is items["文档签名规则"]
        assert concept == "文档签名规则"
        assert dedup is False

    def test_last_source_contains_kebab_slug(self):
        items = {"文档签名规则": {"preferred": "p", "last_source": "合并自: a, document-signature-rule, b"}}
        fm = {"name": "document-signature-rule", "description": ""}
        existing, concept, dedup = sm._find_existing_correction(items, fm, "document signature rule")
        assert existing is items["文档签名规则"]
        assert concept == "文档签名规则"
        assert dedup is True

    def test_last_source_contains_spaced_slug(self):
        items = {"文档签名规则": {"preferred": "p", "last_source": "document signature rule"}}
        fm = {"name": "document-signature-rule", "description": ""}
        existing, concept, dedup = sm._find_existing_correction(items, fm, "document signature rule")
        assert existing is items["文档签名规则"]
        assert concept == "文档签名规则"
        assert dedup is True

    def test_description_overlaps_existing_chinese_concept(self):
        items = {"飞书操作规则": {"preferred": "p", "last_source": "其它来源"}}
        fm = {"name": "feishu-ops", "description": "飞书操作的两条核心规则"}
        existing, concept, dedup = sm._find_existing_correction(items, fm, "feishu ops")
        assert existing is items["飞书操作规则"]
        assert concept == "飞书操作规则"
        assert dedup is True

    def test_no_match(self):
        items = {"无关概念": {"preferred": "p", "last_source": "zzz"}}
        fm = {"name": "brand-new", "description": "completely new"}
        assert sm._find_existing_correction(items, fm, "brand new") == ({}, "brand new", False)


# ── _SyncState ──────────────────────────────────────────────

class TestSyncState:
    def _state(self):
        return sm._SyncState({}, {}, scanned=3)

    def test_init_creates_structure_and_stats(self):
        st = self._state()
        assert st.profile["user_preferences"] == {"likes": [], "dislikes": [], "notes": []}
        assert st.corrections["items"] == {}
        assert st.stats["scanned"] == 3
        assert st.has_changes is False

    def test_add_likes_dedup_and_detail(self):
        st = self._state()
        st.add_likes(["a", "b", "a"])
        assert st.stats["profile_likes_added"] == 2
        assert st.stats["details"] == ["新增偏好(喜欢): a", "新增偏好(喜欢): b"]
        st.add_likes(["a"], detail=False)
        assert st.stats["profile_likes_added"] == 2

    def test_add_likes_without_detail(self):
        st = self._state()
        st.add_likes(["a"], detail=False)
        assert st.stats["profile_likes_added"] == 1
        assert st.stats["details"] == []

    def test_add_dislikes_dedup_and_detail(self):
        st = self._state()
        st.add_dislikes(["x", "x", "y"])
        assert st.stats["profile_dislikes_added"] == 2
        assert st.stats["details"] == ["新增偏好(避免): x", "新增偏好(避免): y"]
        st.add_dislikes(["z"], detail=False)
        assert st.stats["profile_dislikes_added"] == 3
        assert len(st.stats["details"]) == 2

    def test_add_note_dedup_and_empty(self):
        st = self._state()
        st.add_note("备注一")
        st.add_note("备注一")
        st.add_note("")
        assert st.stats["profile_notes_added"] == 1
        assert st.notes == ["备注一"]
        assert st.stats["details"] == ["新增备注: 备注一"]

    def test_set_persona_first_time_and_repeat(self):
        st = self._state()
        st.set_persona("人设A")
        assert st.persona_updated is True
        assert st.profile["iris_persona"]["description"] == "人设A"
        assert st.stats["details"] == ["更新 Iris 人设"]
        st.set_persona("人设A")
        assert st.stats["details"] == ["更新 Iris 人设"]
        st.set_persona("")
        assert st.stats["details"] == ["更新 Iris 人设"]

    def test_existing_persona_same_value_not_updated(self):
        st = sm._SyncState({"iris_persona": {"description": "已有"}}, {}, scanned=0)
        st.set_persona("已有")
        assert st.persona_updated is False

    @pytest.mark.parametrize("field", [
        "profile_likes_added", "profile_dislikes_added", "profile_notes_added",
        "corrections_added", "corrections_updated",
    ])
    def test_has_changes_each_stat_component(self, field):
        st = self._state()
        st.stats[field] = 1
        assert st.has_changes is True

    def test_has_changes_persona_component(self):
        st = self._state()
        st.persona_updated = True
        assert st.has_changes is True

    def test_finalize_writes_sorted_and_timestamps(self):
        st = self._state()
        st.add_likes(["b", "a"], detail=False)
        st.add_dislikes(["y", "x"], detail=False)
        st.add_note("n")
        st.finalize()
        prefs = st.profile["user_preferences"]
        assert prefs["likes"] == ["a", "b"]
        assert prefs["dislikes"] == ["x", "y"]
        assert prefs["notes"] == ["n"]
        assert st.profile["updated_at"]
        assert st.corrections["updated_at"] == st.profile["updated_at"]


# ── run_sync 端到端 ─────────────────────────────────────────

class TestRunSync:
    def test_missing_system_dir_returns_error(self, tmp_path):
        result = sm.run_sync(tmp_path / "nope", tmp_path / "iris")
        assert "error" in result
        assert "系统记忆目录不存在" in result["error"]

    def test_empty_system_dir(self, tmp_path):
        sys_dir = tmp_path / "sys"
        sys_dir.mkdir()
        result = sm.run_sync(sys_dir, tmp_path / "iris")
        assert result == {"synced": False, "reason": "系统记忆目录为空"}

    def _populate(self, sys_dir: Path) -> None:
        _write_md(sys_dir, "document-signature-rule.md", CORRECTION_MD)
        _write_md(sys_dir, "answer-style.md", LIKES_MD)
        _write_md(sys_dir, "some-project.md", SKIP_MD)

    def test_three_files_end_to_end_writes_memory(self, tmp_path):
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        self._populate(sys_dir)

        stats = sm.run_sync(sys_dir, iris_dir)

        assert stats["scanned"] == 3
        assert stats["skipped"] == 1
        assert stats["corrections_added"] == 1
        assert stats["corrections_updated"] == 0
        assert stats["profile_likes_added"] >= 1
        assert stats["synced"] is True
        assert stats["dry_run"] is False
        assert "新增纠正: document signature rule" in stats["details"]

        profile_path = iris_dir / "long_term" / "profile.json"
        corrections_path = iris_dir / "long_term" / "corrections.json"
        assert profile_path.exists() and corrections_path.exists()

        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        assert profile["user_preferences"]["likes"] == ["先给结论", "简洁直接的回答"]
        assert profile["updated_at"]

        corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
        entry = corrections["items"]["document signature rule"]
        assert entry["preferred"].startswith("文档签名规则：用户要求每份文档末尾统一签名。")
        assert entry["update_count"] == 1
        assert entry["last_source"] == "document-signature-rule"

    def test_dry_run_does_not_write(self, tmp_path):
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        self._populate(sys_dir)

        stats = sm.run_sync(sys_dir, iris_dir, dry_run=True)

        assert stats["synced"] is True
        assert stats["dry_run"] is True
        assert stats["corrections_added"] == 1
        assert not (iris_dir / "long_term").exists()

    def test_second_run_same_content_is_idempotent(self, tmp_path):
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        self._populate(sys_dir)
        sm.run_sync(sys_dir, iris_dir)
        before = (iris_dir / "long_term" / "corrections.json").read_text(encoding="utf-8")

        stats = sm.run_sync(sys_dir, iris_dir)

        assert stats["corrections_added"] == 0
        assert stats["corrections_updated"] == 0
        assert stats["profile_likes_added"] == 0
        assert stats["synced"] is False
        assert (iris_dir / "long_term" / "corrections.json").read_text(encoding="utf-8") == before

    def test_dedup_longer_existing_chinese_entry_skips_short_english_entry(self, tmp_path):
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        _write_md(sys_dir, "document-signature-rule.md", CORRECTION_MD)

        lt = iris_dir / "long_term"
        lt.mkdir(parents=True)
        existing_items = {
            "文档签名规则": {
                "preferred": "文档签名规则的完整表述：" + "细节" * 100,
                "update_count": 3,
                "updated_at": "2026-01-01T00:00:00",
                "last_source": "合并自: 会话挖掘, document-signature-rule",
            }
        }
        (lt / "corrections.json").write_text(
            json.dumps({"items": existing_items, "updated_at": None}, ensure_ascii=False), encoding="utf-8",
        )

        stats = sm.run_sync(sys_dir, iris_dir)

        assert stats["corrections_added"] == 0
        assert stats["corrections_updated"] == 0
        assert stats["synced"] is False
        after = json.loads((lt / "corrections.json").read_text(encoding="utf-8"))
        assert list(after["items"]) == ["文档签名规则"]
        assert after["items"]["文档签名规则"]["update_count"] == 3

    def test_exact_concept_with_changed_preferred_is_updated(self, tmp_path):
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        _write_md(sys_dir, "document-signature-rule.md", CORRECTION_MD)

        lt = iris_dir / "long_term"
        lt.mkdir(parents=True)
        (lt / "corrections.json").write_text(json.dumps({
            "items": {"document signature rule": {"preferred": "旧表述", "update_count": 2}},
            "updated_at": None,
        }, ensure_ascii=False), encoding="utf-8")

        stats = sm.run_sync(sys_dir, iris_dir)

        assert stats["corrections_added"] == 0
        assert stats["corrections_updated"] == 1
        assert "更新纠正: document signature rule" in stats["details"]
        after = json.loads((lt / "corrections.json").read_text(encoding="utf-8"))
        assert after["items"]["document signature rule"]["update_count"] == 3

    def test_profile_and_notes_targets(self, tmp_path):
        """user 类文件走 profile（人设 + 偏好，不记 details）；reference 类走备注。"""
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        _write_md(sys_dir, "iris-identity.md", (
            "---\nname: iris-identity\ndescription: Iris 身份\ntype: user\n---\n"
            "- **知识助理**：服务数据智能部\n\n我喜欢结构化输出。\n"
        ))
        _write_md(sys_dir, "ref.md", (
            "---\nname: ref\ndescription: 参考规则\ntype: reference\n---\n# 标题\n第一行内容\n"
        ))

        stats = sm.run_sync(sys_dir, iris_dir)

        assert stats["profile_likes_added"] == 1
        assert stats["profile_notes_added"] == 1
        assert stats["details"] == ["更新 Iris 人设", "新增备注: 参考规则：第一行内容"]
        profile = json.loads((iris_dir / "long_term" / "profile.json").read_text(encoding="utf-8"))
        assert profile["iris_persona"]["description"] == "知识助理：服务数据智能部"
        assert profile["user_preferences"]["likes"] == ["结构化输出"]
        assert profile["user_preferences"]["notes"] == ["参考规则：第一行内容"]

    def test_corrupt_existing_json_falls_back_to_default(self, tmp_path):
        sys_dir = tmp_path / "sys"
        iris_dir = tmp_path / "iris"
        _write_md(sys_dir, "answer-style.md", LIKES_MD)
        lt = iris_dir / "long_term"
        lt.mkdir(parents=True)
        (lt / "profile.json").write_text("{broken", encoding="utf-8")

        stats = sm.run_sync(sys_dir, iris_dir)

        assert stats["profile_likes_added"] == 2
        profile = json.loads((lt / "profile.json").read_text(encoding="utf-8"))
        assert profile["user_preferences"]["likes"] == ["先给结论", "简洁直接的回答"]


# ── 输出格式 ────────────────────────────────────────────────

class TestFormatPretty:
    def test_error_output(self):
        out = sm._format_pretty({"error": "系统记忆目录不存在: /x"})
        assert out.startswith("## 记忆同步")
        assert "错误：系统记忆目录不存在: /x" in out

    def test_no_changes_output_with_dry_run(self):
        out = sm._format_pretty({"synced": False, "scanned": 3, "skipped": 1, "dry_run": True})
        assert "扫描 3 个文件，无变更，跳过 1 个" in out
        assert "(dry-run 模式)" in out

    def test_no_changes_output_without_dry_run(self):
        out = sm._format_pretty({"synced": False, "reason": "系统记忆目录为空"})
        assert "扫描 0 个文件，无变更，跳过 0 个" in out
        assert "dry-run" not in out

    def test_changes_output(self):
        stats = {
            "synced": True, "dry_run": True, "scanned": 3, "skipped": 1,
            "profile_likes_added": 2, "profile_dislikes_added": 1, "profile_notes_added": 1,
            "corrections_added": 1, "corrections_updated": 1,
            "details": ["新增纠正: x", "新增偏好(喜欢): y"],
        }
        out = sm._format_pretty(stats)
        assert "扫描：3 个系统记忆文件" in out
        assert "跳过：1 个" in out
        assert "偏好(喜欢)：+2 条" in out
        assert "偏好(避免)：+1 条" in out
        assert "备注：+1 条" in out
        assert "纠正规则：+1 条" in out
        assert "纠正规则：Δ1 条" in out
        assert "(dry-run 模式，未实际写入)" in out
        assert "详情：" in out
        assert "· 新增纠正: x" in out
        assert "· 新增偏好(喜欢): y" in out


class TestBuildParser:
    def test_defaults(self):
        args = sm._build_parser().parse_args([])
        assert args.project_root == "."
        assert args.pretty is False
        assert args.dry_run is False

    def test_flags(self):
        args = sm._build_parser().parse_args(["--project-root", "/p", "--pretty", "--dry-run"])
        assert args.project_root == "/p"
        assert args.pretty is True
        assert args.dry_run is True
