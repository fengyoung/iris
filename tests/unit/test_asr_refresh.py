"""daily-start 的 ASR 资源更新（重建 → 部署 → 重启 VocoType）— 单元测试。

设计要点：
  - 指纹增量：Wiki 未变化必须整体跳过（否则每天白跑十余次 LLM 调用）
  - 重启失败不回滚，但必须显式记账（配置已换却未生效是「没有失败信号」的典型）
"""

import json
import logging
from types import SimpleNamespace

import pytest

from iris.app.cli._handlers import _wiki
from iris.wiki.asr import _vocotype
from iris.wiki.asr.version import compute_input_fingerprint

LOGGER = logging.getLogger("test-asr-refresh")


def _bundle(root, wiki_root):
    return SimpleNamespace(root=root, wiki={"wiki_root": str(wiki_root)})


@pytest.fixture
def wiki_root(tmp_path):
    root = tmp_path / "wiki"
    (root / "04-人物").mkdir(parents=True)
    (root / "04-人物" / "人物-张三.md").write_text(
        "---\ntitle: 张三\ntype: person\n---\n\n# 张三\n", encoding="utf-8")
    return root


def _write_version(data_dir, fingerprint):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "asr_prompt_version.json").write_text(json.dumps({
        "version": "0.0.6", "generated_at": "2026-10-08T09:57:52Z",
        "wiki_page_count": 1, "term_count": 10, "fingerprint": fingerprint,
        "prompt_text": "",
    }), encoding="utf-8")


def _fingerprint_of(wiki_root, data_dir):
    from iris.wiki.context_loader import WikiContextLoader
    pages = WikiContextLoader(wiki_root).load_pages(
        sort_order=["person", "concept", "project", "domain"])
    return compute_input_fingerprint(pages, data_dir)


