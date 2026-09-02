"""BiweeklyCache 单元测试。"""

from __future__ import annotations

import json
import pytest

from iris.analysis._biweekly_cache import BiweeklyCache


@pytest.fixture
def cache(tmp_path) -> BiweeklyCache:
    return BiweeklyCache(tmp_path / "cache")


class TestContentHash:
    def test_same_text_same_hash(self, cache):
        assert cache.content_hash("hello") == cache.content_hash("hello")

    def test_different_text_different_hash(self, cache):
        assert cache.content_hash("hello") != cache.content_hash("world")

    def test_prefix_len_respected(self, cache):
        long_text = "A" * 3000 + "B" * 1000
        short_text = "A" * 3000 + "C" * 1000
        # prefix_len=2000，两者前 2000 字符相同 → hash 相同
        assert cache.content_hash(long_text, 2000) == cache.content_hash(short_text, 2000)
        # prefix_len=全文 → hash 不同
        assert cache.content_hash(long_text, len(long_text)) != cache.content_hash(short_text, len(short_text))


class TestOpDirectionsCache:
    def test_miss_when_no_file(self, cache):
        assert cache.load_op_directions("abc123") is None

    def test_miss_when_hash_differs(self, cache):
        cache.save_op_directions("hash1", [{"id": 1, "name": "方向一"}])
        assert cache.load_op_directions("hash2") is None

    def test_hit_when_hash_matches(self, cache):
        directions = [{"id": 1, "name": "方向一"}, {"id": 2, "name": "方向二"}]
        cache.save_op_directions("hash1", directions)
        result = cache.load_op_directions("hash1")
        assert result == directions

    def test_miss_when_directions_empty(self, cache):
        # 空列表不应缓存，但即使写入也不应命中（load 检查 if directions）
        cache.save_op_directions("hash1", [])
        # save 会写文件，但 load 会因 directions 为空而返回 None
        assert cache.load_op_directions("hash1") is None

    def test_corrupted_file_returns_none(self, cache, tmp_path):
        path = cache.cache_dir / "op_directions.json"
        path.write_text("not valid json", encoding="utf-8")
        assert cache.load_op_directions("any") is None


class TestStage1FilterCache:
    def test_miss_when_no_file(self, cache):
        assert cache.load_stage1_filter("inv", "dir") is None

    def test_miss_when_either_hash_differs(self, cache):
        data = {"方向一": {"high": [], "medium": [], "low": [], "none": []}}
        cache.save_stage1_filter("inv1", "dir1", data)
        assert cache.load_stage1_filter("inv1", "dir2") is None
        assert cache.load_stage1_filter("inv2", "dir1") is None

    def test_hit_when_both_hashes_match(self, cache):
        data = {"方向一": {"high": [{"label": "A"}], "medium": [], "low": [], "none": []}}
        cache.save_stage1_filter("inv1", "dir1", data)
        result = cache.load_stage1_filter("inv1", "dir1")
        assert result == data


class TestStyleGuideCache:
    def test_miss_when_no_file(self, cache):
        assert cache.load_style_guide() is None

    def test_save_and_load(self, cache):
        guide = {"narrative_voice": "决策者视角", "version": 1}
        cache.save_style_guide(guide)
        result = cache.load_style_guide()
        assert result == guide

    def test_corrupted_returns_none(self, cache):
        (cache.cache_dir / "style_guide.json").write_text("{bad json", encoding="utf-8")
        assert cache.load_style_guide() is None


class TestFileBriefsCache:
    def test_empty_index_on_missing_file(self, cache):
        assert cache.load_brief_index() == {}

    def test_save_and_load_brief(self, cache):
        index = {}
        brief = {"brief_md": "## 摘要\n内容", "primary_direction": 1}
        cache.save_brief("label-A", "hash1", brief, index)
        assert index == {"label-A": "hash1"}

        loaded = cache.load_brief("label-A", "hash1", index)
        assert loaded == brief

    def test_miss_when_hash_differs(self, cache):
        index = {"label-A": "hash1"}
        (cache.briefs_dir / "hash1.json").write_text(
            json.dumps({"brief_md": "内容"}), encoding="utf-8")
        assert cache.load_brief("label-A", "hash2", index) is None

    def test_miss_when_file_missing(self, cache):
        index = {"label-A": "hash1"}  # 索引有记录但文件不存在
        assert cache.load_brief("label-A", "hash1", index) is None

    def test_flush_writes_index(self, cache):
        index = {"label-A": "abc", "label-B": "def"}
        # 写两个 brief 文件
        for h in ["abc", "def"]:
            (cache.briefs_dir / f"{h}.json").write_text("{}", encoding="utf-8")
        cache.flush_brief_index(index)
        loaded = json.loads((cache.briefs_dir / "index.json").read_text("utf-8"))
        assert loaded == index

    def test_flush_cleans_orphan_files(self, cache, tmp_path):
        import time
        # 写一个不在索引中的旧文件（模拟超期）
        orphan = cache.briefs_dir / "orphan_hash.json"
        orphan.write_text("{}", encoding="utf-8")
        # 将其 mtime 设为 31 天前
        old_time = time.time() - 31 * 86400
        import os
        os.utime(str(orphan), (old_time, old_time))
        # flush 时 valid_hashes 不含 orphan_hash → 应被清理
        cache.flush_brief_index({"label-A": "valid_hash"})
        assert not orphan.exists()

    def test_flush_keeps_recent_orphan(self, cache):
        # 不在索引中但新文件（< 30 天）→ 不删除
        recent = cache.briefs_dir / "recent_hash.json"
        recent.write_text("{}", encoding="utf-8")
        cache.flush_brief_index({})
        assert recent.exists()
