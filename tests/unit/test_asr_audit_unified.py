"""ASR 审计统一实现 + 部署守卫 + corrector 路径归一 — 单元测试。

对应 2026-10-10 的三处收敛：
  1. asr-audit 命令与 daily-start 第 7 阶段合并为同一实现（原先两份各漂各的）；
  2. --deploy 合并手动词后加 500 条硬上限守卫（原先静默越界）；
  3. profile 里的相对路径归一为项目根绝对路径（原先以 CWD 为基准）。
"""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris.app.cli._handlers import _wiki
from iris.core.exceptions import IrisValueError
from iris.wiki.asr import audit as audit_mod


LOGGER = logging.getLogger("test-asr-audit")


def _bundle(wiki_root, root=None):
    return SimpleNamespace(wiki={"wiki_root": str(wiki_root)}, root=root)


class _FakePage:
    def __init__(self, title, page_type, path):
        self.title = title
        self.page_type = page_type
        self.path = path


@pytest.fixture
def wiki_root(tmp_path):
    """最小 Wiki：一个人物页 + 一个项目页。

    page_type 由 frontmatter 的 ``type`` 决定（不是目录），缺了会一律落成 domain。
    """
    root = tmp_path / "wiki"
    (root / "04-人物").mkdir(parents=True)
    (root / "03-项目").mkdir(parents=True)
    (root / "04-人物" / "人物-张三.md").write_text(
        "---\ntitle: 张三\ntype: person\nstatus: published\n---\n\n# 张三\n",
        encoding="utf-8")
    (root / "03-项目" / "项目-拍图验真.md").write_text(
        "---\ntitle: 拍图验真\ntype: project\nstatus: published\n---\n\n# 拍图验真\n",
        encoding="utf-8")
    return root


def _patch_audit_dir(monkeypatch, bak_dir):
    # asr_bak_dir 现接受可选 root，用 *a/**k 吸收
    monkeypatch.setattr(audit_mod, "asr_bak_dir", lambda *a, **k: bak_dir)


def _write_hotwords(bak_dir, words, name="asr-hotwords-20261010-010101.txt"):
    bak_dir.mkdir(parents=True, exist_ok=True)
    path = bak_dir / name
    path.write_text("\n".join(words) + "\n", encoding="utf-8")
    return path