class TestFingerprintGate:
    def test_skips_when_wiki_unchanged(self, tmp_path, wiki_root, monkeypatch):
        """指纹一致 → 跳过，且绝不调用生成（这条是成本护栏）。"""
        _write_version(tmp_path / "data", _fingerprint_of(wiki_root, tmp_path / "data"))

        def _boom(*a, **k):
            raise AssertionError("Wiki 未变化时不应触发生成")

        monkeypatch.setattr(_wiki, "run_build_asr_prompt", _boom)
        result = _wiki.refresh_asr_resources(_bundle(tmp_path, wiki_root), LOGGER)
        assert result["status"] == "skipped"
        assert "未变化" in result["reason"]

    def test_runs_when_fingerprint_differs(self, tmp_path, wiki_root, monkeypatch):
        _write_version(tmp_path / "data", "deadbeefdeadbeef")
        monkeypatch.setattr(_wiki, "run_build_asr_prompt",
                            lambda *a, **k: {"hotword_count": 494, "version": "0.0.7"})
        monkeypatch.setattr(_vocotype, "restart", lambda: {"status": "restarted",
                                                           "detail": "已重启"})
        result = _wiki.refresh_asr_resources(_bundle(tmp_path, wiki_root), LOGGER)
        assert result["status"] == "ok"
        assert result["previous_fingerprint"] == "deadbeefdeadbeef"

    def test_runs_when_no_version_file(self, tmp_path, wiki_root, monkeypatch):
        """首次生成：没有版本文件也必须跑，不能当成「未变化」。"""
        monkeypatch.setattr(_wiki, "run_build_asr_prompt",
                            lambda *a, **k: {"hotword_count": 1, "version": "0.0.1"})
        monkeypatch.setattr(_vocotype, "restart", lambda: {"status": "restarted",
                                                           "detail": ""})
        assert _wiki.refresh_asr_resources(_bundle(tmp_path, wiki_root),
                                           LOGGER)["status"] == "ok"

    def test_manual_hotwords_change_triggers_rebuild(self, tmp_path, wiki_root,
                                                     monkeypatch):
        """手动热词是热词表的第二个来源，改了它必须触发重建。

        否则「改 data/asr_manual_hotwords.txt 想让它生效」这个意图永远落空——
        Wiki 没动，指纹就不动，daily-start 直接跳过。
        """
        data_dir = tmp_path / "data"
        _write_version(data_dir, _fingerprint_of(wiki_root, data_dir))
        (data_dir / "asr_manual_hotwords.txt").write_text("冯逸东\n", encoding="utf-8")

        monkeypatch.setattr(_wiki, "run_build_asr_prompt",
                            lambda *a, **k: {"hotword_count": 1, "version": "0.0.7"})
        monkeypatch.setattr(_vocotype, "restart", lambda: {"status": "restarted",
                                                           "detail": ""})
        assert _wiki.refresh_asr_resources(
            _bundle(tmp_path, wiki_root), LOGGER)["status"] == "ok"

    def test_manual_hotwords_comment_does_not_trigger(self, tmp_path, wiki_root,
                                                      monkeypatch):
        """只加注释/调顺序不该触发重建（一次重建十几轮 LLM 调用）。"""
        data_dir = tmp_path / "data"
        (data_dir).mkdir(parents=True)
        (data_dir / "asr_manual_hotwords.txt").write_text(
            "# 第一版注释\n冯逸东\niris\n", encoding="utf-8")
        _write_version(data_dir, _fingerprint_of(wiki_root, data_dir))

        (data_dir / "asr_manual_hotwords.txt").write_text(
            "# 改过的注释\n\niris\n冯逸东\n", encoding="utf-8")

        def _boom(*a, **k):
            raise AssertionError("注释/顺序变化不应触发重建")

        monkeypatch.setattr(_wiki, "run_build_asr_prompt", _boom)
        assert _wiki.refresh_asr_resources(
            _bundle(tmp_path, wiki_root), LOGGER)["status"] == "skipped"

    def test_skips_without_wiki(self, tmp_path):
        result = _wiki.refresh_asr_resources(
            SimpleNamespace(root=tmp_path, wiki={}), LOGGER)
        assert result["status"] == "skipped"

    def test_skips_when_wiki_dir_missing(self, tmp_path):
        result = _wiki.refresh_asr_resources(
            _bundle(tmp_path, tmp_path / "nope"), LOGGER)
        assert result["status"] == "skipped"


class TestRefreshPayload:
    def _run(self, tmp_path, wiki_root, monkeypatch, restart_result):
        _write_version(tmp_path / "data", "oldfingerprint00")
        monkeypatch.setattr(_wiki, "run_build_asr_prompt", lambda *a, **k: {
            "hotword_count": 494, "replace_mapping_count": 876,
            "version": "0.0.7", "deployed": ["data/asr_replace_dict.json"]})
        monkeypatch.setattr(_vocotype, "restart", lambda: restart_result)
        return _wiki.refresh_asr_resources(_bundle(tmp_path, wiki_root), LOGGER)

    def test_ok_when_restarted(self, tmp_path, wiki_root, monkeypatch):
        r = self._run(tmp_path, wiki_root, monkeypatch,
                      {"status": "restarted", "detail": "已重启，新热词表生效"})
        assert r["status"] == "ok"
        assert r["hotword_count"] == 494 and r["replace_mapping_count"] == 876
        assert r["vocotype"]["status"] == "restarted"
        assert "warning" not in r

    def test_ok_when_app_not_running(self, tmp_path, wiki_root, monkeypatch):
        """App 没开不算失败：下次启动自会读到新配置。"""
        r = self._run(tmp_path, wiki_root, monkeypatch,
                      {"status": "not_running", "detail": "未在运行"})
        assert r["status"] == "ok"
        assert "warning" not in r

    @pytest.mark.parametrize("status", ["quit_failed", "start_failed"])
    def test_deploy_only_when_restart_failed(self, tmp_path, wiki_root,
                                             monkeypatch, status):
        """已部署但未重启 ⇒ 新热词表没生效，必须显式告警。"""
        r = self._run(tmp_path, wiki_root, monkeypatch,
                      {"status": status, "detail": "退出超时"})
        assert r["status"] == "deploy_only"
        assert "未重启" in r["warning"]
        assert "退出超时" in r["warning"]

    def test_uses_all_mode_and_deploy(self, tmp_path, wiki_root, monkeypatch):
        _write_version(tmp_path / "data", "oldfingerprint00")
        seen = {}

        def _capture(args, bundle, logger):
            seen["mode"] = args.asr_mode
            seen["deploy"] = args.deploy
            return {"hotword_count": 1, "version": "0.0.7"}

        monkeypatch.setattr(_wiki, "run_build_asr_prompt", _capture)
        monkeypatch.setattr(_vocotype, "restart", lambda: {"status": "restarted",
                                                           "detail": ""})
        _wiki.refresh_asr_resources(_bundle(tmp_path, wiki_root), LOGGER)
        assert seen == {"mode": "all", "deploy": True}


