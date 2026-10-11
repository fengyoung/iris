"""ASR 存档目录与保留策略 — 单元测试。"""

import os
import time

from iris.wiki.asr.artifacts import (
    BAK_DIRNAME,
    LEGACY_DIRNAME,
    VOCOTYPE_BACKUP_DIRNAME,
    asr_bak_dir,
    latest_artifact,
    prune_bak,
)
from iris.utils.paths import get_project_root


def _touch(path, content="x", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestBakDir:
    def test_under_project_root_output(self):
        bak = asr_bak_dir()
        assert bak == get_project_root() / "output" / BAK_DIRNAME

    def test_name(self):
        assert BAK_DIRNAME == "asr-corrector-bak"


class TestLatestArtifact:
    def test_none_when_missing_dir(self, tmp_path):
        assert latest_artifact(tmp_path / "nope", "asr-hotwords-*.txt") is None

    def test_none_when_no_match(self, tmp_path):
        _touch(tmp_path / "unrelated.txt")
        assert latest_artifact(tmp_path, "asr-hotwords-*.txt") is None

    def test_picks_newest_by_mtime(self, tmp_path):
        old = _touch(tmp_path / "asr-hotwords-20260719-124901.txt", mtime=1_700_000_000)
        new = _touch(tmp_path / "asr-hotwords-20260811-012923.txt", mtime=1_800_000_000)
        assert latest_artifact(tmp_path, "asr-hotwords-*.txt") == new
        assert latest_artifact(tmp_path, "asr-hotwords-*.txt") != old

    def test_version_prefix_does_not_break_ordering(self, tmp_path):
        """版本号前缀会让字典序把更旧的排在前面（'4' > '0'），必须按 mtime 选。

        真实语料：asr-prompt-v4.11.4-20260719（旧）与 asr-prompt-v0.0.6-20261008（新）。
        """
        _touch(tmp_path / "asr-prompt-v4.11.4-20260719-124901.md", mtime=1_700_000_000)
        newest = _touch(tmp_path / "asr-prompt-v0.0.6-20261008-095752.md", mtime=1_800_000_000)
        assert latest_artifact(tmp_path, "asr-prompt-v*.md") == newest

    def test_dir_family(self, tmp_path):
        _touch(tmp_path / "vocotype-backup" / "20260920-170619" / "hotwords.txt", mtime=1_700_000_000)
        new = tmp_path / "vocotype-backup" / "20261008-180732"
        _touch(new / "hotwords.txt", mtime=1_800_000_000)
        assert latest_artifact(tmp_path, "vocotype-backup/*", is_dir=True) == new

    def test_dir_family_ignores_files(self, tmp_path):
        """同名条目既有文件也有目录时，is_dir 过滤必须真的生效。"""
        as_file = _touch(tmp_path / "asr-hotwords-20260001-000000.txt", content="file")
        as_dir = tmp_path / "asr-hotwords-20260002-000000.txt"
        as_dir.mkdir()
        assert latest_artifact(tmp_path, "asr-hotwords-*.txt", is_dir=False) == as_file
        assert latest_artifact(tmp_path, "asr-hotwords-*.txt", is_dir=True) == as_dir


class TestPruneBak:
    def _make(self, tmp_path, pattern_fmt, count, base=1_700_000_000):
        made = []
        for i in range(count):
            made.append(_touch(tmp_path / pattern_fmt.format(i=i), mtime=base + i))
        return made

    def test_keeps_newest_and_moves_rest(self, tmp_path):
        made = self._make(tmp_path, "asr-hotwords-2026{i:04d}-000000.txt", 25)
        moved = prune_bak(tmp_path, keep=20)
        assert len(moved) == 5
        # 最旧的 5 个被移走，最新的 20 个留在原地
        for old in made[:5]:
            assert not old.exists()
            assert (tmp_path / LEGACY_DIRNAME / old.name).exists()
        for keep in made[5:]:
            assert keep.exists()

    def test_each_family_pruned_independently(self, tmp_path):
        self._make(tmp_path, "asr-hotwords-2026{i:04d}-000000.txt", 3)
        self._make(tmp_path, "asr-replace-dict-2026{i:04d}-000000.json", 22)
        moved = prune_bak(tmp_path, keep=20)
        assert len(moved) == 2
        assert len(list(tmp_path.glob("asr-hotwords-*.txt"))) == 3  # 未被动过

    def test_backup_dirs_keep_grouping_in_legacy(self, tmp_path):
        for i in range(3):
            _touch(tmp_path / VOCOTYPE_BACKUP_DIRNAME / f"2026{i:04d}-000000" / "hotwords.txt",
                   mtime=1_700_000_000 + i)
        moved = prune_bak(tmp_path, keep=1)
        assert len(moved) == 2
        # legacy 下保留 vocotype-backup 这一层，否则同名时间戳目录会与产物混在一起
        assert (tmp_path / LEGACY_DIRNAME / VOCOTYPE_BACKUP_DIRNAME).is_dir()
        assert len(list((tmp_path / LEGACY_DIRNAME / VOCOTYPE_BACKUP_DIRNAME).iterdir())) == 2

    def test_idempotent_and_does_not_reprune_legacy(self, tmp_path):
        self._make(tmp_path, "asr-hotwords-2026{i:04d}-000000.txt", 22)
        assert len(prune_bak(tmp_path, keep=20)) == 2
        assert prune_bak(tmp_path, keep=20) == []  # 再跑一次不应重复搬运

    def test_keep_zero_disables(self, tmp_path):
        self._make(tmp_path, "asr-hotwords-2026{i:04d}-000000.txt", 5)
        assert prune_bak(tmp_path, keep=0) == []
        assert not (tmp_path / LEGACY_DIRNAME).exists()

    def test_missing_dir_is_noop(self, tmp_path):
        assert prune_bak(tmp_path / "nope") == []

    def test_does_not_overwrite_same_name_in_legacy(self, tmp_path):
        """legacy 里已有同名文件时不覆盖、也不静默丢弃（留在原地并告警）。"""
        legacy = tmp_path / LEGACY_DIRNAME
        _touch(legacy / "asr-hotwords-20260001-000000.txt", content="already there")
        _touch(tmp_path / "asr-hotwords-20260001-000000.txt", content="new copy",
               mtime=1_700_000_000)
        newest = _touch(tmp_path / "asr-hotwords-20260002-000000.txt", content="newest",
                        mtime=1_800_000_000)
        moved = prune_bak(tmp_path, keep=1)  # 最旧的 1 条该被搬走，但它撞名了
        assert moved == []
        assert (legacy / "asr-hotwords-20260001-000000.txt").read_text() == "already there"
        assert (tmp_path / "asr-hotwords-20260001-000000.txt").exists()  # 未丢
        assert newest.exists()


class TestOrderingStability:
    def test_equal_mtime_falls_back_to_name(self, tmp_path):
        same = time.time()
        a = _touch(tmp_path / "asr-hotwords-20260001-000000.txt", mtime=same)
        b = _touch(tmp_path / "asr-hotwords-20260002-000000.txt", mtime=same)
        assert latest_artifact(tmp_path, "asr-hotwords-*.txt") == b
        assert a.exists()