class TestRunAsrAudit:
    def test_skipped_without_wiki(self):
        result = audit_mod.run_asr_audit(SimpleNamespace(wiki={}))
        assert result.payload["status"] == "skipped"

    def test_skipped_when_wiki_dir_missing(self, tmp_path):
        result = audit_mod.run_asr_audit(_bundle(tmp_path / "nope"))
        assert result.payload["status"] == "skipped"

    def test_skipped_when_no_artifacts(self, wiki_root, tmp_path, monkeypatch):
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        result = audit_mod.run_asr_audit(_bundle(wiki_root))
        assert result.payload["status"] == "skipped"
        # 「没有产物」不能渲染成「覆盖 0%」
        assert "build-asr-prompt" in result.payload["reason"]

    def test_reads_from_bak_dir_not_output_root(self, wiki_root, tmp_path, monkeypatch):
        bak = tmp_path / "bak"
        _write_hotwords(bak, ["张三", "拍图验真"])
        # output 根目录放一份「更新」的假产物，审计不应读到
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "asr-hotwords-99999999-000000.txt").write_text(
            "张三\n", encoding="utf-8")
        _patch_audit_dir(monkeypatch, bak)

        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        assert payload["status"] == "ok"
        assert payload["hotword_count"] == 2
        assert payload["sources"]["hotwords_file"] == str(
            bak / "asr-hotwords-20261010-010101.txt")

    def test_full_coverage_needs_no_update(self, wiki_root, tmp_path, monkeypatch):
        _write_hotwords(tmp_path / "bak", ["张三", "拍图验真"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        assert payload["issues"] == []
        assert payload["needs_update"] is False

    def test_coverage_gap_drives_needs_update(self, wiki_root, tmp_path, monkeypatch):
        _write_hotwords(tmp_path / "bak", ["张三"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        assert payload["needs_update"] is True
        assert any("项目未覆盖" in i for i in payload["issues"])
        assert "suggestion" in payload

    def test_noise_goes_to_warnings_not_issues(self, wiki_root, tmp_path, monkeypatch):
        """噪音词是「该手工清理」，不是「该重跑生成」，两者不能混在一个字段。"""
        _write_hotwords(tmp_path / "bak", ["张三", "拍图验真", "AI"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        assert payload["needs_update"] is False          # 覆盖完整
        assert payload["issues"] == []
        assert payload["warnings"]                      # 但有噪音待清理
        assert any("噪音词" in w for w in payload["warnings"])

    def test_dict_quality_reported(self, wiki_root, tmp_path, monkeypatch):
        bak = tmp_path / "bak"
        _write_hotwords(bak, ["张三", "拍图验真"])
        (bak / "asr-replace-dict-20261010-010101.json").write_text(
            json.dumps({"replace_map": {"检测板": "剪切板", "剪贴板": "剪切板"}},
                       ensure_ascii=False), encoding="utf-8")
        _patch_audit_dir(monkeypatch, bak)
        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        assert payload["dict_quality"]["total_rules"] == 2

    def test_corrupt_dict_does_not_abort_audit(self, wiki_root, tmp_path, monkeypatch):
        bak = tmp_path / "bak"
        _write_hotwords(bak, ["张三", "拍图验真"])
        (bak / "asr-replace-dict-20261010-010101.json").write_text("{ 坏 json",
                                                                   encoding="utf-8")
        _patch_audit_dir(monkeypatch, bak)
        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        assert payload["status"] == "ok"
        assert payload["dict_quality"]["total_rules"] == 0

    def test_payload_fields_cover_both_former_shapes(self, wiki_root, tmp_path, monkeypatch):
        """统一 payload 必须是两份旧实现的并集，否则总有一个消费方读到空。"""
        _write_hotwords(tmp_path / "bak", ["张三", "拍图验真"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        payload = audit_mod.run_asr_audit(_bundle(wiki_root)).payload
        for key in ("status", "hotword_count", "persons", "projects", "concepts",
                    "slot_efficiency", "noise_count", "issues", "needs_update",
                    "dict_quality", "sources"):
            assert key in payload, key


class TestDailyStartUsesSameImplementation:
    def test_daily_delegates_to_shared_audit(self, wiki_root, tmp_path, monkeypatch):
        from iris.app.cli._handlers import _system

        _write_hotwords(tmp_path / "bak", ["张三", "拍图验真"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        payload = _system._daily_asr_audit(_bundle(wiki_root))
        assert payload["status"] == "ok"
        assert payload["hotword_count"] == 2

    def test_daily_does_not_use_cwd_relative_path(self, wiki_root, tmp_path, monkeypatch):
        """原先这里读 Path("output")（CWD 相对），换个工作目录就报「未找到热词文件」。"""
        _write_hotwords(tmp_path / "bak", ["张三", "拍图验真"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        monkeypatch.chdir(tmp_path)  # CWD 下没有 output/
        from iris.app.cli._handlers import _system
        assert _system._daily_asr_audit(_bundle(wiki_root))["status"] == "ok"

    def test_error_reason_carries_detail(self, wiki_root, monkeypatch):
        from iris.app.cli._handlers import _system

        def _boom(bundle):
            raise RuntimeError("磁盘炸了")

        monkeypatch.setattr(audit_mod, "run_asr_audit", _boom)
        payload = _system._daily_asr_audit(_bundle(wiki_root))
        assert payload["status"] == "error"
        # 原先只回「ASR 审计异常」六个字，诊断线索全丢
        assert "磁盘炸了" in payload["reason"]


class TestDeployHotwordLimit:
    def _deploy(self, tmp_path, monkeypatch, words, manual=None):
        voco = tmp_path / "VocoType"
        voco.mkdir(exist_ok=True)
        monkeypatch.setenv("IRIS_VOCOTYPE_DIR", str(voco))
        monkeypatch.setattr(_wiki, "_merge_manual_hotwords",
                            lambda h, d, l, **k: list(h) + list(manual or []))
        return _wiki._deploy_asr_to_vocotype(
            words, "hw.txt", "", "", "", LOGGER,
            bak_dir=tmp_path / "bak", data_dir=tmp_path / "data"), voco

    def test_rejects_over_limit(self, tmp_path, monkeypatch):
        words = [f"词{i}" for i in range(501)]
        with pytest.raises(IrisValueError, match="超过 VocoType 上限 500"):
            self._deploy(tmp_path, monkeypatch, words)

    def test_at_limit_passes(self, tmp_path, monkeypatch):
        words = [f"词{i}" for i in range(500)]
        deployed, voco = self._deploy(tmp_path, monkeypatch, words)
        assert (voco / "hotwords.txt").read_text(encoding="utf-8").count("\n") == 500

    def test_manual_words_counted_toward_limit(self, tmp_path, monkeypatch):
        """越界往往不是生成侧超了，而是手动词叠上去——必须一起算。"""
        words = [f"词{i}" for i in range(495)]
        with pytest.raises(IrisValueError):
            self._deploy(tmp_path, monkeypatch, words, manual=[f"手{i}" for i in range(10)])

    def test_nothing_written_when_over_limit(self, tmp_path, monkeypatch):
        voco = tmp_path / "VocoType"
        voco.mkdir()
        (voco / "hotwords.txt").write_text("旧内容\n", encoding="utf-8")
        monkeypatch.setenv("IRIS_VOCOTYPE_DIR", str(voco))
        monkeypatch.setattr(_wiki, "_merge_manual_hotwords", lambda h, d, l, **k: list(h))
        with pytest.raises(IrisValueError):
            _wiki._deploy_asr_to_vocotype(
                [f"词{i}" for i in range(501)], "hw.txt", "", "", "", LOGGER,
                bak_dir=tmp_path / "bak", data_dir=tmp_path / "data")
        # 拒绝就整体不写：不能落一张半截表
        assert (voco / "hotwords.txt").read_text(encoding="utf-8") == "旧内容\n"


class TestDataCopiesAreAtomic:
    def test_replace_dict_and_prompt_written_atomically(self, tmp_path, monkeypatch):
        """corrector 每 5 秒轮询这两个文件，非原子写会让它读到半截内容。"""
        from iris.utils import shared

        voco = tmp_path / "VocoType"
        voco.mkdir()
        monkeypatch.setenv("IRIS_VOCOTYPE_DIR", str(voco))
        monkeypatch.setattr(_wiki, "_merge_manual_hotwords", lambda h, d, l, **k: list(h))

        calls = []
        real = shared.atomic_write_text

        def _spy(path, content, **kw):
            calls.append(Path(path).name)
            return real(path, content, **kw)

        monkeypatch.setattr(_wiki, "atomic_write_text", _spy)

        rd = tmp_path / "asr-replace-dict-x.json"
        rd.write_text('{"replace_map": {}}', encoding="utf-8")
        prompt = tmp_path / "asr-prompt-v9-x.md"
        prompt.write_text("提示词", encoding="utf-8")

        data_dir = tmp_path / "data"
        _wiki._deploy_asr_to_vocotype(
            ["词"], "hw.txt", str(rd), "提示词", str(prompt), LOGGER,
            bak_dir=tmp_path / "bak", data_dir=data_dir)

        assert "asr_replace_dict.json" in calls
        assert "asr_prompt.md" in calls
        assert (data_dir / "asr_replace_dict.json").exists()
        assert (data_dir / "asr_prompt.md").read_text(encoding="utf-8") == "提示词"


class TestAsrAuditCliExitCode:
    def test_returns_1_when_no_artifact(self, tmp_path, monkeypatch, capsys):
        """无产物时退出码 1（旧实现返回 0 并给一份「覆盖 0%」的报告，把
        「没跑过」渲染成「跑出来是 0 分」）。"""
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        args = SimpleNamespace(pretty=False)
        rc = _wiki.handle_asr_audit(args, _bundle(tmp_path / "wiki"), LOGGER)
        assert rc == 1

    def test_returns_0_on_success(self, wiki_root, tmp_path, monkeypatch, capsys):
        _write_hotwords(tmp_path / "bak", ["张三", "拍图验真"])
        _patch_audit_dir(monkeypatch, tmp_path / "bak")
        args = SimpleNamespace(pretty=False)
        rc = _wiki.handle_asr_audit(args, _bundle(wiki_root), LOGGER)
        assert rc == 0
        assert '"status": "ok"' in capsys.readouterr().out


class TestManualHotwordsSensitiveFilter:
    def test_manual_words_pass_through_filter(self, tmp_path, monkeypatch):
        """手动词是唯一能绕过 Phase 1 守卫进 VocoType 的通道，必须过过滤。"""
        manual = tmp_path / "asr_manual_hotwords.txt"
        manual.write_text("# 注释\n手动词甲\n手动词乙\n", encoding="utf-8")
        seen = {}

        def _fake_filter(words, on_drop=None):
            seen["words"] = list(words)
            return list(words)[:1]

        monkeypatch.setattr("iris.wiki._sensitive.filter_sensitive_terms", _fake_filter)

        merged = _wiki._merge_manual_hotwords(["生成词"], [], LOGGER,
                                              data_dir=tmp_path)
        assert seen["words"] == ["手动词甲", "手动词乙"]
        assert "生成词" in merged and "手动词甲" in merged and "手动词乙" not in merged


class TestResolveProfilePath:
    def test_relative_resolved_against_project_root(self):
        from iris.utils.paths import get_project_root
        assert _wiki.resolve_profile_path("data/asr_replace_dict.json") == \
            get_project_root() / "data" / "asr_replace_dict.json"

    def test_relative_no_longer_cwd_dependent(self, tmp_path, monkeypatch):
        before = _wiki.resolve_profile_path("data/asr_prompt.md")
        monkeypatch.chdir(tmp_path)
        assert _wiki.resolve_profile_path("data/asr_prompt.md") == before

    def test_absolute_passed_through(self, tmp_path):
        p = tmp_path / "custom.json"
        assert _wiki.resolve_profile_path(str(p)) == p

    def test_tilde_expanded(self, monkeypatch):
        assert _wiki.resolve_profile_path("~/x.json").is_absolute()

    def test_bare_relative_does_not_raise(self):
        """裸相对路径（无 data/ 前缀）不能冒 ValueError 到 CLI 顶层。"""
        from iris.utils.paths import get_project_root
        assert _wiki.resolve_profile_path("asr_replace_dict.json") == \
            get_project_root() / "asr_replace_dict.json"