class TestVocotypeRestart:
    def test_not_running_skips_quit(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_vocotype, "is_running", lambda: False)
        monkeypatch.setattr(_vocotype, "_run", lambda *a, **k: calls.append(a) or
                            SimpleNamespace(returncode=0, stdout="", stderr=""))
        result = _vocotype.restart()
        assert result["status"] == "not_running"
        assert calls == []  # 没有发退出/启动请求

    def test_restarted(self, monkeypatch):
        state = {"running": True}
        monkeypatch.setattr(_vocotype, "is_running", lambda: state["running"])

        def _run(cmd, timeout=15.0):
            if cmd[0] == "osascript":
                state["running"] = False
            elif cmd[0] == "open":
                state["running"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_vocotype, "_run", _run)
        monkeypatch.setattr(_vocotype, "_wait", lambda pred, t: pred())
        assert _vocotype.restart()["status"] == "restarted"

    def test_quit_failed_on_nonzero(self, monkeypatch):
        monkeypatch.setattr(_vocotype, "is_running", lambda: True)
        monkeypatch.setattr(_vocotype, "_run", lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="", stderr="拒绝退出"))
        result = _vocotype.restart()
        assert result["status"] == "quit_failed"
        assert "拒绝退出" in result["detail"]

    def test_quit_timeout_does_not_force_kill(self, monkeypatch):
        """退出超时不能退化成 pkill——可能正在录音。"""
        monkeypatch.setattr(_vocotype, "is_running", lambda: True)
        monkeypatch.setattr(_vocotype, "_run", lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="", stderr=""))
        monkeypatch.setattr(_vocotype, "_wait", lambda pred, t: False)
        result = _vocotype.restart(quit_timeout=0.1)
        assert result["status"] == "quit_failed"
        assert "未强制终止" in result["detail"]

    def test_start_failed_when_never_comes_back(self, monkeypatch):
        state = {"running": True}
        monkeypatch.setattr(_vocotype, "is_running", lambda: state["running"])

        def _run(cmd, timeout=15.0):
            if cmd[0] == "osascript":
                state["running"] = False
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_vocotype, "_run", _run)
        monkeypatch.setattr(_vocotype, "_wait", lambda pred, t: pred())
        assert _vocotype.restart()["status"] == "start_failed"

    def test_oserror_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr(_vocotype, "is_running", lambda: True)

        def _boom(*a, **k):
            raise OSError("osascript 不存在")

        monkeypatch.setattr(_vocotype, "_run", _boom)
        assert _vocotype.restart()["status"] == "quit_failed"


class TestDailyStage:
    def test_stage_isolates_failure(self, monkeypatch):
        from iris.app.cli._handlers import _system

        monkeypatch.setattr(_wiki, "refresh_asr_resources",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸了")))
        result = _system._daily_asr_refresh(_bundle("/tmp", "/tmp"), LOGGER)
        assert result["status"] == "error"
        assert "炸了" in result["reason"]
